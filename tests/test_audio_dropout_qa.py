import json
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import audio_dropout_qa as dropout  # noqa: E402


MEDIA = {
    "duration": 1.0,
    "format_name": "wav",
    "audio_stream_index": 0,
    "audio_codec": "pcm_s16le",
    "sample_rate": 48000,
    "channels": 1,
    "channel_layout": "mono",
}


def _settings(**overrides):
    settings = dropout.normalize_settings()
    settings.update(overrides)
    return dropout.normalize_settings(settings)


def _windows(*, quiet_runs=(), count=50, active_db=-20.0):
    quiet = {index for start, end in quiet_runs for index in range(start, end + 1)}
    return [
        {
            "index": index,
            "time": round(index * 0.02, 6),
            "rms_dbfs": None if index in quiet else active_db,
            "peak_dbfs": None if index in quiet else active_db + 3,
        }
        for index in range(count)
    ]


def _analysis(quiet_runs=()):
    return dropout.analyze_windows(
        _windows(quiet_runs=quiet_runs),
        duration=1.0,
        settings=_settings(),
    )


def _probe(_path):
    return dict(MEDIA)


def _safe_analyze(_path, *, media, settings):
    assert media["duration"] == 1.0
    assert settings["window_ms"] == 20
    return _analysis()


def _candidate_analyze(_path, *, media, settings):
    assert media["duration"] == 1.0
    assert settings["window_ms"] == 20
    return _analysis(((20, 22),))


def _evidence(_source, output, **_kwargs):
    output.write_bytes(b"normal-speed wav evidence")


def _response(report, decision="intentional_pause"):
    response = json.loads(json.dumps(report["response_template"]))
    response["reviewed_by"] = "editor-label"
    response["full_track_played_at_1x"] = True
    for review in response["reviews"]:
        review["decision"] = decision
        review["audible_observations"] = {
            "before": "The spoken phrase is continuous before the marker.",
            "during": "A clean deliberate beat is audible at the marker.",
            "after": "The next word begins cleanly after the marker.",
        }
        review["reason"] = "The script includes a deliberate dramatic pause here."
        if decision != "intentional_pause":
            review["repair_action"] = "Restore the missing source samples and render again."
    return response


def test_parse_analysis_log_extracts_levels_and_silence():
    log = """
[Parsed_ametadata_3] frame:0    pts:0       pts_time:0
[Parsed_ametadata_3] lavfi.astats.Overall.Peak_level=-12.000000
[Parsed_ametadata_3] lavfi.astats.Overall.RMS_level=-20.500000
[Parsed_ametadata_3] frame:1    pts:320     pts_time:0.02
[Parsed_ametadata_3] lavfi.astats.Overall.Peak_level=-inf
[Parsed_ametadata_3] lavfi.astats.Overall.RMS_level=-inf
"""

    assert dropout.parse_analysis_log(log) == [
        {"index": 0, "time": 0.0, "peak_dbfs": -12.0, "rms_dbfs": -20.5},
        {"index": 1, "time": 0.02, "peak_dbfs": None, "rms_dbfs": None},
    ]


def test_brief_zero_run_between_active_context_is_candidate():
    analysis = _analysis(((20, 22),))

    assert analysis["candidate_count_before_limit"] == 1
    candidate = analysis["candidates"][0]
    assert candidate["start_time"] == 0.4
    assert candidate["end_time"] == 0.46
    assert candidate["duration_ms"] == 60.0
    assert candidate["depth_db"] == 100.0
    assert candidate["candidate_id"].startswith("dropout-001-")


def test_edge_gap_and_long_pause_are_not_brief_dropout_candidates():
    assert _analysis(((0, 2),))["candidates"] == []
    assert _analysis(((12, 36),))["candidates"] == []


def test_inactive_context_prevents_false_candidate():
    windows = _windows(quiet_runs=((20, 22),))
    for index in range(14, 20):
        windows[index]["rms_dbfs"] = -55.0
        windows[index]["peak_dbfs"] = -50.0
    analysis = dropout.analyze_windows(
        windows,
        duration=1.0,
        settings=_settings(min_dropout_ms=40, max_dropout_ms=600),
    )

    assert analysis["candidates"] == []


def test_candidate_limit_is_recorded_as_truncated_blocker():
    analysis = dropout.analyze_windows(
        _windows(quiet_runs=((10, 11), (25, 26), (40, 41)), count=60),
        duration=1.2,
        settings=_settings(max_candidates=2),
    )

    assert len(analysis["candidates"]) == 2
    assert analysis["candidate_count_before_limit"] == 3
    assert analysis["truncated"] is True
    assert "candidate limit hid 1 candidate" in dropout._scan_blockers(analysis)[0]


