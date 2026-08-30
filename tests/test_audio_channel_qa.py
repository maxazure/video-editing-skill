import json
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import audio_channel_qa  # noqa: E402
from audio_channel_qa import (  # noqa: E402
    analyze_frames,
    build_report,
    emit_markdown,
    evaluate_analysis,
    normalize_settings,
    parse_analysis_log,
    verify_report,
)


STEREO_MEDIA = {
    "duration": 1.0,
    "format_name": "wav",
    "audio_stream_index": 0,
    "audio_codec": "pcm_s16le",
    "sample_rate": 48000,
    "channels": 2,
    "channel_layout": "stereo",
}


def _frames(*, phase=1.0, left_start=0.0, right_start=0.0, left_db=-20.0, right_db=-20.0):
    rows = []
    for index in range(20):
        time = index * 0.05
        left_active = time + 1e-9 >= left_start
        right_active = time + 1e-9 >= right_start
        rows.append(
            {
                "index": index,
                "time": round(time, 6),
                "phase_correlation": phase,
                "left_rms_dbfs": left_db if left_active else None,
                "right_rms_dbfs": right_db if right_active else None,
                "left_peak_dbfs": left_db + 3 if left_active else None,
                "right_peak_dbfs": right_db + 3 if right_active else None,
            }
        )
    return rows


def _probe(_path):
    return dict(STEREO_MEDIA)


def _measure(_path, *, settings):
    assert settings["window_ms"] == 50
    return _frames()


def test_parse_analysis_log_extracts_phase_and_channel_levels():
    log = """
[Parsed_ametadata_2] frame:0    pts:0       pts_time:0
[Parsed_ametadata_2] lavfi.aphasemeter.phase=-0.250000
[Parsed_ametadata_2] lavfi.astats.1.Peak_level=-10.000000
[Parsed_ametadata_2] lavfi.astats.1.RMS_level=-20.000000
[Parsed_ametadata_2] lavfi.astats.2.Peak_level=-11.000000
[Parsed_ametadata_2] lavfi.astats.2.RMS_level=-21.000000
"""

    assert parse_analysis_log(log) == [
        {
            "index": 0,
            "time": 0.0,
            "phase_correlation": -0.25,
            "left_peak_dbfs": -10.0,
            "left_rms_dbfs": -20.0,
            "right_peak_dbfs": -11.0,
            "right_rms_dbfs": -21.0,
        }
    ]


def test_clean_in_phase_stereo_is_ready():
    settings = normalize_settings()
    analysis = analyze_frames(_frames(), duration=1.0, settings=settings)
    checks = evaluate_analysis(analysis, channels=2, settings=settings)

    assert analysis["onset_skew_ms"] == 0.0
    assert analysis["balance_db"] == 0.0
    assert analysis["phase_correlation"] == 1.0
    assert analysis["mono_fold_down_loss_db"] == 0.0
    assert all(item["status"] == "pass" for item in checks)


def test_channel_only_opening_blocks_onset_alignment():
    settings = normalize_settings()
    analysis = analyze_frames(
        _frames(right_start=0.30),
        duration=1.0,
        settings=settings,
    )
    checks = evaluate_analysis(analysis, channels=2, settings=settings)
    by_name = {item["name"]: item for item in checks}

    assert analysis["onset_skew_ms"] == 300.0
    assert by_name["onset_alignment"]["status"] == "block"


def test_energy_imbalance_and_missing_channel_block():
    settings = normalize_settings()
    imbalanced = analyze_frames(
        _frames(left_db=-20.0, right_db=-32.0),
        duration=1.0,
        settings=settings,
    )
    checks = {item["name"]: item for item in evaluate_analysis(imbalanced, channels=2, settings=settings)}
    assert imbalanced["balance_db"] == pytest.approx(12.0)
    assert checks["left_right_balance"]["status"] == "block"

    missing = analyze_frames(
        _frames(right_db=-80.0),
        duration=1.0,
        settings=settings,
    )
    checks = {item["name"]: item for item in evaluate_analysis(missing, channels=2, settings=settings)}
    assert checks["channel_activity"]["status"] == "block"


