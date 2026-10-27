from __future__ import annotations

import pytest

from mmm import config as config_module
from mmm.config import Config, Profile

SAMPLE = """
default = "phone"

[profiles.local]
transport = "local"
token = "aaa"
port = 7357

[profiles.phone]
transport = "ssh"
token = "bbb"
ssh_host = "phone"
ssh_port = 8022
port = 7357
local_port = 7400
"""


def test_parse_reads_profiles_and_the_default():
    config = config_module.parse(SAMPLE)
    assert config.default == "phone"
    assert set(config.profiles) == {"local", "phone"}
    phone = config.profile()
    assert phone.transport == "ssh"
    assert phone.ssh_port == 8022
    assert phone.connect_port == 7400


def test_the_mini_toml_fallback_agrees_with_tomllib():
    # Python 3.10 has no tomllib, so the fallback has to read the same file.
    mini = config_module._mini_toml(SAMPLE)
    assert mini["default"] == "phone"
    assert mini["profiles"]["phone"]["ssh_port"] == 8022
    assert mini["profiles"]["local"]["transport"] == "local"


def test_a_local_profile_connects_to_its_own_port():
    profile = Profile(name="local", token="x", transport="local", port=7357)
    assert profile.connect_port == 7357
    assert profile.local_port == 7357


def test_an_unknown_transport_is_refused():
    with pytest.raises(ValueError):
        Profile(name="x", token="t", transport="carrier-pigeon")


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MMM_CONFIG", str(path))
    config = Config(default="phone")
    config.profiles["phone"] = Profile(
        name="phone",
        token="bbb",
        transport="ssh",
        ssh_host="phone",
        ssh_port=8022,
        local_port=7400,
    )
    saved = config_module.save(config)
    assert saved == path
    assert oct(path.stat().st_mode)[-3:] == "600"

    back = config_module.load()
    profile = back.profile("phone")
    assert profile.token == "bbb"
    assert profile.ssh_host == "phone"
    assert profile.connect_port == 7400


def test_the_profile_can_be_chosen_by_environment(monkeypatch):
    config = config_module.parse(SAMPLE)
    monkeypatch.setenv("MMM_PROFILE", "local")
    assert config.profile().name == "local"


def test_a_missing_profile_says_what_there_is():
    config = config_module.parse(SAMPLE)
    with pytest.raises(LookupError) as caught:
        config.profile("nope")
    assert "local" in str(caught.value)


def test_no_config_at_all_asks_for_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("MMM_CONFIG", str(tmp_path / "missing.toml"))
    config = config_module.load()
    with pytest.raises(LookupError) as caught:
        config.profile()
    assert "mmm pair" in str(caught.value)


def test_unknown_keys_are_kept_rather_than_dropped():
    config = config_module.parse('[profiles.x]\ntransport = "local"\nfuture_thing = 1\n')
    assert config.profiles["x"].extra == {"future_thing": 1}


def test_a_tailnet_profile_dials_the_phone_directly(tmp_path, monkeypatch):
    monkeypatch.setenv("MMM_CONFIG", str(tmp_path / "config.toml"))
    config = Config(default="phone")
    config.profiles["phone"] = Profile(
        name="phone", token="t", transport="tailnet", host="phone", port=7400
    )
    config_module.save(config)
    profile = config_module.load().profile("phone")
    assert profile.transport == "tailnet"
    assert profile.host == "phone"
    assert profile.connect_port == 7400
