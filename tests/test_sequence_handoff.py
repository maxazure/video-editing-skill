import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import sequence_handoff  # noqa: E402
from storyboard_plan import build_storyboard_plan  # noqa: E402


def _transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "打开产品页面演示自动化流程"},
            {"id": 2, "start": 2.0, "end": 4.0, "text": "这个流程让客户节省一半时间"},
            {"id": 3, "start": 4.0, "end": 6.0, "text": "评论区告诉我你怎么看"},
        ]
    }


def _storyboard(tmp_path, *, shots=3):
    path = tmp_path / "work" / "storyboard_plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_storyboard_plan(_transcript(), max_shots=shots), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _prepared(tmp_path, *, shots=3):
    storyboard = _storyboard(tmp_path, shots=shots)
    request = sequence_handoff.build_request(
        root=tmp_path,
        storyboard_path=storyboard,
        generated_at="2026-09-15T00:00:00Z",
    )
    request_path = tmp_path / "work" / "sequence_handoff_request.json"
    response_path = tmp_path / "work" / "sequence_handoff_response.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    response = json.loads(json.dumps(request["response_template"]))
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return storyboard, request, response, request_path, response_path


def _approve(response):
    response["reviewed_by"] = "Jay"
    response["review_notes"] = "Reviewed every adjacent shot before prompt generation."
    for row in response["boundary_decisions"]:
        row["decision"] = "approve"
        row["review_note"] = "The declared baton and fallback are shootable and editable."
    return response


def test_prepare_binds_every_adjacent_boundary_and_suggests_editable_handles(tmp_path):
    _, request, _, _, _ = _prepared(tmp_path)

    assert request["version"] == sequence_handoff.REQUEST_VERSION
    assert request["summary"]["shots"] == 3
    assert request["summary"]["boundaries"] == 2
    assert request["summary"]["blocking"] == 0
    first = request["boundaries"][0]
    assert first["from_shot"] == "shot_001"
    assert first["to_shot"] == "shot_002"
    assert first["suggestion"]["head_handle_seconds"] == 0.5
    assert first["suggestion"]["tail_handle_seconds"] == 0.5
    assert first["suggestion"]["axis_note"]
    assert first["suggestion"]["screen_direction_note"]


def test_approved_review_builds_ready_source_bound_report(tmp_path):
    _, request, response, request_path, response_path = _prepared(tmp_path)
    _approve(response)
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")

    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
        generated_at="2026-09-15T00:10:00Z",
    )

    assert report["version"] == sequence_handoff.REPORT_VERSION
    assert report["status"] == "ready"
    assert report["summary"]["approved"] == 2
    assert report["summary"]["blocking"] == 0
    assert report["report_id"].startswith("sh_report_")
    assert all(item["offer_from"] and item["receive_in"] for item in report["boundaries"])


def test_missing_review_and_revision_fail_closed(tmp_path):
    _, request, response, request_path, response_path = _prepared(tmp_path)
    response["reviewed_by"] = "Jay"
    response["boundary_decisions"][0]["decision"] = "revise"
    response["boundary_decisions"][0]["review_note"] = "Direction needs another shot."
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")

    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "blocked"
    assert "boundary requires revision: boundary_001" in report["blockers"]
    assert "boundary boundary_002 decision must be approve or revise" in report["blockers"]
    assert "boundary boundary_002 requires review_note" in report["blockers"]


def test_axis_and_deliberate_rupture_rules_are_consistent(tmp_path):
    _, request, response, request_path, response_path = _prepared(tmp_path)
    _approve(response)
    first = response["boundary_decisions"][0]
    first["axis_decision"] = "reset"
    first["edit_type"] = "eyeline_match"
    second = response["boundary_decisions"][1]
    second["carrier_type"] = "deliberate_rupture"
    second["edit_type"] = "hard_cut"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")

    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert any("resets the axis" in item for item in report["blockers"])
    assert any("deliberate_rupture" in item for item in report["blockers"])


