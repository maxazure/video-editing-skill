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
    fit_clock_drift,
    measure_clock_drift,
    select_audio_stream,
    validate_output_paths,
)


def test_clock_drift_fit_reports_signed_affine_mapping_and_factors():
    probes = [
        {
            "reference_time_seconds": time,
            "offset_seconds": 0.1 - 0.0002 * time,
            "confidence": 0.95,
        }
        for time in (0.0, 100.0, 200.0, 300.0)
    ]
    probes.append({
        "reference_time_seconds": 150.0,
        "offset_seconds": 4.0,
        "confidence": 0.95,
    })

    fit = fit_clock_drift(
        probes,
        min_confidence=0.45,
        drift_threshold_ms=50.0,
        residual_threshold_seconds=0.02,
    )

    assert fit["trusted"] is True
    assert fit["accepted_probe_count"] == 5
    assert fit["fit_inlier_count"] == 4
    assert fit["offset_slope_ppm"] == -200.0
    assert fit["accumulated_drift_ms"] == -60.0
    assert fit["requires_correction"] is True
    assert fit["source_zero_on_reference_seconds"] == pytest.approx(0.09998, abs=1e-4)
    correction = fit["advisory_correction"]
    assert correction["selected_audio_atempo_factor"] == pytest.approx(1.0002)
    assert correction["advisory_video_setpts_multiplier"] == pytest.approx(0.9998)
    assert correction["applied"] is False


def test_clock_drift_fit_rejects_when_residuals_have_no_consensus():
    fit = fit_clock_drift(
        [
            {"reference_time_seconds": 0.0, "offset_seconds": 0.0, "confidence": 0.9},
            {"reference_time_seconds": 100.0, "offset_seconds": 0.07, "confidence": 0.9},
            {"reference_time_seconds": 200.0, "offset_seconds": -0.11, "confidence": 0.9},
            {"reference_time_seconds": 300.0, "offset_seconds": 0.23, "confidence": 0.9},
            {"reference_time_seconds": 400.0, "offset_seconds": -0.29, "confidence": 0.9},
        ],
        min_confidence=0.45,
        drift_threshold_ms=80.0,
        residual_threshold_seconds=0.005,
    )

    assert fit["trusted"] is False
    assert "too_few_consensus_inliers" in fit["reasons"]
    assert fit["advisory_correction"] is None


def test_clock_drift_fit_does_not_trust_only_three_points():
    fit = fit_clock_drift(
        [
            {
                "reference_time_seconds": time,
                "offset_seconds": 0.1 + 0.0003 * time,
                "confidence": 0.95,
            }
            for time in (0.0, 100.0, 200.0)
        ],
        min_confidence=0.45,
        drift_threshold_ms=50.0,
        residual_threshold_seconds=0.02,
    )

    assert fit["trusted"] is False
    assert "too_few_confident_probes" in fit["reasons"]


def test_clock_drift_measurement_uses_spaced_bounded_windows(monkeypatch):
    decode_calls = []
    local_offsets = iter([-0.02, -0.01, 0.0, 0.01, 0.02])

    def fake_decode(path, **kwargs):
        decode_calls.append((path, kwargs))
        return [0.0, 1.0, 0.0]

    def fake_estimate(*args, **kwargs):
        return {
            "offset_seconds": next(local_offsets),
            "confidence": 0.9,
            "score": 0.9,
            "score_margin": 0.2,
        }

    monkeypatch.setattr(multicam_sync, "decode_audio_envelope", fake_decode)
    monkeypatch.setattr(multicam_sync, "estimate_offset", fake_estimate)

    drift = measure_clock_drift(
        reference_path="reference.mp4",
        source_path="source.mp4",
        reference_stream_index=0,
        source_stream_index=1,
        reference_duration=120.0,
        source_duration=120.0,
        base_offset_seconds=0.3,
        sample_rate=8000,
        frame_ms=40.0,
        probe_count=5,
        probe_seconds=10.0,
        search_seconds=1.0,
        min_confidence=0.45,
        drift_threshold_ms=10.0,
    )

    assert drift["status"] == "correction_required"
    assert drift["fit"]["accepted_probe_count"] == 5
    assert drift["fit"]["fit_inlier_count"] == 5
    assert drift["fit"]["offset_slope_ppm"] > 0
    assert len(decode_calls) == 10
    starts = [call[1]["start_seconds"] for call in decode_calls]
    assert min(starts) >= 0.0
    assert max(starts) + 12.0 <= 120.0
    assert all(call[1]["max_duration"] == 12.0 for call in decode_calls)


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


def _make_drift_audio(reference, source, tempo=0.996):
    signal = (
        "aevalsrc='(0.45+0.2*sin(2*PI*0.37*t)+0.12*sin(2*PI*0.113*t))*"
        "sin(2*PI*(330*t+18*sin(0.19*t)))':s=8000:d=60"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", signal,
            "-c:a", "pcm_s16le", str(reference),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(reference),
            "-af", f"atempo={tempo}", "-c:a", "pcm_s16le", str(source),
        ],
        check=True,
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
@pytest.mark.parametrize(("tempo", "expected_ppm"), [(0.996, -4016.0), (1.004, 3984.0)])
def test_real_cli_measures_clock_drift_and_strict_blocks_unapplied_correction(
    tmp_path,
    tempo,
    expected_ppm,
):
    reference = tmp_path / "reference.wav"
    source = tmp_path / "slow-source.wav"
    report = tmp_path / "multicam_sync.json"
    markdown = tmp_path / "multicam_sync.md"
    _make_drift_audio(reference, source, tempo=tempo)

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "multicam_sync.py"),
            "--reference-media", str(reference),
            "--angle", str(source),
            "--output", str(report),
            "--markdown", str(markdown),
            "--measure-clock-drift",
            "--drift-probes", "7",
            "--drift-probe-seconds", "6",
            "--drift-search-seconds", "1",
            "--drift-threshold-ms", "80",
            "--frame-ms", "20",
            "--max-offset", "1",
            "--max-probe-seconds", "10",
            "--min-confidence", "0.2",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    drift = payload["angles"][1]["clock_drift"]
    assert payload["status"] == "review"
    assert payload["settings"]["clock_drift_measured"] is True
    assert payload["summary"]["clock_drift_review"] == 1
    assert drift["status"] == "correction_required"
    assert drift["fit"]["trusted"] is True
    assert drift["fit"]["offset_slope_ppm"] == pytest.approx(expected_ppm, abs=1200.0)
    assert abs(drift["fit"]["accumulated_drift_ms"]) > 150.0
    atempo_factor = drift["fit"]["advisory_correction"]["selected_audio_atempo_factor"]
    assert (atempo_factor > 1.0) is (expected_ppm < 0)
    assert drift["fit"]["advisory_correction"]["applied"] is False
    assert "Clock Drift" in markdown.read_text(encoding="utf-8")


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
