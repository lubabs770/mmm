"""Text and picture messages."""

from __future__ import annotations

import base64
import re
import threading
import time
from typing import Any

from ..client import Android, MmmError
from . import facade

#: Part content types for :func:`send_mms`, as WSP well-known short integers.
MMS_IMAGE_TYPES = {"image/jpeg": 0x9E, "image/gif": 0x9D, "image/png": 0xA0}


def send(a: Android, to: str, text: str) -> bool:
    """Sends a text; the app splits anything longer than one SMS."""
    return bool(facade(a, "smsSend", to, text))


def recent(a: Android, limit: int = 20) -> Any:
    """The newest messages in the SMS store."""
    return facade(a, "smsList", limit)


def decode(a: Android, payload: dict) -> tuple[str, str]:
    """Sender and full text of an SMS_RECEIVED intent, decoded on the phone."""
    extras = (payload or {}).get("extras") or {}
    pdus = extras.get("pdus") or []
    fmt = extras.get("format") or "3gpp"
    sms = a.cls("android.telephony.SmsMessage")
    sender, parts = "", []
    for pdu in pdus:
        message = a.call(sms, "createFromPdu", pdu, fmt)
        sender = sender or str(a.call(message, "getDisplayOriginatingAddress") or "")
        parts.append(str(a.call(message, "getMessageBody") or ""))
        message.release()
    return sender, "".join(parts)


def send_mms(a: Android, to: str, image: bytes, name: str, mime: str, caption: str = "") -> None:
    """Sends one picture (plus an optional caption) as an MMS. Needs a full-scope token.

    The PDU is built here and written to a MediaStore Downloads row the app owns:
    that is a content Uri the platform's MmsService can be granted, so the app
    needs no provider of its own. Keep the image under ~1 MB; carriers drop larger.
    Success means the platform accepted it; the result shows up in content://mms
    (msg_box 2 and resp_st 128 once the carrier's MMSC took it).
    """
    pdu = mms_pdu(to, image, name, mime, caption)
    resolver = a.call(a.context, "getContentResolver")
    downloads = a.call(a.cls("android.net.Uri"), "parse", "content://media/external/downloads")
    a.call(resolver, "delete", downloads, "_display_name LIKE 'mmm-out-%'", None,
           sig=["android.net.Uri", "java.lang.String", "java.lang.String[]"])
    values = a.new("android.content.ContentValues")
    a.call(values, "put", "_display_name", f"mmm-out-{int(time.time())}.pdu",
           sig=["java.lang.String", "java.lang.String"])
    item = a.call(resolver, "insert", downloads, values,
                  sig=["android.net.Uri", "android.content.ContentValues"])
    stream = a.call(resolver, "openOutputStream", item, sig=["android.net.Uri"])
    try:
        a.call(stream, "write", pdu, sig=["byte[]"])
    finally:
        a.call(stream, "close")
    manager = a.call(a.cls("android.telephony.SmsManager"), "getDefault")
    a.call(manager, "sendMultimediaMessage", a.context, item, None, None, None,
           sig=["android.content.Context", "android.net.Uri", "java.lang.String",
                "android.os.Bundle", "android.app.PendingIntent"])


def mms_pdu(to: str, image: bytes, name: str, mime: str, caption: str = "") -> bytes:
    """An M-Send.req (OMA MMS 1.2 encapsulation): multipart/mixed, optional text + one image."""
    if mime not in MMS_IMAGE_TYPES:
        raise ValueError(f"mime must be one of {', '.join(MMS_IMAGE_TYPES)}")
    head = (b"\x8c\x80"                                     # message-type: m-send-req
            + b"\x98" + _cstr(f"T{int(time.time() * 1000):x}")  # transaction-id
            + b"\x8d\x92"                                   # mms-version 1.2
            + b"\x89\x01\x81"                               # from: insert-address-token
            + b"\x97" + _cstr(f"{to}/TYPE=PLMN")            # to
            + b"\x84\xa3")                                  # content-type: multipart/mixed
    parts = []
    if caption:
        ctype = b"\x83\x81\xea"                             # text/plain; charset=utf-8
        parts.append((bytes([len(ctype)]) + ctype + b"\x8e" + _cstr("text.txt"), caption.encode()))
    ctype = bytes([MMS_IMAGE_TYPES[mime]]) + b"\x85" + _cstr(name)  # <type>; name=<name>
    parts.append((bytes([len(ctype)]) + ctype + b"\x8e" + _cstr(name), image))
    body = _uintvar(len(parts)) + b"".join(
        _uintvar(len(headers)) + _uintvar(len(data)) + headers + data for headers, data in parts
    )
    return head + body


# --------------------------------------------------------------- receiving

