#!/usr/bin/env python3
"""
Generate video cover images using headless Chrome for perfect text rendering.

Multiple styles optimized for Xiaohongshu/Douyin/YouTube Shorts. Supports
title + subtitle, video frame backgrounds, and smart Chinese line breaking.

Available styles:
  bold     — Black bg, large white text, clean and simple (default)
  news     — Dark gradient bg, white title + yellow subtitle, for hot takes
  frame    — Video first frame bg with dark overlay, white text with outline
  gradient — Colored gradient bg, white text with glow effect
  minimal  — Black bg, thin white text, understated and elegant

Usage:
  python3 generate_cover_image.py <video> --title "标题" --style bold
  python3 generate_cover_image.py <video> --title "标题" --subtitle "副标题" --style news
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

STYLES = ["bold", "news", "frame", "gradient", "minimal"]


def find_chrome():
    """Find Chrome/Chromium binary path."""
    candidates = []
    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]
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
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "1", output_path]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Smart Chinese line breaking
# ---------------------------------------------------------------------------

def _is_good_break(title, pos):
    """Check if position is a natural Chinese phrase boundary."""
    if pos <= 0 or pos >= len(title):
        return False
    after = title[pos]
    before = title[pos - 1]
    if before in "，。、！？；：的了呢吧吗啊哦呀":
        return True
    if after in "做让把去来在从对用跟给为是不但而且如所能会就都也还最":
        return True
    return False


def _smart_lines(title, chars_per_line):
    """Break Chinese title into lines at natural positions."""
    if len(title) <= chars_per_line:
        return [title]
    mid = len(title) // 2
    best = mid
    for offset in range(min(5, mid)):
        for pos in [mid + offset, mid - offset]:
            if _is_good_break(title, pos):
                best = pos
                break
        else:
            continue
        break
    lines = []
    for part in [title[:best].strip(), title[best:].strip()]:
        if len(part) > chars_per_line:
            lines.extend(_smart_lines(part, chars_per_line))
        else:
            lines.append(part)
    return lines


def _text_to_html(text, font_size, width):
    """Convert text to HTML with smart line breaks."""
    effective_char_w = font_size * 1.06
    chars_per_line = max(4, int(width * 0.88 / effective_char_w))
    lines = _smart_lines(text, chars_per_line)
    return "\n".join(f'<div class="line">{line}</div>' for line in lines)


# ---------------------------------------------------------------------------
# Font stack
# ---------------------------------------------------------------------------

FONT_STACK = '"Heiti SC", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "SimHei", sans-serif'


# ---------------------------------------------------------------------------
# Style builders — each returns complete HTML
# ---------------------------------------------------------------------------

def _style_bold(title, subtitle, width, height, frame_b64):
    """Black background, large white text, clean."""
    fs = _calc_font_size(width, height, 0.11)
    sub_fs = int(fs * 0.55)
    stroke_w = max(4, int(fs * 0.06))
    title_html = _text_to_html(title, fs, width)
    sub_html = _text_to_html(subtitle, sub_fs, width) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
.bg{{width:100%;height:100%;background:#000;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:{int(fs*0.5)}px}}
.title{{width:{int(width*0.88)}px;color:#FFF;font-family:{FONT_STACK};
  font-size:{fs}px;font-weight:900;line-height:1.3;text-align:center;
  letter-spacing:0.04em;paint-order:stroke fill;
  -webkit-text-stroke:{stroke_w}px rgba(255,255,255,0.1);
  text-shadow:0 0 {stroke_w*2}px rgba(255,255,255,0.1),0 {stroke_w}px {stroke_w*3}px rgba(0,0,0,0.5)}}
.subtitle{{width:{int(width*0.88)}px;color:#AAAAAA;font-family:{FONT_STACK};
  font-size:{sub_fs}px;font-weight:600;line-height:1.4;text-align:center;letter-spacing:0.02em}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg">
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _style_news(title, subtitle, width, height, frame_b64):
    """Dark gradient, white title + yellow subtitle — for hot takes."""
    fs = _calc_font_size(width, height, 0.11)
    sub_fs = int(fs * 0.65)
    stroke_w = max(5, int(fs * 0.08))
    title_html = _text_to_html(title, fs, width)
    sub_html = _text_to_html(subtitle, sub_fs, width) if subtitle else ""

    bg_css = "background: linear-gradient(170deg, #1a1a2e 0%, #16213e 40%, #0f3460 100%);"
    if frame_b64:
        bg_css = f"""background-image: url("data:image/png;base64,{frame_b64}");
          background-size:cover;background-position:center;"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
