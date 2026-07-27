#!/usr/bin/env python3
"""Align multiple camera recordings to one reversible reference timeline.

The tool reuses audio_sync.py's dependency-free envelope correlation, records
one offset and confidence score per angle, computes common coverage, and can
render a short aligned grid preview. Source media is never modified.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audio_sync import (  # noqa: E402
    AudioSyncError,
    decode_audio_envelope,
    estimate_offset,
    probe_duration,
)


VERSION = "multicam_sync_plan.v1"
LONG_FORM_SECONDS = 30 * 60
MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s*dB", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round4(value: float) -> float:
    return round(float(value), 4)


def _round3(value: float) -> float:
    return round(float(value), 3)


def _absolute(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


def _probe_audio_stream_count(path: str) -> int:
    result = _run([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        path,
    ])
    if result.returncode != 0:
        raise AudioSyncError(result.stderr.strip() or f"ffprobe failed: {path}")
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _mean_volume_db(
    path: str,
    *,
    stream_index: int,
    start_seconds: float,
    duration_seconds: float,
) -> Optional[float]:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "info"]
    if start_seconds > 0:
        cmd.extend(["-ss", f"{start_seconds:.3f}"])
    cmd.extend([
        "-i",
        path,
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        f"0:a:{stream_index}",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ])
    result = _run(cmd)
    match = MEAN_VOLUME_RE.search(result.stderr or "")
    if not match:
        return None
    raw = match.group(1).lower()
    return float("-inf") if raw == "-inf" else float(raw)


def select_audio_stream(
    path: str,
    *,
    duration: Optional[float],
    override: Optional[int] = None,
    probe_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Select an audio stream, preferring the loudest track for multi-track media."""

    stream_count = _probe_audio_stream_count(path)
    if stream_count < 1:
        raise AudioSyncError(f"no audio stream found: {path}")
    if override is not None:
        if override < 0 or override >= stream_count:
            raise AudioSyncError(
                f"audio stream {override} is outside 0..{stream_count - 1}: {path}"
            )
        return {
            "index": int(override),
            "method": "manual",
            "stream_count": stream_count,
            "probe_start_seconds": None,
            "probe_duration_seconds": None,
            "candidates": [],
        }
    if stream_count == 1:
        return {
            "index": 0,
            "method": "single_stream",
            "stream_count": 1,
            "probe_start_seconds": None,
            "probe_duration_seconds": None,
            "candidates": [{"index": 0, "mean_volume_db": None}],
        }

    effective_duration = max(1.0, min(probe_seconds, duration or probe_seconds))
    start = max(0.0, ((duration or effective_duration) - effective_duration) / 2.0)
    candidates: List[Dict[str, Any]] = []
    for index in range(stream_count):
        mean_db = _mean_volume_db(
            path,
            stream_index=index,
            start_seconds=start,
            duration_seconds=effective_duration,
        )
        candidates.append({
            "index": index,
            "mean_volume_db": _round3(mean_db) if mean_db is not None and math.isfinite(mean_db) else mean_db,
        })

    measurable = [
        item for item in candidates
        if item["mean_volume_db"] is not None and math.isfinite(float(item["mean_volume_db"]))
    ]
    if not measurable:
        raise AudioSyncError(f"could not measure any audio stream loudness: {path}")
    selected = max(measurable, key=lambda item: float(item["mean_volume_db"]))
    return {
        "index": int(selected["index"]),
        "method": "loudest_mean_volume",
        "stream_count": stream_count,
        "probe_start_seconds": _round3(start),
        "probe_duration_seconds": _round3(effective_duration),
        "candidates": candidates,
    }


def coverage_for_offset(
    *,
    reference_duration: float,
    source_duration: float,
    offset_seconds: float,
) -> Tuple[Optional[List[float]], Optional[List[float]]]:
    """Return shared coverage in reference and source clocks.

    offset_seconds is the source t=0 position on the reference timeline.
    """

    ref_start = max(0.0, offset_seconds)
    ref_end = min(reference_duration, offset_seconds + source_duration)
    if ref_end <= ref_start:
        return None, None
    src_start = ref_start - offset_seconds
    src_end = ref_end - offset_seconds
    return (
        [_round4(ref_start), _round4(ref_end)],
        [_round4(src_start), _round4(src_end)],
    )


