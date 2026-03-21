#!/usr/bin/env python3
"""
Generate a video cover image from the first frame with title text overlay.

Usage:
  python3 generate_cover.py <video_path> --title "视频标题"
  python3 generate_cover.py <video_path> --transcript <transcript.json>

If --title is not provided but --transcript is, a title suggestion will be
printed for the AI agent to refine (the agent should summarize from the
audience's perspective and pass the result back via --title).

Output: <video_name>_cover.jpg (in the same directory as the video)
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile


def find_chinese_font(custom_font_path=None):
    """Find a suitable Chinese font (same logic as burn_subtitles.py)."""
    if custom_font_path and os.path.isfile(custom_font_path):
        return custom_font_path

    script_dir = os.path.dirname(os.path.abspath(__file__))
    skill_fonts_dir = os.path.join(os.path.dirname(script_dir), "fonts")
    google_font_path = os.path.join(skill_fonts_dir, "NotoSansSC[wght].ttf")

    if os.path.isfile(google_font_path):
        return google_font_path

    system = platform.system()
    if system == "Darwin":
        import glob
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/PingFang.ttc",
        ]
        candidates += glob.glob("/System/Library/AssetsV2/**/PingFang.ttc", recursive=True)
    elif system == "Windows":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = [
            os.path.join(windir, "Fonts", "msyhbd.ttc"),
            os.path.join(windir, "Fonts", "msyh.ttc"),
            os.path.join(windir, "Fonts", "simhei.ttf"),
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", ":lang=zh"],
            capture_output=True, text=True, check=True
        )
        font_path = result.stdout.strip()
        if font_path and os.path.isfile(font_path):
            return font_path
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None


def sanitize_title(text):
    """Remove special characters that may cause rendering issues or garbled text.

    Keeps: Chinese/Japanese/Korean characters, ASCII letters, digits, common
    punctuation (，。！？、：；· and their ASCII equivalents), spaces.
    """
    # Remove ASS/subtitle control characters
    text = re.sub(r'[{}\\\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Remove emojis and other non-BMP characters that fonts may not support
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    # Remove stray symbols that commonly cause garbled display
    text = re.sub(r'[<>|@#$%^&*~`]', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_first_frame(video_path, output_path):
    """Extract the first frame of a video as a JPEG image."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def get_image_dimensions(image_path):
    """Get width and height of an image via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        image_path,
    ]
    result = subprocess.check_output(cmd, text=True)
    info = json.loads(result)
    streams = info.get("streams", [{}])
    w = streams[0].get("width", 1080) if streams else 1080
    h = streams[0].get("height", 1920) if streams else 1920
    return w, h


def overlay_title(image_path, title, font_path, output_path):
    """Overlay title text on an image using ffmpeg drawtext filter.

    The title is rendered as large, bold white text with a dark shadow,
    centered horizontally, positioned at roughly 40% from the top.
    For multi-line titles (containing \\n), each line is drawn separately.
    """
    width, height = get_image_dimensions(image_path)
    short_side = min(width, height)

    # Font size: ~6% of the short side, clamped
    font_size = max(36, min(int(short_side * 0.06), 120))

    # Split title into lines (max ~10 chars per line for Chinese, ~20 for English)
    lines = _wrap_title(title, max_chars=10, font_size=font_size, img_width=width)

    # Build drawtext filter chain — one filter per line, stacked vertically
    # Center block vertically around 40% from top
    total_lines = len(lines)
    line_height = int(font_size * 1.5)
    block_height = total_lines * line_height
    start_y = int(height * 0.40) - block_height // 2

    # Escape font path for ffmpeg
    escaped_font = font_path.replace("\\", "/").replace(":", "\\:") if font_path else ""

    filters = []
    for i, line in enumerate(lines):
        # Escape text for drawtext: ' → \\', : → \\:, etc.
        escaped_text = line.replace("\\", "\\\\").replace("'", "\u2019")
        escaped_text = escaped_text.replace(":", "\\:")
        y = start_y + i * line_height

        f = (
            f"drawtext=text='{escaped_text}'"
            f":fontsize={font_size}"
            f":fontcolor=white"
            f":borderw=4:bordercolor=black@0.8"
            f":shadowcolor=black@0.5:shadowx=3:shadowy=3"
            f":x=(w-text_w)/2:y={y}"
        )
        if escaped_font:
            f += f":fontfile='{escaped_font}'"
        filters.append(f)

    filter_str = ",".join(filters)

    cmd = [
        "ffmpeg", "-y",
        "-i", image_path,
        "-vf", filter_str,
        "-q:v", "2",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _wrap_title(title, max_chars=10, font_size=60, img_width=1080):
    """Split title into lines that fit the image width."""
    # Estimate usable width (80% of image width)
    usable = img_width * 0.80
    # Chinese char width ≈ font_size; ASCII ≈ 0.5 * font_size
    chars_per_line = max(4, int(usable / font_size))

    if len(title) <= chars_per_line:
        return [title]

    lines = []
    remaining = title
    while remaining:
        if len(remaining) <= chars_per_line:
            lines.append(remaining)
            break
        # Find a natural break point
        cut = chars_per_line
        best = cut
        for offset in range(min(4, cut)):
            for pos in [cut - offset, cut + offset]:
                if 0 < pos < len(remaining) and remaining[pos] in ' ,，。、；：！？·':
                    best = pos + 1 if remaining[pos] != ' ' else pos
                    break
            else:
                continue
            break
        else:
            best = cut
        lines.append(remaining[:best].strip())
        remaining = remaining[best:].strip()

    return lines


def collect_transcript_text(transcript_path):
    """Read transcript JSON and return the combined text of all segments."""
    with open(transcript_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", [])
    return " ".join(seg["text"].strip() for seg in segments if seg.get("text", "").strip())


def main():
    parser = argparse.ArgumentParser(description="Generate video cover image with title")
    parser.add_argument("video_path", help="Path to the video file (uses first frame)")
    parser.add_argument("--title", default=None,
                        help="Cover title text. If omitted, prints transcript summary for AI to refine.")
    parser.add_argument("--transcript", default=None,
                        help="Path to transcript JSON (used for auto-generating title suggestion)")
    parser.add_argument("--font-path", default=None, help="Custom font file path")
    parser.add_argument("--output", default=None,
                        help="Output cover image path (default: <video_name>_cover.jpg)")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video_path)
    if not os.path.isfile(video_path):
        print(f"Error: Video file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # If no title given, output transcript text for the AI agent to summarize
    if not args.title:
        if args.transcript and os.path.isfile(args.transcript):
            full_text = collect_transcript_text(args.transcript)
            print("TRANSCRIPT_FOR_TITLE_GENERATION:")
            print(full_text)
            print("\n(Please provide a --title based on the transcript above)")
        else:
            print("Error: Either --title or --transcript must be provided.", file=sys.stderr)
        sys.exit(0)

    title = sanitize_title(args.title)
    if not title:
        print("Error: Title is empty after sanitization.", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base = os.path.splitext(video_path)[0]
        output_path = base + "_cover.jpg"

    # Step 1: Extract first frame
    frame_fd, frame_path = tempfile.mkstemp(suffix=".jpg", prefix="cover_frame_")
    os.close(frame_fd)

    try:
        print(f"Extracting first frame from: {video_path}")
        extract_first_frame(video_path, frame_path)

        # Step 2: Find font
        font_path = find_chinese_font(args.font_path)
        if not font_path:
            print("WARNING: No Chinese font found, title may not render correctly.", file=sys.stderr)

        # Step 3: Overlay title
        print(f"Overlaying title: {title}")
        overlay_title(frame_path, title, font_path, output_path)

        print(f"Cover generated: {output_path}")
    finally:
        if os.path.exists(frame_path):
            os.remove(frame_path)


if __name__ == "__main__":
    main()
