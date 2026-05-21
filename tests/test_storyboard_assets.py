import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from storyboard_assets import build_asset_manifest, emit_markdown  # noqa: E402
from storyboard_plan import ROUTING_SENTENCE, build_storyboard_plan  # noqa: E402


def _sample_transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "今天聊 AI 的注意力机制"},
            {"id": 2, "start": 2.0, "end": 5.0, "text": "很多人因此产生失业焦虑"},
            {"id": 3, "start": 5.0, "end": 8.0, "text": "但是我发现客户付费意愿增长了 50%"},
            {"id": 4, "start": 8.0, "end": 11.0, "text": "打开电脑演示这个自动化流程"},
            {"id": 5, "start": 11.0, "end": 14.0, "text": "评论区告诉我你怎么看"},
        ]
    }


def _manual_broll_plan():
    return {
        "version": "storyboard_plan.v1",
        "target": {"platform": "xhs", "aspect": "9:16"},
        "shots": [
            {
                "id": "shot_001",
                "section": "value",
                "start": 0.0,
                "end": 2.0,
                "duration": 2.0,
                "keywords": ["workflow"],
                "generation_route": {
                    "primary": "media_library_broll",
                    "fallback": "codex_imagegen",
                    "requires_paid_credits": False,
                },
                "prompts": {"broll_query": "workflow"},
            }
        ],
    }


def test_build_asset_manifest_marks_missing_generation_work(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    manifest = build_asset_manifest(plan, asset_root=str(tmp_path))
    statuses = {item["route"]: item["status"] for item in manifest["items"]}

    assert manifest["routing_note"] == ROUTING_SENTENCE
    assert statuses["codex_imagegen"] == "needs_generation"
    assert statuses["dreamina_video"] == "needs_approval"
    assert statuses["remotion_hyperframes"] == "needs_render"
    assert manifest["summary"]["paid_credit_tasks"] == 1
    assert manifest["summary"]["blocking"] == len(manifest["items"])


def test_existing_expected_asset_is_ready(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    (tmp_path / "imagegen").mkdir()
    (tmp_path / "imagegen" / "shot_001.png").write_bytes(b"fake image")

    manifest = build_asset_manifest(plan, asset_root=str(tmp_path))
    shot_001 = manifest["items"][0]
    assert shot_001["status"] == "ready"
    assert shot_001["resolved_path"].endswith("shot_001.png")
    assert shot_001["blocking"] is False


def test_broll_candidates_are_reported(tmp_path):
    broll_dir = tmp_path / "broll"
    broll_dir.mkdir()
    (broll_dir / "workflow-desk.mp4").write_bytes(b"fake video")

    manifest = build_asset_manifest(_manual_broll_plan(), asset_root=str(tmp_path))
    item = manifest["items"][0]
    assert item["status"] == "candidate_found"
    assert item["candidate_paths"] and item["candidate_paths"][0].endswith("workflow-desk.mp4")


def test_emit_markdown_includes_status_table_and_credit_note(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    manifest = build_asset_manifest(plan, asset_root=str(tmp_path))
    md = emit_markdown(manifest)

    assert ROUTING_SENTENCE in md
    assert "| shot | route | status |" in md
    assert "needs_approval" in md
    assert "Dreamina/即梦 generation may consume credits" in md


def test_cli_writes_manifest_and_strict_fails_when_blocking(tmp_path):
    plan_path = tmp_path / "storyboard.json"
    plan_path.write_text(
        json.dumps(build_storyboard_plan(_sample_transcript(), max_shots=4), ensure_ascii=False),
        encoding="utf-8",
    )
    out_json = tmp_path / "assets.json"
    out_md = tmp_path / "assets.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/storyboard_assets.py"),
            "--storyboard-plan",
            str(plan_path),
            "--asset-root",
            str(tmp_path),
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
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] > 0
    assert ROUTING_SENTENCE in out_md.read_text(encoding="utf-8")
