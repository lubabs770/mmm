from __future__ import annotations

import base64
import threading
import time

import pytest

from mmm import Android, AndroidError, JavaObject, MmmError
from mmm.config import Profile

from .fake_server import FakeServer, default_handler, failure, result


def profile_for(server: FakeServer, **overrides) -> Profile:
    return Profile(
        name="test",
        token=overrides.pop("token", server.token),
        transport="local",
        host="127.0.0.1",
        port=server.port,
        **overrides,
    )


def connect(server: FakeServer, **overrides) -> Android:
    return Android(profile_for(server, **overrides), reconnect=False, timeout=5)


def test_auth_is_the_first_message_and_carries_server_info():
    with FakeServer() as server, connect(server) as android:
        assert server.ops()[0] == "auth"
        assert android.info["model"] == "Fake"
        assert android.connected


def test_a_bad_token_raises():
    with FakeServer() as server:
        with pytest.raises(AndroidError) as caught:
            connect(server, token="wrong")
        assert caught.value.type == "unauthorized"


def test_no_token_at_all_is_refused_before_connecting():
    with pytest.raises(MmmError):
        Android(Profile(name="t", token="", transport="local", port=1), connect=False)


def test_a_static_call_sends_the_target_and_arguments():
    with FakeServer() as server, connect(server) as android:
        build = android.cls("android.os.Build")
        assert isinstance(build, JavaObject)
        android.call(build, "getRadioVersion", 1, "two", True)
        call = server.sent("call")[-1]
        assert call["target"] == {"$h": 100}
        assert call["method"] == "getRadioVersion"
        assert call["args"] == [1, "two", True]


def test_attribute_access_calls_a_method_and_reads_a_field():
    def handler(server, request):
        if request.get("op") == "call" and request.get("method") == "getLevel":
            return result(request, 3)
        if request.get("op") == "get" and request.get("field") == "MODEL":
            return result(request, "Fake")
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        build = android.cls("android.os.Build")
        assert build.getLevel() == 3
        assert str(build.MODEL) == "Fake"
        assert build.MODEL == "Fake"


def test_handles_become_java_objects_and_pass_back_as_handles():
    def handler(server, request):
        if request.get("op") == "call" and request.get("method") == "getSystemService":
            return result(request, {"$h": 55, "cls": "android.telephony.TelephonyManager"})
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        manager = android.context.getSystemService("phone")
        assert isinstance(manager, JavaObject)
        assert manager.cls.endswith("TelephonyManager")
        android.call(manager, "getSimState")
        assert server.sent("call")[-1]["target"] == {"$h": 55}


def test_a_class_reference_can_be_used_as_a_target_again():
    def handler(server, request):
        if request.get("op") == "call" and request.get("method") == "getClass":
            return result(request, {"$cls": "android.os.Build"})
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        reference = android.call(android.cls("x"), "getClass")
        android.call(reference, "getName")
        assert server.sent("call")[-1]["target"] == {"$cls": "android.os.Build"}


def test_bytes_cross_as_base64_both_ways():
    payload = b"\x00\x01\xfe"

    def handler(server, request):
        if request.get("op") == "call":
            return result(request, {"$b64": base64.b64encode(payload).decode()})
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        back = android.call(android.cls("x"), "bytes", payload)
        assert back == payload
        assert server.sent("call")[-1]["args"] == [
            {"$b64": base64.b64encode(payload).decode()}
        ]


def test_an_error_reply_raises_with_the_java_stack():
    def handler(server, request):
        if request.get("op") == "call":
            return failure(request, "java.lang.SecurityException", "nope")
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        with pytest.raises(AndroidError) as caught:
            android.call(android.cls("x"), "boom")
        assert caught.value.type == "java.lang.SecurityException"
        assert caught.value.java_stack == "stack"


def test_sig_and_main_and_expand_reach_the_wire():
    with FakeServer() as server, connect(server) as android:
        android.call(android.cls("x"), "m", 1, sig=["int"], main=True, expand=True)
        call = server.sent("call")[-1]
        assert call["sig"] == ["int"]
        assert call["main"] is True
        assert call["$expand"] is True


def test_expand_snapshots_an_object():
    with FakeServer() as server, connect(server) as android:
        snapshot = android.cls("x").expand()
        assert snapshot == {"$pojo": "x", "a": 1}
        assert server.sent("expand")[-1]["$expand"] is True


