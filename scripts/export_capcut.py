#!/usr/bin/env python3
"""
Export render_config.json to a JianYing (CapCut) draft project folder.

Generates a complete JianYing-compatible project that can be opened directly
in JianYing (剪映) for further editing and export. This avoids the need
for ffmpeg rendering — just copy the output folder into your JianYing
drafts directory and open it.

Usage:
  python3 export_capcut.py --config render_config.json --output ./my_draft

The output folder will contain:
  - draft_content.json   (main project file)
  - draft_meta_info.json (project metadata)

Then copy the folder into your JianYing drafts directory:
  - macOS: ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/
  - Windows: %APPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft/
"""

import argparse
import json
import os
import sys
import time as time_mod
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info

# 1 second = 1,000,000 microseconds
SEC = 1_000_000


def new_id():
    """Generate a UUID hex string (no dashes) for JianYing IDs."""
    return uuid.uuid4().hex.upper()


def make_timerange(start_us, duration_us):
    """Create a JianYing timerange dict."""
    return {"start": int(start_us), "duration": int(duration_us)}


def load_config(config_path):
    """Load render_config.json."""
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_clips(config):
    """Resolve clip entries to list of dicts with video, start, end, text, words."""
    transcript_cache = {}
    clips = []
    for entry in config["clips"]:
        video = os.path.abspath(entry["video"])
        transcript = os.path.abspath(entry["transcript"])
        seg_id = entry["segment_id"]

        if transcript not in transcript_cache:
            with open(transcript, encoding="utf-8") as f:
                data = json.load(f)
            transcript_cache[transcript] = {s["id"]: s for s in data["segments"]}

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
    return clips


def make_video_material(video_path, duration_us, width, height):
    """Create a video material entry for materials.videos."""
    mid = new_id()
    return {
        "id": mid,
        "local_material_id": mid,
        "material_id": mid,
        "material_name": os.path.basename(video_path),
        "path": video_path,
        "type": "video",
        "duration": int(duration_us),
        "width": width,
        "height": height,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "audio_fade": None,
        "media_path": "",
    }


def make_audio_material(audio_path, duration_us):
    """Create an audio material entry for materials.audios."""
    mid = new_id()
    return {
        "id": mid,
        "local_material_id": mid,
        "music_id": mid,
        "name": os.path.basename(audio_path),
        "path": audio_path,
        "type": "extract_music",
        "duration": int(duration_us),
        "category_id": "",
        "category_name": "local",
        "check_flag": 3,
        "copyright_limit_type": "none",
        "effect_id": "",
        "formula_id": "",
        "app_id": 0,
        "source_platform": 0,
        "wave_points": [],
    }


def make_text_material(text, font_size=8.0, color=None, bold=False,
                       outline=True, mat_type="subtitle"):
    """Create a text material entry for materials.texts.

    Args:
        text: The subtitle/text content.
        font_size: Normalized font size (default 8.0).
        color: RGB list [r, g, b] normalized 0-1 (default white).
        bold: Whether text is bold.
        outline: Whether to add black outline stroke.
        mat_type: "subtitle" or "text".
    """
    if color is None:
        color = [1.0, 1.0, 1.0]
    mid = new_id()
    style = {
        "fill": {
            "content": {
                "render_type": "solid",
                "solid": {"color": color},
            }
        },
        "range": [0, len(text)],
        "size": font_size,
        "bold": bold,
        "italic": False,
        "underline": False,
    }
    check_flag = 7
    if outline:
        style["strokes"] = [{
            "content": {
                "render_type": "solid",
                "solid": {"color": [0.0, 0.0, 0.0]},
            },
            "width": 0.04,
        }]
        check_flag |= 8

    content_json = json.dumps({"text": text, "styles": [style]},
                              ensure_ascii=False)
    return {
        "id": mid,
        "type": mat_type,
        "content": content_json,
        "alignment": 1,
        "typesetting": 0,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": check_flag,
        "global_alpha": 1.0,
    }


def make_speed_material(speed=1.0):
    """Create a speed material for materials.speeds."""
    mid = new_id()
    return {
        "id": mid,
        "type": "speed",
        "mode": 0,
        "speed": speed,
        "curve_speed": None,
        "play_speed": speed,
    }


def make_video_segment(material_id, target_start_us, duration_us,
                       source_start_us, speed_id=None):
    """Create a video segment for a track."""
    seg_id = new_id()
    extra_refs = [speed_id] if speed_id else []
    return {
        "id": seg_id,
        "material_id": material_id,
        "target_timerange": make_timerange(target_start_us, duration_us),
        "source_timerange": make_timerange(source_start_us, duration_us),
        "speed": 1.0,
        "volume": 1.0,
        "visible": True,
        "reverse": False,
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "track_attribute": 0,
        "track_render_index": 0,
        "common_keyframes": [],
        "keyframe_refs": [],
        "extra_material_refs": extra_refs,
        "is_tone_modify": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
    }


