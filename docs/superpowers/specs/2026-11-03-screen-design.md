# Screen tab: scrcpy in the MMM UI

Date: 2026-11-03. Status: draft for review.

## Goal

A **Screen** tab in the `mmm serve` web UI that shows the Flip's screen live and
lets you drive it with the Mac keyboard and mouse. No separate `scrcpy` window.
Built on Tango (ya-webadb), running in the page.

Out of scope: audio, clipboard sync, file drop, multi-device picker, WebUSB.

## Constraints found

- Tango `@yume-chan/scrcpy` 2.3.0 supports scrcpy server up to **3.3.3**.
  Homebrew's scrcpy is 5.0.1, so its server jar is unusable. We ship the
  official `scrcpy-server-v3.3.3` (~90 KB, Apache-2.0, SHA-256 pinned).
- UI CSP is `default-src 'self'`, and the Python client has zero dependencies
  (it must install on Termux). So Tango is a **pre-built bundle in `ui/`**,
  committed. No CDN, no Node needed at install time.
- Browsers can't open TCP. `serve` gets a small stdlib WebSocket→TCP pipe.

## Architecture

```
browser (Screen tab)                 mmm serve (Mac)            adb server     Flip
Tango AdbServerClient ──WS /api/adb──► wsbridge ──TCP──► 127.0.0.1:5037 ──► 100.76.200.10:5555
AdbScrcpyClient  (push jar, start 3.3.3, tunnelForward)
WebCodecs decoder → <canvas>;  key/mouse → scrcpy control
```

Reuses the Mac adb server and its already-authorized key: nothing on the phone
changes.

## Components

1. **`client/mmm/wsbridge.py`** (new, stdlib only, ~80 lines)
   - `accept(handler)`: RFC 6455 handshake (Sec-WebSocket-Accept).
   - Frame reader (masked client frames, fragmentation, ping→pong, close) and
     unmasked binary frame writer.
   - `pipe(handler, host, port)`: one TCP socket per WebSocket, two threads
     copying bytes until either side closes.

2. **`serve.py`**
   - `GET /api/adb` with `Upgrade: websocket` → existing Host check + secret
     (from `?s=`, since browsers can't set headers on WebSocket), plus an
     **Origin check** (`http://127.0.0.1:<port>` / `localhost`) against
     cross-site WebSocket hijacking. Then `wsbridge.pipe(..., "127.0.0.1", 5037)`.
     Adb server down → 502 `adb_unavailable` before upgrading.
   - `GET /api/screen` → `{"serial": "<profile host>:5555"}` so the page knows
     which device to `adb connect`.
   - `STATIC` gains `screen.js` (bundle) and `scrcpy-server`
     (`application/octet-stream`).

3. **`client/web/`** (new, dev-only source, not packaged)
   - `package.json` with exact pinned versions: `@yume-chan/adb@2.6.4`,
     `@yume-chan/scrcpy@2.3.0`, `@yume-chan/adb-scrcpy@2.3.2`,
     `@yume-chan/scrcpy-decoder-webcodecs@2.5.3`, `@yume-chan/stream-extra`,
     `esbuild`.
   - `screen.js`: exports `start(canvas, {secret, serial})` → `{stop()}`.
     - Connector: `connect()` opens `ws://…/api/adb?s=…` and wraps it as a
       `ReadableWritablePair<Uint8Array>`; reverse-tunnel methods throw (we use
       `tunnelForward: true`).
     - `getDevices()`; if `serial` absent → `client.wireless.connect(serial)`.
     - `createAdb({serial})` → fetch `/scrcpy-server` → `pushServer` → `start`
       with `ScrcpyOptions3_3_3({audio:false, control:true, tunnelForward:true,
       maxSize: 0, videoCodec:"h264"})`.
     - Video → `WebCodecsVideoDecoder` onto the canvas.
     - Input: `keydown/keyup` on the focused canvas → `injectKeyCode`.
       Map: arrows → DPAD_*, Enter → DPAD_CENTER, Escape → BACK,
       Backspace → DEL, Home → HOME, 0-9 * # → keypad codes, F1/F2 → soft
       left/right (MENU / BACK fallback), letters → `KEYCODE_A…`. Mouse
       down/up/move on canvas → `injectTouch`, scaled to video size.
   - `npm run build` → esbuild ESM bundle → `client/mmm/ui/screen.js`.

4. **`client/mmm/ui/scrcpy-server`**: the v3.3.3 jar, checksum noted in
   `client/web/README`.

5. **`app.js` / `index.html`**: nav button **Screen**; `views.screen`
   lazy-`import("./screen.js")`, Start/Stop button, status line, focusable
   canvas. Leaving the tab or closing the page calls `stop()` (closes the
   scrcpy client, server process exits with the socket).

## Errors

| Case | What the page shows |
|---|---|
| adb server not running | "adb server isn't running on the Mac (`adb start-server`)" |
| `adb connect` fails | "Can't reach <serial> over adb" |
| WebCodecs/H.264 missing | "This browser can't decode H.264 (use Chrome or Safari)" |
| Stream ends | status "stopped", Start button re-enabled |

## Testing

- `client/tests/test_wsbridge.py`: handshake accept-key vector from RFC 6455;
  masked frame round-trip; bridge echoes bytes through a local TCP echo server;
  bad Origin / missing secret → 403 (in `test_serve.py`).
- Manual: `npm run build`, kickstart serve, open UI → Screen → Start: Flip
  screen appears, arrows move focus, Esc goes back, click taps.
- Check the bundle doesn't spawn blob workers (CSP would block them); if the
  decoder needs one, use its main-thread renderer.

## Files touched

New: `client/mmm/wsbridge.py`, `client/web/{package.json,screen.js,README}`,
`client/mmm/ui/{screen.js,scrcpy-server}`, `client/tests/test_wsbridge.py`.
Edited: `client/mmm/serve.py`, `client/mmm/ui/{app.js,index.html,app.css}`,
`docs/ui.md`.
