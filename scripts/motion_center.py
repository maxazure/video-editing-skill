#!/usr/bin/env python3
"""Report where local pixel motion occurs; never choose a crop automatically."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

from border_crop import fingerprint, source_file

VERSION = "motion_center.v1"
WIDTH = 96
HEIGHT = 54


def measure_pair(previous: bytes, current: bytes, *, threshold: int = 24,
                 min_pixels: int = 5, max_coverage: float = 0.35) -> dict:
    """Locate changed pixels in two small grayscale frames."""
    if len(previous) != WIDTH * HEIGHT or len(current) != WIDTH * HEIGHT:
        raise ValueError("invalid decoded frame size")
    changed = [(index, abs(left - right)) for index, (left, right)
               in enumerate(zip(previous, current)) if abs(left - right) >= threshold]
    count = len(changed)
    coverage = count / (WIDTH * HEIGHT)
    status = "static" if count < min_pixels else "global_change" if coverage > max_coverage else "local_motion"
    result = {"status": status, "changed_pixels": count, "coverage": round(coverage, 4),
              "center": None}
    if status == "local_motion":
        weight = sum(delta for _, delta in changed)
        x = sum((index % WIDTH + 0.5) * delta for index, delta in changed) / weight
        y = sum((index // WIDTH + 0.5) * delta for index, delta in changed) / weight
        result["center"] = [round(x / WIDTH, 4), round(y / HEIGHT, 4)]
    return result


def analyze(source: str, *, sample_fps: float = 2, threshold: int = 24,
            min_pixels: int = 5, max_coverage: float = 0.35) -> dict:
    if not math.isfinite(sample_fps) or not 0.5 <= sample_fps <= 4:
        raise ValueError("sample-fps must be 0.5..4")
    if not 1 <= threshold <= 255 or not 1 <= min_pixels <= WIDTH * HEIGHT:
        raise ValueError("threshold must be 1..255 and min-pixels must fit the analysis frame")
    if not math.isfinite(max_coverage) or not 0.05 <= max_coverage <= 0.95:
        raise ValueError("max-coverage must be 0.05..0.95")
    path = source_file(source)
    before = fingerprint(path)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
               "-map", "0:v:0", "-vf", f"setpts=PTS-STARTPTS,fps={sample_fps:g},scale={WIDTH}:{HEIGHT}:flags=area,format=gray",
               "-an", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-1200:] or "FFmpeg failed")
    frame_size = WIDTH * HEIGHT
    if not result.stdout or len(result.stdout) % frame_size:
        raise ValueError("FFmpeg produced no complete analysis frames")
    frames = [result.stdout[i:i + frame_size] for i in range(0, len(result.stdout), frame_size)]
    if len(frames) < 2:
        raise ValueError("source is too short for two sampled frames")
    samples = []
    counts = {"local_motion": 0, "static": 0, "global_change": 0}
    for index, (previous, current) in enumerate(zip(frames, frames[1:]), start=1):
        row = measure_pair(previous, current, threshold=threshold,
                           min_pixels=min_pixels, max_coverage=max_coverage)
        row["time_seconds"] = round(index / sample_fps, 3)
        samples.append(row)
        counts[row["status"]] += 1
    if fingerprint(path) != before:
        raise ValueError("source changed during analysis")
    return {"version": VERSION, "source": before,
            "settings": {"sample_fps": sample_fps, "threshold": threshold,
                         "min_pixels": min_pixels, "max_coverage": max_coverage},
            "analysis_frame": [WIDTH, HEIGHT], "samples": samples,
            "summary": {"sample_count": len(samples), **counts}}


def verify(report: str) -> dict:
    path = source_file(report)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ValueError("unknown motion-center report version")
    fresh = analyze(data["source"]["path"], **data["settings"])
    if fresh != data:
        raise ValueError("report or source changed since analysis")
    return {"status": "verified", "summary": fresh["summary"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("analyze", help="Sample pixel motion across a video")
    scan.add_argument("source")
    scan.add_argument("--output", required=True)
    scan.add_argument("--sample-fps", type=float, default=2)
    scan.add_argument("--threshold", type=int, default=24)
    scan.add_argument("--min-pixels", type=int, default=5)
    scan.add_argument("--max-coverage", type=float, default=0.35)
    check = sub.add_parser("verify", help="Rescan the source and compare the report")
    check.add_argument("report")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify(args.report), ensure_ascii=False))
        else:
            source = source_file(args.source)
            output = Path(args.output).expanduser()
            if output.is_symlink() or output.resolve() == source or output.exists():
                raise ValueError("output must be a new regular path distinct from source")
            report = analyze(str(source), sample_fps=args.sample_fps, threshold=args.threshold,
                             min_pixels=args.min_pixels, max_coverage=args.max_coverage)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps(report["summary"], ensure_ascii=False))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"motion_center: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