def test_live_verify_detects_storyboard_drift(tmp_path):
    storyboard, request, response, request_path, response_path = _prepared(tmp_path)
    _approve(response)
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
        generated_at="2026-09-15T00:10:00Z",
    )
    report_path = tmp_path / "work" / "sequence_handoff.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    current = sequence_handoff.verify_report(str(report_path), project_dir=str(tmp_path))
    storyboard.write_text(storyboard.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale = sequence_handoff.verify_report(str(report_path), project_dir=str(tmp_path))

    assert current["status"] == "ready"
    assert stale["status"] == "blocked"
    assert any("storyboard" in item or "drifted" in item for item in stale["verification_errors"])


def test_single_shot_has_no_boundary_and_explains_warning(tmp_path):
    _, request, _, _, _ = _prepared(tmp_path, shots=1)

    assert request["summary"]["boundaries"] == 0
    assert request["summary"]["blocking"] == 0
    assert request["summary"]["warnings"] == 1


def test_markdown_includes_matrix_and_boundary_details(tmp_path):
    _, request, response, request_path, response_path = _prepared(tmp_path)
    request_md = sequence_handoff.emit_markdown(request)
    _approve(response)
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    report_md = sequence_handoff.emit_markdown(report)

    assert "# Sequence Handoff Request" in request_md
    assert "suggested carrier" in request_md
    assert "# Sequence Handoff Report" in report_md
    assert "## boundary_001" in report_md
    assert "- Audio:" in report_md


def test_cli_prepare_audit_verify_round_trip(tmp_path):
    storyboard = _storyboard(tmp_path)
    script = os.path.join(REPO, "scripts", "sequence_handoff.py")
    prepare = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--storyboard",
            str(storyboard),
            "--output",
            "work/sequence_handoff_request.json",
            "--markdown",
            "work/sequence_handoff_request.md",
            "--response-template",
            "work/sequence_handoff_response.json",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr

    response_path = tmp_path / "work" / "sequence_handoff_response.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    _approve(response)
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    audit = subprocess.run(
        [
            sys.executable,
            script,
            "audit",
            "--project-dir",
            str(tmp_path),
            "--request",
            "work/sequence_handoff_request.json",
            "--response",
            "work/sequence_handoff_response.json",
            "--output",
            "work/sequence_handoff.json",
            "--markdown",
            "work/sequence_handoff.md",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    verify = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--project-dir",
            str(tmp_path),
            "--report",
            "work/sequence_handoff.json",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    prompt_script = os.path.join(REPO, "scripts", "video_prompt_pack.py")
    prompt_pack = subprocess.run(
        [
            sys.executable,
            prompt_script,
            "--storyboard-plan",
            str(storyboard),
            "--project-dir",
            str(tmp_path),
            "--sequence-handoff",
            "work/sequence_handoff.json",
            "--provider",
            "media_library_broll",
            "--output",
            str(tmp_path / "work" / "video_prompt_pack.json"),
        ],
        capture_output=True,
        text=True,
    )
    other_storyboard = tmp_path / "work" / "other_storyboard_plan.json"
    other_storyboard.write_bytes(storyboard.read_bytes())
    mismatched_prompt_pack = subprocess.run(
        [
            sys.executable,
            prompt_script,
            "--storyboard-plan",
            str(other_storyboard),
            "--project-dir",
            str(tmp_path),
            "--sequence-handoff",
            "work/sequence_handoff.json",
            "--provider",
            "media_library_broll",
            "--output",
            str(tmp_path / "work" / "mismatched_video_prompt_pack.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert audit.returncode == 0, audit.stderr
    assert verify.returncode == 0, verify.stderr
    assert prompt_pack.returncode == 0, prompt_pack.stderr
    assert mismatched_prompt_pack.returncode != 0
    assert "bound to a different storyboard plan" in mismatched_prompt_pack.stderr
    assert (tmp_path / "work" / "sequence_handoff.md").is_file()