def common_overlap(angles: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, float]]:
    coverages = [item.get("coverage_in_reference") for item in angles]
    if not coverages or any(not isinstance(value, list) or len(value) != 2 for value in coverages):
        return None
    start = max(float(value[0]) for value in coverages)
    end = min(float(value[1]) for value in coverages)
    if end <= start:
        return None
    return {
        "start": _round4(start),
        "end": _round4(end),
        "duration": _round4(end - start),
    }


def _manual_alignment(offset_seconds: float, frame_seconds: float) -> Dict[str, Any]:
    return {
        "offset_seconds": _round4(offset_seconds),
        "lag_frames": None,
        "frame_seconds": _round4(frame_seconds),
        "score": None,
        "score_margin": None,
        "confidence": 1.0,
        "confidence_source": "manual_assertion",
        "matched_frames": None,
        "matched_seconds": None,
        "searched_offsets": None,
    }


def _status_from_alignment(alignment: Mapping[str, Any], min_confidence: float, method: str) -> str:
    if method == "manual_offset":
        return "ready"
    confidence = alignment.get("confidence")
    return "ready" if confidence is not None and float(confidence) >= min_confidence else "review"


def _angle_id(index: int, path: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(path).stem).strip("-").lower()
    return f"angle_{index:02d}_{stem or 'media'}"


def validate_output_paths(
    outputs: Mapping[str, Optional[str]],
    source_paths: Sequence[str],
) -> None:
    """Prevent report/preview outputs from aliasing sources or each other."""

    sources = [_absolute(path) for path in source_paths]
    resolved_sources = {os.path.realpath(path) for path in sources}
    seen: Dict[str, str] = {}
    for label, raw_path in outputs.items():
        if not raw_path:
            continue
        path = _absolute(raw_path)
        resolved = os.path.realpath(path)
        if resolved in resolved_sources:
            raise ValueError(f"{label} must not overwrite a source file")
        if os.path.exists(path):
            for source in sources:
                if os.path.exists(source) and os.path.samefile(path, source):
                    raise ValueError(f"{label} must not overwrite a source file")
        if resolved in seen:
            raise ValueError(f"{label} must differ from {seen[resolved]}")
        seen[resolved] = label


def evaluate_pairwise_consistency(
    angles: Sequence[Dict[str, Any]],
    envelopes: Mapping[str, Sequence[float]],
    *,
    frame_seconds: float,
    max_offset_seconds: float,
    threshold_seconds: float = 0.08,
) -> Dict[str, Any]:
    """Cross-check non-reference angles against their reference-implied offsets."""

    pairs: List[Dict[str, Any]] = []
    errors: List[str] = []
    threshold = max(frame_seconds, threshold_seconds)
    for left_index in range(1, len(angles)):
        left = angles[left_index]
        left_path = str((left.get("media") or {}).get("path") or "")
        left_env = envelopes.get(left_path)
        if left_env is None or not left.get("alignment"):
            continue
        for right_index in range(left_index + 1, len(angles)):
            right = angles[right_index]
            right_path = str((right.get("media") or {}).get("path") or "")
            right_env = envelopes.get(right_path)
            if right_env is None or not right.get("alignment"):
                continue
            implied = (
                float(right["alignment"]["offset_seconds"])
                - float(left["alignment"]["offset_seconds"])
            )
            pair_search = max(
                max_offset_seconds * 2.0,
                abs(implied) + threshold,
            )
            try:
                direct = estimate_offset(
                    left_env,
                    right_env,
                    frame_seconds=frame_seconds,
                    max_offset_seconds=pair_search,
                )
            except AudioSyncError as exc:
                errors.append(f"{left.get('id')}:{right.get('id')}: {exc}")
                for item in (left, right):
                    if item.get("status") == "ready":
                        item["status"] = "review"
                    if "pairwise_check_failed" not in item.setdefault("warnings", []):
                        item["warnings"].append("pairwise_check_failed")
                continue
            direct_offset = float(direct["offset_seconds"])
            divergence = abs(direct_offset - implied)
            inconsistent = divergence > threshold
            pair = {
                "left": left.get("id"),
                "right": right.get("id"),
                "implied_offset_seconds": _round4(implied),
                "direct_offset_seconds": _round4(direct_offset),
                "divergence_seconds": _round4(divergence),
                "confidence": direct.get("confidence"),
                "inconsistent": inconsistent,
            }
            pairs.append(pair)
            if inconsistent:
                for item in (left, right):
                    if item.get("status") == "ready":
                        item["status"] = "review"
                    if "pairwise_offset_inconsistent" not in item.setdefault("warnings", []):
                        item["warnings"].append("pairwise_offset_inconsistent")

    divergences = [float(item["divergence_seconds"]) for item in pairs]
    blocking = sum(bool(item["inconsistent"]) for item in pairs) + len(errors)
    return {
        "checked": bool(pairs),
        "threshold_seconds": _round4(threshold),
        "pairs": pairs,
        "max_divergence_seconds": _round4(max(divergences)) if divergences else None,
        "blocking": blocking,
        "errors": errors,
    }


