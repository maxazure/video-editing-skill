#!/usr/bin/env python3
"""Bounded, read-only lookup for timestamped transcript JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


VERSION = "transcript_lookup.v1"


def _fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold()
        if unicodedata.category(char)[0] in {"L", "N"}
    )


def load_segments(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("transcript must be a JSON object with segments[]")
    segments: List[Dict[str, Any]] = []
    for position, item in enumerate(payload["segments"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"segment {position} must be an object")
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"segment {position} needs numeric start/end") from exc
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            raise ValueError(f"segment {position} has invalid timing")
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError(f"segment {position} needs text")
        segments.append({"id": item.get("id", position), "start": start, "end": end, "text": text.strip()})
    segments.sort(key=lambda item: (item["start"], item["end"]))
    return segments, hashlib.sha256(raw).hexdigest()


def _summary(segments: Sequence[Mapping[str, Any]], first: int, last: int, context: int) -> Dict[str, Any]:
    selected = segments[first:last + 1]
    nearby = segments[max(0, first - context):min(len(segments), last + context + 1)]
    return {
        "start": selected[0]["start"],
        "end": selected[-1]["end"],
        "segment_ids": [item["id"] for item in selected],
        "text": " ".join(item["text"] for item in selected)[:300],
        "context": [
            {"id": item["id"], "start": item["start"], "end": item["end"], "text": item["text"][:300]}
            for item in nearby
        ],
    }


def search(segments: Sequence[Mapping[str, Any]], query: str, *, limit: int = 20, context: int = 1,
           max_gap: float = 2.0) -> Tuple[List[Dict[str, Any]], int]:
    needle = _fold(query)
    if not needle or len(query) > 200:
        raise ValueError("query must contain letters or numbers and be at most 200 characters")
    if not 1 <= limit <= 50 or not 0 <= context <= 3 or not 0 <= max_gap <= 30:
        raise ValueError("limit, context, or max_gap is outside the allowed range")
    haystack: List[str] = []
    owners: List[int] = []
    for index, item in enumerate(segments):
        if index and (item["start"] - segments[index - 1]["end"] > max_gap or
                      item["start"] < segments[index - 1]["end"] - 0.2):
            haystack.append("\0")
            owners.append(-1)
        folded = _fold(item["text"])
        haystack.extend(folded)
        owners.extend([index] * len(folded))
    content = "".join(haystack)
    results: List[Dict[str, Any]] = []
    total = 0
    offset = 0
    while (offset := content.find(needle, offset)) >= 0:
        first, last = owners[offset], owners[offset + len(needle) - 1]
        total += 1
        if len(results) < limit:
            results.append(_summary(segments, first, last, context))
        offset += len(needle)
    return results, total


def span(segments: Sequence[Mapping[str, Any]], start: float, end: float, *, limit: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("span needs finite start >= 0 and end > start")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be from 1 to 50")
    matches = [item for item in segments if item["start"] < end and item["end"] > start]
    return [
        {"id": item["id"], "start": item["start"], "end": item["end"], "text": item["text"][:300]}
        for item in matches[:limit]
    ], len(matches)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find spoken phrases or inspect a time span in transcript JSON.")
    parser.add_argument("transcript", type=Path, help="Transcript JSON with segments[start,end,text]")
    commands = parser.add_subparsers(dest="command", required=True)
    phrase = commands.add_parser("search", help="Find literal phrase, ignoring case, spacing, and punctuation")
    phrase.add_argument("query")
    phrase.add_argument("--context", type=int, default=1, help="Neighboring segments per match (0-3)")
    phrase.add_argument("--max-gap", type=float, default=2.0, help="Maximum seconds between matched segments (0-30)")
    window = commands.add_parser("span", help="Read transcript segments overlapping a time window")
    window.add_argument("--start", type=float, required=True)
    window.add_argument("--end", type=float, required=True)
    for command in (phrase, window):
        command.add_argument("--limit", type=int, default=20, help="Maximum returned matches (1-50)")
    args = parser.parse_args(argv)
    try:
        segments, digest = load_segments(args.transcript)
        if args.command == "search":
            matches, total = search(segments, args.query, limit=args.limit, context=args.context, max_gap=args.max_gap)
        else:
            matches, total = span(segments, args.start, args.end, limit=args.limit)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"transcript_lookup: {exc}\n")
    print(json.dumps({
        "version": VERSION,
        "transcript": str(args.transcript.resolve()),
        "transcript_sha256": digest,
        "command": args.command,
        "total": total,
        "returned": len(matches),
        "truncated": total > len(matches),
        "matches": matches,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
