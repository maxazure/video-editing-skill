#!/usr/bin/env python3
"""Plan, render, confirm, and verify an audio-guided multicam edit.

The workflow consumes a ready ``multicam_sync_plan.v1``.  It treats the
selected audio stream on each explicitly mapped camera as a proxy for that
speaker, normalizes every stream independently, holds the current camera when
evidence is ambiguous, and folds shots shorter than the configured minimum.
The result is a draft that must be watched in full; audio energy is not speaker
identification and does not prove that the framed person is speaking.
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
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audio_sync import AudioSyncError, decode_audio_envelope  # noqa: E402


VERSION = "multicam_switch_plan.v1"
PENDING_APPLY = "multicam switch draft has not been rendered and validated"
PENDING_REVIEW = "rendered multicam draft still needs full-length human review"
REJECTED_REVIEW = "rendered multicam draft was rejected during human review"
REVIEW_FIELDS = ("speaker_selection", "cut_timing", "sync", "audio_continuity")
REVIEW_VALUES = {"pass", "fail"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run(command)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 3000:
        detail = detail[-3000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _absolute(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def _fraction(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            result = float(numerator) / float(denominator)
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
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
    audios = [item for item in payload.get("streams", []) if item.get("codec_type") == "audio"]
    duration = _fraction((payload.get("format") or {}).get("duration"))
    if duration is None and video:
        duration = _fraction(video.get("duration"))
    fps = _fraction((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate"))
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    rotation = _rotation(video or {})
    if rotation in {90, 270}:
        width, height = height, width
    if duration is None or duration <= 0:
        raise ValueError(f"media duration is unavailable: {path}")
    return {
        "duration": round(duration, 6),
        "has_video": video is not None,
        "has_audio": bool(audios),
        "audio_streams": len(audios),
        "width": width or None,
        "height": height or None,
        "fps": round(fps, 6) if fps else None,
        "rotation": rotation,
        "video_codec": str((video or {}).get("codec_name") or "").lower() or None,
        "audio_codec": str((audios[0] if audios else {}).get("codec_name") or "").lower() or None,
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


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _plan_file(path: str) -> Path:
    candidate = Path(_absolute(path))
    if candidate.is_symlink() or candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise ValueError("multicam switch plan must be an existing non-symlink .json file")
    return candidate


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    size = min(len(left), len(right))
    if size < 2:
        return 0.0
    x = [float(value) for value in left[:size]]
    y = [float(value) for value in right[:size]]
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y)
    )
    return numerator / denominator if denominator > 1e-12 else 0.0


def _round4(value: float) -> float:
    return round(float(value), 4)


def _round3(value: float) -> float:
    return round(float(value), 3)


def _parse_assignments(values: Sequence[str], label: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"{label} expects ANGLE_ID=VALUE: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"{label} expects non-empty ANGLE_ID=VALUE: {raw}")
        if key in result:
            raise ValueError(f"duplicate {label} angle id: {key}")
        result[key] = value
    return result


def _collapse_windows(windows: Sequence[Mapping[str, Any]], end_time: float) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for window in windows:
        angle_id = str(window["selected_angle_id"])
        start = float(window["start"])
        end = float(window["end"])
        if runs and runs[-1]["angle_id"] == angle_id:
            runs[-1]["end"] = end
            runs[-1]["windows"] += 1
            runs[-1]["confident_windows"] += int(bool(window.get("confident")))
        else:
            runs.append({
                "start": start,
                "end": end,
                "angle_id": angle_id,
                "windows": 1,
                "confident_windows": int(bool(window.get("confident"))),
            })
    if runs:
        runs[-1]["end"] = end_time
    return runs


def fold_short_runs(runs: Sequence[Mapping[str, Any]], min_shot_seconds: float) -> List[Dict[str, Any]]:
    folded = [dict(item) for item in runs]
    changed = True
    while changed and len(folded) > 1:
        changed = False
        for index, item in enumerate(folded):
            if float(item["end"]) - float(item["start"]) + 1e-9 >= min_shot_seconds:
                continue
            if index == 0:
                folded[1]["start"] = item["start"]
                folded[1]["windows"] = int(folded[1].get("windows", 0)) + int(item.get("windows", 0))
                folded[1]["confident_windows"] = int(folded[1].get("confident_windows", 0)) + int(item.get("confident_windows", 0))
            else:
                folded[index - 1]["end"] = item["end"]
                folded[index - 1]["windows"] = int(folded[index - 1].get("windows", 0)) + int(item.get("windows", 0))
                folded[index - 1]["confident_windows"] = int(folded[index - 1].get("confident_windows", 0)) + int(item.get("confident_windows", 0))
            del folded[index]
            changed = True
            break
    coalesced: List[Dict[str, Any]] = []
    for item in folded:
        if coalesced and coalesced[-1]["angle_id"] == item["angle_id"]:
            coalesced[-1]["end"] = item["end"]
            coalesced[-1]["windows"] = int(coalesced[-1].get("windows", 0)) + int(item.get("windows", 0))
            coalesced[-1]["confident_windows"] = int(coalesced[-1].get("confident_windows", 0)) + int(item.get("confident_windows", 0))
        else:
            coalesced.append(dict(item))
    return coalesced


def choose_windows(
    envelopes: Mapping[str, Sequence[float]],
    *,
    start_time: float,
    end_time: float,
    window_seconds: float,
    fallback_angle_id: str,
    min_activity_score: float,
    dominance_margin: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if len(envelopes) < 2:
        raise ValueError("at least two speaker-mapped angle envelopes are required")
    expected_windows = int(math.ceil(max(0.0, end_time - start_time) / window_seconds))
    length = min(expected_windows, *(len(values) for values in envelopes.values()))
    if length < 1:
        raise ValueError("speaker-mapped audio produced no analysis windows")
    calibration: Dict[str, Dict[str, float]] = {}
    normalized: Dict[str, List[float]] = {}
    db_levels: Dict[str, List[float]] = {}
    for angle_id, values in envelopes.items():
        db = [20.0 * math.log10(max(float(value), 1.0) / 32768.0) for value in values[:length]]
        floor = _percentile(db, 0.1)
        peak = _percentile(db, 0.95)
        span = max(6.0, peak - floor)
        db_levels[angle_id] = db
        normalized[angle_id] = [max(0.0, min(1.0, (value - floor) / span)) for value in db]
        calibration[angle_id] = {
            "noise_floor_dbfs_p10": _round3(floor),
            "speech_peak_dbfs_p95": _round3(peak),
            "normalization_span_db": _round3(span),
        }

    windows: List[Dict[str, Any]] = []
    previous = fallback_angle_id
    raw_winners: Dict[str, int] = {angle_id: 0 for angle_id in envelopes}
    confident = 0
    for index in range(length):
        ranked = sorted(
            ((values[index], angle_id) for angle_id, values in normalized.items()),
            reverse=True,
        )
        top_score, top_angle = ranked[0]
        runner_score = ranked[1][0]
        margin = top_score - runner_score
        is_confident = top_score >= min_activity_score and margin >= dominance_margin
        if is_confident:
            selected = top_angle
            previous = selected
            raw_winners[top_angle] += 1
            confident += 1
            reason = "dominant_normalized_energy"
        else:
            selected = previous
            reason = "hold_previous_ambiguous_energy"
        window_start = start_time + index * window_seconds
        window_end = min(end_time, window_start + window_seconds)
        windows.append({
            "index": index,
            "start": _round4(window_start),
            "end": _round4(window_end),
            "selected_angle_id": selected,
            "top_candidate_angle_id": top_angle,
            "top_score": round(top_score, 5),
            "runner_score": round(runner_score, 5),
            "dominance_margin": round(margin, 5),
            "confident": is_confident,
            "reason": reason,
        })

    correlations: List[Dict[str, Any]] = []
    ids = list(envelopes)
    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1:]:
            correlations.append({
                "left": left,
                "right": right,
                "correlation": round(_pearson(db_levels[left], db_levels[right]), 6),
            })
    return windows, {
        "calibration": calibration,
        "raw_confident_winners": raw_winners,
        "confident_windows": confident,
        "ambiguous_windows": length - confident,
        "pairwise_level_correlations": correlations,
        "max_pairwise_level_correlation": max(
            (float(item["correlation"]) for item in correlations), default=0.0
        ),
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "sync_plan": plan.get("sync_plan"),
        "settings": plan.get("settings"),
        "sources": plan.get("sources"),
        "speaker_map": plan.get("speaker_map"),
        "common_overlap_in_reference": plan.get("common_overlap_in_reference"),
        "analysis": plan.get("analysis"),
        "switches": plan.get("switches"),
        "delivery": plan.get("delivery"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(_canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_plan(
    sync_plan_path: str,
    *,
    speaker_map: Mapping[str, str],
    delivery: str,
    fallback_angle_id: Optional[str] = None,
    program_audio_angle_id: Optional[str] = None,
    window_seconds: float = 0.5,
    min_shot_seconds: float = 1.5,
    min_activity_score: float = 0.2,
    dominance_margin: float = 0.12,
    correlation_threshold: float = 0.985,
    allow_correlated_audio: bool = False,
) -> Dict[str, Any]:
    if not 0.1 <= window_seconds <= 2.0:
        raise ValueError("window_seconds must be between 0.1 and 2.0")
    if min_shot_seconds < window_seconds or min_shot_seconds > 30.0:
        raise ValueError("min_shot_seconds must be at least one window and no more than 30")
    if not 0.0 <= min_activity_score <= 1.0:
        raise ValueError("min_activity_score must be between 0 and 1")
    if not 0.0 <= dominance_margin <= 1.0:
        raise ValueError("dominance_margin must be between 0 and 1")
    if not 0.0 <= correlation_threshold <= 1.0:
        raise ValueError("correlation_threshold must be between 0 and 1")

    sync_path = Path(_absolute(sync_plan_path))
    if not sync_path.is_file():
        raise ValueError(f"sync plan not found: {sync_path}")
    if sync_path.is_symlink():
        raise ValueError("sync plan must not be a symlink")
    sync_plan = _load_json(sync_path)
    if sync_plan.get("version") != "multicam_sync_plan.v1":
        raise ValueError("--sync-plan must be multicam_sync_plan.v1")
    angles = sync_plan.get("angles") or []
    if not isinstance(angles, list) or len(angles) < 2:
        raise ValueError("sync plan must contain at least two angles")
    by_id = {str(item.get("id")): item for item in angles if isinstance(item, Mapping) and item.get("id")}
    unknown = sorted(set(speaker_map) - set(by_id))
    if unknown:
        raise ValueError(f"speaker mapping references unknown angle id: {unknown[0]}")
    if len(speaker_map) < 2:
        raise ValueError("map at least two video angles with --speaker ANGLE_ID=LABEL")
    fallback = fallback_angle_id or next(iter(speaker_map))
    if fallback not in speaker_map:
        raise ValueError("fallback angle must be one of the speaker-mapped angles")
    program_audio = program_audio_angle_id or next(
        (str(item.get("id")) for item in angles if item.get("role") == "reference"), ""
    )
    if program_audio not in by_id:
        raise ValueError("program audio angle id is not present in the sync plan")

    overlap = sync_plan.get("common_overlap_in_reference") or {}
    try:
        overlap_start = float(overlap["start"])
        overlap_end = float(overlap["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("sync plan has no valid common overlap") from exc
    if overlap_end - overlap_start < min_shot_seconds:
        raise ValueError("common overlap is shorter than one minimum shot")

    blockers: List[str] = []
    warnings: List[str] = []
    if sync_plan.get("status") != "ready" or int((sync_plan.get("summary") or {}).get("blocking") or 0):
        blockers.append("upstream multicam sync plan is not ready")

    source_records: List[Dict[str, Any]] = []
    envelopes: Dict[str, Sequence[float]] = {}
    for item in angles:
        angle_id = str(item.get("id") or "")
        media = item.get("media") or {}
        path = Path(_absolute(str(media.get("path") or "")))
        if not path.is_file():
            raise ValueError(f"angle media not found: {path}")
        if path.is_symlink():
            raise ValueError(f"angle media must not be a symlink: {path}")
        info = _source_info(path)
        audio_stream = item.get("audio_stream") or {}
        alignment = item.get("alignment") or {}
        try:
            stream_index = int(audio_stream["index"])
            offset_seconds = float(alignment["offset_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"angle lacks selected audio/alignment data: {angle_id}") from exc
        record = {
            "id": angle_id,
            "role": item.get("role"),
            "status": item.get("status"),
            "media": info,
            "audio_stream_index": stream_index,
            "offset_seconds": _round4(offset_seconds),
            "clock_drift_status": (item.get("clock_drift") or {}).get("status"),
        }
        source_records.append(record)
        if angle_id not in speaker_map:
            continue
        if item.get("status") != "ready":
            blockers.append(f"speaker angle is not ready in sync plan: {angle_id}")
        if not info.get("has_video"):
            blockers.append(f"speaker angle has no video: {angle_id}")
        drift_status = record["clock_drift_status"]
        if drift_status not in {None, "not_requested", "reference", "stable"}:
            blockers.append(f"speaker angle clock drift is not ready: {angle_id} ({drift_status})")
        source_start = overlap_start - offset_seconds
        try:
            envelopes[angle_id] = decode_audio_envelope(
                str(path),
                sample_rate=8000,
                frame_ms=window_seconds * 1000.0,
                start_seconds=source_start,
                max_duration=overlap_end - overlap_start,
                audio_stream_index=stream_index,
            )
        except AudioSyncError as exc:
            raise ValueError(f"could not analyze selected audio for {angle_id}: {exc}") from exc

    program_record = next(item for item in source_records if item["id"] == program_audio)
    if not program_record["media"].get("has_audio"):
        blockers.append(f"program audio source has no audio: {program_audio}")
    if program_record.get("status") != "ready":
        blockers.append(f"program audio source is not ready in sync plan: {program_audio}")
    if program_record.get("clock_drift_status") not in {None, "not_requested", "reference", "stable"}:
        blockers.append(
            f"program audio clock drift is not ready: {program_audio} "
            f"({program_record.get('clock_drift_status')})"
        )
    output_path = Path(_absolute(delivery))
    if output_path.suffix.lower() not in {".mp4", ".mov"}:
        raise ValueError("delivery must use .mp4 or .mov")
    if output_path == sync_path:
        raise ValueError("delivery must not overwrite the sync plan")
    if output_path.is_symlink():
        raise ValueError("delivery must not be a symlink")
    if any(os.path.samefile(output_path, Path(item["media"]["path"])) for item in source_records if output_path.exists()):
        raise ValueError("delivery must not overwrite source media")
    if str(output_path) in {item["media"]["path"] for item in source_records}:
        raise ValueError("delivery must not overwrite source media")

    windows, metrics = choose_windows(
        envelopes,
        start_time=overlap_start,
        end_time=overlap_end,
        window_seconds=window_seconds,
        fallback_angle_id=fallback,
        min_activity_score=min_activity_score,
        dominance_margin=dominance_margin,
    )
    confident = int(metrics["confident_windows"])
    selected_ids = [angle_id for angle_id, count in metrics["raw_confident_winners"].items() if count]
    if confident < max(3, int(math.ceil(min_shot_seconds / window_seconds))):
        blockers.append("too few confident audio windows for automatic switching")
    if len(selected_ids) < 2:
        blockers.append("dominant audio evidence did not select at least two speaker angles")
    max_correlation = float(metrics["max_pairwise_level_correlation"])
    if max_correlation >= correlation_threshold:
        message = (
            "speaker audio envelopes are highly correlated; shared mix or strong bleed can make "
            "loudest-angle switching unreliable"
        )
        if allow_correlated_audio:
            warnings.append(message + " (explicitly allowed for this draft)")
        else:
            blockers.append(message)
    ambiguous_ratio = int(metrics["ambiguous_windows"]) / max(1, len(windows))
    if ambiguous_ratio > 0.5:
        warnings.append("more than half of analysis windows were ambiguous and held the previous angle")

    raw_runs = _collapse_windows(windows, overlap_end)
    folded = fold_short_runs(raw_runs, min_shot_seconds)
    source_by_id = {item["id"]: item for item in source_records}
    switches: List[Dict[str, Any]] = []
    for index, item in enumerate(folded):
        angle_id = str(item["angle_id"])
        matching_windows = [
            window for window in windows
            if float(window["start"]) < float(item["end"]) - 1e-9
            and float(window["end"]) > float(item["start"]) + 1e-9
        ]
        switches.append({
            "index": index,
            "start": _round4(float(item["start"])),
            "end": _round4(float(item["end"])),
            "duration": _round4(float(item["end"]) - float(item["start"])),
            "angle_id": angle_id,
            "speaker": speaker_map[angle_id],
            "source_start": _round4(float(item["start"]) - float(source_by_id[angle_id]["offset_seconds"])),
            "source_end": _round4(float(item["end"]) - float(source_by_id[angle_id]["offset_seconds"])),
            "analysis_windows": len(matching_windows),
            "confident_windows": sum(
                bool(window.get("confident"))
                and window.get("top_candidate_angle_id") == angle_id
                for window in matching_windows
            ),
        })

    reference_record = next(
        (item for item in source_records if item.get("role") == "reference" and item["media"].get("has_video")),
        next(item for item in source_records if item["id"] in speaker_map),
    )
    reference_media = reference_record["media"]
    if not reference_media.get("width") or not reference_media.get("height") or not reference_media.get("fps"):
        raise ValueError("reference video geometry or frame rate is unavailable")

    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "plan_id": None,
        "sync_plan": _fingerprint(sync_path),
        "settings": {
            "window_seconds": _round3(window_seconds),
            "min_shot_seconds": _round3(min_shot_seconds),
            "min_activity_score": round(min_activity_score, 5),
            "dominance_margin": round(dominance_margin, 5),
            "correlation_threshold": round(correlation_threshold, 6),
            "allow_correlated_audio": bool(allow_correlated_audio),
            "fallback_angle_id": fallback,
            "program_audio_angle_id": program_audio,
            "decision_model": "per-angle_p10_p95_normalized_energy_with_ambiguous_hold",
        },
        "sources": source_records,
        "speaker_map": dict(speaker_map),
        "common_overlap_in_reference": {
            "start": _round4(overlap_start),
            "end": _round4(overlap_end),
            "duration": _round4(overlap_end - overlap_start),
        },
        "analysis": {
            "status": "blocked" if blockers else "ready",
            "blockers": blockers,
            "warnings": warnings,
            "calibration": metrics["calibration"],
            "raw_confident_winners": metrics["raw_confident_winners"],
            "confident_windows": confident,
            "ambiguous_windows": int(metrics["ambiguous_windows"]),
            "ambiguous_ratio": round(ambiguous_ratio, 6),
            "pairwise_level_correlations": metrics["pairwise_level_correlations"],
            "max_pairwise_level_correlation": round(max_correlation, 6),
            "decision_windows": windows,
            "raw_runs": raw_runs,
        },
        "switches": switches,
        "delivery": {
            "output": {"path": str(output_path)},
            "width": int(reference_media["width"]),
            "height": int(reference_media["height"]),
            "fps": float(reference_media["fps"]),
            "video_encoder": "libx264",
            "video_crf": 18,
            "video_preset": "slow",
            "pixel_format": "yuv420p",
            "audio_encoder": "aac",
            "audio_bitrate_kbps": 192,
            "audio_sample_rate": 48000,
            "audio_channels": 2,
        },
        "application": {"status": "pending", "applied_at": None, "output": None, "validation": None},
        "review": {"status": "pending", "reviewed_at": None, "reviewer": None, "checks": {}},
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def _validate_fingerprint(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    path = Path(str(record.get("path") or ""))
    if not path.is_file():
        blockers.append(f"{label} is missing")
        return None
    if path.is_symlink():
        blockers.append(f"{label} must not be a symlink")
        return None
    if record.get("size_bytes") != path.stat().st_size:
        blockers.append(f"{label} size changed")
    if record.get("sha256") != _sha256(path):
        blockers.append(f"{label} sha256 changed")
    return path


def _base_blockers(plan: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    if plan.get("plan_id") != _plan_id(plan):
        blockers.append("plan_id does not match canonical plan content")
    _validate_fingerprint(plan.get("sync_plan") or {}, "sync plan", blockers)
    sources = plan.get("sources") or []
    for item in sources:
        media = item.get("media") if isinstance(item, Mapping) else {}
        _validate_fingerprint(media or {}, f"source {item.get('id')}", blockers)
    analysis = plan.get("analysis") or {}
    blockers.extend(str(value) for value in analysis.get("blockers") or [])
    switches = plan.get("switches") or []
    if not switches:
        blockers.append("switch plan is empty")
    overlap = plan.get("common_overlap_in_reference") or {}
    cursor = float(overlap.get("start") or 0.0)
    for item in switches:
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or 0.0)
        if abs(start - cursor) > 1e-3 or end <= start:
            blockers.append("switches do not form one contiguous positive-duration timeline")
            break
        cursor = end
    if switches and abs(cursor - float(overlap.get("end") or 0.0)) > 1e-3:
        blockers.append("switches do not cover the complete common overlap")
    return blockers


def _output_contract_blockers(plan: Mapping[str, Any], media: Mapping[str, Any]) -> List[str]:
    delivery = plan.get("delivery") or {}
    overlap = plan.get("common_overlap_in_reference") or {}
    blockers: List[str] = []
    duration_tolerance = max(0.12, 2.0 / float(delivery.get("fps") or 25.0))
    if abs(float(media.get("duration") or 0.0) - float(overlap.get("duration") or 0.0)) > duration_tolerance:
        blockers.append("rendered duration does not match common overlap")
    if int(media.get("width") or 0) != int(delivery.get("width") or 0):
        blockers.append("rendered width does not match delivery contract")
    if int(media.get("height") or 0) != int(delivery.get("height") or 0):
        blockers.append("rendered height does not match delivery contract")
    fps = float(media.get("fps") or 0.0)
    if abs(fps - float(delivery.get("fps") or 0.0)) > 0.02:
        blockers.append("rendered frame rate does not match delivery contract")
    if not media.get("has_audio"):
        blockers.append("rendered draft has no audio stream")
    return blockers


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers = _base_blockers(plan)
    warnings = [str(value) for value in (plan.get("analysis") or {}).get("warnings") or []]
    application = plan.get("application") or {}
    output_record = application.get("output") or {}
    output_path: Optional[Path] = None
    if application.get("status") != "applied":
        blockers.append(PENDING_APPLY)
    else:
        output_path = _validate_fingerprint(output_record, "application output", blockers)
        if output_path is not None:
            try:
                live_media = {**_fingerprint(output_path), **probe_media(output_path)}
                if output_record != live_media:
                    blockers.append("application output media record is not canonical")
                blockers.extend(_output_contract_blockers(plan, live_media))
            except (OSError, RuntimeError, ValueError) as exc:
                blockers.append(f"could not live-probe application output: {exc}")
        validation = application.get("validation") or {}
        if validation.get("plan_id") != plan.get("plan_id"):
            blockers.append("application validation is not bound to the current plan_id")
        if validation.get("output_sha256") != output_record.get("sha256"):
            blockers.append("application validation is not bound to the current output")
        if validation.get("full_decode") is not True:
            blockers.append("application output did not pass a full decode")

    review = plan.get("review") or {}
    if review.get("status") == "rejected":
        blockers.append(REJECTED_REVIEW)
    elif review.get("status") != "approved":
        blockers.append(PENDING_REVIEW)
    else:
        checks = review.get("checks") or {}
        if set(checks) != set(REVIEW_FIELDS) or any(checks.get(field) != "pass" for field in REVIEW_FIELDS):
            blockers.append("approved review must pass all canonical checks")
        if review.get("plan_id") != plan.get("plan_id"):
            blockers.append("review is not bound to the current plan_id")
        if review.get("output_sha256") != output_record.get("sha256"):
            blockers.append("review is not bound to the current output")
    unique_blockers = list(dict.fromkeys(blockers))
    return {
        "version": VERSION,
        "status": "ready" if not unique_blockers else "blocked",
        "blockers": unique_blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {"blocking": len(unique_blockers), "warnings": len(set(warnings))},
    }


def build_render_command(plan: Mapping[str, Any], output_path: str) -> List[str]:
    sources = plan.get("sources") or []
    source_index = {str(item["id"]): index for index, item in enumerate(sources)}
    source_by_id = {str(item["id"]): item for item in sources}
    delivery = plan.get("delivery") or {}
    width = int(delivery["width"])
    height = int(delivery["height"])
    fps = float(delivery["fps"])
    command: List[str] = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error"]
    for item in sources:
        command.extend(["-i", str((item.get("media") or {})["path"])])
    filters: List[str] = []
    labels: List[str] = []
    for index, switch in enumerate(plan.get("switches") or []):
        angle_id = str(switch["angle_id"])
        input_index = source_index[angle_id]
        filters.append(
            f"[{input_index}:v:0]trim=start={float(switch['source_start']):.6f}:"
            f"end={float(switch['source_end']):.6f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
            f"fps={fps:.6f},format=yuv420p[v{index}]"
        )
        labels.append(f"[v{index}]")
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vout]")
    audio_id = str((plan.get("settings") or {})["program_audio_angle_id"])
    audio = source_by_id[audio_id]
    audio_input = source_index[audio_id]
    overlap = plan.get("common_overlap_in_reference") or {}
    audio_start = float(overlap["start"]) - float(audio["offset_seconds"])
    audio_end = float(overlap["end"]) - float(audio["offset_seconds"])
    audio_stream = int(audio["audio_stream_index"])
    filters.append(
        f"[{audio_input}:a:{audio_stream}]atrim=start={audio_start:.6f}:end={audio_end:.6f},"
        "asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
    )
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", str(delivery["video_preset"]),
        "-crf", str(delivery["video_crf"]), "-pix_fmt", str(delivery["pixel_format"]),
        "-c:a", "aac", "-b:a", f"{int(delivery['audio_bitrate_kbps'])}k",
        "-ar", str(int(delivery["audio_sample_rate"])), "-ac", str(int(delivery["audio_channels"])),
        "-movflags", "+faststart", output_path,
    ])
    return command


def apply_plan(plan_path: Path, *, force: bool = False) -> Dict[str, Any]:
    plan = _load_json(plan_path)
    blockers = _base_blockers(plan)
    if blockers:
        raise ValueError("cannot apply blocked plan: " + "; ".join(blockers))
    output_path = Path(str((plan.get("delivery") or {}).get("output", {}).get("path") or ""))
    if output_path.is_symlink():
        raise ValueError("delivery output must not be a symlink")
    if output_path.resolve() == plan_path.resolve():
        raise ValueError("delivery output must not overwrite the plan")
    if output_path.exists():
        for item in plan.get("sources") or []:
            if os.path.samefile(output_path, Path(str((item.get("media") or {}).get("path") or ""))):
                raise ValueError("delivery output must not overwrite or hard-link a source")
        if not force:
            raise ValueError(f"delivery output already exists (pass --force to replace): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=str(output_path.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        command = build_render_command(plan, str(temporary))
        _run_checked(command, "multicam render")
        media = probe_media(temporary)
        contract_blockers = _output_contract_blockers(plan, media)
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(temporary), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            "full output decode",
        )
        os.replace(temporary, output_path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise

    output_record = {**_fingerprint(output_path), **probe_media(output_path)}
    plan["application"] = {
        "status": "applied",
        "applied_at": utc_now(),
        "output": output_record,
        "render_command": build_render_command(plan, str(output_path)),
        "validation": {
            "plan_id": plan["plan_id"],
            "output_sha256": output_record["sha256"],
            "full_decode": True,
            "contract_blockers": [],
        },
    }
    plan["review"] = {"status": "pending", "reviewed_at": None, "reviewer": None, "checks": {}}
    _atomic_write_json(plan_path, plan)
    return plan


def confirm_plan(plan_path: Path, *, reviewer: str, checks: Mapping[str, str]) -> Dict[str, Any]:
    plan = _load_json(plan_path)
    application = plan.get("application") or {}
    if application.get("status") != "applied":
        raise ValueError("apply the multicam switch plan before confirming review")
    if set(checks) != set(REVIEW_FIELDS) or any(value not in REVIEW_VALUES for value in checks.values()):
        raise ValueError("confirm requires pass/fail for every canonical review field")
    blockers = _base_blockers(plan)
    output = application.get("output") or {}
    _validate_fingerprint(output, "application output", blockers)
    if blockers:
        raise ValueError("cannot confirm stale or blocked plan: " + "; ".join(blockers))
    approved = all(value == "pass" for value in checks.values())
    plan["review"] = {
        "status": "approved" if approved else "rejected",
        "reviewed_at": utc_now(),
        "reviewer": reviewer,
        "plan_id": plan["plan_id"],
        "output_sha256": output.get("sha256"),
        "checks": dict(checks),
    }
    _atomic_write_json(plan_path, plan)
    return plan


def emit_markdown(plan: Mapping[str, Any], verification: Optional[Mapping[str, Any]] = None) -> str:
    verification = verification or verify_plan(plan)
    analysis = plan.get("analysis") or {}
    settings = plan.get("settings") or {}
    lines = [
        "# Multicam Switch Draft",
        "",
        f"- Status: `{verification.get('status')}`",
        f"- Plan ID: `{plan.get('plan_id')}`",
        f"- Program audio: `{settings.get('program_audio_angle_id')}`",
        f"- Window / minimum shot: `{settings.get('window_seconds')}s / {settings.get('min_shot_seconds')}s`",
        f"- Confident / ambiguous windows: `{analysis.get('confident_windows')} / {analysis.get('ambiguous_windows')}`",
        f"- Maximum pairwise audio-level correlation: `{analysis.get('max_pairwise_level_correlation')}`",
        "",
        "## Switches",
        "",
        "| # | reference range | angle | speaker | confident windows |",
        "|---:|---|---|---|---:|",
    ]
    for item in plan.get("switches") or []:
        lines.append(
            f"| {item.get('index')} | {float(item.get('start') or 0):.3f}–{float(item.get('end') or 0):.3f}s "
            f"| `{item.get('angle_id')}` | {item.get('speaker')} | {item.get('confident_windows')} |"
        )
    lines.extend(["", "## Blocking", ""])
    blockers = verification.get("blockers") or []
    lines.extend([f"- {item}" for item in blockers] or ["- None"])
    lines.extend(["", "## Warnings", ""])
    warnings = verification.get("warnings") or []
    lines.extend([f"- {item}" for item in warnings] or ["- None"])
    lines.extend([
        "",
        "## Review contract",
        "",
        "Watch the complete rendered draft at 1× with sound. Confirm that the framed person is the speaker, "
        "cuts land naturally, picture remains synchronized, and the chosen program audio stays continuous. "
        "Energy is only a draft-selection proxy; ambiguous windows deliberately hold the previous camera.",
        "",
    ])
    return "\n".join(lines)


def _write_markdown(
    path: Optional[str], plan: Mapping[str, Any], *, plan_path: Optional[Path] = None
) -> None:
    if path:
        candidate = Path(_absolute(path))
        if candidate.suffix.lower() != ".md" or candidate.is_symlink():
            raise ValueError("Markdown output must be a non-symlink .md file")
        forbidden = [
            Path(str((plan.get("sync_plan") or {}).get("path") or "")),
            Path(str((plan.get("delivery") or {}).get("output", {}).get("path") or "")),
            *(Path(str((item.get("media") or {}).get("path") or "")) for item in plan.get("sources") or []),
        ]
        if plan_path is not None:
            forbidden.append(plan_path)
        if candidate.resolve() in {item.resolve() for item in forbidden if str(item)}:
            raise ValueError("Markdown output must not overwrite a source, sync plan, JSON plan, or delivery")
        if candidate.exists() and any(
            item.exists() and os.path.samefile(candidate, item) for item in forbidden if str(item)
        ):
            raise ValueError("Markdown output must not hard-link a source, sync plan, JSON plan, or delivery")
        _atomic_write_text(candidate, emit_markdown(plan))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Analyze aligned speaker-camera audio and write a source-bound draft plan.")
    plan_parser.add_argument("--sync-plan", required=True)
    plan_parser.add_argument("--speaker", action="append", default=[], metavar="ANGLE_ID=LABEL", required=True)
    plan_parser.add_argument("--fallback-angle")
    plan_parser.add_argument("--program-audio")
    plan_parser.add_argument("--window", type=float, default=0.5)
    plan_parser.add_argument("--min-shot", type=float, default=1.5)
    plan_parser.add_argument("--min-activity", type=float, default=0.2)
    plan_parser.add_argument("--dominance-margin", type=float, default=0.12)
    plan_parser.add_argument("--correlation-threshold", type=float, default=0.985)
    plan_parser.add_argument("--allow-correlated-audio", action="store_true")
    plan_parser.add_argument("--delivery", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--markdown")
    plan_parser.add_argument("--strict", action="store_true")
    plan_parser.add_argument("--force", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Render and fully decode the planned multicam draft.")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--force", action="store_true")

    confirm_parser = subparsers.add_parser("confirm", help="Record the complete 1× human review.")
    confirm_parser.add_argument("plan")
    confirm_parser.add_argument("--reviewer", required=True)
    for field in REVIEW_FIELDS:
        confirm_parser.add_argument(f"--{field.replace('_', '-')}", required=True, choices=sorted(REVIEW_VALUES))
    confirm_parser.add_argument("--markdown")

    verify_parser = subparsers.add_parser("verify", help="Live-verify sources, output, decode receipt, and review binding.")
    verify_parser.add_argument("plan")
    verify_parser.add_argument("--markdown")
    verify_parser.add_argument("--strict", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                args.sync_plan,
                speaker_map=_parse_assignments(args.speaker, "--speaker"),
                delivery=args.delivery,
                fallback_angle_id=args.fallback_angle,
                program_audio_angle_id=args.program_audio,
                window_seconds=args.window,
                min_shot_seconds=args.min_shot,
                min_activity_score=args.min_activity,
                dominance_margin=args.dominance_margin,
                correlation_threshold=args.correlation_threshold,
                allow_correlated_audio=args.allow_correlated_audio,
            )
            output = Path(_absolute(args.output))
            markdown = Path(_absolute(args.markdown)) if args.markdown else None
            sync_plan = Path(_absolute(args.sync_plan))
            delivery = Path(_absolute(args.delivery))
            if output.suffix.lower() != ".json" or output.is_symlink():
                raise ValueError("plan output must be a non-symlink .json file")
            if output.exists() and not args.force:
                raise ValueError(f"plan output already exists (pass --force to replace): {output}")
            protected = [
                sync_plan,
                delivery,
                *(Path(str((item.get("media") or {}).get("path") or "")) for item in plan.get("sources") or []),
            ]
            forbidden = {item.resolve() for item in protected}
            if output.resolve() in forbidden:
                raise ValueError("plan output must not overwrite the sync plan or delivery")
            if output.exists() and any(item.exists() and os.path.samefile(output, item) for item in protected):
                raise ValueError("plan output must not hard-link a source, sync plan, or delivery")
            if markdown is not None:
                if markdown.suffix.lower() != ".md" or markdown.is_symlink():
                    raise ValueError("Markdown output must be a non-symlink .md file")
                if markdown.resolve() in forbidden or markdown == output:
                    raise ValueError("Markdown output must not overwrite the sync plan, plan, or delivery")
                if markdown.exists() and any(
                    item.exists() and os.path.samefile(markdown, item) for item in [*protected, output]
                ):
                    raise ValueError("Markdown output must not hard-link a source, sync plan, plan, or delivery")
                if markdown.exists() and not args.force:
                    raise ValueError(f"Markdown output already exists (pass --force to replace): {markdown}")
            _atomic_write_json(output, plan)
            _write_markdown(args.markdown, plan, plan_path=output)
            verification = verify_plan(plan)
            print(json.dumps(verification, ensure_ascii=False))
            if args.strict and (plan.get("analysis") or {}).get("blockers"):
                return 2
            return 0
        if args.command == "apply":
            plan_path = _plan_file(args.plan)
            plan = apply_plan(plan_path, force=bool(args.force))
            _write_markdown(args.markdown, plan, plan_path=plan_path)
            print(f"Rendered multicam draft: {(plan.get('delivery') or {}).get('output', {}).get('path')}")
            return 0
        if args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            plan_path = _plan_file(args.plan)
            plan = confirm_plan(plan_path, reviewer=args.reviewer, checks=checks)
            _write_markdown(args.markdown, plan, plan_path=plan_path)
            return 0 if (plan.get("review") or {}).get("status") == "approved" else 2
        plan_path = _plan_file(args.plan)
        plan = _load_json(plan_path)
        verification = verify_plan(plan)
        _write_markdown(args.markdown, plan, plan_path=plan_path)
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        if args.strict and verification.get("status") != "ready":
            return 2
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"multicam_switch error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
