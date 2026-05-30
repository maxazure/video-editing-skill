import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from storyboard_plan import ROUTING_SENTENCE, build_storyboard_plan  # noqa: E402
from transition_bridge import build_transition_bridge_plan, emit_markdown  # noqa: E402


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


def _asset_manifest(tmp_path):
    return {
        "version": "storyboard_asset_manifest.v1",
        "items": [
            {
                "shot_id": "shot_001",
                "status": "ready",
                "resolved_path": str(tmp_path / "imagegen" / "shot_001.png"),
                "expected_path": str(tmp_path / "imagegen" / "shot_001.png"),
            },
            {
                "shot_id": "shot_002",
                "status": "candidate_found",
                "candidate_paths": [str(tmp_path / "broll" / "anxiety.mp4")],
                "candidate_scores": [{"path": str(tmp_path / "broll" / "anxiety.mp4"), "score": 9.5}],
                "expected_path": str(tmp_path / "broll" / "shot_002.mp4"),
            },
        ],
    }


def test_auto_mode_marks_stronger_jumps_for_approval(tmp_path):
    storyboard = build_storyboard_plan(_sample_transcript(), max_shots=5)
    plan = build_transition_bridge_plan(
        storyboard,
        asset_manifest=_asset_manifest(tmp_path),
        asset_root=str(tmp_path),
        mode="auto",
        max_ai_bridges=2,
    )

    assert plan["version"] == "transition_bridge_plan.v1"
    assert plan["routing_note"] == ROUTING_SENTENCE
    assert plan["summary"]["bridges"] == 4
    assert plan["summary"]["paid_credit_tasks"] <= 2
    assert any(bridge["route"] == "dreamina_video" for bridge in plan["bridges"])
    first = plan["bridges"][0]["reference_frames"]["first_frame"]
    last = plan["bridges"][0]["reference_frames"]["last_frame"]
    assert first["asset_status"] == "resolved"
    assert last["asset_status"] == "candidate"


def test_default_mode_uses_local_transition_without_blocking(tmp_path):
    storyboard = build_storyboard_plan(_sample_transcript(), max_shots=4)
    plan = build_transition_bridge_plan(storyboard, asset_root=str(tmp_path), mode="default")

    assert plan["summary"]["paid_credit_tasks"] == 0
    assert plan["summary"]["blocking"] == 0
    assert {bridge["route"] for bridge in plan["bridges"]} == {"deterministic_crossfade"}


def test_emit_markdown_includes_prompts_and_credit_note(tmp_path):
    storyboard = build_storyboard_plan(_sample_transcript(), max_shots=3)
    plan = build_transition_bridge_plan(storyboard, asset_root=str(tmp_path), mode="ai")
    md = emit_markdown(plan)

    assert ROUTING_SENTENCE in md
    assert "| bridge | from -> to | route |" in md
    assert "Dreamina/即梦 transition generation may consume credits" in md
    assert "```text" in md


def test_cli_writes_plan_and_strict_fails_when_approval_needed(tmp_path):
    storyboard_path = tmp_path / "storyboard.json"
    storyboard_path.write_text(
        json.dumps(build_storyboard_plan(_sample_transcript(), max_shots=3), ensure_ascii=False),
        encoding="utf-8",
    )
    out_json = tmp_path / "transition_bridge.json"
    out_md = tmp_path / "transition_bridge.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/transition_bridge.py"),
            "--storyboard-plan",
            str(storyboard_path),
            "--asset-root",
            str(tmp_path),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--mode",
            "ai",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] == payload["summary"]["bridges"]
    assert "Transition Bridge Plan" in out_md.read_text(encoding="utf-8")
