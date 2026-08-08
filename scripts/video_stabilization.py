#!/usr/bin/env python3
"""Plan, apply, and review source-bound video stabilization.

The workflow is deliberately explicit:

1. ``plan`` records the source hash, exact FFmpeg backend, and a human decision.
2. ``apply`` renders a new working copy plus a full-length side-by-side comparison.
3. ``confirm`` records that the comparison was watched before the plan becomes ready.

The original source is never modified. High-quality two-pass ``vidstab`` is preferred
when the local FFmpeg build provides it; otherwise ``deshake`` is an explicit,
lower-quality fallback recorded in the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


VERSION = "video_stabilization.v1"
BACKENDS = {"auto", "vidstab", "deshake"}
PROFILES = {"conservative", "balanced", "strong"}
DECISIONS = {"review", "stabilize", "keep"}
PENDING_APPLY = "approved stabilization has not been applied"
PENDING_REVIEW = "stabilized output still needs full-length comparison review"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


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
    duration = _fraction(data.get("format", {}).get("duration"))
    if duration is None:
        duration = _fraction(video.get("duration"))
    fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if duration is None or duration <= 0 or fps is None or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {path}")
    return {
        "duration": round(duration, 6),
        "fps": round(fps, 6),
        "width": width,
        "height": height,
        "has_audio": audio is not None,
    }


def _source_info(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path)}


def _available_filters() -> Set[str]:
    result = _run_command(["ffmpeg", "-hide_banner", "-filters"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not list FFmpeg filters")
    text = f"{result.stdout}\n{result.stderr}"
    names: Set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in {"T", ".", "S"}:
            names.add(parts[1])
    return names


def select_backend(requested: str, filters: Set[str]) -> str:
    if requested not in BACKENDS:
        raise ValueError(f"backend must be one of: {', '.join(sorted(BACKENDS))}")
    has_vidstab = {"vidstabdetect", "vidstabtransform"}.issubset(filters)
    has_deshake = "deshake" in filters
    if requested == "auto":
        if has_vidstab:
            return "vidstab"
        if has_deshake:
            return "deshake"
        raise ValueError("FFmpeg provides neither vidstabdetect/vidstabtransform nor deshake")
    if requested == "vidstab" and not has_vidstab:
        raise ValueError("requested vidstab backend is unavailable in this FFmpeg build")
    if requested == "deshake" and not has_deshake:
        raise ValueError("requested deshake backend is unavailable in this FFmpeg build")
    return requested


def _settings_for(backend: str, profile: str, fps: float) -> Dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of: {', '.join(sorted(PROFILES))}")
    if backend == "vidstab":
        profile_values = {
            "conservative": (4, 0.5),
            "balanced": (6, 1.0),
            "strong": (8, 2.0),
        }
        shakiness, smoothing_seconds = profile_values[profile]
        return {
            "profile": profile,
            "shakiness": shakiness,
            "accuracy": 15,
            "smoothing_frames": max(1, round(fps * smoothing_seconds)),
            "optzoom": 1,
            "crop": "black",
            "sharpen": 0.3,
            "crf": 18,
            "preset": "medium",
        }
    if backend == "deshake":
        radius = {"conservative": 8, "balanced": 16, "strong": 32}[profile]
        return {
            "profile": profile,
            "rx": radius,
            "ry": radius,
            "edge": "mirror",
            "blocksize": 8,
            "contrast": 125,
            "search": "exhaustive",
            "crf": 18,
            "preset": "medium",
        }
    raise ValueError(f"unsupported backend: {backend}")


def _backend_record(name: str) -> Dict[str, Any]:
    if name == "vidstab":
        return {
            "name": name,
            "required_filters": ["vidstabdetect", "vidstabtransform"],
            "quality": "two_pass_motion_path_smoothing",
        }
    return {
        "name": name,
        "required_filters": ["deshake"],
        "quality": "single_pass_fallback",
    }


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    backend = plan.get("backend") if isinstance(plan.get("backend"), Mapping) else {}
    if backend.get("name") == "deshake":
        return [
            "Using FFmpeg deshake single-pass fallback because two-pass vidstab was not selected; "
            "inspect the entire A/B comparison for residual shake and edge distortion."
        ]
    return []


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "backend": plan.get("backend"),
        "settings": plan.get("settings"),
        "decision": plan.get("decision"),
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


def _media_matches_source(
    media: Mapping[str, Any], source: Mapping[str, Any], label: str, blockers: List[str]
) -> None:
    for field in ("width", "height", "has_audio"):
        if media.get(field) != source.get(field):
            blockers.append(f"{label}.{field} must match source.{field}")
    duration = _fraction(media.get("duration"))
    source_duration = _fraction(source.get("duration"))
    fps = _fraction(source.get("fps")) or 30.0
    tolerance = max(0.1, 3.0 / fps)
    if duration is None or source_duration is None or abs(duration - source_duration) > tolerance:
        blockers.append(f"{label}.duration must match source duration within {tolerance:.3f}s")


def _compute_derived(plan: Mapping[str, Any], filters: Set[str]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")

    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    if source_path is not None:
        try:
            live_source = probe_media(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source probe failed: {exc}")
        else:
            for field in ("duration", "fps", "width", "height", "has_audio"):
                expected = source.get(field)
                actual = live_source.get(field)
                if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                    if abs(float(expected) - float(actual)) > 1e-4:
                        blockers.append(f"source.{field} changed after planning")
                elif expected != actual:
                    blockers.append(f"source.{field} changed after planning")

    backend = plan.get("backend") if isinstance(plan.get("backend"), Mapping) else {}
    backend_name = str(backend.get("name") or "")
    if backend_name not in {"vidstab", "deshake"} or backend != _backend_record(backend_name):
        blockers.append("backend record is not canonical")
    for required in backend.get("required_filters") or []:
        if required not in filters:
            blockers.append(f"required FFmpeg filter is unavailable: {required}")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    fps = _fraction(source.get("fps"))
    if backend_name in {"vidstab", "deshake"} and fps:
        try:
            expected_settings = _settings_for(backend_name, str(settings.get("profile") or ""), fps)
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            if settings != expected_settings:
                blockers.append("settings do not match the selected backend/profile")
    else:
        blockers.append("backend or source fps is invalid")

    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    decision_value = str(decision.get("value") or "")
    if decision_value not in DECISIONS:
        blockers.append(f"decision.value must be one of: {', '.join(sorted(DECISIONS))}")
    reviewer = str(decision.get("reviewed_by_label") or "").strip()
    if decision_value in {"stabilize", "keep"} and not reviewer:
        blockers.append("an explicit stabilize/keep decision requires reviewed_by_label")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    confirmed = False
    if decision_value == "review":
        blockers.append("stabilization decision still needs review")
        if application is not None:
            blockers.append("application must be null while decision is review")
    elif decision_value == "keep":
        if application is not None:
            blockers.append("application must be null when decision is keep")
    elif decision_value == "stabilize":
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
            output_path = _validate_live_file(output, "application.output", blockers)
            _validate_live_file(comparison, "application.comparison", blockers)
            if output_path is not None:
                try:
                    live_output = probe_media(output_path)
                except (RuntimeError, ValueError) as exc:
                    blockers.append(f"application.output probe failed: {exc}")
                else:
                    for field, value in live_output.items():
                        if output.get(field) != value:
                            blockers.append(f"application.output.{field} changed")
                    _media_matches_source(output, source, "application.output", blockers)
            if application.get("backend") != backend_name:
                blockers.append("application.backend does not match planned backend")
            if application.get("filter") != build_filter(backend_name, settings, "<transforms>"):
                blockers.append("application.filter does not match planned settings")
            review = application.get("review") if isinstance(application.get("review"), Mapping) else {}
            confirmed = review.get("status") == "approved"
            if not confirmed:
                blockers.append(PENDING_REVIEW)
            elif not str(review.get("reviewed_by_label") or "").strip() or not str(
                review.get("note") or ""
            ).strip():
                blockers.append("approved comparison review requires reviewer label and note")

    warnings = _computed_warnings(plan)
    summary = {
        "backend": backend_name,
        "profile": settings.get("profile"),
        "decision": decision_value,
        "applied": applied,
        "comparison_reviewed": confirmed,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else "warn" if warnings else "ready"
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def _set_derived(plan: Dict[str, Any], filters: Set[str]) -> Dict[str, Any]:
    derived = _compute_derived(plan, filters)
    plan.update(derived)
    plan["plan_id"] = _plan_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any], filters: Optional[Set[str]] = None) -> Dict[str, Any]:
    available = filters if filters is not None else _available_filters()
    result = dict(plan)
    integrity_blockers: List[str] = []
    if plan.get("plan_id") != _plan_id(plan):
        integrity_blockers.append("plan_id does not match canonical plan content")
    derived = _compute_derived(plan, available)
    if plan.get("blockers") != derived["blockers"]:
        integrity_blockers.append("stored blockers are stale or were modified")
    if plan.get("warnings") != derived["warnings"]:
        integrity_blockers.append("stored warnings are stale or were modified")
    if plan.get("summary") != derived["summary"]:
        integrity_blockers.append("stored summary is stale or was modified")
    if plan.get("status") != derived["status"]:
        integrity_blockers.append("stored status is stale or was modified")
    result.update(derived)
    result["blockers"] = integrity_blockers + list(derived["blockers"])
    result["summary"] = {**derived["summary"], "blocking": len(result["blockers"])}
    result["status"] = "blocked" if result["blockers"] else derived["status"]
    return result


def build_plan(
    source_path: str,
    *,
    backend: str = "auto",
    profile: str = "balanced",
    decision: str = "review",
    reviewed_by_label: str = "",
    note: str = "",
    filters: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    candidate = Path(source_path).expanduser()
    if candidate.is_symlink():
        raise ValueError("source must not be a symlink")
    source = candidate.resolve()
    if not source.is_file():
        raise ValueError(f"source video does not exist: {source}")
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(DECISIONS))}")
    reviewer = reviewed_by_label.strip()
    if decision in {"stabilize", "keep"} and not reviewer:
        raise ValueError("--reviewed-by is required for stabilize/keep decisions")
    available = filters if filters is not None else _available_filters()
    selected_backend = select_backend(backend, available)
    source_info = _source_info(source)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": source_info,
        "backend": _backend_record(selected_backend),
        "settings": _settings_for(selected_backend, profile, float(source_info["fps"])),
        "decision": {
            "value": decision,
            "reviewed_by_label": reviewer,
            "note": note.strip(),
        },
        "application": None,
        "review_contract": {
            "required_for_stabilize": True,
            "instructions": [
                "Watch the full-length side-by-side comparison at normal speed.",
                "Check faces, straight lines, and frame edges for warping or mirrored-edge artifacts.",
                "Reject stabilization that makes an intentional pan floaty or changes composition unacceptably.",
                "Use the stabilized working copy downstream; never replace the original source.",
            ],
            "limitations": [
                "A reviewer label is not identity authentication or a digital signature.",
                "FFmpeg stabilization cannot repair rolling-shutter wobble or motion blur.",
            ],
        },
    }
    return _set_derived(plan, available)


def _filter_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")


def build_filter(backend: str, settings: Mapping[str, Any], transforms: str) -> str:
    if backend == "vidstab":
        result = (
            "vidstabtransform="
            f"input={transforms}:smoothing={settings['smoothing_frames']}:"
            f"optzoom={settings['optzoom']}:crop={settings['crop']}"
        )
        sharpen = float(settings.get("sharpen") or 0)
        if sharpen > 0:
            result += f",unsharp=5:5:{sharpen:.3f}:3:3:0"
        return result
    if backend == "deshake":
        return (
            "deshake="
            f"rx={settings['rx']}:ry={settings['ry']}:edge={settings['edge']}:"
            f"blocksize={settings['blocksize']}:contrast={settings['contrast']}:"
            f"search={settings['search']}"
        )
    raise ValueError(f"unsupported backend: {backend}")


def _encode_command(source: Path, output: Path, video_filter: str, settings: Mapping[str, Any]) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        str(settings["preset"]),
        "-crf",
        str(settings["crf"]),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _comparison_command(source: Path, stabilized: Path, output: Path) -> List[str]:
    graph = (
        "[0:v]scale=-2:720:force_original_aspect_ratio=decrease,setsar=1[left];"
        "[1:v]scale=-2:720:force_original_aspect_ratio=decrease,setsar=1[right];"
        "[left][right]hstack=inputs=2[out]"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-i",
        str(stabilized),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run_command(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{label} failed: {detail[-3000:]}")


def _safe_output(path: str, *, force: bool, forbidden: Set[Path]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.suffix.lower() != ".mp4":
        raise ValueError("stabilized output and comparison must use .mp4")
    if candidate.is_symlink():
        raise ValueError(f"output must not be a symlink: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve()
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


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read stabilization plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("stabilization plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("stabilization plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"stabilization plan must be an existing JSON file: {resolved}")
    return resolved


def apply_plan(
    plan_path: str,
    output_path: str,
    comparison_path: str,
    *,
    force: bool = False,
    filters: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    available = filters if filters is not None else _available_filters()
    verification = verify_plan(plan, available)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))

    source = Path(str(plan["source"]["path"]))
    forbidden = {source.resolve(), plan_file}
    output = _safe_output(output_path, force=force, forbidden=forbidden)
    forbidden.add(output)
    comparison = _safe_output(comparison_path, force=force, forbidden=forbidden)
    backend = str(plan["backend"]["name"])
    settings = plan["settings"]
    temp_output = _temp_mp4(output)
    temp_comparison = _temp_mp4(comparison)
    filter_record = build_filter(backend, settings, "<transforms>")

    try:
        if backend == "vidstab":
            with tempfile.TemporaryDirectory(prefix=".video-stabilization-", dir=str(output.parent)) as temp_dir:
                transforms = Path(temp_dir) / "transforms.trf"
                detect = (
                    "vidstabdetect="
                    f"shakiness={settings['shakiness']}:accuracy={settings['accuracy']}:"
                    f"result={_filter_escape(transforms)}"
                )
                _run_checked(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(source),
                        "-vf",
                        detect,
                        "-an",
                        "-f",
                        "null",
                        "-",
                    ],
                    "vidstab motion analysis",
                )
                if not transforms.is_file() or transforms.stat().st_size == 0:
                    raise RuntimeError("vidstab motion analysis did not create transforms")
                live_filter = build_filter(backend, settings, _filter_escape(transforms))
                _run_checked(_encode_command(source, temp_output, live_filter, settings), "stabilization render")
        else:
            live_filter = build_filter(backend, settings, "")
            _run_checked(_encode_command(source, temp_output, live_filter, settings), "stabilization render")

        output_media = {**_fingerprint(temp_output), **probe_media(temp_output)}
        media_blockers: List[str] = []
        _media_matches_source(output_media, plan["source"], "rendered output", media_blockers)
        if media_blockers:
            raise RuntimeError("; ".join(media_blockers))

        _run_checked(
            _comparison_command(source, temp_output, temp_comparison),
            "side-by-side comparison render",
        )
        comparison_record = _fingerprint(temp_comparison)
        os.replace(temp_output, output)
        os.replace(temp_comparison, comparison)
    finally:
        for temporary in (temp_output, temp_comparison):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    plan["application"] = {
        "backend": backend,
        "filter": filter_record,
        "applied_at": utc_now(),
        "output": {**output_media, **_fingerprint(output), "path": str(output)},
        "comparison": {**comparison_record, **_fingerprint(comparison), "path": str(comparison)},
        "review": {
            "status": "pending",
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
    reviewed_by_label: str,
    note: str,
    filters: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    reviewer = reviewed_by_label.strip()
    review_note = note.strip()
    if not reviewer or not review_note:
        raise ValueError("confirm requires non-empty --reviewed-by and --note")
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    available = filters if filters is not None else _available_filters()
    verification = verify_plan(plan, available)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_REVIEW]:
        raise ValueError("plan is not awaiting comparison review: " + "; ".join(blockers or ["already confirmed"]))
    application = plan.get("application")
    assert isinstance(application, dict)
    application["review"] = {
        "status": "approved",
        "reviewed_by_label": reviewer,
        "note": review_note,
        "reviewed_at": utc_now(),
    }
    _set_derived(plan, available)
    _atomic_write_json(plan_file, plan)
    return plan


def format_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    backend = plan.get("backend") if isinstance(plan.get("backend"), Mapping) else {}
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else None
    lines = [
        "# Video Stabilization Plan",
        "",
        f"- Status: **{str(plan.get('status') or 'unknown').upper()}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Backend: `{backend.get('name', '')}` ({backend.get('quality', '')})",
        f"- Profile: `{(plan.get('settings') or {}).get('profile', '')}`",
        f"- Decision: `{decision.get('value', '')}`",
        f"- Reviewer label: `{decision.get('reviewed_by_label', '')}`",
        "",
        "## Gate",
        "",
    ]
    blockers = plan.get("blockers") or []
    warnings = plan.get("warnings") or []
    if blockers:
        lines.extend(f"- BLOCK: {item}" for item in blockers)
    else:
        lines.append("- No blocking items.")
    lines.append("")
    if warnings:
        lines.extend(["## Warnings", "", *[f"- {item}" for item in warnings], ""])
    if application:
        lines.extend(
            [
                "## Application",
                "",
                f"- Stabilized working copy: `{(application.get('output') or {}).get('path', '')}`",
                f"- Side-by-side comparison: `{(application.get('comparison') or {}).get('path', '')}`",
                f"- Comparison review: `{(application.get('review') or {}).get('status', 'pending')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Required Review",
            "",
            *[f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions", [])],
            "",
            "The reviewer label is a workflow annotation, not authentication or a digital signature.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_destination(path: str, plan: Mapping[str, Any]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("Markdown output must not be a symlink")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".md":
        raise ValueError("Markdown output must use .md")
    forbidden = {Path(str((plan.get("source") or {}).get("path") or "")).resolve()}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    for key in ("output", "comparison"):
        record = application.get(key) if isinstance(application.get(key), Mapping) else {}
        if record.get("path"):
            forbidden.add(Path(str(record["path"])).resolve())
    if resolved in forbidden:
        raise ValueError("Markdown output must not overwrite source or rendered media")
    return resolved


def _write_markdown(
    path: Optional[str], plan: Mapping[str, Any], *, allow_existing: bool = True
) -> None:
    if not path:
        return
    output = _markdown_destination(path, plan)
    if output.exists() and not allow_existing:
        raise ValueError(f"Markdown output already exists (pass --force to replace): {output}")
    _atomic_write_text(output, format_markdown(plan))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and apply source-bound video stabilization.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Report available FFmpeg stabilization filters")
    doctor.add_argument("--json", action="store_true", help="Print JSON only")

    plan = subparsers.add_parser("plan", help="Create a source-bound stabilization decision plan")
    plan.add_argument("source")
    plan.add_argument("--backend", choices=sorted(BACKENDS), default="auto")
    plan.add_argument("--profile", choices=sorted(PROFILES), default="balanced")
    plan.add_argument("--decision", choices=sorted(DECISIONS), default="review")
    plan.add_argument("--reviewed-by", default="")
    plan.add_argument("--note", default="")
    plan.add_argument("--output", required=True)
    plan.add_argument("--markdown")
    plan.add_argument("--strict", action="store_true")
    plan.add_argument("--force", action="store_true", help="Replace an existing plan/report")

    verify = subparsers.add_parser("verify", help="Live-verify a plan, source, and any outputs")
    verify.add_argument("plan")
    verify.add_argument("--strict", action="store_true")

    apply = subparsers.add_parser("apply", help="Render a working copy and side-by-side comparison")
    apply.add_argument("plan")
    apply.add_argument("--output", required=True)
    apply.add_argument("--comparison", required=True)
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser("confirm", help="Confirm the full-length A/B comparison review")
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--markdown")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            filters = _available_filters()
            payload = {
                "ffmpeg": shutil.which("ffmpeg"),
                "vidstab": {"available": {"vidstabdetect", "vidstabtransform"}.issubset(filters)},
                "deshake": {"available": "deshake" in filters},
            }
            try:
                payload["selected_backend"] = select_backend("auto", filters)
            except ValueError:
                payload["selected_backend"] = None
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"FFmpeg: {payload['ffmpeg'] or 'missing'}")
                print(f"vidstab: {'available' if payload['vidstab']['available'] else 'missing'}")
                print(f"deshake: {'available' if payload['deshake']['available'] else 'missing'}")
                print(f"selected backend: {payload['selected_backend'] or 'none'}")
            return 0 if payload["selected_backend"] else 2

        if args.command == "plan":
            plan = build_plan(
                args.source,
                backend=args.backend,
                profile=args.profile,
                decision=args.decision,
                reviewed_by_label=args.reviewed_by,
                note=args.note,
            )
            output_candidate = Path(args.output).expanduser()
            if output_candidate.is_symlink():
                raise ValueError("plan output must not be a symlink")
            output_candidate.parent.mkdir(parents=True, exist_ok=True)
            output = output_candidate.resolve()
            if output.suffix.lower() != ".json":
                raise ValueError("plan output must use .json")
            if output == Path(str(plan["source"]["path"])).resolve():
                raise ValueError("plan output must not overwrite source media")
            if output.exists() and not args.force:
                raise ValueError(f"plan output already exists (pass --force to replace): {output}")
            if args.markdown:
                markdown_output = _markdown_destination(args.markdown, plan)
                if markdown_output.exists() and not args.force:
                    raise ValueError(
                        f"Markdown output already exists (pass --force to replace): {markdown_output}"
                    )
            _atomic_write_json(output, plan)
            _write_markdown(args.markdown, plan, allow_existing=args.force)
            print(
                f"Stabilization plan: {output} [{plan['status']}; "
                f"backend={plan['backend']['name']}; blocking={plan['summary']['blocking']}]"
            )
            return 2 if args.strict and plan["summary"]["blocking"] else 0

        if args.command == "verify":
            plan = _load_plan(_resolve_plan_file(args.plan))
            verification = verify_plan(plan)
            print(
                f"Stabilization plan [{verification['status']}]: "
                f"blocking={verification['summary']['blocking']} "
                f"warnings={verification['summary']['warnings']}"
            )
            for blocker in verification.get("blockers") or []:
                print(f"BLOCK: {blocker}")
            for warning in verification.get("warnings") or []:
                print(f"WARN: {warning}")
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        if args.command == "apply":
            plan = apply_plan(
                args.plan,
                args.output,
                args.comparison,
                force=args.force,
            )
            _write_markdown(args.markdown, plan)
            print(f"Stabilized working copy: {plan['application']['output']['path']}")
            print(f"A/B comparison: {plan['application']['comparison']['path']}")
            print("Next: watch the full comparison, then run confirm.")
            return 0

        plan = confirm_plan(
            args.plan,
            reviewed_by_label=args.reviewed_by,
            note=args.note,
        )
        _write_markdown(args.markdown, plan)
        print(
            f"Stabilization confirmed: {args.plan} "
            f"[{plan['status']}; blocking={plan['summary']['blocking']}]"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
