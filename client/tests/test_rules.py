from __future__ import annotations

import json
import sys

import pytest

from mmm import rules
from mmm.rules import Rule

DUMP = (
    "import json, os, sys; "
    "print(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
    "'env': {k: v for k, v in os.environ.items() if k.startswith('MMM_')}}))"
)


def dumper(**overrides) -> Rule:
    return Rule(trigger="dump", command=[sys.executable, "-c", DUMP], **overrides)


def test_the_first_word_picks_the_rule_case_insensitively():
    table = [Rule("xyz", ["true"]), Rule("*", ["false"])]
    assert rules.match(table, "XYZ hello").trigger == "xyz"
    assert rules.match(table, "  xyz").trigger == "xyz"


def test_anything_else_falls_to_the_catch_all_and_none_without_one():
    assert rules.match([Rule("xyz", ["true"]), Rule("*", ["x"])], "hello").trigger == "*"
    assert rules.match([Rule("xyz", ["true"])], "hello") is None
    assert rules.match([Rule("xyz", ["true"])], "") is None


def test_a_specific_trigger_beats_the_catch_all_whatever_the_order():
    table = [Rule("*", ["x"]), Rule("xyz", ["true"])]
    assert rules.match(table, "xyz").trigger == "xyz"


def test_disabled_rules_are_skipped():
    table = [Rule("xyz", ["true"], enabled=False), Rule("*", ["x"])]
    assert rules.match(table, "xyz").trigger == "*"


def test_the_sender_allowlist_compares_the_last_ten_digits():
    rule = Rule("xyz", ["true"], senders=["(555) 010-0199"])
    assert rules.sender_allowed(rule, "+15550100199")
    assert not rules.sender_allowed(rule, "+15550100198")
    assert not rules.sender_allowed(rule, "")
    assert rules.sender_allowed(Rule("xyz", ["true"]), "anyone")


def test_hostile_text_reaches_the_command_only_through_stdin_and_env():
    text = "dump ; rm -rf ~ `touch pwned` $(touch pwned2) && echo hi"
    result = rules.run(dumper(), text, "+15550100199")
    assert result.exit == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["argv"] == []
    assert seen["stdin"] == text
    assert seen["env"]["MMM_TEXT"] == text
    assert seen["env"]["MMM_ARGS"] == "; rm -rf ~ `touch pwned` $(touch pwned2) && echo hi"
    assert seen["env"]["MMM_SENDER"] == "+15550100199"
    assert seen["env"]["MMM_TRIGGER"] == "dump"


def test_the_catch_all_gets_the_whole_text_as_args():
    rule = dumper()
    rule.trigger = "*"
    seen = json.loads(rules.run(rule, "hello there", "x").stdout)
    assert seen["env"]["MMM_ARGS"] == "hello there"


def test_a_slow_command_is_killed_at_its_timeout():
    rule = Rule("slow", [sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    result = rules.run(rule, "slow", "x")
    assert result.timed_out and result.exit is None
    assert result.ms < 4000


def test_a_missing_command_is_a_result_not_a_crash():
    result = rules.run(Rule("x", ["/no/such/command"]), "x", "y")
    assert result.exit is None and result.stderr


def test_replies_fit_one_sms():
    assert rules.reply_text("  ok\n") == "ok"
    assert len(rules.reply_text("a" * 500)) == 160
    assert len(rules.reply_text("é" * 500)) == 70
    assert rules.reply_text("   ") == ""


@pytest.mark.parametrize(
    "data, problem",
    [
        ({"rules": [{"trigger": "a", "command": ["x"]}, {"trigger": "A", "command": ["y"]}]}, "twice"),
        ({"rules": [{"trigger": "two words", "command": ["x"]}]}, "one word"),
        ({"rules": [{"trigger": "a", "command": []}]}, "no command"),
        ({"rules": [{"trigger": "a", "command": "echo hi"}]}, "list"),
    ],
)
def test_bad_rules_are_refused(data, problem):
    with pytest.raises(ValueError, match=problem):
        rules.parse(data)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "rules.json"
    table = [Rule("xyz", ["echo", "hi"], senders=["+15550100199"], reply=True, timeout=5)]
    rules.save(table, path)
    assert rules.load(path) == table
    assert rules.load(tmp_path / "missing.json") == []


def test_event_rules_take_no_trigger_word_and_no_reply():
    rules.validate([Rule("", ["x"], event="battery-low")])
    with pytest.raises(ValueError, match="trigger words only apply"):
        rules.validate([Rule("hi", ["x"], event="battery-low")])
    with pytest.raises(ValueError, match="reply only works"):
        rules.validate([Rule("*", ["x"], event="call", reply=True)])
    with pytest.raises(ValueError, match="used twice"):
        rules.validate([Rule("*", ["x"], event="call"), Rule("*", ["y"], event="call")])
    with pytest.raises(ValueError, match="must be one of"):
        rules.validate([Rule("*", ["x"], event="not an action")])


def test_an_sms_catch_all_and_an_event_rule_live_side_by_side():
    table = [Rule("*", ["sms"]), Rule("*", ["battery"], event="battery-low"),
             Rule("*", ["custom"], event="com.example.PING")]
    rules.validate(table)
    assert rules.match(table, "hello").command == ["sms"]  # event rules never answer texts
    assert [r.command for r in rules.for_action(table, "android.intent.action.BATTERY_LOW")] == [["battery"]]
    assert rules.event_actions(table) == ["android.intent.action.BATTERY_LOW", "com.example.PING"]


def test_the_event_name_reaches_the_command():
    rule = dumper(event="call")
    out = json.loads(rules.run(rule, '{"action": "x"}', "").stdout)
    assert out["env"]["MMM_EVENT"] == "call" and out["stdin"] == '{"action": "x"}'
