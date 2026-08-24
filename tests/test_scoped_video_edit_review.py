import json
import os
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import scoped_video_edit_review as scoped  # noqa: E402


MEDIA = {
    "duration": 6.0,
    "fps": 24.0,
    "width": 640,
    "height": 360,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "has_audio": True,
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
}


def _fake_evidence(_source, _edited, output_dir, **_kwargs):
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index in range(1, 4):
        path = output_dir / f"comparison_{index:02d}.jpg"
        path.write_bytes(f"comparison-{index}".encode("utf-8"))
        frames.append(path)
    preview = output_dir / "comparison_preview.mp4"
    preview.write_bytes(b"comparison-preview")
    return {
        "canvas": {"width": 1280, "height": 360, "fps": 24.0},
        "frames": frames,
        "preview": preview,
    }


def _request(tmp_path, monkeypatch, *, source_media=None, edited_media=None):
    source = tmp_path / "origin" / "source.mp4"
    edited = tmp_path / "work" / "edited.mp4"
    source.parent.mkdir(parents=True)
    edited.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    edited.write_bytes(b"edited-video")
    media_by_path = {
        str(source): dict(source_media or MEDIA),
        str(edited): dict(edited_media or MEDIA),
    }
    monkeypatch.setattr(scoped, "probe_media", lambda path: dict(media_by_path[path]))
    monkeypatch.setattr(scoped, "generate_comparison_evidence", _fake_evidence)
    request = scoped.prepare_request(
        str(source),
        str(edited),
        project_dir=str(tmp_path),
        evidence_dir="verify/scoped_video_edit",
        change_category="background",
        change="replace only the office background with a night studio",
        protections=["subject_identity", "performance_motion", "camera_motion", "source_audio"],
        start=1.0,
        end=5.0,
    )
    return source, edited, request


def _response(request, **overrides):
    response = {
        "version": scoped.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "review-agent",
        "playback": {
            "source_full_1x": True,
            "edited_full_1x": True,
            "comparison_scope_1x": True,
        },
        "target_change": {
            "status": "pass",
            "evidence": "The office is replaced throughout the declared range without holes.",
        },
        "protections": [
            {
                "key": key,
                "status": "pass",
                "evidence": f"Same-time frames and full playback preserve {key}.",
            }
            for key in request["protections"]
        ],
        "verdict": "pass",
        "repair_action": "",
        "notes": "Reviewed source, edited result, both audio tracks, and every paired frame.",
    }
    response.update(overrides)
    return response


def test_prepare_binds_scope_prompt_media_and_same_time_evidence(tmp_path, monkeypatch):
    _source, _edited, request = _request(tmp_path, monkeypatch)

    assert request["version"] == scoped.REQUEST_VERSION
    assert request["edit_scope"]["start"] == 1.0
    assert request["edit_scope"]["end"] == 5.0
    assert request["evidence"]["sample_times"] == [1.6, 3.0, 4.4]
    assert len(request["evidence"]["frames"]) == 3
    assert request["evidence"]["preview"]["sha256"]
    assert "Change only this background target" in request["suggested_prompt"]
    assert "original dialogue" in request["suggested_prompt"]
    assert request["request_id"] == scoped._request_id(request)
    assert request["response_template"]["request_id"] == request["request_id"]


