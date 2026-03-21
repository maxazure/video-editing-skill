#!/usr/bin/env python3
"""
Burn a visual chapter timeline bar into a video.

The bar shows colored segments representing chapters, with an animated
playhead that sweeps across as the video plays. Chapter titles are briefly
displayed at the start of each chapter.

Chapters can be provided as:
  - A JSON file (same format as transcript, or a dedicated chapters JSON)
  - Auto-generated from transcript segments by grouping adjacent sentences

Usage:
  python3 add_chapter_bar.py <video_path> --chapters <chapters.json>
  python3 add_chapter_bar.py <video_path> --transcript <transcript.json>

Output: <video_name>_chapters.mp4
"""

import argparse
import json
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    detect_gpu, get_ffmpeg_encode_args, escape_ffmpeg_path,
    find_chinese_font,
)

# Material Design inspired palette — high contrast, distinguishable
CHAPTER_COLORS = [
    "0x4CAF50",  # green
    "0x2196F3",  # blue
    "0xFF9800",  # orange
    "0xE91E63",  # pink
    "0x9C27B0",  # purple
    "0x00BCD4",  # cyan
    "0xFF5722",  # deep orange
    "0x3F51B5",  # indigo
    "0x8BC34A",  # light green
    "0xFFC107",  # amber
]


def get_video_info(video_path):
    """Get video duration, width, height."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    result = subprocess.check_output(cmd, text=True)
    info = json.loads(result)
    duration = float(info.get("format", {}).get("duration", 0))
    streams = info.get("streams", [{}])
    s = streams[0] if streams else {}
    w = s.get("width", 1920)
    h = s.get("height", 1080)
    return duration, w, h


def is_portrait(width, height):
    """Check if video is portrait (taller than wide)."""
    return height > width


def load_chapters(chapters_path):
    """Load chapters from a JSON file.

    Expected format:
    {
      "chapters": [
        {"title": "Opening", "start": 0.0, "end": 15.0},
        {"title": "Main Topic", "start": 15.0, "end": 60.0},
        ...
      ]
    }
    """
    with open(chapters_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("chapters", [])


def chapters_from_transcript(transcript_path, max_chapters=8):
    """Auto-generate chapters by grouping transcript segments.

    Groups adjacent segments into roughly equal-duration chapters.
    Returns a chapters list.
    """
    with open(transcript_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    if not segments:
        return []

    total_start = segments[0]["start"]
    total_end = segments[-1]["end"]
    total_duration = total_end - total_start

    if total_duration <= 0:
        return []

    # Decide number of chapters: aim for 15-30s per chapter, max 8
    n_chapters = max(2, min(max_chapters, int(total_duration / 20)))
    target_dur = total_duration / n_chapters

    chapters = []
    chap_start = segments[0]["start"]
    chap_texts = []
    chap_idx = 0

    for seg in segments:
        chap_texts.append(seg["text"].strip())
        elapsed = seg["end"] - chap_start

        # Start a new chapter if we've exceeded target duration
        # (but always include at least one segment per chapter)
        if elapsed >= target_dur and chap_idx < n_chapters - 1:
            # Use the first meaningful text as title
            title = _pick_chapter_title(chap_texts)
            chapters.append({
                "title": title,
                "start": round(chap_start, 2),
                "end": round(seg["end"], 2),
            })
            chap_start = seg["end"]
            chap_texts = []
            chap_idx += 1

    # Last chapter
    if chap_texts or chap_start < total_end:
        title = _pick_chapter_title(chap_texts) if chap_texts else f"Part {chap_idx + 1}"
        chapters.append({
            "title": title,
            "start": round(chap_start, 2),
            "end": round(total_end, 2),
        })

    return chapters


def _pick_chapter_title(texts, max_len=12):
    """Pick a short representative title from a list of segment texts."""
    if not texts:
        return ""
    # Use the longest sentence (usually most informative), trimmed
    best = max(texts, key=len)
    # Trim to max_len characters
    if len(best) > max_len:
        # Try to cut at a natural boundary
        for i in range(max_len, max(max_len - 4, 0), -1):
            if best[i] in " ,，。、；：":
                return best[:i]
        return best[:max_len]
    return best


def _escape_drawtext(text):
    """Escape text for ffmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")  # curly quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


