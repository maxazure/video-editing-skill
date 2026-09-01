import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import temporal_artifact_qa as temporal  # noqa: E402


MEDIA = {
    "duration": 1.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}


def _settings(**overrides):
    values = temporal.default_settings()
    values.update({"analysis_width": 32, "local_radius": 4})
    values.update(overrides)
    return values


def _solid(value, *, width=32, height=2):
    return bytes([value]) * width * height


def _safe_analysis():
    frames = [_solid(24) for _ in range(12)]
    return temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )


def _candidate_analysis():
    frames = [_solid(16)] * 5 + [_solid(245)] + [_solid(16)] * 6
    return temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )


def _evidence(_source, output, **_kwargs):
    output.write_bytes(b"candidate-jpeg")


def _review_response(report, decision="intentional_edit"):
    response = json.loads(json.dumps(report["response_template"]))
    response["reviewed_by"] = "editor-label"
    response["full_video_played_at_1x"] = True
    for item in response["reviews"]:
        item["decision"] = decision
        item["frame_observations"] = {
            "before": "Presenter faces camera before the transient.",
            "suspect": "A full-frame insert appears for one sampled frame.",
            "after": "Presenter returns to the same pose after the transient.",
        }
        item["reason"] = "The one-frame insert is an approved timeline marker."
        if decision != "intentional_edit":
            item["repair_action"] = "Remove the transient and render the shot again."
    return response


def test_single_frame_return_to_state_is_detected():
    frames = [_solid(10)] * 5 + [_solid(250)] + [_solid(10)] * 5

    analysis = temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert len(analysis["candidates"]) == 1
    candidate = analysis["candidates"][0]
    assert candidate["start_frame"] == candidate["end_frame"] == 5
    assert candidate["entry_mse"] > candidate["threshold_mse"]
    assert candidate["recovery_mse"] == 0


def test_normal_hard_cut_is_not_a_return_to_state_candidate():
    frames = [_solid(10)] * 5 + [_solid(250)] * 6

    analysis = temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert analysis["candidates"] == []


def test_two_frame_excursion_is_detected_as_one_candidate():
    frames = [_solid(10)] * 5 + [_solid(250), _solid(240)] + [_solid(10)] * 5

    analysis = temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert [(item["start_frame"], item["end_frame"]) for item in analysis["candidates"]] == [(5, 6)]


def test_sustained_motion_baseline_does_not_create_false_spike():
    frames = [_solid(value) for value in range(0, 220, 20)]

    analysis = temporal.analyze_frame_sequence(
        frames,
        width=32,
        height=2,
        fps=30.0,
        settings=_settings(),
    )

    assert analysis["candidates"] == []


def test_safe_report_is_ready_and_live_verified(tmp_path):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir()
    source.write_bytes(b"final master")
    analysis = _safe_analysis()

    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "verify" / "temporal_artifact_frames",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: json.loads(json.dumps(analysis)),
        evidence_fn=_evidence,
    )
    verification = temporal.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: json.loads(json.dumps(analysis)),
    )

    assert report["status"] == "ready"
    assert report["evidence"] == []
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0


def test_candidate_requires_review_then_intentional_edit_warns(tmp_path):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir()
    source.write_bytes(b"final master")
    analysis = _candidate_analysis()

    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "verify" / "temporal_artifact_frames",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        evidence_fn=_evidence,
    )
    assert report["status"] == "blocked"
    assert "require explicit visual review" in report["blockers"][0]

    audited = temporal.audit_report(
        report,
        _review_response(report),
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )
    verification = temporal.verify_report(
        audited,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )

    assert audited["status"] == "warn"
    assert audited["summary"]["intentional_edits"] == 1
    assert verification["status"] == "warn"
    assert verification["summary"]["blocking"] == 0


