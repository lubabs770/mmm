"""The ``mmm`` command.

    mmm pair <token> [--ssh host | --adb | --tailnet host]
    mmm status
    mmm call android.os.Build MODEL
    mmm watch android.provider.Telephony.SMS_RECEIVED --durable sms
    mmm repl
    mmm mcp
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__, config as config_module
from .client import Android, MmmError, json_default
from .config import Profile


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except MmmError as error:
        print(f"mmm: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mmm", description="All of Android, from here.")
    parser.add_argument("--version", action="version", version=f"mmm {__version__}")
    parser.add_argument("--profile", help="connection profile to use")
    subparsers = parser.add_subparsers(dest="command")

    pair = subparsers.add_parser("pair", help="save a token and how to reach the phone")
    pair.add_argument("token", help="token shown by the Clients screen in the app")
    pair.add_argument("--ssh", metavar="HOST", help="tunnel through ssh to HOST")
    pair.add_argument("--adb", action="store_true", help="tunnel through adb forward")
    pair.add_argument("--tailnet", metavar="HOST",
                      help="dial HOST directly over Tailscale (turn on tailnet in the app)")
    pair.add_argument("--name", help="profile name (default: local, ssh host, or adb)")
    pair.add_argument("--port", type=int, default=config_module.DEFAULT_PORT,
                      help="port MMM listens on, on the phone")
    pair.add_argument("--local-port", type=int, help="local end of the tunnel")
    pair.add_argument("--ssh-port", type=int, help="ssh port (Termux sshd is usually 8022)")
    pair.add_argument("--ssh-user", help="ssh user")
    pair.add_argument("--serial", help="adb device serial")
    pair.add_argument("--no-verify", action="store_true", help="do not test the connection")
    pair.set_defaults(handler=cmd_pair)

    status = subparsers.add_parser("status", help="ping the phone and print server info")
    status.set_defaults(handler=cmd_status)

    call = subparsers.add_parser("call", help="one static call, printed as JSON")
    call.add_argument("cls", help="class name, e.g. android.os.Build")
    call.add_argument("member", help="method or field name")
    call.add_argument("args", nargs="*", help="arguments; JSON when it parses, else a string")
    call.add_argument("--sig", help="comma-separated parameter types to force an overload")
    call.add_argument("--main", action="store_true", help="run on the main looper")
    call.add_argument("--expand", action="store_true", help="snapshot the result as JSON")
    call.set_defaults(handler=cmd_call)

    watch = subparsers.add_parser("watch", help="stream broadcasts as JSON lines")
    watch.add_argument("actions", nargs="+", help="broadcast actions to subscribe to")
    watch.add_argument("--durable", metavar="NAME", help="durable subscription, replayed on reconnect")
    watch.add_argument("--count", type=int, help="stop after this many events")
    watch.set_defaults(handler=cmd_watch)

    repl = subparsers.add_parser("repl", help="Python prompt with `a` already connected")
    repl.set_defaults(handler=cmd_repl)

    mcp = subparsers.add_parser("mcp", help="run as an MCP server on stdio")
    mcp.set_defaults(handler=cmd_mcp)

    return parser


# ----------------------------------------------------------------- commands


def cmd_pair(args: argparse.Namespace) -> int:
    if sum(map(bool, (args.ssh, args.adb, args.tailnet))) > 1:
        print("mmm: pick one of --ssh, --adb or --tailnet", file=sys.stderr)
        return 2
    transport = "ssh" if args.ssh else "adb" if args.adb else "tailnet" if args.tailnet else "local"
    name = args.name or args.ssh or args.tailnet or ("adb" if args.adb else "local")
    profile = Profile(
        name=name,
        token=args.token,
        transport=transport,
        host=args.tailnet or config_module.LOOPBACK,
        port=args.port,
        local_port=args.local_port or args.port,
        ssh_host=args.ssh or "",
        ssh_port=args.ssh_port or 0,
        ssh_user=args.ssh_user or "",
        adb_serial=args.serial or "",
    )
    config = config_module.load()
    config.profiles[name] = profile
    if len(config.profiles) == 1 or config.default not in config.profiles:
        config.default = name
    path = config_module.save(config)
    print(f"saved profile {name!r} ({transport}) to {path}")

    if args.no_verify:
        return 0
    try:
        with Android(profile) as android:
            print(f"connected: {describe(android.info)}")
    except Exception as error:
        print(f"saved, but could not connect yet: {error}", file=sys.stderr)
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with connect(args) as android:
        info = android.ping()
        print(json.dumps(info, indent=2, sort_keys=True))
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    sig = args.sig.split(",") if args.sig else None
    with connect(args) as android:
        target = android.cls(args.cls)
        if args.args or looks_like_method(args.member):
            value = android.call(
                target,
                args.member,
                *[parse_argument(a) for a in args.args],
                sig=sig,
                main=args.main,
                expand=args.expand,
            )
        else:
            value = android.get(target, args.member)
        print(to_json(value))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    with connect(args) as android:
        android.subscribe(
            args.actions,
            name=args.durable or "watch",
            durable=bool(args.durable),
        )
        seen = 0
        for event in android.events():
            print(
                json.dumps(
                    {
                        "sub": event.sub,
                        "event_id": event.event_id,
                        "ts": event.ts,
                        "payload": event.payload,
                    },
                    default=json_default,
                ),
                flush=True,
            )
            seen += 1
            if args.count and seen >= args.count:
                break
    return 0


def cmd_repl(args: argparse.Namespace) -> int:
    import code

    with connect(args) as android:
        banner = (
            f"mmm {__version__} — {describe(android.info)}\n"
            "`a` is connected. Try: a.cls('android.os.Build').MODEL"
        )
        code.InteractiveConsole(locals={"a": android, "mmm": sys.modules["mmm"]}).interact(banner)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import serve

    return serve(profile=args.profile)


# ------------------------------------------------------------------ helpers


def connect(args: argparse.Namespace) -> Android:
    return Android(getattr(args, "profile", None))


def describe(info: dict[str, Any]) -> str:
    model = info.get("model", "?")
    version = info.get("version", "?")
    port = info.get("port", "?")
    tailnet = info.get("tailnet")
    return f"{model}, mmm {version} on 127.0.0.1:{port}" + (f" and {tailnet}" if tailnet else "")


def looks_like_method(name: str) -> bool:
    """`MODEL` is a field, `getRadioVersion` is a method — Java's own convention."""
    return not name.isupper()


def parse_argument(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def to_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=json_default)


if __name__ == "__main__":
    raise SystemExit(main())
