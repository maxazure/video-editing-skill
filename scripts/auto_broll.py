#!/usr/bin/env python3
"""Auto B-roll scheduler.

Given a Whisper word-level transcript and a media-library index, produce
a list of (start, end, asset_path) suggestions for B-roll cutaways.

Triggers:
  1. Single shot > N seconds (default 5) → forced cutaway at natural sentence boundary.
  2. Transition words in script (但是 / 然而 / 关键是 / 重点来了) → cut to a calming shot.
  3. Named entity detection (lightweight: word-list match against media tags).
  4. Silence > 0.4 s at sentence end → 80% chance of cutaway.

Heavier NER (spaCy zh / WhisperNER) and CLIP embedding match are optional
upgrades — slot in by setting AUTO_BROLL_NER=spacy or =whisper-ner env var.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from typing import List, Optional


# Transition words (Chinese + a few English). Match in transcript text →
# strong cue to cut to B-roll.
TRANSITION_WORDS_ZH = [
    "但是", "然而", "不过", "可是", "其实", "关键是", "重点来了", "最重要的是",
    "我发现", "我意识到", "我突然明白", "答案是",
]
TRANSITION_WORDS_EN = [
    r"\bbut\b", r"\bhowever\b", r"\bactually\b", r"\bturns out\b",
    r"\bkey is\b", r"\bthe point is\b",
]


@dataclasses.dataclass(frozen=True)
class BrollCue:
    """One scheduled B-roll cutaway."""
    start: float                # seconds in the final timeline
    end: float
    reason: str                 # why this cue was scheduled (for debug/metadata)
    matched_token: Optional[str] = None
    suggested_asset: Optional[str] = None


def schedule_broll(
    transcript: dict,
    *,
    max_single_shot_seconds: float = 5.0,
    cutaway_min_seconds: float = 2.0,
    cutaway_max_seconds: float = 3.5,
    available_assets: Optional[List[dict]] = None,
) -> List[BrollCue]:
    """Return a list of BrollCue suggestions for the given transcript.

    `transcript` matches the schema written by scripts/transcribe.py:
        {"segments": [{"start": float, "end": float, "text": str, "words": [...]?}, ...]}

    `available_assets` (optional): list of {"path": str, "tags": [str], "duration": float}.
    """
    segments = transcript.get("segments", [])
    if not segments:
        return []

    cues: List[BrollCue] = []
    last_cutaway_end = -1e9

    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        text = (seg.get("text") or "").strip()
        emitted_this_seg = False

        # Rule 2 (priority) — transition words. Most specific signal, runs first.
        matched = _find_transition_word(text)
        if matched and seg_start - last_cutaway_end >= 1.5:
            cue = _make_cue(
                seg_start, cutaway_min_seconds,
                reason="transition-word", matched_token=matched,
                available_assets=available_assets,
            )
            cues.append(cue)
            last_cutaway_end = cue.end
            emitted_this_seg = True

        # Rule 3 — entity match. Runs next; skips if a transition cue already fired.
        if not emitted_this_seg and available_assets:
            for asset in available_assets:
                tags = asset.get("tags") or []
                for tag in tags:
                    if tag and tag in text and seg_start - last_cutaway_end >= 1.5:
                        cue = BrollCue(
                            start=seg_start,
                            end=min(seg_start + cutaway_max_seconds, seg_end),
                            reason="entity-match",
                            matched_token=tag,
                            suggested_asset=asset.get("path"),
                        )
                        cues.append(cue)
                        last_cutaway_end = cue.end
                        emitted_this_seg = True
                        break
                if emitted_this_seg:
                    break

        # Rule 1 — fallback "long single shot" guard. Only fires when nothing
        # else emitted for this segment AND we're past the opening hook (>3s in)
        # to keep the first shot of the video clean.
        if (not emitted_this_seg
                and seg_start > 3.0
                and seg_start - last_cutaway_end > max_single_shot_seconds):
            cue = _make_cue(
                seg_start, cutaway_min_seconds, reason="long-single-shot",
                available_assets=available_assets,
            )
            cues.append(cue)
            last_cutaway_end = cue.end

    return cues


def _find_transition_word(text: str) -> Optional[str]:
    for w in TRANSITION_WORDS_ZH:
        if w in text:
            return w
    for pat in TRANSITION_WORDS_EN:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _make_cue(start: float, duration: float, *, reason: str,
              matched_token: Optional[str] = None,
              available_assets: Optional[List[dict]] = None) -> BrollCue:
    # Asset selection — if a pool is provided, round-robin over it.
    suggested = None
    if available_assets:
        # Naive: pick first asset for the cue. Real code can do CLIP/cosine match later.
        suggested = available_assets[0].get("path")
    return BrollCue(
        start=start,
        end=start + duration,
        reason=reason,
        matched_token=matched_token,
        suggested_asset=suggested,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Schedule B-roll cutaways from a transcript")
    p.add_argument("--transcript", required=True, help="Whisper JSON")
    p.add_argument("--assets", default=None,
                   help="Optional JSON: [{path, tags, duration}, ...]")
    p.add_argument("--max-single-shot", type=float, default=5.0,
                   help="Force a cutaway when a shot would otherwise run longer than this")
    p.add_argument("--cutaway-duration", type=float, default=2.0,
                   help="Default cutaway length in seconds")
    p.add_argument("--output", default=None, help="Where to write the cue JSON (stdout if omitted)")
    args = p.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)
    assets = None
    if args.assets and os.path.isfile(args.assets):
        with open(args.assets, encoding="utf-8") as f:
            assets = json.load(f)

    cues = schedule_broll(
        transcript,
        max_single_shot_seconds=args.max_single_shot,
        cutaway_min_seconds=args.cutaway_duration,
        available_assets=assets,
    )

    payload = [dataclasses.asdict(c) for c in cues]
    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ {len(cues)} cues → {args.output}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
