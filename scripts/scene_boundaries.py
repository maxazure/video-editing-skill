#!/usr/bin/env python3
"""Detect visual scene boundaries with FFmpeg and emit review artifacts."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "scene_boundaries.v1"


def _round3(value: float) -> float:
    return round(float(value), 3)


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def probe_duration(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"could not parse ffprobe duration for {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"invalid media duration for {path}: {duration!r}")
    return duration


def ffmpeg_scene_command(input_path: str, threshold: float) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        input_path,
        "-vf",
        f"select='gt(scene,{threshold:.4f})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]


def parse_scene_times(log_text: str) -> List[float]:
    """Parse `pts_time` values from FFmpeg showinfo output."""
    times: List[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", log_text):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(value) and value >= 0:
            times.append(value)
    return sorted(set(_round3(t) for t in times))


def detect_scene_times(input_path: str, threshold: float) -> List[float]:
    result = _run(ffmpeg_scene_command(input_path, threshold))
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err[-2000:] or "ffmpeg scene detection failed")
    return parse_scene_times((result.stderr or "") + "\n" + (result.stdout or ""))


def _dedupe_nearby(times: Sequence[float], *, min_gap: float, duration: Optional[float] = None) -> List[float]:
    kept: List[float] = []
    previous = 0.0
    for raw in sorted(float(t) for t in times):
        if raw - previous >= min_gap:
            kept.append(raw)
            previous = raw
    if duration is not None and kept and duration - kept[-1] < min_gap:
        kept.pop()
    return kept


def build_scene_plan(
    input_path: str,
    scene_times: Sequence[float],
    *,
    duration: float,
    threshold: float,
    min_scene_duration: float = 1.0,
) -> Dict[str, Any]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if min_scene_duration < 0:
        raise ValueError("min_scene_duration must be non-negative")

    raw_boundaries = [
        float(t)
        for t in scene_times
        if 0.0 < float(t) < duration and math.isfinite(float(t))
    ]
    boundaries = _dedupe_nearby(raw_boundaries, min_gap=min_scene_duration, duration=duration)
    points = [0.0] + boundaries + [duration]

    scenes: List[Dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(points, points[1:]), start=1):
        if end <= start:
            continue
        scenes.append(
            {
                "scene_id": f"scene_{index:03d}",
                "start": _round3(start),
                "end": _round3(end),
                "duration": _round3(end - start),
            }
        )

    return {
        "version": VERSION,
        "source": {
            "video": input_path,
            "duration": _round3(duration),
        },
        "params": {
            "method": "ffmpeg_select_scene_showinfo",
            "threshold": float(threshold),
            "min_scene_duration": float(min_scene_duration),
        },
        "summary": {
            "raw_boundaries": len(raw_boundaries),
            "boundaries": len(boundaries),
            "scenes": len(scenes),
        },
        "boundaries": [_round3(t) for t in boundaries],
        "scenes": scenes,
    }


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Scene Boundaries",
        "",
        f"- Video: `{plan.get('source', {}).get('video', '')}`",
        f"- Duration: `{plan.get('source', {}).get('duration', 0):.3f}s`",
        f"- Threshold: `{plan.get('params', {}).get('threshold', 0):.3f}`",
        f"- Scenes: `{plan.get('summary', {}).get('scenes', 0)}`",
        "",
        "| Scene | Time | Duration |",
        "| --- | --- | ---: |",
    ]
    for scene in plan.get("scenes", []):
        lines.append(
            "| {scene_id} | {start}-{end} | {duration:.2f}s |".format(
                scene_id=scene["scene_id"],
                start=format_time(scene["start"]),
                end=format_time(scene["end"]),
                duration=float(scene["duration"]),
            )
        )
    lines.extend(
        [
            "",
            "## Review Notes",
            "",
            "- Use this JSON with `highlight_picker.py --scene-boundaries` to expand candidate clips to nearby visual cut points.",
            "- Scene detection is visual-only; transcript hook and ending quality still need review.",
            "- If boundaries look too dense, raise `--threshold` or `--min-scene-duration`.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect visual scene boundaries with FFmpeg.")
    parser.add_argument("input", help="Input video path")
    parser.add_argument("--output", required=True, help="Output scene_boundaries JSON")
    parser.add_argument("--markdown", help="Optional Markdown review table")
    parser.add_argument("--threshold", type=float, default=0.35, help="FFmpeg scene score threshold")
    parser.add_argument("--min-scene-duration", type=float, default=1.0, help="Drop boundaries closer than this many seconds")
    parser.add_argument("--duration", type=float, help="Override/provide duration, mainly for tests with --ffmpeg-log")
    parser.add_argument("--ffmpeg-log", help="Parse a saved FFmpeg showinfo log instead of running ffmpeg")
    parser.add_argument("--dry-run", action="store_true", help="Print the FFmpeg command without running it")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.threshold <= 0:
        print("Error: --threshold must be positive", file=sys.stderr)
        return 1
    if args.min_scene_duration < 0:
        print("Error: --min-scene-duration must be non-negative", file=sys.stderr)
        return 1
    if args.dry_run:
        print(" ".join(ffmpeg_scene_command(args.input, args.threshold)))
        return 0

    try:
        if args.ffmpeg_log:
            if args.duration is None:
                print("Error: --ffmpeg-log requires --duration", file=sys.stderr)
                return 1
            with open(args.ffmpeg_log, "r", encoding="utf-8") as f:
                scene_times = parse_scene_times(f.read())
            duration = float(args.duration)
        else:
            duration = float(args.duration) if args.duration is not None else probe_duration(args.input)
            scene_times = detect_scene_times(args.input, args.threshold)
        plan = build_scene_plan(
            args.input,
            scene_times,
            duration=duration,
            threshold=args.threshold,
            min_scene_duration=args.min_scene_duration,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))
    print(
        "Scene boundaries: {boundaries} cuts, {scenes} scenes, wrote {path}".format(
            boundaries=plan["summary"]["boundaries"],
            scenes=plan["summary"]["scenes"],
            path=args.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
