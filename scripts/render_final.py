#!/usr/bin/env python3
"""
Single-pass video renderer. Selects segments, applies subtitles and cover
in ONE encoding pass — no intermediate re-encodes.

Usage:
  python3 render_final.py --config render_config.json --output final.mp4

The config JSON format:
{
  "clips": [
    {"video": "path/to/video1.MOV", "segment_id": 4, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video1.MOV", "segment_id": 5, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video2.MOV", "segment_id": 1, "transcript": "path/to/transcript2.json"}
  ],
  "title": "封面标题",
  "chapters": [
    {"title": "痛点", "start": 0.0, "end": 27.5},
    ...
  ]
}
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    find_chinese_font, get_video_info, get_ffmpeg_encode_args,
    escape_ffmpeg_path, sanitize_title, detect_gpu,
)
from burn_subtitles import (
    detect_language, escape_ass_text, wrap_subtitle_text,
)
from generate_cover_image import generate_cover as generate_cover_image
from _internal_text_guard import check_visible_text
from content_guard import enforce as enforce_platform_rules, HardBlock

# --- Caption style presets ---
CAPTION_PRESETS = {
    "normal": {
        "primary": "&H00FFFFFF", "outline": "&H00000000",
        "outline_w": 3, "shadow": 1, "bold": 1,
    },
    "bold_pop": {
        "primary": "&H00FFFFFF", "outline": "&H00000000",
        "outline_w": 6, "shadow": 3, "bold": 1,
    },
    "neon": {
        "primary": "&H00FFFF00", "outline": "&H00FF00FF",
        "outline_w": 4, "shadow": 0, "bold": 1,
    },
    "minimal": {
        "primary": "&H00FFFFFF", "outline": "&H00000000",
        "outline_w": 0, "shadow": 2, "bold": 0,
    },
    "yellow_pop": {
        "primary": "&H0000FFFF", "outline": "&H00000000",
        "outline_w": 4, "shadow": 1, "bold": 1,
    },
}

# --- Multi-platform output formats ---
OUTPUT_FORMATS = {
    "vertical":   {"width": 1080, "height": 1920, "label": "9:16 (抖音/小红书/TikTok)"},
    "square":     {"width": 1080, "height": 1080, "label": "1:1 (Instagram)"},
    "horizontal": {"width": 1920, "height": 1080, "label": "16:9 (YouTube/B站)"},
}

VERSION_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)(?:_V(?P<version>\d+))?$")


def build_reformat_filter(src_w, src_h, dst_w, dst_h):
    """Build ffmpeg filter to reformat video dimensions via center-crop."""
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if abs(src_ratio - dst_ratio) < 0.01:
        return f"scale={dst_w}:{dst_h}"
    elif src_ratio > dst_ratio:
        return f"scale=-1:{dst_h},crop={dst_w}:{dst_h}"
    else:
        return f"scale={dst_w}:-1,crop={dst_w}:{dst_h}"


def _version_family(path):
    """Return (directory, base stem, extension) for a versioned output family."""
    directory, filename = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(filename)
    match = VERSION_SUFFIX_RE.match(stem)
    if match:
        stem = match.group("stem")
    return directory, stem, ext


def next_versioned_output_path(path):
    """Return the next available `<stem>_V<N><ext>` path without overwriting."""
    directory, stem, ext = _version_family(path)
    os.makedirs(directory or ".", exist_ok=True)

    max_version = 0
    if os.path.isdir(directory):
        pattern = re.compile(rf"^{re.escape(stem)}_V(?P<version>\d+){re.escape(ext)}$")
        for name in os.listdir(directory):
            match = pattern.match(name)
            if match:
                max_version = max(max_version, int(match.group("version")))

    return os.path.join(directory, f"{stem}_V{max_version + 1}{ext}")


def load_config(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)


def load_enrich_plan(plan_path):
    try:
        with open(plan_path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {plan_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Enrich plan not found: {plan_path}", file=sys.stderr)
        sys.exit(1)


def _resolve_plan_path(path, base_dir):
    if not path:
        return None
    if os.path.isabs(path):
        return path
    candidate = os.path.abspath(os.path.join(base_dir, path))
    if os.path.exists(candidate):
        return candidate
    return os.path.abspath(path)


def _float_or_default(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_or_default(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _bounded_float(value, default, *, min_value=None, max_value=None):
    parsed = _float_or_default(value, default)
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _cue_time_range(cue, *, start_key="start", end_key="end", duration_key="duration", default_duration=1.0):
    start = _float_or_default(cue.get(start_key), 0.0)
    if end_key in cue:
        end = _float_or_default(cue.get(end_key), start + default_duration)
    else:
        duration = _float_or_default(cue.get(duration_key), default_duration)
        end = start + duration
    if end <= start:
        end = start + default_duration
    return start, end


def build_bgm_mix_filter_ops(
    *,
    bgm_input_idx,
    voice_label,
    bgm_total,
    bgm_volume,
    bgm_fade_in=0.0,
    bgm_fade_out=0.0,
    ducking=False,
    duck_threshold=0.03,
    duck_ratio=8.0,
    duck_attack=20.0,
    duck_release=250.0,
    duck_makeup=1.0,
):
    """Return filter_complex lines that prepare BGM and mix it with speech.

    When ducking is enabled, the speech track is split so one copy remains the
    audible voice while the other copy sidechains the BGM compressor.
    """
    bgm_total = _bounded_float(bgm_total, 0.0, min_value=0.05)
    bgm_volume = _bounded_float(bgm_volume, 0.15, min_value=0.0, max_value=1.0)
    bgm_fade_in = _bounded_float(bgm_fade_in, 0.0, min_value=0.0)
    bgm_fade_out = _bounded_float(bgm_fade_out, 0.0, min_value=0.0)

    lines = []
    bgm_filters = [
        "aloop=loop=-1:size=2147483647",
        f"atrim=duration={bgm_total:.4f}",
        "asetpts=PTS-STARTPTS",
        f"volume={bgm_volume:.3f}",
    ]
    if bgm_fade_in > 0:
        bgm_filters.append(f"afade=t=in:st=0:d={min(bgm_fade_in, bgm_total):.4f}")
    if bgm_fade_out > 0:
        fade_start = max(0, bgm_total - bgm_fade_out)
        bgm_filters.append(
            f"afade=t=out:st={fade_start:.4f}:d={min(bgm_fade_out, bgm_total):.4f}"
        )

    bgm_base_label = "[bgm_base]" if ducking else "[bgm_a]"
    lines.append(f"[{bgm_input_idx}:a]{','.join(bgm_filters)}{bgm_base_label}")

    if ducking:
        duck_threshold = _bounded_float(duck_threshold, 0.03, min_value=0.0001, max_value=1.0)
        duck_ratio = _bounded_float(duck_ratio, 8.0, min_value=1.0, max_value=20.0)
        duck_attack = _bounded_float(duck_attack, 20.0, min_value=0.01, max_value=2000.0)
        duck_release = _bounded_float(duck_release, 250.0, min_value=0.01, max_value=9000.0)
        duck_makeup = _bounded_float(duck_makeup, 1.0, min_value=1.0, max_value=64.0)
        lines.append(f"{voice_label}asplit=2[voice_mix][voice_sc]")
        lines.append(
            "[bgm_base][voice_sc]"
            f"sidechaincompress=threshold={duck_threshold:.4f}:"
            f"ratio={duck_ratio:.2f}:"
            f"attack={duck_attack:.2f}:"
            f"release={duck_release:.2f}:"
            f"makeup={duck_makeup:.2f}"
            "[bgm_a]"
        )
        lines.append(
            "[voice_mix][bgm_a]amix=inputs=2:duration=first:dropout_transition=0[final_a]"
        )
    else:
        lines.append(
            f"{voice_label}[bgm_a]amix=inputs=2:duration=first:dropout_transition=0[final_a]"
        )

    return lines, "[final_a]"


def merge_enrich_plan(config, plan, *, plan_base_dir):
    """Merge auto_enrich.py output into a render config.

    The plan is additive and non-destructive: existing render_config fields stay
    intact, while plan cues are translated to render_final-native overlays.
    """
    merged = copy.deepcopy(config)
    stats = {
        "broll_overlays": 0,
        "text_badges": 0,
        "chapters": 0,
        "image_overlays": 0,
        "focus_events": 0,
        "missing_broll_assets": 0,
        "missing_image_assets": 0,
        "advisory_imagegen": 0,
    }

    text_badges = list(merged.get("text_badges") or [])
    chapters = list(merged.get("chapters") or [])
    broll_overlays = list(merged.get("broll_overlays") or [])
    image_overlays = list(merged.get("image_overlays") or [])
    focus_events = list(merged.get("focus_events") or [])

    for cue in plan.get("broll") or []:
        asset = (
            cue.get("suggested_asset")
            or cue.get("asset")
            or cue.get("path")
            or cue.get("video")
        )
        asset_path = _resolve_plan_path(asset, plan_base_dir)
        if not asset_path or not os.path.isfile(asset_path):
            stats["missing_broll_assets"] += 1
            continue
        start, end = _cue_time_range(cue, default_duration=2.0)
        broll_overlays.append({
            "video": asset_path,
            "start": start,
            "end": end,
            "source_start": _float_or_default(
                cue.get("source_start", cue.get("broll_start", 0.0)), 0.0,
            ),
            "reason": cue.get("reason"),
        })
        stats["broll_overlays"] += 1

    for cue in plan.get("chapter_cards") or []:
        title = (cue.get("title") or "").strip()
        if not title:
            continue
        start, end = _cue_time_range(cue, default_duration=1.0)
        chapters.append({"title": title, "start": start, "end": end})
        stats["chapters"] += 1

        image_path = _resolve_plan_path(
            cue.get("png") or cue.get("image_path") or cue.get("asset_path"),
            plan_base_dir,
        )
        if image_path and os.path.isfile(image_path):
            image_overlays.append({
                "image": image_path,
                "start": start,
                "end": end,
                "fit": "cover",
                "reason": "chapter-card",
            })
            stats["image_overlays"] += 1
        else:
            text_badges.append({
                "text": title,
                "start": start,
                "end": end,
                "fade_in": 180,
                "fade_out": 220,
                "source": "enrich_plan:chapter_card",
            })
            stats["text_badges"] += 1

    for cue in plan.get("stickers") or []:
        sticker = (cue.get("sticker") or "").strip()
        if not sticker:
            continue
        start, end = _cue_time_range(cue, default_duration=1.4)
        text_badges.append({
            "text": sticker,
            "start": start,
            "end": end,
            "fade_in": 120,
            "fade_out": 120,
            "source": "enrich_plan:sticker",
            "emotion": cue.get("emotion"),
        })
        stats["text_badges"] += 1

    for cue in plan.get("focus_events") or []:
        start, end = _cue_time_range(cue, start_key="start", default_duration=1.2)
        focus_event = copy.deepcopy(cue)
        focus_event["start"] = start
        focus_event["end"] = end
        focus_events.append(focus_event)
        stats["focus_events"] += 1

        label = str(focus_event.get("label") or "").strip()
        if label and focus_event.get("show_label", True):
            text_badges.append({
                "text": label,
                "start": start,
                "end": end,
                "fade_in": 100,
                "fade_out": 140,
                "source": "enrich_plan:screen_focus",
            })
            stats["text_badges"] += 1

    for cue in plan.get("imagegen") or []:
        image_path = _resolve_plan_path(
            cue.get("image_path")
            or cue.get("generated_path")
            or cue.get("asset_path")
            or cue.get("path"),
            plan_base_dir,
        )
        start = _float_or_default(cue.get("timing_seconds", cue.get("start")), 0.0)
        duration = _float_or_default(cue.get("duration"), 2.5)
        if image_path and os.path.isfile(image_path):
            image_overlays.append({
                "image": image_path,
                "start": start,
                "end": start + duration,
                "fit": "cover",
                "reason": cue.get("reason", "imagegen"),
            })
            stats["image_overlays"] += 1
        else:
            stats["advisory_imagegen"] += 1
            if image_path:
                stats["missing_image_assets"] += 1

    if text_badges:
        merged["text_badges"] = text_badges
    if chapters:
        merged["chapters"] = chapters
    if broll_overlays:
        merged["broll_overlays"] = broll_overlays
    if image_overlays:
        merged["image_overlays"] = image_overlays
    if focus_events:
        merged["focus_events"] = focus_events
    merged["_enrich_plan_stats"] = stats
    return merged


def resolve_clips(config):
    """Resolve clip entries to (video_path, start, end, text) tuples."""
    transcript_cache = {}
    clips = []
    errors = []
    for i, entry in enumerate(config["clips"]):
        video = os.path.abspath(entry["video"])
        transcript = os.path.abspath(entry["transcript"])
        seg_id = entry["segment_id"]

        if not os.path.isfile(video):
            errors.append(f"Clip #{i+1}: video not found: {video}")
            continue
        if not os.path.isfile(transcript):
            errors.append(f"Clip #{i+1}: transcript not found: {transcript}")
            continue

        if transcript not in transcript_cache:
            with open(transcript, encoding="utf-8") as f:
                data = json.load(f)
            transcript_cache[transcript] = {s["id"]: s for s in data["segments"]}

        if seg_id not in transcript_cache[transcript]:
            errors.append(f"Clip #{i+1}: segment_id {seg_id} not found in {os.path.basename(transcript)}")
            continue

        seg = transcript_cache[transcript][seg_id]
        resolved = {
            "video": video,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        if "words" in seg:
            resolved["words"] = seg["words"]
        if "broll" in entry:
            resolved["broll"] = os.path.abspath(entry["broll"])
            resolved["broll_start"] = entry.get("broll_start", 0.0)
        clips.append(resolved)

    if errors:
        print("Config validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    return clips


def build_merged_ass(clips, font_name, font_size, video_width, video_height,
                     speed=1.0, cover_duration=0.0, end_cards=None,
                     subtitle_style="normal", text_badges=None):
    """Build a single ASS subtitle file covering the entire merged timeline.

    Args:
        cover_duration: Seconds of cover at the start; subtitles begin after this.
        end_cards: List of {"text": str, "duration": float} for ending cards.
        subtitle_style: Caption preset name (normal/bold_pop/neon/minimal/yellow_pop).
    """
    margin_lr = 60
    usable_width = video_width - 2 * margin_lr
    margin_v = int(video_height * 0.28)
    end_card_fs = int(font_size * 1.4)

    # Apply caption preset
    preset = CAPTION_PRESETS.get(subtitle_style, CAPTION_PRESETS["normal"])
    p_color = preset["primary"]
    o_color = preset["outline"]
    o_width = preset["outline_w"]
    s_depth = preset["shadow"]
    bold = preset["bold"]

    def fmt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    header = f"""[Script Info]