def build_aligned_preview_command(
    angles: Sequence[Mapping[str, Any]],
    *,
    overlap: Mapping[str, Any],
    output_path: str,
    duration_seconds: float = 20.0,
    cell_width: int = 480,
    cell_height: int = 270,
) -> List[str]:
    if len(angles) < 2:
        raise ValueError("aligned preview requires at least two angles")
    if cell_width <= 0 or cell_height <= 0:
        raise ValueError("preview cell dimensions must be positive")

    input_paths = [str((item.get("media") or {}).get("path") or "") for item in angles]
    output_abs = _absolute(output_path)
    validate_output_paths({"preview output": output_abs}, [path for path in input_paths if path])

    start = float(overlap["start"])
    available = float(overlap["duration"])
    duration = min(max(0.1, duration_seconds), available)
    cmd: List[str] = ["ffmpeg", "-y"]
    for item, path in zip(angles, input_paths):
        offset = float((item.get("alignment") or {}).get("offset_seconds") or 0.0)
        local_start = max(0.0, start - offset)
        cmd.extend(["-ss", f"{local_start:.4f}", "-i", path])

    filters: List[str] = []
    labels: List[str] = []
    for index in range(len(angles)):
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v:0]"
            f"scale={cell_width}:{cell_height}:force_original_aspect_ratio=decrease,"
            f"pad={cell_width}:{cell_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,setpts=PTS-STARTPTS[{label}]"
        )

    columns = 2 if len(angles) > 1 else 1
    layout = "|".join(
        f"{(index % columns) * cell_width}_{(index // columns) * cell_height}"
        for index in range(len(angles))
    )
    filters.append(
        "".join(labels)
        + f"xstack=inputs={len(angles)}:layout={layout}:fill=black[vout]"
    )
    reference_stream = int((angles[0].get("audio_stream") or {}).get("index") or 0)
    cmd.extend([
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        f"0:a:{reference_stream}?",
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-shortest",
        output_abs,
    ])
    return cmd


