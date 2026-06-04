#!/usr/bin/env python3
"""Build subject-aware crop/letterbox plans for platform exports.

The script intentionally does not run YOLO/MediaPipe. It consumes detector
output from any tool, turns it into a small auditable reframe plan, and emits
FFmpeg filters that `multi_export.py` can apply.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info  # noqa: E402


VERSION = "smart_reframe.v1"

PLATFORM_TARGETS = {
    "xhs": (1080, 1440),
    "douyin": (1080, 1920),
    "wxch": (1080, 1920),
}

LABEL_WEIGHTS = {
    "face": 1.45,
    "head": 1.35,
    "active_speaker": 1.30,
    "speaker": 1.25,
    "person": 1.00,
    "body": 0.95,
    "subject": 0.90,
    "object": 0.55,
}


def _round3(value: float) -> float:
    return round(float(value), 3)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_target_size(value: Optional[str], platform: str) -> Tuple[int, int]:
    if value:
        cleaned = value.lower().replace("x", ":").replace("X", ":")
        parts = cleaned.split(":", 1)
        if len(parts) != 2:
            raise ValueError("--target-size must look like 1080x1920")
        width = int(parts[0])
        height = int(parts[1])
        if width <= 0 or height <= 0:
            raise ValueError("--target-size dimensions must be positive")
        return width, height
    return PLATFORM_TARGETS[platform]


def target_crop_dimensions(
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
) -> Tuple[int, int]:
    """Return the source-space crop size needed to fill the target aspect."""
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        raise ValueError("dimensions must be positive")

    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if abs(src_ratio - dst_ratio) < 1e-3:
        return src_w, src_h
    if src_ratio > dst_ratio:
        return max(1, int(round(src_h * dst_ratio))), src_h
    return src_w, max(1, int(round(src_w / dst_ratio)))


def _coerce_bbox(raw: Any, src_w: int, src_h: int, *, fmt: Optional[str] = None) -> Optional[Tuple[float, float, float, float]]:
    if raw is None:
        return None

    if isinstance(raw, Mapping):
        if {"x1", "y1", "x2", "y2"} <= set(raw):
            coords = [raw.get("x1"), raw.get("y1"), raw.get("x2"), raw.get("y2")]
            fmt = fmt or "xyxy"
        elif {"left", "top", "right", "bottom"} <= set(raw):
            coords = [raw.get("left"), raw.get("top"), raw.get("right"), raw.get("bottom")]
            fmt = fmt or "xyxy"
        elif {"x", "y", "w", "h"} <= set(raw):
            coords = [raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h")]
            fmt = fmt or "xywh"
        elif {"x", "y", "width", "height"} <= set(raw):
            coords = [raw.get("x"), raw.get("y"), raw.get("width"), raw.get("height")]
            fmt = fmt or "xywh"
        else:
            return None
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 4:
        coords = list(raw[:4])
    else:
        return None

    parsed = [_float_or_none(v) for v in coords]
    if any(v is None for v in parsed):
        return None
    x1, y1, x2, y2 = [float(v) for v in parsed if v is not None]

    if (fmt or "").lower() in {"xywh", "ltwh"}:
        x2 = x1 + x2
        y2 = y1 + y2

    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        x1 *= src_w
        x2 *= src_w
        y1 *= src_h
        y2 *= src_h

    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1 = _clamp(x1, 0, src_w)
    x2 = _clamp(x2, 0, src_w)
    y1 = _clamp(y1, 0, src_h)
    y2 = _clamp(y2, 0, src_h)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return x1, y1, x2, y2


def _record_time(record: Mapping[str, Any], inherited_time: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    start = _float_or_none(record.get("start", record.get("start_time")))
    end = _float_or_none(record.get("end", record.get("end_time")))
    time = _float_or_none(
        record.get(
            "time",
            record.get("timestamp", record.get("t", inherited_time)),
        )
    )
    if time is None and start is not None and end is not None:
        time = (start + end) / 2
    return time, start, end


def _iter_detection_records(payload: Any, inherited_time: Optional[float] = None) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_detection_records(item, inherited_time)
        return
    if not isinstance(payload, Mapping):
        return

    frame_time = _float_or_none(
        payload.get("time", payload.get("timestamp", payload.get("t", inherited_time)))
    )
    for key in ("frames", "detections", "objects", "boxes", "segments"):
        children = payload.get(key)
        if isinstance(children, list):
            for child in children:
                yield from _iter_detection_records(child, frame_time)

    if any(key in payload for key in ("bbox", "box", "rect", "roi", "face_box", "person_box")):
        record = dict(payload)
        if inherited_time is not None and "time" not in record and "timestamp" not in record and "t" not in record:
            record["time"] = inherited_time
        yield record


def normalize_detections(payload: Any, src_w: int, src_h: int) -> List[Dict[str, Any]]:
    """Normalize heterogeneous detector JSON into time-box records."""
    detections: List[Dict[str, Any]] = []
    for record in _iter_detection_records(payload):
        fmt = record.get("bbox_format") or record.get("format")
        time, start, end = _record_time(record, None)
        confidence = _float_or_none(record.get("confidence", record.get("score")))
        confidence = _clamp(confidence if confidence is not None else 1.0, 0.05, 1.0)
        speaker = record.get("speaker", record.get("speaker_id"))

        candidates: List[Tuple[str, Any]] = []
        if record.get("face_box") is not None:
            candidates.append(("face", record.get("face_box")))
        if record.get("person_box") is not None:
            candidates.append(("person", record.get("person_box")))
        for key in ("bbox", "box", "rect", "roi"):
            if record.get(key) is not None:
                label = str(record.get("label", record.get("class", record.get("type", "subject")))).lower()
                candidates.append((label, record.get(key)))

        for label, bbox_raw in candidates:
            bbox = _coerce_bbox(bbox_raw, src_w, src_h, fmt=str(fmt).lower() if fmt else None)
            if bbox is None:
                continue
            detections.append(
                {
                    "time": _round3(time) if time is not None else None,
                    "start": _round3(start) if start is not None else None,
                    "end": _round3(end) if end is not None else None,
                    "label": label,
                    "bbox": [_round3(v) for v in bbox],
                    "confidence": _round3(confidence),
                    "speaker": speaker,
                }
            )

    detections.sort(key=lambda item: (item.get("time") is None, item.get("time") or item.get("start") or 0.0))
    return detections


def _scene_intervals(scene_plan: Optional[Mapping[str, Any]], duration: float) -> List[Dict[str, Any]]:
    if scene_plan:
        scenes = scene_plan.get("scenes")
        if isinstance(scenes, list) and scenes:
            intervals = []
            for idx, scene in enumerate(scenes, start=1):
                start = _float_or_none(scene.get("start"))
                end = _float_or_none(scene.get("end"))
                if start is None or end is None or end <= start:
                    continue
                intervals.append(
                    {
                        "scene_id": scene.get("scene_id") or f"scene_{idx:03d}",
                        "start": max(0.0, start),
                        "end": min(duration, end),
                    }
                )
            if intervals:
                return intervals

        boundaries = scene_plan.get("boundaries")
        if isinstance(boundaries, list):
            points = [0.0]
            points.extend(float(p) for p in boundaries if _float_or_none(p) is not None and 0.0 < float(p) < duration)
            points.append(duration)
            points = sorted(set(_round3(p) for p in points))
            return [
                {"scene_id": f"scene_{idx:03d}", "start": start, "end": end}
                for idx, (start, end) in enumerate(zip(points, points[1:]), start=1)
                if end > start
            ]

    return [{"scene_id": "scene_001", "start": 0.0, "end": duration}]


def _detection_overlaps_interval(detection: Mapping[str, Any], start: float, end: float) -> bool:
    det_start = _float_or_none(detection.get("start"))
    det_end = _float_or_none(detection.get("end"))
    if det_start is not None or det_end is not None:
        det_start = det_start if det_start is not None else det_end
        det_end = det_end if det_end is not None else det_start
        return det_start is not None and det_end is not None and det_end >= start and det_start <= end
    time = _float_or_none(detection.get("time"))
    return time is not None and start <= time <= end


def _weighted_focus(detections: Sequence[Mapping[str, Any]]) -> Tuple[float, float, List[float], Dict[str, int]]:
    total = 0.0
    sum_x = 0.0
    sum_y = 0.0
    xs: List[float] = []
    ys: List[float] = []
    labels: Dict[str, int] = {}
    for detection in detections:
        x1, y1, x2, y2 = [float(v) for v in detection["bbox"]]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        label = str(detection.get("label") or "subject").lower()
        labels[label] = labels.get(label, 0) + 1
        confidence = _float_or_none(detection.get("confidence")) or 1.0
        weight = LABEL_WEIGHTS.get(label, LABEL_WEIGHTS["object"]) * confidence
        total += weight
        sum_x += cx * weight
        sum_y += cy * weight
        xs.extend([x1, x2])
        ys.extend([y1, y2])
    return sum_x / total, sum_y / total, [min(xs), min(ys), max(xs), max(ys)], labels


def _crop_at_focus(
    focus_x: float,
    focus_y: float,
    src_w: int,
    src_h: int,
    crop_w: int,
    crop_h: int,
) -> Dict[str, Any]:
    x = int(round(_clamp(focus_x - crop_w / 2, 0, max(0, src_w - crop_w))))
    y = int(round(_clamp(focus_y - crop_h / 2, 0, max(0, src_h - crop_h))))
    return {
        "width": int(crop_w),
        "height": int(crop_h),
        "x": x,
        "y": y,
        "focus_x": _round3(focus_x / src_w),
        "focus_y": _round3(focus_y / src_h),
    }


def _build_segment(
    interval: Mapping[str, Any],
    detections: Sequence[Mapping[str, Any]],
    *,
    src_w: int,
    src_h: int,
    crop_w: int,
    crop_h: int,
    allow_letterbox: bool,
    wide_subject_threshold: float,
) -> Dict[str, Any]:
    start = float(interval["start"])
    end = float(interval["end"])
    matching = [d for d in detections if _detection_overlaps_interval(d, start, end)]

    if not matching:
        focus_x = src_w / 2
        focus_y = src_h / 2
        return {
            "scene_id": interval.get("scene_id"),
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
            "strategy": "center",
            "crop": _crop_at_focus(focus_x, focus_y, src_w, src_h, crop_w, crop_h),
            "subject": {"detections": 0, "labels": {}, "union_box": None},
            "reason": "no detection in interval; center crop fallback",
            "warnings": ["no_subject_detection"],
        }

    focus_x, focus_y, union_box, labels = _weighted_focus(matching)
    span_w = union_box[2] - union_box[0]
    span_h = union_box[3] - union_box[1]
    if allow_letterbox and (span_w > crop_w * wide_subject_threshold or span_h > crop_h * wide_subject_threshold):
        return {
            "scene_id": interval.get("scene_id"),
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
            "strategy": "letterbox",
            "crop": None,
            "subject": {
                "detections": len(matching),
                "labels": labels,
                "union_box": [_round3(v) for v in union_box],
            },
            "reason": "subject group wider than target crop; preserve full frame with padding",
            "warnings": ["letterbox_selected"],
        }

    return {
        "scene_id": interval.get("scene_id"),
        "start": _round3(start),
        "end": _round3(end),
        "duration": _round3(end - start),
        "strategy": "track",
        "crop": _crop_at_focus(focus_x, focus_y, src_w, src_h, crop_w, crop_h),
        "subject": {
            "detections": len(matching),
            "labels": labels,
            "union_box": [_round3(v) for v in union_box],
        },
        "reason": "weighted subject focus from detector boxes",
        "warnings": [],
    }


def _merge_adjacent_segments(segments: Sequence[Mapping[str, Any]], tolerance_px: int) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for segment in segments:
        current = dict(segment)
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        contiguous = abs(float(previous["end"]) - float(current["start"])) <= 0.02
        same_strategy = previous.get("strategy") == current.get("strategy")
        can_merge = contiguous and same_strategy
        if can_merge and current.get("strategy") in {"track", "center"}:
            prev_crop = previous.get("crop") or {}
            curr_crop = current.get("crop") or {}
            can_merge = (
                prev_crop.get("width") == curr_crop.get("width")
                and prev_crop.get("height") == curr_crop.get("height")
                and abs(int(prev_crop.get("x", 0)) - int(curr_crop.get("x", 0))) <= tolerance_px
                and abs(int(prev_crop.get("y", 0)) - int(curr_crop.get("y", 0))) <= tolerance_px
            )
        if can_merge:
            previous["end"] = current["end"]
            previous["duration"] = _round3(float(previous["end"]) - float(previous["start"]))
            previous["warnings"] = sorted(set(previous.get("warnings", []) + current.get("warnings", [])))
            previous["reason"] = previous.get("reason", "")
            if "merged with adjacent interval" not in previous["reason"]:
                previous["reason"] = (previous["reason"] + "; merged with adjacent interval").strip("; ")
            prev_subject = previous.get("subject") or {}
            curr_subject = current.get("subject") or {}
            prev_subject["detections"] = int(prev_subject.get("detections") or 0) + int(curr_subject.get("detections") or 0)
            labels = dict(prev_subject.get("labels") or {})
            for label, count in (curr_subject.get("labels") or {}).items():
                labels[label] = labels.get(label, 0) + int(count)
            prev_subject["labels"] = labels
            previous["subject"] = prev_subject
        else:
            merged.append(current)

    for idx, segment in enumerate(merged, start=1):
        segment["id"] = f"reframe_{idx:03d}"
    return merged


def build_reframe_plan(
    *,
    video_path: str,
    src_w: int,
    src_h: int,
    duration: float,
    dst_w: int,
    dst_h: int,
    platform: str,
    detections_payload: Optional[Any] = None,
    scene_plan: Optional[Mapping[str, Any]] = None,
    allow_letterbox: bool = True,
    wide_subject_threshold: float = 0.92,
    merge_tolerance_px: int = 8,
) -> Dict[str, Any]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    crop_w, crop_h = target_crop_dimensions(src_w, src_h, dst_w, dst_h)
    detections = normalize_detections(detections_payload, src_w, src_h) if detections_payload is not None else []
    intervals = _scene_intervals(scene_plan, duration)
    raw_segments = [
        _build_segment(
            interval,
            detections,
            src_w=src_w,
            src_h=src_h,
            crop_w=crop_w,
            crop_h=crop_h,
            allow_letterbox=allow_letterbox,
            wide_subject_threshold=wide_subject_threshold,
        )
        for interval in intervals
    ]
    segments = _merge_adjacent_segments(raw_segments, merge_tolerance_px)

    strategy_counts: Dict[str, int] = {}
    warnings: Dict[str, int] = {}
    for segment in segments:
        strategy = str(segment["strategy"])
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
        for warning in segment.get("warnings", []):
            warnings[warning] = warnings.get(warning, 0) + 1

    return {
        "version": VERSION,
        "source": {
            "video": video_path,
            "width": int(src_w),
            "height": int(src_h),
            "duration": _round3(duration),
        },
        "target": {
            "platform": platform,
            "width": int(dst_w),
            "height": int(dst_h),
            "aspect": f"{dst_w}:{dst_h}",
        },
        "params": {
            "detector_dependency": "external_json",
            "allow_letterbox": bool(allow_letterbox),
            "wide_subject_threshold": float(wide_subject_threshold),
            "merge_tolerance_px": int(merge_tolerance_px),
            "source_crop_width": int(crop_w),
            "source_crop_height": int(crop_h),
        },
        "summary": {
            "detections": len(detections),
            "input_intervals": len(intervals),
            "segments": len(segments),
            "strategies": strategy_counts,
            "warnings": warnings,
            "fallback_center_segments": strategy_counts.get("center", 0),
            "letterbox_segments": strategy_counts.get("letterbox", 0),
        },
        "detections": detections,
        "segments": segments,
    }


def plan_matches_target(plan: Mapping[str, Any], width: int, height: int, platform: Optional[str] = None) -> bool:
    target = plan.get("target") or {}
    if int(target.get("width") or 0) != int(width):
        return False
    if int(target.get("height") or 0) != int(height):
        return False
    if platform and target.get("platform") not in {platform, "custom"}:
        return False
    return True


def _segment_filter(segment: Mapping[str, Any], dst_w: int, dst_h: int) -> str:
    if segment.get("strategy") == "letterbox":
        return (
            f"scale={dst_w}:{dst_h}:force_original_aspect_ratio=decrease,"
            f"pad={dst_w}:{dst_h}:(ow-iw)/2:(oh-ih)/2"
        )
    crop = segment.get("crop") or {}
    return "crop={width}:{height}:{x}:{y},scale={dst_w}:{dst_h}".format(
        width=int(crop["width"]),
        height=int(crop["height"]),
        x=int(crop["x"]),
        y=int(crop["y"]),
        dst_w=int(dst_w),
        dst_h=int(dst_h),
    )


def build_reframe_vf(plan: Mapping[str, Any]) -> Optional[str]:
    segments = plan.get("segments") or []
    if len(segments) != 1:
        return None
    target = plan.get("target") or {}
    return _segment_filter(segments[0], int(target["width"]), int(target["height"]))


def build_reframe_filter_complex(plan: Mapping[str, Any], *, output_label: str = "vout") -> str:
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("reframe plan has no segments")
    target = plan.get("target") or {}
    dst_w = int(target["width"])
    dst_h = int(target["height"])
    parts: List[str] = []
    labels: List[str] = []
    for idx, segment in enumerate(segments):
        label = f"vr{idx}"
        labels.append(f"[{label}]")
        parts.append(
            "[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,{filters}[{label}]".format(
                start=float(segment["start"]),
                end=float(segment["end"]),
                filters=_segment_filter(segment, dst_w, dst_h),
                label=label,
            )
        )
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[{output_label}]")
    return ";".join(parts)


def load_reframe_plan(path: str) -> Dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, Mapping) or payload.get("version") != VERSION:
        raise ValueError(f"{path} is not a {VERSION} plan")
    return dict(payload)


def format_time(seconds: float) -> str:
    minutes = int(max(0.0, seconds) // 60)
    rest = max(0.0, seconds) - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    target = plan.get("target") or {}
    summary = plan.get("summary") or {}
    lines = [
        "# Smart Reframe Plan",
        "",
        f"- Source: `{source.get('video', '')}` ({source.get('width')}x{source.get('height')}, {source.get('duration')}s)",
        f"- Target: `{target.get('platform')}` {target.get('width')}x{target.get('height')}",
        f"- Segments: `{summary.get('segments', 0)}`",
        f"- Strategies: `{summary.get('strategies', {})}`",
        f"- Warnings: `{summary.get('warnings', {})}`",
        "",
        "| Segment | Time | Strategy | Crop | Subject | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for segment in plan.get("segments", []):
        crop = segment.get("crop")
        if crop:
            crop_text = "{width}x{height}+{x}+{y}".format(**crop)
        else:
            crop_text = "letterbox"
        subject = segment.get("subject") or {}
        labels = subject.get("labels") or {}
        lines.append(
            "| {id} | {start}-{end} | {strategy} | `{crop}` | {detections} detections {labels} | {reason} |".format(
                id=segment.get("id"),
                start=format_time(float(segment.get("start", 0))),
                end=format_time(float(segment.get("end", 0))),
                strategy=segment.get("strategy"),
                crop=crop_text,
                detections=subject.get("detections", 0),
                labels=labels,
                reason=segment.get("reason", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Detector JSON is external: run your preferred YOLO/MediaPipe/face detector first, then pass it with `--detections`.",
            "- `center` segments are safe fallbacks but should be reviewed when the subject is off-center.",
            "- `letterbox` segments preserve group shots that are too wide for the target crop.",
            "- Use this plan with `multi_export.py --reframe-plan` for the matching platform/target size.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a subject-aware smart reframe plan.")
    parser.add_argument("input", nargs="?", help="Input video path. Optional if --source-width/height/duration are supplied.")
    parser.add_argument("--detections", help="Detector JSON with bbox/time records")
    parser.add_argument("--scene-boundaries", help="scene_boundaries.py JSON, used as reframe intervals")
    parser.add_argument("--platform", default="douyin", choices=sorted(PLATFORM_TARGETS), help="Target platform preset")
    parser.add_argument("--target-size", help="Override target size, e.g. 1080x1920")
    parser.add_argument("--source-width", type=int, help="Source width override")
    parser.add_argument("--source-height", type=int, help="Source height override")
    parser.add_argument("--duration", type=float, help="Source duration override")
    parser.add_argument("--output", required=True, help="Output reframe plan JSON")
    parser.add_argument("--markdown", help="Optional Markdown review packet")
    parser.add_argument("--no-letterbox-wide-groups", action="store_true", help="Track wide groups instead of preserving full frame")
    parser.add_argument("--wide-subject-threshold", type=float, default=0.92, help="Letterbox when subject span exceeds this crop fraction")
    parser.add_argument("--merge-tolerance-px", type=int, default=8, help="Merge adjacent crop intervals within this pixel delta")
    parser.add_argument("--strict", action="store_true", help="Exit 2 if any segment falls back to center crop")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        dst_w, dst_h = parse_target_size(args.target_size, args.platform)
        if args.input and (args.source_width is None or args.source_height is None or args.duration is None):
            duration, src_w, src_h, _fps, _rot = get_video_info(args.input)
        else:
            if args.source_width is None or args.source_height is None or args.duration is None:
                print("Error: provide input video or --source-width --source-height --duration", file=sys.stderr)
                return 1
            src_w = args.source_width
            src_h = args.source_height
            duration = args.duration

        detections_payload = _read_json(args.detections) if args.detections else None
        scene_plan = _read_json(args.scene_boundaries) if args.scene_boundaries else None
        plan = build_reframe_plan(
            video_path=args.input or "(metadata-only)",
            src_w=int(src_w),
            src_h=int(src_h),
            duration=float(duration),
            dst_w=dst_w,
            dst_h=dst_h,
            platform=args.platform,
            detections_payload=detections_payload,
            scene_plan=scene_plan,
            allow_letterbox=not args.no_letterbox_wide_groups,
            wide_subject_threshold=args.wide_subject_threshold,
            merge_tolerance_px=args.merge_tolerance_px,
        )
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    print(
        "Smart reframe: {segments} segments, strategies {strategies}, wrote {path}".format(
            segments=plan["summary"]["segments"],
            strategies=plan["summary"]["strategies"],
            path=args.output,
        )
    )
    if args.strict and plan["summary"].get("fallback_center_segments", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