Title: Merged Subtitles
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{p_color},&H000000FF,{o_color},&H80000000,{bold},0,0,0,100,100,0,0,1,{o_width},{s_depth},2,{margin_lr},{margin_lr},{margin_v},1
Style: EndCard,{font_name},{end_card_fs},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,0,0,0,5,{margin_lr},{margin_lr},0,1
Style: Badge,{font_name},{int(font_size * 1.2)},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,2,0,3,4,0,5,{margin_lr},{margin_lr},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    offset = cover_duration  # Start subtitles after cover
    for clip in clips:
        dur = clip["end"] - clip["start"]
        text = clip["text"]
        lang = detect_language(text)

        if lang == "zh":
            max_chars = int(usable_width / font_size)
        else:
            max_chars = int(usable_width / (font_size * 0.55))

        wrapped = wrap_subtitle_text(text, max_chars, lang)
        escaped = escape_ass_text(wrapped)

        scaled_dur = dur / speed
        start_t = fmt_time(offset)
        end_t = fmt_time(offset + scaled_dur)
        dialogues.append(f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{escaped}")
        offset += scaled_dur

    # Text badges: timed text displayed at screen center (e.g. "开源免费")
    if text_badges:
        for badge in text_badges:
            b_start = badge["start"] / speed + cover_duration
            b_end = badge["end"] / speed + cover_duration
            b_text = escape_ass_text(badge["text"]).replace("\n", "\\N")
            fade_in = badge.get("fade_in", 200)
            fade_out = badge.get("fade_out", 200)
            start_t = fmt_time(b_start)
            end_t = fmt_time(b_end)
            dialogues.append(
                f"Dialogue: 1,{start_t},{end_t},Badge,,0,0,0,,{{\\fad({fade_in},{fade_out})}}{b_text}"
            )

    # End cards: centered text on black screen with fade
    end_cards_duration = 0.0
    if end_cards:
        for card in end_cards:
            card_text = card["text"]
            card_dur = card.get("duration", 3.0)
            fade_in = 300   # ms
            fade_out = 300  # ms
            start_t = fmt_time(offset)
            end_t = fmt_time(offset + card_dur)
            escaped = escape_ass_text(card_text)
            escaped = escaped.replace("\n", "\\N")
            dialogues.append(
                f"Dialogue: 0,{start_t},{end_t},EndCard,,0,0,0,,{{\\fad({fade_in},{fade_out})}}{escaped}"
            )
            offset += card_dur
            end_cards_duration += card_dur

    return header + "\n".join(dialogues) + "\n", offset, end_cards_duration


def _ass_color(hex_color):
    """Convert '#RRGGBB' or '#AARRGGBB' to ASS '&HAABBGGRR' format."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}"
    elif len(h) == 8:
        a, r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"
    return "&H0000FFFF"  # fallback yellow


def _build_karaoke_line(clip, seg_start):
    """Build ASS karaoke text from word timestamps.

    Uses \\kf (smooth fill) for each word. If word timestamps are missing,
    falls back to even distribution across characters.
    """
    words = clip.get("words")
    text = clip["text"]
    seg_duration = clip["end"] - clip["start"]

    if words:
        parts = []
        prev_end = 0.0  # relative to segment start
        for w in words:
            word_rel_end = w["end"] - seg_start
            # Duration from previous word end to this word's end (in centiseconds)
            kf_cs = max(1, round((word_rel_end - prev_end) * 100))
            escaped = escape_ass_text(w["word"])
            parts.append(f"{{\\kf{kf_cs}}}{escaped}")
            prev_end = word_rel_end
        return "".join(parts)
    else:
        # Fallback: distribute evenly across characters
        chars = list(text)
        if not chars:
            return escape_ass_text(text)
        per_char_cs = max(1, round(seg_duration * 100 / len(chars)))
        parts = [f"{{\\kf{per_char_cs}}}{escape_ass_text(c)}" for c in chars]
        return "".join(parts)


def build_karaoke_ass(clips, font_name, font_size, video_width, video_height,
                      speed=1.0, cover_duration=0.0, end_cards=None,
                      highlight_color="#FFFF00", base_color="#FFFFFF",
                      base_alpha="80", text_badges=None):
    """Build ASS subtitle file with karaoke word-by-word highlighting.

    Uses ASS \\kf tags: text starts in SecondaryColour (base/dim) and fills
    to PrimaryColour (highlight) as each word is spoken.

    Args:
        highlight_color: Hex color for the active/highlighted word (default yellow).
        base_color: Hex color for words not yet spoken (default white).
        base_alpha: Alpha hex for base color (00=opaque, FF=transparent, default 80=semi).
    """
    margin_lr = 60
    margin_v = int(video_height * 0.28)
    end_card_fs = int(font_size * 1.4)

    # ASS colors: PrimaryColour = after karaoke fill, SecondaryColour = before fill
    primary = _ass_color(highlight_color)
    # Base color with alpha for "not yet spoken" dimmed look
    bh = base_color.lstrip("#")
    if len(bh) == 6:
        r, g, b = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
        secondary = f"&H{base_alpha}{b:02X}{g:02X}{r:02X}"
    else:
        secondary = f"&H{base_alpha}FFFFFF"

    def fmt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    header = f"""[Script Info]
Title: Karaoke Subtitles
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font_name},{font_size},{primary},{secondary},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1
Style: EndCard,{font_name},{end_card_fs},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,0,0,0,5,{margin_lr},{margin_lr},0,1
Style: Badge,{font_name},{int(font_size * 1.2)},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,2,0,3,4,0,5,{margin_lr},{margin_lr},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    offset = cover_duration

    for clip in clips:
        dur = clip["end"] - clip["start"]
        scaled_dur = dur / speed
        start_t = fmt_time(offset)
        end_t = fmt_time(offset + scaled_dur)

        karaoke_text = _build_karaoke_line(clip, clip["start"])
        dialogues.append(f"Dialogue: 0,{start_t},{end_t},Karaoke,,0,0,0,,{karaoke_text}")
        offset += scaled_dur

    if text_badges:
        for badge in text_badges:
            b_start = badge["start"] / speed + cover_duration
            b_end = badge["end"] / speed + cover_duration
            b_text = escape_ass_text(badge["text"]).replace("\n", "\\N")
            fade_in = badge.get("fade_in", 200)
            fade_out = badge.get("fade_out", 200)
            start_t = fmt_time(b_start)
            end_t = fmt_time(b_end)
            dialogues.append(
                f"Dialogue: 1,{start_t},{end_t},Badge,,0,0,0,,{{\\fad({fade_in},{fade_out})}}{b_text}"
            )

    # End cards (same as normal mode)
    end_cards_duration = 0.0
    if end_cards:
        for card in end_cards:
            card_text = card["text"]
            card_dur = card.get("duration", 3.0)
            start_t = fmt_time(offset)
            end_t = fmt_time(offset + card_dur)
            escaped = escape_ass_text(card_text).replace("\n", "\\N")
            dialogues.append(
                f"Dialogue: 0,{start_t},{end_t},EndCard,,0,0,0,,{{\\fad(300,300)}}{escaped}"
            )
            offset += card_dur
            end_cards_duration += card_dur

    return header + "\n".join(dialogues) + "\n", offset, end_cards_duration