def make_audio_segment(material_id, target_start_us, duration_us,
                       source_start_us=0, volume=1.0, speed_id=None):
    """Create an audio segment for a track."""
    seg_id = new_id()
    extra_refs = [speed_id] if speed_id else []
    return {
        "id": seg_id,
        "material_id": material_id,
        "target_timerange": make_timerange(target_start_us, duration_us),
        "source_timerange": make_timerange(source_start_us, duration_us),
        "speed": 1.0,
        "volume": volume,
        "visible": True,
        "reverse": False,
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": volume,
        "track_attribute": 0,
        "track_render_index": 0,
        "common_keyframes": [],
        "keyframe_refs": [],
        "extra_material_refs": extra_refs,
        "is_tone_modify": False,
    }


def make_text_segment(material_id, target_start_us, duration_us):
    """Create a text segment for a track."""
    seg_id = new_id()
    return {
        "id": seg_id,
        "material_id": material_id,
        "target_timerange": make_timerange(target_start_us, duration_us),
        "source_timerange": make_timerange(0, duration_us),
        "speed": 1.0,
        "volume": 0.0,
        "visible": True,
        "reverse": False,
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "track_attribute": 0,
        "track_render_index": 0,
        "common_keyframes": [],
        "keyframe_refs": [],
        "extra_material_refs": [],
        "is_tone_modify": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.65},
        },
        "uniform_scale": {"on": True, "value": 1.0},
    }


def make_track(track_type, segments, render_index=0):
    """Create a track with segments."""
    return {
        "attribute": 0,
        "flag": 0,
        "id": new_id(),
        "is_default_name": True,
        "name": "",
        "type": track_type,
        "segments": segments,
        "render_index": render_index,
    }


def make_transition_material(duration_us=500000):
    """Create a fade transition material."""
    mid = new_id()
    return {
        "id": mid,
        "category_id": "2",
        "category_name": "fade",
        "duration": int(duration_us),
        "is_overlap": True,
        "name": "fade",
        "platform": "all",
        "type": "fade",
        "resource_id": "",
    }


def build_empty_materials():
    """Build the empty materials dict with all required keys."""
    return {
        "ai_translates": [],
        "audio_balances": [],
        "audio_effects": [],
        "audio_fades": [],
        "audio_track_indexes": [],
        "audios": [],
        "beats": [],
        "canvases": [],
        "chromas": [],
        "color_curves": [],
        "digital_humans": [],
        "drafts": [],
        "effects": [],
        "flowers": [],
        "green_screens": [],
        "handwrites": [],
        "hsl": [],
        "images": [],
        "log_color_wheels": [],
        "loudnesses": [],
        "manual_deformations": [],
        "masks": [],
        "material_animations": [],
        "material_colors": [],
        "multi_language_refs": [],
        "placeholders": [],
        "plugin_effects": [],
        "primary_color_wheels": [],
        "realtime_denoises": [],
        "shapes": [],
        "smart_crops": [],
        "smart_relights": [],
        "sound_channel_mappings": [],
        "speeds": [],
        "stickers": [],
        "tail_leaders": [],
        "text_templates": [],
        "texts": [],
        "time_marks": [],
        "transitions": [],
        "video_effects": [],
        "video_trackings": [],
        "videos": [],
        "vocal_beautifys": [],
        "vocal_separations": [],
    }


def build_draft_meta(draft_name, width, height, duration_us):
    """Build draft_meta_info.json content."""
    now = int(time_mod.time())
    uid = uuid.uuid4()
    draft_id = str(uid).upper()  # Dashed UUID format for meta
    return {
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": "",
        "draft_id": draft_id,
        "draft_is_ai_shorts": False,
        "draft_is_invisible": False,
        "draft_materials": [
            {"type": 0, "value": []},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": []},
        ],
        "draft_materials_copied_info": [],
        "draft_name": draft_name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": "",
        "draft_segment_extra_info": [],
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": duration_us,
    }


def make_image_material(image_path, width, height):
    """Create an image material entry for materials.videos (images go here too)."""
    mid = new_id()
    return {
        "id": mid,
        "local_material_id": mid,
        "material_id": mid,
        "material_name": os.path.basename(image_path),
        "path": image_path,
        "type": "photo",
        "duration": 5 * SEC,
        "width": width,
        "height": height,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "audio_fade": None,
        "media_path": "",
    }


