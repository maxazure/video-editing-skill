#!/usr/bin/env python3
"""Plan and optionally apply visual privacy redactions to a video.

The script intentionally does not run face/license-plate detectors. It accepts
manual boxes or detector JSON from tools such as EgoBlur, deface, YOLO, or a
human review packet, then emits a local JSON/Markdown gate and an optional
FFmpeg command that blurs, pixelates, or masks the selected regions.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info  # noqa: E402


VIDEO_INFO_UNKNOWN = (0.0, 0, 0, 0.0, 0)


@dataclass
class RedactionEvent:
    id: str
    start: float
    end: float
    x: int
    y: int
    w: int
    h: int
    label: str = "sensitive"
    score: Optional[float] = None
    source: str = "manual"
    method: Optional[str] = None
    reviewed: bool = False
    notes: Optional[str] = None

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "reviewed"}:
            return True
        if normalized in {"0", "false", "no", "n", "unreviewed"}:
            return False
    return default


def _load_json(path: str) -> Mapping[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _video_info(video: Optional[str]) -> Tuple[float, int, int, float, int]:
    if not video:
        return VIDEO_INFO_UNKNOWN
    try:
        return get_video_info(video)
    except Exception:
        return VIDEO_INFO_UNKNOWN


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _looks_normalized(values: Sequence[float], unit: str = "") -> bool:
    if unit.lower() in {"normalized", "relative", "ratio", "0-1"}:
        return True
    if unit.lower() in {"pixel", "pixels", "px", "absolute"}:
        return False
    return bool(values) and all(0.0 <= v <= 1.0 for v in values)


def _bbox_from_mapping(item: Mapping[str, Any]) -> Tuple[Optional[List[float]], str, str]:
    unit = str(item.get("unit") or item.get("bbox_unit") or item.get("coordinate_unit") or "")
    fmt = str(item.get("format") or item.get("bbox_format") or "").lower()

    for key in ("box", "bbox", "xyxy", "bounds"):
        raw = item.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 4:
            values = [_float(v) for v in raw]
            if key == "xyxy" or fmt == "xyxy":
                return values, unit, "xyxy"
            if fmt == "xywh":
                return values, unit, "xywh"
            if _looks_normalized(values, unit) and values[2] > values[0] and values[3] > values[1]:
                return values, unit, "xyxy"
            if values[2] > values[0] and values[3] > values[1]:
                return values, unit, "xyxy"
            return values, unit, "xywh"

    if all(key in item for key in ("x", "y", "w", "h")):
        return [_float(item["x"]), _float(item["y"]), _float(item["w"]), _float(item["h"])], unit, "xywh"
    if all(key in item for key in ("x1", "y1", "x2", "y2")):
        return [_float(item["x1"]), _float(item["y1"]), _float(item["x2"]), _float(item["y2"])], unit, "xyxy"
    return None, unit, fmt or "xyxy"


def _normalize_box(
    values: Sequence[float],
    fmt: str,
    unit: str,
    *,
    width: int,
    height: int,
    scale: float,
) -> Tuple[int, int, int, int, List[str]]:
    warnings: List[str] = []
    normalized = _looks_normalized(values, unit)
    if normalized and (width <= 0 or height <= 0):
        raise ValueError("normalized boxes require --video or --width/--height")

    if normalized:
        x1 = values[0] * width
        y1 = values[1] * height
        if fmt == "xywh":
            x2 = x1 + values[2] * width
            y2 = y1 + values[3] * height
        else:
            x2 = values[2] * width
            y2 = values[3] * height
    elif fmt == "xywh":
        x1 = values[0]
        y1 = values[1]
        x2 = x1 + values[2]
        y2 = y1 + values[3]
    else:
        x1, y1, x2, y2 = values

    if x2 < x1:
        x1, x2 = x2, x1
        warnings.append("swapped x coordinates")
    if y2 < y1:
        y1, y2 = y2, y1
        warnings.append("swapped y coordinates")

    box_w = x2 - x1
    box_h = y2 - y1
    if box_w <= 0 or box_h <= 0:
        raise ValueError("box has zero area")

    if scale != 1.0:
        cx = x1 + box_w / 2
        cy = y1 + box_h / 2
        box_w *= scale
        box_h *= scale
        x1 = cx - box_w / 2
        y1 = cy - box_h / 2
        x2 = cx + box_w / 2
        y2 = cy + box_h / 2

    if width > 0:
        before = (x1, x2)
        x1 = max(0, min(width - 1, x1))
        x2 = max(1, min(width, x2))
        if before != (x1, x2):
            warnings.append("clipped x to frame")
    if height > 0:
        before = (y1, y2)
        y1 = max(0, min(height - 1, y1))
        y2 = max(1, min(height, y2))
        if before != (y1, y2):
            warnings.append("clipped y to frame")

    x = int(round(x1))
    y = int(round(y1))
    w = int(round(x2 - x1))
    h = int(round(y2 - y1))
    if w <= 0 or h <= 0:
        raise ValueError("box collapsed after clipping")
    return x, y, w, h, warnings


def _time_range(item: Mapping[str, Any], *, frame_hold: float) -> Tuple[float, float]:
    start = _float(
        item.get("start", item.get("start_time", item.get("time", item.get("timestamp", 0.0)))),
        0.0,
    )
    if "end" in item or "end_time" in item:
        end = _float(item.get("end", item.get("end_time")), start + frame_hold)
    elif "duration" in item:
        end = start + max(0.0, _float(item.get("duration"), frame_hold))
    else:
        end = start + frame_hold
    if end <= start:
        end = start + frame_hold
    return start, end


def _label_allowed(label: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    normalized = label.lower()
    if include and normalized not in {item.lower() for item in include}:
        return False
    if normalized in {item.lower() for item in exclude}:
        return False
    return True


def _event_from_item(
    item: Mapping[str, Any],
    *,
    index: int,
    source: str,
    width: int,
    height: int,
    scale: float,
    frame_hold: float,
) -> Tuple[Optional[RedactionEvent], List[str]]:
    warnings: List[str] = []
    values, unit, fmt = _bbox_from_mapping(item)
    if values is None:
        return None, ["missing bbox"]

    try:
        x, y, w, h, box_warnings = _normalize_box(values, fmt, unit, width=width, height=height, scale=scale)
    except ValueError as exc:
        return None, [str(exc)]

    start, end = _time_range(item, frame_hold=frame_hold)
    if start < 0:
        warnings.append("clipped negative start to 0")
        start = 0.0
    if end <= start:
        return None, ["invalid time range"]

    label = str(item.get("label") or item.get("class") or item.get("category") or item.get("type") or "sensitive")
    score = item.get("score", item.get("confidence"))
    score_value = None if score is None else _float(score)
    event_id = str(item.get("id") or f"redact_{index:03d}")
    event = RedactionEvent(
        id=event_id,
        start=round(start, 4),
        end=round(end, 4),
        x=x,
        y=y,
        w=w,
        h=h,
        label=label,
        score=score_value,
        source=source,
        method=str(item.get("method") or "") or None,
        reviewed=_bool(item.get("reviewed"), False),
        notes=str(item.get("notes") or item.get("reason") or "") or None,
    )
    warnings.extend(box_warnings)
    return event, warnings


def _iter_detection_items(data: Mapping[str, Any]) -> Iterable[Tuple[Mapping[str, Any], str]]:
    for key in ("privacy_redactions", "redactions", "detections", "events", "items"):
        for item in _as_list(data.get(key)):
            if isinstance(item, Mapping):
                yield item, key

    for frame in _as_list(data.get("frames")):
        if not isinstance(frame, Mapping):
            continue
        frame_time = frame.get("time", frame.get("timestamp", frame.get("pts")))
        for key in ("detections", "boxes", "objects", "redactions"):
            for item in _as_list(frame.get(key)):
                if isinstance(item, Mapping):
                    merged = dict(item)
                    merged.setdefault("time", frame_time)
                    yield merged, f"frames.{key}"


def parse_manual_box(raw: str) -> Mapping[str, Any]:
    parts = raw.split(":")
    if len(parts) < 3:
        raise ValueError("--box format is start:end:x,y,w,h[:label[:reviewed]]")
    start = _float(parts[0])
    end = _float(parts[1])
    coords = [part.strip() for part in parts[2].split(",")]
    if len(coords) != 4:
        raise ValueError("--box coordinates must be x,y,w,h")
    label = parts[3] if len(parts) >= 4 and parts[3] else "manual"
    reviewed = _bool(parts[4], True) if len(parts) >= 5 else True
    return {
        "start": start,
        "end": end,
        "x": _float(coords[0]),
        "y": _float(coords[1]),
        "w": _float(coords[2]),
        "h": _float(coords[3]),
        "label": label,
        "reviewed": reviewed,
        "source": "manual",
    }


def load_events(
    detection_paths: Sequence[str],
    manual_boxes: Sequence[str],
    *,
    width: int,
    height: int,
    scale: float,
    frame_hold: float,
    min_score: float,
    include_labels: Sequence[str],
    exclude_labels: Sequence[str],
) -> Tuple[List[RedactionEvent], List[Dict[str, Any]]]:
    events: List[RedactionEvent] = []
    warnings: List[Dict[str, Any]] = []
    index = 1

    for raw in manual_boxes:
        try:
            item = parse_manual_box(raw)
        except ValueError as exc:
            warnings.append({"source": "manual", "message": str(exc), "item": raw})
            continue
        event, item_warnings = _event_from_item(
            item,
            index=index,
            source="manual",
            width=width,
            height=height,
            scale=scale,
            frame_hold=frame_hold,
        )
        if event:
            events.append(event)
            index += 1
        for message in item_warnings:
            warnings.append({"source": "manual", "event_id": event.id if event else None, "message": message})

    for path in detection_paths:
        data = _load_json(path)
        for item, source_key in _iter_detection_items(data):
            label = str(item.get("label") or item.get("class") or item.get("category") or item.get("type") or "sensitive")
            if not _label_allowed(label, include_labels, exclude_labels):
                continue
            score = item.get("score", item.get("confidence"))
            if score is not None and _float(score) < min_score:
                warnings.append({"source": path, "message": "dropped low score detection", "label": label, "score": _float(score)})
                continue
            event, item_warnings = _event_from_item(
                item,
                index=index,
                source=f"{path}:{source_key}",
                width=width,
                height=height,
                scale=scale,
                frame_hold=frame_hold,
            )
            if event:
                events.append(event)
                index += 1
            for message in item_warnings:
                warnings.append({"source": path, "event_id": event.id if event else None, "message": message})

    return events, warnings


def _iou(a: RedactionEvent, b: RedactionEvent) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = a.w * a.h
    area_b = b.w * b.h
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def merge_events(events: Sequence[RedactionEvent], *, iou_threshold: float = 0.7) -> List[RedactionEvent]:
    merged: List[RedactionEvent] = []
    for event in sorted(events, key=lambda item: (item.start, item.end, item.label, item.x, item.y)):
        match = None
        for existing in merged:
            temporal_overlap = min(existing.end, event.end) - max(existing.start, event.start)
            if temporal_overlap > 0 and existing.label == event.label and _iou(existing, event) >= iou_threshold:
                match = existing
                break
        if not match:
            merged.append(event)
            continue

        x1 = min(match.x, event.x)
        y1 = min(match.y, event.y)
        x2 = max(match.x2, event.x2)
        y2 = max(match.y2, event.y2)
        match.start = min(match.start, event.start)
        match.end = max(match.end, event.end)
        match.x = x1
        match.y = y1
        match.w = x2 - x1
        match.h = y2 - y1
        match.score = max(v for v in (match.score, event.score) if v is not None) if (match.score is not None or event.score is not None) else None
        match.reviewed = match.reviewed and event.reviewed
        match.notes = "; ".join(item for item in (match.notes, event.notes, "merged overlap") if item)
    for idx, event in enumerate(merged, start=1):
        event.id = f"redact_{idx:03d}"
    return merged


def _enable_expr(event: RedactionEvent) -> str:
    return f"between(t,{event.start:.4f},{event.end:.4f})"


def build_filter_complex(
    events: Sequence[RedactionEvent],
    *,
    method: str,
    blur_radius: int,
    pixel_blocks: int,
    mask_color: str,
) -> str:
    if not events:
        return ""

    if method == "solid":
        filters = []
        current = "[0:v]"
        for idx, event in enumerate(events):
            out = "[vout]" if idx == len(events) - 1 else f"[v{idx}]"
            filters.append(
                f"{current}drawbox=x={event.x}:y={event.y}:w={event.w}:h={event.h}:"
                f"color={mask_color}:t=fill:enable='{_enable_expr(event)}'{out}"
            )
            current = out
        return ";".join(filters)

    split_labels = "".join(f"[red{idx}]" for idx in range(len(events)))
    filters = [f"[0:v]split={len(events) + 1}[base]{split_labels}"]
    base_label = "[base]"
    for idx, event in enumerate(events):
        effect_label = f"[effect{idx}]"
        out_label = "[vout]" if idx == len(events) - 1 else f"[v{idx}]"
        if (event.method or method) == "pixelate":
            small_w = max(1, event.w // max(1, pixel_blocks))
            small_h = max(1, event.h // max(1, pixel_blocks))
            effect = (
                f"[red{idx}]crop={event.w}:{event.h}:{event.x}:{event.y},"
                f"scale={small_w}:{small_h}:flags=neighbor,"
                f"scale={event.w}:{event.h}:flags=neighbor{effect_label}"
            )
        else:
            radius = max(2, blur_radius or min(event.w, event.h) // 8)
            effect = (
                f"[red{idx}]crop={event.w}:{event.h}:{event.x}:{event.y},"
                f"boxblur=luma_radius={radius}:luma_power=1:chroma_radius={radius}:chroma_power=1{effect_label}"
            )
        filters.append(effect)
        filters.append(f"{base_label}{effect_label}overlay={event.x}:{event.y}:enable='{_enable_expr(event)}'{out_label}")
        base_label = out_label
    return ";".join(filters)


def build_ffmpeg_command(
    video: str,
    output: str,
    filter_complex: str,
    *,
    copy_audio: bool = True,
    video_codec: str = "libx264",
) -> List[str]:
    cmd = ["ffmpeg", "-y", "-i", video]
    if filter_complex:
        cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]"])
    else:
        cmd.extend(["-map", "0:v:0"])
    cmd.extend(["-map", "0:a?"])
    cmd.extend(["-c:v", video_codec])
    if video_codec == "libx264":
        cmd.extend(["-preset", "medium", "-crf", "18"])
    cmd.extend(["-c:a", "copy" if copy_audio else "aac", "-movflags", "+faststart", output])
    return cmd


def summarize(events: Sequence[RedactionEvent], warnings: Sequence[Mapping[str, Any]], *, require_reviewed: bool, require_redactions: bool) -> Dict[str, Any]:
    by_label: Dict[str, int] = {}
    unreviewed = 0
    for event in events:
        by_label[event.label] = by_label.get(event.label, 0) + 1
        if not event.reviewed:
            unreviewed += 1
    blocking = 0
    if require_redactions and not events:
        blocking += 1
    if require_reviewed:
        blocking += unreviewed
    return {
        "total_events": len(events),
        "labels": by_label,
        "unreviewed": unreviewed,
        "warnings": len(warnings),
        "blocking": blocking,
    }


def build_plan(
    *,
    video: Optional[str],
    detection_paths: Sequence[str],
    manual_boxes: Sequence[str],
    width: int = 0,
    height: int = 0,
    method: str = "blur",
    scale: float = 1.15,
    frame_hold: float = 0.25,
    min_score: float = 0.0,
    include_labels: Sequence[str] = (),
    exclude_labels: Sequence[str] = (),
    require_reviewed: bool = False,
    require_redactions: bool = False,
    blur_radius: int = 0,
    pixel_blocks: int = 12,
    mask_color: str = "black@1.0",
    render_output: Optional[str] = None,
    copy_audio: bool = True,
    video_codec: str = "libx264",
) -> Dict[str, Any]:
    duration, probed_w, probed_h, fps, rotation = _video_info(video)
    width = width or probed_w
    height = height or probed_h

    events, warnings = load_events(
        detection_paths,
        manual_boxes,
        width=width,
        height=height,
        scale=scale,
        frame_hold=frame_hold,
        min_score=min_score,
        include_labels=include_labels,
        exclude_labels=exclude_labels,
    )
    events = merge_events(events)
    filter_complex = build_filter_complex(
        events,
        method=method,
        blur_radius=blur_radius,
        pixel_blocks=pixel_blocks,
        mask_color=mask_color,
    )
    ffmpeg_command: List[str] = []
    if video and render_output:
        ffmpeg_command = build_ffmpeg_command(
            video,
            render_output,
            filter_complex,
            copy_audio=copy_audio,
            video_codec=video_codec,
        )

    warnings = list(warnings)
    summary = summarize(events, warnings, require_reviewed=require_reviewed, require_redactions=require_redactions)
    if render_output and not video:
        summary["blocking"] += 1
        warnings.append({"source": "cli", "message": "--render-output requires --video"})
        summary["warnings"] = len(warnings)

    return {
        "version": "privacy_redaction_plan.v1",
        "generated_at": utc_now(),
        "source": {
            "video": video,
            "detections": list(detection_paths),
            "manual_boxes": len(manual_boxes),
        },
        "media": {
            "width": width,
            "height": height,
            "duration": duration,
            "fps": fps,
            "rotation": rotation,
        },
        "settings": {
            "method": method,
            "scale": scale,
            "frame_hold": frame_hold,
            "min_score": min_score,
            "include_labels": list(include_labels),
            "exclude_labels": list(exclude_labels),
            "require_reviewed": require_reviewed,
            "require_redactions": require_redactions,
            "blur_radius": blur_radius,
            "pixel_blocks": pixel_blocks,
            "mask_color": mask_color,
        },
        "summary": summary,
        "warnings": warnings,
        "events": [asdict(event) for event in events],
        "ffmpeg": {
            "filter_complex": filter_complex,
            "command": ffmpeg_command,
            "shell_command": " ".join(shlex.quote(part) for part in ffmpeg_command) if ffmpeg_command else "",
            "render_output": render_output,
        },
        "notes": [
            "This script consumes reviewed/manual or detector-produced boxes; it does not run detection models.",
            "For high-risk privacy work, review the Markdown table and run with --require-reviewed --strict.",
        ],
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    media = plan.get("media") or {}
    lines = [
        "# Privacy Redaction Review",
        "",
        f"- Status: **{'BLOCKED' if summary.get('blocking') else 'READY'}**",
        f"- Events: {summary.get('total_events', 0)}",
        f"- Unreviewed: {summary.get('unreviewed', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Media: {media.get('width', 0)}x{media.get('height', 0)} · {float(media.get('duration') or 0):.2f}s",
        "",
        "## Events",
        "",
        "| id | time | label | box | method | score | reviewed | source |",
        "|---|---:|---|---|---|---:|---:|---|",
    ]
    for event in plan.get("events") or []:
        score = "-" if event.get("score") is None else f"{float(event.get('score')):.2f}"
        method = event.get("method") or (plan.get("settings") or {}).get("method") or "-"
        box = f"{event.get('x')},{event.get('y')} {event.get('w')}x{event.get('h')}"
        lines.append(
            "| {id} | {start:.2f}-{end:.2f} | {label} | `{box}` | {method} | {score} | {reviewed} | `{source}` |".format(
                id=event.get("id", ""),
                start=float(event.get("start") or 0),
                end=float(event.get("end") or 0),
                label=event.get("label", ""),
                box=box,
                method=method,
                score=score,
                reviewed="yes" if event.get("reviewed") else "no",
                source=os.path.basename(str(event.get("source") or "")) or "-",
            )
        )

    warnings = plan.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning.get('message', '')} ({warning.get('source', '-')})")

    shell_command = (plan.get("ffmpeg") or {}).get("shell_command")
    if shell_command:
        lines.extend(["", "## FFmpeg Command", "", "```bash", shell_command, "```"])

    return "\n".join(lines).rstrip() + "\n"


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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a privacy redaction plan from manual boxes or detector JSON."
    )
    parser.add_argument("--video", help="Input video path. Used for probing dimensions and optional render.")
    parser.add_argument("--detections", action="append", default=[], help="Detector/review JSON path; can repeat.")
    parser.add_argument(
        "--box",
        action="append",
        default=[],
        help="Manual box: start:end:x,y,w,h[:label[:reviewed]]. Coordinates are pixels.",
    )
    parser.add_argument("--width", type=int, default=0, help="Frame width when no --video is available.")
    parser.add_argument("--height", type=int, default=0, help="Frame height when no --video is available.")
    parser.add_argument("--output", default="privacy_redaction.json", help="Output JSON plan path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--render-output", help="Optional redacted MP4 output path. If omitted, no render is run.")
    parser.add_argument("--method", choices=["blur", "pixelate", "solid"], default="blur", help="Default redaction effect.")
    parser.add_argument("--scale", type=float, default=1.15, help="Expand boxes around their center for safer coverage.")
    parser.add_argument("--frame-hold", type=float, default=0.25, help="Duration for frame-level detections without end time.")
    parser.add_argument("--min-score", type=float, default=0.0, help="Drop detector boxes below this score.")
    parser.add_argument("--label", action="append", default=[], help="Only include this label; can repeat.")
    parser.add_argument("--exclude-label", action="append", default=[], help="Exclude this label; can repeat.")
    parser.add_argument("--require-reviewed", action="store_true", help="Mark unreviewed events as blocking.")
    parser.add_argument("--require-redactions", action="store_true", help="Mark zero redaction events as blocking.")
    parser.add_argument("--blur-radius", type=int, default=0, help="FFmpeg boxblur radius. 0 derives from box size.")
    parser.add_argument("--pixel-blocks", type=int, default=12, help="Pixelation block divisor; lower is stronger.")
    parser.add_argument("--mask-color", default="black@1.0", help="FFmpeg color for --method solid.")
    parser.add_argument("--video-codec", default="libx264", help="Video codec for optional render.")
    parser.add_argument("--transcode-audio", action="store_true", help="Use AAC instead of copying audio.")
    parser.add_argument("--dry-run", action="store_true", help="Write plan and command but do not execute render.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 if the redaction gate is blocking.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(
            video=args.video,
            detection_paths=args.detections,
            manual_boxes=args.box,
            width=args.width,
            height=args.height,
            method=args.method,
            scale=args.scale,
            frame_hold=args.frame_hold,
            min_score=args.min_score,
            include_labels=args.label,
            exclude_labels=args.exclude_label,
            require_reviewed=args.require_reviewed,
            require_redactions=args.require_redactions,
            blur_radius=args.blur_radius,
            pixel_blocks=args.pixel_blocks,
            mask_color=args.mask_color,
            render_output=args.render_output,
            copy_audio=not args.transcode_audio,
            video_codec=args.video_codec,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"privacy_redact: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, plan)
    if args.markdown:
        write_text(args.markdown, emit_markdown(plan))

    command = (plan.get("ffmpeg") or {}).get("command") or []
    if args.render_output and command and not args.dry_run and not (plan.get("summary") or {}).get("blocking"):
        result = subprocess.run(command)
        if result.returncode != 0:
            return result.returncode

    summary = plan["summary"]
    print(
        "Privacy redaction plan: "
        f"events={summary['total_events']} "
        f"unreviewed={summary['unreviewed']} "
        f"warnings={summary['warnings']} "
        f"blocking={summary['blocking']}",
        file=sys.stderr,
    )
    if args.strict and summary["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
