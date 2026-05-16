"""Audience profiles: structure, presence of the two ships (tech_pro, lifestyle),
and key required fields."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from profiles import load_profile, list_profiles  # noqa: E402


def test_tech_pro_profile_loads():
    p = load_profile("tech_pro")
    assert p["audience"]["name_zh"] == "科技/创业向"
    assert p["duration"]["default_seconds"] == 90
    assert p["audio"]["bgm_gain_below_voice_db"] == -16


def test_lifestyle_profile_loads():
    p = load_profile("lifestyle")
    assert p["audience"]["name_zh"] == "生活方式向"
    assert p["duration"]["default_seconds"] == 60


def test_list_profiles_contains_both():
    profiles = list_profiles()
    assert "tech_pro" in profiles
    assert "lifestyle" in profiles


def test_missing_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("nonexistent_profile_xyz")


def test_required_fields_present():
    """Every profile must define duration, cut, subtitle, audio, aspect."""
    for name in list_profiles():
        p = load_profile(name)
        for required in ("duration", "cut", "subtitle", "audio", "aspect"):
            assert required in p, f"profile {name!r} missing key {required!r}"
