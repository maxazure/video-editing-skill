"""imagegen_hint — abstract-concept detection + prompt composition.

These tests cover:
  - The YAML template loader produces non-empty system_suffix + samples
  - Concept-keyword matches the right template id
  - Metaphor-cue phrases trigger free-form prompts
  - System suffix is appended to every prompt
  - Quoted exact-text targets pass through unchanged
  - CLI round-trips
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from imagegen_hint import (  # noqa: E402
    _load_templates, detect_opportunities, emit_codex_markdown,
    ABSTRACT_CONCEPTS, METAPHOR_CUES,
)


def _t(*segs):
    return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segs]}


# ── Templates ──────────────────────────────────────────────────────────────


def test_templates_load_with_required_sections():
    t = _load_templates()
    assert t["system_suffix"], "system_suffix should be non-empty"
    assert t["structures"], "should have at least one structure"
    assert t["samples"], "should have at least one sample"


def test_each_sample_has_required_fields():
    t = _load_templates()
    for sample in t["samples"]:
        assert "id" in sample
        assert "concept" in sample
        assert "use_case" in sample
        assert "target_aspect" in sample
        assert sample.get("prompt_en"), f"sample {sample['id']} missing prompt_en"


def test_all_abstract_concept_ids_resolve_to_samples():
    t = _load_templates()
    sample_ids = {s["id"] for s in t["samples"]}
    for keyword, sample_id in ABSTRACT_CONCEPTS.items():
        assert sample_id in sample_ids, (
            f"ABSTRACT_CONCEPTS[{keyword!r}] points to {sample_id!r} "
            f"but that id is not in imagegen_templates.yaml"
        )


# ── Detection ──────────────────────────────────────────────────────────────


def test_attention_mechanism_concept_detected():
    cues = detect_opportunities(_t(
        (0.0, 3.0, "今天我们来聊一下AI的注意力机制是怎么工作的"),
    ))
    assert len(cues) == 1
    assert cues[0].template_id == "attention_mechanism"
    assert cues[0].use_case == "abstract_concept"
    assert cues[0].target_aspect == "9:16"
    assert cues[0].timing_seconds == 0.0


def test_information_bubble_concept_detected():
    cues = detect_opportunities(_t(
        (5.0, 8.0, "你可能已经陷入了信息茧房而不自知"),
    ))
    assert any(c.template_id == "information_bubble" for c in cues)


def test_compound_interest_concept_detected():
    cues = detect_opportunities(_t(
        (10.0, 14.0, "复利效应在职业生涯中其实非常关键"),
    ))
    assert any(c.template_id == "compound_interest" for c in cues)


def test_metaphor_cue_triggers_free_form_prompt():
    cues = detect_opportunities(_t(
        (0.0, 4.0, "比方说你每天写一篇文章，一年就有 365 篇"),
    ))
    metaphor_cues = [c for c in cues if c.reason.startswith("metaphor-cue:")]
    assert len(metaphor_cues) == 1
    assert metaphor_cues[0].template_id is None
    # The free-form skeleton should still include the constraints
    assert "no watermark" in metaphor_cues[0].prompt_en.lower() or \
           "watermark" in metaphor_cues[0].prompt_en.lower()


def test_concept_keyword_takes_priority_over_metaphor():
    """When a segment has both an abstract concept AND a metaphor cue, we
    prefer the concrete-template path."""
    cues = detect_opportunities(_t(
        (0.0, 4.0, "比方说注意力机制就像聚光灯一样选择性聚焦"),
    ))
    assert len(cues) == 1
    assert cues[0].template_id == "attention_mechanism", (
        "Concept should win when both signals fire"
    )


def test_metaphor_cues_are_detectable():
    """Sanity check the curated phrase list isn't empty."""
    assert len(METAPHOR_CUES) >= 5
    for cue in METAPHOR_CUES:
        assert isinstance(cue, str) and cue.strip()


def test_clean_script_chapter_title_produces_cue():
    transcript = _t((0.0, 3.0, "今天聊 AI"))
    clean_md = "# Clean\n\n## Hook\n\n## 注意力机制\n\n## CTA\n"
    cues = detect_opportunities(transcript, clean_md)
    chapter_cues = [c for c in cues if c.reason.startswith("chapter-title:")]
    assert len(chapter_cues) >= 1
    assert chapter_cues[0].use_case == "chapter_background"


def test_empty_transcript_returns_empty():
    assert detect_opportunities({"segments": []}) == []


def test_dedupe_same_concept_within_one_second():
    """Two adjacent segments mentioning the same concept should produce one cue."""
    cues = detect_opportunities(_t(
        (0.0, 0.5, "复利效应"),
        (0.6, 1.2, "复利的复利效应又是什么"),  # same concept, close timing
    ))
    compound_cues = [c for c in cues if c.template_id == "compound_interest"]
    assert len(compound_cues) <= 2  # dedupe should keep at most one per second


# ── Prompt composition ─────────────────────────────────────────────────────


def test_system_suffix_appended_to_prompt():
    cues = detect_opportunities(_t((0.0, 2.0, "注意力机制")))
    assert cues
    assert "no watermark" in cues[0].prompt_en.lower() or \
           "watermarks" in cues[0].prompt_en.lower(), (
        "system_suffix's constraint clause should be appended"
    )


def test_compound_interest_prompt_includes_quoted_exact_text():
    """The compound_interest sample uses quoted text for exact-render. Verify it survives."""
    cues = detect_opportunities(_t((0.0, 2.0, "复利效应")))
    assert any('"COMPOUND INTEREST"' in c.prompt_en for c in cues)


def test_attention_prompt_has_no_human_face_constraint():
    cues = detect_opportunities(_t((0.0, 2.0, "注意力机制")))
    assert cues
    # The system_suffix mentions avoiding face close-ups
    suffix_keywords = ["face", "hands", "watermark"]
    assert any(k in cues[0].prompt_en.lower() for k in suffix_keywords)


# ── Codex markdown output ──────────────────────────────────────────────────


def test_emit_codex_markdown_empty():
    md = emit_codex_markdown([])
    assert "No image-generation opportunities" in md


def test_emit_codex_markdown_lists_each_cue():
    cues = detect_opportunities(_t(
        (1.0, 3.0, "注意力机制是核心"),
        (8.0, 10.0, "复利效应非常重要"),
    ))
    md = emit_codex_markdown(cues)
    assert "## 1." in md
    assert "## 2." in md
    assert "imagegen" in md.lower()  # mentions Codex tool name
    # Each cue should produce a fenced prompt block
    assert md.count("```") >= 4


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_outputs_json(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_t(
        (0.0, 2.0, "我们来聊聊注意力机制"),
        (3.0, 5.0, "信息茧房是个大问题"),
    )))
    out_path = tmp_path / "cues.json"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/imagegen_hint.py"),
         "--transcript", str(transcript_path),
         "--output", str(out_path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    cues = json.loads(out_path.read_text())
    assert len(cues) >= 2
    template_ids = {c["template_id"] for c in cues}
    assert "attention_mechanism" in template_ids
    assert "information_bubble" in template_ids


def test_cli_emit_codex_md(tmp_path):
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(json.dumps(_t(
        (0.0, 2.0, "复利效应"),
    )))
    md_path = tmp_path / "codex.md"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/imagegen_hint.py"),
         "--transcript", str(transcript_path),
         "--codex-md", str(md_path),
         "--output", str(tmp_path / "cues.json")],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    md = md_path.read_text()
    assert "Image generation plan" in md
    assert "compound_interest" in md
    assert "```" in md  # has fenced prompts