def test_a_broadcast_event_reaches_its_handler():
    seen: list = []
    with FakeServer() as server, connect(server) as android:
        android.subscribe("android.provider.Telephony.SMS_RECEIVED", name="sms", handler=seen.append)
        subscribe = server.sent("subscribe")[-1]
        assert subscribe["name"] == "sms"
        assert subscribe["action"] == ["android.provider.Telephony.SMS_RECEIVED"]
        server.event("sms", {"action": "SMS_RECEIVED", "extras": {"body": "hi"}})
        deadline = time.time() + 2
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        assert seen and seen[0]["extras"]["body"] == "hi"


def test_the_on_decorator_registers_a_durable_subscription():
    with FakeServer() as server, connect(server) as android:

        @android.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
        def got(intent):
            pass

        subscribe = server.sent("subscribe")[-1]
        assert subscribe["name"] == "sms"
        assert subscribe["durable"] is True


def test_the_events_iterator_yields_events():
    with FakeServer() as server, connect(server) as android:
        android.subscribe("a.b.C", name="watch")
        stream = android.events(timeout=2)
        server.event("watch", {"action": "a.b.C"}, event_id=9)
        event = next(stream)
        assert event.sub == "watch"
        assert event.event_id == 9


def test_a_proxy_method_that_needs_a_value_gets_a_reply():
    calls: list = []

    def listener(method, args):
        calls.append((method, args))
        return 42

    with FakeServer() as server, connect(server) as android:
        android.proxy("android.telephony.PhoneStateListener", listener)
        server.event(
            "proxy#1",
            {"proxy": "proxy#1", "method": "onCallStateChanged", "args": [1, "555"]},
            event_id=11,
            needs_reply=True,
        )
        deadline = time.time() + 2
        while not server.sent("reply") and time.time() < deadline:
            time.sleep(0.01)
        reply = server.sent("reply")[-1]
        assert reply["event_id"] == 11
        assert reply["value"] == 42
        assert calls == [("onCallStateChanged", [1, "555"])]


def test_a_void_proxy_method_sends_no_reply():
    with FakeServer() as server, connect(server) as android:
        android.proxy("java.lang.Runnable", lambda method, args: None)
        server.event("proxy#1", {"method": "run", "args": []}, event_id=12)
        time.sleep(0.2)
        assert server.sent("reply") == []


def test_released_handles_are_sent_in_batches():
    with FakeServer() as server, connect(server) as android:
        for _ in range(40):
            android.cls("android.os.Build").release()
        android.ping()
        released = [handle for request in server.sent("release") for handle in request["handles"]]
        assert len(released) >= 32


def test_reconnect_resubscribes_so_the_backlog_replays():
    with FakeServer() as server:
        android = Android(profile_for(server), reconnect=True, timeout=5)
        try:
            android.subscribe("a.b.C", name="sms", durable=True)
            assert server.connections == 1
            server.drop_connection()
            deadline = time.time() + 10
            while server.connections < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert server.connections == 2
            # Second connection: auth again, then the durable subscription.
            deadline = time.time() + 5
            while len(server.sent("subscribe")) < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert len(server.sent("auth")) == 2
            resubscribe = server.sent("subscribe")[-1]
            assert resubscribe["name"] == "sms"
            assert resubscribe["durable"] is True
        finally:
            android.close()


def test_close_is_idempotent_and_marks_the_client_disconnected():
    with FakeServer() as server:
        android = connect(server)
        android.close()
        android.close()
        assert not android.connected


def test_a_timeout_does_not_hang_forever():
    def handler(server, request):
        if request.get("op") == "call":
            return None  # deliberately never answer
        return default_handler(server, request)

    with FakeServer(handler) as server:
        android = Android(profile_for(server), reconnect=False, timeout=0.3)
        try:
            with pytest.raises(TimeoutError):
                android.call(android.cls("x"), "hangs")
        finally:
            android.close()


