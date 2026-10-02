#!/usr/bin/env python3
"""Remove MP4 container metadata and extra tracks without re-encoding A/V."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from chapter_mux import fingerprint, input_file, output_file, probe, receipt_id, run, stream_hashes


SCHEMA = "metadata_scrub.v1"
FORMAT_TAGS = {"major_brand", "minor_version", "compatible_brands", "encoder"}
STREAM_TAGS = {"language", "handler_name", "vendor_id", "encoder"}


def media_contract(source: Path, output: Path) -> dict[str, Any]:
    before, after = probe(source), probe(output)
    source_streams = before.get("streams") or []
    selected = [stream for stream in source_streams if stream.get("codec_type") in {"video", "audio"}]
    kinds = [stream.get("codec_type") for stream in selected]
    if source.suffix.lower() != ".mp4" or kinds.count("video") != 1 or kinds.count("audio") > 1:
        raise ValueError("source must be MP4 with one video and at most one audio stream")
    duration = float((before.get("format") or {}).get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration < 24 * 3600:
        raise ValueError("source needs a finite duration below 24 hours")
    out_streams = after.get("streams") or []
    if ([(s.get("codec_type"), s.get("codec_name")) for s in out_streams]
            != [(s.get("codec_type"), s.get("codec_name")) for s in selected]):
        raise ValueError("output audio/video streams differ or an extra track remains")
    if after.get("chapters"):
        raise ValueError("output still contains chapters")
    format_tags = (after.get("format") or {}).get("tags") or {}
    unexpected_format = set(format_tags) - FORMAT_TAGS
    unexpected_streams = [set(stream.get("tags") or {}) - STREAM_TAGS for stream in out_streams]
    if unexpected_format or any(unexpected_streams):
        raise ValueError(f"output still has nonstructural metadata: {sorted(unexpected_format)}, "
                         f"{[sorted(tags) for tags in unexpected_streams]}")
    output_duration = float((after.get("format") or {}).get("duration") or 0)
    if abs(output_duration - duration) > 0.1:
        raise ValueError("output duration differs")
    hashes = stream_hashes(source)
    if stream_hashes(output) != hashes:
        raise ValueError("output A/V stream bytes differ")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output),
         "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"])
    return {"duration": output_duration, "av_stream_sha256": hashes,
            "removed_extra_tracks": len(source_streams) - len(selected),
            "removed_chapters": len(before.get("chapters") or []),
            "remaining_format_tags": format_tags,
            "remaining_stream_tags": [stream.get("tags") or {} for stream in out_streams],
            "full_decode": "passed"}


def scrub(source_arg: str, output_arg: str, receipt_arg: str, force: bool) -> dict[str, Any]:
    source = input_file(source_arg)
    source_data = probe(source)
    selected = [s for s in source_data.get("streams", []) if s.get("codec_type") in {"video", "audio"}]
    kinds = [s.get("codec_type") for s in selected]
    if source.suffix.lower() != ".mp4" or kinds.count("video") != 1 or kinds.count("audio") > 1:
        raise ValueError("source must be MP4 with one video and at most one audio stream")
    source_info = fingerprint(source)
    output = output_file(output_arg, [source], force)
    receipt = output_file(receipt_arg, [source, output], force)
    if output.suffix.lower() != ".mp4" or receipt.suffix.lower() != ".json" or output == receipt:
        raise ValueError("output must be .mp4 and receipt must be a distinct .json")
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
             "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1",
             "-map_metadata:s", "-1", "-map_chapters", "-1", "-c", "copy",
             "-movflags", "+faststart", str(temporary)])
        media = media_contract(source, temporary)
        if fingerprint(source) != source_info:
            raise ValueError("source changed during metadata scrub")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    payload: dict[str, Any] = {"schema": SCHEMA, "source": source_info,
                               "output": fingerprint(output), "media": media}
    payload["id"] = receipt_id(payload)
    fd, temp_name = tempfile.mkstemp(prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, receipt)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return payload


def verify(receipt_arg: str) -> dict[str, Any]:
    receipt = input_file(receipt_arg)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    identifier = payload.pop("id", None)
    if payload.get("schema") != SCHEMA or identifier != receipt_id(payload):
        raise ValueError("receipt schema or digest differs")
    source, output = input_file(payload["source"]["path"]), input_file(payload["output"]["path"])
    if fingerprint(source) != payload["source"] or fingerprint(output) != payload["output"]:
        raise ValueError("bound source or output changed")
    if media_contract(source, output) != payload["media"]:
        raise ValueError("media contract differs")
    return {"status": "ready_for_human_review", "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("scrub", help="Remove container metadata, chapters and extra tracks")
    make.add_argument("source")
    make.add_argument("--output", required=True)
    make.add_argument("--receipt", required=True)
    make.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Recheck source/output bytes, stream copy and metadata")
    check.add_argument("receipt")
    args = parser.parse_args()
    try:
        result = (scrub(args.source, args.output, args.receipt, args.force)
                  if args.command == "scrub" else verify(args.receipt))
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, KeyError,
            TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"metadata_scrub error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
