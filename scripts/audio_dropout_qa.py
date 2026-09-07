#!/usr/bin/env python3
"""Detect brief digital audio dropouts and bind a normal-speed listening review.

The local screen measures the first audio stream in fixed mono windows and
finds very quiet, short runs surrounded by clearly active audio. Candidates are
triage signals: each receives a 1x WAV context clip and must be classified by a
human as ``dropout``, ``intentional_pause``, or ``uncertain``.

This command is read-only with respect to the source media. It does not classify
speech, repair audio, or replace whole-program loudness/long-silence checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


VERSION = "audio_dropout_qa.v1"
RESPONSE_VERSION = "audio_dropout_qa_response.v1"
VERIFY_VERSION = "audio_dropout_qa_verify.v1"
DECISIONS = {"dropout", "intentional_pause", "uncertain"}
DEFAULT_SETTINGS: Mapping[str, Any] = {
    "analysis_sample_rate": 16000,
    "window_ms": 20,
    "dropout_threshold_dbfs": -60.0,
    "context_threshold_dbfs": -38.0,
    "context_ms": 120,
    "min_context_active_ratio": 0.75,
    "min_dropout_ms": 40,
    "max_dropout_ms": 400,
    "min_depth_db": 24.0,
    "max_candidates": 24,
    "evidence_padding_seconds": 0.75,
}
MEDIA_KEYS = (
    "duration",
    "format_name",
    "audio_stream_index",
    "audio_codec",
    "sample_rate",
    "channels",
    "channel_layout",
)
ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "ffmpeg_astats_brief_dropout_screen",
    "audio_stream": "first audio stream (0:a:0), downmixed to mono for analysis",
    "sample": "fixed-size windows after deterministic resampling",
    "candidate": (
        "a dropout-threshold run within the configured duration bounds, with active "
        "context on both sides and the configured minimum level drop"
    ),
    "candidate_semantics": "locator only; silence is not automatically classified as damage",
    "human_review_required_when_candidates_exist": True,
    "automatic_repair": False,
    "speech_classification": False,
    "long_silence_measurement": False,
    "rounding_decimals": 6,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


ALGORITHM_ID = _canonical_sha256(ALGORITHM_CONTRACT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


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
        lexical = resolved
    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return lexical


def _project_file(raw_path: str | Path, *, root: Path, label: str) -> Path:
    path = _lexical_project_path(raw_path, root=root, label=label).resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


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
    path = _lexical_project_path(raw_path, root=root, label=label)
    for item in forbidden:
        if _same_path_or_file(path, item):
            raise ValueError(f"{label} must not overwrite bound input: {item}")
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing {label} without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
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


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value if value.endswith("\n") else value + "\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round_or_none(value: Any, digits: int = 6) -> Optional[float]:
    parsed = _finite(value)
    return round(parsed, digits) if parsed is not None else None


def probe_audio_media(path: Path | str) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "ffprobe failed")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    audio = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"), None)
    if not isinstance(audio, Mapping):
        raise ValueError("source has no audio stream")
    format_data = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    duration = _finite(format_data.get("duration")) or _finite(audio.get("duration"))
    sample_rate = _finite(audio.get("sample_rate"))
    channels = _finite(audio.get("channels"))
    if duration is None or duration <= 0:
        raise ValueError("source audio duration must be positive")
    if sample_rate is None or sample_rate <= 0 or channels is None or int(channels) <= 0:
        raise ValueError("source audio stream has an invalid sample rate or channel count")
    return {
        "duration": round(duration, 6),
        "format_name": str(format_data.get("format_name") or ""),
        "audio_stream_index": int(audio.get("index") or 0),
        "audio_codec": str(audio.get("codec_name") or ""),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "channel_layout": str(audio.get("channel_layout") or ""),
    }


def _source_contract(path: Path, *, root: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **{key: media.get(key) for key in MEDIA_KEYS},
    }


def normalize_settings(value: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if value:
        unknown = sorted(set(value) - set(settings))
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)}")
        settings.update(value)
    for key in (
        "analysis_sample_rate",
        "window_ms",
        "context_ms",
        "min_dropout_ms",
        "max_dropout_ms",
        "max_candidates",
    ):
        settings[key] = int(settings[key])
    for key in set(settings).difference(
        {
            "analysis_sample_rate",
            "window_ms",
            "context_ms",
            "min_dropout_ms",
            "max_dropout_ms",
            "max_candidates",
        }
    ):
        settings[key] = float(settings[key])
    blockers = validate_settings(settings)
    if blockers:
        raise ValueError("; ".join(blockers))
    return settings


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []

    def number(key: str) -> Optional[float]:
        return _finite(settings.get(key))

    rate = number("analysis_sample_rate")
    window = number("window_ms")
    dropout = number("dropout_threshold_dbfs")
    context = number("context_threshold_dbfs")
    context_ms = number("context_ms")
    active_ratio = number("min_context_active_ratio")
    minimum = number("min_dropout_ms")
    maximum = number("max_dropout_ms")
    depth = number("min_depth_db")
    candidates = number("max_candidates")
    padding = number("evidence_padding_seconds")
    if rate is None or int(rate) != rate or not 4000 <= rate <= 48000:
        blockers.append("settings.analysis_sample_rate must be an integer between 4000 and 48000")
    if window is None or int(window) != window or not 5 <= window <= 100:
        blockers.append("settings.window_ms must be an integer between 5 and 100")
    if dropout is None or not -120 <= dropout <= -40:
        blockers.append("settings.dropout_threshold_dbfs must be between -120 and -40")
    if context is None or not -80 <= context <= -10:
        blockers.append("settings.context_threshold_dbfs must be between -80 and -10")
    if dropout is not None and context is not None and dropout >= context:
        blockers.append("settings.dropout_threshold_dbfs must be lower than context_threshold_dbfs")
    if context_ms is None or int(context_ms) != context_ms or not 40 <= context_ms <= 1000:
        blockers.append("settings.context_ms must be an integer between 40 and 1000")
    if active_ratio is None or not 0.5 <= active_ratio <= 1:
        blockers.append("settings.min_context_active_ratio must be between 0.5 and 1")
    if minimum is None or maximum is None or int(minimum) != minimum or int(maximum) != maximum or not 10 <= minimum <= maximum <= 2000:
        blockers.append("settings dropout duration must satisfy 10 <= min <= max <= 2000 ms")
    if depth is None or not 3 <= depth <= 100:
        blockers.append("settings.min_depth_db must be between 3 and 100")
    if candidates is None or int(candidates) != candidates or not 1 <= candidates <= 200:
        blockers.append("settings.max_candidates must be an integer between 1 and 200")
    if padding is None or not 0.1 <= padding <= 5:
        blockers.append("settings.evidence_padding_seconds must be between 0.1 and 5")
    return blockers


FRAME_RE = re.compile(r"frame:(\d+)\s+pts:\S+\s+pts_time:([-+0-9.eE]+)")
META_RE = re.compile(r"lavfi\.astats\.Overall\.(RMS_level|Peak_level)=([^\s]+)")


def parse_analysis_log(log: str) -> List[Dict[str, Any]]:
    """Parse FFmpeg astats metadata into deterministic window records."""
    windows: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in log.splitlines():
        frame = FRAME_RE.search(line)
        if frame:
            if current is not None:
                windows.append(current)
            current = {"index": int(frame.group(1)), "time": round(float(frame.group(2)), 6)}
            continue
        if current is None:
            continue
        metric = META_RE.search(line)
        if not metric:
            continue
        key = "rms_dbfs" if metric.group(1) == "RMS_level" else "peak_dbfs"
        current[key] = _round_or_none(metric.group(2))
    if current is not None:
        windows.append(current)
    return windows


def measure_audio_windows(path: Path | str, *, settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    sample_rate = int(settings["analysis_sample_rate"])
    window_samples = max(1, round(sample_rate * int(settings["window_ms"]) / 1000))
    filters = (
        f"aresample={sample_rate},aformat=channel_layouts=mono,"
        f"asetnsamples=n={window_samples}:p=1,"
        "astats=metadata=1:reset=1:measure_perchannel=none:"
        "measure_overall=RMS_level+Peak_level,ametadata=mode=print"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            filters,
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(detail[-1] if detail else "FFmpeg dropout analysis failed")
    windows = parse_analysis_log(result.stderr or result.stdout)
    if not windows:
        raise ValueError("FFmpeg dropout analysis produced no metadata windows")
    return windows


def _level(value: Any, *, floor: float = -120.0) -> float:
    parsed = _finite(value)
    return parsed if parsed is not None else floor


def analyze_windows(
    windows: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    window_seconds = float(settings["window_ms"]) / 1000.0
    context_count = max(1, math.ceil(float(settings["context_ms"]) / float(settings["window_ms"])))
    dropout_threshold = float(settings["dropout_threshold_dbfs"])
    context_threshold = float(settings["context_threshold_dbfs"])
    min_active_ratio = float(settings["min_context_active_ratio"])
    min_duration = float(settings["min_dropout_ms"]) / 1000.0
    max_duration = float(settings["max_dropout_ms"]) / 1000.0
    min_depth = float(settings["min_depth_db"])
    maximum = int(settings["max_candidates"])
    active_windows = sum(1 for item in windows if _level(item.get("rms_dbfs")) >= context_threshold)
    quiet_windows = sum(1 for item in windows if _level(item.get("rms_dbfs")) <= dropout_threshold)
    runs: List[tuple[int, int]] = []
    start: Optional[int] = None
    for index, item in enumerate(windows):
        quiet = _level(item.get("rms_dbfs")) <= dropout_threshold
        if quiet and start is None:
            start = index
        elif not quiet and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(windows) - 1))

    raw_candidates: List[Dict[str, Any]] = []
    for run_start, run_end in runs:
        start_time = max(0.0, float(windows[run_start].get("time") or run_start * window_seconds))
        end_time = min(duration, float(windows[run_end].get("time") or run_end * window_seconds) + window_seconds)
        dropout_duration = max(0.0, end_time - start_time)
        if dropout_duration + 1e-9 < min_duration or dropout_duration - 1e-9 > max_duration:
            continue
        if run_start < context_count or run_end + context_count >= len(windows):
            continue
        before = windows[run_start - context_count : run_start]
        after = windows[run_end + 1 : run_end + 1 + context_count]
        before_levels = [_level(item.get("rms_dbfs")) for item in before]
        after_levels = [_level(item.get("rms_dbfs")) for item in after]
        before_ratio = sum(level >= context_threshold for level in before_levels) / len(before_levels)
        after_ratio = sum(level >= context_threshold for level in after_levels) / len(after_levels)
        if before_ratio < min_active_ratio or after_ratio < min_active_ratio:
            continue
        before_median = statistics.median(before_levels)
        after_median = statistics.median(after_levels)
        dropout_levels = [_level(windows[index].get("rms_dbfs")) for index in range(run_start, run_end + 1)]
        dropout_loudest = max(dropout_levels)
        depth = min(before_median, after_median) - dropout_loudest
        if depth < min_depth:
            continue
        raw_candidates.append(
            {
                "start_window": run_start,
                "end_window": run_end,
                "start_time": round(start_time, 6),
                "end_time": round(end_time, 6),
                "duration_ms": round(dropout_duration * 1000.0, 3),
                "dropout_loudest_dbfs": round(dropout_loudest, 3),
                "context_before_median_dbfs": round(before_median, 3),
                "context_after_median_dbfs": round(after_median, 3),
                "context_before_active_ratio": round(before_ratio, 6),
                "context_after_active_ratio": round(after_ratio, 6),
                "depth_db": round(depth, 3),
            }
        )
    raw_candidates.sort(key=lambda item: (item["start_time"], item["end_time"]))
    candidates = raw_candidates[:maximum]
    for index, candidate in enumerate(candidates, start=1):
        millis = int(round(float(candidate["start_time"]) * 1000))
        candidate["candidate_id"] = f"dropout-{index:03d}-{millis:08d}ms"
    analyzed_seconds = min(duration, len(windows) * window_seconds)
    return {
        "sample": {
            "windows": len(windows),
            "window_seconds": round(window_seconds, 6),
            "analyzed_seconds": round(analyzed_seconds, 6),
            "coverage_ratio": round(analyzed_seconds / duration, 6) if duration > 0 else 0.0,
        },
        "activity": {
            "active_windows": active_windows,
            "quiet_windows": quiet_windows,
            "active_ratio": round(active_windows / len(windows), 6) if windows else 0.0,
            "quiet_ratio": round(quiet_windows / len(windows), 6) if windows else 0.0,
        },
        "candidates": candidates,
        "candidate_count_before_limit": len(raw_candidates),
        "truncated": len(raw_candidates) > maximum,
    }


def analyze_audio(
    path: Path | str,
    *,
    media: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    windows = measure_audio_windows(path, settings=settings)
    return analyze_windows(windows, duration=float(media["duration"]), settings=settings)


def generate_candidate_evidence(
    source: Path,
    output: Path,
    *,
    candidate: Mapping[str, Any],
    media_duration: float,
    padding: float,
    force: bool = False,
) -> None:
    if output.exists() and not force:
        raise ValueError(f"refusing to overwrite candidate evidence without --force: {output}")
    start = max(0.0, float(candidate["start_time"]) - padding)
    end = min(media_duration, float(candidate["end_time"]) + padding)
    clip_duration = max(0.05, end - start)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source),
        "-t",
        f"{clip_duration:.6f}",
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "FFmpeg evidence extraction failed").strip()
        raise ValueError(detail.splitlines()[-1] if detail else "FFmpeg evidence extraction failed")


def _scan_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "version": report.get("version"),
            "project_dir": report.get("project_dir"),
            "source": report.get("source"),
            "algorithm": report.get("algorithm"),
            "settings": report.get("settings"),
            "analysis": report.get("analysis"),
            "evidence": report.get("evidence"),
        }
    )


def _report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "scan_id": report.get("scan_id"),
            "response": report.get("response"),
            "reviews": report.get("reviews"),
            "status": report.get("status"),
            "summary": report.get("summary"),
            "blockers": report.get("blockers"),
            "warnings": report.get("warnings"),
            "limitations": report.get("limitations"),
        }
    )


def _response_template(scan_id: str, candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "scan_id": scan_id,
        "reviewed_by": "",
        "full_track_played_at_1x": None,
        "reviews": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "decision": "",
                "audible_observations": {"before": "", "during": "", "after": ""},
                "reason": "",
                "repair_action": "",
            }
            for candidate in candidates
        ],
    }


def _review_snapshot(
    report: Mapping[str, Any],
    response: Optional[Mapping[str, Any]],
    *,
    scan_blockers: Sequence[str] = (),
) -> Dict[str, Any]:
    blockers = list(scan_blockers)
    warnings: List[str] = []
    candidates = {
        str(item.get("candidate_id") or ""): item
        for item in (report.get("analysis") or {}).get("candidates") or []
        if isinstance(item, Mapping)
    }
    reviews: List[Dict[str, Any]] = []
    if not candidates:
        if response:
            blockers.append("response must be omitted when the automatic screen found no candidates")
    elif not isinstance(response, Mapping):
        blockers.append(f"{len(candidates)} audio dropout candidate(s) require explicit listening review")
    else:
        if response.get("version") != RESPONSE_VERSION:
            blockers.append(f"response version must be {RESPONSE_VERSION}")
        if str(response.get("scan_id") or "") != str(report.get("scan_id") or ""):
            blockers.append("response scan_id does not match the analyzed audio")
        if not str(response.get("reviewed_by") or "").strip():
            blockers.append("reviewed_by is required (label only; not identity authentication)")
        if response.get("full_track_played_at_1x") is not True:
            blockers.append("full_track_played_at_1x must be true")
        raw_reviews = response.get("reviews") or []
        if not isinstance(raw_reviews, list):
            blockers.append("response reviews must be a list")
            raw_reviews = []
        provided: Dict[str, Mapping[str, Any]] = {}
        for raw in raw_reviews:
            if not isinstance(raw, Mapping):
                blockers.append("response reviews must contain objects")
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            if candidate_id in provided:
                blockers.append(f"duplicate response review for {candidate_id}")
            provided[candidate_id] = raw
        for missing in sorted(set(candidates).difference(provided)):
            blockers.append(f"missing response review for {missing}")
        for extra in sorted(set(provided).difference(candidates)):
            blockers.append(f"response contains unknown candidate id {extra}")

        for candidate_id in candidates:
            raw = provided.get(candidate_id, {})
            decision = str(raw.get("decision") or "").strip().lower()
            observations = raw.get("audible_observations") or {}
            errors: List[str] = []
            if decision not in DECISIONS:
                errors.append(f"decision must be one of {sorted(DECISIONS)}")
            if not isinstance(observations, Mapping):
                observations = {}
                errors.append("audible_observations must be an object")
            normalized_observations = {
                key: str(observations.get(key) or "").strip() for key in ("before", "during", "after")
            }
            for key, value in normalized_observations.items():
                if not value:
                    errors.append(f"audible_observations.{key} is required")
            reason = str(raw.get("reason") or "").strip()
            repair_action = str(raw.get("repair_action") or "").strip()
            if not reason:
                errors.append("reason is required")
            if decision in {"dropout", "uncertain"} and not repair_action:
                errors.append(f"{decision or 'non-pass'} decision requires repair_action")
            review = {
                "candidate_id": candidate_id,
                "decision": decision,
                "audible_observations": normalized_observations,
                "reason": reason,
                "repair_action": repair_action,
                "validation_errors": sorted(set(errors)),
            }
            reviews.append(review)
            blockers.extend(f"{candidate_id}: {error}" for error in review["validation_errors"])
            if not review["validation_errors"]:
                if decision == "dropout":
                    blockers.append(f"{candidate_id}: confirmed audio dropout requires repair")
                elif decision == "uncertain":
                    blockers.append(f"{candidate_id}: uncertain audio continuity requires repair or escalation")
                elif decision == "intentional_pause":
                    warnings.append(f"{candidate_id}: detector candidate accepted as an intentional pause")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    summary = {
        "candidates": len(candidates),
        "intentional_pauses": sum(
            1 for item in reviews if item["decision"] == "intentional_pause" and not item["validation_errors"]
        ),
        "confirmed_dropouts": sum(
            1 for item in reviews if item["decision"] == "dropout" and not item["validation_errors"]
        ),
        "uncertain": sum(1 for item in reviews if item["decision"] == "uncertain" and not item["validation_errors"]),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "reviews": reviews,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
    }


def _scan_blockers(analysis: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    sample = analysis.get("sample") if isinstance(analysis.get("sample"), Mapping) else {}
    coverage = _finite(sample.get("coverage_ratio")) or 0.0
    if coverage < 0.98:
        blockers.append(f"audio analysis covered only {coverage:.1%} of the source timeline")
    if analysis.get("truncated"):
        blockers.append(
            f"candidate limit hid {int(analysis.get('candidate_count_before_limit') or 0) - len(analysis.get('candidates') or [])} candidate(s)"
        )
    return blockers


def build_report(
    source: str | Path,
    *,
    project_dir: str | Path,
    evidence_dir: str | Path,
    settings: Optional[Mapping[str, Any]] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
    evidence_fn: Optional[Callable[..., None]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_audio_media
    analyze_fn = analyze_fn or analyze_audio
    evidence_fn = evidence_fn or generate_candidate_evidence
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source_path = _project_file(source, root=root, label="source audio/video")
    evidence_root = _lexical_project_path(evidence_dir, root=root, label="evidence directory")
    if evidence_root.exists() and not evidence_root.is_dir():
        raise ValueError(f"evidence directory is not a directory: {evidence_root}")
    evidence_root.mkdir(parents=True, exist_ok=True)
    normalized_settings = normalize_settings(settings)
    media = dict(probe_fn(source_path))
    analysis = dict(analyze_fn(source_path, media=media, settings=normalized_settings))
    evidence: List[Dict[str, Any]] = []
    for candidate in analysis.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        output = evidence_root / f"{candidate_id}_context.wav"
        if output.is_symlink():
            raise ValueError(f"candidate evidence must not be a symlink: {output}")
        if _same_path_or_file(output, source_path):
            raise ValueError("candidate evidence must not overwrite source media")
        evidence_fn(
            source_path,
            output,
            candidate=candidate,
            media_duration=float(media["duration"]),
            padding=float(normalized_settings["evidence_padding_seconds"]),
            force=force,
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError(f"candidate evidence was not created: {output}")
        evidence.append(
            {
                "candidate_id": candidate_id,
                "path": _relative(output.resolve(), root),
                "sha256": _sha256(output),
                "size_bytes": output.stat().st_size,
                "format": "mono PCM WAV, 48 kHz, normal speed",
            }
        )
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": _source_contract(source_path, root=root, media=media),
        "algorithm": {"id": ALGORITHM_ID, "contract": dict(ALGORITHM_CONTRACT)},
        "settings": normalized_settings,
        "analysis": analysis,
        "evidence": evidence,
        "response": None,
        "reviews": [],
        "limitations": [
            "This screen finds brief near-silent gaps between active windows; it does not classify speech or prove a defect.",
            "Noise gates, deliberate micro-pauses, breath edits, BGM, and SFX can cause false positives or mask a dropout.",
            "Prefer the final isolated dialogue/narration stem; if only the mix exists, disclose that limitation.",
            "Listen to the complete source and every evidence clip at normal speed before delivery.",
            "Run audio_master_report.py separately for LUFS, true peak, LRA, and long silence.",
        ],
    }
    report["scan_id"] = _scan_id(report)
    report["response_template"] = _response_template(report["scan_id"], analysis.get("candidates") or [])
    snapshot = _review_snapshot(report, None, scan_blockers=_scan_blockers(analysis))
    report.update(snapshot)
    report["report_id"] = _report_id(report)
    return report


def _verify_scan(
    report: Mapping[str, Any],
    *,
    project_dir: Optional[str | Path],
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> List[str]:
    blockers: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"report version must be {VERSION}")
    root = Path(project_dir or report.get("project_dir") or ".").expanduser().resolve()
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    try:
        source_path = _project_file(str(source.get("path") or ""), root=root, label="source audio/video")
        probe_fn = probe_fn or probe_audio_media
        analyze_fn = analyze_fn or analyze_audio
        media = dict(probe_fn(source_path))
        expected_source = _source_contract(source_path, root=root, media=media)
        if source != expected_source:
            blockers.append("source bytes or audio media contract drifted")
        if report.get("algorithm") != {"id": ALGORITHM_ID, "contract": dict(ALGORITHM_CONTRACT)}:
            blockers.append("algorithm contract drifted")
        settings = normalize_settings(report.get("settings") if isinstance(report.get("settings"), Mapping) else {})
        live_analysis = dict(analyze_fn(source_path, media=media, settings=settings))
        if report.get("analysis") != live_analysis:
            blockers.append("live dropout measurements or candidates drifted")
    except Exception as exc:
        blockers.append(str(exc))
        root = None

    if root is not None:
        candidate_ids = {
            str(item.get("candidate_id") or "")
            for item in (report.get("analysis") or {}).get("candidates") or []
            if isinstance(item, Mapping)
        }
        evidence_items = report.get("evidence") if isinstance(report.get("evidence"), list) else []
        evidence_ids: set[str] = set()
        for item in evidence_items:
            if not isinstance(item, Mapping):
                blockers.append("evidence entries must be objects")
                continue
            candidate_id = str(item.get("candidate_id") or "")
            if candidate_id in evidence_ids:
                blockers.append(f"duplicate evidence for {candidate_id}")
            evidence_ids.add(candidate_id)
            try:
                evidence_path = _project_file(str(item.get("path") or ""), root=root, label="dropout evidence")
                if item.get("sha256") != _sha256(evidence_path) or int(item.get("size_bytes") or -1) != evidence_path.stat().st_size:
                    blockers.append(f"evidence bytes changed for {candidate_id}")
            except Exception as exc:
                blockers.append(str(exc))
        for missing in sorted(candidate_ids.difference(evidence_ids)):
            blockers.append(f"missing evidence for {missing}")
        for extra in sorted(evidence_ids.difference(candidate_ids)):
            blockers.append(f"evidence contains unknown candidate id {extra}")

    if str(report.get("scan_id") or "") != _scan_id(report):
        blockers.append("scan_id does not match source, settings, analysis, and evidence")
    template = _response_template(str(report.get("scan_id") or ""), (report.get("analysis") or {}).get("candidates") or [])
    if report.get("response_template") != template:
        blockers.append("response template drifted")
    return sorted(set(blockers))


def audit_report(
    report: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    project_dir: Optional[str | Path] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    audited = json.loads(json.dumps(report))
    scan_blockers = _verify_scan(audited, project_dir=project_dir, probe_fn=probe_fn, analyze_fn=analyze_fn)
    audited["response"] = json.loads(json.dumps(response))
    snapshot = _review_snapshot(audited, audited["response"], scan_blockers=scan_blockers + _scan_blockers(audited.get("analysis") or {}))
    audited.update(snapshot)
    audited["report_id"] = _report_id(audited)
    return audited


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[str | Path] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    scan_blockers = _verify_scan(report, project_dir=project_dir, probe_fn=probe_fn, analyze_fn=analyze_fn)
    canonical = _review_snapshot(report, report.get("response"), scan_blockers=scan_blockers + _scan_blockers(report.get("analysis") or {}))
    blockers = list(canonical["blockers"])
    for key in ("status", "reviews", "summary", "blockers", "warnings"):
        if report.get(key) != canonical.get(key):
            blockers.append(f"stored {key} does not match live audio dropout audit")
    if str(report.get("report_id") or "") != _report_id(report):
        blockers.append("report_id does not match stored report content")
    blockers = sorted(set(blockers))
    warnings = sorted(set(canonical["warnings"]))
    summary = dict(canonical["summary"])
    summary["blocking"] = len(blockers)
    summary["warnings"] = len(warnings)
    status = "blocked" if blockers else ("warn" if warnings else "ready")
    return {
        "version": VERIFY_VERSION,
        "verified_at": utc_now(),
        "status": status,
        "report_id": report.get("report_id"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    sample = analysis.get("sample") if isinstance(analysis.get("sample"), Mapping) else {}
    evidence = {str(item.get("candidate_id") or ""): item for item in report.get("evidence") or []}
    reviews = {str(item.get("candidate_id") or ""): item for item in report.get("reviews") or []}
    lines = [
        "# Audio Dropout QA",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Scan ID: `{report.get('scan_id', '')}`",
        f"- Coverage: {float(sample.get('coverage_ratio') or 0):.1%} in {sample.get('windows', 0)} windows",
        f"- Candidates: {(report.get('summary') or {}).get('candidates', 0)}",
        "",
        "## Candidate evidence",
        "",
        "| Candidate | Time | Duration | Context before / after | Depth | Evidence | Decision |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for candidate in analysis.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        path = (evidence.get(candidate_id) or {}).get("path", "")
        decision = (reviews.get(candidate_id) or {}).get("decision", "pending") or "pending"
        lines.append(
            f"| {candidate_id} | {float(candidate.get('start_time') or 0):.3f}-{float(candidate.get('end_time') or 0):.3f}s "
            f"| {float(candidate.get('duration_ms') or 0):.0f} ms "
            f"| {float(candidate.get('context_before_median_dbfs') or 0):.1f} / {float(candidate.get('context_after_median_dbfs') or 0):.1f} dBFS "
            f"| {float(candidate.get('depth_db') or 0):.1f} dB | `{path}` | {decision} |"
        )
    if not analysis.get("candidates"):
        lines.append("| none | - | - | - | - | - | automatic screen found no candidate |")
    lines.extend(
        [
            "",
            "## Listening contract",
            "",
            "- Play the complete track at 1x and listen to every context WAV at 1x.",
            "- Describe what is audible before, during, and after the marked gap.",
            "- Use `intentional_pause` only for a clean, expected pause; it remains a documented warning.",
            "- Use `dropout` for confirmed digital silence, chopped speech, or a broken join; provide a repair action.",
            "- Use `uncertain` when the gap cannot be judged confidently; it remains blocking.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    return "\n".join(lines) + "\n"


def _settings_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "analysis_sample_rate": args.analysis_sample_rate,
        "window_ms": args.window_ms,
        "dropout_threshold_dbfs": args.dropout_threshold_dbfs,
        "context_threshold_dbfs": args.context_threshold_dbfs,
        "context_ms": args.context_ms,
        "min_context_active_ratio": args.min_context_active_ratio,
        "min_dropout_ms": args.min_dropout_ms,
        "max_dropout_ms": args.max_dropout_ms,
        "min_depth_db": args.min_depth_db,
        "max_candidates": args.max_candidates,
        "evidence_padding_seconds": args.evidence_padding_seconds,
    }


def _analyze_command(args: argparse.Namespace) -> int:
    root = Path(args.project_dir).expanduser().resolve()
    source = _project_file(args.source, root=root, label="source audio/video")
    output = _safe_output(args.output, root=root, label="report output", forbidden=[source], force=args.force)
    markdown = _safe_output(args.markdown, root=root, label="Markdown output", forbidden=[source, output], force=args.force) if args.markdown else None
    response_template = _safe_output(args.response_template, root=root, label="response template output", forbidden=[source, output], force=args.force) if args.response_template else None
    report = build_report(
        source,
        project_dir=root,
        evidence_dir=args.evidence_dir,
        settings=_settings_from_args(args),
        force=args.force,
    )
    _atomic_write_json(output, report)
    if markdown:
        _atomic_write_text(markdown, emit_markdown(report))
    if response_template:
        _atomic_write_json(response_template, report["response_template"])
    summary = report["summary"]
    print(
        f"Audio dropout QA: status={report['status']} candidates={summary['candidates']} "
        f"blocking={summary['blocking']} warnings={summary['warnings']}"
    )
    return 2 if args.strict and summary["blocking"] else 0


def _audit_command(args: argparse.Namespace) -> int:
    raw_report = _load_json(args.report)
    root = Path(args.project_dir or str(raw_report.get("project_dir") or ".")).expanduser().resolve()
    report_path = _project_file(args.report, root=root, label="analysis report")
    response_path = _project_file(args.response, root=root, label="listening response")
    source = _project_file(str((raw_report.get("source") or {}).get("path") or ""), root=root, label="source audio/video")
    output = _safe_output(args.output, root=root, label="audit output", forbidden=[source, report_path, response_path], force=args.force)
    markdown = _safe_output(args.markdown, root=root, label="Markdown output", forbidden=[source, report_path, response_path, output], force=args.force) if args.markdown else None
    audited = audit_report(raw_report, _load_json(response_path), project_dir=root)
    _atomic_write_json(output, audited)
    if markdown:
        _atomic_write_text(markdown, emit_markdown(audited))
    summary = audited["summary"]
    print(
        f"Audio dropout audit: status={audited['status']} candidates={summary['candidates']} "
        f"blocking={summary['blocking']} warnings={summary['warnings']}"
    )
    return 2 if args.strict and summary["blocking"] else 0


def _verify_command(args: argparse.Namespace) -> int:
    report = _load_json(args.report)
    verification = verify_report(report, args.project_dir)
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 2 if args.strict and verification["summary"]["blocking"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect brief audio dropouts and bind normal-speed listening evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Run the local screen and export normal-speed WAV evidence.")
    analyze.add_argument("source", help="Project-local final speech stem, speech-dominant mix, or delivery media.")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--evidence-dir", default="verify/audio_dropout_clips")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--response-template")
    analyze.add_argument("--analysis-sample-rate", type=int, default=DEFAULT_SETTINGS["analysis_sample_rate"])
    analyze.add_argument("--window-ms", type=int, default=DEFAULT_SETTINGS["window_ms"])
    analyze.add_argument("--dropout-threshold-dbfs", type=float, default=DEFAULT_SETTINGS["dropout_threshold_dbfs"])
    analyze.add_argument("--context-threshold-dbfs", type=float, default=DEFAULT_SETTINGS["context_threshold_dbfs"])
    analyze.add_argument("--context-ms", type=int, default=DEFAULT_SETTINGS["context_ms"])
    analyze.add_argument("--min-context-active-ratio", type=float, default=DEFAULT_SETTINGS["min_context_active_ratio"])
    analyze.add_argument("--min-dropout-ms", type=int, default=DEFAULT_SETTINGS["min_dropout_ms"])
    analyze.add_argument("--max-dropout-ms", type=int, default=DEFAULT_SETTINGS["max_dropout_ms"])
    analyze.add_argument("--min-depth-db", type=float, default=DEFAULT_SETTINGS["min_depth_db"])
    analyze.add_argument("--max-candidates", type=int, default=DEFAULT_SETTINGS["max_candidates"])
    analyze.add_argument("--evidence-padding-seconds", type=float, default=DEFAULT_SETTINGS["evidence_padding_seconds"])
    analyze.add_argument("--strict", action="store_true")
    analyze.add_argument("--force", action="store_true")
    analyze.set_defaults(func=_analyze_command)

    audit = subparsers.add_parser("audit", help="Bind a completed listening response to the analyzed source and WAV evidence.")
    audit.add_argument("--report", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--project-dir")
    audit.add_argument("--markdown")
    audit.add_argument("--strict", action="store_true")
    audit.add_argument("--force", action="store_true")
    audit.set_defaults(func=_audit_command)

    verify = subparsers.add_parser("verify", help="Re-measure and reject source, evidence, response, or report drift.")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir")
    verify.add_argument("--strict", action="store_true")
    verify.set_defaults(func=_verify_command)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
