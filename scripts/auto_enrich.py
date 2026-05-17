#!/usr/bin/env python3
"""Auto-Enrich orchestrator.

Given a transcript + optional BGM + clean script, produces an enrichment
plan (cue list) that the final render reads from. Combines:
  - auto_broll.schedule_broll
  - auto_chapter_cards.schedule_cards
  - auto_stickers.schedule_stickers
  - beat_sync.snap_to_beats (optional, when BGM provided)

Output is a single JSON the render layer consumes.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_broll import schedule_broll  # noqa: E402
from auto_chapter_cards import parse_chapters_from_md, schedule_cards  # noqa: E402
from auto_stickers import schedule_stickers  # noqa: E402
from imagegen_hint import detect_opportunities as detect_imagegen_opportunities  # noqa: E402


def build_plan(
    transcript: dict,
    *,
    clean_script_path: Optional[str] = None,
    bgm_path: Optional[str] = None,
    assets_path: Optional[str] = None,
    total_duration: float = 90.0,
) -> dict:
    assets = None
    if assets_path and os.path.isfile(assets_path):
        with open(assets_path, encoding="utf-8") as f:
            assets = json.load(f)

    broll = schedule_broll(transcript, available_assets=assets)
    stickers = schedule_stickers(transcript)
    chapters = []
    clean_text = None
    if clean_script_path:
        titles = parse_chapters_from_md(clean_script_path)
        chapters = schedule_cards(titles, total_duration=total_duration)
        if os.path.isfile(clean_script_path):
            with open(clean_script_path, encoding="utf-8") as f:
                clean_text = f.read()

    # Detect AI image-generation opportunities (abstract concepts, visual metaphors).
    # The cues are advisory — the next step (typically a Codex agent) decides
    # which ones to actually generate via the built-in `imagegen` tool.
    imagegen_cues = detect_imagegen_opportunities(transcript, clean_text)

    if bgm_path:
        try:
            from beat_sync import detect_beats, snap_to_beats
            _tempo, beats = detect_beats(bgm_path)
            if beats:
                broll = [
                    dataclasses.replace(c, start=snap_to_beats([c.start], beats)[0])
                    for c in broll
                ]
                stickers = [
                    dataclasses.replace(s, start=snap_to_beats([s.start], beats)[0])
                    for s in stickers
                ]
        except ImportError:
            pass

    return {
        "broll": [dataclasses.asdict(c) for c in broll],
        "stickers": [dataclasses.asdict(c) for c in stickers],
        "chapter_cards": [dataclasses.asdict(c) for c in chapters],
        "imagegen": [dataclasses.asdict(c) for c in imagegen_cues],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Auto-Enrich orchestrator")
    p.add_argument("--transcript", required=True)
    p.add_argument("--clean-script", default=None,
                   help="clean_script.md to extract `## ` headings as chapter cards")
    p.add_argument("--bgm", default=None, help="BGM audio for beat snapping")
    p.add_argument("--assets", default=None,
                   help="Media asset index JSON for B-roll matching")
    p.add_argument("--total-duration", type=float, default=90.0)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)

    plan = build_plan(
        transcript,
        clean_script_path=args.clean_script,
        bgm_path=args.bgm,
        assets_path=args.assets,
        total_duration=args.total_duration,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"✅ enrichment plan → {args.output}")
    print(f"   broll:    {len(plan['broll'])}")
    print(f"   stickers: {len(plan['stickers'])}")
    print(f"   chapters: {len(plan['chapter_cards'])}")
    print(f"   imagegen: {len(plan['imagegen'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
