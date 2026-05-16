#!/usr/bin/env python3
"""Suggest where AI image generation would help a short-form video, and
produce gpt-image-2-shaped prompts for each opportunity.

This script does NOT call any image API itself. It is a planner:
  1. Scan transcript + clean script for abstract concepts and visual metaphors.
  2. For each opportunity, build a complete gpt-image-2 prompt using the
     templates in scripts/prompts/imagegen_templates.yaml.
  3. Emit a JSON of image cues the next step can act on.

When the next step is **Codex** (the OpenAI CLI), the agent should call its
built-in `imagegen` tool with each prompt — no API key needed; Codex routes
to gpt-image-2 internally. When running outside Codex, fall back to:

    python3 scripts/image_gen.py generate \
      --prompt "<prompt>" --size 1024x1536 --quality high
      # requires OPENAI_API_KEY; uses gpt-image-1.5

References:
  - OpenAI Cookbook prompting guide
  - gpt-image-2 model docs (developers.openai.com)
  - fal.ai gpt-image-2 prompting reference
  - Codex imagegen SKILL.md
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from typing import Iterable, List, Optional


# Curated abstract-concept dictionary. Each entry maps detected keywords
# → suggested sample id from imagegen_templates.yaml.
ABSTRACT_CONCEPTS = {
    "注意力机制":       "attention_mechanism",
    "注意力":          "attention_mechanism",
    "信息茧房":         "information_bubble",
    "信息孤岛":         "information_bubble",
    "回音壁":           "information_bubble",
    "复利":             "compound_interest",
    "复利效应":         "compound_interest",
    "雪球效应":         "compound_interest",
    "长尾":             "long_tail_effect",
    "长尾效应":         "long_tail_effect",
}

# Visual-metaphor cues — segments containing these phrases benefit from an
# illustration even when no specific concept is named.
METAPHOR_CUES = [
    "比如", "比方说", "想象一下", "类比", "打个比方",
    "其实就像", "就好像", "好比是",
    "imagine", "for example", "like a ", "it's as if",
]


@dataclasses.dataclass(frozen=True)
class ImageCue:
    """One suggested image-generation opportunity."""
    concept: str               # human-readable concept name
    template_id: Optional[str] # sample id from imagegen_templates.yaml (None if free-form)
    use_case: str              # one of the structure ids
    target_aspect: str         # "9:16" / "3:4" / "1:1"
    prompt_en: str             # full gpt-image-2 prompt (with system suffix)
    prompt_cn: str             # Chinese version (optional)
    timing_seconds: float      # where in the source this opportunity lives
    reason: str                # why we flagged it (for debug)


# ─── YAML reader (tiny, no PyYAML hard dep) ─────────────────────────────────


def _load_templates():
    """Read imagegen_templates.yaml. Prefers PyYAML, falls back to our parser."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "prompts", "imagegen_templates.yaml")
    text = open(path, encoding="utf-8").read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass
    # Fallback parser for this specific file shape: top-level keys
    # `system_suffix`, `structures`, `samples`. Samples are a list of dicts.
    return _parse_imagegen_yaml(text)


