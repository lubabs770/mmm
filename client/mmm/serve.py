"""``mmm serve``: one long-lived connection, the rules engine, and the web UI.

The daemon holds the MMM connection, takes every incoming SMS through a durable
subscription (so texts that arrive while it is down are replayed), runs the
matching rule, and serves the UI on loopback. ``mmm ui`` finds it through
``serve.json`` and opens the page.

The page drives a full-control token, so the HTTP side is loopback-only, checks
the ``Host`` header (DNS rebinding) and wants a secret on every API call. The
MMM token itself never leaves this process.
"""

from __future__ import annotations

import collections
import hmac
import json
import logging
import logging.handlers
import os
import plistlib
import queue
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import android as phone
from . import rules as rules_module
from .android import settings, sms
from .client import Android, AndroidError, JavaClassRef, JavaObject, MmmError, json_default
from .config import config_path

DEFAULT_UI_PORT = 7358
SMS_ACTION = "android.provider.Telephony.SMS_RECEIVED"
SUBSCRIPTION = "mmm-serve"
LABEL = "lubabs770.mmm.serve"
MANAGED_ENV = "MMM_SERVE_MANAGED"
RUNS_KEPT = 1000
STATIC = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "favicon.png": "image/png",
}

log = logging.getLogger("mmm.serve")


# ------------------------------------------------------------------- paths


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "mmm"


def info_path() -> Path:
    """``{pid, port, started, managed}`` of the running daemon."""
    return config_path().with_name("serve.json")


def secret_path() -> Path:
    """Kept across restarts so an open page survives the daemon coming back."""
    return config_path().with_name("ui-secret")


def _write_private(path: Path, text: str) -> None:
    """0600 from the first byte, swapped in whole: the same rule as config.save."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def load_secret() -> str:
    path = secret_path()
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    _write_private(path, value + "\n")
    return value


def _tilde(path: Path) -> str:
    """Home shown as ~, so the page (and screenshots of it) don't show a username."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def running() -> dict | None:
    """The live daemon's serve.json, or None when there is none (or it died)."""
    try:
        info = json.loads(info_path().read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ------------------------------------------------------- start at login


def _launch_agent() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"


def _systemd_unit() -> Path:
    return Path("~/.config/systemd/user/mmm-serve.service").expanduser()


def _serve_argv(port: int, profile: str | None) -> list[str]:
    # From the single-file .pyz, `-m mmm.cli` finds nothing: run the .pyz itself.
    entry = sys.argv[0] if sys.argv else ""
    if entry and os.path.isfile(entry) and zipfile.is_zipfile(entry):
        argv = [sys.executable, os.path.abspath(entry)]
    else:
        argv = [sys.executable, "-m", "mmm.cli"]
    if profile:
        argv += ["--profile", profile]
    return argv + ["serve", "--port", str(port)]


def login_supported() -> bool:
    return sys.platform == "darwin" or (sys.platform.startswith("linux") and _which("systemctl"))


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def login_enabled() -> bool:
    if sys.platform == "darwin":
        return _launch_agent().exists()
    if sys.platform.startswith("linux") and _which("systemctl"):
        done = subprocess.run(
            ["systemctl", "--user", "is-enabled", "mmm-serve"], capture_output=True, text=True
        )
        return done.returncode == 0
    return False


def install_login(port: int, profile: str | None) -> list[str]:
    """Writes the agent/unit; returns the command that starts it."""
    argv = _serve_argv(port, profile)
    logs = state_dir()
    logs.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path = _launch_agent()
        path.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": LABEL,
            "ProgramArguments": argv,
            "RunAtLoad": True,
            "KeepAlive": True,
            # Rules run commands by name, so they need the PATH of the shell that
            # turned this on, not launchd's bare one.
            "EnvironmentVariables": {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "en_US.UTF-8",
                MANAGED_ENV: "1",
            },
            "StandardOutPath": str(logs / "launchd.log"),
            "StandardErrorPath": str(logs / "launchd.log"),
        }
        path.write_bytes(plistlib.dumps(plist))
        return ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]
    path = _systemd_unit()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(_quote_systemd(a) for a in argv)
    path.write_text(
        "[Unit]\nDescription=MMM daemon (rules and web UI)\n\n"
        f"[Service]\nExecStart={command}\nEnvironment={MANAGED_ENV}=1\n"
        f"Environment=PATH={os.environ.get('PATH', '/usr/bin:/bin')}\n"
        "Restart=always\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return ["systemctl", "--user", "enable", "--now", "mmm-serve"]


def uninstall_login() -> list[str]:
    """Removes the agent/unit; returns the command that stops the managed copy."""
    if sys.platform == "darwin":
        _launch_agent().unlink(missing_ok=True)
        return ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]
    subprocess.run(["systemctl", "--user", "disable", "mmm-serve"], capture_output=True)
    _systemd_unit().unlink(missing_ok=True)
    return ["systemctl", "--user", "stop", "mmm-serve"]


