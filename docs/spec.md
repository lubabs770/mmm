<!-- Copied from the design spec; the authoritative version of this document
     lives here. Keep it in step with the code, or say what changed. -->

# MMM — Magnificent Modern Modemizer

## Context

Termux:API only offers about 50 hand-picked commands. It can't react to events (incoming SMS, calls, battery) and can't reach privileged APIs. The goal is to let Linux code use **all of Android** while Android itself stays installed to run the hardware (radio, SIM, SD card, charging, call audio).

MMM is one Android app that exposes the whole Android Java API through a generic reflection RPC (remote procedure call: your code asks the app to run a method for it). A Python client uses it from:
- **Termux on the phone** (direct local connection)
- **A Linux computer** (SSH tunnel or `adb forward`)

The app also has a Material 3 (M3) config UI that works with **both touch and D-pad/keypad** from day one, so it suits touch phones and keypad flip phones alike.

**Public open-source project.** The code, docs, commits and CI contain no identifying details: no personal names, emails, phone numbers, hostnames, device serials or home paths. Neutral identifiers are used throughout:
- Android package: `lubabs770.mmm.modemizer`
- Python package and CLI: `mmm`
- License: MIT

A copy of this spec (everything above "Private build notes") becomes `docs/spec.md`.

---

## Architecture

```
 Linux computer                 Phone
 ┌──────────────┐      ┌─────────────────────────────────────┐
 │ python: mmm  │─ssh─▶│ Termux sshd ─┐                      │
 │ CLI:    mmm  │ -L   │              ▼                      │
 └──────────────┘      │ 127.0.0.1:7357  ◀── Termux: mmm     │
   (or adb forward)    │      │                              │
                       │ MMM app (priv-app, foreground svc)  │
                       │  ├ RPC engine (reflection)          │
                       │  ├ Handle table (per session)       │
                       │  ├ Event hub (broadcasts, proxies,  │
                       │  │            backlog ring buffer)  │
                       │  ├ Auth (per-client tokens)         │
                       │  └ M3 config UI (Compose)           │
                       │ Android (rooted with Magisk)        │
                       └─────────────────────────────────────┘
```

