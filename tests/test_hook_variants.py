import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from hook_variants import build_context, build_plan, emit_markdown, generate_variants  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _transcript():
    return {
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "今天聊 AI 剪辑为什么总是卡在前三秒。"},
            {"start": 2.0, "end": 5.0, "text": "我以前只改字幕，但是数据一直没有起色。"},
            {"start": 5.0, "end": 8.0, "text": "后来我把 hook 拆成 8 个角度，点击率提升了 32%。"},
        ]
    }


def test_build_plan_generates_ranked_hook_variants():
    plan = build_plan(
        transcript=_transcript(),
        topic="AI剪辑",
        persona="剪辑师",
        platform="xhs",
        count=8,
    )

    assert plan["version"] == "hook_variants.v1"
    assert plan["summary"]["variants"] == 8
    assert plan["summary"]["usable"] >= 1
    assert plan["summary"]["recommended"] == plan["variants"][0]["id"]
    assert {item["family"] for item in plan["variants"]} >= {
        "pattern_interrupt",
        "pain_question",
        "number_map",
        "proof_first",
    }
    assert all(item["char_count"] <= item["max_chars"] for item in plan["variants"])


def test_context_extracts_numbers_pain_and_persona():
    context = build_context(
        transcript=_transcript(),
        clean_script="",
        topic=None,
        persona="AI创业者",
        title=None,
    )

    assert context["number"] == "8个"
    assert context["persona"] == "AI创业者"
    assert context["pain"]
    assert context["language"] == "zh"


def test_content_guard_blocks_all_variants_when_topic_is_forbidden():
    context = build_context(
        transcript={},
        clean_script="",
        topic="微信引流",
        persona=None,
        title=None,
    )
    variants = generate_variants(context, platform="xhs", count=3)

    assert all(item["status"] == "blocked" for item in variants)
    assert all(any("hard:diversion-wechat" in risk for risk in item["risks"]) for item in variants)


def test_english_input_uses_english_templates():
    plan = build_plan(
        transcript={
            "segments": [
                {"start": 0, "end": 3, "text": "Most product demos lose viewers because the opening is vague."},
                {"start": 3, "end": 8, "text": "Here are 3 fixes that made the demo clear."},
            ]
        },
        topic="product demos",
        persona="founder",
        platform="youtube_shorts",
        count=4,
    )

    hooks = [item["hook"] for item in plan["variants"]]
    assert any("product demos" in hook for hook in hooks)
    assert plan["input_summary"]["language"] == "en"


def test_emit_markdown_contains_review_table():
    plan = build_plan(transcript=_transcript(), topic="AI剪辑", platform="xhs", count=2)
    markdown = emit_markdown(plan)

    assert "# Hook Variants" in markdown
    assert "| ID | Status | Score | Hook | Angle | Risk |" in markdown
    assert "Next: choose one hook id" in markdown


def test_cli_writes_json_and_markdown(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    out_json = tmp_path / "hook_variants.json"
    out_md = tmp_path / "hook_variants.md"
    transcript_path.write_text(json.dumps(_transcript(), ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/hook_variants.py"),
            "--transcript",
            str(transcript_path),
            "--topic",
            "AI剪辑",
            "--persona",
            "剪辑师",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["usable"] >= 1
    assert "Hook Variants" in out_md.read_text(encoding="utf-8")


def test_cli_strict_fails_when_every_hook_is_blocked(tmp_path):
    out_json = tmp_path / "hook_variants.json"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/hook_variants.py"),
            "--topic",
            "微信引流",
            "--count",
            "2",
            "--output",
            str(out_json),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] == 1
