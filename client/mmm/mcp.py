"""``mmm mcp`` — an MCP server over stdio, so an AI assistant can use the phone.

MCP (Model Context Protocol) is how assistants like Claude plug into tools. This
server runs wherever the client runs: in Termux on the phone, or on a computer with
a tunnel to it.

The curated tools go through the app's ``Facade`` class, which is exactly what the
scoped "assistant" token is allowed to reach. ``android_call`` is the escape hatch
and is only offered to a full-scope token — and the app enforces that regardless of
what this file does.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Callable

from . import __version__
from .client import Android, MmmError

PROTOCOL_VERSION = "2024-11-05"
FACADE = "lubabs770.mmm.modemizer.rpc.Facade"

#: How long a tool call waits for a dropped link to come back.
RECONNECT_WAIT = 10.0
#: events_wait blocks the whole server, so an assistant cannot ask for hours.
MAX_WAIT = 300.0

#: Subscription names an assistant can ask to wait on without knowing the action.
KNOWN_SUBSCRIPTIONS = {
    "sms": ["android.provider.Telephony.SMS_RECEIVED"],
    "calls": ["android.intent.action.PHONE_STATE"],
    "battery": ["android.intent.action.BATTERY_CHANGED"],
}


class Bridge:
    """Holds the connection and the per-subscription event buffers."""

    def __init__(self, profile: str | None = None) -> None:
        self.profile = profile
        self._android: Android | None = None
        self._lock = threading.Lock()
        self._buffers: dict[str, list[Any]] = {}
        self._arrived = threading.Condition()

    @property
    def android(self) -> Android:
        with self._lock:
            if self._android is None or self._android.closed:
                self._android = Android(self.profile)
            elif not self._android.wait_connected(RECONNECT_WAIT):
                # Keep the one connection and let it reconnect: a second one would
                # not carry this bridge's subscriptions.
                raise MmmError("the phone is not reachable right now; still reconnecting")
            return self._android

    def close(self) -> None:
        if self._android is not None:
            self._android.close()

    def facade(self, method: str, *args: Any) -> Any:
        android = self.android
        return android.call(android.cls(FACADE), method, android.context, *args)

    def ensure_subscription(self, name: str, actions: list[str] | None) -> list[str]:
        android = self.android
        wanted = actions or KNOWN_SUBSCRIPTIONS.get(name)
        if not wanted:
            raise MmmError(
                f"unknown subscription {name!r}; pass actions, or use one of "
                f"{', '.join(sorted(KNOWN_SUBSCRIPTIONS))}"
            )
        if name not in self._buffers:
            self._buffers[name] = []

            def collect(payload: Any, _name: str = name) -> None:
                with self._arrived:
                    self._buffers[_name].append(payload)
                    self._arrived.notify_all()

            android.subscribe(wanted, name=name, durable=True, handler=collect)
        return wanted

    def wait_for_events(self, name: str, timeout: float) -> list[Any]:
        deadline = time.monotonic() + timeout
        with self._arrived:
            while not self._buffers.get(name):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._arrived.wait(remaining)
            events, self._buffers[name] = self._buffers[name], []
            return events


def tool_definitions(full_scope: bool) -> list[dict]:
    tools = [
        {
            "name": "sms_send",
            "description": "Send an SMS from the phone.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Phone number"},
                    "body": {"type": "string", "description": "Message text"},
                },
                "required": ["to", "body"],
            },
        },
        {
            "name": "sms_list",
            "description": "Recent SMS messages, newest first.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}},
            },
        },
        {
            "name": "call_log",
            "description": "Recent calls, newest first.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}},
            },
        },
        {
            "name": "contacts_search",
            "description": "Contacts whose name or number contains a term.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["term"],
            },
        },
        {
            "name": "device_status",
            "description": "Signal, battery, network operator and SIM state.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "notify",
            "description": "Post a notification on the phone.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                "required": ["title", "text"],
            },
        },
        {
            "name": "events_wait",
            "description": (
                "Wait for new events on a durable subscription (sms, calls, battery, "
                "or your own name plus actions) and return them."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "subscription": {"type": "string", "default": "sms"},
                    "timeout": {"type": "number", "default": 30},
                    "actions": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    ]
    if full_scope:
        tools.append(
            {
                "name": "android_call",
                "description": (
                    "Call any Android method by reflection. Only available to a "
                    "full-scope token."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cls": {"type": "string", "description": "Class name"},
                        "method": {"type": "string"},
                        "args": {"type": "array"},
                        "on_context": {
                            "type": "boolean",
                            "description": "Pass the app Context as the first argument",
                            "default": False,
                        },
                    },
                    "required": ["cls", "method"],
                },
            }
        )
    return tools


def build_handlers(bridge: Bridge) -> dict[str, Callable[[dict], Any]]:
    def sms_send(arguments: dict) -> Any:
        return {"sent": bridge.facade("smsSend", arguments["to"], arguments["body"])}

    def sms_list(arguments: dict) -> Any:
        return bridge.facade("smsList", int(arguments.get("limit", 20)))

    def call_log(arguments: dict) -> Any:
        return bridge.facade("callLog", int(arguments.get("limit", 20)))

    def contacts_search(arguments: dict) -> Any:
        return bridge.facade(
            "contactsSearch", arguments["term"], int(arguments.get("limit", 20))
        )

    def device_status(arguments: dict) -> Any:
        return bridge.facade("deviceStatus")

    def notify(arguments: dict) -> Any:
        return {"notification": bridge.facade("notify", arguments["title"], arguments["text"])}

    def events_wait(arguments: dict) -> Any:
        name = arguments.get("subscription", "sms")
        actions = arguments.get("actions")
        bridge.ensure_subscription(name, actions)
        timeout = min(max(float(arguments.get("timeout", 30)), 0.0), MAX_WAIT)
        events = bridge.wait_for_events(name, timeout)
        return {"subscription": name, "events": events}

    def android_call(arguments: dict) -> Any:
        android = bridge.android
        target = android.cls(arguments["cls"])
        args = list(arguments.get("args") or [])
        if arguments.get("on_context"):
            args.insert(0, android.context)
        return android.call(target, arguments["method"], *args, expand=True)

    return {
        "sms_send": sms_send,
        "sms_list": sms_list,
        "call_log": call_log,
        "contacts_search": contacts_search,
        "device_status": device_status,
        "notify": notify,
        "events_wait": events_wait,
        "android_call": android_call,
    }


def serve(profile: str | None = None, stdin: Any = None, stdout: Any = None) -> int:
    """Reads JSON-RPC from stdin and writes replies to stdout until EOF."""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    bridge = Bridge(profile)
    handlers = build_handlers(bridge)

    def reply(message: dict) -> None:
        sink.write(json.dumps(message) + "\n")
        sink.flush()

    try:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError:
                continue
            response = handle(request, bridge, handlers)
            if response is not None:
                reply(response)
    finally:
        bridge.close()
    return 0


def handle(request: dict, bridge: Bridge, handlers: dict[str, Callable[[dict], Any]]) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if method is None:
        return None
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return ok(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mmm", "version": __version__},
            },
        )
    if method == "ping":
        return ok(request_id, {})
    if method == "tools/list":
        try:
            full = bool(bridge.android.info.get("scope", {}).get("full"))
        except Exception:
            # Offline: offer the curated set, which any token may use.
            full = False
        return ok(request_id, {"tools": tool_definitions(full)})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        handler = handlers.get(name)
        if handler is None:
            return error(request_id, -32601, f"unknown tool {name!r}")
        try:
            value = handler(arguments)
        except Exception as problem:
            return ok(
                request_id,
                {
                    "content": [{"type": "text", "text": f"{type(problem).__name__}: {problem}"}],
                    "isError": True,
                },
            )
        return ok(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(value, indent=2, default=repr)}],
                "isError": False,
            },
        )
    return error(request_id, -32601, f"unknown method {method!r}")


def ok(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(serve())
