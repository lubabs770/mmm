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


# ----------------------------------------------------------- apps, network, notifications

from mmm.android import apps, network, notifications  # noqa: E402


def test_pm_lists_become_rows():
    out = "package:com.a\npackage:com.b\npackage:com.c\n@\npackage:com.a\n@\npackage:com.c"
    assert apps.parse_pm_lists(out) == [
        {"package": "com.a", "system": True, "enabled": True},
        {"package": "com.b", "system": False, "enabled": True},
        {"package": "com.c", "system": False, "enabled": False},
    ]


@pytest.mark.parametrize("bad", ["", "com", "com.a; reboot", "com.a b", "$(id).x", "-f.x"])
def test_set_enabled_refuses_anything_but_a_package_name(bad):
    with pytest.raises(ValueError):
        apps.set_enabled(None, bad, True)  # validated before the phone is touched


def test_set_enabled_will_not_cut_its_own_link():
    with pytest.raises(ValueError):
        apps.set_enabled(None, "com.tailscale.ipn", False)


def test_wifi_status_and_saved_networks():
    status = ('Wifi is enabled\nWifi is connected to "Home Net"\n'
              "WifiInfo: SSID: Home Net, RSSI: -39, Link speed: 65Mbps, Tx Link speed: 65Mbps")
    assert network.parse_wifi_status(status) == {"enabled": True, "ssid": "Home Net", "rssi": -39, "link_mbps": 65}
    assert network.parse_wifi_status("Wifi is disabled")["ssid"] is None
    saved = "Network Id      SSID                         Security type\n2            Starbucks WiFi                   open\n4            MyOptimum 82dbef                 wpa2"
    assert network.parse_saved_networks(saved) == [
        {"id": 2, "ssid": "Starbucks WiFi", "security": "open"},
        {"id": 4, "ssid": "MyOptimum 82dbef", "security": "wpa2"},
    ]


def test_dumpsys_notifications_are_parsed_and_deduplicated():
    dump = """
    NotificationRecord(0x0a8: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null importance=2
          android.title=String (Family)
          android.text=String (Leah added you)
          android.title=String (ignored second title)
    NotificationRecord(0x05e: pkg=com.android.systemui user=UserHandle{-1} id=3
          android.title=String (Battery is low)
          android.text=String (9% remaining)
    NotificationRecord(0x0ff: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null
          android.title=String (Family)
          android.text=String (Leah added you)
    NotificationRecord(0x099: pkg=com.silent user=UserHandle{0} id=9
"""
    assert notifications.parse_dumpsys(dump) == [
        {"package": "com.whatsapp", "title": "Family", "text": "Leah added you"},
        {"package": "com.android.systemui", "title": "Battery is low", "text": "9% remaining"},
    ]


# ------------------------------------------------------------------- receiving MMS

from mmm.android import sms  # noqa: E402

_SENDER = b"\x80+15550100199/TYPE=PLMN\x00"
NOTIFICATION = (b"\x8c\x82" + b"\x98T123\x00" + b"\x8d\x92"
                + b"\x89" + bytes([len(_SENDER)]) + _SENDER
                + b"\x88\x05\x81\x03\x01\x51\x80"           # expiry: relative, 86400 s
                + b"\x83http://mmsc.example/abc\x00")


def retrieve_conf(parts: list[bytes]) -> bytes:
    """m-retrieve-conf: From, then multipart/related with [parts] (each headers+data pre-joined)."""
    sender = b"\x80+15550100199/TYPE=PLMN\x00"
    head = (b"\x8c\x84" + b"\x98T123\x00" + b"\x8d\x92"
            + b"\x89" + bytes([len(sender)]) + sender
            + b"\x84\x1b\xb3\x89application/smil\x00\x8a<smil>\x00")
    return head + sms._uintvar(len(parts)) + b"".join(parts)


def part(headers: bytes, data: bytes) -> bytes:
    return sms._uintvar(len(headers)) + sms._uintvar(len(data)) + headers + data


def test_an_mms_notice_gives_its_url_and_sender():
    notice = sms.parse_notification(NOTIFICATION)
    assert notice == {"url": "http://mmsc.example/abc", "from": "+15550100199", "transaction_id": "T123"}
    with pytest.raises(ValueError):
        sms.parse_notification(retrieve_conf([]))


def test_a_retrieved_mms_yields_its_parts():
    pdu = retrieve_conf([
        part(b"\x1b" + b"application/smil\x00" + b"\x85smil.xml\x00", b"<smil/>"),
        part(b"\x03\x83\x81\xea" + b"\x8etext.txt\x00", b"look at this"),
        part(b"\x0d\x9e\x85photo.jpg\x00", b"\xff\xd8JPEG"),
        part(b"\x9d" + b"\x8eanim.gif\x00", b"GIF89a"),
    ])
    message = sms.parse_retrieved(pdu)
    assert message["from"] == "+15550100199"
    assert [(p["type"], p["name"], p["data"]) for p in message["parts"]] == [
        ("application/smil", "smil.xml", b"<smil/>"),
        ("text/plain", "text.txt", b"look at this"),
        ("image/jpeg", "photo.jpg", b"\xff\xd8JPEG"),
        ("image/gif", "anim.gif", b"GIF89a"),
    ]


def test_what_send_mms_builds_decodes_back():
    built = sms.mms_pdu("+15550100199", b"\xff\xd8x", "a.jpg", "image/jpeg", "hi")
    body = built[built.index(b"\x84\xa3") + 2:]
    as_retrieved = retrieve_conf([])[:-1] + body  # same multipart, under retrieve-conf headers
    parts = sms.parse_retrieved(as_retrieved)["parts"]
    assert [(p["type"], p["name"], p["data"]) for p in parts] == [
        ("text/plain", "text.txt", b"hi"), ("image/jpeg", "a.jpg", b"\xff\xd8x")]
