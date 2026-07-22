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
NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


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


def ffmpeg_scene_scores_command(input_path: str) -> List[str]:
    """Build an FFmpeg command that prints a scene score for every frame."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        input_path,
        "-vf",
        "select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
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


def parse_scene_scores(log_text: str) -> List[Dict[str, float]]:
    """Parse frame times and ``lavfi.scene_score`` values from FFmpeg metadata output."""
    pending_time: Optional[float] = None
    by_time: Dict[float, float] = {}
    for line in log_text.splitlines():
        time_match = re.search(rf"\bframe:\s*\d+.*?\bpts_time:({NUMBER_PATTERN})", line)
        if time_match:
            try:
                value = float(time_match.group(1))
            except ValueError:
                pending_time = None
            else:
                pending_time = value if math.isfinite(value) and value >= 0 else None
            continue

        score_match = re.search(rf"\blavfi\.scene_score=({NUMBER_PATTERN})", line)
        if score_match and pending_time is not None:
            try:
                score = float(score_match.group(1))
            except ValueError:
                pending_time = None
                continue
            if math.isfinite(score) and score >= 0:
                time_key = _round3(pending_time)
                by_time[time_key] = max(score, by_time.get(time_key, 0.0))
            pending_time = None

    return [
        {"time": time_value, "score": round(by_time[time_value], 6)}
        for time_value in sorted(by_time)
    ]


def adaptive_scene_evidence(
    samples: Sequence[Mapping[str, Any]],
    *,
    adaptive_threshold: float = 3.0,
    window_width: int = 2,
    min_scene_score: float = 0.15,
) -> List[Dict[str, float]]:
    """Return local scene-score spikes using a rolling neighbor average."""
    if adaptive_threshold <= 0:
        raise ValueError("adaptive_threshold must be positive")
    if window_width < 1:
        raise ValueError("window_width must be at least 1")
    if min_scene_score < 0:
        raise ValueError("min_scene_score must be non-negative")

    normalized: List[Dict[str, float]] = []
    for sample in samples:
        try:
            time_value = float(sample.get("time"))
            score = float(sample.get("score"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(time_value) and time_value >= 0 and math.isfinite(score) and score >= 0:
            normalized.append({"time": time_value, "score": score})
    normalized.sort(key=lambda item: item["time"])

    evidence: List[Dict[str, float]] = []
    for index in range(window_width, len(normalized) - window_width):
        target = normalized[index]
        neighbors = normalized[index - window_width:index] + normalized[index + 1:index + window_width + 1]
        local_average = sum(item["score"] for item in neighbors) / len(neighbors)
        if local_average < 1e-9:
            ratio = 255.0 if target["score"] >= min_scene_score else 0.0
        else:
            ratio = min(target["score"] / local_average, 255.0)
        if target["score"] >= min_scene_score and ratio >= adaptive_threshold:
            evidence.append(
                {
                    "time": _round3(target["time"]),
                    "score": round(target["score"], 6),
                    "adaptive_ratio": round(ratio, 3),
                    "local_average": round(local_average, 6),
                }
            )
    return evidence


def detect_scene_times(input_path: str, threshold: float) -> List[float]:
    result = _run(ffmpeg_scene_command(input_path, threshold))
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err[-2000:] or "ffmpeg scene detection failed")
    return parse_scene_times((result.stderr or "") + "\n" + (result.stdout or ""))


def detect_scene_scores(input_path: str) -> List[Dict[str, float]]:
    result = _run(ffmpeg_scene_scores_command(input_path))
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err[-2000:] or "ffmpeg adaptive scene analysis failed")
    return parse_scene_scores((result.stderr or "") + "\n" + (result.stdout or ""))


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
    method: str = "fixed",
    boundary_evidence: Sequence[Mapping[str, Any]] = (),
    adaptive_threshold: float = 3.0,
    window_width: int = 2,
    min_scene_score: float = 0.15,
) -> Dict[str, Any]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if min_scene_duration < 0:
        raise ValueError("min_scene_duration must be non-negative")
    if method not in {"fixed", "adaptive"}:
        raise ValueError("method must be fixed or adaptive")

    raw_boundaries = [
        float(t)
        for t in scene_times
        if 0.0 < float(t) < duration and math.isfinite(float(t))
    ]
    boundaries = _dedupe_nearby(raw_boundaries, min_gap=min_scene_duration, duration=duration)
    normalized_evidence: List[Dict[str, Any]] = []
    for item in boundary_evidence:
        try:
            time_value = _round3(float(item.get("time")))
        except (TypeError, ValueError):
            continue
        if time_value not in boundaries:
            continue
        normalized = {"time": time_value}
        for key in ("score", "adaptive_ratio", "local_average"):
            try:
                value = float(item.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                normalized[key] = value
        normalized_evidence.append(normalized)
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
            "method": "ffmpeg_scene_score_adaptive" if method == "adaptive" else "ffmpeg_select_scene_showinfo",
            "mode": method,
            "threshold": float(threshold),
            "min_scene_duration": float(min_scene_duration),
            **(
                {
                    "adaptive_threshold": float(adaptive_threshold),
                    "window_width": int(window_width),
                    "min_scene_score": float(min_scene_score),
                }
                if method == "adaptive"
                else {}
            ),
        },
        "summary": {
            "raw_boundaries": len(raw_boundaries),
            "boundaries": len(boundaries),
            "scenes": len(scenes),
        },
        "boundaries": [_round3(t) for t in boundaries],
        "boundary_evidence": normalized_evidence,
        "scenes": scenes,
    }


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    params = plan.get("params", {})
    mode = params.get("mode", "fixed")
    lines = [
        "# Scene Boundaries",
        "",
        f"- Video: `{plan.get('source', {}).get('video', '')}`",
        f"- Duration: `{plan.get('source', {}).get('duration', 0):.3f}s`",
        f"- Mode: `{mode}`",
        f"- Threshold: `{params.get('threshold', 0):.3f}`",
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
    evidence = plan.get("boundary_evidence") or []
    if evidence:
        lines.extend(
            [
                "",
                "## Detected Cut Evidence",
                "",
                "| Time | Scene score | Adaptive ratio | Neighbor average |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for item in evidence:
            lines.append(
                "| {time} | {score:.4f} | {ratio:.2f} | {average:.4f} |".format(
                    time=format_time(float(item.get("time", 0.0))),
                    score=float(item.get("score", 0.0)),
                    ratio=float(item.get("adaptive_ratio", 0.0)),
                    average=float(item.get("local_average", 0.0)),
                )
            )
    lines.extend(
        [
            "",
            "## Review Notes",
            "",
            "- Use this JSON with `highlight_picker.py --scene-boundaries` to expand candidate clips to nearby visual cut points.",
            "- Scene detection is visual-only; transcript hook and ending quality still need review.",
            (
                "- If boundaries look too dense, raise `--adaptive-threshold`, `--min-scene-score`, or `--min-scene-duration`."
                if mode == "adaptive"
                else "- If boundaries look too dense, raise `--threshold` or `--min-scene-duration`."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect visual scene boundaries with FFmpeg.")
    parser.add_argument("input", help="Input video path")
    parser.add_argument("--output", required=True, help="Output scene_boundaries JSON")
    parser.add_argument("--markdown", help="Optional Markdown review table")
    parser.add_argument("--method", choices=("fixed", "adaptive"), default="fixed", help="Scene score decision mode")
    parser.add_argument("--threshold", type=float, default=0.35, help="FFmpeg scene score threshold")
    parser.add_argument("--adaptive-threshold", type=float, default=3.0, help="Minimum score/neighbor ratio in adaptive mode")
    parser.add_argument("--window-width", type=int, default=2, help="Frames before and after each target in adaptive mode")
    parser.add_argument("--min-scene-score", type=float, default=0.15, help="Minimum absolute FFmpeg scene score in adaptive mode")
    parser.add_argument("--min-scene-duration", type=float, default=1.0, help="Drop boundaries closer than this many seconds")
    parser.add_argument("--duration", type=float, help="Override/provide duration, mainly for tests with --ffmpeg-log")
    parser.add_argument("--ffmpeg-log", help="Parse saved FFmpeg showinfo/scene-score metadata instead of running ffmpeg")
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
    if args.adaptive_threshold <= 0:
        print("Error: --adaptive-threshold must be positive", file=sys.stderr)
        return 1
    if args.window_width < 1:
        print("Error: --window-width must be at least 1", file=sys.stderr)
        return 1
    if args.min_scene_score < 0:
        print("Error: --min-scene-score must be non-negative", file=sys.stderr)
        return 1
    if args.dry_run:
        command = ffmpeg_scene_scores_command(args.input) if args.method == "adaptive" else ffmpeg_scene_command(args.input, args.threshold)
        print(" ".join(command))
        return 0

    try:
        boundary_evidence: List[Dict[str, float]] = []
        if args.ffmpeg_log:
            if args.duration is None:
                print("Error: --ffmpeg-log requires --duration", file=sys.stderr)
                return 1
            with open(args.ffmpeg_log, "r", encoding="utf-8") as f:
                log_text = f.read()
            if args.method == "adaptive":
                boundary_evidence = adaptive_scene_evidence(
                    parse_scene_scores(log_text),
                    adaptive_threshold=args.adaptive_threshold,
                    window_width=args.window_width,
                    min_scene_score=args.min_scene_score,
                )
                scene_times = [item["time"] for item in boundary_evidence]
            else:
                scene_times = parse_scene_times(log_text)
            duration = float(args.duration)
        else:
            duration = float(args.duration) if args.duration is not None else probe_duration(args.input)
            if args.method == "adaptive":
                boundary_evidence = adaptive_scene_evidence(
                    detect_scene_scores(args.input),
                    adaptive_threshold=args.adaptive_threshold,
                    window_width=args.window_width,
                    min_scene_score=args.min_scene_score,
                )
                scene_times = [item["time"] for item in boundary_evidence]
            else:
                scene_times = detect_scene_times(args.input, args.threshold)
        plan = build_scene_plan(
            args.input,
            scene_times,
            duration=duration,
            threshold=args.threshold,
            min_scene_duration=args.min_scene_duration,
            method=args.method,
            boundary_evidence=boundary_evidence,
            adaptive_threshold=args.adaptive_threshold,
            window_width=args.window_width,
            min_scene_score=args.min_scene_score,
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
