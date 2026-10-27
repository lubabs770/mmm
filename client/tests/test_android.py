from __future__ import annotations

import pytest

from mmm.android.sms import _uintvar, mms_pdu


def test_uintvar():
    assert _uintvar(0) == b"\x00"
    assert _uintvar(127) == b"\x7f"
    assert _uintvar(128) == b"\x81\x00"
    assert _uintvar(92160) == b"\x85\xd0\x00"


def test_mms_pdu_layout():
    pdu = mms_pdu("+15550100199", b"JPEG", "a.jpg", "image/jpeg", "hi")
    assert pdu.startswith(b"\x8c\x80\x98T")  # m-send-req, then the transaction id
    assert b"\x8d\x92\x89\x01\x81\x97+15550100199/TYPE=PLMN\x00\x84\xa3" in pdu
    body = pdu[pdu.index(b"\x84\xa3") + 2:]
    text_part = b"\x0e\x02" + b"\x03\x83\x81\xea" + b"\x8e" + b"text.txt\x00" + b"hi"
    image_part = b"\x10\x04" + b"\x08\x9e\x85a.jpg\x00" + b"\x8ea.jpg\x00" + b"JPEG"
    assert body == b"\x02" + text_part + image_part


def test_mms_pdu_without_caption_has_one_part():
    pdu = mms_pdu("+15550100199", b"GIF", "a.gif", "image/gif")
    assert pdu[pdu.index(b"\x84\xa3") + 2] == 1


def test_mms_pdu_rejects_unknown_type():
    with pytest.raises(ValueError):
        mms_pdu("+15550100199", b"x", "a.webp", "image/webp")
