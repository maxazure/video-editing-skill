#!/usr/bin/env python3
"""Mux a reviewed SRT as a switchable MP4 subtitle track without re-encoding A/V."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "soft_subtitles.v1"
TIME = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[-2000:] or f"{command[0]} failed")
    return result.stdout


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": digest(path), "size_bytes": path.stat().st_size}


def input_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input is missing or a symlink: {path}")
    return path.resolve()


def output_path(value: str, inputs: list[Path], force: bool) -> Path:
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


def seconds(value: str) -> float:
    match = TIME.fullmatch(value)
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value}")
    hours, minutes, secs, millis = map(int, match.groups())
    if minutes >= 60 or secs >= 60:
        raise ValueError(f"invalid SRT timestamp: {value}")
    return hours * 3600 + minutes * 60 + secs + millis / 1000


def parse_srt(content: str, duration: float) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for number, block in enumerate(re.split(r"\n\s*\n", content.lstrip("\ufeff").strip()), start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0].strip() != str(number):
            raise ValueError(f"SRT cue {number} needs sequential numbering and text")
        parts = lines[1].split(" --> ")
        if len(parts) != 2:
            raise ValueError(f"SRT cue {number} has invalid timing")
        start, end = seconds(parts[0]), seconds(parts[1])
        text = "\n".join(lines[2:]).strip()
        if not text or start >= end or end > duration + 0.02:
            raise ValueError(f"SRT cue {number} has empty text or out-of-range timing")
        if cues and start < cues[-1][1] - 0.001:
            raise ValueError(f"SRT cue {number} overlaps the previous cue")
        cues.append((start, end, text))
    if not cues:
        raise ValueError("SRT has no cues")
    return cues


def probe(path: Path) -> dict[str, Any]:
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))


def av_contract(path: Path) -> tuple[float, list[dict[str, Any]]]:
    data = probe(path)
    streams = data.get("streams") or []
    kinds = [item.get("codec_type") for item in streams]
    if kinds.count("video") != 1 or kinds.count("audio") > 1 or any(kind not in {"video", "audio"} for kind in kinds):
        raise ValueError("source must have one video, at most one audio, and no other streams")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not 0 < duration < 24 * 3600:
        raise ValueError("source needs a finite duration below 24 hours")
    return duration, streams


def stream_hashes(path: Path) -> list[str]:
    lines = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
                 "-c", "copy", "-f", "streamhash", "-hash", "SHA256", "-"])
    hashes = [line.strip().split(",", 2)[2] for line in lines.splitlines()
              if re.match(r"^\d+,[va],SHA256=", line)]
    if not hashes:
        raise ValueError("could not hash source streams")
    return hashes


def verify_media(source: Path, subtitles: Path, output: Path, language: str) -> dict[str, Any]:
    duration, source_streams = av_contract(source)
    cues = parse_srt(subtitles.read_text(encoding="utf-8-sig"), duration)
    data = probe(output)
    streams = data.get("streams") or []
    source_av = sorted(source_streams, key=lambda item: 0 if item.get("codec_type") == "video" else 1)
    if [item.get("codec_type") for item in streams] != [item.get("codec_type") for item in source_av] + ["subtitle"]:
        raise ValueError("output stream layout differs from source plus one subtitle")
    if streams[-1].get("codec_name") != "mov_text" or (streams[-1].get("tags") or {}).get("language") != language:
        raise ValueError("output subtitle codec or language differs")
    if [(item.get("codec_name"), item.get("codec_type")) for item in streams[:-1]] != [
        (item.get("codec_name"), item.get("codec_type")) for item in source_av
    ]:
        raise ValueError("output A/V codec differs")
    output_duration = float((data.get("format") or {}).get("duration") or 0)
    if abs(output_duration - duration) > 0.1:
        raise ValueError("output duration differs")
    source_hashes = stream_hashes(source)
    if stream_hashes(output) != source_hashes:
        raise ValueError("output A/V stream bytes differ")
    extracted = run(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:s:0", "-f", "srt", "-"])
    roundtrip = parse_srt(extracted, duration + 0.1)
    if len(roundtrip) != len(cues) or any(
        abs(a[0] - b[0]) > 0.025 or abs(a[1] - b[1]) > 0.025 or a[2] != b[2]
        for a, b in zip(cues, roundtrip)
    ):
        raise ValueError("embedded subtitle content or timing differs from SRT")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output),
         "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"])
    return {"duration": output_duration, "subtitle_codec": "mov_text", "language": language,
            "cue_count": len(cues), "av_stream_sha256": source_hashes, "full_decode": "passed"}


def receipt_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mux(args: argparse.Namespace) -> dict[str, Any]:
    source, subtitles = input_path(args.source), input_path(args.subtitles)
    if source == subtitles:
        raise ValueError("video and SRT must be distinct")
    if subtitles.suffix.lower() != ".srt" or not re.fullmatch(r"[a-z]{3}", args.language):
        raise ValueError("subtitles must be .srt and language must be a three-letter lowercase code")
    duration, _ = av_contract(source)
    parse_srt(subtitles.read_text(encoding="utf-8-sig"), duration)
    source_info, subtitle_info = fingerprint(source), fingerprint(subtitles)
    output = output_path(args.output, [source, subtitles], args.force)
    receipt = output_path(args.receipt, [source, subtitles, output], args.force)
    if output.suffix.lower() != ".mp4" or receipt.suffix.lower() != ".json" or output == receipt:
        raise ValueError("output must be .mp4 and receipt must be a distinct .json")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(subtitles),
             "-map", "0:v:0", "-map", "0:a:0?", "-map", "1:0", "-c:v", "copy", "-c:a", "copy",
             "-c:s", "mov_text", "-metadata:s:s:0", f"language={args.language}",
             "-movflags", "+faststart", str(temporary)])
        media = verify_media(source, subtitles, temporary, args.language)
        if fingerprint(source) != source_info or fingerprint(subtitles) != subtitle_info:
            raise ValueError("source or SRT changed during mux")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    payload: dict[str, Any] = {"schema": SCHEMA, "source": source_info, "subtitles": subtitle_info,
                               "output": fingerprint(output), "language": args.language, "media": media}
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


def verify(value: str) -> dict[str, Any]:
    receipt = input_path(value)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    source, subtitles, output = (input_path(payload[key]["path"]) for key in ("source", "subtitles", "output"))
    if any(fingerprint(path) != payload[key] for path, key in
           ((source, "source"), (subtitles, "subtitles"), (output, "output"))):
        raise ValueError("bound source, SRT, or output changed")
    if verify_media(source, subtitles, output, payload["language"]) != payload["media"]:
        raise ValueError("media contract differs")
    return {"status": "ready_for_human_review", "output": str(output), "cues": payload["media"]["cue_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("mux", help="Add a switchable subtitle track and write a receipt")
    make.add_argument("source")
    make.add_argument("subtitles")
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--language", default="und", help="Three-letter ISO 639-2 code, e.g. zho or eng")
    make.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Check input/output bytes, streams and subtitle roundtrip")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = mux(args) if args.command == "mux" else verify(args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, UnicodeError, FileNotFoundError, FileExistsError, KeyError, TypeError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
