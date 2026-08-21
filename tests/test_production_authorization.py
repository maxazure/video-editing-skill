import copy
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import production_authorization as pa  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_bytes(value if isinstance(value, bytes) else str(value).encode())


def _scope(tmp_path: Path):
    source = tmp_path / "origin" / "host.mp4"
    _write(source, b"source-video")
    scope = {
        "version": pa.SCOPE_VERSION,
        "assets": [
            {"id": "host_video", "path": "origin/host.mp4", "role": "talking-head source"},
        ],
        "actions": [
            {
                "id": "cloud_transcription",
                "kind": "external_upload",
                "description": "Upload the talking-head source for word-level transcription.",
                "purpose": "Create a timed transcript for source-aligned edits.",
                "provider": "Named Transcription API",
                "cost_or_quota": "May consume hosted transcription quota.",
                "asset_ids": ["host_video"],
            },
            {
                "id": "opening_reorder",
                "kind": "editorial_reorder",
                "description": "Move one approved quote to the first three seconds.",
                "purpose": "Strengthen the opening hook without inventing speech.",
                "asset_ids": ["host_video"],
            },
            {
                "id": "voice_clone_narration",
                "kind": "voice_clone",
                "description": "Clone the host voice for one corrected narration line.",
                "purpose": "Repair one approved wording error.",
                "provider": "Named Voice Provider",
                "cost_or_quota": "May consume voice-cloning and TTS quota.",
                "asset_ids": ["host_video"],
            },
        ],
        "rights_items": [
            {
                "id": "host_likeness",
                "kind": "real_person_likeness",
                "subject": "Host",
                "intended_use": "Edit and publish the supplied talking-head footage.",
                "asset_ids": ["host_video"],
            },
            {
                "id": "host_voice",
                "kind": "voice_clone",
                "subject": "Host",
                "intended_use": "Generate one corrected narration line.",
                "asset_ids": ["host_video"],
            },
        ],
    }
    scope_path = tmp_path / "work" / "production_scope.json"
    _write(scope_path, scope)
    return scope_path, source, scope


def _response(request):
    response = copy.deepcopy(request["response_template"])
    response["reviewed_by"] = "project-owner"
    response["review_notes"] = "Approved only for the exact project scope and bound source bytes."
    for item in response["action_decisions"]:
        item["decision"] = "approve"
        item["note"] = f"Approved exact action {item['action_id']}."
    bases = {
        item["id"]: item["allowed_bases"][0]
        for item in request["rights_items"]
    }
    for item in response["rights_decisions"]:
        item["decision"] = "approve"
        item["basis"] = bases[item["rights_id"]]
        item["evidence_note"] = "The named subject states they are the source subject/speaker and approves this use."
    return response


def _ready_report(tmp_path: Path):
    scope_path, source, _scope_data = _scope(tmp_path)
    root = tmp_path.resolve()
    request = pa.build_request(root=root, scope_path=scope_path)
    response = _response(request)
    request_path = tmp_path / "work" / "production_authorization_request.json"
    response_path = tmp_path / "work" / "production_authorization_response.json"
    report_path = tmp_path / "work" / "production_authorization.json"
    _write(request_path, request)
    _write(response_path, response)
    report = pa.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    _write(report_path, report)
    return report, report_path, request, response, request_path, response_path, source, scope_path


def test_ready_report_binds_actions_rights_and_sources(tmp_path):
    report, report_path, _request, _response_data, _request_path, _response_path, _source, _scope_path = _ready_report(tmp_path)

    assert report["status"] == "ready"
    assert report["summary"]["actions_approved"] == 3
    assert report["summary"]["rights_approved"] == 2
    assert report["inputs"]["assets"][0]["sha256"]
    assert report["report_id"].startswith("pa_report_")
    assert pa.verify_report(str(report_path), project_dir=str(tmp_path))["status"] == "ready"


def test_prepare_rejects_unknown_assets_and_incomplete_external_action(tmp_path):
    scope_path, _source, scope = _scope(tmp_path)
    scope["actions"][0]["asset_ids"] = ["missing_asset"]
    scope["actions"][0]["provider"] = ""
    scope["actions"][0]["cost_or_quota"] = ""
    _write(scope_path, scope)

    request = pa.build_request(root=tmp_path.resolve(), scope_path=scope_path)

    assert request["summary"]["blocking"] > 0
    assert any("unknown assets" in item for item in request["blockers"])
    assert any("exact provider" in item for item in request["blockers"])
    assert any("cost or quota" in item for item in request["blockers"])


def test_prepare_requires_exact_publish_destination(tmp_path):
    scope_path, _source, scope = _scope(tmp_path)
    scope["actions"].append(
        {
            "id": "publish_xhs",
            "kind": "publish",
            "description": "Publish the approved delivery file.",
            "purpose": "Release the final post.",
            "provider": "",
            "cost_or_quota": "",
            "asset_ids": ["host_video"],
        }
    )
    _write(scope_path, scope)

    request = pa.build_request(root=tmp_path.resolve(), scope_path=scope_path)

    assert any("publish_xhs must name the exact provider or surface" in item for item in request["blockers"])


