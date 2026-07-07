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


def test_source_inventory_can_be_required_for_analysis(tmp_path):
    _write(tmp_path / "work" / "source_inventory.json", {
        "version": "project_bootstrap.v1",
        "status": "ready",
        "summary": {"files": 1, "warnings": 0},
        "files": [{"relative_path": "origin/raw/talk.mp4"}],
    })
    _write(tmp_path / "work" / "transcript.json", {"segments": []})

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["source_inventory"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "source_inventory")
    assert gate["required"] is True
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_hook_variants_can_be_required_for_review(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "hook_variants.json", {
        "version": "hook_variants.v1",
        "summary": {"variants": 2, "usable": 2, "blocking": 0},
        "variants": [{"id": "hook_01", "hook": "别再卡开头了"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["hook_variants"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "hook_variants")
    assert gate["required"] is True
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


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


def test_video_understanding_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "video_understanding.json", {
        "version": "video_understanding.v1",
        "summary": {"frames": 2, "detections": 1, "tracks": 1},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["video_understanding"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "video_understanding")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_color_grade_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "color_grade.json", {
        "version": "color_grade.v1",
        "preset": "screen",
        "ffmpeg": {"vf": "eq=contrast=1.0800"},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["color_grade"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "color_grade")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_takes_pack_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "takes_pack.json", {
        "version": "takes_pack.v1",
        "summary": {"sources": 2, "phrases": 8, "warnings": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "takes_pack")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1
    assert gate["blocks_when_present"] is False


def test_shorts_batch_artifact_is_discovered_without_blocking_when_ready(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "shorts_batch.json", {
        "version": "shorts_batch.v1",
        "status": "ready",
        "summary": {"jobs": 2, "planned": 2, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "shorts_batch")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1
    assert gate["blocks_when_present"] is True


def test_optional_shorts_batch_blocks_when_source_missing(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "shorts_batch.json", {
        "version": "shorts_batch.v1",
        "status": "blocked",
        "summary": {"jobs": 2, "planned": 0, "blocking": 2},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "shorts_batch" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "shorts_batch")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_emphasis_plan_is_discovered_as_enrich_plan(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "emphasis_plan.json", {
        "version": "auto_emphasis_plan.v1",
        "emphasis_cues": [{"start": 1.0, "end": 2.0, "label": "重点"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    gate = next(g for g in manifest["gates"] if g["category"] == "enrich_plan")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_optional_edit_preflight_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "edit_preflight.json", {
        "version": "edit_preflight.v1",
        "status": "blocked",
        "summary": {
            "blocking": 2,
            "warnings": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "edit_preflight" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_preflight")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_publish_package_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "publish_package.json", {
        "version": "publish_package.v1",
        "summary": {
            "platforms": 3,
            "ready_platforms": 2,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "publish_package" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "publish_package")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_publish_package_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "publish_package.json", {
        "version": "publish_package.v1",
        "summary": {
            "platforms": 3,
            "ready_platforms": 3,
            "blocking": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["publish_package"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "publish_package")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_review_dashboard_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "review_dashboard.json", {
        "version": "review_dashboard.v1",
        "status": "ready",
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "review_dashboard")
    assert gate["status"] == "ready"
    assert gate["blocks_when_present"] is False


def test_otio_handoff_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "day58_edit.otio", {
        "OTIO_SCHEMA": "Timeline.1",
        "name": "DAY58",
    })
    _write(tmp_path / "work" / "day58_edit.otio.json", {
        "kind": "nle_handoff_otio",
        "event_count": 2,
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "nle_handoff")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 2
    assert gate["blocks_when_present"] is False


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


def test_optional_source_receipts_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "source_receipts.json", {
        "version": "source_receipts.v1",
        "summary": {
            "claims": 2,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "source_receipts" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "source_receipts")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_source_receipts_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "source_receipts.json", {
        "version": "source_receipts.v1",
        "summary": {
            "claims": 1,
            "blocking": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["source_receipts"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "source_receipts")
    assert gate["required"] is True
    assert gate["status"] == "ready"


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


def test_optional_audio_sync_blocks_when_low_confidence(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "audio_sync_plan.json", {
        "version": "audio_sync_plan.v1",
        "summary": {
            "status": "review",
            "blocking": 1,
            "offset_seconds": 0.36,
            "confidence": 0.22,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_sync" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_sync")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_audio_master_report_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "output" / "day58_audio_master_report.json", {
        "version": "audio_master_report.v1",
        "summary": {
            "blocking": 1,
            "warnings": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_master_report" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_master_report")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


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
