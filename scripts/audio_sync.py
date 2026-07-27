#!/usr/bin/env python3
"""Estimate and plan synchronization for an external audio recording.

This tool is for shoots where the camera/screen recording has scratch audio and
there is a cleaner microphone/lav/recorder track next to it. It extracts a
lightweight amplitude envelope from both files, estimates the offset, and writes
an auditable JSON/Markdown plan. It can also build or run the FFmpeg command
that replaces the reference media's audio with the synced external track.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "audio_sync_plan.v1"


class AudioSyncError(ValueError):
    """Raised when audio sync cannot be estimated from the supplied inputs."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round4(value: float) -> float:
    return round(float(value), 4)


def _round3(value: float) -> float:
    return round(float(value), 3)


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


def probe_duration(path: str) -> Optional[float]:
    result = _run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    if result.returncode != 0:
        return None
    try:
        duration = float((result.stdout or "").strip())
    except ValueError:
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def decode_audio_envelope(
    path: str,
    *,
    sample_rate: int = 8000,
    frame_ms: float = 40.0,
    max_duration: Optional[float] = None,
    audio_stream_index: Optional[int] = None,
) -> List[float]:
    """Decode media audio to a mono amplitude envelope using FFmpeg."""

    if frame_ms <= 0:
        raise AudioSyncError("frame_ms must be greater than 0")
    if sample_rate <= 0:
        raise AudioSyncError("sample_rate must be greater than 0")

    cmd = ["ffmpeg", "-v", "error", "-i", path]
    if audio_stream_index is not None:
        if audio_stream_index < 0:
            raise AudioSyncError("audio_stream_index must be non-negative")
        cmd.extend(["-map", f"0:a:{audio_stream_index}"])
    cmd.extend(["-vn", "-ac", "1", "-ar", str(sample_rate)])
    if max_duration and max_duration > 0:
        cmd.extend(["-t", str(float(max_duration))])
    cmd.extend(["-f", "s16le", "-"])

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AudioSyncError(stderr or f"ffmpeg failed to decode audio: {path}")
    if not result.stdout:
        raise AudioSyncError(f"no decodable audio stream found: {path}")

    samples = array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()

    frame_samples = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    envelope: List[float] = []
    for start in range(0, len(samples), frame_samples):
        chunk = samples[start:start + frame_samples]
        if not chunk:
            continue
        envelope.append(sum(abs(int(value)) for value in chunk) / float(len(chunk)))
    return envelope


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return float(ordered[index])


def _sync_features(values: Sequence[float]) -> List[float]:
    """Turn raw amplitude envelopes into sparse energy/onset features.

    Silence should not create positive evidence for a lag. We therefore remove a
    low noise floor and score only active energy plus a small onset component.
    """

    if not values:
        return []
    floor = _percentile(values, 0.2)
    peak = max(values)
    span = peak - floor
    if span <= 1e-9:
        return [0.0 for _ in values]

    energy = [max(0.0, (value - floor) / span) for value in values]
    onset: List[float] = []
    previous = 0.0
    for value in energy:
        onset.append(max(0.0, value - previous))
        previous = value
    return [0.8 * e + 0.2 * o for e, o in zip(energy, onset)]


def _score_lag(
    reference: Sequence[float],
    external: Sequence[float],
    lag: int,
    *,
    total_ref_energy: float,
    total_ext_energy: float,
) -> Tuple[Optional[float], int]:
    start = max(0, lag)
    end = min(len(reference), len(external) + lag)
    overlap = end - start
    if overlap <= 0:
        return None, 0

    dot = 0.0
    ref_energy = 0.0
    ext_energy = 0.0
    for ref_index in range(start, end):
        ext_index = ref_index - lag
        ref_value = reference[ref_index]
        ext_value = external[ext_index]
        dot += ref_value * ext_value
        ref_energy += ref_value * ref_value
        ext_energy += ext_value * ext_value
    if ref_energy <= 1e-9 or ext_energy <= 1e-9:
        return None, overlap
    raw_score = dot / math.sqrt(ref_energy * ext_energy)
    coverage = min(ref_energy / total_ref_energy, ext_energy / total_ext_energy)
    return raw_score * max(0.0, min(1.0, coverage)), overlap


