#!/usr/bin/env python3
"""Plan, verify, and render source-bound local speed ramps with FFmpeg.

The planner compiles explicit source-time ramp/hold events into small constant-
speed pieces.  It is intentionally deterministic: no action detection, model
call, upload, or paid provider is hidden behind this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "speed_ramp_plan.v1"
APPLY_VERSION = "speed_ramp_apply.v1"
CURVES = {"linear", "ease", "s_curve", "snap"}
MIN_SPEED = 0.1
MAX_SPEED = 4.0
DEFAULT_RAMP_STEPS = 8
ROUND_DIGITS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text(path: str, value: str) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(value, encoding="utf-8")


def _fraction(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    return float(value)


def probe_media(path: str) -> Dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed for source video: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON: {exc}") from exc

    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video, Mapping):
        raise ValueError("source has no video stream")
    raw_duration = payload.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = float(raw_duration)
        fps = _fraction(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"source duration/fps is invalid: {exc}") from exc
    if duration <= 0 or fps <= 0:
        raise ValueError("source duration and fps must be greater than zero")
    return {
        "duration": duration,
        "fps": fps,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def parse_ramp(value: str) -> Dict[str, Any]:
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 5:
        raise ValueError("--ramp expects START,END,FROM_SPEED,TO_SPEED,CURVE")
    try:
        start, end, from_speed, to_speed = map(float, parts[:4])
    except ValueError as exc:
        raise ValueError("--ramp time and speed values must be numeric") from exc
    return {
        "kind": "ramp",
        "start": start,
        "end": end,
        "from_speed": from_speed,
        "to_speed": to_speed,
        "curve": parts[4].lower().replace("-", "_"),
    }


def parse_hold(value: str) -> Dict[str, Any]:
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 3:
        raise ValueError("--hold expects START,END,SPEED")
    try:
        start, end, speed = map(float, parts)
    except ValueError as exc:
        raise ValueError("--hold time and speed values must be numeric") from exc
    return {"kind": "hold", "start": start, "end": end, "speed": speed}


def _event_start_speed(event: Mapping[str, Any]) -> float:
    if event.get("kind") == "hold":
        return float(event["speed"])
    return float(event["from_speed"])


def _event_end_speed(event: Mapping[str, Any]) -> float:
    if event.get("kind") == "hold":
        return float(event["speed"])
    return float(event["to_speed"])


def normalize_events(events: Sequence[Mapping[str, Any]], duration: float) -> List[Dict[str, Any]]:
    if not events:
        raise ValueError("at least one --ramp or --hold event is required")
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(events, start=1):
        kind = str(raw.get("kind") or "")
        if kind not in {"ramp", "hold"}:
            raise ValueError(f"event {index} kind must be ramp or hold")
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"event {index} start/end must be numeric") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"event {index} needs 0 <= start < end")
        if end > duration + 1e-6:
            raise ValueError(f"event {index} ends after source duration {duration:.3f}s")

        item: Dict[str, Any] = {
            "id": f"speed-{index:03d}",
            "kind": kind,
            "start": _round(start),
            "end": _round(end),
        }
        if kind == "hold":
            speed = _validate_speed(raw.get("speed"), f"event {index} speed")
            item["speed"] = speed
        else:
            from_speed = _validate_speed(raw.get("from_speed"), f"event {index} from_speed")
            to_speed = _validate_speed(raw.get("to_speed"), f"event {index} to_speed")
            curve = str(raw.get("curve") or "").lower().replace("-", "_")
            if curve not in CURVES:
                raise ValueError(f"event {index} curve must be one of: {', '.join(sorted(CURVES))}")
            item.update({"from_speed": from_speed, "to_speed": to_speed, "curve": curve})
        normalized.append(item)

    normalized.sort(key=lambda item: (item["start"], item["end"], item["id"]))
    for index, item in enumerate(normalized, start=1):
        item["id"] = f"speed-{index:03d}"
        if index > 1 and item["start"] < normalized[index - 2]["end"] - 1e-6:
            raise ValueError(f"events overlap at {item['start']:.3f}s")
    return normalized


def _validate_speed(value: Any, label: str) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(speed) or speed < MIN_SPEED or speed > MAX_SPEED:
        raise ValueError(f"{label} must be between {MIN_SPEED:g}x and {MAX_SPEED:g}x")
    return _round(speed)


def curve_progress(curve: str, position: float) -> float:
    position = min(1.0, max(0.0, float(position)))
    if curve == "linear":
        return position
    if curve == "ease":
        return (1.0 - math.cos(math.pi * position)) / 2.0
    if curve == "s_curve":
        return position ** 3 * (position * (position * 6.0 - 15.0) + 10.0)
    if curve == "snap":
        return 0.0 if position < 0.5 else 1.0
    raise ValueError(f"unknown curve: {curve}")


def _add_piece(
    pieces: List[Dict[str, Any]],
    *,
    source_start: float,
    source_end: float,
    speed: float,
    event_id: str,
    curve: str,
) -> None:
    if source_end - source_start <= 1e-8:
        return
    output_start = pieces[-1]["output_end"] if pieces else 0.0
    output_duration = (source_end - source_start) / speed
    pieces.append(
        {
            "id": f"piece-{len(pieces) + 1:04d}",
            "event_id": event_id,
            "curve": curve,
            "source_start": _round(source_start),
            "source_end": _round(source_end),
            "source_duration": _round(source_end - source_start),
            "speed": _round(speed),
            "output_start": _round(output_start),
            "output_end": _round(output_start + output_duration),
            "output_duration": _round(output_duration),
        }
    )


def compile_pieces(
    events: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    ramp_steps: int,
) -> List[Dict[str, Any]]:
    if isinstance(ramp_steps, bool) or not isinstance(ramp_steps, int) or not 2 <= ramp_steps <= 60:
        raise ValueError("ramp_steps must be an integer between 2 and 60")
    pieces: List[Dict[str, Any]] = []
    cursor = 0.0
    for event in events:
        start = float(event["start"])
        end = float(event["end"])
        if start > cursor + 1e-8:
            _add_piece(
                pieces,
                source_start=cursor,
                source_end=start,
                speed=1.0,
                event_id="normal",
                curve="constant",
            )

        if event["kind"] == "hold":
            _add_piece(
                pieces,
                source_start=start,
                source_end=end,
                speed=float(event["speed"]),
                event_id=str(event["id"]),
                curve="constant",
            )
        else:
            steps = 2 if event["curve"] == "snap" else ramp_steps
            width = (end - start) / steps
            for step in range(steps):
                piece_start = start + step * width
                piece_end = end if step == steps - 1 else start + (step + 1) * width
                progress = curve_progress(str(event["curve"]), (step + 0.5) / steps)
                speed = float(event["from_speed"]) + (
                    float(event["to_speed"]) - float(event["from_speed"])
                ) * progress
                _add_piece(
                    pieces,
                    source_start=piece_start,
                    source_end=piece_end,
                    speed=speed,
                    event_id=str(event["id"]),
                    curve=str(event["curve"]),
                )
        cursor = end

    if cursor < duration - 1e-8:
        _add_piece(
            pieces,
            source_start=cursor,
            source_end=duration,
            speed=1.0,
            event_id="normal",
            curve="constant",
        )
    return pieces


def _continuity_warnings(events: Sequence[Mapping[str, Any]], *, duration: float) -> List[str]:
    warnings: List[str] = []
    previous_end = 0.0
    previous_speed = 1.0
    for event in events:
        start_speed = _event_start_speed(event)
        if float(event["start"]) > previous_end + 1e-6:
            if previous_end > 0 and abs(previous_speed - 1.0) > 0.02:
                warnings.append(
                    f"Abrupt return to 1.000x at {previous_end:.3f}s; review the snap with audio"
                )
            previous_speed = 1.0
        if abs(start_speed - previous_speed) > 0.02:
            warnings.append(
                f"Abrupt speed boundary at {float(event['start']):.3f}s: "
                f"{previous_speed:.3f}x -> {start_speed:.3f}x; review the snap with audio"
            )
        previous_end = float(event["end"])
        previous_speed = _event_end_speed(event)
    if previous_end < duration - 1e-6 and abs(previous_speed - 1.0) > 0.02:
        warnings.append(
            f"Abrupt return to 1.000x at {float(events[-1]['end']):.3f}s; review the snap with audio"
        )
    return warnings


def _quality_warnings(
    events: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    fps: float,
    has_audio: bool,
    interpolate_fps: int,
    mute_audio: bool,
    pieces: Sequence[Mapping[str, Any]],
) -> List[str]:
    warnings = _continuity_warnings(events, duration=duration)
    min_speed = min(float(piece["speed"]) for piece in pieces)
    native_unique_fps = fps * min_speed
    if min_speed < 1.0 and not interpolate_fps and native_unique_fps < 18.0:
        warnings.append(
            f"Slowest section has about {native_unique_fps:.2f} native unique fps; "
            "use --interpolate-fps or expect repeated frames"
        )
    if interpolate_fps:
        interpolated_unique_fps = interpolate_fps * min_speed
        if interpolate_fps <= fps:
            warnings.append("interpolate_fps is not above source fps and adds no slow-motion samples")
        elif interpolated_unique_fps < fps * 0.8:
            warnings.append(
                f"Interpolation yields about {interpolated_unique_fps:.2f} unique fps at the slowest point; "
                f"consider at least {math.ceil(fps / min_speed):d} fps"
            )
    if has_audio and not mute_audio and min_speed < 0.5:
        warnings.append("Audio below 0.5x can sound unnatural; listen at 1x or choose --mute-audio")
    return warnings


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "events": plan.get("events"),
        "settings": plan.get("settings"),
        "pieces": plan.get("pieces"),
        "output": plan.get("output"),
        "review_contract": plan.get("review_contract"),
        "warnings": plan.get("warnings"),
        "blockers": plan.get("blockers"),
        "summary": plan.get("summary"),
        "status": plan.get("status"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_core(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_speed_ramp_plan(
    source_path: str,
    *,
    duration: float,
    fps: float,
    has_audio: bool,
    events: Sequence[Mapping[str, Any]],
    ramp_steps: int = DEFAULT_RAMP_STEPS,
    interpolate_fps: int = 0,
    mute_audio: bool = False,
) -> Dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    if duration <= 0 or fps <= 0:
        raise ValueError("duration and fps must be greater than zero")
    if interpolate_fps < 0 or interpolate_fps > 480:
        raise ValueError("interpolate_fps must be between 0 and 480")
    normalized = normalize_events(events, duration)
    pieces = compile_pieces(normalized, duration=duration, ramp_steps=ramp_steps)
    min_speed = min(float(piece["speed"]) for piece in pieces)
    max_speed = max(float(piece["speed"]) for piece in pieces)
    output_duration = float(pieces[-1]["output_end"])
    warnings = _quality_warnings(
        normalized,
        duration=duration,
        fps=fps,
        has_audio=has_audio,
        interpolate_fps=interpolate_fps,
        mute_audio=mute_audio,
        pieces=pieces,
    )

    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": {
            "path": str(source),
            "sha256": _sha256(source),
            "size_bytes": source.stat().st_size,
            "duration": _round(duration),
            "fps": _round(fps),
            "has_audio": bool(has_audio),
        },
        "events": normalized,
        "settings": {
            "ramp_steps": ramp_steps,
            "interpolate_fps": int(interpolate_fps),
            "mute_audio": bool(mute_audio),
            "speed_bounds": [MIN_SPEED, MAX_SPEED],
        },
        "pieces": pieces,
        "output": {
            "duration": _round(output_duration),
            "fps": _round(fps),
            "audio": bool(has_audio and not mute_audio),
        },
        "review_contract": {
            "required": True,
            "instructions": [
                "Watch the rendered output at 1x with audio.",
                "Verify each snap/curve lands on the intended impact frame.",
                "Inspect interpolation for warping, duplicated limbs, or edge artifacts.",
                "Re-run render_qa.py and invalidate downstream subtitles/timecoded artifacts after timing changes.",
            ],
        },
        "warnings": warnings,
        "blockers": [],
        "summary": {
            "events": len(normalized),
            "pieces": len(pieces),
            "source_duration": _round(duration),
            "output_duration": _round(output_duration),
            "minimum_speed": _round(min_speed),
            "maximum_speed": _round(max_speed),
            "blocking": 0,
            "warnings": len(warnings),
        },
        "status": "review" if warnings else "ready",
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    if plan.get("plan_id") != _plan_id(plan):
        blockers.append("plan_id does not match canonical plan content")

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

    source_duration = _as_float(source.get("duration"))
    source_fps = _as_float(source.get("fps"))
    if source_duration is None or source_duration <= 0:
        blockers.append("source.duration must be greater than zero")
    if source_fps is None or source_fps <= 0:
        blockers.append("source.fps must be greater than zero")

    pieces = plan.get("pieces") if isinstance(plan.get("pieces"), list) else []
    events = plan.get("events") if isinstance(plan.get("events"), list) else []
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    ramp_steps = settings.get("ramp_steps")
    interpolate_fps = settings.get("interpolate_fps")
    mute_audio = settings.get("mute_audio")
    has_audio = source.get("has_audio")
    if isinstance(ramp_steps, bool) or not isinstance(ramp_steps, int) or not 2 <= ramp_steps <= 60:
        blockers.append("settings.ramp_steps must be an integer between 2 and 60")
    if isinstance(interpolate_fps, bool) or not isinstance(interpolate_fps, int) or not 0 <= interpolate_fps <= 480:
        blockers.append("settings.interpolate_fps must be an integer between 0 and 480")
    if not isinstance(mute_audio, bool):
        blockers.append("settings.mute_audio must be boolean")
    if not isinstance(has_audio, bool):
        blockers.append("source.has_audio must be boolean")
    if settings.get("speed_bounds") != [MIN_SPEED, MAX_SPEED]:
        blockers.append("settings.speed_bounds does not match supported bounds")

    if not pieces:
        blockers.append("pieces must be a non-empty list")
    source_cursor = 0.0
    output_cursor = 0.0
    for index, piece in enumerate(pieces, start=1):
        if not isinstance(piece, Mapping):
            blockers.append(f"piece {index} must be an object")
            continue
        start = _as_float(piece.get("source_start"))
        end = _as_float(piece.get("source_end"))
        speed = _as_float(piece.get("speed"))
        output_start = _as_float(piece.get("output_start"))
        output_end = _as_float(piece.get("output_end"))
        if None in {start, end, speed, output_start, output_end}:
            blockers.append(f"piece {index} has invalid numeric fields")
            continue
        assert start is not None and end is not None and speed is not None
        assert output_start is not None and output_end is not None
        if abs(start - source_cursor) > 2e-5:
            blockers.append(f"piece {index} breaks source coverage at {source_cursor:.6f}s")
        if abs(output_start - output_cursor) > 2e-5:
            blockers.append(f"piece {index} breaks output coverage at {output_cursor:.6f}s")
        if end <= start:
            blockers.append(f"piece {index} source range is not positive")
        if not MIN_SPEED <= speed <= MAX_SPEED:
            blockers.append(f"piece {index} speed is outside supported bounds")
        expected = (end - start) / speed if speed > 0 else -1.0
        if abs((output_end - output_start) - expected) > 3e-5:
            blockers.append(f"piece {index} output duration does not match source duration / speed")
        source_cursor = end
        output_cursor = output_end

    if source_duration is not None and pieces and abs(source_cursor - source_duration) > 2e-5:
        blockers.append("piece timeline does not cover the complete source duration")
    output = plan.get("output") if isinstance(plan.get("output"), Mapping) else {}
    declared_output = _as_float(output.get("duration"))
    if declared_output is None or abs(declared_output - output_cursor) > 2e-5:
        blockers.append("output.duration does not match compiled piece timeline")
    output_fps = _as_float(output.get("fps"))
    if source_fps is not None and (output_fps is None or abs(output_fps - source_fps) > 2e-5):
        blockers.append("output.fps must match source.fps")
    if isinstance(has_audio, bool) and isinstance(mute_audio, bool):
        expected_audio = has_audio and not mute_audio
        if output.get("audio") is not expected_audio:
            blockers.append("output.audio does not match source audio / mute_audio settings")

    normalized_events: List[Dict[str, Any]] = []
    recomputed_pieces: List[Dict[str, Any]] = []
    can_recompile = (
        source_duration is not None
        and source_duration > 0
        and isinstance(ramp_steps, int)
        and not isinstance(ramp_steps, bool)
        and 2 <= ramp_steps <= 60
    )
    if can_recompile:
        try:
            normalized_events = normalize_events(events, source_duration)
            recomputed_pieces = compile_pieces(
                normalized_events,
                duration=source_duration,
                ramp_steps=ramp_steps,
            )
        except ValueError as exc:
            blockers.append(f"events cannot be compiled: {exc}")
        else:
            if normalized_events != events:
                blockers.append("events are not in canonical normalized form")
            if recomputed_pieces != pieces:
                blockers.append("pieces do not match events and ramp_steps")

    computed_warnings: List[str] = []
    can_recompute_warnings = (
        bool(recomputed_pieces)
        and source_duration is not None
        and source_fps is not None
        and isinstance(has_audio, bool)
        and isinstance(interpolate_fps, int)
        and not isinstance(interpolate_fps, bool)
        and isinstance(mute_audio, bool)
    )
    if can_recompute_warnings:
        computed_warnings = _quality_warnings(
            normalized_events,
            duration=source_duration,
            fps=source_fps,
            has_audio=has_audio,
            interpolate_fps=interpolate_fps,
            mute_audio=mute_audio,
            pieces=recomputed_pieces,
        )
        if plan.get("warnings") != computed_warnings:
            blockers.append("warnings do not match the compiled speed/fps/audio evidence")

        speeds = [float(piece["speed"]) for piece in recomputed_pieces]
        expected_summary = {
            "events": len(normalized_events),
            "pieces": len(recomputed_pieces),
            "source_duration": _round(source_duration),
            "output_duration": _round(float(recomputed_pieces[-1]["output_end"])),
            "minimum_speed": _round(min(speeds)),
            "maximum_speed": _round(max(speeds)),
            "blocking": 0,
            "warnings": len(computed_warnings),
        }
        if plan.get("summary") != expected_summary:
            blockers.append("summary does not match compiled plan evidence")
        expected_status = "review" if computed_warnings else "ready"
        if plan.get("status") != expected_status:
            blockers.append("status does not match compiled warnings")
    if plan.get("blockers") != []:
        blockers.append("blockers must be an empty list in a generated plan")
    if not isinstance(plan.get("review_contract"), Mapping) or not plan.get("review_contract", {}).get("required"):
        blockers.append("review_contract.required must be true")

    warnings.extend(computed_warnings)
    return {
        "version": "speed_ramp_verify.v1",
        "plan_id": plan.get("plan_id"),
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {"blocking": len(set(blockers)), "warnings": len(set(warnings))},
    }


def _atempo_filters(speed: float) -> List[str]:
    factors: List[float] = []
    remaining = float(speed)
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-9:
        factors.append(remaining)
    return [f"atempo={factor:.8f}" for factor in factors]


def build_filter_graph(plan: Mapping[str, Any]) -> str:
    pieces = plan["pieces"]
    source_fps = float(plan["source"]["fps"])
    interpolation = int(plan["settings"].get("interpolate_fps") or 0)
    include_audio = bool(plan["output"].get("audio"))
    filters: List[str] = []
    concat_inputs: List[str] = []
    for index, piece in enumerate(pieces):
        start = float(piece["source_start"])
        end = float(piece["source_end"])
        speed = float(piece["speed"])
        video_filters = [
            f"trim=start={start:.6f}:end={end:.6f}",
            "setpts=PTS-STARTPTS",
        ]
        if interpolation and speed < 1.0 - 1e-9:
            video_filters.append(f"minterpolate=fps={interpolation}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir")
        video_filters.extend([f"setpts=PTS/{speed:.8f}", f"fps={source_fps:.6f}"])
        filters.append(f"[0:v]{','.join(video_filters)}[v{index}]")
        concat_inputs.append(f"[v{index}]")

        if include_audio:
            audio_filters = [
                f"atrim=start={start:.6f}:end={end:.6f}",
                "asetpts=PTS-STARTPTS",
                *_atempo_filters(speed),
            ]
            filters.append(f"[0:a]{','.join(audio_filters)}[a{index}]")
            concat_inputs.append(f"[a{index}]")

    audio_count = 1 if include_audio else 0
    filters.append(
        f"{''.join(concat_inputs)}concat=n={len(pieces)}:v=1:a={audio_count}"
        f"[vconcat]{'[aout]' if include_audio else ''}"
    )
    filters.append(f"[vconcat]fps={source_fps:.6f}[vout]")
    return ";".join(filters)


def build_ffmpeg_command(plan: Mapping[str, Any], output_path: str) -> List[str]:
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
    if plan["output"].get("audio"):
        command.extend(["-map", "[aout]"])
    command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"])
    if plan["output"].get("audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", output_path])
    return command


def apply_plan(plan: Mapping[str, Any], output_path: str, *, force: bool = False) -> Dict[str, Any]:
    verification = verify_plan(plan)
    if verification["summary"]["blocking"]:
        raise ValueError("speed-ramp plan is blocked: " + "; ".join(verification["blockers"]))
    output = Path(output_path).expanduser().resolve()
    source = Path(str(plan["source"]["path"])).resolve()
    if output.suffix.lower() != ".mp4":
        raise ValueError("speed-ramp output must use the .mp4 extension")
    if output == source:
        raise ValueError("output must not overwrite the source video")
    if output.is_symlink():
        raise ValueError("output must not be a symlink")
    if output.exists() and not force:
        raise ValueError(f"output already exists; use --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        command = build_ffmpeg_command(plan, str(temporary))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg speed-ramp render failed: {result.stderr.strip()}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("ffmpeg did not create a usable output file")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

    output_probe = probe_media(str(output))
    return {
        "version": APPLY_VERSION,
        "applied_at": utc_now(),
        "plan_id": plan.get("plan_id"),
        "source_sha256": plan["source"]["sha256"],
        "output": {
            "path": str(output),
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
            "duration": _round(output_probe["duration"]),
            "fps": _round(output_probe["fps"]),
            "has_audio": bool(output_probe["has_audio"]),
        },
        "review_required": True,
    }


def render_markdown(plan: Mapping[str, Any], *, plan_path: str = "work/speed_ramp_plan.json") -> str:
    summary = plan["summary"]
    source = plan["source"]
    lines = [
        "# Speed Ramp Plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- Plan ID: `{plan['plan_id']}`",
        f"- Source: `{source['path']}` (`{source['duration']:.3f}s`, `{source['fps']:.3f} fps`)",
        f"- Speed range: `{summary['minimum_speed']:.3f}x` – `{summary['maximum_speed']:.3f}x`",
        f"- Output estimate: `{summary['output_duration']:.3f}s` across {summary['pieces']} pieces",
        f"- Interpolation: `{plan['settings']['interpolate_fps'] or 'off'}`",
        f"- Audio: `{'muted' if not plan['output']['audio'] else 'tempo-matched'}`",
        "",
        "## Events",
        "",
        "| ID | Kind | Source range | Speed | Curve / impact anchor |",
        "|---|---|---:|---:|---|",
    ]
    for event in plan["events"]:
        if event["kind"] == "hold":
            speed = f"{event['speed']:.3f}x"
            curve = "constant"
        else:
            speed = f"{event['from_speed']:.3f}x → {event['to_speed']:.3f}x"
            anchor = (float(event["start"]) + float(event["end"])) / 2.0
            curve = f"{event['curve']} / {anchor:.3f}s"
        lines.append(
            f"| {event['id']} | {event['kind']} | {event['start']:.3f}–{event['end']:.3f}s | {speed} | {curve} |"
        )

    if plan.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    lines.extend(
        [
            "",
            "## Apply after review",
            "",
            "```bash",
            " ".join(
                shlex.quote(part)
                for part in [
                    "python3",
                    "scripts/speed_ramp.py",
                    "apply",
                    plan_path,
                    "--output",
                    "work/speed-ramped.mp4",
                    "--receipt",
                    "work/speed_ramp_apply.json",
                ]
            ),
            "```",
            "",
            "Watch the result at 1× with audio. Confirm every impact frame, curve, and interpolation window; "
            "then re-run render QA and regenerate any subtitle or timecoded artifact whose timing changed.",
            "",
            "This plan is source-bound and local. It does not detect impacts, call a provider, or make AI interpolation claims.",
            "",
        ]
    )
    return "\n".join(lines)


def load_plan(path: str) -> Dict[str, Any]:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("plan JSON root must be an object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan, verify, and apply local speed ramps")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Create a source-bound speed-ramp plan")
    plan_parser.add_argument("video")
    plan_parser.add_argument("--ramp", action="append", default=[], metavar="START,END,FROM,TO,CURVE")
    plan_parser.add_argument("--hold", action="append", default=[], metavar="START,END,SPEED")
    plan_parser.add_argument("--ramp-steps", type=int, default=DEFAULT_RAMP_STEPS)
    plan_parser.add_argument("--interpolate-fps", type=int, default=0, help="Opt-in FFmpeg motion interpolation before slowing")
    plan_parser.add_argument("--mute-audio", action="store_true")
    plan_parser.add_argument("--output", default="work/speed_ramp_plan.json")
    plan_parser.add_argument("--markdown")

    verify_parser = subparsers.add_parser("verify", help="Recompute plan digest and source binding")
    verify_parser.add_argument("plan")
    verify_parser.add_argument("--json")
    verify_parser.add_argument("--strict", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Render a verified plan transactionally")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--receipt")
    apply_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            metadata = probe_media(args.video)
            events = [parse_ramp(value) for value in args.ramp] + [parse_hold(value) for value in args.hold]
            plan = build_speed_ramp_plan(
                args.video,
                duration=metadata["duration"],
                fps=metadata["fps"],
                has_audio=metadata["has_audio"],
                events=events,
                ramp_steps=args.ramp_steps,
                interpolate_fps=args.interpolate_fps,
                mute_audio=args.mute_audio,
            )
            _write_json(args.output, plan)
            if args.markdown:
                _write_text(args.markdown, render_markdown(plan, plan_path=args.output))
            print(json.dumps(plan["summary"], ensure_ascii=False))
            return 0

        plan = load_plan(args.plan)
        if args.command == "verify":
            verification = verify_plan(plan)
            if args.json:
                _write_json(args.json, verification)
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        receipt = apply_plan(plan, args.output, force=args.force)
        if args.receipt:
            _write_json(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
