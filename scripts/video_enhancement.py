#!/usr/bin/env python3
"""Plan, apply, confirm, and verify a source-bound local video finishing pass.

This workflow handles deterministic delivery resizing and optional motion-compensated
frame interpolation with FFmpeg.  It does not claim to reconstruct missing detail or
replace an ML video-restoration model.  The original source is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


VERSION = "video_enhancement.v1"
PENDING_APPLY = "video enhancement has not been applied and validated"
PENDING_REVIEW = "enhanced output still needs full-length A/B review"
REJECTED_REVIEW = "enhanced output was rejected during A/B review"
REVIEW_FIELDS = ("detail", "edges", "motion_cadence", "audio_sync")
REVIEW_VALUES = {"pass", "fail"}


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


def _available_filters() -> Set[str]:
    result = _run_command(["ffmpeg", "-hide_banner", "-filters"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not list FFmpeg filters")
    names: Set[str] = set()
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in {"T", ".", "S"}:
            names.add(parts[1])
    return names


def _even(value: float) -> int:
    parsed = max(2, int(round(value)))
    return parsed if parsed % 2 == 0 else parsed - 1


def _request_record(
    *, target_short_edge: Optional[int], scale: Optional[float], target_fps: Optional[float]
) -> Dict[str, Any]:
    return {
        "target_short_edge": int(target_short_edge) if target_short_edge is not None else None,
        "scale": round(float(scale), 6) if scale is not None else None,
        "target_fps": round(float(target_fps), 6) if target_fps is not None else None,
    }


def _settings_for(source: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    width = int(source.get("width") or 0)
    height = int(source.get("height") or 0)
    source_fps = float(source.get("fps") or 0)
    if width <= 0 or height <= 0 or source_fps <= 0:
        raise ValueError("source media contract is incomplete")
    short_edge = request.get("target_short_edge")
    scale = request.get("scale")
    if short_edge is not None and scale is not None:
        raise ValueError("choose target_short_edge or scale, not both")
    if short_edge is not None:
        short_edge = int(short_edge)
        if short_edge <= min(width, height):
            raise ValueError("target_short_edge must be larger than the source short edge")
        if short_edge > 4320:
            raise ValueError("target_short_edge must not exceed 4320")
        scale_value = short_edge / min(width, height)
    elif scale is not None:
        scale_value = float(scale)
        if not math.isfinite(scale_value) or not 1.0 < scale_value <= 4.0:
            raise ValueError("scale must be greater than 1 and no more than 4")
    else:
        scale_value = 1.0
    target_width = _even(width * scale_value)
    target_height = _even(height * scale_value)

    requested_fps = request.get("target_fps")
    target_fps = source_fps if requested_fps is None else float(requested_fps)
    if not math.isfinite(target_fps) or target_fps < source_fps - 0.01:
        raise ValueError("target_fps must be at least the source fps")
    if target_fps > 120:
        raise ValueError("target_fps must not exceed 120")
    interpolate = target_fps > source_fps + max(0.01, source_fps * 0.001)
    resize = target_width != width or target_height != height
    if not resize and not interpolate:
        raise ValueError("request is a no-op; choose a larger short edge, scale, or fps")
    return {
        "target_width": target_width,
        "target_height": target_height,
        "target_fps": round(target_fps, 6),
        "scale_factor": round(scale_value, 6),
        "resize": resize,
        "interpolate": interpolate,
        "scaler": "lanczos",
        "interpolator": "ffmpeg_minterpolate_mci_aobmc_bidir" if interpolate else None,
        "video_encoder": "libx264",
        "preset": "slow",
        "crf": 16,
        "pixel_format": "yuv420p",
        "audio_encoder": "aac" if source.get("has_audio") else None,
        "audio_bitrate_kbps": 192 if source.get("has_audio") else None,
    }


def required_filters(settings: Mapping[str, Any]) -> Set[str]:
    filters = {"scale", "setsar", "hstack", "fps"}
    if settings.get("interpolate"):
        filters.add("minterpolate")
    return filters


def build_filter(settings: Mapping[str, Any]) -> str:
    filters: List[str] = []
    if settings.get("interpolate"):
        filters.append(
            "minterpolate="
            f"fps={float(settings['target_fps']):.6f}:mi_mode=mci:mc_mode=aobmc:"
            "me_mode=bidir:vsbmc=1"
        )
    filters.extend(
        [
            f"scale={int(settings['target_width'])}:{int(settings['target_height'])}:flags=lanczos",
            "setsar=1",
        ]
    )
    return ",".join(filters)


def _encode_command(source: Path, output: Path, settings: Mapping[str, Any]) -> List[str]:
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", build_filter(settings),
        "-c:v", "libx264", "-preset", str(settings["preset"]), "-crf", str(settings["crf"]),
        "-pix_fmt", str(settings["pixel_format"]),
    ]
    if settings.get("audio_encoder"):
        command.extend(["-c:a", "aac", "-b:a", f"{settings['audio_bitrate_kbps']}k"])
    command.extend(["-movflags", "+faststart", str(output)])
    return command


def _comparison_command(
    source: Path, enhanced: Path, output: Path, settings: Mapping[str, Any]
) -> List[str]:
    fps = float(settings["target_fps"])
    graph = (
        f"[0:v]fps={fps:.6f},scale=-2:720:force_original_aspect_ratio=decrease,setsar=1[left];"
        f"[1:v]fps={fps:.6f},scale=-2:720:force_original_aspect_ratio=decrease,setsar=1[right];"
        "[left][right]hstack=inputs=2[out]"
    )
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source), "-i", str(enhanced),
        "-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-movflags",
        "+faststart", str(output),
    ]


def _decode_command(path: Path) -> List[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
        "-map", "0", "-f", "null", "-",
    ]


def _computed_warnings(settings: Mapping[str, Any]) -> List[str]:
    warnings = [
        "Local Lanczos resizing preserves composition but does not reconstruct missing source detail like an ML VSR model."
    ]
    if settings.get("interpolate"):
        warnings.append(
            "Motion-compensated interpolation can create ghosting or warped edges during fast motion, occlusion, and cuts."
        )
    if float(settings.get("scale_factor") or 1) > 2.0:
        warnings.append("Scale factor exceeds 2x; inspect faces, text, line art, and compression artifacts closely.")
    return warnings


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "request": plan.get("request"),
        "settings": plan.get("settings"),
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


def _planned_path(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.suffix.lower() != ".mp4":
        blockers.append(f"{label}.path must use .mp4")
    if candidate.is_symlink():
        blockers.append(f"{label}.path must not be a symlink")
    return candidate


def _output_contract_blockers(
    media: Mapping[str, Any], settings: Mapping[str, Any], source: Mapping[str, Any]
) -> List[str]:
    blockers: List[str] = []
    for field in ("width", "height"):
        expected = int(settings.get(f"target_{field}") or 0)
        if int(media.get(field) or 0) != expected:
            blockers.append(f"enhanced output {field} must be {expected}")
    expected_fps = float(settings.get("target_fps") or 0)
    actual_fps = float(media.get("fps") or 0)
    if abs(actual_fps - expected_fps) > max(0.02, expected_fps * 0.001):
        blockers.append("enhanced output fps does not match the planned target")
    if media.get("has_audio") != source.get("has_audio"):
        blockers.append("enhanced output audio presence must match the source")
    source_duration = float(source.get("duration") or 0)
    output_duration = float(media.get("duration") or 0)
    tolerance = max(0.1, 3.0 / max(expected_fps, 1.0))
    if abs(output_duration - source_duration) > tolerance:
        blockers.append(f"enhanced output duration must match source within {tolerance:.3f}s")
    return blockers


def _compute_derived(plan: Mapping[str, Any], filters: Set[str]) -> Dict[str, Any]:
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
    for name in sorted(required_filters(settings)):
        if name not in filters:
            blockers.append(f"required FFmpeg filter is unavailable: {name}")

    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    enhanced_path = _planned_path(
        delivery.get("enhanced") if isinstance(delivery.get("enhanced"), Mapping) else {},
        "delivery.enhanced",
        blockers,
    )
    comparison_path = _planned_path(
        delivery.get("comparison") if isinstance(delivery.get("comparison"), Mapping) else {},
        "delivery.comparison",
        blockers,
    )
    if enhanced_path is not None and delivery.get("enhanced") != {
        "path": str(enhanced_path),
        "format": "mp4",
    }:
        blockers.append("delivery.enhanced record is not canonical")
    if comparison_path is not None and delivery.get("comparison") != {
        "path": str(comparison_path),
        "format": "mp4",
    }:
        blockers.append("delivery.comparison record is not canonical")
    if enhanced_path is not None and comparison_path is not None and enhanced_path == comparison_path:
        blockers.append("enhanced output and comparison paths must differ")
    if source_path is not None:
        for label, candidate in (("enhanced", enhanced_path), ("comparison", comparison_path)):
            if candidate is not None and candidate.resolve() == source_path.resolve():
                blockers.append(f"{label} output must not overwrite the source")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    review_status = "missing"
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        comparison = (
            application.get("comparison")
            if isinstance(application.get("comparison"), Mapping)
            else {}
        )
        live_output_path = _validate_live_file(output, "application.output", blockers)
        _validate_live_file(comparison, "application.comparison", blockers)
        if enhanced_path is not None and output.get("path") != str(enhanced_path):
            blockers.append("application.output.path does not match planned enhanced output")
        if comparison_path is not None and comparison.get("path") != str(comparison_path):
            blockers.append("application.comparison.path does not match planned comparison")
        if live_output_path is not None:
            try:
                live_media = {**_fingerprint(live_output_path), **probe_media(live_output_path)}
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"enhanced output probe failed: {exc}")
            else:
                if output != live_media:
                    blockers.append("stored enhanced output contract is stale or was modified")
                blockers.extend(_output_contract_blockers(live_media, settings, source))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        if validation.get("output_decoded") is not True or validation.get("comparison_decoded") is not True:
            blockers.append("full FFmpeg decode validation is missing")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("decode validation is not bound to the current output sha256")
        if validation.get("comparison_sha256") != comparison.get("sha256"):
            blockers.append("decode validation is not bound to the current comparison sha256")
        if application.get("filter") != build_filter(settings):
            blockers.append("application.filter does not match the canonical settings")
        review = application.get("review") if isinstance(application.get("review"), Mapping) else {}
        review_status = str(review.get("status") or "pending")
        if review_status == "pending":
            blockers.append(PENDING_REVIEW)
        elif review_status == "rejected":
            blockers.append(REJECTED_REVIEW)
        elif review_status == "approved":
            checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
            if set(checks) != set(REVIEW_FIELDS) or any(checks.get(key) != "pass" for key in REVIEW_FIELDS):
                blockers.append("approved review must pass all canonical A/B checks")
            if not str(review.get("reviewed_by_label") or "").strip() or not str(
                review.get("note") or ""
            ).strip():
                blockers.append("approved review requires reviewer label and note")
        else:
            blockers.append("application.review.status is invalid")

    warnings = _computed_warnings(settings)
    summary = {
        "source_size": f"{source.get('width', 0)}x{source.get('height', 0)}",
        "target_size": f"{settings.get('target_width', 0)}x{settings.get('target_height', 0)}",
        "source_fps": source.get("fps"),
        "target_fps": settings.get("target_fps"),
        "interpolate": bool(settings.get("interpolate")),
        "applied": applied,
        "review": review_status,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else "warn" if warnings else "ready"
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def _set_derived(plan: Dict[str, Any], filters: Set[str]) -> Dict[str, Any]:
    plan.update(_compute_derived(plan, filters))
    plan["plan_id"] = _plan_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any], filters: Optional[Set[str]] = None) -> Dict[str, Any]:
    available = filters if filters is not None else _available_filters()
    result = dict(plan)
    integrity_blockers: List[str] = []
    if plan.get("plan_id") != _plan_id(plan):
        integrity_blockers.append("plan_id does not match canonical plan content")
    derived = _compute_derived(plan, available)
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
    enhanced_path: str,
    comparison_path: str,
    *,
    target_short_edge: Optional[int] = None,
    scale: Optional[float] = None,
    target_fps: Optional[float] = None,
    filters: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    candidate = Path(source_path).expanduser()
    if candidate.is_symlink():
        raise ValueError("source must not be a symlink")
    source = candidate.resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    request = _request_record(
        target_short_edge=target_short_edge, scale=scale, target_fps=target_fps
    )
    source_info = _source_info(source)
    settings = _settings_for(source_info, request)
    available = filters if filters is not None else _available_filters()
    enhanced = Path(enhanced_path).expanduser().resolve()
    comparison = Path(comparison_path).expanduser().resolve()
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": source_info,
        "request": request,
        "settings": settings,
        "delivery": {
            "enhanced": {"path": str(enhanced), "format": "mp4"},
            "comparison": {"path": str(comparison), "format": "mp4"},
        },
        "application": None,
        "review_contract": {
            "required": True,
            "fields": list(REVIEW_FIELDS),
            "instructions": [
                "Watch the complete side-by-side comparison at normal speed; source is left and enhanced output is right.",
                "Confirm real detail is retained without halos, ringing, waxy texture, or damaged text and line art.",
                "Inspect cuts, hands, faces, occlusions, and fast motion for interpolation ghosts or warped edges.",
                "Watch and listen to the complete enhanced output separately to confirm audio sync and continuity.",
            ],
            "limitations": [
                "Lanczos resizing cannot recreate missing detail or remove source compression artifacts.",
                "A reviewer label is a workflow annotation, not identity authentication or a digital signature.",
            ],
        },
    }
    return _set_derived(plan, available)


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("enhancement plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"enhancement plan must be an existing JSON file: {resolved}")
    return resolved


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read enhancement plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("enhancement plan must be a JSON object")
    return data


def _safe_output(path: Path, *, force: bool, forbidden: Set[Path]) -> Path:
    if path.suffix.lower() != ".mp4":
        raise ValueError("enhanced output and comparison must use .mp4")
    if path.is_symlink():
        raise ValueError(f"output must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if resolved in forbidden:
        raise ValueError(f"output must not overwrite a source, plan, or sibling output: {resolved}")
    if resolved.exists() and not force:
        raise ValueError(f"output already exists (pass --force to replace): {resolved}")
    return resolved


def _temp_mp4(destination: Path) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp.mp4", dir=str(destination.parent)
    )
    os.close(fd)
    return Path(name)


def apply_plan(
    plan_path: str, *, force: bool = False, filters: Optional[Set[str]] = None
) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    available = filters if filters is not None else _available_filters()
    verification = verify_plan(plan, available)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))
    source = Path(str(plan["source"]["path"]))
    enhanced = _safe_output(
        Path(str(plan["delivery"]["enhanced"]["path"])),
        force=force,
        forbidden={source.resolve(), plan_file},
    )
    comparison = _safe_output(
        Path(str(plan["delivery"]["comparison"]["path"])),
        force=force,
        forbidden={source.resolve(), plan_file, enhanced},
    )
    temp_enhanced = _temp_mp4(enhanced)
    temp_comparison = _temp_mp4(comparison)
    settings = plan["settings"]
    try:
        _run_checked(_encode_command(source, temp_enhanced, settings), "video enhancement render")
        output_media = {**_fingerprint(temp_enhanced), **probe_media(temp_enhanced)}
        contract_blockers = _output_contract_blockers(output_media, settings, plan["source"])
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(_decode_command(temp_enhanced), "full enhanced output decode")
        _run_checked(
            _comparison_command(source, temp_enhanced, temp_comparison, settings),
            "full-length A/B comparison render",
        )
        _run_checked(_decode_command(temp_comparison), "full comparison decode")
        comparison_record = _fingerprint(temp_comparison)
        os.replace(temp_enhanced, enhanced)
        os.replace(temp_comparison, comparison)
    finally:
        for temporary in (temp_enhanced, temp_comparison):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output = {**_fingerprint(enhanced), **probe_media(enhanced)}
    comparison_record = {**comparison_record, **_fingerprint(comparison), "path": str(comparison)}
    plan["application"] = {
        "applied_at": utc_now(),
        "filter": build_filter(settings),
        "output": output,
        "comparison": comparison_record,
        "validation": {
            "validated_at": utc_now(),
            "output_decoded": True,
            "comparison_decoded": True,
            "output_sha256": output["sha256"],
            "comparison_sha256": comparison_record["sha256"],
        },
        "review": {
            "status": "pending",
            "checks": {field: "" for field in REVIEW_FIELDS},
            "reviewed_by_label": "",
            "note": "",
            "reviewed_at": None,
        },
    }
    _set_derived(plan, available)
    _atomic_write_json(plan_file, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    checks: Mapping[str, str],
    reviewed_by_label: str,
    note: str,
    filters: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    reviewer = reviewed_by_label.strip()
    review_note = note.strip()
    if not reviewer or not review_note:
        raise ValueError("confirm requires non-empty reviewed_by_label and note")
    if set(checks) != set(REVIEW_FIELDS) or any(value not in REVIEW_VALUES for value in checks.values()):
        raise ValueError("confirm requires pass/fail for every canonical review field")
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    available = filters if filters is not None else _available_filters()
    verification = verify_plan(plan, available)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_REVIEW]:
        raise ValueError("plan is not awaiting A/B review: " + "; ".join(blockers or ["already reviewed"]))
    application = plan.get("application")
    assert isinstance(application, dict)
    status = "approved" if all(checks[field] == "pass" for field in REVIEW_FIELDS) else "rejected"
    application["review"] = {
        "status": status,
        "checks": {field: checks[field] for field in REVIEW_FIELDS},
        "reviewed_by_label": reviewer,
        "note": review_note,
        "reviewed_at": utc_now(),
    }
    _set_derived(plan, available)
    _atomic_write_json(plan_file, plan)
    return plan


def format_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    review = application.get("review") if isinstance(application.get("review"), Mapping) else {}
    lines = [
        "# Video Enhancement Plan",
        "",
        f"- Status: **{str(plan.get('status') or 'unknown').upper()}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Geometry: `{source.get('width', 0)}x{source.get('height', 0)}` → `{settings.get('target_width', 0)}x{settings.get('target_height', 0)}`",
        f"- Cadence: `{source.get('fps', 0)}` → `{settings.get('target_fps', 0)}` fps",
        f"- Interpolation: `{bool(settings.get('interpolate'))}`",
        f"- Review: `{review.get('status', 'not_applied')}`",
        "",
        "## Gate",
        "",
    ]
    blockers = plan.get("blockers") or []
    lines.extend([f"- BLOCK: {item}" for item in blockers] or ["- No blocking items."])
    lines.append("")
    warnings = plan.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", "", *[f"- {item}" for item in warnings], ""])
    if application:
        lines.extend(
            [
                "## Outputs",
                "",
                f"- Enhanced output: `{(application.get('output') or {}).get('path', '')}`",
                f"- Full-length A/B: `{(application.get('comparison') or {}).get('path', '')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Required Review",
            "",
            *[f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions", [])],
            "",
            "Lanczos scaling is a deterministic resize, not an ML detail-restoration claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_artifacts(
    output_path: str,
    plan: Mapping[str, Any],
    *,
    markdown_path: Optional[str],
    force: bool,
) -> None:
    output = Path(output_path).expanduser()
    if output.is_symlink() or output.suffix.lower() != ".json":
        raise ValueError("plan output must be a non-symlink .json file")
    output.parent.mkdir(parents=True, exist_ok=True)
    resolved = output.resolve()
    if resolved.exists() and not force:
        raise ValueError(f"plan output already exists (pass --force to replace): {resolved}")
    source = Path(str((plan.get("source") or {}).get("path") or "")).resolve()
    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    forbidden = {
        source,
        Path(str((delivery.get("enhanced") or {}).get("path") or "")).resolve(),
        Path(str((delivery.get("comparison") or {}).get("path") or "")).resolve(),
    }
    if resolved in forbidden:
        raise ValueError("plan output must not overwrite source or rendered media")
    markdown: Optional[Path] = None
    if markdown_path:
        markdown = Path(markdown_path).expanduser()
        if markdown.is_symlink() or markdown.suffix.lower() != ".md":
            raise ValueError("Markdown output must be a non-symlink .md file")
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown = markdown.resolve()
        if markdown in forbidden or markdown == resolved:
            raise ValueError("Markdown output must not overwrite source, media, or JSON plan")
        if markdown.exists() and not force:
            raise ValueError(f"Markdown output already exists (pass --force to replace): {markdown}")
    _atomic_write_json(resolved, plan)
    if markdown is not None:
        _atomic_write_text(markdown, format_markdown(plan))


def _refresh_markdown(path: Optional[str], plan: Mapping[str, Any]) -> None:
    if not path:
        return
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or candidate.suffix.lower() != ".md":
        raise ValueError("Markdown output must be a non-symlink .md file")
    _atomic_write_text(candidate.resolve(), format_markdown(plan))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a source-bound local video resize and optional frame interpolation pass."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Create a source-bound enhancement plan.")
    plan.add_argument("source")
    target = plan.add_mutually_exclusive_group()
    target.add_argument("--target-short-edge", type=int)
    target.add_argument("--scale", type=float)
    plan.add_argument("--fps", type=float, dest="target_fps")
    plan.add_argument("--enhanced", required=True, help="Planned enhanced MP4 output")
    plan.add_argument("--comparison", required=True, help="Planned full-length A/B MP4")
    plan.add_argument("--output", required=True, help="Plan JSON")
    plan.add_argument("--markdown")
    plan.add_argument("--strict", action="store_true")
    plan.add_argument("--force", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Render and fully decode both outputs.")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser("confirm", help="Record the complete A/B and audio review.")
    confirm.add_argument("plan")
    for field in REVIEW_FIELDS:
        confirm.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_VALUES), required=True)
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--markdown")

    verify = subparsers.add_parser("verify", help="Live-verify source, outputs, and review state.")
    verify.add_argument("plan")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "plan":
            plan = build_plan(
                args.source,
                args.enhanced,
                args.comparison,
                target_short_edge=args.target_short_edge,
                scale=args.scale,
                target_fps=args.target_fps,
            )
            _write_artifacts(
                args.output, plan, markdown_path=args.markdown, force=bool(args.force)
            )
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 2 if args.strict and plan.get("blockers") else 0
        if args.command == "apply":
            plan = apply_plan(args.plan, force=bool(args.force))
            _refresh_markdown(args.markdown, plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        if args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            plan = confirm_plan(
                args.plan,
                checks=checks,
                reviewed_by_label=args.reviewed_by,
                note=args.note,
            )
            _refresh_markdown(args.markdown, plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 2 if plan.get("blockers") else 0
        plan_file = _resolve_plan_file(args.plan)
        plan = verify_plan(_load_plan(plan_file))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 2 if args.strict and plan.get("blockers") else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
