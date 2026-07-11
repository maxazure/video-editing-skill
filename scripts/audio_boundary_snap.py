#!/usr/bin/env python3
"""Snap selected clip boundaries to words, sentence endings, and silence.

The script is local-first and reviewable. It reads an existing highlight or
segment plan plus a word-timestamped transcript, optionally runs FFmpeg
``silencedetect`` on the source media, and writes an adjusted plan that remains
compatible with ``shorts_batch.py`` through its top-level ``selected`` list.
It does not render or rewrite media.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "audio_boundary_plan.v1"
SENTENCE_END_RE = re.compile(r"[.!?。！？；;][\"'”’）)】]*$")


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


def _round3(value: Any) -> float:
    return round(float(value), 3)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def _word_from_mapping(raw: Mapping[str, Any]) -> Optional[Word]:
    token_type = str(raw.get("type") or "word").lower()
    if token_type in {"spacing", "audio_event"}:
        return None
    text = str(raw.get("word") if raw.get("word") is not None else raw.get("text") or "").strip()
    if not text:
        return None
    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None
    return Word(text=text, start=_round3(start), end=_round3(end))


def normalize_words(transcript: Mapping[str, Any]) -> List[Word]:
    """Read Whisper segment words or ElevenLabs Scribe top-level words."""
    words: List[Word] = []
    for raw in transcript.get("words") or []:
        if isinstance(raw, Mapping):
            word = _word_from_mapping(raw)
            if word:
                words.append(word)
    if not words:
        for segment in transcript.get("segments") or []:
            if not isinstance(segment, Mapping):
                continue
            for raw in segment.get("words") or []:
                if isinstance(raw, Mapping):
                    word = _word_from_mapping(raw)
                    if word:
                        words.append(word)
    return sorted(words, key=lambda item: (item.start, item.end, item.text))


def normalize_silences(transcript: Mapping[str, Any]) -> List[Dict[str, float]]:
    silences: List[Dict[str, float]] = []
    for raw in transcript.get("silences") or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            silences.append({"start": _round3(start), "end": _round3(end)})
    return silences


def parse_silencedetect(log: str, duration: Optional[float] = None) -> List[Dict[str, float]]:
    silences: List[Dict[str, float]] = []
    current_start: Optional[float] = None
    for line in log.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            if end > current_start:
                silences.append({"start": _round3(current_start), "end": _round3(end)})
            current_start = None
    if current_start is not None and duration is not None and duration > current_start:
        silences.append({"start": _round3(current_start), "end": _round3(duration)})
    return silences


def probe_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip())
    return float(result.stdout.strip())


def run_silencedetect(
    path: str,
    *,
    duration: float,
    min_duration: float = 0.3,
    noise_db: str = "-35dB",
) -> List[Dict[str, float]]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-map", "0:a:0", "-af",
            f"silencedetect=n={noise_db}:d={min_duration:.3f}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffmpeg silencedetect failed").strip().splitlines()[-1])
    return parse_silencedetect(result.stderr, duration=duration)


def merge_silences(*groups: Iterable[Mapping[str, Any]]) -> List[Dict[str, float]]:
    normalized: List[Tuple[float, float]] = []
    for group in groups:
        for raw in group:
            try:
                start = float(raw["start"])
                end = float(raw["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                normalized.append((start, end))
    normalized.sort()
    merged: List[List[float]] = []
    for start, end in normalized:
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": _round3(start), "end": _round3(end)} for start, end in merged]


def _is_sentence_end(text: str) -> bool:
    return bool(SENTENCE_END_RE.search(text.rstrip()))


def _start_boundary(words: Sequence[Word], proposed: float, window: float) -> Tuple[float, str, Optional[Word]]:
    for word in words:
        if word.start < proposed < word.end:
            return word.start, "word_start", word
    future = [word for word in words if proposed <= word.start <= proposed + window]
    if future:
        return future[0].start, "next_word_start", future[0]
    prior = [word for word in words if 0 <= proposed - word.start <= window]
    if prior:
        word = prior[-1]
        return word.start, "nearest_word_start", word
    return proposed, "unchanged_no_word_nearby", None


def _end_boundary(
    words: Sequence[Word],
    proposed: float,
    *,
    sentence_window: float,
    padding: float,
) -> Tuple[float, str, Optional[Word]]:
    before = [(index, word) for index, word in enumerate(words) if word.start <= proposed + 0.001]
    if not before:
        future = [word for word in words if proposed <= word.end <= proposed + sentence_window]
        if not future:
            return proposed, "unchanged_no_word_nearby", None
        last_index = words.index(future[0])
    else:
        last_index = before[-1][0]

    last_word = words[last_index]
    target_word = last_word
    reason = "sentence_end" if _is_sentence_end(last_word.text) else "word_end"
    if not _is_sentence_end(last_word.text):
        for word in words[last_index + 1:]:
            if word.end > proposed + sentence_window:
                break
            if _is_sentence_end(word.text):
                target_word = word
                reason = "extended_to_sentence_end"
                break

    end = target_word.end + padding
    target_index = words.index(target_word)
    if target_index + 1 < len(words):
        end = min(end, words[target_index + 1].start)
    return end, reason, target_word


def _silence_before(
    silences: Sequence[Mapping[str, float]],
    word_start: float,
    window: float,
) -> Optional[float]:
    candidates = []
    for silence in silences:
        midpoint = (silence["start"] + silence["end"]) / 2.0
        if silence["end"] <= word_start + 0.05 and 0 <= word_start - midpoint <= window:
            candidates.append(silence)
    if not candidates:
        return None
    silence = max(candidates, key=lambda item: item["end"])
    return (silence["start"] + silence["end"]) / 2.0


def _silence_after(
    silences: Sequence[Mapping[str, float]],
    word_end: float,
    target_end: float,
    window: float,
) -> Optional[float]:
    candidates = []
    for silence in silences:
        midpoint = (silence["start"] + silence["end"]) / 2.0
        if silence["start"] >= word_end - 0.05 and 0 <= midpoint - word_end <= window:
            candidates.append(silence)
    if not candidates:
        return None
    silence = min(
        candidates,
        key=lambda item: abs(((item["start"] + item["end"]) / 2.0) - target_end),
    )
    return (silence["start"] + silence["end"]) / 2.0


def snap_candidate(
    candidate: Mapping[str, Any],
    *,
    words: Sequence[Word],
    silences: Sequence[Mapping[str, float]],
    duration: float,
    start_window: float = 1.5,
    sentence_window: float = 3.0,
    silence_window: float = 1.5,
    padding_ms: int = 300,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
) -> Dict[str, Any]:
    adjusted = dict(candidate)
    blockers: List[str] = []
    warnings: List[str] = []
    try:
        original_start = float(candidate["start"])
        original_end = float(candidate["end"])
    except (KeyError, TypeError, ValueError):
        adjusted["audio_boundary_snap"] = {
            "applied": False,
            "blockers": ["candidate must contain numeric start and end"],
            "warnings": [],
        }
        return adjusted
    if original_end <= original_start:
        adjusted["audio_boundary_snap"] = {
            "applied": False,
            "original_start": _round3(original_start),
            "original_end": _round3(original_end),
            "blockers": ["candidate end must be greater than start"],
            "warnings": [],
        }
        return adjusted
    if not words:
        adjusted["audio_boundary_snap"] = {
            "applied": False,
            "original_start": _round3(original_start),
            "original_end": _round3(original_end),
            "snapped_start": _round3(original_start),
            "snapped_end": _round3(original_end),
            "blockers": ["word timestamps missing; rerun transcribe.py with --word-timestamps"],
            "warnings": [],
        }
        return adjusted

    start, start_reason, first_word = _start_boundary(words, original_start, start_window)
    if first_word and silences:
        silence_start = _silence_before(silences, first_word.start, silence_window)
        if silence_start is not None:
            start = silence_start
            start_reason = "silence_midpoint_before_word"

    end, end_reason, last_word = _end_boundary(
        words,
        original_end,
        sentence_window=sentence_window,
        padding=max(0, padding_ms) / 1000.0,
    )
    sentence_extended = end_reason == "extended_to_sentence_end"
    if last_word and silences:
        silence_end = _silence_after(silences, last_word.end, end, silence_window)
        if silence_end is not None:
            end = silence_end
            end_reason = "silence_midpoint_after_word"

    start = max(0.0, start)
    if duration > 0:
        end = min(duration, end)
    snapped_duration = end - start
    if end <= start:
        blockers.append("snapped end is not after snapped start")
    if min_duration is not None and snapped_duration < float(min_duration):
        blockers.append(f"snapped duration {snapped_duration:.3f}s is below minimum {float(min_duration):.3f}s")
    if max_duration is not None and snapped_duration > float(max_duration):
        blockers.append(f"snapped duration {snapped_duration:.3f}s exceeds maximum {float(max_duration):.3f}s")
    if start_reason == "unchanged_no_word_nearby":
        warnings.append("no word boundary found near start")
    if end_reason == "unchanged_no_word_nearby":
        warnings.append("no word boundary found near end")

    start = _round3(start)
    end = _round3(end)
    adjusted["start"] = start
    adjusted["end"] = end
    adjusted["duration"] = _round3(max(0.0, end - start))
    adjusted["audio_boundary_snap"] = {
        "applied": abs(start - original_start) > 0.0005 or abs(end - original_end) > 0.0005,
        "original_start": _round3(original_start),
        "original_end": _round3(original_end),
        "snapped_start": start,
        "snapped_end": end,
        "start_delta": _round3(start - original_start),
        "end_delta": _round3(end - original_end),
        "start_reason": start_reason,
        "end_reason": end_reason,
        "sentence_extended": sentence_extended,
        "first_word": first_word.text if first_word else None,
        "last_word": last_word.text if last_word else None,
        "blockers": blockers,
        "warnings": warnings,
    }
    return adjusted


def _selected_items(payload: Mapping[str, Any]) -> Tuple[str, List[Mapping[str, Any]]]:
    for key in ("selected", "segments", "clips"):
        items = [item for item in payload.get(key) or [] if isinstance(item, Mapping)]
        if items:
            return key, items
    return "selected", []


def build_audio_boundary_plan(
    candidates: Mapping[str, Any],
    transcript: Mapping[str, Any],
    *,
    silences: Sequence[Mapping[str, float]] = (),
    duration: Optional[float] = None,
    media_path: str = "",
    start_window: float = 1.5,
    sentence_window: float = 3.0,
    silence_window: float = 1.5,
    padding_ms: int = 300,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
    issues: Sequence[str] = (),
) -> Dict[str, Any]:
    words = normalize_words(transcript)
    source_key, items = _selected_items(candidates)
    candidate_params = dict(candidates.get("params") or {})
    inferred_min = min_duration if min_duration is not None else candidate_params.get("min_duration")
    inferred_max = max_duration if max_duration is not None else candidate_params.get("max_duration")
    global_blockers = list(issues)
    try:
        min_limit = float(inferred_min) if inferred_min is not None else None
    except (TypeError, ValueError):
        min_limit = None
        global_blockers.append("min_duration must be numeric")
    try:
        max_limit = float(inferred_max) if inferred_max is not None else None
    except (TypeError, ValueError):
        max_limit = None
        global_blockers.append("max_duration must be numeric")
    if duration is None:
        duration = transcript.get("duration")
    try:
        media_duration = float(duration or 0.0)
    except (TypeError, ValueError):
        media_duration = 0.0
    if media_duration <= 0:
        endpoints = [word.end for word in words]
        for item in items:
            try:
                endpoints.append(float(item.get("end") or 0.0))
            except (TypeError, ValueError):
                continue
        media_duration = max(endpoints or [0.0])

    selected = [
        snap_candidate(
            item,
            words=words,
            silences=silences,
            duration=media_duration,
            start_window=max(0.0, start_window),
            sentence_window=max(0.0, sentence_window),
            silence_window=max(0.0, silence_window),
            padding_ms=max(0, padding_ms),
            min_duration=min_limit,
            max_duration=max_limit,
        )
        for item in items
    ]
    clip_blockers = sum(len((item.get("audio_boundary_snap") or {}).get("blockers") or []) for item in selected)
    clip_warnings = sum(len((item.get("audio_boundary_snap") or {}).get("warnings") or []) for item in selected)
    if not items:
        global_blockers.append("candidate plan has no selected, segments, or clips entries")
    blocking = clip_blockers + len(global_blockers)
    adjusted_count = sum(1 for item in selected if (item.get("audio_boundary_snap") or {}).get("applied"))
    sentence_extended = sum(
        1 for item in selected
        if (item.get("audio_boundary_snap") or {}).get("sentence_extended")
    )
    silence_snapped = sum(
        1 for item in selected
        if "silence_midpoint" in str((item.get("audio_boundary_snap") or {}).get("start_reason"))
        or "silence_midpoint" in str((item.get("audio_boundary_snap") or {}).get("end_reason"))
    )

    candidate_params.update({
        "start_window": max(0.0, start_window),
        "sentence_window": max(0.0, sentence_window),
        "silence_window": max(0.0, silence_window),
        "padding_ms": max(0, padding_ms),
        "min_duration": inferred_min,
        "max_duration": inferred_max,
    })
    return {
        "version": VERSION,
        "status": "blocked" if blocking else "ready",
        "source": {
            "candidate_version": candidates.get("version"),
            "candidate_collection": source_key,
            "transcript_words": len(words),
            "media": media_path,
            "duration": _round3(media_duration),
            "silence_regions": len(silences),
        },
        "params": candidate_params,
        "summary": {
            "selected": len(selected),
            "adjusted": adjusted_count,
            "sentence_extended": sentence_extended,
            "silence_snapped": silence_snapped,
            "blocking": blocking,
            "warnings": clip_warnings,
        },
        "blockers": global_blockers,
        "selected": selected,
        "notes": [
            "Review adjustment deltas before rendering or batch export.",
            "The output selected list can be passed directly to shorts_batch.py --highlights.",
            "This script does not render, upload, or rewrite source media.",
        ],
    }


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    lines = [
        "# Audio Boundary Plan",
        "",
        f"- Status: **{str(plan.get('status') or '').upper()}**",
        f"- Word timestamps: `{(plan.get('source') or {}).get('transcript_words', 0)}`",
        f"- Silence regions: `{(plan.get('source') or {}).get('silence_regions', 0)}`",
        f"- Selected clips: `{summary.get('selected', 0)}`",
        f"- Adjusted clips: `{summary.get('adjusted', 0)}`",
        f"- Blocking: `{summary.get('blocking', 0)}`",
        "",
        "| clip | original | snapped | start delta | end delta | start reason | end reason | blockers |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for index, item in enumerate(plan.get("selected") or [], start=1):
        snap = item.get("audio_boundary_snap") or {}
        blockers = "; ".join(snap.get("blockers") or []) or "-"
        lines.append(
            "| {clip} | {old_start}-{old_end} | {new_start}-{new_end} | {start_delta:+.3f}s | "
            "{end_delta:+.3f}s | {start_reason} | {end_reason} | {blockers} |".format(
                clip=str(item.get("id") or item.get("name") or index).replace("|", "\\|"),
                old_start=format_time(float(snap.get("original_start") or item.get("start") or 0.0)),
                old_end=format_time(float(snap.get("original_end") or item.get("end") or 0.0)),
                new_start=format_time(float(snap.get("snapped_start") or item.get("start") or 0.0)),
                new_end=format_time(float(snap.get("snapped_end") or item.get("end") or 0.0)),
                start_delta=float(snap.get("start_delta") or 0.0),
                end_delta=float(snap.get("end_delta") or 0.0),
                start_reason=snap.get("start_reason") or "-",
                end_reason=snap.get("end_reason") or "-",
                blockers=blockers.replace("|", "\\|"),
            )
        )
    if plan.get("blockers"):
        lines.extend(["", "## Global Blockers", ""])
        lines.extend(f"- {item}" for item in plan.get("blockers") or [])
    lines.extend([
        "",
        "## Review Notes",
        "",
        "- Confirm the first and last spoken words still preserve the intended meaning.",
        "- A duration blocker means the safe word/sentence boundary exceeds the source platform limit; shorten the candidate manually.",
        "- Pass the reviewed JSON to `shorts_batch.py --highlights` for per-short render configs.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snap selected clip boundaries to word, sentence, and silence cut points."
    )
    parser.add_argument("--candidates", required=True, help="Highlight/segment plan containing selected, segments, or clips")
    parser.add_argument("--transcript", required=True, help="Transcript JSON with word timestamps")
    parser.add_argument("--media", help="Optional source media for ffprobe and FFmpeg silencedetect")
    parser.add_argument("--output", required=True, help="Output audio_boundary_plan JSON")
    parser.add_argument("--markdown", help="Optional Markdown review table")
    parser.add_argument("--start-window", type=float, default=1.5)
    parser.add_argument("--sentence-window", type=float, default=3.0)
    parser.add_argument("--silence-window", type=float, default=1.5)
    parser.add_argument("--padding-ms", type=int, default=300)
    parser.add_argument("--min-duration", type=float)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--silence-min-duration", type=float, default=0.3)
    parser.add_argument("--silence-noise-db", default="-35dB")
    parser.add_argument("--no-silence", action="store_true", help="Disable transcript and FFmpeg silence snapping")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        candidates = load_json(args.candidates)
        transcript = load_json(args.transcript)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    issues: List[str] = []
    warnings: List[str] = []
    duration: Optional[float] = None
    ffmpeg_silences: List[Dict[str, float]] = []
    media_path = os.path.abspath(os.path.expanduser(args.media)) if args.media else ""
    if media_path:
        if not os.path.isfile(media_path):
            issues.append(f"source media not found: {media_path}")
        else:
            try:
                duration = probe_duration(media_path)
                if not args.no_silence:
                    ffmpeg_silences = run_silencedetect(
                        media_path,
                        duration=duration,
                        min_duration=max(0.05, args.silence_min_duration),
                        noise_db=args.silence_noise_db,
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append(f"media silence analysis unavailable: {exc}")

    transcript_silences = [] if args.no_silence else normalize_silences(transcript)
    silences = merge_silences(transcript_silences, ffmpeg_silences)
    plan = build_audio_boundary_plan(
        candidates,
        transcript,
        silences=silences,
        duration=duration,
        media_path=media_path,
        start_window=args.start_window,
        sentence_window=args.sentence_window,
        silence_window=args.silence_window,
        padding_ms=args.padding_ms,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        issues=issues,
    )
    if warnings:
        plan["warnings"] = warnings
        plan["summary"]["warnings"] += len(warnings)
    write_json(args.output, plan)
    if args.markdown:
        write_text(args.markdown, emit_markdown(plan))
    print(
        "Audio boundary plan: status={status} selected={selected} adjusted={adjusted} blocking={blocking} wrote={path}".format(
            status=plan["status"],
            selected=plan["summary"]["selected"],
            adjusted=plan["summary"]["adjusted"],
            blocking=plan["summary"]["blocking"],
            path=args.output,
        )
    )
    if args.strict and plan["summary"]["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
