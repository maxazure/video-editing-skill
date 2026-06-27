import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from audio_master_report import (  # noqa: E402
    emit_markdown,
    evaluate_measurements,
    parse_ebur128_summary,
    parse_silencedetect,
    summarize_checks,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parse_ebur128_summary_extracts_publish_metrics():
    log = """
[Parsed_ebur128_0 @ x] Summary:

  Integrated loudness:
    I:         -16.4 LUFS
    Threshold: -27.1 LUFS

  Loudness range:
    LRA:         4.6 LU
    Threshold: -37.2 LUFS
    LRA low:   -18.8 LUFS
    LRA high:  -14.2 LUFS

  True peak:
    Peak:       -1.7 dBFS
"""

    measurements = parse_ebur128_summary(log)

    assert measurements["integrated_lufs"] == -16.4
    assert measurements["integrated_threshold_lufs"] == -27.1
    assert measurements["lra_lu"] == 4.6
    assert measurements["lra_low_lufs"] == -18.8
    assert measurements["lra_high_lufs"] == -14.2
    assert measurements["true_peak_dbfs"] == -1.7


def test_parse_silencedetect_segments():
    log = "\n".join([
        "[silencedetect @ x] silence_start: 10.1",
        "[silencedetect @ x] silence_end: 14.4 | silence_duration: 4.3",
    ])

    assert parse_silencedetect(log) == [{"start": 10.1, "end": 14.4, "duration": 4.3}]


def test_evaluate_measurements_passes_publish_safe_audio():
    checks = evaluate_measurements(
        {"integrated_lufs": -16.4, "true_peak_dbfs": -1.7, "lra_lu": 6.0},
        [],
        target_lufs=-16.0,
        tolerance=2.0,
        max_true_peak_dbfs=-1.0,
        max_lra_lu=18.0,
        max_silence_seconds=3.0,
    )

    summary = summarize_checks(checks)
    assert summary["status"] == "ready"
    assert summary["blocking"] == 0


def test_evaluate_measurements_blocks_bad_loudness_peak_and_silence():
    checks = evaluate_measurements(
        {"integrated_lufs": -24.0, "true_peak_dbfs": -0.2, "lra_lu": 22.0},
        [{"start": 1.0, "end": 5.5, "duration": 4.5}],
        target_lufs=-16.0,
        tolerance=2.0,
        max_true_peak_dbfs=-1.0,
        max_lra_lu=18.0,
        max_silence_seconds=3.0,
    )

    by_name = {check["name"]: check["status"] for check in checks}
    assert by_name["integrated_loudness"] == "fail"
    assert by_name["true_peak"] == "fail"
    assert by_name["loudness_range"] == "fail"
    assert by_name["long_silence"] == "fail"
    assert summarize_checks(checks)["blocking"] == 4


def test_emit_markdown_lists_metrics_and_checks():
    report = {
        "source_path": "output/master.mp4",
        "measurements": {"integrated_lufs": -16.2, "true_peak_dbfs": -1.5, "lra_lu": 5.0},
        "silence": {"total_seconds": 0.0},
        "checks": [
            {"name": "integrated_loudness", "status": "pass", "message": "Integrated loudness -16.20 LUFS"},
        ],
        "summary": {"status": "ready", "blocking": 0, "warnings": 0},
        "suggested_filter": "loudnorm=I=-16:TP=-1:LRA=11",
    }

    markdown = emit_markdown(report)

    assert "# Audio Master Report" in markdown
    assert "Integrated loudness" in markdown
    assert "loudnorm=I=-16:TP=-1:LRA=11" in markdown


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "audio_master_report.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "audio master loudness report" in result.stdout.lower()


def test_cli_missing_file_writes_strict_report(tmp_path):
    out_json = tmp_path / "audio_master_report.json"
    out_md = tmp_path / "audio_master_report.md"
    missing = tmp_path / "missing.mp4"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "audio_master_report.py"),
            str(missing),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["version"] == "audio_master_report.v1"
    assert report["summary"]["blocking"] == 1
