#!/usr/bin/env python3
"""Generate chapter title-card PNGs for short-form video.

Inputs: clean_script.md (with `## ChapterName` headings) OR a list of
{title, start_seconds} pairs from a transcript-analysis step.

Outputs: one 1080×1920 PNG per chapter card + a manifest JSON the
render pipeline can `overlay` from.

Three design templates:
  - color_block    — large word on a colored block (Xiaohongshu standard)
  - tag_title      — top tag pill + main title + bottom takeaway strip
  - fullscreen_quote — black background, single line, centered

Auto-trigger rules (when not given an explicit chapter list):
  - markdown `## ` headings in clean_script.md
  - silence > 1.5s detected in the audio (via ffmpeg silencedetect)
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from typing import List, Optional


@dataclasses.dataclass(frozen=True)
class ChapterCard:
    title: str
    start: float            # seconds in final timeline
    duration: float = 1.0
    style: str = "color_block"   # color_block / tag_title / fullscreen_quote
    color: str = "#1A1A1A"
    text_color: str = "#FFFFFF"
    tag: Optional[str] = None
    takeaway: Optional[str] = None


def parse_chapters_from_md(md_path: str) -> List[str]:
    """Return list of chapter titles from `## ` headings in a markdown file."""
    titles: List[str] = []
    if not os.path.isfile(md_path):
        return titles
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("## ") and not line.startswith("### "):
                titles.append(line[3:].strip())
    return titles


def detect_silence_boundaries(audio_path: str, *, threshold_db: float = -30.0,
                              min_duration_seconds: float = 1.5) -> List[float]:
    """Return start times of silences ≥ min_duration in the audio file.

    Uses ffmpeg silencedetect. Empty list if ffmpeg fails or the file is missing.
    """
    if not os.path.isfile(audio_path):
        return []
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", audio_path,
             "-af", f"silencedetect=n={threshold_db}dB:d={min_duration_seconds}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    starts: List[float] = []
    for line in (out.stderr or "").splitlines():
        if "silence_start" in line:
            try:
                token = line.split("silence_start:")[1].strip().split()[0]
                starts.append(float(token))
            except (IndexError, ValueError):
                continue
    return starts


def schedule_cards(
    titles: List[str],
    *,
    boundaries: Optional[List[float]] = None,
    total_duration: float = 90.0,
    style: str = "color_block",
    min_cards: int = 0,
    max_cards: int = 5,
) -> List[ChapterCard]:
    """Place chapter cards. If `boundaries` given, snap to those; otherwise
    distribute evenly across `total_duration`."""
    if not titles:
        return []
    n = min(max(len(titles), min_cards), max_cards)
    titles = titles[:n]

    if boundaries and len(boundaries) >= n:
        starts = boundaries[:n]
    else:
        # evenly spaced, skip first 3s (don't card the opening hook)
        if n == 0:
            return []
        step = (total_duration - 3.0) / (n + 1)
        starts = [3.0 + step * (i + 1) for i in range(n)]

    out: List[ChapterCard] = []
    palette = [
        ("#1A1A1A", "#FFFFFF"),
        ("#FF4040", "#FFFFFF"),
        ("#FFD63D", "#1A1A1A"),
        ("#3D7BFF", "#FFFFFF"),
        ("#1A1A1A", "#FFD63D"),
    ]
    for i, (title, start) in enumerate(zip(titles, starts)):
        bg, fg = palette[i % len(palette)]
        out.append(ChapterCard(title=title, start=round(start, 2),
                                duration=1.0, style=style,
                                color=bg, text_color=fg))
    return out


def render_card_png(card: ChapterCard, *, width: int = 1080, height: int = 1920,
                    output_path: str, font_path: Optional[str] = None) -> str:
    """Render the chapter card to a PNG using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), card.color)
    draw = ImageDraw.Draw(img)

    title = card.title
    target_h_ratio = 0.18 if card.style == "color_block" else 0.12
    font_size = int(height * target_h_ratio)

    font = None
    if font_path and os.path.isfile(font_path):
        try:
            font = ImageFont.truetype(font_path, size=font_size)
        except Exception:  # noqa: BLE001
            font = None
    if font is None:
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/STHeiti Medium.ttc", size=font_size,
            )
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2 - bbox[1]
    draw.text((x, y), title, fill=card.text_color, font=font)

    if card.tag:
        tag_font = ImageFont.truetype(font.path, size=font_size // 3) if hasattr(font, "path") else font
        tag_bbox = draw.textbbox((0, 0), card.tag, font=tag_font)
        tag_w = tag_bbox[2] - tag_bbox[0]
        pad = 24
        draw.rectangle(
            [(width / 2 - tag_w / 2 - pad, y - 160 - pad),
             (width / 2 + tag_w / 2 + pad, y - 160 + 60)],
            fill=card.text_color,
        )
        draw.text((width / 2 - tag_w / 2, y - 160 - 16),
                  card.tag, fill=card.color, font=tag_font)

    img.save(output_path, format="PNG", optimize=True)
    return output_path


def main() -> int:
    p = argparse.ArgumentParser(description="Auto-generate chapter title cards")
    p.add_argument("--script", help="Path to clean_script.md (parses `## ` headings)")
    p.add_argument("--audio", help="Optional audio file for silence-based boundary detection")
    p.add_argument("--titles", nargs="*", default=None,
                   help="Override: explicit list of chapter titles")
    p.add_argument("--total-duration", type=float, default=90.0)
    p.add_argument("--style", choices=["color_block", "tag_title", "fullscreen_quote"],
                   default="color_block")
    p.add_argument("--output-dir", default="./chapter_cards")
    p.add_argument("--font-path", default=None)
    p.add_argument("--no-render", action="store_true", help="Manifest only, skip PNG output")
    args = p.parse_args()

    titles = args.titles or (parse_chapters_from_md(args.script) if args.script else [])
    if not titles:
        print("No chapter titles found (pass --titles or --script with `## ` headings).",
              file=sys.stderr)
        return 1

    boundaries = detect_silence_boundaries(args.audio) if args.audio else None
    cards = schedule_cards(
        titles, boundaries=boundaries,
        total_duration=args.total_duration,
        style=args.style,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = []
    for i, card in enumerate(cards):
        png_path = os.path.join(args.output_dir, f"chapter_{i:02d}.png")
        if not args.no_render:
            try:
                render_card_png(card, output_path=png_path, font_path=args.font_path)
            except ImportError:
                print("Pillow not installed; install with `pip install Pillow` to render PNGs.",
                      file=sys.stderr)
                return 2
        manifest.append({**dataclasses.asdict(card), "png": png_path})

    manifest_path = os.path.join(args.output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"✅ {len(cards)} cards → {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
