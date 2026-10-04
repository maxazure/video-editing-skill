#!/usr/bin/env python3
"""Align existing keyframes with nearby timestamped speech for source review."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from transcript_lookup import load_segments


VERSION = "visual_transcript_index.v1"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file without symlinks: {path}")
    return path.resolve()


def make_index(keyframes_path: Path, transcript_path: Path, *, radius: float = 3.0,
               max_segments: int = 8) -> dict[str, Any]:
    keyframes_path = regular_file(keyframes_path, "keyframe metadata")
    transcript_path = regular_file(transcript_path, "transcript")
    if not math.isfinite(radius) or not 0.1 <= radius <= 30:
        raise ValueError("radius must be 0.1–30 seconds")
    if not 1 <= max_segments <= 12:
        raise ValueError("max-segments must be 1–12")
    metadata = json.loads(keyframes_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("keyframes"), list):
        raise ValueError("keyframe metadata needs keyframes[]")
    frames = metadata["keyframes"]
    if not 1 <= len(frames) <= 64:
        raise ValueError("keyframe count must be 1–64")
    duration = float(metadata["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid video duration")
    video = regular_file(Path(metadata["video"]), "source video")
    segments, transcript_sha = load_segments(transcript_path)
    if any(part["end"] > duration + 0.25 for part in segments):
        raise ValueError("transcript extends beyond source video")
    rows: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    previous_time = -1.0
    for position, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            raise ValueError(f"keyframe {position} must be an object")
        stamp = float(frame["timestamp"])
        if not math.isfinite(stamp) or stamp < 0 or stamp > duration or stamp <= previous_time:
            raise ValueError(f"keyframe {position} has invalid or unordered timestamp")
        previous_time = stamp
        picture = regular_file(Path(frame["path"]), f"keyframe {position} image")
        if picture in seen_paths:
            raise ValueError("duplicate keyframe image")
        seen_paths.add(picture)
        start, end = max(0.0, stamp - radius), min(duration, stamp + radius)
        nearby = [part for part in segments if part["start"] < end and part["end"] > start]
        rows.append({
            "timestamp": stamp,
            "image": str(picture),
            "image_sha256": digest(picture),
            "speech_window": {"start": start, "end": end},
            "speech_total": len(nearby),
            "speech_truncated": len(nearby) > max_segments,
            "speech": [
                {"id": part["id"], "start": part["start"], "end": part["end"],
                 "text": part["text"][:300]}
                for part in nearby[:max_segments]
            ],
        })
    return {
        "version": VERSION,
        "video": str(video), "video_sha256": digest(video), "duration": duration,
        "keyframes": str(keyframes_path), "keyframes_sha256": digest(keyframes_path),
        "transcript": str(transcript_path), "transcript_sha256": transcript_sha,
        "radius": radius, "max_segments": max_segments, "frames": rows,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Visual transcript index", "", f"Source: `{report['video']}`", "",
             "Frames are samples. Speech is from the nearby time window, not a claim about what the frame shows.", ""]
    for row in report["frames"]:
        image_url = quote(row["image"], safe="/")
        lines += [f"## {row['timestamp']:.3f}s", "", f"![Frame at {row['timestamp']:.3f}s]({image_url})", "",
                  f"Nearby speech ({row['speech_window']['start']:.3f}–{row['speech_window']['end']:.3f}s):", ""]
        if not row["speech"]:
            lines.append("- No transcript segment in this window.")
        for part in row["speech"]:
            speech = html.escape(part["text"].replace("\n", " ").replace("\r", " "))
            lines.append(f"- {part['start']:.3f}–{part['end']:.3f}s [{part['id']}]: {speech}")
        if row["speech_truncated"]:
            lines.append(f"- Showing {len(row['speech'])} of {row['speech_total']} segments; narrow the window or read the transcript.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build JSON and readable Markdown from existing keyframes and transcript")
    build.add_argument("keyframes", type=Path, help="JSON from extract_keyframes.py")
    build.add_argument("transcript", type=Path, help="JSON with segments[start,end,text]")
    build.add_argument("--radius", type=float, default=3.0)
    build.add_argument("--max-segments", type=int, default=8)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--markdown", type=Path, required=True)
    verify = commands.add_parser("verify", help="Rebuild index from live sources and compare exact contents")
    verify.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            report = make_index(args.keyframes, args.transcript, radius=args.radius, max_segments=args.max_segments)
            outputs = [args.output, args.markdown]
            protected = {Path(report[name]) for name in ("video", "keyframes", "transcript")}
            protected.update(Path(row["image"]) for row in report["frames"])
            if any(path.exists() or path.is_symlink() for path in outputs):
                raise ValueError("output already exists")
            if outputs[0].resolve() == outputs[1].resolve() or any(path.resolve() in protected for path in outputs):
                raise ValueError("output paths collide with each other or an input")
            notes = markdown(report)
            report["markdown_path"] = str(args.markdown.resolve())
            report["markdown_sha256"] = hashlib.sha256(notes.encode("utf-8")).hexdigest()
            for path in outputs:
                path.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            args.markdown.write_text(notes, encoding="utf-8")
        else:
            stored = json.loads(regular_file(args.report, "report").read_text(encoding="utf-8"))
            if not isinstance(stored, dict) or stored.get("version") != VERSION:
                raise ValueError("unsupported report version")
            current = make_index(Path(stored["keyframes"]), Path(stored["transcript"]),
                                 radius=stored["radius"], max_segments=stored["max_segments"])
            notes = markdown(current)
            notes_path = regular_file(Path(stored["markdown_path"]), "Markdown index")
            current["markdown_path"] = str(notes_path)
            current["markdown_sha256"] = hashlib.sha256(notes.encode("utf-8")).hexdigest()
            if current != stored or notes_path.read_bytes() != notes.encode("utf-8"):
                raise ValueError("report or source bytes changed")
            print("visual transcript index: verified")
    except (OSError, UnicodeError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"visual_transcript_index: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