def test_confirmed_artifact_and_uncertain_decisions_fail_closed(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _candidate_analysis()
    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        evidence_fn=_evidence,
    )

    for decision in ("artifact", "uncertain"):
        audited = temporal.audit_report(
            report,
            _review_response(report, decision=decision),
            project_dir=tmp_path,
            probe_fn=lambda _path: dict(MEDIA),
            analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        )
        assert audited["status"] == "blocked"
        assert audited["summary"]["blocking"] >= 1


def test_incomplete_frame_observations_are_rejected(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _candidate_analysis()
    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        evidence_fn=_evidence,
    )
    response = _review_response(report)
    response["reviews"][0]["frame_observations"]["suspect"] = ""

    audited = temporal.audit_report(
        report,
        response,
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )

    assert audited["status"] == "blocked"
    assert any("frame_observations.suspect" in item for item in audited["blockers"])


def test_source_analysis_and_evidence_drift_fail_closed(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _candidate_analysis()
    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        evidence_fn=_evidence,
    )
    audited = temporal.audit_report(
        report,
        _review_response(report),
        project_dir=tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )

    source.write_bytes(b"changed master")
    stale_source = temporal.verify_report(
        audited,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )
    assert any("source video bytes changed" in item for item in stale_source["blockers"])

    source.write_bytes(b"final master")
    evidence_path = tmp_path / audited["evidence"][0]["path"]
    evidence_path.write_bytes(b"changed evidence")
    stale_evidence = temporal.verify_report(
        audited,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
    )
    assert any("evidence bytes changed" in item for item in stale_evidence["blockers"])


def test_tampered_analysis_and_derived_state_are_rejected(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _safe_analysis()
    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: json.loads(json.dumps(analysis)),
        evidence_fn=_evidence,
    )
    report["analysis"]["transitions"]["maximum_mse"] = 999.0
    report["status"] = "warn"

    verification = temporal.verify_report(
        report,
        tmp_path,
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: json.loads(json.dumps(analysis)),
    )

    assert any("live temporal artifact evidence differs" in item for item in verification["blockers"])
    assert any("stored status" in item for item in verification["blockers"])
    assert any("scan_id" in item for item in verification["blockers"])


def test_markdown_requires_full_playback_and_contextual_review(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    analysis = _candidate_analysis()
    report = temporal.build_report(
        source,
        project_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        settings=_settings(),
        probe_fn=lambda _path: dict(MEDIA),
        analyze_fn=lambda *_args, **_kwargs: dict(analysis),
        evidence_fn=_evidence,
    )

    markdown = temporal.emit_markdown(report)

    assert "before / suspect / after" in markdown
    assert "Play the full video at 1x" in markdown
    assert "not an automatic visual-quality verdict" in markdown


def test_cli_refuses_report_hardlink_to_source(tmp_path, monkeypatch):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    report_path = tmp_path / "temporal_artifact_qa.json"
    os.link(source, report_path)
    monkeypatch.setattr(temporal, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(temporal, "analyze_video", lambda *_args, **_kwargs: _safe_analysis())

    result = temporal.main([
        "analyze",
        str(source),
        "--project-dir",
        str(tmp_path),
        "--output",
        str(report_path),
        "--force",
    ])

    assert result == 1
    assert source.read_bytes() == b"final master"


def test_force_refuses_candidate_evidence_symlink(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final master")
    protected = tmp_path / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "temporal_artifact_0001_before_suspect_after.jpg").symlink_to(protected)
    analysis = _candidate_analysis()

    try:
        temporal.build_report(
            source,
            project_dir=tmp_path,
            evidence_dir=evidence_dir,
            settings=_settings(),
            probe_fn=lambda _path: dict(MEDIA),
            analyze_fn=lambda *_args, **_kwargs: dict(analysis),
            evidence_fn=_evidence,
            force=True,
        )
    except ValueError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("expected candidate evidence symlink to be rejected")
    assert protected.read_text(encoding="utf-8") == "keep"


def test_cli_help_lists_analyze_audit_and_verify():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/temporal_artifact_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "analyze" in result.stdout
    assert "audit" in result.stdout
    assert "verify" in result.stdout
