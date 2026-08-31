import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import encode_quality_qa as quality  # noqa: E402


MEDIA = {
    "duration": 2.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}


def _analysis(*, mean_ssim=0.99, p05_ssim=0.98, mean_psnr=42.0):
    return {
        "normalization": {
            "width": 640,
            "height": 360,
            "fps": 30.0,
            "candidate_scaled": False,
            "pixel_format": "yuv420p",
            "scale_flags": "lanczos",
        },
        "frames_compared": 60,
        "seconds_compared": 2.0,
        "ssim": {
            "mean": mean_ssim,
            "p05": p05_ssim,
            "minimum": min(p05_ssim, mean_ssim),
        },
        "psnr": {
            "mean_finite_db": mean_psnr,
            "p05_finite_db": mean_psnr - 1,
            "minimum_finite_db": mean_psnr - 2,
            "finite_frames": 60,
            "infinite_frames": 0,
            "all_infinite": False,
        },
        "worst_frames": [
            {
                "frame": 31,
                "time": 1.0,
                "ssim": p05_ssim,
                "psnr_db": mean_psnr - 2,
                "psnr_infinite": False,
            }
        ],
    }


def _files(tmp_path: Path):
    reference = tmp_path / "output" / "master.mp4"
    candidate = tmp_path / "output" / "delivery.mp4"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference bytes")
    candidate.write_bytes(b"candidate bytes")
    return reference, candidate


def test_parse_stats_and_summarize_worst_frames(tmp_path):
    ssim_path = tmp_path / "ssim.log"
    psnr_path = tmp_path / "psnr.log"
    ssim_path.write_text(
        "n:1 Y:0.990000 U:1.000000 V:1.000000 All:0.991000 (20.0)\n"
        "n:2 Y:0.900000 U:0.950000 V:0.950000 All:0.910000 (10.0)\n",
        encoding="utf-8",
    )
    psnr_path.write_text(
        "n:1 mse_avg:1.00 psnr_avg:48.000000\n"
        "n:2 mse_avg:4.00 psnr_avg:36.000000\n",
        encoding="utf-8",
    )

    result = quality.summarize_metrics(
        quality._parse_stats(ssim_path, "ssim"),
        quality._parse_stats(psnr_path, "psnr"),
        fps=30.0,
        width=640,
        height=360,
        candidate_scaled=False,
        worst_frames=1,
    )

    assert result["frames_compared"] == 2
    assert result["ssim"]["mean"] == pytest.approx(0.9505)
    assert result["psnr"]["mean_finite_db"] == pytest.approx(42.0)
    assert result["worst_frames"] == [{
        "frame": 2,
        "time": 0.033333,
        "ssim": 0.91,
        "psnr_db": 36.0,
        "psnr_infinite": False,
    }]


def test_psnr_infinity_is_json_safe_and_passes_threshold():
    result = quality.summarize_metrics(
        [{"frame": 1, "value": 1.0, "infinite": False}],
        [{"frame": 1, "value": None, "infinite": True}],
        fps=30,
        width=640,
        height=360,
        candidate_scaled=False,
        worst_frames=1,
    )

    assert result["psnr"]["all_infinite"] is True
    assert result["psnr"]["mean_finite_db"] is None
    assert result["worst_frames"][0]["psnr_infinite"] is True


def test_quality_thresholds_block_average_and_low_tail_regressions():
    report = {
        "analysis": _analysis(mean_ssim=0.94, p05_ssim=0.88, mean_psnr=33.0),
        "settings": {
            "min_mean_ssim": 0.95,
            "min_p05_ssim": 0.90,
            "min_mean_psnr_db": 35.0,
        },
        "reference": {"width": 640, "height": 360},
        "candidate": {"width": 640, "height": 360},
    }

    state = quality._derived_snapshot(report)

    assert state["status"] == "blocked"
    assert state["summary"]["blocking"] == 3
    assert any("P05 SSIM" in item for item in state["blockers"])