def build_multicam_sync_plan(
    *,
    reference_media: str,
    angle_media: Sequence[str],
    manual_offsets: Optional[Mapping[str, float]] = None,
    audio_stream_overrides: Optional[Mapping[str, int]] = None,
    sample_rate: int = 8000,
    frame_ms: float = 40.0,
    max_offset_seconds: float = 60.0,
    max_probe_seconds: Optional[float] = 180.0,
    min_confidence: float = 0.45,
    pairwise_check: bool = True,
    pairwise_threshold_seconds: float = 0.08,
    preview_output: Optional[str] = None,
    preview_duration: float = 20.0,
) -> Dict[str, Any]:
    if not angle_media:
        raise ValueError("at least one --angle is required")
    if sample_rate <= 0 or frame_ms <= 0:
        raise ValueError("sample_rate and frame_ms must be positive")
    if max_offset_seconds < 0:
        raise ValueError("max_offset_seconds must be non-negative")
    if max_probe_seconds is not None and max_probe_seconds <= 0:
        raise ValueError("max_probe_seconds must be positive or None")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    if pairwise_threshold_seconds <= 0:
        raise ValueError("pairwise_threshold_seconds must be positive")
    if preview_duration <= 0:
        raise ValueError("preview_duration must be positive")

    paths = [_absolute(reference_media)] + [_absolute(path) for path in angle_media]
    if len(set(paths)) != len(paths):
        raise ValueError("reference and angle paths must be unique")
    manual = {_absolute(path): float(value) for path, value in (manual_offsets or {}).items()}
    if any(not math.isfinite(value) for value in manual.values()):
        raise ValueError("manual offsets must be finite")
    stream_overrides = {
        _absolute(path): int(value) for path, value in (audio_stream_overrides or {}).items()
    }
    unknown_manual = sorted(set(manual) - set(paths[1:]))
    if unknown_manual:
        raise ValueError(f"manual offset path is not an --angle: {unknown_manual[0]}")
    unknown_streams = sorted(set(stream_overrides) - set(paths))
    if unknown_streams:
        raise ValueError(f"audio stream path is not an input: {unknown_streams[0]}")

    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    reference_duration: Optional[float] = None
    reference_envelope: Optional[List[float]] = None
    envelopes: Dict[str, Sequence[float]] = {}
    automatic_paths = [path for path in paths[1:] if path not in manual]

    for index, path in enumerate(paths):
        exists = Path(path).is_file()
        duration = probe_duration(path) if exists else None
        record: Dict[str, Any] = {
            "id": _angle_id(index, path),
            "role": "reference" if index == 0 else "angle",
            "media": {
                "path": path,
                "exists": exists,
                "duration": _round3(duration) if duration else None,
            },
            "status": "blocked",
            "method": "reference" if index == 0 else None,
            "audio_stream": None,
            "alignment": None,
            "coverage_in_reference": None,
            "coverage_in_source": None,
            "warnings": [],
        }
        if not exists:
            record["warnings"].append("media_missing")
        elif duration is None:
            record["warnings"].append("duration_unavailable")
        else:
            try:
                record["audio_stream"] = select_audio_stream(
                    path,
                    duration=duration,
                    override=stream_overrides.get(path),
                )
            except AudioSyncError as exc:
                record["warnings"].append("audio_stream_unavailable")
                record["error"] = str(exc)

        if index == 0:
            reference_duration = duration
            record["alignment"] = _manual_alignment(0.0, frame_ms / 1000.0)
            if exists and duration and (record["audio_stream"] or not automatic_paths):
                record["status"] = "ready"
                record["coverage_in_reference"] = [0.0, _round4(duration)]
                record["coverage_in_source"] = [0.0, _round4(duration)]
                if record["audio_stream"] is None:
                    record.pop("error", None)
                    record["warnings"] = [
                        warning for warning in record["warnings"]
                        if warning != "audio_stream_unavailable"
                    ]
                    record["warnings"].append("manual_only_reference_without_audio")
            records.append(record)
            continue

        if not exists or duration is None or reference_duration is None:
            records.append(record)
            continue

        if path in manual:
            alignment = _manual_alignment(manual[path], frame_ms / 1000.0)
            method = "manual_offset"
            record["warnings"].append("manual_offset_not_independently_verified")
            if record["audio_stream"] is None:
                record.pop("error", None)
                record["warnings"] = [
                    warning for warning in record["warnings"]
                    if warning != "audio_stream_unavailable"
                ]
                record["warnings"].append("manual_offset_without_audio")
        else:
            if not record["audio_stream"]:
                records.append(record)
                continue
            method = "envelope_cross_correlation"
            try:
                if reference_envelope is None:
                    reference_stream = records[0].get("audio_stream") or {}
                    reference_envelope = decode_audio_envelope(
                        paths[0],
                        sample_rate=sample_rate,
                        frame_ms=frame_ms,
                        max_duration=max_probe_seconds,
                        audio_stream_index=int(reference_stream["index"]),
                    )
                    envelopes[paths[0]] = reference_envelope
                source_envelope = decode_audio_envelope(
                    path,
                    sample_rate=sample_rate,
                    frame_ms=frame_ms,
                    max_duration=max_probe_seconds,
                    audio_stream_index=int(record["audio_stream"]["index"]),
                )
                envelopes[path] = source_envelope
                alignment = estimate_offset(
                    reference_envelope,
                    source_envelope,
                    frame_seconds=frame_ms / 1000.0,
                    max_offset_seconds=max_offset_seconds,
                )
            except (AudioSyncError, KeyError, TypeError, ValueError) as exc:
                record["warnings"].append("alignment_failed")
                record["error"] = str(exc)
                records.append(record)
                continue

        offset = float(alignment["offset_seconds"])
        coverage_ref, coverage_src = coverage_for_offset(
            reference_duration=reference_duration,
            source_duration=duration,
            offset_seconds=offset,
        )
        record["method"] = method
        record["alignment"] = alignment
        record["coverage_in_reference"] = coverage_ref
        record["coverage_in_source"] = coverage_src
        record["status"] = _status_from_alignment(alignment, min_confidence, method)
        if record["status"] == "review":
            record["warnings"].append("low_confidence_alignment")
        if coverage_ref is None:
            record["status"] = "blocked"
            record["warnings"].append("no_reference_overlap")
        searched = alignment.get("searched_offsets")
        if isinstance(searched, list) and len(searched) == 2:
            boundary = max(abs(float(searched[0])), abs(float(searched[1])))
            if abs(abs(offset) - boundary) <= float(alignment["frame_seconds"]):
                record["warnings"].append("offset_near_search_boundary")
        records.append(record)

    pairwise = {
        "checked": False,
        "threshold_seconds": _round4(max(frame_ms / 1000.0, pairwise_threshold_seconds)),
        "pairs": [],
        "max_divergence_seconds": None,
        "blocking": 0,
        "errors": [],
    }
    if pairwise_check and len(records) >= 3:
        pairwise = evaluate_pairwise_consistency(
            records,
            envelopes,
            frame_seconds=frame_ms / 1000.0,
            max_offset_seconds=max_offset_seconds,
            threshold_seconds=pairwise_threshold_seconds,
        )
        if pairwise["errors"]:
            warnings.append("pairwise_consistency_check_incomplete")
        if pairwise["blocking"]:
            warnings.append("pairwise_offset_inconsistent")

    overlap = common_overlap(records)
    if overlap is None:
        warnings.append("no_common_overlap")
    elif float(overlap["duration"]) < 1.0:
        warnings.append("common_overlap_under_one_second")

    if any(
        float((item.get("media") or {}).get("duration") or 0.0) >= LONG_FORM_SECONDS
        for item in records
    ):
        warnings.append("clock_drift_not_measured_for_long_form")

    preview: Dict[str, Any] = {
        "output": _absolute(preview_output) if preview_output else None,
        "duration": None,
        "command": None,
        "applied": False,
        "output_exists": False,
    }
    if preview_output and overlap and all(item.get("status") != "blocked" for item in records):
        preview["duration"] = _round3(min(preview_duration, float(overlap["duration"])))
        preview["command"] = build_aligned_preview_command(
            records,
            overlap=overlap,
            output_path=preview_output,
            duration_seconds=preview_duration,
        )

    ready_count = sum(item.get("status") == "ready" for item in records)
    review_count = sum(item.get("status") == "review" for item in records)
    blocked_count = sum(item.get("status") == "blocked" for item in records)
    blocking = blocked_count + review_count
    overlap_blocked = overlap is None or (overlap and float(overlap["duration"]) < 1.0)
    if overlap_blocked:
        blocking += 1
    status = "blocked" if blocked_count or overlap_blocked else "review" if review_count else "ready"

    next_actions = [
        "Review every offset, confidence score, selected audio stream, and coverage window.",
        "Render or inspect the aligned preview before building a multicam edit.",
        "Keep source files unchanged; consume offsets in an NLE, EDL, or FFmpeg plan.",
    ]
    if "clock_drift_not_measured_for_long_form" in warnings:
        next_actions.insert(
            0,
            "Long-form input detected: verify head/middle/tail sync because V1 does not measure clock drift.",
        )

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": status,
        "source_safety": {
            "originals_modified": False,
            "source_media_reencoded": False,
            "preview_is_derivative": True,
        },
        "settings": {
            "sample_rate": int(sample_rate),
            "frame_ms": _round3(frame_ms),
            "max_offset_seconds": _round3(max_offset_seconds),
            "max_probe_seconds": _round3(max_probe_seconds) if max_probe_seconds else None,
            "min_confidence": _round3(min_confidence),
            "pairwise_check": bool(pairwise_check),
            "pairwise_threshold_seconds": _round4(pairwise_threshold_seconds),
            "clock_drift_measured": False,
            "offset_semantics": (
                "source t=0 on the reference timeline; positive places the source later"
            ),
        },
        "reference_media": paths[0],
        "angles": records,
        "common_overlap_in_reference": overlap,
        "pairwise_consistency": pairwise,
        "preview": preview,
        "summary": {
            "inputs": len(records),
            "ready": ready_count,
            "review": review_count,
            "blocked": blocked_count,
            "blocking": blocking,
            "preview_failed": 0,
            "common_overlap_seconds": _round3(float(overlap["duration"])) if overlap else 0.0,
            "manual_offsets": sum(item.get("method") == "manual_offset" for item in records),
            "auto_alignments": sum(
                item.get("method") == "envelope_cross_correlation" for item in records
            ),
            "max_pairwise_divergence_seconds": pairwise.get("max_divergence_seconds"),
            "warnings": len(warnings) + sum(len(item.get("warnings") or []) for item in records),
        },
        "warnings": warnings,
        "next_actions": next_actions,
    }


