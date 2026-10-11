#!/usr/bin/env python3
"""Render an ordered photo montage with gentle zoom, crossfades, and optional music."""

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


SCHEMA = "photo_montage.v1"


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[-2500:] or f"{command[0]} failed")
    return result.stdout


def safe_input(value: str, suffixes: set[str]) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError(f"missing, linked, or unsupported input: {path}")
    return path.resolve(strict=True)


def safe_output(value: str, inputs: list[Path], *, force: bool, suffix: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or path.suffix.lower() != suffix:
        raise ValueError(f"output must be a regular {suffix} path: {path}")
    path = path.resolve(strict=False)
    if path in inputs or (path.exists() and any(path.samefile(item) for item in inputs)):
        raise ValueError("output would overwrite an input")
    if path.exists() and not force:
        raise FileExistsError(f"output exists; pass --force to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def receipt_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams"]
    if count_frames:
        command.append("-count_frames")
    return json.loads(run([*command, "-of", "json", str(path)]))


def validate_settings(photos: list[Path], settings: dict[str, Any]) -> tuple[int, int, int]:
    if not 2 <= len(photos) <= 100 or len(set(photos)) != len(photos):
        raise ValueError("provide 2–100 distinct photos in playback order")
    width, height, fps = (settings[key] for key in ("width", "height", "fps"))
    if any(type(value) is not int for value in (width, height, fps)):
        raise ValueError("width, height, and fps must be integers")
    if width < 160 or height < 160 or width > 1920 or height > 1920 or width % 2 or height % 2:
        raise ValueError("width and height must be even integers from 160 to 1920")
    if not 24 <= fps <= 60:
        raise ValueError("fps must be 24–60")
    each, transition, zoom = (settings[key] for key in ("each", "transition", "zoom"))
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (each, transition, zoom)):
        raise ValueError("each, transition, and zoom must be finite numbers")
    if not 0.4 <= each <= 10 or not 0.1 <= transition <= min(0.6, each / 2):
        raise ValueError("each must be 0.4–10s; transition must be 0.1–0.6s and at most half of each")
    if not 1 <= zoom <= 1.3:
        raise ValueError("zoom must be 1.0–1.3")
    if settings["effect"] not in ("fade", "fadewhite"):
        raise ValueError("effect must be fade or fadewhite")
    each_frames = round(each * fps)
    transition_frames = round(transition * fps)
    if transition_frames < 2 or transition_frames >= each_frames:
        raise ValueError("transition must span at least two frames and be shorter than each photo")
    if len(photos) * each_frames / fps > 180:
        raise ValueError("montage duration must be at most 180 seconds")
    for photo in photos:
        data = probe(photo)
        streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
        if not streams or int(streams[0].get("width") or 0) < 2 or int(streams[0].get("height") or 0) < 2:
            raise ValueError(f"photo cannot be decoded: {photo}")
    return each_frames, transition_frames, len(photos) * each_frames


