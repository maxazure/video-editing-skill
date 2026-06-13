import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from generation_task_log import (  # noqa: E402
    emit_markdown,
    import_provider_decision,
    new_log,
    refresh_log,
    task_from_raw_json,
    upsert_task,
)


def test_add_dreamina_task_generates_poll_and_download_commands():
    log = upsert_task(
        new_log(),
        {
            "provider": "dreamina",
            "provider_task_id": "submit_123",
            "shot_id": "shot_002",
            "status": "submitted",
            "expected_path": "/tmp/work/generated_video/shot_002.mp4",
            "poll_command": "dreamina query_result --submit_id=submit_123",
            "download_command": "dreamina query_result --submit_id=submit_123 --download_dir=/tmp/work/generated_video",
        },
    )

    assert log["version"] == "generation_task_log.v1"
    assert log["summary"]["blocking"] == 1
    assert log["tasks"][0]["task_key"] == "dreamina:submit_123"
    assert "query_result" in log["tasks"][0]["poll_command"]
    assert log["tasks"][0]["readiness"] == "pending"


def test_completed_task_blocks_until_asset_exists(tmp_path):
    expected = tmp_path / "generated_video" / "shot_002.mp4"
    log = upsert_task(
        new_log(),
        {
            "provider": "dreamina",
            "provider_task_id": "submit_123",
            "shot_id": "shot_002",
            "status": "completed",
            "expected_path": str(expected),
        },
    )

    assert log["summary"]["needs_download"] == 1
    assert log["summary"]["blocking"] == 1

    expected.parent.mkdir()
    expected.write_bytes(b"fake mp4")
    refreshed = refresh_log(log)

    assert refreshed["summary"]["ready"] == 1
    assert refreshed["summary"]["blocking"] == 0
    assert refreshed["tasks"][0]["local_asset_exists"] is True


def test_import_provider_decision_seeds_paid_tasks():
    decision_log = {
        "version": "provider_decision_log.v1",
        "decisions": [
            {
                "decision_id": "pd_001",
                "shot_id": "shot_002",
                "selected": "dreamina_video",
                "status": "needs_approval",
                "approval_required": True,
                "paid_credit": True,
                "estimated_usd": 0.75,
                "expected_path": "/tmp/work/generated_video/shot_002.mp4",
                "next_action": "Ask for explicit paid-credit approval before submitting.",
            }
        ],
    }

    log = import_provider_decision(new_log(), decision_log)

    assert log["summary"]["needs_approval"] == 1
    assert log["summary"]["blocking"] == 1
    assert log["tasks"][0]["task_key"] == "dreamina_video:shot_002"
    assert log["tasks"][0]["approval_required"] is True


def test_submitted_task_replaces_provider_decision_placeholder_for_same_shot():
    decision_log = {
        "decisions": [
            {
                "decision_id": "pd_001",
                "shot_id": "shot_002",
                "selected": "dreamina_video",
                "approval_required": True,
                "paid_credit": True,
                "expected_path": "/tmp/work/generated_video/shot_002.mp4",
            }
        ]
    }

    log = import_provider_decision(new_log(), decision_log)
    log = upsert_task(
        log,
        {
            "provider": "dreamina",
            "provider_task_id": "submit_123",
            "shot_id": "shot_002",
            "status": "submitted",
            "expected_path": "/tmp/work/generated_video/shot_002.mp4",
        },
    )

    assert log["summary"]["tasks"] == 1
    assert log["tasks"][0]["task_key"] == "dreamina:submit_123"
    assert log["tasks"][0]["provider_task_id"] == "submit_123"


def test_task_from_raw_json_maps_pixverse_status_code():
    task = task_from_raw_json({
        "id": 123456,
        "status_code": 1,
        "video_url": "https://example.com/video.mp4",
    })

    assert task["provider_task_id"] == "123456"
    assert task["status"] == "completed"
    assert task["result_url"] == "https://example.com/video.mp4"


def test_emit_markdown_includes_commands_and_next_actions():
    log = upsert_task(
        new_log(),
        {
            "provider": "dreamina",
            "provider_task_id": "submit_123",
            "status": "processing",
            "poll_command": "dreamina query_result --submit_id=submit_123",
        },
    )

    md = emit_markdown(log)

    assert "# Generation Task Log" in md
    assert "dreamina:submit_123" in md
    assert "Poll:" in md
    assert "Next Actions" in md


def test_cli_add_writes_log_and_strict_fails_while_pending(tmp_path):
    log_path = tmp_path / "generation_tasks.json"
    md_path = tmp_path / "generation_tasks.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/generation_task_log.py"),
            "add",
            "--log",
            str(log_path),
            "--provider",
            "dreamina",
            "--task-id",
            "submit_123",
            "--shot-id",
            "shot_002",
            "--expected-path",
            str(tmp_path / "generated_video" / "shot_002.mp4"),
            "--markdown",
            str(md_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] == 1
    assert "Generation Task Log" in md_path.read_text(encoding="utf-8")