def _parse_imagegen_yaml(text: str) -> dict:
    """Minimal parser tuned to imagegen_templates.yaml."""
    lines = text.splitlines()
    out = {"system_suffix": "", "structures": {}, "samples": []}
    i = 0

    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue

        if raw.startswith("system_suffix:"):
            # multiline block scalar (`|-` then indented lines)
            i += 1
            chunks = []
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                if lines[i].strip():
                    chunks.append(lines[i].strip())
                i += 1
            out["system_suffix"] = " ".join(chunks)
            continue

        if raw.startswith("structures:"):
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                line = lines[i]
                if line.startswith("  ") and not line.startswith("    ") and ":" in line:
                    key = line.strip().rstrip(":")
                    out["structures"][key] = {}
                    i += 1
                    while i < len(lines) and lines[i].startswith("    "):
                        sub = lines[i].strip()
                        if ":" in sub:
                            k, _, v = sub.partition(":")
                            v = v.strip()
                            if v.startswith("[") and v.endswith("]"):
                                v = [x.strip().strip('"') for x in v[1:-1].split(",")]
                            else:
                                v = v.strip('"')
                            out["structures"][key][k.strip()] = v
                        i += 1
                else:
                    i += 1
            continue

        if raw.startswith("samples:"):
            i += 1
            current = None
            while i < len(lines):
                line = lines[i]
                if line.startswith("  - id:"):
                    if current:
                        out["samples"].append(current)
                    current = {"id": line.split(":", 1)[1].strip()}
                    i += 1
                elif current is not None and line.startswith("    ") and ":" in line:
                    rest = line[4:]
                    k, _, v = rest.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if v in ("|-", "|"):
                        # Block scalar: gather indented lines until indent drops
                        i += 1
                        chunks = []
                        while i < len(lines) and lines[i].startswith("      "):
                            chunks.append(lines[i][6:].rstrip())
                            i += 1
                        current[k] = "\n".join(chunks).strip()
                        continue
                    else:
                        current[k] = v.strip('"')
                    i += 1
                elif line.strip() and not line.startswith("  "):
                    break
                else:
                    i += 1
            if current:
                out["samples"].append(current)
            continue

        i += 1

    return out


# ─── Detection ──────────────────────────────────────────────────────────────


def detect_opportunities(transcript: dict,
                         clean_script_text: Optional[str] = None
                         ) -> List[ImageCue]:
    """Return ImageCue list ranked by timing."""
    templates = _load_templates()
    samples_by_id = {s["id"]: s for s in templates.get("samples", [])}
    system_suffix = templates.get("system_suffix", "")

    cues: List[ImageCue] = []

    for seg in transcript.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))

        # 1) Abstract concept keyword hit
        for keyword, sample_id in ABSTRACT_CONCEPTS.items():
            if keyword in text and sample_id in samples_by_id:
                sample = samples_by_id[sample_id]
                cues.append(ImageCue(
                    concept=keyword,
                    template_id=sample_id,
                    use_case=sample.get("use_case", "abstract_concept"),
                    target_aspect=sample.get("target_aspect", "9:16"),
                    prompt_en=_compose_prompt(sample.get("prompt_en", ""), system_suffix),
                    prompt_cn=sample.get("prompt_cn", ""),
                    timing_seconds=start,
                    reason=f"abstract-concept:{keyword}",
                ))
                break  # one cue per segment max

        else:
            # 2) Metaphor cue — visual analogy needed but no canned sample
            for cue_phrase in METAPHOR_CUES:
                if cue_phrase in text:
                    cues.append(ImageCue(
                        concept=text[:40],
                        template_id=None,
                        use_case="abstract_concept",
                        target_aspect="9:16",
                        prompt_en=_compose_free_form_prompt(text, system_suffix),
                        prompt_cn="",
                        timing_seconds=start,
                        reason=f"metaphor-cue:{cue_phrase}",
                    ))
                    break

    # 3) Chapter cards from clean script that look concept-shaped
    if clean_script_text:
        for line in clean_script_text.splitlines():
            line = line.strip()
            if not (line.startswith("## ") and not line.startswith("### ")):
                continue
            title = line[3:].strip()
            for keyword, sample_id in ABSTRACT_CONCEPTS.items():
                if keyword in title and sample_id in samples_by_id:
                    sample = samples_by_id[sample_id]
                    cues.append(ImageCue(
                        concept=f"chapter:{title}",
                        template_id=sample_id,
                        use_case="chapter_background",
                        target_aspect="9:16",
                        prompt_en=_compose_prompt(sample.get("prompt_en", ""), system_suffix),
                        prompt_cn=sample.get("prompt_cn", ""),
                        timing_seconds=0.0,
                        reason=f"chapter-title:{keyword}",
                    ))
                    break

    # Dedupe by template_id + timing (within 1s window)
    seen = set()
    unique: List[ImageCue] = []
    for cue in sorted(cues, key=lambda c: c.timing_seconds):
        key = (cue.template_id or cue.concept, round(cue.timing_seconds))
        if key in seen:
            continue
        seen.add(key)
        unique.append(cue)
    return unique


