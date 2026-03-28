#!/usr/bin/env python3
"""
Generate a Remotion timeline.json from a transcript.json for standup comedy videos.

Given an audio transcript (from transcribe.py), this script creates a timeline
config with varied text animations, background colors, and transitions to make
the audio-only content visually engaging.

Usage:
    python3 scripts/generate_standup_timeline.py transcript.json \
        --audio audio/standup.wav \
        --output remotion-standup/public/timeline.json

    # Select specific segments
    python3 scripts/generate_standup_timeline.py transcript.json \
        --audio audio/standup.wav \
        --segments 1-10,12,15-20 \
        --output timeline.json

    # Customize style
    python3 scripts/generate_standup_timeline.py transcript.json \
        --audio audio/standup.wav \
        --style energetic \
        --font "LXGW WenKai" \
        --output timeline.json
"""

import argparse
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------------------
# Animation & style palettes
# ---------------------------------------------------------------------------

# Text animations — cycled and mixed for variety
ANIMATIONS = [
    "springIn", "scaleUp", "fadeIn", "typewriter", "bounce",
    "slam", "wave", "rotateIn", "splitReveal", "scaleDown",
]

# Emphasis animations — used for short sentences (likely punchlines)
EMPHASIS_ANIMATIONS = ["slam", "shake", "bounce", "scaleDown", "glitch"]

# Background color palettes (gradient pairs/triples)
BACKGROUND_PALETTES = [
    ["#0f0c29", "#302b63", "#24243e"],  # 深紫蓝
    ["#1a1a2e", "#16213e", "#0f3460"],  # 深蓝
    ["#2d1b69", "#11998e"],             # 紫绿
    ["#1f1c2c", "#928DAB"],             # 灰紫
    ["#0F2027", "#203A43", "#2C5364"],  # 深青
    ["#141E30", "#243B55"],             # 深海蓝
    ["#000000", "#434343"],             # 黑灰
    ["#1a002e", "#5b0060", "#870060"],  # 暗紫红
    ["#0d0d0d", "#1a1a2e"],             # 纯黑蓝
    ["#1B1B1B", "#2C3E50"],             # 炭灰蓝
    ["#0c0c1d", "#1b2838", "#2a4858"],  # 午夜蓝
    ["#1a0000", "#4a0000"],             # 暗红
]

# Accent colors for punchline backgrounds
PUNCHLINE_PALETTES = [
    ["#e94560", "#1a1a2e"],  # 红黑
    ["#ff6b35", "#1a1a2e"],  # 橙黑
    ["#FFD700", "#1a1a2e"],  # 金黑
    ["#00ff87", "#0a0a2e"],  # 绿黑
    ["#ff2e63", "#252a34"],  # 粉黑
]

# Text colors
TEXT_COLORS = ["#ffffff", "#f0f0f0"]
PUNCHLINE_TEXT_COLORS = ["#ffffff", "#FFD700", "#ff6b6b", "#00ff87"]

# Transition types
TRANSITIONS = ["fade", "slide", "wipe", "fade", "fade"]  # bias toward fade

