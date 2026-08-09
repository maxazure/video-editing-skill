#!/usr/bin/env python3
"""Plan, apply, and verify source-bound target-size delivery encodes.

The workflow is intentionally narrow: create an H.264/AAC MP4 under an explicit
MiB ceiling without modifying the source.  A plan records the source hash and a
deterministic two-pass bitrate contract.  Apply renders to a sibling temporary
file, validates the encoded media, performs a full FFmpeg decode, and only then
atomically promotes it to the requested delivery path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "delivery_encode.v1"
PENDING_APPLY = "delivery encode has not been applied and validated"
SAFETY_FACTOR = 0.94
MIN_VIDEO_BITRATE_BPS = 150_000
PRESETS = {"veryfast", "medium", "slow"}
MP4_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}


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


def _fraction(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            result = float(numerator) / denominator_value
        else:
            result = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) else None


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
    duration = _fraction((data.get("format") or {}).get("duration"))
    if duration is None:
        duration = _fraction(video.get("duration"))
    fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if duration is None or duration <= 0 or fps is None or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {path}")
    return {
        "duration": round(duration, 6),
        "fps": round(fps, 6),
        "width": width,
        "height": height,
        "rotation": rotation,
        "has_audio": audio is not None,
        "video_codec": str(video.get("codec_name") or "").lower(),
        "audio_codec": str((audio or {}).get("codec_name") or "").lower() or None,
        "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
        "format_names": sorted(
            token.strip().lower()
            for token in str((data.get("format") or {}).get("format_name") or "").split(",")
            if token.strip()
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _source_info(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path)}


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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _even(value: float) -> int:
    parsed = max(2, int(round(value)))
    return parsed if parsed % 2 == 0 else parsed - 1


def _fit_within(width: int, height: int, max_width: Optional[int], max_height: Optional[int]) -> tuple[int, int]:
    if max_width is not None and max_width < 2:
        raise ValueError("max_width must be at least 2")
    if max_height is not None and max_height < 2:
        raise ValueError("max_height must be at least 2")
    if max_width is None and max_height is None:
        return width, height
    ratio = min((max_width or width) / width, (max_height or height) / height, 1.0)
    return _even(width * ratio), _even(height * ratio)


def _request_record(
    *,
    max_size_mib: float,
    audio_bitrate_kbps: int,
    max_width: Optional[int],
    max_height: Optional[int],
    fps: Optional[float],
    preset: str,
) -> Dict[str, Any]:
    return {
        "max_size_mib": round(float(max_size_mib), 6),
        "audio_bitrate_kbps": int(audio_bitrate_kbps),
        "max_width": int(max_width) if max_width is not None else None,
        "max_height": int(max_height) if max_height is not None else None,
        "fps": round(float(fps), 6) if fps is not None else None,
        "preset": str(preset),
    }


def _settings_for(source: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        max_size_mib = float(request.get("max_size_mib"))
        audio_bitrate_kbps = int(request.get("audio_bitrate_kbps"))
        preset = str(request.get("preset") or "")
    except (TypeError, ValueError) as exc:
        raise ValueError("delivery request contains invalid numeric values") from exc
    if not math.isfinite(max_size_mib) or max_size_mib <= 0:
        raise ValueError("max_size_mib must be positive")
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of: {', '.join(sorted(PRESETS))}")
    if not 64 <= audio_bitrate_kbps <= 320:
        raise ValueError("audio_bitrate_kbps must be between 64 and 320")

    duration = float(source.get("duration") or 0)
    source_fps = float(source.get("fps") or 0)
    width = int(source.get("width") or 0)
    height = int(source.get("height") or 0)
    if duration <= 0 or source_fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("source media contract is incomplete")
    requested_fps = request.get("fps")
    target_fps = source_fps if requested_fps is None else float(requested_fps)
    if not math.isfinite(target_fps) or target_fps <= 0:
        raise ValueError("fps must be positive")
    if target_fps > source_fps + max(0.01, source_fps * 0.001):
        raise ValueError("delivery encoding will not synthesize frames above the source fps")

    target_width, target_height = _fit_within(
        width,
        height,
        request.get("max_width"),
        request.get("max_height"),
    )
    target_size_bytes = int(max_size_mib * 1024 * 1024)
    audio_bitrate_bps = audio_bitrate_kbps * 1000 if source.get("has_audio") else 0
    total_bitrate_bps = int(target_size_bytes * 8 * SAFETY_FACTOR / duration)
    video_bitrate_bps = total_bitrate_bps - audio_bitrate_bps
    if video_bitrate_bps < MIN_VIDEO_BITRATE_BPS:
        minimum_bytes = math.ceil(
            (MIN_VIDEO_BITRATE_BPS + audio_bitrate_bps) * duration / (8 * SAFETY_FACTOR)
        )
        minimum_mib = minimum_bytes / (1024 * 1024)
        raise ValueError(
            f"target is too small for the source duration; use at least {minimum_mib:.2f} MiB "
            "or shorten/downsample the source"
        )
    return {
        "container": "mp4",
        "video_codec": "h264",
        "video_encoder": "libx264",
        "audio_codec": "aac" if source.get("has_audio") else None,
        "pixel_format": "yuv420p",
        "preset": preset,
        "safety_factor": SAFETY_FACTOR,
        "target_size_bytes": target_size_bytes,
        "target_width": target_width,
        "target_height": target_height,
        "target_fps": round(target_fps, 6),
        "video_bitrate_bps": video_bitrate_bps,
        "audio_bitrate_bps": audio_bitrate_bps,
        "duration_tolerance_seconds": round(max(0.25, 3.0 / target_fps), 6),
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "request": plan.get("request"),
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
    raw_path = str(record.get("path") or "")
    candidate = Path(raw_path).expanduser()
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
    media: Mapping[str, Any], settings: Mapping[str, Any], source: Mapping[str, Any]
) -> List[str]:
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append("delivery output is not an MP4-family container")
    if media.get("video_codec") != "h264":
        blockers.append("delivery output video codec must be H.264")
    if media.get("pixel_format") != "yuv420p":
        blockers.append("delivery output pixel format must be yuv420p")
    if media.get("width") != settings.get("target_width") or media.get("height") != settings.get("target_height"):
        blockers.append("delivery output dimensions do not match the planned contract")
    expected_fps = float(settings.get("target_fps") or 0)
    observed_fps = float(media.get("fps") or 0)
    if abs(observed_fps - expected_fps) > max(0.05, expected_fps * 0.01):
        blockers.append("delivery output fps does not match the planned contract")
    expected_duration = float(source.get("duration") or 0)
    observed_duration = float(media.get("duration") or 0)
    tolerance = float(settings.get("duration_tolerance_seconds") or 0)
    if abs(observed_duration - expected_duration) > tolerance:
        blockers.append("delivery output duration drift exceeds the planned tolerance")
    if media.get("has_audio") != source.get("has_audio"):
        blockers.append("delivery output audio presence does not match the source")
    if source.get("has_audio") and media.get("audio_codec") != "aac":
        blockers.append("delivery output audio codec must be AAC")
    if int(media.get("size_bytes") or 0) > int(settings.get("target_size_bytes") or 0):
        blockers.append("delivery output exceeds the hard maximum size")
    return blockers


def _computed_warnings(
    source: Mapping[str, Any], settings: Mapping[str, Any], application: Any
) -> List[str]:
    warnings: List[str] = []
    if int(settings.get("video_bitrate_bps") or 0) < 600_000:
        warnings.append("Planned video bitrate is below 600 kbps; inspect text, faces, and motion for compression damage.")
    if int(source.get("size_bytes") or 0) <= int(settings.get("target_size_bytes") or 0):
        warnings.append("Source is already within the requested size ceiling; re-encode only when delivery compatibility requires it.")
    source_pixels = int(source.get("width") or 0) * int(source.get("height") or 0)
    target_pixels = int(settings.get("target_width") or 0) * int(settings.get("target_height") or 0)
    if source_pixels and target_pixels / source_pixels < 0.5:
        warnings.append("Delivery resolution contains less than half the source pixels; review small text and fine detail.")
    if isinstance(application, Mapping):
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        actual = int(output.get("size_bytes") or 0)
        target = int(settings.get("target_size_bytes") or 0)
        if target and actual < target * 0.65:
            warnings.append("Encoded output is far below the size ceiling; quality may be lower than the budget permits.")
    return warnings


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

    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    if source_path is not None:
        try:
            live_source = _source_info(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source fingerprint or media contract changed after planning")

    request = plan.get("request") if isinstance(plan.get("request"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        expected_settings = _settings_for(source, request)
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical request/source contract")

    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    delivery_path = Path(str(delivery.get("path") or "")).expanduser()
    if not delivery_path.is_absolute():
        blockers.append("delivery.path must be absolute")
    elif delivery_path.suffix.lower() != ".mp4":
        blockers.append("delivery.path must use .mp4")
    elif delivery_path.is_symlink():
        blockers.append("delivery.path must not be a symlink")
    if delivery != {"path": str(delivery_path), "format": "mp4"}:
        blockers.append("delivery record is not canonical")
    if source.get("path") and delivery_path.is_absolute():
        if delivery_path.resolve() == Path(str(source.get("path"))).resolve():
            blockers.append("delivery output must not overwrite the source")

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
        if output_path is not None:
            try:
                live_media = {**_fingerprint(output_path), **probe_media(output_path)}
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"delivery output probe failed: {exc}")
            else:
                if output != live_media:
                    blockers.append("stored delivery output contract is stale or was modified")
                blockers.extend(_output_contract_blockers(live_media, settings, source))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_decode = _decode_command(delivery_path) if delivery_path.is_absolute() else []
        if validation.get("decode_checked") is not True:
            blockers.append("full FFmpeg decode validation is missing")
        if validation.get("decode_command") != expected_decode:
            blockers.append("decode validation command is stale or non-canonical")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("decode validation is not bound to the current output sha256")

    warnings = _computed_warnings(source, settings, application)
    summary = {
        "target_size_bytes": settings.get("target_size_bytes"),
        "video_bitrate_bps": settings.get("video_bitrate_bps"),
        "audio_bitrate_bps": settings.get("audio_bitrate_bps"),
        "target_width": settings.get("target_width"),
        "target_height": settings.get("target_height"),
        "target_fps": settings.get("target_fps"),
        "applied": applied,
        "output_size_bytes": (
            (application.get("output") or {}).get("size_bytes") if applied else None
        ),
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
    *,
    max_size_mib: float,
    audio_bitrate_kbps: int = 128,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    fps: Optional[float] = None,
    preset: str = "medium",
) -> Dict[str, Any]:
    source_candidate = Path(source_path).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("source must not be a symlink")
    source = source_candidate.resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    delivery_candidate = Path(delivery_path).expanduser()
    if delivery_candidate.is_symlink():
        raise ValueError("delivery output must not be a symlink")
    delivery = delivery_candidate.resolve()
    if delivery.suffix.lower() != ".mp4":
        raise ValueError("delivery output must use .mp4")
    if delivery == source:
        raise ValueError("delivery output must not overwrite the source")

    source_record = _source_info(source)
    request = _request_record(
        max_size_mib=max_size_mib,
        audio_bitrate_kbps=audio_bitrate_kbps,
        max_width=max_width,
        max_height=max_height,
        fps=fps,
        preset=preset,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": source_record,
        "request": request,
        "settings": _settings_for(source_record, request),
        "delivery": {"path": str(delivery), "format": "mp4"},
        "application": None,
        "review_contract": {
            "instructions": [
                "Watch the complete delivery file at normal speed after technical validation.",
                "Inspect small text, faces, gradients, fast motion, lip sync, and audio for compression damage.",
                "Run render_qa.py and approval_receipt.py on the exact delivery bytes before publishing.",
            ],
            "limitations": [
                "A successful full decode proves technical readability, not acceptable visual quality.",
                "Two-pass bitrate control targets a hard byte ceiling with headroom; it is not perceptual quality optimization.",
                "MiB means 1,048,576 bytes.",
            ],
        },
    }
    return _set_derived(plan)


def build_commands(plan: Mapping[str, Any], temporary_output: Path, passlog: Path) -> List[List[str]]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    width = int(settings.get("target_width") or 0)
    height = int(settings.get("target_height") or 0)
    source_width = int((plan.get("source") or {}).get("width") or 0)
    source_height = int((plan.get("source") or {}).get("height") or 0)
    filters: List[str] = []
    if (width, height) != (source_width, source_height):
        filters.append(f"scale={width}:{height}")
    shared = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
    ]
    if filters:
        shared.extend(["-vf", ",".join(filters)])
    shared.extend(
        [
            "-r",
            f"{float(settings['target_fps']):.6f}".rstrip("0").rstrip("."),
            "-c:v",
            "libx264",
            "-preset",
            str(settings["preset"]),
            "-b:v",
            f"{round(int(settings['video_bitrate_bps']) / 1000)}k",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    first = [
        *shared,
        "-pass",
        "1",
        "-passlogfile",
        str(passlog),
        "-an",
        "-f",
        "null",
        "-y",
        os.devnull,
    ]
    second = [
        *shared,
        "-pass",
        "2",
        "-passlogfile",
        str(passlog),
    ]
    if (plan.get("source") or {}).get("has_audio"):
        second.extend(
            [
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-b:a",
                f"{round(int(settings['audio_bitrate_bps']) / 1000)}k",
            ]
        )
    else:
        second.append("-an")
    second.extend(
        [
            "-sn",
            "-dn",
            "-metadata:s:v:0",
            "rotate=0",
            "-movflags",
            "+faststart",
            "-y",
            str(temporary_output),
        ]
    )
    return [first, second]


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read delivery encode plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("delivery encode plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("delivery encode plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"delivery encode plan must be an existing JSON file: {resolved}")
    return resolved


def _safe_delivery(path: Path, *, source: Path, plan_file: Path, force: bool) -> Path:
    if path.suffix.lower() != ".mp4":
        raise ValueError("delivery output must use .mp4")
    if path.is_symlink():
        raise ValueError("delivery output must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if resolved in {source.resolve(), plan_file.resolve()}:
        raise ValueError("delivery output must not overwrite the source or plan")
    if resolved.exists() and not force:
        raise ValueError(f"delivery output already exists (pass --force to replace): {resolved}")
    return resolved


def _check_disk_space(destination: Path, target_size_bytes: int) -> None:
    required = max(8 * 1024 * 1024, math.ceil(target_size_bytes * 1.25))
    free = shutil.disk_usage(destination.parent).free
    if free < required:
        raise ValueError(
            f"not enough free space for safe delivery encode: need {required} bytes, have {free}"
        )


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    verification = verify_plan(plan)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))

    source = Path(str(plan["source"]["path"]))
    delivery = _safe_delivery(
        Path(str(plan["delivery"]["path"])),
        source=source,
        plan_file=plan_file,
        force=force,
    )
    settings = plan["settings"]
    _check_disk_space(delivery, int(settings["target_size_bytes"]))
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".tmp.mp4", dir=str(delivery.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with tempfile.TemporaryDirectory(prefix=".delivery-encode-", dir=str(delivery.parent)) as temp_dir:
            passlog = Path(temp_dir) / "ffmpeg2pass"
            for index, command in enumerate(build_commands(plan, temporary, passlog), start=1):
                _run_checked(command, f"delivery encode pass {index}")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("delivery encode did not create a non-empty output")
        temporary_media = {**_fingerprint(temporary), **probe_media(temporary)}
        contract_blockers = _output_contract_blockers(temporary_media, settings, plan["source"])
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(_decode_command(temporary), "full delivery decode validation")
        os.replace(temporary, delivery)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    output = {**_fingerprint(delivery), **probe_media(delivery)}
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "validation": {
            "verified_at": utc_now(),
            "decode_checked": True,
            "decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
        },
    }
    _set_derived(plan)
    _atomic_write_json(plan_file, plan)
    return plan


def _preview_commands(plan: Mapping[str, Any]) -> List[List[str]]:
    delivery = Path(str((plan.get("delivery") or {}).get("path") or "delivery.mp4"))
    return build_commands(plan, delivery.with_name("<temporary-output>.mp4"), Path("<passlog>"))


def format_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else None
    lines = [
        "# Target-size Delivery Encode",
        "",
        f"- Status: **{str(plan.get('status') or 'unknown').upper()}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Delivery: `{delivery.get('path', '')}`",
        f"- Hard ceiling: `{settings.get('target_size_bytes', 0)}` bytes",
        f"- Video bitrate: `{settings.get('video_bitrate_bps', 0)}` bps",
        f"- Audio bitrate: `{settings.get('audio_bitrate_bps', 0)}` bps",
        f"- Contract: `{settings.get('target_width', 0)}x{settings.get('target_height', 0)}` "
        f"at `{settings.get('target_fps', 0)}` fps, H.264/AAC MP4",
        "",
        "## Gate",
        "",
    ]
    blockers = plan.get("blockers") or []
    warnings = plan.get("warnings") or []
    lines.extend([f"- BLOCK: {item}" for item in blockers] or ["- No blocking items."])
    if warnings:
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in warnings]])
    lines.extend(["", "## Two-pass Command Preview", ""])
    for index, command in enumerate(_preview_commands(plan), start=1):
        lines.extend([f"Pass {index}:", "", "```bash", shlex.join(command), "```", ""])
    if application:
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        validation = (
            application.get("validation")
            if isinstance(application.get("validation"), Mapping)
            else {}
        )
        lines.extend(
            [
                "## Applied Output",
                "",
                f"- Size: `{output.get('size_bytes', 0)}` bytes",
                f"- SHA-256: `{output.get('sha256', '')}`",
                f"- Full decode checked: `{validation.get('decode_checked', False)}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Required Human Review",
            "",
            *[f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions", [])],
            "",
            *[f"- Limitation: {item}" for item in (plan.get("review_contract") or {}).get("limitations", [])],
            "",
        ]
    )
    return "\n".join(lines)


def _artifact_destination(path: str, suffix: str, forbidden: Sequence[Path]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"artifact output must not be a symlink: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve()
    if resolved.suffix.lower() != suffix:
        raise ValueError(f"artifact output must use {suffix}")
    if resolved in {item.resolve() for item in forbidden}:
        raise ValueError("artifact output must not overwrite source or delivery media")
    return resolved


def _write_markdown(path: Optional[str], plan: Mapping[str, Any]) -> None:
    if not path:
        return
    forbidden = [
        Path(str((plan.get("source") or {}).get("path") or "")),
        Path(str((plan.get("delivery") or {}).get("path") or "")),
    ]
    output = _artifact_destination(path, ".md", forbidden)
    _atomic_write_text(output, format_markdown(plan))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and verify a source-bound target-size MP4 delivery.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Create a two-pass delivery encode plan.")
    plan.add_argument("source", help="Source video; never overwritten.")
    plan.add_argument("--delivery", required=True, help="Final .mp4 delivery path.")
    plan.add_argument("--max-size-mib", required=True, type=float, help="Hard output ceiling in MiB.")
    plan.add_argument("--audio-bitrate-kbps", type=int, default=128)
    plan.add_argument("--max-width", type=int)
    plan.add_argument("--max-height", type=int)
    plan.add_argument("--fps", type=float, help="Optional CFR target; cannot exceed source fps.")
    plan.add_argument("--preset", choices=sorted(PRESETS), default="medium")
    plan.add_argument("--output", required=True, help="Plan JSON output.")
    plan.add_argument("--markdown")
    plan.add_argument("--force", action="store_true", help="Replace existing plan/Markdown artifacts only.")

    apply_parser = subparsers.add_parser("apply", help="Run, validate, and atomically promote the encode.")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--force", action="store_true", help="Replace an existing delivery after validation.")

    verify = subparsers.add_parser("verify", help="Live-verify source, output, and stored contract.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true", help="Exit 2 on warnings as well as blockers.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            payload = build_plan(
                args.source,
                args.delivery,
                max_size_mib=args.max_size_mib,
                audio_bitrate_kbps=args.audio_bitrate_kbps,
                max_width=args.max_width,
                max_height=args.max_height,
                fps=args.fps,
                preset=args.preset,
            )
            forbidden = [Path(payload["source"]["path"]), Path(payload["delivery"]["path"])]
            output = _artifact_destination(args.output, ".json", forbidden)
            markdown = _artifact_destination(args.markdown, ".md", forbidden) if args.markdown else None
            for candidate in (output, markdown):
                if candidate is not None and candidate.exists() and not args.force:
                    raise ValueError(f"artifact already exists (pass --force to replace): {candidate}")
            _atomic_write_json(output, payload)
            if markdown is not None:
                _atomic_write_text(markdown, format_markdown(payload))
        elif args.command == "apply":
            payload = apply_plan(args.plan, force=args.force)
            _write_markdown(args.markdown, payload)
        else:
            plan_file = _resolve_plan_file(args.plan)
            payload = verify_plan(_load_plan(plan_file))
            _write_markdown(args.markdown, payload)
            print(
                f"Delivery encode: {payload['status']} "
                f"({payload['summary']['blocking']} blocking, {payload['summary']['warnings']} warnings)"
            )
            if payload["summary"]["blocking"] or (args.strict and payload["summary"]["warnings"]):
                return 2
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Delivery encode: {payload['status']} "
        f"({payload['summary']['blocking']} blocking, {payload['summary']['warnings']} warnings)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
