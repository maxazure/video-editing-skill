#!/usr/bin/env python3
"""Build sampled-frame video understanding artifacts with optional YOLO.

The default mode has no ML dependency: it samples timeline frames and emits a
reviewable JSON shell. Passing ``--detector yolo`` imports Ultralytics at
runtime, runs object detection on sampled frames, then links detections into
lightweight tracklets that downstream scripts can consume.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info  # noqa: E402


VERSION = "video_understanding.v1"

PERSON_LABELS = {"person", "face", "head", "body", "hand"}
DEVICE_LABELS = {"cell phone", "phone", "mobile phone", "laptop", "tv", "monitor", "keyboard", "mouse"}
VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "bicycle", "train", "boat"}
FOOD_LABELS = {"bottle", "cup", "bowl", "banana", "apple", "sandwich", "orange", "cake", "pizza"}
ANIMAL_LABELS = {"cat", "dog", "bird", "horse", "sheep", "cow"}
SIGN_OBJECT_LABELS = {"book", "clock", "traffic light", "stop sign"}


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


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _tensor_to_list(value: Any) -> List[Any]:
    if value is None:
        return []
    for attr in ("detach", "cpu", "numpy"):
        method = getattr(value, attr, None)
        if callable(method):
            value = method()
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return [value]


def _safe_label(value: Any) -> str:
    label = str(value or "object").strip().lower()
    return label or "object"


def _bbox_area(box: Sequence[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _bbox_center(box: Sequence[float]) -> Tuple[float, float]:
    return (float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2


def _bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    x1 = max(float(a[0]), float(b[0]))
    y1 = max(float(a[1]), float(b[1]))
    x2 = min(float(a[2]), float(b[2]))
    y2 = min(float(a[3]), float(b[3]))
    inter = _bbox_area([x1, y1, x2, y2])
    if inter <= 0:
        return 0.0
    union = _bbox_area(a) + _bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def _coerce_bbox(raw: Any, width: int, height: int, *, fmt: str = "", unit: str = "") -> Optional[List[float]]:
    if raw is None:
        return None
    fmt = fmt.lower()
    unit = unit.lower()
    if isinstance(raw, Mapping):
        if {"x1", "y1", "x2", "y2"} <= set(raw):
            values = [raw["x1"], raw["y1"], raw["x2"], raw["y2"]]
            fmt = fmt or "xyxy"
        elif {"left", "top", "right", "bottom"} <= set(raw):
            values = [raw["left"], raw["top"], raw["right"], raw["bottom"]]
            fmt = fmt or "xyxy"
        elif {"x", "y", "w", "h"} <= set(raw):
            values = [raw["x"], raw["y"], raw["w"], raw["h"]]
            fmt = fmt or "xywh"
        elif {"x", "y", "width", "height"} <= set(raw):
            values = [raw["x"], raw["y"], raw["width"], raw["height"]]
            fmt = fmt or "xywh"
        else:
            return None
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 4:
        values = list(raw[:4])
    else:
        return None

    parsed = [_float_or_none(v) for v in values]
    if any(v is None for v in parsed):
        return None
    x1, y1, x2, y2 = [float(v) for v in parsed if v is not None]
    if fmt in {"xywh", "ltwh"}:
        x2 = x1 + x2
        y2 = y1 + y2

    normalized = unit in {"normalized", "relative", "ratio", "0-1"}
    if not normalized and max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        normalized = True
    if normalized:
        if width <= 0 or height <= 0:
            return None
        x1 *= width
        x2 *= width
        y1 *= height
        y2 *= height

    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if width > 0:
        x1 = _clamp(x1, 0.0, float(width))
        x2 = _clamp(x2, 0.0, float(width))
    if height > 0:
        y1 = _clamp(y1, 0.0, float(height))
        y2 = _clamp(y2, 0.0, float(height))
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return [_round3(x1), _round3(y1), _round3(x2), _round3(y2)]


def _scene_intervals(scene_plan: Optional[Mapping[str, Any]], duration: float) -> List[Dict[str, Any]]:
    if scene_plan:
        raw_scenes = scene_plan.get("scenes")
        if isinstance(raw_scenes, list):
            scenes: List[Dict[str, Any]] = []
            for index, scene in enumerate(raw_scenes, start=1):
                if not isinstance(scene, Mapping):
                    continue
                start = _float_or_none(scene.get("start"))
                end = _float_or_none(scene.get("end"))
                if start is None or end is None or end <= start:
                    continue
                scenes.append(
                    {
                        "scene_id": str(scene.get("scene_id") or f"scene_{index:03d}"),
                        "start": _round3(_clamp(start, 0.0, duration)),
                        "end": _round3(_clamp(end, 0.0, duration)),
                    }
                )
            if scenes:
                return [scene for scene in scenes if scene["end"] > scene["start"]]

        raw_boundaries = scene_plan.get("boundaries")
        if isinstance(raw_boundaries, list):
            points = [0.0]
            for value in raw_boundaries:
                point = _float_or_none(value)
                if point is not None and 0.0 < point < duration:
                    points.append(_round3(point))
            points.append(_round3(duration))
            points = sorted(set(points))
            return [
                {"scene_id": f"scene_{index:03d}", "start": start, "end": end}
                for index, (start, end) in enumerate(zip(points, points[1:]), start=1)
                if end > start
            ]
    return [{"scene_id": "scene_001", "start": 0.0, "end": _round3(duration)}]


def scene_id_for_time(time_value: float, scene_plan: Optional[Mapping[str, Any]], duration: float) -> Optional[str]:
    for scene in _scene_intervals(scene_plan, duration):
        if float(scene["start"]) <= time_value <= float(scene["end"]):
            return str(scene["scene_id"])
    return None


def sample_times(
    duration: float,
    *,
    sample_interval: float = 2.0,
    max_frames: int = 24,
    scene_plan: Optional[Mapping[str, Any]] = None,
    mode: str = "scene-and-interval",
) -> List[float]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if sample_interval <= 0:
        raise ValueError("sample_interval must be positive")
    if max_frames <= 0:
        raise ValueError("max_frames must be positive")

    candidates: List[float] = []
    first = min(0.5, max(0.0, duration / 2))
    candidates.append(first)

    if mode in {"interval", "scene-and-interval"}:
        t = first + sample_interval
        while t < duration:
            candidates.append(t)
            t += sample_interval

    if mode == "scene-and-interval" and scene_plan:
        for scene in _scene_intervals(scene_plan, duration):
            start = float(scene["start"])
            end = float(scene["end"])
            span = end - start
            if span <= 0:
                continue
            candidates.extend(
                [
                    start + min(0.35, span / 3),
                    start + span / 2,
                    end - min(0.35, span / 3),
                ]
            )

    candidates.append(max(0.0, duration - min(0.5, duration / 2)))
    cleaned = sorted(
        {
            _round3(_clamp(value, 0.0, max(0.0, duration - 0.001)))
            for value in candidates
            if math.isfinite(float(value))
        }
    )

    deduped: List[float] = []
    for value in cleaned:
        if not deduped or value - deduped[-1] >= 0.12:
            deduped.append(value)
    if len(deduped) <= max_frames:
        return deduped

    if max_frames == 1:
        return [deduped[0]]
    step = (len(deduped) - 1) / (max_frames - 1)
    indices = sorted({int(round(i * step)) for i in range(max_frames)})
    selected = [deduped[i] for i in indices]
    while len(selected) > max_frames:
        selected.pop(-2)
    return selected


def extract_frame(video_path: str, time_value: float, output_path: str, *, overwrite: bool = False) -> None:
    if os.path.exists(output_path) and not overwrite:
        return
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{time_value:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg frame extraction failed").strip())


def build_frame_records(
    video_path: str,
    *,
    duration: float,
    width: int,
    height: int,
    scene_plan: Optional[Mapping[str, Any]],
    sample_interval: float,
    max_frames: int,
    sample_mode: str,
    frames_dir: str,
    extract: bool,
    overwrite: bool = False,
) -> List[Dict[str, Any]]:
    times = sample_times(
        duration,
        sample_interval=sample_interval,
        max_frames=max_frames,
        scene_plan=scene_plan,
        mode=sample_mode,
    )
    records: List[Dict[str, Any]] = []
    for index, time_value in enumerate(times, start=1):
        frame_id = f"frame_{index:04d}"
        filename = f"{frame_id}_t{int(round(time_value * 1000)):07d}.jpg"
        path = os.path.join(frames_dir, filename) if extract else ""
        if extract:
            extract_frame(video_path, time_value, path, overwrite=overwrite)
        records.append(
            {
                "frame_id": frame_id,
                "time": _round3(time_value),
                "path": path,
                "scene_id": scene_id_for_time(time_value, scene_plan, duration),
                "width": int(width),
                "height": int(height),
                "detections": [],
            }
        )
    return records


def _record_time(record: Mapping[str, Any], inherited_time: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    start = _float_or_none(record.get("start", record.get("start_time")))
    end = _float_or_none(record.get("end", record.get("end_time")))
    time_value = _float_or_none(record.get("time", record.get("timestamp", record.get("t", inherited_time))))
    if time_value is None and start is not None and end is not None:
        time_value = (start + end) / 2
    return time_value, start, end


def _iter_external_detections(
    payload: Any,
    *,
    inherited_time: Optional[float] = None,
    inherited_frame_id: Optional[str] = None,
    inherited_scene_id: Optional[str] = None,
) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_external_detections(
                item,
                inherited_time=inherited_time,
                inherited_frame_id=inherited_frame_id,
                inherited_scene_id=inherited_scene_id,
            )
        return
    if not isinstance(payload, Mapping):
        return

    time_value = _float_or_none(payload.get("time", payload.get("timestamp", payload.get("t", inherited_time))))
    frame_id = payload.get("frame_id", inherited_frame_id)
    scene_id = payload.get("scene_id", inherited_scene_id)

    for key in ("frames", "detections", "objects", "boxes", "segments", "items"):
        children = payload.get(key)
        if isinstance(children, list):
            for child in children:
                yield from _iter_external_detections(
                    child,
                    inherited_time=time_value,
                    inherited_frame_id=str(frame_id) if frame_id else None,
                    inherited_scene_id=str(scene_id) if scene_id else None,
                )

    if any(key in payload for key in ("bbox", "box", "rect", "roi", "xyxy", "bounds", "face_box", "person_box")):
        record = dict(payload)
        if time_value is not None and not any(key in record for key in ("time", "timestamp", "t")):
            record["time"] = time_value
        if frame_id is not None and "frame_id" not in record:
            record["frame_id"] = str(frame_id)
        if scene_id is not None and "scene_id" not in record:
            record["scene_id"] = str(scene_id)
        yield record


def normalize_external_detections(
    payload: Any,
    *,
    width: int,
    height: int,
    duration: float,
    source: str,
) -> List[Dict[str, Any]]:
    detections: List[Dict[str, Any]] = []
    for index, record in enumerate(_iter_external_detections(payload), start=1):
        fmt = str(record.get("bbox_format") or record.get("format") or "").lower()
        unit = str(record.get("unit") or record.get("bbox_unit") or record.get("coordinate_unit") or "").lower()
        time_value, start, end = _record_time(record, None)
        confidence = _float_or_none(record.get("confidence", record.get("score", record.get("probability"))))
        label = _safe_label(record.get("label", record.get("class_name", record.get("class", record.get("type")))))
        candidates: List[Tuple[str, Any]] = []
        if record.get("face_box") is not None:
            candidates.append(("face", record.get("face_box")))
        if record.get("person_box") is not None:
            candidates.append(("person", record.get("person_box")))
        for key in ("bbox", "box", "rect", "roi", "xyxy", "bounds"):
            if record.get(key) is not None:
                candidates.append((label, record.get(key)))

        for label_value, bbox_raw in candidates:
            bbox = _coerce_bbox(bbox_raw, width, height, fmt=fmt, unit=unit)
            if bbox is None:
                continue
            item = {
                "id": str(record.get("id") or f"det_{len(detections) + 1:05d}"),
                "frame_id": str(record.get("frame_id") or ""),
                "scene_id": str(record.get("scene_id") or ""),
                "time": _round3(time_value) if time_value is not None else None,
                "start": _round3(start) if start is not None else None,
                "end": _round3(end) if end is not None else None,
                "label": _safe_label(label_value),
                "bbox": bbox,
                "confidence": _round3(_clamp(confidence if confidence is not None else 1.0, 0.0, 1.0)),
                "source": str(record.get("source") or source),
            }
            if record.get("track_id") is not None:
                item["track_id"] = str(record.get("track_id"))
            if record.get("class_id") is not None:
                item["class_id"] = record.get("class_id")
            detections.append(item)
    detections.sort(key=lambda item: (item.get("time") is None, item.get("time") or item.get("start") or 0.0, item["id"]))
    for index, item in enumerate(detections, start=1):
        item["id"] = str(item.get("id") or f"det_{index:05d}")
        if item.get("time") is not None:
            item["time"] = _round3(_clamp(float(item["time"]), 0.0, duration))
    return detections


def parse_yolo_classes(value: Optional[str]) -> Optional[List[int]]:
    if not value:
        return None
    classes: List[int] = []
    for token in value.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            classes.append(int(token))
        except ValueError as exc:
            raise ValueError("--classes accepts numeric YOLO class ids, for example 0,62,63") from exc
    return classes or None


def run_yolo_detector(
    frames: Sequence[Mapping[str, Any]],
    *,
    model_name: str,
    conf: float,
    imgsz: int,
    device: Optional[str],
    classes: Optional[List[int]],
) -> List[Dict[str, Any]]:
    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError as exc:
        raise RuntimeError("ultralytics is not installed. Install it with `pip install ultralytics`.") from exc

    paths = [str(frame.get("path") or "") for frame in frames if frame.get("path")]
    if not paths:
        raise RuntimeError("YOLO detection requires extracted frames; remove --no-extract.")

    frame_by_path = {str(frame["path"]): frame for frame in frames if frame.get("path")}
    model = YOLO(model_name)
    kwargs: Dict[str, Any] = {"conf": float(conf), "imgsz": int(imgsz), "verbose": False}
    if device:
        kwargs["device"] = device
    if classes is not None:
        kwargs["classes"] = classes
    results = model(paths, **kwargs)

    detections: List[Dict[str, Any]] = []
    for result_index, result in enumerate(results):
        result_path = str(getattr(result, "path", "") or "")
        frame = frame_by_path.get(result_path)
        if frame is None:
            # Ultralytics can normalize paths differently. Fall back to order.
            index = min(result_index, len(frames) - 1)
            frame = frames[index]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        xyxy = _tensor_to_list(getattr(boxes, "xyxy", None))
        confs = _tensor_to_list(getattr(boxes, "conf", None))
        classes_out = _tensor_to_list(getattr(boxes, "cls", None))
        track_ids = _tensor_to_list(getattr(boxes, "id", None))
        names = getattr(result, "names", None) or getattr(model, "names", {})

        for idx, box in enumerate(xyxy):
            if not isinstance(box, Sequence) or len(box) < 4:
                continue
            class_id = int(float(classes_out[idx])) if idx < len(classes_out) else -1
            if isinstance(names, Mapping):
                label = _safe_label(names.get(class_id, f"class_{class_id}"))
            else:
                try:
                    label = _safe_label(names[class_id])
                except Exception:
                    label = f"class_{class_id}"
            confidence = float(confs[idx]) if idx < len(confs) else 1.0
            item = {
                "id": f"det_{len(detections) + 1:05d}",
                "frame_id": str(frame.get("frame_id") or ""),
                "scene_id": str(frame.get("scene_id") or ""),
                "time": frame.get("time"),
                "label": label,
                "class_id": class_id,
                "bbox": [_round3(float(v)) for v in list(box[:4])],
                "confidence": _round3(_clamp(confidence, 0.0, 1.0)),
                "source": f"ultralytics:{model_name}",
            }
            if idx < len(track_ids) and track_ids[idx] is not None:
                item["track_id"] = str(int(float(track_ids[idx])))
            detections.append(item)
    return detections


def _label_category(label: str) -> str:
    label = _safe_label(label)
    if label in PERSON_LABELS:
        return "person"
    if label in DEVICE_LABELS:
        return "device"
    if label in VEHICLE_LABELS:
        return "vehicle"
    if label in FOOD_LABELS:
        return "food"
    if label in ANIMAL_LABELS:
        return "animal"
    if label in SIGN_OBJECT_LABELS:
        return "sign_or_text"
    return "object"


def _assign_generated_track_ids(
    detections: Sequence[Dict[str, Any]],
    *,
    width: int,
    height: int,
    max_gap: float,
    iou_threshold: float,
) -> None:
    active: List[Dict[str, Any]] = []
    next_id = 1
    diagonal = math.hypot(max(width, 1), max(height, 1))

    for detection in sorted(detections, key=lambda item: (item.get("time") is None, item.get("time") or 0.0)):
        if detection.get("track_id"):
            continue
        time_value = _float_or_none(detection.get("time"))
        if time_value is None:
            detection["track_id"] = f"trk_{next_id:03d}"
            next_id += 1
            continue

        best_score = -1.0
        best_track: Optional[Dict[str, Any]] = None
        label = _safe_label(detection.get("label"))
        cx, cy = _bbox_center(detection["bbox"])
        for track in active:
            if track["label"] != label:
                continue
            gap = time_value - float(track["last_time"])
            if gap < -0.001 or gap > max_gap:
                continue
            iou = _bbox_iou(detection["bbox"], track["last_bbox"])
            last_cx, last_cy = _bbox_center(track["last_bbox"])
            center_distance = math.hypot(cx - last_cx, cy - last_cy)
            center_score = max(0.0, 1.0 - center_distance / max(diagonal * 0.18, 1.0))
            area_a = _bbox_area(detection["bbox"])
            area_b = _bbox_area(track["last_bbox"])
            area_score = min(area_a, area_b) / max(area_a, area_b, 1.0)
            score = iou + 0.35 * center_score + 0.15 * area_score
            if score > best_score:
                best_score = score
                best_track = track

        should_link = best_track is not None and (
            _bbox_iou(detection["bbox"], best_track["last_bbox"]) >= iou_threshold or best_score >= 0.42
        )
        if should_link and best_track is not None:
            detection["track_id"] = best_track["track_id"]
            best_track["last_time"] = time_value
            best_track["last_bbox"] = detection["bbox"]
        else:
            detection["track_id"] = f"trk_{next_id:03d}"
            active.append(
                {
                    "track_id": detection["track_id"],
                    "label": label,
                    "last_time": time_value,
                    "last_bbox": detection["bbox"],
                }
            )
            next_id += 1


def build_tracks(
    detections: Sequence[Dict[str, Any]],
    *,
    width: int,
    height: int,
    max_gap: float = 4.5,
    iou_threshold: float = 0.18,
) -> List[Dict[str, Any]]:
    mutable = list(detections)
    _assign_generated_track_ids(mutable, width=width, height=height, max_gap=max_gap, iou_threshold=iou_threshold)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for detection in mutable:
        track_id = str(detection.get("track_id") or "")
        if not track_id:
            continue
        grouped.setdefault(track_id, []).append(detection)

    tracks: List[Dict[str, Any]] = []
    frame_area = max(1.0, float(width) * float(height))
    for track_id, items in grouped.items():
        items = sorted(items, key=lambda item: item.get("time") or item.get("start") or 0.0)
        times = [
            float(value)
            for value in (item.get("time", item.get("start")) for item in items)
            if _float_or_none(value) is not None
        ]
        labels: Dict[str, int] = {}
        confidences: List[float] = []
        centers: List[Tuple[float, float]] = []
        xs: List[float] = []
        ys: List[float] = []
        max_area_ratio = 0.0
        for item in items:
            label = _safe_label(item.get("label"))
            labels[label] = labels.get(label, 0) + 1
            confidence = _float_or_none(item.get("confidence"))
            if confidence is not None:
                confidences.append(confidence)
            box = [float(v) for v in item["bbox"]]
            centers.append(_bbox_center(box))
            xs.extend([box[0], box[2]])
            ys.extend([box[1], box[3]])
            max_area_ratio = max(max_area_ratio, _bbox_area(box) / frame_area)

        motion = 0.0
        for prev, curr in zip(centers, centers[1:]):
            motion += math.hypot(curr[0] - prev[0], curr[1] - prev[1])
        dominant_label = sorted(labels.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        tracks.append(
            {
                "track_id": track_id,
                "label": dominant_label,
                "category": _label_category(dominant_label),
                "start": _round3(min(times)) if times else None,
                "end": _round3(max(times)) if times else None,
                "frames": len({str(item.get("frame_id") or "") for item in items if item.get("frame_id")}),
                "detections": len(items),
                "mean_confidence": _round3(sum(confidences) / len(confidences)) if confidences else None,
                "max_area_ratio": _round3(max_area_ratio),
                "motion_distance_px": _round3(motion),
                "motion_distance_ratio": _round3(motion / max(math.hypot(width, height), 1.0)),
                "bbox_union": [_round3(min(xs)), _round3(min(ys)), _round3(max(xs)), _round3(max(ys))] if xs and ys else None,
            }
        )
    tracks.sort(key=lambda item: (item["start"] is None, item["start"] or 0.0, item["track_id"]))
    return tracks


def _nearest_frame_id(frames: Sequence[Mapping[str, Any]], time_value: Optional[float]) -> str:
    if time_value is None or not frames:
        return ""
    nearest = min(frames, key=lambda frame: abs(float(frame.get("time") or 0.0) - time_value))
    return str(nearest.get("frame_id") or "")


def attach_detections_to_frames(frames: Sequence[Dict[str, Any]], detections: Sequence[Dict[str, Any]]) -> None:
    by_id = {str(frame["frame_id"]): frame for frame in frames}
    for frame in frames:
        frame["detections"] = []
    for detection in detections:
        frame_id = str(detection.get("frame_id") or "")
        if not frame_id:
            frame_id = _nearest_frame_id(frames, _float_or_none(detection.get("time")))
            if frame_id:
                detection["frame_id"] = frame_id
        if frame_id in by_id:
            by_id[frame_id]["detections"].append(
                {
                    key: value
                    for key, value in detection.items()
                    if key not in {"start", "end"} or value is not None
                }
            )


def build_scene_tags(detections: Sequence[Mapping[str, Any]], tracks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for detection in detections:
        category = _label_category(str(detection.get("label") or "object"))
        counts[category] = counts.get(category, 0) + 1

    tags: List[Dict[str, Any]] = []
    for category, count in sorted(counts.items()):
        tags.append({"tag": category, "evidence": count, "source": "detections"})
    if any(float(track.get("max_area_ratio") or 0.0) >= 0.22 for track in tracks):
        tags.append({"tag": "dominant_subject", "evidence": 1, "source": "tracks"})
    if any(float(track.get("motion_distance_ratio") or 0.0) >= 0.12 for track in tracks):
        tags.append({"tag": "moving_subject", "evidence": 1, "source": "tracks"})
    if not tags:
        tags.append({"tag": "no_detected_objects", "evidence": 0, "source": "summary"})
    return tags


def summarize_labels(detections: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    labels: Dict[str, int] = {}
    for detection in detections:
        label = _safe_label(detection.get("label"))
        labels[label] = labels.get(label, 0) + 1
    return dict(sorted(labels.items(), key=lambda pair: (-pair[1], pair[0])))


def build_understanding_plan(
    *,
    video_path: str,
    duration: float,
    width: int,
    height: int,
    fps: float,
    frames: Sequence[Dict[str, Any]],
    detections: Sequence[Dict[str, Any]],
    detector: str,
    model: str,
    sample_interval: float,
    max_frames: int,
    sample_mode: str,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    normalized_detections = [dict(item) for item in detections]
    tracks = build_tracks(normalized_detections, width=width, height=height) if normalized_detections else []
    frame_records = [dict(frame) for frame in frames]
    attach_detections_to_frames(frame_records, normalized_detections)

    warning_list = list(warnings or [])
    if detector == "none":
        warning_list.append("detector_disabled")
    if detector != "none" and not normalized_detections:
        warning_list.append("no_objects_detected")
    low_confidence = sum(1 for item in normalized_detections if float(item.get("confidence") or 0.0) < 0.35)
    if low_confidence:
        warning_list.append(f"low_confidence_detections:{low_confidence}")

    return {
        "version": VERSION,
        "source": {
            "video": video_path,
            "duration": _round3(duration),
            "width": int(width),
            "height": int(height),
            "fps": _round3(fps),
        },
        "params": {
            "detector": detector,
            "model": model if detector != "none" else "",
            "sample_interval": float(sample_interval),
            "max_frames": int(max_frames),
            "sample_mode": sample_mode,
            "tracking": "sample_iou_tracklets",
        },
        "summary": {
            "frames": len(frame_records),
            "detections": len(normalized_detections),
            "tracks": len(tracks),
            "labels": summarize_labels(normalized_detections),
            "warnings": sorted(set(warning_list)),
        },
        "frames": frame_records,
        "detections": normalized_detections,
        "tracks": tracks,
        "scene_tags": build_scene_tags(normalized_detections, tracks),
        "warnings": sorted(set(warning_list)),
    }


def format_time(seconds: Any) -> str:
    value = _float_or_none(seconds)
    if value is None:
        return "-"
    value = max(0.0, value)
    minutes = int(value // 60)
    rest = value - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    summary = plan.get("summary") or {}
    lines = [
        "# Video Understanding",
        "",
        f"- Video: `{source.get('video', '')}`",
        f"- Duration: `{float(source.get('duration') or 0.0):.3f}s`",
        f"- Size: `{source.get('width', 0)}x{source.get('height', 0)}`",
        f"- Detector: `{(plan.get('params') or {}).get('detector', 'none')}`",
        f"- Frames / Detections / Tracks: `{summary.get('frames', 0)} / {summary.get('detections', 0)} / {summary.get('tracks', 0)}`",
        "",
        "## Label Summary",
        "",
    ]
    labels = summary.get("labels") or {}
    if labels:
        for label, count in labels.items():
            lines.append(f"- `{label}`: {count}")
    else:
        lines.append("- No object detections.")

    lines.extend(["", "## Sample Frames", "", "| Frame | Time | Scene | Detections | Path |", "| --- | ---: | --- | ---: | --- |"])
    for frame in _as_list(plan.get("frames")):
        lines.append(
            "| {frame_id} | {time} | {scene_id} | {count} | `{path}` |".format(
                frame_id=frame.get("frame_id", ""),
                time=format_time(frame.get("time")),
                scene_id=frame.get("scene_id") or "-",
                count=len(_as_list(frame.get("detections"))),
                path=frame.get("path") or "",
            )
        )

    lines.extend(["", "## Tracks", "", "| Track | Label | Time | Frames | Motion | Max Area |", "| --- | --- | --- | ---: | ---: | ---: |"])
    tracks = _as_list(plan.get("tracks"))
    if tracks:
        for track in tracks:
            lines.append(
                "| {track_id} | {label} | {start}-{end} | {frames} | {motion:.3f} | {area:.3f} |".format(
                    track_id=track.get("track_id", ""),
                    label=track.get("label", ""),
                    start=format_time(track.get("start")),
                    end=format_time(track.get("end")),
                    frames=track.get("frames", 0),
                    motion=float(track.get("motion_distance_ratio") or 0.0),
                    area=float(track.get("max_area_ratio") or 0.0),
                )
            )
    else:
        lines.append("| - | - | - | 0 | 0.000 | 0.000 |")

    lines.extend(["", "## Scene Tags", ""])
    for tag in _as_list(plan.get("scene_tags")):
        lines.append(f"- `{tag.get('tag')}` from `{tag.get('source')}` evidence `{tag.get('evidence')}`")

    warnings = _as_list(plan.get("warnings"))
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning}`")

    lines.extend(
        [
            "",
            "## Downstream Usage",
            "",
            "- Feed this JSON into `smart_reframe.py --detections` for subject-aware portrait crops.",
            "- Feed the same detections into `privacy_redact.py --detections` when labels/boxes indicate faces, screens, plates, or sensitive areas.",
            "- Review sampled frames before trusting automatic crops on fast motion, occlusion, or very small subjects.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample video frames and optionally run YOLO object detection.")
    parser.add_argument("input", help="Input video path")
    parser.add_argument("--output", default="video_understanding.json", help="Output video_understanding JSON")
    parser.add_argument("--markdown", help="Optional Markdown review report")
    parser.add_argument("--frames-dir", help="Directory for sampled frames")
    parser.add_argument("--scene-boundaries", help="Optional scene_boundaries.py JSON")
    parser.add_argument("--external-detections", action="append", default=[], help="Existing detector JSON to merge")
    parser.add_argument("--detector", choices=["none", "yolo"], default="none", help="Detector to run on sampled frames")
    parser.add_argument("--model", default="yolo11n.pt", help="Ultralytics model name/path when --detector yolo")
    parser.add_argument("--conf", type=float, default=0.25, help="YOLO confidence threshold")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO image size")
    parser.add_argument("--device", help="Optional YOLO device, for example mps, cuda, cpu")
    parser.add_argument("--classes", help="Optional comma-separated numeric YOLO class ids")
    parser.add_argument("--sample-interval", type=float, default=2.0, help="Seconds between interval samples")
    parser.add_argument("--max-frames", type=int, default=24, help="Maximum sampled frames")
    parser.add_argument("--sample", choices=["interval", "scene-and-interval"], default="scene-and-interval")
    parser.add_argument("--source-width", type=int, help="Override source width, mainly for tests")
    parser.add_argument("--source-height", type=int, help="Override source height, mainly for tests")
    parser.add_argument("--duration", type=float, help="Override source duration, mainly for tests")
    parser.add_argument("--fps", type=float, help="Override source fps, mainly for tests")
    parser.add_argument("--no-extract", action="store_true", help="Do not extract frame images")
    parser.add_argument("--overwrite-frames", action="store_true", help="Overwrite existing sampled frame images")
    parser.add_argument("--strict", action="store_true", help="Return 2 when detector output is empty")
    return parser.parse_args(argv)


def _resolve_video_info(args: argparse.Namespace) -> Tuple[float, int, int, float]:
    if args.duration is not None and args.source_width is not None and args.source_height is not None:
        return float(args.duration), int(args.source_width), int(args.source_height), float(args.fps or 0.0)
    duration, width, height, fps, _rotation = get_video_info(args.input)
    return (
        float(args.duration if args.duration is not None else duration),
        int(args.source_width if args.source_width is not None else width),
        int(args.source_height if args.source_height is not None else height),
        float(args.fps if args.fps is not None else fps),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.conf <= 0 or args.conf > 1:
        print("Error: --conf must be in (0, 1].", file=sys.stderr)
        return 1
    if args.imgsz <= 0:
        print("Error: --imgsz must be positive.", file=sys.stderr)
        return 1

    try:
        duration, width, height, fps = _resolve_video_info(args)
        if duration <= 0 or width <= 0 or height <= 0:
            raise ValueError("duration, width, and height must be positive")
        scene_plan = _read_json(args.scene_boundaries) if args.scene_boundaries else None
        frames_dir = args.frames_dir or str(Path(args.output).with_suffix("").parent / "video_understanding_frames")
        extract = not args.no_extract
        frames = build_frame_records(
            args.input,
            duration=duration,
            width=width,
            height=height,
            scene_plan=scene_plan,
            sample_interval=args.sample_interval,
            max_frames=args.max_frames,
            sample_mode=args.sample,
            frames_dir=frames_dir,
            extract=extract,
            overwrite=args.overwrite_frames,
        )

        detections: List[Dict[str, Any]] = []
        for detection_path in args.external_detections:
            detections.extend(
                normalize_external_detections(
                    _read_json(detection_path),
                    width=width,
                    height=height,
                    duration=duration,
                    source=f"external:{os.path.basename(detection_path)}",
                )
            )
        if args.detector == "yolo":
            if not extract:
                raise RuntimeError("YOLO detection requires extracted frames; remove --no-extract.")
            detections.extend(
                run_yolo_detector(
                    frames,
                    model_name=args.model,
                    conf=args.conf,
                    imgsz=args.imgsz,
                    device=args.device,
                    classes=parse_yolo_classes(args.classes),
                )
            )

        plan = build_understanding_plan(
            video_path=args.input,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            frames=frames,
            detections=detections,
            detector=args.detector,
            model=args.model,
            sample_interval=args.sample_interval,
            max_frames=args.max_frames,
            sample_mode=args.sample,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"video_understanding: {exc}", file=sys.stderr)
        return 2 if args.detector == "yolo" else 1

    write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    print(
        "Video understanding: {frames} frames, {detections} detections, {tracks} tracks, wrote {path}".format(
            frames=plan["summary"]["frames"],
            detections=plan["summary"]["detections"],
            tracks=plan["summary"]["tracks"],
            path=args.output,
        )
    )
    if args.strict and args.detector != "none" and int(plan["summary"]["detections"]) == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