def test_prepare_rejects_same_content_and_underspecified_invariants(tmp_path, monkeypatch):
    source = tmp_path / "origin" / "source.mp4"
    edited = tmp_path / "work" / "edited.mp4"
    source.parent.mkdir(parents=True)
    edited.parent.mkdir(parents=True)
    source.write_bytes(b"same")
    edited.write_bytes(b"same")
    monkeypatch.setattr(scoped, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(scoped, "generate_comparison_evidence", _fake_evidence)

    try:
        scoped.prepare_request(
            str(source),
            str(edited),
            project_dir=str(tmp_path),
            evidence_dir="verify/scoped",
            change_category="background",
            change="replace the background",
            protections=["subject_identity", "camera_motion"],
        )
    except ValueError as exc:
        assert "identical bytes" in str(exc)
    else:
        raise AssertionError("identical source and edited bytes must be rejected")

    edited.write_bytes(b"different")
    try:
        scoped.prepare_request(
            str(source),
            str(edited),
            project_dir=str(tmp_path),
            evidence_dir="verify/scoped",
            change_category="background",
            change="replace the background",
            protections=["subject_identity"],
        )
    except ValueError as exc:
        assert "at least two" in str(exc)
    else:
        raise AssertionError("an underspecified preservation contract must be rejected")


def test_prepare_rejects_project_escape_and_symlink_inputs(tmp_path, monkeypatch):
    source = tmp_path / "origin" / "source.mp4"
    edited = tmp_path / "work" / "edited.mp4"
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    source.parent.mkdir(parents=True)
    edited.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    edited.write_bytes(b"edited")
    outside.write_bytes(b"outside")
    monkeypatch.setattr(scoped, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(scoped, "generate_comparison_evidence", _fake_evidence)

    try:
        scoped.prepare_request(
            str(source),
            str(outside),
            project_dir=str(tmp_path),
            evidence_dir="verify/scoped",
            change_category="background",
            change="replace the background",
            protections=["subject_identity", "camera_motion"],
        )
    except ValueError as exc:
        assert "inside the project" in str(exc)
    else:
        raise AssertionError("project escape must be rejected")

    link = tmp_path / "work" / "edited-link.mp4"
    link.symlink_to(edited)
    try:
        scoped.prepare_request(
            str(source),
            str(link),
            project_dir=str(tmp_path),
            evidence_dir="verify/scoped",
            change_category="background",
            change="replace the background",
            protections=["subject_identity", "camera_motion"],
        )
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink input must be rejected")


def test_force_cannot_overwrite_source_through_evidence_hardlink(tmp_path, monkeypatch):
    source = tmp_path / "origin" / "source.mp4"
    edited = tmp_path / "work" / "edited.mp4"
    evidence_dir = tmp_path / "verify" / "scoped"
    source.parent.mkdir(parents=True)
    edited.parent.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    source.write_bytes(b"source")
    edited.write_bytes(b"edited")
    os.link(source, evidence_dir / "comparison_01.jpg")
    monkeypatch.setattr(scoped, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(scoped, "generate_comparison_evidence", _fake_evidence)

    try:
        scoped.prepare_request(
            str(source),
            str(edited),
            project_dir=str(tmp_path),
            evidence_dir=str(evidence_dir),
            change_category="background",
            change="replace the background",
            protections=["subject_identity", "camera_motion"],
            force=True,
        )
    except ValueError as exc:
        assert "must not overwrite source video" in str(exc)
    else:
        raise AssertionError("evidence hardlink to source must be rejected even with --force")
    assert source.read_bytes() == b"source"


def test_ready_report_live_verifies_and_detects_source_or_evidence_drift(tmp_path, monkeypatch):
    source, _edited, request = _request(tmp_path, monkeypatch)
    report = scoped.build_report(request, _response(request))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert scoped.verify_report(report)["status"] == "ready"

    frame = tmp_path / request["evidence"]["frames"][0]["path"]
    frame.write_bytes(b"changed-evidence")
    verification = scoped.verify_report(report)
    assert verification["status"] == "blocked"
    assert any("evidence bytes changed" in item for item in verification["blockers"])

    frame.write_bytes(b"comparison-1")
    source.write_bytes(b"changed-source")
    verification = scoped.verify_report(report)
    assert any("source video bytes changed" in item for item in verification["blockers"])


def test_failed_or_unobservable_checks_require_fail_verdict_and_repair(tmp_path, monkeypatch):
    _source, _edited, request = _request(tmp_path, monkeypatch)
    response = _response(request)
    response["protections"][1]["status"] = "not_observable"
    response["protections"][1]["evidence"] = "Fast occlusion hides the hands during the change."
    response["verdict"] = "fail"
    response["repair_action"] = "Regenerate only this background edit with the gesture and timing locked."

    report = scoped.build_report(request, response)

    assert report["status"] == "blocked"
    assert any("performance_motion=not_observable" in item for item in report["blockers"])
    assert report["review"]["repair_action"]

    response["repair_action"] = ""
    report = scoped.build_report(request, response)
    assert "repair_action is required when review fails" in report["blockers"]


def test_automatic_media_drift_cannot_be_approved_by_human_response(tmp_path, monkeypatch):
    edited_media = dict(MEDIA, duration=6.5, width=1280, height=720, fps=30.0)
    _source, _edited, request = _request(tmp_path, monkeypatch, edited_media=edited_media)
    response = _response(request, verdict="fail", repair_action="Re-run the edit while preserving the media contract.")
    report = scoped.build_report(request, response)

    assert report["status"] == "blocked"
    assert any("duration drift" in item for item in report["blockers"])
    assert "edited dimensions differ from the source" in report["blockers"]
    assert "edited frame rate differs from the source by more than 0.05 fps" in report["blockers"]


def test_tampered_prompt_or_report_state_fails_live_verification(tmp_path, monkeypatch):
    _source, _edited, request = _request(tmp_path, monkeypatch)
    report = scoped.build_report(request, _response(request))
    report["request"]["suggested_prompt"] = "Change everything."
    report["request"]["request_id"] = scoped._request_id(report["request"])
    report["response"]["request_id"] = report["request"]["request_id"]
    report["status"] = "ready"
    report["summary"]["blocking"] = 0
    report["blockers"] = []
    report["report_id"] = scoped._report_id(report)

    verification = scoped.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("suggested prompt" in item for item in verification["blockers"])


def test_malformed_embedded_contract_returns_blocked_instead_of_crashing(tmp_path, monkeypatch):
    _source, _edited, request = _request(tmp_path, monkeypatch)
    report = scoped.build_report(request, _response(request))
    report["request"]["edit_scope"]["start"] = "not-a-time"
    report["request"]["request_id"] = scoped._request_id(report["request"])
    report["response"]["request_id"] = report["request"]["request_id"]

    verification = scoped.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("invalid edit scope" in item for item in verification["blockers"])


def test_main_writes_request_response_report_and_markdown(tmp_path, monkeypatch):
    source = tmp_path / "origin" / "source.mp4"
    edited = tmp_path / "work" / "edited.mp4"
    source.parent.mkdir(parents=True)
    edited.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    edited.write_bytes(b"edited")
    monkeypatch.setattr(scoped, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(scoped, "generate_comparison_evidence", _fake_evidence)

    code = scoped.main(
        [
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--source",
            str(source),
            "--edited",
            str(edited),
            "--change-category",
            "wardrobe",
            "--change",
            "change only the jacket from blue to red",
            "--preserve",
            "subject_identity",
            "--preserve",
            "performance_motion",
            "--evidence-dir",
            "verify/scoped",
            "--output",
            "work/request.json",
            "--markdown",
            "work/request.md",
            "--response-template",
            "work/response.json",
        ]
    )
    assert code == 0
    request = json.loads((tmp_path / "work" / "request.json").read_text(encoding="utf-8"))
    response = _response(request)
    (tmp_path / "work" / "response.json").write_text(json.dumps(response), encoding="utf-8")

    code = scoped.main(
        [
            "audit",
            "--request",
            str(tmp_path / "work" / "request.json"),
            "--response",
            str(tmp_path / "work" / "response.json"),
            "--output",
            str(tmp_path / "work" / "scoped_video_edit_review.json"),
            "--markdown",
            str(tmp_path / "work" / "scoped_video_edit_review.md"),
            "--strict",
        ]
    )
    assert code == 0
    assert "Status: **ready**" in (tmp_path / "work" / "scoped_video_edit_review.md").read_text(encoding="utf-8")

    source_before = source.read_bytes()
    code = scoped.main(
        [
            "audit",
            "--request",
            str(tmp_path / "work" / "request.json"),
            "--response",
            str(tmp_path / "work" / "response.json"),
            "--output",
            str(source),
            "--markdown",
            str(tmp_path / "work" / "unsafe.md"),
            "--force",
        ]
    )
    assert code == 1
    assert source.read_bytes() == source_before
