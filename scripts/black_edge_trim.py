#!/usr/bin/env python3
"""Plan, render, review, and verify source-bound edge-black trimming.

The detector only proposes leading/trailing black ranges.  With the default
``silent_only`` audio policy, every removed range must also be covered by
detected silence.  Rendering writes a new CFR MP4 plus a normal-speed proof of
the original edge windows; a human review remains mandatory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from loop_fill import (
    CADENCE_ALGORITHM,
    HDR_TRANSFERS,
    _atomic_write_json,
    _atomic_write_text,
    _decode_command,
    _fingerprint,
    _inside_project,
    _media_info,
    _resolve_project_path,
    _run_checked,
    _run_command,
    _source_rate,
    _temporary_output,
    utc_now,
)


VERSION = "black_edge_trim_plan.v1"
PENDING_APPLY = "edge-black trim has not been rendered and validated"
PENDING_CONFIRM = "edge proof and full trimmed delivery review have not been confirmed"
REVIEW_FIELDS = (
    "first_visible_frame",
    "last_visible_frame",
    "content_coverage",
    "audio_continuity",
)
REVIEW_CHOICES = {"pass", "fail", "unobservable", "not_applicable"}
DETECTOR_ALGORITHM = {
    "id": "ffmpeg-edge-black-plus-silence-v1",
    "video": "FFmpeg blackdetect; only intervals touching the source head/tail are eligible",
    "audio": "FFmpeg silencedetect coverage over the exact proposed removal range",
    "boundary": "keep trim_padding seconds inside each detected black edge",
    "policy": "silent_only blocks audible black; allow_audible is an explicit override",
}


def _finite(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return number


def _normalized_intervals(intervals: Sequence[Mapping[str, Any]], duration: float) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    for interval in intervals:
        start = max(0.0, min(duration, float(interval.get("start") or 0)))
        end = max(start, min(duration, float(interval.get("end") or start)))
        if end - start <= 1e-6:
            continue
        normalized.append(
            {
                "start": round(start, 6),
                "end": round(end, 6),
                "duration": round(end - start, 6),
            }
        )
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    merged: List[Dict[str, float]] = []
    for item in normalized:
        if merged and item["start"] <= merged[-1]["end"] + 1e-6:
            merged[-1]["end"] = round(max(merged[-1]["end"], item["end"]), 6)
            merged[-1]["duration"] = round(merged[-1]["end"] - merged[-1]["start"], 6)
        else:
            merged.append(dict(item))
    return merged


def parse_blackdetect(text: str, *, duration: float) -> List[Dict[str, float]]:
    """Parse FFmpeg blackdetect output, including a black range open at EOF."""
    intervals: List[Dict[str, float]] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        for kind, raw in re.findall(r"black_(start|end)\s*[:=]\s*(-?\d+(?:\.\d+)?)", line):
            value = float(raw)
            if kind == "start":
                pending = value
            elif pending is not None:
                intervals.append({"start": pending, "end": value})
                pending = None
    if pending is not None and duration > pending:
        intervals.append({"start": pending, "end": duration})
    return _normalized_intervals(intervals, duration)


def parse_silencedetect(text: str, *, duration: float) -> List[Dict[str, float]]:
    """Parse FFmpeg silencedetect output, including a silent range open at EOF."""
    intervals: List[Dict[str, float]] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        start_match = re.search(r"silence_start\s*[:=]\s*(-?\d+(?:\.\d+)?)", line)
        if start_match:
            pending = float(start_match.group(1))
        end_match = re.search(r"silence_end\s*[:=]\s*(-?\d+(?:\.\d+)?)", line)
        if end_match and pending is not None:
            intervals.append({"start": pending, "end": float(end_match.group(1))})
            pending = None
    if pending is not None and duration > pending:
        intervals.append({"start": pending, "end": duration})
    return _normalized_intervals(intervals, duration)


def _detect_black(source: Path, settings: Mapping[str, Any]) -> List[Dict[str, float]]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        "-i",
        str(source),
        "-vf",
        (
            f"setpts=PTS-STARTPTS,blackdetect=d={float(settings['black_min_duration']):.6f}:"
            f"pic_th={float(settings['picture_black_ratio']):.6f}:"
            f"pix_th={float(settings['pixel_black_threshold']):.6f}"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = _run_command(command)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "").split())[-3000:]
        raise RuntimeError(f"blackdetect failed{': ' + detail if detail else ''}")
    return parse_blackdetect(result.stderr or "", duration=float(settings["source_duration_seconds"]))


def _detect_silence(source: Path, settings: Mapping[str, Any]) -> List[Dict[str, float]]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        "-i",
        str(source),
        "-af",
        (
            f"asetpts=PTS-STARTPTS,silencedetect=noise={settings['silence_noise']}:"
            f"d={float(settings['silence_min_duration']):.6f}"
        ),
        "-vn",
        "-f",
        "null",
        "-",
    ]
    result = _run_command(command)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "").split())[-3000:]
        raise RuntimeError(f"silencedetect failed{': ' + detail if detail else ''}")
    return parse_silencedetect(result.stderr or "", duration=float(settings["source_duration_seconds"]))


def _coverage(start: float, end: float, intervals: Sequence[Mapping[str, Any]]) -> float:
    if end <= start:
        return 1.0
    covered = 0.0
    for interval in intervals:
        covered += max(0.0, min(end, float(interval["end"])) - max(start, float(interval["start"])))
    return min(1.0, covered / (end - start))


def _proof_windows(
    *, duration: float, trim_start: float, trim_end: float, context: float
) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    if trim_start > 1e-6:
        windows.append(
            {
                "edges": ["leading"],
                "start": 0.0,
                "end": min(duration, trim_start + context),
            }
        )
    if duration - trim_end > 1e-6:
        windows.append(
            {
                "edges": ["trailing"],
                "start": max(0.0, trim_end - context),
                "end": duration,
            }
        )
    windows.sort(key=lambda item: item["start"])
    merged: List[Dict[str, Any]] = []
    for item in windows:
        if merged and float(item["start"]) <= float(merged[-1]["end"]) + 1e-6:
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(item["end"]))
            merged[-1]["edges"] = sorted(set(merged[-1]["edges"] + item["edges"]))
        else:
            merged.append(dict(item))
    for item in merged:
        item["start"] = round(float(item["start"]), 6)
        item["end"] = round(float(item["end"]), 6)
        item["duration"] = round(float(item["end"]) - float(item["start"]), 6)
    return merged


def _settings_for(
    source: Mapping[str, Any],
    *,
    black_min_duration: float,
    picture_black_ratio: float,
    pixel_black_threshold: float,
    edge_tolerance: float,
    trim_padding: float,
    audio_policy: str,
    silence_noise: str,
    silence_min_duration: float,
    silence_coverage: float,
    proof_context: float,
) -> Dict[str, Any]:
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (
        isinstance(bit_depth, int) and bit_depth > 8
    ):
        raise ValueError("use hdr_sdr.py before trimming HDR/BT.2020/greater-than-8-bit material")
    rate = _source_rate(source)
    if audio_policy not in {"silent_only", "allow_audible"}:
        raise ValueError("audio_policy must be silent_only or allow_audible")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?dB", silence_noise, re.IGNORECASE):
        raise ValueError("silence_noise must use an FFmpeg dB value such as -45dB")
    noise_db = float(silence_noise[:-2])
    if not math.isfinite(noise_db) or noise_db < -120 or noise_db > -1:
        raise ValueError("silence_noise must be between -120dB and -1dB")
    silence_noise = f"{noise_db:g}dB"
    black_min_duration = _finite(black_min_duration, "black_min_duration", minimum=0.05, maximum=60)
    picture_black_ratio = _finite(picture_black_ratio, "picture_black_ratio", minimum=0.5, maximum=1)
    pixel_black_threshold = _finite(pixel_black_threshold, "pixel_black_threshold", minimum=0, maximum=0.5)
    edge_tolerance = _finite(edge_tolerance, "edge_tolerance", minimum=0, maximum=2)
    trim_padding = _finite(trim_padding, "trim_padding", minimum=0, maximum=2)
    silence_min_duration = _finite(silence_min_duration, "silence_min_duration", minimum=0.05, maximum=10)
    silence_coverage = _finite(silence_coverage, "silence_coverage", minimum=0.5, maximum=1)
    proof_context = _finite(proof_context, "proof_context", minimum=0.1, maximum=10)
    duration = float(source.get("video_duration") or source.get("duration") or 0)
    if duration <= 0:
        raise ValueError("source has no measurable positive video duration")
    frame_seconds = 1.0 / float(rate["fps"])
    return {
        "detector_algorithm": DETECTOR_ALGORITHM,
        "source_duration_seconds": round(duration, 6),
        "black_min_duration": round(black_min_duration, 6),
        "picture_black_ratio": round(picture_black_ratio, 6),
        "pixel_black_threshold": round(pixel_black_threshold, 6),
        "edge_tolerance": round(edge_tolerance, 6),
        "trim_padding": round(trim_padding, 6),
        "audio_policy": audio_policy,
        "silence_noise": silence_noise,
        "silence_min_duration": round(silence_min_duration, 6),
        "silence_coverage": round(silence_coverage, 6),
        "proof_context": round(proof_context, 6),
        "target_rate": rate,
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "pixel_format": "yuv420p",
        "output_has_audio": bool(source.get("has_audio")),
        "audio_encoder": "aac" if source.get("has_audio") else None,
        "audio_sample_rate": 48000 if source.get("has_audio") else None,
        "audio_bitrate_kbps": 192 if source.get("has_audio") else None,
        "duration_tolerance_seconds": round(max(0.1, 2 * frame_seconds), 6),
        "stream_start_tolerance_seconds": round(max(0.02, frame_seconds), 6),
        "av_end_tolerance_seconds": round(max(0.1, 2 * frame_seconds), 6),
    }


def _analyze_edges(source: Path, source_record: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    duration = float(settings["source_duration_seconds"])
    black = _detect_black(source, settings)
    silence = _detect_silence(source, settings) if source_record.get("has_audio") else []
    tolerance = float(settings["edge_tolerance"])
    leading = next((item for item in black if float(item["start"]) <= tolerance), None)
    trailing = next((item for item in reversed(black) if float(item["end"]) >= duration - tolerance), None)
    padding = float(settings["trim_padding"])
    trim_start = max(0.0, float(leading["end"]) - padding) if leading else 0.0
    trim_end = min(duration, float(trailing["start"]) + padding) if trailing else duration
    if leading is trailing and leading is not None:
        trim_start, trim_end = 0.0, duration
    if trim_end - trim_start < 0.25:
        trim_start, trim_end = 0.0, duration
    leading_removed = trim_start
    trailing_removed = duration - trim_end
    ranges: List[Dict[str, Any]] = []
    if leading_removed > 1e-6:
        ranges.append(
            {
                "edge": "leading",
                "start": 0.0,
                "end": round(trim_start, 6),
                "duration": round(leading_removed, 6),
                "silence_coverage": round(_coverage(0.0, trim_start, silence), 6)
                if source_record.get("has_audio")
                else None,
            }
        )
    if trailing_removed > 1e-6:
        ranges.append(
            {
                "edge": "trailing",
                "start": round(trim_end, 6),
                "end": round(duration, 6),
                "duration": round(trailing_removed, 6),
                "silence_coverage": round(_coverage(trim_end, duration, silence), 6)
                if source_record.get("has_audio")
                else None,
            }
        )
    proof_windows = _proof_windows(
        duration=duration,
        trim_start=trim_start,
        trim_end=trim_end,
        context=float(settings["proof_context"]),
    )
    return {
        "black_intervals": black,
        "silence_intervals": silence,
        "leading_black": leading,
        "trailing_black": trailing,
        "trim_start_seconds": round(trim_start, 6),
        "trim_end_seconds": round(trim_end, 6),
        "output_duration_seconds": round(trim_end - trim_start, 6),
        "leading_removed_seconds": round(leading_removed, 6),
        "trailing_removed_seconds": round(trailing_removed, 6),
        "total_removed_seconds": round(leading_removed + trailing_removed, 6),
        "removal_ranges": ranges,
        "proof_windows": proof_windows,
        "proof_duration_seconds": round(sum(float(item["duration"]) for item in proof_windows), 6),
    }


def _analysis_blockers(plan: Mapping[str, Any]) -> List[str]:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    analysis = plan.get("analysis") or {}
    blockers: List[str] = []
    if float(analysis.get("total_removed_seconds") or 0) < 0.05:
        blockers.append("no trimmable leading or trailing black interval was detected")
    if float(analysis.get("output_duration_seconds") or 0) < 0.25:
        blockers.append("edge trim would leave less than 0.25 seconds of content")
    if source.get("has_audio") and settings.get("audio_policy") == "silent_only":
        required = float(settings.get("silence_coverage") or 0)
        for item in analysis.get("removal_ranges") or []:
            coverage = float(item.get("silence_coverage") or 0)
            if coverage + 1e-9 < required:
                blockers.append(
                    f"{item.get('edge')} black removal is only {coverage:.1%} silent; "
                    f"silent_only requires {required:.1%}"
                )
    return blockers


def _review_contract(has_audio: bool) -> Dict[str, Any]:
    return {
        "playback": "Watch the complete trimmed delivery and every source-edge proof window at normal speed.",
        "checks": list(REVIEW_FIELDS),
        "audio_continuity_expected": has_audio,
        "pass_rule": (
            "full_playback=completed, proof_playback=completed, every visual/content check=pass, "
            "and audio_continuity=pass when audio exists or not_applicable for a silent source"
        ),
        "limitations": [
            "Blackdetect measures pixel darkness and does not know whether a black title, fade, or dramatic pause is intentional.",
            "Silencedetect measures level, not speech meaning; allow_audible can remove dialogue or music and requires explicit review.",
            "Only source edges are eligible; internal black intervals remain untouched.",
        ],
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: plan.get(key)
        for key in (
            "version",
            "project_root",
            "source",
            "settings",
            "analysis",
            "delivery",
            "edge_proof",
            "application",
            "review",
            "review_contract",
            "blockers",
            "warnings",
            "summary",
            "status",
        )
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(_canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_live_file(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.is_symlink() or not candidate.is_file():
        blockers.append(f"{label} is missing or is a symlink")
        return None
    live = _fingerprint(candidate)
    if live.get("sha256") != record.get("sha256") or live.get("size_bytes") != record.get("size_bytes"):
        blockers.append(f"{label} fingerprint changed")
    return candidate.resolve()


def _format_matches_mp4(value: Any) -> bool:
    return bool({str(item).lower() for item in (value or [])}.intersection({"mov", "mp4"}))


def _media_contract_blockers(
    media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any], *, proof: bool
) -> List[str]:
    noun = "edge proof" if proof else "trimmed delivery"
    expected_duration = float(
        (settings.get("proof_duration_seconds") if proof else settings.get("output_duration_seconds")) or 0
    )
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append(f"{noun} is not an MP4-family container")
    if media.get("video_codec") != "h264" or media.get("pixel_format") != "yuv420p":
        blockers.append(f"{noun} must use H.264 yuv420p")
    if media.get("rotation") != 0:
        blockers.append(f"{noun} must bake display rotation")
    if media.get("width") != source.get("width") or media.get("height") != source.get("height"):
        blockers.append(f"{noun} displayed dimensions do not match the source")
    target_fps = float((settings.get("target_rate") or {}).get("fps") or 0)
    if abs(float(media.get("avg_fps") or 0) - target_fps) > 0.001:
        blockers.append(f"{noun} average fps does not match the plan")
    cadence = media.get("cadence") if isinstance(media.get("cadence"), Mapping) else {}
    if cadence.get("algorithm") != CADENCE_ALGORITHM or cadence.get("is_variable") or cadence.get("non_monotonic_intervals"):
        blockers.append(f"{noun} decoded cadence is not constant and monotonic")
    if abs(float(media.get("video_duration") or 0) - expected_duration) > float(settings.get("duration_tolerance_seconds") or 0):
        blockers.append(f"{noun} duration misses the planned range")
    expected_audio = bool(settings.get("output_has_audio"))
    if bool(media.get("has_audio")) != expected_audio:
        blockers.append(f"{noun} audio presence does not match the source")
    if expected_audio:
        if media.get("audio_codec") != "aac" or media.get("sample_rate") != 48000:
            blockers.append(f"{noun} audio must use 48 kHz AAC")
        video_start = float(media.get("video_start_time") or 0)
        audio_start = float(media.get("audio_start_time") or 0)
        if max(abs(video_start), abs(audio_start), abs(video_start - audio_start)) > float(
            settings.get("stream_start_tolerance_seconds") or 0
        ):
            blockers.append(f"{noun} audio/video streams do not start together near zero")
        video_end = video_start + float(media.get("video_duration") or 0)
        audio_end = audio_start + float(media.get("audio_duration") or 0)
        if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
            blockers.append(f"{noun} audio/video ends differ beyond tolerance")
    return blockers


def _review_blockers(plan: Mapping[str, Any]) -> List[str]:
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    settings = plan.get("settings") or {}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    if not review:
        return [PENDING_CONFIRM]
    blockers: List[str] = []
    if not str(review.get("reviewed_by") or "").strip() or not str(review.get("note") or "").strip():
        blockers.append("reviewed_by and a non-empty review note are required")
    if review.get("full_playback") != "completed":
        blockers.append("complete normal-speed trimmed-delivery playback is required")
    if review.get("proof_playback") != "completed":
        blockers.append("complete normal-speed edge-proof playback is required")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    for field in REVIEW_FIELDS:
        expected = "not_applicable" if field == "audio_continuity" and not settings.get("output_has_audio") else "pass"
        if checks.get(field) != expected:
            blockers.append(f"review check {field} must be {expected}, got {checks.get(field) or 'missing'}")
    output = application.get("output") or {}
    proof = application.get("edge_proof") or {}
    if review.get("output_sha256") != output.get("sha256"):
        blockers.append("review is not bound to the current trimmed delivery sha256")
    if review.get("edge_proof_sha256") != proof.get("sha256"):
        blockers.append("review is not bound to the current edge-proof sha256")
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    analysis = plan.get("analysis") or {}
    warnings: List[str] = []
    if source.get("has_audio") and settings.get("audio_policy") == "allow_audible":
        warnings.append("allow_audible bypasses the silence-coverage gate; listen to every removed edge before approval.")
    if (
        len(analysis.get("proof_windows") or []) == 1
        and float(analysis.get("leading_removed_seconds") or 0) > 0
        and float(analysis.get("trailing_removed_seconds") or 0) > 0
    ):
        warnings.append("Source-edge proof windows overlap and were merged into one continuous proof clip.")
    return warnings


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    root = Path(str(plan.get("project_root") or "")).expanduser()
    if not root.is_absolute() or not root.is_dir():
        blockers.append("project_root must be an existing absolute directory")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    live_source: Optional[Mapping[str, Any]] = None
    if source_path is not None:
        if root.is_absolute() and not _inside_project(source_path, root):
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
            black_min_duration=settings.get("black_min_duration"),
            picture_black_ratio=settings.get("picture_black_ratio"),
            pixel_black_threshold=settings.get("pixel_black_threshold"),
            edge_tolerance=settings.get("edge_tolerance"),
            trim_padding=settings.get("trim_padding"),
            audio_policy=str(settings.get("audio_policy") or ""),
            silence_noise=str(settings.get("silence_noise") or ""),
            silence_min_duration=settings.get("silence_min_duration"),
            silence_coverage=settings.get("silence_coverage"),
            proof_context=settings.get("proof_context"),
        )
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
        expected_settings = settings
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical edge-trim contract")
    analysis = plan.get("analysis") if isinstance(plan.get("analysis"), Mapping) else {}
    if source_path is not None and live_source is not None and settings == expected_settings:
        try:
            live_analysis = _analyze_edges(source_path, live_source, settings)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"live edge analysis failed: {exc}")
        else:
            if analysis != live_analysis:
                blockers.append("black/silence analysis changed or was modified after planning")
    blockers.extend(_analysis_blockers(plan))
    paths: Dict[str, Path] = {}
    for key, purpose in (("delivery", "edge_black_trimmed_working_copy"), ("edge_proof", "original_edge_cut_review")):
        record = plan.get(key) if isinstance(plan.get(key), Mapping) else {}
        candidate = Path(str(record.get("path") or "")).expanduser()
        paths[key] = candidate
        if not candidate.is_absolute():
            blockers.append(f"{key}.path must be absolute")
        elif root.is_absolute() and not _inside_project(candidate, root):
            blockers.append(f"{key}.path escaped the project directory")
        elif candidate.suffix.lower() != ".mp4":
            blockers.append(f"{key}.path must use .mp4")
        elif candidate.is_symlink():
            blockers.append(f"{key}.path must not be a symlink")
        if record != {"path": str(candidate), "format": "mp4", "purpose": purpose}:
            blockers.append(f"{key} record is not canonical")
    if paths.get("delivery") == paths.get("edge_proof"):
        blockers.append("delivery and edge_proof paths must differ")
    if source.get("path"):
        for key, candidate in paths.items():
            if candidate.is_absolute() and candidate.resolve() == Path(str(source["path"])).resolve():
                blockers.append(f"{key} must not overwrite the source")
    if plan.get("review_contract") != _review_contract(bool(source.get("has_audio"))):
        blockers.append("review_contract is stale or modified")
    application = plan.get("application")
    applied = isinstance(application, Mapping)
    analysis_with_durations = {
        **settings,
        "output_duration_seconds": analysis.get("output_duration_seconds"),
        "proof_duration_seconds": analysis.get("proof_duration_seconds"),
    }
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        proof = application.get("edge_proof") if isinstance(application.get("edge_proof"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        proof_path = _validate_live_file(proof, "application.edge_proof", blockers)
        if output.get("path") != str(paths.get("delivery") or ""):
            blockers.append("application.output.path does not match delivery.path")
        if proof.get("path") != str(paths.get("edge_proof") or ""):
            blockers.append("application.edge_proof.path does not match edge_proof.path")
        for live_path, stored, proof_mode in ((output_path, output, False), (proof_path, proof, True)):
            if live_path is None:
                continue
            try:
                live = _media_info(live_path)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"output media probe failed: {exc}")
                continue
            if stored != live:
                blockers.append("stored output media contract is stale or modified")
            blockers.extend(_media_contract_blockers(live, source, analysis_with_durations, proof=proof_mode))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        if validation.get("output_decode_checked") is not True or validation.get("output_decode_command") != _decode_command(paths["delivery"]):
            blockers.append("full trimmed-delivery decode validation is missing or stale")
        if validation.get("proof_decode_checked") is not True or validation.get("proof_decode_command") != _decode_command(paths["edge_proof"]):
            blockers.append("full edge-proof decode validation is missing or stale")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("output validation is not bound to the current delivery")
        if validation.get("edge_proof_sha256") != proof.get("sha256"):
            blockers.append("proof validation is not bound to the current edge proof")
        if validation.get("analysis") != {
            "trim_start_seconds": analysis.get("trim_start_seconds"),
            "trim_end_seconds": analysis.get("trim_end_seconds"),
            "proof_windows": analysis.get("proof_windows"),
        }:
            blockers.append("validated edge boundaries are stale or modified")
        blockers.extend(_review_blockers(plan))
    warnings = _computed_warnings(plan)
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    summary = {
        "trim_start_seconds": analysis.get("trim_start_seconds"),
        "trim_end_seconds": analysis.get("trim_end_seconds"),
        "total_removed_seconds": analysis.get("total_removed_seconds"),
        "output_duration_seconds": analysis.get("output_duration_seconds"),
        "audio_policy": settings.get("audio_policy"),
        "applied": applied,
        "confirmed": bool(review) and not _review_blockers(plan),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
        "status": "blocked" if blockers else "warn" if warnings else "ready",
    }


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
    edge_proof_path: str,
    *,
    project_dir: str = ".",
    black_min_duration: float = 0.25,
    picture_black_ratio: float = 0.98,
    pixel_black_threshold: float = 0.10,
    edge_tolerance: float = 0.10,
    trim_padding: float = 0.08,
    audio_policy: str = "silent_only",
    silence_noise: str = "-45dB",
    silence_min_duration: float = 0.15,
    silence_coverage: float = 0.95,
    proof_context: float = 0.75,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _resolve_project_path(source_path, root, label="source", must_exist=True)
    delivery = _resolve_project_path(delivery_path, root, label="delivery", must_exist=False, suffix=".mp4")
    proof = _resolve_project_path(edge_proof_path, root, label="edge proof", must_exist=False, suffix=".mp4")
    if len({source, delivery, proof}) != 3:
        raise ValueError("source, delivery, and edge proof paths must all differ")
    source_record = _media_info(source)
    settings = _settings_for(
        source_record,
        black_min_duration=black_min_duration,
        picture_black_ratio=picture_black_ratio,
        pixel_black_threshold=pixel_black_threshold,
        edge_tolerance=edge_tolerance,
        trim_padding=trim_padding,
        audio_policy=audio_policy,
        silence_noise=silence_noise,
        silence_min_duration=silence_min_duration,
        silence_coverage=silence_coverage,
        proof_context=proof_context,
    )
    analysis = _analyze_edges(source, source_record, settings)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "source": source_record,
        "settings": settings,
        "analysis": analysis,
        "delivery": {"path": str(delivery), "format": "mp4", "purpose": "edge_black_trimmed_working_copy"},
        "edge_proof": {"path": str(proof), "format": "mp4", "purpose": "original_edge_cut_review"},
        "application": None,
        "review": None,
        "review_contract": _review_contract(bool(source_record.get("has_audio"))),
    }
    return _set_derived(plan)


def _color_args(source: Mapping[str, Any]) -> List[str]:
    args: List[str] = []
    for key, option in (
        ("color_primaries", "-color_primaries"),
        ("color_transfer", "-color_trc"),
        ("color_space", "-colorspace"),
        ("color_range", "-color_range"),
    ):
        value = str(source.get(key) or "unknown").lower()
        if value not in {"", "unknown", "unspecified", "reserved"}:
            args.extend([option, value])
    return args


def build_command(plan: Mapping[str, Any], output: Path) -> List[str]:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    analysis = plan.get("analysis") or {}
    start = float(analysis["trim_start_seconds"])
    end = float(analysis["trim_end_seconds"])
    duration = float(analysis["output_duration_seconds"])
    rate = str((settings.get("target_rate") or {}).get("rational") or "")
    graph = [
        f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
        f"fps=fps={rate}:start_time=0:round=near,setsar=1,format=yuv420p[vout]"
    ]
    if settings.get("output_has_audio"):
        graph.append(
            f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
            f"aresample=48000:async=1:first_pts=0,aformat=sample_rates=48000:channel_layouts=stereo,"
            f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[aout]"
        )
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source["path"]),
        "-filter_complex", ";".join(graph), "-map", "[vout]",
    ]
    if settings.get("output_has_audio"):
        command.extend(["-map", "[aout]"])
    command.extend(
        [
            "-c:v", "libx264", "-preset", str(settings["video_preset"]), "-crf", str(settings["video_crf"]),
            "-pix_fmt", "yuv420p", "-r", rate, "-fps_mode", "cfr", *_color_args(source),
            "-metadata:s:v:0", "rotate=0",
        ]
    )
    if settings.get("output_has_audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(output)])
    return command


def _proof_command(plan: Mapping[str, Any], output: Path) -> List[str]:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    windows = (plan.get("analysis") or {}).get("proof_windows") or []
    rate = str((settings.get("target_rate") or {}).get("rational") or "")
    graph: List[str] = []
    labels: List[str] = []
    for index, window in enumerate(windows):
        start = float(window["start"])
        end = float(window["end"])
        duration = float(window["duration"])
        graph.append(
            f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"fps=fps={rate}:start_time=0:round=near,setsar=1,format=yuv420p[v{index}]"
        )
        labels.append(f"[v{index}]")
        if settings.get("output_has_audio"):
            graph.append(
                f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                f"aresample=48000:async=1:first_pts=0,aformat=sample_rates=48000:channel_layouts=stereo,"
                f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[a{index}]"
            )
            labels.append(f"[a{index}]")
    if len(windows) > 1:
        graph.append(
            "".join(labels)
            + f"concat=n={len(windows)}:v=1:a={1 if settings.get('output_has_audio') else 0}"
            + ("[vout][aout]" if settings.get("output_has_audio") else "[vout]")
        )
        video_map, audio_map = "[vout]", "[aout]"
    else:
        video_map, audio_map = "[v0]", "[a0]"
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source["path"]),
        "-filter_complex", ";".join(graph), "-map", video_map,
    ]
    if settings.get("output_has_audio"):
        command.extend(["-map", audio_map])
    command.extend(
        [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-r", rate, "-fps_mode", "cfr", *_color_args(source), "-metadata:s:v:0", "rotate=0",
        ]
    )
    if settings.get("output_has_audio"):
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(output)])
    return command


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read edge-trim plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("edge-trim plan must be a JSON object")
    return payload


def _resolve_plan_file(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError("edge-trim plan must not be a symlink")
    path = candidate.resolve()
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError(f"edge-trim plan must be an existing JSON file: {path}")
    return path


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    verification = verify_plan(plan)
    substantive = [item for item in verification.get("blockers") or [] if item != PENDING_APPLY]
    if substantive:
        raise ValueError("plan is not safe to apply: " + "; ".join(substantive))
    root = Path(str(plan.get("project_root") or ""))
    if not _inside_project(path, root):
        raise ValueError("edge-trim plan must stay inside the project directory")
    source = Path(str(plan["source"]["path"]))
    delivery = Path(str(plan["delivery"]["path"]))
    proof = Path(str(plan["edge_proof"]["path"]))
    for label, target in (("delivery", delivery), ("edge proof", proof)):
        if target.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
        if target.exists() and not force:
            raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    source_before = _fingerprint(source)
    temporary_delivery = _temporary_output(delivery)
    temporary_proof = _temporary_output(proof)
    contract = {
        **plan["settings"],
        "output_duration_seconds": plan["analysis"]["output_duration_seconds"],
        "proof_duration_seconds": plan["analysis"]["proof_duration_seconds"],
    }
    try:
        _run_checked(build_command(plan, temporary_delivery), "edge-black trim render")
        output_info = _media_info(temporary_delivery)
        output_blockers = _media_contract_blockers(output_info, plan["source"], contract, proof=False)
        if output_blockers:
            raise RuntimeError("output validation failed: " + "; ".join(output_blockers))
        _run_checked(_decode_command(temporary_delivery), "full trimmed-delivery decode")
        _run_checked(_proof_command(plan, temporary_proof), "source-edge proof render")
        proof_info = _media_info(temporary_proof)
        proof_blockers = _media_contract_blockers(proof_info, plan["source"], contract, proof=True)
        if proof_blockers:
            raise RuntimeError("edge-proof validation failed: " + "; ".join(proof_blockers))
        _run_checked(_decode_command(temporary_proof), "full edge-proof decode")
        if _fingerprint(source) != source_before:
            raise RuntimeError("source changed during edge trimming; outputs were not promoted")
        delivery.parent.mkdir(parents=True, exist_ok=True)
        proof.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary_delivery, delivery)
        os.replace(temporary_proof, proof)
    finally:
        for temporary in (temporary_delivery, temporary_proof):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output_info = _media_info(delivery)
    proof_info = _media_info(proof)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output_info,
        "edge_proof": proof_info,
        "validation": {
            "validated_at": utc_now(),
            "output_decode_checked": True,
            "output_decode_command": _decode_command(delivery),
            "proof_decode_checked": True,
            "proof_decode_command": _decode_command(proof),
            "output_sha256": output_info["sha256"],
            "edge_proof_sha256": proof_info["sha256"],
            "analysis": {
                "trim_start_seconds": plan["analysis"]["trim_start_seconds"],
                "trim_end_seconds": plan["analysis"]["trim_end_seconds"],
                "proof_windows": plan["analysis"]["proof_windows"],
            },
        },
    }
    plan["review"] = None
    _set_derived(plan)
    final = verify_plan(plan)
    substantive = [item for item in final.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise RuntimeError("applied edge-trim plan failed final verification: " + "; ".join(substantive))
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    proof_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_plan(path)
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the edge-trim plan before confirming it")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty review note are required")
    reviewable = dict(plan)
    reviewable["review"] = None
    _set_derived(reviewable)
    current = verify_plan(reviewable)
    substantive = [item for item in current.get("blockers") or [] if item != PENDING_CONFIRM]
    if substantive:
        raise ValueError("plan is not safe to confirm: " + "; ".join(substantive))
    if full_playback not in {"completed", "not_completed"} or proof_playback not in {"completed", "not_completed"}:
        raise ValueError("playback values must be completed or not_completed")
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    application = plan["application"]
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "full_playback": full_playback,
        "proof_playback": proof_playback,
        "checks": normalized,
        "output_sha256": application["output"]["sha256"],
        "edge_proof_sha256": application["edge_proof"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    analysis = plan.get("analysis") or {}
    lines = [
        "# Edge-black Trim Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Source: `{(plan.get('source') or {}).get('path', '')}`",
        f"- Trim range: `{analysis.get('trim_start_seconds')}` → `{analysis.get('trim_end_seconds')}` seconds",
        f"- Removed: `{analysis.get('total_removed_seconds')}` seconds",
        f"- Audio policy: `{(plan.get('settings') or {}).get('audio_policy')}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path', '')}`",
        f"- Edge proof: `{(plan.get('edge_proof') or {}).get('path', '')}`",
        "",
        "## Removal ranges",
        "",
    ]
    for item in analysis.get("removal_ranges") or []:
        coverage = item.get("silence_coverage")
        suffix = "no audio" if coverage is None else f"silence coverage {float(coverage):.1%}"
        lines.append(f"- {item.get('edge')}: {item.get('start')}–{item.get('end')}s ({suffix})")
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(
        [
            "",
            "## Review contract",
            "",
            "Play the proof and complete delivery at 1×. Confirm the first and last visible frames are intentional, no title/fade/content was lost, and audio starts/ends naturally.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_optional_markdown(path: Optional[str], report: Mapping[str, Any], *, forbidden: Sequence[Path]) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if target in {item.expanduser().resolve() for item in forbidden}:
        raise ValueError("markdown output must not overlap a source, plan, delivery, or proof file")
    if target.is_symlink() or not _inside_project(target, Path(str(report["project_root"]))):
        raise ValueError("markdown output must stay inside the project and must not be a symlink")
    _atomic_write_text(target, render_markdown(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trim only leading/trailing black ranges, guard audible edges, and require source-bound review."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Detect source-edge black/silence and write a blocked plan.")
    plan.add_argument("source", help="Project-local progressive CFR SDR source video.")
    plan.add_argument("--delivery", required=True, help="Project-local trimmed MP4 working copy.")
    plan.add_argument("--edge-proof", required=True, help="Project-local normal-speed source-edge proof MP4.")
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--black-min-duration", type=float, default=0.25)
    plan.add_argument("--picture-black-ratio", type=float, default=0.98)
    plan.add_argument("--pixel-black-threshold", type=float, default=0.10)
    plan.add_argument("--edge-tolerance", type=float, default=0.10)
    plan.add_argument("--trim-padding", type=float, default=0.08)
    plan.add_argument("--audio-policy", choices=("silent_only", "allow_audible"), default="silent_only")
    plan.add_argument("--silence-noise", default="-45dB")
    plan.add_argument("--silence-min-duration", type=float, default=0.15)
    plan.add_argument("--silence-coverage", type=float, default=0.95)
    plan.add_argument("--proof-context", type=float, default=0.75)
    plan.add_argument("--output", required=True, help="Plan JSON path.")
    plan.add_argument("--markdown")
    plan.add_argument("--force", action="store_true")
    apply = subparsers.add_parser("apply", help="Render, fully decode, and atomically promote delivery/proof.")
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")
    confirm = subparsers.add_parser(
        "confirm",
        help="Record normal-speed full-delivery and source-edge proof review.",
        description="Record normal-speed full-delivery and source-edge proof review.",
    )
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    confirm.add_argument("--proof-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True)
    confirm.add_argument("--markdown")
    verify = subparsers.add_parser("verify", help="Re-run source detectors and reject plan/output/review drift.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            output = Path(args.output).expanduser().resolve()
            if output.is_symlink() or (output.exists() and not args.force):
                raise FileExistsError(f"plan output exists or is a symlink; pass --force to replace: {output}")
            report = build_plan(
                args.source,
                args.delivery,
                args.edge_proof,
                project_dir=args.project_dir,
                black_min_duration=args.black_min_duration,
                picture_black_ratio=args.picture_black_ratio,
                pixel_black_threshold=args.pixel_black_threshold,
                edge_tolerance=args.edge_tolerance,
                trim_padding=args.trim_padding,
                audio_policy=args.audio_policy,
                silence_noise=args.silence_noise,
                silence_min_duration=args.silence_min_duration,
                silence_coverage=args.silence_coverage,
                proof_context=args.proof_context,
            )
            forbidden = [Path(report["source"]["path"]), Path(report["delivery"]["path"]), Path(report["edge_proof"]["path"])]
            if output in {item.resolve() for item in forbidden} or not _inside_project(output, Path(report["project_root"])):
                raise ValueError("plan output must stay inside the project and differ from media outputs")
            _atomic_write_json(output, report)
            _write_optional_markdown(args.markdown, report, forbidden=[output, *forbidden])
        elif args.command == "apply":
            report = apply_plan(args.plan, force=args.force)
            forbidden = [Path(args.plan), Path(report["source"]["path"]), Path(report["delivery"]["path"]), Path(report["edge_proof"]["path"])]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        elif args.command == "confirm":
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                proof_playback=args.proof_playback,
                checks={field: getattr(args, field) for field in REVIEW_FIELDS},
            )
            forbidden = [Path(args.plan), Path(report["source"]["path"]), Path(report["delivery"]["path"]), Path(report["edge_proof"]["path"])]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        else:
            plan_path = _resolve_plan_file(args.plan)
            report = verify_plan(_load_plan(plan_path))
            forbidden = [plan_path, Path(str((report.get("source") or {}).get("path") or "")), Path(str((report.get("delivery") or {}).get("path") or "")), Path(str((report.get("edge_proof") or {}).get("path") or ""))]
            _write_optional_markdown(args.markdown, report, forbidden=forbidden)
        print(json.dumps({"status": report["status"], "plan_id": report.get("plan_id"), "summary": report["summary"]}, ensure_ascii=False))
        return 2 if getattr(args, "strict", False) and report["summary"]["blocking"] else 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"black_edge_trim.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
