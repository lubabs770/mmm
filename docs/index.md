# MMM — Magnificent Modern Modemizer

MMM turns an Android phone into a programmable modem you can drive from Python,
without giving up the phone. Android stays installed and keeps running the hardware
— radio, SIM, SD card, charging, call audio — while one app exposes the **whole
Android Java API** over a small RPC protocol.

Termux:API offers about fifty hand-picked commands, can't react to events and can't
reach privileged APIs. MMM has no list: if the framework can do it, you can call it.

```python
from mmm import Android

a = Android()
print(a.cls("android.os.Build").MODEL)

tm = a.context.getSystemService("phone")
print(tm.getSignalStrength().getLevel())

@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def got(intent):
    print(intent["extras"])

a.run()
```

## How it fits together

```
 Linux computer                 Phone
 ┌──────────────┐      ┌─────────────────────────────────────┐
 │ python: mmm  │─ssh─▶│ Termux sshd ─┐                      │
 │ CLI:    mmm  │ -L   │              ▼                      │
 └──────────────┘      │ 127.0.0.1:7357  ◀── Termux: mmm     │
   (or adb forward)    │      │                              │
                       │ MMM app (foreground service)        │
                       │  ├ RPC engine (reflection)          │
                       │  ├ Handle table (per session)       │
                       │  ├ Event hub (broadcasts, proxies,  │
                       │  │            backlog ring buffer)  │
                       │  ├ Auth (per-client tokens)         │
                       │  └ M3 config UI (Compose)           │
                       └─────────────────────────────────────┘
```

The app listens on **loopback** by default. A computer reaches it through an SSH
tunnel, `adb forward`, or directly over Tailscale once you turn that on, and every
connection has to present a token that was created by
pairing in the app.

## Start here

- **[Install](install.md)** — the APK, the optional Magisk module, the Python client.
- **[Pairing](pairing.md)** — tokens, scopes, and the three transports.
- **[Python guide](python.md)** — objects, calls, events, proxies, and the curated
  helpers in `mmm.android` (SMS and MMS, apps, Wi-Fi, notifications, calls).
- **[Protocol](protocol.md)** — the wire format, if you are writing another client.
- **[Recipes](recipes.md)** — an SMS bot, a call screener, a notification forwarder.
- **[MCP](mcp.md)** — letting an AI assistant use the phone.
- **[Web UI and rules](ui.md)** — `mmm ui`, and running commands when a text, a
  picture or a phone event arrives.
- **[Security model](security.md)** — what a token is worth, and what it isn't.

## What it runs on

- Android 7.1 (API 25) and later, touch phones and keypad phones alike.
- Root is optional: it grants permissions without tapping and keeps the service out
  of Doze. Without root, MMM is an ordinary app.
- A Magisk module can install MMM as a priv-app, which is the only way to hold
  `MODIFY_PHONE_STATE` and `READ_PRIVILEGED_PHONE_STATE` for call control.
