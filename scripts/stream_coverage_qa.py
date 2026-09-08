#!/usr/bin/env python3
"""Verify that final video and audio streams cover the intended timeline.

The report uses decoded frame timestamps rather than trusting only the MP4
container duration.  It is read-only and source-bound: verification hashes the
current media, decodes it again, and recomputes every timing decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


VERSION = "stream_coverage_qa.v1"
VERIFY_VERSION = "stream_coverage_qa_verify.v1"
DEFAULT_SETTINGS: Mapping[str, Any] = {
    "require_audio": True,
    "max_av_start_skew_ms": 80.0,
    "max_av_end_skew_ms": 100.0,
    "max_container_gap_ms": 100.0,
    "expected_duration_seconds": None,
    "max_expected_duration_delta_ms": 100.0,
    "expected_video_frames": None,
    "max_expected_frame_delta": 1,
}
ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "ffprobe_decoded_frame_timeline_coverage",
    "streams": "first decoded video stream and first decoded audio stream",
    "timestamp": "best_effort_timestamp_time, falling back to pts_time",
    "frame_end": "timestamp plus decoded duration, nb_samples/sample_rate, adjacent delta, or nominal video frame duration",
    "decode": "ffmpeg -xerror full decode plus ffprobe -show_frames for each measured stream",
    "checks": [
        "required stream presence",
        "full decode",
        "monotonic decoded timestamps",
        "audio/video decoded start and end agreement",
        "per-stream decoded coverage of the container timeline",
        "optional expected duration and video frame count",
    ],
    "automatic_repair": False,
    "creative_sync_judgement": False,
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


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 6) -> Optional[float]:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _rate(value: Any) -> Optional[float]:
    if not value or str(value) == "0/0":
        return None
    try:
        result = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) and result > 0 else None


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


def _atomic_write(path: Path, value: str) -> None:
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


def _stream_contract(stream: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "index": int(stream.get("index") or 0),
        "codec_type": str(stream.get("codec_type") or ""),
        "codec_name": str(stream.get("codec_name") or ""),
        "time_base": str(stream.get("time_base") or ""),
        "start_time": _round(stream.get("start_time")),
        "duration": _round(stream.get("duration")),
        "nb_frames": int(stream["nb_frames"]) if str(stream.get("nb_frames") or "").isdigit() else None,
        "avg_frame_rate": str(stream.get("avg_frame_rate") or ""),
        "r_frame_rate": str(stream.get("r_frame_rate") or ""),
        "sample_rate": int(stream["sample_rate"]) if str(stream.get("sample_rate") or "").isdigit() else None,
        "channels": int(stream["channels"]) if str(stream.get("channels") or "").isdigit() else None,
        "width": int(stream.get("width") or 0) or None,
        "height": int(stream.get("height") or 0) or None,
    }


def probe_media(path: Path | str) -> Dict[str, Any]:
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
    video = [_stream_contract(item) for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"]
    audio = [_stream_contract(item) for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"]
    format_data = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    duration = _finite(format_data.get("duration"))
    if duration is None or duration <= 0:
        raise ValueError("container duration must be positive")
    start = _finite(format_data.get("start_time"))
    if start is None:
        starts = [item.get("start_time") for item in [*video, *audio] if item.get("start_time") is not None]
        start = min(starts) if starts else 0.0
    return {
        "container": {
            "format_name": str(format_data.get("format_name") or ""),
            "start_time": round(start, 6),
            "duration": round(duration, 6),
            "end_time": round(start + duration, 6),
        },
        "video_streams": video,
        "audio_streams": audio,
    }


def measure_stream_frames(path: Path | str, selector: str) -> Sequence[Mapping[str, Any]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            selector,
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,pts_time,pkt_duration_time,duration_time,nb_samples,sample_rate",
            "-print_format",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(detail[-1] if detail else f"ffprobe {selector} frame scan failed")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe {selector} frame scan returned invalid JSON") from exc
    frames = payload.get("frames")
    if not isinstance(frames, list):
        raise ValueError(f"ffprobe {selector} frame scan returned no frames array")
    return frames


def full_decode(path: Path | str) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    detail = (result.stderr or result.stdout).strip().splitlines()
    return {
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "error": "" if result.returncode == 0 else (detail[-1] if detail else "FFmpeg full decode failed"),
    }


def summarize_frames(
    rows: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    stream: Mapping[str, Any],
) -> Dict[str, Any]:
    parsed: list[Dict[str, Optional[float]]] = []
    for row in rows:
        pts = _finite(row.get("best_effort_timestamp_time"))
        if pts is None:
            pts = _finite(row.get("pts_time"))
        if pts is None:
            continue
        duration = _finite(row.get("pkt_duration_time"))
        if duration is None or duration <= 0:
            duration = _finite(row.get("duration_time"))
        if (duration is None or duration <= 0) and kind == "audio":
            samples = _finite(row.get("nb_samples"))
            sample_rate = _finite(row.get("sample_rate")) or _finite(stream.get("sample_rate"))
            if samples is not None and sample_rate is not None and samples > 0 and sample_rate > 0:
                duration = samples / sample_rate
        parsed.append({"pts": pts, "duration": duration if duration and duration > 0 else None})
    if not parsed:
        raise ValueError(f"decoded {kind} stream contains no timestamped frames")

    deltas = [
        parsed[index + 1]["pts"] - parsed[index]["pts"]  # type: ignore[operator]
        for index in range(len(parsed) - 1)
        if parsed[index + 1]["pts"] is not None and parsed[index]["pts"] is not None
    ]
    positive_deltas = [value for value in deltas if value > 0]
    fallback = statistics.median(positive_deltas) if positive_deltas else None
    if kind == "video":
        nominal_fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
        if fallback is None and nominal_fps:
            fallback = 1.0 / nominal_fps
    else:
        nominal_fps = None
    normalized = []
    for index, row in enumerate(parsed):
        duration = row["duration"]
        if duration is None and index + 1 < len(parsed):
            candidate = parsed[index + 1]["pts"] - row["pts"]  # type: ignore[operator]
            duration = candidate if candidate > 0 else None
        if duration is None:
            duration = fallback or 0.0
        normalized.append({"pts": round(float(row["pts"]), 9), "duration": round(float(duration), 9)})

    starts = [row["pts"] for row in normalized]
    ends = [row["pts"] + row["duration"] for row in normalized]
    regressions = sum(1 for value in deltas if value < -1e-9)
    duplicates = sum(1 for value in deltas if abs(value) <= 1e-9)
    first = starts[0]
    last_start = starts[-1]
    decoded_end = max(ends)
    return {
        "kind": kind,
        "frame_count": len(normalized),
        "timestamped_rows": len(normalized),
        "first_pts": round(first, 6),
        "last_pts": round(last_start, 6),
        "last_frame_duration": round(normalized[-1]["duration"], 6),
        "decoded_end": round(decoded_end, 6),
        "decoded_duration": round(decoded_end - first, 6),
        "timestamp_regressions": regressions,
        "duplicate_timestamps": duplicates,
        "median_frame_delta": _round(statistics.median(positive_deltas) if positive_deltas else None),
        "nominal_fps": _round(nominal_fps),
        "metadata_nb_frames": stream.get("nb_frames"),
        "timeline_sha256": _canonical_sha256(normalized),
    }


def normalize_settings(value: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if value:
        unknown = sorted(set(value) - set(settings))
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)}")
        settings.update(value)
    settings["require_audio"] = bool(settings["require_audio"])
    for key in (
        "max_av_start_skew_ms",
        "max_av_end_skew_ms",
        "max_container_gap_ms",
        "max_expected_duration_delta_ms",
    ):
        settings[key] = float(settings[key])
    if settings["expected_duration_seconds"] is not None:
        settings["expected_duration_seconds"] = float(settings["expected_duration_seconds"])
    if settings["expected_video_frames"] is not None:
        settings["expected_video_frames"] = int(settings["expected_video_frames"])
    settings["max_expected_frame_delta"] = int(settings["max_expected_frame_delta"])
    blockers = validate_settings(settings)
    if blockers:
        raise ValueError("; ".join(blockers))
    return settings


def validate_settings(settings: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for key in (
        "max_av_start_skew_ms",
        "max_av_end_skew_ms",
        "max_container_gap_ms",
        "max_expected_duration_delta_ms",
    ):
        value = _finite(settings.get(key))
        if value is None or not 0 <= value <= 5000:
            blockers.append(f"settings.{key} must be between 0 and 5000 ms")
    expected_duration = settings.get("expected_duration_seconds")
    if expected_duration is not None and (_finite(expected_duration) is None or float(expected_duration) <= 0):
        blockers.append("settings.expected_duration_seconds must be positive when provided")
    expected_frames = settings.get("expected_video_frames")
    if expected_frames is not None and (not isinstance(expected_frames, int) or expected_frames <= 0):
        blockers.append("settings.expected_video_frames must be a positive integer when provided")
    frame_delta = settings.get("max_expected_frame_delta")
    if not isinstance(frame_delta, int) or not 0 <= frame_delta <= 100:
        blockers.append("settings.max_expected_frame_delta must be an integer between 0 and 100")
    return blockers


def _check(name: str, status: str, message: str, **details: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"name": name, "status": status, "message": message}
    if details:
        result["details"] = details
    return result


def evaluate_analysis(
    media: Mapping[str, Any],
    analysis: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    checks: list[Dict[str, Any]] = []
    video_streams = media.get("video_streams") if isinstance(media.get("video_streams"), list) else []
    audio_streams = media.get("audio_streams") if isinstance(media.get("audio_streams"), list) else []
    video = analysis.get("video") if isinstance(analysis.get("video"), Mapping) else None
    audio = analysis.get("audio") if isinstance(analysis.get("audio"), Mapping) else None
    decode = analysis.get("full_decode") if isinstance(analysis.get("full_decode"), Mapping) else {}
    container = media.get("container") if isinstance(media.get("container"), Mapping) else {}

    checks.append(_check("video_stream", "pass" if video_streams else "block", f"Found {len(video_streams)} video stream(s)"))
    if not audio_streams:
        status = "block" if settings["require_audio"] else "warn"
        checks.append(_check("audio_stream", status, "No audio stream found"))
    else:
        checks.append(_check("audio_stream", "pass", f"Found {len(audio_streams)} audio stream(s)"))
    if len(video_streams) > 1:
        checks.append(_check("video_stream_scope", "warn", "Only the first video stream was measured"))
    if len(audio_streams) > 1:
        checks.append(_check("audio_stream_scope", "warn", "Only the first audio stream was measured"))

    checks.append(
        _check(
            "full_decode",
            "pass" if decode.get("passed") else "block",
            "FFmpeg decoded the selected delivery streams" if decode.get("passed") else str(decode.get("error") or "FFmpeg full decode failed"),
        )
    )
    for kind, measured in (("video", video), ("audio", audio)):
        if measured is None:
            if kind == "video" or (kind == "audio" and settings["require_audio"]):
                checks.append(_check(f"{kind}_decoded_timeline", "block", f"No decoded {kind} timeline was measured"))
            continue
        regressions = int(measured.get("timestamp_regressions") or 0)
        status = "block" if regressions else "pass"
        checks.append(
            _check(
                f"{kind}_decoded_timeline",
                status,
                f"Decoded {measured.get('frame_count')} {kind} frame(s); timestamp regressions={regressions}",
                frame_count=measured.get("frame_count"),
                timestamp_regressions=regressions,
            )
        )
        metadata_count = measured.get("metadata_nb_frames")
        # AAC nb_frames may include priming/discard frames that are not emitted
        # by -show_frames.  The decoded audio end PTS is the coverage authority.
        if kind == "video" and metadata_count is not None:
            delta = abs(int(metadata_count) - int(measured.get("frame_count") or 0))
            checks.append(
                _check(
                    f"{kind}_metadata_frame_count",
                    "block" if delta else "pass",
                    f"Decoded count differs from stream nb_frames by {delta}",
                    decoded=measured.get("frame_count"),
                    metadata=metadata_count,
                    delta=delta,
                )
            )

    if video is not None and audio is not None:
        start_skew = abs(float(video["first_pts"]) - float(audio["first_pts"])) * 1000.0
        end_skew = abs(float(video["decoded_end"]) - float(audio["decoded_end"])) * 1000.0
        checks.append(
            _check(
                "av_start_coverage",
                "block" if start_skew > float(settings["max_av_start_skew_ms"]) else "pass",
                f"Decoded audio/video starts differ by {start_skew:.3f} ms",
                skew_ms=round(start_skew, 3),
            )
        )
        checks.append(
            _check(
                "av_end_coverage",
                "block" if end_skew > float(settings["max_av_end_skew_ms"]) else "pass",
                f"Decoded audio/video ends differ by {end_skew:.3f} ms",
                skew_ms=round(end_skew, 3),
            )
        )

    container_start = _finite(container.get("start_time"))
    container_end = _finite(container.get("end_time"))
    for kind, measured in (("video", video), ("audio", audio)):
        if measured is None or container_start is None or container_end is None:
            continue
        head_gap = abs(float(measured["first_pts"]) - container_start) * 1000.0
        tail_gap = abs(container_end - float(measured["decoded_end"])) * 1000.0
        max_gap = float(settings["max_container_gap_ms"])
        checks.append(
            _check(
                f"{kind}_container_coverage",
                "block" if head_gap > max_gap or tail_gap > max_gap else "pass",
                f"Decoded {kind} head/tail gaps versus container are {head_gap:.3f}/{tail_gap:.3f} ms",
                head_gap_ms=round(head_gap, 3),
                tail_gap_ms=round(tail_gap, 3),
            )
        )

    expected_duration = settings.get("expected_duration_seconds")
    if expected_duration is not None:
        allowed = float(settings["max_expected_duration_delta_ms"])
        observed = [("container", container.get("duration"))]
        if video is not None:
            observed.append(("video", video.get("decoded_duration")))
        if audio is not None:
            observed.append(("audio", audio.get("decoded_duration")))
        for kind, value in observed:
            delta = abs(float(value) - float(expected_duration)) * 1000.0
            checks.append(
                _check(
                    f"{kind}_expected_duration",
                    "block" if delta > allowed else "pass",
                    f"{kind.capitalize()} duration differs from expected by {delta:.3f} ms",
                    expected_seconds=expected_duration,
                    observed_seconds=value,
                    delta_ms=round(delta, 3),
                )
            )

    expected_frames = settings.get("expected_video_frames")
    if expected_frames is not None and video is not None:
        delta = abs(int(video.get("frame_count") or 0) - int(expected_frames))
        checks.append(
            _check(
                "video_expected_frame_count",
                "block" if delta > int(settings["max_expected_frame_delta"]) else "pass",
                f"Decoded video frame count differs from expected by {delta}",
                expected=expected_frames,
                decoded=video.get("frame_count"),
                delta=delta,
            )
        )
    return checks


def summarize_checks(checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    blocking = sum(1 for item in checks if item.get("status") == "block")
    warnings = sum(1 for item in checks if item.get("status") == "warn")
    return {
        "status": "blocked" if blocking else "warn" if warnings else "ready",
        "blocking": blocking,
        "warnings": warnings,
        "checks": len(checks),
    }


def _report_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "source": report.get("source"),
        "algorithm": report.get("algorithm"),
        "settings": report.get("settings"),
        "analysis": report.get("analysis"),
        "checks": report.get("checks"),
        "summary": report.get("summary"),
        "limitations": report.get("limitations"),
    }


def build_report(
    source: Path | str,
    *,
    project_dir: Path | str,
    settings: Optional[Mapping[str, Any]] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[[Path | str, str], Sequence[Mapping[str, Any]]]] = None,
    decode_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    source_path = _project_file(source, root=root, label="source")
    normalized_settings = normalize_settings(settings)
    probe_fn = probe_fn or probe_media
    measure_fn = measure_fn or measure_stream_frames
    decode_fn = decode_fn or full_decode
    media = dict(probe_fn(source_path))
    video_streams = media.get("video_streams") if isinstance(media.get("video_streams"), list) else []
    audio_streams = media.get("audio_streams") if isinstance(media.get("audio_streams"), list) else []
    analysis: Dict[str, Any] = {"full_decode": dict(decode_fn(source_path)), "video": None, "audio": None}
    if video_streams:
        analysis["video"] = summarize_frames(measure_fn(source_path, "v:0"), kind="video", stream=video_streams[0])
    if audio_streams:
        analysis["audio"] = summarize_frames(measure_fn(source_path, "a:0"), kind="audio", stream=audio_streams[0])
    checks = evaluate_analysis(media, analysis, normalized_settings)
    report: Dict[str, Any] = {
        "schema": VERSION,
        "generated_at": utc_now(),
        "source": {
            "path": _relative(source_path, root),
            "sha256": _sha256(source_path),
            "size_bytes": source_path.stat().st_size,
            "media_contract": media,
        },
        "algorithm": {"id": ALGORITHM_ID, "contract": dict(ALGORITHM_CONTRACT)},
        "settings": normalized_settings,
        "analysis": analysis,
        "checks": checks,
        "summary": summarize_checks(checks),
        "limitations": [
            "Only the first video and first audio streams are measured; extra streams are reported as warnings.",
            "Timestamp coverage detects truncated or offset streams but does not prove perceptual lip-sync or editorial timing.",
            "Intentional asymmetric heads or tails require explicit tolerance settings and documented review.",
            "Watch the complete final file at normal speed after the deterministic gate passes.",
        ],
    }
    report["report_id"] = _canonical_sha256(_report_snapshot(report))
    return report


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[Path | str] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[[Path | str, str], Sequence[Mapping[str, Any]]]] = None,
    decode_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not isinstance(report, Mapping):
        blockers.append("report must be a JSON object")
    if report.get("schema") != VERSION:
        blockers.append(f"report schema must be {VERSION}")
    root = Path(project_dir or ".").expanduser().resolve()
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    try:
        source_path_value = source.get("path")
        if not source_path_value:
            raise ValueError("report source.path is missing")
        current = build_report(
            str(source_path_value),
            project_dir=root,
            settings=settings,
            probe_fn=probe_fn,
            measure_fn=measure_fn,
            decode_fn=decode_fn,
        )
    except Exception as exc:
        blockers.append(str(exc))
        current = None
    if current is not None:
        comparisons = (
            ("source", "source bytes or media contract drifted"),
            ("algorithm", "algorithm contract drifted"),
            ("settings", "coverage settings drifted"),
            ("analysis", "live decoded timeline measurements drifted"),
            ("checks", "derived coverage checks drifted"),
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
            message = str(check.get("message") or check.get("name") or "stream coverage check")
            if check.get("status") == "block":
                blockers.append(f"stream coverage blocker: {message}")
            elif check.get("status") == "warn":
                warnings.append(f"stream coverage warning: {message}")
    blocking = len(set(blockers))
    warning_count = len(set(warnings))
    status = "blocked" if blocking else "warn" if warning_count else "ready"
    return {
        "schema": VERIFY_VERSION,
        "verified_at": utc_now(),
        "status": status,
        "report_id": report.get("report_id"),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {"status": status, "blocking": blocking, "warnings": warning_count},
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    media = source.get("media_contract") if isinstance(source.get("media_contract"), Mapping) else {}
    container = media.get("container") if isinstance(media.get("container"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    lines = [
        "# Stream Coverage QA",
        "",
        f"- Status: `{(report.get('summary') or {}).get('status')}`",
        f"- Source: `{source.get('path')}`",
        f"- SHA-256: `{source.get('sha256')}`",
        f"- Container timeline: `{container.get('start_time')}s → {container.get('end_time')}s`",
        f"- Full decode: `{'pass' if (analysis.get('full_decode') or {}).get('passed') else 'fail'}`",
        "",
        "## Decoded coverage",
        "",
        "| stream | frames | first PTS | decoded end | decoded duration | timestamp regressions |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind in ("video", "audio"):
        item = analysis.get(kind) if isinstance(analysis.get(kind), Mapping) else None
        if item is None:
            lines.append(f"| {kind} | — | — | — | — | — |")
        else:
            lines.append(
                f"| {kind} | {item.get('frame_count')} | {item.get('first_pts')}s | "
                f"{item.get('decoded_end')}s | {item.get('decoded_duration')}s | "
                f"{item.get('timestamp_regressions')} |"
            )
    lines.extend(["", "## Checks", ""])
    for check in report.get("checks") or []:
        if isinstance(check, Mapping):
            lines.append(f"- `{str(check.get('status') or '').upper()}` **{check.get('name')}** — {check.get('message')}")
    lines.extend(
        [
            "",
            "## Review boundary",
            "",
            "This gate catches truncated, offset, or short decoded streams that a playable container can hide. It does not judge lip-sync, intentional handles, or creative timing. Watch the complete final file at 1× after it passes.",
            "",
            "After any render, remux, trim, subtitle burn-in, or delivery encode, regenerate this report. Hand-editing the JSON cannot make a stale report valid because `verify` rehashes and decodes the source again.",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"JSON must contain an object: {path}")
    return payload


def _print_summary(payload: Mapping[str, Any]) -> None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    print(
        f"status={summary.get('status')} blocking={summary.get('blocking', 0)} "
        f"warnings={summary.get('warnings', 0)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify decoded audio/video timeline coverage instead of trusting container duration alone."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Decode a final video and write a source-bound coverage report")
    analyze.add_argument("source")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--allow-no-audio", action="store_true")
    analyze.add_argument("--max-av-start-skew-ms", type=float, default=80.0)
    analyze.add_argument("--max-av-end-skew-ms", type=float, default=100.0)
    analyze.add_argument("--max-container-gap-ms", type=float, default=100.0)
    analyze.add_argument("--expected-duration", type=float)
    analyze.add_argument("--max-expected-duration-delta-ms", type=float, default=100.0)
    analyze.add_argument("--expected-video-frames", type=int)
    analyze.add_argument("--max-expected-frame-delta", type=int, default=1)
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true", help="Exit 2 when coverage has blockers")

    verify = subparsers.add_parser("verify", help="Rehash, decode, and recompute a saved report")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when the report is stale or blocked")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.project_dir).expanduser().resolve()
        if args.command == "analyze":
            source = _project_file(args.source, root=root, label="source")
            settings = {
                "require_audio": not args.allow_no_audio,
                "max_av_start_skew_ms": args.max_av_start_skew_ms,
                "max_av_end_skew_ms": args.max_av_end_skew_ms,
                "max_container_gap_ms": args.max_container_gap_ms,
                "expected_duration_seconds": args.expected_duration,
                "max_expected_duration_delta_ms": args.max_expected_duration_delta_ms,
                "expected_video_frames": args.expected_video_frames,
                "max_expected_frame_delta": args.max_expected_frame_delta,
            }
            report = build_report(source, project_dir=root, settings=settings)
            forbidden = [source]
            output = _safe_output(args.output, root=root, label="output", forbidden=forbidden, force=args.force)
            markdown = None
            if args.markdown:
                markdown = _safe_output(args.markdown, root=root, label="markdown", forbidden=[*forbidden, output], force=args.force)
            _atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2))
            if markdown is not None:
                _atomic_write(markdown, emit_markdown(report))
            _print_summary(report)
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path = _project_file(args.report, root=root, label="report")
        verification = verify_report(_load_json(report_path), root)
        _print_summary(verification)
        for blocker in verification.get("blockers") or []:
            print(f"BLOCK: {blocker}")
        for warning in verification.get("warnings") or []:
            print(f"WARN: {warning}")
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
