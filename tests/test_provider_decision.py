import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from provider_decision import (  # noqa: E402
    build_provider_decision_log,
    emit_markdown,
    parse_cost_overrides,
)
from storyboard_plan import ROUTING_SENTENCE  # noqa: E402


def _asset_manifest():
    return {
        "version": "storyboard_asset_manifest.v1",
        "routing_note": ROUTING_SENTENCE,
        "asset_root": "/tmp/video-work",
        "items": [
            {
                "shot_id": "shot_001",
                "route": "codex_imagegen",
                "fallback_route": "media_library_broll",
                "status": "needs_generation",
                "expected_path": "/tmp/video-work/imagegen/shot_001.png",
                "candidate_paths": [],
                "next_action": "Generate the still.",
            },
            {
                "shot_id": "shot_002",
                "route": "dreamina_video",
                "fallback_route": "media_library_broll",
                "status": "needs_approval",
                "expected_path": "/tmp/video-work/generated_video/shot_002.mp4",
                "candidate_paths": [],
                "next_action": "Confirm paid credits.",
            },
        ],
    }


def test_build_provider_decision_scores_options_and_marks_paid_approval():
    log = build_provider_decision_log(
        _asset_manifest(),
        command_lookup=lambda name: name == "dreamina",
    )

    assert log["routing_note"] == ROUTING_SENTENCE
    assert log["summary"]["items"] == 2
    assert log["summary"]["approval_required"] == 1
    assert log["decisions"][0]["selected"] == "codex_imagegen"
    assert log["decisions"][0]["status"] == "ready_to_execute"
    assert log["decisions"][1]["selected"] == "dreamina_video"
    assert log["decisions"][1]["status"] == "needs_approval"
    assert log["decisions"][1]["paid_credit"] is True
    assert log["decisions"][1]["options_considered"][0]["score"] > 0


def test_ready_and_candidate_items_skip_generation():
    manifest = {
        "version": "storyboard_asset_manifest.v1",
        "items": [
            {
                "shot_id": "shot_001",
                "route": "codex_imagegen",
                "status": "ready",
                "expected_path": "/tmp/work/imagegen/shot_001.png",
            },
            {
                "shot_id": "shot_002",
                "route": "media_library_broll",
                "status": "candidate_found",
                "expected_path": "/tmp/work/broll/shot_002.mp4",
                "candidate_paths": ["/tmp/media/workflow.mp4"],
            },
        ],
    }
    log = build_provider_decision_log(manifest)

    assert [d["selected"] for d in log["decisions"]] == [
        "existing_asset",
        "local_media_candidate",
    ]
    assert log["summary"]["approval_required"] == 0
    assert log["decisions"][0]["status"] == "ready"
    assert log["decisions"][1]["status"] == "candidate_found"


def test_budget_cap_blocks_selected_paid_tasks():
    log = build_provider_decision_log(
        _asset_manifest(),
        budget_cap_usd=0.10,
        command_lookup=lambda name: True,
    )

    paid_decision = log["decisions"][1]
    assert paid_decision["selected"] == "dreamina_video"
    assert paid_decision["budget_status"] == "over_cap"
    assert paid_decision["status"] == "budget_blocked"
    assert log["summary"]["budget_blocked"] == 1


def test_missing_command_is_reported_but_fallback_can_be_selected():
    log = build_provider_decision_log(
        _asset_manifest(),
        command_lookup=lambda name: False,
    )
    dreamina_options = log["decisions"][1]["options_considered"]
    dreamina = [opt for opt in dreamina_options if opt["option_id"] == "dreamina_video"][0]

    assert dreamina["available"] is False
    assert "dreamina" in dreamina["missing_requirements"]
    assert log["decisions"][1]["selected"] != "dreamina_video"
    assert log["decisions"][1]["status"] == "fallback_selected"
    assert log["summary"]["fallback_selected"] == 1


def test_emit_markdown_includes_decisions():
    log = build_provider_decision_log(
        _asset_manifest(),
        command_lookup=lambda name: True,
    )
    md = emit_markdown(log)

    assert "# Provider Decision Log" in md
    assert "shot_002" in md
    assert "Dreamina" in md
    assert ROUTING_SENTENCE in md


def test_parse_cost_overrides():
    assert parse_cost_overrides(["dreamina_video=1.2"]) == {"dreamina_video": 1.2}


def test_cli_writes_json_and_markdown_and_strict_fails_on_approval(tmp_path):
    manifest_path = tmp_path / "assets.json"
    manifest_path.write_text(json.dumps(_asset_manifest(), ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "provider.json"
    out_md = tmp_path / "provider.md"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_dreamina = fake_bin / "dreamina"
    fake_dreamina.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_dreamina.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/provider_decision.py"),
            "--asset-manifest",
            str(manifest_path),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["approval_required"] >= 1
    assert "Provider Decision Log" in out_md.read_text(encoding="utf-8")
