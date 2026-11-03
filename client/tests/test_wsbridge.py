from __future__ import annotations

import io
import os
import socket
import threading

from mmm import wsbridge


def masked(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """A frame the way a browser sends it: always masked."""
    key = os.urandom(4)
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def test_the_accept_key_matches_the_rfc_example():
    assert wsbridge.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frames_of_every_length_and_fragments_read_back():
    big = os.urandom(70000)
    stream = io.BytesIO(masked(2, b"hi") + masked(2, b"x" * 300)
                        + masked(2, b"ab", fin=False) + masked(0, b"cd") + masked(2, big))
    out = io.BytesIO()
    assert wsbridge.read_message(stream, out) == b"hi"
    assert wsbridge.read_message(stream, out) == b"x" * 300
    assert wsbridge.read_message(stream, out) == b"abcd"
    assert wsbridge.read_message(stream, out) == big
    assert wsbridge.read_message(stream, out) is None  # EOF


def test_a_ping_gets_a_pong_and_close_ends_the_stream():
    stream = io.BytesIO(masked(9, b"p") + masked(2, b"data") + masked(8, b""))
    out = io.BytesIO()
    assert wsbridge.read_message(stream, out) == b"data"
    assert out.getvalue() == wsbridge.frame(10, b"p")
    assert wsbridge.read_message(stream, out) is None


def test_bytes_go_both_ways_through_the_pipe():
    echo = socket.create_server(("127.0.0.1", 0))

    def serve_echo():
        conn, _ = echo.accept()
        with conn:
            while data := conn.recv(65536):
                conn.sendall(data[::-1])

    threading.Thread(target=serve_echo, daemon=True).start()
    browser, server_side = socket.socketpair()
    rfile, wfile = server_side.makefile("rb"), server_side.makefile("wb", buffering=0)
    pipe = threading.Thread(target=wsbridge.pipe, args=(rfile, wfile, echo.getsockname()), daemon=True)
    pipe.start()
    browser.sendall(masked(2, b"hello"))
    reply = browser.makefile("rb")
    assert wsbridge.read_message(reply, io.BytesIO()) == b"olleh"
    browser.sendall(masked(8, b""))
    pipe.join(timeout=5)
    assert not pipe.is_alive()
    echo.close()
