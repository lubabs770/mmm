"""The Python side of MMM: ``Android``, and the proxy objects it hands out.

    from mmm import Android
    a = Android()
    tm = a.context.getSystemService("phone")
    print(tm.getSignalStrength().getLevel())

Every attribute lookup on a :class:`JavaObject` turns into a call on the phone, so
the whole Android API is reachable without anything being wrapped by hand.
"""

from __future__ import annotations

import base64
import collections
import itertools
import json
import math
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from . import config as config_module
from .config import Profile
from .transport import Transport, for_profile

__all__ = [
    "Android",
    "AndroidError",
    "Event",
    "JavaObject",
    "MmmError",
    "NotConnected",
]

RELEASE_BATCH = 32


class MmmError(RuntimeError):
    """Anything that went wrong on this side of the socket."""


class NotConnected(MmmError):
    pass


class AndroidError(MmmError):
    """An exception raised inside the app, carried across the wire."""

    def __init__(self, type: str, message: str, java_stack: str | None = None) -> None:
        super().__init__(f"{type}: {message}" if message else type)
        self.type = type
        self.message = message
        self.java_stack = java_stack


@dataclass
class Event:
    sub: str
    event_id: int
    ts: int
    payload: Any


class _Pending:
    __slots__ = ("done", "value", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class Android:
    """A connection to one phone."""

    def __init__(
        self,
        profile: str | Profile | None = None,
        *,
        token: str | None = None,
        host: str | None = None,
        port: int | None = None,
        transport: str | None = None,
        config_file: str | None = None,
        connect: bool = True,
        reconnect: bool = True,
        timeout: float = 30.0,
    ) -> None:
        if isinstance(profile, Profile):
            self.profile = profile
        elif profile is None and token:
            # Everything needed was passed in; no config file has to exist.
            self.profile = Profile(
                token=token,
                transport=transport or "local",
                host=host or config_module.LOOPBACK,
                port=port or config_module.DEFAULT_PORT,
            )
        else:
            loaded = config_module.load(
                config_module.Path(config_file).expanduser() if config_file else None
            )
            self.profile = loaded.profile(profile)
        if token:
            self.profile.token = token
        if host:
            self.profile.host = host
        if port:
            self.profile.port = port
            self.profile.local_port = self.profile.local_port or port
        if transport:
            self.profile.transport = transport
        if not self.profile.token:
            raise MmmError("no token; pair this client first with `mmm pair <token>`")

        self.timeout = timeout
        self.info: dict[str, Any] = {}

        self._transport: Transport = for_profile(self.profile)
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._handlers: dict[str, Callable[[Any], None]] = {}
        self._proxy_handlers: dict[str, Callable[[str, list], Any]] = {}
        self._subscriptions: dict[str, dict] = {}
        # A deque because __del__ appends from whatever thread the GC runs on, and
        # its append and popleft are atomic without a lock a finalizer could deadlock on.
        self._releases: collections.deque[int] = collections.deque()
        # Bumped on every connection. Handles belong to one session, so an object from
        # an earlier generation must never be sent to (or released on) a later one.
        self._generation = 0
        # Events and proxy calls are handled here, off the reader thread, so a
        # handler may call the phone itself without waiting on its own reply.
        self._inbox: queue.Queue[dict | None] = queue.Queue()
        self._dispatcher: threading.Thread | None = None
        self._queue: queue.Queue[Event] | None = None
        self._closed = False
        self._connected = threading.Event()
        self._reconnect = reconnect
        # Set by the reader as soon as the phone rejects the token, so the hang-up
        # that follows is not mistaken for a dropped link worth reconnecting.
        self._rejected = False
        self._context: JavaObject | None = None

        if connect:
            self.connect()

    # ------------------------------------------------------------- lifecycle

    def connect(self, resubscribe: bool = True) -> None:
        with self._state_lock:
            if self._socket is not None:
                return
            connection = self._transport.connect()
            self._socket = connection
            self._closed = False
            self._rejected = False
            self._generation += 1
            self._ensure_dispatcher()
            self._reader = threading.Thread(
                target=self._read_loop, args=(connection,), name="mmm-reader", daemon=True
            )
            self._reader.start()
        try:
            self.info = self._request("auth", token=self.profile.token)
        except AndroidError as error:
            if error.type == "unauthorized":
                # Retrying a revoked token only fills the phone's activity log.
                self.close()
            raise
        self._connected.set()
        if resubscribe and self._subscriptions:
            # Durable subscriptions replay their backlog on resubscribe, so an SMS
            # that arrived while the link was down still gets delivered.
            for name, spec in list(self._subscriptions.items()):
                self._request(
                    "subscribe",
                    name=name,
                    action=spec["actions"],
                    durable=spec["durable"],
                )

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._connected.clear()
            connection, self._socket = self._socket, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._transport.close()
        self._fail_pending(NotConnected("connection closed"))
        # Let this dispatcher finish; a later connect() starts a fresh one.
        self._inbox.put(None)
        self._dispatcher = None

    def __enter__(self) -> "Android":
        if self._socket is None:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._socket is not None and not self._closed

    @property
    def closed(self) -> bool:
        """True after close(): this object will not reconnect by itself any more."""
        return self._closed

    def wait_connected(self, timeout: float) -> bool:
        """Waits for a reconnect in progress; True if the link is up."""
        return self._connected.wait(timeout) and self.connected

    # ----------------------------------------------------------------- calls

    def _request(self, op: str, **fields: Any) -> Any:
        return self._request_fields(op, fields)

    def _request_fields(self, op: str, fields: dict) -> Any:
        if self._socket is None:
            raise NotConnected("not connected; call connect() first")
        request_id = next(self._ids)
        pending = _Pending()
        self._pending[request_id] = pending
        payload = {"op": op, "id": request_id}
        payload.update({key: value for key, value in fields.items() if value is not None})
        try:
            self._send(payload)
        except Exception:
            self._pending.pop(request_id, None)
            raise
        if not pending.done.wait(self.timeout):
            self._pending.pop(request_id, None)
            raise TimeoutError(f"no reply to {op} after {self.timeout}s")
        self._pending.pop(request_id, None)
        if pending.error is not None:
            raise pending.error
        return pending.value

    def _send(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        connection = self._socket
        if connection is None:
            raise NotConnected("not connected")
        with self._write_lock:
            connection.sendall(line.encode("utf-8"))

    def ping(self) -> dict:
        """Server info: version, port, root, priv-app, connected clients."""
        self._flush_releases(force=True)
        return self._request("ping")

    def cls(self, name: str) -> "JavaObject":
        """A handle to a class, for static calls and constants."""
        result = self._request("cls", name=name)
        return self._from_wire(result)

    def new(self, class_name: str, *args: Any, sig: list[str] | None = None) -> "JavaObject":
        self._flush_releases()
        result = self._request(
            "new", cls=class_name, args=[self._to_wire(a) for a in args], sig=sig
        )
        return self._from_wire(result)

    @property
    def context(self) -> "JavaObject":
        """The app's ``Context``, the way into most of the framework."""
        if self._context is None:
            self._context = self._from_wire(self._request("context"))
        return self._context

    def call(
        self,
        target: Any,
        method: str,
        *args: Any,
        sig: list[str] | None = None,
        main: bool = False,
        expand: bool = False,
    ) -> Any:
        """Calls [method] on a handle, a class name, or a :class:`JavaObject`."""
        self._flush_releases()
        fields: dict[str, Any] = {
            "target": self._to_wire(target),
            "method": method,
            "args": [self._to_wire(a) for a in args],
        }
        if sig:
            fields["sig"] = sig
        if main:
            fields["main"] = True
        if expand:
            fields["$expand"] = True
        return self._from_wire(self._request_fields("call", fields))

    def get(self, target: Any, field: str) -> Any:
        return self._from_wire(
            self._request("get", target=self._to_wire(target), field=field)
        )

    def set(self, target: Any, field: str, value: Any) -> None:
        self._request(
            "set", target=self._to_wire(target), field=field, value=self._to_wire(value)
        )

    def expand(self, target: Any) -> Any:
        """A JSON snapshot of [target] instead of a handle."""
        return self._request_fields(
            "expand", {"target": self._to_wire(target), "$expand": True}
        )

    def settings(self) -> dict:
        """The app's own settings, which are RPC-settable like everything else."""
        bridge = self.cls("lubabs770.mmm.modemizer.settings.SettingsBridge")
        return self.call(bridge, "get", self.context)

    def set_setting(self, key: str, value: Any) -> dict:
        bridge = self.cls("lubabs770.mmm.modemizer.settings.SettingsBridge")
        return self.call(bridge, "set", self.context, key, value)

    # ----------------------------------------------------------------- events

    def subscribe(
        self,
        actions: str | list[str],
        name: str | None = None,
        durable: bool | str = False,
        handler: Callable[[Any], None] | None = None,
    ) -> str:
        """Registers a broadcast subscription and returns its name."""
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(durable, str):
            name, durable = durable, True
        if not name:
            name = f"sub{next(self._ids)}"
        self._subscriptions[name] = {"actions": list(actions), "durable": bool(durable)}
        if handler is not None:
            self._handlers[name] = handler
        self._request("subscribe", name=name, action=list(actions), durable=bool(durable))
        return name

    def unsubscribe(self, name: str) -> bool:
        self._subscriptions.pop(name, None)
        self._handlers.pop(name, None)
        return bool(self._request("unsubscribe", name=name))

    def on(
        self,
        *actions: str,
        durable: str | None = None,
        name: str | None = None,
    ) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Decorator form: ``@a.on("…SMS_RECEIVED", durable="sms")``."""

        def decorate(function: Callable[[Any], None]) -> Callable[[Any], None]:
            self.subscribe(
                list(actions),
                name=name or durable or function.__name__,
                durable=bool(durable),
                handler=function,
            )
            return function

        return decorate

    def proxy(self, interfaces: str | list[str], handler: Callable[[str, list], Any]) -> "JavaObject":
        """A Java object implementing [interfaces] whose calls go to [handler].

        The handler is called as ``handler(method_name, args)``. Methods that return
        a value run on a thread of their own, so the handler may call the phone even
        if that call fires this listener again; whatever it returns is sent back.
        ``void`` methods run in order on the event thread. The phone waits only
        ``proxy_reply_timeout_ms`` for a value; and a callback the framework makes on
        its main thread blocks that thread while it waits, so a handler for one must
        not make ``main=True`` calls.
        """
        if isinstance(interfaces, str):
            interfaces = [interfaces]
        result = self._request("proxy", interfaces=list(interfaces))
        # The app picks the subscription name and tells us; events arrive under it.
        listener = JavaObject(
            self, int(result["$h"]), result.get("cls", interfaces[0]), self._generation
        )
        self._proxy_handlers[result["sub"]] = handler
        return listener

    def events(self, timeout: float | None = None) -> Iterator[Event]:
        """Iterates over every event, for pipelines and ``mmm watch``.

        The queue starts filling as soon as this is called, not at the first
        ``next()``, so nothing is lost between the two.
        """
        if self._queue is None:
            self._queue = queue.Queue()
        return self._event_stream(timeout)

    def _event_stream(self, timeout: float | None) -> Iterator[Event]:
        assert self._queue is not None
        while not self._closed:
            try:
                yield self._queue.get(timeout=timeout if timeout else 1.0)
            except queue.Empty:
                if timeout:
                    return

    def run(self) -> None:
        """Blocks, dispatching events to handlers, until interrupted or closed."""
        try:
            while not self._closed:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    # ----------------------------------------------------------------- handles

    def _queue_release(self, handle: int, generation: int, flush: bool = True) -> None:
        """Queues a handle for release; stale ones are dropped, never sent.

        ``flush`` is False from ``__del__``: a finalizer can run in the middle of a
        send on the same thread, and must not try to send itself.
        """
        if self._closed or generation != self._generation:
            return
        self._releases.append(handle)
        if flush and len(self._releases) >= RELEASE_BATCH:
            self._flush_releases()

    def _flush_releases(self, force: bool = False) -> None:
        if not self._releases or self._socket is None:
            return
        if not force and len(self._releases) < RELEASE_BATCH:
            return
        batch: list[int] = []
        while True:
            try:
                batch.append(self._releases.popleft())
            except IndexError:
                break
        if not batch:
            return
        try:
            # Fire and forget: the reply carries nothing worth waiting for.
            self._send({"op": "release", "handles": batch})
        except Exception:
            pass

    # -------------------------------------------------------------- marshalling

    def _to_wire(self, value: Any) -> Any:
        if isinstance(value, JavaObject):
            if value._generation != self._generation:
                raise NotConnected(
                    f"{value!r} came from an earlier connection; handles do not survive "
                    "a reconnect, so look the object up again"
                )
            return {"$h": value.handle}
        if isinstance(value, JavaClassRef):
            # A class that came back from getClass() is usable as a target again.
            return {"$cls": value.name}
        if isinstance(value, Member):
            return self._to_wire(value.value)
        if isinstance(value, float) and not math.isfinite(value):
            # json.dumps would write a bare NaN, which is not JSON.
            return {"$float": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
        if isinstance(value, (bytes, bytearray)):
            return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
        if isinstance(value, dict):
            return {str(k): self._to_wire(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_wire(v) for v in value]
        return value

    def _from_wire(self, value: Any) -> Any:
        if isinstance(value, dict):
            if "$h" in value:
                return JavaObject(
                    self, int(value["$h"]), value.get("cls", "java.lang.Object"), self._generation
                )
            if "$b64" in value:
                return base64.b64decode(value["$b64"])
            if "$float" in value:
                return float(value["$float"])
            if "$cls" in value:
                return JavaClassRef(value["$cls"])
            return {key: self._from_wire(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._from_wire(item) for item in value]
        return value

    # ---------------------------------------------------------------- reading

    def _read_loop(self, connection: socket.socket) -> None:
        stream = connection.makefile("r", encoding="utf-8", newline="\n")
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                self._dispatch(message)
        except OSError:
            pass
        finally:
            self._on_disconnect(connection)

    def _dispatch(self, message: dict) -> None:
        op = message.get("op")
        if op == "event":
            if message.get("needs_reply") and message.get("sub") in self._proxy_handlers:
                # Not queued behind other handlers: the phone is blocked waiting, and a
                # nested callback from inside a handler must not wait on the outer one.
                threading.Thread(
                    target=self._run_handler, args=(message,), name="mmm-proxy", daemon=True
                ).start()
            else:
                self._inbox.put(message)
            return
        if op == "error" and message.get("type") == "unauthorized":
            self._rejected = True
        pending = self._pending.get(message.get("id"))
        if pending is None:
            return
        if op == "error":
            pending.error = AndroidError(
                message.get("type", "error"),
                message.get("message", ""),
                message.get("java_stack"),
            )
        else:
            pending.value = message.get("value")
        pending.done.set()

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher is not None and self._dispatcher.is_alive():
            return
        self._inbox = queue.Queue()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, args=(self._inbox,), name="mmm-events", daemon=True
        )
        self._dispatcher.start()

    def _dispatch_loop(self, inbox: "queue.Queue[dict | None]") -> None:
        """Runs handlers one event at a time, in arrival order."""
        while True:
            message = inbox.get()
            if message is None:
                return
            self._run_handler(message)

    def _run_handler(self, message: dict) -> None:
        try:
            self._on_event(message)
        except Exception as error:  # nothing here may stop later events
            self._log_handler_error(str(message.get("sub", "")), error)

    def _on_event(self, message: dict) -> None:
        sub = message.get("sub", "")
        payload = message.get("payload")
        handler = self._proxy_handlers.get(sub)
        if handler is not None:
            method = (payload or {}).get("method", "")
            args = [self._from_wire(a) for a in (payload or {}).get("args", [])]
            result = None
            try:
                result = handler(method, args)
            except Exception as error:  # a listener must never kill the connection
                result = None
                self._log_handler_error(sub, error)
            if message.get("needs_reply"):
                try:
                    self._send(
                        {
                            "op": "reply",
                            "event_id": int(message.get("event_id", 0)),
                            "value": self._to_wire(result),
                        }
                    )
                except Exception:
                    pass
            return

        # Handlers and the iterator see the same decoded payload: base64 is already
        # bytes, nested handles are already JavaObjects.
        event = Event(
            sub,
            int(message.get("event_id", 0)),
            int(message.get("ts", 0)),
            self._from_wire(payload),
        )
        broadcast = self._handlers.get(sub)
        if broadcast is not None:
            try:
                broadcast(event.payload)
            except Exception as error:
                self._log_handler_error(sub, error)
        if self._queue is not None:
            self._queue.put(event)

    def _log_handler_error(self, sub: str, error: BaseException) -> None:
        import traceback

        print(f"mmm: handler for {sub} raised {error!r}", flush=True)
        traceback.print_exc()

    def _on_disconnect(self, connection: socket.socket) -> None:
        with self._state_lock:
            if self._socket is not connection:
                return
            self._socket = None
            self._connected.clear()
            # Handles belong to a session, so none of them survive this. Moving the
            # generation on now, not at the next connect, also stops objects collected
            # in between from queueing their old handles for the new session.
            self._generation += 1
            self._context = None
            self._releases.clear()
        self._fail_pending(NotConnected("connection lost"))
        if self._closed or self._rejected or not self._reconnect:
            return
        threading.Thread(target=self._reconnect_loop, name="mmm-reconnect", daemon=True).start()

    def _reconnect_loop(self) -> None:
        delay = 0.5
        while not self._closed and self._socket is None:
            time.sleep(delay)
            delay = min(delay * 2, 15.0)
            try:
                self.connect()
                return
            except Exception:
                continue

    def _fail_pending(self, error: BaseException) -> None:
        for pending in list(self._pending.values()):
            if not pending.done.is_set():
                pending.error = error
                pending.done.set()


class JavaClassRef:
    """A class that crossed the wire as a name rather than a handle."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"<java class {self.name}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JavaClassRef) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)


