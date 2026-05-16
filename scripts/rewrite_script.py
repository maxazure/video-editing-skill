#!/usr/bin/env python3
"""Story Engine — turn a raw Whisper transcript into a Xiaohongshu-ready 5-field script.

This is NOT itself an LLM client. It produces:
  1) a structured prompt the user/agent feeds to their LLM
  2) a JSON-schema validator the LLM output must satisfy
  3) a writer that materialises the 5 fields into a clean_script.md

The 5 fields are: hook / pain / turn / value[] / cta.

Usage:
    # Step 1 — emit the prompt
    python3 scripts/rewrite_script.py \\
        --transcript day58/work/transcript.json \\
        --structure pain_solve \\
        --hook-template auto \\
        --max-duration 150 \\
        --emit-prompt > /tmp/prompt.md
    # (paste /tmp/prompt.md into Claude/ChatGPT, get JSON back)

    # Step 2 — validate + materialise
    python3 scripts/rewrite_script.py \\
        --transcript day58/work/transcript.json \\
        --llm-output /tmp/llm.json \\
        --output day58/work/clean_script.md
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from content_guard import enforce as enforce_platform_rules, HardBlock  # noqa: E402


STRUCTURES = {
    "pain_solve": {
        "label": "痛点解决型（教程/干货）",
        "order": ["hook", "pain", "turn", "value", "cta"],
        "rules": [
            "Hook < 15 字，包含核心反差/数字/痛点",
            "Pain 描述真实场景，引发共鸣",
            "Turn 给出转折发现，铺垫下面 N 个 value",
            "Value 必须是 3 个并列短句，每条 < 25 字",
            "CTA 至少含一个问句 + 一个收藏诱因",
        ],
    },
    "story_reversal": {
        "label": "故事反转型（人设/种草）",
        "order": ["hook", "pain", "turn", "value", "cta"],
        "rules": [
            "Hook 用强时间锚点（凌晨 X 点 / 那天）",
            "Pain 写旧状态/触发事件",
            "Turn 写心理变化",
            "Value 写新状态结论（1-2 条）",
            "CTA 走共鸣求证类（'只有我这样吗'）",
        ],
    },
    "listicle": {
        "label": "清单/盘点型",
        "order": ["hook", "pain", "turn", "value", "cta"],
        "rules": [
            "Hook 必须含 '我整理了 N 个'",
            "Pain 可省略；如有则写'之前不知道这些'",
            "Turn 一句过渡",
            "Value 是 N 条清单，每条 < 20 字",
            "CTA 走收藏诱因 + 续集悬念",
        ],
    },
}


def load_hook_templates():
    path = os.path.join(os.path.dirname(__file__), "prompts", "hook_templates.yaml")
    text = open(path, encoding="utf-8").read()
    # Tiny custom parser tuned to our prompts file (list of maps, "- key: value")
    items = []
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- id:"):
            if current:
                items.append(current)
            current = {"id": line.split(":", 1)[1].strip()}
        elif current is not None and ":" in line:
            k, _, v = line.strip().partition(":")
            current[k.strip()] = v.strip().strip('"')
    if current:
        items.append(current)
    return items


def load_cta_templates():
    path = os.path.join(os.path.dirname(__file__), "prompts", "cta_templates.yaml")
    text = open(path, encoding="utf-8").read()
    items = []
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- id:"):
            if current:
                items.append(current)
            current = {"id": line.split(":", 1)[1].strip()}
        elif current is not None and ":" in line:
            k, _, v = line.strip().partition(":")
            current[k.strip()] = v.strip().strip('"')
    if current:
        items.append(current)
    return items


def emit_prompt(transcript: dict, structure: str, hook_template: str,
                max_duration_seconds: int, persona: str | None) -> str:
    if structure not in STRUCTURES:
        raise ValueError(f"unknown structure {structure!r}; pick from {list(STRUCTURES)}")
    s = STRUCTURES[structure]
    hooks = load_hook_templates()
    ctas = load_cta_templates()

    # Compact transcript: just the segment texts and timings
    transcript_lines = []
    for seg in transcript.get("segments", []):
        start = seg.get("start", 0.0)
        text = seg.get("text", "").strip()
        if text:
            transcript_lines.append(f"[{start:>7.2f}s] {text}")
    transcript_block = "\n".join(transcript_lines)

    hook_options = "\n".join(
        f"- {h['id']} — {h.get('label', '')}: `{h.get('pattern_zh', '')}`"
        for h in hooks
    )
    cta_options = "\n".join(
        f"- {c['id']} — {c.get('label', '')}: `{c.get('pattern_zh', '')}`"
        for c in ctas
    )

    prompt = f"""你是一位小红书短视频的内容编辑。把下面这份口播转写稿重写成一个结构化的发布稿。

