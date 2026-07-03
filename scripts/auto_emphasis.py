#!/usr/bin/env python3
"""Transcript-driven emphasis cue planner.

The output is intentionally an enrich-plan fragment: it does not render media
itself, but marks moments where render_final.py can add a short badge and/or a
subtle push-in. Rules are deterministic so review artifacts are reproducible.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Iterable, List, Mapping, Optional


VERSION = "auto_emphasis_plan.v1"


@dataclasses.dataclass(frozen=True)
class EmphasisCue:
    start: float
    end: float
    label: str
    trigger: str
    matched_text: str
    score: float
    reason: str
    effect: str = "badge_push_in"
    zoom: float = 1.08
    x: float = 0.5
    y: float = 0.5
    marker: bool = False
    show_badge: bool = True
    source_segment_id: Optional[Any] = None
    text_anchor: Optional[str] = None


TRIGGER_RULES = [
    {
        "trigger": "numeric_claim",
        "pattern": re.compile(
            r"(\d+(?:\.\d+)?\s?(?:%|％|倍|万|千|亿|k|m|b|x)|\b\d+(?:\.\d+)?\s?(?:percent|times|million|billion)\b)",
            re.IGNORECASE,
        ),
        "label": "数据点",
        "score": 0.94,
        "zoom": 1.10,
        "reason": "numeric claim should visually land",
    },
    {
        "trigger": "payoff_line",
        "pattern": re.compile(
            r"(所以|结论|答案是|这就是|最终|结果|记住|划重点|in conclusion|that's why|therefore|so the point is)",
            re.IGNORECASE,
        ),
        "label": "重点",
        "score": 0.90,
        "zoom": 1.12,
        "reason": "payoff or conclusion line",
    },
    {
        "trigger": "contrast_turn",
        "pattern": re.compile(
            r"(但是|然而|不过|可是|其实|关键是|重点来了|反过来|but\b|however\b|actually\b|turns out\b|the key is\b)",
            re.IGNORECASE,
        ),
        "label": "转折",
        "score": 0.84,
        "zoom": 1.08,
        "reason": "contrast turn changes the argument",
    },
    {
        "trigger": "question_hook",
        "pattern": re.compile(
            r"(为什么|怎么|是不是|凭什么|[?？]|why\b|how\b|what if\b|do you\b|did you\b)",
            re.IGNORECASE,
        ),
        "label": "问题来了",
        "score": 0.80,
        "zoom": 1.07,
        "reason": "question or curiosity hook",
    },
    {
        "trigger": "risk_warning",
        "pattern": re.compile(
            r"(注意|风险|小心|千万别|不要|别急|careful\b|warning\b|risk\b|avoid\b)",
            re.IGNORECASE,
        ),
        "label": "注意",
        "score": 0.78,
        "zoom": 1.07,
        "reason": "risk or warning beat",
    },
]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _segment_text(segment: Mapping[str, Any]) -> str:
    return str(segment.get("text") or segment.get("sentence") or "").strip()


def _word_text(word: Mapping[str, Any]) -> str:
    return str(word.get("word") or word.get("text") or word.get("token") or "").strip()


def _word_anchor(segment: Mapping[str, Any], matched_text: str) -> Optional[float]:
    needle = matched_text.strip().lower()
    if not needle:
        return None
    for word in segment.get("words") or []:
        if not isinstance(word, Mapping):
            continue
        token = _word_text(word).lower()
        if not token:
            continue
        if needle in token or token in needle:
            return _as_float(word.get("start"), _as_float(segment.get("start"), 0.0))
    return None


def _approx_anchor(segment: Mapping[str, Any], match: re.Match[str]) -> float:
    text = _segment_text(segment)
    seg_start = _as_float(segment.get("start"), 0.0)
    seg_end = _as_float(segment.get("end"), seg_start + 1.0)
    word_anchor = _word_anchor(segment, match.group(0))
    if word_anchor is not None:
        return word_anchor
    if not text or seg_end <= seg_start:
        return seg_start
    ratio = max(0.0, min(1.0, match.start() / max(len(text), 1)))
    return seg_start + (seg_end - seg_start) * ratio


def _cue_end(start: float, segment: Mapping[str, Any], duration: float) -> float:
    seg_end = _as_float(segment.get("end"), start + duration)
    end = min(seg_end, start + duration)
    if end - start < 0.45:
        end = start + duration
    return round(end, 3)


def _candidate_from_rule(segment: Mapping[str, Any], rule: Mapping[str, Any]) -> Optional[EmphasisCue]:
    text = _segment_text(segment)
    if not text:
        return None
    match = rule["pattern"].search(text)
    if not match:
        return None
    start = round(_approx_anchor(segment, match), 3)
    return EmphasisCue(
        start=start,
        end=_cue_end(start, segment, 1.05),
        label=str(rule["label"]),
        trigger=str(rule["trigger"]),
        matched_text=match.group(0),
        score=float(rule["score"]),
        reason=str(rule["reason"]),
        zoom=float(rule["zoom"]),
        source_segment_id=segment.get("id"),
        text_anchor=text[:24],
    )


def _pause_candidate(
    segment: Mapping[str, Any],
    *,
    previous_end: float,
    pause_threshold_seconds: float,
) -> Optional[EmphasisCue]:
    seg_start = _as_float(segment.get("start"), 0.0)
    if previous_end < 0 or seg_start - previous_end < pause_threshold_seconds:
        return None
    text = _segment_text(segment)
    if not text:
        return None
    start = round(seg_start + 0.08, 3)
    return EmphasisCue(
        start=start,
        end=_cue_end(start, segment, 0.95),
        label="停顿后重点",
        trigger="pause_resume",
        matched_text=f"{seg_start - previous_end:.1f}s pause",
        score=0.72,
        reason="line resumes after a noticeable pause",
        zoom=1.06,
        source_segment_id=segment.get("id"),
        text_anchor=text[:24],
    )


def _segment_candidates(
    segment: Mapping[str, Any],
    *,
    previous_end: float,
    pause_threshold_seconds: float,
) -> List[EmphasisCue]:
    candidates = []
    for rule in TRIGGER_RULES:
        cue = _candidate_from_rule(segment, rule)
        if cue:
            candidates.append(cue)
    pause = _pause_candidate(
        segment,
        previous_end=previous_end,
        pause_threshold_seconds=pause_threshold_seconds,
    )
    if pause:
        candidates.append(pause)
    return candidates


def schedule_emphasis(
    transcript: Mapping[str, Any],
    *,
    min_interval_seconds: float = 3.0,
    max_cues: int = 12,
    pause_threshold_seconds: float = 0.85,
) -> List[EmphasisCue]:
    """Return non-overlapping emphasis cues from transcript segments."""
    raw_candidates: List[EmphasisCue] = []
    previous_end = -1.0

    for segment in transcript.get("segments", []) or []:
        if not isinstance(segment, Mapping):
            continue
        raw_candidates.extend(
            _segment_candidates(
                segment,
                previous_end=previous_end,
                pause_threshold_seconds=pause_threshold_seconds,
            )
        )
        previous_end = _as_float(segment.get("end"), previous_end)

    selected: List[EmphasisCue] = []
    for cue in sorted(raw_candidates, key=lambda item: (-item.score, item.start)):
        if any(abs(cue.start - chosen.start) < min_interval_seconds for chosen in selected):
            continue
        selected.append(cue)
        if len(selected) >= max_cues:
            break

    return sorted(selected, key=lambda item: item.start)


def build_plan(
    transcript: Mapping[str, Any],
    *,
    min_interval_seconds: float = 3.0,
    max_cues: int = 12,
    pause_threshold_seconds: float = 0.85,
) -> dict:
    cues = schedule_emphasis(
        transcript,
        min_interval_seconds=min_interval_seconds,
        max_cues=max_cues,
        pause_threshold_seconds=pause_threshold_seconds,
    )
    trigger_counts = Counter(cue.trigger for cue in cues)
    return {
        "version": VERSION,
        "summary": {
            "cues": len(cues),
            "triggers": dict(sorted(trigger_counts.items())),
            "min_interval_seconds": min_interval_seconds,
            "max_cues": max_cues,
        },
        "emphasis_cues": [dataclasses.asdict(cue) for cue in cues],
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Auto Emphasis Plan",
        "",
        f"- Version: `{plan.get('version', VERSION)}`",
        f"- Cues: {plan.get('summary', {}).get('cues', len(plan.get('emphasis_cues') or []))}",
        "",
        "| time | trigger | label | matched | reason |",
        "|---:|---|---|---|---|",
    ]
    for cue in plan.get("emphasis_cues") or []:
        lines.append(
            "| {start:.2f}-{end:.2f}s | `{trigger}` | {label} | {matched} | {reason} |".format(
                start=_as_float(cue.get("start")),
                end=_as_float(cue.get("end")),
                trigger=str(cue.get("trigger") or ""),
                label=str(cue.get("label") or ""),
                matched=str(cue.get("matched_text") or "").replace("|", "\\|"),
                reason=str(cue.get("reason") or "").replace("|", "\\|"),
            )
        )
    if not plan.get("emphasis_cues"):
        lines.append("| - | - | - | - | No emphasis cues found. |")
    lines.extend([
        "",
        "Render with `render_final.py --enrich-plan`; cues become timed badges and subtle push-ins.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create transcript-driven emphasis cues for enrich_plan rendering.")
    parser.add_argument("--transcript", required=True, help="Transcript JSON with segments[].")
    parser.add_argument("--output", default=None, help="Output JSON path; stdout if omitted.")
    parser.add_argument("--markdown", default=None, help="Optional Markdown review path.")
    parser.add_argument("--min-interval", type=float, default=3.0, help="Minimum seconds between selected cues.")
    parser.add_argument("--max-cues", type=int, default=12, help="Maximum cues to emit.")
    parser.add_argument("--pause-threshold", type=float, default=0.85, help="Gap before a segment that can trigger pause_resume.")
    args = parser.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)

    plan = build_plan(
        transcript,
        min_interval_seconds=args.min_interval,
        max_cues=args.max_cues,
        pause_threshold_seconds=args.pause_threshold,
    )

    out_text = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ {len(plan['emphasis_cues'])} emphasis cues → {args.output}")
    else:
        print(out_text)

    if args.markdown:
        os.makedirs(os.path.dirname(args.markdown) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))
        print(f"📝 emphasis review → {args.markdown}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
