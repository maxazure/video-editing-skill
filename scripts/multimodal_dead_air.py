#!/usr/bin/env python3
"""Plan, verify, and apply conservative multimodal dead-air cuts.

Audio-only silence removal can delete deliberate pauses while useful visual
motion continues.  This workflow proposes a cut only when an FFmpeg silence
interval is also mostly covered by FFmpeg freeze detection.  It removes only
the shared interval, preserves boundary padding, caps automatic removal, and
binds the plan to the source bytes before a single-pass render.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jump_cut import (  # noqa: E402
    DEFAULT_FADE_SECONDS,
    DEFAULT_MAX_REMOVAL_RATIO,
    Segment,
    build_ffmpeg_command,
    build_keep_segments,
    infer_removed_segments,
    measure_adaptive_noise_db,
    parse_silencedetect,
    run_ffmpeg_with_fallback,
)


VERSION = "multimodal_dead_air_plan.v1"
DEFAULT_NOISE_DB = -35.0
DEFAULT_MIN_SILENCE = 1.0
DEFAULT_FREEZE_NOISE = 0.02
DEFAULT_MIN_FREEZE = 1.0
DEFAULT_STATIC_OVERLAP = 0.60
DEFAULT_PAD = 0.08
DEFAULT_MIN_KEEP = 0.15
MEDIA_KEYS = (
    "duration",
    "fps",
    "width",
    "height",
    "rotation",
    "has_audio",
    "has_video",
    "video_codec",
    "pixel_format",
    "audio_codec",
    "sample_rate",
    "channels",
)


def _round4(value: float) -> float:
    return round(max(0.0, float(value)), 4)


def _absolute_path(value: str) -> Path:
    """Return an absolute path without following the final symlink."""
    return Path(os.path.abspath(os.path.expanduser(value)))


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


def _run_checked(command: Sequence[str], label: str) -> subprocess.CompletedProcess[str]:
    result = _run(command)
    if result.returncode == 0:
        return result
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 2000:
        detail = detail[-2000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _fraction(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            parsed = float(numerator) / float(denominator)
        else:
            parsed = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return parsed if math.isfinite(parsed) else None


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
    result = _run_checked(
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
        "ffprobe",
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next(
        (item for item in payload.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (item for item in payload.get("streams", []) if item.get("codec_type") == "audio"),
        None,
    )
    if not video or not audio:
        raise ValueError("multimodal dead-air detection requires both video and audio streams")
    duration = _fraction((payload.get("format") or {}).get("duration"))
    if duration is None:
        duration = _fraction(video.get("duration"))
    fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    video_codec = str(video.get("codec_name") or "")
    pixel_format = str(video.get("pix_fmt") or "")
    audio_codec = str(audio.get("codec_name") or "")
    sample_rate = int(audio.get("sample_rate") or 0)
    channels = int(audio.get("channels") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if (
        duration is None
        or duration <= 0
        or fps is None
        or fps <= 0
        or width <= 0
        or height <= 0
        or not video_codec
        or not pixel_format
        or not audio_codec
        or sample_rate <= 0
        or channels <= 0
    ):
        raise ValueError(f"video metadata is incomplete: {path}")
    return {
        "duration": round(duration, 6),
        "fps": round(fps, 6),
        "width": width,
        "height": height,
        "rotation": rotation,
        "has_audio": True,
        "has_video": True,
        "video_codec": video_codec,
        "pixel_format": pixel_format,
        "audio_codec": audio_codec,
        "sample_rate": sample_rate,
        "channels": channels,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_info(path: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **{key: media[key] for key in MEDIA_KEYS},
    }


def parse_freezedetect(log: str, duration: Optional[float] = None) -> List[Segment]:
    """Parse FFmpeg freezedetect metadata, including a trailing freeze."""
    segments: List[Segment] = []
    current_start: Optional[float] = None
    for line in log.splitlines():
        start_match = re.search(r"freeze_start:\s*(-?[0-9.]+)", line)
        if start_match:
            current_start = max(0.0, float(start_match.group(1)))
        end_match = re.search(r"freeze_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            if end > current_start:
                segments.append(
                    Segment(_round4(current_start), _round4(end), _round4(end - current_start))
                )
            current_start = None
    if current_start is not None and duration is not None and duration > current_start:
        segments.append(
            Segment(_round4(current_start), _round4(duration), _round4(duration - current_start))
        )
    return segments


def detect_silences(path: Path, noise_db: float, min_silence: float, duration: float) -> List[Segment]:
    result = _run_checked(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            f"silencedetect=n={noise_db:.2f}dB:d={min_silence:.3f}",
            "-f",
            "null",
            "-",
        ],
        "silence detection",
    )
    return parse_silencedetect(result.stderr, duration=duration)


def detect_freezes(path: Path, noise: float, min_freeze: float, duration: float) -> List[Segment]:
    result = _run_checked(
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
        "freeze detection",
    )
    return parse_freezedetect(result.stderr, duration=duration)


def _segment_dict(segment: Segment) -> Dict[str, float]:
    return {"start": segment.start, "end": segment.end, "duration": segment.duration}


def _merge_segments(segments: Iterable[Segment], gap: float = 0.001) -> List[Segment]:
    merged: List[Segment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        if segment.end <= segment.start:
            continue
        if merged and segment.start <= merged[-1].end + gap:
            start = merged[-1].start
            end = max(merged[-1].end, segment.end)
            merged[-1] = Segment(_round4(start), _round4(end), _round4(end - start))
        else:
            merged.append(segment)
    return merged


def _intersections(target: Segment, intervals: Sequence[Segment]) -> List[Segment]:
    overlaps = []
    for interval in intervals:
        start = max(target.start, interval.start)
        end = min(target.end, interval.end)
        if end > start:
            overlaps.append(Segment(_round4(start), _round4(end), _round4(end - start)))
    return _merge_segments(overlaps)


def _validate_segments(segments: Sequence[Segment], duration: float, label: str) -> None:
    previous_end = 0.0
    for segment in segments:
        if segment.start < 0 or segment.end <= segment.start or segment.end > duration + 0.001:
            raise ValueError(f"invalid {label} interval: {segment}")
        if segment.start < previous_end - 0.001:
            raise ValueError(f"overlapping {label} intervals are not canonical")
        previous_end = segment.end


def derive_analysis(
    duration: float,
    silences: Sequence[Segment],
    freezes: Sequence[Segment],
    *,
    min_static_overlap_ratio: float,
    pad_seconds: float,
    min_keep_seconds: float,
    max_removal_ratio: float,
    allow_over_budget: bool,
) -> Dict[str, Any]:
    """Intersect silence and freeze evidence, then derive safe keep/remove intervals."""
    silences = _merge_segments(silences)
    freezes = _merge_segments(freezes)
    _validate_segments(silences, duration, "silence")
    _validate_segments(freezes, duration, "freeze")

    candidates: List[Dict[str, Any]] = []
    shared_intervals: List[Segment] = []
    for silence in silences:
        overlaps = _intersections(silence, freezes)
        overlap_seconds = sum(item.duration for item in overlaps)
        ratio = overlap_seconds / silence.duration if silence.duration else 0.0
        if ratio + 1e-9 < min_static_overlap_ratio:
            continue
        shared_intervals.extend(overlaps)
        candidates.append(
            {
                "silence": _segment_dict(silence),
                "static_overlap_seconds": _round4(overlap_seconds),
                "static_overlap_ratio": round(ratio, 4),
                "shared_intervals": [_segment_dict(item) for item in overlaps],
            }
        )

    shared_intervals = _merge_segments(shared_intervals)
    keep_segments = build_keep_segments(
        duration,
        shared_intervals,
        pad=pad_seconds,
        min_keep=min_keep_seconds,
    )
    removed_segments = infer_removed_segments(duration, keep_segments)
    removed_seconds = sum(item.duration for item in removed_segments)
    kept_seconds = sum(item.duration for item in keep_segments)
    proposed_ratio = removed_seconds / duration if duration else 0.0
    over_budget = proposed_ratio > max_removal_ratio + 1e-9
    blockers: List[str] = []
    warnings: List[str] = []
    if over_budget and not allow_over_budget:
        blockers.append(
            f"proposed multimodal removal is {proposed_ratio:.1%}, above the "
            f"{max_removal_ratio:.1%} safety budget; review candidates or explicitly approve "
            "--allow-over-budget"
        )
    elif over_budget:
        warnings.append(
            f"{proposed_ratio:.1%} removal exceeds the {max_removal_ratio:.1%} safety budget; "
            "explicit override recorded"
        )
    if shared_intervals and not keep_segments:
        blockers.append("all source media would be removed; refusing an empty output")
    return {
        "candidates": candidates,
        "shared_intervals": [_segment_dict(item) for item in shared_intervals],
        "removed_segments": [_segment_dict(item) for item in removed_segments],
        "keep_segments": [_segment_dict(item) for item in keep_segments],
        "removal_budget": {
            "max_ratio": round(max_removal_ratio, 4),
            "max_seconds": _round4(duration * max_removal_ratio),
            "proposed_ratio": round(proposed_ratio, 4),
            "proposed_seconds": _round4(removed_seconds),
            "over_budget": over_budget,
            "override": bool(allow_over_budget and over_budget),
        },
        "output_duration_estimate": _round4(kept_seconds),
        "blockers": blockers,
        "warnings": warnings,
        "status": "blocked" if blockers else "ready",
        "summary": {
            "silences": len(silences),
            "freezes": len(freezes),
            "candidates": len(candidates),
            "rejected_silences": len(silences) - len(candidates),
            "removed_seconds": _round4(removed_seconds),
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    payload = {
        key: plan.get(key)
        for key in (
            "version",
            "source",
            "delivery",
            "settings",
            "detections",
            "candidates",
            "shared_intervals",
            "removed_segments",
            "keep_segments",
            "removal_budget",
            "output_duration_estimate",
        )
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_plan(
    source_path: str,
    delivery_path: str,
    *,
    media: Mapping[str, Any],
    silences: Sequence[Segment],
    freezes: Sequence[Segment],
    noise_db: float,
    min_silence: float = DEFAULT_MIN_SILENCE,
    freeze_noise: float = DEFAULT_FREEZE_NOISE,
    min_freeze: float = DEFAULT_MIN_FREEZE,
    min_static_overlap_ratio: float = DEFAULT_STATIC_OVERLAP,
    pad_seconds: float = DEFAULT_PAD,
    fade_seconds: float = DEFAULT_FADE_SECONDS,
    min_keep_seconds: float = DEFAULT_MIN_KEEP,
    max_removal_ratio: float = DEFAULT_MAX_REMOVAL_RATIO,
    allow_over_budget: bool = False,
) -> Dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    delivery = _absolute_path(delivery_path)
    analysis = derive_analysis(
        float(media["duration"]),
        silences,
        freezes,
        min_static_overlap_ratio=min_static_overlap_ratio,
        pad_seconds=pad_seconds,
        min_keep_seconds=min_keep_seconds,
        max_removal_ratio=max_removal_ratio,
        allow_over_budget=allow_over_budget,
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "source": _source_info(source, media),
        "delivery": str(delivery),
        "settings": {
            "noise_db": round(noise_db, 2),
            "min_silence_seconds": round(min_silence, 4),
            "freeze_noise": round(freeze_noise, 6),
            "min_freeze_seconds": round(min_freeze, 4),
            "min_static_overlap_ratio": round(min_static_overlap_ratio, 4),
            "pad_seconds": round(pad_seconds, 4),
            "fade_seconds": round(fade_seconds, 4),
            "min_keep_seconds": round(min_keep_seconds, 4),
            "max_removal_ratio": round(max_removal_ratio, 4),
            "allow_over_budget": bool(allow_over_budget),
        },
        "detections": {
            "silences": [_segment_dict(item) for item in _merge_segments(silences)],
            "freezes": [_segment_dict(item) for item in _merge_segments(freezes)],
        },
        "review_contract": [
            "Review every removed interval with timeline_view.py before apply.",
            "After apply, watch the complete output at 1x with audio and rerun render_qa.py.",
            "The plan digest is not a signature or human approval.",
        ],
        **analysis,
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def _segments_from(value: Any, duration: float, label: str) -> List[Segment]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    segments: List[Segment] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} entries must be objects")
        try:
            start = float(item["start"])
            end = float(item["end"])
            stored_duration = float(item["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid {label} entry") from exc
        if abs(stored_duration - (end - start)) > 0.002:
            raise ValueError(f"{label} duration does not match its boundaries")
        segments.append(Segment(_round4(start), _round4(end), _round4(end - start)))
    segments = _merge_segments(segments)
    _validate_segments(segments, duration, label)
    return segments


def _settings_errors(settings: Mapping[str, Any]) -> List[str]:
    checks = (
        ("min_silence_seconds", 0.1, 60.0),
        ("freeze_noise", 0.0, 1.0),
        ("min_freeze_seconds", 0.1, 60.0),
        ("min_static_overlap_ratio", 0.01, 1.0),
        ("pad_seconds", 0.0, 5.0),
        ("fade_seconds", 0.0, 1.0),
        ("min_keep_seconds", 0.0, 10.0),
        ("max_removal_ratio", 0.0, 1.0),
    )
    errors = []
    for key, minimum, maximum in checks:
        try:
            value = float(settings[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"settings.{key} is missing or invalid")
            continue
        if not minimum <= value <= maximum:
            errors.append(f"settings.{key} must be between {minimum} and {maximum}")
    try:
        noise_db = float(settings["noise_db"])
        if not -80.0 <= noise_db <= 0.0:
            errors.append("settings.noise_db must be between -80 and 0")
    except (KeyError, TypeError, ValueError):
        errors.append("settings.noise_db is missing or invalid")
    if not isinstance(settings.get("allow_over_budget"), bool):
        errors.append("settings.allow_over_budget must be boolean")
    return errors


def _media_changed(stored: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    for key in MEDIA_KEYS:
        if key not in stored or key not in current:
            return True
        if isinstance(stored[key], float) or isinstance(current[key], float):
            if abs(float(stored[key]) - float(current[key])) > 0.001:
                return True
        elif stored[key] != current[key]:
            return True
    return False


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    blockers.extend(_settings_errors(settings))
    source_path = Path(str(source.get("path") or "")).expanduser()
    current_media: Optional[Dict[str, Any]] = None
    if not source_path.is_file():
        blockers.append(f"source file is missing: {source_path}")
    else:
        if source.get("sha256") != _sha256(source_path) or source.get("size_bytes") != source_path.stat().st_size:
            blockers.append("source bytes changed after the plan was created")
        try:
            current_media = probe_media(source_path)
        except (OSError, RuntimeError, ValueError) as exc:
            blockers.append(str(exc))
        if current_media is not None and _media_changed(source, current_media):
            blockers.append("source media contract changed after the plan was created")

    detections = plan.get("detections") if isinstance(plan.get("detections"), Mapping) else {}
    try:
        duration = float(source.get("duration") or 0)
        silences = _segments_from(detections.get("silences"), duration, "silences")
        freezes = _segments_from(detections.get("freezes"), duration, "freezes")
        if not blockers or not _settings_errors(settings):
            derived = derive_analysis(
                duration,
                silences,
                freezes,
                min_static_overlap_ratio=float(settings["min_static_overlap_ratio"]),
                pad_seconds=float(settings["pad_seconds"]),
                min_keep_seconds=float(settings["min_keep_seconds"]),
                max_removal_ratio=float(settings["max_removal_ratio"]),
                allow_over_budget=bool(settings["allow_over_budget"]),
            )
            for key in (
                "candidates",
                "shared_intervals",
                "removed_segments",
                "keep_segments",
                "removal_budget",
                "output_duration_estimate",
                "blockers",
                "warnings",
                "status",
                "summary",
            ):
                if plan.get(key) != derived[key]:
                    blockers.append(f"stored {key} does not match canonical multimodal analysis")
            blockers.extend(derived["blockers"])
            warnings.extend(derived["warnings"])
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(str(exc))

    if plan.get("plan_id") != _plan_id(plan):
        blockers.append("plan_id does not match canonical plan content")

    delivery = _absolute_path(str(plan.get("delivery") or ""))
    if delivery.is_symlink():
        blockers.append(f"refusing symlink delivery: {delivery}")
    application = plan.get("application")
    if application is not None:
        if not isinstance(application, Mapping):
            blockers.append("application must be an object")
        elif not delivery.is_file():
            blockers.append(f"applied delivery is missing: {delivery}")
        else:
            if application.get("sha256") != _sha256(delivery) or application.get("size_bytes") != delivery.stat().st_size:
                blockers.append("applied delivery bytes changed after validation")
            try:
                output_media = probe_media(delivery)
            except (OSError, RuntimeError, ValueError) as exc:
                blockers.append(str(exc))
            else:
                stored_media = application.get("media")
                if not isinstance(stored_media, Mapping) or _media_changed(stored_media, output_media):
                    blockers.append("applied delivery media contract changed after validation")
                expected = float(plan.get("output_duration_estimate") or 0)
                tolerance = max(0.2, 2.0 / float(source.get("fps") or 1.0))
                if abs(float(output_media["duration"]) - expected) > tolerance:
                    blockers.append("applied delivery duration does not match the cut plan")
            if application.get("full_decode_checked") is not True:
                blockers.append("application does not record a successful full decode")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    return {
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "warnings": warnings,
        "summary": {"blocking": len(blockers), "warnings": len(warnings)},
    }


def emit_markdown(plan: Mapping[str, Any], plan_path: Optional[str] = None) -> str:
    settings = plan["settings"]
    summary = plan["summary"]
    lines = [
        "# Multimodal Dead-Air Plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- Source: `{plan['source']['path']}`",
        f"- Delivery: `{plan['delivery']}`",
        f"- Source SHA-256: `{plan['source']['sha256']}`",
        f"- Silence threshold: `{settings['noise_db']} dB`, minimum `{settings['min_silence_seconds']}s`",
        f"- Freeze threshold: `{settings['freeze_noise']}`, minimum `{settings['min_freeze_seconds']}s`",
        f"- Required static coverage: `{settings['min_static_overlap_ratio']:.0%}`",
        f"- Proposed removal: `{summary['removed_seconds']:.3f}s` / `{plan['removal_budget']['proposed_ratio']:.1%}`",
        "",
        "A silence is eligible only when freeze evidence covers the configured share of that silence. "
        "The renderer removes only the shared interval, not the whole silence.",
        "",
        "## Candidates",
        "",
        "| # | Silence | Static coverage | Shared intervals |",
        "|---:|---:|---:|---|",
    ]
    for index, candidate in enumerate(plan.get("candidates") or [], start=1):
        silence = candidate["silence"]
        shared = ", ".join(
            f"{item['start']:.3f}–{item['end']:.3f}s" for item in candidate["shared_intervals"]
        )
        lines.append(
            f"| {index} | {silence['start']:.3f}–{silence['end']:.3f}s | "
            f"{candidate['static_overlap_ratio']:.1%} | {shared} |"
        )
    if not plan.get("candidates"):
        lines.append("| — | — | — | No silence met the multimodal threshold |")
    lines.extend(["", "## Review", ""])
    for item in plan.get("review_contract") or []:
        lines.append(f"- {item}")
    if plan.get("removed_segments"):
        cut_count = len(plan["removed_segments"])
        review_plan = plan_path or "work/multimodal_dead_air_plan.json"
        lines.extend(
            [
                "",
                "Review source cut boundaries:",
                "",
                "```bash",
                f"python3 scripts/timeline_view.py {shlex.quote(plan['source']['path'])} \\",
                f"  --cut-list {shlex.quote(str(review_plan))} \\",
                f"  --output-dir verify/dead-air-cuts --limit {cut_count}",
                "```",
            ]
        )
    if plan.get("blockers"):
        lines.extend(["", "## Blockers", ""] + [f"- {item}" for item in plan["blockers"]])
    if plan.get("warnings"):
        lines.extend(["", "## Warnings", ""] + [f"- {item}" for item in plan["warnings"]])
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, value: str) -> None:
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


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("plan root must be an object")
    return payload


def _ensure_outputs_available(paths: Sequence[Optional[Path]], force: bool) -> None:
    for path in paths:
        if path is None:
            continue
        if path.is_symlink():
            raise ValueError(f"refusing symlink output: {path}")
        if path.exists() and not force:
            raise ValueError(f"output exists; pass --force to replace: {path}")


def _ensure_distinct_artifacts(paths: Mapping[str, Path]) -> None:
    seen: Dict[Path, str] = {}
    for label, path in paths.items():
        canonical = path.resolve(strict=False)
        previous = seen.get(canonical)
        if previous is not None:
            raise ValueError(f"{label} must not overwrite {previous}: {path}")
        seen[canonical] = label


def _validate_rendered_output(path: Path, plan: Mapping[str, Any]) -> Dict[str, Any]:
    media = probe_media(path)
    source = plan["source"]
    expected = float(plan["output_duration_estimate"])
    tolerance = max(0.2, 2.0 / float(plan["source"]["fps"]))
    if abs(float(media["duration"]) - expected) > tolerance:
        raise RuntimeError(
            f"rendered duration {media['duration']:.3f}s does not match expected {expected:.3f}s"
        )
    if media["video_codec"] != "h264" or media["audio_codec"] != "aac":
        raise RuntimeError(
            "rendered codecs must be H.264/AAC, got "
            f"{media['video_codec']}/{media['audio_codec']}"
        )
    if media["pixel_format"] != "yuv420p":
        raise RuntimeError(
            f"rendered pixel format must be yuv420p, got {media['pixel_format']!r}"
        )
    for key in ("width", "height", "sample_rate", "channels"):
        if media[key] != source[key]:
            raise RuntimeError(
                f"rendered {key} {media[key]!r} does not match source contract {source[key]!r}"
            )
    if abs(float(media["fps"]) - float(source["fps"])) > 0.001:
        raise RuntimeError(
            f"rendered fps {media['fps']!r} does not match source contract {source['fps']!r}"
        )
    _run_checked(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        "full output decode",
    )
    return media


def apply_plan(plan_path: str, *, force: bool = False, markdown_path: Optional[str] = None) -> Dict[str, Any]:
    plan_file = _absolute_path(plan_path)
    if plan_file.is_symlink():
        raise ValueError(f"refusing symlink plan: {plan_file}")
    plan = _load_plan(plan_file)
    verification = verify_plan(plan)
    if verification["summary"]["blocking"]:
        raise ValueError("plan is not ready to apply: " + "; ".join(verification["blockers"]))
    if not plan.get("removed_segments"):
        raise ValueError("plan has no multimodal dead-air candidates; nothing to apply")
    source = Path(plan["source"]["path"]).resolve()
    delivery = _absolute_path(plan["delivery"])
    markdown_file = _absolute_path(markdown_path) if markdown_path else None
    named_paths = {"source": source, "plan": plan_file, "delivery": delivery}
    if markdown_file is not None:
        named_paths["markdown"] = markdown_file
    _ensure_distinct_artifacts(named_paths)
    if markdown_file is not None and markdown_file.is_symlink():
        raise ValueError(f"refusing symlink markdown: {markdown_file}")
    _ensure_outputs_available([delivery], force)
    delivery.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".mp4", dir=str(delivery.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        keep_segments = [Segment(**item) for item in plan["keep_segments"]]
        command = build_ffmpeg_command(
            str(source),
            str(temporary),
            keep_segments,
            has_video=True,
            fade_seconds=float(plan["settings"]["fade_seconds"]),
        )
        run_ffmpeg_with_fallback(command, has_video=True)
        media = _validate_rendered_output(temporary, plan)
        application = {
            "sha256": _sha256(temporary),
            "size_bytes": temporary.stat().st_size,
            "media": media,
            "full_decode_checked": True,
        }
        os.replace(temporary, delivery)
        plan["application"] = application
        _write_json(plan_file, plan)
        if markdown_file is not None:
            _atomic_write(markdown_file, emit_markdown(plan, str(plan_file)))
        return plan
    finally:
        if temporary.exists():
            temporary.unlink()


def _noise_db(value: str, source: Path) -> float:
    if value == "auto":
        return measure_adaptive_noise_db(str(source))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("--noise-db must be 'auto' or a number") from exc
    if not -80.0 <= parsed <= 0.0:
        raise ValueError("--noise-db must be between -80 and 0")
    return parsed


def _add_common_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source")
    parser.add_argument("--delivery", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown")
    parser.add_argument("--noise-db", default="auto")
    parser.add_argument("--min-silence", type=float, default=DEFAULT_MIN_SILENCE)
    parser.add_argument("--freeze-noise", type=float, default=DEFAULT_FREEZE_NOISE)
    parser.add_argument("--min-freeze", type=float, default=DEFAULT_MIN_FREEZE)
    parser.add_argument("--min-static-overlap", type=float, default=DEFAULT_STATIC_OVERLAP)
    parser.add_argument("--pad", type=float, default=DEFAULT_PAD)
    parser.add_argument("--fade-duration", type=float, default=DEFAULT_FADE_SECONDS)
    parser.add_argument("--min-keep", type=float, default=DEFAULT_MIN_KEEP)
    parser.add_argument("--max-removal-ratio", type=float, default=DEFAULT_MAX_REMOVAL_RATIO)
    parser.add_argument("--allow-over-budget", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservative silence + freeze dead-air cuts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="Detect multimodal dead air and write a source-bound plan")
    _add_common_plan_args(plan_parser)
    verify_parser = subparsers.add_parser("verify", help="Live-verify source, plan, and optional delivery")
    verify_parser.add_argument("plan")
    verify_parser.add_argument("--strict", action="store_true")
    apply_parser = subparsers.add_parser("apply", help="Render the verified plan in one FFmpeg pass")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            source = Path(args.source).expanduser().resolve()
            if not source.is_file():
                raise ValueError(f"source file is missing: {source}")
            if not 0.01 <= args.min_static_overlap <= 1.0:
                raise ValueError("--min-static-overlap must be between 0.01 and 1")
            if not 0.0 <= args.max_removal_ratio <= 1.0:
                raise ValueError("--max-removal-ratio must be between 0 and 1")
            if args.min_silence < 0.1 or args.min_freeze < 0.1:
                raise ValueError("--min-silence and --min-freeze must be at least 0.1 seconds")
            if not 0.0 <= args.freeze_noise <= 1.0:
                raise ValueError("--freeze-noise must be between 0 and 1")
            if args.pad < 0 or args.fade_duration < 0 or args.min_keep < 0:
                raise ValueError("--pad, --fade-duration, and --min-keep must be non-negative")
            plan_path = _absolute_path(args.output)
            markdown_path = _absolute_path(args.markdown) if args.markdown else None
            delivery_path = _absolute_path(args.delivery)
            named_paths = {"source": source, "delivery": delivery_path, "plan": plan_path}
            if markdown_path is not None:
                named_paths["markdown"] = markdown_path
            _ensure_distinct_artifacts(named_paths)
            if delivery_path.is_symlink():
                raise ValueError(f"refusing symlink delivery: {delivery_path}")
            _ensure_outputs_available([plan_path, markdown_path], args.force)
            media = probe_media(source)
            noise_db = _noise_db(args.noise_db, source)
            silences = detect_silences(source, noise_db, args.min_silence, float(media["duration"]))
            freezes = detect_freezes(source, args.freeze_noise, args.min_freeze, float(media["duration"]))
            plan = build_plan(
                str(source),
                str(delivery_path),
                media=media,
                silences=silences,
                freezes=freezes,
                noise_db=noise_db,
                min_silence=args.min_silence,
                freeze_noise=args.freeze_noise,
                min_freeze=args.min_freeze,
                min_static_overlap_ratio=args.min_static_overlap,
                pad_seconds=args.pad,
                fade_seconds=args.fade_duration,
                min_keep_seconds=args.min_keep,
                max_removal_ratio=args.max_removal_ratio,
                allow_over_budget=args.allow_over_budget,
            )
            _write_json(plan_path, plan)
            if markdown_path:
                _atomic_write(markdown_path, emit_markdown(plan, str(plan_path)))
            print(
                f"Multimodal dead-air plan: {plan['status']} | "
                f"candidates={plan['summary']['candidates']} | "
                f"removed={plan['summary']['removed_seconds']:.3f}s"
            )
            return 2 if args.strict and plan["summary"]["blocking"] else 0
        if args.command == "verify":
            verification = verify_plan(_load_plan(_absolute_path(args.plan)))
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 2 if args.strict and verification["summary"]["blocking"] else 0
        plan = apply_plan(args.plan, force=args.force, markdown_path=args.markdown)
        print(
            f"Multimodal dead-air delivery: {plan['delivery']} | "
            f"removed={plan['summary']['removed_seconds']:.3f}s"
        )
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
