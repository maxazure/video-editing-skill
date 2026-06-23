#!/usr/bin/env python3
"""Pre-render edit preflight for render_config, enrich_plan, and cut lists.

This is a local gate for agents: it checks structure, file links, timing, and
bounded overlay parameters before an expensive render starts. It does not
decode media, render, upload, or call generation providers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

PATH_FIELDS = {
    "render_config.clip.video": ("video",),
    "render_config.clip.transcript": ("transcript",),
    "render_config.clip.broll": ("broll",),
    "render_config.broll_overlay.video": ("video", "path", "asset"),
    "render_config.image_overlay.image": ("image", "path", "asset"),
    "render_config.pip_overlay.video": ("video", "camera", "path"),
    "render_config.bgm": ("bgm", "music", "audio"),
    "enrich_plan.broll.asset": ("suggested_asset", "asset", "path", "video"),
    "enrich_plan.imagegen.image": ("image_path", "generated_path", "asset_path", "path"),
    "enrich_plan.pip_overlay.video": ("video", "camera", "path"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def _round(value: float) -> float:
    return round(float(value), 3)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _severity_rank(severity: str) -> int:
    return {"block": 3, "warn": 2, "info": 1}.get(severity, 0)


def _first_value(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _resolve_path(raw_path: Any, base_dir: Path) -> Optional[Path]:
    if raw_path in (None, ""):
        return None
    text = str(raw_path).strip()
    if not text or text.startswith(("http://", "https://")):
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _path_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in AUDIO_EXTS:
        return "audio"
    return "file"


class CheckList:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(
        self,
        severity: str,
        code: str,
        source: str,
        message: str,
        *,
        path: Optional[Path] = None,
        value: Any = None,
    ) -> None:
        item: Dict[str, Any] = {
            "severity": severity,
            "code": code,
            "source": source,
            "message": message,
        }
        if path is not None:
            item["path"] = str(path)
        if value is not None:
            item["value"] = value
        self.items.append(item)

    def block(self, code: str, source: str, message: str, *, path: Optional[Path] = None, value: Any = None) -> None:
        self.add("block", code, source, message, path=path, value=value)

    def warn(self, code: str, source: str, message: str, *, path: Optional[Path] = None, value: Any = None) -> None:
        self.add("warn", code, source, message, path=path, value=value)

    def info(self, code: str, source: str, message: str, *, path: Optional[Path] = None, value: Any = None) -> None:
        self.add("info", code, source, message, path=path, value=value)


def _check_json_object(data: Any, source: str, checks: CheckList) -> bool:
    if isinstance(data, Mapping):
        return True
    checks.block("invalid_json_shape", source, "JSON root must be an object.")
    return False


def _check_file(
    raw_path: Any,
    *,
    base_dir: Path,
    source: str,
    checks: CheckList,
    required: bool = True,
    expected_kind: str = "",
) -> Optional[Path]:
    path = _resolve_path(raw_path, base_dir)
    if path is None:
        if required:
            checks.block("missing_path_value", source, "Expected a local file path.")
        return None
    if not path.exists():
        checks.block("missing_file", source, "Linked file does not exist.", path=path)
        return path
    if not path.is_file():
        checks.block("not_a_file", source, "Linked path is not a file.", path=path)
        return path
    if expected_kind and _path_kind(path) != expected_kind:
        checks.warn(
            "unexpected_file_type",
            source,
            f"Expected {expected_kind} file, saw {_path_kind(path)} extension.",
            path=path,
        )
    return path


def _time_range(
    item: Mapping[str, Any],
    source: str,
    checks: CheckList,
    *,
    default_duration: Optional[float] = None,
    required: bool = True,
) -> Optional[Tuple[float, float, float]]:
    start = _as_float(item.get("start", item.get("time", item.get("timing_seconds", 0.0))))
    end = _as_float(item.get("end"))
    duration = _as_float(item.get("duration"))

    if start is None:
        checks.block("invalid_start", f"{source}.start", "Start time must be numeric.", value=item.get("start"))
        return None

    if end is None:
        if duration is not None:
            end = start + duration
        elif default_duration is not None:
            end = start + default_duration
        elif required:
            checks.block("missing_end", source, "Time cue needs end or duration.")
            return None
        else:
            return None

    if end is None:
        return None
    if end <= start:
        checks.block("invalid_time_range", source, "End time must be after start.", value={"start": start, "end": end})
        return None
    if start < 0:
        checks.block("negative_start", source, "Start time cannot be negative.", value=start)
        return None

    return start, end, end - start


def _check_timeline_bound(
    start: float,
    end: float,
    *,
    timeline_duration: Optional[float],
    source: str,
    checks: CheckList,
    tolerance: float = 0.25,
) -> None:
    if timeline_duration is None:
        return
    if start > timeline_duration + tolerance:
        checks.block(
            "cue_after_timeline",
            source,
            "Cue starts after the current output timeline.",
            value={"start": _round(start), "timeline_duration": _round(timeline_duration)},
        )
    elif end > timeline_duration + tolerance:
        checks.warn(
            "cue_extends_past_timeline",
            source,
            "Cue extends past the current output timeline and may be clipped.",
            value={"end": _round(end), "timeline_duration": _round(timeline_duration)},
        )


def _segment_map(transcript_path: Path, checks: CheckList, source: str) -> Dict[str, Mapping[str, Any]]:
    try:
        transcript = load_json(str(transcript_path))
    except (OSError, json.JSONDecodeError) as exc:
        checks.block("invalid_transcript_json", source, f"Cannot read transcript JSON: {exc}", path=transcript_path)
        return {}
    if not isinstance(transcript, Mapping) or not isinstance(transcript.get("segments"), list):
        checks.block("invalid_transcript_shape", source, "Transcript must contain segments[].", path=transcript_path)
        return {}
    segments: Dict[str, Mapping[str, Any]] = {}
    for index, segment in enumerate(transcript.get("segments") or [], start=1):
        if not isinstance(segment, Mapping):
            continue
        seg_id = segment.get("id", segment.get("segment_id", index))
        segments[str(seg_id)] = segment
    return segments


def _clip_range_from_transcript(
    clip: Mapping[str, Any],
    *,
    index: int,
    base_dir: Path,
    transcript_cache: Dict[str, Dict[str, Mapping[str, Any]]],
    checks: CheckList,
) -> Optional[Tuple[float, float, float]]:
    transcript_path = _check_file(
        clip.get("transcript"),
        base_dir=base_dir,
        source=f"render_config.clips[{index}].transcript",
        checks=checks,
        expected_kind="file",
    )
    if transcript_path is None:
        return None

    cache_key = str(transcript_path)
    if cache_key not in transcript_cache:
        transcript_cache[cache_key] = _segment_map(transcript_path, checks, f"render_config.clips[{index}].transcript")
    segment_id = clip.get("segment_id")
    if segment_id is None:
        checks.block("missing_segment_id", f"render_config.clips[{index}]", "Clip needs segment_id when start/end are absent.")
        return None
    segment = transcript_cache[cache_key].get(str(segment_id))
    if segment is None:
        checks.block(
            "segment_not_found",
            f"render_config.clips[{index}].segment_id",
            "segment_id was not found in transcript.",
            value=segment_id,
        )
        return None
    return _time_range(segment, f"render_config.clips[{index}].segment", checks)


def check_render_config(path: str, checks: CheckList) -> Dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    base_dir = config_path.parent
    try:
        config = load_json(str(config_path))
    except (OSError, json.JSONDecodeError) as exc:
        checks.block("invalid_json", "render_config", f"Cannot read render config: {exc}", path=config_path)
        return {"path": str(config_path), "clips": 0, "timeline_duration": None}
    if not _check_json_object(config, "render_config", checks):
        return {"path": str(config_path), "clips": 0, "timeline_duration": None}

    clips = config.get("clips")
    if not isinstance(clips, list) or not clips:
        checks.block("empty_clips", "render_config.clips", "Render config must contain a non-empty clips list.")
        clips = []

    transcript_cache: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    timeline_duration = 0.0
    valid_clip_count = 0
    for index, clip in enumerate(clips, start=1):
        source = f"render_config.clips[{index}]"
        if not isinstance(clip, Mapping):
            checks.block("invalid_clip", source, "Clip must be an object.")
            continue
        _check_file(clip.get("video"), base_dir=base_dir, source=f"{source}.video", checks=checks, expected_kind="video")
        if "start" in clip or "end" in clip:
            timing = _time_range(clip, source, checks)
        else:
            timing = _clip_range_from_transcript(
                clip,
                index=index,
                base_dir=base_dir,
                transcript_cache=transcript_cache,
                checks=checks,
            )
        if timing:
            valid_clip_count += 1
            timeline_duration += timing[2]
        if "broll" in clip:
            _check_file(clip.get("broll"), base_dir=base_dir, source=f"{source}.broll", checks=checks, expected_kind="video")

    _check_config_overlay_list(
        config.get("broll_overlays") or [],
        base_dir=base_dir,
        key="video",
        expected_kind="video",
        source_prefix="render_config.broll_overlays",
        timeline_duration=timeline_duration if valid_clip_count else None,
        checks=checks,
    )
    _check_config_overlay_list(
        config.get("image_overlays") or [],
        base_dir=base_dir,
        key="image",
        expected_kind="image",
        source_prefix="render_config.image_overlays",
        timeline_duration=timeline_duration if valid_clip_count else None,
        checks=checks,
    )
    _check_config_overlay_list(
        config.get("pip_overlays") or [],
        base_dir=base_dir,
        key="video",
        expected_kind="video",
        source_prefix="render_config.pip_overlays",
        timeline_duration=timeline_duration if valid_clip_count else None,
        checks=checks,
        check_pip_params=True,
    )
    _check_text_badges(config.get("text_badges") or [], timeline_duration, checks, "render_config.text_badges")
    _check_focus_events(config.get("focus_events") or [], timeline_duration, checks, "render_config.focus_events")

    bgm = _first_value(config, PATH_FIELDS["render_config.bgm"])
    if bgm:
        _check_file(bgm, base_dir=base_dir, source="render_config.bgm", checks=checks, expected_kind="audio")

    return {
        "path": str(config_path),
        "clips": len(clips),
        "valid_clips": valid_clip_count,
        "timeline_duration": _round(timeline_duration) if valid_clip_count else None,
    }


def _check_config_overlay_list(
    overlays: Any,
    *,
    base_dir: Path,
    key: str,
    expected_kind: str,
    source_prefix: str,
    timeline_duration: Optional[float],
    checks: CheckList,
    check_pip_params: bool = False,
) -> None:
    if not isinstance(overlays, list):
        checks.block("invalid_overlay_list", source_prefix, "Overlay field must be a list.")
        return
    for index, overlay in enumerate(overlays, start=1):
        source = f"{source_prefix}[{index}]"
        if not isinstance(overlay, Mapping):
            checks.block("invalid_overlay", source, "Overlay must be an object.")
            continue
        _check_file(overlay.get(key), base_dir=base_dir, source=f"{source}.{key}", checks=checks, expected_kind=expected_kind)
        timing = _time_range(overlay, source, checks, default_duration=2.0)
        if timing:
            _check_timeline_bound(timing[0], timing[1], timeline_duration=timeline_duration, source=source, checks=checks)
        if check_pip_params:
            _check_pip_params(overlay, source, checks)


def _check_text_badges(items: Any, timeline_duration: Optional[float], checks: CheckList, source_prefix: str) -> None:
    if not isinstance(items, list):
        checks.block("invalid_badge_list", source_prefix, "text_badges must be a list.")
        return
    for index, badge in enumerate(items, start=1):
        source = f"{source_prefix}[{index}]"
        if not isinstance(badge, Mapping):
            checks.block("invalid_badge", source, "Badge must be an object.")
            continue
        if not str(badge.get("text") or "").strip():
            checks.block("empty_badge_text", f"{source}.text", "Badge text cannot be empty.")
        timing = _time_range(badge, source, checks, default_duration=1.0)
        if timing:
            _check_timeline_bound(timing[0], timing[1], timeline_duration=timeline_duration, source=source, checks=checks)


def _check_focus_events(items: Any, timeline_duration: Optional[float], checks: CheckList, source_prefix: str) -> None:
    if not isinstance(items, list):
        checks.block("invalid_focus_list", source_prefix, "focus_events must be a list.")
        return
    for index, event in enumerate(items, start=1):
        source = f"{source_prefix}[{index}]"
        if not isinstance(event, Mapping):
            checks.block("invalid_focus_event", source, "Focus event must be an object.")
            continue
        timing = _time_range(event, source, checks, default_duration=1.2)
        if timing:
            _check_timeline_bound(timing[0], timing[1], timeline_duration=timeline_duration, source=source, checks=checks)
        for axis in ("x", "y"):
            value = _as_float(event.get(axis, event.get(f"norm_{axis}")))
            if value is None:
                checks.warn("missing_focus_coordinate", f"{source}.{axis}", "Focus event is missing a normalized coordinate.")
                continue
            source_dim = _as_float(event.get(f"source_{'width' if axis == 'x' else 'height'}"))
            if value > 1.0 and not source_dim:
                checks.block(
                    "pixel_coordinate_without_source_size",
                    f"{source}.{axis}",
                    "Pixel coordinate needs source_width/source_height so render_final can normalize it.",
                    value=value,
                )
            elif value < 0:
                checks.block("negative_focus_coordinate", f"{source}.{axis}", "Focus coordinate cannot be negative.", value=value)
        zoom = _as_float(event.get("zoom"))
        if zoom is not None and not (1.05 <= zoom <= 4.0):
            checks.warn("focus_zoom_clamped", f"{source}.zoom", "render_final will clamp focus zoom to 1.05-4.0.", value=zoom)


def _check_pip_params(item: Mapping[str, Any], source: str, checks: CheckList) -> None:
    width_ratio = _as_float(item.get("width_ratio"))
    if width_ratio is not None and not (0.08 <= width_ratio <= 0.6):
        checks.warn("pip_width_ratio_clamped", f"{source}.width_ratio", "PIP width ratio should stay between 0.08 and 0.6.", value=width_ratio)
    opacity = _as_float(item.get("opacity"))
    if opacity is not None and not (0.0 <= opacity <= 1.0):
        checks.warn("pip_opacity_clamped", f"{source}.opacity", "PIP opacity should stay between 0 and 1.", value=opacity)
    source_start = _as_float(item.get("source_start"))
    if source_start is not None and source_start < 0:
        checks.block("negative_source_start", f"{source}.source_start", "PIP source_start cannot be negative.", value=source_start)


def check_enrich_plan(path: str, checks: CheckList, timeline_duration: Optional[float]) -> Dict[str, Any]:
    plan_path = Path(path).expanduser().resolve()
    base_dir = plan_path.parent
    try:
        plan = load_json(str(plan_path))
    except (OSError, json.JSONDecodeError) as exc:
        checks.block("invalid_json", "enrich_plan", f"Cannot read enrich plan: {exc}", path=plan_path)
        return {"path": str(plan_path), "timed_items": 0}
    if not _check_json_object(plan, "enrich_plan", checks):
        return {"path": str(plan_path), "timed_items": 0}

    timed_items = 0
    timed_items += _check_plan_media_list(
        plan.get("broll") or [],
        base_dir=base_dir,
        path_keys=PATH_FIELDS["enrich_plan.broll.asset"],
        expected_kind="video",
        source_prefix="enrich_plan.broll",
        checks=checks,
        timeline_duration=timeline_duration,
        missing_path_severity="warn",
    )
    timed_items += _check_plan_media_list(
        plan.get("imagegen") or [],
        base_dir=base_dir,
        path_keys=PATH_FIELDS["enrich_plan.imagegen.image"],
        expected_kind="image",
        source_prefix="enrich_plan.imagegen",
        checks=checks,
        timeline_duration=timeline_duration,
        missing_path_severity="warn",
    )
    timed_items += _check_plan_media_list(
        plan.get("pip_overlays") or [],
        base_dir=base_dir,
        path_keys=PATH_FIELDS["enrich_plan.pip_overlay.video"],
        expected_kind="video",
        source_prefix="enrich_plan.pip_overlays",
        checks=checks,
        timeline_duration=timeline_duration,
        missing_path_severity="block",
        check_pip_params=True,
    )
    _check_text_badges(plan.get("stickers") or [], timeline_duration, checks, "enrich_plan.stickers")
    _check_focus_events(plan.get("focus_events") or [], timeline_duration, checks, "enrich_plan.focus_events")
    _check_chapter_cards(plan.get("chapter_cards") or [], base_dir, timeline_duration, checks)

    return {"path": str(plan_path), "timed_items": timed_items}


def _check_plan_media_list(
    items: Any,
    *,
    base_dir: Path,
    path_keys: Sequence[str],
    expected_kind: str,
    source_prefix: str,
    checks: CheckList,
    timeline_duration: Optional[float],
    missing_path_severity: str,
    check_pip_params: bool = False,
) -> int:
    if not isinstance(items, list):
        checks.block("invalid_plan_list", source_prefix, "Plan field must be a list.")
        return 0
    count = 0
    for index, item in enumerate(items, start=1):
        source = f"{source_prefix}[{index}]"
        if not isinstance(item, Mapping):
            checks.block("invalid_plan_item", source, "Plan item must be an object.")
            continue
        path_value = _first_value(item, path_keys)
        if path_value:
            _check_file(path_value, base_dir=base_dir, source=f"{source}.asset", checks=checks, expected_kind=expected_kind)
        elif missing_path_severity == "block":
            checks.block("missing_overlay_asset", f"{source}.asset", "Timed overlay needs a local media path before render.")
        else:
            checks.warn("unlinked_advisory_asset", f"{source}.asset", "Plan item has no local asset path yet; keep it as planning-only or link a file before render.")
        timing = _time_range(item, source, checks, default_duration=2.0, required=False)
        if timing:
            count += 1
            _check_timeline_bound(timing[0], timing[1], timeline_duration=timeline_duration, source=source, checks=checks)
        if check_pip_params:
            _check_pip_params(item, source, checks)
    return count


def _check_chapter_cards(items: Any, base_dir: Path, timeline_duration: Optional[float], checks: CheckList) -> None:
    if not isinstance(items, list):
        checks.block("invalid_chapter_list", "enrich_plan.chapter_cards", "chapter_cards must be a list.")
        return
    for index, item in enumerate(items, start=1):
        source = f"enrich_plan.chapter_cards[{index}]"
        if not isinstance(item, Mapping):
            checks.block("invalid_chapter_card", source, "Chapter card must be an object.")
            continue
        if not str(item.get("title") or "").strip():
            checks.warn("missing_chapter_title", f"{source}.title", "Chapter card has no title.")
        timing = _time_range(item, source, checks, default_duration=1.0)
        if timing:
            _check_timeline_bound(timing[0], timing[1], timeline_duration=timeline_duration, source=source, checks=checks)
        image_path = _first_value(item, ("png", "image_path", "asset_path"))
        if image_path:
            _check_file(image_path, base_dir=base_dir, source=f"{source}.image", checks=checks, expected_kind="image")


def check_cut_list(path: str, checks: CheckList) -> Dict[str, Any]:
    cut_path = Path(path).expanduser().resolve()
    base_dir = cut_path.parent
    try:
        data = load_json(str(cut_path))
    except (OSError, json.JSONDecodeError) as exc:
        checks.block("invalid_json", "cut_list", f"Cannot read cut list: {exc}", path=cut_path)
        return {"path": str(cut_path), "keep_segments": 0}
    if not _check_json_object(data, "cut_list", checks):
        return {"path": str(cut_path), "keep_segments": 0}

    source_path = _first_value(data, ("input", "source", "video"))
    if source_path:
        _check_file(source_path, base_dir=base_dir, source="cut_list.input", checks=checks, expected_kind="video")

    keep_segments = data.get("keep_segments")
    if not isinstance(keep_segments, list) or not keep_segments:
        checks.block("empty_keep_segments", "cut_list.keep_segments", "Cut list must contain keep_segments[].")
        return {"path": str(cut_path), "keep_segments": 0}

    last_end = -1.0
    for index, segment in enumerate(keep_segments, start=1):
        source = f"cut_list.keep_segments[{index}]"
        if not isinstance(segment, Mapping):
            checks.block("invalid_keep_segment", source, "Keep segment must be an object.")
            continue
        timing = _time_range(segment, source, checks)
        if not timing:
            continue
        if timing[0] < last_end:
            checks.warn("overlapping_keep_segment", source, "Keep segments overlap or are out of order.")
        last_end = max(last_end, timing[1])

    fade = _as_float(data.get("fade_seconds"))
    if fade is not None and fade < 0:
        checks.block("negative_fade", "cut_list.fade_seconds", "fade_seconds cannot be negative.", value=fade)

    return {"path": str(cut_path), "keep_segments": len(keep_segments)}


def build_preflight(
    *,
    render_config: Optional[str] = None,
    enrich_plans: Sequence[str] = (),
    cut_lists: Sequence[str] = (),
) -> Dict[str, Any]:
    checks = CheckList()
    artifacts: Dict[str, Any] = {
        "render_config": None,
        "enrich_plans": [],
        "cut_lists": [],
    }

    timeline_duration: Optional[float] = None
    if render_config:
        artifacts["render_config"] = check_render_config(render_config, checks)
        timeline_duration = artifacts["render_config"].get("timeline_duration")
    elif not cut_lists:
        checks.warn("no_primary_timeline", "inputs", "No render_config or cut_list was provided.")

    for plan in enrich_plans:
        artifacts["enrich_plans"].append(check_enrich_plan(plan, checks, timeline_duration))
    for cut_list in cut_lists:
        artifacts["cut_lists"].append(check_cut_list(cut_list, checks))

    blocking = sum(1 for item in checks.items if item["severity"] == "block")
    warnings = sum(1 for item in checks.items if item["severity"] == "warn")
    if blocking:
        status = "blocked"
    elif warnings:
        status = "warn"
    else:
        status = "ready"

    next_actions = _next_actions(checks.items)
    return {
        "version": "edit_preflight.v1",
        "generated_at": utc_now(),
        "status": status,
        "summary": {
            "blocking": blocking,
            "warnings": warnings,
            "checks": len(checks.items),
            "render_config_clips": (artifacts["render_config"] or {}).get("clips", 0),
            "enrich_plans": len(artifacts["enrich_plans"]),
            "cut_lists": len(artifacts["cut_lists"]),
            "timeline_duration": timeline_duration,
        },
        "artifacts": artifacts,
        "checks": sorted(checks.items, key=lambda item: (-_severity_rank(item["severity"]), item["source"], item["code"])),
        "next_actions": next_actions,
        "notes": [
            "This preflight validates local edit artifacts only; it does not render or inspect media streams.",
            "Run render_qa.py after render; preflight catches bad plans, not visual/audio defects in the encoded output.",
        ],
    }


def _next_actions(items: Sequence[Mapping[str, Any]]) -> List[str]:
    actions: List[str] = []
    for item in items:
        code = item.get("code")
        source = item.get("source")
        if item.get("severity") == "info":
            continue
        if code == "missing_file":
            actions.append(f"Relink or create the missing file for {source}.")
        elif code in {"invalid_time_range", "negative_start", "cue_after_timeline", "cue_extends_past_timeline"}:
            actions.append(f"Fix timing for {source}.")
        elif code in {"segment_not_found", "missing_segment_id", "invalid_transcript_json", "invalid_transcript_shape"}:
            actions.append(f"Fix transcript mapping for {source}.")
        elif code in {"empty_clips", "empty_keep_segments"}:
            actions.append(f"Create non-empty edit segments for {source}.")
        elif code == "unlinked_advisory_asset":
            actions.append(f"Link a local asset for {source} or keep that plan item out of render.")
        elif code == "pixel_coordinate_without_source_size":
            actions.append(f"Add source_width/source_height or normalized x/y for {source}.")
        else:
            actions.append(f"Review {source}: {item.get('message', '')}")
    return list(dict.fromkeys(actions))


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Edit Preflight",
        "",
        f"- Status: **{str(report.get('status', '')).upper()}**",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Render config clips: {summary.get('render_config_clips', 0)}",
        f"- Timeline duration: {summary.get('timeline_duration')}",
        "",
        "## Checks",
        "",
        "| severity | code | source | message | path/value |",
        "|---|---|---|---|---|",
    ]
    checks = report.get("checks") or []
    if checks:
        for item in checks:
            detail = item.get("path")
            if detail is None and "value" in item:
                detail = json.dumps(item["value"], ensure_ascii=False)
            lines.append(
                "| {severity} | `{code}` | `{source}` | {message} | `{detail}` |".format(
                    severity=item.get("severity", ""),
                    code=item.get("code", ""),
                    source=item.get("source", ""),
                    message=str(item.get("message", "")).replace("|", "\\|"),
                    detail=str(detail or "").replace("|", "\\|"),
                )
            )
    else:
        lines.append("| info | `ready` | `inputs` | No preflight issues found. |  |")

    actions = report.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight render_config/enrich_plan/cut-list artifacts before rendering.")
    parser.add_argument("--config", "--render-config", dest="render_config", help="render_final.py render_config JSON.")
    parser.add_argument("--enrich-plan", action="append", default=[], help="auto_enrich/screen_focus/pip enrich plan JSON; can repeat.")
    parser.add_argument("--cut-list", action="append", default=[], help="rough_cut.py or jump_cut.py cut-list JSON; can repeat.")
    parser.add_argument("--output", help="Write edit_preflight.v1 JSON report.")
    parser.add_argument("--markdown", help="Write Markdown review report.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 on warnings as well as blocking issues.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not any([args.render_config, args.enrich_plan, args.cut_list]):
        print("Error: provide --config, --enrich-plan, or --cut-list.", file=sys.stderr)
        return 2

    report = build_preflight(
        render_config=args.render_config,
        enrich_plans=args.enrich_plan,
        cut_lists=args.cut_list,
    )
    if args.output:
        write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))

    status = report["status"]
    print(f"Edit preflight: {status} ({report['summary']['blocking']} blocking, {report['summary']['warnings']} warnings)")
    if status == "blocked" or (args.strict and status == "warn"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
