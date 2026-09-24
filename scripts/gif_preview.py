#!/usr/bin/env python3
"""Render a short, palette-optimized GIF preview and verify its source receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "gif_preview.v1"


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[-2000:] or f"{command[0]} failed")
    return result.stdout


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": digest(path), "size_bytes": path.stat().st_size}


def safe_input(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input is missing or a symlink: {path}")
    return path.resolve()


def safe_output(value: str, inputs: list[Path], force: bool) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError(f"output is a symlink: {path}")
    path = path.resolve()
    if path in inputs or (path.exists() and any(path.samefile(item) for item in inputs)):
        raise ValueError("output would overwrite an input")
    if path.exists() and not force:
        raise FileExistsError(f"output exists; pass --force to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams"]
    if count_frames:
        command.append("-count_frames")
    command += ["-of", "json", str(path)]
    return json.loads(run(command))


def video_stream(data: dict[str, Any]) -> dict[str, Any]:
    video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("source or output has no video stream")
    return video


def source_duration(path: Path) -> float:
    data = probe(path)
    video_stream(data)
    duration = float(data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source video has no finite positive duration")
    return duration


def validate_settings(source: Path, start: float, duration: float, width: int, fps: int) -> None:
    total = source_duration(source)
    if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
        raise ValueError("start and duration must be finite; start >= 0 and duration > 0")
    if duration > 10 or start + duration > total + 0.01:
        raise ValueError("excerpt must fit the source and be at most 10 seconds")
    if width < 64 or width > 640 or width % 2:
        raise ValueError("width must be an even integer from 64 to 640")
    if fps < 5 or fps > 20:
        raise ValueError("fps must be 5–20")


def verify_media(path: Path, duration: float, width: int, fps: int) -> dict[str, Any]:
    data = probe(path, count_frames=True)
    video = video_stream(data)
    if video.get("codec_name") != "gif" or int(video.get("width") or 0) != width:
        raise ValueError("output is not a GIF at the requested width")
    if any(item.get("codec_type") == "audio" for item in data.get("streams", [])):
        raise ValueError("GIF output unexpectedly contains audio")
    height = int(video.get("height") or 0)
    frames = int(video.get("nb_read_frames") or 0)
    if height < 2 or frames < 2 or abs(frames - duration * fps) > 2:
        raise ValueError("GIF dimensions or frame count differ from the excerpt")
    actual_duration = float(data.get("format", {}).get("duration") or 0)
    if abs(actual_duration - duration) > max(0.2, 2 / fps):
        raise ValueError("GIF duration differs from the excerpt")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"])
    return {"codec": "gif", "width": width, "height": height, "frames": frames,
            "duration": actual_duration, "full_decode": "passed"}


def receipt_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def render(args: argparse.Namespace) -> dict[str, Any]:
    source = safe_input(args.source)
    validate_settings(source, args.start, args.duration, args.width, args.fps)
    source_info = fingerprint(source)
    output = safe_output(args.output, [source], args.force)
    receipt = safe_output(args.receipt, [source, output], args.force)
    if output == receipt or (output.exists() and receipt.exists() and output.samefile(receipt)):
        raise ValueError("GIF output and receipt must be distinct")
    if output.suffix.lower() != ".gif" or receipt.suffix.lower() != ".json":
        raise ValueError("output must be .gif and receipt must be .json")
    settings = {"start": round(args.start, 6), "duration": round(args.duration, 6),
                "width": args.width, "fps": args.fps}
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".gif", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        graph = (f"fps={args.fps},scale={args.width}:-2:flags=lanczos,split[p][q];"
                 "[p]palettegen=stats_mode=full[pal];"
                 "[q][pal]paletteuse=dither=bayer:bayer_scale=5[out]")
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(args.start), "-t", str(args.duration),
             "-i", str(source), "-filter_complex", graph, "-map", "[out]", "-an", "-f", "gif", str(temporary)])
        media = verify_media(temporary, args.duration, args.width, args.fps)
        if temporary.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("GIF exceeds 20 MiB; shorten excerpt or lower width/fps")
        if fingerprint(source) != source_info:
            raise ValueError("source changed during GIF render")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    payload: dict[str, Any] = {"schema": SCHEMA, "source": source_info, "settings": settings,
                               "output": fingerprint(output), "media": media}
    payload["id"] = receipt_id(payload)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, receipt)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return payload


def verify(receipt_path: Path) -> dict[str, Any]:
    receipt_path = safe_input(str(receipt_path))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    source = safe_input(payload["source"]["path"])
    output = safe_input(payload["output"]["path"])
    if fingerprint(source) != payload["source"] or fingerprint(output) != payload["output"]:
        raise ValueError("bound source or GIF changed")
    settings = payload["settings"]
    validate_settings(source, settings["start"], settings["duration"], settings["width"], settings["fps"])
    if verify_media(output, settings["duration"], settings["width"], settings["fps"]) != payload["media"]:
        raise ValueError("GIF media contract changed")
    if output.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("GIF exceeds 20 MiB")
    return {"status": "ready_for_human_review", "output": str(output), "frames": payload["media"]["frames"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("render", help="Render GIF and source-bound receipt")
    make.add_argument("source", help="Source video with a video stream")
    make.add_argument("--start", type=float, required=True)
    make.add_argument("--duration", type=float, required=True)
    make.add_argument("--width", type=int, default=480)
    make.add_argument("--fps", type=int, default=12)
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Check source, output, media contract and receipt")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = render(args) if args.command == "render" else verify(Path(args.receipt))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, KeyError, TypeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
