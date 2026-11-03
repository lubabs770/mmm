// The Screen tab: scrcpy in the page, through the Mac's adb server.
//
// The page can't open TCP, so every adb server connection is a WebSocket to
// /api/adb, which mmm serve pipes to 127.0.0.1:5037. From there it's plain
// Tango: push the scrcpy server, start it, decode H.264 with WebCodecs.
// Built with `npm run build` into client/mmm/ui/screen.js (committed).

import { AdbServerClient } from "@yume-chan/adb";
import { AdbScrcpyClient, AdbScrcpyOptions3_3_3 } from "@yume-chan/adb-scrcpy";
import {
  AndroidKeyCode, AndroidKeyEventAction, AndroidMotionEventAction, ScrcpyPointerId,
} from "@yume-chan/scrcpy";
import {
  WebCodecsVideoDecoder, WebGLVideoFrameRenderer, BitmapVideoFrameRenderer,
} from "@yume-chan/scrcpy-decoder-webcodecs";
import { MaybeConsumable, PushReadableStream } from "@yume-chan/stream-extra";

const SERVER_PATH = "/data/local/tmp/mmm-scrcpy-server.jar";

/** One adb server connection = one WebSocket to /api/adb. */
function connector(secret) {
  return {
    connect() {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(`ws://${location.host}/api/adb?s=${encodeURIComponent(secret)}`);
        ws.binaryType = "arraybuffer";
        let done;
        const closed = new Promise((r) => { done = r; });
        let push;
        const readable = new PushReadableStream((controller) => {
          push = controller;
        });
        ws.onmessage = (event) => push.enqueue(new Uint8Array(event.data));
        ws.onclose = () => {
          try { push.close(); } catch { /* already closed */ }
          done(undefined);
        };
        ws.onerror = () => reject(new Error("adb server isn't reachable through mmm serve"));
        ws.onopen = () => resolve({
          readable,
          writable: new MaybeConsumable.WritableStream({
            write(chunk) { ws.send(chunk); },
            close() { ws.close(); },
            abort() { ws.close(); },
          }),
          close() { ws.close(); },
          closed,
        });
      });
    },
    addReverseTunnel() { throw new Error("reverse tunnels aren't supported here"); },
    removeReverseTunnel() {},
    clearReverseTunnels() {},
  };
}

// The Flip's keypad, from the Mac keyboard. Anything not listed falls back to
// Tango's table, which is keyed by KeyboardEvent.code (KeyA, Digit1, Space…).
const KEYS = {
  Enter: AndroidKeyCode.AndroidDPadCenter,
  NumpadEnter: AndroidKeyCode.AndroidDPadCenter,
  Escape: AndroidKeyCode.AndroidBack,
  Home: AndroidKeyCode.AndroidHome,
  F1: 1, // KEYCODE_SOFT_LEFT
  F2: 2, // KEYCODE_SOFT_RIGHT
  F3: AndroidKeyCode.AndroidCall,
  F4: AndroidKeyCode.AndroidEndCall,
};

function keyCode(event) {
  if (event.key === "*") return AndroidKeyCode.Star;
  if (event.key === "#") return AndroidKeyCode.Pound;
  return KEYS[event.code] ?? AndroidKeyCode[event.code];
}

/**
 * Start mirroring onto `canvas`. Returns {stop, done}; `done` settles when the
 * stream ends. `status(text)` is called as things happen.
 */
export async function start(canvas, { secret, serial, status }) {
  if (!WebCodecsVideoDecoder.isSupported) {
    throw new Error("This browser can't decode H.264 (use Chrome or Safari)");
  }
  const server = new AdbServerClient(connector(secret));

  status("finding the phone…");
  let devices = await server.getDevices();
  if (serial && !devices.some((d) => d.serial === serial)) {
    status(`adb connect ${serial}…`);
    try {
      await server.wireless.connect(serial);
    } catch (error) {
      throw new Error(`Can't reach ${serial} over adb: ${error.message}`);
    }
    devices = await server.getDevices();
  }
  const device = serial ? devices.find((d) => d.serial === serial) : devices[0];
  if (!device) throw new Error(serial ? `Can't reach ${serial} over adb` : "adb sees no device");
  const adb = await server.createAdb({ serial: device.serial });

  status("pushing scrcpy server…");
  const jar = await fetch("scrcpy-server");
  if (!jar.ok) throw new Error(`scrcpy-server: HTTP ${jar.status}`);
  await AdbScrcpyClient.pushServer(adb, jar.body, SERVER_PATH);

  status("starting…");
  const options = new AdbScrcpyOptions3_3_3({
    audio: false,
    control: true,
    tunnelForward: true,
    videoCodec: "h264",
    stayAwake: true,
  });
  const client = await AdbScrcpyClient.start(adb, SERVER_PATH, options);
  // The server's log must be read or it blocks; keep the tail for errors.
  const output = [];
  client.output.pipeTo(new WritableStream({ write(line) { output.push(line); output.splice(0, output.length - 20); } }))
    .catch(() => {});

  const video = await client.videoStream;
  const renderer = WebGLVideoFrameRenderer.isSupported
    ? new WebGLVideoFrameRenderer(canvas)
    : new BitmapVideoFrameRenderer(canvas);
  const decoder = new WebCodecsVideoDecoder({ codec: video.metadata.codec, renderer });
  const streaming = video.stream.pipeTo(decoder.writable);
  status("live");

  const controller = client.controller;
  const size = () => ({ videoWidth: decoder.width, videoHeight: decoder.height });

  const onKey = (event) => {
    const code = keyCode(event);
    if (code === undefined || event.metaKey) return; // leave Cmd-shortcuts to the browser
    event.preventDefault();
    controller.injectKeyCode({
      action: event.type === "keydown" ? AndroidKeyEventAction.Down : AndroidKeyEventAction.Up,
      keyCode: code,
      repeat: event.repeat ? 1 : 0,
      metaState: 0,
    });
  };
  const touch = (action) => (event) => {
    if (action === AndroidMotionEventAction.Move && !(event.buttons & 1)) return;
    event.preventDefault();
    canvas.focus();
    const box = canvas.getBoundingClientRect();
    const { videoWidth, videoHeight } = size();
    if (!videoWidth || !box.width) return;
    controller.injectTouch({
      action,
      pointerId: ScrcpyPointerId.Finger,
      pointerX: Math.round(((event.clientX - box.left) / box.width) * videoWidth),
      pointerY: Math.round(((event.clientY - box.top) / box.height) * videoHeight),
      videoWidth,
      videoHeight,
      pressure: action === AndroidMotionEventAction.Up ? 0 : 1,
      actionButton: 0,
      buttons: 0,
    });
  };
  const down = touch(AndroidMotionEventAction.Down);
  const move = touch(AndroidMotionEventAction.Move);
  const up = touch(AndroidMotionEventAction.Up);
  canvas.addEventListener("keydown", onKey);
  canvas.addEventListener("keyup", onKey);
  canvas.addEventListener("pointerdown", down);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", up);

  let stopped = false;
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    canvas.removeEventListener("keydown", onKey);
    canvas.removeEventListener("keyup", onKey);
    canvas.removeEventListener("pointerdown", down);
    canvas.removeEventListener("pointermove", move);
    canvas.removeEventListener("pointerup", up);
    await client.close().catch(() => {});
    decoder.dispose();
    await adb.close().catch(() => {});
  };
  const done = Promise.race([client.exited, streaming])
    .catch(() => {})
    .then(async () => {
      const wasStopped = stopped;
      await stop();
      if (!wasStopped && output.length) throw new Error(output.at(-1));
    });
  return { stop, done };
}