def estimate_offset(
    reference_envelope: Sequence[float],
    external_envelope: Sequence[float],
    *,
    frame_seconds: float,
    max_offset_seconds: float = 10.0,
    min_overlap_seconds: float = 2.0,
) -> Dict[str, Any]:
    """Estimate offset_seconds = reference_time - external_time.

    Positive offsets mean the external track should be delayed in the final
    output. Negative offsets mean the external track should be trimmed at the
    front so it starts earlier relative to the reference timeline.
    """

    if frame_seconds <= 0:
        raise AudioSyncError("frame_seconds must be greater than 0")
    if len(reference_envelope) < 2 or len(external_envelope) < 2:
        raise AudioSyncError("not enough audio envelope frames to estimate sync")

    reference = _sync_features(reference_envelope)
    external = _sync_features(external_envelope)
    total_ref_energy = sum(value * value for value in reference)
    total_ext_energy = sum(value * value for value in external)
    if total_ref_energy <= 1e-9 or total_ext_energy <= 1e-9:
        raise AudioSyncError("audio envelope has too little active energy to estimate sync")
    max_lag = max(0, int(round(max_offset_seconds / frame_seconds)))
    max_lag = min(max_lag, max(len(reference), len(external)) - 1)
    min_overlap = max(1, int(round(min_overlap_seconds / frame_seconds)))

    scored: List[Tuple[float, int, int]] = []
    for lag in range(-max_lag, max_lag + 1):
        score, overlap = _score_lag(
            reference,
            external,
            lag,
            total_ref_energy=total_ref_energy,
            total_ext_energy=total_ext_energy,
        )
        if score is None or overlap < min_overlap:
            continue
        scored.append((float(score), lag, overlap))

    if not scored:
        raise AudioSyncError("could not find enough overlapping audio to estimate sync")

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_lag, best_overlap = scored[0]
    next_scores = [score for score, lag, _overlap in scored[1:] if abs(lag - best_lag) > 2]
    second_score = next_scores[0] if next_scores else scored[1][0] if len(scored) > 1 else -1.0
    margin = best_score - second_score

    correlation_quality = max(0.0, min(1.0, best_score))
    margin_quality = max(0.0, min(1.0, margin / 0.12))
    confidence = max(0.0, min(1.0, 0.8 * correlation_quality + 0.2 * margin_quality))

    return {
        "offset_seconds": _round4(best_lag * frame_seconds),
        "lag_frames": best_lag,
        "frame_seconds": _round4(frame_seconds),
        "score": _round3(best_score),
        "score_margin": _round3(margin),
        "confidence": _round3(confidence),
        "matched_frames": int(best_overlap),
        "matched_seconds": _round3(best_overlap * frame_seconds),
        "searched_offsets": [-_round4(max_lag * frame_seconds), _round4(max_lag * frame_seconds)],
    }


def audio_filter_from_offset(offset_seconds: float, *, duration: Optional[float] = None) -> str:
    filters: List[str] = []
    if offset_seconds > 0:
        delay_ms = int(round(offset_seconds * 1000.0))
        filters.append(f"adelay={delay_ms}:all=1")
    elif offset_seconds < 0:
        filters.append(f"atrim=start={_round4(abs(offset_seconds))}")
    filters.append("asetpts=PTS-STARTPTS")
    if duration and duration > 0:
        filters.append(f"atrim=duration={_round4(duration)}")
    filters.append("aresample=48000")
    return ",".join(filters)


def build_replace_audio_command(
    *,
    reference_media: str,
    external_audio: str,
    output_media: str,
    offset_seconds: float,
    duration: Optional[float] = None,
    audio_bitrate: str = "192k",
) -> List[str]:
    audio_filter = audio_filter_from_offset(offset_seconds, duration=duration)
    return [
        "ffmpeg",
        "-y",
        "-i",
        reference_media,
        "-i",
        external_audio,
        "-filter_complex",
        f"[1:a]{audio_filter}[synced_a]",
        "-map",
        "0:v:0?",
        "-map",
        "[synced_a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-shortest",
        output_media,
    ]


def _status_from_confidence(confidence: float, min_confidence: float) -> str:
    return "ready" if confidence >= min_confidence else "review"


