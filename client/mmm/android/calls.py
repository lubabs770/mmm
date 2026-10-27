"""Call log and contacts, through the Facade (works with the scoped token)."""

from __future__ import annotations

from typing import Any

from ..client import Android
from . import facade


def log(a: Android, limit: int = 50) -> Any:
    """Newest calls first: ``number``, ``name``, ``date``, ``duration``, ``type``
    (1 incoming, 2 outgoing, 3 missed, 5 rejected)."""
    return facade(a, "callLog", limit)


def contacts(a: Android, term: str, limit: int = 50) -> Any:
    """Contacts whose name or number contains [term]: ``display_name``, ``data1`` (number)."""
    return facade(a, "contactsSearch", term, limit)