def test_antiphase_stereo_blocks_phase_and_mono_fold_down():
    settings = normalize_settings()
    analysis = analyze_frames(_frames(phase=-1.0), duration=1.0, settings=settings)
    checks = {item["name"]: item for item in evaluate_analysis(analysis, channels=2, settings=settings)}

    assert analysis["phase_correlation"] == -1.0
    assert analysis["negative_phase_seconds"] == 1.0
    assert analysis["mono_fold_down_loss_db"] == 120.0
    assert checks["phase_correlation"]["status"] == "block"
    assert checks["negative_phase_windows"]["status"] == "block"
    assert checks["mono_fold_down"]["status"] == "block"


def test_mono_passes_and_multichannel_fails_closed(tmp_path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")

    mono = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=lambda _path: {**STEREO_MEDIA, "channels": 1, "channel_layout": "mono"},
        measure_fn=lambda *_args, **_kwargs: pytest.fail("mono should not run stereo measurement"),
    )
    assert mono["summary"]["status"] == "ready"
    assert mono["analysis"]["mode"] == "mono"

    surround = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=lambda _path: {**STEREO_MEDIA, "channels": 6, "channel_layout": "5.1"},
        measure_fn=lambda *_args, **_kwargs: pytest.fail("surround should fail before stereo measurement"),
    )
    assert surround["summary"]["status"] == "blocked"
    assert surround["analysis"]["mode"] == "unsupported_multichannel"


def test_build_and_live_verify_detect_source_and_derived_drift(tmp_path):
    source = tmp_path / "master.wav"
    source.write_bytes(b"stereo bytes")
    report = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )

    current = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert current["summary"]["blocking"] == 0

    tampered = json.loads(json.dumps(report))
    tampered["analysis"]["balance_db"] = 4.0
    tampered["report_id"] = audio_channel_qa._canonical_sha256(
        audio_channel_qa._report_snapshot(tampered)
    )
    stale = verify_report(
        tampered,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert stale["summary"]["blocking"] > 0
    assert "live channel measurements drifted" in stale["blockers"]

    source.write_bytes(b"changed stereo bytes")
    source_drift = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert "source bytes or media contract drifted" in source_drift["blockers"]


def test_live_verify_preserves_current_audio_risk_status(tmp_path):
    source = tmp_path / "master.wav"
    source.write_bytes(b"anti-phase stereo bytes")

    def anti_measure(_path, *, settings):
        return _frames(phase=-1.0)

    report = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=anti_measure,
    )
    verification = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=anti_measure,
    )

    assert report["summary"]["status"] == "blocked"
    assert verification["summary"]["blocking"] >= 3
    assert any("mono fold-down" in item for item in verification["blockers"])


def test_settings_and_project_path_validation(tmp_path):
    with pytest.raises(ValueError, match="unknown settings"):
        normalize_settings({"mystery": 1})
    with pytest.raises(ValueError, match="balance thresholds"):
        normalize_settings({"warn_balance_db": 8, "max_balance_db": 6})

    outside = tmp_path.parent / "outside-audio.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="inside the project"):
        build_report(outside, project_dir=tmp_path, probe_fn=_probe, measure_fn=_measure)
    outside.unlink()


def test_symlink_source_and_hardlink_output_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "master.wav"
    source.write_bytes(b"stereo bytes")
    link = tmp_path / "source-link.wav"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        build_report(link, project_dir=tmp_path, probe_fn=_probe, measure_fn=_measure)

    output = tmp_path / "report.json"
    os.link(source, output)
    monkeypatch.setattr(audio_channel_qa, "probe_audio_media", _probe)
    monkeypatch.setattr(audio_channel_qa, "measure_audio_channels", _measure)
    result = audio_channel_qa.main(
        [
            "analyze",
            str(source),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(output),
            "--force",
        ]
    )
    assert result == 1
    assert source.read_bytes() == b"stereo bytes"


def test_markdown_and_cli_help_explain_review_boundary(tmp_path):
    source = tmp_path / "master.wav"
    source.write_bytes(b"stereo bytes")
    report = build_report(source, project_dir=tmp_path, probe_fn=_probe, measure_fn=_measure)
    markdown = emit_markdown(report)
    assert "Mono fold-down loss" in markdown
    assert "complete 1× listening" in markdown

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "audio_channel_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "balance, phase, and mono fold-down" in result.stdout
