"""Curated Android APIs over a connected :class:`~mmm.Android`, written once and
shared by ``mmm serve``, ``mmm mcp``, the CLI and your own scripts.

Plain functions that take the connection first: ``sms.send(a, to, text)``.
Anything that goes through :data:`FACADE` works with the scoped "assistant"
token; everything else is raw reflection and needs a full-scope token.
"""

from __future__ import annotations

from typing import Any

from ..client import Android, MmmError

FACADE = "lubabs770.mmm.modemizer.rpc.Facade"
ROOT = "lubabs770.mmm.modemizer.service.Root"


def facade(a: Android, method: str, *args: Any) -> Any:
    """Calls one of the app's ``Facade`` helpers, which take the Context first."""
    return a.call(a.cls(FACADE), method, a.context, *args)


def shell(a: Android, command: str, timeout_ms: int = 10_000) -> str:
    """Runs [command] as root on the phone (``su -c``) and returns its output.

    Raises MmmError on a non-zero exit. Needs root and a full-scope token. Never put
    untrusted text in [command]: callers validate anything they interpolate.
    """
    result = a.call(a.get(a.cls(ROOT), "INSTANCE"), "run", command, timeout_ms, expand=True)
    if not result.get("ok"):
        raise MmmError(f"`{command.split()[0]}` failed (exit {result.get('code')}): {str(result.get('output', ''))[:200]}")
    return str(result.get("output", ""))


def restart_listener(a: Android) -> None:
    """Makes the app rebind its listeners after a port or tailnet change."""
    runtime = a.cls("lubabs770.mmm.modemizer.service.MmmRuntime")
    a.call(a.get(runtime, "INSTANCE"), "restartListener", a.context)
