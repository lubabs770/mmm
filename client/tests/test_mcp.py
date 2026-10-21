from __future__ import annotations

import io
import json
import threading
import time

from mmm import mcp


class StubBridge:
    """A Bridge that answers without a phone."""

    def __init__(self, full: bool = True) -> None:
        self.calls: list[tuple] = []
        self.full = full
        self.subscribed: list[tuple] = []
        self._pending: dict[str, list] = {}
        self.android = type("Info", (), {"info": {"scope": {"full": full}}})()

    def facade(self, method, *args):
        self.calls.append((method, args))
        return {"method": method, "args": list(args)}

    def ensure_subscription(self, name, actions):
        self.subscribed.append((name, actions))
        return actions or ["android.provider.Telephony.SMS_RECEIVED"]

    def wait_for_events(self, name, timeout):
        return self._pending.pop(name, [])

    def close(self):
        pass


def call_tool(bridge, name, arguments=None):
    handlers = mcp.build_handlers(bridge)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    response = mcp.handle(request, bridge, handlers)
    assert response is not None
    return response["result"]


def test_initialize_reports_tool_support():
    bridge = StubBridge()
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        bridge,
        mcp.build_handlers(bridge),
    )
    assert response["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert "tools" in response["result"]["capabilities"]


def test_notifications_get_no_response():
    bridge = StubBridge()
    assert (
        mcp.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            bridge,
            mcp.build_handlers(bridge),
        )
        is None
    )


def test_a_full_token_is_offered_the_generic_tool():
    bridge = StubBridge(full=True)
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, bridge, mcp.build_handlers(bridge)
    )
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert "android_call" in names
    assert "sms_send" in names


def test_a_scoped_token_is_not_offered_the_generic_tool():
    bridge = StubBridge(full=False)
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, bridge, mcp.build_handlers(bridge)
    )
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert "android_call" not in names
    assert {"sms_send", "sms_list", "call_log", "contacts_search", "device_status", "notify",
            "events_wait"} <= set(names)


def test_sms_send_goes_through_the_facade():
    bridge = StubBridge()
    result = call_tool(bridge, "sms_send", {"to": "+15555550123", "body": "hi"})
    assert result["isError"] is False
    assert bridge.calls == [("smsSend", ("+15555550123", "hi"))]


def test_sms_list_passes_its_limit():
    bridge = StubBridge()
    call_tool(bridge, "sms_list", {"limit": 5})
    assert bridge.calls == [("smsList", (5,))]


def test_device_status_needs_no_arguments():
    bridge = StubBridge()
    call_tool(bridge, "device_status")
    assert bridge.calls == [("deviceStatus", ())]


def test_events_wait_creates_the_subscription_it_was_asked_for():
    bridge = StubBridge()
    result = call_tool(bridge, "events_wait", {"subscription": "sms", "timeout": 0})
    assert bridge.subscribed == [("sms", None)]
    assert json.loads(result["content"][0]["text"])["subscription"] == "sms"


def test_a_failing_tool_is_reported_as_an_error_not_a_crash():
    bridge = StubBridge()

    def boom(_arguments):
        raise RuntimeError("no SIM")

    handlers = mcp.build_handlers(bridge)
    handlers["sms_send"] = boom
    response = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sms_send", "arguments": {}},
        },
        bridge,
        handlers,
    )
    assert response["result"]["isError"] is True
    assert "no SIM" in response["result"]["content"][0]["text"]


def test_an_unknown_tool_is_a_json_rpc_error():
    bridge = StubBridge()
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
        bridge,
        mcp.build_handlers(bridge),
    )
    assert response["error"]["code"] == -32601


def test_serve_reads_ndjson_from_stdin_and_writes_replies():
    source = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
    )
    sink = io.StringIO()
    bridge = StubBridge()
    # serve() builds its own Bridge, so drive handle() through a tiny loop instead.
    handlers = mcp.build_handlers(bridge)
    for line in source:
        response = mcp.handle(json.loads(line), bridge, handlers)
        if response is not None:
            sink.write(json.dumps(response) + "\n")
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
