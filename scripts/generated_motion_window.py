#!/usr/bin/env python3
"""Detect, approve, trim, and live-verify active motion windows in short clips.

Sparse contact sheets can hide a generated clip that holds its first frame for
several hundred milliseconds before useful motion begins.  This workflow uses
FFmpeg ``freezedetect`` as temporal evidence, derives active intervals, blocks
until a reviewer chooses trim/keep/reject, and binds any trimmed working copy
to the exact source bytes and approved range.

The detector only measures full-frame similarity.  It does not decide whether
stillness is creatively wrong, whether motion is meaningful, or whether a
generated subject/product is visually valid.  Those remain review decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


VERSION = "generated_motion_window.v1"
VERIFY_VERSION = "generated_motion_window_verify.v1"
DEFAULT_FREEZE_NOISE = 0.003
DEFAULT_MIN_FREEZE = 0.25
DEFAULT_MIN_ACTIVE = 0.25
PENDING_REVIEW = "motion-window decision still needs human review"
PENDING_APPLY = "approved motion-window trim has not been applied"
ROUND_DIGITS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    return round(max(0.0, float(value)), ROUND_DIGITS)


def _finite_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(f"ffprobe failed for {media_path}: {detail}")
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


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_project_path(raw_path: str | Path, *, root: Path, label: str) -> Path:
    lexical = Path(raw_path).expanduser()
    if not lexical.is_absolute():
        lexical = root / lexical
    lexical = Path(os.path.abspath(str(lexical)))
    if lexical.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {lexical}")
    if not _within(lexical, root):
        resolved = lexical.resolve()
        if not _within(resolved, root):
            raise ValueError(f"{label} must stay inside the project directory: {lexical}")
        # macOS exposes /tmp as /private/tmp.  Accept that filesystem alias,
        # while the component walk below still rejects project-local symlinks.
        lexical = resolved
    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return lexical


def _project_file(raw_path: str | Path, *, root: Path, label: str) -> Path:
    lexical = _lexical_project_path(raw_path, root=root, label=label)
    resolved = lexical.resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def _same_path_or_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _safe_output(
    raw_path: str | Path,
    *,
    root: Path,
    label: str,
    forbidden: Sequence[Path],
    force: bool,
) -> Path:
    destination = _lexical_project_path(raw_path, root=root, label=label)
    if destination.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {destination}")
    for blocked in forbidden:
        if _same_path_or_file(destination, blocked):
            raise ValueError(f"{label} must not overwrite bound input: {blocked}")
    if destination.exists() and not force:
        raise ValueError(f"refusing to overwrite existing {label} without --force: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


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


def parse_freezedetect(log: str, *, duration: float) -> List[Dict[str, float]]:
    """Parse FFmpeg freezedetect output, including a trailing frozen range."""
    ranges: List[Dict[str, float]] = []
    current_start: Optional[float] = None
    for line in log.splitlines():
        start_match = re.search(r"freeze_start:\s*(-?[0-9.]+)", line)
        if start_match:
            current_start = max(0.0, float(start_match.group(1)))
        end_match = re.search(r"freeze_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = min(float(duration), float(end_match.group(1)))
            if end > current_start:
                ranges.append(_range(current_start, end))
            current_start = None
    if current_start is not None and duration > current_start:
        ranges.append(_range(current_start, duration))
    return _merge_ranges(ranges, duration=duration)


def detect_freezes(
    path: Path,
    *,
    duration: float,
    noise: float,
    min_freeze: float,
) -> List[Dict[str, float]]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            f"freezedetect=noise={noise:.6f}:d={min_freeze:.3f}",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "freeze detection failed").split())
        raise ValueError(f"freeze detection failed: {detail[-2000:]}")
    return parse_freezedetect(result.stderr, duration=duration)


def _range(start: float, end: float) -> Dict[str, float]:
    start_value = _round(start)
    end_value = _round(end)
    return {"start": start_value, "end": end_value, "duration": _round(end_value - start_value)}


def _merge_ranges(
    ranges: Iterable[Mapping[str, Any]],
    *,
    duration: float,
    gap: float = 0.001,
) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    for item in ranges:
        start = _finite_float(item.get("start"))
        end = _finite_float(item.get("end"))
        if start is None or end is None:
            raise ValueError("freeze ranges require finite start/end values")
        start = max(0.0, start)
        end = min(float(duration), end)
        if end <= start:
            continue
        normalized.append(_range(start, end))
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    merged: List[Dict[str, float]] = []
    for item in normalized:
        if merged and item["start"] <= merged[-1]["end"] + gap:
            merged[-1] = _range(merged[-1]["start"], max(merged[-1]["end"], item["end"]))
        else:
            merged.append(dict(item))
    return merged


def _active_intervals(duration: float, freezes: Sequence[Mapping[str, Any]]) -> List[Dict[str, float]]:
    active: List[Dict[str, float]] = []
    cursor = 0.0
    for item in freezes:
        start = float(item["start"])
        end = float(item["end"])
        if start > cursor + 1e-6:
            active.append(_range(cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 1e-6:
        active.append(_range(cursor, duration))
    return active


def derive_analysis(
    *,
    duration: float,
    fps: float,
    freezes: Sequence[Mapping[str, Any]],
    min_active: float,
) -> Dict[str, Any]:
    if duration <= 0 or fps <= 0:
        raise ValueError("duration and fps must be positive")
    canonical_freezes = _merge_ranges(freezes, duration=duration)
    active = _active_intervals(duration, canonical_freezes)
    edge_tolerance = max(0.002, 0.5 / fps)
    leading = next(
        (dict(item) for item in canonical_freezes if item["start"] <= edge_tolerance),
        None,
    )
    trailing = next(
        (
            dict(item)
            for item in reversed(canonical_freezes)
            if item["end"] >= duration - edge_tolerance
        ),
        None,
    )
    interior = [
        dict(item)
        for item in canonical_freezes
        if not (
            leading
            and abs(item["start"] - leading["start"]) <= 1e-6
            and abs(item["end"] - leading["end"]) <= 1e-6
        )
        and not (
            trailing
            and abs(item["start"] - trailing["start"]) <= 1e-6
            and abs(item["end"] - trailing["end"]) <= 1e-6
        )
    ]
    recommended_start = float(leading["end"]) if leading else 0.0
    recommended_end = float(trailing["start"]) if trailing else duration
    recommended_duration = max(0.0, recommended_end - recommended_start)
    freeze_seconds = sum(float(item["duration"]) for item in canonical_freezes)
    active_seconds = sum(float(item["duration"]) for item in active)
    if active_seconds + 1e-6 < min_active or recommended_duration + 1e-6 < min_active:
        action = "reject_or_repair"
        reasons = ["The detector found no sufficiently long active motion window."]
    elif leading or trailing:
        action = "trim"
        reasons = ["Trim edge freezes so the selected source window starts and ends on active frames."]
    else:
        action = "keep"
        reasons = ["No full-frame leading or trailing freeze met the configured threshold."]
    if interior:
        reasons.append("Interior freeze intervals remain and require full-speed human review.")
    return {
        "freezes": canonical_freezes,
        "active_intervals": active,
        "leading_freeze": leading,
        "trailing_freeze": trailing,
        "interior_freezes": interior,
        "freeze_seconds": _round(freeze_seconds),
        "active_seconds": _round(active_seconds),
        "freeze_ratio": round(freeze_seconds / duration, 6),
        "recommendation": {
            "action": action,
            "start": _round(recommended_start),
            "end": _round(recommended_end),
            "duration": _round(recommended_duration),
            "reasons": reasons,
        },
    }


def _canonical_detection(
    *,
    duration: float,
    fps: float,
    freezes: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    analysis = derive_analysis(
        duration=duration,
        fps=fps,
        freezes=freezes,
        min_active=float(settings["min_active_seconds"]),
    )
    analysis["filter"] = (
        f"freezedetect=noise={float(settings['freeze_noise']):.6f}:"
        f"d={float(settings['min_freeze_seconds']):.3f}"
    )
    return {key: value for key, value in analysis.items() if key != "recommendation"}


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_dir": plan.get("project_dir"),
        "source": plan.get("source"),
        "settings": plan.get("settings"),
        "detection": plan.get("detection"),
        "recommendation": plan.get("recommendation"),
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


def _validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    noise = _finite_float(settings.get("freeze_noise"))
    min_freeze = _finite_float(settings.get("min_freeze_seconds"))
    min_active = _finite_float(settings.get("min_active_seconds"))
    if noise is None or not 0 <= noise <= 1:
        blockers.append("settings.freeze_noise must be between 0 and 1")
    if min_freeze is None or not 0.1 <= min_freeze <= 10:
        blockers.append("settings.min_freeze_seconds must be between 0.1 and 10")
    if min_active is None or not 0.1 <= min_active <= 60:
        blockers.append("settings.min_active_seconds must be between 0.1 and 60")
    return blockers


def _range_is_active(
    point: float,
    intervals: Sequence[Mapping[str, Any]],
    *,
    is_end: bool,
) -> bool:
    epsilon = 1e-5
    for item in intervals:
        start = float(item.get("start") or 0)
        end = float(item.get("end") or 0)
        if is_end and start + epsilon < point <= end + epsilon:
            return True
        if not is_end and start - epsilon <= point < end - epsilon:
            return True
    return False


def _media_contract_blockers(
    source: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    selected_duration: float,
) -> List[str]:
    blockers: List[str] = []
    if int(output.get("width") or 0) != int(source.get("width") or 0):
        blockers.append("trimmed output width differs from source display width")
    if int(output.get("height") or 0) != int(source.get("height") or 0):
        blockers.append("trimmed output height differs from source display height")
    source_fps = float(source.get("fps") or 0)
    if abs(float(output.get("fps") or 0) - source_fps) > 0.02:
        blockers.append("trimmed output fps differs from source fps")
    if bool(output.get("has_audio")) != bool(source.get("has_audio")):
        blockers.append("trimmed output audio-stream presence differs from source")
    duration_tolerance = max(0.12, 3.0 / max(source_fps, 1.0))
    if abs(float(output.get("duration") or 0) - selected_duration) > duration_tolerance:
        blockers.append("trimmed output duration differs from the approved motion window")
    if str(output.get("video_codec") or "") != "h264":
        blockers.append("trimmed output must use H.264")
    if str(output.get("pixel_format") or "") != "yuv420p":
        blockers.append("trimmed output must use yuv420p")
    if bool(source.get("has_audio")) and str(output.get("audio_codec") or "") != "aac":
        blockers.append("trimmed output must use AAC when source audio exists")
    return blockers


def _structural_blockers(plan: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"unsupported version: {plan.get('version')!r}")
    project_raw = str(plan.get("project_dir") or "")
    project = Path(project_raw).expanduser()
    if not project_raw or not project.is_absolute():
        blockers.append("project_dir must be an absolute path")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    blockers.extend(_validate_settings(settings))
    duration = _finite_float(source.get("duration"))
    fps = _finite_float(source.get("fps"))
    if duration is None or duration <= 0 or fps is None or fps <= 0:
        blockers.append("source duration/fps contract is invalid")
        return blockers
    detection = plan.get("detection") if isinstance(plan.get("detection"), Mapping) else {}
    freezes = detection.get("freezes") if isinstance(detection.get("freezes"), list) else []
    if not blockers:
        try:
            expected_analysis = derive_analysis(
                duration=duration,
                fps=fps,
                freezes=freezes,
                min_active=float(settings["min_active_seconds"]),
            )
            expected_detection = _canonical_detection(
                duration=duration,
                fps=fps,
                freezes=freezes,
                settings=settings,
            )
            if detection != expected_detection:
                blockers.append("stored detection is not the canonical derivation of freeze evidence")
            if plan.get("recommendation") != expected_analysis["recommendation"]:
                blockers.append("stored recommendation is not the canonical motion-window recommendation")
        except (TypeError, ValueError, KeyError) as exc:
            blockers.append(f"motion-window evidence is invalid: {exc}")
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    if str(decision.get("action") or "") not in {"review", "trim", "keep", "reject"}:
        blockers.append("decision.action must be review, trim, keep, or reject")
    application = plan.get("application")
    if application is not None and not isinstance(application, Mapping):
        blockers.append("application must be null or an object")
    return blockers


def _live_source_blockers(
    plan: Mapping[str, Any],
    *,
    probe_fn: Callable[[Path | str], Mapping[str, Any]],
    detect_fn: Callable[..., Sequence[Mapping[str, Any]]],
) -> List[str]:
    blockers: List[str] = []
    root = Path(str(plan.get("project_dir") or "")).expanduser()
    if not root.is_absolute():
        return ["project_dir must be an absolute path"]
    root = root.resolve()
    source_record = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    try:
        source_path = _project_file(str(source_record.get("path") or ""), root=root, label="source")
    except ValueError as exc:
        return [str(exc)]
    if _sha256(source_path) != str(source_record.get("sha256") or ""):
        blockers.append("source bytes changed after motion-window analysis")
    if source_path.stat().st_size != int(source_record.get("size_bytes") or -1):
        blockers.append("source size changed after motion-window analysis")
    try:
        live_media = dict(probe_fn(source_path))
    except Exception as exc:
        blockers.append(f"source probe failed: {exc}")
        return blockers
    for key in ("duration", "fps", "width", "height", "rotation", "has_audio", "video_codec", "audio_codec", "pixel_format"):
        stored = source_record.get(key)
        live = live_media.get(key)
        if isinstance(stored, float) or isinstance(live, float):
            if abs(float(stored or 0) - float(live or 0)) > 1e-5:
                blockers.append(f"source media contract changed: {key}")
        elif stored != live:
            blockers.append(f"source media contract changed: {key}")
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    if not _validate_settings(settings):
        try:
            live_freezes = list(
                detect_fn(
                    source_path,
                    duration=float(live_media["duration"]),
                    noise=float(settings["freeze_noise"]),
                    min_freeze=float(settings["min_freeze_seconds"]),
                )
            )
            expected_detection = _canonical_detection(
                duration=float(live_media["duration"]),
                fps=float(live_media["fps"]),
                freezes=live_freezes,
                settings=settings,
            )
            if plan.get("detection") != expected_detection:
                blockers.append("live freeze evidence changed after motion-window analysis")
        except Exception as exc:
            blockers.append(f"live freeze detection failed: {exc}")
    return blockers


def _application_blockers(
    plan: Mapping[str, Any],
    *,
    probe_fn: Callable[[Path | str], Mapping[str, Any]],
) -> List[str]:
    application = plan.get("application")
    if not isinstance(application, Mapping):
        return []
    blockers: List[str] = []
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    selected = application.get("selected_range")
    expected_selected = {
        "start": _round(float(decision.get("start") or 0)),
        "end": _round(float(decision.get("end") or 0)),
        "duration": _round(float(decision.get("end") or 0) - float(decision.get("start") or 0)),
    }
    if selected != expected_selected:
        blockers.append("application selected_range differs from the approved decision")
    root = Path(str(plan.get("project_dir") or "")).expanduser()
    if not root.is_absolute():
        return blockers + ["project_dir must be an absolute path"]
    root = root.resolve()
    output_record = application.get("output") if isinstance(application.get("output"), Mapping) else {}
    try:
        output_path = _project_file(str(output_record.get("path") or ""), root=root, label="application output")
    except ValueError as exc:
        return blockers + [str(exc)]
    source_path = Path(str((plan.get("source") or {}).get("path") or ""))
    if _same_path_or_file(output_path, source_path):
        blockers.append("application output must not overwrite source")
    if _sha256(output_path) != str(output_record.get("sha256") or ""):
        blockers.append("trimmed output bytes changed after apply")
    if output_path.stat().st_size != int(output_record.get("size_bytes") or -1):
        blockers.append("trimmed output size changed after apply")
    try:
        live_media = dict(probe_fn(output_path))
    except Exception as exc:
        blockers.append(f"trimmed output probe failed: {exc}")
        return blockers
    for key in ("duration", "fps", "width", "height", "rotation", "has_audio", "video_codec", "audio_codec", "pixel_format"):
        stored = output_record.get(key)
        live = live_media.get(key)
        if isinstance(stored, float) or isinstance(live, float):
            if abs(float(stored or 0) - float(live or 0)) > 1e-5:
                blockers.append(f"trimmed output media contract changed: {key}")
        elif stored != live:
            blockers.append(f"trimmed output media contract changed: {key}")
    blockers.extend(
        _media_contract_blockers(
            plan.get("source") if isinstance(plan.get("source"), Mapping) else {},
            live_media,
            selected_duration=float(expected_selected["duration"]),
        )
    )
    return blockers


def _decision_blockers(plan: Mapping[str, Any]) -> List[str]:
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    action = str(decision.get("action") or "")
    if action == "review":
        return [PENDING_REVIEW]
    if action == "reject":
        return ["reviewer rejected this generated clip motion window"]
    blockers: List[str] = []
    if not str(decision.get("reviewed_by") or "").strip():
        blockers.append("decision.reviewed_by is required")
    if not str(decision.get("note") or "").strip():
        blockers.append("decision.note is required")
    detection = plan.get("detection") if isinstance(plan.get("detection"), Mapping) else {}
    active = detection.get("active_intervals") if isinstance(detection.get("active_intervals"), list) else []
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    duration = float(source.get("duration") or 0)
    start = _finite_float(decision.get("start"))
    end = _finite_float(decision.get("end"))
    if action == "keep":
        if start not in {None, 0.0} or end not in {None, duration}:
            blockers.append("keep decision must retain the complete source range")
        if plan.get("application") is not None:
            blockers.append("keep decision must not retain a trim application")
        return blockers
    if start is None or end is None or start < 0 or end <= start or end > duration + 1e-6:
        blockers.append("trim decision requires a valid in-bounds start/end range")
        return blockers
    min_active = float((plan.get("settings") or {}).get("min_active_seconds") or DEFAULT_MIN_ACTIVE)
    if end - start + 1e-6 < min_active:
        blockers.append("approved trim is shorter than min_active_seconds")
    if not _range_is_active(start, active, is_end=False):
        blockers.append("approved trim start must be inside an active interval")
    if not _range_is_active(end, active, is_end=True):
        blockers.append("approved trim end must be inside an active interval")
    if not isinstance(plan.get("application"), Mapping):
        blockers.append(PENDING_APPLY)
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    detection = plan.get("detection") if isinstance(plan.get("detection"), Mapping) else {}
    interior = detection.get("interior_freezes") if isinstance(detection.get("interior_freezes"), list) else []
    if interior:
        warnings.append(
            "Interior full-frame freeze intervals remain; play the complete clip at 1x and reject stop-start motion that trimming cannot repair."
        )
    if float(detection.get("freeze_ratio") or 0) >= 0.5:
        warnings.append("At least half of the source duration is full-frame freeze evidence.")
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    if decision.get("action") == "keep" and (
        detection.get("leading_freeze") or detection.get("trailing_freeze")
    ):
        warnings.append("Reviewer intentionally kept an edge freeze; confirm it serves the edit rather than hiding delayed motion.")
    if decision.get("action") == "trim" and isinstance(plan.get("application"), Mapping):
        warnings.append(
            "The trimmed working copy was re-encoded for frame-accurate boundaries; review it at 1x and re-run generated-clip/final-render QA."
        )
    return warnings


def _summary(plan: Mapping[str, Any], blockers: Sequence[str], warnings: Sequence[str]) -> Dict[str, Any]:
    detection = plan.get("detection") if isinstance(plan.get("detection"), Mapping) else {}
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    return {
        "freezes": len(detection.get("freezes") or []),
        "active_intervals": len(detection.get("active_intervals") or []),
        "freeze_seconds": _round(float(detection.get("freeze_seconds") or 0)),
        "active_seconds": _round(float(detection.get("active_seconds") or 0)),
        "decision": str(decision.get("action") or "review"),
        "applied": int(isinstance(plan.get("application"), Mapping)),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }


def _derived_snapshot(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers = _structural_blockers(plan) + _decision_blockers(plan)
    warnings = _computed_warnings(plan)
    status = "blocked" if blockers else ("warn" if warnings else "ready")
    return {
        "warnings": warnings,
        "blockers": blockers,
        "summary": _summary(plan, blockers, warnings),
        "status": status,
    }


def _set_derived(plan: Dict[str, Any]) -> None:
    snapshot = _derived_snapshot(plan)
    plan.update(snapshot)
    plan["plan_id"] = _plan_id(plan)


def build_plan(
    source_path: str | Path,
    *,
    project_dir: str | Path,
    freeze_noise: float = DEFAULT_FREEZE_NOISE,
    min_freeze: float = DEFAULT_MIN_FREEZE,
    min_active: float = DEFAULT_MIN_ACTIVE,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    detect_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    detect_fn = detect_fn or detect_freezes
    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"project directory does not exist: {project}")
    source = _project_file(source_path, root=project, label="source")
    settings = {
        "freeze_noise": float(freeze_noise),
        "min_freeze_seconds": float(min_freeze),
        "min_active_seconds": float(min_active),
    }
    setting_blockers = _validate_settings(settings)
    if setting_blockers:
        raise ValueError("; ".join(setting_blockers))
    media = dict(probe_fn(source))
    freezes = list(
        detect_fn(
            source,
            duration=float(media["duration"]),
            noise=float(freeze_noise),
            min_freeze=float(min_freeze),
        )
    )
    analysis = derive_analysis(
        duration=float(media["duration"]),
        fps=float(media["fps"]),
        freezes=freezes,
        min_active=float(min_active),
    )
    detection = _canonical_detection(
        duration=float(media["duration"]),
        fps=float(media["fps"]),
        freezes=freezes,
        settings=settings,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "project_dir": str(project),
        "source": _source_contract(source, media),
        "settings": settings,
        "detection": detection,
        "recommendation": analysis["recommendation"],
        "decision": {
            "action": "review",
            "start": None,
            "end": None,
            "reviewed_by": "",
            "note": "",
            "reviewed_at": None,
        },
        "application": None,
        "review_contract": {
            "required_playback": [
                "Play the complete source at 1x; a contact sheet is not sufficient.",
                "Confirm the chosen first frame is already inside meaningful motion.",
                "Check interior stop-start freezes, subject/product integrity, and audio continuity.",
                "After apply, play the trimmed working copy and re-run generated clip and final render QA.",
            ],
            "detector_limit": (
                "FFmpeg freezedetect measures full-frame similarity only; it cannot judge meaningful motion, "
                "creative holds, identity, anatomy, product geometry, or story value."
            ),
            "source_safe": "The source file is never overwritten; trim apply writes a new H.264/AAC working copy.",
        },
    }
    _set_derived(plan)
    return plan


def _stored_state_blockers(plan: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    expected = _derived_snapshot(plan)
    for key in ("warnings", "blockers", "summary", "status"):
        if plan.get(key) != expected[key]:
            blockers.append(f"stored {key} is stale or non-canonical")
    if str(plan.get("plan_id") or "") != _plan_id(plan):
        blockers.append("plan_id does not match canonical plan contents")
    return blockers


def verify_plan(
    plan: Mapping[str, Any],
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    detect_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    detect_fn = detect_fn or detect_freezes
    derived = _derived_snapshot(plan)
    blockers = list(derived["blockers"])
    blockers.extend(_stored_state_blockers(plan))
    blockers.extend(_live_source_blockers(plan, probe_fn=probe_fn, detect_fn=detect_fn))
    blockers.extend(_application_blockers(plan, probe_fn=probe_fn))
    blockers = list(dict.fromkeys(blockers))
    warnings = list(derived["warnings"])
    status = "blocked" if blockers else ("warn" if warnings else "ready")
    return {
        "version": VERIFY_VERSION,
        "plan_id": plan.get("plan_id"),
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "summary": _summary(plan, blockers, warnings),
    }


def confirm_plan(
    plan: Mapping[str, Any],
    *,
    decision: str,
    reviewed_by: str,
    note: str,
    start: Optional[float] = None,
    end: Optional[float] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    detect_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    detect_fn = detect_fn or detect_freezes
    integrity = _structural_blockers(plan) + _stored_state_blockers(plan)
    integrity.extend(_live_source_blockers(plan, probe_fn=probe_fn, detect_fn=detect_fn))
    integrity.extend(_application_blockers(plan, probe_fn=probe_fn))
    if integrity:
        raise ValueError("cannot confirm invalid/stale motion-window plan: " + "; ".join(dict.fromkeys(integrity)))
    action = decision.strip().lower()
    if action not in {"trim", "keep", "reject"}:
        raise ValueError("decision must be trim, keep, or reject")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("--reviewed-by and --note are required")
    updated = json.loads(json.dumps(plan))
    source_duration = float((updated.get("source") or {}).get("duration") or 0)
    recommendation = updated.get("recommendation") or {}
    if action == "trim":
        chosen_start = float(recommendation.get("start") if start is None else start)
        chosen_end = float(recommendation.get("end") if end is None else end)
    else:
        chosen_start = 0.0
        chosen_end = source_duration
    updated["decision"] = {
        "action": action,
        "start": _round(chosen_start),
        "end": _round(chosen_end),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "reviewed_at": utc_now(),
    }
    updated["application"] = None
    updated["updated_at"] = utc_now()
    _set_derived(updated)
    decision_errors = [item for item in _decision_blockers(updated) if item != PENDING_APPLY]
    if decision_errors:
        raise ValueError("invalid motion-window decision: " + "; ".join(decision_errors))
    return updated


def build_ffmpeg_command(plan: Mapping[str, Any], output_path: Path | str) -> List[str]:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    start = float(decision.get("start") or 0)
    end = float(decision.get("end") or 0)
    selected_duration = end - start
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source.get("path")),
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{selected_duration:.6f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "-1",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        f"{float(source.get('fps') or 30):.6f}",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return command


def _run_checked(command: Sequence[str], label: str) -> None:
    result = subprocess.run(list(command), capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    raise ValueError(f"{label} failed: {detail[-2000:]}")


def _decode_check(path: Path) -> None:
    _run_checked(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "null", "-"],
        "full decode check",
    )


def apply_plan(
    plan_path: str | Path,
    *,
    output: str | Path,
    force: bool = False,
) -> Dict[str, Any]:
    raw_plan_path = Path(plan_path).expanduser()
    with raw_plan_path.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    if not isinstance(plan, dict):
        raise ValueError("motion-window plan root must be an object")
    root = Path(str(plan.get("project_dir") or "")).expanduser().resolve()
    plan_file = _project_file(raw_plan_path, root=root, label="plan")
    source_file = _project_file(str((plan.get("source") or {}).get("path") or ""), root=root, label="source")
    verification = verify_plan(plan)
    allowed = {PENDING_APPLY}
    unexpected = [item for item in verification["blockers"] if item not in allowed]
    if unexpected or str((plan.get("decision") or {}).get("action") or "") != "trim":
        detail = unexpected or ["plan decision must be trim before apply"]
        raise ValueError("cannot apply motion-window plan: " + "; ".join(detail))
    destination = _safe_output(
        output,
        root=root,
        label="trimmed output",
        forbidden=[source_file, plan_file],
        force=force,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix or ".mp4", dir=str(destination.parent)
    )
    os.close(descriptor)
    os.unlink(temporary_name)
    temporary = Path(temporary_name)
    try:
        command = build_ffmpeg_command(plan, temporary)
        _run_checked(command, "motion-window trim")
        output_media = probe_media(temporary)
        decision = plan["decision"]
        selected_duration = float(decision["end"]) - float(decision["start"])
        contract_blockers = _media_contract_blockers(plan["source"], output_media, selected_duration=selected_duration)
        if contract_blockers:
            raise ValueError("trimmed output contract failed: " + "; ".join(contract_blockers))
        _decode_check(temporary)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    output_record = _media_fingerprint(destination)
    selected_range = _range(float(plan["decision"]["start"]), float(plan["decision"]["end"]))
    plan["application"] = {
        "applied_at": utc_now(),
        "selected_range": selected_range,
        "output": output_record,
        "encoder": {
            "video": "libx264 crf=18 preset=medium yuv420p",
            "audio": "aac 192k" if plan["source"].get("has_audio") else "none",
            "full_decode": True,
        },
    }
    plan["updated_at"] = utc_now()
    _set_derived(plan)
    post = verify_plan(plan)
    if post["summary"]["blocking"]:
        raise ValueError("applied motion-window plan did not live-verify: " + "; ".join(post["blockers"]))
    _atomic_write_json(plan_file, plan)
    return plan


def render_markdown(plan: Mapping[str, Any], *, plan_path: str = "work/generated_motion_window.json") -> str:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    detection = plan.get("detection") if isinstance(plan.get("detection"), Mapping) else {}
    recommendation = plan.get("recommendation") if isinstance(plan.get("recommendation"), Mapping) else {}
    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    summary = plan.get("summary") if isinstance(plan.get("summary"), Mapping) else {}
    lines = [
        "# Generated Motion Window",
        "",
        f"- Status: `{plan.get('status')}`",
        f"- Source: `{source.get('path')}`",
        f"- Source SHA-256: `{source.get('sha256')}`",
        f"- Media: `{source.get('duration')}s / {source.get('fps')}fps / {source.get('width')}x{source.get('height')} / audio={source.get('has_audio')}`",
        f"- Detection: `{detection.get('filter')}`",
        f"- Freeze / active: `{summary.get('freeze_seconds')}s / {summary.get('active_seconds')}s`",
        f"- Recommendation: `{recommendation.get('action')}` `{recommendation.get('start')}–{recommendation.get('end')}s`",
        f"- Decision: `{decision.get('action')}`",
        "",
        "## Temporal evidence",
        "",
        "| Kind | Start | End | Duration |",
        "|---|---:|---:|---:|",
    ]
    for item in detection.get("freezes") or []:
        lines.append(f"| freeze | {item['start']:.3f} | {item['end']:.3f} | {item['duration']:.3f} |")
    for item in detection.get("active_intervals") or []:
        lines.append(f"| active | {item['start']:.3f} | {item['end']:.3f} | {item['duration']:.3f} |")
    if not detection.get("freezes") and not detection.get("active_intervals"):
        lines.append("| — | — | — | — |")
    lines.extend(["", "## Review contract", ""])
    for item in (plan.get("review_contract") or {}).get("required_playback") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "The detector is evidence, not creative approval. A deliberate still, hold, or product pause may be valid; delayed or stop-start generated motion may require trim or rejection.",
            "",
            "## Commands",
            "",
            "```bash",
            f"python3 scripts/generated_motion_window.py confirm {plan_path} --decision trim --reviewed-by editor --note \"full-speed source reviewed; trim begins inside motion\"",
            f"python3 scripts/generated_motion_window.py apply {plan_path} --output work/generated-active.mp4",
            f"python3 scripts/generated_motion_window.py verify {plan_path} --strict",
            "```",
        ]
    )
    if plan.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in plan["warnings"])
    if plan.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in plan["blockers"])
    return "\n".join(lines) + "\n"


def _load_plan(path: str | Path) -> Dict[str, Any]:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("motion-window plan root must be an object")
    return payload


def _write_markdown_for_plan(
    plan: Mapping[str, Any],
    *,
    plan_file: Path,
    markdown: Optional[str],
    force: bool,
) -> Optional[Path]:
    if not markdown:
        return None
    root = Path(str(plan.get("project_dir") or "")).expanduser().resolve()
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    output = Path(str((application.get("output") or {}).get("path") or "")) if application else None
    forbidden = [source, plan_file]
    if output:
        forbidden.append(output)
    destination = _safe_output(
        markdown,
        root=root,
        label="markdown",
        forbidden=forbidden,
        force=force,
    )
    _atomic_write_text(destination, render_markdown(plan, plan_path=str(plan_file)))
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect and source-bind active motion windows in short generated clips."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Detect freezes and create a pending review plan.")
    analyze.add_argument("source")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--freeze-noise", type=float, default=DEFAULT_FREEZE_NOISE)
    analyze.add_argument("--min-freeze", type=float, default=DEFAULT_MIN_FREEZE)
    analyze.add_argument("--min-active", type=float, default=DEFAULT_MIN_ACTIVE)
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser("confirm", help="Record a human trim/keep/reject decision.")
    confirm.add_argument("plan")
    confirm.add_argument("--decision", required=True, choices=("trim", "keep", "reject"))
    confirm.add_argument("--start", type=float)
    confirm.add_argument("--end", type=float)
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--markdown")

    apply_parser = subparsers.add_parser("apply", help="Render the approved trim to a new working copy.")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--force", action="store_true")

    verify = subparsers.add_parser("verify", help="Live-verify source, evidence, decision, and output.")
    verify.add_argument("plan")
    verify.add_argument("--json")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    verify.add_argument("--force", action="store_true")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "analyze":
            project = Path(args.project_dir).expanduser().resolve()
            source = _project_file(args.source, root=project, label="source")
            output = _safe_output(
                args.output,
                root=project,
                label="plan",
                forbidden=[source],
                force=args.force,
            )
            plan = build_plan(
                source,
                project_dir=project,
                freeze_noise=args.freeze_noise,
                min_freeze=args.min_freeze,
                min_active=args.min_active,
            )
            markdown_path = None
            if args.markdown:
                markdown_path = _safe_output(
                    args.markdown,
                    root=project,
                    label="markdown",
                    forbidden=[source, output],
                    force=args.force,
                )
            _atomic_write_json(output, plan)
            if markdown_path:
                _atomic_write_text(markdown_path, render_markdown(plan, plan_path=str(output)))
            print(
                f"generated-motion-window: {plan['status']} recommendation={plan['recommendation']['action']} "
                f"freezes={plan['summary']['freezes']} blocking={plan['summary']['blocking']}"
            )
            return 0

        raw_plan_file = Path(args.plan).expanduser()
        plan = _load_plan(raw_plan_file)
        root = Path(str(plan.get("project_dir") or "")).expanduser().resolve()
        plan_file = _project_file(raw_plan_file, root=root, label="plan")
        if args.command == "confirm":
            updated = confirm_plan(
                plan,
                decision=args.decision,
                start=args.start,
                end=args.end,
                reviewed_by=args.reviewed_by,
                note=args.note,
            )
            _atomic_write_json(plan_file, updated)
            if args.markdown:
                _write_markdown_for_plan(
                    updated, plan_file=plan_file, markdown=args.markdown, force=True
                )
            print(
                f"generated-motion-window: {updated['status']} decision={updated['decision']['action']} "
                f"blocking={updated['summary']['blocking']}"
            )
            return 0
        if args.command == "apply":
            updated = apply_plan(plan_file, output=args.output, force=args.force)
            if args.markdown:
                _write_markdown_for_plan(
                    updated, plan_file=plan_file, markdown=args.markdown, force=True
                )
            print(
                f"generated-motion-window: {updated['status']} output={updated['application']['output']['path']} "
                f"blocking={updated['summary']['blocking']} warnings={updated['summary']['warnings']}"
            )
            return 0
        verification = verify_plan(plan)
        if args.json:
            application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
            applied_output = (application.get("output") or {}).get("path") if application else None
            forbidden = [plan_file, Path(str(plan["source"]["path"]))]
            if applied_output:
                forbidden.append(Path(str(applied_output)))
            destination = _safe_output(
                args.json,
                root=root,
                label="verification JSON",
                forbidden=forbidden,
                force=args.force,
            )
            _atomic_write_json(destination, verification)
        if args.markdown:
            _write_markdown_for_plan(
                plan, plan_file=plan_file, markdown=args.markdown, force=args.force
            )
        print(
            f"generated-motion-window: {verification['status']} blocking={verification['summary']['blocking']} "
            f"warnings={verification['summary']['warnings']}"
        )
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"generated-motion-window: error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