.bg{{width:100%;height:100%;{bg_css}position:relative;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(fs*0.4)}px}}
.overlay{{position:absolute;top:0;left:0;right:0;bottom:0;
  background:linear-gradient(180deg,rgba(0,0,0,0.3) 0%,rgba(0,0,0,0.5) 50%,rgba(0,0,0,0.7) 100%)}}
.title,.subtitle{{position:relative;z-index:1;width:{int(width*0.88)}px;text-align:center}}
.title{{color:#FFF;font-family:{FONT_STACK};font-size:{fs}px;font-weight:900;
  line-height:1.3;letter-spacing:0.04em;paint-order:stroke fill;
  -webkit-text-stroke:{stroke_w}px #000;
  text-shadow:{stroke_w}px {stroke_w}px 0 #000,-{stroke_w}px -{stroke_w}px 0 #000,
    {stroke_w}px -{stroke_w}px 0 #000,-{stroke_w}px {stroke_w}px 0 #000,
    0 0 {stroke_w*3}px rgba(0,0,0,0.8)}}
.subtitle{{color:#FFD700;font-family:{FONT_STACK};font-size:{sub_fs}px;font-weight:900;
  line-height:1.3;letter-spacing:0.04em;paint-order:stroke fill;
  -webkit-text-stroke:{int(stroke_w*0.8)}px #000;
  text-shadow:{stroke_w}px {stroke_w}px 0 #000,-{stroke_w}px -{stroke_w}px 0 #000,
    {stroke_w}px -{stroke_w}px 0 #000,-{stroke_w}px {stroke_w}px 0 #000,
    0 0 {stroke_w*3}px rgba(0,0,0,0.8)}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg"><div class="overlay"></div>
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _style_frame(title, subtitle, width, height, frame_b64):
    """Video frame background with heavy dark overlay, bold outlined text."""
    fs = _calc_font_size(width, height, 0.11)
    sub_fs = int(fs * 0.55)
    stroke_w = max(6, int(fs * 0.09))
    title_html = _text_to_html(title, fs, width)
    sub_html = _text_to_html(subtitle, sub_fs, width) if subtitle else ""

    if frame_b64:
        bg_css = f"""background-image:url("data:image/png;base64,{frame_b64}");
          background-size:cover;background-position:center;"""
    else:
        bg_css = "background:#111;"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
.bg{{width:100%;height:100%;{bg_css}position:relative;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(fs*0.4)}px}}
.overlay{{position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.45)}}
.title,.subtitle{{position:relative;z-index:1;width:{int(width*0.88)}px;text-align:center}}
.title{{color:#FFF;font-family:{FONT_STACK};font-size:{fs}px;font-weight:900;
  line-height:1.3;letter-spacing:0.04em;paint-order:stroke fill;
  -webkit-text-stroke:{stroke_w}px #000;
  text-shadow:{stroke_w}px {stroke_w}px 0 #000,-{stroke_w}px -{stroke_w}px 0 #000,
    {stroke_w}px -{stroke_w}px 0 #000,-{stroke_w}px {stroke_w}px 0 #000,
    0 {stroke_w*2}px {stroke_w*4}px rgba(0,0,0,0.6)}}
