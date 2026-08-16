import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import reference_edit_rhythm as rhythm  # noqa: E402


def _params(*, require_match=False):
    return {
        "scene_threshold": 0.3,
        "min_scene_gap": 0.2,
        "sample_fps": 1.0,
        "max_frames": 24,
        "thumb_width": 320,
        "require_match": require_match,
        "max_cut_density_delta": 0.4,
        "max_median_shot_delta": 0.5,
        "max_final_hold_delta": 0.15,
        "max_boundary_distance": 0.12,
        "max_phase_share_delta": 0.3,
    }


def _media(duration=10.0):
    return {
        "duration": duration,
        "fps": 24.0,
        "width": 160,
        "height": 90,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "has_audio": False,
        "audio_codec": "",
        "sample_rate": 0,
        "channels": 0,
    }


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(path, root, *, duration=10.0):
    path = Path(path)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
        "media": _media(duration),
    }


def _evidence(path, root):
    path = Path(path)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
        "sampling": {"estimated_frames": 4},
    }


def _report_fixture(tmp_path, monkeypatch, *, require_match=False):
    reference = tmp_path / "origin" / "reference.mp4"
    candidate = tmp_path / "output" / "candidate.mp4"
    reference_sheet = tmp_path / "verify" / "reference_contact_sheet.jpg"
    candidate_sheet = tmp_path / "verify" / "candidate_contact_sheet.jpg"
    for path, payload in (
        (reference, b"reference-video"),
        (candidate, b"candidate-video"),
        (reference_sheet, b"reference-sheet"),
        (candidate_sheet, b"candidate-sheet"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    monkeypatch.setattr(rhythm, "probe_media", lambda _path: _media())
    sources = {
        "reference": _record(reference, tmp_path),
        "candidate": _record(candidate, tmp_path),
    }
    evidence = {
        "reference_contact_sheet": _evidence(reference_sheet, tmp_path),
        "candidate_contact_sheet": _evidence(candidate_sheet, tmp_path),
    }
    report = rhythm.build_report(
        project_dir=str(tmp_path),
        sources=sources,
        evidence=evidence,
        reference_boundaries=[2, 5, 8],
        candidate_boundaries=[2, 5, 8],
        params=_params(require_match=require_match),
    )
    return report, reference, candidate, reference_sheet, candidate_sheet


def test_matching_timelines_keep_structural_comparison_ready():
    reference = rhythm.analyze_timeline(10, [2, 5, 8])
    candidate = rhythm.analyze_timeline(10, [2, 5, 8])

    comparison = rhythm.compare_timelines(
        reference,
        candidate,
        require_match=True,
        max_cut_density_delta=0.4,
        max_median_shot_delta=0.5,
        max_final_hold_delta=0.15,
        max_boundary_distance=0.12,
        max_phase_share_delta=0.3,
    )

    assert comparison["summary"] == {"status": "ready", "blocking": 0, "warnings": 0}
    assert comparison["measurements"]["normalized_boundary_distance"] == 0


def test_divergent_timeline_warns_by_default_and_blocks_when_required():
    reference = rhythm.analyze_timeline(10, [1, 3, 5, 7, 9])
    candidate = rhythm.analyze_timeline(10, [5])

    advisory = rhythm.compare_timelines(
        reference,
        candidate,
        require_match=False,
        max_cut_density_delta=0.4,
        max_median_shot_delta=0.5,
        max_final_hold_delta=0.15,
        max_boundary_distance=0.12,
        max_phase_share_delta=0.3,
    )
    required = rhythm.compare_timelines(
        reference,
        candidate,
        require_match=True,
        max_cut_density_delta=0.4,
        max_median_shot_delta=0.5,
        max_final_hold_delta=0.15,
        max_boundary_distance=0.12,
        max_phase_share_delta=0.3,
    )

    assert advisory["summary"]["blocking"] == 0
    assert advisory["summary"]["warnings"] >= 3
    assert required["summary"]["blocking"] == advisory["summary"]["warnings"]
    assert {item["severity"] for item in required["findings"]} == {"block"}


def test_verify_report_detects_source_and_evidence_drift(tmp_path, monkeypatch):
    report, reference, _, _, candidate_sheet = _report_fixture(tmp_path, monkeypatch)

    assert rhythm.verify_report(report)["summary"]["blocking"] == 0

    reference.write_bytes(b"changed-reference-video")
    candidate_sheet.write_bytes(b"changed-candidate-sheet")
    verification = rhythm.verify_report(report)

    assert verification["summary"]["blocking"] >= 2
    assert any("reference video bytes changed" in item for item in verification["blockers"])
    assert any("candidate_contact_sheet bytes changed" in item for item in verification["blockers"])


def test_verify_recomputes_derived_state_even_with_rewritten_report_id(tmp_path, monkeypatch):
    report, *_ = _report_fixture(tmp_path, monkeypatch)
    report["candidate"]["metrics"]["cuts"] = 999
    report["report_id"] = rhythm.canonical_report_id(report)

    verification = rhythm.verify_report(report)

    assert verification["summary"]["blocking"] >= 1
    assert "stored candidate rhythm state is not canonical" in verification["blockers"]


def test_verify_rejects_missing_absolute_project_dir(tmp_path, monkeypatch):
    report, *_ = _report_fixture(tmp_path, monkeypatch)
    report.pop("project_dir")
    report["report_id"] = rhythm.canonical_report_id(report)

    verification = rhythm.verify_report(report)

    assert "project_dir must be a non-empty absolute path" in verification["blockers"]


def test_analyze_preflights_all_contact_sheet_overwrites(tmp_path):
    reference = tmp_path / "origin" / "reference.mp4"
    candidate = tmp_path / "output" / "candidate.mp4"
    reference.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    reference.write_bytes(b"reference")
    candidate.write_bytes(b"candidate")
    evidence_dir = tmp_path / "verify" / "reference_edit_rhythm"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "candidate_contact_sheet.jpg").write_bytes(b"existing")

    with pytest.raises(ValueError, match="without --force"):
        rhythm.analyze_project(
            project_dir=str(tmp_path),
            reference_path=str(reference),
            candidate_path=str(candidate),
            evidence_dir=str(evidence_dir),
            scene_threshold=0.3,
            min_scene_gap=0.2,
            sample_fps=1.0,
            max_frames=24,
            thumb_width=320,
            require_match=False,
            max_cut_density_delta=0.4,
            max_median_shot_delta=0.5,
            max_final_hold_delta=0.15,
            max_boundary_distance=0.12,
            max_phase_share_delta=0.3,
            force=False,
        )

    assert not (evidence_dir / "reference_contact_sheet.jpg").exists()


def _make_three_shot_video(path, colors):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for color in colors:
        command.extend(["-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d=0.6"])
    command.extend(
        [
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    subprocess.run(command, check=True, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_cli_analyze_and_verify_real_videos(tmp_path):
    reference = tmp_path / "origin" / "reference.mp4"
    candidate = tmp_path / "output" / "candidate.mp4"
    reference.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    _make_three_shot_video(reference, ["red", "green", "blue"])
    _make_three_shot_video(candidate, ["white", "yellow", "black"])
    report_path = tmp_path / "work" / "reference_edit_rhythm.json"
    markdown = tmp_path / "work" / "reference_edit_rhythm.md"
    script = os.path.join(REPO, "scripts", "reference_edit_rhythm.py")

    analyze = subprocess.run(
        [
            sys.executable,
            script,
            "analyze",
            "--project-dir",
            str(tmp_path),
            "--reference",
            str(reference),
            "--candidate",
            str(candidate),
            "--output",
            str(report_path),
            "--markdown",
            str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert analyze.returncode == 0, analyze.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["version"] == "reference_edit_rhythm.v1"
    assert report["sources"]["reference"]["sha256"] != report["sources"]["candidate"]["sha256"]
    assert (tmp_path / report["evidence"]["reference_contact_sheet"]["path"]).exists()
    assert markdown.exists()

    verify = subprocess.run(
        [sys.executable, script, "verify", "--report", str(report_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert "blocking=0" in verify.stderr