#: WSP well-known content types an MMS part is likely to use.
_WELL_KNOWN_TYPES = {
    0x03: "text/plain", 0x1D: "image/gif", 0x1E: "image/jpeg", 0x1F: "image/tiff",
    0x20: "image/png", 0x21: "image/vnd.wap.wbmp", 0x23: "application/vnd.wap.multipart.mixed",
    0x33: "application/vnd.wap.multipart.related", 0x3E: "application/smil",
}
_FROM, _CONTENT_TYPE, _CONTENT_LOCATION, _TRANSACTION_ID, _MESSAGE_TYPE = 0x89, 0x84, 0x83, 0x98, 0x8C


def parse_notification(pdu: bytes) -> dict:
    """An m-notification-ind (the WAP push that says an MMS is waiting):
    ``{"url", "from", "transaction_id"}``."""
    if pdu[:2] != b"\x8c\x82":
        raise ValueError("not an m-notification-ind")
    try:
        headers = _headers(pdu, 0)[0]
    except IndexError as error:
        raise ValueError("truncated MMS notice") from error
    return {
        "url": _text(headers.get(_CONTENT_LOCATION, b"")),
        "from": _address(headers.get(_FROM, b"")),
        "transaction_id": _text(headers.get(_TRANSACTION_ID, b"")),
    }


def parse_retrieved(pdu: bytes) -> dict:
    """An m-retrieve-conf (the downloaded MMS): ``{"from", "parts": [{"type", "name", "data"}]}``."""
    if pdu[:2] != b"\x8c\x84":
        raise ValueError("not an m-retrieve-conf")
    try:
        return _parse_retrieved(pdu)
    except IndexError as error:
        raise ValueError("truncated MMS") from error


def _parse_retrieved(pdu: bytes) -> dict:
    headers, offset = _headers(pdu, 0, stop_at=_CONTENT_TYPE)
    count, offset = _read_uintvar(pdu, offset)
    parts = []
    for _ in range(count):
        header_len, offset = _read_uintvar(pdu, offset)
        data_len, offset = _read_uintvar(pdu, offset)
        head, offset = pdu[offset:offset + header_len], offset + header_len
        data, offset = pdu[offset:offset + data_len], offset + data_len
        ctype, name = _part_type_and_name(head)
        parts.append({"type": ctype, "name": name, "data": data})
    return {"from": _address(headers.get(_FROM, b"")), "parts": parts}


def download_mms(a: Android, url: str, timeout: float = 120) -> bytes:
    """Fetches a waiting MMS through the platform MmsService and returns the
    m-retrieve-conf bytes. Needs root and a full-scope token.

    The platform service, not the messaging app, so the MMSC proxy is resolved on
    the MMS network (the TCL's stock app resolves it on Wi-Fi and fails). Same
    MediaStore trick as :func:`send_mms` for a Uri the service may write to; the
    bytes come back through ``base64`` as root because the app cannot stream them.
    """
    from . import shell  # late: shell lives in the package, which imports this module

    action = f"lubabs770.mmm.MMS_DOWNLOADED.{int(time.time() * 1000)}"
    resolver = a.call(a.context, "getContentResolver")
    downloads = a.call(a.cls("android.net.Uri"), "parse", "content://media/external/downloads")
    values = a.new("android.content.ContentValues")
    a.call(values, "put", "_display_name", f"mmm-in-{int(time.time() * 1000)}.pdu",
           sig=["java.lang.String", "java.lang.String"])
    item = a.call(resolver, "insert", downloads, values,
                  sig=["android.net.Uri", "android.content.ContentValues"])
    try:
        # MmsService writes into an existing file; an empty insert has none yet.
        a.call(a.call(resolver, "openOutputStream", item, sig=["android.net.Uri"]), "close")
        done = threading.Event()
        result: dict = {}
        a.subscribe([action], name=action, handler=lambda payload: (result.update(payload or {}), done.set()))
        try:
            intent = a.new("android.content.Intent", action, sig=["java.lang.String"])
            a.call(intent, "setPackage", a.call(a.context, "getPackageName"))
            pending = a.call(a.cls("android.app.PendingIntent"), "getBroadcast", a.context, 0, intent,
                             0x08000000,  # FLAG_UPDATE_CURRENT, mutable so the service can add extras
                             sig=["android.content.Context", "int", "android.content.Intent", "int"])
            manager = a.call(a.cls("android.telephony.SmsManager"), "getDefault")
            a.call(manager, "downloadMultimediaMessage", a.context, url, item, None, pending,
                   sig=["android.content.Context", "java.lang.String", "android.net.Uri",
                        "android.os.Bundle", "android.app.PendingIntent"])
            if not done.wait(timeout):
                raise MmmError(f"no MMS download result within {timeout:.0f} s")
        finally:
            a.unsubscribe(action)
        status = (result.get("extras") or {}).get("android.telephony.extra.MMS_HTTP_STATUS")
        if status:
            raise MmmError(f"MMS download failed: HTTP {status}")
        path = _data_path(a, resolver, item)
        data = base64.b64decode(shell(a, f"base64 -w 0 {_shell_quote(path)}", timeout_ms=60_000).strip())
        if not data:
            raise MmmError("MMS download produced an empty file")
        return data
    finally:
        a.call(resolver, "delete", item, None, None,
               sig=["android.net.Uri", "java.lang.String", "java.lang.String[]"])


