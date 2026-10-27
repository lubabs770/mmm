from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mmm import Android
from mmm import rules as rules_module
from mmm.config import Profile
from mmm.android.settings import parse_cursor_dump
from mmm.serve import Daemon

from .fake_server import FakeServer, default_handler, result

SECRET = "test-secret"
SENDER = "+15550100199"


def phone(server: FakeServer, request: dict):
    """The fake app, plus the Facade and SmsMessage calls the daemon makes."""
    if request.get("op") != "call":
        return default_handler(server, request)
    method = request["method"]
    args = request.get("args") or []
    if method == "smsSend":
        return result(request, True)
    if method == "smsList":
        return result(request, [{"_id": 1, "address": SENDER, "body": "hi", "date": 1, "type": 1}])
    if method == "get" and args and args[0].get("$h") == 1:  # SettingsBridge.get(context)
        return result(request, {"port": 7357, "tailnet": True})
    if method == "set":  # SettingsBridge.set(context, key, value)
        return result(request, {"port": 7357, args[1]: args[2]})
    if method == "createFromPdu":
        return result(request, {"$h": 501, "cls": "android.telephony.SmsMessage"})
    if method == "getDisplayOriginatingAddress":
        return result(request, SENDER)
    if method == "getMessageBody":
        return result(request, server.sms_body)
    if method == "getContentResolver":
        return result(request, {"$h": 600, "cls": "android.content.ContentResolver"})
    if method == "run":  # Root.run(command, timeoutMs), expanded
        return result(request, {"ok": True, "code": 0, "output": server.shell_output})
    if method == "makeThing":
        return result(request, {"$h": 700, "cls": "com.example.Thing"})
    if method == "useThing":
        return result(request, args)
    return default_handler(server, request)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MMM_CONFIG", str(tmp_path / "config.toml"))
    server = FakeServer(phone)
    server.sms_body = "dump hello"
    server.shell_output = ""
    android = Android(
        Profile(name="test", token=server.token, transport="local", host="127.0.0.1", port=server.port),
        reconnect=False,
        timeout=5,
    )
    daemon = Daemon(android, port=0, rules_file=tmp_path / "rules.json", state=tmp_path / "state",
                    secret=SECRET, write_info=False)
    daemon.start()
    yield server, daemon, tmp_path
    daemon.stop()
    server.close()


def request(daemon, method, path, body=None, secret=SECRET, host=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{daemon.port}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-MMM-Secret": secret, "Content-Type": "application/json"},
    )
    if host:
        req.add_unredirected_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def sms_event(server, event_id):
    server.event("mmm-serve", {"action": "android.provider.Telephony.SMS_RECEIVED",
                               "extras": {"pdus": [{"$b64": "AAE="}], "format": "3gpp"}},
                 event_id=event_id)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_the_api_wants_the_secret(setup):
    _, daemon, _ = setup
    assert request(daemon, "GET", "/api/mac", secret="wrong")[0] == 403
    assert request(daemon, "GET", "/api/mac", secret="")[0] == 403
    assert request(daemon, "GET", "/api/mac")[0] == 200


def test_a_foreign_host_header_is_refused(setup):
    _, daemon, _ = setup
    assert request(daemon, "GET", "/api/mac", host="evil.example:80")[0] == 403


def test_the_page_itself_is_served_without_the_secret(setup):
    _, daemon, _ = setup
    with urllib.request.urlopen(f"http://127.0.0.1:{daemon.port}/", timeout=5) as res:
        assert b"<title>MMM</title>" in res.read()
        assert "script-src 'self'" in res.headers["Content-Security-Policy"]


def test_the_favicon_is_served_as_a_png(setup):
    _, daemon, _ = setup
    for path in ("/favicon.png", "/favicon.ico"):
        with urllib.request.urlopen(f"http://127.0.0.1:{daemon.port}{path}", timeout=5) as res:
            assert res.headers["Content-Type"] == "image/png"
            assert res.read().startswith(b"\x89PNG")


def test_mmm_settings_round_trip(setup):
    server, daemon, _ = setup
    assert request(daemon, "GET", "/api/mmm-settings") == (200, {"port": 7357, "tailnet": True})
    status, value = request(daemon, "POST", "/api/mmm-settings", {"key": "backlogHours", "value": 48})
    assert status == 200 and value["backlogHours"] == 48


def test_sending_an_sms_goes_through_the_facade(setup):
    server, daemon, _ = setup
    assert request(daemon, "POST", "/api/sms", {"to": SENDER, "body": "yo"}) == (200, {"sent": True})
    send = [r for r in server.sent("call") if r["method"] == "smsSend"][-1]
    assert send["args"][1:] == [SENDER, "yo"]
    assert request(daemon, "POST", "/api/sms", {"to": "", "body": "yo"})[0] == 400


