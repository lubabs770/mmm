"""Wi-Fi state and saved networks. Needs root (``cmd wifi`` through su)."""

from __future__ import annotations

import re

from ..client import Android
from . import shell


def wifi(a: Android) -> dict:
    """``{"enabled", "ssid", "rssi", "link_mbps", "ip", "saved": [{"id", "ssid", "security"}]}``."""
    out = shell(a, "cmd wifi status; echo @; cmd wifi list-networks; echo @; ip -4 -o addr show wlan0")
    status, saved, addr = (re.split(r"^@$", out, flags=re.M) + ["", ""])[:3]
    info = parse_wifi_status(status)
    info["saved"] = parse_saved_networks(saved)
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", addr)
    info["ip"] = match.group(1) if match else None
    return info


def parse_wifi_status(text: str) -> dict:
    ssid = re.search(r'Wifi is connected to "(.*)"', text)
    rssi = re.search(r"RSSI: (-?\d+)", text)
    speed = re.search(r"Link speed: (\d+)Mbps", text)
    return {
        "enabled": "Wifi is enabled" in text,
        "ssid": ssid.group(1) if ssid else None,
        "rssi": int(rssi.group(1)) if rssi else None,
        "link_mbps": int(speed.group(1)) if speed else None,
    }


def parse_saved_networks(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        match = re.match(r"^(\d+)\s+(.*?)\s+(\S+)$", line.strip())
        if match:
            rows.append({"id": int(match.group(1)), "ssid": match.group(2), "security": match.group(3)})
    return rows
