"""multi_export — aspect-ratio filter selection + command-build smoke tests.

We don't actually run ffmpeg in tests (large videos, slow). Instead we
verify the constructed commands and aspect filter strings.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from multi_export import PRESETS, _source_aspect_filter, build_ffmpeg_command  # noqa: E402


def test_three_presets_present():
    assert set(PRESETS.keys()) >= {"xhs", "douyin", "wxch"}


def test_xhs_is_3_4():
    p = PRESETS["xhs"]
    assert (p.width, p.height) == (1080, 1440)


def test_douyin_is_9_16():
    p = PRESETS["douyin"]
    assert (p.width, p.height) == (1080, 1920)


def test_wxch_caps_duration_to_60s():
    assert PRESETS["wxch"].max_duration_seconds == 60.0


def test_aspect_filter_same_ratio():
    # 9:16 → 9:16: just scale
    f = _source_aspect_filter(1080, 1920, 1080, 1920)
    assert f.startswith("scale=")


def test_aspect_filter_9_16_to_3_4_crops_top_bottom():
    f = _source_aspect_filter(1080, 1920, 1080, 1440)
    assert "crop=" in f and "scale=1080:1440" in f


def test_aspect_filter_16_9_to_9_16_crops_sides():
    f = _source_aspect_filter(1920, 1080, 1080, 1920)
    assert "crop=" in f and "scale=1080:1920" in f


def test_build_command_includes_t_when_capped():
    """wxch caps duration → -t should appear in the command."""
    cmd = build_ffmpeg_command("in.mp4", "out.mp4", PRESETS["wxch"],
                                src_w=1080, src_h=1920, src_duration=120.0)
    assert "-t" in cmd
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "60.00"


def test_build_command_no_t_when_short():
    """When source is already shorter than the cap, no -t is added."""
    cmd = build_ffmpeg_command("in.mp4", "out.mp4", PRESETS["wxch"],
                                src_w=1080, src_h=1920, src_duration=30.0)
    assert "-t" not in cmd


def test_build_command_uses_faststart():
    cmd = build_ffmpeg_command("in.mp4", "out.mp4", PRESETS["xhs"],
                                src_w=1080, src_h=1920, src_duration=60.0)
    assert "-movflags" in cmd
    idx = cmd.index("-movflags")
    assert cmd[idx + 1] == "+faststart"
