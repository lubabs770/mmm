"""Notifications: every app's active ones (root), and posting your own (Facade)."""

from __future__ import annotations

import re

from ..client import Android
from . import facade, shell


def active(a: Android) -> list[dict]:
    """Every app's live notifications as ``{"package", "title", "text"}``, from dumpsys."""
    return parse_dumpsys(shell(a, "dumpsys notification --noredact", timeout_ms=15_000))


def post(a: Android, title: str, text: str) -> int:
    """Posts a notification from MMM itself; works with the scoped token."""
    return facade(a, "notify", title, text)


def parse_dumpsys(text: str) -> list[dict]:
    """``NotificationRecord`` blocks → rows. dumpsys lists a record in more than one
    section, so identical rows are kept once."""
    rows: list[dict] = []
    seen: set[tuple] = set()
    current: dict | None = None

    def flush() -> None:
        if current and (current["title"] or current["text"]):
            key = (current["package"], current["title"], current["text"])
            if key not in seen:
                seen.add(key)
                rows.append(current)

    for line in text.splitlines():
        record = re.match(r"\s*NotificationRecord\(\S+ pkg=(\S+)", line)
        if record:
            flush()
            current = {"package": record.group(1), "title": "", "text": ""}
            continue
        field = re.match(r"\s*android\.(title|text)=\S+ \((.*)\)\s*$", line)
        if field and current is not None and not current[field.group(1)]:
            current[field.group(1)] = field.group(2).strip()
    flush()
    return rows