def filter_graph(count: int, settings: dict[str, Any], each_frames: int, transition_frames: int, *, music: bool) -> str:
    width, height, fps = (settings[key] for key in ("width", "height", "fps"))
    each_seconds = each_frames / fps
    transition_seconds = transition_frames / fps
    graph = []
    for index in range(count):
        frames = each_frames + (transition_frames if index < count - 1 else 0)
        zoom = settings["zoom"]
        z = f"{zoom:.6f}-({zoom - 1:.6f})*min(on/{max(frames - 1, 1)},1)"
        graph.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,"
            f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d=1:s={width}x{height}:fps={fps},trim=end_frame={frames},"
            f"setpts=PTS-STARTPTS,format=yuv420p[p{index}]"
        )
    current = "p0"
    for index in range(1, count):
        target = f"x{index}"
        graph.append(
            f"[{current}][p{index}]xfade=transition={settings['effect']}:"
            f"duration={transition_seconds:.6f}:offset={index * each_seconds:.6f}[{target}]"
        )
        current = target
    total_seconds = count * each_seconds
    # Frame-based fades cover the first/last 0.2 seconds without changing length.
    fade_frames = max(2, min(round(0.2 * fps), each_frames // 3))
    graph.append(
        f"[{current}]trim=end_frame={count * each_frames},"
        f"fade=t=in:s=0:n={fade_frames},"
        f"fade=t=out:s={count * each_frames - fade_frames}:n={fade_frames},"
        "format=yuv420p[vout]"
    )
    if music:
        graph.append(
            f"[{count}:a]atrim=duration={total_seconds:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={max(0, total_seconds - 1):.6f}:d=1[aout]"
        )
    return ";".join(graph)


def verify_media(path: Path, settings: dict[str, Any], frames: int, *, music: bool) -> dict[str, Any]:
    data = probe(path, count_frames=True)
    videos = [item for item in data.get("streams", []) if item.get("codec_type") == "video"]
    audios = [item for item in data.get("streams", []) if item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != int(music):
        raise ValueError("output stream count differs from the montage plan")
    video = videos[0]
    if (video.get("codec_name") != "h264" or int(video.get("width") or 0) != settings["width"]
            or int(video.get("height") or 0) != settings["height"]
            or int(video.get("nb_read_frames") or 0) != frames
            or video.get("avg_frame_rate") != f"{settings['fps']}/1"):
        raise ValueError("output video dimensions, codec, frame rate, or decoded frame count differ")
    duration = float(data.get("format", {}).get("duration") or 0)
    if abs(duration - frames / settings["fps"]) > max(0.08, 2 / settings["fps"]):
        raise ValueError("output duration differs from the photo timeline")
    if music and audios[0].get("codec_name") != "aac":
        raise ValueError("output audio is not AAC")
    if music and abs(float(audios[0].get("duration") or 0) - frames / settings["fps"]) > 0.1:
        raise ValueError("output audio does not cover the photo timeline")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"])
    if music:
        run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"])
    return {"frames": frames, "duration_seconds": duration, "full_decode": "passed"}


def render(args: argparse.Namespace) -> dict[str, Any]:
    photos = [safe_input(value, {".jpg", ".jpeg", ".png", ".webp"}) for value in args.photos]
    music = safe_input(args.music, {".mp3", ".m4a", ".wav", ".flac"}) if args.music else None
    settings = {"width": args.width, "height": args.height, "fps": args.fps,
                "each": args.each, "transition": args.transition, "zoom": args.zoom,
                "effect": args.effect}
    each_frames, transition_frames, frames = validate_settings(photos, settings)
    if music and not any(item.get("codec_type") == "audio" for item in probe(music).get("streams", [])):
        raise ValueError("music input has no audio stream")
    inputs = photos + ([music] if music else [])
    before = [fingerprint(item) for item in inputs]
    output = safe_output(args.output, inputs, force=args.force, suffix=".mp4")
    receipt = safe_output(args.receipt, [*inputs, output], force=args.force, suffix=".json")
    if output == receipt or (output.exists() and receipt.exists() and output.samefile(receipt)):
        raise ValueError("output and receipt must be distinct")
    descriptor, name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        command = ["ffmpeg", "-y", "-v", "error"]
        for index, photo in enumerate(photos):
            count = each_frames + (transition_frames if index < len(photos) - 1 else 0)
            command += ["-loop", "1", "-framerate", str(args.fps), "-t", f"{count / args.fps:.6f}", "-i", str(photo)]
        if music:
            command += ["-stream_loop", "-1", "-i", str(music)]
        graph = filter_graph(len(photos), settings, each_frames, transition_frames, music=bool(music))
        command += ["-filter_complex", graph, "-map", "[vout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(args.fps)]
        if music:
            command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(temporary)]
        run(command)
        media = verify_media(temporary, settings, frames, music=bool(music))
        if [fingerprint(item) for item in inputs] != before:
            raise ValueError("an input changed during rendering")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    payload: dict[str, Any] = {"schema": SCHEMA, "photos": before[:len(photos)],
                               "music": before[-1] if music else None, "settings": settings,
                               "output": fingerprint(output), "media": media}
    payload["id"] = receipt_id(payload)
    descriptor, name = tempfile.mkstemp(prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, receipt)
    finally:
        Path(name).unlink(missing_ok=True)
    return {"output": str(output), "receipt": str(receipt), "frames": frames, "duration_seconds": media["duration_seconds"]}


def verify(value: str) -> dict[str, Any]:
    receipt = safe_input(value, {".json"})
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    photos = [safe_input(item["path"], {".jpg", ".jpeg", ".png", ".webp"}) for item in payload["photos"]]
    music = safe_input(payload["music"]["path"], {".mp3", ".m4a", ".wav", ".flac"}) if payload["music"] else None
    if [fingerprint(item) for item in photos] != payload["photos"]:
        raise ValueError("photo bytes or playback order changed")
    if music and fingerprint(music) != payload["music"]:
        raise ValueError("music bytes changed")
    _, _, frames = validate_settings(photos, payload["settings"])
    if music and not any(item.get("codec_type") == "audio" for item in probe(music).get("streams", [])):
        raise ValueError("music input has no audio stream")
    output = safe_input(payload["output"]["path"], {".mp4"})
    if fingerprint(output) != payload["output"]:
        raise ValueError("output bytes changed")
    if verify_media(output, payload["settings"], frames, music=bool(music)) != payload["media"]:
        raise ValueError("output media contract changed")
    return {"status": "ready_for_human_review", "output": str(output), "frames": frames}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("render", help="Render photos in the given order and write a source receipt")
    make.add_argument("photos", nargs="+")
    make.add_argument("--music")
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--width", type=int, default=1080)
    make.add_argument("--height", type=int, default=1920)
    make.add_argument("--fps", type=int, default=30)
    make.add_argument("--each", type=float, default=1.0, help="Seconds between photo onsets")
    make.add_argument("--transition", type=float, default=0.3, help="Overlap duration in seconds")
    make.add_argument("--zoom", type=float, default=1.08, help="Initial zoom, easing to 1.0")
    make.add_argument("--effect", choices=("fade", "fadewhite"), default="fade")
    make.add_argument("--force", action="store_true")
    check = commands.add_parser("verify", help="Verify source, output, frame count, and full decode")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = render(args) if args.command == "render" else verify(args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"photo montage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