def build_draft(config, clips, width, height, fps):
    """Build the complete draft_content.json from config and resolved clips.

    Returns (draft_content_dict, total_duration_us).
    """
    materials = build_empty_materials()
    video_segments = []
    subtitle_segments = []   # Bottom-positioned subtitles (字幕轨)
    overlay_segments = []    # Title / end-card text (文字轨)
    audio_segments = []

    # Video material cache: path -> material dict
    video_mat_cache = {}
    timeline_offset_us = 0

    # --- Cover (片头) ---
    cover_duration = config.get("cover_duration", 2.0)
    title = config.get("title", "")
    if title and cover_duration > 0:
        cover_dur_us = int(cover_duration * SEC)

        # Check for custom cover image first
        custom_cover = config.get("cover_image")
        if custom_cover and os.path.isfile(custom_cover):
            custom_cover = os.path.abspath(custom_cover)
            img_mat = make_image_material(custom_cover, width, height)
            materials["videos"].append(img_mat)

            speed_mat = make_speed_material(1.0)
            materials["speeds"].append(speed_mat)
            cover_seg = make_video_segment(
                img_mat["id"], 0, cover_dur_us,
                source_start_us=0,
                speed_id=speed_mat["id"],
            )
            video_segments.append(cover_seg)
            print(f"Cover: custom image ({os.path.basename(custom_cover)})")
        else:
            # Use first clip's video as cover source (freeze first frame)
            first_video = clips[0].get("broll", clips[0]["video"])
            if first_video not in video_mat_cache:
                dur, vw, vh, vfps, _ = get_video_info(first_video)
                vmat = make_video_material(first_video, int(dur * SEC), vw, vh)
                video_mat_cache[first_video] = vmat
                materials["videos"].append(vmat)
            mat = video_mat_cache[first_video]

            speed_mat = make_speed_material(1.0)
            materials["speeds"].append(speed_mat)
            cover_seg = make_video_segment(
                mat["id"], 0, cover_dur_us,
                source_start_us=int(clips[0]["start"] * SEC),
                speed_id=speed_mat["id"],
            )
            video_segments.append(cover_seg)
            print(f"Cover: freeze first frame")

        # Title text overlay on cover
        title_mat = make_text_material(title, font_size=12.0, bold=True,
                                       outline=True, mat_type="text")
        materials["texts"].append(title_mat)
        title_seg = make_text_segment(title_mat["id"], 0, cover_dur_us)
        title_seg["clip"]["transform"]["y"] = 0.0  # center
        overlay_segments.append(title_seg)

        # Subtitle text on cover (if provided)
        cover_subtitle = config.get("subtitle")
        if cover_subtitle:
            sub_mat = make_text_material(cover_subtitle, font_size=7.0,
                                         color=[1.0, 1.0, 0.0], bold=False,
                                         outline=True, mat_type="text")
            materials["texts"].append(sub_mat)
            sub_seg = make_text_segment(sub_mat["id"], 0, cover_dur_us)
            sub_seg["clip"]["transform"]["y"] = 0.12
            overlay_segments.append(sub_seg)

        timeline_offset_us = cover_dur_us

    # --- Main video clips + subtitles ---
    for clip in clips:
        video_path = clip.get("broll", clip["video"])
        source_start = clip.get("broll_start", clip["start"]) if "broll" in clip else clip["start"]
        duration = clip["end"] - clip["start"]
        duration_us = int(duration * SEC)
        source_start_us = int(source_start * SEC)

        # Ensure video material exists
        if video_path not in video_mat_cache:
            dur, vw, vh, vfps, _ = get_video_info(video_path)
            vmat = make_video_material(video_path, int(dur * SEC), vw, vh)
            video_mat_cache[video_path] = vmat
            materials["videos"].append(vmat)
        mat = video_mat_cache[video_path]

        # Speed material
        speed_mat = make_speed_material(1.0)
        materials["speeds"].append(speed_mat)

        # Video segment
        vseg = make_video_segment(
            mat["id"], timeline_offset_us, duration_us,
            source_start_us=source_start_us,
            speed_id=speed_mat["id"],
        )
        video_segments.append(vseg)

        # Subtitle text segment
        text = clip.get("text", "").strip()
        if text:
            tmat = make_text_material(text, font_size=8.0, bold=True,
                                      outline=True, mat_type="subtitle")
            materials["texts"].append(tmat)
            tseg = make_text_segment(tmat["id"], timeline_offset_us, duration_us)
            subtitle_segments.append(tseg)

        timeline_offset_us += duration_us

    # --- End cards (片尾) ---
    end_cards = config.get("end_cards", [])
    for card in end_cards:
        card_text = card["text"]
        card_dur = card.get("duration", 3.0)
        card_dur_us = int(card_dur * SEC)

        # End card text material (larger, centered)
        ec_mat = make_text_material(card_text, font_size=10.0, bold=True,
                                     outline=False, mat_type="text")
        materials["texts"].append(ec_mat)
        ec_seg = make_text_segment(ec_mat["id"], timeline_offset_us, card_dur_us)
        ec_seg["clip"]["transform"]["y"] = 0.0  # center
        overlay_segments.append(ec_seg)

        timeline_offset_us += card_dur_us

    total_duration_us = timeline_offset_us

    # --- Transitions between video clips ---
    transition_dur_us = 300000  # 300ms default
    if len(video_segments) > 1:
        for i in range(1, len(video_segments)):
            tmat = make_transition_material(transition_dur_us)
            materials["transitions"].append(tmat)

    # --- BGM audio track ---
    bgm_path = config.get("bgm")
    if bgm_path and os.path.isfile(bgm_path):
        bgm_path = os.path.abspath(bgm_path)
        bgm_volume = config.get("bgm_volume", 0.15)

        amat = make_audio_material(bgm_path, total_duration_us)
        materials["audios"].append(amat)

        speed_mat = make_speed_material(1.0)
        materials["speeds"].append(speed_mat)

        aseg = make_audio_segment(
            amat["id"], 0, total_duration_us,
            source_start_us=0, volume=bgm_volume,
            speed_id=speed_mat["id"],
        )
        audio_segments.append(aseg)

    # --- Build tracks ---
    tracks = []

    # Main video track
    if video_segments:
        tracks.append(make_track("video", video_segments, render_index=2))

    # Subtitle text track (bottom-positioned captions)
    if subtitle_segments:
        tracks.append(make_track("text", subtitle_segments, render_index=6))

    # Overlay text track (title / end cards — separate track for easy editing)
    if overlay_segments:
        tracks.append(make_track("text", overlay_segments, render_index=7))

    # BGM audio track
    if audio_segments:
        tracks.append(make_track("audio", audio_segments, render_index=1))

    # --- Assemble draft_content ---
    draft_content = {
        "id": new_id(),
        "canvas_config": {
            "height": height,
            "ratio": "original",
            "width": width,
        },
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
        },
        "cover": None,
        "create_time": int(time_mod.time()),
        "duration": total_duration_us,
        "extra_info": None,
        "fps": float(fps),
        "free_render_index_mode_on": False,
        "group_container": None,
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "materials": materials,
        "mutable_config": None,
        "name": "",
        "new_version": "110.0.0",
        "platform": {
            "app_id": 3704,
            "app_source": "lv",
            "app_version": "5.9.0",
            "os": "mac",
        },
        "last_modified_platform": {
            "app_id": 3704,
            "app_source": "lv",
            "app_version": "5.9.0",
            "os": "mac",
        },
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": int(time_mod.time()),
        "version": 360000,
    }

    return draft_content, total_duration_us


