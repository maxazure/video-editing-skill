#!/usr/bin/env python3
"""Auto stickers — schedule emoji/sticker overlays based on script emotion.

For AI/tech vertical we use a small "decent" set of unicode emojis as
stickers. No PNG asset deps. Emoji are mapped from sentence-level emotion
tags (excited / doubt / conclusion / joke / data / warning).

Frequency rules (from research):
  - Every 8-15 seconds for AI/tech style (sober, not萌).
  - Single shot ≤ 2 stickers — over that looks cheap.
  - Avoid covering faces/subtitles; default position right-of-句尾.

Emotion detection is keyword-based by default. A pluggable hook lets
heavier LLM-emotion-tagging replace it.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from typing import Iterable, List, Optional


# Per-emotion sticker pool (unicode). Order = priority.
EMOTION_STICKERS = {
    "excited":     ["🚀", "✨", "🔥"],
    "doubt":       ["🤔", "❓"],
    "conclusion":  ["💡", "✅"],
    "data":        ["📈", "📊", "📉"],
    "warning":     ["⚠️", "❗"],
    "joke":        ["😅", "🤣"],
}

# Keyword → emotion mapping (Chinese-first; English fallbacks).
KEYWORD_EMOTION = [
    (r"突然|不敢相信|没想到|疯狂|超|爆|amazing|unbelievable", "excited"),
    (r"为什么|怎么会|是不是|不一定|可能|或许|why\b|maybe", "doubt"),
    (r"所以|结论|总结|因此|记住|划重点|in conclusion|so basically", "conclusion"),
    (r"\d+%|\d+[万千百]|增长|下降|growth|percent", "data"),
    (r"小心|注意|千万别|不要|warning|careful", "warning"),
    (r"哈哈|笑死|搞笑|funny|lol", "joke"),
]


@dataclasses.dataclass(frozen=True)
class StickerCue:
    start: float
    end: float
    emotion: str
    sticker: str
    text_anchor: Optional[str] = None  # which word it should sit beside


def _classify(text: str) -> Optional[str]:
    for pat, emotion in KEYWORD_EMOTION:
        if re.search(pat, text, re.IGNORECASE):
            return emotion
    return None


def schedule_stickers(
    transcript: dict,
    *,
    min_interval_seconds: float = 8.0,
    max_per_segment: int = 2,
    duration_seconds: float = 1.4,
) -> List[StickerCue]:
    """Pick segments that contain emotion keywords; drop a sticker at the segment start."""
    cues: List[StickerCue] = []
    last_emit = -1e9
    per_segment_count = 0
    last_seg_idx = -1

    for idx, seg in enumerate(transcript.get("segments", [])):
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        emotion = _classify(text)
        if not emotion:
            continue

        if seg_start - last_emit < min_interval_seconds:
            continue

        if idx == last_seg_idx and per_segment_count >= max_per_segment:
            continue
        if idx != last_seg_idx:
            per_segment_count = 0
            last_seg_idx = idx

        pool = EMOTION_STICKERS.get(emotion, ["✨"])
        sticker = pool[len(cues) % len(pool)]

        cues.append(StickerCue(
            start=seg_start + 0.2,  # land just after segment start
            end=min(seg_start + 0.2 + duration_seconds, seg_end),
            emotion=emotion,
            sticker=sticker,
            text_anchor=text[:6],
        ))
        last_emit = seg_start
        per_segment_count += 1

    return cues


def main() -> int:
    p = argparse.ArgumentParser(description="Schedule emoji stickers based on transcript emotion")
    p.add_argument("--transcript", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--min-interval", type=float, default=8.0)
    p.add_argument("--max-per-segment", type=int, default=2)
    args = p.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)

    cues = schedule_stickers(
        transcript,
        min_interval_seconds=args.min_interval,
        max_per_segment=args.max_per_segment,
    )

    payload = [dataclasses.asdict(c) for c in cues]
    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ {len(cues)} sticker cues → {args.output}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
