#!/usr/bin/env python3
"""Copy MP4 audio/video into a chaptered MP4 and verify the embedded markers."""

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

from chapter_markers import ChapterMarker, chapters_to_ffmetadata


SCHEMA = "chapter_mux.v1"


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(" ".join((result.stderr or result.stdout).split())[-2000:])
    return result.stdout


def input_file(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input is missing or a symlink: {path}")
    return path.resolve()


def output_file(value: str, inputs: list[Path], force: bool) -> Path:
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


def fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def probe(path: Path) -> dict[str, Any]:
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                           "-show_chapters", "-of", "json", str(path)]))


def source_contract(path: Path) -> tuple[float, list[tuple[str, str]]]:
    if path.suffix.lower() != ".mp4":
        raise ValueError("source must be .mp4")
    data = probe(path)
    streams = [(item.get("codec_type"), item.get("codec_name")) for item in data.get("streams", [])]
    kinds = [kind for kind, _ in streams]
    if kinds.count("video") != 1 or kinds.count("audio") > 1 or any(kind not in {"video", "audio"} for kind in kinds):
        raise ValueError("source must have one video, at most one audio, and no other streams")
    if data.get("chapters"):
        raise ValueError("source already has chapters")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration < 24 * 3600:
        raise ValueError("source needs a finite duration below 24 hours")
    return duration, streams


def load_chapters(path: Path, duration: float) -> list[ChapterMarker]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != "chapter_markers.v1":
        raise ValueError("chapters must be a chapter_markers.v1 JSON manifest")
    raw = data.get("chapters")
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        raise ValueError("chapters must contain 1–100 markers")
    chapters: list[ChapterMarker] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"chapter {index} must be an object")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 80 or "\n" in title or "\r" in title:
            raise ValueError(f"chapter {index} needs a single-line title of at most 80 characters")
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"chapter {index} needs numeric start/end") from exc
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                or end > duration + 0.1 or (index == 1 and abs(start) > 0.001)
                or (chapters and abs(start - chapters[-1].end) > 0.001)):
            raise ValueError(f"chapter {index} timing is out of range or not contiguous")
        chapters.append(ChapterMarker(f"ch{index:02d}", title, start, end, end - start))
    if abs(chapters[-1].end - duration) > 0.1:
        raise ValueError("final chapter end must match the source duration within 0.1 seconds")
    return chapters


def stream_hashes(path: Path) -> list[str]:
    output = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
                  "-c", "copy", "-f", "streamhash", "-hash", "SHA256", "-"])
    hashes = [line.strip().split(",", 2)[2] for line in output.splitlines()
              if ",SHA256=" in line and line.split(",", 2)[1] in {"v", "a"}]
    if not hashes:
        raise ValueError("could not hash source streams")
    return hashes


def verify_media(source: Path, chapter_file: Path, output: Path) -> dict[str, Any]:
    duration, source_streams = source_contract(source)
    chapters = load_chapters(chapter_file, duration)
    data = probe(output)
    output_streams = [(item.get("codec_type"), item.get("codec_name")) for item in data.get("streams", [])]
    if (output_streams[:-1] != source_streams or output_streams[-1:] != [("data", "bin_data")]):
        raise ValueError("output A/V stream layout or chapter data stream differs")
    output_duration = float((data.get("format") or {}).get("duration") or 0)
    if abs(output_duration - duration) > 0.1:
        raise ValueError("output duration differs")
    embedded = data.get("chapters") or []
    if len(embedded) != len(chapters):
        raise ValueError("embedded chapter count differs")
    for expected, actual in zip(chapters, embedded):
        if (abs(float(actual.get("start_time", -1)) - expected.start) > 0.005
                or abs(float(actual.get("end_time", -1)) - expected.end) > 0.005
                or (actual.get("tags") or {}).get("title") != expected.title):
            raise ValueError("embedded chapter title or timing differs")
    hashes = stream_hashes(source)
    if stream_hashes(output) != hashes:
        raise ValueError("output A/V stream bytes differ")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-map", "0:v:0",
         "-map", "0:a:0?", "-f", "null", "-"])
    return {"duration": output_duration, "chapter_count": len(chapters),
            "chapters": [{"start": c.start, "end": c.end, "title": c.title} for c in chapters],
            "av_stream_sha256": hashes, "full_decode": "passed"}


def receipt_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mux(source_arg: str, chapters_arg: str, output_arg: str, receipt_arg: str, force: bool) -> dict[str, Any]:
    source, chapter_file = input_file(source_arg), input_file(chapters_arg)
    if source == chapter_file or source.samefile(chapter_file):
        raise ValueError("source and chapter manifest must be distinct")
    duration, _ = source_contract(source)
    chapters = load_chapters(chapter_file, duration)
    source_info, chapter_info = fingerprint(source), fingerprint(chapter_file)
    output = output_file(output_arg, [source, chapter_file], force)
    receipt = output_file(receipt_arg, [source, chapter_file, output], force)
    if output.suffix.lower() != ".mp4" or receipt.suffix.lower() != ".json" or output == receipt:
        raise ValueError("output must be .mp4 and receipt must be a distinct .json")
    fd_meta, temp_meta = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".ffmetadata", dir=output.parent)
    fd_video, temp_video = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(fd_video)
    os.close(fd_meta)
    metadata, temporary = Path(temp_meta), Path(temp_video)
    try:
        metadata.write_text(chapters_to_ffmetadata(chapters), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-f", "ffmetadata", "-i", str(metadata),
             "-map", "0", "-map_metadata", "0", "-map_chapters", "1", "-c", "copy",
             "-movflags", "+faststart", str(temporary)])
        media = verify_media(source, chapter_file, temporary)
        if fingerprint(source) != source_info or fingerprint(chapter_file) != chapter_info:
            raise ValueError("source or chapter manifest changed during mux")
        os.replace(temporary, output)
    finally:
        metadata.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
    payload: dict[str, Any] = {"schema": SCHEMA, "source": source_info, "chapters": chapter_info,
                               "output": fingerprint(output), "media": media}
    payload["id"] = receipt_id(payload)
    fd, temp_receipt = tempfile.mkstemp(prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_receipt, receipt)
    finally:
        Path(temp_receipt).unlink(missing_ok=True)
    return payload


def verify(receipt_arg: str) -> dict[str, Any]:
    receipt = input_file(receipt_arg)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    source, chapters, output = (input_file(payload[key]["path"]) for key in ("source", "chapters", "output"))
    if any(fingerprint(path) != payload[key] for key, path in
           (("source", source), ("chapters", chapters), ("output", output))):
        raise ValueError("bound source, chapters, or output changed")
    if verify_media(source, chapters, output) != payload["media"]:
        raise ValueError("media contract differs")
    return {"status": "ready_for_human_review", "output": str(output),
            "chapters": payload["media"]["chapter_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("mux", help="Copy A/V and embed reviewed chapter markers")
    make.add_argument("source")
    make.add_argument("chapters", help="chapter_markers.v1 JSON from chapter_markers.py")
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Recheck input/output bytes, markers, streams and decode")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = (mux(args.source, args.chapters, args.output, args.receipt, args.force)
                  if args.command == "mux" else verify(args.receipt))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, UnicodeError, FileNotFoundError, FileExistsError, KeyError, TypeError,
            RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
