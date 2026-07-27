import json
import math
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import multicam_sync  # noqa: E402
from multicam_sync import (  # noqa: E402
    build_aligned_preview_command,
    build_multicam_sync_plan,
    common_overlap,
    coverage_for_offset,
    emit_markdown,
    evaluate_pairwise_consistency,
    select_audio_stream,
    validate_output_paths,
)


def test_coverage_for_positive_and_negative_offsets():
    assert coverage_for_offset(
        reference_duration=10.0,
        source_duration=8.0,
        offset_seconds=2.0,
    ) == ([2.0, 10.0], [0.0, 8.0])
    assert coverage_for_offset(
        reference_duration=10.0,
        source_duration=8.0,
        offset_seconds=-2.0,
    ) == ([0.0, 6.0], [2.0, 8.0])


def test_common_overlap_requires_every_angle():
    angles = [
        {"coverage_in_reference": [0.0, 10.0]},
        {"coverage_in_reference": [2.0, 9.0]},
        {"coverage_in_reference": [1.0, 8.0]},
    ]
    assert common_overlap(angles) == {"start": 2.0, "end": 8.0, "duration": 6.0}
    assert common_overlap(angles + [{"coverage_in_reference": None}]) is None


def test_preview_command_uses_local_starts_and_reference_audio(tmp_path):
    angles = [
        {
            "media": {"path": str(tmp_path / "a.mp4")},
            "alignment": {"offset_seconds": 0.0},
            "audio_stream": {"index": 2},
        },
        {
            "media": {"path": str(tmp_path / "b.mp4")},
            "alignment": {"offset_seconds": 1.5},
            "audio_stream": {"index": 0},
        },
        {
            "media": {"path": str(tmp_path / "c.mp4")},
            "alignment": {"offset_seconds": -0.5},
            "audio_stream": {"index": 0},
        },
    ]
    command = build_aligned_preview_command(
        angles,
        overlap={"start": 2.0, "end": 8.0, "duration": 6.0},
        output_path=str(tmp_path / "preview.mp4"),
        duration_seconds=4.0,
    )
    joined = " ".join(command)

    assert "-ss 2.0000 -i" in joined
    assert "-ss 0.5000 -i" in joined
    assert "-ss 2.5000 -i" in joined
    assert "xstack=inputs=3:layout=0_0|480_0|0_270" in joined
    assert "-map 0:a:2?" in joined
    assert "-t 4.000" in joined


def test_preview_refuses_to_overwrite_source(tmp_path):
    source = tmp_path / "a.mp4"
    angles = [
        {"media": {"path": str(source)}, "alignment": {"offset_seconds": 0}},
        {"media": {"path": str(tmp_path / "b.mp4")}, "alignment": {"offset_seconds": 0}},
    ]
    with pytest.raises(ValueError, match="overwrite"):
        build_aligned_preview_command(
            angles,
            overlap={"start": 0, "end": 2, "duration": 2},
            output_path=str(source),
        )


def test_apply_preview_failure_is_explicit_and_still_writes_report(tmp_path, monkeypatch):
    reference = tmp_path / "reference.mp4"
    source = tmp_path / "source.mp4"
    report = tmp_path / "multicam_sync.json"
    preview = tmp_path / "preview.mp4"
    plan = {
        "status": "ready",
        "warnings": [],
        "summary": {
            "ready": 2,
            "review": 0,
            "blocked": 0,
            "blocking": 0,
            "preview_failed": 0,
            "warnings": 0,
        },
        "preview": {
            "command": ["ffmpeg", "-i", str(reference), str(preview)],
            "output": str(preview),
            "applied": False,
            "output_exists": False,
        },
    }
    monkeypatch.setattr(multicam_sync, "build_multicam_sync_plan", lambda **kwargs: plan)
    monkeypatch.setattr(
        multicam_sync,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 1, "", "preview boom"),
    )

    result = multicam_sync.main([
        "--reference-media",
        str(reference),
        "--angle",
        str(source),
        "--output",
        str(report),
        "--preview-output",
        str(preview),
        "--apply-preview",
        "--strict",
    ])

    assert result == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["summary"]["blocking"] == 1
    assert payload["summary"]["blocked"] == 0
    assert payload["summary"]["preview_failed"] == 1
    assert payload["summary"]["warnings"] == 1
    assert payload["preview"]["applied"] is False
    assert payload["preview"]["output_exists"] is False
    assert payload["preview"]["error"] == "preview boom"
    assert "preview_render_failed" in payload["warnings"]


