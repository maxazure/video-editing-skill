import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from pipeline_manifest import build_manifest, emit_markdown  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _publish_ready_project(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "day58_master.mp4", "fake video")
    _write(tmp_path / "output" / "day58_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "day58_caption.json", {"title": "demo"})


def test_publish_ready_manifest_passes_when_required_artifacts_exist(tmp_path):
    _publish_ready_project(tmp_path)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    assert manifest["summary"]["required_ready"] == manifest["summary"]["required"]
    assert manifest["missing_required"] == []


def test_missing_required_artifacts_block_publish_ready(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "master_video" in manifest["missing_required"]
    assert any("render_final.py" in action for action in manifest["next_actions"])


def test_render_qa_fail_blocks_even_when_file_exists(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "output" / "day58_qa.json", {
        "status": "fail",
        "files": [{"path": "final.mp4", "status": "fail"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "render_qa" in manifest["blocked_gates"]


def test_optional_provider_decision_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "provider_decision.json", {
        "version": "provider_decision_log.v1",
        "summary": {
            "approval_required": 1,
            "budget_blocked": 0,
            "selected_missing_requirements": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "provider_decision" in manifest["blocked_gates"]
    provider_gate = next(g for g in manifest["gates"] if g["category"] == "provider_decision")
    assert "approval_required=1" in provider_gate["notes"]


def test_optional_privacy_redaction_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "privacy_redaction.json", {
        "version": "privacy_redaction_plan.v1",
        "summary": {
            "total_events": 1,
            "unreviewed": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "privacy_redaction" in manifest["blocked_gates"]
    privacy_gate = next(g for g in manifest["gates"] if g["category"] == "privacy_redaction")
    assert "1 blocking item(s) in summary.blocking" in privacy_gate["notes"]


def test_optional_localization_pack_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "localization_pack.json", {
        "version": "localization_pack.v1",
        "summary": {
            "cue_count": 2,
            "missing_translations": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "localization_pack" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "localization_pack")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_asset_provenance_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "asset_provenance.json", {
        "version": "asset_provenance.v1",
        "summary": {
            "items": 1,
            "blocking": 1,
            "missing_license": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "asset_provenance" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "asset_provenance")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_audio_cue_sheet_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "audio_cue_sheet.json", {
        "version": "audio_cue_sheet.v1",
        "summary": {
            "music_cues": 1,
            "sfx_cues": 2,
            "blocking": 2,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_cue_sheet" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_cue_sheet")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_video_prompt_pack_blocks_when_unapproved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "video_prompt_pack.json", {
        "version": "video_prompt_pack.v1",
        "summary": {
            "items": 2,
            "approval_required": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "video_prompt_pack" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "video_prompt_pack")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_generation_task_log_blocks_when_async_task_unfinished(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "generation_tasks.json", {
        "version": "generation_task_log.v1",
        "summary": {
            "tasks": 1,
            "blocking": 1,
            "pending": 1,
        },
        "tasks": [
            {
                "task_key": "dreamina:submit_123",
                "provider": "dreamina",
                "provider_task_id": "submit_123",
                "status": "processing",
            }
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "generation_task_log" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "generation_task_log")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_privacy_redaction_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["privacy_redaction"])

    assert manifest["status"] == "blocked"
    assert "privacy_redaction" in manifest["missing_required"]


def test_markdown_contains_gate_table_and_next_actions(tmp_path):
    manifest = build_manifest(str(tmp_path), target_stage="render_ready")

    markdown = emit_markdown(manifest)

    assert "# Pipeline Manifest" in markdown
    assert "| category | required | status | artifacts | latest | notes |" in markdown
    assert "## Next Actions" in markdown


def test_cli_writes_json_and_markdown_and_strict_exit_code(tmp_path):
    out_json = tmp_path / "pipeline_manifest.json"
    out_md = tmp_path / "pipeline_manifest.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "pipeline_manifest.py"),
            "--project-dir",
            str(tmp_path),
            "--target-stage",
            "publish_ready",
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
    manifest = json.loads(out_json.read_text(encoding="utf-8"))
    assert manifest["version"] == "pipeline_manifest.v1"
