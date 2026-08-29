#!/usr/bin/env python3
"""Measure phrase-level narration loudness consistency.

The input media must be the finalized isolated narration track (audio or video)
after per-phrase processing, plus a project-local JSON manifest containing at
least two exact ``start``/``end`` ranges.  FFmpeg's ebur128 filter measures each
range independently so a program-wide loudness pass cannot hide one phrase
that is substantially louder or quieter than its neighbors.

This is a deterministic delivery gate, not a voice-performance evaluator.  It
does not judge timbre, pronunciation, cadence, artifacts, or dialogue/BGM
balance, and it does not replace the final-program audio master report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from audio_master_report import parse_ebur128_summary


VERSION = "narration_loudness_qa.v1"
VERIFY_VERSION = "narration_loudness_qa_verify.v1"
DEFAULT_TARGET_LUFS = -18.0
DEFAULT_TOLERANCE_LU = 2.0
DEFAULT_MAX_SPREAD_LU = 1.0
DEFAULT_MAX_TRUE_PEAK_DBTP = -2.0
DEFAULT_MAX_LRA_LU = 5.0
DEFAULT_MIN_SEGMENT_SECONDS = 0.5
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
    "name": "phrase_scoped_ffmpeg_ebur128",
    "audio_stream": "first audio stream (0:a:0)",
    "filter": "ebur128=peak=true",
    "measurements": ["integrated_lufs", "true_peak_dbtp", "lra_lu"],
    "comparison": "non-exempt phrase integrated-loudness max minus min",
    "rounding_decimals": 2,
    "performance_evaluation": False,
    "final_program_mix_evaluation": False,
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
        # macOS exposes /tmp as /private/tmp. Accept that filesystem alias while
        # still rejecting project-local symlink traversal below.
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
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
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


def _round_or_none(value: Any, digits: int = 2) -> Optional[float]:
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
    if duration is None or duration <= 0:
        raise ValueError("source audio duration must be positive")
    sample_rate = _finite(audio.get("sample_rate"))
    channels = _finite(audio.get("channels"))
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


def _file_contract(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _clean_text(value: Any) -> str:
    return " ".join(str("" if value is None else value).replace("\ufeff", "").split())


def _normalize_exception(value: Any, *, segment_id: str) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"segment {segment_id} loudness_exception must be an object")
    unknown = sorted(set(value) - {"reason", "reviewer"})
    if unknown:
        raise ValueError(f"segment {segment_id} loudness_exception has unknown fields: {', '.join(unknown)}")
    reason = _clean_text(value.get("reason"))
    reviewer = _clean_text(value.get("reviewer"))
    if not reason or not reviewer:
        raise ValueError(f"segment {segment_id} loudness_exception requires reason and reviewer")
    return {"reason": reason, "reviewer": reviewer}


def normalize_segments(payload: Mapping[str, Any], *, source_duration: float) -> List[Dict[str, Any]]:
    collection = payload.get("segments")
    if not isinstance(collection, list):
        collection = payload.get("phrases")
    if not isinstance(collection, list):
        raise ValueError("segment manifest must contain segments[] or phrases[]")
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(collection, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"segment {index} must be an object")
        segment_id = _clean_text(item.get("id") if item.get("id") is not None else index)
        if not segment_id or segment_id in seen:
            raise ValueError(f"segment id must be non-empty and unique: {segment_id!r}")
        seen.add(segment_id)
        start = _finite(item.get("start"))
        end = _finite(item.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            raise ValueError(f"segment {segment_id} has an invalid start/end range")
        if end > float(source_duration) + 0.05:
            raise ValueError(
                f"segment {segment_id} ends at {end:.3f}s beyond source duration {source_duration:.3f}s"
            )
        normalized.append(
            {
                "id": segment_id,
                "start": round(start, 6),
                "end": round(end, 6),
                "text": _clean_text(item.get("text")),
                "speaker": _clean_text(item.get("speaker") or item.get("speaker_id")),
                "loudness_exception": _normalize_exception(
                    item.get("loudness_exception"), segment_id=segment_id
                ),
            }
        )
    if len(normalized) < 2:
        raise ValueError("segment manifest must contain at least two narration ranges")
    normalized.sort(key=lambda item: (float(item["start"]), float(item["end"]), item["id"]))
    for previous, current in zip(normalized, normalized[1:]):
        if float(current["start"]) < float(previous["end"]) - 1e-6:
            raise ValueError(f"narration segments overlap: {previous['id']} and {current['id']}")
    return normalized


def load_segment_manifest(path: Path, *, source_duration: float) -> List[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"segment manifest is invalid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("segment manifest must be a JSON object")
    return normalize_segments(payload, source_duration=source_duration)


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    target = _finite(settings.get("target_lufs"))
    tolerance = _finite(settings.get("tolerance_lu"))
    spread = _finite(settings.get("max_spread_lu"))
    true_peak = _finite(settings.get("max_true_peak_dbtp"))
    lra = _finite(settings.get("max_lra_lu"))
    minimum = _finite(settings.get("min_segment_seconds"))
    if target is None or not -36 <= target <= -8:
        blockers.append("settings.target_lufs must be between -36 and -8")
    if tolerance is None or not 0.1 <= tolerance <= 12:
        blockers.append("settings.tolerance_lu must be between 0.1 and 12")
    if spread is None or not 0.1 <= spread <= 12:
        blockers.append("settings.max_spread_lu must be between 0.1 and 12")
    if true_peak is None or not -12 <= true_peak <= 0:
        blockers.append("settings.max_true_peak_dbtp must be between -12 and 0")
    if lra is None or not 0.1 <= lra <= 30:
        blockers.append("settings.max_lra_lu must be between 0.1 and 30")
    if minimum is None or not 0.25 <= minimum <= 10:
        blockers.append("settings.min_segment_seconds must be between 0.25 and 10")
    return blockers


def measure_range(path: Path | str, *, start: float, end: float) -> Dict[str, Optional[float]]:
    duration = float(end) - float(start)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{float(start):.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    log = result.stderr or result.stdout
    if result.returncode != 0 and "Summary:" not in log:
        detail = log.strip().splitlines()
        raise ValueError(detail[-1] if detail else "FFmpeg ebur128 measurement failed")
    parsed = parse_ebur128_summary(log)
    return {
        "integrated_lufs": _round_or_none(parsed.get("integrated_lufs")),
        "true_peak_dbtp": _round_or_none(parsed.get("true_peak_dbfs")),
        "lra_lu": _round_or_none(parsed.get("lra_lu")),
    }


def analyze_segments(
    path: Path,
    segments: Sequence[Mapping[str, Any]],
    *,
    measure_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    measure_fn = measure_fn or measure_range
    measurements: List[Dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        measured = measure_fn(path, start=start, end=end)
        measurements.append(
            {
                "id": str(segment["id"]),
                "start": round(start, 6),
                "end": round(end, 6),
                "duration": round(end - start, 6),
                "integrated_lufs": _round_or_none(measured.get("integrated_lufs")),
                "true_peak_dbtp": _round_or_none(measured.get("true_peak_dbtp")),
                "lra_lu": _round_or_none(measured.get("lra_lu")),
            }
        )
    return measurements


def _derived_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    segments = report.get("segments") if isinstance(report.get("segments"), list) else []
    measurements = report.get("measurements") if isinstance(report.get("measurements"), list) else []
    by_id = {
        str(item.get("id")): item
        for item in measurements
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    blockers: List[str] = []
    warnings: List[str] = []
    comparable: List[tuple[str, float]] = []
    target_violations = 0
    peak_violations = 0
    lra_violations = 0
    exceptions = 0
    minimum_violations = 0
    target_value = _finite(settings.get("target_lufs"))
    tolerance_value = _finite(settings.get("tolerance_lu"))
    spread_value = _finite(settings.get("max_spread_lu"))
    peak_value = _finite(settings.get("max_true_peak_dbtp"))
    lra_value = _finite(settings.get("max_lra_lu"))
    minimum_value = _finite(settings.get("min_segment_seconds"))
    target = float(target_value if target_value is not None else DEFAULT_TARGET_LUFS)
    tolerance = float(tolerance_value if tolerance_value is not None else DEFAULT_TOLERANCE_LU)
    max_spread = float(spread_value if spread_value is not None else DEFAULT_MAX_SPREAD_LU)
    max_peak = float(peak_value if peak_value is not None else DEFAULT_MAX_TRUE_PEAK_DBTP)
    max_lra = float(lra_value if lra_value is not None else DEFAULT_MAX_LRA_LU)
    min_duration = float(minimum_value if minimum_value is not None else DEFAULT_MIN_SEGMENT_SECONDS)

    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        segment_id = str(segment.get("id") or "")
        measurement = by_id.get(segment_id)
        exception = segment.get("loudness_exception") if isinstance(segment.get("loudness_exception"), Mapping) else None
        if exception:
            exceptions += 1
            warnings.append(
                f"{segment_id} uses a documented loudness exception reviewed by "
                f"{exception.get('reviewer')}: {exception.get('reason')}"
            )
        if measurement is None:
            blockers.append(f"{segment_id} has no phrase-level loudness measurement")
            continue
        duration = _finite(measurement.get("duration"))
        integrated = _finite(measurement.get("integrated_lufs"))
        true_peak = _finite(measurement.get("true_peak_dbtp"))
        lra = _finite(measurement.get("lra_lu"))
        if duration is None or duration < min_duration:
            minimum_violations += 1
            blockers.append(
                f"{segment_id} duration {float(duration or 0):.3f}s is shorter than the "
                f"{min_duration:.3f}s measurement minimum"
            )
        if integrated is None:
            blockers.append(f"{segment_id} integrated loudness could not be measured")
        else:
            delta = abs(integrated - target)
            if delta > tolerance + 1e-9:
                target_violations += 1
                message = (
                    f"{segment_id} integrated loudness {integrated:.2f} LUFS is {delta:.2f} LU from "
                    f"target {target:.2f} +/- {tolerance:.2f}"
                )
                if exception:
                    warnings.append(message + " (documented exception)")
                else:
                    blockers.append(message)
            if not exception:
                comparable.append((segment_id, integrated))
        if true_peak is None:
            blockers.append(f"{segment_id} true peak could not be measured")
        elif true_peak > max_peak + 1e-9:
            peak_violations += 1
            blockers.append(
                f"{segment_id} true peak {true_peak:.2f} dBTP exceeds {max_peak:.2f} dBTP; "
                "documented loudness exceptions do not bypass the peak ceiling"
            )
        if lra is None:
            blockers.append(f"{segment_id} LRA could not be measured")
        elif lra > max_lra + 1e-9:
            lra_violations += 1
            message = f"{segment_id} LRA {lra:.2f} LU exceeds {max_lra:.2f} LU"
            if exception:
                warnings.append(message + " (documented exception)")
            else:
                blockers.append(message)

    spread: Optional[float] = None
    quietest: Optional[str] = None
    loudest: Optional[str] = None
    if len(comparable) < 2:
        blockers.append("at least two non-exempt measured narration segments are required for spread QA")
    else:
        quietest, quietest_value = min(comparable, key=lambda item: item[1])
        loudest, loudest_value = max(comparable, key=lambda item: item[1])
        spread = round(loudest_value - quietest_value, 2)
        if spread > max_spread + 1e-9:
            blockers.append(
                f"non-exempt narration spread {spread:.2f} LU exceeds {max_spread:.2f} LU "
                f"({quietest} {quietest_value:.2f} LUFS -> {loudest} {loudest_value:.2f} LUFS)"
            )

    summary = {
        "segments": len(segments),
        "measured_segments": len(by_id),
        "comparable_segments": len(comparable),
        "documented_exceptions": exceptions,
        "target_violations": target_violations,
        "peak_violations": peak_violations,
        "lra_violations": lra_violations,
        "minimum_duration_violations": minimum_violations,
        "spread_lu": spread,
        "quietest_segment": quietest,
        "loudest_segment": loudest,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }


def canonical_report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "version": report.get("version"),
            "source": report.get("source"),
            "segment_manifest": report.get("segment_manifest"),
            "settings": report.get("settings"),
            "algorithm": report.get("algorithm"),
            "segments": report.get("segments"),
            "measurements": report.get("measurements"),
            "status": report.get("status"),
            "blockers": report.get("blockers"),
            "warnings": report.get("warnings"),
            "summary": report.get("summary"),
            "limitations": report.get("limitations"),
        }
    )


def _set_derived(report: Dict[str, Any]) -> None:
    report.update(_derived_snapshot(report))
    report["report_id"] = canonical_report_id(report)


def build_report(
    media_path: Path | str,
    segments_path: Path | str,
    *,
    project_dir: Path | str,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    tolerance_lu: float = DEFAULT_TOLERANCE_LU,
    max_spread_lu: float = DEFAULT_MAX_SPREAD_LU,
    max_true_peak_dbtp: float = DEFAULT_MAX_TRUE_PEAK_DBTP,
    max_lra_lu: float = DEFAULT_MAX_LRA_LU,
    min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_audio_media
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _project_file(media_path, root=root, label="narration media")
    manifest = _project_file(segments_path, root=root, label="segment manifest")
    if _same_path_or_file(source, manifest):
        raise ValueError("narration media and segment manifest must be different files")
    settings = {
        "target_lufs": float(target_lufs),
        "tolerance_lu": float(tolerance_lu),
        "max_spread_lu": float(max_spread_lu),
        "max_true_peak_dbtp": float(max_true_peak_dbtp),
        "max_lra_lu": float(max_lra_lu),
        "min_segment_seconds": float(min_segment_seconds),
    }
    setting_errors = validate_settings(settings)
    if setting_errors:
        raise ValueError("invalid settings: " + "; ".join(setting_errors))
    media = dict(probe_fn(source))
    normalized = load_segment_manifest(manifest, source_duration=float(media["duration"]))
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": _source_contract(source, root=root, media=media),
        "segment_manifest": _file_contract(manifest, root=root),
        "settings": settings,
        "algorithm": {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT},
        "segments": normalized,
        "measurements": analyze_segments(source, normalized, measure_fn=measure_fn),
        "limitations": [
            "Use the finalized isolated narration track; music, effects, or source dialogue make phrase measurements ambiguous.",
            "Short-range ebur128 measurements are less stable than program-wide loudness and require normal-speed listening.",
            "This report does not judge timbre, pronunciation, cadence, synthesis artifacts, or voice performance.",
            "Run audio_master_report.py on the final mixed master; this phrase gate does not replace final-program QA.",
        ],
    }
    _set_derived(report)
    return report


def _structural_blockers(report: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported version: {report.get('version')!r}")
    project = str(report.get("project_dir") or "")
    if not project or not Path(project).expanduser().is_absolute():
        blockers.append("project_dir must be an absolute path")
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    blockers.extend(validate_settings(settings))
    if report.get("algorithm") != {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT}:
        blockers.append("algorithm contract differs from the current narration loudness analyzer")
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    manifest = report.get("segment_manifest") if isinstance(report.get("segment_manifest"), Mapping) else {}
    if not str(source.get("path") or "") or len(str(source.get("sha256") or "")) != 64:
        blockers.append("source fingerprint is incomplete")
    if not str(manifest.get("path") or "") or len(str(manifest.get("sha256") or "")) != 64:
        blockers.append("segment manifest fingerprint is incomplete")
    segments = report.get("segments") if isinstance(report.get("segments"), list) else []
    measurements = report.get("measurements") if isinstance(report.get("measurements"), list) else []
    if len(segments) < 2:
        blockers.append("stored report must contain at least two segments")
    if len(measurements) != len(segments):
        blockers.append("stored measurements must cover every segment exactly once")
    expected = _derived_snapshot(report)
    for key in ("status", "blockers", "warnings", "summary"):
        if report.get(key) != expected[key]:
            blockers.append(f"stored {key} differs from canonical derived state")
    if report.get("report_id") != canonical_report_id(report):
        blockers.append("report_id does not match canonical report content")
    return blockers


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[Path | str] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_audio_media
    structural = _structural_blockers(report)
    live_blockers: List[str] = []
    stored_project = Path(str(report.get("project_dir") or "")).expanduser()
    requested_project = Path(project_dir).expanduser().resolve() if project_dir is not None else stored_project.resolve()
    if not stored_project.is_absolute() or stored_project.resolve() != requested_project:
        live_blockers.append("report project_dir differs from the current project")
    source_record = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    manifest_record = report.get("segment_manifest") if isinstance(report.get("segment_manifest"), Mapping) else {}
    source: Optional[Path] = None
    manifest: Optional[Path] = None
    if not live_blockers:
        try:
            source = _project_file(str(source_record.get("path") or ""), root=requested_project, label="narration source")
            manifest = _project_file(str(manifest_record.get("path") or ""), root=requested_project, label="segment manifest")
        except ValueError as exc:
            live_blockers.append(str(exc))
    if source is not None and manifest is not None:
        if _same_path_or_file(source, manifest):
            live_blockers.append("narration source and segment manifest resolve to the same file")
        if _sha256(source) != str(source_record.get("sha256") or ""):
            live_blockers.append("narration source bytes changed after analysis")
        if source.stat().st_size != int(source_record.get("size_bytes") or -1):
            live_blockers.append("narration source size changed after analysis")
        if _sha256(manifest) != str(manifest_record.get("sha256") or ""):
            live_blockers.append("segment manifest bytes changed after analysis")
        if manifest.stat().st_size != int(manifest_record.get("size_bytes") or -1):
            live_blockers.append("segment manifest size changed after analysis")
        try:
            live_media = dict(probe_fn(source))
        except Exception as exc:
            live_blockers.append(f"narration source probe failed: {exc}")
        else:
            if {key: live_media.get(key) for key in MEDIA_KEYS} != {
                key: source_record.get(key) for key in MEDIA_KEYS
            }:
                live_blockers.append("narration source media contract changed after analysis")
            try:
                live_segments = load_segment_manifest(manifest, source_duration=float(live_media["duration"]))
            except Exception as exc:
                live_blockers.append(f"live segment manifest validation failed: {exc}")
            else:
                if live_segments != report.get("segments"):
                    live_blockers.append("live normalized segments differ from the stored segment contract")
                if not structural and not live_blockers:
                    try:
                        live_measurements = analyze_segments(source, live_segments, measure_fn=measure_fn)
                    except Exception as exc:
                        live_blockers.append(f"live narration loudness analysis failed: {exc}")
                    else:
                        if live_measurements != report.get("measurements"):
                            live_blockers.append("live phrase measurements differ from the stored evidence")
    quality = _derived_snapshot(report)
    all_blockers = list(quality["blockers"]) + structural + live_blockers
    warnings = list(quality["warnings"])
    return {
        "version": VERIFY_VERSION,
        "status": "blocked" if all_blockers else ("warn" if warnings else "ready"),
        "report_id": report.get("report_id"),
        "blockers": all_blockers,
        "warnings": warnings,
        "summary": {
            **quality["summary"],
            "quality_blocking": len(quality["blockers"]),
            "integrity_blocking": len(structural) + len(live_blockers),
            "blocking": len(all_blockers),
            "warnings": len(warnings),
        },
    }


def _md(value: Any) -> str:
    return _clean_text(value).replace("|", "\\|").replace("`", "'")


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    segments = {
        str(item.get("id")): item
        for item in (report.get("segments") if isinstance(report.get("segments"), list) else [])
        if isinstance(item, Mapping)
    }
    lines = [
        "# Narration Loudness QA",
        "",
        f"- Status: **{report.get('status', 'blocked')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Target: {settings.get('target_lufs')} LUFS +/- {settings.get('tolerance_lu')} LU",
        f"- Maximum non-exempt spread: {settings.get('max_spread_lu')} LU",
        f"- Maximum true peak: {settings.get('max_true_peak_dbtp')} dBTP",
        f"- Maximum per-segment LRA: {settings.get('max_lra_lu')} LU",
        "",
        "## Phrase measurements",
        "",
        "| id | time | duration | integrated | true peak | LRA | exception | text |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for measurement in report.get("measurements") or []:
        if not isinstance(measurement, Mapping):
            continue
        segment = segments.get(str(measurement.get("id")), {})
        exception = segment.get("loudness_exception") if isinstance(segment.get("loudness_exception"), Mapping) else None
        exception_text = f"{exception.get('reviewer')}: {exception.get('reason')}" if exception else ""
        lines.append(
            f"| {_md(measurement.get('id'))} | {float(measurement.get('start') or 0):.3f}–"
            f"{float(measurement.get('end') or 0):.3f}s | {float(measurement.get('duration') or 0):.3f}s | "
            f"{measurement.get('integrated_lufs')} LUFS | {measurement.get('true_peak_dbtp')} dBTP | "
            f"{measurement.get('lra_lu')} LU | {_md(exception_text)} | {_md(segment.get('text'))} |"
        )
    lines.extend(["", "## Blocking findings", ""])
    if report.get("blockers"):
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    else:
        lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    if report.get("warnings"):
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Required listening and next checks",
            "",
            "- Listen to every phrase and every boundary at normal speed; numbers cannot detect robotic tone, clipped breaths, bad pronunciation, or artifacts.",
            "- Fix the finalized narration asset or document a real creative exception in the segment manifest, then re-run analyze and verify.",
            "- Mix the approved narration into the final master, then run audio_master_report.py on the complete program.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    return "\n".join(lines) + "\n"


def _load_report(path: Path | str) -> Dict[str, Any]:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("report must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure source-bound phrase-level loudness consistency on a finalized narration track"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="Measure every narration range and write JSON/Markdown")
    analyze_parser.add_argument("media", help="Finalized isolated narration audio or video")
    analyze_parser.add_argument("--segments", required=True, help="JSON containing exact segments[] or phrases[] ranges")
    analyze_parser.add_argument("--project-dir", default=".")
    analyze_parser.add_argument("--output", default="verify/narration_loudness_qa.json")
    analyze_parser.add_argument("--markdown", default="verify/narration_loudness_qa.md")
    analyze_parser.add_argument("--target-lufs", type=float, default=DEFAULT_TARGET_LUFS)
    analyze_parser.add_argument("--tolerance-lu", type=float, default=DEFAULT_TOLERANCE_LU)
    analyze_parser.add_argument("--max-spread-lu", type=float, default=DEFAULT_MAX_SPREAD_LU)
    analyze_parser.add_argument("--max-true-peak-dbtp", type=float, default=DEFAULT_MAX_TRUE_PEAK_DBTP)
    analyze_parser.add_argument("--max-lra-lu", type=float, default=DEFAULT_MAX_LRA_LU)
    analyze_parser.add_argument("--min-segment-seconds", type=float, default=DEFAULT_MIN_SEGMENT_SECONDS)
    analyze_parser.add_argument("--force", action="store_true")
    analyze_parser.add_argument("--strict", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Re-measure live source bytes and verify the report")
    verify_parser.add_argument("--report", default="verify/narration_loudness_qa.json")
    verify_parser.add_argument("--project-dir")
    verify_parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            root = Path(args.project_dir).expanduser().resolve()
            source = _project_file(args.media, root=root, label="narration media")
            segments = _project_file(args.segments, root=root, label="segment manifest")
            output = _safe_output(
                args.output,
                root=root,
                label="JSON output",
                forbidden=[source, segments],
                force=args.force,
            )
            markdown = _safe_output(
                args.markdown,
                root=root,
                label="Markdown output",
                forbidden=[source, segments, output],
                force=args.force,
            )
            report = build_report(
                source,
                segments,
                project_dir=root,
                target_lufs=args.target_lufs,
                tolerance_lu=args.tolerance_lu,
                max_spread_lu=args.max_spread_lu,
                max_true_peak_dbtp=args.max_true_peak_dbtp,
                max_lra_lu=args.max_lra_lu,
                min_segment_seconds=args.min_segment_seconds,
            )
            _atomic_write_json(output, report)
            _atomic_write_text(markdown, emit_markdown(report))
            print(
                f"narration_loudness_qa status={report['status']} "
                f"segments={report['summary']['segments']} spread_lu={report['summary']['spread_lu']} "
                f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']} report={output}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report = _load_report(args.report)
        verification = verify_report(report, args.project_dir)
        print(
            f"narration_loudness_qa verify status={verification['status']} "
            f"blocking={verification['summary']['blocking']} warnings={verification['summary']['warnings']}"
        )
        for blocker in verification["blockers"]:
            print(f"BLOCK: {blocker}")
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
