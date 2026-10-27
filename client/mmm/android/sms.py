"""Text and picture messages."""

from __future__ import annotations

import time
from typing import Any

from ..client import Android
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


def _cstr(text: str) -> bytes:
    return text.encode() + b"\0"


def _uintvar(n: int) -> bytes:
    out = [n & 0x7F]
    while n > 0x7F:
        n >>= 7
        out.insert(0, 0x80 | (n & 0x7F))
    return bytes(out)
