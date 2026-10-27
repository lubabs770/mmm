"""Rules: an incoming SMS whose first word is a trigger runs a command here.

A rule maps a trigger word to an argv. The message never becomes part of that
argv and no shell is involved: the command reads the text from stdin or from the
``MMM_*`` environment variables, so nothing a sender types can be executed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import config_path

CATCH_ALL = "*"
#: One SMS: 160 GSM-7 characters, or 70 once anything outside ASCII forces UCS-2.
SMS_GSM = 160
SMS_UCS2 = 70


def rules_path() -> Path:
    """Lives next to config.toml, so ``MMM_CONFIG`` moves it too."""
    return config_path().with_name("rules.json")


@dataclass
class Rule:
    """One trigger word and the command it runs."""

    trigger: str
    command: list[str]
    senders: list[str] = field(default_factory=list)
    reply: bool = False
    timeout: int = 60
    enabled: bool = True

    @classmethod
    def from_json(cls, raw: dict) -> "Rule":
        command = raw.get("command")
        if isinstance(command, str):
            raise ValueError("command must be a list of arguments, not a string")
        return cls(
            trigger=str(raw.get("trigger", "")).strip().lower(),
            command=[str(part) for part in (command or [])],
            senders=[str(s) for s in raw.get("senders") or []],
            reply=bool(raw.get("reply", False)),
            timeout=max(1, min(int(raw.get("timeout", 60)), 3600)),
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass
class Result:
    exit: int | None
    stdout: str
    stderr: str
    ms: int
    timed_out: bool = False


def validate(rules: list[Rule]) -> None:
    """Raises ValueError naming the first problem."""
    seen: set[str] = set()
    for rule in rules:
        if not rule.trigger or (rule.trigger != CATCH_ALL and not re.fullmatch(r"\S+", rule.trigger)):
            raise ValueError(f"trigger {rule.trigger!r} must be one word, or {CATCH_ALL!r}")
        if rule.trigger in seen:
            raise ValueError(f"trigger {rule.trigger!r} is used twice")
        seen.add(rule.trigger)
        if not rule.command or not rule.command[0].strip():
            raise ValueError(f"rule {rule.trigger!r} has no command")


def parse(data: Any) -> list[Rule]:
    rules = [Rule.from_json(raw) for raw in (data or {}).get("rules", [])]
    validate(rules)
    return rules


def load(path: Path | None = None) -> list[Rule]:
    path = path or rules_path()
    if not path.exists():
        return []
    return parse(json.loads(path.read_text(encoding="utf-8")))


def save(rules: list[Rule], path: Path | None = None) -> Path:
    validate(rules)
    path = path or rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"rules": [asdict(r) for r in rules]}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def first_word(text: str) -> str:
    words = text.split()
    return words[0].lower() if words else ""


def rest(text: str) -> str:
    """Everything after the trigger word."""
    parts = text.strip().split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number)[-10:]


def sender_allowed(rule: Rule, sender: str) -> bool:
    if not rule.senders:
        return True
    wanted = _digits(sender)
    return bool(wanted) and any(_digits(s) == wanted for s in rule.senders)


def match(rules: list[Rule], text: str) -> Rule | None:
    """The enabled rule for this text's first word, else the catch-all, else None.

    The sender is checked separately (``sender_allowed``) so a blocked match can be
    logged as blocked rather than vanishing.
    """
    word = first_word(text)
    live = [r for r in rules if r.enabled]
    for rule in live:
        if rule.trigger == word:
            return rule
    for rule in live:
        if rule.trigger == CATCH_ALL:
            return rule
    return None


def environment(text: str, sender: str, rule: Rule) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        MMM_TEXT=text,
        MMM_SENDER=sender,
        MMM_TRIGGER=first_word(text),
        MMM_ARGS=rest(text) if rule.trigger != CATCH_ALL else text,
    )
    return env


def run(rule: Rule, text: str, sender: str) -> Result:
    """Runs the rule's command. The text goes in through stdin and env only."""
    argv = [os.path.expanduser(rule.command[0]), *rule.command[1:]]
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            input=text,
            capture_output=True,
            text=True,
            timeout=rule.timeout,
            cwd=os.path.expanduser("~"),
            env=environment(text, sender, rule),
        )
    except subprocess.TimeoutExpired as expired:
        return Result(None, _text(expired.stdout), _text(expired.stderr), _ms(started), timed_out=True)
    except OSError as error:
        return Result(None, "", str(error), _ms(started))
    return Result(done.returncode, done.stdout, done.stderr, _ms(started))


def reply_text(stdout: str) -> str:
    """Stdout cut to one SMS; empty means no reply."""
    out = stdout.strip()
    limit = SMS_GSM if out.isascii() else SMS_UCS2
    return out[:limit]


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
