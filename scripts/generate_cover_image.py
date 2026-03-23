#!/usr/bin/env python3
"""
Generate a video cover image using headless Chrome for perfect text rendering.

Extracts the first frame from a video, creates an HTML page with the frame as
background and title text overlay, then uses headless Chrome to screenshot it
at exact video resolution. This produces anti-aliased text with CSS effects
(gradients, shadows, outlines) that far exceed ffmpeg drawtext quality.

Usage:
  python3 generate_cover_image.py <video_path> --title "标题" --output cover.png
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


def build_cover_html(frame_path, title, width, height):
    """Build HTML for cover with frame background and title overlay."""
    # Read frame and encode as base64 data URI
    with open(frame_path, "rb") as f:
        frame_data = base64.b64encode(f.read()).decode("ascii")

    # Determine if portrait
    is_portrait = height > width
    short_side = min(width, height)

    # Font size scales with short side
    font_size = max(36, min(int(short_side * 0.065), 130))
    line_height = 1.4

    # Vertical position: 40% from top
    top_pct = 38

    # Text shadow and stroke for readability
    text_shadow = "0 4px 12px rgba(0,0,0,0.8), 0 2px 4px rgba(0,0,0,0.6)"
    text_stroke = f"{max(1, font_size // 30)}px rgba(0,0,0,0.5)"

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
    background-image: url("data:image/png;base64,{frame_data}");
    background-size: cover;
    background-position: center;
    position: relative;
  }}
  .overlay {{
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(
      180deg,
      rgba(0,0,0,0.1) 0%,
      rgba(0,0,0,0.45) 30%,
      rgba(0,0,0,0.45) 50%,
      rgba(0,0,0,0.1) 80%,
      rgba(0,0,0,0.0) 100%
    );
  }}
  .title {{
    position: absolute;
    top: {top_pct}%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: {int(width * 0.82)}px;
    color: white;
    font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "Heiti SC", sans-serif;
    font-size: {font_size}px;
    font-weight: 800;
    line-height: {line_height};
    text-align: center;
    text-shadow: {text_shadow};
    -webkit-text-stroke: {text_stroke};
    letter-spacing: 0.02em;
    word-break: break-all;
  }}
</style>
</head>
<body>
  <div class="bg">
    <div class="overlay"></div>
    <div class="title">{title}</div>
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


def generate_cover(video_path, title, output_path=None, width=None, height=None):
    """Generate a cover image for a video.

    Args:
        video_path: Path to source video (first frame will be extracted)
        title: Cover title text
        output_path: Output PNG path (default: <video_name>_cover.png)
        width, height: Override dimensions (default: from video)

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
        # Step 1: Extract first frame
        frame_path = os.path.join(tmp_dir, "frame.png")
        extract_first_frame(video_path, frame_path)

        # Step 2: Build HTML
        html_content = build_cover_html(frame_path, title, width, height)
        html_path = os.path.join(tmp_dir, "cover.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # Step 3: Chrome screenshot
        chrome_screenshot(chrome_path, html_path, output_path, width, height)
        print(f"[cover] Generated: {output_path} ({width}x{height})")
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Generate video cover image via Chrome")
    parser.add_argument("video_path", help="Path to source video")
    parser.add_argument("--title", required=True, help="Cover title text")
    parser.add_argument("--output", default=None, help="Output PNG path")
    args = parser.parse_args()

    result = generate_cover(args.video_path, args.title, args.output)
    if not result:
        print("Failed to generate cover image", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
