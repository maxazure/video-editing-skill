"""auto_enrich orchestrator — end-to-end plan composition."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from auto_enrich import build_plan  # noqa: E402


def _sample_transcript():
    return {
        "segments": [
            {"start": 0.5, "end": 2.0, "text": "今天聊聊 AI 失业焦虑"},
            {"start": 3.0, "end": 5.0, "text": "但是 AI 实际上让我更有机会"},
            {"start": 8.0, "end": 12.0, "text": "我突然意识到客户付费意愿没降"},
            {"start": 18.0, "end": 22.0, "text": "增长了 50% 的项目数量"},
        ]
    }


def test_build_plan_no_extras():
    plan = build_plan(_sample_transcript())
    assert "broll" in plan
    assert "stickers" in plan
    assert "chapter_cards" in plan
    assert "imagegen" in plan
    # At minimum the transition word "但是" + the long-shot gap should yield broll cues
    assert len(plan["broll"]) >= 1


def test_build_plan_includes_imagegen_for_abstract_concept():
    """A transcript that mentions 注意力机制 should produce an imagegen cue."""
    transcript = {
        "segments": [
            {"start": 1.0, "end": 4.0, "text": "我们来聊一下注意力机制的本质"},
        ]
    }
    plan = build_plan(transcript)
    assert len(plan["imagegen"]) >= 1
    assert plan["imagegen"][0]["template_id"] == "attention_mechanism"


def test_build_plan_with_clean_script(tmp_path):
    md = tmp_path / "clean.md"
    md.write_text("## Hook\n\n## Pain\n\n## Value\n")
    plan = build_plan(_sample_transcript(),
                      clean_script_path=str(md), total_duration=30)
    assert len(plan["chapter_cards"]) == 3


def test_cli_writes_plan(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_sample_transcript()))
    out_path = tmp_path / "plan.json"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/auto_enrich.py"),
         "--transcript", str(transcript_path),
         "--output", str(out_path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    plan = json.loads(out_path.read_text())
    assert "broll" in plan and "stickers" in plan and "chapter_cards" in plan