def test_concurrent_calls_get_their_own_replies():
    def handler(server, request):
        if request.get("op") == "call":
            delay = 0.2 if request["method"] == "slow" else 0.0

            def answer():
                time.sleep(delay)
                server.send(result(request, request["method"]))

            threading.Thread(target=answer, daemon=True).start()
            return None
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        target = android.cls("x")
        answers: dict = {}

        def call(name):
            answers[name] = android.call(target, name)

        threads = [threading.Thread(target=call, args=(name,)) for name in ("slow", "fast")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert answers == {"slow": "slow", "fast": "fast"}


def wait_until(condition, seconds: float = 3.0) -> bool:
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        time.sleep(0.01)
    return condition()


def test_a_handler_can_call_the_phone_without_deadlocking():
    answers: list = []
    with FakeServer() as server, connect(server) as android:
        facade = android.cls("lubabs770.mmm.modemizer.rpc.Facade")

        def on_sms(payload):
            # Before handlers moved off the reader thread this waited for the full
            # timeout: the reply could only be read by the thread running this.
            answers.append(android.call(facade, "smsList", android.context, 1))

        android.subscribe("android.provider.Telephony.SMS_RECEIVED", name="sms", handler=on_sms)
        started = time.time()
        server.event("sms", {"action": "SMS_RECEIVED"})
        assert wait_until(lambda: answers)
        assert answers == ["called"]
        assert time.time() - started < 2


def test_a_proxy_handler_can_call_the_phone_and_still_reply():
    with FakeServer() as server, connect(server) as android:

        def handler(method, args):
            return android.call(android.cls("x"), "y")

        android.proxy("java.lang.Runnable", handler)
        server.event("proxy#1", {"method": "call", "args": []}, event_id=9, needs_reply=True)
        assert wait_until(lambda: server.sent("reply"))
        assert server.sent("reply")[-1] == {"op": "reply", "event_id": 9, "value": "called"}


def test_objects_from_an_old_connection_are_refused_and_never_released():
    with FakeServer() as server:
        android = Android(profile_for(server), reconnect=True, timeout=5)
        try:
            old = android.new("java.lang.Object")
            server.drop_connection()
            assert wait_until(lambda: len(server.sent("auth")) == 2 and android.connected, 10)
            with pytest.raises(MmmError):
                android.call(old, "toString")
            # Collecting the old object must not free whatever the new session
            # happens to keep under the same number.
            for _ in range(40):
                old.release()
            android.ping()
            assert all(old.handle not in r["handles"] for r in server.sent("release"))
        finally:
            android.close()


def test_a_rejected_token_does_not_keep_retrying():
    with FakeServer() as server:
        with pytest.raises(AndroidError):
            Android(profile_for(server, token="revoked"), reconnect=True, timeout=5)
        time.sleep(1.5)
        assert server.connections == 1


def test_objects_collected_while_disconnected_are_not_released_on_the_next_session():
    with FakeServer() as server:
        android = connect(server)
        try:
            old = android.new("java.lang.Object")
            server.drop_connection()
            assert wait_until(lambda: not android.connected)
            # Garbage collection between the drop and the reconnect: these belong to
            # the dead session and must never be sent to the new one.
            for _ in range(40):
                old.release()
            android.connect()
            android.ping()
            assert all(old.handle not in r["handles"] for r in server.sent("release"))
        finally:
            android.close()


def test_non_finite_floats_cross_both_ways():
    import math

    def handler(server, request):
        if request.get("op") == "call":
            return result(request, [{"$float": "NaN"}, {"$float": "-Infinity"}, 1.5])
        return default_handler(server, request)

    with FakeServer(handler) as server, connect(server) as android:
        value = android.call("x.Y", "z", float("inf"), float("nan"))
        assert math.isnan(value[0]) and value[1] == float("-inf") and value[2] == 1.5
        assert server.sent("call")[-1]["args"] == [{"$float": "Infinity"}, {"$float": "NaN"}]


def test_a_proxy_reply_does_not_wait_behind_another_proxy_handler():
    second_ran = threading.Event()
    with FakeServer() as server, connect(server) as android:

        def handler(method, args):
            if method == "outer":
                # A nested callback fired while this one is still running: it has to
                # be served, or both wait out the phone's reply timeout.
                server.event("proxy#1", {"method": "inner", "args": []}, event_id=2, needs_reply=True)
                assert second_ran.wait(3)
                return "outer-done"
            second_ran.set()
            return "inner-done"

        android.proxy("java.lang.Runnable", handler)
        server.event("proxy#1", {"method": "outer", "args": []}, event_id=1, needs_reply=True)
        assert wait_until(lambda: len(server.sent("reply")) == 2, 5)
        replies = {r["event_id"]: r["value"] for r in server.sent("reply")}
        assert replies == {1: "outer-done", 2: "inner-done"}