def build_chapter_bar_filter(chapters, total_duration, width, height, font_path=None):
    """Build the complete ffmpeg filter string for chapter timeline bar.

    Returns the -vf filter string.
    """
    portrait = is_portrait(width, height)
    short_side = min(width, height)

    # Bar dimensions
    bar_height = max(4, int(short_side * 0.008))  # ~0.8% of short side
    bar_alpha = 0.85

    # Position: portrait -> top area; landscape -> bottom
    if portrait:
        # Portrait: place bar at top, below status bar area (~3% from top)
        bar_y = int(height * 0.03)
    else:
        # Landscape: bottom edge
        bar_y = height - bar_height

    # Font size for chapter labels
    label_font_size = max(12, int(short_side * 0.018))
    if portrait:
        label_y = bar_y + bar_height + 4  # below bar for portrait
    else:
        label_y = bar_y - label_font_size - 6  # above bar for landscape

    filters = []

    # 1. Dark background bar for contrast
    filters.append(
        f"drawbox=x=0:y={bar_y}:w=iw:h={bar_height}:color=black@0.5:t=fill"
    )

    # 2. Colored chapter segments
    for i, chap in enumerate(chapters):
        color = CHAPTER_COLORS[i % len(CHAPTER_COLORS)]
        chap_dur = chap["end"] - chap["start"]
        # Calculate x position and width as proportions of total duration
        x_frac = chap["start"] / total_duration
        w_frac = chap_dur / total_duration

        x_expr = f"iw*{x_frac:.6f}"
        w_expr = f"iw*{w_frac:.6f}"

        filters.append(
            f"drawbox=x={x_expr}:y={bar_y}:w={w_expr}:h={bar_height}"
            f":color={color}@{bar_alpha}:t=fill"
        )

    # 3. Thin separator lines between chapters
    for i in range(1, len(chapters)):
        x_frac = chapters[i]["start"] / total_duration
        filters.append(
            f"drawbox=x=iw*{x_frac:.6f}-1:y={bar_y}:w=2:h={bar_height}"
            f":color=white@0.9:t=fill"
        )

    # 4. Animated playhead (semi-transparent white sweep)
    filters.append(
        f"drawbox=x=0:y={bar_y}:w=iw*t/{total_duration:.2f}:h={bar_height}"
        f":color=white@0.35:t=fill"
    )

    # 5. Playhead cursor (small bright dot/line at current position)
    cursor_w = max(2, bar_height // 3)
    filters.append(
        f"drawbox=x=iw*t/{total_duration:.2f}-{cursor_w // 2}:y={bar_y}-1"
        f":w={cursor_w}:h={bar_height}+2"
        f":color=white@0.95:t=fill"
    )

    # 6. Chapter title labels — show for 3 seconds at the start of each chapter
    if font_path:
        escaped_font = escape_ffmpeg_path(font_path)
        font_arg = f":fontfile='{escaped_font}'"
    else:
        font_arg = ""

    for i, chap in enumerate(chapters):
        title = chap.get("title", "").strip()
        if not title:
            continue

        escaped_title = _escape_drawtext(title)
        chap_start = chap["start"]
        chap_end = min(chap_start + 3.0, chap["end"])  # show for up to 3s

        # Center label over its segment
        mid_frac = (chap["start"] + chap["end"]) / 2.0 / total_duration

        # Fade: 0.3s in, hold, 0.5s out
        fade_in_end = chap_start + 0.3
        fade_out_start = chap_end - 0.5
        # Alpha expression: smooth fade in/out
        alpha_expr = (
            f"if(lt(t\\,{fade_in_end:.2f})\\,"
            f"(t-{chap_start:.2f})/0.3\\,"
            f"if(lt(t\\,{fade_out_start:.2f})\\,1\\,"
            f"({chap_end:.2f}-t)/0.5))"
        )

        filters.append(
            f"drawtext=text='{escaped_title}'"
            f":fontsize={label_font_size}"
            f":fontcolor=white@'%{{eif\\:{alpha_expr}\\:d\\:2}}'"
            f":borderw=2:bordercolor=black@0.6"
            f":x=iw*{mid_frac:.6f}-tw/2:y={label_y}"
            f":enable='between(t,{chap_start:.2f},{chap_end:.2f})'"
            f"{font_arg}"
        )

    return ",".join(filters)


def main():
    parser = argparse.ArgumentParser(
        description="Add a visual chapter timeline bar to a video")
    parser.add_argument("video_path", help="Path to the video file")
    parser.add_argument("--chapters", default=None,
                        help="Path to chapters JSON file")
    parser.add_argument("--transcript", default=None,
                        help="Path to transcript JSON (auto-generate chapters)")
    parser.add_argument("--max-chapters", type=int, default=8,
                        help="Max number of auto-generated chapters (default: 8)")
    parser.add_argument("--font-path", default=None,
                        help="Custom font file path for chapter labels")
    parser.add_argument("--output", default=None,
                        help="Output video path (default: <name>_chapters.mp4)")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video_path)
    if not os.path.isfile(video_path):
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Load or generate chapters
    if args.chapters:
        chapters = load_chapters(args.chapters)
    elif args.transcript:
        chapters = chapters_from_transcript(args.transcript, args.max_chapters)
    else:
        print("Error: Either --chapters or --transcript must be provided.", file=sys.stderr)
        sys.exit(1)

    if not chapters:
        print("Error: No chapters found or generated.", file=sys.stderr)
        sys.exit(1)

    # Get video info
    duration, width, height = get_video_info(video_path)
    print(f"Video: {width}x{height}, {duration:.1f}s, "
          f"{'portrait' if is_portrait(width, height) else 'landscape'}")
    print(f"Chapters: {len(chapters)}")
    for i, ch in enumerate(chapters):
        print(f"  [{i+1}] {ch['start']:.1f}s - {ch['end']:.1f}s  {ch.get('title', '')}")

    # Find font
    font_path, font_name = find_chinese_font(args.font_path)

    # Build filter
    vf = build_chapter_bar_filter(chapters, duration, width, height, font_path)

    # Output path
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base, ext = os.path.splitext(video_path)
        output_path = base + "_chapters" + ext

    # GPU encoding
    encode_args = get_ffmpeg_encode_args()

    print(f"Adding chapter bar...")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
    ] + encode_args + [
        "-c:a", "copy",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"Done: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    # Also output chapters as YouTube-compatible timestamps
    print("\nYouTube chapter timestamps:")
    for ch in chapters:
        m = int(ch["start"] // 60)
        s = int(ch["start"] % 60)
        print(f"  {m}:{s:02d} {ch.get('title', '')}")


if __name__ == "__main__":
    main()
