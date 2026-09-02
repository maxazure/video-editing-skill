#!/usr/bin/env python3
"""Verify subtitle cue timing against an isolated speech track.

This local, read-only gate compares ``subtitle_pack.v1`` cue intervals with
audio activity measured by FFmpeg ``silencedetect``.  It is designed for an
isolated narration/dialogue bus, or a speech-dominant track without music or
effects.  It does not classify speech and must not be run on a full mix where
BGM could hide an orphan or mistimed caption.
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "caption_speech_qa.v1"
VERIFY_VERSION = "caption_speech_qa_verify.v1"
DEFAULT_SETTINGS: Mapping[str, Any] = {
    "noise_db": -36.0,
    "min_silence_seconds": 0.16,
    "min_active_ratio": 0.25,
    "warn_active_ratio": 0.50,
    "max_leading_silence_seconds": 0.60,
    "max_trailing_silence_seconds": 0.60,
    "max_internal_silence_seconds": 0.80,
    "timeline_tolerance_seconds": 0.05,
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
    "name": "ffmpeg_silencedetect_caption_interval_overlap",
    "audio_stream": "first audio stream (0:a:0)",
    "timed_text": "subtitle_pack.v1 output-timeline cues",
    "activity_model": "invert normalized FFmpeg silencedetect intervals",
    "cue_checks": [
        "audio activity exists during the cue",
        "minimum active-time ratio",
        "maximum leading and trailing silence",
        "warning for long internal silence",
        "cue stays inside the speech-track timeline",
    ],
    "rounding_decimals": 6,
    "automatic_repair": False,
    "speech_classification": False,
    "required_input_role": "isolated speech/dialogue/narration or speech-dominant track without BGM/SFX",
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
        # macOS exposes /tmp as /private/tmp. Accept that filesystem alias but
        # still reject project-local symlink traversal below.
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
        raise ValueError("speech source has no audio stream")
    format_data = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    duration = _finite(format_data.get("duration")) or _finite(audio.get("duration"))
    sample_rate = _finite(audio.get("sample_rate"))
    channels = _finite(audio.get("channels"))
    if duration is None or duration <= 0:
        raise ValueError("speech source duration must be positive")
    if sample_rate is None or sample_rate <= 0 or channels is None or int(channels) <= 0:
        raise ValueError("speech source has an invalid sample rate or channel count")
    return {
        "duration": round(duration, 6),
        "format_name": str(format_data.get("format_name") or ""),
        "audio_stream_index": int(audio.get("index") or 0),
        "audio_codec": str(audio.get("codec_name") or ""),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "channel_layout": str(audio.get("channel_layout") or ""),
    }


def load_subtitle_pack(path: Path | str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("subtitle pack must be a JSON object")
    if payload.get("version") != "subtitle_pack.v1":
        raise ValueError("subtitle input must be subtitle_pack.v1 JSON")
    raw_cues = payload.get("cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise ValueError("subtitle pack must contain a non-empty cues[] list")
    cues: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    previous_start: Optional[float] = None
    for index, raw in enumerate(raw_cues, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"subtitle cue {index} must be an object")
        start = _finite(raw.get("start"))
        end = _finite(raw.get("end"))
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
        if start is None or end is None or end <= start:
            raise ValueError(f"subtitle cue {index} has invalid start/end")
        if not text:
            raise ValueError(f"subtitle cue {index} has empty text")
        if previous_start is not None and start < previous_start:
            raise ValueError("subtitle cues must be ordered by non-decreasing start time")
        cue_id = str(raw.get("index") or raw.get("id") or index)
        if cue_id in seen_ids:
            raise ValueError(f"subtitle cue id must be unique: {cue_id}")
        seen_ids.add(cue_id)
        cues.append(
            {
                "id": cue_id,
                "index": index,
                "start": round(start, 6),
                "end": round(end, 6),
                "text": text,
            }
        )
        previous_start = start
    return dict(payload), cues


def normalize_settings(value: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    settings = dict(DEFAULT_SETTINGS)
    if value:
        unknown = sorted(set(value) - set(settings))
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)}")
        settings.update(value)
    normalized = {key: float(item) for key, item in settings.items()}
    blockers = validate_settings(normalized)
    if blockers:
        raise ValueError("; ".join(blockers))
    return normalized


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    noise = _finite(settings.get("noise_db"))
    min_silence = _finite(settings.get("min_silence_seconds"))
    min_ratio = _finite(settings.get("min_active_ratio"))
    warn_ratio = _finite(settings.get("warn_active_ratio"))
    leading = _finite(settings.get("max_leading_silence_seconds"))
    trailing = _finite(settings.get("max_trailing_silence_seconds"))
    internal = _finite(settings.get("max_internal_silence_seconds"))
    tolerance = _finite(settings.get("timeline_tolerance_seconds"))
    if noise is None or not -80 <= noise <= -10:
        blockers.append("settings.noise_db must be between -80 and -10 dBFS")
    if min_silence is None or not 0.05 <= min_silence <= 5:
        blockers.append("settings.min_silence_seconds must be between 0.05 and 5")
    if min_ratio is None or warn_ratio is None or not 0 <= min_ratio <= warn_ratio <= 1:
        blockers.append("settings active ratios must satisfy 0 <= min <= warn <= 1")
    if leading is None or not 0 <= leading <= 5:
        blockers.append("settings.max_leading_silence_seconds must be between 0 and 5")
    if trailing is None or not 0 <= trailing <= 5:
        blockers.append("settings.max_trailing_silence_seconds must be between 0 and 5")
    if internal is None or not 0 <= internal <= 10:
        blockers.append("settings.max_internal_silence_seconds must be between 0 and 10")
    if tolerance is None or not 0 <= tolerance <= 1:
        blockers.append("settings.timeline_tolerance_seconds must be between 0 and 1")
    return blockers


SILENCE_START_RE = re.compile(r"silence_start:\s*([-+0-9.eE]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([-+0-9.eE]+)")


def normalize_intervals(
    intervals: Sequence[Sequence[float]],
    *,
    duration: float,
) -> List[List[float]]:
    candidates: List[List[float]] = []
    for raw in intervals:
        if len(raw) != 2:
            continue
        start = _finite(raw[0])
        end = _finite(raw[1])
        if start is None or end is None:
            continue
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        if end <= start:
            continue
        candidates.append([start, end])
    normalized: List[List[float]] = []
    for start, end in sorted(candidates):
        if normalized and start <= normalized[-1][1] + 1e-6:
            normalized[-1][1] = max(normalized[-1][1], end)
        else:
            normalized.append([start, end])
    return [[round(start, 6), round(end, 6)] for start, end in normalized]


def parse_silence_log(log: str, *, duration: float) -> List[List[float]]:
    intervals: List[List[float]] = []
    open_start: Optional[float] = None
    for line in log.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            value = _finite(start_match.group(1))
            if value is not None and open_start is None:
                open_start = value
        end_match = SILENCE_END_RE.search(line)
        if end_match:
            value = _finite(end_match.group(1))
            if value is not None:
                intervals.append([open_start if open_start is not None else 0.0, value])
                open_start = None
    if open_start is not None:
        intervals.append([open_start, duration])
    return normalize_intervals(intervals, duration=duration)


def measure_silences(
    path: Path | str,
    *,
    duration: float,
    settings: Mapping[str, Any],
) -> List[List[float]]:
    noise = float(settings["noise_db"])
    minimum = float(settings["min_silence_seconds"])
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
            f"silencedetect=noise={noise:g}dB:d={minimum:g}",
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
        raise ValueError(detail[-1] if detail else "FFmpeg silence analysis failed")
    return parse_silence_log(result.stderr or result.stdout, duration=duration)


def active_intervals_from_silence(
    silences: Sequence[Sequence[float]],
    *,
    duration: float,
) -> List[List[float]]:
    result: List[List[float]] = []
    cursor = 0.0
    for start, end in normalize_intervals(silences, duration=duration):
        if start > cursor:
            result.append([round(cursor, 6), round(start, 6)])
        cursor = max(cursor, end)
    if cursor < duration:
        result.append([round(cursor, 6), round(duration, 6)])
    return result


def _overlap_intervals(
    intervals: Sequence[Sequence[float]],
    *,
    start: float,
    end: float,
) -> List[List[float]]:
    overlaps: List[List[float]] = []
    for interval_start, interval_end in intervals:
        left = max(start, float(interval_start))
        right = min(end, float(interval_end))
        if right > left:
            overlaps.append([round(left, 6), round(right, 6)])
    return overlaps


def analyze_cues(
    cues: Sequence[Mapping[str, Any]],
    *,
    silences: Sequence[Sequence[float]],
    duration: float,
    settings: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    active = active_intervals_from_silence(silences, duration=duration)
    tolerance = float(settings["timeline_tolerance_seconds"])
    cue_metrics: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []

    for cue in cues:
        cue_id = str(cue.get("id") or cue.get("index") or len(cue_metrics) + 1)
        start = float(cue["start"])
        end = float(cue["end"])
        cue_duration = end - start
        analysis_start = max(0.0, start)
        analysis_end = min(duration, end)
        overlaps = _overlap_intervals(active, start=analysis_start, end=analysis_end)
        active_seconds = sum(right - left for left, right in overlaps)
        active_ratio = active_seconds / cue_duration if cue_duration > 0 else 0.0
        leading_silence = cue_duration if not overlaps else max(0.0, overlaps[0][0] - start)
        trailing_silence = cue_duration if not overlaps else max(0.0, end - overlaps[-1][1])
        internal_gaps = [
            max(0.0, overlaps[index + 1][0] - overlaps[index][1])
            for index in range(len(overlaps) - 1)
        ]
        longest_internal = max(internal_gaps, default=0.0)
        metric = {
            "cue_id": cue_id,
            "cue_index": int(cue["index"]),
            "start": round(start, 6),
            "end": round(end, 6),
            "duration": round(cue_duration, 6),
            "text": str(cue["text"]),
            "active_seconds": round(active_seconds, 6),
            "active_ratio": round(active_ratio, 6),
            "leading_silence_seconds": round(leading_silence, 6),
            "trailing_silence_seconds": round(trailing_silence, 6),
            "longest_internal_silence_seconds": round(longest_internal, 6),
            "active_overlaps": overlaps,
        }
        cue_metrics.append(metric)

        status = "pass"
        messages: List[str] = []
        if start < -tolerance or end > duration + tolerance:
            status = "block"
            messages.append(f"cue leaves the {duration:.3f}s speech-track timeline")
        if active_seconds <= 1e-6:
            status = "block"
            messages.append("no measurable audio activity overlaps this visible cue")
        else:
            if active_ratio < float(settings["min_active_ratio"]):
                status = "block"
                messages.append(f"only {active_ratio:.1%} of the cue overlaps audio activity")
            elif active_ratio < float(settings["warn_active_ratio"]):
                status = "warn" if status == "pass" else status
                messages.append(f"only {active_ratio:.1%} of the cue overlaps audio activity")
            if leading_silence > float(settings["max_leading_silence_seconds"]):
                status = "block"
                messages.append(f"leading silence is {leading_silence:.3f}s")
            if trailing_silence > float(settings["max_trailing_silence_seconds"]):
                status = "block"
                messages.append(f"trailing silence is {trailing_silence:.3f}s")
            if longest_internal > float(settings["max_internal_silence_seconds"]):
                status = "warn" if status == "pass" else status
                messages.append(f"internal silence reaches {longest_internal:.3f}s")
        if not messages:
            messages.append(f"{active_ratio:.1%} active coverage with bounded cue edges")
        checks.append(
            {
                "name": "caption_speech_alignment",
                "cue_id": cue_id,
                "cue_index": int(cue["index"]),
                "status": status,
                "message": "; ".join(messages),
            }
        )

    active_seconds_total = sum(end - start for start, end in active)
    cue_union = normalize_intervals(
        [[max(0.0, float(cue["start"])), min(duration, float(cue["end"]))] for cue in cues],
        duration=duration,
    )
    captioned_active = 0.0
    for start, end in cue_union:
        captioned_active += sum(right - left for left, right in _overlap_intervals(active, start=start, end=end))
    analysis = {
        "silence_intervals": normalize_intervals(silences, duration=duration),
        "active_intervals": active,
        "active_seconds": round(active_seconds_total, 6),
        "captioned_active_seconds": round(captioned_active, 6),
        "captioned_active_ratio": round(captioned_active / active_seconds_total, 6) if active_seconds_total else 0.0,
        "cue_metrics": cue_metrics,
    }
    return analysis, checks


def summarize_checks(checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    blocking = sum(1 for item in checks if item.get("status") == "block")
    warnings = sum(1 for item in checks if item.get("status") == "warn")
    return {
        "status": "blocked" if blocking else "warn" if warnings else "ready",
        "blocking": blocking,
        "warnings": warnings,
        "cue_count": len(checks),
        "ready_cues": len(checks) - blocking - warnings,
    }


def _file_contract(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _source_contract(path: Path, *, root: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        **_file_contract(path, root=root),
        **{key: media.get(key) for key in MEDIA_KEYS},
    }


def _report_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "speech_source": report.get("speech_source"),
        "subtitle_pack": report.get("subtitle_pack"),
        "algorithm": report.get("algorithm"),
        "settings": report.get("settings"),
        "analysis": report.get("analysis"),
        "checks": report.get("checks"),
        "summary": report.get("summary"),
        "limitations": report.get("limitations"),
    }


def build_report(
    speech_source: Path | str,
    subtitle_pack: Path | str,
    *,
    project_dir: Path | str,
    settings: Optional[Mapping[str, Any]] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Sequence[Sequence[float]]]] = None,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    speech_path = _project_file(speech_source, root=root, label="speech source")
    subtitle_path = _project_file(subtitle_pack, root=root, label="subtitle pack")
    if _same_path_or_file(speech_path, subtitle_path):
        raise ValueError("speech source and subtitle pack must be different files")
    probe_fn = probe_fn or probe_audio_media
    measure_fn = measure_fn or measure_silences
    media = dict(probe_fn(speech_path))
    normalized_settings = normalize_settings(settings)
    subtitle_payload, cues = load_subtitle_pack(subtitle_path)
    silences = measure_fn(
        speech_path,
        duration=float(media["duration"]),
        settings=normalized_settings,
    )
    analysis, checks = analyze_cues(
        cues,
        silences=silences,
        duration=float(media["duration"]),
        settings=normalized_settings,
    )
    subtitle_contract = {
        **_file_contract(subtitle_path, root=root),
        "version": subtitle_payload.get("version"),
        "cue_count": len(cues),
    }
    report: Dict[str, Any] = {
        "schema": VERSION,
        "generated_at": utc_now(),
        "speech_source": _source_contract(speech_path, root=root, media=media),
        "subtitle_pack": subtitle_contract,
        "algorithm": {"id": ALGORITHM_ID, "contract": dict(ALGORITHM_CONTRACT)},
        "settings": normalized_settings,
        "analysis": analysis,
        "checks": checks,
        "summary": summarize_checks(checks),
        "limitations": [
            "FFmpeg amplitude activity is not speech recognition or forced alignment.",
            "Use an isolated narration/dialogue bus or a speech-dominant track without BGM/SFX; music and effects can create false passes.",
            "This gate catches gross orphan, offset, edge, and pause errors but does not prove word- or phoneme-level synchronization.",
            "Listen to the complete speech track while watching the final captions at normal speed before delivery.",
        ],
    }
    report["report_id"] = _canonical_sha256(_report_snapshot(report))
    return report


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[Path | str] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Sequence[Sequence[float]]]] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if not isinstance(report, Mapping):
        blockers.append("report must be a JSON object")
    if report.get("schema") != VERSION:
        blockers.append(f"report schema must be {VERSION}")
    root = Path(project_dir or ".").expanduser().resolve()
    source = report.get("speech_source") if isinstance(report.get("speech_source"), Mapping) else {}
    subtitles = report.get("subtitle_pack") if isinstance(report.get("subtitle_pack"), Mapping) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    try:
        if not source.get("path"):
            raise ValueError("report speech_source.path is missing")
        if not subtitles.get("path"):
            raise ValueError("report subtitle_pack.path is missing")
        current = build_report(
            str(source["path"]),
            str(subtitles["path"]),
            project_dir=root,
            settings=settings,
            probe_fn=probe_fn,
            measure_fn=measure_fn,
        )
    except Exception as exc:
        blockers.append(str(exc))
        current = None

    if current is not None:
        comparisons = (
            ("speech_source", "speech source bytes or media contract drifted"),
            ("subtitle_pack", "subtitle pack bytes or cue contract drifted"),
            ("algorithm", "algorithm contract drifted"),
            ("settings", "analysis settings drifted"),
            ("analysis", "live cue/audio measurements drifted"),
            ("checks", "derived cue checks drifted"),
            ("summary", "derived summary drifted"),
            ("limitations", "review limitations drifted"),
            ("report_id", "canonical report id drifted"),
        )
        for key, message in comparisons:
            if report.get(key) != current.get(key):
                blockers.append(message)
        for check in current.get("checks", []):
            if not isinstance(check, Mapping):
                continue
            message = str(check.get("message") or "caption/speech check")
            cue_id = str(check.get("cue_id") or "?")
            if check.get("status") == "block":
                blockers.append(f"caption/speech blocker at cue {cue_id}: {message}")
            elif check.get("status") == "warn":
                warnings.append(f"caption/speech warning at cue {cue_id}: {message}")
    blocking = len(blockers)
    status = "blocked" if blocking else "warn" if warnings else "ready"
    return {
        "schema": VERIFY_VERSION,
        "verified_at": utc_now(),
        "status": status,
        "report_id": report.get("report_id"),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {"status": status, "blocking": blocking, "warnings": len(set(warnings))},
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("speech_source") if isinstance(report.get("speech_source"), Mapping) else {}
    subtitles = report.get("subtitle_pack") if isinstance(report.get("subtitle_pack"), Mapping) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    lines = [
        "# Caption / Speech QA",
        "",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Speech source: `{source.get('path', '')}`",
        f"- Subtitle pack: `{subtitles.get('path', '')}`",
        f"- Cues: `{summary.get('cue_count', 0)}`",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Captioned active-audio ratio: `{float(analysis.get('captioned_active_ratio') or 0):.1%}`",
        "",
        "## Cue checks",
        "",
        "| Cue | Time | Active | Edge silence (L/R) | Status | Note |",
        "|---:|---:|---:|---:|---|---|",
    ]
    metrics = {
        str(item.get("cue_id")): item
        for item in analysis.get("cue_metrics", [])
        if isinstance(item, Mapping)
    }
    for check in report.get("checks", []):
        if not isinstance(check, Mapping):
            continue
        metric = metrics.get(str(check.get("cue_id")), {})
        note = str(check.get("message") or "").replace("|", "\\|")
        lines.append(
            f"| {check.get('cue_id', '')} | {float(metric.get('start') or 0):.3f}–{float(metric.get('end') or 0):.3f}s "
            f"| {float(metric.get('active_ratio') or 0):.1%} "
            f"| {float(metric.get('leading_silence_seconds') or 0):.3f}/{float(metric.get('trailing_silence_seconds') or 0):.3f}s "
            f"| **{str(check.get('status') or '').upper()}** | {note} |"
        )
    lines.extend(
        [
            "",
            "## Review boundary",
            "",
            "This is an amplitude-based timing gate, not speech recognition or forced alignment. Use an isolated speech/dialogue/narration track without BGM or SFX, then watch every caption with the complete final mix at normal speed. A ready report does not prove word- or phoneme-level sync.",
        ]
    )
    return "\n".join(lines) + "\n"


def _settings_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "noise_db": args.noise_db,
        "min_silence_seconds": args.min_silence_seconds,
        "min_active_ratio": args.min_active_ratio,
        "warn_active_ratio": args.warn_active_ratio,
        "max_leading_silence_seconds": args.max_leading_silence_seconds,
        "max_trailing_silence_seconds": args.max_trailing_silence_seconds,
        "max_internal_silence_seconds": args.max_internal_silence_seconds,
        "timeline_tolerance_seconds": args.timeline_tolerance_seconds,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit subtitle cue timing against an isolated speech track using FFmpeg audio activity."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyze subtitle cues against project-local isolated speech")
    analyze.add_argument("speech_source")
    analyze.add_argument("--subtitle-pack", required=True)
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--noise-db", type=float, default=DEFAULT_SETTINGS["noise_db"])
    analyze.add_argument("--min-silence-seconds", type=float, default=DEFAULT_SETTINGS["min_silence_seconds"])
    analyze.add_argument("--min-active-ratio", type=float, default=DEFAULT_SETTINGS["min_active_ratio"])
    analyze.add_argument("--warn-active-ratio", type=float, default=DEFAULT_SETTINGS["warn_active_ratio"])
    analyze.add_argument("--max-leading-silence-seconds", type=float, default=DEFAULT_SETTINGS["max_leading_silence_seconds"])
    analyze.add_argument("--max-trailing-silence-seconds", type=float, default=DEFAULT_SETTINGS["max_trailing_silence_seconds"])
    analyze.add_argument("--max-internal-silence-seconds", type=float, default=DEFAULT_SETTINGS["max_internal_silence_seconds"])
    analyze.add_argument("--timeline-tolerance-seconds", type=float, default=DEFAULT_SETTINGS["timeline_tolerance_seconds"])
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")
    verify = subparsers.add_parser("verify", help="Re-measure a saved report against live inputs")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.project_dir).expanduser().resolve()
        if args.command == "analyze":
            speech = _project_file(args.speech_source, root=root, label="speech source")
            subtitles = _project_file(args.subtitle_pack, root=root, label="subtitle pack")
            output = _safe_output(
                args.output,
                root=root,
                label="output",
                forbidden=[speech, subtitles],
                force=args.force,
            )
            markdown = None
            if args.markdown:
                markdown = _safe_output(
                    args.markdown,
                    root=root,
                    label="markdown",
                    forbidden=[speech, subtitles, output],
                    force=args.force,
                )
            report = build_report(
                speech,
                subtitles,
                project_dir=root,
                settings=_settings_from_args(args),
            )
            _atomic_write_json(output, report)
            if markdown is not None:
                _atomic_write_text(markdown, emit_markdown(report))
            print(
                f"Caption / speech QA: {report['summary']['status']} "
                f"(blocking={report['summary']['blocking']}, warnings={report['summary']['warnings']})"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path = _project_file(args.report, root=root, label="report")
        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
        verification = verify_report(report, root)
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
