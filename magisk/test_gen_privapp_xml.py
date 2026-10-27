"""Tests for the priv-app allowlist generator, which boot depends on."""

from __future__ import annotations

from pathlib import Path

import pytest

from gen_privapp_xml import MarkerError, privileged_permissions, render

MANIFEST = Path(__file__).resolve().parent.parent / "app/src/main/AndroidManifest.xml"


def test_the_real_manifest_yields_the_privileged_permissions():
    found = privileged_permissions(MANIFEST.read_text(encoding="utf-8"))
    assert found == [
        "android.permission.MODIFY_PHONE_STATE",
        "android.permission.READ_PRIVILEGED_PHONE_STATE",
        "android.permission.WRITE_SECURE_SETTINGS",
        "android.permission.START_ACTIVITIES_FROM_BACKGROUND",
    ]


def test_only_the_marked_block_is_used():
    manifest = """
    <uses-permission android:name="android.permission.SEND_SMS" />
    <!-- privileged: held only as a priv-app -->
    <uses-permission android:name="android.permission.MODIFY_PHONE_STATE" />
    <!-- /privileged -->
    <uses-permission android:name="android.permission.READ_SMS" />
    """
    assert privileged_permissions(manifest) == ["android.permission.MODIFY_PHONE_STATE"]


def test_duplicates_collapse_and_order_is_kept():
    manifest = """
    <!-- privileged -->
    <uses-permission android:name="android.permission.B" />
    <uses-permission android:name="android.permission.A" />
    <uses-permission android:name="android.permission.B" />
    <!-- /privileged -->
    """
    assert privileged_permissions(manifest) == ["android.permission.B", "android.permission.A"]


def test_a_missing_marker_is_an_error_rather_than_an_empty_allowlist():
    with pytest.raises(MarkerError):
        privileged_permissions("<manifest/>")
    with pytest.raises(MarkerError):
        privileged_permissions("<!-- privileged -->\n")
    with pytest.raises(MarkerError):
        privileged_permissions("<!-- privileged -->\n<!-- /privileged -->")


def test_the_rendered_xml_names_the_package_and_every_permission():
    xml = render(["android.permission.MODIFY_PHONE_STATE"])
    assert '<privapp-permissions package="lubabs770.mmm.modemizer">' in xml
    assert '<permission name="android.permission.MODIFY_PHONE_STATE"/>' in xml
    assert xml.startswith('<?xml version="1.0" encoding="utf-8"?>')