def _quote_systemd(arg: str) -> str:
    return f'"{arg}"' if re.search(r"\s", arg) else arg


def spawn_detached(port: int, profile: str | None) -> None:
    subprocess.Popen(
        _serve_argv(port, profile),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={k: v for k, v in os.environ.items() if k != MANAGED_ENV},
    )


# ----------------------------------------------------------------- daemon


class Daemon:
    """Connection, rules and the HTTP server. ``start`` returns once it listens."""

    def __init__(
        self,
        android: Android,
        *,
        port: int = DEFAULT_UI_PORT,
        profile: str | None = None,
        rules_file: Path | None = None,
        state: Path | None = None,
        secret: str | None = None,
        write_info: bool = True,
    ) -> None:
        self.android = android
        self.port = port
        self.profile = profile
        self.rules_file = rules_file or rules_module.rules_path()
        self.state = state or state_dir()
        self.state.mkdir(parents=True, exist_ok=True)
        self.secret = secret or load_secret()
        self.write_info = write_info
        self.managed = os.environ.get(MANAGED_ENV) == "1"
        self.started = time.time()
        self.link: dict[str, Any] = {"connected": False, "error": None, "latency_ms": None}
        self.rules = self._load_rules()
        self.runs: collections.deque[dict] = collections.deque(maxlen=RUNS_KEPT)
        self._load_runs()
        self._last_event = self._load_last_event()
        self._event_lock = threading.Lock()
        self._listeners: list[queue.Queue] = []
        self._listeners_lock = threading.Lock()
        self._handles: dict[int, Any] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mmm-rule")
        self._stop = threading.Event()
        self.httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.httpd = self._bind()
        self.port = self.httpd.server_address[1]
        self.httpd.mmm = self  # type: ignore[attr-defined]
        threading.Thread(target=self.httpd.serve_forever, name="mmm-http", daemon=True).start()
        events = self.android.events()
        self.android.subscribe([SMS_ACTION], name=SUBSCRIPTION, durable=True)
        threading.Thread(target=self._event_loop, args=(events,), name="mmm-sms", daemon=True).start()
        threading.Thread(target=self._ping_loop, name="mmm-ping", daemon=True).start()
        if self.write_info:
            _write_private(
                info_path(),
                json.dumps({"pid": os.getpid(), "port": self.port, "started": self.started,
                            "managed": self.managed}),
            )
        log.info("serving on http://127.0.0.1:%s (%s)", self.port, "managed" if self.managed else "manual")

    def _bind(self) -> ThreadingHTTPServer:
        # A copy handing over to launchd/systemd may still hold the port for a moment.
        deadline = time.monotonic() + 10
        while True:
            try:
                return ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def stop(self) -> None:
        self._stop.set()
        self._release()

    def _release(self) -> None:
        """Frees the port, serve.json and the connection without ending wait()."""
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        with self._listeners_lock:
            for listener in self._listeners:
                listener.put(None)
        self._pool.shutdown(wait=False)
        if self.write_info:
            info = running()
            if info and int(info.get("pid", 0)) == os.getpid():
                info_path().unlink(missing_ok=True)
        self.android.close()

    def wait(self) -> None:
        try:
            while not self._stop.wait(1):
                pass
        except KeyboardInterrupt:
            pass

    # ------------------------------------------------------------- events

    def _event_loop(self, events) -> None:
        for event in events:
            if self._stop.is_set():
                return
            if event.sub != SUBSCRIPTION:
                continue
            try:
                self.handle_sms_event(event.event_id, event.payload)
            except Exception:
                log.exception("could not handle event %s", event.event_id)

    def handle_sms_event(self, event_id: int, payload: dict) -> None:
        # The id is stored before the rule runs: a crash costs one run, never a repeat.
        with self._event_lock:
            if event_id <= self._last_event:
                log.info("event %s already handled; skipped", event_id)
                return
            self._last_event = event_id
            _write_private(self.state / "serve-state.json", json.dumps({"last_event_id": event_id}))
        sender, text = sms.decode(self.android, payload)
        log.info("sms from %s: %d chars", sender, len(text))
        self.broadcast("sms", {"sender": sender, "text": text, "ts": int(time.time() * 1000)})
        rule = rules_module.match(self.rules, text)
        if rule is None:
            return
        if not rules_module.sender_allowed(rule, sender):
            self._record(rule, sender, text, None, blocked=True)
            return
        self._pool.submit(self._run_rule, rule, sender, text)

    def _run_rule(self, rule: rules_module.Rule, sender: str, text: str) -> None:
        result = rules_module.run(rule, text, sender)
        replied = False
        if rule.reply and result.exit == 0:
            reply = rules_module.reply_text(result.stdout)
            if reply:
                try:
                    sms.send(self.android, sender, reply)
                    replied = True
                except MmmError as error:
                    log.warning("reply to %s failed: %s", sender, error)
        self._record(rule, sender, text, result, replied=replied)

    def _record(self, rule, sender, text, result, *, blocked=False, replied=False, test=False) -> dict:
        entry = {
            "ts": int(time.time() * 1000),
            "sender": sender,
            "trigger": rules_module.first_word(text),
            "rule": rule.trigger,
            "blocked": blocked,
            "test": test,
            "exit": None if result is None else result.exit,
            "timed_out": bool(result and result.timed_out),
            "ms": None if result is None else result.ms,
            "stdout": "" if result is None else result.stdout[-2000:],
            "stderr": "" if result is None else result.stderr[-2000:],
            "replied": replied,
        }
        self.runs.append(entry)
        path = self.state / "runs.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        if len(self.runs) == RUNS_KEPT and path.stat().st_size > 4 * 1024 * 1024:
            _write_private(path, "".join(json.dumps(r) + "\n" for r in self.runs))
        log.info("rule %r for %s: exit %s%s", rule.trigger, sender, entry["exit"],
                 " (blocked sender)" if blocked else "")
        self.broadcast("run", entry)
        return entry

    def test_rule(self, trigger: str, text: str, sender: str) -> dict:
        """Runs a rule exactly as an SMS would, but never replies."""
        rule = next((r for r in self.rules if r.trigger == trigger), None)
        if rule is None:
            raise ValueError(f"no rule {trigger!r}")
        return self._record(rule, sender, text, rules_module.run(rule, text, sender), test=True)

    def _ping_loop(self) -> None:
        while not self._stop.is_set():
            before = time.monotonic()
            try:
                self.android.ping()
                self.link.update(connected=True, error=None,
                                 latency_ms=int((time.monotonic() - before) * 1000))
            except Exception as error:
                self.link.update(connected=False, error=str(error), latency_ms=None)
            self.broadcast("link", dict(self.link))
            self._stop.wait(30)

    # -------------------------------------------------------------- state

    def _load_rules(self) -> list[rules_module.Rule]:
        try:
            return rules_module.load(self.rules_file)
        except (ValueError, OSError) as error:
            log.error("rules not loaded: %s", error)
            return []

    def save_rules(self, data: Any) -> list[rules_module.Rule]:
        parsed = rules_module.parse(data)
        rules_module.save(parsed, self.rules_file)
        self.rules = parsed
        return parsed

    def _load_runs(self) -> None:
        path = self.state / "runs.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-RUNS_KEPT:]:
                try:
                    self.runs.append(json.loads(line))
                except ValueError:
                    pass

    def _load_last_event(self) -> int:
        try:
            return int(json.loads((self.state / "serve-state.json").read_text())["last_event_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    # -------------------------------------------------------------- live

    def broadcast(self, kind: str, data: Any) -> None:
        with self._listeners_lock:
            for listener in self._listeners:
                listener.put((kind, data))

    def listen(self) -> queue.Queue:
        listener: queue.Queue = queue.Queue()
        with self._listeners_lock:
            self._listeners.append(listener)
        return listener

    def unlisten(self, listener: queue.Queue) -> None:
        with self._listeners_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    # ------------------------------------------------------------ status

    def mac_status(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        runs_today = [r for r in self.runs
                      if time.strftime("%Y-%m-%d", time.localtime(r["ts"] / 1000)) == today and not r["test"]]
        log_file = self.state / "serve.log"
        tail: list[str] = []
        if log_file.exists():
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
        return {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "uptime_s": int(time.time() - self.started),
            "ui": f"http://127.0.0.1:{self.port}",
            "managed": self.managed,
            "profile": self.android.profile.name,
            "transport": self.android._transport.describe(),
            "link": dict(self.link, connected=self.android.connected and self.link["connected"]),
            "login": {"supported": bool(login_supported()), "enabled": login_enabled()},
            "rules": len(self.rules),
            "runs_today": len(runs_today),
            "last_run": self.runs[-1] if self.runs else None,
            "log": tail,
        }

    def set_login(self, on: bool) -> dict:
        """Hands the daemon over to launchd/systemd, or back to a plain process."""
        if on:
            start = install_login(self.port, self.profile)
            if not self.managed:
                # The managed copy takes over the port once this one is gone.
                threading.Thread(target=self._handover, args=(start,), daemon=True).start()
            return {"enabled": True, "handover": not self.managed}
        stop = uninstall_login()
        if self.managed:
            spawn_detached(self.port, self.profile)
            threading.Thread(target=lambda: (time.sleep(0.5), subprocess.run(stop, capture_output=True)),
                             daemon=True).start()
            return {"enabled": False, "handover": True}
        return {"enabled": False, "handover": False}

    def _handover(self, start: list[str]) -> None:
        # Not stop(): that ends wait(), and the main thread would exit the process
        # before the system copy was ever started.
        time.sleep(0.5)
        self._release()
        done = subprocess.run(start, capture_output=True, text=True)
        if done.returncode != 0:
            log.error("handover failed: %s exited %s: %s", start[0], done.returncode,
                      (done.stderr or done.stdout).strip())
        else:
            log.info("handed over to the system service")
        os._exit(0)

    # ------------------------------------------------------------ console

    def to_page(self, value: Any) -> Any:
        """Plain JSON for the page; Java objects become handle chips it can send back."""
        if isinstance(value, JavaObject):
            self._handles[value.handle] = value
            return {"$h": value.handle, "cls": value.cls}
        if isinstance(value, JavaClassRef):
            return {"$cls": value.name}
        if isinstance(value, dict):
            return {str(k): self.to_page(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.to_page(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return json_default(value)

    def from_page(self, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) <= {"$h", "cls"} and "$h" in value:
                handle = int(value["$h"])
                if handle not in self._handles:
                    raise ValueError(f"handle #{handle} is unknown here (it may predate a reconnect)")
                return self._handles[handle]
            if value == {"$ctx": True}:
                return self.android.context
            return {k: self.from_page(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.from_page(v) for v in value]
        return value

    def console(self, body: dict) -> Any:
        op = body.get("op")
        a = self.android
        if op == "cls":
            return self.to_page(a.cls(str(body["target"])))
        target = body.get("target")
        target = self.from_page(target) if isinstance(target, dict) else a.cls(str(target))
        member = str(body.get("member") or "")
        if op == "call":
            args = [self.from_page(x) for x in body.get("args") or []]
            return self.to_page(a.call(target, member, *args, sig=body.get("sig") or None,
                                       main=bool(body.get("main"))))
        if op == "get":
            return self.to_page(a.get(target, member))
        if op == "set":
            a.set(target, member, self.from_page(body.get("value")))
            return True
        if op == "expand":
            return self.to_page(a.expand(target))
        raise ValueError(f"unknown console op {op!r}")

    def release(self, handle: int) -> bool:
        obj = self._handles.pop(int(handle), None)
        if obj is not None:
            obj.release()
        return obj is not None


# -------------------------------------------------------------------- http


class HttpError(Exception):
    def __init__(self, status: int, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.status, self.kind = status, kind


class Handler(BaseHTTPRequestHandler):
    server_version = "mmm-serve"
    protocol_version = "HTTP/1.1"

    @property
    def d(self) -> Daemon:
        return self.server.mmm  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
        log.debug("http: " + format, *args)

    # ------------------------------------------------------------ plumbing

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.d.port}", f"localhost:{self.d.port}")

    def _secret_ok(self, query: dict) -> bool:
        given = self.headers.get("X-MMM-Secret") or (query.get("s") or [""])[0]
        return hmac.compare_digest(given.encode(), self.d.secret.encode())

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise HttpError(413, "request too large")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError as error:
            raise HttpError(400, f"bad JSON: {error}") from error

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._send_json(403, {"type": "forbidden", "message": "bad Host header"})
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not url.path.startswith("/api/"):
            self._static(url.path)
            return
        if not self._secret_ok(query):
            self._send_json(403, {"type": "forbidden", "message": "missing or wrong secret"})
            return
        try:
            if url.path == "/api/events" and method == "GET":
                self._events()
                return
            self._send_json(200, self._route(method, url.path, query))
        except HttpError as error:
            self._send_json(error.status, {"type": error.kind, "message": str(error)})
        except AndroidError as error:
            self._send_json(502, {"type": error.type, "message": str(error)})
        except MmmError as error:
            self._send_json(503, {"type": "unreachable", "message": str(error)})
        except (ValueError, KeyError, TypeError) as error:
            self._send_json(400, {"type": "bad_request", "message": str(error)})
        except BrokenPipeError:
            pass

    def _static(self, path: str) -> None:
        name = {"/": "index.html", "/favicon.ico": "favicon.png"}.get(path, path.lstrip("/"))
        if name not in STATIC:
            self._send_json(404, {"type": "not_found", "message": path})
            return
        body = resources.files("mmm").joinpath("ui", name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC[name])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        listener = self.d.listen()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b"event: link\ndata: " + json.dumps(self.d.link).encode() + b"\n\n")
            self.wfile.flush()
            while True:
                try:
                    item = listener.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if item is None:
                    return
                kind, data = item
                self.wfile.write(f"event: {kind}\ndata: ".encode()
                                 + json.dumps(data, default=json_default).encode() + b"\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.d.unlisten(listener)
            self.close_connection = True

    # -------------------------------------------------------------- routes

    def _route(self, method: str, path: str, query: dict) -> Any:
        d = self.d
        if (method, path) == ("GET", "/api/mac"):
            return d.mac_status()
        if (method, path) == ("POST", "/api/mac/login"):
            return d.set_login(bool(self._body().get("on")))
        if (method, path) == ("GET", "/api/phone"):
            info = d.android.ping()
            return {"info": info, "status": d.to_page(phone.facade(d.android, "deviceStatus"))}
        if path == "/api/mmm-settings":
            if method == "GET":
                return d.android.settings()
            body = self._body()
            key = str(body["key"])
            result = d.android.set_setting(key, body.get("value"))
            if key in ("port", "tailnet"):
                phone.restart_listener(d.android)
            return result
        if path == "/api/sms":
            if method == "GET":
                limit = max(1, min(int((query.get("limit") or ["200"])[0]), 1000))
                return d.to_page(sms.recent(d.android, limit))
            body = self._body()
            to, text = str(body.get("to") or "").strip(), str(body.get("body") or "")
            if not to or not text:
                raise HttpError(400, "to and body are both required")
            return {"sent": sms.send(d.android, to, text)}
        match = re.fullmatch(r"/api/settings/(system|secure|global)", path)
        if match:
            if method == "GET":
                return settings.read(d.android, match.group(1))
            body = self._body()
            return {"ok": settings.write(d.android, match.group(1), str(body["name"]), str(body["value"]))}
        if path == "/api/rules":
            if method == "PUT":
                body = self._body()
                # The page edits a command as one line; split it like a shell would, but
                # it is only ever run as the resulting argv, never through a shell.
                for raw in body.get("rules") or []:
                    if isinstance(raw.get("command"), str):
                        raw["command"] = shlex.split(raw["command"])
                d.save_rules(body)
            return {"rules": [dict(vars(r), command_line=shlex.join(r.command)) for r in d.rules],
                    "path": _tilde(d.rules_file)}
        if (method, path) == ("POST", "/api/rules/test"):
            body = self._body()
            return d.test_rule(str(body["trigger"]), str(body.get("text") or ""),
                               str(body.get("sender") or "+15550100000"))
        if (method, path) == ("GET", "/api/runs"):
            limit = max(1, min(int((query.get("limit") or ["100"])[0]), RUNS_KEPT))
            return list(d.runs)[-limit:][::-1]
        if (method, path) == ("POST", "/api/console"):
            return {"value": d.console(self._body())}
        if (method, path) == ("POST", "/api/console/release"):
            return {"released": d.release(int(self._body()["h"]))}
        raise HttpError(404, f"no route {method} {path}", "not_found")


# --------------------------------------------------------------------- run


def setup_logging() -> None:
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        folder / "serve.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("mmm.serve")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr.isatty():
        root.addHandler(logging.StreamHandler())


def serve(profile: str | None, port: int) -> int:
    """Entry point for ``mmm serve``."""
    setup_logging()
    # During a handover the old copy is still shutting down for a moment.
    deadline = time.monotonic() + 10
    other = running()
    while other and int(other["pid"]) != os.getpid() and time.monotonic() < deadline:
        time.sleep(0.5)
        other = running()
    if other and int(other["pid"]) != os.getpid():
        print(f"mmm serve is already running (pid {other['pid']}, port {other['port']})", file=sys.stderr)
        return 1
    android = Android(profile)
    daemon = Daemon(android, port=port, profile=profile)
    daemon.start()
    print(f"mmm serve: http://127.0.0.1:{daemon.port}  (open it with `mmm ui`)", flush=True)
    import signal

    signal.signal(signal.SIGTERM, lambda *_: daemon._stop.set())
    daemon.wait()
    daemon.stop()
    return 0


def open_ui(profile: str | None, port: int, open_browser: bool = True) -> int:
    """Entry point for ``mmm ui``: start the daemon if needed, then open the page."""
    info = running()
    if info is None:
        spawn_detached(port, profile)
        deadline = time.monotonic() + 15
        while info is None and time.monotonic() < deadline:
            time.sleep(0.3)
            info = running()
        if info is None:
            print(f"mmm: the daemon did not start; see {state_dir() / 'serve.log'}", file=sys.stderr)
            return 1
    url = f"http://127.0.0.1:{info['port']}/#s={load_secret()}"
    if open_browser:
        import webbrowser

        webbrowser.open(url)
        print(f"opened http://127.0.0.1:{info['port']}/")
    else:
        print(url)
    return 0