def test_an_sms_runs_its_rule_once_even_when_replayed(setup):
    server, daemon, tmp = setup
    marker = tmp / "ran.txt"
    script = f"import os; open({str(marker)!r}, 'a').write(os.environ['MMM_ARGS'] + '\\n'); print('done')"
    status, _ = request(daemon, "PUT", "/api/rules", {"rules": [
        {"trigger": "dump", "command": [sys.executable, "-c", script], "reply": True}]})
    assert status == 200

    sms_event(server, 5)
    assert wait_for(lambda: marker.exists())
    assert wait_for(lambda: any(r["method"] == "smsSend" for r in server.sent("call")))
    reply = [r for r in server.sent("call") if r["method"] == "smsSend"][-1]
    assert reply["args"][1:] == [SENDER, "done"]

    sms_event(server, 5)  # a replay of the same event
    sms_event(server, 4)  # and an older one
    time.sleep(0.5)
    assert marker.read_text() == "hello\n"
    assert json.loads((tmp / "state" / "serve-state.json").read_text()) == {"last_event_id": 5}


def test_a_blocked_sender_is_logged_not_run(setup):
    server, daemon, tmp = setup
    marker = tmp / "ran.txt"
    request(daemon, "PUT", "/api/rules", {"rules": [
        {"trigger": "dump", "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w')"],
         "senders": ["+15550100000"]}]})
    sms_event(server, 9)
    assert wait_for(lambda: daemon.runs and daemon.runs[-1]["blocked"])
    assert not marker.exists()


def test_rules_accept_a_command_line_and_reject_bad_tables(setup):
    _, daemon, tmp = setup
    status, value = request(daemon, "PUT", "/api/rules", {"rules": [{"trigger": "Echo", "command": "echo 'a b'"}]})
    assert status == 200
    assert value["rules"][0]["command"] == ["echo", "a b"]
    assert value["rules"][0]["trigger"] == "echo"
    assert rules_module.load(tmp / "rules.json")[0].command == ["echo", "a b"]
    status, value = request(daemon, "PUT", "/api/rules", {"rules": [{"trigger": "two words", "command": "x"}]})
    assert status == 400 and "one word" in value["message"]


def test_the_test_button_runs_without_replying(setup):
    server, daemon, _ = setup
    request(daemon, "PUT", "/api/rules", {"rules": [
        {"trigger": "dump", "command": [sys.executable, "-c", "print('x')"], "reply": True}]})
    status, run = request(daemon, "POST", "/api/rules/test", {"trigger": "dump", "text": "dump hi"})
    assert status == 200 and run["exit"] == 0 and run["test"] is True
    assert not any(r["method"] == "smsSend" for r in server.sent("call"))


def test_console_handles_come_back_as_chips_and_resolve_in_args(setup):
    server, daemon, _ = setup
    status, made = request(daemon, "POST", "/api/console",
                           {"op": "call", "target": "com.example.Factory", "member": "makeThing", "args": []})
    assert status == 200 and made["value"] == {"$h": 700, "cls": "com.example.Thing"}
    status, used = request(daemon, "POST", "/api/console",
                           {"op": "call", "target": "com.example.Factory", "member": "useThing",
                            "args": [made["value"], {"$ctx": True}]})
    assert status == 200
    sent = [r for r in server.sent("call") if r["method"] == "useThing"][-1]
    assert sent["args"] == [{"$h": 700}, {"$h": 1}]
    assert request(daemon, "POST", "/api/console",
                   {"op": "call", "target": "x", "member": "useThing", "args": [{"$h": 999}]})[0] == 400


def test_mac_status_reports_the_daemon_and_link(setup):
    _, daemon, _ = setup
    status, mac = request(daemon, "GET", "/api/mac")
    assert status == 200
    assert mac["profile"] == "test" and mac["ui"].endswith(str(daemon.port))
    assert mac["link"]["connected"] is True
    assert set(mac["login"]) == {"supported", "enabled"}


def test_a_cursor_dump_parses_into_settings():
    dump = (
        ">>>>> Dumping cursor android.database.sqlite.SQLiteCursor@1\n"
        "0 {\n   name=screen_brightness\n   value=128\n}\n"
        "1 {\n   name=note\n   value=line one\nline two\n}\n"
        "2 {\n   name=empty\n   value=null\n}\n"
        "<<<<<\n"
    )
    assert parse_cursor_dump(dump) == {"screen_brightness": "128", "note": "line one\nline two", "empty": ""}


def test_the_login_item_runs_the_pyz_itself_when_started_from_one(tmp_path, monkeypatch):
    import zipfile

    from mmm import serve

    pyz = tmp_path / "mmm-client.pyz"
    with zipfile.ZipFile(pyz, "w") as archive:
        archive.writestr("__main__.py", "")
    monkeypatch.setattr(sys, "argv", [str(pyz), "ui"])
    assert serve._serve_argv(7358, "flip") == [sys.executable, str(pyz), "--profile", "flip", "serve", "--port", "7358"]
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/mmm", "ui"])
    assert serve._serve_argv(7358, None)[:3] == [sys.executable, "-m", "mmm.cli"]


