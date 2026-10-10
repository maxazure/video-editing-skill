#!/usr/bin/env python3
"""Flag transcript segments that overlap measured digital silence.

This is a review aid for an isolated speech recording. Low volume does not
prove an ASR hallucination, and loud music does not prove speech is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from caption_speech_qa import measure_silences, probe_audio_media


VERSION = "transcript_silence_qa.v1"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def segments_from(path: Path, duration: float) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("transcript must contain segments[]")
    result = []
    previous_start = -1.0
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"segment {index} must be an object")
        start, end = row.get("start"), row.get("end")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or end > duration + 0.05
                or start < previous_start):
            raise ValueError(f"segment {index} has invalid or unordered timing")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"segment {index} needs text")
        segment_id = str(row.get("id", index))
        if segment_id in seen:
            raise ValueError(f"duplicate segment id: {segment_id}")
        seen.add(segment_id)
        result.append({"id": segment_id, "start": float(start), "end": float(end), "text": text.strip()})
        previous_start = start
    return result


def analyze(audio: Path, transcript: Path, *, noise_db: float = -40.0,
            min_silence: float = 0.15, flag_ratio: float = 0.8) -> dict:
    if not -80 <= noise_db <= -10 or not 0.05 <= min_silence <= 5 or not 0 < flag_ratio <= 1:
        raise ValueError("invalid thresholds")
    audio = audio.resolve(strict=True)
    transcript = transcript.resolve(strict=True)
    media = probe_audio_media(audio)
    duration = media["duration"]
    segments = segments_from(transcript, duration)
    silences = measure_silences(audio, duration=duration,
                                settings={"noise_db": noise_db, "min_silence_seconds": min_silence})
    reviewed = []
    for segment in segments:
        start, end = segment["start"], segment["end"]
        silent = sum(max(0.0, min(end, b) - max(start, a)) for a, b in silences)
        ratio = silent / (end - start)
        reviewed.append({**segment, "silent_seconds": round(silent, 6),
                         "silent_ratio": round(ratio, 6),
                         "status": "review" if ratio + 1e-9 >= flag_ratio else "clear"})
    return {
        "version": VERSION,
        "audio": {"path": str(audio), "sha256": digest(audio), "media": media},
        "transcript": {"path": str(transcript), "sha256": digest(transcript)},
        "settings": {"noise_db": noise_db, "min_silence": min_silence, "flag_ratio": flag_ratio},
        "silences": silences,
        "segments": reviewed,
        "summary": {"total": len(reviewed), "review": sum(row["status"] == "review" for row in reviewed)},
    }


def verify(path: Path) -> dict:
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("version") != VERSION:
        raise ValueError("unsupported report version")
    try:
        settings = saved["settings"]
        current = analyze(Path(saved["audio"]["path"]), Path(saved["transcript"]["path"]),
                          noise_db=settings["noise_db"], min_silence=settings["min_silence"],
                          flag_ratio=settings["flag_ratio"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid report") from exc
    if saved != current:
        raise ValueError("report, source, or live silence measurement changed")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("analyze", help="Measure silence against raw transcript segments")
    scan.add_argument("audio", type=Path)
    scan.add_argument("transcript", type=Path)
    scan.add_argument("--output", required=True, type=Path)
    scan.add_argument("--noise-db", type=float, default=-40.0)
    scan.add_argument("--min-silence", type=float, default=0.15)
    scan.add_argument("--flag-ratio", type=float, default=0.8)
    check = sub.add_parser("verify", help="Recheck report and live audio")
    check.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            report = analyze(args.audio, args.transcript, noise_db=args.noise_db,
                             min_silence=args.min_silence, flag_ratio=args.flag_ratio)
            output = args.output.resolve()
            if output in (Path(report["audio"]["path"]), Path(report["transcript"]["path"])):
                raise ValueError("output must not overwrite an input")
            if output.exists():
                raise ValueError("output already exists")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            report = verify(args.report)
        print(json.dumps(report["summary"], ensure_ascii=False))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