def test_setting_contract_rejects_ambiguous_thresholds():
    with pytest.raises(ValueError, match="lower than context"):
        dropout.normalize_settings({"dropout_threshold_dbfs": -30.0})
    with pytest.raises(ValueError, match="duration"):
        dropout.normalize_settings({"min_dropout_ms": 500, "max_dropout_ms": 400})
    with pytest.raises(ValueError, match="unknown settings"):
        dropout.normalize_settings({"mystery": 1})


def test_clean_report_is_ready_and_live_verified(tmp_path):
    source = tmp_path / "work" / "speech.wav"
    source.parent.mkdir()
    source.write_bytes(b"clean speech")
    report = dropout.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "verify" / "audio_dropout_clips",
        probe_fn=_probe,
        analyze_fn=_safe_analyze,
        evidence_fn=_evidence,
    )
    verification = dropout.verify_report(report, tmp_path, probe_fn=_probe, analyze_fn=_safe_analyze)

    assert report["status"] == "ready"
    assert report["evidence"] == []
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0


def test_candidate_requires_review_then_intentional_pause_warns(tmp_path):
    source = tmp_path / "work" / "speech.wav"
    source.parent.mkdir()
    source.write_bytes(b"speech with a pause")
    report = dropout.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "verify" / "audio_dropout_clips",
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
        evidence_fn=_evidence,
    )

    assert report["status"] == "blocked"
    assert "require explicit listening review" in report["blockers"][0]
    audited = dropout.audit_report(
        report,
        _response(report),
        project_dir=tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )
    verification = dropout.verify_report(
        audited,
        tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )

    assert audited["status"] == "warn"
    assert audited["summary"]["intentional_pauses"] == 1
    assert verification["status"] == "warn"
    assert verification["summary"]["blocking"] == 0


@pytest.mark.parametrize("decision", ["dropout", "uncertain"])
def test_confirmed_or_uncertain_candidate_remains_blocking(tmp_path, decision):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"speech with damage")
    report = dropout.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
        evidence_fn=_evidence,
    )
    audited = dropout.audit_report(
        report,
        _response(report, decision=decision),
        project_dir=tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )

    assert audited["status"] == "blocked"
    assert audited["summary"]["blocking"] >= 1


def test_source_evidence_and_derived_drift_fail_closed(tmp_path):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"speech with pause")
    report = dropout.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
        evidence_fn=_evidence,
    )
    audited = dropout.audit_report(
        report,
        _response(report),
        project_dir=tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )

    source.write_bytes(b"changed speech")
    stale_source = dropout.verify_report(
        audited,
        tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )
    assert "source bytes or audio media contract drifted" in stale_source["blockers"]

    source.write_bytes(b"speech with pause")
    evidence = tmp_path / audited["evidence"][0]["path"]
    evidence.write_bytes(b"changed evidence")
    stale_evidence = dropout.verify_report(
        audited,
        tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )
    assert any("evidence bytes changed" in item for item in stale_evidence["blockers"])

    evidence.write_bytes(b"normal-speed wav evidence")
    tampered = json.loads(json.dumps(audited))
    tampered["analysis"]["candidates"][0]["depth_db"] = 5.0
    derived = dropout.verify_report(
        tampered,
        tmp_path,
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
    )
    assert "live dropout measurements or candidates drifted" in derived["blockers"]


def test_project_escape_symlink_and_hardlink_output_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"clean speech")
    link = tmp_path / "speech-link.wav"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        dropout.build_report(
            link,
            project_dir=tmp_path,
            evidence_dir=tmp_path / "evidence",
            probe_fn=_probe,
            analyze_fn=_safe_analyze,
            evidence_fn=_evidence,
        )

    outside = tmp_path.parent / "outside-speech.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="inside the project"):
        dropout.build_report(
            outside,
            project_dir=tmp_path,
            evidence_dir=tmp_path / "evidence",
            probe_fn=_probe,
            analyze_fn=_safe_analyze,
            evidence_fn=_evidence,
        )
    outside.unlink()

    output = tmp_path / "report.json"
    os.link(source, output)
    monkeypatch.setattr(dropout, "probe_audio_media", _probe)
    monkeypatch.setattr(dropout, "analyze_audio", _safe_analyze)
    result = dropout.main(
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
    assert source.read_bytes() == b"clean speech"


def test_markdown_and_cli_help_state_listening_boundary(tmp_path):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"speech")
    report = dropout.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        probe_fn=_probe,
        analyze_fn=_candidate_analyze,
        evidence_fn=_evidence,
    )
    markdown = dropout.emit_markdown(report)
    assert "complete track at 1x" in markdown
    assert "intentional_pause" in markdown

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "audio_dropout_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "normal-speed listening evidence" in result.stdout
