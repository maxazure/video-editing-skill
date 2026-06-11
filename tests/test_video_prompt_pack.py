import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from storyboard_plan import ROUTING_SENTENCE, build_storyboard_plan  # noqa: E402
from video_prompt_pack import build_video_prompt_pack, emit_markdown  # noqa: E402


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


def test_video_prompt_pack_auto_routes_and_blocks_paid_approval(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    pack = build_video_prompt_pack(
        plan,
        asset_root=str(tmp_path),
        characters=["same Chinese founder-host, navy jacket"],
        brand_anchors=["palette=charcoal,white,signal yellow"],
    )

    assert pack["routing_note"] == ROUTING_SENTENCE
    assert pack["summary"]["approval_required"] == 1
    assert pack["summary"]["blocking"] == 1
    assert "same Chinese founder-host" in pack["global"]["character_sheet_prompt"]

    dreamina_items = [item for item in pack["items"] if item["provider"] == "dreamina_seedance"]
    assert len(dreamina_items) == 1
    assert dreamina_items[0]["approval_status"] == "needs_approval"
    assert "may consume provider credits" in dreamina_items[0]["approval_note"]
    assert "no hard-coded Chinese text" in dreamina_items[0]["negative_prompt"]


def test_animate_stills_turns_codex_imagegen_into_i2v_prompts(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    (tmp_path / "imagegen").mkdir()
    (tmp_path / "imagegen" / "shot_001.png").write_bytes(b"fake image")

    pack = build_video_prompt_pack(plan, asset_root=str(tmp_path), animate_stills=True, approved=True)
    shot_001 = next(item for item in pack["items"] if item["shot_id"] == "shot_001")

    assert shot_001["provider"] == "dreamina_seedance"
    assert shot_001["mode"] == "image_to_video"
    assert shot_001["reference"]["resolved_path"].endswith("shot_001.png")
    assert shot_001["approval_status"] == "approved"
    assert pack["summary"]["blocking"] == 0


def test_provider_override_builds_veo_prompts_for_all_shots():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=3)
    pack = build_video_prompt_pack(plan, provider="veo", approved=True, max_duration=6)

    assert pack["summary"]["provider_veo"] == 3
    assert pack["summary"]["blocking"] == 0
    assert all(item["provider"] == "veo" for item in pack["items"])
    assert all(item["duration_seconds"] <= 6 for item in pack["items"])
    assert "Create a" in pack["items"][0]["prompt"]


def test_emit_markdown_includes_prompt_table_and_character_sheet(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=4)
    pack = build_video_prompt_pack(plan, asset_root=str(tmp_path))
    md = emit_markdown(pack)

    assert "# Video Prompt Pack" in md
    assert "| shot | provider | mode | approval | reference |" in md
    assert "## Character / Style Reference" in md
    assert ROUTING_SENTENCE in md


def test_cli_writes_prompt_pack_and_strict_fails_until_approved(tmp_path):
    plan_path = tmp_path / "storyboard_plan.json"
    plan_path.write_text(
        json.dumps(build_storyboard_plan(_sample_transcript(), max_shots=5), ensure_ascii=False),
        encoding="utf-8",
    )
    out_json = tmp_path / "video_prompt_pack.json"
    out_md = tmp_path / "video_prompt_pack.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/video_prompt_pack.py"),
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
    assert payload["summary"]["blocking"] == 1
    assert ROUTING_SENTENCE in out_md.read_text(encoding="utf-8")

    approved = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/video_prompt_pack.py"),
            "--storyboard-plan",
            str(plan_path),
            "--output",
            str(out_json),
            "--approved",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert approved.returncode == 0