def _clips_in_temporal_order(clips):
    """Check if all clips come from one video and are in temporal order.

    KNOWN ISSUE: This function does NOT check for broll fields. When all clips
    reference the same voiceover video but each has a different broll source,
    this returns True and the select filter path is used. The select filter
    ignores broll entirely, and with 100+ segments the between() expression
    can cause OOM. For voiceover-over-broll workflows with many segments,
    bypass render_final.py and use a manual ffmpeg pipeline instead.
    See SKILL.md "K3: render_final.py select filter OOM" for details.
    """
    videos = set(c["video"] for c in clips)
    if len(videos) != 1:
        return False
    # If any clip has broll, select filter won't handle it correctly
    if any("broll" in c for c in clips):
        return False
    for i in range(1, len(clips)):
        if clips[i]["start"] < clips[i - 1]["start"]:
            return False
    return True


def build_select_filter(clips, fps):
    """Build filter using select/aselect with between() expressions.

    Much simpler than trim/concat: one expression selects all segments,
    FFmpeg decodes the full source but only encodes selected frames.
    Only works for single-video, temporally-ordered clips.

    Returns (filter_str, input_files).
    """
    between_exprs = [
        f"between(t,{c['start']:.4f},{c['end']:.4f})" for c in clips
    ]
    select_expr = "+".join(between_exprs)

    filters = [
        f"[0:v]select='{select_expr}',setpts=N/{fps:.4f}/TB[merged_v]",
        f"[0:a]aselect='{select_expr}',asetpts=N/SR/TB[merged_a]",
    ]
    return ";\n".join(filters), [clips[0]["video"]]