def build_audio_sync_plan(
    *,
    reference_media: str,
    external_audio: str,
    offset_seconds: Optional[float] = None,
    replace_output: Optional[str] = None,
    sample_rate: int = 8000,
    frame_ms: float = 40.0,
    max_offset_seconds: float = 10.0,
    max_probe_seconds: Optional[float] = 180.0,
    min_confidence: float = 0.45,
    audio_bitrate: str = "192k",
) -> Dict[str, Any]:
    reference_path = os.path.abspath(os.path.expanduser(reference_media))
    external_path = os.path.abspath(os.path.expanduser(external_audio))
    output_path = os.path.abspath(os.path.expanduser(replace_output)) if replace_output else None

    reference_duration = probe_duration(reference_path) if Path(reference_path).exists() else None
    external_duration = probe_duration(external_path) if Path(external_path).exists() else None
    warnings: List[str] = []

    if offset_seconds is None:
        reference_env = decode_audio_envelope(
            reference_path,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            max_duration=max_probe_seconds,
        )
        external_env = decode_audio_envelope(
            external_path,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            max_duration=max_probe_seconds,
        )
        alignment = estimate_offset(
            reference_env,
            external_env,
            frame_seconds=frame_ms / 1000.0,
            max_offset_seconds=max_offset_seconds,
        )
        method = "envelope_cross_correlation"
    else:
        alignment = {
            "offset_seconds": _round4(offset_seconds),
            "lag_frames": None,
            "frame_seconds": _round4(frame_ms / 1000.0),
            "score": None,
            "score_margin": None,
            "confidence": 1.0,
            "matched_frames": None,
            "matched_seconds": None,
            "searched_offsets": None,
        }
        method = "manual_offset"

    confidence = float(alignment.get("confidence") or 0.0)
    status = _status_from_confidence(confidence, min_confidence)
    offset = float(alignment["offset_seconds"])

    if not Path(reference_path).exists():
        warnings.append("reference_media_missing")
    if not Path(external_path).exists():
        warnings.append("external_audio_missing")
    if status == "review":
        warnings.append("low_confidence_alignment")
    searched = alignment.get("searched_offsets")
    if isinstance(searched, list) and len(searched) == 2:
        if abs(abs(offset) - max(abs(float(searched[0])), abs(float(searched[1])))) <= alignment["frame_seconds"]:
            warnings.append("offset_near_search_boundary")

    replace_command = None
    if output_path:
        replace_command = build_replace_audio_command(
            reference_media=reference_path,
            external_audio=external_path,
            output_media=output_path,
            offset_seconds=offset,
            duration=reference_duration,
            audio_bitrate=audio_bitrate,
        )

    blocking = 1 if status != "ready" else 0
    if "reference_media_missing" in warnings or "external_audio_missing" in warnings:
        blocking += 1

    next_actions = [
        "Review offset and confidence before replacing production audio.",
        "Use --replace-output with --apply only after the sync plan looks correct.",
    ]
    if status == "review":
        next_actions.insert(0, "Low confidence: verify with a clap, waveform, or short preview before applying.")
    if not output_path:
        next_actions.append("Pass --replace-output output/synced.mp4 to get a ready-to-run FFmpeg command.")

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": status,
        "method": method,
        "reference_media": {
            "path": reference_path,
            "exists": Path(reference_path).exists(),
            "duration": _round3(reference_duration) if reference_duration else None,
        },
        "external_audio": {
            "path": external_path,
            "exists": Path(external_path).exists(),
            "duration": _round3(external_duration) if external_duration else None,
        },
        "settings": {
            "sample_rate": sample_rate,
            "frame_ms": _round3(frame_ms),
            "max_offset_seconds": _round3(max_offset_seconds),
            "max_probe_seconds": _round3(max_probe_seconds) if max_probe_seconds else None,
            "min_confidence": _round3(min_confidence),
        },
        "alignment": alignment,
        "summary": {
            "status": status,
            "blocking": blocking,
            "warnings": len(warnings),
            "offset_seconds": _round4(offset),
            "confidence": _round3(confidence),
            "interpretation": (
                "positive offset delays the external track; negative offset trims its front"
            ),
        },
        "replace_audio": {
            "output": output_path,
            "filter": audio_filter_from_offset(offset, duration=reference_duration),
            "command": replace_command,
            "applied": False,
        },
        "warnings": warnings,
        "next_actions": next_actions,
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary", {}) if isinstance(plan.get("summary"), Mapping) else {}
    alignment = plan.get("alignment", {}) if isinstance(plan.get("alignment"), Mapping) else {}
    replace = plan.get("replace_audio", {}) if isinstance(plan.get("replace_audio"), Mapping) else {}
    lines = [
        "# Audio Sync Plan",
        "",
        f"- version: `{plan.get('version')}`",
        f"- status: `{plan.get('status')}`",
        f"- method: `{plan.get('method')}`",
        f"- offset_seconds: `{summary.get('offset_seconds')}`",
        f"- confidence: `{summary.get('confidence')}`",
        f"- interpretation: {summary.get('interpretation')}",
        "",
        "| input | path | duration | exists |",
        "|---|---|---:|---:|",
    ]
    for label in ("reference_media", "external_audio"):
        item = plan.get(label, {}) if isinstance(plan.get(label), Mapping) else {}
        lines.append(
            f"| {label} | `{item.get('path', '')}` | {item.get('duration')} | {item.get('exists')} |"
        )
    lines.extend([
        "",
        "## Alignment",
        "",
        f"- score: `{alignment.get('score')}`",
        f"- score_margin: `{alignment.get('score_margin')}`",
        f"- matched_seconds: `{alignment.get('matched_seconds')}`",
        f"- searched_offsets: `{alignment.get('searched_offsets')}`",
    ])
    warnings = list(plan.get("warnings") or [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in warnings)
    command = replace.get("command")
    if command:
        lines.extend([
            "",
            "## Replace Audio Command",
            "",
            "```bash",
            " ".join(_shell_quote(part) for part in command),
            "```",
        ])
    next_actions = list(plan.get("next_actions") or [])
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {item}" for item in next_actions)
    lines.append("")
    return "\n".join(lines)


def _shell_quote(value: Any) -> str:
    text = str(value)
    if not text:
        return "''"
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+-=.,/:@%"
    if all(char in safe for char in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate sync offset for external audio and write an auditable replacement plan.",
    )
    parser.add_argument("--reference-media", required=True, help="Video/audio with scratch reference audio.")
    parser.add_argument("--external-audio", required=True, help="Cleaner microphone/lav/recorder audio file.")
    parser.add_argument("--output", required=True, help="JSON audio_sync_plan.v1 output path.")
    parser.add_argument("--markdown", help="Optional Markdown review output path.")
    parser.add_argument("--replace-output", help="Optional media output path for replacing audio.")
    parser.add_argument("--apply", action="store_true", help="Run the generated FFmpeg replace-audio command.")
    parser.add_argument("--offset", type=float, help="Manual offset seconds; skips automatic estimation.")
    parser.add_argument("--sample-rate", type=int, default=8000, help="Envelope decode sample rate.")
    parser.add_argument("--frame-ms", type=float, default=40.0, help="Envelope frame size in milliseconds.")
    parser.add_argument("--max-offset", type=float, default=10.0, help="Maximum absolute offset to search.")
    parser.add_argument(
        "--max-probe-seconds",
        type=float,
        default=180.0,
        help="Limit audio decoded for estimation; use 0 for full file.",
    )
    parser.add_argument("--min-confidence", type=float, default=0.45, help="Strict/review confidence threshold.")
    parser.add_argument("--audio-bitrate", default="192k", help="AAC bitrate for --apply output.")
    parser.add_argument("--strict", action="store_true", help="Return 2 when the plan is not ready.")
    args = parser.parse_args(argv)

    max_probe = None if args.max_probe_seconds == 0 else args.max_probe_seconds
    try:
        plan = build_audio_sync_plan(
            reference_media=args.reference_media,
            external_audio=args.external_audio,
            offset_seconds=args.offset,
            replace_output=args.replace_output,
            sample_rate=args.sample_rate,
            frame_ms=args.frame_ms,
            max_offset_seconds=args.max_offset,
            max_probe_seconds=max_probe,
            min_confidence=args.min_confidence,
            audio_bitrate=args.audio_bitrate,
        )
    except AudioSyncError as exc:
        print(f"audio_sync error: {exc}", file=sys.stderr)
        return 1

    command = plan.get("replace_audio", {}).get("command")
    if args.apply:
        if not command:
            print("audio_sync error: --apply requires --replace-output", file=sys.stderr)
            return 1
        result = _run(command)
        if result.returncode != 0:
            print(result.stderr.strip() or "ffmpeg replace-audio command failed", file=sys.stderr)
            return result.returncode or 1
        plan["replace_audio"]["applied"] = True
        plan["replace_audio"]["output_exists"] = Path(args.replace_output).is_file()

    _write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    if args.strict and int(plan.get("summary", {}).get("blocking") or 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
