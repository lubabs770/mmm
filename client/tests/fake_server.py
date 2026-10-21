"""A fake MMM app: enough NDJSON to exercise the client without a phone."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable


class FakeServer:
    """Speaks the wire protocol on a loopback port and records what it was sent."""

    def __init__(self, handler: Callable[["FakeServer", dict], Any] | None = None) -> None:
        self.handler = handler or default_handler
        self.requests: list[dict] = []
        self.connections = 0
        self.token = "deadbeef"
        self._lock = threading.Lock()
        self._connection: socket.socket | None = None
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._closed = False
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    # ---------------------------------------------------------------- serving

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self.connections += 1
                self._connection = connection
            threading.Thread(target=self._session, args=(connection,), daemon=True).start()

    def _session(self, connection: socket.socket) -> None:
        stream = connection.makefile("r", encoding="utf-8", newline="\n")
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                request = json.loads(line)
                with self._lock:
                    self.requests.append(request)
                response = self.handler(self, request)
                if response is not None:
                    self.send(response, connection)
        except OSError:
            pass

    # ----------------------------------------------------------------- helpers

    def send(self, message: dict, connection: socket.socket | None = None) -> None:
        target = connection or self._connection
        if target is None:
            raise RuntimeError("nothing is connected yet")
        target.sendall((json.dumps(message) + "\n").encode("utf-8"))

    def event(self, sub: str, payload: Any, event_id: int = 1, needs_reply: bool = False) -> None:
        message = {
            "op": "event",
            "sub": sub,
            "event_id": event_id,
            "ts": 1700000000000,
            "payload": payload,
        }
        if needs_reply:
            message["needs_reply"] = True
        self.send(message)

    def ops(self) -> list[str]:
        with self._lock:
            return [request.get("op", "") for request in self.requests]

    def sent(self, op: str) -> list[dict]:
        with self._lock:
            return [request for request in self.requests if request.get("op") == op]

    def drop_connection(self) -> None:
        """Pretends Wi-Fi went away.

        The shutdown matters: `makefile` dups the descriptor, so closing the socket
        alone would leave the peer waiting instead of seeing the connection end.
        """
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def close(self) -> None:
        self._closed = True
        self.drop_connection()
        self._listener.close()

    def __enter__(self) -> "FakeServer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def result(request: dict, value: Any) -> dict:
    return {"op": "result", "id": request.get("id"), "value": value}


def failure(request: dict, type: str, message: str) -> dict:
    return {
        "op": "error",
        "id": request.get("id"),
        "type": type,
        "message": message,
        "java_stack": "stack",
    }


SERVER_INFO = {
    "server": "mmm",
    "version": "test",
    "protocol": 1,
    "port": 7357,
    "model": "Fake",
    "root": False,
    "priv_app": False,
}


def default_handler(server: FakeServer, request: dict) -> Any:
    """Answers the ops the tests do not care about in detail."""
    op = request.get("op")
    if op == "auth":
        if request.get("token") != server.token:
            return failure(request, "unauthorized", "unknown or revoked token")
        return result(request, dict(SERVER_INFO, client="test", scope={"full": True}))
    if op == "ping":
        return result(request, SERVER_INFO)
    if op == "cls":
        return result(
            request,
            {"$h": 100, "cls": "java.lang.Class", "name": request.get("name")},
        )
    if op == "context":
        return result(request, {"$h": 1, "cls": "android.app.Application"})
    if op == "new":
        return result(request, {"$h": 200, "cls": request.get("cls")})
    if op == "proxy":
        return result(
            request,
            {"$h": 300, "cls": (request.get("interfaces") or ["?"])[0], "sub": "proxy#1"},
        )
    if op == "subscribe":
        return result(
            request,
            {
                "subscribed": request.get("name"),
                "durable": bool(request.get("durable")),
                "replayed": 0,
            },
        )
    if op == "unsubscribe":
        return result(request, True)
    if op == "release":
        return result(request, len(request.get("handles") or []))
    if op == "get":
        return result(request, "field-value")
    if op == "set":
        return result(request, True)
    if op == "expand":
        return result(request, {"$pojo": "x", "a": 1})
    if op == "call":
        return result(request, "called")
    return failure(request, "IllegalArgumentException", f"unknown op {op!r}")