.subtitle{{color:#EEE;font-family:{FONT_STACK};font-size:{sub_fs}px;font-weight:600;
  line-height:1.4;letter-spacing:0.02em;
  text-shadow:0 2px 8px rgba(0,0,0,0.8)}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg"><div class="overlay"></div>
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _style_gradient(title, subtitle, width, height, frame_b64):
    """Colored gradient background with glowing white text."""
    fs = _calc_font_size(width, height, 0.10)
    sub_fs = int(fs * 0.55)
    title_html = _text_to_html(title, fs, width)
    sub_html = _text_to_html(subtitle, sub_fs, width) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
.bg{{width:100%;height:100%;
  background:linear-gradient(135deg,#667eea 0%,#764ba2 50%,#f093fb 100%);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(fs*0.5)}px}}
.title{{width:{int(width*0.85)}px;color:#FFF;font-family:{FONT_STACK};
  font-size:{fs}px;font-weight:900;line-height:1.35;text-align:center;
  letter-spacing:0.03em;
  text-shadow:0 0 40px rgba(255,255,255,0.3),0 4px 20px rgba(0,0,0,0.3)}}
.subtitle{{width:{int(width*0.85)}px;color:rgba(255,255,255,0.85);font-family:{FONT_STACK};
  font-size:{sub_fs}px;font-weight:500;line-height:1.4;text-align:center;
  letter-spacing:0.02em}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg">
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _style_minimal(title, subtitle, width, height, frame_b64):
    """Black background, thin elegant white text."""
    fs = _calc_font_size(width, height, 0.08)
    sub_fs = int(fs * 0.5)
    title_html = _text_to_html(title, fs, width)
    sub_html = _text_to_html(subtitle, sub_fs, width) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
.bg{{width:100%;height:100%;background:#000;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(fs*0.6)}px}}
.title{{width:{int(width*0.80)}px;color:#FFF;font-family:"PingFang SC",{FONT_STACK};
  font-size:{fs}px;font-weight:300;line-height:1.5;text-align:center;letter-spacing:0.08em}}
.subtitle{{width:{int(width*0.80)}px;color:#888;font-family:"PingFang SC",{FONT_STACK};
  font-size:{sub_fs}px;font-weight:300;line-height:1.5;text-align:center;letter-spacing:0.06em}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg">
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _calc_font_size(width, height, ratio):
    short_side = min(width, height)
    is_portrait = height > width
    if is_portrait:
        return max(48, min(int(short_side * ratio), 180))
    return max(40, min(int(short_side * ratio * 0.8), 140))


STYLE_BUILDERS = {
    "bold": _style_bold,
    "news": _style_news,
    "frame": _style_frame,
    "gradient": _style_gradient,
    "minimal": _style_minimal,
}


# ---------------------------------------------------------------------------
# Chrome screenshot
# ---------------------------------------------------------------------------

def chrome_screenshot(chrome_path, html_path, output_path, width, height):
    """Use headless Chrome to screenshot an HTML file."""
    cmd = [
        chrome_path, "--headless", "--disable-gpu", "--disable-software-rasterizer",
        "--no-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars",
        f"--screenshot={output_path}", f"--window-size={width},{height}",
        "--force-device-scale-factor=1", f"file://{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if not os.path.isfile(output_path):
        raise RuntimeError(f"Chrome screenshot failed.\nstderr: {result.stderr[:500]}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_cover(video_path, title, output_path=None, width=None, height=None,
                   style="bold", subtitle=None, use_frame=False):
    """Generate a cover image for a video.

    Args:
        video_path: Path to source video
        title: Main cover title text
        output_path: Output PNG path
        width, height: Override dimensions
        style: One of "bold", "news", "frame", "gradient", "minimal"
        subtitle: Optional secondary text line (yellow on news, gray on others)
        use_frame: Use video first frame as background (auto-enabled for "frame" style)

    Returns:
        Path to generated cover PNG, or None if Chrome not available.
    """
    chrome_path = find_chrome()
    if not chrome_path:
        print("[cover] Chrome/Chromium not found", file=sys.stderr)
        return None

    if width is None or height is None:
        _, w, h, _, _ = get_video_info(video_path)
        width = width or w
        height = height or h

    if output_path is None:
        base = os.path.splitext(video_path)[0]
        output_path = f"{base}_cover.png"

    title = sanitize_title(title)
    subtitle = sanitize_title(subtitle) if subtitle else None

    if style not in STYLE_BUILDERS:
        print(f"[cover] Unknown style '{style}', falling back to 'bold'", file=sys.stderr)
        style = "bold"

    # "frame" style always uses video frame; others optionally
    needs_frame = (style == "frame") or use_frame
    builder = STYLE_BUILDERS[style]

    tmp_dir = tempfile.mkdtemp(prefix="cover_")
    try:
        frame_b64 = None
        if needs_frame:
            frame_path = os.path.join(tmp_dir, "frame.png")
            extract_first_frame(video_path, frame_path)
            with open(frame_path, "rb") as f:
                frame_b64 = base64.b64encode(f.read()).decode("ascii")

        html = builder(title, subtitle, width, height, frame_b64)
        html_path = os.path.join(tmp_dir, "cover.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        chrome_screenshot(chrome_path, html_path, output_path, width, height)
        print(f"[cover] Generated: {output_path} ({width}x{height}, style={style})")
        return output_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Generate video cover image via Chrome")
    parser.add_argument("video_path", help="Path to source video")
    parser.add_argument("--title", required=True, help="Main title text")
    parser.add_argument("--subtitle", default=None, help="Secondary text line")
    parser.add_argument("--style", default="bold", choices=STYLES, help="Cover style")
    parser.add_argument("--use-frame", action="store_true", help="Use video frame as background")
    parser.add_argument("--output", default=None, help="Output PNG path")
    args = parser.parse_args()

    result = generate_cover(args.video_path, args.title, args.output,
                           style=args.style, subtitle=args.subtitle,
                           use_frame=args.use_frame)
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
