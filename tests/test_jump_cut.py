import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import jump_cut  # noqa: E402
from jump_cut import (  # noqa: E402
    Segment,
    build_cut_plan,
    build_ffmpeg_command,
    build_keep_segments,
    parse_loudnorm_threshold,
    parse_silencedetect,
)


def test_parse_loudnorm_threshold_from_ffmpeg_json():
    log = '''
    [Parsed_loudnorm_0 @ x]
    {
      "input_i" : "-20.11",
      "input_thresh" : "-31.46",
      "target_offset" : "-0.08"
    }
    '''
    assert parse_loudnorm_threshold(log) == -31.46


def test_parse_loudnorm_threshold_uses_safe_fallback():
    assert parse_loudnorm_threshold("no json here", fallback=-36.0) == -36.0


def test_parse_silencedetect_handles_trailing_silence():
    log = "\n".join([
        "[silencedetect @ x] silence_start: 1.0",
        "[silencedetect @ x] silence_end: 1.8 | silence_duration: 0.8",
        "[silencedetect @ x] silence_start: 4.5",
    ])
    assert parse_silencedetect(log, duration=5.0) == [
        Segment(start=1.0, end=1.8, duration=0.8),
        Segment(start=4.5, end=5.0, duration=0.5),
    ]


def test_build_keep_segments_preserves_padding_around_cuts():
    keep = build_keep_segments(
        10.0,
        [Segment(start=2.0, end=4.0, duration=2.0), Segment(start=7.0, end=7.5, duration=0.5)],
        pad=0.1,
        min_keep=0.15,
    )
    assert keep == [
        Segment(start=0.0, end=2.1, duration=2.1),
        Segment(start=3.9, end=7.1, duration=3.2),
        Segment(start=7.4, end=10.0, duration=2.6),
    ]


def test_build_cut_plan_reports_speedup_and_removed_segments():
    plan = build_cut_plan(
        "talking.mp4",
        "talking.jumpcut.mp4",
        duration=8.0,
        silences=[Segment(start=2.0, end=4.0, duration=2.0)],
        noise_db=-34.5,
        min_silence=0.5,
        pad=0.0,
        min_keep=0.15,
    )
    assert plan["removed_seconds"] == 2.0
    assert plan["output_duration_estimate"] == 6.0
    assert plan["speedup_ratio"] == 1.333
    assert plan["fade_seconds"] == 0.03
    assert plan["removed_segments"] == [{"start": 2.0, "end": 4.0, "duration": 2.0}]
    assert plan["status"] == "blocked"
    assert plan["removal_budget"] == {
        "max_ratio": 0.2,
        "max_seconds": 1.6,
        "proposed_ratio": 0.25,
        "proposed_seconds": 2.0,
        "over_budget": True,
        "override": False,
    }
    assert plan["summary"]["blocking"] == 1


def test_explicit_override_records_warning_and_unblocks_plan():
    plan = build_cut_plan(
        "talking.mp4",
        "talking.jumpcut.mp4",
        duration=8.0,
        silences=[Segment(start=2.0, end=4.0, duration=2.0)],
        noise_db=-34.5,
        min_silence=0.5,
        pad=0.0,
        min_keep=0.15,
        allow_over_budget=True,
    )

    assert plan["status"] == "ready"
    assert plan["removal_budget"]["override"] is True
    assert plan["summary"] == {"blocking": 0, "warnings": 1}
    assert "explicit --allow-over-budget" in plan["warnings"][0]


def test_cli_refuses_render_when_removal_budget_is_exceeded(tmp_path, monkeypatch):
    input_path = tmp_path / "talking.mp4"
    output_path = tmp_path / "jumpcut.mp4"
    cut_list = tmp_path / "jump_cut.json"
    input_path.write_bytes(b"fake")
    metadata = {
        "format": {"duration": "10.0"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    }
    rendered = []
    monkeypatch.setattr(jump_cut, "probe_media", lambda _path: metadata)
    monkeypatch.setattr(jump_cut, "measure_adaptive_noise_db", lambda _path: -35.0)
    monkeypatch.setattr(
        jump_cut,
        "detect_silences",
        lambda *_args, **_kwargs: [Segment(start=2.0, end=5.0, duration=3.0)],
    )
    monkeypatch.setattr(jump_cut, "run_ffmpeg_with_fallback", lambda *_args, **_kwargs: rendered.append(True))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "jump_cut.py",
            str(input_path),
            "--output", str(output_path),
            "--cut-list", str(cut_list),
        ],
    )

    assert jump_cut.main() == 2
    assert rendered == []
    assert not output_path.exists()
    assert cut_list.exists()


def test_build_ffmpeg_command_uses_single_concat_encode_for_video():
    cmd = build_ffmpeg_command(
        "in.mp4",
        "out.mp4",
        [Segment(start=0.0, end=2.0, duration=2.0), Segment(start=3.0, end=5.0, duration=2.0)],
        has_video=True,
    )
    joined = " ".join(cmd)
    assert "-filter_complex" in cmd
    assert "trim=start=0.0000:end=2.0000" in joined
    assert "atrim=start=3.0000:end=5.0000" in joined
    assert "afade=t=in:st=0:d=0.0300" in joined
    assert "afade=t=out:st=1.9700:d=0.0300" in joined
    assert "concat=n=2:v=1:a=1[outv][outa]" in joined
    assert "-map" in cmd and "[outv]" in cmd and "[outa]" in cmd


def test_build_ffmpeg_command_can_disable_cut_fades():
    cmd = build_ffmpeg_command(
        "in.mp4",
        "out.mp4",
        [Segment(start=0.0, end=2.0, duration=2.0)],
        has_video=False,
        fade_seconds=0.0,
    )
    assert "afade=" not in " ".join(cmd)
    assert "concat=n=1:v=0:a=1[outa]" in " ".join(cmd)


def test_build_ffmpeg_command_clamps_fade_to_half_segment():
    cmd = build_ffmpeg_command(
        "in.mp4",
        "out.mp4",
        [Segment(start=0.0, end=0.04, duration=0.04)],
        has_video=False,
        fade_seconds=0.03,
    )
    joined = " ".join(cmd)
    assert "afade=t=in:st=0:d=0.0200" in joined
    assert "afade=t=out:st=0.0200:d=0.0200" in joined
