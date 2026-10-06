#!/usr/bin/env python3
"""Inspect persistent in-frame black bars and suggest a conservative crop filter."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

VERSION = "border_crop.v1"
SAMPLE = re.compile(r"\bt:([\d.]+).*?\bcrop=(\d+):(\d+):(\d+):(\d+)")


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def source_file(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source must be a regular file without symlink: {path}")
    return path.resolve()


def geometry(path: Path) -> tuple[int, int]:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height", "-of", "json", str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or "ffprobe failed")[-1000:])
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ValueError("source has no video stream")
    width, height = int(streams[0]["width"]), int(streams[0]["height"])
    if width < 2 or height < 2:
        raise ValueError("invalid video dimensions")
    return width, height


def parse_samples(stderr: str, width: int, height: int) -> list[dict]:
    samples = []
    for timestamp, w, h, x, y in SAMPLE.findall(stderr):
        time = float(timestamp)
        rect = tuple(map(int, (w, h, x, y)))
        if not math.isfinite(time) or time < 0 or any(n < 0 for n in rect):
            raise ValueError("invalid cropdetect sample")
        if rect[0] == 0 or rect[1] == 0 or rect[0] + rect[2] > width or rect[1] + rect[3] > height:
            raise ValueError("cropdetect rectangle lies outside the video")
        samples.append({"time_seconds": time, "crop": list(rect)})
    if not samples:
        raise ValueError("FFmpeg produced no cropdetect samples")
    if any(b["time_seconds"] < a["time_seconds"] for a, b in zip(samples, samples[1:])):
        raise ValueError("cropdetect timestamps are out of order")
    return samples


def decide(samples: list[dict], width: int, height: int,
           min_border: int, min_consensus: float) -> dict:
    if not samples:
        raise ValueError("no samples")
    rects = [tuple(row["crop"]) for row in samples]
    chosen, votes = Counter(rects).most_common(1)[0]
    count = len(samples)
    thirds = []
    for index in range(3):
        subset = rects[index * count // 3:(index + 1) * count // 3]
        thirds.append(round(sum(rect == chosen for rect in subset) / len(subset), 4) if subset else 0.0)
    w, h, x, y = chosen
    borders = {"left": x, "right": width - x - w, "top": y, "bottom": height - y - h}
    significant = any(value >= min_border for value in borders.values())
    consistent = count >= 3 and votes / count >= min_consensus and all(
        ratio >= min_consensus for ratio in thirds)
    status = "ready" if significant and consistent else ("no_crop" if not significant and consistent else "review")
    return {"status": status, "crop": list(chosen) if status == "ready" else None,
            "filter": f"crop={w}:{h}:{x}:{y}" if status == "ready" else None,
            "candidate": list(chosen), "borders_px": borders, "sample_count": count,
            "matching_samples": votes, "consensus": round(votes / count, 4),
            "third_consensus": thirds,
            "reason": ("stable black border candidate; inspect representative frames before applying"
                       if status == "ready" else "no significant persistent border"
                       if status == "no_crop" else "crop measurements vary or too few frames were sampled")}


def analyze(source: str, *, sample_fps: float = 2, limit: int = 24,
            min_border: int = 8, min_consensus: float = 0.8) -> dict:
    if not math.isfinite(sample_fps) or not 0.5 <= sample_fps <= 5:
        raise ValueError("sample-fps must be 0.5..5")
    if not 0 <= limit <= 64 or not 2 <= min_border <= 256:
        raise ValueError("limit must be 0..64 and min-border 2..256")
    if not math.isfinite(min_consensus) or not 0.5 <= min_consensus <= 1:
        raise ValueError("min-consensus must be 0.5..1")
    path = source_file(source)
    before = fingerprint(path)
    width, height = geometry(path)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
               "-i", str(path), "-map", "0:v:0", "-vf",
               f"fps={sample_fps:g},cropdetect=limit={limit}:round=2:reset=1:skip=0",
               "-an", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or "FFmpeg cropdetect failed")[-1200:])
    samples = parse_samples(result.stderr, width, height)
    if fingerprint(path) != before:
        raise ValueError("source changed during analysis")
    settings = {"sample_fps": sample_fps, "limit": limit, "min_border": min_border,
                "min_consensus": min_consensus}
    return {"version": VERSION, "source": before, "dimensions": [width, height],
            "settings": settings, "samples": samples,
            "decision": decide(samples, width, height, min_border, min_consensus)}


def verify(report: str) -> dict:
    path = source_file(report)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ValueError("unknown border-crop report version")
    settings = data["settings"]
    fresh = analyze(data["source"]["path"], **settings)
    if fresh != data:
        raise ValueError("report or source changed since analysis")
    return {"status": "verified", "decision": fresh["decision"]["status"],
            "filter": fresh["decision"]["filter"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("analyze", help="Analyze in-frame black borders")
    scan.add_argument("source")
    scan.add_argument("--output", required=True)
    scan.add_argument("--sample-fps", type=float, default=2)
    scan.add_argument("--limit", type=int, default=24)
    scan.add_argument("--min-border", type=int, default=8)
    scan.add_argument("--min-consensus", type=float, default=0.8)
    check = sub.add_parser("verify", help="Reanalyze source and verify every report field")
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
            report = analyze(str(source), sample_fps=args.sample_fps, limit=args.limit,
                             min_border=args.min_border, min_consensus=args.min_consensus)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps(report["decision"], ensure_ascii=False))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"border_crop: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