### Transport decision: loopback TCP + tokens (not a Unix socket)
SELinux (Android's mandatory access-control layer) normally blocks one app, Termux, from connecting to another app's Unix socket. So:
- Listen on **`127.0.0.1:7357`** only, never on a network interface. The port is configurable.
- Every connection must authenticate first with a **per-client token**. Tokens are created by pairing in the UI and can be revoked.
- Remote computers reach the port only through an SSH `-L` tunnel (for example through Termux's sshd) or `adb forward tcp:7357 tcp:7357`. Nothing is exposed to the network.
- *Later addition:* an opt-in `tailnet` setting also binds the phone's Tailscale address, accepting only peers in 100.64.0.0/10, for a computer on the same tailnet to connect without a tunnel. Off by default.

---

## Component 1 — Android app (Kotlin)

Gradle (AGP 8.x), Java 17, Jetpack Compose + Material 3. minSdk 25, targetSdk 34.

### 1a. Service
- `MmmService`: foreground service with a persistent notification, required so Android doesn't kill it. Started on `BOOT_COMPLETED` and from the UI.
- Accept loop on a coroutine IO dispatcher, one session per connection.
- With root: puts itself on the battery-optimization whitelist (`dumpsys deviceidle whitelist +lubabs770.mmm.modemizer`).

### 1b. Wire protocol: newline-delimited JSON
Each message is one JSON object per line, so it can be debugged with `nc`. Every request has an `id`; replies echo it, which lets several calls be in flight at once.

| op | purpose |
|---|---|
| `auth {token}` | must be the first message; otherwise the connection closes |
| `cls {name}` | get a handle to a class (for static calls) |
| `new {cls, args}` | construct an object |
| `call {target, method, args, sig?}` | call an instance or static method |
| `get` / `set {target, field, value}` | read or write a field |
| `context` | handle to the app's Context |
| `release {handles}` | free handles (sent in batches by the client's GC) |
| `proxy {interfaces}` | make a Java object implementing the listener interfaces; its calls arrive as `event`s |
| `subscribe {name, action[], durable}` | register a broadcast receiver for those actions |
| `unsubscribe {name}` | |
| `reply {event_id, value}` | return value for a proxy method that needs one |

Server → client: `result {id, value}`, `error {id, type, message, java_stack}`, `event {sub, event_id, ts, payload}`.

**Value marshalling**
- null, bool, numbers and strings map directly to JSON. `byte[]` becomes `{"$b64": …}`.
- Lists, arrays and maps of plain values become JSON.
- **Everything else** becomes a handle: `{"$h": 42, "cls": "android.telephony.SignalStrength"}`.
- Helper `{"$expand": true}` on `call` asks for a Bundle, Intent or POJO (plain Java object) to be returned as a JSON snapshot instead of a handle. This is used for events.

**Overloads**: the engine picks the Java method whose parameter types fit the given args, choosing the most specific match. If it's ambiguous, it errors and lists the candidates. `sig: ["java.lang.String", "int"]` forces a choice.

**Threading**: calls run on a worker pool by default. `main: true` runs on the main Looper for APIs that require it (main-thread-only UI and some telephony listeners).

### 1c. Handles
- Each session gets its own `HandleTable` (`Long → Any`) with a cap (default 50k).
- Freed on `release` and when the client disconnects.
- Event payload handles belong to the subscribing session.

### 1d. Events
- **Broadcasts**: one `BroadcastReceiver` registered at runtime per subscription. The Intent is expanded to JSON (action, data, extras) with handles for any extras that can't be serialized.
- **Proxies**: `java.lang.reflect.Proxy` with an `InvocationHandler` that sends an `event`. Methods returning `void` don't block. Methods returning a value wait for `reply`, with a timeout (default 2s) after which a default value is returned.
- **Backlog**: a `durable` subscription survives disconnects. Its events go into a ring buffer (default 500 events or 24h) and are replayed on resubscribe with the same name and token. Durable subscriptions are saved to disk and re-registered on boot, so an SMS arriving while nobody is connected still gets delivered later.

### 1e. Auth & audit
- A token is 32 random bytes, stored hashed in app-private storage (DataStore) along with a user-chosen client label and a **scope**: `full`, or `scoped` with an allow-list (see Component 4).
- A rolling audit log records connects, auth failures, subscribe calls, and a per-client method-call counter (not every argument). It stays on the device and is viewable in the UI.

### 1f. Permissions
- The manifest requests only what MMM's documented features use: SMS (send, receive, read), phone state, call log (read), contacts (read), notifications, foreground service and boot-completed.
- When installed as a priv-app (Component 2) it can also hold `MODIFY_PHONE_STATE` and `READ_PRIVILEGED_PHONE_STATE`, for call control.
- The Permissions screen shows each one's state and opens the system grant prompt. With root, "Grant missing" runs `pm grant` for the declared ones.

### 1g. M3 config UI (Jetpack Compose, Material 3)
Dynamic color (Material You) where supported, a fixed palette otherwise. Light and dark themes.

**Touch + D-pad from day one**
- Every interactive element is focusable with a visible M3 focus indicator.
- Logical focus order, with `FocusRequester` on each screen's primary action.
- On small screens (keypad phones), navigation is a top-level list instead of a bottom bar.
- Keypad shortcuts: number keys jump to screens. Softkeys map to back and the primary action.
- Layout adapts by window size class, so the same screens fit 240×320 up to phone size.

**Screens**
1. **Status**: service on/off, port, root yes/no, priv-app yes/no, connected clients (live), events per minute.
2. **Clients**: list with label, last seen and revoke. "Pair new client" generates a token and shows copyable setup commands:
   - Termux: `mmm pair <token>`
   - Remote computer: `mmm pair <token> --ssh <host>` (or `--adb`)
3. **Permissions**: each declared permission with its granted state, "Grant missing (root)", and a priv-app install status hint.
4. **Subscriptions**: active and durable subscriptions, backlog sizes, and a clear-backlog action.
5. **Activity**: the audit log.
6. **Settings**: port, backlog limits, start on boot, proxy reply timeout, theme.

---

## Component 2 — Magisk module (built in CI)

`mmm-magisk.zip` contains:
- `system/priv-app/MMM/MMM.apk`
- `system/etc/permissions/privapp-permissions-mmm.xml`, which allowlists the privileged permissions. **This is required**: on Android 9+ with `ro.control_privapp_permissions=enforce`, a priv-app that requests privileged permissions without an allowlist can stop the phone from booting. The XML is generated from the manifest in CI so the two can't get out of sync.
- `service.sh`: runs at boot and starts the service as a fallback.

This is optional. Without it, MMM still works as a normal app with root-granted runtime permissions; only privileged calls fail.

---

## Component 3 — Python client + CLI (`client/` in the same repo)

Pure Python 3.10+ with no dependencies, so it installs cleanly on Termux and any Linux: `pip install "git+https://github.com/<owner>/mmm#subdirectory=client"`.

```python
from mmm import Android
a = Android()                       # reads ~/.config/mmm/config.toml
tm = a.context.getSystemService("phone")
tm.getSignalStrength().getLevel()

@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def got(intent): print(intent["extras"])

a.run()                             # event loop; or a.events() iterator
```

- `JavaObject` is a proxy class. `__getattr__` returns a callable method or a field read, and `__del__` queues `release`.
- A background reader thread sends replies to waiting callers and events to handlers.
- Config profiles: `local` (Termux: `127.0.0.1`), `ssh` (the client starts `ssh -N -L` itself and restarts it if it drops), `adb` (runs `adb forward`).
- Automatic reconnect, re-subscribing durable subscriptions so the backlog replays.

**CLI `mmm`**
- `mmm pair <token> [--ssh host | --adb]`: writes the config profile.
- `mmm status`: ping and server info.
- `mmm call <class> <method> [args…]`: one-off static call, prints JSON.
- `mmm watch <action> [--durable name]`: streams events as JSON lines, for piping in shell.
- `mmm repl`: Python REPL with `a` already connected.

---

## Security model (for the README)
- MMM gives full control of the phone to anyone holding a valid token, by design.
- Loopback only by default; remote access through SSH or adb, which bring their own authentication, or the opt-in tailnet listener, which relies on Tailscale's.
- Release APKs are signed with a key kept in CI secrets, **never committed**. Otherwise anyone could sign an "update" that replaces a privileged app with full phone access.

---

## Component 4 — LLM support

**4a. LLM docs**
- `LLM.md` at the repo root orients coding agents: architecture, where each piece lives, protocol invariants, how to test, and rules such as "every UI setting must also be settable via RPC".
- The docs site publishes `llms.txt` (an index) and `llms-full.txt` (all docs in one file), generated by CI from the same Markdown sources, so AI assistants can learn the API.

**4b. MCP server: `mmm mcp`**
MCP (Model Context Protocol) is the standard way AI assistants like Claude plug into tools. `mmm mcp` is a stdio MCP server in the Python client, so it runs wherever the client runs: Termux or a remote computer.
- **Curated tools**: `sms_send`, `sms_list`, `call_log`, `contacts_search`, `device_status` (signal, battery, network), `notify`.
- **Event tool**: `events_wait {subscription, timeout}` returns new events, such as SMS that arrived.
- **Generic tool**: `android_call {class, method, args}` for anything else. Off unless the token's scope allows it.

**Token scopes (enforced in the app, not the client)**
- Each token is `full` or `scoped`.
- A scoped token carries an allow-list of classes and methods (wildcards allowed) and of subscribable broadcast actions.
- MCP pairing in the UI defaults to a scoped preset: SMS, contacts, status, notify.
- Rejected calls appear in the Activity log.

---

## Repo layout

```
mmm/
  app/                      # Android app (Kotlin, Compose)
    src/main/java/lubabs770/mmm/modemizer/
      service/  rpc/  events/  auth/  ui/
  magisk/                   # module template + privapp xml generator
  client/                   # python package: library, CLI, mcp server
  docs/                     # docs site sources (Markdown), spec.md
  LLM.md  LICENSE  README.md
  .github/workflows/
    ci.yml                  # every push/PR: Kotlin tests, lint, APK debug build, python tests
    release.yml             # on tag v*: signed APK + magisk zip + wheel + pyz → GitHub Release
    docs.yml                # on main docs/** change: build site + llms.txt → GitHub Pages
```

---

## Release & CI workflows

**`ci.yml`**: runs on every push and PR. Runs the Kotlin unit tests, Android lint and the Python tests, then builds a debug APK and uploads it as an artifact.

**`release.yml`**: runs on a `v*` tag.
1. Build a **universal APK** that runs on every Android CPU type (arm64-v8a, armeabi-v7a, x86, x86_64). The app is pure Kotlin with no native code, so one APK covers them all; a CI check fails the build if a native `.so` ever appears.
   *As built:* Compose pulls in `androidx.graphics.path`, which ships a `.so` for all four ABIs, so "no `.so` at all" was the wrong test. `tools/check_universal_apk.sh` asserts the invariant that was meant instead: if native libraries are present, every ABI must be. Signed with the key from GitHub secrets.
2. Build the Magisk module zip from that APK plus the generated privapp XML.
3. Build the Python client as a wheel plus a single-file `mmm.pyz` (`python mmm.pyz …`). It's plain Python, so the same file works on Termux, Linux, macOS and Windows.
4. Generate `SHA256SUMS` and publish a GitHub Release with auto-generated notes.

The version comes from the tag and is written into the APK `versionName`/`versionCode` and the Python package.

**`docs.yml`**: builds the docs site with MkDocs Material from `docs/`, generates `llms.txt` and `llms-full.txt`, and deploys to **GitHub Pages** (the default `*.github.io` address, so no personal domain or extra accounts).

**Docs site pages**: install (APK / Magisk / client), pairing (Termux, SSH, adb), Python guide, protocol reference, recipes (SMS bot, call screener, notification forwarder), MCP setup, security model.

---

## Milestones

1. **Core RPC**: service, auth, `cls`/`new`/`call`/`get`/`set`/`release`, marshalling, Python client `local` profile, `mmm call`. Installable as a normal app; root not needed yet.
2. **Events**: broadcasts, proxies, durable backlog, `mmm watch`.
3. **M3 UI**: all six screens with touch and D-pad, pairing flow.
4. **Root & priv-app**: Grant missing, Magisk module with privapp XML, battery whitelist.
5. **Remote**: `ssh` and `adb` profiles, reconnect, `mmm repl`.
6. **Release**: `release.yml`, signing secrets, first `v0.1.0` tag.
7. **LLM**: token scopes, `mmm mcp`, `LLM.md`.
8. **Docs site**: `docs.yml`, pages, `llms.txt`.

`ci.yml` and `LLM.md` start in milestone 1 and grow from there.

---

## Verification

- **JVM unit tests** (CI): overload resolution, marshalling round trips, handle table release and cap, backlog ring buffer.
- **Python tests** (CI): client against a fake NDJSON server (proxy objects, events, reconnect and replay).
- **On-device acceptance checklist** (after each milestone):
  1. From Termux: `mmm call android.os.Build MODEL` returns the model.
  2. Signal level through `TelephonyManager`.
  3. `SmsManager.sendTextMessage` delivers to a test number.
  4. `mmm watch SMS_RECEIVED --durable sms`: text the phone and the event appears. Disconnect, text again, reconnect, and the backlog replays.
  5. A durable `PHONE_STATE` subscription reports an incoming call's state, and a proxy `SensorEventListener` reports light-sensor readings.
  6. As priv-app: answer and end a call (`TelecomManager.acceptRingingCall` / `endCall`), and read privileged phone state.
  7. From a remote computer over `--ssh`: same as 1–4, and the tunnel recovers after Wi-Fi drops.
  8. UI fully operable with D-pad only (emulator with a keyboard, plus a keypad phone) and with touch.
  9. A wrong or revoked token is rejected and appears in the Activity log. Port 7357 can't be reached from another machine on the LAN.
  10. A scoped (MCP) token can send SMS but gets rejected on `android_call` to a class outside its allow-list. Claude Code connected through `mmm mcp` can read and send a text.
  11. Release dry run: push a `v0.0.0-test` tag on a fork or branch. The release contains the APK, Magisk zip, wheel, pyz and SHA256SUMS. The APK installs on both an arm64 device and an x86_64 emulator.
  12. The docs site builds, and `llms.txt` lists every page.

## Open items
- Whether MMM should optionally take the **default SMS app** role (needed for reliable MMS send/receive storage). Proposed as a toggle after milestone 4.