def build_trim_filter(clips, target_w=None, target_h=None):
    """Build filter_complex string for trimming and concatenating clips.

    Fallback for multi-video or reordered clips where select filter
    cannot be used.

    Supports B-roll: clips with a "broll" key use video from the broll
    source but audio from the original source. B-roll is scaled/cropped
    to match target_w x target_h.

    Returns (filter_str, input_files).
    """
    # Deduplicate input files while preserving order
    input_files = []
    input_index = {}
    for clip in clips:
        for vpath in [clip["video"], clip.get("broll")]:
            if vpath and vpath not in input_index:
                input_index[vpath] = len(input_files)
                input_files.append(vpath)

    filters = []
    n = len(clips)
    concat_inputs = ""

    for i, clip in enumerate(clips):
        audio_idx = input_index[clip["video"]]
        broll = clip.get("broll")
        video_idx = input_index[broll] if broll else audio_idx
        s = clip["start"]
        e = clip["end"]
        dur = e - s

        if broll:
            broll_start = clip.get("broll_start", 0.0)
            broll_end = broll_start + dur
            filters.append(
                f"[{video_idx}:v]trim=start={broll_start:.4f}:end={broll_end:.4f},setpts=PTS-STARTPTS,scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}[v{i}]"
            )
        else:
            filters.append(
                f"[{video_idx}:v]trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS[v{i}]"
            )
        filters.append(
            f"[{audio_idx}:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs += f"[v{i}][a{i}]"

    filters.append(f"{concat_inputs}concat=n={n}:v=1:a=1[merged_v][merged_a]")

    return ";\n".join(filters), input_files


def _clamp(value, low, high):
    return max(low, min(high, value))


def _safe_filter_color(value):
    color = str(value or "red@0.85").strip()
    if re.match(r"^[A-Za-z0-9#@.]+$", color):
        return color
    return "red@0.85"


def normalize_focus_event(cue, *, default_duration=1.2, default_zoom=1.75):
    """Normalize a screen-focus cue for render-time filter construction."""
    start = _float_or_default(cue.get("start", cue.get("time", cue.get("timestamp"))), 0.0)
    if "end" in cue:
        end = _float_or_default(cue.get("end"), start + default_duration)
    else:
        end = start + _float_or_default(cue.get("duration"), default_duration)
    if end <= start:
        end = start + default_duration

    x = _float_or_default(cue.get("x", cue.get("norm_x", 0.5)), 0.5)
    y = _float_or_default(cue.get("y", cue.get("norm_y", 0.5)), 0.5)
    source_w = _float_or_default(cue.get("source_width"), 0.0)
    source_h = _float_or_default(cue.get("source_height"), 0.0)
    if (x > 1.0 or y > 1.0) and source_w > 0 and source_h > 0:
        x /= source_w
        y /= source_h

    zoom = _clamp(_float_or_default(cue.get("zoom"), default_zoom), 1.05, 4.0)
    return {
        "start": start,
        "end": end,
        "x": _clamp(x, 0.0, 1.0),
        "y": _clamp(y, 0.0, 1.0),
        "zoom": zoom,
        "transition": _clamp(_float_or_default(cue.get("transition"), 0.16), 0.0, 0.8),
        "marker": cue.get("marker", True),
        "marker_color": _safe_filter_color(cue.get("marker_color", "red@0.85")),
        "marker_size": _clamp(_float_or_default(cue.get("marker_size"), 0.13), 0.04, 0.35),
    }


def build_focus_filter_ops(
    current_v_label,
    focus_events,
    *,
    width,
    height,
    cover_duration,
    speed,
    stage_idx,
):
    """Build timed crop/scale overlays for click-focus zooms."""
    filter_lines = []
    label = current_v_label
    next_stage = stage_idx

    for cue in focus_events or []:
        event = normalize_focus_event(cue)
        start_out = cover_duration + event["start"] / speed
        end_out = cover_duration + event["end"] / speed
        if end_out <= start_out:
            continue

        crop_w = max(2, min(width, int(round(width / event["zoom"]))))
        crop_h = max(2, min(height, int(round(height / event["zoom"]))))
        center_x = event["x"] * width
        center_y = event["y"] * height
        crop_x = int(round(_clamp(center_x - crop_w / 2, 0, width - crop_w)))
        crop_y = int(round(_clamp(center_y - crop_h / 2, 0, height - crop_h)))
        focus_x = int(round((center_x - crop_x) * width / crop_w))
        focus_y = int(round((center_y - crop_y) * height / crop_h))

        base_label = f"[focus_base_{next_stage}]"
        src_label = f"[focus_src_{next_stage}]"
        zoom_label = f"[focus_zoom_{next_stage}]"
        out_label = f"[vstage{next_stage}]"
        filter_lines.append(f"{label}split=2{base_label}{src_label}")

        zoom_filters = [
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
            f"scale={width}:{height}:flags=lanczos",
            "setsar=1",
            "format=rgba",
        ]
        if event["marker"]:
            box = int(round(min(width, height) * event["marker_size"]))
            box = max(40, min(box, min(width, height)))
            box_x = int(round(_clamp(focus_x - box / 2, 0, width - box)))
            box_y = int(round(_clamp(focus_y - box / 2, 0, height - box)))
            thickness = max(4, int(round(box * 0.06)))
            zoom_filters.append(
                f"drawbox=x={box_x}:y={box_y}:w={box}:h={box}:"
                f"color={event['marker_color']}:t={thickness}"
            )
        duration = end_out - start_out
        transition = min(event["transition"], duration / 2)
        if transition > 0:
            zoom_filters.append(f"fade=t=in:st={start_out:.4f}:d={transition:.4f}:alpha=1")
            zoom_filters.append(
                f"fade=t=out:st={max(start_out, end_out - transition):.4f}:"
                f"d={transition:.4f}:alpha=1"
            )
        filter_lines.append(f"{src_label}{','.join(zoom_filters)}{zoom_label}")
        filter_lines.append(
            f"{base_label}{zoom_label}overlay=0:0:"
            f"enable='between(t,{start_out:.4f},{end_out:.4f})'{out_label}"
        )
        label = out_label
        next_stage += 1

    return filter_lines, label, next_stage


def generate_cover_png(video_path, title, width, height, temp_files,
                       style="bold", subtitle=None, use_frame=False):
    """Generate cover PNG using headless Chrome.

    Returns path to the cover PNG, or None if generation fails.
    """
    if not title:
        return None

    fd, cover_path = tempfile.mkstemp(suffix=".png", prefix="cover_")
    os.close(fd)
    temp_files.append(cover_path)

    result = generate_cover_image(
        video_path, title, output_path=cover_path,
        width=width, height=height, style=style,
        subtitle=subtitle, use_frame=use_frame,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Single-pass video renderer")
    parser.add_argument("--config", required=True, help="Path to render config JSON")
    parser.add_argument("--enrich-plan", action="append", default=[],
                        help="Optional enrich-plan JSON. Repeatable. Merges B-roll, "
                             "stickers, chapter cards, generated image cues, and "
                             "screen focus events into the render.")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--font-path", default=None, help="Custom font path")
    parser.add_argument("--font-size", type=int, default=48, help="Subtitle font size")
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--no-cover", action="store_true")
    parser.add_argument("--no-loudnorm", action="store_true",
                        help="Disable the default dynaudnorm+compressor+loudnorm chain on the speech track")
    parser.add_argument("--no-content-guard", action="store_true",
                        help="Disable the Xiaohongshu/RED platform-rule lint (not recommended)")
    parser.add_argument("--profile", default=None,
                        help="Audience profile (tech_pro, lifestyle, ...). Sets sensible defaults for cut/subtitle/audio.")
    parser.add_argument("--speed", nargs="*", type=float, default=[],
                        help="Additional speed variants to render (e.g. --speed 1.25 1.5)")
    parser.add_argument("--primary-speed", type=float, default=1.0,
                        help="Primary output speed (default 1.0). When set, the main output is "
                             "rendered at this speed instead of 1.0; --speed values become extra variants.")
    parser.add_argument("--versioned-output", action="store_true",
                        help="Write to the next <name>_V<N>.mp4 path instead of overwriting --output. "
                             "Can also be enabled with config versioned_output: true.")
    parser.add_argument("--cover-duration", type=float, default=None,
                        help="Cover freeze duration in seconds (default: from config or 2.0)")
    parser.add_argument("--cleanup", action="store_true", help="Remove temp files after render")
    parser.add_argument("--bgm", default=None,
                        help="Background music file path (overrides config)")
    parser.add_argument("--bgm-volume", type=float, default=None,
                        help="BGM volume 0.0-1.0 (default: from config or 0.15)")
    parser.add_argument("--bgm-fade-in", type=float, default=None,
                        help="BGM fade-in seconds (default: from config or 0)")
    parser.add_argument("--bgm-fade-out", type=float, default=None,
                        help="BGM fade-out seconds (default: from config or 3)")
    bgm_duck_group = parser.add_mutually_exclusive_group()
    bgm_duck_group.add_argument("--bgm-ducking", dest="bgm_ducking",
                                action="store_true",
                                help="Enable voice-aware BGM ducking via sidechain compression")
    bgm_duck_group.add_argument("--no-bgm-ducking", dest="bgm_ducking",
                                action="store_false",
                                help="Disable BGM ducking even if config enables it")
    parser.set_defaults(bgm_ducking=None)
    parser.add_argument("--bgm-duck-threshold", type=float, default=None,
                        help="BGM ducking sidechain threshold, 0.0001-1.0 (default: config or 0.03)")
    parser.add_argument("--bgm-duck-ratio", type=float, default=None,
                        help="BGM ducking compression ratio, 1-20 (default: config or 8)")
    parser.add_argument("--bgm-duck-attack", type=float, default=None,
                        help="BGM ducking attack in milliseconds (default: config or 20)")
    parser.add_argument("--bgm-duck-release", type=float, default=None,
                        help="BGM ducking release in milliseconds (default: config or 250)")
    parser.add_argument("--subtitle-style", default=None,
                        choices=["normal", "karaoke", "bold_pop", "neon", "minimal", "yellow_pop"],
                        help="Subtitle style (default: from config or 'normal')")
    parser.add_argument("--formats", nargs="*",
                        choices=list(OUTPUT_FORMATS.keys()),
                        help="Additional output formats: vertical, square, horizontal")
    args = parser.parse_args()

    config = load_config(args.config)
    for enrich_plan_path in args.enrich_plan:
        plan = load_enrich_plan(enrich_plan_path)
        config = merge_enrich_plan(
            config, plan, plan_base_dir=os.path.dirname(os.path.abspath(enrich_plan_path)),
        )
        stats = config.get("_enrich_plan_stats", {})
        print(
            f"[enrich] {os.path.basename(enrich_plan_path)} applied "
            f"broll={stats.get('broll_overlays', 0)}, "
            f"badges={stats.get('text_badges', 0)}, "
            f"chapters={stats.get('chapters', 0)}, "
            f"image_overlays={stats.get('image_overlays', 0)}, "
            f"focus={stats.get('focus_events', 0)}"
        )
        skipped = stats.get("missing_broll_assets", 0) + stats.get("missing_image_assets", 0)
        if skipped:
            print(f"[enrich] skipped missing media assets: {skipped}", file=sys.stderr)
        if stats.get("advisory_imagegen", 0):
            print(
                f"[enrich] advisory imagegen cues without generated files: "
                f"{stats['advisory_imagegen']}"
            )

    # Audience profile — overlays sensible defaults from scripts/profiles/<name>.yaml
    # onto fields the user didn't pass via CLI. The CLI / config always wins; profile
    # only fills in blanks.
    if args.profile:
        try:
            from profiles import load_profile
            prof = load_profile(args.profile)
            print(f"[profile] {args.profile} — {prof.get('audience', {}).get('name_zh', '')}")
            # Apply: subtitle font size if user kept the default
            if args.font_size == 48 and "subtitle" in prof:
                args.font_size = prof["subtitle"].get("font_size_at_1080p", args.font_size)
                print(f"  font_size := {args.font_size}")
        except (FileNotFoundError, ImportError) as exc:
            print(f"[profile] warning: {exc}", file=sys.stderr)

    # Guard visible-text fields BEFORE any expensive work — no speed/model/engine/debug
    # tokens may reach the frame. (User-facing content only; this catches accidents
    # like "DAY 58 — 1.25x".)
    check_visible_text(config.get("title"))
    check_visible_text(config.get("subtitle"))
    for chapter in config.get("chapters", []) or []:
        check_visible_text(chapter.get("title") if isinstance(chapter, dict) else chapter)
    for badge in config.get("text_badges", []) or []:
        check_visible_text(badge.get("text") if isinstance(badge, dict) else badge)
    for focus in config.get("focus_events", []) or []:
        check_visible_text(focus.get("label") if isinstance(focus, dict) else None)
    for card in config.get("end_cards", []) or []:
        check_visible_text(card.get("text") if isinstance(card, dict) else card)

    # Platform content-rule lint (Xiaohongshu/RED). Hard violations stop the render.
    if not args.no_content_guard:
        title_texts = [config.get("title") or "", config.get("subtitle") or ""]
        for ch in config.get("chapters", []) or []:
            title_texts.append(ch.get("title", "") if isinstance(ch, dict) else ch)
        try:
            enforce_platform_rules(title_texts, strict=True, context="title")
        except HardBlock as exc:
            print(f"\n🚫 Content guard refused export: {exc}", file=sys.stderr)
            print("   Override with --no-content-guard if you really mean it.", file=sys.stderr)
            sys.exit(2)

    clips = resolve_clips(config)

    if not clips:
        print("Error: No clips in config", file=sys.stderr)
        sys.exit(1)

    # Get video dimensions from first source
    first_video = clips[0]["video"]
    _, width, height, fps, _ = get_video_info(first_video)
    print(f"Video: {width}x{height}, {fps:.2f}fps")

    # Scale font size based on shorter side
    ref_dimension = min(width, height)
    font_size = int(args.font_size * ref_dimension / 1080)

    # Find font
    font_path, font_name = find_chinese_font(args.font_path)
    print(f"Font: {font_name}")

    # Subtitle style
    sub_style = args.subtitle_style or config.get("subtitle_style", "normal")
    if sub_style == "karaoke":
        has_words = any("words" in c for c in clips)
        print(f"Subtitles: karaoke (word-level highlight)")
        if not has_words:
            print("  Note: No word timestamps in transcript — using even distribution fallback")

    # --- Step 1: Build segment selection filter ---
    if _clips_in_temporal_order(clips):
        base_filter, input_files = build_select_filter(clips, fps)
        print(f"Using select filter: {len(clips)} segments from 1 video")
    else:
        base_filter, input_files = build_trim_filter(clips, target_w=width, target_h=height)
        print(f"Using trim/concat filter: {len(clips)} clips from {len(input_files)} video(s)")

    # Collect all speeds to render: primary first, then extras (no duplicates).
    # --primary-speed lets the main output be sped-up (or slowed down) directly,
    # without rendering a 1.0× version first. Day58 wanted 1.25× as the only output.
    primary = args.primary_speed
    all_speeds = [primary] + [s for s in args.speed if s != primary]

    total_duration = sum(c["end"] - c["start"] for c in clips)
    title = config.get("title", "")
    chapters = config.get("chapters", [])
    encode_args = get_ffmpeg_encode_args()
    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    if args.versioned_output or config.get("versioned_output"):
        requested_output = output_path
        output_path = next_versioned_output_path(output_path)
        print(f"[output] versioned: {requested_output} → {output_path}")
    temp_files = []
    failed_speeds = []

    # Cover duration: CLI arg > config > default 2.0 (0 if no title or --no-cover)
    if args.cover_duration is not None:
        cover_duration = args.cover_duration
    else:
        cover_duration = config.get("cover_duration", 2.0)
    if not title or args.no_cover:
        cover_duration = 0.0

    # Generate cover PNG once (reused across all speed variants)
    cover_png_path = None
    cover_style = config.get("cover_style", "bold")
    cover_subtitle = config.get("subtitle", None)
    use_frame = config.get("cover_use_frame", False)
    custom_cover = config.get("cover_image", None)
    if cover_duration > 0 and custom_cover and os.path.isfile(custom_cover):
        cover_png_path = os.path.abspath(custom_cover)
        print(f"Cover: {cover_duration:.1f}s freeze + custom image ({custom_cover})")
    elif cover_duration > 0 and title:
        cover_png_path = generate_cover_png(
            clips[0]["video"], title, width, height, temp_files,
            style=cover_style, subtitle=cover_subtitle, use_frame=use_frame,
        )
        if cover_png_path:
            print(f"Cover: {cover_duration:.1f}s freeze + Chrome-rendered overlay")
        else:
            print(f"Cover: {cover_duration:.1f}s freeze (no title overlay — Chrome not found)")

    # --- BGM config ---
    bgm_path = args.bgm or config.get("bgm")
    if bgm_path and os.path.isfile(bgm_path):
        bgm_path = os.path.abspath(bgm_path)
    elif bgm_path:
        print(f"Warning: BGM file not found: {bgm_path}", file=sys.stderr)
        bgm_path = None
    bgm_volume = args.bgm_volume if args.bgm_volume is not None else config.get("bgm_volume", 0.15)
    bgm_fade_in = args.bgm_fade_in if args.bgm_fade_in is not None else config.get("bgm_fade_in", 0.0)
    bgm_fade_out = args.bgm_fade_out if args.bgm_fade_out is not None else config.get("bgm_fade_out", 3.0)
    bgm_ducking = (
        args.bgm_ducking
        if args.bgm_ducking is not None
        else _bool_or_default(config.get("bgm_ducking"), False)
    )
    bgm_duck_threshold = (
        args.bgm_duck_threshold
        if args.bgm_duck_threshold is not None
        else config.get("bgm_duck_threshold", 0.03)
    )
    bgm_duck_ratio = (
        args.bgm_duck_ratio
        if args.bgm_duck_ratio is not None
        else config.get("bgm_duck_ratio", 8.0)
    )
    bgm_duck_attack = (
        args.bgm_duck_attack
        if args.bgm_duck_attack is not None
        else config.get("bgm_duck_attack", 20.0)
    )
    bgm_duck_release = (
        args.bgm_duck_release
        if args.bgm_duck_release is not None
        else config.get("bgm_duck_release", 250.0)
    )
    if bgm_path:
        mix_mode = "ducking" if bgm_ducking else "static"
        print(
            f"BGM: {os.path.basename(bgm_path)} "
            f"(volume={bgm_volume}, fade_in={bgm_fade_in}s, "
            f"fade_out={bgm_fade_out}s, mix={mix_mode})"
        )

    for idx, speed in enumerate(all_speeds):
        # The first speed in all_speeds is always the primary output (writes to
        # the requested --output path). Extras get a "_<speed>x" suffix.
        if idx == 0:
            out_path = output_path
            label = f"{speed}x"
        else:
            base, ext = os.path.splitext(output_path)
            speed_label = f"{speed}x".replace(".", "_")
            out_path = f"{base}_{speed_label}{ext}"
            label = f"{speed}x"

        effective_duration = total_duration / speed

        # --- Build subtitle ASS (scaled for speed, offset by cover duration) ---
        end_cards = config.get("end_cards", None)
        ass_path = None
        end_cards_duration = 0.0
        subtitle_style = args.subtitle_style or config.get("subtitle_style", "normal")
        if not args.no_subtitles:
            if subtitle_style == "karaoke":
                highlight_color = config.get("subtitle_highlight_color", "#FFFF00")
                base_color = config.get("subtitle_base_color", "#FFFFFF")
                base_alpha = config.get("subtitle_base_alpha", "80")
                ass_content, _, end_cards_duration = build_karaoke_ass(
                    clips, font_name, font_size, width, height,
                    speed=speed, cover_duration=cover_duration,
                    end_cards=end_cards,
                    highlight_color=highlight_color,
                    base_color=base_color,
                    base_alpha=base_alpha,
                    text_badges=config.get("text_badges"),
                )
            else:
                ass_content, _, end_cards_duration = build_merged_ass(
                    clips, font_name, font_size, width, height,
                    speed=speed, cover_duration=cover_duration,
                    end_cards=end_cards,
                    subtitle_style=subtitle_style,
                    text_badges=config.get("text_badges"),
                )
            fd, ass_path = tempfile.mkstemp(suffix=".ass", prefix=f"sub_{label}_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ass_content)
            temp_files.append(ass_path)

        # --- Build video filter chain on [merged_v] ---
        # Keep B-roll/image overlays before subtitles so cutaways do not cover
        # readable captions. Persistent HUD overlays still render last.
        pre_vf_parts = []

        # Speed adjustment (before cover padding so cover stays at normal speed)
        if speed != 1.0:
            pre_vf_parts.append(f"setpts=PTS/{speed}")

        # Cover: freeze first frame for cover_duration seconds
        if cover_duration > 0:
            pre_vf_parts.append(
                f"tpad=start_duration={cover_duration}:start_mode=clone"
            )

        # Subtitles (ASS timing already includes cover offset)
        subtitle_filter = None
        if ass_path:
            escaped_ass = escape_ffmpeg_path(ass_path)
            if font_path:
                fonts_dir = escape_ffmpeg_path(os.path.dirname(font_path))
                subtitle_filter = f"ass='{escaped_ass}':fontsdir='{fonts_dir}'"
            else:
                subtitle_filter = f"ass='{escaped_ass}'"

        # --- Build audio filter chain on [merged_a] ---
        af_parts = []
        if speed != 1.0:
            remaining = speed
            while remaining > 2.0:
                af_parts.append("atempo=2.0")
                remaining /= 2.0
            af_parts.append(f"atempo={remaining:.4f}")

        # Speech loudness chain (day58 lesson: after a speed change the mid section
        # got noticeably quieter; manual fix at the time was the same chain below).
        # Disable with --no-loudnorm for music-heavy or already-mastered tracks.
        if not args.no_loudnorm:
            af_parts.append("dynaudnorm=f=250:g=15")
            af_parts.append("acompressor=threshold=-18dB:ratio=3:attack=20:release=200")
            af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")

        # Audio: add silence for cover duration (after speed + loudness adjustment,
        # so the silent cover region stays at -inf dB instead of being normalised up)
        if cover_duration > 0:
            delay_ms = int(cover_duration * 1000)
            af_parts.append(f"adelay={delay_ms}:all=1")

        # Note: end cards silence is provided by anullsrc in the concat, no apad needed

        # --- Extra inputs tracking (order matters: must match -i order in cmd) ---
        extra_inputs = []  # list of (type, idx, path)

        bgm_input_idx = None
        if bgm_path:
            bgm_input_idx = len(input_files) + len(extra_inputs)
            extra_inputs.append(("bgm", bgm_input_idx, bgm_path))
            bgm_total = effective_duration + cover_duration + end_cards_duration

        cover_input_idx = None
        cover_overlay_op = None
        if cover_png_path and cover_duration > 0:
            cover_input_idx = len(input_files) + len(extra_inputs)
            extra_inputs.append(("cover", cover_input_idx, cover_png_path))
            cover_overlay_op = f"[cover_img]overlay=0:0:enable='lte(t,{cover_duration:.4f})'"

        overlay_input_idx = None
        persistent_overlay_op = None
        overlay_path = config.get("video_overlay")
        if overlay_path and os.path.isfile(overlay_path):
            overlay_input_idx = len(input_files) + len(extra_inputs)
            extra_inputs.append(("overlay", overlay_input_idx, os.path.abspath(overlay_path)))
            persistent_overlay_op = f"[overlay_img]overlay=0:0:enable='gt(t,{cover_duration:.4f})'"

        rec_blink = config.get("rec_blink")
        rec_dot_input_idx = None
        rec_dot_overlay_op = None
        if rec_blink:
            dot_path = rec_blink.get("dot_image")
            if dot_path and os.path.isfile(dot_path):
                rec_dot_input_idx = len(input_files) + len(extra_inputs)
                extra_inputs.append(("rec_dot", rec_dot_input_idx, os.path.abspath(dot_path)))
                bx = rec_blink.get("x", 62)
                by = rec_blink.get("y", 55)
                period = rec_blink.get("period", 1.0)
                half = period / 2
                rec_dot_overlay_op = (
                    f"[rec_dot]overlay={bx}:{by}:enable='if(gt(t,{cover_duration:.1f}),gte(mod(t,{period:.2f}),{half:.2f}),0)'"
                )

        timed_broll_inputs = []
        for cue in config.get("broll_overlays", []) or []:
            video_path = cue.get("video")
            if not video_path or not os.path.isfile(video_path):
                continue
            input_idx = len(input_files) + len(extra_inputs)
            extra_inputs.append(("broll", input_idx, os.path.abspath(video_path)))
            timed_broll_inputs.append((cue, input_idx))

        timed_image_inputs = []
        for cue in config.get("image_overlays", []) or []:
            image_path = cue.get("image")
            if not image_path or not os.path.isfile(image_path):
                continue
            input_idx = len(input_files) + len(extra_inputs)
            extra_inputs.append(("image_overlay", input_idx, os.path.abspath(image_path)))
            timed_image_inputs.append((cue, input_idx))

        # --- Assemble full filter_complex ---
        filter_lines = [base_filter]

        # End cards: concat black frames after merged video
        if end_cards_duration > 0:
            fps_val = fps
            filter_lines.append(
                f"color=c=black:s={width}x{height}:d={end_cards_duration:.4f}:r={fps_val:.4f}[black_v]"
            )
            filter_lines.append(
                f"anullsrc=r=48000:cl=stereo:d={end_cards_duration:.4f}[black_a]"
            )
            filter_lines.append(
                f"[merged_v][merged_a][black_v][black_a]concat=n=2:v=1:a=1[merged_v2][merged_a2]"
            )
            # Replace labels for downstream processing
            merged_v_label = "[merged_v2]"
            merged_a_label = "[merged_a2]"
        else:
            merged_v_label = "[merged_v]"
            merged_a_label = "[merged_a]"

        if cover_input_idx is not None:
            filter_lines.append(f"[{cover_input_idx}:v]scale={width}:{height},format=rgba[cover_img]")
        if overlay_input_idx is not None:
            filter_lines.append(f"[{overlay_input_idx}:v]scale={width}:{height},format=rgba[overlay_img]")
        if rec_dot_input_idx is not None:
            filter_lines.append(f"[{rec_dot_input_idx}:v]format=rgba[rec_dot]")

        current_v_label = merged_v_label
        video_stage_idx = 0

        if pre_vf_parts:
            pre_chain = ",".join(pre_vf_parts)
            filter_lines.append(f"{current_v_label}{pre_chain}[pre_v]")
            current_v_label = "[pre_v]"

        for ov_idx, (cue, input_idx) in enumerate(timed_broll_inputs):
            cue_start, cue_end = _cue_time_range(cue, default_duration=2.0)
            start_out = cover_duration + cue_start / speed
            end_out = cover_duration + cue_end / speed
            duration_out = max(0.05, end_out - start_out)
            source_start = _float_or_default(cue.get("source_start", cue.get("broll_start", 0.0)), 0.0)
            prep_label = f"broll_src_{ov_idx}"
            out_label = f"[vstage{video_stage_idx}]"
            filter_lines.append(
                f"[{input_idx}:v]trim=start={source_start:.4f}:duration={duration_out:.4f},"
                f"setpts=PTS-STARTPTS+{start_out:.4f}/TB,"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,format=rgba[{prep_label}]"
            )
            filter_lines.append(
                f"{current_v_label}[{prep_label}]overlay=0:0:"
                f"enable='between(t,{start_out:.4f},{end_out:.4f})'{out_label}"
            )
            current_v_label = out_label
            video_stage_idx += 1

        for ov_idx, (cue, input_idx) in enumerate(timed_image_inputs):
            cue_start, cue_end = _cue_time_range(cue, default_duration=2.5)
            start_out = cover_duration + cue_start / speed
            end_out = cover_duration + cue_end / speed
            prep_label = f"image_ov_{ov_idx}"
            out_label = f"[vstage{video_stage_idx}]"
            fit = cue.get("fit", "cover")
            if fit == "contain":
                image_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=rgba"
                )
            else:
                image_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},format=rgba"
                )
            filter_lines.append(f"[{input_idx}:v]{image_filter}[{prep_label}]")
            filter_lines.append(
                f"{current_v_label}[{prep_label}]overlay=0:0:"
                f"enable='between(t,{start_out:.4f},{end_out:.4f})'{out_label}"
            )
            current_v_label = out_label
            video_stage_idx += 1

        focus_lines, current_v_label, video_stage_idx = build_focus_filter_ops(
            current_v_label,
            config.get("focus_events"),
            width=width,
            height=height,
            cover_duration=cover_duration,
            speed=speed,
            stage_idx=video_stage_idx,
        )
        filter_lines.extend(focus_lines)

        if subtitle_filter:
            filter_lines.append(f"{current_v_label}{subtitle_filter}[sub_v]")
            current_v_label = "[sub_v]"

        for opart in [cover_overlay_op, persistent_overlay_op, rec_dot_overlay_op]:
            if not opart:
                continue
            out_label = f"[vstage{video_stage_idx}]"
            filter_lines.append(f"{current_v_label}{opart}{out_label}")
            current_v_label = out_label
            video_stage_idx += 1

        map_v = current_v_label

        if af_parts:
            af_chain = ",".join(af_parts)
            filter_lines.append(f"{merged_a_label}{af_chain}[voice_a]")
            voice_label = "[voice_a]"
        else:
            voice_label = merged_a_label

        # BGM: loop, trim, gain/fades, optional voice-aware ducking, then mix.
        if bgm_input_idx is not None:
            bgm_lines, map_a = build_bgm_mix_filter_ops(
                bgm_input_idx=bgm_input_idx,
                voice_label=voice_label,
                bgm_total=bgm_total,
                bgm_volume=bgm_volume,
                bgm_fade_in=bgm_fade_in,
                bgm_fade_out=bgm_fade_out,
                ducking=bgm_ducking,
                duck_threshold=bgm_duck_threshold,
                duck_ratio=bgm_duck_ratio,
                duck_attack=bgm_duck_attack,
                duck_release=bgm_duck_release,
            )
            filter_lines.extend(bgm_lines)
        else:
            map_a = voice_label

        full_filter = ";\n".join(filter_lines)

        # Write filter to temp file
        fd, filter_path = tempfile.mkstemp(suffix=".txt", prefix=f"fc_{label}_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(full_filter)
        temp_files.append(filter_path)

        # --- Single ffmpeg encode from source ---
        cmd = ["ffmpeg", "-y"]
        for inp in input_files:
            cmd.extend(["-i", inp])
        # Add extra inputs in tracked order
        for etype, eidx, epath in extra_inputs:
            cmd.extend(["-i", epath])
        cmd.extend([
            "-filter_complex_script", filter_path,
            "-map", map_v,
            "-map", map_a,
        ])
        cmd.extend(encode_args)
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
        cmd.append(out_path)

        total_out = effective_duration + cover_duration + end_cards_duration
        print(f"\nRendering {label} ({total_out:.0f}s)...")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg error ({label}):\n{e.stderr[-2000:]}", file=sys.stderr)
            failed_speeds.append(label)
            continue

        size_mb = os.path.getsize(out_path) / 1024 / 1024
        print(f"Done: {out_path} ({size_mb:.1f}MB)")

    # Report failures
    if failed_speeds:
        print(f"\nWARNING: Failed to render: {', '.join(failed_speeds)}", file=sys.stderr)

    # Print chapter timeline (for pasting into Xiaohongshu / YouTube etc.)
    if chapters:
        print("\n时间轴（可直接复制到小红书）:")
        for ch in chapters:
            t = ch["start"] + cover_duration
            m, s = divmod(t, 60)
            print(f"  {int(m)}:{int(s):02d} {ch.get('title', '')}")

    # --- Multi-platform format export ---
    base_output = output_path
    if args.formats and os.path.isfile(base_output):
        for fmt_name in args.formats:
            fmt = OUTPUT_FORMATS[fmt_name]
            fmt_output = base_output.replace(".mp4", f"_{fmt_name}.mp4")
            reformat = build_reformat_filter(width, height, fmt["width"], fmt["height"])
            fmt_cmd = [
                "ffmpeg", "-y", "-i", base_output,
                "-vf", reformat,
                "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-c:a", "copy",
                fmt_output,
            ]
            print(f"\nRendering {fmt['label']}...")
            try:
                subprocess.run(fmt_cmd, check=True, capture_output=True, text=True)
                size_mb = os.path.getsize(fmt_output) / 1024 / 1024
                print(f"Done: {fmt_output} ({size_mb:.1f}MB)")
            except subprocess.CalledProcessError as e:
                print(f"Format error ({fmt_name}):\n{e.stderr[-2000:]}", file=sys.stderr)

    # --- Cleanup ---
    for p in temp_files:
        if p and os.path.exists(p):
            os.remove(p)
    if temp_files:
        print("\nTemp files cleaned up.")


if __name__ == "__main__":
    main()
