import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from export_edl import build_events, load_cut_list_segments, load_render_config_segments  # noqa: E402
from export_otio import build_otio_document, render_otio  # noqa: E402


def test_build_otio_document_creates_video_and_audio_tracks(tmp_path):
    source = tmp_path / "A Cam.mov"
    cut_list = tmp_path / "rough_cut.json"
    cut_list.write_text(json.dumps({
        "input": str(source),
        "keep_segments": [
            {"start": 1.0, "end": 2.0, "label": "hook"},
            {"start": 4.0, "end": 4.5, "label": "payoff"},
        ],
    }), encoding="utf-8")

    events = build_events(load_cut_list_segments(str(cut_list)), fps=30.0)
    doc = build_otio_document(events, title="DAY58", fps=30.0)

    assert doc["OTIO_SCHEMA"] == "Timeline.1"
    assert doc["name"] == "DAY58"
    tracks = doc["tracks"]["children"]
    assert [track["kind"] for track in tracks] == ["Video", "Audio"]
    assert [track["name"] for track in tracks] == ["V1", "A1"]
    assert len(tracks[0]["children"]) == 2

    first_clip = tracks[0]["children"][0]
    assert first_clip["OTIO_SCHEMA"] == "Clip.2"
    assert first_clip["name"] == "hook"
    assert first_clip["media_reference"]["target_url"].startswith("file://")
    assert first_clip["source_range"]["start_time"]["value"] == 30
    assert first_clip["source_range"]["duration"]["value"] == 30
    assert first_clip["metadata"]["video_editing_skill"]["record_start"] == 0.0


def test_build_otio_document_can_write_video_only(tmp_path):
    cut_list = tmp_path / "jump_cut.json"
    cut_list.write_text(json.dumps({
        "input": str(tmp_path / "talking.mp4"),
        "keep_segments": [{"start": 0.0, "end": 1.0}],
    }), encoding="utf-8")

    events = build_events(load_cut_list_segments(str(cut_list)), fps=24.0)
    doc = build_otio_document(events, title="Video Only", fps=24.0, include_audio_track=False)

    tracks = doc["tracks"]["children"]
    assert len(tracks) == 1
    assert tracks[0]["kind"] == "Video"


def test_render_otio_can_include_transcript_metadata(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {"id": 7, "start": 2.0, "end": 3.25, "text": "use this line"},
        ],
    }), encoding="utf-8")
    config = tmp_path / "render_config.json"
    config.write_text(json.dumps({
        "clips": [
            {"video": "talking.mp4", "transcript": str(transcript), "segment_id": 7},
        ],
    }), encoding="utf-8")

    events = build_events(load_render_config_segments(str(config)), fps=30.0)
    data = json.loads(render_otio(
        events,
        title="Transcript",
        fps=30.0,
        include_transcript=True,
    ))

    clip_meta = data["tracks"]["children"][0]["children"][0]["metadata"]["video_editing_skill"]
    assert clip_meta["transcript"] == "use this line"
    assert clip_meta["source_start"] == 2.0
    assert clip_meta["source_end"] == 3.25


def test_cli_writes_otio_and_manifest(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    output = tmp_path / "rough_cut.otio"
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
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "export_otio.py"),
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
    assert output.with_suffix(".otio.json").exists()
    otio_doc = json.loads(output.read_text(encoding="utf-8"))
    manifest = json.loads(output.with_suffix(".otio.json").read_text(encoding="utf-8"))
    assert otio_doc["OTIO_SCHEMA"] == "Timeline.1"
    assert manifest["kind"] == "nle_handoff_otio"
    assert manifest["event_count"] == 2
    assert manifest["track_count"] == 2
    assert manifest["duration_seconds"] == 1.5


def test_cli_can_write_video_only_without_manifest(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    output = tmp_path / "video_only.otio"
    cut_list.write_text(json.dumps({
        "input": str(tmp_path / "talking.mp4"),
        "keep_segments": [{"start": 0.0, "end": 1.0}],
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "export_otio.py"),
            "--cut-list", str(cut_list),
            "--output", str(output),
            "--fps", "24",
            "--no-audio-track",
            "--no-manifest",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert not output.with_suffix(".otio.json").exists()
    otio_doc = json.loads(output.read_text(encoding="utf-8"))
    tracks = otio_doc["tracks"]["children"]
    assert len(tracks) == 1
    assert tracks[0]["kind"] == "Video"
