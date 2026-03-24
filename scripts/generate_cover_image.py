#!/usr/bin/env python3
"""
Generate video cover images using headless Chrome for perfect text rendering.

Multiple styles optimized for Xiaohongshu/Douyin/YouTube Shorts. Supports
title + subtitle, video frame backgrounds, and smart Chinese line breaking.

Available styles:
  bold      — Black bg, large white text, clean and simple (default)
  news      — Dark gradient bg, white title + yellow subtitle, for hot takes
  frame     — Video first frame bg with dark overlay, white text with outline
  gradient  — Colored gradient bg, white text with glow effect
  minimal   — Black bg, thin white text, understated and elegant
  white     — Pure white bg, modern editorial look
  techcard  — Split tutorial cover with frame card and bold copy

Usage:
  python3 generate_cover_image.py <video> --title "标题" --style bold
  python3 generate_cover_image.py <video> --title "标题" --subtitle "副标题" --style news
"""

import argparse
import base64
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info, sanitize_title

STYLES = ["bold", "news", "frame", "gradient", "minimal", "white", "techcard"]
_CHROME_VIEWPORT_DELTA = {}


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


def get_chrome_viewport_delta(chrome_path, width, height):
    """Probe the difference between outer window height and inner viewport height."""
    cache_key = (chrome_path, width, height)
    if cache_key in _CHROME_VIEWPORT_DELTA:
        return _CHROME_VIEWPORT_DELTA[cache_key]

    if platform.system() != "Darwin":
        _CHROME_VIEWPORT_DELTA[cache_key] = 0
        return 0

    probe_fd, probe_path = tempfile.mkstemp(suffix=".html", prefix="chrome_viewport_probe_")
    os.close(probe_fd)
    try:
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write(
                '<!doctype html><meta charset="utf-8">'
                '<script>document.write(window.innerWidth+"x"+window.innerHeight)</script>'
            )
        cmd = [
            chrome_path, "--headless", "--disable-gpu", "--no-sandbox",
            "--dump-dom", f"--window-size={width},{height}", f"file://{probe_path}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        match = re.search(r"(\d+)x(\d+)", result.stdout)
        if not match:
            delta = 0
        else:
            inner_height = int(match.group(2))
            delta = max(0, height - inner_height)
        _CHROME_VIEWPORT_DELTA[cache_key] = delta
        return delta
    finally:
        try:
            os.remove(probe_path)
        except OSError:
            pass


def extract_first_frame(video_path, output_path, timestamp=None):
    """Extract one frame as PNG."""
    cmd = ["ffmpeg", "-y"]
    if timestamp:
        cmd.extend(["-ss", str(timestamp)])
    cmd.extend(["-i", video_path, "-vframes", "1", "-q:v", "1", output_path])
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


TITLE_MAX_UNITS = 8.0
ASCII_UNIT = 0.55
SPACE_UNIT = 0.35
PUNCT_UNIT = 0.5
TITLE_CHAR_WIDTH_FACTOR = 1.06
SUBTITLE_SCALE = 0.5


def _char_units(ch):
    """Approximate display width in Chinese-character units."""
    if "\u4e00" <= ch <= "\u9fff":
        return 1.0
    if ch.isspace():
        return SPACE_UNIT
    if ch.isascii():
        if ch.isalnum():
            return ASCII_UNIT
        return PUNCT_UNIT
    return 1.0


def _text_units(text):
    return sum(_char_units(ch) for ch in text)


def _wrap_text_by_units(text, max_units):
    """Wrap text by visual width so one line is about max_units Chinese chars."""
    text = (text or "").strip()
    if not text:
        return []

    if _text_units(text) <= max_units:
        return [text]

    lines = []
    remaining = text
    while remaining:
        if _text_units(remaining) <= max_units:
            lines.append(remaining)
            break

        current_units = 0.0
        hard_cut = None
        soft_cut = None
        for idx, ch in enumerate(remaining):
            next_units = current_units + _char_units(ch)
            if next_units > max_units:
                hard_cut = max(1, idx)
                break
            current_units = next_units
            if ch.isspace() or ch in "，。、！？；：,:!?/-" or _is_good_break(remaining, idx + 1):
                soft_cut = idx + 1

        cut = soft_cut or hard_cut or len(remaining)
        line = remaining[:cut].strip()
        if not line:
            line = remaining[:1]
            cut = 1
        lines.append(line)
        remaining = remaining[cut:].strip()

    return lines


def _text_to_html(text, font_size, container_width, max_units=None):
    """Convert text to HTML with width-aware line wrapping."""
    if not text:
        return ""
    if max_units is None:
        effective_char_w = font_size * TITLE_CHAR_WIDTH_FACTOR
        max_units = max(4, container_width / effective_char_w)
    lines = _wrap_text_by_units(text, max_units)
    return "\n".join(f'<div class="line">{line}</div>' for line in lines)


def _calc_title_font_size(container_width, height, max_units=TITLE_MAX_UNITS):
    """Size title text so one line fits about max_units Chinese characters."""
    target = int(container_width / (max_units * TITLE_CHAR_WIDTH_FACTOR))
    min_size = 64 if min(container_width, height) >= 900 else 48
    max_size = int(height * 0.24)
    return max(min_size, min(target, max_size))


def _calc_subtitle_font_size(title_font_size):
    """Subtitles default to half the title size for mobile readability."""
    return max(32, int(title_font_size * SUBTITLE_SCALE))


# ---------------------------------------------------------------------------
# Font stack
# ---------------------------------------------------------------------------

FONT_STACK = '"Heiti SC", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "SimHei", sans-serif'


# ---------------------------------------------------------------------------
# Style builders — each returns complete HTML
# ---------------------------------------------------------------------------

def _style_bold(title, subtitle, width, height, frame_b64):
    """Black background, large white text, clean."""
    title_width = int(width * 0.88)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    stroke_w = max(4, int(fs * 0.06))
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#000}}
.bg{{position:fixed;inset:0;background:#000;display:flex;flex-direction:column;
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
    title_width = int(width * 0.88)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    stroke_w = max(5, int(fs * 0.08))
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    bg_css = "background: linear-gradient(170deg, #1a1a2e 0%, #16213e 40%, #0f3460 100%);"
    if frame_b64:
        bg_css = f"""background-image: url("data:image/png;base64,{frame_b64}");
          background-size:cover;background-position:center;"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#16213e}}
.bg{{position:fixed;inset:0;{bg_css}
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
    title_width = int(width * 0.88)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    stroke_w = max(6, int(fs * 0.09))
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    if frame_b64:
        bg_css = f"""background-image:url("data:image/png;base64,{frame_b64}");
          background-size:cover;background-position:center;"""
    else:
        bg_css = "background:#111;"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#111}}
.bg{{position:fixed;inset:0;{bg_css}
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
    title_width = int(width * 0.85)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#764ba2}}
.bg{{position:fixed;inset:0;
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
    title_width = int(width * 0.80)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#000}}
.bg{{position:fixed;inset:0;background:#000;
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


def _style_white(title, subtitle, width, height, frame_b64):
    """Pure white background, dark text, clean modern look."""
    title_width = int(width * 0.82)
    subtitle_width = title_width
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 2) if subtitle else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@500;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#FFFFFF}}
.bg{{position:fixed;inset:0;background:#FFFFFF;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(fs*0.45)}px}}
.title{{width:{int(width*0.82)}px;color:#1a1a1a;
  font-family:"PingFang SC","Noto Sans SC",{FONT_STACK};
  font-size:{fs}px;font-weight:900;line-height:1.35;text-align:center;
  letter-spacing:0.02em}}
.subtitle{{width:{int(width*0.82)}px;color:#666666;
  font-family:"PingFang SC","Noto Sans SC",{FONT_STACK};
  font-size:{sub_fs}px;font-weight:500;line-height:1.5;text-align:center;
  letter-spacing:0.04em}}
.line{{white-space:nowrap}}
</style></head><body><div class="bg">
  <div class="title">{title_html}</div>
  {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
</div></body></html>"""


def _style_techcard(title, subtitle, width, height, frame_b64):
    """Split tutorial cover with editorial copy and a frame card."""
    title_width = int(width * 0.48)
    subtitle_width = int(width * 0.43)
    fs = _calc_title_font_size(title_width, height)
    sub_fs = _calc_subtitle_font_size(fs)
    badge_fs = int(fs * 0.20)
    chip_fs = int(fs * 0.24)
    title_html = _text_to_html(title, fs, title_width, max_units=TITLE_MAX_UNITS)
    sub_html = _text_to_html(subtitle, sub_fs, subtitle_width, max_units=TITLE_MAX_UNITS * 1.6) if subtitle else ""

    preview_html = ""
    if frame_b64:
        preview_html = f"""
      <div class="shotWrap">
        <div class="shot">
          <img src="data:image/png;base64,{frame_b64}" alt="">
        </div>
      </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100vw;height:100vh;overflow:hidden;background:#f5f1ea}}
body{{font-family:"PingFang SC","Noto Sans SC",{FONT_STACK};background:#f5f1ea;color:#111}}
.bg{{position:fixed;inset:0;overflow:hidden;
  background:
    radial-gradient(circle at 13% 18%, rgba(255,156,76,0.24) 0, rgba(255,156,76,0.0) 24%),
    radial-gradient(circle at 82% 24%, rgba(57,145,255,0.22) 0, rgba(57,145,255,0.0) 24%),
    linear-gradient(140deg, #f7f2eb 0%, #f2eee7 48%, #ece8df 100%);}}
.grid{{position:absolute;inset:0;opacity:0.12;
  background-image:
    linear-gradient(rgba(0,0,0,0.12) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,0,0,0.12) 1px, transparent 1px);
  background-size:{int(width*0.04)}px {int(width*0.04)}px;
  mask-image:linear-gradient(180deg,rgba(0,0,0,0.8),transparent 85%);}}
.layout{{position:relative;z-index:1;width:100%;height:100%;display:flex;align-items:center;
  justify-content:space-between;padding:{int(width*0.07)}px {int(width*0.08)}px {int(width*0.07)}px {int(width*0.08)}px;
  gap:{int(width*0.04)}px}}
.copy{{flex:1 1 {int(width*0.50)}px;display:flex;flex-direction:column;align-items:flex-start;gap:{int(fs*0.34)}px}}
.badge{{display:inline-flex;align-items:center;gap:{int(badge_fs*0.6)}px;padding:{int(badge_fs*0.68)}px {int(badge_fs*1.1)}px;
  border-radius:999px;background:#111;color:#fff;font-size:{badge_fs}px;font-weight:800;letter-spacing:0.08em;
  box-shadow:0 10px 30px rgba(0,0,0,0.12)}}
.badge::before{{content:"";width:{int(badge_fs*0.65)}px;height:{int(badge_fs*0.65)}px;border-radius:50%;background:#ff8c42}}
.title{{max-width:{int(width*0.48)}px;font-size:{fs}px;font-weight:900;line-height:1.16;letter-spacing:-0.03em;color:#161616}}
.title .line{{white-space:nowrap}}
.subtitle{{max-width:{int(width*0.43)}px;font-size:{sub_fs}px;font-weight:600;line-height:1.45;color:#5d5d5d;letter-spacing:0.01em}}
.chips{{display:flex;gap:{int(chip_fs*0.55)}px;flex-wrap:wrap;margin-top:{int(chip_fs*0.2)}px}}
.chip{{display:inline-flex;align-items:center;padding:{int(chip_fs*0.55)}px {int(chip_fs*0.9)}px;border-radius:999px;
  background:rgba(17,17,17,0.06);font-size:{chip_fs}px;font-weight:700;color:#1d1d1d}}
.shotWrap{{flex:0 0 {int(width*0.39)}px;display:flex;justify-content:flex-end}}
.shot{{width:{int(width*0.39)}px;height:{int(height*0.63)}px;border-radius:{int(width*0.022)}px;
  background:linear-gradient(180deg, rgba(255,255,255,0.75), rgba(255,255,255,0.25));
  padding:{int(width*0.010)}px;box-shadow:0 24px 60px rgba(0,0,0,0.18);transform:rotate(4deg)}}
.shot img{{width:100%;height:100%;object-fit:cover;object-position:center;border-radius:{int(width*0.016)}px;
  filter:saturate(0.88) contrast(1.04) brightness(0.96)}}
.accent{{position:absolute;right:{int(width*0.06)}px;bottom:{int(height*0.08)}px;
  width:{int(width*0.14)}px;height:{int(width*0.14)}px;border-radius:50%;
  border:{max(3, int(width*0.0032))}px solid rgba(17,17,17,0.12)}}
</style></head><body><div class="bg">
  <div class="grid"></div>
  <div class="layout">
    <div class="copy">
      <div class="badge">OPENCLAW SKILL</div>
      <div class="title">{title_html}</div>
      {"<div class='subtitle'>" + sub_html + "</div>" if sub_html else ""}
      <div class="chips">
        <div class="chip">开源免费</div>
        <div class="chip">本地运行</div>
        <div class="chip">教程向</div>
      </div>
    </div>
    {preview_html}
  </div>
  <div class="accent"></div>
</div></body></html>"""


STYLE_BUILDERS = {
    "bold": _style_bold,
    "news": _style_news,
    "frame": _style_frame,
    "gradient": _style_gradient,
    "minimal": _style_minimal,
    "white": _style_white,
    "techcard": _style_techcard,
}


# ---------------------------------------------------------------------------
# Chrome screenshot
# ---------------------------------------------------------------------------

def chrome_screenshot(chrome_path, html_path, output_path, width, height):
    """Use headless Chrome to screenshot an HTML file."""
    extra_height = get_chrome_viewport_delta(chrome_path, width, height)
    screenshot_height = height + extra_height
    raw_output = output_path
    if extra_height:
        base, ext = os.path.splitext(output_path)
        raw_output = f"{base}_raw{ext}"

    cmd = [
        chrome_path, "--headless", "--disable-gpu", "--disable-software-rasterizer",
        "--no-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars",
        f"--screenshot={raw_output}", f"--window-size={width},{screenshot_height}",
        "--force-device-scale-factor=1", f"file://{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if not os.path.isfile(raw_output):
        raise RuntimeError(f"Chrome screenshot failed.\nstderr: {result.stderr[:500]}")
    if extra_height:
        crop_cmd = [
            "ffmpeg", "-y", "-i", raw_output,
            "-vf", f"crop={width}:{height}:0:0",
            "-frames:v", "1", output_path,
        ]
        crop_result = subprocess.run(crop_cmd, capture_output=True, text=True, timeout=30)
        if crop_result.returncode != 0 or not os.path.isfile(output_path):
            raise RuntimeError(f"Screenshot crop failed.\nstderr: {crop_result.stderr[:500]}")
        os.remove(raw_output)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_cover(video_path, title, output_path=None, width=None, height=None,
                   style="bold", subtitle=None, use_frame=False,
                   frame_timestamp=None):
    """Generate a cover image for a video.

    Args:
        video_path: Path to source video
        title: Main cover title text
        output_path: Output PNG path
        width, height: Override dimensions
        style: One of "bold", "news", "frame", "gradient", "minimal",
            "white", "techcard"
        subtitle: Optional secondary text line (yellow on news, gray on others)
        use_frame: Use video first frame as background (auto-enabled for "frame" style)
        frame_timestamp: Timestamp to extract a background frame from

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

    # Some styles depend on a frame background; others optionally use one.
    needs_frame = (style in {"frame", "techcard"}) or use_frame
    builder = STYLE_BUILDERS[style]

    tmp_dir = tempfile.mkdtemp(prefix="cover_")
    try:
        frame_b64 = None
        if needs_frame:
            frame_path = os.path.join(tmp_dir, "frame.png")
            extract_first_frame(video_path, frame_path, timestamp=frame_timestamp)
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
    parser.add_argument("--frame-timestamp", default=None,
                        help="Timestamp for background frame, e.g. 00:10:00 or 600")
    parser.add_argument("--output", default=None, help="Output PNG path")
    args = parser.parse_args()

    result = generate_cover(args.video_path, args.title, args.output,
                           style=args.style, subtitle=args.subtitle,
                           use_frame=args.use_frame,
                           frame_timestamp=args.frame_timestamp)
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
