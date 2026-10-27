"""Android's system / secure / global settings tables. Needs a full-scope token."""

from __future__ import annotations

import re

from ..client import Android

CLASSES = {
    "system": "android.provider.Settings$System",
    "secure": "android.provider.Settings$Secure",
    "global": "android.provider.Settings$Global",
}


def read(a: Android, namespace: str) -> dict[str, str]:
    """Every name and value in one table, sorted by name."""
    _check(namespace)
    resolver = a.call(a.context, "getContentResolver")
    uri = a.call(a.cls("android.net.Uri"), "parse", f"content://settings/{namespace}")
    cursor = a.call(resolver, "query", uri, ["name", "value"], None, None, None)
    if cursor is None:
        return {}
    try:
        dump = a.call(a.cls("android.database.DatabaseUtils"), "dumpCursorToString", cursor)
    finally:
        a.call(cursor, "close")
    return dict(sorted(parse_cursor_dump(str(dump)).items()))


def write(a: Android, namespace: str, name: str, value: str) -> bool:
    _check(namespace)
    resolver = a.call(a.context, "getContentResolver")
    return bool(a.call(a.cls(CLASSES[namespace]), "putString", resolver, name, value))


def parse_cursor_dump(dump: str) -> dict[str, str]:
    """``DatabaseUtils.dumpCursorToString`` output → ``{name: value}``.

    One RPC call for the whole table instead of two per row over the network.
    A value that spans lines keeps its newlines.
    """
    out: dict[str, str] = {}
    row: dict[str, str] = {}
    last: str | None = None
    for line in dump.splitlines():
        if re.match(r"^\d+ \{$", line):
            row, last = {}, None
        elif line == "}":
            if "name" in row:
                out[row["name"]] = "" if row.get("value") == "null" else row.get("value", "")
            last = None
        elif line.startswith("   ") and "=" in line and re.match(r"^   \w+=", line):
            key, _, value = line[3:].partition("=")
            row[key] = value
            last = key
        elif last is not None and not line.startswith((">>>>>", "<<<<<")):
            row[last] += "\n" + line
    return out


def _check(namespace: str) -> None:
    if namespace not in CLASSES:
        raise ValueError(f"namespace must be one of {', '.join(CLASSES)}")
