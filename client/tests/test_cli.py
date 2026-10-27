from __future__ import annotations

import base64
import json

from mmm.cli import to_json
from mmm.client import json_default

# The start of a real SMS-DELIVER PDU: not valid UTF-8, so repr was the only way out.
PDU = bytes.fromhex("07919107739667f9040b917123662471f0")


def test_bytes_round_trip_through_the_json_output():
    line = json.dumps({"pdus": [PDU]}, default=json_default)
    encoded = json.loads(line)["pdus"][0]["$b64"]
    assert base64.b64decode(encoded) == PDU


def test_call_output_keeps_the_bytes_too():
    assert base64.b64decode(json.loads(to_json(PDU))["$b64"]) == PDU
