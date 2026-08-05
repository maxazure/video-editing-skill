#!/usr/bin/env python3
"""Measure shot-level color/exposure risks in a rendered video.

The analyzer is deliberately local and deterministic. It samples the encoded
output with FFmpeg ``signalstats``, aggregates robust per-shot medians, and
separates objective delivery blockers (notably out-of-broadcast-range pixels)
from aesthetic review warnings such as an intentional day/night cut.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scene_boundaries import build_scene_plan, detect_scene_times  # noqa: E402


VERSION = "shot_color_qa.v1"
NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
SIGNAL_KEYS = (
    "YLOW",
    "YAVG",
    "YHIGH",
    "UAVG",
    "VAVG",
    "SATAVG",
    "BRNG",
)


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), capture_output=True, text=True)


def _round3(value: float) -> float:
    return round(float(value), 3)


def _median(values: Sequence[float]) -> float:
    return round(float(statistics.median(values)), 3)


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: str, value: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(value, encoding="utf-8")


def probe_video(path: str) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,color_range,color_space,color_transfer,color_primaries:format=duration",
        "-of",
        "json",
        path,
    ]
    result = _run(command)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffprobe failed").strip())
    try:
        data = json.loads(result.stdout or "{}")
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("could not parse ffprobe video metadata") from exc
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("video metadata contains an invalid duration or dimensions")
    return {
        "duration": _round3(duration),
        "width": width,
        "height": height,
        "color_range": str(stream.get("color_range") or "unknown"),
        "color_space": str(stream.get("color_space") or "unknown"),
        "color_transfer": str(stream.get("color_transfer") or "unknown"),
        "color_primaries": str(stream.get("color_primaries") or "unknown"),
    }


def signalstats_command(path: str, *, sample_fps: float, sample_width: int) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        path,
        "-vf",
        (
            f"fps={sample_fps:.6f},scale={sample_width}:-2:flags=area,"
            "signalstats=stat=brng,metadata=print"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]


def parse_signalstats(log_text: str) -> List[Dict[str, float]]:
    """Parse FFmpeg metadata=print output into one record per sampled frame."""
    frames: List[Dict[str, float]] = []
    current: Optional[Dict[str, float]] = None
    frame_re = re.compile(rf"\bframe:\s*\d+.*?\bpts_time:({NUMBER_PATTERN})")
    stat_re = re.compile(rf"\blavfi\.signalstats\.([A-Z]+)=({NUMBER_PATTERN})")

    for line in log_text.splitlines():
        frame_match = frame_re.search(line)
        if frame_match:
            if current is not None:
                frames.append(current)
            try:
                time_value = float(frame_match.group(1))
            except ValueError:
                current = None
                continue
            current = {"time": time_value} if math.isfinite(time_value) and time_value >= 0 else None
            continue

        stat_match = stat_re.search(line)
        if current is None or stat_match is None:
            continue
        key = stat_match.group(1)
        if key not in SIGNAL_KEYS:
            continue
        try:
            value = float(stat_match.group(2))
        except ValueError:
            continue
        if math.isfinite(value):
            current[key.lower()] = value

    if current is not None:
        frames.append(current)

    complete: List[Dict[str, float]] = []
    required = {key.lower() for key in SIGNAL_KEYS}
    for frame in frames:
        if required.issubset(frame):
            complete.append({key: _round3(value) for key, value in frame.items()})
    return complete


def sample_video(path: str, *, sample_fps: float, sample_width: int) -> List[Dict[str, float]]:
    result = _run(signalstats_command(path, sample_fps=sample_fps, sample_width=sample_width))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "FFmpeg signalstats failed").strip()
        raise RuntimeError(detail[-3000:])
    samples = parse_signalstats((result.stderr or "") + "\n" + (result.stdout or ""))
    if not samples:
        raise RuntimeError("FFmpeg signalstats returned no complete frame samples")
    return samples


def load_scene_intervals(path: str, *, duration: float) -> Tuple[List[Dict[str, Any]], str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read scene boundaries: {exc}") from exc
    scenes = data.get("scenes") if isinstance(data, Mapping) else None
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene boundaries JSON must contain a non-empty scenes[] list")

    normalized: List[Dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(scenes, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"scene {index} is not an object")
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"scene {index} has invalid start/end") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"scene {index} has invalid start/end")
        if index == 1 and start > 0.05:
            raise ValueError("scene coverage must start at the beginning of the video")
        if start + 0.002 < previous_end:
            raise ValueError(f"scene {index} overlaps the previous scene")
        if index > 1 and start - previous_end > 0.05:
            raise ValueError(f"scene {index} leaves an uncovered timeline gap")
        if end > duration + 0.05:
            raise ValueError(f"scene {index} exceeds video duration")
        normalized.append(
            {
                "scene_id": str(item.get("scene_id") or f"scene_{index:03d}"),
                "start": _round3(start),
                "end": _round3(min(end, duration)),
                "duration": _round3(min(end, duration) - start),
            }
        )
        previous_end = end
    if duration - previous_end > 0.05:
        raise ValueError("scene coverage must reach the end of the video")
    return normalized, str(data.get("version") or "external")


def auto_scene_intervals(
    path: str,
    *,
    duration: float,
    scene_threshold: float,
    min_scene_duration: float,
) -> List[Dict[str, Any]]:
    scene_times = detect_scene_times(path, scene_threshold)
    plan = build_scene_plan(
        path,
        scene_times,
        duration=duration,
        threshold=scene_threshold,
        min_scene_duration=min_scene_duration,
    )
    return list(plan["scenes"])


def _samples_for_scene(
    samples: Sequence[Mapping[str, float]],
    *,
    start: float,
    end: float,
    boundary_margin: float,
) -> Tuple[List[Mapping[str, float]], bool]:
    within = [sample for sample in samples if start <= float(sample["time"]) < end]
    margin = min(boundary_margin, max(0.0, (end - start) / 2.0 - 0.01))
    interior = [
        sample
        for sample in within
        if start + margin <= float(sample["time"]) <= end - margin
    ]
    return (interior, False) if interior else (within, bool(within))


def _aggregate_scene(samples: Sequence[Mapping[str, float]]) -> Dict[str, float]:
    metrics = {
        "luma_low": _median([float(item["ylow"]) for item in samples]),
        "luma_avg": _median([float(item["yavg"]) for item in samples]),
        "luma_high": _median([float(item["yhigh"]) for item in samples]),
        "chroma_u_avg": _median([float(item["uavg"]) for item in samples]),
        "chroma_v_avg": _median([float(item["vavg"]) for item in samples]),
        "saturation_avg": _median([float(item["satavg"]) for item in samples]),
        "broadcast_range_ratio": _median([float(item["brng"]) for item in samples]),
    }
    metrics["luma_spread"] = round(metrics["luma_high"] - metrics["luma_low"], 3)
    return metrics


def build_qa_report(
    *,
    video_path: str,
    video_info: Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, float]],
    params: Mapping[str, Any],
    scene_source: Mapping[str, Any],
) -> Dict[str, Any]:
    shots: List[Dict[str, Any]] = []
    warnings: List[str] = []
    blockers: List[str] = []

    dark_limit = float(params["dark_luma"])
    bright_limit = float(params["bright_luma"])
    contrast_limit = float(params["low_contrast_spread"])
    saturation_limit = float(params["high_saturation"])
    broadcast_limit = float(params["max_broadcast_range_ratio"])
    fail_on_extremes = bool(params.get("fail_on_extremes"))
    full_range = str(video_info.get("color_range") or "").lower() == "pc"
    ignore_broadcast = bool(params.get("ignore_broadcast_range"))

    for scene in scenes:
        scene_id = str(scene.get("scene_id") or f"scene_{len(shots) + 1:03d}")
        start = float(scene["start"])
        end = float(scene["end"])
        selected, boundary_only = _samples_for_scene(
            samples,
            start=start,
            end=end,
            boundary_margin=float(params["boundary_margin"]),
        )
        flags: List[str] = []
        blocking_flags: List[str] = []
        if not selected:
            flags.append("missing_samples")
            blocking_flags.append("missing_samples")
            blockers.append(f"{scene_id}: no signalstats samples fall inside the scene")
            metrics: Dict[str, float] = {}
        else:
            metrics = _aggregate_scene(selected)
            if boundary_only:
                flags.append("boundary_sample_only")
                warnings.append(f"{scene_id}: only boundary-adjacent samples were available")
            if metrics["luma_avg"] <= dark_limit:
                flags.append("extreme_dark")
                warnings.append(f"{scene_id}: median luma {metrics['luma_avg']:.1f} is very dark")
                if fail_on_extremes:
                    blocking_flags.append("extreme_dark")
                    blockers.append(f"{scene_id}: extreme dark shot requires review")
            if metrics["luma_avg"] >= bright_limit:
                flags.append("extreme_bright")
                warnings.append(f"{scene_id}: median luma {metrics['luma_avg']:.1f} is very bright")
                if fail_on_extremes:
                    blocking_flags.append("extreme_bright")
                    blockers.append(f"{scene_id}: extreme bright shot requires review")
            if metrics["luma_spread"] <= contrast_limit:
                flags.append("low_contrast")
                warnings.append(f"{scene_id}: luma spread {metrics['luma_spread']:.1f} is low")
            if metrics["saturation_avg"] >= saturation_limit:
                flags.append("high_saturation")
                warnings.append(
                    f"{scene_id}: median saturation {metrics['saturation_avg']:.1f} is high"
                )
            if metrics["broadcast_range_ratio"] > broadcast_limit:
                if ignore_broadcast:
                    flags.append("broadcast_range_ignored")
                elif full_range:
                    flags.append("declared_full_range")
                    warnings.append(
                        f"{scene_id}: pixels exceed broadcast range, but the stream declares full range"
                    )
                else:
                    flags.append("broadcast_range_exceeded")
                    blocking_flags.append("broadcast_range_exceeded")
                    blockers.append(
                        f"{scene_id}: {metrics['broadcast_range_ratio']:.3%} median pixels exceed broadcast range"
                    )

        shots.append(
            {
                "scene_id": scene_id,
                "start": _round3(start),
                "end": _round3(end),
                "duration": _round3(end - start),
                "sample_count": len(selected),
                "sample_times": [_round3(float(item["time"])) for item in selected],
                "metrics": metrics,
                "flags": flags,
                "blocking_flags": blocking_flags,
            }
        )

    transitions: List[Dict[str, Any]] = []
    fail_on_jumps = bool(params.get("fail_on_jumps"))
    for before, after in zip(shots, shots[1:]):
        if not before["metrics"] or not after["metrics"]:
            continue
        left = before["metrics"]
        right = after["metrics"]
        luma_delta = abs(float(right["luma_avg"]) - float(left["luma_avg"]))
        chroma_distance = math.hypot(
            float(right["chroma_u_avg"]) - float(left["chroma_u_avg"]),
            float(right["chroma_v_avg"]) - float(left["chroma_v_avg"]),
        )
        saturation_delta = abs(
            float(right["saturation_avg"]) - float(left["saturation_avg"])
        )
        flags: List[str] = []
        blocking_flags: List[str] = []
        cut_time = float(after["start"])
        if luma_delta >= float(params["max_luma_jump"]):
            flags.append("abrupt_luma_change")
            warnings.append(
                f"{before['scene_id']} → {after['scene_id']} at {format_time(cut_time)}: "
                f"luma changes by {luma_delta:.1f}"
            )
        if chroma_distance >= float(params["max_chroma_jump"]):
            flags.append("abrupt_chroma_change")
            warnings.append(
                f"{before['scene_id']} → {after['scene_id']} at {format_time(cut_time)}: "
                f"chroma distance is {chroma_distance:.1f}"
            )
        if flags and fail_on_jumps:
            blocking_flags.extend(flags)
            blockers.append(
                f"{before['scene_id']} → {after['scene_id']}: abrupt color change requires review"
            )
        transitions.append(
            {
                "from_scene": before["scene_id"],
                "to_scene": after["scene_id"],
                "cut_time": _round3(cut_time),
                "luma_delta": _round3(luma_delta),
                "chroma_distance": _round3(chroma_distance),
                "saturation_delta": _round3(saturation_delta),
                "flags": flags,
                "blocking_flags": blocking_flags,
            }
        )

    flag_counts: Dict[str, int] = {}
    for item in list(shots) + list(transitions):
        for flag in item.get("flags") or []:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    unique_warnings = list(dict.fromkeys(warnings))
    unique_blockers = list(dict.fromkeys(blockers))
    status = "blocked" if unique_blockers else ("warn" if unique_warnings else "ready")
    return {
        "version": VERSION,
        "status": status,
        "source": {
            "video": os.path.abspath(video_path),
            **dict(video_info),
        },
        "scene_source": dict(scene_source),
        "params": dict(params),
        "summary": {
            "status": status,
            "shots": len(shots),
            "transitions": len(transitions),
            "sampled_frames": len(samples),
            "blocking": len(unique_blockers),
            "warnings": len(unique_warnings),
            **{key: flag_counts.get(key, 0) for key in sorted(flag_counts)},
        },
        "shots": shots,
        "transitions": transitions,
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "notes": [
            "Shot metrics are robust medians from downscaled FFmpeg signalstats samples.",
            "Abrupt luma/chroma changes are review warnings by default because intentional scene changes are valid.",
            "Chroma displacement is not a calibrated white-balance or skin-tone measurement.",
            "Review flagged cuts in the rendered master; this report does not predict aesthetics or engagement.",
        ],
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Shot Color QA",
        "",
        f"- Video: `{report.get('source', {}).get('video', '')}`",
        f"- Status: **{str(report.get('status', '')).upper()}**",
        f"- Shots: `{summary.get('shots', 0)}`",
        f"- Sampled frames: `{summary.get('sampled_frames', 0)}`",
        f"- Blocking: `{summary.get('blocking', 0)}`",
        f"- Warnings: `{summary.get('warnings', 0)}`",
        "",
        "## Shot Metrics",
        "",
        "| Shot | Time | Samples | Y avg | Y spread | U/V | Sat | BRNG | Flags |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for shot in report.get("shots") or []:
        metrics = shot.get("metrics") or {}
        lines.append(
            "| {scene} | {start}-{end} | {samples} | {yavg} | {spread} | {uv} | {sat} | {brng} | {flags} |".format(
                scene=shot.get("scene_id", ""),
                start=format_time(float(shot.get("start", 0))),
                end=format_time(float(shot.get("end", 0))),
                samples=shot.get("sample_count", 0),
                yavg=f"{float(metrics['luma_avg']):.1f}" if "luma_avg" in metrics else "-",
                spread=f"{float(metrics['luma_spread']):.1f}" if "luma_spread" in metrics else "-",
                uv=(
                    f"{float(metrics['chroma_u_avg']):.1f}/{float(metrics['chroma_v_avg']):.1f}"
                    if "chroma_u_avg" in metrics and "chroma_v_avg" in metrics
                    else "-"
                ),
                sat=f"{float(metrics['saturation_avg']):.1f}" if "saturation_avg" in metrics else "-",
                brng=(
                    f"{float(metrics['broadcast_range_ratio']):.2%}"
                    if "broadcast_range_ratio" in metrics
                    else "-"
                ),
                flags=", ".join(shot.get("flags") or []) or "-",
            )
        )

    flagged = [item for item in report.get("transitions") or [] if item.get("flags")]
    if flagged:
        lines.extend(
            [
                "",
                "## Flagged Cuts",
                "",
                "| Cut | Time | Δ luma | Chroma distance | Δ saturation | Flags |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for item in flagged:
            lines.append(
                "| {left} → {right} | {time} | {luma:.1f} | {chroma:.1f} | {sat:.1f} | {flags} |".format(
                    left=item.get("from_scene", ""),
                    right=item.get("to_scene", ""),
                    time=format_time(float(item.get("cut_time", 0))),
                    luma=float(item.get("luma_delta", 0)),
                    chroma=float(item.get("chroma_distance", 0)),
                    sat=float(item.get("saturation_delta", 0)),
                    flags=", ".join(item.get("flags") or []),
                )
            )

    for heading, key in (("Blocking Issues", "blockers"), ("Review Warnings", "warnings")):
        items = report.get(key) or []
        if items:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(f"- {item}" for item in items)

    if flagged:
        video = str(report.get("source", {}).get("video", ""))
        lines.extend(["", "## Review Commands", ""])
        for item in flagged:
            time_value = float(item.get("cut_time", 0))
            slug = str(time_value).replace(".", "_")
            lines.append(
                f"- `python3 scripts/timeline_view.py {shlex.quote(video)} "
                f"--at {time_value:.3f} --radius 1.5 --output verify/color_cut_{slug}.png`"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A luma/chroma jump is not automatically wrong; cuts between locations, graphics, day/night, or deliberate looks can be valid.",
            "- Broadcast-range blockers are objective encoded-output risks unless the stream explicitly declares full range or you deliberately ignore that check.",
            "- This report is not a calibrated waveform/vectorscope, white-balance decision, skin-tone check, or aesthetic score.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze shot-level luma, chroma, saturation, and broadcast-range risks."
    )
    parser.add_argument("input", help="Rendered master or platform export")
    parser.add_argument("--scene-boundaries", help="Optional scene_boundaries.v1 JSON; otherwise detect cuts")
    parser.add_argument("--output", required=True, help="Output shot_color_qa.v1 JSON")
    parser.add_argument("--markdown", help="Optional Markdown review report")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="Signalstats samples per second")
    parser.add_argument("--sample-width", type=int, default=320, help="Analysis width in pixels")
    parser.add_argument("--scene-threshold", type=float, default=0.35, help="FFmpeg scene score threshold")
    parser.add_argument("--min-scene-duration", type=float, default=1.0, help="Minimum detected scene duration")
    parser.add_argument("--boundary-margin", type=float, default=0.15, help="Exclude samples near cuts")
    parser.add_argument("--dark-luma", type=float, default=32.0, help="Warn below median YAVG")
    parser.add_argument("--bright-luma", type=float, default=220.0, help="Warn above median YAVG")
    parser.add_argument("--low-contrast-spread", type=float, default=18.0, help="Warn below YHIGH-YLOW")
    parser.add_argument("--high-saturation", type=float, default=95.0, help="Warn above SATAVG")
    parser.add_argument("--max-broadcast-range-ratio", type=float, default=0.01, help="Block above median BRNG ratio")
    parser.add_argument("--max-luma-jump", type=float, default=45.0, help="Warn above adjacent-shot YAVG delta")
    parser.add_argument("--max-chroma-jump", type=float, default=55.0, help="Warn above adjacent-shot U/V distance")
    parser.add_argument("--ignore-broadcast-range", action="store_true", help="Do not block on BRNG pixels")
    parser.add_argument("--fail-on-extremes", action="store_true", help="Escalate extreme dark/bright shots to blockers")
    parser.add_argument("--fail-on-jumps", action="store_true", help="Escalate abrupt adjacent-shot changes to blockers")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.sample_fps <= 0 or args.sample_fps > 10:
        raise ValueError("--sample-fps must be > 0 and <= 10")
    if args.sample_width < 64 or args.sample_width > 1920:
        raise ValueError("--sample-width must be between 64 and 1920")
    if args.scene_threshold <= 0 or args.scene_threshold > 1:
        raise ValueError("--scene-threshold must be > 0 and <= 1")
    if args.min_scene_duration <= 0:
        raise ValueError("--min-scene-duration must be positive")
    if args.boundary_margin < 0:
        raise ValueError("--boundary-margin must be non-negative")
    if args.dark_luma < 0 or args.bright_luma > 255 or args.dark_luma >= args.bright_luma:
        raise ValueError("luma thresholds must satisfy 0 <= dark < bright <= 255")
    if args.low_contrast_spread < 0 or args.high_saturation < 0:
        raise ValueError("contrast and saturation thresholds must be non-negative")
    if not 0 <= args.max_broadcast_range_ratio <= 1:
        raise ValueError("--max-broadcast-range-ratio must be between 0 and 1")
    if args.max_luma_jump <= 0 or args.max_chroma_jump <= 0:
        raise ValueError("jump thresholds must be positive")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        if not os.path.isfile(args.input):
            raise ValueError(f"input video not found: {args.input}")
        info = probe_video(args.input)
        if args.scene_boundaries:
            scenes, scene_version = load_scene_intervals(
                args.scene_boundaries,
                duration=float(info["duration"]),
            )
            scene_source = {
                "mode": "provided",
                "path": os.path.abspath(args.scene_boundaries),
                "version": scene_version,
            }
        else:
            scenes = auto_scene_intervals(
                args.input,
                duration=float(info["duration"]),
                scene_threshold=args.scene_threshold,
                min_scene_duration=args.min_scene_duration,
            )
            scene_source = {
                "mode": "detected",
                "method": "ffmpeg_select_scene_showinfo",
                "threshold": args.scene_threshold,
                "min_scene_duration": args.min_scene_duration,
            }
        samples = sample_video(
            args.input,
            sample_fps=args.sample_fps,
            sample_width=args.sample_width,
        )
        params = {
            "sample_fps": args.sample_fps,
            "sample_width": args.sample_width,
            "scene_threshold": args.scene_threshold,
            "min_scene_duration": args.min_scene_duration,
            "boundary_margin": args.boundary_margin,
            "dark_luma": args.dark_luma,
            "bright_luma": args.bright_luma,
            "low_contrast_spread": args.low_contrast_spread,
            "high_saturation": args.high_saturation,
            "max_broadcast_range_ratio": args.max_broadcast_range_ratio,
            "max_luma_jump": args.max_luma_jump,
            "max_chroma_jump": args.max_chroma_jump,
            "ignore_broadcast_range": args.ignore_broadcast_range,
            "fail_on_extremes": args.fail_on_extremes,
            "fail_on_jumps": args.fail_on_jumps,
        }
        report = build_qa_report(
            video_path=args.input,
            video_info=info,
            scenes=scenes,
            samples=samples,
            params=params,
            scene_source=scene_source,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))
    print(
        "Shot color QA: {status} shots={shots} samples={samples} blocking={blocking} warnings={warnings}".format(
            status=report["status"],
            shots=report["summary"]["shots"],
            samples=report["summary"]["sampled_frames"],
            blocking=report["summary"]["blocking"],
            warnings=report["summary"]["warnings"],
        ),
        file=sys.stderr,
    )
    if args.strict and report["summary"]["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
