#!/usr/bin/env python3
"""Package a reviewed SDR MP4 as a source-bound, single-rendition HLS VOD."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMA = "hls_vod.v1"
SEGMENT = re.compile(r"seg_(\d{4,})\.ts\Z")


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(" ".join((result.stderr or result.stdout).split())[-2000:])
    return result.stdout


def fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error"]
    if count_frames:
        command.append("-count_frames")
    command += ["-show_format", "-show_streams", "-of", "json", str(path)]
    return json.loads(run(command))


def source_contract(source: Path) -> dict[str, Any]:
    if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".mp4":
        raise ValueError("source must be a regular MP4 file, not a symlink")
    data = probe(source)
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1 or len(audio) > 1 or len(streams) != len(video) + len(audio):
        raise ValueError("source needs one video, at most one audio, and no other streams")
    v = video[0]
    if v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p":
        raise ValueError("source must be an SDR H.264/yuv420p MP4 master")
    if audio and audio[0].get("codec_name") != "aac":
        raise ValueError("source audio must be AAC")
    if (v.get("color_transfer") in {"smpte2084", "arib-std-b67"} or
            v.get("color_space") in {"bt2020nc", "bt2020c"} or
            v.get("color_primaries") == "bt2020"):
        raise ValueError("HDR/BT.2020 source needs an SDR master before HLS packaging")
    if (v.get("tags") or {}).get("rotate") or any(s.get("rotation") for s in v.get("side_data_list") or []):
        raise ValueError("rotated source needs a display-orientation master")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration <= 4 * 3600:
        raise ValueError("source duration must be finite and at most 4 hours")
    try:
        fps = float(Fraction(v["avg_frame_rate"]))
        nominal = float(Fraction(v["r_frame_rate"]))
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("source needs a valid constant frame rate") from exc
    if not 1 <= fps <= 60 or abs(fps - nominal) > 0.01:
        raise ValueError("source needs a constant frame rate between 1 and 60 fps")
    if int(v.get("width") or 0) % 2 or int(v.get("height") or 0) % 2:
        raise ValueError("source dimensions must be even")
    return {"duration": duration, "fps": fps, "width": int(v["width"]),
            "height": int(v["height"]), "has_audio": bool(audio)}


def playlist_segments(directory: Path) -> tuple[list[str], float]:
    playlist = directory / "index.m3u8"
    if playlist.is_symlink() or not playlist.is_file():
        raise ValueError("HLS playlist is missing or unsafe")
    lines = playlist.read_text(encoding="utf-8").splitlines()
    allowed_prefixes = ("#EXT-X-VERSION:", "#EXT-X-TARGETDURATION:",
                        "#EXT-X-MEDIA-SEQUENCE:", "#EXTINF:")
    required = {"#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-ENDLIST"}
    if not required.issubset(lines):
        raise ValueError("playlist is missing VOD, independent-segment, or end markers")
    if any(line.startswith("#") and line not in required and not line.startswith(allowed_prefixes)
           for line in lines):
        raise ValueError("playlist contains unsupported directives")
    names = [line for line in lines if line and not line.startswith("#")]
    durations = []
    for line in lines:
        if line.startswith("#EXTINF:"):
            try:
                durations.append(float(line[8:].split(",", 1)[0]))
            except ValueError as exc:
                raise ValueError("invalid EXTINF duration") from exc
    if not names or len(names) != len(durations) or any(not math.isfinite(d) or d <= 0 for d in durations):
        raise ValueError("playlist segments or durations are invalid")
    if names != [f"seg_{i:04d}.ts" for i in range(len(names))]:
        raise ValueError("playlist must contain only consecutive local TS segments")
    if any(not SEGMENT.fullmatch(name) for name in names):
        raise ValueError("unsafe segment URI")
    if {p.name for p in directory.iterdir()} != {"index.m3u8", *names}:
        raise ValueError("HLS directory contains missing or unexpected files")
    for name in names:
        segment = directory / name
        if segment.is_symlink() or not segment.is_file() or segment.stat().st_size == 0:
            raise ValueError(f"HLS segment is missing or unsafe: {name}")
    return names, sum(durations)


def verify_media(source: Path, directory: Path) -> dict[str, Any]:
    contract = source_contract(source)
    names, playlist_duration = playlist_segments(directory)
    if abs(playlist_duration - contract["duration"]) > 0.15:
        raise ValueError("HLS playlist duration differs from source")
    playlist = directory / "index.m3u8"
    data = probe(playlist, count_frames=True)
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if (len(video) != 1 or video[0].get("codec_name") != "h264" or
            len(audio) != int(contract["has_audio"]) or
            (audio and audio[0].get("codec_name") != "aac") or len(streams) != 1 + len(audio)):
        raise ValueError("HLS video/audio stream contract differs")
    if (video[0].get("pix_fmt") != "yuv420p" or
            int(video[0].get("width") or 0) != contract["width"] or
            int(video[0].get("height") or 0) != contract["height"] or
            abs(float(Fraction(video[0]["avg_frame_rate"])) - contract["fps"]) > 0.01):
        raise ValueError("HLS image dimensions, pixel format, or frame rate differ")
    source_video = next(s for s in probe(source, count_frames=True)["streams"]
                        if s.get("codec_type") == "video")
    source_frames = int(source_video.get("nb_read_frames") or 0)
    output_frames = int(video[0].get("nb_read_frames") or 0)
    if not source_frames or source_frames != output_frames:
        raise ValueError("HLS decoded video frame count differs from source")
    for name in names:
        packet = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
                                 "-show_entries", "packet=flags", "-of", "json", str(directory / name)]))
        if not packet.get("packets") or "K" not in packet["packets"][0].get("flags", ""):
            raise ValueError(f"segment does not start with a key frame: {name}")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(playlist), "-map", "0:v:0",
         "-map", "0:a:0?", "-f", "null", "-"])
    return {"segment_count": len(names), "duration": round(playlist_duration, 6),
            "video_frames": output_frames, "has_audio": bool(audio), "full_decode": "passed"}


def files_receipt(directory: Path) -> dict[str, dict[str, Any]]:
    names, _ = playlist_segments(directory)
    return {name: fingerprint(directory / name) for name in ["index.m3u8", *names]}


def receipt_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def package(source_arg: str, output_arg: str, receipt_arg: str, segment_seconds: int) -> dict[str, Any]:
    source = Path(source_arg).expanduser()
    contract = source_contract(source)
    if not 2 <= segment_seconds <= 10:
        raise ValueError("segment seconds must be between 2 and 10")
    source = source.resolve()
    output_input = Path(output_arg).expanduser()
    receipt_input = Path(receipt_arg).expanduser()
    if output_input.is_symlink() or receipt_input.is_symlink():
        raise ValueError("output directory or receipt is a symlink")
    output = output_input.resolve()
    receipt = receipt_input.resolve()
    if output.exists() or receipt.exists() or output == source or receipt == source or receipt.is_relative_to(output):
        raise ValueError("output directory and external receipt must be new and distinct from source")
    if receipt.suffix.lower() != ".json":
        raise ValueError("receipt must be .json")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    source_info = fingerprint(source)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        gop = max(1, round(contract["fps"] * segment_seconds))
        command = ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-map", "0:v:0"]
        if contract["has_audio"]:
            command += ["-map", "0:a:0"]
        command += ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
                    "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
                    "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})"]
        if contract["has_audio"]:
            command += ["-c:a", "aac", "-b:a", "128k"]
        command += ["-f", "hls", "-hls_time", str(segment_seconds), "-hls_playlist_type", "vod",
                    "-hls_flags", "independent_segments", "-hls_segment_filename",
                    str(temporary / "seg_%04d.ts"), str(temporary / "index.m3u8")]
        run(command)
        media = verify_media(source, temporary)
        if fingerprint(source) != source_info:
            raise ValueError("source changed during HLS packaging")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    payload: dict[str, Any] = {"schema": SCHEMA, "source": {"path": str(source), **source_info},
                               "output_dir": str(output), "segment_seconds": segment_seconds,
                               "files": files_receipt(output), "media": media}
    payload["id"] = receipt_id(payload)
    fd, name = tempfile.mkstemp(prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, receipt)
    finally:
        Path(name).unlink(missing_ok=True)
    return payload


def verify(receipt_arg: str) -> dict[str, Any]:
    receipt = Path(receipt_arg).expanduser()
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError("receipt is missing or unsafe")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    claimed = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or claimed != receipt_id(payload):
        raise ValueError("receipt digest differs")
    source = Path(payload["source"]["path"])
    directory = Path(payload["output_dir"])
    if directory.is_symlink() or not directory.is_dir() or source.is_symlink():
        raise ValueError("source or output directory is missing or unsafe")
    if fingerprint(source) != {k: payload["source"][k] for k in ("sha256", "size_bytes")}:
        raise ValueError("source changed")
    if files_receipt(directory) != payload["files"]:
        raise ValueError("HLS files changed")
    if verify_media(source, directory) != payload["media"]:
        raise ValueError("HLS media contract changed")
    return {"status": "verified", "segment_count": payload["media"]["segment_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("package", help="Create a new HLS VOD directory and external receipt")
    make.add_argument("source")
    make.add_argument("--output-dir", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--segment-seconds", type=int, default=6)
    check = commands.add_parser("verify", help="Recheck the source, playlist, segments and full decode")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = (package(args.source, args.output_dir, args.receipt, args.segment_seconds)
                  if args.command == "package" else verify(args.receipt))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"hls_vod: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
