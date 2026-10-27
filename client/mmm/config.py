"""Connection profiles, stored in ``~/.config/mmm/config.toml``.

A profile says how to reach the phone and which token to present. ``mmm pair``
writes one; the library reads it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 7357
LOOPBACK = "127.0.0.1"

TRANSPORTS = ("local", "ssh", "adb", "tailnet")


def config_path() -> Path:
    """Honours ``MMM_CONFIG`` and ``XDG_CONFIG_HOME``."""
    override = os.environ.get("MMM_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "mmm" / "config.toml"


@dataclass
class Profile:
    """One way of reaching one phone."""

    name: str = "local"
    token: str = ""
    transport: str = "local"
    host: str = LOOPBACK
    port: int = DEFAULT_PORT
    #: Local end of the tunnel for the ``ssh`` and ``adb`` transports.
    local_port: int = 0
    ssh_host: str = ""
    ssh_port: int = 0
    ssh_user: str = ""
    adb_serial: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.transport not in TRANSPORTS:
            raise ValueError(
                f"unknown transport {self.transport!r}; expected one of {', '.join(TRANSPORTS)}"
            )
        if not self.local_port:
            self.local_port = self.port

    @property
    def connect_port(self) -> int:
        """The port the client actually opens a socket to."""
        return self.port if self.transport in ("local", "tailnet") else self.local_port

    def to_toml(self) -> str:
        lines = [f"[profiles.{_quote_key(self.name)}]", f"transport = {_quote(self.transport)}"]
        if self.token:
            lines.append(f"token = {_quote(self.token)}")
        lines.append(f"host = {_quote(self.host)}")
        lines.append(f"port = {self.port}")
        if self.transport not in ("local", "tailnet"):
            lines.append(f"local_port = {self.local_port}")
        if self.ssh_host:
            lines.append(f"ssh_host = {_quote(self.ssh_host)}")
        if self.ssh_port:
            lines.append(f"ssh_port = {self.ssh_port}")
        if self.ssh_user:
            lines.append(f"ssh_user = {_quote(self.ssh_user)}")
        if self.adb_serial:
            lines.append(f"adb_serial = {_quote(self.adb_serial)}")
        return "\n".join(lines) + "\n"


@dataclass
class Config:
    default: str = "local"
    profiles: dict[str, Profile] = field(default_factory=dict)

    def profile(self, name: str | None = None) -> Profile:
        wanted = name or os.environ.get("MMM_PROFILE") or self.default
        if wanted in self.profiles:
            return self.profiles[wanted]
        if not self.profiles:
            raise LookupError(
                f"no profiles in {config_path()}; run `mmm pair <token>` first"
            )
        raise LookupError(
            f"no profile {wanted!r} in {config_path()}; "
            f"have {', '.join(sorted(self.profiles))}"
        )

    def to_toml(self) -> str:
        parts = [f"default = {_quote(self.default)}\n"]
        for name in sorted(self.profiles):
            parts.append(self.profiles[name].to_toml())
        return "\n".join(parts)


def _quote(value: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, as long as
    non-ASCII stays literal (TOML has no surrogate-pair escapes)."""
    return json.dumps(str(value), ensure_ascii=False)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _quote_key(name: str) -> str:
    return name if _BARE_KEY.match(name) else _quote(name)


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    return parse(path.read_text(encoding="utf-8"))


def parse(text: str) -> Config:
    data = _load_toml(text)
    config = Config(default=str(data.get("default", "local")))
    for name, raw in (data.get("profiles") or {}).items():
        known = {
            key: raw[key]
            for key in (
                "token",
                "transport",
                "host",
                "port",
                "local_port",
                "ssh_host",
                "ssh_port",
                "ssh_user",
                "adb_serial",
            )
            if key in raw
        }
        extra = {k: v for k, v in raw.items() if k not in known}
        config.profiles[name] = Profile(name=name, extra=extra, **known)
    if config.default not in config.profiles and config.profiles:
        config.default = sorted(config.profiles)[0]
    return config


def save(config: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file holds tokens that grant full control of the phone, so it is created
    # 0600 from the first byte (never written wider and tightened afterwards), and
    # swapped in whole so a crash cannot leave half a file.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(config.to_toml())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    return path


def _load_toml(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+

        return tomllib.loads(text)
    except ModuleNotFoundError:
        return _mini_toml(text)


_KEY_VALUE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")
_TABLE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _mini_toml(text: str) -> dict:
    """Enough TOML for this one file, for Python 3.10 where ``tomllib`` is missing.

    Handles tables, strings, integers and booleans — which is all a profile is.
    """
    root: dict = {}
    table = root
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        matched_table = _TABLE.match(line)
        if matched_table:
            table = root
            for part in matched_table.group(1).split("."):
                table = table.setdefault(part.strip().strip("\""), {})
            continue
        matched = _KEY_VALUE.match(line)
        if not matched:
            continue
        key, raw = matched.group(1), matched.group(2)
        if raw.startswith("#"):
            continue
        if raw.startswith('"'):
            # A basic string, escapes and all; stop at its closing quote, so a
            # " #" inside the value is not taken for a comment.
            value, _ = json.JSONDecoder().raw_decode(raw)
            table[key] = value
            continue
        raw = raw.split(" #")[0].strip()
        if raw.startswith("'"):
            table[key] = raw[1:-1]
        elif raw in ("true", "false"):
            table[key] = raw == "true"
        else:
            try:
                table[key] = int(raw)
            except ValueError:
                table[key] = raw
    return root
