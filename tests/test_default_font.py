"""Default font picker should prefer Heavy/Medium-weight CJK fonts.

Day58 production used `Hiragino Sans GB W3` (too thin) as default.
The new policy: pick a weight ≥ Medium when available, never W3/Light.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from utils import find_chinese_font, _find_system_font, detect_platform  # noqa: E402


def test_returns_a_font():
    path, name = find_chinese_font(None)
    assert path is not None or name is not None


def test_macos_default_avoids_thin_weights():
    """On a Mac, the default font name MUST NOT be a thin weight (W3/Light/Regular only)."""
    if detect_platform() != "macos":
        return
    path, name = find_chinese_font(None)
    if not name:
        return
    # Forbidden thin-weight markers
    thin_markers = [r"\bW3\b", r"\bW0\b", r"\bLight\b", r"\bThin\b", r"\bExtraLight\b"]
    for marker in thin_markers:
        assert not re.search(marker, name, re.IGNORECASE), (
            f"Default font fell back to a thin weight: {name!r}. "
            "Heavy/Medium/Bold/SemiBold/Regular preferred over W3/Light/Thin."
        )


def test_macos_system_picker_prefers_stheiti_medium_over_pingfang_regular():
    """When both STHeiti Medium and PingFang are present (typical Mac), prefer the medium weight."""
    if detect_platform() != "macos":
        return
    if not os.path.isfile("/System/Library/Fonts/STHeiti Medium.ttc"):
        return
    path, name = _find_system_font("macos")
    # Path or name must indicate a Medium/Heavy/Bold weight
    combined = f"{path or ''} {name or ''}".lower()
    assert any(w in combined for w in ("medium", "heavy", "bold", "semibold")), (
        f"_find_system_font on macOS should prefer Medium weight when available. "
        f"Got path={path!r} name={name!r}"
    )
