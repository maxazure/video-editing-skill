#!/usr/bin/env python3
"""Generate platform-ready opening-hook variants for short-form videos.

The script is deterministic and local-first: it does not call an LLM, upload
media, or submit generation jobs. It turns a transcript / clean script into a
review packet of distinct hook angles so an editor can pick the first 3 seconds
before rewriting, rendering, or running paid generation.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from content_guard import ViolationLevel, scan_text  # noqa: E402


VERSION = "hook_variants.v1"

PLATFORM_LIMITS = {
    "xhs": 18,
    "douyin": 18,
    "wxch": 20,
    "tiktok": 48,
    "reels": 48,
    "youtube_shorts": 50,
}

STOPWORDS = {
    "这个", "一个", "就是", "其实", "然后", "因为", "所以", "但是", "我们", "你们", "他们",
    "今天", "大家", "如果", "没有", "不是", "可以", "可能", "已经", "需要", "时候", "真的",
    "the", "and", "that", "this", "with", "from", "your", "you", "are", "for", "but", "not",
}

PAIN_HINTS = ("卡", "焦虑", "失败", "浪费", "不会", "不懂", "太慢", "混乱", "踩坑", "担心", "risk", "stuck", "slow")
CONTRAST_HINTS = ("但是", "不过", "其实", "反而", "以前", "现在", "从", "到", "but", "however", "actually")
PROOF_HINTS = ("数据", "截图", "案例", "结果", "证据", "来源", "实验", "对比", "增长", "下降", "proof", "case")


@dataclasses.dataclass(frozen=True)
class HookFamily:
    family: str
    angle: str
    emotional_trigger: str
    pacing: str
    base_score: float
    zh_template: str
    en_template: str
    visual_cue: str
    reason: str


HOOK_FAMILIES: Sequence[HookFamily] = (
    HookFamily(
        "pattern_interrupt",
        "反常识打断",
        "surprise",
        "hard cut in first 0.3s",
        0.88,
        "别再{topic}了",
        "Stop doing {topic}",
        "hard-cut to speaker, first subtitle appears immediately",
        "interrupts a familiar behavior before the viewer scrolls",
    ),
    HookFamily(
        "pain_question",
        "痛点问句",
        "recognition",
        "question pause before answer",
        0.86,
        "你也卡在{pain}吗？",
        "Still stuck on {pain}?",
        "tight talking-head crop with question badge",
        "turns the viewer's current problem into the opening line",
    ),
    HookFamily(
        "number_map",
        "数字清单",
        "clarity",
        "fast list beat",
        0.84,
        "{number}步讲清{topic}",
        "{number} steps to fix {topic}",
        "number badge plus quick jump cut",
        "promises a concrete structure and easy retention",
    ),
    HookFamily(
        "contrast_turn",
        "前后反差",
        "curiosity",
        "before/after split",
        0.83,
        "{before}到{after}差这步",
        "From {before} to {after}",
        "split-screen before/after frame",
        "uses contrast to make the payoff visible early",
    ),
    HookFamily(
        "proof_first",
        "证据先行",
        "trust",
        "show receipt before thesis",
        0.82,
        "先看{proof}再下结论",
        "See the {proof} first",
        "flash source receipt, screenshot, or result card",
        "opens with evidence for reference-heavy videos",
    ),
    HookFamily(
        "time_box",
        "时间承诺",
        "utility",
        "countdown rhythm",
        0.80,
        "{minutes}分钟搞懂{topic}",
        "Understand {topic} in {minutes} min",
        "countdown or progress-bar opening",
        "sets a bounded time cost for the viewer",
    ),
    HookFamily(
        "mistake_fix",
        "避坑纠错",
        "loss aversion",
        "warning then fix",
        0.79,
        "{topic}先避开这坑",
        "Avoid this {topic} mistake",
        "warning badge, then quick solution cut",
        "frames the clip as a mistake the viewer can avoid",
    ),
    HookFamily(
        "identity_lens",
        "身份视角",
        "authority",
        "calm direct address",
        0.77,
        "做{persona}后才懂{topic}",
        "What {persona} taught me about {topic}",
        "speaker name/title lower-third",
        "adds a credible point of view when the creator persona matters",
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text(path: Optional[str]) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_json(path: Optional[str]) -> Mapping[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _segment_text(segment: Mapping[str, Any]) -> str:
    return str(segment.get("text") or segment.get("sentence") or "").strip()


def transcript_text(transcript: Mapping[str, Any]) -> str:
    lines: List[str] = []
    for segment in transcript.get("segments") or []:
        if isinstance(segment, Mapping):
            text = _segment_text(segment)
            if text:
                lines.append(text)
    if not lines:
        text = str(transcript.get("text") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def transcript_duration(transcript: Mapping[str, Any]) -> Optional[float]:
    ends = []
    for segment in transcript.get("segments") or []:
        if isinstance(segment, Mapping):
            try:
                ends.append(float(segment.get("end")))
            except (TypeError, ValueError):
                continue
    return max(ends) if ends else None


def _clean_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[_*`>#-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_sentence(text: str) -> str:
    for part in re.split(r"[。！？!?；;\n]", text):
        part = part.strip()
        if part:
            return part
    return text.strip()


def _visible_len(text: str) -> int:
    return len(text.strip())


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _text_terms(text: str) -> List[str]:
    cjk_terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9%％]{1,12}", text)
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    terms: List[str] = []
    for term in cjk_terms + ascii_terms:
        normalized = term.strip("，。！？!?、：:；;（）()[]【】\"'")
        if not normalized or normalized.lower() in STOPWORDS:
            continue
        if normalized in STOPWORDS:
            continue
        if len(normalized) < 2:
            continue
        terms.append(normalized)
    return terms


def _top_terms(text: str, *, limit: int = 8) -> List[str]:
    counter = Counter(_text_terms(text))
    return [term for term, _ in counter.most_common(limit)]


def _numbers(text: str) -> List[str]:
    found = re.findall(r"\d+(?:\.\d+)?\s?(?:%|％|倍|个|步|分钟|小时|天|次|万|千|k|m)?", text, flags=re.I)
    return [re.sub(r"\s+(?=[%％倍个步分钟小时天次万千])", "", item.strip()) for item in found if item.strip()]


def _compact(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", "", text.strip()) if _has_cjk(text) else re.sub(r"\s+", " ", text.strip())
    if _visible_len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _fallback_topic(text: str, terms: Sequence[str]) -> str:
    if terms:
        return terms[0]
    first = _first_sentence(text)
    return first[:10] if first else "这条视频"


def _find_hint(text: str, hints: Iterable[str], fallback: str) -> str:
    for sentence in re.split(r"[。！？!?；;\n]", text):
        stripped = sentence.strip()
        if not stripped:
            continue
        lowered_sentence = stripped.lower()
        if any(hint.lower() in lowered_sentence for hint in hints):
            return stripped
    return fallback


def _contrast_pair(text: str, topic: str) -> tuple[str, str]:
    match = re.search(r"从(.{1,8})到(.{1,8})", text)
    if match:
        return _compact(match.group(1), 5), _compact(match.group(2), 5)
    if "以前" in text and "现在" in text:
        return "以前", "现在"
    if re.search(r"\b(before|after)\b", text, re.I):
        return "before", "after"
    return topic, "结果"


def _estimate_minutes(duration: Optional[float], text: str) -> str:
    if duration and duration > 0:
        return str(max(1, round(duration / 60)))
    length = max(len(text), 1)
    return str(max(1, min(3, round(length / 260))))


def build_context(
    *,
    transcript: Mapping[str, Any],
    clean_script: str,
    topic: Optional[str],
    persona: Optional[str],
    title: Optional[str],
) -> dict[str, Any]:
    raw_text = "\n".join(part for part in (title or "", topic or "", transcript_text(transcript), clean_script) if part)
    normalized = _clean_markdown(raw_text)
    terms = _top_terms(normalized)
    base_topic = topic or _fallback_topic(normalized, terms)
    compact_topic = _compact(base_topic, 6 if _has_cjk(base_topic) else 22)
    pain = _compact(_find_hint(normalized, PAIN_HINTS, compact_topic), 7 if _has_cjk(normalized) else 24)
    proof = _compact(_find_hint(normalized, PROOF_HINTS, terms[1] if len(terms) > 1 else compact_topic), 6 if _has_cjk(normalized) else 18)
    before, after = _contrast_pair(normalized, compact_topic)
    number = _numbers(normalized)

    return {
        "text": normalized,
        "title": title or "",
        "topic": compact_topic,
        "raw_topic": base_topic,
        "pain": pain,
        "proof": proof,
        "before": _compact(before, 5 if _has_cjk(normalized) else 14),
        "after": _compact(after, 5 if _has_cjk(normalized) else 14),
        "number": number[0] if number else "3",
        "minutes": _estimate_minutes(transcript_duration(transcript), normalized),
        "persona": _compact(persona or "创作者", 5 if _has_cjk(normalized) else 18),
        "terms": terms,
        "language": "zh" if _has_cjk(normalized) else "en",
        "duration_seconds": transcript_duration(transcript),
        "opening_line": _first_sentence(normalized),
    }


def _format_hook(family: HookFamily, context: Mapping[str, Any], *, language: str, max_chars: int) -> str:
    template = family.zh_template if language == "zh" else family.en_template
    values = {
        "topic": context["topic"],
        "pain": context["pain"],
        "proof": context["proof"],
        "before": context["before"],
        "after": context["after"],
        "number": context["number"],
        "minutes": context["minutes"],
        "persona": context["persona"],
    }
    hook = template.format(**values)
    if _visible_len(hook) <= max_chars:
        return hook

    # Shrink the most variable slots and retry. The status still records length
    # warnings if a platform with a very small limit cannot be satisfied.
    tighter = dict(values)
    for key in ("topic", "pain", "proof", "before", "after", "persona"):
        tighter[key] = _compact(str(tighter[key]), 4 if language == "zh" else 12)
    hook = template.format(**tighter)
    return hook if _visible_len(hook) <= max_chars else _compact(hook, max_chars)


def _family_score(family: HookFamily, context: Mapping[str, Any]) -> tuple[float, List[str]]:
    text = str(context["text"]).lower()
    score = family.base_score
    reasons = [family.reason]

    if family.family == "number_map" and _numbers(str(context["text"])):
        score += 0.06
        reasons.append("source already contains a number or measurable result")
    if family.family == "pain_question" and any(hint.lower() in text for hint in PAIN_HINTS):
        score += 0.05
        reasons.append("source contains explicit pain/problem language")
    if family.family == "contrast_turn" and any(hint.lower() in text for hint in CONTRAST_HINTS):
        score += 0.05
        reasons.append("source contains a contrast or turn")
    if family.family == "proof_first" and any(hint.lower() in text for hint in PROOF_HINTS):
        score += 0.05
        reasons.append("source contains proof/result language")
    if family.family == "identity_lens" and str(context.get("persona")) not in {"创作者", "creator"}:
        score += 0.04
        reasons.append("persona was provided")

    return min(score, 0.98), reasons


def _violation_dicts(hook: str) -> List[dict[str, str]]:
    out = []
    for violation in scan_text(hook, context="title"):
        out.append(
            {
                "level": violation.level.value,
                "category": violation.category,
                "match": violation.match,
                "context": violation.context,
            }
        )
    return out


def _variant_status(hook: str, max_chars: int, violations: Sequence[Mapping[str, str]]) -> tuple[str, List[str]]:
    risks = []
    if _visible_len(hook) > max_chars:
        risks.append(f"over platform limit ({_visible_len(hook)}>{max_chars})")
    hard = [item for item in violations if item.get("level") == ViolationLevel.HARD.value]
    soft = [item for item in violations if item.get("level") == ViolationLevel.SOFT.value]
    if hard:
        risks.extend(f"hard:{item.get('category')}:{item.get('match')}" for item in hard)
    if soft:
        risks.extend(f"soft:{item.get('category')}:{item.get('match')}" for item in soft)
    if hard or _visible_len(hook) > max_chars:
        return "blocked", risks
    if soft:
        return "review", risks
    return "ready", risks


def generate_variants(
    context: Mapping[str, Any],
    *,
    platform: str = "xhs",
    count: int = 8,
    max_chars: Optional[int] = None,
    language: Optional[str] = None,
) -> List[dict[str, Any]]:
    limit = max_chars or PLATFORM_LIMITS.get(platform, 18)
    lang = language or str(context.get("language") or "zh")
    variants: List[dict[str, Any]] = []

    for index, family in enumerate(HOOK_FAMILIES[: max(0, count)], 1):
        hook = _format_hook(family, context, language=lang, max_chars=limit)
        score, reasons = _family_score(family, context)
        violations = _violation_dicts(hook)
        status, risks = _variant_status(hook, limit, violations)
        if status == "blocked":
            score -= 0.30
        elif risks:
            score -= 0.08
        variants.append(
            {
                "id": f"hook_{index:02d}",
                "family": family.family,
                "hook": hook,
                "char_count": _visible_len(hook),
                "max_chars": limit,
                "platform": platform,
                "angle": family.angle,
                "emotional_trigger": family.emotional_trigger,
                "pacing": family.pacing,
                "visual_cue": family.visual_cue,
                "subtitle_first_line": hook,
                "score": round(max(0.0, min(score, 1.0)), 3),
                "status": status,
                "risks": risks,
                "violations": violations,
                "reasons": reasons,
                "source_terms": list(context.get("terms") or [])[:5],
            }
        )

    return sorted(variants, key=lambda item: (-float(item["score"]), item["id"]))


def build_plan(
    *,
    transcript: Mapping[str, Any],
    clean_script: str = "",
    topic: Optional[str] = None,
    persona: Optional[str] = None,
    title: Optional[str] = None,
    platform: str = "xhs",
    count: int = 8,
    max_chars: Optional[int] = None,
    language: Optional[str] = None,
) -> dict[str, Any]:
    context = build_context(
        transcript=transcript,
        clean_script=clean_script,
        topic=topic,
        persona=persona,
        title=title,
    )
    if not context["text"]:
        raise ValueError("provide --transcript, --clean-script, --title, or --topic")

    variants = generate_variants(
        context,
        platform=platform,
        count=count,
        max_chars=max_chars,
        language=language,
    )
    usable = [item for item in variants if item["status"] != "blocked"]
    recommended = usable[0]["id"] if usable else None
    blocking = 0 if usable else 1

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "platform": platform,
        "max_chars": max_chars or PLATFORM_LIMITS.get(platform, 18),
        "input_summary": {
            "topic": context["raw_topic"],
            "compact_topic": context["topic"],
            "persona": context["persona"],
            "language": language or context["language"],
            "duration_seconds": context["duration_seconds"],
            "opening_line": context["opening_line"],
            "source_terms": context["terms"],
        },
        "summary": {
            "variants": len(variants),
            "usable": len(usable),
            "blocked_variants": len([item for item in variants if item["status"] == "blocked"]),
            "blocking": blocking,
            "recommended": recommended,
            "next_action": (
                "Pick one hook id, then feed it into rewrite_script.py / clean_script.md before render."
                if usable
                else "Rewrite the topic/hook inputs; every generated hook is blocked by length or content guard."
            ),
        },
        "variants": variants,
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") if isinstance(plan.get("summary"), Mapping) else {}
    input_summary = plan.get("input_summary") if isinstance(plan.get("input_summary"), Mapping) else {}
    lines = [
        "# Hook Variants",
        "",
        f"- Platform: `{plan.get('platform')}`",
        f"- Topic: {input_summary.get('compact_topic') or input_summary.get('topic') or ''}",
        f"- Recommended: `{summary.get('recommended') or 'none'}`",
        f"- Usable variants: {summary.get('usable', 0)} / {summary.get('variants', 0)}",
        "",
        "| ID | Status | Score | Hook | Angle | Risk |",
        "|---|---:|---:|---|---|---|",
    ]
    for variant in plan.get("variants") or []:
        risks = "; ".join(variant.get("risks") or []) or "-"
        lines.append(
            "| {id} | {status} | {score:.3f} | {hook} | {angle} | {risks} |".format(
                id=variant.get("id"),
                status=variant.get("status"),
                score=float(variant.get("score") or 0),
                hook=str(variant.get("hook") or "").replace("|", "\\|"),
                angle=str(variant.get("angle") or "").replace("|", "\\|"),
                risks=risks.replace("|", "\\|"),
            )
        )

    lines.extend(["", "## Review Notes", ""])
    for variant in plan.get("variants") or []:
        lines.append(f"### {variant.get('id')} — {variant.get('family')}")
        lines.append(f"- Hook: {variant.get('hook')}")
        lines.append(f"- Visual cue: {variant.get('visual_cue')}")
        lines.append(f"- Pacing: {variant.get('pacing')}")
        reasons = "; ".join(variant.get("reasons") or [])
        if reasons:
            lines.append(f"- Why it may work: {reasons}")
        if variant.get("risks"):
            lines.append(f"- Review risk: {'; '.join(variant.get('risks') or [])}")
        lines.append("")

    lines.append("Next: choose one hook id, update the clean script hook, then render/QA normally.")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate opening-hook variants for short-form video review.")
    parser.add_argument("--transcript", help="Whisper transcript JSON with segments.")
    parser.add_argument("--clean-script", help="Reviewed clean_script.md or text file.")
    parser.add_argument("--title", help="Existing title or candidate hook context.")
    parser.add_argument("--topic", help="Explicit topic override.")
    parser.add_argument("--persona", help="Creator identity, e.g. AI创业者 / product designer.")
    parser.add_argument("--platform", default="xhs", choices=sorted(PLATFORM_LIMITS), help="Target platform preset.")
    parser.add_argument("--count", type=int, default=8, help="Number of variants to emit, max 8.")
    parser.add_argument("--max-chars", type=int, help="Override platform hook length limit.")
    parser.add_argument("--language", choices=("zh", "en"), help="Force hook language; default auto-detect.")
    parser.add_argument("--output", required=True, help="Path to hook_variants.json.")
    parser.add_argument("--markdown", help="Optional Markdown review packet path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when no usable hook remains.")
    args = parser.parse_args(argv)

    transcript = _load_json(args.transcript)
    clean_script = _read_text(args.clean_script)

    plan = build_plan(
        transcript=transcript,
        clean_script=clean_script,
        topic=args.topic,
        persona=args.persona,
        title=args.title,
        platform=args.platform,
        count=min(max(args.count, 1), len(HOOK_FAMILIES)),
        max_chars=args.max_chars,
        language=args.language,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.markdown:
        markdown = Path(args.markdown)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(emit_markdown(plan), encoding="utf-8")

    if args.strict and int(plan["summary"]["blocking"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