# Style presets
STYLE_PRESETS = {
    "calm": {
        "animations": ["fadeIn", "springIn", "typewriter", "splitReveal"],
        "emphasis_animations": ["scaleUp", "springIn"],
        "transition_duration_ms": 500,
        "progress_color": "#4ecdc4",
        "waveform_color": "#ffffff40",
    },
    "energetic": {
        "animations": ["slam", "bounce", "scaleUp", "shake", "wave", "glitch"],
        "emphasis_animations": ["slam", "shake", "glitch", "bounce"],
        "transition_duration_ms": 300,
        "progress_color": "#e94560",
        "waveform_color": "#ffffff",
    },
    "default": {
        "animations": ANIMATIONS,
        "emphasis_animations": EMPHASIS_ANIMATIONS,
        "transition_duration_ms": 400,
        "progress_color": "#e94560",
        "waveform_color": "#ffffff",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_segments_selection(selection_str, max_id):
    """Parse segment selection string like '1-10,12,15-20'."""
    if not selection_str:
        return None  # select all
    selected = set()
    for part in selection_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            for i in range(int(start), int(end) + 1):
                if 1 <= i <= max_id:
                    selected.add(i)
        else:
            i = int(part)
            if 1 <= i <= max_id:
                selected.add(i)
    return selected


def is_short_sentence(text, language="zh"):
    """Detect if a sentence is short (likely a punchline or reaction)."""
    clean = re.sub(r'[^\w]', '', text)
    if language == "zh":
        return len(clean) <= 8
    return len(clean.split()) <= 5


def is_punchline_candidate(text, prev_text=None):
    """Heuristic: detect potential punchlines based on text patterns."""
    # Short sentences after long ones are often punchlines
    if prev_text and len(text) < len(prev_text) * 0.4:
        return True
    # Exclamation, question marks, or laughter indicators
    if re.search(r'[！!？?]', text):
        return True
    # Common Chinese comedy keywords
    keywords = ["哈哈", "笑", "对吧", "就这", "完了", "没了", "真的",
                "太", "绝了", "离谱", "卧槽", "我去", "可以", "牛"]
    return any(kw in text for kw in keywords)


def detect_language(segments):
    """Simple language detection from transcript segments."""
    total = "".join(seg["text"] for seg in segments)
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', total))
    return "zh" if chinese_chars > len(total) * 0.3 else "en"


def build_scene(seg, index, total, language, style, prev_text=None):
    """Build a single scene dict from a transcript segment."""
    text = seg["text"].strip()
    start_ms = int(seg["start"] * 1000)
    end_ms = int(seg["end"] * 1000)

    # Determine if this is a punchline/emphasis moment
    is_punch = is_punchline_candidate(text, prev_text)
    is_short = is_short_sentence(text, language)

    # Pick animation
    if is_punch or is_short:
        anims = style["emphasis_animations"]
    else:
        anims = style["animations"]
    animation = anims[index % len(anims)]

    # Pick emphasis level
    if is_punch:
        emphasis = 1.6
    elif is_short:
        emphasis = 1.3
    elif len(text) > 30 if language == "zh" else len(text.split()) > 15:
        emphasis = 0.8  # long text gets smaller font
    else:
        emphasis = 1.0

    # Pick background
    if is_punch:
        palette = PUNCHLINE_PALETTES[index % len(PUNCHLINE_PALETTES)]
        bg_type = "radial"
    else:
        palette = BACKGROUND_PALETTES[index % len(BACKGROUND_PALETTES)]
        bg_type = "gradient"

    # Pick text color
    text_color = (
        PUNCHLINE_TEXT_COLORS[index % len(PUNCHLINE_TEXT_COLORS)]
        if is_punch
        else TEXT_COLORS[index % len(TEXT_COLORS)]
    )

    # Pick transition
    trans_type = TRANSITIONS[index % len(TRANSITIONS)]
    trans_duration = style["transition_duration_ms"]

    # Last scene: no transition
    if index == total - 1:
        trans_type = "none"
        trans_duration = 0

    # For long Chinese text, insert line breaks
    display_text = text
    if language == "zh" and len(text) > 12:
        # Break roughly in the middle at punctuation or mid-point
        mid = len(text) // 2
        # Try to find punctuation near middle
        best = mid
        for offset in range(min(6, mid)):
            for pos in [mid + offset, mid - offset]:
                if 0 < pos < len(text) and text[pos] in "，。、；：！？,.;:!? ":
                    best = pos + 1
                    break
            else:
                continue
            break
        if best != mid or len(text) > 16:
            display_text = text[:best].rstrip() + "\n" + text[best:].lstrip()

    return {
        "id": index + 1,
        "startMs": start_ms,
        "endMs": end_ms,
        "text": display_text,
        "animation": animation,
        "emphasis": emphasis,
        "background": {
            "type": bg_type,
            "colors": palette,
            "animateAngle": not is_punch,
        },
        "textColor": text_color,
        "transition": {
            "type": trans_type,
            "durationMs": trans_duration,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_timeline(transcript_path, audio_src, segments_sel=None,
                      style_name="default", font_family=None,
                      fps=30, width=1080, height=1920):
    """Generate a Remotion timeline from transcript.json."""
    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript = json.load(f)

    segments = transcript.get("segments", [])
    if not segments:
        print("ERROR: No segments found in transcript.", file=sys.stderr)
        sys.exit(1)

    # Filter segments if selection provided
    selected_ids = parse_segments_selection(segments_sel, len(segments))
    if selected_ids:
        segments = [s for s in segments if s["id"] in selected_ids]

    if not segments:
        print("ERROR: No segments matched the selection.", file=sys.stderr)
        sys.exit(1)

    # Detect language
    language = detect_language(segments)
    print(f"[timeline] Detected language: {language}")
    print(f"[timeline] Segments: {len(segments)}")

    # Style preset
    style = STYLE_PRESETS.get(style_name, STYLE_PRESETS["default"])

    # Default font
    if not font_family:
        font_family = "Noto Sans SC, sans-serif" if language == "zh" else "Inter, sans-serif"

    # Build scenes
    scenes = []
    prev_text = None
    for i, seg in enumerate(segments):
        scene = build_scene(seg, i, len(segments), language, style, prev_text)
        scenes.append(scene)
        prev_text = seg["text"]

    # Rebuild timing: scenes are consecutive (no gaps)
    # The original transcript timestamps define scene boundaries
    # We keep them as-is since they correspond to the audio

    timeline = {
        "fps": fps,
        "width": width,
        "height": height,
        "audioSrc": audio_src,
        "fontFamily": font_family,
        "scenes": scenes,
        "showProgressBar": True,
        "progressBarColor": style["progress_color"],
        "showWaveform": True,
        "waveformColor": style["waveform_color"],
    }

    return timeline


def main():
    parser = argparse.ArgumentParser(
        description="Generate Remotion timeline from transcript for standup comedy video"
    )
    parser.add_argument("transcript", help="Path to transcript.json")
    parser.add_argument("--audio", required=True,
                        help="Audio source path relative to public/ (e.g. audio/standup.wav)")
    parser.add_argument("--output", "-o", default="timeline.json",
                        help="Output timeline.json path (default: timeline.json)")
    parser.add_argument("--segments", "-s", default=None,
                        help="Segment selection (e.g. 1-10,12,15-20). Default: all")
    parser.add_argument("--style", default="default",
                        choices=["default", "calm", "energetic"],
                        help="Visual style preset (default: default)")
    parser.add_argument("--font", default=None,
                        help="Font family (e.g. 'LXGW WenKai, sans-serif')")
    parser.add_argument("--fps", type=int, default=30,
                        help="Frames per second (default: 30)")
    parser.add_argument("--width", type=int, default=1080,
                        help="Video width (default: 1080)")
    parser.add_argument("--height", type=int, default=1920,
                        help="Video height (default: 1920)")

    args = parser.parse_args()

    if not os.path.isfile(args.transcript):
        print(f"ERROR: Transcript file not found: {args.transcript}", file=sys.stderr)
        sys.exit(1)

    timeline = generate_timeline(
        args.transcript,
        args.audio,
        segments_sel=args.segments,
        style_name=args.style,
        font_family=args.font,
        fps=args.fps,
        width=args.width,
        height=args.height,
    )

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    n = len(timeline["scenes"])
    last = timeline["scenes"][-1]
    duration_s = last["endMs"] / 1000
    print(f"[timeline] Generated {n} scenes, {duration_s:.1f}s total")
    print(f"[timeline] Style: {args.style}, Font: {timeline['fontFamily']}")
    print(f"[timeline] Output: {args.output}")

    # Show scene summary
    print()
    print(f"{'#':>3}  {'Time':>12}  {'Animation':>14}  {'Em':>4}  Text")
    print("─" * 70)
    for s in timeline["scenes"]:
        t0 = f"{s['startMs']/1000:.1f}-{s['endMs']/1000:.1f}s"
        txt = s["text"].replace("\n", " ").strip()
        if len(txt) > 30:
            txt = txt[:28] + "…"
        print(f"{s['id']:>3}  {t0:>12}  {s['animation']:>14}  {s['emphasis']:>4.1f}  {txt}")


if __name__ == "__main__":
    main()
