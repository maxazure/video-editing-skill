#!/usr/bin/env python3
"""Build auditable storyboard shot cards from a transcript.

This planner does not call image or video generation APIs. It produces a
machine-readable plan that an agent can review before spending credits or
rendering generated assets.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROUTING_SENTENCE = (
    "生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。"
)

SECTION_LABELS = {
    "hook": "Hook",
    "pain": "Pain",
    "turn": "Turn",
    "value": "Value",
    "cta": "CTA",
}

ABSTRACT_KEYWORDS = {
    "注意力机制": "attention mechanism",
    "信息茧房": "information bubble",
    "复利": "compound interest",
    "长尾": "long-tail effect",
    "飞轮": "flywheel",
    "护城河": "competitive moat",
    "杠杆": "leverage",
    "闭环": "closed loop",
    "漏斗": "funnel",
    "增长": "growth loop",
}

DATA_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:%|倍|万|k|K|w|W|次|天|年|小时|分钟|秒)|一半|翻倍|增长|下降|成本|收入|转化)")

MOTION_KEYWORDS = {
    "打开", "走", "跑", "切换", "滑动", "点击", "演示", "进入", "离开",
    "镜头", "城市", "工厂", "办公室", "咖啡", "客户", "产品", "旅行", "路上",
}

EMOTION_KEYWORDS = {
    "焦虑", "兴奋", "惊讶", "后悔", "开心", "压力", "崩溃", "反转", "突然",
    "意识到", "担心", "害怕", "庆幸",
}

CTA_KEYWORDS = {"评论", "收藏", "关注", "转发", "点赞", "留言", "告诉我", "你怎么看"}

STOPWORDS = {
    "今天", "我们", "这个", "一个", "就是", "其实", "然后", "因为", "所以",
    "但是", "如果", "他们", "自己", "没有", "不是", "可以", "以及", "the",
    "and", "that", "with", "this", "from", "have",
}


@dataclasses.dataclass(frozen=True)
class Segment:
    idx: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _round2(value: float) -> float:
    return round(float(value), 2)


def load_transcript(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_segments(transcript: Dict[str, Any]) -> List[Segment]:
    segments: List[Segment] = []
    for pos, raw in enumerate(transcript.get("segments") or []):
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") if raw.get("end") is not None else start)
        if end < start:
            end = start
        idx = int(raw.get("id") or raw.get("segment_id") or pos + 1)
        segments.append(Segment(idx=idx, start=start, end=end, text=text))
    return segments


def parse_clean_script_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        m = re.match(r"^##+\s+(.+?)\s*$", line)
        if m:
            heading = m.group(1).strip().lower()
            current = _canonical_section(heading)
            sections.setdefault(current, [])
            continue
        if current:
            stripped = line.strip()
            if stripped:
                sections[current].append(stripped)
    return {key: "\n".join(lines) for key, lines in sections.items() if lines}


def _canonical_section(value: str) -> str:
    v = value.lower()
    if "hook" in v or "开头" in v or "钩子" in v:
        return "hook"
    if "pain" in v or "痛点" in v:
        return "pain"
    if "turn" in v or "反转" in v or "转折" in v:
        return "turn"
    if "cta" in v or "call" in v or "行动" in v or "结尾" in v:
        return "cta"
    return "value"


def group_segments(segments: Sequence[Segment], max_shots: int) -> List[List[Segment]]:
    if not segments:
        return []
    shot_count = min(max(1, max_shots), len(segments))
    groups: List[List[Segment]] = []
    for i in range(shot_count):
        start = math.floor(i * len(segments) / shot_count)
        end = math.floor((i + 1) * len(segments) / shot_count)
        group = list(segments[start:end])
        if group:
            groups.append(group)
    return groups


def section_for_group(index: int, total: int, text: str) -> str:
    lowered = text.lower()
    if any(k in text for k in CTA_KEYWORDS):
        return "cta"
    if index == 0:
        return "hook"
    if index == total - 1:
        return "cta"
    ratio = index / max(1, total - 1)
    if ratio <= 0.25:
        return "pain"
    if ratio <= 0.45:
        return "turn"
    if "but" in lowered or "however" in lowered or "但是" in text or "反而" in text:
        return "turn"
    return "value"


def choose_route(text: str, section: str) -> Tuple[str, str, str, bool]:
    if section == "cta":
        return (
            "remotion_hyperframes",
            "media_library_broll",
            "CTA or end-card content works best as deterministic motion graphics.",
            False,
        )
    if _find_abstract_keyword(text):
        return (
            "codex_imagegen",
            "media_library_broll",
            "Abstract concept detected; make a still visual first, then animate or overlay it.",
            False,
        )
    if DATA_RE.search(text):
        return (
            "remotion_hyperframes",
            "codex_imagegen",
            "Numbers or metrics detected; deterministic charts/cards are safer than free-form video.",
            False,
        )
    if any(k in text for k in MOTION_KEYWORDS):
        return (
            "dreamina_video",
            "media_library_broll",
            "Action or scene language detected; generated video can be useful after approval.",
            True,
        )
    if any(k in text for k in EMOTION_KEYWORDS):
        return (
            "codex_imagegen",
            "dreamina_video",
            "Emotion or story turn detected; start with a controllable visual metaphor.",
            False,
        )
    return (
        "media_library_broll",
        "codex_imagegen",
        "Default to existing footage search before generating new media.",
        False,
    )


def _find_abstract_keyword(text: str) -> Optional[str]:
    for keyword in ABSTRACT_KEYWORDS:
        if keyword in text:
            return keyword
    return None


def visual_language(section: str, route: str, index: int) -> Dict[str, str]:
    if section == "hook":
        return {
            "shot_size": "medium_close",
            "camera_movement": "slow dolly in",
            "composition": "vertical 9:16, subject or metaphor centered, strong empty space for subtitles",
        }
    if section == "cta":
        return {
            "shot_size": "title_card",
            "camera_movement": "subtle push-in",
            "composition": "vertical 9:16, high-contrast end card with safe margins",
        }
    if route == "remotion_hyperframes":
        return {
            "shot_size": "graphic_card",
            "camera_movement": "animated reveal",
            "composition": "vertical 9:16 card layout with one dominant number or phrase",
        }
    if route == "dreamina_video":
        return {
            "shot_size": "medium_wide",
            "camera_movement": "smooth tracking movement",
            "composition": "vertical 9:16, clear subject motion, no dense text inside generated footage",
        }
    size = "wide" if index % 3 == 0 else "medium"
    return {
        "shot_size": size,
        "camera_movement": "gentle pan" if index % 2 else "locked-off",
        "composition": "vertical 9:16, clean foreground, subtitle-safe lower third",
    }


def extract_keywords(text: str, limit: int = 5) -> List[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{1,}|[\u4e00-\u9fff]{2,}", text)
    scored: Dict[str, int] = {}
    for token in tokens:
        if token.lower() in STOPWORDS or token in STOPWORDS:
            continue
        scored[token] = scored.get(token, 0) + 1
    return [k for k, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def build_prompts(
    *,
    text: str,
    section: str,
    route: str,
    target_aspect: str,
    keywords: Sequence[str],
    visual: Dict[str, str],
) -> Dict[str, str]:
    keyword_text = ", ".join(keywords) if keywords else text[:36]
    abstract = _find_abstract_keyword(text)
    subject = ABSTRACT_KEYWORDS.get(abstract, keyword_text) if abstract else keyword_text
    base_style = (
        f"{target_aspect} short-form social video, {visual['composition']}, "
        "cinematic but clean, no watermark, no UI chrome, avoid readable Chinese text inside generated media"
    )

    prompts: Dict[str, str] = {
        "broll_query": keyword_text,
        "review_question": f"Does this shot clearly support the {SECTION_LABELS.get(section, section)} beat?",
    }
    if route == "codex_imagegen":
        prompts["image_prompt_en"] = (
            f"{subject}. {visual['shot_size']}, {visual['camera_movement']}. {base_style}. "
            "Leave negative space for burned subtitles."
        )
    elif route == "dreamina_video":
        prompts["video_prompt_en"] = (
            f"{subject}. First frame: stable composition with the key subject visible. "
            f"Motion: {visual['camera_movement']} with natural movement. Last frame: clean pause for edit. "
            f"{base_style}. Keep continuity with previous shot colors and subject scale."
        )
    elif route == "remotion_hyperframes":
        prompts["motion_graphics_brief"] = (
            f"Build a {target_aspect} deterministic motion-graphics card for: {keyword_text}. "
            "Use large readable typography, one focal stat/phrase, and subtitle-safe margins."
        )
    else:
        prompts["broll_query"] = keyword_text
        prompts["fallback_image_prompt_en"] = (
            f"Editorial B-roll fallback for {subject}. {base_style}."
        )
    return prompts


def build_storyboard_plan(
    transcript: Dict[str, Any],
    *,
    clean_script_text: Optional[str] = None,
    max_shots: int = 8,
    target_aspect: str = "9:16",
    platform: str = "xhs",
) -> Dict[str, Any]:
    segments = normalize_segments(transcript)
    groups = group_segments(segments, max_shots=max_shots)
    clean_sections = parse_clean_script_sections(clean_script_text or "")
    shots = []
    route_counts: Dict[str, int] = {}
    continuity_palette = ["warm white", "deep charcoal", "signal yellow"]

    for idx, group in enumerate(groups):
        text = " ".join(seg.text for seg in group)
        section = section_for_group(idx, len(groups), text)
        if section in clean_sections and len(clean_sections[section]) > len(text):
            narration_hint = clean_sections[section]
        else:
            narration_hint = text
        primary, fallback, why, paid = choose_route(text, section)
        route_counts[primary] = route_counts.get(primary, 0) + 1
        visual = visual_language(section, primary, idx)
        keywords = extract_keywords(text)
        prompts = build_prompts(
            text=text,
            section=section,
            route=primary,
            target_aspect=target_aspect,
            keywords=keywords,
            visual=visual,
        )
        shot_id = f"shot_{idx + 1:03d}"
        prev_id = f"shot_{idx:03d}" if idx else None
        shots.append({
            "id": shot_id,
            "section": section,
            "start": _round2(group[0].start),
            "end": _round2(group[-1].end),
            "duration": _round2(group[-1].end - group[0].start),
            "source_segment_ids": [seg.idx for seg in group],
            "narration": narration_hint,
            "keywords": keywords,
            "visual": {
                **visual,
                "first_frame": (
                    f"Open on {visual['shot_size']} for {keywords[0] if keywords else section}; "
                    "keep a clean lower third for subtitles."
                ),
                "motion": visual["camera_movement"],
                "last_frame": "Hold a clean final frame for 6-10 frames so the next cut has breathing room.",
            },
            "generation_route": {
                "primary": primary,
                "fallback": fallback,
                "why": why,
                "requires_paid_credits": paid,
                "approval_note": (
                    "Dreamina/即梦 generation may consume credits; confirm before submitting."
                    if paid else ""
                ),
            },
            "prompts": prompts,
            "continuity": {
                "reuse_reference_from": prev_id,
                "anchors": [
                    f"palette={','.join(continuity_palette)}",
                    f"aspect={target_aspect}",
                    "same subtitle-safe lower third",
                    f"keyword_thread={keywords[0] if keywords else section}",
                ],
            },
            "review_checks": [
                "Narration and visual purpose match.",
                "Generated media has no hard-coded Chinese subtitle text.",
                "Lower third remains clear for burned subtitles.",
                "Cut has a stable first or last frame for timeline review.",
            ],
        })

    total_duration = 0.0
    if segments:
        total_duration = max(seg.end for seg in segments) - min(seg.start for seg in segments)

    return {
        "version": "storyboard_plan.v1",
        "routing_note": ROUTING_SENTENCE,
        "source": {
            "transcript_segments": len(segments),
            "duration": _round2(total_duration),
            "clean_script_sections": sorted(clean_sections.keys()),
        },
        "target": {
            "platform": platform,
            "aspect": target_aspect,
            "max_shots": max_shots,
        },
        "route_summary": route_counts,
        "shots": shots,
        "next_steps": [
            "Review shot cards before generating paid video assets.",
            "Use Codex image_gen for codex_imagegen prompts.",
            "For dreamina_video prompts, ask for confirmation because Dreamina/即梦 may consume credits.",
            "After assets exist, add paths to enrich_plan imagegen[] or render_config image_overlays/broll_overlays.",
            "Run render_qa.py and timeline_view.py after rendering.",
        ],
    }


def emit_markdown(plan: Dict[str, Any]) -> str:
    lines = [
        "# Storyboard Plan",
        "",
        plan.get("routing_note", ROUTING_SENTENCE),
        "",
        f"- Target: {plan['target']['platform']} {plan['target']['aspect']}",
        f"- Shots: {len(plan.get('shots', []))}",
        f"- Routes: {json.dumps(plan.get('route_summary', {}), ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    for shot in plan.get("shots", []):
        route = shot["generation_route"]
        lines.extend([
            f"## {shot['id']} · {SECTION_LABELS.get(shot['section'], shot['section'])} · {route['primary']}",
            "",
            f"- Time: {shot['start']}s-{shot['end']}s ({shot['duration']}s)",
            f"- Narration: {shot['narration']}",
            f"- Why this route: {route['why']}",
            f"- Continuity: {', '.join(shot['continuity']['anchors'])}",
            "",
        ])
        prompts = shot.get("prompts", {})
        for key in ("image_prompt_en", "video_prompt_en", "motion_graphics_brief", "broll_query", "fallback_image_prompt_en"):
            if key in prompts:
                lines.extend([f"**{key}**", "", "```text", prompts[key], "```", ""])
        if route.get("approval_note"):
            lines.append(f"> {route['approval_note']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build storyboard shot cards and generation routing from transcript JSON."
    )
    parser.add_argument("--transcript", required=True, help="Transcript JSON with segments[].")
    parser.add_argument("--clean-script", help="Optional clean_script.md from rewrite_script.py.")
    parser.add_argument("--output", required=True, help="Output storyboard plan JSON.")
    parser.add_argument("--markdown", help="Optional Markdown shot-card output.")
    parser.add_argument("--max-shots", type=int, default=8, help="Maximum shot cards to create.")
    parser.add_argument("--target-aspect", default="9:16", help="Target aspect ratio, e.g. 9:16 or 3:4.")
    parser.add_argument("--platform", default="xhs", help="Target platform label.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    transcript = load_transcript(args.transcript)
    clean_script_text = None
    if args.clean_script:
        with open(args.clean_script, "r", encoding="utf-8") as f:
            clean_script_text = f.read()

    plan = build_storyboard_plan(
        transcript,
        clean_script_text=clean_script_text,
        max_shots=args.max_shots,
        target_aspect=args.target_aspect,
        platform=args.platform,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    print(f"Wrote storyboard plan: {args.output}")
    if args.markdown:
        print(f"Wrote storyboard markdown: {args.markdown}")
    print(f"Shots: {len(plan['shots'])}; routes: {json.dumps(plan['route_summary'], ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
