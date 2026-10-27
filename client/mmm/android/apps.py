"""Installed packages: list, disable, enable. Needs root (``pm`` through su)."""

from __future__ import annotations

import re

from ..client import Android
from . import shell

PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
#: Disabling these cuts the link the request travels over, with no way back but a cable.
PROTECTED = {"lubabs770.mmm.modemizer", "com.tailscale.ipn"}


def installed(a: Android) -> list[dict]:
    """Every package as ``{"package", "system", "enabled"}``, sorted by name."""
    return parse_pm_lists(shell(a, "pm list packages; echo @; pm list packages -s; echo @; pm list packages -d"))


def set_enabled(a: Android, package: str, enabled: bool) -> str:
    """``pm enable`` / ``pm disable-user``: reversible, unlike uninstalling."""
    if not PACKAGE.fullmatch(package):
        raise ValueError(f"{package!r} is not a package name")
    if not enabled and package in PROTECTED:
        raise ValueError(f"{package} carries this connection; disabling it would strand the phone")
    command = f"pm enable {package}" if enabled else f"pm disable-user --user 0 {package}"
    return shell(a, command).strip()


def parse_pm_lists(output: str) -> list[dict]:
    """The three ``pm list packages`` blocks (all, -s, -d) split by ``@`` lines."""
    blocks: list[set[str]] = [set(), set(), set()]
    index = 0
    for line in output.splitlines():
        if line.strip() == "@":
            index = min(index + 1, 2)
        elif line.startswith("package:"):
            blocks[index].add(line[len("package:"):].strip())
    every, system, disabled = blocks
    return [{"package": p, "system": p in system, "enabled": p not in disabled} for p in sorted(every)]
