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
import json
import os
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


def load_config(config_path):
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


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
                     speed=1.0, cover_duration=0.0, end_cards=None):
    """Build a single ASS subtitle file covering the entire merged timeline.

    Args:
        cover_duration: Seconds of cover at the start; subtitles begin after this.
        end_cards: List of {"text": str, "duration": float} for ending cards.
    """
    margin_lr = 60
    usable_width = video_width - 2 * margin_lr
    margin_v = int(video_height * 0.28)
    end_card_fs = int(font_size * 1.4)

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
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1
Style: EndCard,{font_name},{end_card_fs},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,0,0,0,5,{margin_lr},{margin_lr},0,1

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


def _clips_in_temporal_order(clips):
    """Check if all clips come from one video and are in temporal order."""
    videos = set(c["video"] for c in clips)
    if len(videos) != 1:
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
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--font-path", default=None, help="Custom font path")
    parser.add_argument("--font-size", type=int, default=48, help="Subtitle font size")
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--no-cover", action="store_true")
    parser.add_argument("--speed", nargs="*", type=float, default=[],
                        help="Additional speed variants to render (e.g. --speed 1.25 1.5)")
    parser.add_argument("--cover-duration", type=float, default=None,
                        help="Cover freeze duration in seconds (default: from config or 2.0)")
    parser.add_argument("--cleanup", action="store_true", help="Remove temp files after render")
    args = parser.parse_args()

    config = load_config(args.config)
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

    # --- Step 1: Build segment selection filter ---
    if _clips_in_temporal_order(clips):
        base_filter, input_files = build_select_filter(clips, fps)
        print(f"Using select filter: {len(clips)} segments from 1 video")
    else:
        base_filter, input_files = build_trim_filter(clips, target_w=width, target_h=height)
        print(f"Using trim/concat filter: {len(clips)} clips from {len(input_files)} video(s)")

    # Collect all speeds to render (1.0 = original, plus any extras)
    all_speeds = [1.0] + [s for s in args.speed if s != 1.0]

    total_duration = sum(c["end"] - c["start"] for c in clips)
    title = config.get("title", "")
    chapters = config.get("chapters", [])
    encode_args = get_ffmpeg_encode_args()
    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
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

    for speed in all_speeds:
        if speed == 1.0:
            out_path = output_path
            label = "1x"
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
        if not args.no_subtitles:
            ass_content, _, end_cards_duration = build_merged_ass(
                clips, font_name, font_size, width, height,
                speed=speed, cover_duration=cover_duration,
                end_cards=end_cards,
            )
            fd, ass_path = tempfile.mkstemp(suffix=".ass", prefix=f"sub_{label}_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ass_content)
            temp_files.append(ass_path)

        # --- Build video filter chain on [merged_v] ---
        vf_parts = []

        # Speed adjustment (before cover padding so cover stays at normal speed)
        if speed != 1.0:
            vf_parts.append(f"setpts=PTS/{speed}")

        # Cover: freeze first frame for cover_duration seconds
        if cover_duration > 0:
            vf_parts.append(
                f"tpad=start_duration={cover_duration}:start_mode=clone"
            )

        # Subtitles (ASS timing already includes cover offset)
        if ass_path:
            escaped_ass = escape_ffmpeg_path(ass_path)
            if font_path:
                fonts_dir = escape_ffmpeg_path(os.path.dirname(font_path))
                vf_parts.append(f"ass='{escaped_ass}':fontsdir='{fonts_dir}'")
            else:
                vf_parts.append(f"ass='{escaped_ass}'")

        # --- Build audio filter chain on [merged_a] ---
        af_parts = []
        if speed != 1.0:
            remaining = speed
            while remaining > 2.0:
                af_parts.append("atempo=2.0")
                remaining /= 2.0
            af_parts.append(f"atempo={remaining:.4f}")

        # Audio: add silence for cover duration (after speed adjustment)
        if cover_duration > 0:
            delay_ms = int(cover_duration * 1000)
            af_parts.append(f"adelay={delay_ms}:all=1")

        # Note: end cards silence is provided by anullsrc in the concat, no apad needed

        # --- Cover PNG overlay ---
        # If we have a cover PNG, add it as an overlay on the tpad-frozen frame
        cover_input_idx = None
        if cover_png_path and cover_duration > 0:
            cover_input_idx = len(input_files)  # index of cover PNG input
            # Overlay the cover PNG during the cover_duration period
            vf_parts.append(
                f"[cover_img]overlay=0:0:enable='lte(t,{cover_duration:.4f})'")

        # --- Persistent video overlay (e.g. badges, watermarks) ---
        overlay_input_idx = None
        overlay_path = config.get("video_overlay")
        if overlay_path and os.path.isfile(overlay_path):
            overlay_input_idx = len(input_files) + (1 if cover_input_idx is not None else 0)
            vf_parts.append(
                f"[overlay_img]overlay=0:0:enable='gt(t,{cover_duration:.4f})'")

        # --- REC dot blink (blinking dot near DAY badge, like a camcorder) ---
        # Uses a small dot PNG overlaid with alternating enable expression
        rec_blink = config.get("rec_blink")
        rec_dot_input_idx = None
        if rec_blink:
            dot_path = rec_blink.get("dot_image")
            if dot_path and os.path.isfile(dot_path):
                extra_inputs = sum(1 for x in [cover_input_idx, overlay_input_idx] if x is not None)
                rec_dot_input_idx = len(input_files) + extra_inputs
                bx = rec_blink.get("x", 62)
                by = rec_blink.get("y", 55)
                period = rec_blink.get("period", 1.0)
                half = period / 2
                vf_parts.append(
                    f"[rec_dot]overlay={bx}:{by}:enable='if(gt(t,{cover_duration:.1f}),gte(mod(t,{period:.2f}),{half:.2f}),0)'"
                )

        # --- Assemble full filter_complex ---
        filter_lines = [base_filter]

        # End cards: concat black frames after merged video
        if end_cards_duration > 0:
            fps_val = fps if speed == 1.0 else fps
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

        if vf_parts:
            # Count how many overlay operations we have at the end
            overlay_count = sum(1 for x in [cover_input_idx, overlay_input_idx, rec_dot_input_idx] if x is not None)
            if overlay_count > 0:
                pre_parts = vf_parts[:-overlay_count]
                overlay_parts = vf_parts[-overlay_count:]

                current_label = merged_v_label
                if pre_parts:
                    pre_chain = ",".join(pre_parts)
                    filter_lines.append(f"{current_label}{pre_chain}[pre_v]")
                    current_label = "[pre_v]"

                for oi, opart in enumerate(overlay_parts):
                    out_label = f"[ov{oi}]" if oi < len(overlay_parts) - 1 else "[final_v]"
                    filter_lines.append(f"{current_label}{opart}{out_label}")
                    current_label = out_label
            else:
                vf_chain = ",".join(vf_parts)
                filter_lines.append(f"{merged_v_label}{vf_chain}[final_v]")
            map_v = "[final_v]"
        else:
            map_v = merged_v_label

        if af_parts:
            af_chain = ",".join(af_parts)
            filter_lines.append(f"{merged_a_label}{af_chain}[final_a]")
            map_a = "[final_a]"
        else:
            map_a = merged_a_label

        # Add cover image scaling/labeling if needed
        if cover_input_idx is not None:
            cover_prep = f"[{cover_input_idx}:v]scale={width}:{height},format=rgba[cover_img]"
            filter_lines.insert(1, cover_prep)

        # Add persistent overlay image scaling/labeling if needed
        if overlay_input_idx is not None:
            overlay_prep = f"[{overlay_input_idx}:v]scale={width}:{height},format=rgba[overlay_img]"
            filter_lines.insert(1 + (1 if cover_input_idx is not None else 0), overlay_prep)

        # Add REC dot image prep if needed
        if rec_dot_input_idx is not None:
            prep_idx = 1 + sum(1 for x in [cover_input_idx, overlay_input_idx] if x is not None)
            dot_prep = f"[{rec_dot_input_idx}:v]format=rgba[rec_dot]"
            filter_lines.insert(prep_idx, dot_prep)

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
        # Add cover PNG as input if available
        if cover_input_idx is not None:
            cmd.extend(["-i", cover_png_path])
        # Add persistent overlay PNG as input if available
        if overlay_input_idx is not None:
            cmd.extend(["-i", os.path.abspath(overlay_path)])
        # Add REC dot PNG as input if available
        if rec_dot_input_idx is not None:
            cmd.extend(["-i", os.path.abspath(rec_blink["dot_image"])])
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

    # --- Cleanup ---
    for p in temp_files:
        if p and os.path.exists(p):
            os.remove(p)
    if temp_files:
        print("\nTemp files cleaned up.")


if __name__ == "__main__":
    main()