def test_handing_over_frees_the_port_without_ending_the_wait(setup, monkeypatch):
    server, daemon, _ = setup
    from mmm import serve

    calls = []
    monkeypatch.setattr(serve.subprocess, "run", lambda argv, **kw: calls.append(argv) or
                        serve.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(serve.os, "_exit", lambda code: calls.append(("exit", code)))
    monkeypatch.setattr(serve.time, "sleep", lambda s: None)
    daemon._handover(["launchctl", "bootstrap", "gui/501", "x.plist"])
    assert calls == [["launchctl", "bootstrap", "gui/501", "x.plist"], ("exit", 0)]
    # The port is free for the system copy, but wait() was never released early.
    assert not daemon._stop.is_set()
    import socket as sock
    with sock.socket() as probe:
        probe.bind(("127.0.0.1", daemon.port))


def test_the_rules_path_is_shown_with_a_tilde(setup, monkeypatch):
    from mmm import serve

    monkeypatch.setattr(serve.Path, "home", classmethod(lambda cls: Path("/home/someone")))
    assert serve._tilde(Path("/home/someone/.config/mmm/rules.json")) == "~/.config/mmm/rules.json"
    assert serve._tilde(Path("/home/someoneelse/rules.json")) == "/home/someoneelse/rules.json"
    assert serve._tilde(Path("/etc/rules.json")) == "/etc/rules.json"


def test_an_event_rule_runs_on_its_broadcast_with_the_intent_on_stdin(setup):
    server, daemon, tmp = setup
    marker = tmp / "event.json"
    script = f"import os, sys; open({str(marker)!r}, 'w').write(os.environ['MMM_EVENT'] + ' ' + sys.stdin.read())"
    status, body = request(daemon, "PUT", "/api/rules", {"rules": [
        {"trigger": "*", "event": "battery-low", "command": [sys.executable, "-c", script]}]})
    assert status == 200 and body["rules"][0]["event"] == "battery-low"
    subscribed = [r for r in server.sent("subscribe") if r.get("name") == "mmm-serve-events"]
    assert subscribed and subscribed[-1]["action"] == ["android.intent.action.BATTERY_LOW"]

    server.event("mmm-serve-events", {"action": "android.intent.action.BATTERY_LOW", "extras": {}})
    assert wait_for(lambda: marker.exists() and marker.read_text())
    name, payload = marker.read_text().split(" ", 1)
    assert name == "battery-low" and json.loads(payload)["action"] == "android.intent.action.BATTERY_LOW"
    assert wait_for(lambda: daemon.runs and daemon.runs[-1]["rule"] == "battery-low")


def test_apps_are_listed_and_a_bad_package_is_refused(setup):
    server, daemon, _ = setup
    server.shell_output = "package:com.a\npackage:com.b\n@\npackage:com.a\n@\npackage:com.b"
    status, rows = request(daemon, "GET", "/api/apps")
    assert status == 200 and rows == [{"package": "com.a", "system": True, "enabled": True},
                                      {"package": "com.b", "system": False, "enabled": False}]
    assert request(daemon, "POST", "/api/apps", {"package": "com.a; reboot", "enabled": False})[0] == 400


def test_mms_wants_an_image(setup):
    _, daemon, _ = setup
    assert request(daemon, "POST", "/api/mms", {"to": SENDER, "image": ""})[0] == 400
    assert request(daemon, "POST", "/api/mms", {"to": SENDER, "image": "not base64!"})[0] == 400


def test_an_mms_runs_the_sms_rule_with_its_pictures(setup, monkeypatch):
    from mmm.android import sms as sms_module

    from .test_android import NOTIFICATION, part, retrieve_conf
    server, daemon, tmp = setup
    out = tmp / "mms.json"
    script = (f"import json, os, sys; json.dump({{'text': sys.stdin.read(), 'sender': os.environ['MMM_SENDER'], "
              f"'media': os.environ['MMM_MEDIA'].splitlines()}}, open({str(out)!r}, 'w'))")
    request(daemon, "PUT", "/api/rules", {"rules": [{"trigger": "*", "command": [sys.executable, "-c", script]}]})
    subscribed = [r for r in server.sent("subscribe") if r.get("name") == "mmm-serve-mms"]
    assert subscribed and subscribed[-1]["types"] == ["application/vnd.wap.mms-message"]

    fetched = []
    pdu = retrieve_conf([part(b"\x03\x83\x81\xea\x8etext.txt\x00", b"see this"),
                         part(b"\x0d\x9e\x85photo.jpg\x00", b"\xff\xd8JPEG")])
    monkeypatch.setattr(sms_module, "download_mms", lambda a, url: fetched.append(url) or pdu)
    payload = {"action": "android.provider.Telephony.WAP_PUSH_RECEIVED",
               "extras": {"data": {"$b64": base64.b64encode(NOTIFICATION).decode()}}}
    server.event("mmm-serve-mms", payload, event_id=11)
    assert wait_for(lambda: out.exists() and out.read_text())
    got = json.loads(out.read_text())
    assert fetched == ["http://mmsc.example/abc"]
    assert got["text"] == "see this" and got["sender"] == "+15550100199"
    assert len(got["media"]) == 1 and Path(got["media"][0]).read_bytes() == b"\xff\xd8JPEG"

    server.event("mmm-serve-mms", payload, event_id=11)  # a replay is not fetched again
    time.sleep(0.3)
    assert fetched == ["http://mmsc.example/abc"]
