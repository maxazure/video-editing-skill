"""multi_export — aspect-ratio filter selection + command-build smoke tests.

We don't actually run ffmpeg in tests (large videos, slow). Instead we
verify the constructed commands and aspect filter strings.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from multi_export import PRESETS, _source_aspect_filter, build_ffmpeg_command  # noqa: E402
from smart_reframe import build_reframe_plan  # noqa: E402


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


def test_build_command_can_use_single_segment_reframe_plan():
    plan = build_reframe_plan(
        video_path="in.mp4",
        src_w=1920,
        src_h=1080,
        duration=10.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[
            {"time": 2.0, "label": "face", "bbox": [1450, 200, 1600, 380]},
        ],
    )

    cmd = build_ffmpeg_command(
        "in.mp4",
        "out.mp4",
        PRESETS["douyin"],
        src_w=1920,
        src_h=1080,
        src_duration=10.0,
        reframe_plan=plan,
    )

    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "crop=608:1080" in vf
    assert "scale=1080:1920" in vf


def test_build_command_uses_filter_complex_for_multi_segment_reframe_plan():
    plan = build_reframe_plan(
        video_path="in.mp4",
        src_w=1920,
        src_h=1080,
        duration=6.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        scene_plan={
            "scenes": [
                {"scene_id": "scene_001", "start": 0.0, "end": 3.0},
                {"scene_id": "scene_002", "start": 3.0, "end": 6.0},
            ]
        },
        detections_payload=[
            {"time": 1.0, "label": "face", "bbox": [120, 200, 240, 380]},
            {"time": 4.0, "label": "face", "bbox": [1500, 200, 1640, 380]},
        ],
        merge_tolerance_px=0,
    )

    cmd = build_ffmpeg_command(
        "in.mp4",
        "out.mp4",
        PRESETS["douyin"],
        src_w=1920,
        src_h=1080,
        src_duration=6.0,
        reframe_plan=plan,
    )

    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=3.000" in fc
    assert "concat=n=2:v=1:a=0[vout]" in fc
    assert "-map" in cmd
    assert "[vout]" in cmd
