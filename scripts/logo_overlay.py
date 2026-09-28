#!/usr/bin/env python3
"""Add a bounded PNG logo to a reviewed MP4 and verify its source-bound receipt."""

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


SCHEMA = "logo_overlay.v1"
CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[-2000:] or f"{command[0]} failed")
    return result.stdout


def fingerprint(path: Path) -> dict[str, Any]:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return {"path": str(path), "sha256": sha.hexdigest(), "size_bytes": path.stat().st_size}


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
    return json.loads(run(command + ["-of", "json", str(path)]))


def streams(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in data.get("streams", []) if item.get("codec_type") == kind]


def inspect_inputs(source: Path, logo: Path, width_fraction: float, opacity: float,
                   margin: int, corner: str) -> dict[str, Any]:
    if source.suffix.lower() != ".mp4" or logo.suffix.lower() != ".png":
        raise ValueError("source must be .mp4 and logo must be .png")
    if not math.isfinite(width_fraction) or not 0.05 <= width_fraction <= 0.4:
        raise ValueError("width fraction must be 0.05–0.4")
    if not math.isfinite(opacity) or not 0 < opacity <= 1:
        raise ValueError("opacity must be greater than 0 and at most 1")
    if margin < 0 or corner not in CORNERS:
        raise ValueError("margin must be nonnegative and corner must be valid")
    src = probe(source, count_frames=True)
    logo_data = probe(logo)
    video = streams(src, "video")
    audio = streams(src, "audio")
    logo_video = streams(logo_data, "video")
    if len(video) != 1 or len(audio) > 1 or len(src.get("streams", [])) != len(video) + len(audio):
        raise ValueError("source must have one video, at most one audio, and no other streams")
    if len(logo_video) != 1 or len(logo_data.get("streams", [])) != 1 or logo_video[0].get("codec_name") != "png":
        raise ValueError("logo must contain one PNG image")
    vw, vh = int(video[0].get("width") or 0), int(video[0].get("height") or 0)
    lw, lh = int(logo_video[0].get("width") or 0), int(logo_video[0].get("height") or 0)
    duration = float(src.get("format", {}).get("duration") or 0)
    frame_count = int(video[0].get("nb_read_frames") or 0)
    if (min(vw, vh, lw, lh) <= 0 or vw % 2 or vh % 2 or frame_count <= 0
            or not math.isfinite(duration) or duration <= 0):
        raise ValueError("source needs even dimensions and positive duration; logo needs positive dimensions")
    target_width = max(2, round(vw * width_fraction / 2) * 2)
    target_height = round(target_width * lh / lw)
    if target_width + 2 * margin > vw or target_height + 2 * margin > vh:
        raise ValueError("scaled logo and margins do not fit the video frame")
    return {"width": vw, "height": vh, "duration": duration, "frames": frame_count,
            "logo_width": target_width,
            "audio": bool(audio), "audio_codec": audio[0].get("codec_name") if audio else None}


def verify_media(output: Path, source_info: dict[str, Any]) -> dict[str, Any]:
    data = probe(output, count_frames=True)
    video = streams(data, "video")
    audio = streams(data, "audio")
    if len(video) != 1 or len(audio) != int(source_info["audio"]) or len(data.get("streams", [])) != 1 + len(audio):
        raise ValueError("output stream layout differs from source")
    if (video[0].get("codec_name") != "h264" or video[0].get("pix_fmt") != "yuv420p"
            or int(video[0].get("width") or 0) != source_info["width"]
            or int(video[0].get("height") or 0) != source_info["height"]):
        raise ValueError("output video contract differs")
    if audio and audio[0].get("codec_name") != source_info["audio_codec"]:
        raise ValueError("output audio codec differs")
    duration = float(data.get("format", {}).get("duration") or 0)
    if abs(duration - source_info["duration"]) > 0.15:
        raise ValueError("output duration differs from source")
    if int(video[0].get("nb_read_frames") or 0) != source_info["frames"]:
        raise ValueError("output frame count differs from source")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-map", "0:v:0", "-f", "null", "-"])
    if audio:
        run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-map", "0:a:0", "-f", "null", "-"])
    return {"codec": "h264", "pixel_format": "yuv420p", "width": source_info["width"],
            "height": source_info["height"], "duration": duration,
            "frames": int(video[0].get("nb_read_frames") or 0), "audio_codec": source_info["audio_codec"],
            "full_decode": "passed"}


def receipt_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def render(args: argparse.Namespace) -> dict[str, Any]:
    source, logo = safe_input(args.source), safe_input(args.logo)
    if source == logo or source.samefile(logo):
        raise ValueError("source and logo must be distinct")
    info = inspect_inputs(source, logo, args.width_fraction, args.opacity, args.margin, args.corner)
    original_source, original_logo = fingerprint(source), fingerprint(logo)
    output = safe_output(args.output, [source, logo], args.force)
    receipt = safe_output(args.receipt, [source, logo, output], args.force)
    if output == receipt or (output.exists() and receipt.exists() and output.samefile(receipt)):
        raise ValueError("video output and receipt must be distinct")
    if output.suffix.lower() != ".mp4" or receipt.suffix.lower() != ".json":
        raise ValueError("output must be .mp4 and receipt must be .json")
    x = str(args.margin) if args.corner.endswith("left") else f"W-w-{args.margin}"
    y = str(args.margin) if args.corner.startswith("top") else f"H-h-{args.margin}"
    graph = (f"[1:v]format=rgba,scale={info['logo_width']}:-1:flags=lanczos,"
             f"colorchannelmixer=aa={args.opacity}[wm];"
             f"[0:v][wm]overlay={x}:{y}:shortest=1:format=auto,format=yuv420p[v]")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-loop", "1", "-i", str(logo),
             "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264",
             "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-fps_mode", "passthrough", "-movflags", "+faststart", str(temporary)])
        media = verify_media(temporary, info)
        if fingerprint(source) != original_source or fingerprint(logo) != original_logo:
            raise ValueError("source or logo changed during render")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    settings = {"width_fraction": args.width_fraction, "opacity": args.opacity,
                "margin": args.margin, "corner": args.corner}
    payload: dict[str, Any] = {"schema": SCHEMA, "source": original_source, "logo": original_logo,
                               "settings": settings, "source_media": info,
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
    receipt = safe_input(str(receipt_path))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    source, logo, output = (safe_input(payload[name]["path"]) for name in ("source", "logo", "output"))
    if any(fingerprint(path) != payload[name] for name, path in (("source", source), ("logo", logo), ("output", output))):
        raise ValueError("bound source, logo or output changed")
    info = inspect_inputs(source, logo, **payload["settings"])
    if info != payload["source_media"] or verify_media(output, info) != payload["media"]:
        raise ValueError("media contract changed")
    return {"status": "ready_for_human_review", "output": str(output), "frames": payload["media"]["frames"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("render", help="Overlay a PNG logo and write a source-bound receipt")
    make.add_argument("source")
    make.add_argument("logo")
    make.add_argument("--width-fraction", type=float, default=0.15, help="Logo width / video width (0.05–0.4)")
    make.add_argument("--opacity", type=float, default=1.0)
    make.add_argument("--margin", type=int, default=24, help="Inset in output pixels")
    make.add_argument("--corner", choices=sorted(CORNERS), default="bottom-right")
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Recheck bound inputs, output and full decode")
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
