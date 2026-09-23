#!/usr/bin/env python3
"""Render and live-verify a captioned audiogram from an audio excerpt."""

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
from pathlib import Path
from typing import Any


VERSION = "podcast_audiogram.v1"
SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[-2000:] or f"command failed: {command[0]}")
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def probe(path: Path) -> dict[str, Any]:
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))


def stream(data: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((item for item in data.get("streams", []) if item.get("codec_type") == kind), None)


def timestamp(value: str) -> float:
    match = SRT_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"invalid SRT timestamp: {value}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def check_srt(path: Path, duration: float) -> int:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip())
    if not blocks or not blocks[0]:
        raise ValueError("SRT has no cues")
    previous_end = 0.0
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2 or " --> " not in lines[0] or not any(line.strip() for line in lines[1:]):
            raise ValueError(f"SRT cue {index} needs a time range and text")
        start_text, end_text = lines[0].split(" --> ", 1)
        start, end = timestamp(start_text), timestamp(end_text)
        if start < previous_end - 0.001 or end <= start or end > duration + 0.05:
            raise ValueError(f"SRT cue {index} overlaps, reverses, or exceeds the selected excerpt")
        previous_end = end
    return len(blocks)


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


def verify_media(path: Path, duration: float, width: int, height: int, fps: int) -> dict[str, Any]:
    data = probe(path)
    video, audio = stream(data, "video"), stream(data, "audio")
    if not video or not audio:
        raise ValueError("output requires both video and audio")
    actual = float(data["format"]["duration"])
    if (video.get("codec_name"), video.get("pix_fmt"), video.get("width"), video.get("height")) != ("h264", "yuv420p", width, height):
        raise ValueError("output video codec, pixel format, or canvas differs")
    if audio.get("codec_name") != "aac":
        raise ValueError("output audio is not AAC")
    rate = video.get("avg_frame_rate", "0/1")
    numerator, denominator = map(int, rate.split("/"))
    if not denominator or abs(numerator / denominator - fps) > 0.01:
        raise ValueError("output frame rate differs")
    if abs(actual - duration) > max(0.15, 2 / fps):
        raise ValueError(f"output duration differs: {actual:.3f}s vs {duration:.3f}s")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    return {"duration": actual, "video_codec": "h264", "audio_codec": "aac", "width": width, "height": height, "fps": fps, "full_decode": "passed"}


def render(args: argparse.Namespace) -> dict[str, Any]:
    audio, cover, subtitles = map(safe_input, (args.audio, args.cover, args.subtitles))
    if cover.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("cover must be a PNG, JPEG, or WebP still image")
    inputs = [audio, cover, subtitles]
    output = safe_output(args.output, inputs, args.force)
    receipt = safe_output(args.receipt, inputs + [output], args.force)
    if receipt == output:
        raise ValueError("receipt and video output must differ")
    if output.suffix.lower() != ".mp4" or receipt.suffix.lower() != ".json":
        raise ValueError("output must be .mp4 and receipt must be .json")
    if not all(value > 0 and value % 2 == 0 for value in (args.width, args.height)):
        raise ValueError("canvas dimensions must be positive even integers")
    if args.fps < 15 or args.fps > 60:
        raise ValueError("fps must be 15–60")
    audio_data, cover_data = probe(audio), probe(cover)
    if stream(audio_data, "audio") is None or stream(cover_data, "video") is None:
        raise ValueError("audio needs an audio stream and cover needs an image/video stream")
    if stream(cover_data, "audio") is not None:
        raise ValueError("cover must be an image without audio")
    audio_duration = float(audio_data["format"]["duration"])
    if not math.isfinite(args.start) or args.start < 0 or args.start >= audio_duration:
        raise ValueError("start is outside audio")
    duration = args.duration if args.duration is not None else audio_duration - args.start
    if not math.isfinite(duration) or duration <= 0 or duration > 600 or args.start + duration > audio_duration + 0.01:
        raise ValueError("duration must fit audio and be at most 600 seconds")
    duration = round(duration, 3)
    cue_count = check_srt(subtitles, duration)
    width, height, fps = args.width, args.height, args.fps
    wave_width = int(width * 0.78) // 2 * 2
    wave_height = int(height * 0.09) // 2 * 2
    with tempfile.TemporaryDirectory(prefix="audiogram-") as temp_dir:
        # An ASCII temporary filename avoids FFmpeg filtergraph escaping issues with user paths.
        caption_file = Path(temp_dir) / "captions.srt"
        caption_file.write_bytes(subtitles.read_bytes())
        temporary = output.parent / f".{output.stem}.{os.getpid()}.tmp.mp4"
        if temporary.exists():
            raise FileExistsError(f"temporary output exists: {temporary}")
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x121824,setsar=1[base];"
            f"[1:a]asplit=2[aout][wavein];"
            f"[wavein]volume=4,showwaves=s={wave_width}x{wave_height}:mode=p2p:rate={fps}:colors=white,format=yuva420p[wave];"
            f"[base][wave]overlay=(W-w)/2:H*0.48:shortest=1[v0];"
            f"[v0]subtitles=filename={caption_file}:force_style='Fontsize=26,Alignment=2,MarginV=80,Outline=2'[vout]"
        )
        try:
            run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(fps), "-i", str(cover),
                 "-ss", str(args.start), "-t", str(duration), "-i", str(audio), "-filter_complex", graph,
                 "-map", "[vout]", "-map", "[aout]", "-t", str(duration), "-r", str(fps),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(temporary)])
            media = verify_media(temporary, duration, width, height, fps)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    payload = {"schema": VERSION, "inputs": {name: fingerprint(path) for name, path in zip(("audio", "cover", "subtitles"), inputs)},
               "settings": {"start": args.start, "duration": duration, "width": width, "height": height, "fps": fps, "caption_cues": cue_count},
               "output": fingerprint(output), "media": media}
    payload["id"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    descriptor, temp_name = tempfile.mkstemp(dir=receipt.parent, prefix=f".{receipt.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, receipt)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return payload


def verify(receipt_path: Path) -> dict[str, Any]:
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != VERSION or identifier != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest():
        raise ValueError("receipt schema or digest differs")
    for item in list(payload["inputs"].values()) + [payload["output"]]:
        path = safe_input(item["path"])
        if fingerprint(path) != item:
            raise ValueError(f"bound file changed: {path}")
    settings = payload["settings"]
    if check_srt(Path(payload["inputs"]["subtitles"]["path"]), settings["duration"]) != settings["caption_cues"]:
        raise ValueError("caption cue count differs")
    media = verify_media(Path(payload["output"]["path"]), settings["duration"], settings["width"], settings["height"], settings["fps"])
    if media != payload["media"]:
        raise ValueError("output media contract changed")
    return {"status": "ready_for_human_review", "output": payload["output"]["path"], "caption_cues": settings["caption_cues"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    render_parser = sub.add_parser("render", help="Make an MP4 and source-bound receipt")
    render_parser.add_argument("audio")
    render_parser.add_argument("cover")
    render_parser.add_argument("subtitles", help="SRT times must be relative to the selected excerpt")
    render_parser.add_argument("--start", type=float, default=0.0)
    render_parser.add_argument("--duration", type=float)
    render_parser.add_argument("--width", type=int, default=720)
    render_parser.add_argument("--height", type=int, default=1280)
    render_parser.add_argument("--fps", type=int, default=30)
    render_parser.add_argument("--output", required=True)
    render_parser.add_argument("--receipt", required=True)
    render_parser.add_argument("--force", action="store_true")
    verify_parser = sub.add_parser("verify", help="Recheck input/output hashes, media, and full decode")
    verify_parser.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = render(args) if args.command == "render" else verify(safe_input(args.receipt))
    except (ValueError, FileExistsError, OSError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result if args.command == "verify" else {"status": "ready_for_human_review", "output": result["output"]["path"], "receipt": args.receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
