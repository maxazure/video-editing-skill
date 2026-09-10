#!/usr/bin/env python3
"""Detect interlace/telecine risk, create a progressive working copy, and verify it.

The workflow is intentionally conservative. It samples the exact source with
FFmpeg idet, refuses likely telecine and progressive material, binds an explicit
human decision, deinterlaces true interlaced footage with bwdif (yadif fallback),
fully decodes the result, produces a full-length A/B review file, and requires a
recorded normal-speed review before the plan becomes ready. Source media is never
modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "interlace_conform.v1"
ANALYSIS_VERSION = "interlace_analysis.v1"
DETECTION_ALGORITHM = "ffmpeg_idet_multisample.v1"
PENDING_APPLY = "interlace conform has not been applied and validated"
PENDING_CONFIRM = "full-length interlace A/B review has not been confirmed"
MP4_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
CLASSIFICATIONS = {
    "progressive",
    "interlaced_tff",
    "interlaced_bff",
    "telecine_candidate",
    "mixed_or_uncertain",
}
OVERRIDES = {"interlaced_tff", "interlaced_bff", "telecine_candidate", "progressive"}
MODES = {"frame": "send_frame", "field": "send_field"}
PARITIES = {"auto", "tff", "bff"}
REVIEW_CHOICES = {"pass", "fail", "unobservable"}
REVIEW_FIELDS = (
    "residual_combing",
    "motion_smoothness",
    "line_detail",
    "field_order",
    "audio_sync",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run_command(command)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 3000:
        detail = detail[-3000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _fraction_float(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            left, right = value.split("/", 1)
            parsed = float(left) / float(right)
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


def _bit_depth(video: Mapping[str, Any]) -> Optional[int]:
    try:
        explicit = int(video.get("bits_per_raw_sample") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    pixel_format = str(video.get("pix_fmt") or "").lower()
    for token in ("p16", "p14", "p12", "p10", "p9"):
        if token in pixel_format:
            return int(token[1:])
    return 8 if pixel_format else None


def _stream_duration(stream: Mapping[str, Any], fallback: Optional[float]) -> Optional[float]:
    direct = _fraction_float(stream.get("duration"))
    if direct is not None:
        return direct
    duration_ts = _fraction_float(stream.get("duration_ts"))
    time_base = _fraction_float(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return duration_ts * time_base
    return fallback


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
    video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"video stream not found: {path}")
    audio = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), None)
    format_duration = _fraction_float((data.get("format") or {}).get("duration"))
    video_duration = _stream_duration(video, format_duration)
    audio_duration = _stream_duration(audio or {}, format_duration) if audio else None
    duration = video_duration or format_duration
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if duration is None or duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {path}")
    video_start = _fraction_float(video.get("start_time")) or 0.0
    audio_start = (_fraction_float((audio or {}).get("start_time")) or 0.0) if audio else None
    avg_rate = str(video.get("avg_frame_rate") or "0/0")
    nominal_rate = str(video.get("r_frame_rate") or "0/0")
    return {
        "duration": round(duration, 6),
        "video_duration": round(video_duration or duration, 6),
        "audio_duration": round(audio_duration, 6) if audio_duration is not None else None,
        "avg_frame_rate": avg_rate,
        "r_frame_rate": nominal_rate,
        "avg_fps": round(_fraction_float(avg_rate) or 0.0, 9),
        "nominal_fps": round(_fraction_float(nominal_rate) or 0.0, 9),
        "field_order": str(video.get("field_order") or "unknown").lower(),
        "width": width,
        "height": height,
        "rotation": rotation,
        "video_start_time": round(video_start, 6),
        "audio_start_time": round(audio_start, 6) if audio_start is not None else None,
        "has_audio": audio is not None,
        "video_codec": str(video.get("codec_name") or "").lower(),
        "audio_codec": str((audio or {}).get("codec_name") or "").lower() or None,
        "sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
        "channels": int((audio or {}).get("channels") or 0) or None,
        "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
        "bit_depth": _bit_depth(video),
        "sample_aspect_ratio": str(video.get("sample_aspect_ratio") or "unknown"),
        "color_primaries": str(video.get("color_primaries") or "unknown").lower(),
        "color_transfer": str(video.get("color_transfer") or "unknown").lower(),
        "color_space": str(video.get("color_space") or "unknown").lower(),
        "color_range": str(video.get("color_range") or "unknown").lower(),
        "format_names": sorted(
            token.strip().lower()
            for token in str((data.get("format") or {}).get("format_name") or "").split(",")
            if token.strip()
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


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


def _inside_project(path: Path, project_root: Path) -> bool:
    try:
        path.resolve().relative_to(project_root.resolve())
        return True
    except ValueError:
        return False


def _resolve_project_path(
    value: str,
    project_root: Path,
    *,
    label: str,
    must_exist: bool,
    suffix: Optional[str] = None,
) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    if not _inside_project(resolved, project_root):
        raise ValueError(f"{label} must stay inside the project directory")
    if must_exist and not resolved.is_file():
        raise ValueError(f"{label} does not exist: {resolved}")
    if suffix and resolved.suffix.lower() != suffix:
        raise ValueError(f"{label} must use {suffix}")
    return resolved


def _sample_windows(duration: float, sample_seconds: float) -> List[Dict[str, float]]:
    if sample_seconds <= 0 or sample_seconds > 60:
        raise ValueError("sample_seconds must be greater than 0 and at most 60")
    span = min(float(sample_seconds), float(duration))
    if duration <= span * 1.25:
        starts = [0.0]
    else:
        starts = [0.0, max(0.0, (duration - span) / 2.0), max(0.0, duration - span)]
    windows: List[Dict[str, float]] = []
    for start in starts:
        item = {"start": round(start, 6), "duration": round(min(span, duration - start), 6)}
        if item not in windows:
            windows.append(item)
    return windows


def _parse_idet_summary(text: str) -> Dict[str, Dict[str, int]]:
    patterns = {
        "repeated": r"Repeated Fields:\s*Neither:\s*(\d+)\s+Top:\s*(\d+)\s+Bottom:\s*(\d+)",
        "single": r"Single frame detection:\s*TFF:\s*(\d+)\s+BFF:\s*(\d+)\s+Progressive:\s*(\d+)\s+Undetermined:\s*(\d+)",
        "multiple": r"Multi frame detection:\s*TFF:\s*(\d+)\s+BFF:\s*(\d+)\s+Progressive:\s*(\d+)\s+Undetermined:\s*(\d+)",
    }
    parsed: Dict[str, Dict[str, int]] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if not matches:
            raise RuntimeError(f"FFmpeg idet output did not contain the {name} summary")
        values = [int(item) for item in matches[-1]]
        if name == "repeated":
            parsed[name] = dict(zip(("neither", "top", "bottom"), values))
        else:
            parsed[name] = dict(zip(("tff", "bff", "progressive", "undetermined"), values))
    return parsed


def _classify_idet(counts: Mapping[str, Mapping[str, int]], media: Mapping[str, Any]) -> Dict[str, Any]:
    repeated = counts.get("repeated") or {}
    multiple = counts.get("multiple") or {}
    repeated_total = sum(int(value) for value in repeated.values())
    multi_total = sum(int(value) for value in multiple.values())
    repeated_ratio = (
        (int(repeated.get("top") or 0) + int(repeated.get("bottom") or 0)) / repeated_total
        if repeated_total
        else 0.0
    )
    tff = int(multiple.get("tff") or 0)
    bff = int(multiple.get("bff") or 0)
    interlaced = tff + bff
    interlaced_ratio = interlaced / multi_total if multi_total else 0.0
    progressive_ratio = int(multiple.get("progressive") or 0) / multi_total if multi_total else 0.0
    parity_dominance = max(tff, bff) / interlaced if interlaced else 0.0
    fps = float(media.get("avg_fps") or media.get("nominal_fps") or 0)
    field_order = str(media.get("field_order") or "unknown").lower()
    near_ntsc_30 = 29.5 <= fps <= 30.5

    reasons: List[str] = []
    if multi_total < 10:
        classification = "mixed_or_uncertain"
        confidence = 0.0
        reasons.append("fewer than 10 frames were classifiable")
    elif near_ntsc_30 and repeated_ratio >= 0.10 and interlaced_ratio >= 0.40:
        classification = "telecine_candidate"
        confidence = min(1.0, 0.5 + repeated_ratio)
        reasons.append("NTSC-rate material contains a material repeated-field pattern")
        reasons.append("inverse telecine should be evaluated before any deinterlacer")
    elif interlaced_ratio >= 0.60 and parity_dominance >= 0.75:
        classification = "interlaced_tff" if tff >= bff else "interlaced_bff"
        confidence = interlaced_ratio * parity_dominance
        reasons.append("idet reports a dominant field order across most sampled frames")
    elif progressive_ratio >= 0.80 or (
        field_order == "progressive" and repeated_ratio < 0.05 and parity_dominance < 0.75
    ):
        classification = "progressive"
        confidence = max(progressive_ratio, 0.80 if field_order == "progressive" else 0.0)
        reasons.append("stream flags and sampled field evidence are consistent with progressive video")
    else:
        classification = "mixed_or_uncertain"
        confidence = max(progressive_ratio, interlaced_ratio * parity_dominance)
        reasons.append("field evidence is mixed, low-confidence, or lacks a stable cadence")

    return {
        "classification": classification,
        "confidence": round(confidence, 6),
        "ratios": {
            "repeated_fields": round(repeated_ratio, 6),
            "interlaced_frames": round(interlaced_ratio, 6),
            "progressive_frames": round(progressive_ratio, 6),
            "parity_dominance": round(parity_dominance, 6),
        },
        "reasons": reasons,
    }


def analyze_interlace(path: Path, media: Mapping[str, Any], *, sample_seconds: float = 8.0) -> Dict[str, Any]:
    windows = _sample_windows(float(media.get("duration") or 0), sample_seconds)
    samples: List[Dict[str, Any]] = []
    aggregate = {
        "repeated": {"neither": 0, "top": 0, "bottom": 0},
        "single": {"tff": 0, "bff": 0, "progressive": 0, "undetermined": 0},
        "multiple": {"tff": 0, "bff": 0, "progressive": 0, "undetermined": 0},
    }
    for window in windows:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{window['start']:.6f}",
            "-i",
            str(path),
            "-t",
            f"{window['duration']:.6f}",
            "-map",
            "0:v:0",
            "-vf",
            "idet",
            "-an",
            "-f",
            "null",
            "-",
        ]
        result = _run_command(command)
        if result.returncode != 0:
            detail = " ".join((result.stderr or result.stdout or "").split())
            raise RuntimeError(f"FFmpeg idet analysis failed{': ' + detail[-2000:] if detail else ''}")
        counts = _parse_idet_summary("\n".join((result.stdout, result.stderr)))
        samples.append({**window, "counts": counts})
        for group, values in counts.items():
            for key, value in values.items():
                aggregate[group][key] += value
    classification = _classify_idet(aggregate, media)
    return {
        "algorithm": DETECTION_ALGORITHM,
        "sample_seconds": float(sample_seconds),
        "windows": samples,
        "counts": aggregate,
        **classification,
        "limitations": [
            "idet is a heuristic sample, not a proof of source capture history or telecine cadence.",
            "Mixed edits, animation, static frames, bad stream flags, or cadence breaks can require frame-by-frame review.",
            "A telecine candidate must use an IVTC-specific workflow; this script deliberately does not deinterlace it.",
        ],
    }


def _source_info(path: Path, *, sample_seconds: float) -> Dict[str, Any]:
    media = probe_media(path)
    return {**_fingerprint(path), **media, "interlace_analysis": analyze_interlace(path, media, sample_seconds=sample_seconds)}


def _output_info(path: Path, *, sample_seconds: float) -> Dict[str, Any]:
    media = probe_media(path)
    return {**_fingerprint(path), **media, "interlace_analysis": analyze_interlace(path, media, sample_seconds=sample_seconds)}


def build_analysis(source_path: str, *, project_dir: str = ".", sample_seconds: float = 8.0) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _resolve_project_path(source_path, root, label="source", must_exist=True)
    record = _source_info(source, sample_seconds=sample_seconds)
    analysis = record["interlace_analysis"]
    return {
        "version": ANALYSIS_VERSION,
        "generated_at": utc_now(),
        "project_root": str(root),
        "source": record,
        "recommendation": _recommendation(str(analysis.get("classification") or "")),
    }


def _recommendation(classification: str) -> str:
    if classification == "telecine_candidate":
        return "Stop and evaluate inverse telecine; do not run a deinterlacer on this source."
    if classification in {"interlaced_tff", "interlaced_bff"}:
        return "Review representative motion, then create an explicit deinterlace plan with the detected parity."
    if classification == "progressive":
        return "Keep the original progressive source; a deinterlace pass would add a lossy re-encode."
    return "Inspect representative motion frame by frame and record a classification override only with evidence."


def _available_filters() -> set[str]:
    result = _run_command(["ffmpeg", "-hide_banner", "-filters"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not list FFmpeg filters")
    names = set()
    for line in "\n".join((result.stdout, result.stderr)).splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[.A-Z|]{2,8}", parts[0]) and re.fullmatch(r"[A-Za-z0-9_]+", parts[1]):
            names.add(parts[1])
    if not names:
        raise RuntimeError("could not parse FFmpeg filter listing")
    return names


def _ffmpeg_version() -> str:
    result = _run_command(["ffmpeg", "-version"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg -version failed")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("ffmpeg -version returned no version line")
    return lines[0]


def _rate_record(value: str, multiplier: int) -> Dict[str, Any]:
    try:
        rate = Fraction(value) * multiplier
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid source frame rate: {value}") from exc
    if rate <= 0 or float(rate) > 240:
        raise ValueError("output frame rate must be greater than 0 and at most 240 fps")
    return {
        "numerator": rate.numerator,
        "denominator": rate.denominator,
        "rational": f"{rate.numerator}/{rate.denominator}",
        "fps": round(float(rate), 9),
    }


def _settings_for(source: Mapping[str, Any], *, mode: str, parity: str) -> Dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    if parity not in PARITIES:
        raise ValueError(f"parity must be one of {sorted(PARITIES)}")
    transfer = str(source.get("color_transfer") or "unknown").lower()
    primaries = str(source.get("color_primaries") or "unknown").lower()
    bit_depth = source.get("bit_depth")
    if transfer in HDR_TRANSFERS or primaries == "bt2020" or (isinstance(bit_depth, int) and bit_depth > 8):
        raise ValueError(
            "HDR/BT.2020/greater-than-8-bit source requires an explicit color workflow before this H.264 SDR working copy"
        )
    filters = _available_filters()
    if "idet" not in filters:
        raise ValueError("FFmpeg idet filter is required for interlace analysis")
    backend = "bwdif" if "bwdif" in filters else "yadif" if "yadif" in filters else ""
    if not backend:
        raise ValueError("FFmpeg must provide bwdif or yadif for deinterlacing")
    output_rate = _rate_record(str(source.get("avg_frame_rate") or "0/0"), 2 if mode == "field" else 1)
    resolved_parity = parity
    classification = str((source.get("interlace_analysis") or {}).get("effective_classification") or (source.get("interlace_analysis") or {}).get("classification") or "")
    if parity == "auto" and classification in {"interlaced_tff", "interlaced_bff"}:
        resolved_parity = "tff" if classification.endswith("tff") else "bff"
    video_filter = f"{backend}=mode={MODES[mode]}:parity={resolved_parity}:deint=all,setfield=prog,setsar=1,format=yuv420p"
    frame_seconds = 1.0 / float(output_rate["fps"])
    return {
        "detection_algorithm": DETECTION_ALGORITHM,
        "ffmpeg_version": _ffmpeg_version(),
        "backend": backend,
        "mode": mode,
        "requested_parity": parity,
        "resolved_parity": resolved_parity,
        "output_rate": output_rate,
        "video_filter": video_filter,
        "container": "mp4",
        "video_codec": "h264",
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "pixel_format": "yuv420p",
        "audio_codec": "aac" if source.get("has_audio") else None,
        "audio_bitrate_kbps": 192 if source.get("has_audio") else None,
        "audio_sample_rate": 48000 if source.get("has_audio") else None,
        "duration_tolerance_seconds": round(max(0.12, frame_seconds * 2), 6),
        "stream_start_tolerance_seconds": round(max(0.03, frame_seconds), 6),
        "av_end_tolerance_seconds": round(max(0.12, frame_seconds * 2), 6),
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_root": plan.get("project_root"),
        "source": plan.get("source"),
        "decision": plan.get("decision"),
        "settings": plan.get("settings"),
        "delivery": plan.get("delivery"),
        "comparison": plan.get("comparison"),
        "application": plan.get("application"),
        "review": plan.get("review"),
        "review_contract": plan.get("review_contract"),
        "blockers": plan.get("blockers"),
        "warnings": plan.get("warnings"),
        "summary": plan.get("summary"),
        "status": plan.get("status"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(_canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_live_file(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
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


def _output_contract_blockers(media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if not set(str(item).lower() for item in media.get("format_names") or []).intersection(MP4_FORMATS):
        blockers.append("deinterlaced working copy is not an MP4-family container")
    if media.get("video_codec") != "h264":
        blockers.append("deinterlaced working copy video codec must be H.264")
    if media.get("pixel_format") != "yuv420p":
        blockers.append("deinterlaced working copy pixel format must be yuv420p")
    if media.get("field_order") != "progressive":
        blockers.append("deinterlaced working copy is not explicitly flagged progressive")
    if media.get("rotation") != 0:
        blockers.append("deinterlaced working copy must bake display rotation and clear rotation metadata")
    if media.get("width") != source.get("width") or media.get("height") != source.get("height"):
        blockers.append("deinterlaced working copy displayed dimensions do not match the source")
    target = float((settings.get("output_rate") or {}).get("fps") or 0)
    if abs(float(media.get("avg_fps") or 0) - target) > 0.001:
        blockers.append("deinterlaced working copy average fps does not match the planned rate")
    if abs(float(media.get("video_duration") or 0) - float(source.get("video_duration") or 0)) > float(settings.get("duration_tolerance_seconds") or 0):
        blockers.append("deinterlaced working copy duration drift exceeds the planned tolerance")
    if media.get("has_audio") != source.get("has_audio"):
        blockers.append("deinterlaced working copy audio presence does not match the source")
    if source.get("has_audio"):
        if media.get("audio_codec") != "aac":
            blockers.append("deinterlaced working copy audio codec must be AAC")
        if media.get("sample_rate") != settings.get("audio_sample_rate"):
            blockers.append("deinterlaced working copy audio sample rate does not match the plan")
        video_start = float(media.get("video_start_time") or 0)
        audio_start = float(media.get("audio_start_time") or 0)
        tolerance = float(settings.get("stream_start_tolerance_seconds") or 0)
        if abs(video_start) > tolerance or abs(audio_start) > tolerance or abs(video_start - audio_start) > tolerance:
            blockers.append("deinterlaced working copy audio/video streams do not start together near zero")
        video_end = video_start + float(media.get("video_duration") or 0)
        audio_end = audio_start + float(media.get("audio_duration") or 0)
        if abs(video_end - audio_end) > float(settings.get("av_end_tolerance_seconds") or 0):
            blockers.append("deinterlaced working copy audio/video end times exceed the planned tolerance")
    output_analysis = media.get("interlace_analysis") if isinstance(media.get("interlace_analysis"), Mapping) else {}
    if output_analysis.get("classification") != "progressive":
        blockers.append("deinterlaced working copy is not classified as progressive by the live idet check")
    return blockers


def _decode_command(path: Path) -> List[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0", "-f", "null", "-"]


def _review_blockers(review: Any) -> List[str]:
    if not isinstance(review, Mapping):
        return [PENDING_CONFIRM]
    blockers: List[str] = []
    if review.get("full_playback") != "completed":
        blockers.append("full-length A/B playback was not completed at normal speed")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    for field in REVIEW_FIELDS:
        if checks.get(field) != "pass":
            blockers.append(f"review check {field} must be pass")
    if not str(review.get("reviewed_by") or "").strip():
        blockers.append("reviewed_by is required")
    if not str(review.get("note") or "").strip():
        blockers.append("review note is required")
    return blockers


def _computed_warnings(plan: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    analysis = source.get("interlace_analysis") if isinstance(source.get("interlace_analysis"), Mapping) else {}
    if analysis.get("classification_override"):
        warnings.append("A human classification override replaced the sampled idet classification; retain the review evidence.")
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    if settings.get("backend") == "yadif":
        warnings.append("bwdif is unavailable; the plan uses the declared yadif fallback.")
    if settings.get("mode") == "field":
        warnings.append("Field mode doubles the frame rate to preserve field-time motion; confirm cadence and delivery compatibility.")
    return warnings


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
    project_root = Path(str(plan.get("project_root") or "")).expanduser()
    if not project_root.is_absolute() or not project_root.is_dir():
        blockers.append("project_root must be an existing absolute directory")
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    sample_seconds = float((source.get("interlace_analysis") or {}).get("sample_seconds") or 8.0)
    if source_path is not None:
        if project_root.is_absolute() and not _inside_project(source_path, project_root):
            blockers.append("source escaped the project directory")
        try:
            live_source = _source_info(source_path, sample_seconds=sample_seconds)
            override = (source.get("interlace_analysis") or {}).get("classification_override")
            if override:
                live_source["interlace_analysis"]["classification_override"] = override
                live_source["interlace_analysis"]["effective_classification"] = override
            else:
                live_source["interlace_analysis"]["effective_classification"] = live_source["interlace_analysis"]["classification"]
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source interlace/media probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source fingerprint, media contract, or interlace analysis changed after planning")

    decision = plan.get("decision") if isinstance(plan.get("decision"), Mapping) else {}
    if decision.get("action") != "deinterlace":
        blockers.append("decision.action must be deinterlace")
    if not str(decision.get("reviewed_by") or "").strip() or not str(decision.get("note") or "").strip():
        blockers.append("deinterlace decision requires reviewed_by and a non-empty note")
    effective = str((source.get("interlace_analysis") or {}).get("effective_classification") or "")
    if effective not in {"interlaced_tff", "interlaced_bff"}:
        blockers.append("effective source classification must be true interlaced before deinterlacing")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        expected_settings = _settings_for(
            source,
            mode=str(settings.get("mode") or ""),
            parity=str(settings.get("requested_parity") or ""),
        )
    except (RuntimeError, ValueError) as exc:
        blockers.append(str(exc))
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical interlace conform contract")

    paths: Dict[str, Path] = {}
    for key in ("delivery", "comparison"):
        record = plan.get(key) if isinstance(plan.get(key), Mapping) else {}
        candidate = Path(str(record.get("path") or "")).expanduser()
        paths[key] = candidate
        if not candidate.is_absolute():
            blockers.append(f"{key}.path must be absolute")
        elif project_root.is_absolute() and not _inside_project(candidate, project_root):
            blockers.append(f"{key}.path escaped the project directory")
        elif candidate.suffix.lower() != ".mp4":
            blockers.append(f"{key}.path must use .mp4")
        elif candidate.is_symlink():
            blockers.append(f"{key}.path must not be a symlink")
    if paths.get("delivery") == paths.get("comparison"):
        blockers.append("delivery and comparison paths must be different")
    if source.get("path"):
        for key, candidate in paths.items():
            if candidate.is_absolute() and candidate.resolve() == Path(str(source.get("path"))).resolve():
                blockers.append(f"{key} must not overwrite the source")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        if output.get("path") != str(paths.get("delivery") or ""):
            blockers.append("application.output.path does not match delivery.path")
        if output_path is not None:
            try:
                live_output = _output_info(output_path, sample_seconds=sample_seconds)
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"deinterlaced working-copy probe failed: {exc}")
            else:
                if output != live_output:
                    blockers.append("stored working-copy contract is stale or was modified")
                blockers.extend(_output_contract_blockers(live_output, source, settings))
        comparison = application.get("comparison") if isinstance(application.get("comparison"), Mapping) else {}
        comparison_path = _validate_live_file(comparison, "application.comparison", blockers)
        if comparison.get("path") != str(paths.get("comparison") or ""):
            blockers.append("application.comparison.path does not match comparison.path")
        if comparison_path is not None:
            try:
                live_comparison = {**_fingerprint(comparison_path), **probe_media(comparison_path)}
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"comparison probe failed: {exc}")
            else:
                if comparison != live_comparison:
                    blockers.append("stored comparison contract is stale or was modified")
        review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
        if review and review.get("comparison_sha256") != comparison.get("sha256"):
            blockers.append("human review is not bound to the current comparison sha256")
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_decode = _decode_command(paths["delivery"]) if paths.get("delivery") and paths["delivery"].is_absolute() else []
        if validation.get("decode_checked") is not True or validation.get("decode_command") != expected_decode:
            blockers.append("full FFmpeg decode validation is missing or stale")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("decode validation is not bound to the current output sha256")
        if validation.get("detection_algorithm") != DETECTION_ALGORITHM:
            blockers.append("output interlace validation algorithm is stale or missing")
        blockers.extend(_review_blockers(plan.get("review")))

    warnings = _computed_warnings(plan)
    analysis = source.get("interlace_analysis") if isinstance(source.get("interlace_analysis"), Mapping) else {}
    summary = {
        "observed_classification": analysis.get("classification"),
        "effective_classification": analysis.get("effective_classification"),
        "mode": settings.get("mode"),
        "output_fps": (settings.get("output_rate") or {}).get("fps"),
        "backend": settings.get("backend"),
        "applied": applied,
        "confirmed": isinstance(plan.get("review"), Mapping) and not _review_blockers(plan.get("review")),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else "warn" if warnings else "ready"
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


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
            integrity.append(f"stored {field} is stale or was modified")
    result.update(derived)
    result["blockers"] = integrity + list(derived["blockers"])
    result["summary"] = {**derived["summary"], "blocking": len(result["blockers"])}
    result["status"] = "blocked" if result["blockers"] else derived["status"]
    return result


def build_plan(
    source_path: str,
    delivery_path: str,
    comparison_path: str,
    *,
    project_dir: str = ".",
    mode: str = "frame",
    parity: str = "auto",
    classification_override: Optional[str] = None,
    reviewed_by: str,
    note: str,
    sample_seconds: float = 8.0,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty decision note are required")
    if classification_override and classification_override not in OVERRIDES:
        raise ValueError(f"classification override must be one of {sorted(OVERRIDES)}")
    source = _resolve_project_path(source_path, root, label="source", must_exist=True)
    delivery = _resolve_project_path(delivery_path, root, label="delivery", must_exist=False, suffix=".mp4")
    comparison = _resolve_project_path(comparison_path, root, label="comparison", must_exist=False, suffix=".mp4")
    if len({source, delivery, comparison}) != 3:
        raise ValueError("source, delivery, and comparison paths must all be different")
    source_record = _source_info(source, sample_seconds=sample_seconds)
    analysis = source_record["interlace_analysis"]
    observed = str(analysis.get("classification") or "")
    effective = classification_override or observed
    if classification_override:
        analysis["classification_override"] = classification_override
    analysis["effective_classification"] = effective
    if effective == "telecine_candidate":
        raise ValueError("source is a telecine candidate; use an inverse-telecine workflow before deinterlacing")
    if effective == "progressive":
        raise ValueError("source is progressive; keep it unchanged instead of adding a lossy deinterlace encode")
    if effective == "mixed_or_uncertain":
        raise ValueError("source field structure is mixed or uncertain; inspect frames and pass an evidence-backed classification override")
    settings = _settings_for(source_record, mode=mode, parity=parity)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_root": str(root),
        "source": source_record,
        "decision": {
            "action": "deinterlace",
            "reviewed_by": reviewed_by.strip(),
            "note": note.strip(),
            "recorded_at": utc_now(),
        },
        "settings": settings,
        "delivery": {"path": str(delivery), "format": "mp4", "purpose": "progressive_working_copy"},
        "comparison": {"path": str(comparison), "format": "mp4", "purpose": "full_length_source_vs_progressive_review"},
        "application": None,
        "review": None,
        "review_contract": {
            "playback": "Watch the complete side-by-side comparison at normal speed with audio.",
            "checks": list(REVIEW_FIELDS),
            "source_side": "left",
            "progressive_side": "right",
            "pass_rule": "full_playback=completed and every check=pass",
        },
    }
    return _set_derived(plan)


def build_command(plan: Mapping[str, Any], output_path: Path) -> List[str]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") or {}
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source),
        "-map", "0:v:0", "-vf", str(settings.get("video_filter")),
        "-c:v", "libx264", "-preset", str(settings.get("video_preset")),
        "-crf", str(settings.get("video_crf")), "-pix_fmt", "yuv420p",
        "-fps_mode", "cfr", "-r", str((settings.get("output_rate") or {}).get("rational")),
    ]
    if (plan.get("source") or {}).get("has_audio"):
        command.extend([
            "-map", "0:a:0?", "-af", "aresample=async=1:first_pts=0,asetpts=N/SR/TB",
            "-c:a", "aac", "-b:a", f"{settings.get('audio_bitrate_kbps')}k", "-ar", str(settings.get("audio_sample_rate")),
        ])
    else:
        command.append("-an")
    command.extend(["-map_metadata", "-1", "-movflags", "+faststart", str(output_path)])
    return command


def _comparison_command(plan: Mapping[str, Any], output_media: Path, comparison_path: Path) -> List[str]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") or {}
    rate = str((settings.get("output_rate") or {}).get("rational"))
    graph = (
        f"[0:v]fps={rate},setpts=PTS-STARTPTS,scale=640:-2:flags=lanczos,setsar=1,setfield=prog[left];"
        f"[1:v]fps={rate},setpts=PTS-STARTPTS,scale=640:-2:flags=lanczos,setsar=1[right];"
        "[left][right]hstack=inputs=2:shortest=1[v]"
    )
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source), "-i", str(output_media),
        "-filter_complex", graph, "-map", "[v]", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(comparison_path),
    ]
    return command


def _temporary_output(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=target.suffix, dir=str(target.parent))
    os.close(fd)
    os.unlink(name)
    return Path(name)


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError("plan must be an existing non-symlink JSON file")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    verification = verify_plan(plan)
    allowed = {PENDING_APPLY}
    substantive = [item for item in verification.get("blockers") or [] if item not in allowed]
    if substantive:
        raise ValueError("plan is not safe to apply: " + "; ".join(substantive))
    source = Path(str(plan["source"]["path"]))
    delivery = Path(str(plan["delivery"]["path"]))
    comparison = Path(str(plan["comparison"]["path"]))
    if path in {source, delivery, comparison}:
        raise ValueError("plan path must not overlap source, delivery, or comparison")
    for label, target in (("delivery", delivery), ("comparison", comparison)):
        if target.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
        if target.exists() and not force:
            raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    source_before = _fingerprint(source)
    temporary_delivery = _temporary_output(delivery)
    temporary_comparison = _temporary_output(comparison)
    try:
        encode_command = build_command(plan, temporary_delivery)
        _run_checked(encode_command, "interlace conform encode")
        sample_seconds = float(plan["source"]["interlace_analysis"]["sample_seconds"])
        output = _output_info(temporary_delivery, sample_seconds=sample_seconds)
        contract_blockers = _output_contract_blockers(output, plan["source"], plan["settings"])
        if contract_blockers:
            raise RuntimeError("output validation failed: " + "; ".join(contract_blockers))
        decode_command = _decode_command(temporary_delivery)
        _run_checked(decode_command, "full output decode")
        compare_command = _comparison_command(plan, temporary_delivery, temporary_comparison)
        _run_checked(compare_command, "full-length A/B comparison")
        comparison_info = {**_fingerprint(temporary_comparison), **probe_media(temporary_comparison)}
        if _fingerprint(source) != source_before:
            raise RuntimeError("source changed during interlace conform; output was not promoted")
        os.replace(temporary_delivery, delivery)
        os.replace(temporary_comparison, comparison)
    finally:
        for temporary in (temporary_delivery, temporary_comparison):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output["path"] = str(delivery)
    comparison_info["path"] = str(comparison)
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "comparison": comparison_info,
        "validation": {
            "decode_checked": True,
            "decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
            "detection_algorithm": DETECTION_ALGORITHM,
            "output_classification": output["interlace_analysis"]["classification"],
        },
    }
    plan["review"] = None
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError("plan must be an existing non-symlink JSON file")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the interlace conform plan before confirming it")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty review note are required")
    current = verify_plan(plan)
    allowed = {PENDING_CONFIRM}
    substantive = [item for item in current.get("blockers") or [] if item not in allowed]
    if substantive:
        raise ValueError("plan is not safe to confirm: " + "; ".join(substantive))
    if full_playback not in {"completed", "not_completed"}:
        raise ValueError("full_playback must be completed or not_completed")
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "full_playback": full_playback,
        "checks": normalized,
        "comparison_sha256": plan["application"]["comparison"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def emit_analysis_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    analysis = source.get("interlace_analysis") if isinstance(source.get("interlace_analysis"), Mapping) else {}
    ratios = analysis.get("ratios") if isinstance(analysis.get("ratios"), Mapping) else {}
    lines = [
        "# Interlace Analysis",
        "",
        f"- Source: `{source.get('path')}`",
        f"- Classification: `{analysis.get('classification')}`",
        f"- Confidence: {analysis.get('confidence')}",
        f"- Stream field order: `{source.get('field_order')}`",
        f"- Repeated-field ratio: {ratios.get('repeated_fields')}",
        f"- Interlaced-frame ratio: {ratios.get('interlaced_frames')}",
        f"- Parity dominance: {ratios.get('parity_dominance')}",
        "",
        "## Recommendation",
        "",
        str(report.get("recommendation") or ""),
        "",
        "## Evidence",
        "",
    ]
    lines.extend(f"- {item}" for item in analysis.get("reasons") or [])
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in analysis.get("limitations") or [])
    return "\n".join(lines) + "\n"


def emit_plan_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") if isinstance(plan.get("summary"), Mapping) else {}
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    lines = [
        "# Interlace Conform Plan",
        "",
        f"- Status: `{plan.get('status')}`",
        f"- Plan ID: `{plan.get('plan_id')}`",
        f"- Source: `{source.get('path')}`",
        f"- Observed / effective: `{summary.get('observed_classification')}` / `{summary.get('effective_classification')}`",
        f"- Backend / mode / parity: `{settings.get('backend')}` / `{settings.get('mode')}` / `{settings.get('resolved_parity')}`",
        f"- Output rate: `{(settings.get('output_rate') or {}).get('rational')}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path')}`",
        f"- Comparison: `{(plan.get('comparison') or {}).get('path')}`",
        "",
    ]
    blockers = [str(item) for item in plan.get("blockers") or []]
    warnings = [str(item) for item in plan.get("warnings") or []]
    if blockers:
        lines.extend(["## Blockers", "", *[f"- {item}" for item in blockers], ""])
    if warnings:
        lines.extend(["## Warnings", "", *[f"- {item}" for item in warnings], ""])
    lines.extend([
        "## Review contract",
        "",
        "Play the complete comparison at 1×. The source is left and the progressive working copy is right. Confirm no residual combing, cadence/judder, line-detail loss, wrong field order, or audio-sync regression.",
        "",
    ])
    return "\n".join(lines)


def _write_optional_markdown(
    path: Optional[str],
    text: str,
    *,
    force: bool,
    forbidden: Sequence[Path] = (),
) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if target in {item.expanduser().resolve() for item in forbidden}:
        raise ValueError("markdown output must not overlap a source, plan, JSON, delivery, or comparison file")
    if target.is_symlink():
        raise ValueError("markdown output must not be a symlink")
    if target.exists() and not force:
        raise FileExistsError(f"markdown output exists; pass --force to replace it: {target}")
    _atomic_write_text(target, text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect interlace/telecine risk and create a reviewed progressive working copy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Sample source field structure without modifying media.")
    analyze.add_argument("source")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--sample-seconds", type=float, default=8.0, help="Seconds per head/middle/tail idet sample (max 60).")
    analyze.add_argument("--output", help="Optional interlace_analysis.v1 JSON path.")
    analyze.add_argument("--markdown", help="Optional human-readable Markdown path.")
    analyze.add_argument("--force", action="store_true")

    plan = subparsers.add_parser("plan", help="Bind a reviewed true-interlace decision and output paths.")
    plan.add_argument("source")
    plan.add_argument("--delivery", required=True, help="Progressive MP4 working-copy path inside the project.")
    plan.add_argument("--comparison", required=True, help="Full-length side-by-side MP4 review path inside the project.")
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--mode", choices=sorted(MODES), default="frame", help="frame keeps frame rate; field preserves field-time motion at double fps.")
    plan.add_argument("--parity", choices=sorted(PARITIES), default="auto")
    plan.add_argument("--classification-override", choices=sorted(OVERRIDES), help="Evidence-backed manual classification when idet is inconclusive.")
    plan.add_argument("--reviewed-by", required=True)
    plan.add_argument("--note", required=True)
    plan.add_argument("--sample-seconds", type=float, default=8.0)
    plan.add_argument("--output", required=True, help="interlace_conform.v1 JSON plan path.")
    plan.add_argument("--markdown")
    plan.add_argument("--force", action="store_true")

    apply = subparsers.add_parser("apply", help="Encode, fully validate, and atomically promote the progressive working copy.")
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")

    confirm = subparsers.add_parser(
        "confirm",
        help="Record the full-length normal-speed A/B review.",
        description="Record the full-length normal-speed A/B review.",
    )
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True)
    confirm.add_argument("--markdown")

    verify = subparsers.add_parser("verify", help="Re-probe source/output/comparison and reject plan or environment drift.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            report = build_analysis(args.source, project_dir=args.project_dir, sample_seconds=args.sample_seconds)
            source_path = Path(str(report["source"]["path"]))
            forbidden = [source_path]
            if args.output:
                output = Path(args.output).expanduser().resolve()
                if output == source_path:
                    raise ValueError("analysis output must not overwrite the source")
                if output.is_symlink() or (output.exists() and not args.force):
                    raise FileExistsError(f"analysis output exists or is a symlink; pass --force to replace: {output}")
                _atomic_write_json(output, report)
                forbidden.append(output)
            _write_optional_markdown(args.markdown, emit_analysis_markdown(report), force=args.force, forbidden=forbidden)
            analysis = report["source"]["interlace_analysis"]
            print(json.dumps({"classification": analysis["classification"], "confidence": analysis["confidence"], "recommendation": report["recommendation"]}, ensure_ascii=False))
            return 0
        if args.command == "plan":
            output = Path(args.output).expanduser().resolve()
            if output.is_symlink() or (output.exists() and not args.force):
                raise FileExistsError(f"plan output exists or is a symlink; pass --force to replace: {output}")
            report = build_plan(
                args.source,
                args.delivery,
                args.comparison,
                project_dir=args.project_dir,
                mode=args.mode,
                parity=args.parity,
                classification_override=args.classification_override,
                reviewed_by=args.reviewed_by,
                note=args.note,
                sample_seconds=args.sample_seconds,
            )
            plan_targets = {Path(str(report["source"]["path"])), Path(str(report["delivery"]["path"])), Path(str(report["comparison"]["path"]))}
            if output in plan_targets:
                raise ValueError("plan output must not overlap source, delivery, or comparison")
            _atomic_write_json(output, report)
            _write_optional_markdown(args.markdown, emit_plan_markdown(report), force=args.force, forbidden=[output, *plan_targets])
        elif args.command == "apply":
            report = apply_plan(args.plan, force=args.force)
            forbidden = [Path(args.plan), Path(str(report["source"]["path"])), Path(str(report["delivery"]["path"])), Path(str(report["comparison"]["path"]))]
            _write_optional_markdown(args.markdown, emit_plan_markdown(report), force=True, forbidden=forbidden)
        elif args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                checks=checks,
            )
            forbidden = [Path(args.plan), Path(str(report["source"]["path"])), Path(str(report["delivery"]["path"])), Path(str(report["comparison"]["path"]))]
            _write_optional_markdown(args.markdown, emit_plan_markdown(report), force=True, forbidden=forbidden)
        else:
            plan_path = Path(args.plan).expanduser().resolve()
            report = verify_plan(json.loads(plan_path.read_text(encoding="utf-8")))
            forbidden = [plan_path, Path(str(report["source"]["path"])), Path(str(report["delivery"]["path"])), Path(str(report["comparison"]["path"]))]
            _write_optional_markdown(args.markdown, emit_plan_markdown(report), force=True, forbidden=forbidden)
        print(json.dumps({"status": report["status"], "plan_id": report.get("plan_id"), "summary": report["summary"]}, ensure_ascii=False))
        return 2 if getattr(args, "strict", False) and report["summary"]["blocking"] else 0
    except (FileExistsError, OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"interlace_conform error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
