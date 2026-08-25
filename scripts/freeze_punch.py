#!/usr/bin/env python3
"""Plan, render, and live-verify source-bound freeze-punch emphasis edits.

A freeze-punch replaces a short source-time window with the first frame of
that window, optionally cropped toward an anchor for a subtle punch-in.  The
audio timeline and total duration stay unchanged.  The tool is deterministic
and local: it does not detect the impact frame, call a model, or upload media.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "freeze_punch_plan.v1"
VERIFY_VERSION = "freeze_punch_verify.v1"
PENDING_APPLY = "freeze-punch render has not been applied"
MIN_FREEZE_SECONDS = 0.1
MAX_FREEZE_SECONDS = 3.0
MIN_SCALE = 1.0
MAX_SCALE = 1.5
DEFAULT_SCALE = 1.08
ROUND_DIGITS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _fraction(value: Any) -> Optional[float]:
    if value in {None, "", "0/0", "N/A"}:
        return None
    try:
        text = str(value)
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _rotation(video: Mapping[str, Any]) -> int:
    raw = (video.get("tags") or {}).get("rotate")
    if raw is None:
        for item in video.get("side_data_list") or []:
            if isinstance(item, Mapping) and item.get("rotation") is not None:
                raw = item.get("rotation")
                break
    try:
        value = int(round(float(raw or 0))) % 360
    except (TypeError, ValueError):
        return 0
    return value if value in {0, 90, 180, 270} else 0


def probe_media(path: Path | str) -> Dict[str, Any]:
    media_path = Path(path).expanduser().resolve()
    if not media_path.is_file():
        raise ValueError(f"video does not exist: {media_path}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed for {media_path}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {media_path}") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video, Mapping):
        raise ValueError(f"video stream not found: {media_path}")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = _fraction((payload.get("format") or {}).get("duration")) or _fraction(video.get("duration"))
    fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if not duration or duration <= 0 or not fps or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {media_path}")
    return {
        "duration": _round(duration),
        "fps": _round(fps),
        "width": width,
        "height": height,
        "rotation": rotation,
        "has_audio": audio is not None,
        "video_codec": str(video.get("codec_name") or "").lower() or None,
        "audio_codec": str((audio or {}).get("codec_name") or "").lower() or None,
        "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def _media_fingerprint(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path)}


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _same_path_or_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _even_down(value: float) -> int:
    parsed = max(2, int(math.floor(value)))
    return parsed if parsed % 2 == 0 else parsed - 1


def _even_coordinate(value: float) -> int:
    parsed = max(0, int(math.floor(value)))
    return parsed if parsed % 2 == 0 else parsed - 1


def parse_freeze(value: str) -> Dict[str, Any]:
    parts = [part.strip() for part in value.split(",")]
    if not 2 <= len(parts) <= 5:
        raise ValueError("--freeze expects TIME,DURATION[,SCALE[,ANCHOR_X[,ANCHOR_Y]]]")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("--freeze values must be numeric") from exc
    return {
        "time": numbers[0],
        "duration": numbers[1],
        "scale": numbers[2] if len(numbers) >= 3 else DEFAULT_SCALE,
        "anchor_x": numbers[3] if len(numbers) >= 4 else 0.5,
        "anchor_y": numbers[4] if len(numbers) >= 5 else 0.5,
    }


def normalize_events(
    events: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    width: int,
    height: int,
) -> List[Dict[str, Any]]:
    if not events:
        raise ValueError("at least one --freeze event is required")
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError("source duration and dimensions must be positive")
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(events, start=1):
        time = _finite_float(raw.get("time"))
        freeze_duration = _finite_float(raw.get("duration"))
        scale = _finite_float(raw.get("scale"))
        anchor_x = _finite_float(raw.get("anchor_x"))
        anchor_y = _finite_float(raw.get("anchor_y"))
        if None in {time, freeze_duration, scale, anchor_x, anchor_y}:
            raise ValueError(f"freeze event {index} has invalid numeric fields")
        assert time is not None and freeze_duration is not None and scale is not None
        assert anchor_x is not None and anchor_y is not None
        if time < 0:
            raise ValueError(f"freeze event {index} time must be non-negative")
        if not MIN_FREEZE_SECONDS <= freeze_duration <= MAX_FREEZE_SECONDS:
            raise ValueError(
                f"freeze event {index} duration must be between {MIN_FREEZE_SECONDS:g} and {MAX_FREEZE_SECONDS:g} seconds"
            )
        end = time + freeze_duration
        if end > duration + 1e-6:
            raise ValueError(f"freeze event {index} ends after source duration {duration:.3f}s")
        if not MIN_SCALE <= scale <= MAX_SCALE:
            raise ValueError(f"freeze event {index} scale must be between {MIN_SCALE:g} and {MAX_SCALE:g}")
        if not 0 <= anchor_x <= 1 or not 0 <= anchor_y <= 1:
            raise ValueError(f"freeze event {index} anchors must be between 0 and 1")
        crop_width = _even_down(width / scale)
        crop_height = _even_down(height / scale)
        crop_x = _even_coordinate((width - crop_width) * anchor_x) if width > crop_width else 0
        crop_y = _even_coordinate((height - crop_height) * anchor_y) if height > crop_height else 0
        crop_x = min(max(0, crop_x), max(0, width - crop_width))
        crop_y = min(max(0, crop_y), max(0, height - crop_height))
        normalized.append(
            {
                "id": f"freeze-{index:03d}",
                "time": _round(time),
                "end": _round(end),
                "duration": _round(freeze_duration),
                "scale": _round(scale),
                "anchor_x": _round(anchor_x),
                "anchor_y": _round(anchor_y),
                "crop": {
                    "x": crop_x,
                    "y": crop_y,
                    "width": crop_width,
                    "height": crop_height,
                },
            }
        )
    normalized.sort(key=lambda item: (item["time"], item["end"]))
    for index, item in enumerate(normalized, start=1):
        item["id"] = f"freeze-{index:03d}"
        if index > 1 and item["time"] < normalized[index - 2]["end"] - 1e-6:
            raise ValueError(f"freeze events overlap at {item['time']:.3f}s")
    return normalized


def compile_pieces(events: Sequence[Mapping[str, Any]], *, duration: float) -> List[Dict[str, Any]]:
    pieces: List[Dict[str, Any]] = []
    cursor = 0.0
    for event in events:
        start = float(event["time"])
        end = float(event["end"])
        if start > cursor + 1e-8:
            pieces.append(
                {
                    "id": f"piece-{len(pieces) + 1:04d}",
                    "kind": "normal",
                    "event_id": None,
                    "source_start": _round(cursor),
                    "source_end": _round(start),
                    "output_start": _round(cursor),
                    "output_end": _round(start),
                }
            )
        pieces.append(
            {
                "id": f"piece-{len(pieces) + 1:04d}",
                "kind": "freeze",
                "event_id": event["id"],
                "source_start": _round(start),
                "source_end": _round(end),
                "output_start": _round(start),
                "output_end": _round(end),
            }
        )
        cursor = end
    if cursor < duration - 1e-8:
        pieces.append(
            {
                "id": f"piece-{len(pieces) + 1:04d}",
                "kind": "normal",
                "event_id": None,
                "source_start": _round(cursor),
                "source_end": _round(duration),
                "output_start": _round(cursor),
                "output_end": _round(duration),
            }
        )
    return pieces


def _quality_warnings(events: Sequence[Mapping[str, Any]], *, has_audio: bool) -> List[str]:
    warnings: List[str] = []
    if has_audio:
        warnings.append(
            "Audio remains continuous while picture motion is replaced; avoid visible speaking mouths and review every freeze at 1x"
        )
    if any(float(event["duration"]) > 1.5 for event in events):
        warnings.append("A freeze exceeds 1.5s and may feel stalled unless the emphasis is strongly motivated")
    if any(float(event["scale"]) > 1.2 for event in events):
        warnings.append("A punch scale exceeds 1.20x; inspect crop loss and interpolation softness")
    return warnings


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "events": plan.get("events"),
        "pieces": plan.get("pieces"),
        "delivery": plan.get("delivery"),
        "application": plan.get("application"),
        "review_contract": plan.get("review_contract"),
        "warnings": plan.get("warnings"),
        "blockers": plan.get("blockers"),
        "summary": plan.get("summary"),
        "status": plan.get("status"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_contract(path: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        **_fingerprint(path),
        "duration": _round(float(media["duration"])),
        "fps": _round(float(media["fps"])),
        "width": int(media["width"]),
        "height": int(media["height"]),
        "rotation": int(media.get("rotation") or 0),
        "has_audio": bool(media.get("has_audio")),
        "video_codec": media.get("video_codec"),
        "audio_codec": media.get("audio_codec"),
        "pixel_format": media.get("pixel_format"),
    }


def build_plan(
    source_path: str,
    delivery_path: str,
    *,
    media: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    source_input = Path(source_path).expanduser()
    delivery_input = Path(delivery_path).expanduser()
    if source_input.is_symlink():
        raise ValueError("source video must not be a symlink")
    if delivery_input.is_symlink():
        raise ValueError("freeze-punch delivery must not be a symlink")
    source = source_input.resolve()
    delivery = delivery_input.resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    if delivery.suffix.lower() != ".mp4":
        raise ValueError("freeze-punch delivery must use the .mp4 extension")
    if _same_path_or_file(source, delivery):
        raise ValueError("delivery must not overwrite or alias the source video")
    source_contract = _source_contract(source, media)
    output_width = _even_down(int(source_contract["width"]))
    output_height = _even_down(int(source_contract["height"]))
    normalized = normalize_events(
        events,
        duration=float(source_contract["duration"]),
        width=int(source_contract["width"]),
        height=int(source_contract["height"]),
    )
    pieces = compile_pieces(normalized, duration=float(source_contract["duration"]))
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": source_contract,
        "events": normalized,
        "pieces": pieces,
        "delivery": {
            "path": str(delivery),
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac" if source_contract["has_audio"] else None,
            "pixel_format": "yuv420p",
            "duration": source_contract["duration"],
            "fps": source_contract["fps"],
            "width": output_width,
            "height": output_height,
            "has_audio": source_contract["has_audio"],
            "duration_tolerance_seconds": _round(max(0.15, 2.0 / float(source_contract["fps"]))),
        },
        "application": None,
        "review_contract": {
            "required": True,
            "instructions": [
                "Choose each impact frame manually; this tool does not detect peak action or expression.",
                "Watch the rendered output at 1x with audio and inspect the exact freeze entry and exit.",
                "Reject frozen speaking mouths, accidental jump-ahead motion, lost hands/products, or excessive crop softness.",
                "Run render_qa.py on the applied delivery before using it as a downstream source.",
            ],
            "timeline_effect": "picture window replaced; audio and total duration unchanged",
        },
        "warnings": [],
        "blockers": [],
        "summary": {},
        "status": "blocked",
    }
    _set_derived(plan)
    return plan


def _media_contract_differences(stored: Mapping[str, Any], live: Mapping[str, Any]) -> List[str]:
    differences: List[str] = []
    for key in ("width", "height", "rotation", "has_audio", "video_codec", "audio_codec", "pixel_format"):
        if stored.get(key) != live.get(key):
            differences.append(f"{key} changed ({stored.get(key)!r} -> {live.get(key)!r})")
    for key in ("duration", "fps"):
        stored_value = _finite_float(stored.get(key))
        live_value = _finite_float(live.get(key))
        tolerance = 0.001 if key == "fps" else max(0.03, 1.0 / max(float(stored.get("fps") or 1), 1.0))
        if stored_value is None or live_value is None or abs(stored_value - live_value) > tolerance:
            differences.append(f"{key} changed ({stored.get(key)!r} -> {live.get(key)!r})")
    return differences


def _output_contract_blockers(output: Mapping[str, Any], delivery: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if output.get("video_codec") != delivery.get("video_codec"):
        blockers.append("delivery video codec is not H.264")
    if output.get("pixel_format") != delivery.get("pixel_format"):
        blockers.append("delivery pixel format is not yuv420p")
    if output.get("has_audio") is not delivery.get("has_audio"):
        blockers.append("delivery audio presence does not match the source contract")
    if delivery.get("has_audio") and output.get("audio_codec") != delivery.get("audio_codec"):
        blockers.append("delivery audio codec is not AAC")
    for key in ("width", "height"):
        if output.get(key) != delivery.get(key):
            blockers.append(f"delivery {key} does not match the planned value")
    output_fps = _finite_float(output.get("fps"))
    planned_fps = _finite_float(delivery.get("fps"))
    if output_fps is None or planned_fps is None or abs(output_fps - planned_fps) > 0.01:
        blockers.append("delivery fps does not match the planned value")
    output_duration = _finite_float(output.get("duration"))
    planned_duration = _finite_float(delivery.get("duration"))
    tolerance = _finite_float(delivery.get("duration_tolerance_seconds"))
    if (
        output_duration is None
        or planned_duration is None
        or tolerance is None
        or abs(output_duration - planned_duration) > tolerance
    ):
        blockers.append("delivery duration does not match the unchanged-timeline contract")
    return blockers


def _structural_blockers(plan: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = Path(str(source.get("path") or "")).expanduser()
    if not source_path.is_absolute():
        blockers.append("source.path must be absolute")
    elif not source_path.is_file():
        blockers.append(f"source file is missing: {source_path}")
    else:
        if source_path.is_symlink():
            blockers.append("source.path must not be a symlink")
        if source.get("size_bytes") != source_path.stat().st_size:
            blockers.append("source size changed after planning")
        elif source.get("sha256") != _sha256(source_path):
            blockers.append("source sha256 changed after planning")
        try:
            live_source = probe_media(source_path)
        except (OSError, ValueError) as exc:
            blockers.append(f"source media cannot be probed: {exc}")
        else:
            blockers.extend(f"source media {item}" for item in _media_contract_differences(source, live_source))

    duration = _finite_float(source.get("duration"))
    width = source.get("width")
    height = source.get("height")
    events = plan.get("events") if isinstance(plan.get("events"), list) else []
    pieces = plan.get("pieces") if isinstance(plan.get("pieces"), list) else []
    normalized: List[Dict[str, Any]] = []
    compiled: List[Dict[str, Any]] = []
    if duration is None or duration <= 0 or not isinstance(width, int) or not isinstance(height, int):
        blockers.append("source duration/dimensions contract is invalid")
    else:
        try:
            normalized = normalize_events(events, duration=duration, width=width, height=height)
            compiled = compile_pieces(normalized, duration=duration)
        except ValueError as exc:
            blockers.append(f"events cannot be compiled: {exc}")
        else:
            if normalized != events:
                blockers.append("events are not in canonical normalized form")
            if compiled != pieces:
                blockers.append("pieces do not match canonical freeze events")

    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    delivery_path = Path(str(delivery.get("path") or "")).expanduser()
    if not delivery_path.is_absolute():
        blockers.append("delivery.path must be absolute")
    elif delivery_path.is_symlink():
        blockers.append("delivery.path must not be a symlink")
    elif source_path.is_absolute() and _same_path_or_file(source_path, delivery_path):
        blockers.append("delivery must not overwrite or alias the source video")
    expected_delivery = {
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac" if source.get("has_audio") else None,
        "pixel_format": "yuv420p",
        "duration": source.get("duration"),
        "fps": source.get("fps"),
        "width": _even_down(int(width)) if isinstance(width, int) and width > 0 else None,
        "height": _even_down(int(height)) if isinstance(height, int) and height > 0 else None,
        "has_audio": source.get("has_audio"),
        "duration_tolerance_seconds": _round(max(0.15, 2.0 / float(source.get("fps"))))
        if _finite_float(source.get("fps")) and float(source.get("fps")) > 0
        else None,
    }
    for key, expected in expected_delivery.items():
        if delivery.get(key) != expected:
            blockers.append(f"delivery.{key} does not match the source-bound output contract")

    review = plan.get("review_contract") if isinstance(plan.get("review_contract"), Mapping) else {}
    if review.get("required") is not True or review.get("timeline_effect") != "picture window replaced; audio and total duration unchanged":
        blockers.append("review_contract does not match the freeze-punch review requirements")
    if not isinstance(review.get("instructions"), list) or len(review.get("instructions") or []) < 4:
        blockers.append("review_contract.instructions are incomplete")
    return blockers


def _application_blockers(plan: Mapping[str, Any]) -> List[str]:
    application = plan.get("application")
    if application is None:
        return [PENDING_APPLY]
    if not isinstance(application, Mapping):
        return ["application must be null or an object"]
    blockers: List[str] = []
    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
    output_path = Path(str(output.get("path") or "")).expanduser()
    delivery_path = Path(str(delivery.get("path") or "")).expanduser()
    if not output_path.is_absolute() or output_path != delivery_path:
        blockers.append("application output path does not match delivery.path")
    elif not output_path.is_file():
        blockers.append(f"applied delivery is missing: {output_path}")
    else:
        if output_path.is_symlink():
            blockers.append("applied delivery must not be a symlink")
        if output.get("size_bytes") != output_path.stat().st_size:
            blockers.append("applied delivery size changed")
        elif output.get("sha256") != _sha256(output_path):
            blockers.append("applied delivery sha256 changed")
        try:
            live_output = probe_media(output_path)
        except (OSError, ValueError) as exc:
            blockers.append(f"applied delivery cannot be probed: {exc}")
        else:
            stored_media = {key: value for key, value in output.items() if key not in {"path", "sha256", "size_bytes"}}
            blockers.extend(
                f"applied delivery media {item}" for item in _media_contract_differences(stored_media, live_output)
            )
            blockers.extend(_output_contract_blockers(live_output, delivery))
    validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
    if validation.get("decode_checked") is not True:
        blockers.append("application full-decode validation is missing")
    if validation.get("output_sha256") != output.get("sha256"):
        blockers.append("application validation hash does not match output")
    return blockers


def _computed_state(plan: Mapping[str, Any]) -> Dict[str, Any]:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    events = plan.get("events") if isinstance(plan.get("events"), list) else []
    structural = _structural_blockers(plan)
    application = _application_blockers(plan)
    blockers = sorted(set(structural + application))
    warnings = _quality_warnings(events, has_audio=bool(source.get("has_audio"))) if events else []
    summary = {
        "events": len(events),
        "pieces": len(plan.get("pieces") or []) if isinstance(plan.get("pieces"), list) else 0,
        "source_duration": source.get("duration"),
        "output_duration": (plan.get("delivery") or {}).get("duration")
        if isinstance(plan.get("delivery"), Mapping)
        else None,
        "timeline_changed": False,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else ("review" if warnings else "ready")
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def _set_derived(plan: Dict[str, Any]) -> None:
    computed = _computed_state(plan)
    plan.update(computed)
    plan["plan_id"] = _plan_id(plan)


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    computed = _computed_state(plan)
    blockers = list(computed["blockers"])
    if plan.get("plan_id") != _plan_id(plan):
        blockers.append("plan_id does not match canonical plan content")
    for key in ("blockers", "warnings", "summary", "status"):
        if plan.get(key) != computed[key]:
            blockers.append(f"stored {key} does not match live-derived state")
    blockers = sorted(set(blockers))
    warnings = list(computed["warnings"])
    return {
        "version": VERIFY_VERSION,
        "plan_id": plan.get("plan_id"),
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": {"blocking": len(blockers), "warnings": len(warnings)},
    }


def build_filter_graph(plan: Mapping[str, Any]) -> str:
    source = plan["source"]
    delivery = plan["delivery"]
    events_by_id = {event["id"]: event for event in plan["events"]}
    fps = float(delivery["fps"])
    output_width = int(delivery["width"])
    output_height = int(delivery["height"])
    frame_duration = 1.0 / fps
    filters: List[str] = []
    concat_inputs: List[str] = []
    for index, piece in enumerate(plan["pieces"]):
        start = float(piece["source_start"])
        end = float(piece["source_end"])
        label = f"v{index}"
        if piece["kind"] == "normal":
            chain = (
                f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
                f"scale={output_width}:{output_height}:flags=lanczos,setsar=1,fps={fps:.6f}"
            )
        else:
            event = events_by_id[piece["event_id"]]
            crop = event["crop"]
            sample_end = min(float(source["duration"]), start + frame_duration)
            chain = (
                f"trim=start={start:.6f}:end={sample_end:.6f},setpts=PTS-STARTPTS,"
                "select='eq(n\\,0)',"
                f"crop={int(crop['width'])}:{int(crop['height'])}:{int(crop['x'])}:{int(crop['y'])},"
                f"scale={output_width}:{output_height}:flags=lanczos,setsar=1,"
                f"tpad=stop_mode=clone:stop_duration={float(event['duration']):.6f},"
                f"trim=duration={float(event['duration']):.6f},setpts=PTS-STARTPTS,fps={fps:.6f}"
            )
        filters.append(f"[0:v]{chain}[{label}]")
        concat_inputs.append(f"[{label}]")
    filters.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[vconcat]")
    filters.append(f"[vconcat]fps={fps:.6f},format=yuv420p[vout]")
    if delivery.get("has_audio"):
        duration = float(delivery["duration"])
        filters.append(
            f"[0:a]atrim=start=0:end={duration:.6f},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[aout]"
        )
    return ";".join(filters)


def build_ffmpeg_command(plan: Mapping[str, Any], output_path: Path | str) -> List[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(plan["source"]["path"]),
        "-filter_complex",
        build_filter_graph(plan),
        "-map",
        "[vout]",
    ]
    if plan["delivery"].get("has_audio"):
        command.extend(["-map", "[aout]"])
    command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"])
    if plan["delivery"].get("has_audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(output_path)])
    return command


def _decode_command(path: Path) -> List[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "null", "-"]


def _run_checked(command: Sequence[str], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed: {result.stderr.strip()}")


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read freeze-punch plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("freeze-punch plan must be a JSON object")
    return payload


def _resolve_plan_file(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError("freeze-punch plan must not be a symlink")
    path = candidate.resolve()
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError(f"freeze-punch plan must be an existing JSON file: {path}")
    return path


def apply_plan_file(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    verification = verify_plan(plan)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("freeze-punch plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))
    source_input = Path(str(plan["source"]["path"])).expanduser()
    delivery_input = Path(str(plan["delivery"]["path"])).expanduser()
    if source_input.is_symlink():
        raise ValueError("source video must not be a symlink")
    if delivery_input.is_symlink():
        raise ValueError("freeze-punch delivery must not be a symlink")
    source = source_input.resolve()
    delivery = delivery_input.resolve()
    if delivery.suffix.lower() != ".mp4":
        raise ValueError("freeze-punch delivery must use the .mp4 extension")
    delivery.parent.mkdir(parents=True, exist_ok=True)
    if _same_path_or_file(delivery, source) or _same_path_or_file(delivery, plan_file):
        raise ValueError("delivery must not overwrite or alias the source or plan")
    if delivery.exists() and not force:
        raise ValueError(f"delivery already exists; use --force to replace it: {delivery}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".tmp.mp4", dir=str(delivery.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _run_checked(build_ffmpeg_command(plan, temporary), "freeze-punch render")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("freeze-punch render did not create a non-empty output")
        temporary_media = _media_fingerprint(temporary)
        contract_blockers = _output_contract_blockers(temporary_media, plan["delivery"])
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(_decode_command(temporary), "full freeze-punch decode validation")
        os.replace(temporary, delivery)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    output = _media_fingerprint(delivery)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "validation": {
            "verified_at": utc_now(),
            "decode_checked": True,
            "output_sha256": output["sha256"],
        },
    }
    _set_derived(plan)
    _atomic_write_json(plan_file, plan)
    return plan


def render_markdown(plan: Mapping[str, Any], *, plan_path: str = "work/freeze_punch_plan.json") -> str:
    source = plan["source"]
    delivery = plan["delivery"]
    lines = [
        "# Freeze-Punch Plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- Plan ID: `{plan['plan_id']}`",
        f"- Source: `{source['path']}` (`{source['duration']:.3f}s`, `{source['fps']:.3f} fps`)",
        f"- Delivery: `{delivery['path']}`",
        "- Timeline: picture windows are replaced; audio and total duration stay unchanged",
        "",
        "## Events",
        "",
        "| ID | Freeze window | Scale | Anchor | Crop |",
        "|---|---:|---:|---:|---:|",
    ]
    for event in plan["events"]:
        crop = event["crop"]
        lines.append(
            f"| {event['id']} | {event['time']:.3f}–{event['end']:.3f}s | "
            f"{event['scale']:.3f}x | {event['anchor_x']:.2f}, {event['anchor_y']:.2f} | "
            f"{crop['width']}x{crop['height']}+{crop['x']}+{crop['y']} |"
        )
    if plan.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    lines.extend(
        [
            "",
            "## Apply after frame review",
            "",
            "```bash",
            " ".join(
                shlex.quote(part)
                for part in ["python3", "scripts/freeze_punch.py", "apply", plan_path]
            ),
            "```",
            "",
            "After apply, run `verify --strict`, then watch every entry/exit at 1x with audio. "
            "Reject a frozen speaking mouth, a confusing action jump, lost subject detail, or excessive crop softness.",
            "",
            "This local plan does not detect peak frames or prove editorial taste. SHA-256 binds bytes; it is not a signature.",
        ]
    )
    return "\n".join(lines)


def _safe_plan_outputs(
    source: Path,
    delivery: Path,
    plan_path: Path,
    markdown_path: Optional[Path],
    *,
    force: bool,
) -> None:
    outputs = [plan_path] + ([markdown_path] if markdown_path else [])
    for output in outputs:
        assert output is not None
        if output.is_symlink():
            raise ValueError(f"output must not be a symlink: {output}")
        if any(_same_path_or_file(output, protected) for protected in (source, delivery)):
            raise ValueError("plan/Markdown outputs must not overwrite or alias source/delivery media")
        if output.exists() and not force:
            raise ValueError(f"output already exists; use --force to replace it: {output}")
    if markdown_path and _same_path_or_file(plan_path, markdown_path):
        raise ValueError("plan JSON and Markdown outputs must be distinct")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan, apply, and live-verify local freeze-punch emphasis edits"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Create a source-bound freeze-punch plan")
    plan_parser.add_argument("video")
    plan_parser.add_argument(
        "--freeze",
        action="append",
        default=[],
        metavar="TIME,DURATION[,SCALE[,ANCHOR_X[,ANCHOR_Y]]]",
    )
    plan_parser.add_argument("--delivery", required=True, help="Planned H.264/AAC MP4 output")
    plan_parser.add_argument("--output", default="work/freeze_punch_plan.json")
    plan_parser.add_argument("--markdown")
    plan_parser.add_argument("--force", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Live-verify plan, source, and applied output")
    verify_parser.add_argument("plan")
    verify_parser.add_argument("--json")
    verify_parser.add_argument("--strict", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Render, validate, promote, and bind the delivery")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            source = Path(os.path.abspath(Path(args.video).expanduser()))
            delivery = Path(os.path.abspath(Path(args.delivery).expanduser()))
            output = Path(os.path.abspath(Path(args.output).expanduser()))
            markdown = Path(os.path.abspath(Path(args.markdown).expanduser())) if args.markdown else None
            _safe_plan_outputs(source, delivery, output, markdown, force=args.force)
            plan = build_plan(
                str(source),
                str(delivery),
                media=probe_media(source),
                events=[parse_freeze(value) for value in args.freeze],
            )
            _atomic_write_json(output, plan)
            if markdown:
                _atomic_write_text(markdown, render_markdown(plan, plan_path=str(output)))
            print(json.dumps(plan["summary"], ensure_ascii=False))
            return 0

        plan_file = _resolve_plan_file(args.plan)
        if args.command == "verify":
            verification = verify_plan(_load_plan(plan_file))
            if args.json:
                _atomic_write_json(Path(args.json).expanduser().resolve(), verification)
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        applied = apply_plan_file(str(plan_file), force=args.force)
        print(json.dumps(applied["summary"], ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
