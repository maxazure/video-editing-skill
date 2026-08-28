import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import flash_safety_qa as flash  # noqa: E402


MEDIA = {
    "duration": 2.0,
    "fps": 30.0,
    "width": 1920,
    "height": 1080,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}


def _settings(**overrides):
    values = {
        "analysis_fps": 30.0,
        "analysis_width": 64,
        "luma_change": 0.10,
        "red_change": 0.10,
        "area_fraction": 0.25,
        "pair_gap_seconds": 0.50,
    }
    values.update(overrides)
    return values


def _solid(rgb, *, width=4, height=2):
    return bytes(rgb) * width * height


def _alternating_frames(first, second, *, groups=12, frames_per_group=3):
    frames = []
    for group in range(groups + 1):
        frame = _solid(first if group % 2 == 0 else second)
        frames.extend([frame] * frames_per_group)
    return frames


def _safe_analysis():
    frames = [_solid((24, 24, 24)) for _ in range(31)]
    return flash.analyze_frame_sequence(
        frames,
        width=4,
        height=2,
        fps=30.0,
        settings=_settings(),
    )


def test_high_frequency_luminance_flashes_create_blocking_window():
    analysis = flash.analyze_frame_sequence(
        _alternating_frames((0, 0, 0), (255, 255, 255)),
        width=4,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert len(analysis["flashes"]["luminance"]) >= 5
    assert analysis["peaks"]["luminance_1s"] > 3
    assert any(
        item["signal"] == "luminance"
        and item["rule"] == "more_than_3_flashes_in_1_second"
        for item in analysis["risk_windows"]
    )


def test_saturated_red_flashes_are_tracked_separately():
    analysis = flash.analyze_frame_sequence(
        _alternating_frames((0, 0, 0), (255, 0, 0)),
        width=4,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert len(analysis["flashes"]["saturated_red"]) >= 5
    assert any(item["signal"] == "saturated_red" for item in analysis["risk_windows"])


def test_static_sequence_is_ready_and_pairing_is_conservative():
    safe = _safe_analysis()
    report = {"analysis": safe}
    snapshot = flash._derived_snapshot(report)

    assert safe["flashes"]["luminance"] == []
    assert snapshot["status"] == "ready"
    assert snapshot["summary"]["blocking"] == 0

    same_direction = [
        {"time": 0.1, "direction": "rise", "affected_fraction": 1.0},
        {"time": 0.2, "direction": "rise", "affected_fraction": 1.0},
    ]
    assert flash.pair_transitions(same_direction, max_gap=0.5) == []


def test_two_flashes_below_blocking_threshold_are_warning_only():
    frames = _alternating_frames((0, 0, 0), (255, 255, 255), groups=4)
    analysis = flash.analyze_frame_sequence(
        frames,
        width=4,
        height=2,
        fps=30.0,
        settings=_settings(),
    )
    snapshot = flash._derived_snapshot({"analysis": analysis})

    assert len(analysis["flashes"]["luminance"]) == 2
    assert snapshot["status"] == "warn"
    assert snapshot["summary"]["blocking"] == 0
    assert snapshot["summary"]["warnings"] == 1


def test_extended_window_blocks_ten_flashes_without_one_second_burst():
    flashes = [
        {"start": index * 0.45, "end": index * 0.45 + 0.1}
        for index in range(10)
    ]

    assert flash._rolling_peak(flashes, 1.0) <= 3
    windows = flash._risk_windows(
        flashes,
        signal="luminance",
        window_seconds=5.0,
        minimum_count=10,
        rule="at_least_10_flashes_in_5_seconds",
    )

    assert windows == [{
        "signal": "luminance",
        "rule": "at_least_10_flashes_in_5_seconds",
        "start": 0.0,
        "end": 4.15,
        "flashes": 10,
    }]


def test_build_and_live_verify_bind_source_analysis_and_derived_state(tmp_path):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir()
    source.write_bytes(b"final master")
    analysis = _safe_analysis()

    report = flash.build_report(
        source,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: dict(analysis),
    )
    verification = flash.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: dict(analysis),
    )

    assert report["status"] == "ready"
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0

    source.write_bytes(b"changed master")
    stale = flash.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: dict(analysis),
    )
    assert stale["status"] == "blocked"
    assert any("source video bytes changed" in item for item in stale["blockers"])


def test_report_tampering_and_live_evidence_drift_fail_closed(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _safe_analysis()
    report = flash.build_report(
        source,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: dict(analysis),
    )

    report["status"] = "blocked"
    tampered = flash.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: dict(analysis),
    )
    assert any("stored status" in item for item in tampered["blockers"])

    report["status"] = "ready"
    drifted_analysis = dict(analysis)
    drifted_analysis["sample"] = {**analysis["sample"], "frames": analysis["sample"]["frames"] + 1}
    drifted = flash.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: drifted_analysis,
    )
    assert any("live flash-safety evidence differs" in item for item in drifted["blockers"])


def test_markdown_states_non_certification_and_remediation(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    report = flash.build_report(
        source,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda _path, **_kwargs: _safe_analysis(),
    )

    markdown = flash.emit_markdown(report)
    assert "not medical advice" in markdown
    assert "accredited photosensitivity analyzer" in markdown
    assert "lowering contrast/red saturation" in markdown


def test_cli_refuses_report_hardlink_to_source(tmp_path, monkeypatch):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    report_path = tmp_path / "flash_safety_qa.json"
    os.link(source, report_path)
    monkeypatch.setattr(flash, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(flash, "analyze_video", lambda _path, **_kwargs: _safe_analysis())

    result = flash.main([
        "analyze",
        str(source),
        "--project-dir",
        str(tmp_path),
        "--output",
        str(report_path),
        "--markdown",
        str(tmp_path / "flash_safety_qa.md"),
        "--force",
    ])

    assert result == 1
    assert source.read_bytes() == b"final master"


def test_cli_help_lists_analyze_and_verify():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/flash_safety_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "analyze" in result.stdout
    assert "verify" in result.stdout