def _compose_prompt(base_prompt: str, system_suffix: str) -> str:
    """Append the universal constraint suffix."""
    if not base_prompt:
        return ""
    base = base_prompt.strip()
    if system_suffix and system_suffix not in base:
        return base + "\n\n" + system_suffix.strip()
    return base


def _compose_free_form_prompt(seg_text: str, system_suffix: str) -> str:
    """For metaphor-cue segments with no curated sample. Build a skeleton
    the user/agent can refine."""
    base = (
        f"An editorial illustration that visualises this idea metaphorically: "
        f"\"{seg_text[:80]}\". "
        f"Use a concrete physical metaphor (objects, light, geometry) rather "
        f"than literal humans. Flat-vector or isometric style, restrained "
        f"navy-and-gold palette, vertical 9:16 composition with the top "
        f"quarter empty for caption overlay."
    )
    if system_suffix:
        return base + "\n\n" + system_suffix.strip()
    return base


# ─── Output ─────────────────────────────────────────────────────────────────


def emit_codex_markdown(cues: List[ImageCue]) -> str:
    """Render cues as a markdown briefing the Codex agent can act on directly."""
    if not cues:
        return "_No image-generation opportunities detected in the source._\n"
    parts = [
        "# Image generation plan",
        "",
        "Run each prompt below through the Codex built-in `imagegen` tool ",
        "(no API key needed; routes to gpt-image-2). Save outputs to ",
        "`work/imagegen/` with the cue's `concept` slug as the filename.",
        "",
    ]
    for i, cue in enumerate(cues, 1):
        parts.extend([
            f"## {i}. {cue.concept}  ({cue.use_case} · {cue.target_aspect})",
            "",
            f"- **trigger**: {cue.reason}",
            f"- **timing**: t≈{cue.timing_seconds:.1f}s",
            f"- **template**: `{cue.template_id or 'free-form'}`",
            "",
            "**Prompt (EN)**:",
            "",
            "```",
            cue.prompt_en,
            "```",
            "",
        ])
        if cue.prompt_cn:
            parts.extend([
                "<details><summary>中文 prompt（参考，不要替换 EN）</summary>",
                "",
                cue.prompt_cn,
                "",
                "</details>",
                "",
            ])
    return "\n".join(parts)


# ─── CLI ────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description="Suggest image-generation opportunities for a short-form video")
    p.add_argument("--transcript", required=True, help="Whisper transcript JSON")
    p.add_argument("--clean-script", default=None,
                   help="Optional clean_script.md to also scan chapter titles")
    p.add_argument("--output", default=None,
                   help="JSON output path (default stdout)")
    p.add_argument("--emit-codex", action="store_true",
                   help="Also emit a markdown briefing for the Codex agent")
    p.add_argument("--codex-md", default=None,
                   help="Write the Codex markdown briefing to this path")
    args = p.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)

    clean_text = None
    if args.clean_script and os.path.isfile(args.clean_script):
        clean_text = open(args.clean_script, encoding="utf-8").read()

    cues = detect_opportunities(transcript, clean_text)

    payload = [dataclasses.asdict(c) for c in cues]
    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ {len(cues)} cues → {args.output}")
    else:
        print(out_text)

    if args.emit_codex or args.codex_md:
        md = emit_codex_markdown(cues)
        if args.codex_md:
            with open(args.codex_md, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"✅ Codex briefing → {args.codex_md}", file=sys.stderr)
        else:
            print(md, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
