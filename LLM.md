# LLM.md — orientation for coding agents

MMM is one Android app that exposes the **whole** Android Java API over a
newline-delimited JSON RPC, plus a dependency-free Python client. `docs/spec.md` is
the design document; this file is how to work in the tree without breaking the
things that are easy to break.

## Where everything is

```
app/                                     Android app (Kotlin, Compose M3)
  src/main/java/lubabs770/mmm/modemizer/
    rpc/      Session (protocol), RpcEngine (reflection), Overloads,
              Marshal (JSON ↔ Java), HandleTable, Facade (curated ops)
    events/   EventHub (subscriptions, backlogs), Backlog (ring buffer),
              ProxyFactory (Java listeners), EventTarget
    auth/     TokenStore, Scope, AuditLog
    service/  MmmService (foreground service), RpcServer (listener),
              MmmRuntime (what the UI reads), Root/Privileged, BootReceiver
    settings/ Settings + SettingsStore, SettingsBridge (settings over RPC)
    ui/       MainActivity, MmmApp (nav), one file per screen, Theme, FocusKit
    util/     JsonFile, B64, Ids
  src/test/   JVM unit tests (no Android types in what they touch)
magisk/       module template, gen_privapp_xml.py, build.sh, its own tests
client/mmm/   client.py (Android, JavaObject), transport.py, config.py,
              cli.py, mcp.py
client/tests/ tests against a fake NDJSON server (tests/fake_server.py)
docs/         the docs site; gen_llms.py builds llms.txt
```

## Invariants

Breaking one of these is a bug even if everything compiles.

1. **Every setting the UI can change is also settable over RPC.** When you add a
   field to `MmmSettings`, add it to `SettingsStore.set`, to `SettingsBridge.get`,
   and to the Settings screen. `docs/python.md` mentions the bridge; keep it true.
2. **Broadcast event payloads are plain JSON — never handles.** A durable event can
   be replayed into a session that did not create it, so `EventHub` marshals with a
   null `HandleSink`, which turns anything unrepresentable into
   `{"$opaque": …, "repr": …}`. Proxy events may carry handles, and are therefore
   live-only and never buffered.
3. **Handles belong to a session.** They are freed on `release` and when the
   connection ends. Nothing may hand a handle to another session, and the client
   must not assume one survives a reconnect. Handle numbers come from one
   process-wide counter so a stale one is unknown rather than someone else's
   object; the Python client also tags each `JavaObject` with its connection.
4. **The listener binds `127.0.0.1`, plus the tailnet address only when the user
   turns `tailnet` on.** Never another network interface, never `0.0.0.0`. The
   tailnet socket refuses any peer outside 100.64.0.0/10 (`Tailnet.contains`):
   Linux delivers packets for any local address on any interface, so binding alone
   does not keep the LAN out. Otherwise remote access is SSH or `adb forward`.
5. **`auth` is the first message or the connection closes.** No op is answered
   before it, and it must arrive within 10 s in a small line. Revoking a token
   closes its live sessions (`MmmRuntime.revokeClient`), and `Session` re-checks the
   token on every request.
6. **Scopes are enforced in the app.** `Session.requireScope` guards `call`, `new`,
   `get` and `set`; `subscribe` checks each action. Scoped tokens only reach public
   members (`publicOnly`) and cannot `expand`. A new op that reaches Java needs its
   own check. Never move enforcement into the client.
7. **The priv-app allowlist is generated from the manifest.** Edit the manifest's
   `<!-- privileged -->` block; `magisk/gen_privapp_xml.py` does the rest. A
   hand-edited allowlist that falls behind the manifest can stop a phone booting.
8. **The Python client has no dependencies.** CI fails if the wheel grows a
   `Requires-Dist`. It has to install on Termux.
9. **One universal APK.** MMM writes no native code, but Compose pulls in
   `androidx.graphics.path`, which ships a `.so` for all four ABIs — so the check is
   `tools/check_universal_apk.sh`: if any native library is present, every ABI must
   be. An ABI split or an `abiFilters` line would break that.
10. **minSdk is 25.** Anything newer needs a `Build.VERSION.SDK_INT` check —
    `java.util.Base64` (API 26), `Method.getParameterCount` (26),
    `Process.waitFor(timeout)` (26) and typed `startForeground` (29/34) are the ones
    already worked around.

## How the pieces fit

A request arrives at `Session.run`, which reads one line, decides whether it is
cheap (`release`, `reply`, `unsubscribe`, `ping` — handled on the reader thread) or
real (dispatched to a worker, or the main Looper when `main: true`), then:

```
Session.execute → RpcEngine (resolveClass / construct / call / getField / setField)
                   └ Overloads.findMethod  → scores candidates against the args
                   └ Overloads.coerceArgs  → JSON numbers into int/long/float/…
               → Marshal.toJson / expand   → plain JSON, or a handle via HandleSink
```

Events run the other way: `EventHub` registers a `BroadcastReceiver` per
subscription (on its own `mmm-events` thread, never the main one), expands the
`Intent` to JSON and either emits it to the attached session or buffers it in that
subscription's `Backlog` — including when the emit fails, which is why
`Session.emit` throws instead of dropping. On the Python side, the reader thread
only reads; events and proxy calls run on a separate dispatcher thread so a
handler can call the phone. `ProxyFactory` builds a
`java.lang.reflect.Proxy` whose `InvocationHandler` emits an event and, for a method
that returns a value, blocks until the client's `reply` or the timeout.

## Testing

```sh
gradle testDebugUnitTest          # JVM tests: overloads, marshalling, handles, backlog, scopes
gradle lintDebug                  # Android lint, abortOnError = true
pip install -e "client[dev]" && python -m pytest client/tests -q
(cd magisk && python -m pytest -q)
```

- **Builds happen in CI**, not on a dev box: `ci.yml` on every push and PR,
  `release.yml` on a `v*` tag. Never dispatch a workflow on `main` without being
  asked to.
- JVM tests must not touch Android classes at the point they execute: the unit-test
  `android.jar` is a stub whose methods throw. `org.json` is a real test dependency
  so marshalling can be tested. Keep `Overloads`, `HandleTable`, `Backlog` and
  `Scope` free of Android imports.
- Python tests never need a phone: `client/tests/fake_server.py` speaks the protocol
  and records what it was sent. When you add an op, teach the fake server about it.
- `gen_privapp_xml.py` has its own tests because the failure mode is a phone that
  will not boot.

## Style

- Kotlin: 4 spaces, trailing commas, explicit visibility only where it is not
  obvious. Comments say *why*; the code says what.
- Python: `from __future__ import annotations`, type hints on public functions,
  docstrings that start with one line about the purpose.
- Nothing in the repo identifies a person or a device: no names, emails, phone
  numbers, hostnames, serials or home paths. Example numbers come from the
  `+1555…` reserved range; example hosts are `phone`.

## Things that look wrong but are not

- `Facade` adds no powers — a full token can call everything it calls. It exists so a
  scoped token can be allowed one class instead of a dozen.
- `cls` returns a handle (the spec says so), while a `Class` *returned from a call*
  marshals as `{"$cls": name}`. Both are accepted as a `target`.
- Settings are JSON files in `filesDir`, not DataStore: a few kilobytes of structured
  JSON, written atomically, with no dependency.
- The `id` on an `auth` reply is echoed like any other, and the reply doubles as the
  server-info response — the same JSON `ping` returns.