def _shell_quote(value: Any) -> str:
    text = str(value)
    if not text:
        return "''"
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+-=.,/:@%"
    if all(char in safe for char in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    overlap = plan.get("common_overlap_in_reference") or {}
    lines = [
        "# Multicam Sync Plan",
        "",
        f"- version: `{plan.get('version')}`",
        f"- status: `{plan.get('status')}`",
        f"- inputs: `{summary.get('inputs')}`",
        f"- blocking: `{summary.get('blocking')}`",
        (
            f"- common overlap: `{overlap.get('start')}` → `{overlap.get('end')}` "
            f"(`{overlap.get('duration')}`s)"
            if overlap else "- common overlap: `none`"
        ),
        "- source media: unchanged; preview is a disposable derivative",
        "",
        "| angle | role | offset (s) | confidence | audio stream | coverage (reference) | status |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in plan.get("angles") or []:
        alignment = item.get("alignment") or {}
        stream = item.get("audio_stream") or {}
        coverage = item.get("coverage_in_reference")
        coverage_text = (
            f"{coverage[0]} → {coverage[1]}" if isinstance(coverage, list) and len(coverage) == 2 else "—"
        )
        lines.append(
            "| `{id}` | {role} | {offset} | {confidence} | {stream} | {coverage} | {status} |".format(
                id=item.get("id"),
                role=item.get("role"),
                offset=alignment.get("offset_seconds"),
                confidence=alignment.get("confidence"),
                stream=stream.get("index"),
                coverage=coverage_text,
                status=item.get("status"),
            )
        )
        if item.get("error"):
            lines.append(f"|  | error | `{item.get('error')}` |  |  |  |  |")
        for warning in item.get("warnings") or []:
            lines.append(f"|  | warning | `{warning}` |  |  |  |  |")

    warnings = list(plan.get("warnings") or [])
    if warnings:
        lines.extend(["", "## Plan Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in warnings)

    pairwise = plan.get("pairwise_consistency") or {}
    if pairwise.get("checked"):
        lines.extend([
            "",
            "## Pairwise Consistency",
            "",
            f"- threshold_seconds: `{pairwise.get('threshold_seconds')}`",
            f"- max_divergence_seconds: `{pairwise.get('max_divergence_seconds')}`",
            "",
            "| left | right | implied offset | direct offset | divergence | status |",
            "|---|---|---:|---:|---:|---|",
        ])
        for pair in pairwise.get("pairs") or []:
            lines.append(
                f"| `{pair.get('left')}` | `{pair.get('right')}` | "
                f"{pair.get('implied_offset_seconds')} | {pair.get('direct_offset_seconds')} | "
                f"{pair.get('divergence_seconds')} | "
                f"{'review' if pair.get('inconsistent') else 'ready'} |"
            )

    preview = plan.get("preview") or {}
    if preview.get("command"):
        lines.extend([
            "",
            "## Aligned Preview Command",
            "",
            "```bash",
            " ".join(_shell_quote(part) for part in preview["command"]),
            "```",
        ])

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Positive offset means that angle starts later on the reference timeline.",
        "- Automatic confidence below the configured threshold must be reviewed.",
        "- V1 does not measure clock drift; long recordings need head/middle/tail checks.",
    ])
    next_actions = list(plan.get("next_actions") or [])
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {item}" for item in next_actions)
    lines.append("")
    return "\n".join(lines)


def _parse_path_values(values: Sequence[str], *, value_type: Any, label: str) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"{label} must look like PATH=VALUE")
        path, raw_value = raw.rsplit("=", 1)
        if not path.strip():
            raise ValueError(f"{label} path is empty")
        try:
            value = value_type(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {label} value: {raw_value!r}") from exc
        parsed[_absolute(path.strip())] = value
    return parsed


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(_absolute(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(_absolute(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Align 2+ camera recordings to one auditable reference timeline.",
    )
    parser.add_argument("--reference-media", required=True, help="Reference camera/audio media.")
    parser.add_argument(
        "--angle",
        action="append",
        required=True,
        help="Additional camera/audio media; repeat for each angle.",
    )
    parser.add_argument("--output", required=True, help="JSON multicam_sync_plan.v1 output.")
    parser.add_argument("--markdown", help="Optional Markdown review output.")
    parser.add_argument(
        "--manual-offset",
        action="append",
        default=[],
        metavar="PATH=SECONDS",
        help="Use an explicit source-t0-on-reference offset for one --angle.",
    )
    parser.add_argument(
        "--audio-stream",
        action="append",
        default=[],
        metavar="PATH=INDEX",
        help="Override the logical 0:a:INDEX used for one input.",
    )
    parser.add_argument("--sample-rate", type=int, default=8000)
    parser.add_argument("--frame-ms", type=float, default=40.0)
    parser.add_argument("--max-offset", type=float, default=60.0)
    parser.add_argument(
        "--max-probe-seconds",
        type=float,
        default=180.0,
        help="Decode this many seconds from each input; 0 uses the full file.",
    )
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument(
        "--no-pairwise-check",
        action="store_true",
        help="Skip cross-checking non-reference angle offsets against each other.",
    )
    parser.add_argument(
        "--pairwise-threshold",
        type=float,
        default=0.08,
        help="Maximum reference-implied vs direct pair offset divergence.",
    )
    parser.add_argument("--preview-output", help="Optional aligned grid MP4 output.")
    parser.add_argument("--preview-duration", type=float, default=20.0)
    parser.add_argument(
        "--apply-preview",
        action="store_true",
        help="Run the generated FFmpeg preview command after writing the plan.",
    )
    parser.add_argument("--strict", action="store_true", help="Return 2 unless the plan is ready.")
    args = parser.parse_args(argv)

    if args.apply_preview and not args.preview_output:
        parser.error("--apply-preview requires --preview-output")
    try:
        validate_output_paths(
            {
                "--output": args.output,
                "--markdown": args.markdown,
                "--preview-output": args.preview_output,
            },
            [args.reference_media] + list(args.angle),
        )
        manual_offsets = _parse_path_values(
            args.manual_offset,
            value_type=float,
            label="--manual-offset",
        )
        stream_overrides = _parse_path_values(
            args.audio_stream,
            value_type=int,
            label="--audio-stream",
        )
        plan = build_multicam_sync_plan(
            reference_media=args.reference_media,
            angle_media=args.angle,
            manual_offsets=manual_offsets,
            audio_stream_overrides=stream_overrides,
            sample_rate=args.sample_rate,
            frame_ms=args.frame_ms,
            max_offset_seconds=args.max_offset,
            max_probe_seconds=None if args.max_probe_seconds == 0 else args.max_probe_seconds,
            min_confidence=args.min_confidence,
            pairwise_check=not args.no_pairwise_check,
            pairwise_threshold_seconds=args.pairwise_threshold,
            preview_output=args.preview_output,
            preview_duration=args.preview_duration,
        )
    except (AudioSyncError, OSError, ValueError) as exc:
        print(f"multicam_sync error: {exc}", file=sys.stderr)
        return 1

    command = (plan.get("preview") or {}).get("command")
    if args.apply_preview:
        if not command:
            print("multicam_sync error: preview command unavailable while plan is blocked", file=sys.stderr)
            _write_json(args.output, plan)
            if args.markdown:
                _write_text(args.markdown, emit_markdown(plan))
            return 2
        os.makedirs(os.path.dirname(str(plan["preview"]["output"])) or ".", exist_ok=True)
        result = _run(command)
        preview = plan["preview"]
        preview["applied"] = result.returncode == 0
        preview["output_exists"] = bool(
            preview.get("output") and Path(str(preview["output"])).is_file()
        )
        if result.returncode != 0 or not preview["output_exists"]:
            preview["error"] = result.stderr.strip() or "aligned preview render failed"
            plan["status"] = "blocked"
            plan["summary"]["blocking"] = int(plan["summary"]["blocking"]) + 1
            plan["summary"]["preview_failed"] = 1
            if "preview_render_failed" not in plan["warnings"]:
                plan["warnings"].append("preview_render_failed")
                plan["summary"]["warnings"] = int(plan["summary"]["warnings"]) + 1
        else:
            preview["rendered_duration"] = probe_duration(str(preview["output"]))

    _write_json(args.output, plan)
    if args.markdown:
        _write_text(args.markdown, emit_markdown(plan))
    if args.strict and (plan.get("status") != "ready" or int(plan["summary"]["blocking"])):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
