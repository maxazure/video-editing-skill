import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from timeline_view import (  # noqa: E402
    TimelineWindow,
    build_ffmpeg_command,
    build_filter,
    clamp_window,
    explicit_window,
    grid_for_frames,
    load_cut_windows,
)


def test_clamp_window_keeps_requested_radius_near_end():
    window = clamp_window(center=9.5, radius=2.0, duration=10.0)
    assert window == TimelineWindow(start=6.0, end=10.0, duration=4.0, label="at_9.50s")


def test_explicit_window_clamps_to_media_duration():
    window = explicit_window(start=1.25, end=8.0, duration=5.5)
    assert window == TimelineWindow(start=1.25, end=5.5, duration=4.25, label="1.25-5.50s")


def test_grid_for_frames_caps_columns():
    assert grid_for_frames(12) == (6, 2)
    assert grid_for_frames(5) == (5, 1)


def test_build_filter_stacks_waveform_when_audio_exists():
    filter_arg = build_filter(
        TimelineWindow(start=3.0, end=6.0, duration=3.0),
        frame_count=12,
        width=1600,
        waveform_height=180,
        has_audio=True,
    )
    assert "tile=6x2" in filter_arg
    assert "showwavespic=s=1600x180" in filter_arg
    assert "[film][wave]vstack=inputs=2[out]" in filter_arg


def test_build_filter_omits_waveform_without_audio():
    filter_arg = build_filter(
        TimelineWindow(start=0.0, end=3.0, duration=3.0),
        frame_count=6,
        width=1200,
        waveform_height=180,
        has_audio=False,
    )
    assert "showwavespic" not in filter_arg
    assert "[film]null[out]" in filter_arg


def test_build_ffmpeg_command_targets_single_png_frame():
    cmd = build_ffmpeg_command(
        "in.mp4",
        "view.png",
        TimelineWindow(start=2.0, end=5.0, duration=3.0),
        frame_count=9,
        width=900,
        has_audio=False,
    )
    joined = " ".join(cmd)
    assert cmd[:3] == ["ffmpeg", "-y", "-hide_banner"]
    assert "-ss 2.0000" in joined
    assert "-t 3.0000" in joined
    assert "-frames:v 1" in joined
    assert cmd[-1] == "view.png"


def test_load_cut_windows_uses_removed_segment_midpoints(tmp_path):
    cut_list = tmp_path / "cuts.json"
    cut_list.write_text(json.dumps({
        "removed_segments": [
            {"start": 2.0, "end": 4.0, "duration": 2.0},
            {"start": 9.0, "end": 10.0, "duration": 1.0},
        ]
    }), encoding="utf-8")

    windows = load_cut_windows(
        str(cut_list),
        key="removed_segments",
        radius=1.0,
        duration=10.0,
        limit=10,
    )

    assert windows == [
        TimelineWindow(start=2.0, end=4.0, duration=2.0, label="removed_segments_001_2.00-4.00s"),
        TimelineWindow(start=8.0, end=10.0, duration=2.0, label="removed_segments_002_9.00-10.00s"),
    ]
