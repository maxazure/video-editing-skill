#!/usr/bin/env python3
"""Plan, render, review, and verify a source-bound multi-clip assembly.

Every input is normalized inside one FFmpeg filter graph before concatenation.
The tool never falls back to unchecked stream copy.  It binds ordered source
bytes, media/cadence contracts, output settings, a boundary-proof video, full
decode receipts, and a human normal-speed seam review.
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
from typing import Any, Dict, List, Mapping, Optional, Sequence

from frame_rate_conform import CADENCE_ALGORITHM, analyze_cadence, parse_rate, probe_media


VERSION = "clip_assembly_plan.v1"
PENDING_APPLY = "clip assembly has not been rendered and validated"
PENDING_CONFIRM = "complete delivery and boundary-proof review have not been confirmed"
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
REVIEW_FIELDS = (
    "clip_order",
    "visual_seams",
    "frame_continuity",
    "audio_seams",
    "complete_coverage",
)
REVIEW_CHOICES = {"pass", "fail", "unobservable", "not_applicable"}
MAX_CLIPS = 500
MAX_DIMENSION = 8192
MAX_DURATION_SECONDS = 24 * 60 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


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


def _default_rate(source: Mapping[str, Any]) -> Dict[str, Any]:
    for key in ("r_frame_rate", "avg_frame_rate"):
        try:
            return parse_rate(str(source.get(key) or ""))
        except ValueError:
            continue
    cadence = source.get("cadence") if isinstance(source.get("cadence"), Mapping) else {}
    median = float((cadence.get("interval_seconds") or {}).get("median") or 0)
    if median > 0:
        return parse_rate(str(round(1.0 / median, 6)))
    raise ValueError("first source has no usable frame-rate contract; pass --fps explicitly")


def _validate_source(source: Mapping[str, Any], index: int, audio_mode: str) -> None:
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (
        isinstance(bit_depth, int) and bit_depth > 8
    ):
        raise ValueError(
            f"source {index} is HDR/BT.2020/greater-than-8-bit; create an explicit "
            "Rec.709 derivative with hdr_sdr.py before assembly"
        )
    duration = float(source.get("video_duration") or 0)
    if duration <= 0:
        raise ValueError(f"source {index} has no positive video duration")
    if source.get("has_audio") and audio_mode != "drop":
        fps = float(source.get("avg_fps") or source.get("nominal_fps") or 30)
        tolerance = max(0.1, 2.0 / max(fps, 1.0))
        video_start = float(source.get("video_start_time") or 0)
        audio_start = float(source.get("audio_start_time") or 0)
        video_end = video_start + duration
        audio_end = audio_start + float(source.get("audio_duration") or 0)
        if abs(video_start - audio_start) > tolerance or abs(video_end - audio_end) > tolerance:
            raise ValueError(
                f"source {index} audio/video boundaries differ beyond {tolerance:.3f}s; "
                "repair sync or use --audio-mode drop before assembly"
            )


def _settings_for(
    sources: Sequence[Mapping[str, Any]],
    *,
    width: int,
    height: int,
    fps: Any,
    fit: str,
    audio_mode: str,
    proof_context_seconds: float,
) -> Dict[str, Any]:
    if len(sources) < 2 or len(sources) > MAX_CLIPS:
        raise ValueError(f"clip assembly requires 2-{MAX_CLIPS} sources")
    if fit not in {"contain", "crop"}:
        raise ValueError("fit must be contain or crop")
    if audio_mode not in {"fill_silence", "drop"}:
        raise ValueError("audio_mode must be fill_silence or drop")
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(f"output dimensions must be positive and at most {MAX_DIMENSION}px")
    if width % 2 or height % 2:
        raise ValueError("output width and height must be even for yuv420p")
    rate = parse_rate(fps)
    if not math.isfinite(proof_context_seconds) or proof_context_seconds < 0.1:
        raise ValueError("proof context must be at least 0.1 seconds")
    for index, source in enumerate(sources, start=1):
        _validate_source(source, index, audio_mode)
    durations = [float(source.get("video_duration") or 0) for source in sources]
    expected_duration = sum(durations)
    if expected_duration > MAX_DURATION_SECONDS:
        raise ValueError("assembled delivery must be at most 24 hours")
    has_any_audio = any(bool(source.get("has_audio")) for source in sources)
    output_has_audio = audio_mode == "fill_silence" and has_any_audio
    boundaries: List[Dict[str, Any]] = []
    cursor = 0.0
    proof_cursor = 0.0
    for index in range(len(sources) - 1):
        cursor += durations[index]
        before = min(proof_context_seconds, durations[index] / 2.0)
        after = min(proof_context_seconds, durations[index + 1] / 2.0)
        if before < 0.05 or after < 0.05:
            raise ValueError(
                f"sources {index + 1}/{index + 2} are too short for a reviewable boundary proof"
            )
        segment_duration = before + after
        boundaries.append(
            {
                "id": f"boundary-{index + 1:03d}",
                "before_source_index": index + 1,
                "after_source_index": index + 2,
                "output_time_seconds": round(cursor, 6),
                "window_start_seconds": round(cursor - before, 6),
                "window_end_seconds": round(cursor + after, 6),
                "window_duration_seconds": round(segment_duration, 6),
                "proof_start_seconds": round(proof_cursor, 6),
            }
        )
        proof_cursor += segment_duration
    duration_tolerance = max(0.1, 2.0 / float(rate["fps"]))
    normalizations: List[Dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        normalizations.append(
            {
                "source_index": index,
                "display_size": f"{source.get('width')}x{source.get('height')}",
                "rotation_degrees": int(source.get("rotation") or 0),
                "decoded_cadence": "vfr" if (source.get("cadence") or {}).get("is_variable") else "cfr",
                "sample_aspect_ratio": source.get("sample_aspect_ratio"),
                "audio": (
                    f"{source.get('sample_rate')}Hz/{source.get('channels')}ch"
                    if source.get("has_audio")
                    else "synthetic_silence" if output_has_audio else "none"
                ),
            }
        )
    return {
        "source_count": len(sources),
        "boundary_count": len(boundaries),
        "target_width": width,
        "target_height": height,
        "target_rate": rate,
        "fit": fit,
        "pad_color": "black",
        "pixel_format": "yuv420p",
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "audio_mode": audio_mode,
        "output_has_audio": output_has_audio,
        "audio_encoder": "aac" if output_has_audio else None,
        "audio_bitrate_kbps": 192 if output_has_audio else None,
        "audio_sample_rate": 48000 if output_has_audio else None,
        "audio_channels": 2 if output_has_audio else None,
        "expected_duration_seconds": round(expected_duration, 6),
        "proof_context_requested_seconds": round(proof_context_seconds, 6),
        "proof_duration_seconds": round(proof_cursor, 6),
        "duration_tolerance_seconds": round(duration_tolerance, 6),
        "stream_start_tolerance_seconds": round(max(0.02, 1.0 / float(rate["fps"])), 6),
        "av_end_tolerance_seconds": round(duration_tolerance, 6),
        "boundaries": boundaries,
        "normalizations": normalizations,
    }


def _review_contract(output_has_audio: bool) -> Dict[str, Any]:
    return {
        "playback": "Watch the complete delivery and every proof window at normal speed with audio when present.",
        "checks": list(REVIEW_FIELDS),
        "audio_transition_expected": bool(output_has_audio),
        "pass_rule": (
            "full_playback=completed, proof_playback=completed, every picture/order/coverage check=pass, "
            "and audio_seams=pass when audio is delivered or not_applicable when it is absent"
        ),
        "limitations": [
            "The assembly uses hard cuts; it does not invent transitions or semantic continuity.",
            "Geometry, cadence, pixel aspect, timestamps, pixel format, and audio layout are normalized in one encode.",
            "The boundary proof accelerates review but never replaces complete delivery playback.",
        ],
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: plan.get(key)
        for key in (
            "version",
            "project_root",
            "sources",
            "settings",
            "delivery",
            "boundary_proof",
            "application",
            "review",
            "review_contract",
            "blockers",
            "warnings",
            "summary",
            "status",
        )
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_live_file(
    record: Mapping[str, Any], label: str, blockers: List[str]
) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.is_symlink() or not candidate.is_file():
        blockers.append(f"{label} is missing or is a symlink")
        return None
    try:
        live = _fingerprint(candidate)
    except OSError as exc:
        blockers.append(f"{label} could not be fingerprinted: {exc}")
        return None
    if record.get("sha256") != live["sha256"] or record.get("size_bytes") != live["size_bytes"]:
        blockers.append(f"{label} fingerprint changed")
    return candidate.resolve()


def _format_matches_mp4(value: Any) -> bool:
    return bool({str(item).lower() for item in (value or [])}.intersection({"mov", "mp4"}))


def _output_contract_blockers(
    media: Mapping[str, Any], settings: Mapping[str, Any], *, target_duration: float, label: str
) -> List[str]:
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append(f"{label} is not an MP4-family container")
    if media.get("video_codec") != "h264" or media.get("pixel_format") != "yuv420p":
        blockers.append(f"{label} must use H.264 yuv420p")
    if media.get("rotation") != 0:
        blockers.append(f"{label} must bake display rotation and clear rotation metadata")
    if media.get("width") != settings.get("target_width") or media.get("height") != settings.get("target_height"):
        blockers.append(f"{label} displayed dimensions do not match the assembly contract")
    if media.get("sample_aspect_ratio") != "1:1":
        blockers.append(f"{label} must use square pixels (SAR 1:1)")
    target_fps = float((settings.get("target_rate") or {}).get("fps") or 0)
    if abs(float(media.get("avg_fps") or 0) - target_fps) > 0.001:
        blockers.append(f"{label} average fps does not match the assembly contract")
    cadence = media.get("cadence") if isinstance(media.get("cadence"), Mapping) else {}
    if cadence.get("algorithm") != CADENCE_ALGORITHM or cadence.get("is_variable"):
        blockers.append(f"{label} decoded cadence is not constant under the planned algorithm")
    tolerance = float(settings.get("duration_tolerance_seconds") or 0)
    if abs(float(media.get("video_duration") or 0) - target_duration) > tolerance:
        blockers.append(f"{label} video duration does not match the sum of planned clips")
    expected_audio = bool(settings.get("output_has_audio"))
    if bool(media.get("has_audio")) != expected_audio:
        blockers.append(f"{label} audio presence does not match the plan")
    if expected_audio:
        if media.get("audio_codec") != "aac" or media.get("sample_rate") != 48000 or media.get("channels") != 2:
            blockers.append(f"{label} audio must use 48 kHz stereo AAC")
        video_start = float(media.get("video_start_time") or 0)
        audio_start = float(media.get("audio_start_time") or 0)
        start_tolerance = float(settings.get("stream_start_tolerance_seconds") or 0)
        if abs(video_start) > start_tolerance or abs(audio_start) > start_tolerance:
            blockers.append(f"{label} streams do not start near zero")
        if abs(video_start - audio_start) > start_tolerance:
            blockers.append(f"{label} audio/video start times differ beyond tolerance")
        video_end = video_start + float(media.get("video_duration") or 0)
        audio_end = audio_start + float(media.get("audio_duration") or 0)
        if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
            blockers.append(f"{label} audio/video end times differ beyond tolerance")
    return blockers


def _decode_command(path: Path) -> List[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
        "-map", "0", "-f", "null", "-",
    ]


def _review_blockers(plan: Mapping[str, Any]) -> List[str]:
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    if not review:
        return [PENDING_CONFIRM]
    blockers: List[str] = []
    if not str(review.get("reviewed_by") or "").strip() or not str(review.get("note") or "").strip():
        blockers.append("reviewed_by and a non-empty review note are required")
    if review.get("full_playback") != "completed":
        blockers.append("complete normal-speed delivery playback is required")
    if review.get("proof_playback") != "completed":
        blockers.append("complete normal-speed boundary-proof playback is required")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    for field in REVIEW_FIELDS:
        expected = "not_applicable" if field == "audio_seams" and not settings.get("output_has_audio") else "pass"
        if checks.get(field) != expected:
            blockers.append(f"review check {field} must be {expected}, got {checks.get(field) or 'missing'}")
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
    proof = application.get("boundary_proof") if isinstance(application.get("boundary_proof"), Mapping) else {}
    if review.get("output_sha256") != output.get("sha256"):
        blockers.append("review is not bound to the current assembled delivery sha256")
    if review.get("boundary_proof_sha256") != proof.get("sha256"):
        blockers.append("review is not bound to the current boundary-proof sha256")
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    sources = plan.get("sources") if isinstance(plan.get("sources"), list) else []
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    warnings: List[str] = []
    if settings.get("output_has_audio") and any(not source.get("has_audio") for source in sources):
        warnings.append("Silent sources receive synthetic 48 kHz stereo silence; review every audio boundary.")
    if any((source.get("cadence") or {}).get("is_variable") for source in sources):
        warnings.append("At least one VFR source is normalized to the planned CFR inside the assembly encode.")
    return warnings


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    project_root = Path(str(plan.get("project_root") or "")).expanduser()
    if not project_root.is_absolute() or not project_root.is_dir():
        blockers.append("project_root must be an existing absolute directory")
    sources = plan.get("sources") if isinstance(plan.get("sources"), list) else []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            blockers.append(f"source {index} must be an object")
            continue
        source_path = _validate_live_file(source, f"source {index}", blockers)
        if source_path is None:
            continue
        if project_root.is_absolute() and not _inside_project(source_path, project_root):
            blockers.append(f"source {index} escaped the project directory")
        try:
            live_source = _media_info(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source {index} media/cadence probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append(f"source {index} fingerprint, media contract, or decoded cadence changed")
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        expected_settings = _settings_for(
            sources,
            width=int(settings.get("target_width") or 0),
            height=int(settings.get("target_height") or 0),
            fps=str((settings.get("target_rate") or {}).get("rational") or ""),
            fit=str(settings.get("fit") or ""),
            audio_mode=str(settings.get("audio_mode") or ""),
            proof_context_seconds=float(settings.get("proof_context_requested_seconds") or 0),
        )
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
        expected_settings = settings
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical clip-assembly contract")
    paths: Dict[str, Path] = {}
    expected_records = {
        "delivery": {"format": "mp4", "purpose": "normalized_hard_cut_assembly"},
        "boundary_proof": {"format": "mp4", "purpose": "all_assembly_boundaries_review"},
    }
    for key, expected_record in expected_records.items():
        record = plan.get(key) if isinstance(plan.get(key), Mapping) else {}
        candidate = Path(str(record.get("path") or "")).expanduser()
        paths[key] = candidate
        if not candidate.is_absolute():
            blockers.append(f"{key}.path must be absolute")
        elif project_root.is_absolute() and not _inside_project(candidate, project_root):
            blockers.append(f"{key}.path escaped the project directory")
        elif candidate.suffix.lower() != ".mp4":
            blockers.append(f"{key}.path must use .mp4")
        elif candidate.is_symlink():
            blockers.append(f"{key}.path must not be a symlink")
        if record != {"path": str(candidate), **expected_record}:
            blockers.append(f"{key} record is not canonical")
    if paths.get("delivery") == paths.get("boundary_proof"):
        blockers.append("delivery and boundary_proof paths must differ")
    for index, source in enumerate(sources, start=1):
        source_path = Path(str((source or {}).get("path") or "")).expanduser()
        for key, candidate in paths.items():
            if candidate.is_absolute() and source_path.is_absolute() and candidate.resolve() == source_path.resolve():
                blockers.append(f"{key} must not overwrite source {index}")
    if plan.get("review_contract") != _review_contract(bool(settings.get("output_has_audio"))):
        blockers.append("review_contract is stale or modified")
    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        proof = application.get("boundary_proof") if isinstance(application.get("boundary_proof"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        proof_path = _validate_live_file(proof, "application.boundary_proof", blockers)
        if output.get("path") != str(paths.get("delivery") or ""):
            blockers.append("application.output.path does not match delivery.path")
        if proof.get("path") != str(paths.get("boundary_proof") or ""):
            blockers.append("application.boundary_proof.path does not match boundary_proof.path")
        if output_path is not None:
            try:
                live_output = _media_info(output_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"assembled delivery probe failed: {exc}")
            else:
                if output != live_output:
                    blockers.append("stored assembled-delivery contract is stale or modified")
                blockers.extend(
                    _output_contract_blockers(
                        live_output,
                        settings,
                        target_duration=float(settings.get("expected_duration_seconds") or 0),
                        label="assembled delivery",
                    )
                )
        if proof_path is not None:
            try:
                live_proof = _media_info(proof_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"boundary-proof probe failed: {exc}")
            else:
                if proof != live_proof:
                    blockers.append("stored boundary-proof contract is stale or modified")
                blockers.extend(
                    _output_contract_blockers(
                        live_proof,
                        settings,
                        target_duration=float(settings.get("proof_duration_seconds") or 0),
                        label="boundary proof",
                    )
                )
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_output_decode = _decode_command(paths["delivery"]) if paths.get("delivery", Path()).is_absolute() else []
        expected_proof_decode = _decode_command(paths["boundary_proof"]) if paths.get("boundary_proof", Path()).is_absolute() else []
        if validation.get("output_decode_checked") is not True or validation.get("output_decode_command") != expected_output_decode:
            blockers.append("full assembled-delivery decode validation is missing or stale")
        if validation.get("proof_decode_checked") is not True or validation.get("proof_decode_command") != expected_proof_decode:
            blockers.append("full boundary-proof decode validation is missing or stale")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("output decode validation is not bound to the current delivery sha256")
        if validation.get("boundary_proof_sha256") != proof.get("sha256"):
            blockers.append("proof decode validation is not bound to the current boundary-proof sha256")
        if validation.get("boundaries") != settings.get("boundaries"):
            blockers.append("validated boundary map is stale or modified")
        blockers.extend(_review_blockers(plan))
    warnings = _computed_warnings(plan)
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    summary = {
        "source_count": len(sources),
        "boundary_count": int(settings.get("boundary_count") or 0),
        "expected_duration_seconds": settings.get("expected_duration_seconds"),
        "fit": settings.get("fit"),
        "audio_mode": settings.get("audio_mode"),
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
    source_paths: Sequence[str],
    delivery_path: str,
    boundary_proof_path: str,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[str] = None,
    fit: str = "contain",
    audio_mode: str = "fill_silence",
    proof_context_seconds: float = 0.75,
    project_dir: str = ".",
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    if len(source_paths) < 2:
        raise ValueError("provide at least two source clips in playback order")
    resolved_sources = [
        _resolve_project_path(value, root, label=f"source {index}", must_exist=True)
        for index, value in enumerate(source_paths, start=1)
    ]
    if len(set(resolved_sources)) != len(resolved_sources):
        raise ValueError("source clips must be unique; use loop_fill.py for repeated material")
    delivery = _resolve_project_path(
        delivery_path, root, label="delivery", must_exist=False, suffix=".mp4"
    )
    boundary_proof = _resolve_project_path(
        boundary_proof_path, root, label="boundary proof", must_exist=False, suffix=".mp4"
    )
    if delivery == boundary_proof or delivery in resolved_sources or boundary_proof in resolved_sources:
        raise ValueError("sources, delivery, and boundary proof paths must all differ")
    sources = [_media_info(path) for path in resolved_sources]
    if (width is None) != (height is None):
        raise ValueError("provide both --width and --height, or neither")
    target_width = int(width if width is not None else sources[0]["width"])
    target_height = int(height if height is not None else sources[0]["height"])
    target_rate = parse_rate(fps) if fps is not None else _default_rate(sources[0])
    settings = _settings_for(
        sources,
        width=target_width,
        height=target_height,
        fps=target_rate["rational"],
        fit=fit,
        audio_mode=audio_mode,
        proof_context_seconds=proof_context_seconds,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "sources": sources,
        "settings": settings,
        "delivery": {
            "path": str(delivery),
            "format": "mp4",
            "purpose": "normalized_hard_cut_assembly",
        },
        "boundary_proof": {
            "path": str(boundary_proof),
            "format": "mp4",
            "purpose": "all_assembly_boundaries_review",
        },
        "application": None,
        "review": None,
        "review_contract": _review_contract(bool(settings["output_has_audio"])),
    }
    return _set_derived(plan)


def _geometry_filter(settings: Mapping[str, Any]) -> str:
    width = int(settings["target_width"])
    height = int(settings["target_height"])
    if settings.get("fit") == "crop":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def build_command(plan: Mapping[str, Any], output_path: Path) -> List[str]:
    sources = plan.get("sources") or []
    settings = plan.get("settings") or {}
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    for source in sources:
        command.extend(["-i", str(source["path"])])
    audio_inputs: Dict[int, int] = {}
    if settings.get("output_has_audio"):
        for index, source in enumerate(sources):
            if not source.get("has_audio"):
                input_index = len(sources) + len(audio_inputs)
                audio_inputs[index] = input_index
                command.extend(
                    [
                        "-f", "lavfi", "-t", f"{float(source['video_duration']):.6f}",
                        "-i", "anullsrc=r=48000:cl=stereo",
                    ]
                )
    graph: List[str] = []
    labels: List[str] = []
    geometry = _geometry_filter(settings)
    rate = (settings.get("target_rate") or {}).get("rational")
    for index, source in enumerate(sources):
        duration = float(source["video_duration"])
        graph.append(
            f"[{index}:v:0]trim=start=0:duration={duration:.6f},setpts=PTS-STARTPTS,"
            f"{geometry},fps=fps={rate}:start_time=0:round=near,setsar=1,format=yuv420p[v{index}]"
        )
        labels.append(f"[v{index}]")
        if settings.get("output_has_audio"):
            audio_input = f"{index}:a:0" if source.get("has_audio") else f"{audio_inputs[index]}:a:0"
            graph.append(
                f"[{audio_input}]atrim=start=0:duration={duration:.6f},asetpts=PTS-STARTPTS,"
                f"aresample=48000:async=1:first_pts=0,"
                f"aformat=sample_rates=48000:channel_layouts=stereo,"
                f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[a{index}]"
            )
            labels.append(f"[a{index}]")
    graph.append(
        "".join(labels)
        + f"concat=n={len(sources)}:v=1:a={1 if settings.get('output_has_audio') else 0}"
        + ("[vout][aout]" if settings.get("output_has_audio") else "[vout]")
    )
    command.extend(["-filter_complex", ";".join(graph), "-map", "[vout]"])
    if settings.get("output_has_audio"):
        command.extend(["-map", "[aout]"])
    command.extend(
        [
            "-c:v", "libx264", "-preset", str(settings.get("video_preset") or "medium"),
            "-crf", str(settings.get("video_crf") or 18), "-pix_fmt", "yuv420p",
            "-metadata:s:v:0", "rotate=0", "-r", str(rate), "-fps_mode", "cfr",
        ]
    )
    if settings.get("output_has_audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(output_path)])
    return command


def _proof_command(plan: Mapping[str, Any], delivery: Path, proof: Path) -> List[str]:
    settings = plan.get("settings") or {}
    boundaries = settings.get("boundaries") or []
    graph: List[str] = []
    labels: List[str] = []
    for index, boundary in enumerate(boundaries):
        start = float(boundary["window_start_seconds"])
        end = float(boundary["window_end_seconds"])
        graph.append(f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{index}]")
        labels.append(f"[v{index}]")
        if settings.get("output_has_audio"):
            graph.append(f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{index}]")
            labels.append(f"[a{index}]")
    graph.append(
        "".join(labels)
        + f"concat=n={len(boundaries)}:v=1:a={1 if settings.get('output_has_audio') else 0}"
        + ("[vout][aout]" if settings.get("output_has_audio") else "[vout]")
    )
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(delivery),
        "-filter_complex", ";".join(graph), "-map", "[vout]",
    ]
    if settings.get("output_has_audio"):
        command.extend(["-map", "[aout]"])
    command.extend(
        [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-metadata:s:v:0", "rotate=0",
        ]
    )
    if settings.get("output_has_audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(proof)])
    return command


def _temporary_output(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp.mp4", dir=str(target.parent))
    os.close(fd)
    return Path(name)


def _resolve_plan_file(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError("clip-assembly plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"clip-assembly plan must be an existing JSON file: {resolved}")
    return resolved


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read clip-assembly plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("clip-assembly plan must be a JSON object")
    return data


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    verification = verify_plan(plan)
    substantive = [item for item in verification.get("blockers") or [] if item != PENDING_APPLY]
    if substantive:
        raise ValueError("plan is not safe to apply: " + "; ".join(substantive))
    project_root = Path(str(plan.get("project_root") or ""))
    if not _inside_project(path, project_root):
        raise ValueError("clip-assembly plan must stay inside the project directory")
    delivery = Path(str(plan["delivery"]["path"]))
    proof = Path(str(plan["boundary_proof"]["path"]))
    for label, target in (("delivery", delivery), ("boundary proof", proof)):
        if target.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
        if target.exists() and not force:
            raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    sources_before = [_fingerprint(Path(str(source["path"]))) for source in plan["sources"]]
    temporary_delivery = _temporary_output(delivery)
    temporary_proof = _temporary_output(proof)
    try:
        _run_checked(build_command(plan, temporary_delivery), "clip-assembly render")
        temporary_output_info = _media_info(temporary_delivery)
        output_blockers = _output_contract_blockers(
            temporary_output_info,
            plan["settings"],
            target_duration=float(plan["settings"]["expected_duration_seconds"]),
            label="assembled delivery",
        )
        if output_blockers:
            raise RuntimeError("output validation failed: " + "; ".join(output_blockers))
        _run_checked(_decode_command(temporary_delivery), "full assembled-delivery decode")
        _run_checked(_proof_command(plan, temporary_delivery, temporary_proof), "boundary-proof render")
        temporary_proof_info = _media_info(temporary_proof)
        proof_blockers = _output_contract_blockers(
            temporary_proof_info,
            plan["settings"],
            target_duration=float(plan["settings"]["proof_duration_seconds"]),
            label="boundary proof",
        )
        if proof_blockers:
            raise RuntimeError("boundary-proof validation failed: " + "; ".join(proof_blockers))
        _run_checked(_decode_command(temporary_proof), "full boundary-proof decode")
        live_sources = [_fingerprint(Path(str(source["path"]))) for source in plan["sources"]]
        if live_sources != sources_before:
            raise RuntimeError("a source changed during assembly; outputs were not promoted")
        os.replace(temporary_delivery, delivery)
        os.replace(temporary_proof, proof)
    finally:
        for temporary in (temporary_delivery, temporary_proof):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output_info = _media_info(delivery)
    proof_info = _media_info(proof)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output_info,
        "boundary_proof": proof_info,
        "validation": {
            "validated_at": utc_now(),
            "output_decode_checked": True,
            "output_decode_command": _decode_command(delivery),
            "proof_decode_checked": True,
            "proof_decode_command": _decode_command(proof),
            "output_sha256": output_info["sha256"],
            "boundary_proof_sha256": proof_info["sha256"],
            "boundaries": plan["settings"]["boundaries"],
            "cadence_algorithm": CADENCE_ALGORITHM,
        },
    }
    plan["review"] = None
    _set_derived(plan)
    final = verify_plan(plan)
    substantive = [item for item in final.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise RuntimeError("applied clip assembly failed final verification: " + "; ".join(substantive))
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    proof_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the clip-assembly plan before confirming it")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty review note are required")
    reviewable = dict(plan)
    reviewable["review"] = None
    _set_derived(reviewable)
    current = verify_plan(reviewable)
    substantive = [item for item in current.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise ValueError("plan is not safe to confirm: " + "; ".join(substantive))
    if full_playback not in {"completed", "not_completed"}:
        raise ValueError("full_playback must be completed or not_completed")
    if proof_playback not in {"completed", "not_completed"}:
        raise ValueError("proof_playback must be completed or not_completed")
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    application = plan["application"]
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "full_playback": full_playback,
        "proof_playback": proof_playback,
        "checks": normalized,
        "output_sha256": application["output"]["sha256"],
        "boundary_proof_sha256": application["boundary_proof"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    settings = plan.get("settings") or {}
    lines = [
        "# Clip Assembly Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Sources / boundaries: `{settings.get('source_count')}` / `{settings.get('boundary_count')}`",
        f"- Target: `{settings.get('target_width')}x{settings.get('target_height')}` @ `{(settings.get('target_rate') or {}).get('rational')}` fps, fit `{settings.get('fit')}`",
        f"- Expected duration: `{settings.get('expected_duration_seconds')}` seconds",
        f"- Audio: `{settings.get('audio_mode')}`; delivered `{settings.get('output_has_audio')}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path', '')}`",
        f"- Boundary proof: `{(plan.get('boundary_proof') or {}).get('path', '')}`",
        "",
        "## Ordered sources",
        "",
        "| # | Path | Duration | Display | Cadence | Audio |",
        "|---:|---|---:|---|---|---|",
    ]
    for index, (source, normalization) in enumerate(
        zip(plan.get("sources") or [], settings.get("normalizations") or []), start=1
    ):
        lines.append(
            f"| {index} | `{source.get('path')}` | {float(source.get('video_duration') or 0):.3f}s | "
            f"{normalization.get('display_size')} | {normalization.get('decoded_cadence')} | {normalization.get('audio')} |"
        )
    lines.extend(["", "## Boundary proof map", "", "| Boundary | Output time | Proof time | Sources |", "|---|---:|---:|---|"])
    for boundary in settings.get("boundaries") or []:
        lines.append(
            f"| {boundary.get('id')} | {float(boundary.get('output_time_seconds') or 0):.3f}s | "
            f"{float(boundary.get('proof_start_seconds') or 0):.3f}s | "
            f"{boundary.get('before_source_index')} → {boundary.get('after_source_index')} |"
        )
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(
        [
            "",
            "## Review contract",
            "",
            "Play the complete delivery and boundary proof at 1×. Confirm source order and total coverage, then check every hard cut for a freeze, duplicate/missing frame, visual discontinuity, click, pop, gap, or truncated phrase. Geometry/cadence normalization cannot judge editorial continuity.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_optional_markdown(
    value: Optional[str], report: Mapping[str, Any], *, forbidden: Sequence[Path]
) -> None:
    if not value:
        return
    target = Path(value).expanduser().resolve()
    if target in {item.expanduser().resolve() for item in forbidden}:
        raise ValueError("markdown output must not overlap a source, plan, delivery, or proof file")
    if target.is_symlink():
        raise ValueError("markdown output must not be a symlink")
    _atomic_write_text(target, render_markdown(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble heterogeneous video clips through one normalized, source-bound encode."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Bind ordered sources, normalized output, and proof paths.")
    plan.add_argument("sources", nargs="+", help="Two or more project-local video clips in playback order.")
    plan.add_argument("--delivery", required=True, help="Project-local assembled MP4 delivery.")
    plan.add_argument("--boundary-proof", required=True, help="Project-local all-boundary proof MP4.")
    plan.add_argument("--width", type=int)
    plan.add_argument("--height", type=int)
    plan.add_argument("--fps", help="Exact rational or decimal output frame rate; defaults to the first clip.")
    plan.add_argument("--fit", choices=("contain", "crop"), default="contain")
    plan.add_argument("--audio-mode", choices=("fill-silence", "drop"), default="fill-silence")
    plan.add_argument("--proof-context", type=float, default=0.75, help="Seconds before/after each boundary.")
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--output", required=True, help="Plan JSON path.")
    plan.add_argument("--markdown", help="Optional human-readable plan path.")
    plan.add_argument("--force", action="store_true", help="Replace plan/Markdown artifacts only.")
    apply = subparsers.add_parser("apply", help="Render, fully decode, and atomically promote delivery/proof.")
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")
    confirm = subparsers.add_parser("confirm", help="Record full-delivery and all-boundary review.")
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    confirm.add_argument("--proof-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True)
    confirm.add_argument("--markdown")
    verify = subparsers.add_parser("verify", help="Re-probe all sources, outputs, proof, and review.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            output = Path(args.output).expanduser().resolve()
            if output.is_symlink() or (output.exists() and not args.force):
                raise FileExistsError(f"plan output exists or is a symlink; pass --force to replace: {output}")
            report = build_plan(
                args.sources,
                args.delivery,
                args.boundary_proof,
                width=args.width,
                height=args.height,
                fps=args.fps,
                fit=args.fit,
                audio_mode=args.audio_mode.replace("-", "_"),
                proof_context_seconds=args.proof_context,
                project_dir=args.project_dir,
            )
            forbidden = [
                *(Path(str(source["path"])) for source in report["sources"]),
                Path(str(report["delivery"]["path"])),
                Path(str(report["boundary_proof"]["path"])),
            ]
            if output in {item.resolve() for item in forbidden}:
                raise ValueError("plan output must not overlap a source, delivery, or boundary proof")
            if not _inside_project(output, Path(str(report["project_root"]))):
                raise ValueError("plan output must stay inside the project directory")
            _atomic_write_json(output, report)
            _write_optional_markdown(args.markdown, report, forbidden=[output, *forbidden])
        elif args.command == "apply":
            report = apply_plan(args.plan, force=args.force)
            forbidden = [
                Path(args.plan),
                *(Path(str(source["path"])) for source in report["sources"]),
                Path(str(report["delivery"]["path"])),
                Path(str(report["boundary_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        elif args.command == "confirm":
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                proof_playback=args.proof_playback,
                checks={field: getattr(args, field) for field in REVIEW_FIELDS},
            )
            forbidden = [
                Path(args.plan),
                *(Path(str(source["path"])) for source in report["sources"]),
                Path(str(report["delivery"]["path"])),
                Path(str(report["boundary_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        else:
            plan_path = _resolve_plan_file(args.plan)
            report = verify_plan(_load_plan(plan_path))
            forbidden = [
                plan_path,
                *(Path(str(source.get("path") or "")) for source in report.get("sources") or []),
                Path(str((report.get("delivery") or {}).get("path") or "")),
                Path(str((report.get("boundary_proof") or {}).get("path") or "")),
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
        print(f"clip_assembly.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
