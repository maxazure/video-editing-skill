#!/usr/bin/env python3
"""Plan, apply, and verify source-bound VFR-to-CFR working copies.

The workflow is deliberately narrow: inspect decoded presentation timestamps,
bind the exact source and requested target rate, encode a new H.264/AAC MP4,
prove constant cadence and stream timing, fully decode it, then atomically
promote it.  Source media is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


VERSION = "frame_rate_conform.v1"
CADENCE_ALGORITHM = "ffprobe_best_effort_pts_interval.v1"
PENDING_APPLY = "frame-rate conform has not been applied and validated"
MP4_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
STANDARD_RATES = (
    Fraction(24000, 1001),
    Fraction(24, 1),
    Fraction(25, 1),
    Fraction(30000, 1001),
    Fraction(30, 1),
    Fraction(50, 1),
    Fraction(60000, 1001),
    Fraction(60, 1),
    Fraction(120, 1),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run_command(command)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 3000:
        detail = detail[-3000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _fraction_float(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            parsed = float(numerator) / float(denominator)
        else:
            parsed = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_rate(value: Union[str, float, int]) -> Dict[str, Any]:
    text = str(value).strip()
    if not text:
        raise ValueError("target frame rate is required")
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid target frame rate: {value}") from exc
    if rate <= 0 or float(rate) > 240:
        raise ValueError("target frame rate must be greater than 0 and at most 240 fps")
    if "/" not in text:
        for standard in STANDARD_RATES:
            if abs(float(rate) - float(standard)) <= 0.001:
                rate = standard
                break
        else:
            rate = rate.limit_denominator(100000)
    rational = f"{rate.numerator}/{rate.denominator}"
    return {
        "numerator": rate.numerator,
        "denominator": rate.denominator,
        "rational": rational,
        "fps": round(float(rate), 9),
    }


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


def _bit_depth(video: Mapping[str, Any]) -> Optional[int]:
    try:
        explicit = int(video.get("bits_per_raw_sample") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    pixel_format = str(video.get("pix_fmt") or "").lower()
    for token in ("p16", "p14", "p12", "p10", "p9"):
        if token in pixel_format:
            return int(token[1:])
    return 8 if pixel_format else None


def _stream_duration(stream: Mapping[str, Any], fallback: Optional[float]) -> Optional[float]:
    duration = _fraction_float(stream.get("duration"))
    if duration is not None:
        return duration
    duration_ts = _fraction_float(stream.get("duration_ts"))
    time_base = _fraction_float(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return duration_ts * time_base
    return fallback


def probe_media(path: Path) -> Dict[str, Any]:
    result = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next(
        (item for item in data.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if not video:
        raise ValueError(f"video stream not found: {path}")
    audio = next(
        (item for item in data.get("streams", []) if item.get("codec_type") == "audio"),
        None,
    )
    format_duration = _fraction_float((data.get("format") or {}).get("duration"))
    video_duration = _stream_duration(video, format_duration)
    audio_duration = _stream_duration(audio or {}, format_duration) if audio else None
    duration = video_duration or format_duration
    avg_fps = _fraction_float(video.get("avg_frame_rate"))
    nominal_fps = _fraction_float(video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if duration is None or duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {path}")
    video_start = _fraction_float(video.get("start_time")) or 0.0
    audio_start = (_fraction_float((audio or {}).get("start_time")) or 0.0) if audio else None
    return {
        "duration": round(duration, 6),
        "video_duration": round(video_duration or duration, 6),
        "audio_duration": round(audio_duration, 6) if audio_duration is not None else None,
        "avg_frame_rate": str(video.get("avg_frame_rate") or "0/0"),
        "r_frame_rate": str(video.get("r_frame_rate") or "0/0"),
        "avg_fps": round(avg_fps, 9) if avg_fps else None,
        "nominal_fps": round(nominal_fps, 9) if nominal_fps else None,
        "width": width,
        "height": height,
        "rotation": rotation,
        "video_start_time": round(video_start, 6),
        "audio_start_time": round(audio_start, 6) if audio_start is not None else None,
        "has_audio": audio is not None,
        "video_codec": str(video.get("codec_name") or "").lower(),
        "audio_codec": str((audio or {}).get("codec_name") or "").lower() or None,
        "sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
        "channels": int((audio or {}).get("channels") or 0) or None,
        "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
        "bit_depth": _bit_depth(video),
        "sample_aspect_ratio": str(video.get("sample_aspect_ratio") or "unknown"),
        "color_primaries": str(video.get("color_primaries") or "unknown").lower(),
        "color_transfer": str(video.get("color_transfer") or "unknown").lower(),
        "color_space": str(video.get("color_space") or "unknown").lower(),
        "color_range": str(video.get("color_range") or "unknown").lower(),
        "format_names": sorted(
            token.strip().lower()
            for token in str((data.get("format") or {}).get("format_name") or "").split(",")
            if token.strip()
        ),
    }


def analyze_cadence(path: Path, *, tolerance_ratio: float = 0.02) -> Dict[str, Any]:
    if tolerance_ratio <= 0 or tolerance_ratio > 0.25:
        raise ValueError("cadence tolerance ratio must be greater than 0 and at most 0.25")
    result = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"frame timestamp probe failed for {path}")
    timestamps: List[float] = []
    for raw in (result.stdout or "").splitlines():
        token = raw.strip().split(",", 1)[0]
        try:
            value = float(token)
        except ValueError:
            continue
        if math.isfinite(value):
            timestamps.append(value)
    if len(timestamps) < 2:
        raise ValueError(f"at least two decoded video timestamps are required: {path}")
    deltas = [right - left for left, right in zip(timestamps, timestamps[1:])]
    positive = [item for item in deltas if item > 0]
    non_monotonic = len(deltas) - len(positive)
    if not positive:
        raise ValueError(f"decoded video timestamps are not monotonic: {path}")
    median = statistics.median(positive)
    tolerance_seconds = max(0.000002, median * tolerance_ratio)
    variable = sum(1 for item in positive if abs(item - median) > tolerance_seconds)
    ordered = sorted(positive)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "algorithm": CADENCE_ALGORITHM,
        "tolerance_ratio": tolerance_ratio,
        "tolerance_seconds": round(tolerance_seconds, 9),
        "frame_count": len(timestamps),
        "interval_count": len(deltas),
        "non_monotonic_intervals": non_monotonic,
        "variable_intervals": variable,
        "variable_ratio": round((variable + non_monotonic) / len(deltas), 9),
        "is_variable": bool(variable or non_monotonic),
        "interval_seconds": {
            "min": round(min(positive), 9),
            "p05": round(percentile(0.05), 9),
            "median": round(median, 9),
            "mean": round(statistics.fmean(positive), 9),
            "p95": round(percentile(0.95), 9),
            "max": round(max(positive), 9),
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def _source_info(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path), "cadence": analyze_cadence(path)}


def _output_info(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path), "cadence": analyze_cadence(path)}


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _inside_project(path: Path, project_root: Path) -> bool:
    try:
        path.resolve().relative_to(project_root.resolve())
        return True
    except ValueError:
        return False


def _resolve_project_path(
    value: str,
    project_root: Path,
    *,
    label: str,
    must_exist: bool,
    suffix: Optional[str] = None,
) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    if not _inside_project(resolved, project_root):
        raise ValueError(f"{label} must stay inside the project directory")
    if must_exist and not resolved.is_file():
        raise ValueError(f"{label} does not exist: {resolved}")
    if suffix and resolved.suffix.lower() != suffix:
        raise ValueError(f"{label} must use {suffix}")
    return resolved


def _settings_for(source: Mapping[str, Any], target_fps: Union[str, float, int]) -> Dict[str, Any]:
    target = parse_rate(target_fps)
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (isinstance(bit_depth, int) and bit_depth > 8):
        raise ValueError(
            "HDR/BT.2020/greater-than-8-bit source requires an explicit color workflow; "
            "use hdr_sdr.py for an SDR derivative before frame-rate conformance"
        )
    frame_seconds = 1.0 / float(target["fps"])
    rate = str(target["rational"])
    return {
        "cadence_algorithm": CADENCE_ALGORITHM,
        "cadence_tolerance_ratio": 0.02,
        "target_rate": target,
        "video_filter": f"fps=fps={rate}:start_time=0:round=near,setsar=1,format=yuv420p",
        "audio_filter": "aresample=async=1:first_pts=0,asetpts=N/SR/TB" if source.get("has_audio") else None,
        "container": "mp4",
        "video_codec": "h264",
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "pixel_format": "yuv420p",
        "audio_codec": "aac" if source.get("has_audio") else None,
        "audio_bitrate_kbps": 192 if source.get("has_audio") else None,
        "audio_sample_rate": 48000 if source.get("has_audio") else None,
        "duration_tolerance_seconds": round(max(0.1, frame_seconds * 2), 6),
        "stream_start_tolerance_seconds": round(max(0.02, frame_seconds), 6),
        "av_end_tolerance_seconds": round(max(0.1, frame_seconds * 2), 6),
        "frame_count_tolerance": 1,
    }


def _computed_warnings(source: Mapping[str, Any], settings: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    cadence = source.get("cadence") if isinstance(source.get("cadence"), Mapping) else {}
    target = float((settings.get("target_rate") or {}).get("fps") or 0)
    median_interval = float((cadence.get("interval_seconds") or {}).get("median") or 0)
    measured = 1.0 / median_interval if median_interval > 0 else float(source.get("avg_fps") or 0)
    if not cadence.get("is_variable"):
        warnings.append("Source decoded timestamps already appear constant; this conform still re-encodes the picture.")
    if measured > 0 and target < measured - 0.05:
        warnings.append("Target fps is below the measured source cadence; motion samples will be dropped.")
    elif measured > 0 and target > measured + 0.05:
        warnings.append("Target fps is above the measured source cadence; frames will be duplicated, not reconstructed.")
    return warnings


def _source_timing_blockers(source: Mapping[str, Any], settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if not source.get("has_audio"):
        return blockers
    start_tolerance = float(settings.get("stream_start_tolerance_seconds") or 0)
    video_start = float(source.get("video_start_time") or 0)
    audio_start = float(source.get("audio_start_time") or 0)
    if abs(video_start - audio_start) > start_tolerance:
        blockers.append(
            "source audio/video start offset exceeds one target frame; decide or repair sync before conforming"
        )
    video_end = video_start + float(source.get("video_duration") or 0)
    audio_end = audio_start + float(source.get("audio_duration") or 0)
    if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
        blockers.append(
            "source audio/video end offset exceeds the conform tolerance; inspect drift or intentional tails first"
        )
    return blockers


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_root": plan.get("project_root"),
        "source": plan.get("source"),
        "settings": plan.get("settings"),
        "delivery": plan.get("delivery"),
        "application": plan.get("application"),
        "review_contract": plan.get("review_contract"),
        "blockers": plan.get("blockers"),
        "warnings": plan.get("warnings"),
        "summary": plan.get("summary"),
        "status": plan.get("status"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_live_file(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.is_symlink():
        blockers.append(f"{label}.path must not be a symlink")
        return None
    if not candidate.is_file():
        blockers.append(f"{label} file is missing: {candidate}")
        return None
    if record.get("size_bytes") != candidate.stat().st_size:
        blockers.append(f"{label} size changed")
    elif record.get("sha256") != _sha256(candidate):
        blockers.append(f"{label} sha256 changed")
    return candidate


def _format_matches_mp4(format_names: Any) -> bool:
    return bool({str(item).lower() for item in format_names or []}.intersection(MP4_FORMATS))


def _output_contract_blockers(
    media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any]
) -> List[str]:
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append("conformed working copy is not an MP4-family container")
    if media.get("video_codec") != "h264":
        blockers.append("conformed working copy video codec must be H.264")
    if media.get("pixel_format") != "yuv420p":
        blockers.append("conformed working copy pixel format must be yuv420p")
    if media.get("rotation") != 0:
        blockers.append("conformed working copy must bake display rotation and clear rotation metadata")
    if media.get("width") != source.get("width") or media.get("height") != source.get("height"):
        blockers.append("conformed working copy displayed dimensions do not match the source")
    target = float((settings.get("target_rate") or {}).get("fps") or 0)
    observed = float(media.get("avg_fps") or 0)
    if abs(observed - target) > 0.001:
        blockers.append("conformed working copy average fps does not match the exact target rate")
    cadence = media.get("cadence") if isinstance(media.get("cadence"), Mapping) else {}
    if cadence.get("algorithm") != CADENCE_ALGORITHM:
        blockers.append("conformed working copy cadence algorithm is missing or stale")
    if cadence.get("is_variable") or cadence.get("non_monotonic_intervals"):
        blockers.append("conformed working copy decoded timestamps are not constant and monotonic")
    duration_tolerance = float(settings.get("duration_tolerance_seconds") or 0)
    if abs(float(media.get("video_duration") or 0) - float(source.get("video_duration") or 0)) > duration_tolerance:
        blockers.append("conformed working copy video duration drift exceeds the planned tolerance")
    if media.get("has_audio") != source.get("has_audio"):
        blockers.append("conformed working copy audio presence does not match the source")
    if source.get("has_audio"):
        if media.get("audio_codec") != "aac":
            blockers.append("conformed working copy audio codec must be AAC")
        if media.get("sample_rate") != settings.get("audio_sample_rate"):
            blockers.append("conformed working copy audio sample rate does not match the plan")
        video_start = float(media.get("video_start_time") or 0)
        audio_start = float(media.get("audio_start_time") or 0)
        start_tolerance = float(settings.get("stream_start_tolerance_seconds") or 0)
        if abs(video_start) > start_tolerance or abs(audio_start) > start_tolerance:
            blockers.append("conformed working copy audio/video streams do not start near zero")
        if abs(video_start - audio_start) > start_tolerance:
            blockers.append("conformed working copy audio/video start times differ by more than one target frame")
        video_end = video_start + float(media.get("video_duration") or 0)
        audio_end = audio_start + float(media.get("audio_duration") or 0)
        if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
            blockers.append("conformed working copy audio/video end times exceed the planned tolerance")
    frame_count = int(cadence.get("frame_count") or 0)
    expected_frames = round(float(media.get("video_duration") or 0) * target)
    if abs(frame_count - expected_frames) > int(settings.get("frame_count_tolerance") or 0):
        blockers.append("conformed working copy frame count does not match duration × target fps")
    return blockers


def _decode_command(path: Path) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0",
        "-f",
        "null",
        "-",
    ]


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    project_root = Path(str(plan.get("project_root") or "")).expanduser()
    if not project_root.is_absolute() or not project_root.is_dir():
        blockers.append("project_root must be an existing absolute directory")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    if source_path is not None:
        if project_root.is_absolute() and not _inside_project(source_path, project_root):
            blockers.append("source escaped the project directory")
        try:
            live_source = _source_info(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source cadence/media probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source fingerprint, media contract, or decoded cadence changed after planning")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    target_value = str((settings.get("target_rate") or {}).get("rational") or "")
    try:
        expected_settings = _settings_for(source, target_value)
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical frame-rate conform contract")
        blockers.extend(_source_timing_blockers(source, expected_settings))

    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    delivery_path = Path(str(delivery.get("path") or "")).expanduser()
    if not delivery_path.is_absolute():
        blockers.append("delivery.path must be absolute")
    elif project_root.is_absolute() and not _inside_project(delivery_path, project_root):
        blockers.append("delivery.path escaped the project directory")
    elif delivery_path.suffix.lower() != ".mp4":
        blockers.append("delivery.path must use .mp4")
    elif delivery_path.is_symlink():
        blockers.append("delivery.path must not be a symlink")
    if delivery != {"path": str(delivery_path), "format": "mp4", "purpose": "cfr_working_copy"}:
        blockers.append("delivery record is not canonical")
    if source.get("path") and delivery_path.is_absolute():
        if delivery_path.resolve() == Path(str(source.get("path"))).resolve():
            blockers.append("conformed working copy must not overwrite the source")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        if output.get("path") != delivery.get("path"):
            blockers.append("application.output.path does not match delivery.path")
        if output_path is not None and project_root.is_absolute() and not _inside_project(output_path, project_root):
            blockers.append("application.output escaped the project directory")
        if output_path is not None:
            try:
                live_output = _output_info(output_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"conformed working copy probe failed: {exc}")
            else:
                if output != live_output:
                    blockers.append("stored working-copy contract is stale or was modified")
                blockers.extend(_output_contract_blockers(live_output, source, settings))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_decode = _decode_command(delivery_path) if delivery_path.is_absolute() else []
        if validation.get("decode_checked") is not True:
            blockers.append("full FFmpeg decode validation is missing")
        if validation.get("decode_command") != expected_decode:
            blockers.append("decode validation command is stale or non-canonical")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("decode validation is not bound to the current working-copy sha256")
        if validation.get("cadence_algorithm") != CADENCE_ALGORITHM:
            blockers.append("cadence validation algorithm is stale or missing")
        if validation.get("target_rate") != settings.get("target_rate"):
            blockers.append("cadence validation target rate is stale or modified")
        output_cadence = output.get("cadence") if isinstance(output.get("cadence"), Mapping) else {}
        if validation.get("output_frame_count") != output_cadence.get("frame_count"):
            blockers.append("cadence validation frame count is stale or modified")

    warnings = _computed_warnings(source, settings) if settings else []
    cadence = source.get("cadence") if isinstance(source.get("cadence"), Mapping) else {}
    summary = {
        "source_variable": bool(cadence.get("is_variable")),
        "source_frames": int(cadence.get("frame_count") or 0),
        "source_variable_intervals": int(cadence.get("variable_intervals") or 0),
        "target_fps": (settings.get("target_rate") or {}).get("fps"),
        "applied": applied,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else "warn" if warnings else "ready"
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def _set_derived(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan.update(_compute_derived(plan))
    plan["plan_id"] = _plan_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(plan)
    integrity_blockers: List[str] = []
    if plan.get("plan_id") != _plan_id(plan):
        integrity_blockers.append("plan_id does not match canonical plan content")
    derived = _compute_derived(plan)
    for field in ("blockers", "warnings", "summary", "status"):
        if plan.get(field) != derived[field]:
            integrity_blockers.append(f"stored {field} is stale or was modified")
    result.update(derived)
    result["blockers"] = integrity_blockers + list(derived["blockers"])
    result["summary"] = {**derived["summary"], "blocking": len(result["blockers"])}
    result["status"] = "blocked" if result["blockers"] else derived["status"]
    return result


def build_plan(
    source_path: str,
    delivery_path: str,
    target_fps: Union[str, float, int],
    *,
    project_dir: str = ".",
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _resolve_project_path(source_path, root, label="source", must_exist=True)
    delivery = _resolve_project_path(
        delivery_path, root, label="delivery", must_exist=False, suffix=".mp4"
    )
    if delivery == source:
        raise ValueError("conformed working copy must not overwrite the source")
    source_record = _source_info(source)
    settings = _settings_for(source_record, target_fps)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_root": str(root),
        "source": source_record,
        "settings": settings,
        "delivery": {"path": str(delivery), "format": "mp4", "purpose": "cfr_working_copy"},
        "application": None,
        "review_contract": {
            "instructions": [
                "Use the conformed file, never the original VFR source, for downstream cuts, captions, sync, and renders.",
                "Watch the complete working copy at normal speed with sound; inspect pans, screen motion, speech sync, and the ending.",
                "After downstream rendering, rerun the normal final-master QA and bind the new bytes into approval receipts.",
            ],
            "limitations": [
                "CFR conformance duplicates or drops decoded pictures; it does not reconstruct missing motion samples.",
                "This workflow does not fix independent-recorder clock drift, lip sync, or an intentional source offset.",
                "HDR, BT.2020, and greater-than-8-bit inputs require an explicit color workflow before this SDR H.264 working copy.",
            ],
        },
    }
    return _set_derived(plan)


def _color_args(source: Mapping[str, Any]) -> List[str]:
    fields = (
        ("color_primaries", "-color_primaries"),
        ("color_transfer", "-color_trc"),
        ("color_space", "-colorspace"),
        ("color_range", "-color_range"),
    )
    args: List[str] = []
    for key, option in fields:
        value = str(source.get(key) or "unknown").lower()
        if value not in {"", "unknown", "unspecified", "reserved"}:
            args.extend([option, value])
    return args


def build_command(plan: Mapping[str, Any], temporary_output: Path) -> List[str]:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    rate = str((settings.get("target_rate") or {}).get("rational") or "")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source.get("path") or ""),
        "-map",
        "0:v:0",
        "-vf",
        str(settings.get("video_filter") or ""),
        "-c:v",
        "libx264",
        "-crf",
        str(settings.get("video_crf")),
        "-preset",
        str(settings.get("video_preset")),
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-r",
        rate,
        *_color_args(source),
        "-map_metadata",
        "-1",
        "-metadata:s:v:0",
        "rotate=0",
    ]
    if source.get("has_audio"):
        command.extend(
            [
                "-map",
                "0:a:0?",
                "-af",
                str(settings.get("audio_filter") or ""),
                "-c:a",
                "aac",
                "-b:a",
                f"{settings.get('audio_bitrate_kbps')}k",
                "-ar",
                str(settings.get("audio_sample_rate")),
            ]
        )
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-movflags", "+faststart", "-y", str(temporary_output)])
    return command


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read frame-rate conform plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("frame-rate conform plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("frame-rate conform plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"frame-rate conform plan must be an existing JSON file: {resolved}")
    return resolved


def _safe_delivery(path: Path, *, source: Path, plan_file: Path, project_root: Path, force: bool) -> Path:
    if path.suffix.lower() != ".mp4":
        raise ValueError("conformed working copy must use .mp4")
    if path.is_symlink():
        raise ValueError("conformed working copy must not be a symlink")
    if not _inside_project(path, project_root):
        raise ValueError("conformed working copy must stay inside the project directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if resolved in {source.resolve(), plan_file.resolve()}:
        raise ValueError("conformed working copy must not overwrite the source or plan")
    if resolved.exists() and not force:
        raise ValueError(f"conformed working copy already exists (pass --force to replace): {resolved}")
    return resolved


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    verification = verify_plan(plan)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))
    project_root = Path(str(plan["project_root"]))
    if not _inside_project(plan_file, project_root):
        raise ValueError("frame-rate conform plan must stay inside the project directory")
    source = Path(str(plan["source"]["path"]))
    delivery = _safe_delivery(
        Path(str(plan["delivery"]["path"])),
        source=source,
        plan_file=plan_file,
        project_root=project_root,
        force=force,
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".tmp.mp4", dir=str(delivery.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _run_checked(build_command(plan, temporary), "frame-rate conform encode")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("frame-rate conform encode did not create a non-empty output")
        temporary_info = _output_info(temporary)
        contract_blockers = _output_contract_blockers(
            temporary_info, plan["source"], plan["settings"]
        )
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(_decode_command(temporary), "full conformed working-copy decode validation")
        if _source_info(source) != plan["source"]:
            raise RuntimeError("source changed during frame-rate conformance; working copy was not promoted")
        os.replace(temporary, delivery)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    output = _output_info(delivery)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "validation": {
            "verified_at": utc_now(),
            "decode_checked": True,
            "decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
            "cadence_algorithm": CADENCE_ALGORITHM,
            "target_rate": plan["settings"]["target_rate"],
            "output_frame_count": output["cadence"]["frame_count"],
        },
    }
    _set_derived(plan)
    final_verification = verify_plan(plan)
    if final_verification.get("blockers"):
        raise RuntimeError("applied frame-rate conform plan failed final verification")
    _atomic_write_json(plan_file, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    cadence = source.get("cadence") or {}
    intervals = cadence.get("interval_seconds") or {}
    target = (plan.get("settings") or {}).get("target_rate") or {}
    delivery = plan.get("delivery") or {}
    lines = [
        "# Frame-rate Conform Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Source cadence: `{source.get('avg_frame_rate')}` average / `{source.get('r_frame_rate')}` nominal",
        f"- Decoded frames: `{cadence.get('frame_count', 0)}`",
        f"- Variable intervals: `{cadence.get('variable_intervals', 0)}` / `{cadence.get('interval_count', 0)}`",
        f"- Interval min / median / max: `{intervals.get('min')}` / `{intervals.get('median')}` / `{intervals.get('max')}` seconds",
        f"- Target CFR: `{target.get('rational')}` (`{target.get('fps')}` fps)",
        f"- Working copy: `{delivery.get('path', '')}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(["", "## Required review", ""])
    lines.extend(f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions", []))
    lines.append("")
    return "\n".join(lines)


def _artifact_destination(
    path: str, suffix: str, project_root: Path, forbidden: Sequence[Path]
) -> Path:
    resolved = _resolve_project_path(
        path, project_root, label="output artifact", must_exist=False, suffix=suffix
    )
    if any(resolved == item.resolve() for item in forbidden):
        raise ValueError("output artifact must not overwrite the source or conformed working copy")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify a source-bound constant-frame-rate working copy."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser(
        "plan", help="Measure source cadence and bind an exact target frame rate."
    )
    plan_parser.add_argument("source", help="Project-local source video.")
    plan_parser.add_argument("--fps", required=True, help="Exact target fps, e.g. 30 or 30000/1001.")
    plan_parser.add_argument("--delivery", required=True, help="Project-local CFR working-copy MP4.")
    plan_parser.add_argument("--project-dir", default=".", help="Project root for path containment.")
    plan_parser.add_argument("--output", required=True, help="Plan JSON path.")
    plan_parser.add_argument("--markdown", help="Optional human-readable review path.")
    plan_parser.add_argument("--force", action="store_true", help="Replace plan/review artifacts.")
    apply_parser = subparsers.add_parser(
        "apply",
        help="Conform, validate cadence and timing, fully decode, and atomically promote.",
        description="Conform, validate cadence and timing, fully decode, and atomically promote.",
    )
    apply_parser.add_argument("plan", help="Existing frame-rate conform plan JSON.")
    apply_parser.add_argument("--force", action="store_true", help="Replace an existing working copy.")
    verify_parser = subparsers.add_parser(
        "verify", help="Live-verify plan, source, working copy, cadence, timing, and hashes."
    )
    verify_parser.add_argument("plan", help="Existing frame-rate conform plan JSON.")
    verify_parser.add_argument("--strict", action="store_true", help="Return 2 for blockers or warnings.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            root = Path(args.project_dir).expanduser().resolve()
            plan = build_plan(args.source, args.delivery, args.fps, project_dir=str(root))
            forbidden = [Path(plan["source"]["path"]), Path(plan["delivery"]["path"])]
            output = _artifact_destination(args.output, ".json", root, forbidden)
            markdown = (
                _artifact_destination(args.markdown, ".md", root, forbidden)
                if args.markdown
                else None
            )
            if markdown is not None and markdown == output:
                raise ValueError("plan JSON and Markdown outputs must be different files")
            for candidate in (output, markdown):
                if candidate is not None and candidate.exists() and not args.force:
                    raise ValueError(f"output artifact already exists (pass --force to replace): {candidate}")
            _atomic_write_json(output, plan)
            if markdown is not None:
                _atomic_write_text(markdown, render_markdown(plan))
            print(
                f"Frame-rate conform plan: {plan['status']} "
                f"(blocking={plan['summary']['blocking']}, warnings={plan['summary']['warnings']})"
            )
            return 0
        if args.command == "apply":
            plan = apply_plan(args.plan, force=args.force)
            print(
                f"Frame-rate conform: {plan['status']} "
                f"(blocking={plan['summary']['blocking']}, warnings={plan['summary']['warnings']})"
            )
            return 0
        plan_file = _resolve_plan_file(args.plan)
        verification = verify_plan(_load_plan(plan_file))
        print(
            f"Frame-rate conform verification: {verification['status']} "
            f"(blocking={verification['summary']['blocking']}, "
            f"warnings={verification['summary']['warnings']})"
        )
        if verification["blockers"] or (args.strict and verification["warnings"]):
            return 2
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
