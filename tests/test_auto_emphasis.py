import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from auto_emphasis import build_plan, emit_markdown, schedule_emphasis  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _transcript():
    return {
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 2.0,
                "text": "为什么大家都忽略这个问题？",
                "words": [{"word": "为什么", "start": 0.1, "end": 0.4}],
            },
            {
                "id": 2,
                "start": 4.0,
                "end": 6.0,
                "text": "转化率增长了 42%",
                "words": [{"word": "42%", "start": 5.1, "end": 5.4}],
            },
            {"id": 3, "start": 8.0, "end": 10.0, "text": "但是关键是执行速度"},
            {"id": 4, "start": 13.0, "end": 15.0, "text": "所以结论很简单"},
        ]
    }


def test_schedule_emphasis_detects_core_triggers_and_word_anchor():
    cues = schedule_emphasis(_transcript(), min_interval_seconds=1.0)
    triggers = [cue.trigger for cue in cues]

    assert "question_hook" in triggers
    assert "numeric_claim" in triggers
    assert "contrast_turn" in triggers
    assert "payoff_line" in triggers
    numeric = next(cue for cue in cues if cue.trigger == "numeric_claim")
    assert numeric.start == 5.1
    assert numeric.label == "数据点"
    assert numeric.effect == "badge_push_in"


def test_build_plan_applies_min_interval_and_summary():
    transcript = {
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "为什么是 30%？"},
            {"start": 0.8, "end": 2.6, "text": "但是答案是增长"},
        ]
    }

    plan = build_plan(transcript, min_interval_seconds=3.0, max_cues=4)

    assert plan["version"] == "auto_emphasis_plan.v1"
    assert plan["summary"]["cues"] == 1
    assert len(plan["emphasis_cues"]) == 1
    assert plan["emphasis_cues"][0]["trigger"] == "numeric_claim"


def test_emit_markdown_lists_cues():
    plan = build_plan(_transcript(), min_interval_seconds=1.0)
    markdown = emit_markdown(plan)

    assert "# Auto Emphasis Plan" in markdown
    assert "`numeric_claim`" in markdown
    assert "Render with `render_final.py --enrich-plan`" in markdown


def test_cli_writes_json_and_markdown(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    out_json = tmp_path / "emphasis_plan.json"
    out_md = tmp_path / "emphasis_plan.md"
    transcript_path.write_text(json.dumps(_transcript(), ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/auto_emphasis.py"),
            "--transcript",
            str(transcript_path),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--min-interval",
            "1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["cues"] >= 4
    assert "Auto Emphasis Plan" in out_md.read_text(encoding="utf-8")