def test_build_and_live_verify_bind_both_videos_and_metrics(tmp_path):
    reference, candidate = _files(tmp_path)
    analysis = _analysis()
    probe = lambda _path: dict(MEDIA)
    measure = lambda *_args, **_kwargs: dict(analysis)

    report = quality.build_report(
        reference,
        candidate,
        project_dir=tmp_path,
        probe_fn=probe,
        measure_fn=measure,
    )
    verification = quality.verify_report(
        report,
        tmp_path,
        probe_fn=probe,
        measure_fn=measure,
    )

    assert report["status"] == "ready"
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0

    candidate.write_bytes(b"changed candidate")
    stale = quality.verify_report(
        report,
        tmp_path,
        probe_fn=probe,
        measure_fn=measure,
    )
    assert stale["status"] == "blocked"
    assert any("candidate video bytes changed" in item for item in stale["blockers"])


def test_report_tampering_and_live_metric_drift_fail_closed(tmp_path):
    reference, candidate = _files(tmp_path)
    probe = lambda _path: dict(MEDIA)
    report = quality.build_report(
        reference,
        candidate,
        project_dir=tmp_path,
        probe_fn=probe,
        measure_fn=lambda *_args, **_kwargs: _analysis(),
    )

    report["status"] = "blocked"
    tampered = quality.verify_report(
        report,
        tmp_path,
        probe_fn=probe,
        measure_fn=lambda *_args, **_kwargs: _analysis(),
    )
    assert any("stored status" in item for item in tampered["blockers"])

    report["status"] = "ready"
    drifted = quality.verify_report(
        report,
        tmp_path,
        probe_fn=probe,
        measure_fn=lambda *_args, **_kwargs: _analysis(mean_ssim=0.98),
    )
    assert any("live encode-quality metrics differ" in item for item in drifted["blockers"])


def test_pair_validation_rejects_timeline_aspect_and_identity_mismatch(tmp_path):
    reference, candidate = _files(tmp_path)
    settings = {
        "min_mean_ssim": 0.95,
        "min_p05_ssim": 0.90,
        "min_mean_psnr_db": 35.0,
        "duration_tolerance_frames": 1.0,
        "fps_tolerance": 0.01,
        "worst_frames": 12,
    }
    with pytest.raises(ValueError, match="distinct files"):
        quality.validate_pair(
            reference,
            reference,
            reference_media=MEDIA,
            candidate_media=MEDIA,
            settings=settings,
        )
    with pytest.raises(ValueError, match="durations differ"):
        quality.validate_pair(
            reference,
            candidate,
            reference_media=MEDIA,
            candidate_media={**MEDIA, "duration": 2.2},
            settings=settings,
        )
    with pytest.raises(ValueError, match="aspect ratios differ"):
        quality.validate_pair(
            reference,
            candidate,
            reference_media=MEDIA,
            candidate_media={**MEDIA, "width": 360, "height": 640},
            settings=settings,
        )


def test_markdown_scopes_metric_and_human_review_boundaries(tmp_path):
    reference, candidate = _files(tmp_path)
    report = quality.build_report(
        reference,
        candidate,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=lambda *_args, **_kwargs: _analysis(),
    )

    markdown = quality.emit_markdown(report)

    assert "Worst frames" in markdown
    assert "same-timeline" in markdown
    assert "does not claim a VMAF score" in markdown
    assert "Audio quality is not measured" in markdown


def test_cli_refuses_report_hardlink_to_reference(tmp_path, monkeypatch):
    reference, candidate = _files(tmp_path)
    report_path = tmp_path / "encode_quality_qa.json"
    os.link(reference, report_path)
    monkeypatch.setattr(quality, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(quality, "measure_quality", lambda *_args, **_kwargs: _analysis())

    result = quality.main([
        "analyze",
        str(reference),
        str(candidate),
        "--project-dir",
        str(tmp_path),
        "--output",
        str(report_path),
        "--force",
    ])

    assert result == 1
    assert reference.read_bytes() == b"reference bytes"


def test_cli_help_lists_analyze_and_verify():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/encode_quality_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "analyze" in result.stdout
    assert "verify" in result.stdout
