"""A WebSocket that is really a TCP socket: the page's way to reach the adb server.

Browsers can't open TCP, so the Screen tab speaks the adb server protocol over a
WebSocket and this pipes the bytes to ``127.0.0.1:5037``. Binary messages only;
message boundaries mean nothing, it is a byte stream both ways.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
from typing import BinaryIO

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 16 * 1024 * 1024


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode: int, payload: bytes) -> bytes:
    """One unmasked final frame, the way a server sends it."""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 1 << 16:
        head = bytes([0x80 | opcode, 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 127]) + n.to_bytes(8, "big")
    return head + payload


def _exact(rfile: BinaryIO, n: int) -> bytes | None:
    data = rfile.read(n) if n else b""
    return data if len(data) == n else None


def read_message(rfile: BinaryIO, wfile: BinaryIO, lock: threading.Lock | None = None) -> bytes | None:
    """The next data message, answering pings on the way. None on close or EOF."""
    lock = lock or threading.Lock()
    parts: list[bytes] = []
    while True:
        head = _exact(rfile, 2)
        if head is None:
            return None
        fin, opcode, n = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x7F
        if n == 126:
            ext = _exact(rfile, 2)
        elif n == 127:
            ext = _exact(rfile, 8)
        else:
            ext = b""
        if ext is None:
            return None
        n = int.from_bytes(ext, "big") if ext else n
        if n > MAX_MESSAGE:
            return None
        key = _exact(rfile, 4) if head[1] & 0x80 else b""
        payload = _exact(rfile, n)
        if key is None or payload is None:
            return None
        if key:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        if opcode == 8:  # close: echo it, then we are done
            with lock:
                wfile.write(frame(8, payload[:2]))
            return None
        if opcode == 9:
            with lock:
                wfile.write(frame(10, payload))
            continue
        if opcode == 10:
            continue
        parts.append(payload)
        if fin:
            return b"".join(parts)


def pipe(rfile: BinaryIO, wfile: BinaryIO, address: tuple[str, int]) -> None:
    """Copy WebSocket messages to a TCP socket and its bytes back, until either side ends."""
    lock = threading.Lock()
    tcp = socket.create_connection(address, timeout=5)
    tcp.settimeout(None)

    def tcp_to_ws() -> None:
        try:
            while data := tcp.recv(65536):
                with lock:
                    wfile.write(frame(2, data))
            with lock:
                wfile.write(frame(8, (1000).to_bytes(2, "big")))
        except OSError:
            pass

    back = threading.Thread(target=tcp_to_ws, name="wsbridge", daemon=True)
    back.start()
    try:
        while (message := read_message(rfile, wfile, lock)) is not None:
            tcp.sendall(message)
    except OSError:
        pass
    finally:
        try:
            tcp.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        tcp.close()
        back.join(timeout=5)