**结构**: {s['label']} ({structure})
**规则**:
{chr(10).join('- ' + r for r in s['rules'])}
**目标时长**: ≤ {max_duration_seconds} 秒
{'**人设**: ' + persona if persona else ''}

**钩子模板候选**（{hook_template} = 让你选最合适的一个，并说明为什么）:
{hook_options}

**CTA 模板候选**（必须挑 1-2 个，含至少一个问句）:
{cta_options}

**原始口播稿** (timestamps in seconds):
{transcript_block}

**输出格式**（严格 JSON，无其他文字）:
```json
{{
  "hook": "≤ 15 字的钩子文本",
  "hook_template_id": "从候选里挑的 id",
  "pain": "30-50 字的痛点共鸣段落",
  "turn": "20-40 字的转折发现",
  "value": ["20-25 字干货 1", "20-25 字干货 2", "20-25 字干货 3"],
  "cta": "20-40 字的 CTA，包含问句",
  "cta_template_ids": ["挑的 cta id 列表"],
  "estimated_speech_seconds": 整数估计的口播秒数,
  "discarded_segments": ["你删除的原稿片段，用于回滚"]
}}
```

不要输出任何其他文字。不要写解释。只输出 JSON。
"""
    return prompt


def validate_llm_output(data: dict, max_duration_seconds: int) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors = []
    required = ["hook", "pain", "turn", "value", "cta"]
    for field in required:
        if field not in data:
            errors.append(f"missing required field: {field}")
            continue
    if "hook" in data:
        if not isinstance(data["hook"], str):
            errors.append("hook must be str")
        elif len(data["hook"]) > 18:
            errors.append(f"hook too long ({len(data['hook'])} > 18 chars)")
    if "value" in data:
        if not isinstance(data["value"], list):
            errors.append("value must be a list")
        elif len(data["value"]) < 1 or len(data["value"]) > 5:
            errors.append(f"value should have 1-5 bullets, got {len(data['value'])}")
    if "estimated_speech_seconds" in data:
        if data["estimated_speech_seconds"] > max_duration_seconds:
            errors.append(
                f"estimated_speech_seconds {data['estimated_speech_seconds']} "
                f"exceeds max_duration {max_duration_seconds}"
            )
    return errors


def materialise(data: dict) -> str:
    """Turn validated LLM output into a clean_script.md."""
    lines = [
        "# Clean Script",
        "",
        f"_(generated by rewrite_script.py — hook={data.get('hook_template_id')}, "
        f"cta={data.get('cta_template_ids')})_",
        "",
        "## Hook",
        data["hook"],
        "",
        "## Pain",
        data["pain"],
        "",
        "## Turn",
        data["turn"],
        "",
        "## Value",
    ]
    for i, v in enumerate(data["value"], 1):
        lines.append(f"{i}. {v}")
    lines.extend(["", "## CTA", data["cta"], ""])
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Story Engine for Xiaohongshu shorts")
    p.add_argument("--transcript", required=True, help="Whisper transcript JSON")
    p.add_argument("--structure", default="pain_solve",
                   choices=list(STRUCTURES), help="Story structure to apply")
    p.add_argument("--hook-template", default="auto",
                   help="'auto' = let LLM choose; or a specific id like 'pain_relate'")
    p.add_argument("--max-duration", type=int, default=150,
                   help="Max target spoken duration in seconds")
    p.add_argument("--persona", default=None, help="Speaker identity hint (used in hook)")
    p.add_argument("--emit-prompt", action="store_true",
                   help="Print the LLM prompt and exit")
    p.add_argument("--llm-output", default=None,
                   help="Path to JSON file with the LLM's response, to validate + materialise")
    p.add_argument("--output", default=None,
                   help="Where to write clean_script.md (required with --llm-output)")
    args = p.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)

    if args.emit_prompt and not args.llm_output:
        sys.stdout.write(emit_prompt(
            transcript, args.structure, args.hook_template,
            args.max_duration, args.persona,
        ))
        return 0

    if args.llm_output:
        with open(args.llm_output, encoding="utf-8") as f:
            data = json.load(f)
        errors = validate_llm_output(data, args.max_duration)
        if errors:
            for e in errors:
                print(f"❌ {e}", file=sys.stderr)
            return 1
        # Content-guard the rewritten fields
        try:
            enforce_platform_rules([data["hook"]], strict=True, context="title")
            enforce_platform_rules(
                [data["pain"], data["turn"], data["cta"]] + list(data["value"]),
                strict=True, context="script",
            )
        except HardBlock as exc:
            print(f"🚫 Rewritten script trips the content guard: {exc}", file=sys.stderr)
            return 2

        md = materialise(data)
        if args.output:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"✅ Wrote {args.output} ({len(md)} bytes)")
        else:
            sys.stdout.write(md)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