def main():
    parser = argparse.ArgumentParser(
        description="Export render_config.json to a JianYing (CapCut) draft project")
    parser.add_argument("--config", required=True,
                        help="Path to render_config.json")
    parser.add_argument("--output", required=True,
                        help="Output draft folder path")
    parser.add_argument("--name", default=None,
                        help="Draft project name (default: from config title or folder name)")
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)
    clips = resolve_clips(config)
    if not clips:
        print("Error: No clips in config", file=sys.stderr)
        sys.exit(1)

    # Get video info from first clip
    first_video = clips[0].get("broll", clips[0]["video"])
    _, width, height, fps, _ = get_video_info(first_video)
    print(f"Video: {width}x{height}, {fps:.0f}fps")
    print(f"Clips: {len(clips)}")

    # Build draft
    draft_content, total_duration_us = build_draft(config, clips, width, height, fps)
    total_secs = total_duration_us / SEC
    print(f"Duration: {total_secs:.1f}s")

    # Draft name
    draft_name = args.name or config.get("title", "") or os.path.basename(args.output)

    # Create output folder
    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    # Write draft_content.json
    content_path = os.path.join(output_dir, "draft_content.json")
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(draft_content, f, ensure_ascii=False, indent=2)

    # Write draft_meta_info.json
    meta = build_draft_meta(draft_name, width, height, total_duration_us)
    meta_path = os.path.join(output_dir, "draft_meta_info.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\nDraft exported: {output_dir}")
    print(f"  - draft_content.json ({os.path.getsize(content_path) / 1024:.1f}KB)")
    print(f"  - draft_meta_info.json")
    print(f"\nTo use in JianYing (剪映):")
    print(f"  1. Copy the folder '{os.path.basename(output_dir)}' to your JianYing drafts directory:")
    print(f"     macOS:   ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/")
    print(f"     Windows: %APPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft/")
    print(f"  2. Open JianYing — the project '{draft_name}' should appear in your drafts")
    print(f"  3. You can now edit, add effects, and export from JianYing directly")


if __name__ == "__main__":
    main()
