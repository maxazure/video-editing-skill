import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from audio_sync import (  # noqa: E402
    audio_filter_from_offset,
    build_audio_sync_plan,
    build_replace_audio_command,
    emit_markdown,
    estimate_offset,
)


def _impulse_envelope(length=80, event_at=30):
    values = [0.05] * length
    for index, amp in enumerate([1.0, 0.75, 0.35, 0.15]):
        values[event_at + index] = amp
    values[event_at + 10] = 0.6
    values[event_at + 11] = 0.25
    return values


def test_estimate_offset_positive_means_delay_external_track():
    reference = _impulse_envelope(event_at=30)
    external = _impulse_envelope(event_at=25)

    result = estimate_offset(
        reference,
        external,
        frame_seconds=0.04,
        max_offset_seconds=1.0,
        min_overlap_seconds=0.4,
    )

    assert result["offset_seconds"] == 0.2
    assert result["lag_frames"] == 5
    assert result["confidence"] >= 0.8


def test_estimate_offset_negative_means_trim_external_front():
    reference = _impulse_envelope(event_at=20)
    external = _impulse_envelope(event_at=26)

    result = estimate_offset(
        reference,
        external,
        frame_seconds=0.04,
        max_offset_seconds=1.0,
        min_overlap_seconds=0.4,
    )

    assert result["offset_seconds"] == -0.24
    assert result["lag_frames"] == -6


def test_audio_filter_from_offset_delays_or_trims():
    assert audio_filter_from_offset(0.32, duration=12.5).startswith("adelay=320:all=1")
    assert "atrim=duration=12.5" in audio_filter_from_offset(0.32, duration=12.5)
    assert audio_filter_from_offset(-0.48).startswith("atrim=start=0.48")


def test_build_replace_audio_command_maps_reference_video_and_synced_audio(tmp_path):
    command = build_replace_audio_command(
        reference_media=str(tmp_path / "camera.mp4"),
        external_audio=str(tmp_path / "lav.wav"),
        output_media=str(tmp_path / "synced.mp4"),
        offset_seconds=-0.25,
        duration=42.0,
    )

    joined = " ".join(command)
    assert "[1:a]atrim=start=0.25" in joined
    assert "-map 0:v:0? -map [synced_a]" in joined
    assert command[-1].endswith("synced.mp4")


def test_manual_audio_sync_plan_writes_reviewable_artifact(tmp_path):
    reference = tmp_path / "camera.mp4"
    external = tmp_path / "lav.wav"
    output = tmp_path / "synced.mp4"
    reference.write_bytes(b"fake")
    external.write_bytes(b"fake")

    plan = build_audio_sync_plan(
        reference_media=str(reference),
        external_audio=str(external),
        offset_seconds=0.18,
        replace_output=str(output),
    )

    assert plan["version"] == "audio_sync_plan.v1"
    assert plan["status"] == "ready"
    assert plan["method"] == "manual_offset"
    assert plan["summary"]["offset_seconds"] == 0.18
    assert plan["summary"]["blocking"] == 0
    assert plan["replace_audio"]["command"]

    markdown = emit_markdown(plan)
    assert "Audio Sync Plan" in markdown
    assert "Replace Audio Command" in markdown


def test_cli_writes_json_and_markdown_with_manual_offset(tmp_path):
    reference = tmp_path / "camera.mp4"
    external = tmp_path / "lav.wav"
    out_json = tmp_path / "audio_sync.json"
    out_md = tmp_path / "audio_sync.md"
    reference.write_bytes(b"fake")
    external.write_bytes(b"fake")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "audio_sync.py"),
            "--reference-media",
            str(reference),
            "--external-audio",
            str(external),
            "--offset",
            "0.12",
            "--replace-output",
            str(tmp_path / "synced.mp4"),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["offset_seconds"] == 0.12
    assert "Audio Sync Plan" in out_md.read_text(encoding="utf-8")
