"""Story Engine — prompt emission, LLM-output validation, materialisation."""
import json
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from rewrite_script import (  # noqa: E402
    STRUCTURES, validate_llm_output, materialise, emit_prompt,
    load_hook_templates, load_cta_templates,
)


def _sample_transcript():
    return {
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "今天聊聊 AI 失业焦虑。"},
            {"start": 2.5, "end": 5.0, "text": "我发现机会比焦虑多得多。"},
        ]
    }


def test_templates_load():
    hooks = load_hook_templates()
    ctas = load_cta_templates()
    assert len(hooks) == 8, f"expected 8 hook templates, got {len(hooks)}"
    assert len(ctas) == 5, f"expected 5 cta templates, got {len(ctas)}"
    # spot-check structure
    assert hooks[0]["id"] == "anti_consensus"
    assert ctas[0]["id"] == "save_bait"


def test_emit_prompt_contains_all_required_blocks():
    prompt = emit_prompt(_sample_transcript(), "pain_solve", "auto", 150, None)
    assert "pain_solve" in prompt
    assert "钩子模板候选" in prompt
    assert "CTA 模板候选" in prompt
    assert "JSON" in prompt
    assert "今天聊聊 AI 失业焦虑" in prompt


def test_validate_accepts_well_formed_output():
    data = {
        "hook": "AI失业焦虑？我看到机会",
        "hook_template_id": "anti_consensus",
        "pain": "客户找我做网站，我用 AI 加速完成。",
        "turn": "AI 没让我失业，反而让我更忙。",
        "value": ["AI 让我接更多项目", "客户付费意愿没降", "时间成本反而下降了"],
        "cta": "你是焦虑还是抓住机会？评论区告诉我",
        "estimated_speech_seconds": 90,
    }
    assert validate_llm_output(data, 150) == []


def test_validate_rejects_too_long_hook():
    data = {
        "hook": "A" * 25,
        "pain": "x", "turn": "x", "value": ["x"], "cta": "x",
    }
    errors = validate_llm_output(data, 150)
    assert any("hook too long" in e for e in errors)


def test_validate_rejects_missing_field():
    data = {"hook": "x", "pain": "x", "turn": "x", "cta": "x"}  # no value
    errors = validate_llm_output(data, 150)
    assert any("value" in e for e in errors)


def test_validate_rejects_duration_overrun():
    data = {
        "hook": "x", "pain": "x", "turn": "x", "value": ["a"], "cta": "x",
        "estimated_speech_seconds": 999,
    }
    errors = validate_llm_output(data, 150)
    assert any("exceeds max_duration" in e for e in errors)


def test_materialise_produces_markdown():
    data = {
        "hook": "AI失业焦虑？我看到机会",
        "pain": "客户找我做网站。",
        "turn": "AI 没让我失业。",
        "value": ["接更多项目", "客户付费意愿没降"],
        "cta": "你是焦虑还是抓住机会？",
    }
    md = materialise(data)
    assert "# Clean Script" in md
    assert "## Hook" in md
    assert "## Pain" in md
    assert "## Value" in md
    assert "1. 接更多项目" in md
    assert "2. 客户付费意愿没降" in md
    assert "## CTA" in md


def test_cli_emit_prompt(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_sample_transcript()))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/rewrite_script.py"),
         "--transcript", str(transcript_path), "--emit-prompt"],
        capture_output=True, text=True, check=True,
    )
    assert "JSON" in out.stdout
    assert "pain_solve" in out.stdout


def test_cli_materialise_round_trip(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_sample_transcript()))
    llm_path = tmp_path / "llm.json"
    llm_path.write_text(json.dumps({
        "hook": "AI失业焦虑？我看到机会",
        "pain": "客户找我做网站，AI 加速完成。",
        "turn": "AI 没让我失业，反而让我更忙。",
        "value": ["接更多项目", "时间成本下降"],
        "cta": "你是焦虑还是抓住机会？",
        "estimated_speech_seconds": 90,
    }))
    out_path = tmp_path / "clean.md"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/rewrite_script.py"),
         "--transcript", str(transcript_path),
         "--llm-output", str(llm_path),
         "--output", str(out_path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    assert out_path.exists()
    content = out_path.read_text()
    assert "AI失业焦虑" in content


def test_cli_rejects_llm_output_with_diversion(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_sample_transcript()))
    llm_path = tmp_path / "llm.json"
    llm_path.write_text(json.dumps({
        "hook": "加微信 wx",
        "pain": "x", "turn": "x", "value": ["a"], "cta": "x",
        "estimated_speech_seconds": 30,
    }))
    out_path = tmp_path / "clean.md"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/rewrite_script.py"),
         "--transcript", str(transcript_path),
         "--llm-output", str(llm_path),
         "--output", str(out_path)],
        capture_output=True, text=True,
    )
    assert out.returncode != 0, "Diversion-flagged LLM output should be rejected"
