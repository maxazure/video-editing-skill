import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from export_edl import (  # noqa: E402
    build_events,
    frames_to_timecode,
    load_cut_list_segments,
    load_render_config_segments,
    render_edl,
    seconds_to_timecode,
)


def test_timecode_helpers_use_non_drop_frame_math():
    assert frames_to_timecode(0, 30.0) == "00:00:00:00"
    assert frames_to_timecode(31, 30.0) == "00:00:01:01"
    assert seconds_to_timecode(3661.5, 30.0) == "01:01:01:15"


def test_load_cut_list_uses_keep_segments_and_source_override(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    cut_list.write_text(json.dumps({
        "kind": "rough_cut",
        "keep_segments": [
            {"start": 0.0, "end": 1.25},
            {"start": 2.0, "end": 3.0},
        ],
    }), encoding="utf-8")

    segments = load_cut_list_segments(str(cut_list), source_override="origin/talking.mp4")

    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == 1.25
    assert segments[0].source.endswith(os.path.join("origin", "talking.mp4"))


def test_build_events_make_record_time_contiguous(tmp_path):
    cut_list = tmp_path / "jump_cut.json"
    cut_list.write_text(json.dumps({
        "input": "talking.mp4",
        "keep_segments": [
            {"start": 1.0, "end": 2.0},
            {"start": 4.0, "end": 4.5},
        ],
    }), encoding="utf-8")
    segments = load_cut_list_segments(str(cut_list))
    events = build_events(segments, fps=30.0)

    assert events[0].source_in_tc == "00:00:01:00"
    assert events[0].record_in_tc == "00:00:00:00"
    assert events[0].record_out_tc == "00:00:01:00"
    assert events[1].source_in_tc == "00:00:04:00"
    assert events[1].record_in_tc == "00:00:01:00"
    assert events[1].record_out_tc == "00:00:01:15"


def test_render_edl_includes_cmx_style_event_and_source_comments(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    cut_list.write_text(json.dumps({
        "input": "A Cam.mov",
        "keep_segments": [{"start": 0.0, "end": 2.0}],
    }), encoding="utf-8")
    events = build_events(load_cut_list_segments(str(cut_list)), fps=24.0)
    text = render_edl(events, title="DAY58")

    assert text.startswith("TITLE: DAY58\nFCM: NON-DROP FRAME")
    assert "001  ACAM     V     C" in text
    assert "00:00:00:00 00:00:02:00 00:00:00:00 00:00:02:00" in text
    assert "* SOURCE FILE:" in text


def test_load_render_config_supports_direct_start_end(tmp_path):
    config = tmp_path / "render_config.json"
    config.write_text(json.dumps({
        "clips": [
            {"video": "a.mp4", "start": 3.0, "end": 4.25, "text": "direct range"},
        ],
    }), encoding="utf-8")

    segments = load_render_config_segments(str(config))

    assert segments[0].source.endswith("a.mp4")
    assert segments[0].start == 3.0
    assert segments[0].end == 4.25
    assert segments[0].text == "direct range"


def test_load_render_config_resolves_transcript_segment_ids(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "skip"},
            {"id": 2, "start": 1.5, "end": 3.0, "text": "use me"},
        ],
    }), encoding="utf-8")
    config = tmp_path / "render_config.json"
    config.write_text(json.dumps({
        "clips": [
            {"video": "talking.mp4", "transcript": str(transcript), "segment_id": 2},
        ],
    }), encoding="utf-8")

    segments = load_render_config_segments(str(config))

    assert segments[0].start == 1.5
    assert segments[0].end == 3.0
    assert segments[0].text == "use me"


def test_cli_writes_edl_and_manifest(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    output = tmp_path / "rough_cut.edl"
    cut_list.write_text(json.dumps({
        "input": str(tmp_path / "talking.mp4"),
        "keep_segments": [
            {"start": 0.0, "end": 1.0},
            {"start": 2.0, "end": 2.5},
        ],
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "export_edl.py"),
            "--cut-list", str(cut_list),
            "--output", str(output),
            "--fps", "30",
            "--title", "ROUGH",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert output.with_suffix(".edl.json").exists()
    manifest = json.loads(output.with_suffix(".edl.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "nle_handoff_edl"
    assert manifest["event_count"] == 2
    assert manifest["duration_seconds"] == 1.5