def test_prepare_requires_voice_rights_for_voice_clone_action(tmp_path):
    scope_path, _source, scope = _scope(tmp_path)
    scope["rights_items"] = [scope["rights_items"][0]]
    _write(scope_path, scope)

    request = pa.build_request(root=tmp_path.resolve(), scope_path=scope_path)

    assert "voice_clone action requires a voice_clone rights item" in request["blockers"]


def test_audit_blocks_rejected_or_missing_decisions(tmp_path):
    scope_path, _source, _scope_data = _scope(tmp_path)
    root = tmp_path.resolve()
    request = pa.build_request(root=root, scope_path=scope_path)
    response = _response(request)
    response["action_decisions"][0]["decision"] = "reject"
    response["action_decisions"] = response["action_decisions"][:-1]
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    _write(request_path, request)
    _write(response_path, response)

    report = pa.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "blocked"
    assert "action rejected: cloud_transcription" in report["blockers"]
    assert "missing action decision: voice_clone_narration" in report["blockers"]


def test_audit_enforces_rights_basis_and_evidence_note(tmp_path):
    scope_path, _source, scope = _scope(tmp_path)
    scope["rights_items"].append(
        {
            "id": "child_presenter",
            "kind": "minor_likeness",
            "subject": "Child presenter",
            "intended_use": "Appear in the final social clip.",
            "asset_ids": ["host_video"],
        }
    )
    _write(scope_path, scope)
    root = tmp_path.resolve()
    request = pa.build_request(root=root, scope_path=scope_path)
    response = _response(request)
    child = next(item for item in response["rights_decisions"] if item["rights_id"] == "child_presenter")
    child.update({"decision": "approve", "basis": "subject_self", "evidence_note": ""})
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    _write(request_path, request)
    _write(response_path, response)

    report = pa.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert any("unsupported approval basis" in item for item in report["blockers"])
    assert any("evidence_note is required" in item for item in report["blockers"])


def test_live_verify_detects_source_response_and_report_drift(tmp_path):
    report, report_path, _request, response, _request_path, response_path, source, _scope_path = _ready_report(tmp_path)
    source.write_bytes(b"changed-source")
    stale_source = pa.verify_report(str(report_path), project_dir=str(tmp_path))
    assert stale_source["status"] == "blocked"
    assert any("drifted" in item for item in stale_source["verification_errors"])

    source.write_bytes(b"source-video")
    response["review_notes"] = "changed after audit"
    _write(response_path, response)
    stale_response = pa.verify_report(str(report_path), project_dir=str(tmp_path))
    assert stale_response["status"] == "blocked"
    assert "response file has drifted" in stale_response["verification_errors"]

    _write(response_path, report["response"])
    tampered = dict(report)
    tampered["reviewed_by"] = "somebody-else"
    _write(report_path, tampered)
    stale_report = pa.verify_report(str(report_path), project_dir=str(tmp_path))
    assert stale_report["status"] == "blocked"
    assert any("derived authorization state" in item for item in stale_report["verification_errors"])


def test_cli_prepare_audit_verify_round_trip(tmp_path):
    scope_path, _source, _scope_data = _scope(tmp_path)
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    report_path = tmp_path / "work" / "production_authorization.json"
    markdown_path = tmp_path / "work" / "production_authorization.md"
    script = os.path.join(REPO, "scripts", "production_authorization.py")

    prepare = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--scope",
            str(scope_path),
            "--output",
            str(request_path),
            "--response-template",
            str(response_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    request = json.loads(request_path.read_text(encoding="utf-8"))
    _write(response_path, _response(request))
    audit = subprocess.run(
        [
            sys.executable,
            script,
            "audit",
            "--project-dir",
            str(tmp_path),
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--output",
            str(report_path),
            "--markdown",
            str(markdown_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 0, audit.stderr
    verify = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--project-dir",
            str(tmp_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert "Reviewer labels" in markdown_path.read_text(encoding="utf-8")


def test_cli_never_overwrites_bound_source_even_with_force(tmp_path):
    scope_path, source, _scope_data = _scope(tmp_path)
    script = os.path.join(REPO, "scripts", "production_authorization.py")

    result = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--scope",
            str(scope_path),
            "--output",
            str(source),
            "--response-template",
            str(tmp_path / "work" / "response.json"),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must not overwrite an input" in result.stderr
    assert source.read_bytes() == b"source-video"

    hardlink = tmp_path / "work" / "source-alias.mp4"
    os.link(source, hardlink)
    result = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--scope",
            str(scope_path),
            "--output",
            str(hardlink),
            "--response-template",
            str(tmp_path / "work" / "response.json"),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must not overwrite a hard-linked input" in result.stderr
    assert source.read_bytes() == b"source-video"


def test_pipeline_manifest_live_verifies_production_authorization(tmp_path):
    _report, _report_path, _request, _response_data, _request_path, _response_path, source, _scope_path = _ready_report(tmp_path)
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "demo_master.mp4", b"master")
    _write(tmp_path / "output" / "demo_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "demo_caption.json", {"title": "demo"})

    ready = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["production_authorization"],
    )
    gate = next(item for item in ready["gates"] if item["category"] == "production_authorization")
    assert gate["status"] == "ready"

    source.write_bytes(b"changed")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(item for item in stale["gates"] if item["category"] == "production_authorization")
    assert gate["status"] == "blocked"
    assert "production_authorization" in stale["blocked_gates"]