class JavaObject:
    """A Java object living in the app, referenced by handle."""

    __slots__ = ("_client", "handle", "cls", "_generation", "__weakref__")

    def __init__(self, client: Android, handle: int, cls: str, generation: int | None = None) -> None:
        self._client = client
        self.handle = handle
        self.cls = cls
        #: The connection this handle belongs to; see ``Android._generation``.
        self._generation = client._generation if generation is None else generation

    def __getattr__(self, name: str) -> "Member":
        if name.startswith("_"):
            raise AttributeError(name)
        return Member(self, name)

    def call(self, method: str, *args: Any, **options: Any) -> Any:
        return self._client.call(self, method, *args, **options)

    def get(self, field: str) -> Any:
        return self._client.get(self, field)

    def set(self, field: str, value: Any) -> None:
        self._client.set(self, field, value)

    def expand(self) -> Any:
        """A JSON snapshot of this object: a Bundle, an Intent or a plain POJO."""
        return self._client.expand(self)

    def release(self) -> None:
        self._client._queue_release(self.handle, self._generation)

    def __repr__(self) -> str:
        return f"<{self.cls} #{self.handle}>"

    def __del__(self) -> None:
        try:
            self._client._queue_release(self.handle, self._generation, flush=False)
        except Exception:
            pass


class Member:
    """A name on a Java object: call it for a method, read it for a field."""

    __slots__ = ("_owner", "_name", "_cached", "_read")

    def __init__(self, owner: JavaObject, name: str) -> None:
        self._owner = owner
        self._name = name
        self._cached: Any = None
        self._read = False

    def __call__(self, *args: Any, **options: Any) -> Any:
        return self._owner.call(self._name, *args, **options)

    @property
    def value(self) -> Any:
        if not self._read:
            self._cached = self._owner.get(self._name)
            self._read = True
        return self._cached

    def __repr__(self) -> str:
        return repr(self.value)

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)

    def __bool__(self) -> bool:
        return bool(self.value)

    def __eq__(self, other: object) -> bool:
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)


def json_default(item: Any) -> Any:
    """What ``json.dumps`` cannot write itself. Bytes go out as ``{"$b64": …}``, the
    same shape the protocol uses, so nothing is lost and no consumer parses repr."""
    if isinstance(item, JavaObject):
        return {"$h": item.handle, "cls": item.cls}
    if isinstance(item, (bytes, bytearray)):
        return {"$b64": base64.b64encode(bytes(item)).decode("ascii")}
    return repr(item)
