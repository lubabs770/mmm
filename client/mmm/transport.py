"""Getting a socket to the phone's loopback port.

By default MMM listens on loopback only, so reaching it from a computer means
tunnelling: SSH (Termux's sshd, usually) or ``adb forward``. Both carry their own
authentication; the token is checked on top. With the app's tailnet setting on, a
computer on the same Tailscale network can dial the phone directly instead.
"""

from __future__ import annotations

import socket
import subprocess
import time

from .config import LOOPBACK, Profile


class TunnelError(RuntimeError):
    pass


class Transport:
    """Opens sockets to the RPC port, bringing up a tunnel first if needed."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def connect(self, timeout: float = 5.0) -> socket.socket:
        self.ensure()
        return self._dial(timeout)

    def ensure(self) -> None:
        """Brings the tunnel up, or checks it is still up. Cheap to call often."""

    def close(self) -> None:
        """Tears down anything [ensure] started."""

    def describe(self) -> str:
        return f"{self.profile.host}:{self.profile.connect_port}"

    def _dial(self, timeout: float) -> socket.socket:
        connection = socket.create_connection(
            (self.profile.host, self.profile.connect_port), timeout=timeout
        )
        connection.settimeout(None)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return connection

    def _wait_for_port(self, port: int, deadline: float) -> None:
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((LOOPBACK, port), timeout=1.0):
                    return
            except OSError as error:  # not up yet
                last = error
                time.sleep(0.2)
        raise TunnelError(f"nothing is listening on {LOOPBACK}:{port} ({last})")


class LocalTransport(Transport):
    """On the phone itself, in Termux: the port is simply there."""


class TailnetTransport(Transport):
    """Straight to the phone's Tailscale name or address; Tailscale is the tunnel."""

    def describe(self) -> str:
        return f"tailnet {self.profile.host}:{self.profile.port}"


class SshTransport(Transport):
    """``ssh -N -L`` held open by this process, and restarted when it drops."""

    def __init__(self, profile: Profile) -> None:
        super().__init__(profile)
        self._process: subprocess.Popen | None = None

    def ensure(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        profile = self.profile
        if not profile.ssh_host:
            raise TunnelError("the ssh transport needs ssh_host in the profile")
        command = [
            "ssh",
            "-N",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            f"{profile.local_port}:{LOOPBACK}:{profile.port}",
        ]
        if profile.ssh_port:
            command += ["-p", str(profile.ssh_port)]
        command.append(
            f"{profile.ssh_user}@{profile.ssh_host}" if profile.ssh_user else profile.ssh_host
        )
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            self._wait_for_port(profile.local_port, time.monotonic() + 20)
        except TunnelError:
            self.close()
            raise

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def describe(self) -> str:
        return f"ssh {self.profile.ssh_host} → {LOOPBACK}:{self.profile.port}"


class AdbTransport(Transport):
    """``adb forward``, for a phone on the USB cable."""

    def ensure(self) -> None:
        profile = self.profile
        command = ["adb"]
        if profile.adb_serial:
            command += ["-s", profile.adb_serial]
        command += ["forward", f"tcp:{profile.local_port}", f"tcp:{profile.port}"]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise TunnelError(f"adb forward failed: {result.stderr.strip() or result.stdout.strip()}")
        self._wait_for_port(profile.local_port, time.monotonic() + 10)

    def close(self) -> None:
        profile = self.profile
        command = ["adb"]
        if profile.adb_serial:
            command += ["-s", profile.adb_serial]
        command += ["forward", "--remove", f"tcp:{profile.local_port}"]
        subprocess.run(command, capture_output=True, text=True)

    def describe(self) -> str:
        return f"adb forward → {LOOPBACK}:{self.profile.port}"


def for_profile(profile: Profile) -> Transport:
    return {
        "local": LocalTransport,
        "ssh": SshTransport,
        "adb": AdbTransport,
        "tailnet": TailnetTransport,
    }[profile.transport](profile)
