#!/usr/bin/env python3
"""Plan, render, review, and verify a source-bound repeated clip.

This tool fills a fixed slot by repeating one progressive CFR source.  It does
not invent a crossfade or claim that arbitrary endpoints are seamless.  The
exact rendered seam is exported as a normal-speed proof clip; a human must
review the proof and full delivery before the plan becomes ready.
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


VERSION = "loop_fill_plan.v1"
PENDING_APPLY = "loop fill has not been rendered and validated"
PENDING_CONFIRM = "loop seam and full delivery review have not been confirmed"
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
REVIEW_FIELDS = (
    "visual_transition",
    "motion_continuity",
    "duplicate_flash",
    "audio_transition",
    "slot_coverage",
)
REVIEW_CHOICES = {"pass", "fail", "unobservable", "not_applicable"}
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_REPEATS = 10000


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
            "frame_rate_conform.py before looping"
        )
    median = float((cadence.get("interval_seconds") or {}).get("median") or 0)
    if median <= 0:
        raise ValueError("source decoded cadence has no usable median frame interval")
    measured = 1.0 / median
    candidates = [source.get("r_frame_rate"), source.get("avg_frame_rate")]
    for candidate in candidates:
        try:
            rate = parse_rate(str(candidate or ""))
        except ValueError:
            continue
        if abs(float(rate["fps"]) - measured) <= 0.01:
            return rate
    return parse_rate(str(round(measured, 6)))


def _validate_source_contract(source: Mapping[str, Any], audio_mode: str) -> None:
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (
        isinstance(bit_depth, int) and bit_depth > 8
    ):
        raise ValueError(
            "HDR/BT.2020/greater-than-8-bit source requires an explicit color workflow; "
            "use hdr_sdr.py for an SDR derivative before loop filling"
        )
    if audio_mode not in {"preserve", "drop"}:
        raise ValueError("audio_mode must be preserve or drop")
    if audio_mode == "preserve" and source.get("has_audio"):
        fps = float(_source_rate(source)["fps"])
        tolerance = max(0.1, 2.0 / fps)
        video_start = float(source.get("video_start_time") or 0)
        audio_start = float(source.get("audio_start_time") or 0)
        video_end = video_start + float(source.get("video_duration") or 0)
        audio_end = audio_start + float(source.get("audio_duration") or 0)
        if abs(video_start - audio_start) > tolerance or abs(video_end - audio_end) > tolerance:
            raise ValueError(
                "source audio/video boundaries differ beyond tolerance; repair or explicitly drop audio before looping"
            )


def _settings_for(
    source: Mapping[str, Any],
    *,
    request_kind: str,
    requested_value: Any,
    audio_mode: str,
    proof_context_seconds: float,
) -> Dict[str, Any]:
    if request_kind not in {"times", "duration"}:
        raise ValueError("request_kind must be times or duration")
    _validate_source_contract(source, audio_mode)
    source_duration = float(source.get("duration") or 0)
    if source_duration <= 0:
        raise ValueError("source has no measurable positive duration")
    if request_kind == "times":
        try:
            numeric_repeats = float(requested_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("times must be an integer") from exc
        if not math.isfinite(numeric_repeats) or not numeric_repeats.is_integer():
            raise ValueError("times must be an integer")
        repeats = int(numeric_repeats)
        if repeats < 2 or repeats > MAX_REPEATS:
            raise ValueError(f"times must be between 2 and {MAX_REPEATS}")
        target_duration = source_duration * repeats
        normalized_requested: Any = repeats
    else:
        target_duration = parse_duration(str(requested_value))
        if target_duration <= source_duration + 0.001:
            raise ValueError(
                f"target duration ({target_duration:g}s) must be longer than the source "
                f"({source_duration:.3f}s)"
            )
        repeats = int(math.ceil((target_duration - 1e-9) / source_duration))
        if repeats > MAX_REPEATS:
            raise ValueError(f"target duration requires more than {MAX_REPEATS} source reads")
        normalized_requested = round(target_duration, 6)
    if target_duration > MAX_DURATION_SECONDS:
        raise ValueError("looped delivery must be at most 24 hours")
    if not math.isfinite(proof_context_seconds) or proof_context_seconds < 0.1:
        raise ValueError("proof context must be at least 0.1 seconds")
    before = min(float(proof_context_seconds), source_duration / 2.0)
    after = min(float(proof_context_seconds), target_duration - source_duration)
    if after < 0.05:
        raise ValueError("target duration leaves too little post-seam material for review")
    rate = _source_rate(source)
    frame_seconds = 1.0 / float(rate["fps"])
    output_has_audio = bool(source.get("has_audio")) and audio_mode == "preserve"
    seam_count = max(1, int(math.ceil((target_duration - 1e-9) / source_duration)) - 1)
    return {
        "request_kind": request_kind,
        "requested_value": normalized_requested,
        "source_duration_seconds": round(source_duration, 6),
        "target_duration_seconds": round(target_duration, 6),
        "source_reads": repeats,
        "stream_loop": repeats - 1,
        "seam_count": seam_count,
        "first_seam_seconds": round(source_duration, 6),
        "partial_final_cycle": abs((target_duration / source_duration) - round(target_duration / source_duration)) > 1e-6,
        "video_rate": rate,
        "video_filter": f"fps=fps={rate['rational']}:start_time=0:round=near,setsar=1,format=yuv420p",
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "pixel_format": "yuv420p",
        "audio_mode": audio_mode,
        "output_has_audio": output_has_audio,
        "audio_filter": "aresample=async=1:first_pts=0,asetpts=N/SR/TB" if output_has_audio else None,
        "audio_encoder": "aac" if output_has_audio else None,
        "audio_bitrate_kbps": 192 if output_has_audio else None,
        "audio_sample_rate": 48000 if output_has_audio else None,
        "proof_context_requested_seconds": round(float(proof_context_seconds), 6),
        "proof_before_seconds": round(before, 6),
        "proof_after_seconds": round(after, 6),
        "proof_duration_seconds": round(before + after, 6),
        "duration_tolerance_seconds": round(max(0.1, frame_seconds * 2), 6),
        "stream_start_tolerance_seconds": round(max(0.02, frame_seconds), 6),
        "av_end_tolerance_seconds": round(max(0.1, frame_seconds * 2), 6),
    }


def _review_contract(output_has_audio: bool) -> Dict[str, Any]:
    return {
        "playback": "Watch the complete delivery and the seam proof at normal speed with audio when present.",
        "checks": list(REVIEW_FIELDS),
        "audio_transition_expected": bool(output_has_audio),
        "pass_rule": (
            "full_playback=completed, seam_playback=completed, every visual check=pass, and "
            "audio_transition=pass when audio is preserved or not_applicable when no audio is delivered"
        ),
        "limitations": [
            "The renderer repeats the exact source and does not add a crossfade or synthesize missing motion.",
            "A proof around the first seam represents identical source-to-source joins; the full output still needs review.",
            "A fixed-duration fill may end partway through the last source cycle and is not an endlessly looping master.",
        ],
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_root": plan.get("project_root"),
        "source": plan.get("source"),
        "settings": plan.get("settings"),
        "delivery": plan.get("delivery"),
        "seam_proof": plan.get("seam_proof"),
        "application": plan.get("application"),
        "review": plan.get("review"),
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
    media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any]
) -> List[str]:
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append("looped delivery is not an MP4-family container")
    if media.get("video_codec") != "h264" or media.get("pixel_format") != "yuv420p":
        blockers.append("looped delivery must use H.264 yuv420p")
    if media.get("rotation") != 0:
        blockers.append("looped delivery must bake display rotation and clear rotation metadata")
    if media.get("width") != source.get("width") or media.get("height") != source.get("height"):
        blockers.append("looped delivery displayed dimensions do not match the source")
    target_rate = float((settings.get("video_rate") or {}).get("fps") or 0)
    if abs(float(media.get("avg_fps") or 0) - target_rate) > 0.001:
        blockers.append("looped delivery average fps does not match the source CFR contract")
    cadence = media.get("cadence") if isinstance(media.get("cadence"), Mapping) else {}
    if cadence.get("algorithm") != CADENCE_ALGORITHM or cadence.get("is_variable"):
        blockers.append("looped delivery decoded cadence is not constant under the planned algorithm")
    tolerance = float(settings.get("duration_tolerance_seconds") or 0)
    target_duration = float(settings.get("target_duration_seconds") or 0)
    if abs(float(media.get("video_duration") or 0) - target_duration) > tolerance:
        blockers.append("looped delivery video duration misses the requested slot")
    expected_audio = bool(settings.get("output_has_audio"))
    if bool(media.get("has_audio")) != expected_audio:
        blockers.append("looped delivery audio presence does not match the plan")
    if expected_audio:
        if media.get("audio_codec") != "aac" or media.get("sample_rate") != 48000:
            blockers.append("looped delivery audio must use 48 kHz AAC")
        video_start = float(media.get("video_start_time") or 0)
        audio_start = float(media.get("audio_start_time") or 0)
        start_tolerance = float(settings.get("stream_start_tolerance_seconds") or 0)
        if abs(video_start) > start_tolerance or abs(audio_start) > start_tolerance:
            blockers.append("looped delivery streams do not start near zero")
        if abs(video_start - audio_start) > start_tolerance:
            blockers.append("looped delivery audio/video start times differ beyond tolerance")
        video_end = video_start + float(media.get("video_duration") or 0)
        audio_end = audio_start + float(media.get("audio_duration") or 0)
        if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
            blockers.append("looped delivery audio/video end times differ beyond tolerance")
    return blockers


def _proof_contract_blockers(
    media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any]
) -> List[str]:
    blockers = _output_contract_blockers(
        media,
        source,
        {**settings, "target_duration_seconds": settings.get("proof_duration_seconds")},
    )
    return [item.replace("looped delivery", "seam proof") for item in blockers]


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


def _review_blockers(plan: Mapping[str, Any]) -> List[str]:
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    blockers: List[str] = []
    if not review:
        return [PENDING_CONFIRM]
    if not str(review.get("reviewed_by") or "").strip() or not str(review.get("note") or "").strip():
        blockers.append("reviewed_by and a non-empty review note are required")
    if review.get("full_playback") != "completed":
        blockers.append("complete normal-speed delivery playback is required")
    if review.get("seam_playback") != "completed":
        blockers.append("complete normal-speed seam-proof playback is required")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    expected_audio = bool(settings.get("output_has_audio"))
    for field in REVIEW_FIELDS:
        value = checks.get(field)
        expected = "pass"
        if field == "audio_transition" and not expected_audio:
            expected = "not_applicable"
        if value != expected:
            blockers.append(f"review check {field} must be {expected}, got {value or 'missing'}")
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
    proof = application.get("seam_proof") if isinstance(application.get("seam_proof"), Mapping) else {}
    if review.get("output_sha256") != output.get("sha256"):
        blockers.append("review is not bound to the current looped delivery sha256")
    if review.get("seam_proof_sha256") != proof.get("sha256"):
        blockers.append("review is not bound to the current seam-proof sha256")
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    warnings: List[str] = []
    if float(settings.get("proof_after_seconds") or 0) < 0.25:
        warnings.append("The requested slot leaves less than 0.25 seconds after the first seam in the proof.")
    return warnings


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
            live_source = _media_info(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source media/cadence probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source fingerprint, media contract, or decoded cadence changed after planning")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        expected_settings = _settings_for(
            source,
            request_kind=str(settings.get("request_kind") or ""),
            requested_value=settings.get("requested_value"),
            audio_mode=str(settings.get("audio_mode") or ""),
            proof_context_seconds=float(settings.get("proof_context_requested_seconds") or 0),
        )
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
        expected_settings = settings
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical loop-fill contract")

    paths: Dict[str, Path] = {}
    expected_records = {
        "delivery": {"format": "mp4", "purpose": "fixed_duration_repeated_clip"},
        "seam_proof": {"format": "mp4", "purpose": "first_repeat_seam_review"},
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
    if paths.get("delivery") == paths.get("seam_proof"):
        blockers.append("delivery and seam_proof paths must be different")
    if source.get("path"):
        for key, candidate in paths.items():
            if candidate.is_absolute() and candidate.resolve() == Path(str(source.get("path"))).resolve():
                blockers.append(f"{key} must not overwrite the source")
    expected_review_contract = _review_contract(bool(settings.get("output_has_audio")))
    if plan.get("review_contract") != expected_review_contract:
        blockers.append("review_contract is stale or modified")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        proof = application.get("seam_proof") if isinstance(application.get("seam_proof"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        proof_path = _validate_live_file(proof, "application.seam_proof", blockers)
        if output.get("path") != str(paths.get("delivery") or ""):
            blockers.append("application.output.path does not match delivery.path")
        if proof.get("path") != str(paths.get("seam_proof") or ""):
            blockers.append("application.seam_proof.path does not match seam_proof.path")
        if output_path is not None:
            try:
                live_output = _media_info(output_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"looped delivery probe failed: {exc}")
            else:
                if output != live_output:
                    blockers.append("stored looped-delivery contract is stale or modified")
                blockers.extend(_output_contract_blockers(live_output, source, settings))
        if proof_path is not None:
            try:
                live_proof = _media_info(proof_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"seam-proof probe failed: {exc}")
            else:
                if proof != live_proof:
                    blockers.append("stored seam-proof contract is stale or modified")
                blockers.extend(_proof_contract_blockers(live_proof, source, settings))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_output_decode = _decode_command(paths["delivery"]) if paths.get("delivery", Path()).is_absolute() else []
        expected_proof_decode = _decode_command(paths["seam_proof"]) if paths.get("seam_proof", Path()).is_absolute() else []
        if validation.get("output_decode_checked") is not True or validation.get("output_decode_command") != expected_output_decode:
            blockers.append("full looped-delivery decode validation is missing or stale")
        if validation.get("proof_decode_checked") is not True or validation.get("proof_decode_command") != expected_proof_decode:
            blockers.append("full seam-proof decode validation is missing or stale")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("output decode validation is not bound to the current delivery sha256")
        if validation.get("seam_proof_sha256") != proof.get("sha256"):
            blockers.append("proof decode validation is not bound to the current seam-proof sha256")
        if validation.get("first_seam_seconds") != settings.get("first_seam_seconds"):
            blockers.append("validated seam position is stale or modified")
        blockers.extend(_review_blockers(plan))

    warnings = _computed_warnings(plan)
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    summary = {
        "request_kind": settings.get("request_kind"),
        "target_duration_seconds": settings.get("target_duration_seconds"),
        "source_reads": int(settings.get("source_reads") or 0),
        "seam_count": int(settings.get("seam_count") or 0),
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
    source_path: str,
    delivery_path: str,
    seam_proof_path: str,
    *,
    times: Optional[int] = None,
    duration: Optional[str] = None,
    audio_mode: str = "preserve",
    proof_context_seconds: float = 1.0,
    project_dir: str = ".",
) -> Dict[str, Any]:
    if (times is None) == (duration is None):
        raise ValueError("provide exactly one of times or duration")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source_path_resolved = _resolve_project_path(
        source_path, root, label="source", must_exist=True
    )
    delivery = _resolve_project_path(
        delivery_path, root, label="delivery", must_exist=False, suffix=".mp4"
    )
    seam_proof = _resolve_project_path(
        seam_proof_path, root, label="seam proof", must_exist=False, suffix=".mp4"
    )
    if len({source_path_resolved, delivery, seam_proof}) != 3:
        raise ValueError("source, delivery, and seam proof paths must all be different")
    source = _media_info(source_path_resolved)
    settings = _settings_for(
        source,
        request_kind="times" if times is not None else "duration",
        requested_value=times if times is not None else duration,
        audio_mode=audio_mode,
        proof_context_seconds=proof_context_seconds,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "source": source,
        "settings": settings,
        "delivery": {
            "path": str(delivery),
            "format": "mp4",
            "purpose": "fixed_duration_repeated_clip",
        },
        "seam_proof": {
            "path": str(seam_proof),
            "format": "mp4",
            "purpose": "first_repeat_seam_review",
        },
        "application": None,
        "review": None,
        "review_contract": _review_contract(bool(settings["output_has_audio"])),
    }
    return _set_derived(plan)


def build_command(plan: Mapping[str, Any], output_path: Path) -> List[str]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") or {}
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-stream_loop",
        str(settings.get("stream_loop")),
        "-i",
        str(source),
        "-t",
        f"{float(settings.get('target_duration_seconds') or 0):.6f}",
        "-map",
        "0:v:0",
        "-vf",
        str(settings.get("video_filter") or ""),
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
    ]
    if settings.get("output_has_audio"):
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
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(output_path)])
    return command


def _proof_command(plan: Mapping[str, Any], rendered_path: Path, proof_path: Path) -> List[str]:
    settings = plan.get("settings") or {}
    start = float(settings.get("first_seam_seconds") or 0) - float(
        settings.get("proof_before_seconds") or 0
    )
    duration = float(settings.get("proof_duration_seconds") or 0)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(rendered_path),
        "-ss",
        f"{max(0.0, start):.6f}",
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
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
    ]
    if settings.get("output_has_audio"):
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(proof_path)])
    return command


def _temporary_output(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp.mp4", dir=str(target.parent))
    os.close(fd)
    return Path(name)


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read loop-fill plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("loop-fill plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("loop-fill plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"loop-fill plan must be an existing JSON file: {resolved}")
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
        raise ValueError("loop-fill plan must stay inside the project directory")
    source = Path(str(plan["source"]["path"]))
    delivery = Path(str(plan["delivery"]["path"]))
    seam_proof = Path(str(plan["seam_proof"]["path"]))
    for label, target in (("delivery", delivery), ("seam proof", seam_proof)):
        if target.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
        if target.exists() and not force:
            raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    source_before = _fingerprint(source)
    temporary_delivery = _temporary_output(delivery)
    temporary_proof = _temporary_output(seam_proof)
    try:
        _run_checked(build_command(plan, temporary_delivery), "loop-fill render")
        temporary_output_info = _media_info(temporary_delivery)
        output_blockers = _output_contract_blockers(
            temporary_output_info, plan["source"], plan["settings"]
        )
        if output_blockers:
            raise RuntimeError("output validation failed: " + "; ".join(output_blockers))
        _run_checked(_decode_command(temporary_delivery), "full looped-delivery decode")
        _run_checked(
            _proof_command(plan, temporary_delivery, temporary_proof), "loop-seam proof render"
        )
        temporary_proof_info = _media_info(temporary_proof)
        proof_blockers = _proof_contract_blockers(
            temporary_proof_info, plan["source"], plan["settings"]
        )
        if proof_blockers:
            raise RuntimeError("seam-proof validation failed: " + "; ".join(proof_blockers))
        _run_checked(_decode_command(temporary_proof), "full loop-seam proof decode")
        if _fingerprint(source) != source_before:
            raise RuntimeError("source changed during loop rendering; outputs were not promoted")
        os.replace(temporary_delivery, delivery)
        os.replace(temporary_proof, seam_proof)
    finally:
        for temporary in (temporary_delivery, temporary_proof):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output = _media_info(delivery)
    proof = _media_info(seam_proof)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "seam_proof": proof,
        "validation": {
            "validated_at": utc_now(),
            "output_decode_checked": True,
            "output_decode_command": _decode_command(delivery),
            "proof_decode_checked": True,
            "proof_decode_command": _decode_command(seam_proof),
            "output_sha256": output["sha256"],
            "seam_proof_sha256": proof["sha256"],
            "first_seam_seconds": plan["settings"]["first_seam_seconds"],
            "cadence_algorithm": CADENCE_ALGORITHM,
        },
    }
    plan["review"] = None
    _set_derived(plan)
    final = verify_plan(plan)
    substantive = [item for item in final.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise RuntimeError("applied loop-fill plan failed final verification: " + "; ".join(substantive))
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    seam_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the loop-fill plan before confirming it")
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
    if seam_playback not in {"completed", "not_completed"}:
        raise ValueError("seam_playback must be completed or not_completed")
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    application = plan["application"]
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "full_playback": full_playback,
        "seam_playback": seam_playback,
        "checks": normalized,
        "output_sha256": application["output"]["sha256"],
        "seam_proof_sha256": application["seam_proof"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    lines = [
        "# Loop Fill Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Source / target duration: `{settings.get('source_duration_seconds')}` / `{settings.get('target_duration_seconds')}` seconds",
        f"- Source reads / internal seams: `{settings.get('source_reads')}` / `{settings.get('seam_count')}`",
        f"- Audio mode: `{settings.get('audio_mode')}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path', '')}`",
        f"- First-seam proof: `{(plan.get('seam_proof') or {}).get('path', '')}`",
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
            "Play the seam proof and full delivery at 1×. Confirm the picture does not jump, flash, duplicate a frame, or break motion continuity. When audio is preserved, listen for a click, pop, gap, or repeated phrase at the seam. A hard-repeat render does not make arbitrary endpoints seamless.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_optional_markdown(
    path: Optional[str], report: Mapping[str, Any], *, forbidden: Sequence[Path]
) -> None:
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
        description="Fill a fixed slot with a repeated CFR clip and require source-bound seam review."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Bind the source, repeat target, output, and seam proof.")
    plan.add_argument("source", help="Project-local progressive CFR source video.")
    target = plan.add_mutually_exclusive_group(required=True)
    target.add_argument("--times", type=int, help="Total number of complete source reads; minimum 2.")
    target.add_argument("--duration", help="Exact target duration in seconds, mm:ss, or hh:mm:ss.")
    plan.add_argument("--audio-mode", choices=("preserve", "drop"), default="preserve")
    plan.add_argument("--proof-context", type=float, default=1.0, help="Seconds before/after the first seam.")
    plan.add_argument("--delivery", required=True, help="Project-local repeated MP4 delivery.")
    plan.add_argument("--seam-proof", required=True, help="Project-local normal-speed first-seam proof MP4.")
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--output", required=True, help="Plan JSON path.")
    plan.add_argument("--markdown", help="Optional human-readable plan path.")
    plan.add_argument("--force", action="store_true", help="Replace plan/Markdown artifacts only.")

    apply = subparsers.add_parser(
        "apply", help="Render, fully decode, probe, and atomically promote delivery and proof."
    )
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser(
        "confirm",
        help="Record normal-speed full-delivery and seam-proof review.",
        description="Record normal-speed full-delivery and seam-proof review.",
    )
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    confirm.add_argument("--seam-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(
            f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True
        )
    confirm.add_argument("--markdown")

    verify = subparsers.add_parser(
        "verify", help="Re-probe source, output, proof, review, and reject drift."
    )
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
                args.seam_proof,
                times=args.times,
                duration=args.duration,
                audio_mode=args.audio_mode,
                proof_context_seconds=args.proof_context,
                project_dir=args.project_dir,
            )
            forbidden = [
                Path(str(report["source"]["path"])),
                Path(str(report["delivery"]["path"])),
                Path(str(report["seam_proof"]["path"])),
            ]
            if output in {item.resolve() for item in forbidden}:
                raise ValueError("plan output must not overlap source, delivery, or seam proof")
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
                Path(str(report["seam_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        elif args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                seam_playback=args.seam_playback,
                checks=checks,
            )
            forbidden = [
                Path(args.plan),
                Path(str(report["source"]["path"])),
                Path(str(report["delivery"]["path"])),
                Path(str(report["seam_proof"]["path"])),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        else:
            plan_path = _resolve_plan_file(args.plan)
            report = verify_plan(_load_plan(plan_path))
            forbidden = [
                plan_path,
                Path(str((report.get("source") or {}).get("path") or "")),
                Path(str((report.get("delivery") or {}).get("path") or "")),
                Path(str((report.get("seam_proof") or {}).get("path") or "")),
            ]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "plan_id": report.get("plan_id"),
                    "summary": report["summary"],
                },
                ensure_ascii=False,
            )
        )
        return 2 if getattr(args, "strict", False) and report["summary"]["blocking"] else 0
    except (FileExistsError, OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"loop_fill.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
