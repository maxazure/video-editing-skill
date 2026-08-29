import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import narration_loudness_qa as narration  # noqa: E402


MEDIA = {
    "duration": 6.0,
    "format_name": "wav",
    "audio_stream_index": 0,
    "audio_codec": "pcm_s16le",
    "sample_rate": 48000,
    "channels": 1,
    "channel_layout": "mono",
}


def _segments_payload(*, exception=False):
    first = {"id": "line-1", "start": 0.0, "end": 2.0, "text": "First line"}
    if exception:
        first["loudness_exception"] = {
            "reason": "intentional whispered opening",
            "reviewer": "editor",
        }
    return {
        "segments": [
            first,
            {"id": "line-2", "start": 2.4, "end": 4.8, "text": "Second line"},
        ]
    }


def _write_inputs(tmp_path, *, payload=None):
    source = tmp_path / "work" / "narration.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"final narration bytes")
    segments = tmp_path / "work" / "narration_segments.json"
    segments.write_text(json.dumps(payload or _segments_payload()), encoding="utf-8")
    return source, segments


def _measurements(first=-18.1, second=-17.4, *, peak=-3.0, lra=2.0):
    def measure(_path, *, start, end):
        return {
            "integrated_lufs": first if start < 1.0 else second,
            "true_peak_dbtp": peak,
            "lra_lu": lra,
        }

    return measure


def test_normalize_segments_accepts_segments_or_phrases_and_rejects_overlap():
    normalized = narration.normalize_segments(_segments_payload(), source_duration=6.0)
    phrases = narration.normalize_segments(
        {"phrases": _segments_payload()["segments"]},
        source_duration=6.0,
    )

    assert normalized == phrases
    assert [item["id"] for item in normalized] == ["line-1", "line-2"]

    numeric_ids = _segments_payload()
    numeric_ids["segments"][0]["id"] = 0
    assert narration.normalize_segments(numeric_ids, source_duration=6.0)[0]["id"] == "0"

    overlapping = _segments_payload()
    overlapping["segments"][1]["start"] = 1.9
    with pytest.raises(ValueError, match="overlap"):
        narration.normalize_segments(overlapping, source_duration=6.0)


def test_exception_requires_reason_reviewer_and_rejects_unknown_fields():
    missing = _segments_payload(exception=True)
    missing["segments"][0]["loudness_exception"].pop("reviewer")
    with pytest.raises(ValueError, match="requires reason and reviewer"):
        narration.normalize_segments(missing, source_duration=6.0)

    unknown = _segments_payload(exception=True)
    unknown["segments"][0]["loudness_exception"]["approved"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        narration.normalize_segments(unknown, source_duration=6.0)


def test_ready_report_measures_each_phrase_and_computes_spread(tmp_path):
    source, segments = _write_inputs(tmp_path)
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(),
    )

    assert report["status"] == "ready"
    assert report["summary"]["segments"] == 2
    assert report["summary"]["spread_lu"] == 0.7
    assert report["summary"]["blocking"] == 0
    assert report["measurements"][0]["true_peak_dbtp"] == -3.0


def test_target_spread_peak_lra_and_minimum_fail_closed(tmp_path):
    source, segments = _write_inputs(tmp_path)
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        min_segment_seconds=2.2,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(first=-24.0, second=-17.0, peak=-1.0, lra=7.0),
    )

    assert report["status"] == "blocked"
    assert report["summary"]["target_violations"] == 1
    assert report["summary"]["peak_violations"] == 2
    assert report["summary"]["lra_violations"] == 2
    assert report["summary"]["minimum_duration_violations"] == 1
    assert report["summary"]["spread_lu"] == 7.0
    assert any("spread 7.00 LU" in item for item in report["blockers"])


def test_documented_exception_excludes_target_lra_and_spread_but_not_peak(tmp_path):
    source, segments = _write_inputs(tmp_path, payload=_segments_payload(exception=True))
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(first=-26.0, second=-18.0, peak=-1.0, lra=8.0),
    )

    assert report["status"] == "blocked"
    assert report["summary"]["documented_exceptions"] == 1
    assert report["summary"]["comparable_segments"] == 1
    assert any("do not bypass the peak ceiling" in item for item in report["blockers"])
    assert any("documented exception" in item for item in report["warnings"])


def test_build_and_live_verify_bind_media_segments_and_measurements(tmp_path):
    source, segments = _write_inputs(tmp_path)
    measure = _measurements()
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=measure,
    )
    verification = narration.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=measure,
    )

    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0

    segments.write_text(json.dumps(_segments_payload(), indent=2), encoding="utf-8")
    stale = narration.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=measure,
    )
    assert stale["status"] == "blocked"
    assert any("segment manifest bytes changed" in item for item in stale["blockers"])


def test_tampering_and_live_measurement_drift_fail_closed(tmp_path):
    source, segments = _write_inputs(tmp_path)
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(),
    )

    report["summary"]["spread_lu"] = 9.0
    tampered = narration.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(),
    )
    assert any("stored summary" in item for item in tampered["blockers"])

    report["summary"]["spread_lu"] = 0.7
    drifted = narration.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(first=-18.1, second=-16.8),
    )
    assert any("live phrase measurements differ" in item for item in drifted["blockers"])


def test_markdown_distinguishes_phrase_gate_from_final_mix_and_performance(tmp_path):
    source, segments = _write_inputs(tmp_path)
    report = narration.build_report(
        source,
        segments,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        measure_fn=_measurements(),
    )

    markdown = narration.emit_markdown(report)
    assert "Phrase measurements" in markdown
    assert "audio_master_report.py" in markdown
    assert "does not judge timbre" in markdown


def test_inputs_must_stay_in_project_and_not_use_symlinks(tmp_path):
    _source, segments = _write_inputs(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    outside.write_bytes(b"outside narration")
    linked = tmp_path / "work" / "linked.wav"
    linked.symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        narration.build_report(
            linked,
            segments,
            project_dir=tmp_path,
            probe_fn=lambda _path: dict(MEDIA),
            measure_fn=_measurements(),
        )
    with pytest.raises(ValueError, match="stay inside the project"):
        narration.build_report(
            outside,
            segments,
            project_dir=tmp_path,
            probe_fn=lambda _path: dict(MEDIA),
            measure_fn=_measurements(),
        )


def test_cli_refuses_report_hardlink_to_bound_media(tmp_path):
    source, segments = _write_inputs(tmp_path)
    report_path = tmp_path / "verify" / "narration_loudness_qa.json"
    report_path.parent.mkdir()
    os.link(source, report_path)

    result = narration.main(
        [
            "analyze",
            str(source),
            "--segments",
            str(segments),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(report_path),
            "--markdown",
            str(tmp_path / "verify" / "narration_loudness_qa.md"),
            "--force",
        ]
    )

    assert result == 1
    assert source.read_bytes() == b"final narration bytes"


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "narration_loudness_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "phrase-level loudness consistency" in result.stdout
