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
