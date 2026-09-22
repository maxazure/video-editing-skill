#!/usr/bin/env python3
"""Build, review, and verify a source-bound forward/reverse loop.

The selected CFR source range is emitted as ``0..N-1,N-2..1``.  Omitting the
two repeated endpoint frames avoids the one-frame holds produced by a naive
forward-plus-reverse concat.  Source audio is intentionally dropped: a
boomerang gesture is normally cut under a separate music/SFX timeline, while
reversing speech or ambience needs an explicit audio-design decision.
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
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from frame_rate_conform import CADENCE_ALGORITHM, analyze_cadence, parse_rate, probe_media


VERSION = "ping_pong_loop_plan.v1"
ALGORITHM = {
    "id": "endpoint-deduplicated-forward-reverse-v1",
    "cycle": "frames 0..N-1 followed by N-2..1",
    "turnaround_policy": "exclude the duplicated last frame",
    "loop_seam_policy": "exclude the duplicated first frame at the end of each cycle",
    "audio_policy": "drop source audio; add reviewed music/SFX downstream",
}
PENDING_APPLY = "ping-pong loop has not been rendered and validated"
PENDING_CONFIRM = "full delivery, turnaround proof, and loop-seam proof have not been confirmed"
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
REVIEW_FIELDS = (
    "turnaround_motion",
    "loop_seam_motion",
    "duplicate_hold",
    "framing_integrity",
    "creative_intent",
)
REVIEW_CHOICES = {"pass", "fail", "unobservable"}
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_CYCLES = 10000


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def _media_info(path: Path) -> Dict[str, Any]:
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


def parse_duration(value: str) -> float:
    text = str(value).strip()
    if not text:
        raise ValueError("duration is required")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid duration: {value}")
    try:
        numbers = [float(item) for item in parts]
    except ValueError as exc:
        raise ValueError(f"invalid duration: {value}") from exc
    if any(not math.isfinite(item) or item < 0 for item in numbers):
        raise ValueError(f"invalid duration: {value}")
    if len(numbers) == 3 and (numbers[1] >= 60 or numbers[2] >= 60):
        raise ValueError(f"invalid hh:mm:ss duration: {value}")
    if len(numbers) == 2 and numbers[1] >= 60:
        raise ValueError(f"invalid mm:ss duration: {value}")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds <= 0 or seconds > MAX_DURATION_SECONDS:
        raise ValueError("duration must be greater than zero and at most 24 hours")
    return seconds


def _source_rate(source: Mapping[str, Any]) -> Dict[str, Any]:
    cadence = source.get("cadence") if isinstance(source.get("cadence"), Mapping) else {}
    if cadence.get("is_variable") or cadence.get("non_monotonic_intervals"):
        raise ValueError(
            "source decoded cadence is variable or non-monotonic; create a CFR working copy with "
            "frame_rate_conform.py before building a ping-pong loop"
        )
    median = float((cadence.get("interval_seconds") or {}).get("median") or 0)
    if median <= 0:
        raise ValueError("source decoded cadence has no usable median frame interval")
    measured = 1.0 / median
    for candidate in (source.get("r_frame_rate"), source.get("avg_frame_rate")):
        try:
            rate = parse_rate(str(candidate or ""))
        except ValueError:
            continue
        if abs(float(rate["fps"]) - measured) <= 0.01:
            return rate
    return parse_rate(str(round(measured, 6)))


def _validate_source_contract(source: Mapping[str, Any]) -> None:
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (
        isinstance(bit_depth, int) and bit_depth > 8
    ):
        raise ValueError(
            "HDR/BT.2020/greater-than-8-bit source requires an explicit color workflow; "
            "use hdr_sdr.py for an SDR derivative first"
        )
    _source_rate(source)
    frame_count = int(((source.get("cadence") or {}).get("frame_count") or 0))
    if frame_count < 3:
        raise ValueError("source must contain at least three decoded video frames")


def _finite_seconds(value: Optional[float], label: str) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return parsed


def _settings_for(
    source: Mapping[str, Any],
    *,
    start_seconds: Optional[float],
    end_seconds: Optional[float],
    request_kind: str,
    requested_value: Any,
    proof_context_seconds: float,
    max_working_set_mib: float,
) -> Dict[str, Any]:
    _validate_source_contract(source)
    if request_kind not in {"cycles", "duration"}:
        raise ValueError("request_kind must be cycles or duration")
    rate = _source_rate(source)
    fps = float(rate["fps"])
    source_frames = int(((source.get("cadence") or {}).get("frame_count") or 0))
    requested_start = _finite_seconds(start_seconds, "start")
    requested_end = _finite_seconds(end_seconds, "end")
    start_frame = int(round((requested_start or 0.0) * fps))
    end_frame = source_frames if requested_end is None else int(round(requested_end * fps))
    if start_frame < 0 or start_frame >= source_frames:
        raise ValueError("start resolves outside the decoded source frame range")
    if end_frame <= start_frame or end_frame > source_frames:
        raise ValueError("end must resolve after start and within the decoded source frame range")
    selected_frames = end_frame - start_frame
    if selected_frames < 3:
        raise ValueError("selected range must contain at least three frames")

    cycle_frames = selected_frames * 2 - 2
    if request_kind == "cycles":
        try:
            numeric = float(requested_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("cycles must be an integer") from exc
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError("cycles must be an integer")
        cycles = int(numeric)
        if cycles < 1 or cycles > MAX_CYCLES:
            raise ValueError(f"cycles must be between 1 and {MAX_CYCLES}")
        target_frames = cycle_frames * cycles
        normalized_requested: Any = cycles
    else:
        duration = parse_duration(str(requested_value))
        target_frames = int(round(duration * fps))
        if target_frames < cycle_frames:
            raise ValueError(
                f"target duration must cover at least one complete ping-pong cycle ({cycle_frames / fps:.6f}s)"
            )
        normalized_requested = duration
    if target_frames / fps > MAX_DURATION_SECONDS:
        raise ValueError("target duration must be at most 24 hours")

    try:
        context = float(proof_context_seconds)
        memory_limit = float(max_working_set_mib)
    except (TypeError, ValueError) as exc:
        raise ValueError("proof context and memory limit must be numeric") from exc
    if not math.isfinite(context) or context <= 0 or context > 5:
        raise ValueError("proof context must be greater than zero and at most 5 seconds")
    if not math.isfinite(memory_limit) or memory_limit < 64 or memory_limit > 32768:
        raise ValueError("max working set must be between 64 and 32768 MiB")
    context_frames = max(1, min(int(round(context * fps)), selected_frames - 1))
    estimated_mib = (
        int(source.get("width") or 0)
        * int(source.get("height") or 0)
        * selected_frames
        * 4.0
        / (1024 * 1024)
    )
    if estimated_mib > memory_limit:
        raise ValueError(
            f"selected range may require about {estimated_mib:.1f} MiB for reverse buffering, above "
            f"the {memory_limit:.1f} MiB limit; shorten --start/--end or explicitly raise the limit"
        )
    reads = int(math.ceil(target_frames / cycle_frames))
    return {
        "algorithm": dict(ALGORITHM),
        "requested_start_seconds": requested_start,
        "requested_end_seconds": requested_end,
        "start_frame": start_frame,
        "end_frame_exclusive": end_frame,
        "snapped_start_seconds": round(start_frame / fps, 9),
        "snapped_end_seconds": round(end_frame / fps, 9),
        "selected_frame_count": selected_frames,
        "selected_duration_seconds": round(selected_frames / fps, 9),
        "forward_frame_count": selected_frames,
        "reverse_frame_count": selected_frames - 2,
        "cycle_frame_count": cycle_frames,
        "cycle_duration_seconds": round(cycle_frames / fps, 9),
        "turnaround_frame": selected_frames,
        "loop_seam_frame": cycle_frames,
        "request_kind": request_kind,
        "requested_value": normalized_requested,
        "target_frame_count": target_frames,
        "target_duration_seconds": round(target_frames / fps, 9),
        "cycle_reads": reads,
        "loop_filter_repeats": max(0, reads - 1),
        "complete_cycles": target_frames // cycle_frames,
        "partial_final_cycle": bool(target_frames % cycle_frames),
        "proof_context_seconds_requested": round(context, 6),
        "proof_context_frames": context_frames,
        "proof_frame_count": context_frames * 2,
        "max_working_set_mib": round(memory_limit, 3),
        "estimated_reverse_working_set_mib": round(estimated_mib, 3),
        "fps": round(fps, 9),
        "fps_rational": str(rate["rational"]),
        "video_encoder": "libx264",
        "video_preset": "medium",
        "video_crf": 18,
        "pixel_format": "yuv420p",
        "output_has_audio": False,
    }


def _review_contract() -> Dict[str, Any]:
    return {
        "playback_speed": "1x",
        "audio_expected": False,
        "required_playbacks": ["full_delivery", "turnaround_proof", "loop_seam_proof"],
        "required_checks": list(REVIEW_FIELDS),
        "allowed_choices": sorted(REVIEW_CHOICES),
        "pass_policy": "all playbacks completed and every check pass",
    }


def _plan_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_root": plan.get("project_root"),
        "source": plan.get("source"),
        "settings": plan.get("settings"),
        "delivery": plan.get("delivery"),
        "turnaround_proof": plan.get("turnaround_proof"),
        "loop_seam_proof": plan.get("loop_seam_proof"),
        "review_contract": plan.get("review_contract"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    raw = json.dumps(_plan_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decode_command(path: Path) -> List[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"]


def _rate_float(value: Any) -> Optional[float]:
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None


def _media_contract_blockers(
    media: Mapping[str, Any],
    source: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    expected_frames: int,
    label: str,
) -> List[str]:
    blockers: List[str] = []
    if int(media.get("width") or 0) != int(source.get("width") or 0) or int(media.get("height") or 0) != int(source.get("height") or 0):
        blockers.append(f"{label} display dimensions do not match the source")
    if media.get("has_audio"):
        blockers.append(f"{label} unexpectedly contains audio")
    if str(media.get("video_codec") or "") != "h264":
        blockers.append(f"{label} video codec must be h264")
    if str(media.get("pixel_format") or "") != "yuv420p":
        blockers.append(f"{label} pixel format must be yuv420p")
    if str(media.get("sample_aspect_ratio") or "") not in {"1:1", "unknown"}:
        blockers.append(f"{label} sample aspect ratio must be 1:1")
    actual_fps = _rate_float(media.get("avg_frame_rate")) or float(media.get("avg_fps") or 0)
    expected_fps = float(settings.get("fps") or 0)
    if actual_fps <= 0 or abs(actual_fps - expected_fps) > 0.001:
        blockers.append(f"{label} frame rate does not match the source-bound target")
    cadence = media.get("cadence") if isinstance(media.get("cadence"), Mapping) else {}
    if cadence.get("algorithm") != CADENCE_ALGORITHM:
        blockers.append(f"{label} cadence algorithm is stale or missing")
    if cadence.get("is_variable") or cadence.get("non_monotonic_intervals"):
        blockers.append(f"{label} decoded cadence is variable or non-monotonic")
    if int(cadence.get("frame_count") or 0) != expected_frames:
        blockers.append(f"{label} decoded frame count does not match {expected_frames}")
    expected_duration = expected_frames / expected_fps if expected_fps > 0 else 0
    if abs(float(media.get("duration") or 0) - expected_duration) > max(0.05, 2.0 / max(expected_fps, 1)):
        blockers.append(f"{label} duration does not match its frame contract")
    return blockers


def _validate_live_file(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.is_symlink() or not candidate.is_file():
        blockers.append(f"{label} is missing or a symlink")
        return None
    try:
        fingerprint = _fingerprint(candidate)
    except OSError as exc:
        blockers.append(f"{label} could not be fingerprinted: {exc}")
        return None
    if record.get("sha256") != fingerprint["sha256"] or record.get("size_bytes") != fingerprint["size_bytes"]:
        blockers.append(f"{label} bytes changed after application")
    return candidate


def _review_blockers(plan: Mapping[str, Any]) -> List[str]:
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else None
    if not review:
        return [PENDING_CONFIRM]
    blockers: List[str] = []
    if review.get("full_playback") != "completed":
        blockers.append("full delivery playback is incomplete")
    if review.get("turnaround_playback") != "completed":
        blockers.append("turnaround proof playback is incomplete")
    if review.get("seam_playback") != "completed":
        blockers.append("loop-seam proof playback is incomplete")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    for field in REVIEW_FIELDS:
        if checks.get(field) != "pass":
            blockers.append(f"review check {field} must be pass")
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    for review_key, app_key in (
        ("output_sha256", "output"),
        ("turnaround_proof_sha256", "turnaround_proof"),
        ("loop_seam_proof_sha256", "loop_seam_proof"),
    ):
        app_record = application.get(app_key) if isinstance(application.get(app_key), Mapping) else {}
        if review.get(review_key) != app_record.get("sha256"):
            blockers.append(f"review {review_key} is stale")
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    warnings: List[str] = []
    if source.get("has_audio"):
        warnings.append("source audio is intentionally dropped; add reviewed music/SFX downstream")
    if settings.get("partial_final_cycle"):
        warnings.append("delivery ends inside a ping-pong cycle and is not itself a complete external loop")
    estimate = float(settings.get("estimated_reverse_working_set_mib") or 0)
    limit = float(settings.get("max_working_set_mib") or 0)
    if limit > 0 and estimate >= limit * 0.75:
        warnings.append("selected range is close to the declared reverse-buffer memory limit")
    return warnings


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    project_root = Path(str(plan.get("project_root") or "")).expanduser()
    if not project_root.is_absolute() or not project_root.is_dir():
        blockers.append("project_root must be an existing absolute directory")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = Path(str(source.get("path") or "")).expanduser()
    if not source_path.is_absolute() or not source_path.is_file() or source_path.is_symlink():
        blockers.append("source is missing, relative, or a symlink")
    elif project_root.is_absolute() and not _inside_project(source_path, project_root):
        blockers.append("source escaped the project directory")
    else:
        try:
            live_source = _media_info(source_path)
        except (OSError, RuntimeError, ValueError) as exc:
            blockers.append(f"source probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source bytes, media contract, or cadence changed after planning")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    if source:
        try:
            expected_settings = _settings_for(
                source,
                start_seconds=settings.get("requested_start_seconds"),
                end_seconds=settings.get("requested_end_seconds"),
                request_kind=str(settings.get("request_kind") or ""),
                requested_value=settings.get("requested_value"),
                proof_context_seconds=float(settings.get("proof_context_seconds_requested") or 0),
                max_working_set_mib=float(settings.get("max_working_set_mib") or 0),
            )
        except (TypeError, ValueError) as exc:
            blockers.append(f"settings cannot be recomputed: {exc}")
        else:
            if settings != expected_settings:
                blockers.append("settings do not match the canonical ping-pong contract")

    path_specs = {
        "delivery": ("ping_pong_loop_delivery", int(settings.get("target_frame_count") or 0)),
        "turnaround_proof": ("turnaround_boundary_review", int(settings.get("proof_frame_count") or 0)),
        "loop_seam_proof": ("loop_seam_boundary_review", int(settings.get("proof_frame_count") or 0)),
    }
    paths: Dict[str, Path] = {}
    for key, (purpose, _) in path_specs.items():
        record = plan.get(key) if isinstance(plan.get(key), Mapping) else {}
        candidate = Path(str(record.get("path") or "")).expanduser()
        paths[key] = candidate
        expected = {"path": str(candidate), "format": "mp4", "purpose": purpose}
        if record != expected:
            blockers.append(f"{key} record is not canonical")
        if not candidate.is_absolute():
            blockers.append(f"{key}.path must be absolute")
        elif project_root.is_absolute() and not _inside_project(candidate, project_root):
            blockers.append(f"{key}.path escaped the project directory")
        elif candidate.suffix.lower() != ".mp4":
            blockers.append(f"{key}.path must use .mp4")
        elif candidate.is_symlink():
            blockers.append(f"{key}.path must not be a symlink")
    if len({path.resolve() for path in paths.values() if path.is_absolute()}) != 3:
        blockers.append("delivery and proof paths must all be different")
    if source_path.is_absolute() and any(path.is_absolute() and path.resolve() == source_path.resolve() for path in paths.values()):
        blockers.append("delivery and proof paths must not overwrite the source")
    if plan.get("review_contract") != _review_contract():
        blockers.append("review_contract is stale or modified")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        for key, (_, expected_frames) in path_specs.items():
            stored = application.get(key if key != "delivery" else "output")
            stored = stored if isinstance(stored, Mapping) else {}
            live_path = _validate_live_file(stored, f"application.{key}", blockers)
            if stored.get("path") != str(paths.get(key) or ""):
                blockers.append(f"application.{key}.path does not match the plan")
            if live_path is not None:
                try:
                    live_media = _media_info(live_path)
                except (RuntimeError, ValueError) as exc:
                    blockers.append(f"{key} probe failed: {exc}")
                else:
                    if stored != live_media:
                        blockers.append(f"stored {key} media contract is stale or modified")
                    blockers.extend(
                        _media_contract_blockers(
                            live_media,
                            source,
                            settings,
                            expected_frames=expected_frames,
                            label=key,
                        )
                    )
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        validation_records = {
            "output": (paths.get("delivery"), application.get("output")),
            "turnaround_proof": (paths.get("turnaround_proof"), application.get("turnaround_proof")),
            "loop_seam_proof": (paths.get("loop_seam_proof"), application.get("loop_seam_proof")),
        }
        for key, (candidate, record) in validation_records.items():
            expected_command = _decode_command(candidate) if candidate is not None and candidate.is_absolute() else []
            if validation.get(f"{key}_decode_checked") is not True or validation.get(f"{key}_decode_command") != expected_command:
                blockers.append(f"full {key} decode validation is missing or stale")
            record = record if isinstance(record, Mapping) else {}
            if validation.get(f"{key}_sha256") != record.get("sha256"):
                blockers.append(f"{key} decode validation is not bound to current bytes")
        if validation.get("algorithm") != ALGORITHM:
            blockers.append("application algorithm receipt is stale or modified")
        blockers.extend(_review_blockers(plan))

    warnings = _computed_warnings(plan)
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    summary = {
        "request_kind": settings.get("request_kind"),
        "selected_frames": int(settings.get("selected_frame_count") or 0),
        "cycle_frames": int(settings.get("cycle_frame_count") or 0),
        "target_frames": int(settings.get("target_frame_count") or 0),
        "target_duration_seconds": settings.get("target_duration_seconds"),
        "applied": applied,
        "confirmed": bool(review) and not _review_blockers(plan),
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
    integrity: List[str] = []
    if plan.get("plan_id") != _plan_id(plan):
        integrity.append("plan_id does not match canonical plan content")
    derived = _compute_derived(plan)
    for field in ("blockers", "warnings", "summary", "status"):
        if plan.get(field) != derived[field]:
            integrity.append(f"stored {field} is stale or modified")
    result.update(derived)
    result["blockers"] = integrity + list(derived["blockers"])
    result["summary"] = {**derived["summary"], "blocking": len(result["blockers"])}
    result["status"] = "blocked" if result["blockers"] else derived["status"]
    return result


def build_plan(
    source_path: str,
    delivery_path: str,
    turnaround_proof_path: str,
    loop_seam_proof_path: str,
    *,
    start: Optional[float] = None,
    end: Optional[float] = None,
    cycles: Optional[int] = None,
    duration: Optional[str] = None,
    proof_context_seconds: float = 0.5,
    max_working_set_mib: float = 2048,
    project_dir: str = ".",
) -> Dict[str, Any]:
    if (cycles is None) == (duration is None):
        raise ValueError("provide exactly one of cycles or duration")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source_path_resolved = _resolve_project_path(source_path, root, label="source", must_exist=True)
    delivery = _resolve_project_path(delivery_path, root, label="delivery", must_exist=False, suffix=".mp4")
    turnaround_proof = _resolve_project_path(
        turnaround_proof_path, root, label="turnaround proof", must_exist=False, suffix=".mp4"
    )
    loop_seam_proof = _resolve_project_path(
        loop_seam_proof_path, root, label="loop seam proof", must_exist=False, suffix=".mp4"
    )
    if len({source_path_resolved, delivery, turnaround_proof, loop_seam_proof}) != 4:
        raise ValueError("source, delivery, and proof paths must all be different")
    source = _media_info(source_path_resolved)
    settings = _settings_for(
        source,
        start_seconds=start,
        end_seconds=end,
        request_kind="cycles" if cycles is not None else "duration",
        requested_value=cycles if cycles is not None else duration,
        proof_context_seconds=proof_context_seconds,
        max_working_set_mib=max_working_set_mib,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "source": source,
        "settings": settings,
        "delivery": {"path": str(delivery), "format": "mp4", "purpose": "ping_pong_loop_delivery"},
        "turnaround_proof": {
            "path": str(turnaround_proof),
            "format": "mp4",
            "purpose": "turnaround_boundary_review",
        },
        "loop_seam_proof": {
            "path": str(loop_seam_proof),
            "format": "mp4",
            "purpose": "loop_seam_boundary_review",
        },
        "application": None,
        "review": None,
        "review_contract": _review_contract(),
    }
    return _set_derived(plan)


def build_command(plan: Mapping[str, Any], output_path: Path) -> List[str]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") or {}
    start = int(settings.get("start_frame") or 0)
    end = int(settings.get("end_frame_exclusive") or 0)
    count = int(settings.get("selected_frame_count") or 0)
    cycle = int(settings.get("cycle_frame_count") or 0)
    target = int(settings.get("target_frame_count") or 0)
    repeats = int(settings.get("loop_filter_repeats") or 0)
    fps = float(settings.get("fps") or 0)
    rate = str(settings.get("fps_rational") or "")
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,fps={rate},trim=start_frame={start}:end_frame={end},"
        f"setpts=N/({fps:.9f}*TB),split=2[fwd][revsrc];"
        f"[fwd]setpts=PTS-STARTPTS[forward];"
        f"[revsrc]trim=start_frame=1:end_frame={count - 1},reverse,setpts=PTS-STARTPTS[backward];"
        f"[forward][backward]concat=n=2:v=1:a=0[cycle]"
    )
    tail = ""
    input_label = "cycle"
    if repeats:
        tail += f";[cycle]loop=loop={repeats}:size={cycle}:start=0[repeated]"
        input_label = "repeated"
    tail += (
        f";[{input_label}]trim=start_frame=0:end_frame={target},"
        f"setpts=N/({fps:.9f}*TB),setsar=1,format=yuv420p[outv]"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        graph + tail,
        "-map",
        "[outv]",
        "-frames:v",
        str(target),
        "-c:v",
        str(settings.get("video_encoder") or "libx264"),
        "-preset",
        str(settings.get("video_preset") or "medium"),
        "-crf",
        str(settings.get("video_crf") or 18),
        "-pix_fmt",
        str(settings.get("pixel_format") or "yuv420p"),
        "-metadata:s:v:0",
        "rotate=0",
        "-an",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _turnaround_proof_command(plan: Mapping[str, Any], rendered_path: Path, proof_path: Path) -> List[str]:
    settings = plan.get("settings") or {}
    boundary = int(settings.get("turnaround_frame") or 0)
    context = int(settings.get("proof_context_frames") or 0)
    fps = float(settings.get("fps") or 0)
    graph = (
        f"[0:v]trim=start_frame={boundary - context}:end_frame={boundary + context},"
        f"setpts=N/({fps:.9f}*TB),setsar=1,format=yuv420p[outv]"
    )
    return _proof_command(rendered_path, proof_path, graph, context * 2)


def _loop_seam_proof_command(plan: Mapping[str, Any], rendered_path: Path, proof_path: Path) -> List[str]:
    settings = plan.get("settings") or {}
    boundary = int(settings.get("loop_seam_frame") or 0)
    context = int(settings.get("proof_context_frames") or 0)
    fps = float(settings.get("fps") or 0)
    graph = (
        f"[0:v]trim=start_frame={boundary - context}:end_frame={boundary},setpts=PTS-STARTPTS[tail];"
        f"[0:v]trim=start_frame=0:end_frame={context},setpts=PTS-STARTPTS[head];"
        f"[tail][head]concat=n=2:v=1:a=0,setpts=N/({fps:.9f}*TB),setsar=1,format=yuv420p[outv]"
    )
    return _proof_command(rendered_path, proof_path, graph, context * 2)


def _proof_command(rendered_path: Path, proof_path: Path, graph: str, frames: int) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(rendered_path),
        "-filter_complex",
        graph,
        "-map",
        "[outv]",
        "-frames:v",
        str(frames),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-metadata:s:v:0",
        "rotate=0",
        "-an",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(proof_path),
    ]


def _temporary_output(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp.mp4", dir=str(target.parent))
    os.close(fd)
    return Path(name)


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read ping-pong plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("ping-pong plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("ping-pong plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"ping-pong plan must be an existing JSON file: {resolved}")
    return resolved


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    verification = verify_plan(plan)
    substantive = [item for item in verification.get("blockers") or [] if item != PENDING_APPLY]
    if substantive:
        raise ValueError("plan is not safe to apply: " + "; ".join(substantive))
    project_root = Path(str(plan.get("project_root") or ""))
    if not _inside_project(path, project_root):
        raise ValueError("ping-pong plan must stay inside the project directory")
    source = Path(str(plan["source"]["path"]))
    delivery = Path(str(plan["delivery"]["path"]))
    turnaround = Path(str(plan["turnaround_proof"]["path"]))
    seam = Path(str(plan["loop_seam_proof"]["path"]))
    for label, target in (("delivery", delivery), ("turnaround proof", turnaround), ("loop seam proof", seam)):
        if target.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
        if target.exists() and not force:
            raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    source_before = _fingerprint(source)
    temp_delivery = _temporary_output(delivery)
    temp_turnaround = _temporary_output(turnaround)
    temp_seam = _temporary_output(seam)
    settings = plan["settings"]
    expected_proof_frames = int(settings["proof_frame_count"])
    try:
        _run_checked(build_command(plan, temp_delivery), "ping-pong render")
        output_info = _media_info(temp_delivery)
        output_blockers = _media_contract_blockers(
            output_info,
            plan["source"],
            settings,
            expected_frames=int(settings["target_frame_count"]),
            label="delivery",
        )
        if output_blockers:
            raise RuntimeError("output validation failed: " + "; ".join(output_blockers))
        _run_checked(_decode_command(temp_delivery), "full ping-pong delivery decode")
        _run_checked(
            _turnaround_proof_command(plan, temp_delivery, temp_turnaround),
            "turnaround proof render",
        )
        _run_checked(_loop_seam_proof_command(plan, temp_delivery, temp_seam), "loop-seam proof render")
        for label, candidate in (("turnaround proof", temp_turnaround), ("loop seam proof", temp_seam)):
            info = _media_info(candidate)
            proof_blockers = _media_contract_blockers(
                info,
                plan["source"],
                settings,
                expected_frames=expected_proof_frames,
                label=label,
            )
            if proof_blockers:
                raise RuntimeError(f"{label} validation failed: " + "; ".join(proof_blockers))
            _run_checked(_decode_command(candidate), f"full {label} decode")
        if _fingerprint(source) != source_before:
            raise RuntimeError("source changed during ping-pong rendering; outputs were not promoted")
        os.replace(temp_delivery, delivery)
        os.replace(temp_turnaround, turnaround)
        os.replace(temp_seam, seam)
    finally:
        for temporary in (temp_delivery, temp_turnaround, temp_seam):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output = _media_info(delivery)
    turnaround_info = _media_info(turnaround)
    seam_info = _media_info(seam)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "turnaround_proof": turnaround_info,
        "loop_seam_proof": seam_info,
        "validation": {
            "validated_at": utc_now(),
            "output_decode_checked": True,
            "output_decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
            "turnaround_proof_decode_checked": True,
            "turnaround_proof_decode_command": _decode_command(turnaround),
            "turnaround_proof_sha256": turnaround_info["sha256"],
            "loop_seam_proof_decode_checked": True,
            "loop_seam_proof_decode_command": _decode_command(seam),
            "loop_seam_proof_sha256": seam_info["sha256"],
            "algorithm": dict(ALGORITHM),
            "cadence_algorithm": CADENCE_ALGORITHM,
        },
    }
    plan["review"] = None
    _set_derived(plan)
    final = verify_plan(plan)
    substantive = [item for item in final.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise RuntimeError("applied ping-pong plan failed final verification: " + "; ".join(substantive))
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    turnaround_playback: str,
    seam_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the ping-pong plan before confirming it")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty review note are required")
    reviewable = dict(plan)
    reviewable["review"] = None
    _set_derived(reviewable)
    current = verify_plan(reviewable)
    substantive = [item for item in current.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise ValueError("plan is not safe to confirm: " + "; ".join(substantive))
    playbacks = {
        "full_playback": full_playback,
        "turnaround_playback": turnaround_playback,
        "seam_playback": seam_playback,
    }
    if any(value not in {"completed", "not_completed"} for value in playbacks.values()):
        raise ValueError("every playback state must be completed or not_completed")
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    application = plan["application"]
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        **playbacks,
        "checks": normalized,
        "output_sha256": application["output"]["sha256"],
        "turnaround_proof_sha256": application["turnaround_proof"]["sha256"],
        "loop_seam_proof_sha256": application["loop_seam_proof"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    lines = [
        "# Ping-pong Loop Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Selected frames: `{settings.get('start_frame')}`–`{int(settings.get('end_frame_exclusive') or 0) - 1}` (`{settings.get('selected_frame_count')}` frames)",
        f"- Cycle: `{settings.get('forward_frame_count')}` forward + `{settings.get('reverse_frame_count')}` reverse = `{settings.get('cycle_frame_count')}` frames",
        f"- Target: `{settings.get('target_frame_count')}` frames / `{settings.get('target_duration_seconds')}` seconds",
        f"- Reverse working-set estimate / limit: `{settings.get('estimated_reverse_working_set_mib')}` / `{settings.get('max_working_set_mib')}` MiB",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path', '')}`",
        f"- Turnaround proof: `{(plan.get('turnaround_proof') or {}).get('path', '')}`",
        f"- Loop-seam proof: `{(plan.get('loop_seam_proof') or {}).get('path', '')}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(
        [
            "",
            "## Review contract",
            "",
            "Play the turnaround proof, loop-seam proof, and complete delivery at 1×. Confirm both direction changes are intentional, no endpoint becomes a one-frame hold or flash, framing stays stable, and the effect supports the edit. Source audio is always dropped; add music/SFX only after this visual loop is approved.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_optional_markdown(path: Optional[str], report: Mapping[str, Any], *, forbidden: Sequence[Path]) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if target in {item.expanduser().resolve() for item in forbidden}:
        raise ValueError("markdown output must not overlap a source, plan, delivery, or proof file")
    if target.is_symlink():
        raise ValueError("markdown output must not be a symlink")
    _atomic_write_text(target, render_markdown(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an endpoint-deduplicated forward/reverse loop with source-bound review proofs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Bind the CFR source range, target, delivery, and proofs.")
    plan.add_argument("source", help="Project-local progressive CFR source video.")
    plan.add_argument("--start", type=float, help="Selected source start in seconds; snaps to the nearest frame.")
    plan.add_argument("--end", type=float, help="Selected source end in seconds; snaps to an exclusive frame boundary.")
    target = plan.add_mutually_exclusive_group(required=True)
    target.add_argument("--cycles", type=int, help="Number of complete forward/reverse cycles.")
    target.add_argument("--duration", help="Target duration in seconds, mm:ss, or hh:mm:ss; must cover one cycle.")
    plan.add_argument("--proof-context", type=float, default=0.5, help="Seconds around each direction boundary.")
    plan.add_argument(
        "--max-working-set-mib",
        type=float,
        default=2048,
        help="Fail before rendering when conservative reverse-buffer estimate exceeds this value.",
    )
    plan.add_argument("--delivery", required=True)
    plan.add_argument("--turnaround-proof", required=True)
    plan.add_argument("--loop-seam-proof", required=True)
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--output", required=True, help="Plan JSON path.")
    plan.add_argument("--markdown")
    plan.add_argument("--force", action="store_true", help="Replace plan/Markdown artifacts only.")

    apply = subparsers.add_parser("apply", help="Render, fully decode, and atomically promote delivery and proofs.")
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser("confirm", help="Record normal-speed proof and full-delivery review.")
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    confirm.add_argument("--turnaround-playback", choices=("completed", "not_completed"), required=True)
    confirm.add_argument("--seam-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True)
    confirm.add_argument("--markdown")

    verify = subparsers.add_parser("verify", help="Re-probe source, outputs, proofs, and review; reject drift.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            output = Path(args.output).expanduser().resolve()
            if output.is_symlink() or (output.exists() and not args.force):
                raise FileExistsError(f"plan output exists or is a symlink; pass --force to replace: {output}")
            report = build_plan(
                args.source,
                args.delivery,
                args.turnaround_proof,
                args.loop_seam_proof,
                start=args.start,
                end=args.end,
                cycles=args.cycles,
                duration=args.duration,
                proof_context_seconds=args.proof_context,
                max_working_set_mib=args.max_working_set_mib,
                project_dir=args.project_dir,
            )
            forbidden = [
                Path(str(report["source"]["path"])),
                Path(str(report["delivery"]["path"])),
                Path(str(report["turnaround_proof"]["path"])),
                Path(str(report["loop_seam_proof"]["path"])),
            ]
            if output in {item.resolve() for item in forbidden}:
                raise ValueError("plan output must not overlap source, delivery, or proofs")
            if not _inside_project(output, Path(str(report["project_root"]))):
                raise ValueError("plan output must stay inside the project directory")
            _atomic_write_json(output, report)
            _write_optional_markdown(args.markdown, report, forbidden=[output, *forbidden])
        elif args.command == "apply":
            report = apply_plan(args.plan, force=args.force)
            forbidden = [
                Path(args.plan),
                Path(str(report["source"]["path"])),
                Path(str(report["delivery"]["path"])),
                Path(str(report["turnaround_proof"]["path"])),
                Path(str(report["loop_seam_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        elif args.command == "confirm":
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                turnaround_playback=args.turnaround_playback,
                seam_playback=args.seam_playback,
                checks={field: getattr(args, field) for field in REVIEW_FIELDS},
            )
            forbidden = [
                Path(args.plan),
                Path(str(report["source"]["path"])),
                Path(str(report["delivery"]["path"])),
                Path(str(report["turnaround_proof"]["path"])),
                Path(str(report["loop_seam_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        else:
            plan_path = _resolve_plan_file(args.plan)
            report = verify_plan(_load_plan(plan_path))
            forbidden = [
                plan_path,
                Path(str((report.get("source") or {}).get("path") or "")),
                Path(str((report.get("delivery") or {}).get("path") or "")),
                Path(str((report.get("turnaround_proof") or {}).get("path") or "")),
                Path(str((report.get("loop_seam_proof") or {}).get("path") or "")),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        print(
            json.dumps(
                {"status": report["status"], "plan_id": report.get("plan_id"), "summary": report["summary"]},
                ensure_ascii=False,
            )
        )
        return 2 if getattr(args, "strict", False) and report["summary"]["blocking"] else 0
    except (FileExistsError, OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"ping_pong_loop.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
