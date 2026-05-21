import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from storyboard_plan import (  # noqa: E402
    ROUTING_SENTENCE,
    build_storyboard_plan,
    emit_markdown,
    normalize_segments,
)


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


def test_normalize_segments_preserves_ids_and_timing():
    segments = normalize_segments(_sample_transcript())
    assert len(segments) == 5
    assert segments[0].idx == 1
    assert segments[-1].end == 14.0


def test_build_storyboard_plan_routes_abstract_and_data():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    assert plan["routing_note"] == ROUTING_SENTENCE
    routes = [shot["generation_route"]["primary"] for shot in plan["shots"]]
    assert "codex_imagegen" in routes
    assert "remotion_hyperframes" in routes
    assert "dreamina_video" in routes
    dreamina = [s for s in plan["shots"] if s["generation_route"]["primary"] == "dreamina_video"]
    assert dreamina and dreamina[0]["generation_route"]["requires_paid_credits"] is True


def test_clean_script_sections_are_recorded():
    clean = "## Hook\nAI失业焦虑？\n\n## CTA\n你怎么看？\n"
    plan = build_storyboard_plan(_sample_transcript(), clean_script_text=clean, max_shots=3)
    assert plan["source"]["clean_script_sections"] == ["cta", "hook"]


def test_emit_markdown_includes_prompts_and_routing_sentence():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=4)
    md = emit_markdown(plan)
    assert ROUTING_SENTENCE in md
    assert "## shot_001" in md
    assert "```text" in md


def test_cli_writes_json_and_markdown(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps(_sample_transcript(), ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "storyboard.json"
    out_md = tmp_path / "storyboard.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/storyboard_plan.py"),
            "--transcript",
            str(transcript),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--max-shots",
            "4",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(payload["shots"]) == 4
    assert ROUTING_SENTENCE in out_md.read_text(encoding="utf-8")