def test_output_validation_rejects_source_aliases_and_duplicate_outputs(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    source_alias = tmp_path / "source-alias.mp4"
    source_alias.symlink_to(source)
    report = tmp_path / "report.json"

    with pytest.raises(ValueError, match="source file"):
        validate_output_paths({"--output": str(source_alias)}, [str(source)])
    with pytest.raises(ValueError, match="must differ"):
        validate_output_paths(
            {"--output": str(report), "--markdown": str(report)},
            [str(source)],
        )


def _impulse_envelope(length=80, event_at=30):
    values = [0.05] * length
    for index, amp in enumerate([1.0, 0.75, 0.35, 0.15]):
        values[event_at + index] = amp
    values[event_at + 10] = 0.6
    values[event_at + 11] = 0.25
    return values


def test_pairwise_consistency_marks_transitive_offset_mismatch_for_review():
    left_path = "/tmp/cam-b.mp4"
    right_path = "/tmp/cam-c.mp4"
    angles = [
        {
            "id": "reference",
            "media": {"path": "/tmp/cam-a.mp4"},
            "alignment": {"offset_seconds": 0.0},
            "status": "ready",
            "warnings": [],
        },
        {
            "id": "cam-b",
            "media": {"path": left_path},
            "alignment": {"offset_seconds": 0.2},
            "status": "ready",
            "warnings": [],
        },
        {
            "id": "cam-c",
            "media": {"path": right_path},
            "alignment": {"offset_seconds": 0.7},
            "status": "ready",
            "warnings": [],
        },
    ]
    result = evaluate_pairwise_consistency(
        angles,
        {
            left_path: _impulse_envelope(event_at=25),
            right_path: _impulse_envelope(event_at=20),
        },
        frame_seconds=0.04,
        max_offset_seconds=1.0,
        threshold_seconds=0.08,
    )

    assert result["checked"] is True
    assert result["blocking"] == 1
    assert math.isclose(result["pairs"][0]["direct_offset_seconds"], 0.2, abs_tol=0.01)
    assert result["pairs"][0]["divergence_seconds"] == 0.3
    assert angles[1]["status"] == "review"
    assert angles[2]["status"] == "review"


def test_pairwise_search_covers_full_difference_between_opposite_offsets():
    left_path = "/tmp/early.mp4"
    right_path = "/tmp/late.mp4"
    angles = [
        {"id": "reference", "media": {"path": "/tmp/ref.mp4"}, "status": "ready"},
        {
            "id": "early",
            "media": {"path": left_path},
            "alignment": {"offset_seconds": -1.0},
            "status": "ready",
            "warnings": [],
        },
        {
            "id": "late",
            "media": {"path": right_path},
            "alignment": {"offset_seconds": 1.0},
            "status": "ready",
            "warnings": [],
        },
    ]
    result = evaluate_pairwise_consistency(
        angles,
        {
            left_path: _impulse_envelope(length=120, event_at=70),
            right_path: _impulse_envelope(length=120, event_at=20),
        },
        frame_seconds=0.04,
        max_offset_seconds=1.0,
        threshold_seconds=0.08,
    )

    assert result["blocking"] == 0
    assert result["pairs"][0]["direct_offset_seconds"] == 2.0
    assert result["pairs"][0]["divergence_seconds"] == 0.0


def _make_multitrack_media(path):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=12:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:sample_rate=8000:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=900:sample_rate=8000:duration=2",
            "-filter_complex",
            "[1:a]volume=0.01[aquiet];[2:a]volume=0.7[aloud]",
            "-map",
            "0:v",
            "-map",
            "[aquiet]",
            "-map",
            "[aloud]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
def test_selects_loudest_audio_stream_from_multitrack_media(tmp_path):
    media = tmp_path / "multitrack.mp4"
    _make_multitrack_media(media)

    selected = select_audio_stream(str(media), duration=2.0)

    assert selected["method"] == "loudest_mean_volume"
    assert selected["index"] == 1
    assert selected["candidates"][1]["mean_volume_db"] > selected["candidates"][0]["mean_volume_db"]


def _make_sync_media(reference, source, delay=0.4):
    event = (
        "aevalsrc='if(between(t,0.4,0.55),0.8*sin(2*PI*440*t),"
        "if(between(t,1.4,1.65),0.7*sin(2*PI*770*t),0))'"
        ":s=8000:d=3"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=25:d=3",
            "-f",
            "lavfi",
            "-i",
            event,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(reference),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=25:d=3",
            "-f",
            "lavfi",
            "-i",
            event,
            "-filter_complex",
            (
                f"[0:v]tpad=start_duration={delay}:color=black,"
                "trim=duration=3,setpts=PTS-STARTPTS[v];"
                f"[1:a]adelay={int(delay * 1000)}:all=1,atrim=duration=3[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )


def _make_silent_video(path, color):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=12:d=2",
            "-c:v",
            "libx264",
            "-an",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
def test_real_cli_aligns_two_angles_and_renders_preview(tmp_path):
    reference = tmp_path / "reference.mp4"
    source = tmp_path / "source.mp4"
    report = tmp_path / "multicam_sync.json"
    markdown = tmp_path / "multicam_sync.md"
    preview = tmp_path / "multicam_preview.mp4"
    _make_sync_media(reference, source, delay=0.4)

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "multicam_sync.py"),
            "--reference-media",
            str(reference),
            "--angle",
            str(source),
            "--output",
            str(report),
            "--markdown",
            str(markdown),
            "--preview-output",
            str(preview),
            "--preview-duration",
            "1",
            "--apply-preview",
            "--max-offset",
            "1",
            "--min-confidence",
            "0.3",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    alignment = payload["angles"][1]["alignment"]
    assert math.isclose(alignment["offset_seconds"], -0.4, abs_tol=0.08)
    assert payload["summary"]["blocking"] == 0
    assert payload["preview"]["applied"] is True
    assert preview.is_file() and preview.stat().st_size > 0
    assert "Multicam Sync Plan" in markdown.read_text(encoding="utf-8")
    ssim = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-ss",
            "0.5",
            "-i",
            str(preview),
            "-frames:v",
            "1",
            "-filter_complex",
            "[0:v]split=2[a][b];[a]crop=480:270:0:0[left];"
            "[b]crop=480:270:480:0[right];[left][right]ssim",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    match = re.search(r"All:([0-9.]+)", ssim.stderr)
    assert match and float(match.group(1)) > 0.90, ssim.stderr


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
def test_manual_offsets_allow_audio_less_angles(tmp_path):
    reference = tmp_path / "silent-reference.mp4"
    source = tmp_path / "silent-angle.mp4"
    _make_silent_video(reference, "blue")
    _make_silent_video(source, "red")

    plan = build_multicam_sync_plan(
        reference_media=str(reference),
        angle_media=[str(source)],
        manual_offsets={str(source): 0.1},
    )

    assert plan["status"] == "ready"
    assert plan["summary"]["blocking"] == 0
    assert "manual_only_reference_without_audio" in plan["angles"][0]["warnings"]
    assert "manual_offset_without_audio" in plan["angles"][1]["warnings"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
def test_manual_offsets_warn_but_can_pass_strict(tmp_path):
    reference = tmp_path / "reference.mp4"
    source = tmp_path / "source.mp4"
    _make_sync_media(reference, source, delay=0.2)

    plan = build_multicam_sync_plan(
        reference_media=str(reference),
        angle_media=[str(source)],
        manual_offsets={str(source): -0.2},
    )

    assert plan["status"] == "ready"
    assert plan["summary"]["blocking"] == 0
    assert plan["summary"]["manual_offsets"] == 1
    assert "manual_offset_not_independently_verified" in plan["angles"][1]["warnings"]
    assert "V1 does not measure clock drift" in emit_markdown(plan)