def _data_path(a: Android, resolver: Any, item: Any) -> str:
    """The file behind a MediaStore row, as root sees it (FUSE paths map to /data/media)."""
    cursor = a.call(resolver, "query", item, ["_data"], None, None, None)
    try:
        if not a.call(cursor, "moveToFirst"):
            raise MmmError("MediaStore row vanished")
        path = str(a.call(cursor, "getString", 0))
    finally:
        a.call(cursor, "close")
    return path.replace("/storage/emulated/", "/data/media/", 1)


def _shell_quote(path: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        raise MmmError(f"unexpected MediaStore path {path!r}")
    return path


def _headers(pdu: bytes, offset: int, stop_at: int | None = None) -> tuple[dict[int, bytes], int]:
    """Header field → raw value bytes. Stops after [stop_at]'s value (the body follows it)."""
    headers: dict[int, bytes] = {}
    while offset < len(pdu):
        field = pdu[offset]
        offset += 1
        start = offset
        offset = _skip_value(pdu, offset)
        headers.setdefault(field, pdu[start:offset])
        if field == stop_at:
            break
    return headers, offset


def _skip_value(pdu: bytes, offset: int) -> int:
    """The end of one WSP-encoded value starting at [offset]."""
    first = pdu[offset]
    if first <= 30:                       # short-length, then that many bytes
        return offset + 1 + first
    if first == 31:                       # length-quote, uintvar length
        length, offset = _read_uintvar(pdu, offset + 1)
        return offset + length
    if first < 128:                       # text, NUL-terminated
        end = pdu.index(0, offset)
        return end + 1
    return offset + 1                     # short integer


def _value_body(raw: bytes) -> bytes:
    """A value with its length prefix (if any) removed."""
    if raw and raw[0] <= 30:
        return raw[1:1 + raw[0]]
    if raw and raw[0] == 31:
        length, offset = _read_uintvar(raw, 1)
        return raw[offset:offset + length]
    return raw


def _text(raw: bytes) -> str:
    """An encoded-string-value: plain text, or length + charset + text."""
    body = _value_body(raw)
    if raw and raw[0] <= 31 and body:     # charset first: one short int, or a long int
        body = body[1:] if body[0] >= 128 else body[1 + body[0]:]
    if body[:1] == b"\x7f":               # text quote
        body = body[1:]
    return body.split(b"\0", 1)[0].decode("utf-8", "replace")


def _address(raw: bytes) -> str:
    """From: value-length, 0x80 address-present, encoded-string; ``/TYPE=PLMN`` dropped."""
    body = _value_body(raw)
    if not body or body[0] != 0x80:
        return ""
    return _text(body[1:]).split("/TYPE=", 1)[0]


def _part_type_and_name(head: bytes) -> tuple[str, str]:
    """A multipart entry's content type and filename (name param or Content-Location)."""
    end = _skip_value(head, 0)
    ctype_raw, rest = head[:end], head[end:]
    body = _value_body(ctype_raw)
    if ctype_raw[0] >= 128:
        ctype, params = _WELL_KNOWN_TYPES.get(ctype_raw[0] & 0x7F, "application/octet-stream"), b""
    elif ctype_raw[0] <= 31:              # general form: type then parameters
        if body[0] >= 128:
            ctype, params = _WELL_KNOWN_TYPES.get(body[0] & 0x7F, "application/octet-stream"), body[1:]
        else:
            name_end = body.index(0)
            ctype, params = body[:name_end].decode("ascii", "replace"), body[name_end + 1:]
    else:
        ctype, params = _text(ctype_raw), b""
    name = ""
    for marker in (b"\x85", b"\x97"):     # name (1.1), name (1.4) parameters
        at = params.find(marker)
        if at >= 0 and at + 1 < len(params) and 32 <= params[at + 1] < 128:
            name = params[at + 1:].split(b"\0", 1)[0].decode("utf-8", "replace")
            break
    if not name:
        at = rest.find(bytes([0x80 | 0x0E]))   # Content-Location
        if at >= 0:
            name = rest[at + 1:].split(b"\0", 1)[0].decode("utf-8", "replace")
    return ctype, name


def _read_uintvar(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset


def _cstr(text: str) -> bytes:
    return text.encode() + b"\0"


def _uintvar(n: int) -> bytes:
    out = [n & 0x7F]
    while n > 0x7F:
        n >>= 7
        out.insert(0, 0x80 | (n & 0x7F))
    return bytes(out)
