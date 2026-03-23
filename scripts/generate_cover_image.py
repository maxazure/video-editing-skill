#!/usr/bin/env python3
"""
Generate a video cover image using headless Chrome for perfect text rendering.

Uses HTML/CSS to produce bold, eye-catching covers in the style of Xiaohongshu
(小红书) with thick outlined Chinese text, anti-aliased rendering, and rich
visual effects that far exceed ffmpeg drawtext quality.

Usage:
  python3 generate_cover_image.py <video_path> --title "标题" --output cover.png
  python3 generate_cover_image.py <video_path> --title "标题" --use-frame
  # Or import and call generate_cover() from another script
"""

import argparse
import base64
import os
import platform
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info, sanitize_title


def find_chrome():
    """Find Chrome/Chromium binary path."""
    candidates = []
    system = platform.system()

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        ]
    elif system == "Linux":
        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
        ]
    elif system == "Windows":
        import glob
        for base in [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")]:
            if base:
                candidates.extend(glob.glob(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")))

    for c in candidates:
        if os.path.isfile(c):
            return c
        found = shutil.which(c)
        if found:
            return found

    return None


def extract_first_frame(video_path, output_path):
    """Extract first frame as PNG."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "1",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _is_good_break(title, pos):
    """Check if position `pos` is a good place to break a Chinese title.

    Good break points: before action verbs, prepositions, conjunctions,
    or after particles and punctuation — natural phrase boundaries.
    """
    if pos <= 0 or pos >= len(title):
        return False
    after = title[pos]      # char that starts the new line
    before = title[pos - 1] # char that ends the previous line
    # Great: break after punctuation or particles
    if before in "，。、！？；：的了呢吧吗啊哦呀":
        return True
    # Good: break before action/function words (natural phrase starters)
    if after in "做让把去来在从对用跟给为是不但而且如所能会就都也还":
        return True
    return False


def _smart_lines(title, chars_per_line):
    """Break Chinese title into lines at natural positions.

    Returns a list of line strings.
    """
    if len(title) <= chars_per_line:
        return [title]

    mid = len(title) // 2
    # Search outward from mid for the best break point
    best = mid
    found = False
    for offset in range(min(5, mid)):
        for pos in [mid + offset, mid - offset]:
            if _is_good_break(title, pos):
                best = pos
                found = True
                break
        if found:
            break

    lines = []
    for part in [title[:best].strip(), title[best:].strip()]:
        if len(part) > chars_per_line:
            lines.extend(_smart_lines(part, chars_per_line))
        else:
            lines.append(part)
    return lines


def build_cover_html(title, width, height, frame_base64=None):
    """Build HTML for cover in bold Xiaohongshu style.

    Args:
        title: Cover title text
        width, height: Video dimensions
        frame_base64: Optional base64-encoded first frame PNG for background.
                      If None, uses solid black background.
    """
    short_side = min(width, height)
    is_portrait = height > width

    # Font size — bold and impactful, sized to fit ~7 chars per line
    if is_portrait:
        font_size = max(64, min(int(short_side * 0.11), 160))
    else:
        font_size = max(48, min(int(short_side * 0.08), 130))

    # Stroke thickness — thick like Xiaohongshu style
    stroke_w = max(6, int(font_size * 0.09))

    # Background
    if frame_base64:
        bg_css = f"""
    background-image: url("data:image/png;base64,{frame_base64}");
    background-size: cover;
    background-position: center;"""
        overlay_css = "background: rgba(0,0,0,0.4);"
        # On photo background: white text + thick black outline
        text_color = "#FFFFFF"
        text_stroke_color = "#000000"
        shadow_css = f"""
      {stroke_w}px {stroke_w}px 0 #000,
      -{stroke_w}px -{stroke_w}px 0 #000,
      {stroke_w}px -{stroke_w}px 0 #000,
      -{stroke_w}px {stroke_w}px 0 #000,
      0 0 {stroke_w * 3}px rgba(0,0,0,0.8),
      0 {stroke_w * 2}px {stroke_w * 5}px rgba(0,0,0,0.4);"""
    else:
        bg_css = "background: #000;"
        overlay_css = "background: transparent;"
        # On black background: white text, no black outline (invisible on black)
        # Use white glow for depth instead
        text_color = "#FFFFFF"
        text_stroke_color = "rgba(255,255,255,0.15)"
        shadow_css = f"""
      0 0 {stroke_w * 2}px rgba(255,255,255,0.15),
      0 {stroke_w}px {stroke_w * 3}px rgba(0,0,0,0.6);"""

    # Estimate chars per line (text-stroke doesn't affect layout width)
    letter_spacing_factor = 1.04  # 0.04em
    effective_char_w = font_size * letter_spacing_factor
    chars_per_line = max(4, int(width * 0.88 / effective_char_w))
    lines = _smart_lines(title, chars_per_line)
    title_html = "\n".join(f'<div class="line">{line}</div>' for line in lines)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    width: {width}px;
    height: {height}px;
    overflow: hidden;
  }}
  .bg {{
    width: 100%;
    height: 100%;
    {bg_css}
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .overlay {{
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    {overlay_css}
  }}
  .title-wrap {{
    position: relative;
    z-index: 1;
    width: {int(width * 0.88)}px;
  }}
  .title {{
    color: {text_color};
    font-family: "Heiti SC", "PingFang SC", "Noto Sans SC",
                 "Microsoft YaHei", "SimHei", "STHeiti", sans-serif;
    font-size: {font_size}px;
    font-weight: 900;
    line-height: 1.35;
    text-align: center;
    letter-spacing: 0.04em;
    paint-order: stroke fill;
    -webkit-text-stroke: {stroke_w}px {text_stroke_color};
    text-shadow: {shadow_css}
  }}
  .line {{
    white-space: nowrap;
  }}
</style>
</head>
<body>
  <div class="bg">
    <div class="overlay"></div>
    <div class="title-wrap">
      <div class="title">{title_html}</div>
    </div>
  </div>
</body>
</html>"""
    return html


def chrome_screenshot(chrome_path, html_path, output_path, width, height):
    """Use headless Chrome to screenshot an HTML file."""
    cmd = [
        chrome_path,
        "--headless",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        f"--screenshot={output_path}",
        f"--window-size={width},{height}",
        "--force-device-scale-factor=1",
        f"file://{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if not os.path.isfile(output_path):
        raise RuntimeError(
            f"Chrome screenshot failed.\nstdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )


def generate_cover(video_path, title, output_path=None, width=None, height=None,
                   use_frame=False):
    """Generate a cover image for a video.

    Args:
        video_path: Path to source video
        title: Cover title text
        output_path: Output PNG path (default: <video_name>_cover.png)
        width, height: Override dimensions (default: from video)
        use_frame: If True, use video's first frame as background.
                   If False (default), use solid black background.

    Returns:
        Path to generated cover PNG, or None if Chrome not available.
    """
    chrome_path = find_chrome()
    if not chrome_path:
        print("[cover] Chrome/Chromium not found, cannot generate HTML cover", file=sys.stderr)
        return None

    if width is None or height is None:
        _, w, h, _, _ = get_video_info(video_path)
        width = width or w
        height = height or h

    if output_path is None:
        base = os.path.splitext(video_path)[0]
        output_path = f"{base}_cover.png"

    title = sanitize_title(title)

    tmp_dir = tempfile.mkdtemp(prefix="cover_")
    try:
        # Optional: extract first frame for background
        frame_base64 = None
        if use_frame:
            frame_path = os.path.join(tmp_dir, "frame.png")
            extract_first_frame(video_path, frame_path)
            with open(frame_path, "rb") as f:
                frame_base64 = base64.b64encode(f.read()).decode("ascii")

        # Build HTML
        html_content = build_cover_html(title, width, height, frame_base64)
        html_path = os.path.join(tmp_dir, "cover.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # Chrome screenshot
        chrome_screenshot(chrome_path, html_path, output_path, width, height)
        bg_type = "frame" if use_frame else "black"
        print(f"[cover] Generated: {output_path} ({width}x{height}, bg={bg_type})")
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Generate video cover image via Chrome")
    parser.add_argument("video_path", help="Path to source video")
    parser.add_argument("--title", required=True, help="Cover title text")
    parser.add_argument("--output", default=None, help="Output PNG path")
    parser.add_argument("--use-frame", action="store_true",
                        help="Use video first frame as background (default: black)")
    args = parser.parse_args()

    result = generate_cover(args.video_path, args.title, args.output,
                           use_frame=args.use_frame)
    if not result:
        print("Failed to generate cover image", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
