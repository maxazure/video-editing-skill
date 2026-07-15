#!/usr/bin/env python3
"""Detect repeated speech in a transcript made from a rendered edit.

The script is intentionally read-only and provider-free. Re-transcribe the
rendered master with ``transcribe.py``, then use this report to catch exact
boundary leaks, adjacent near-duplicate takes, and immediate in-segment
stutters that visual/audio signal QA cannot identify.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "speech_continuity_qa.v1"
TOKEN_RE = re.compile(r"[\u3400-\u9fff]|[a-z0-9]+(?:['’][a-z0-9]+)?", re.IGNORECASE)


@dataclass(frozen=True)
class TranscriptSegment:
    segment_id: Any
    start: float
    end: float
    text: str
    speaker: str
    tokens: Tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("\ufeff", "").strip()


def tokenize(value: Any) -> List[str]:
    return [token.lower().replace("’", "'") for token in TOKEN_RE.findall(_clean_text(value))]


def _display_tokens(tokens: Sequence[str]) -> str:
    output = ""
    for token in tokens:
        if not output:
            output = token
        elif re.fullmatch(r"[\u3400-\u9fff]", token) or re.search(r"[\u3400-\u9fff]$", output):
            output += token
        else:
            output += " " + token
    return output


def _segment_text(segment: Mapping[str, Any]) -> str:
    text = _clean_text(segment.get("text"))
    if text:
        return text
    words: List[str] = []
    for word in segment.get("words") or []:
        if not isinstance(word, Mapping):
            continue
        value = _clean_text(word.get("word") if word.get("word") is not None else word.get("text"))
        if value:
            words.append(value)
    return _display_tokens(words)


def load_segments(payload: Mapping[str, Any]) -> List[TranscriptSegment]:
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("transcript must contain a non-empty segments[] list")

    segments: List[TranscriptSegment] = []
    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"segment {index} must be an object")
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"segment {index} has invalid start/end") from exc
        text = _segment_text(raw)
        tokens = tuple(tokenize(text))
        if end <= start:
            raise ValueError(f"segment {index} end must be after start")
        if not tokens:
            continue
        segments.append(
            TranscriptSegment(
                segment_id=raw.get("id", index),
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                speaker=_clean_text(raw.get("speaker") or raw.get("speaker_id")),
                tokens=tokens,
            )
        )
    if not segments:
        raise ValueError("transcript has no segments with speech text")
    return sorted(segments, key=lambda item: (item.start, item.end, str(item.segment_id)))


def find_boundary_overlap(
    previous: Sequence[str],
    current: Sequence[str],
    *,
    min_units: int,
    max_units: int,
) -> Tuple[str, ...]:
    largest = min(len(previous), len(current), max_units)
    for size in range(largest, min_units - 1, -1):
        if tuple(previous[-size:]) == tuple(current[:size]):
            return tuple(current[:size])
    return ()


def find_internal_repeats(
    tokens: Sequence[str],
    *,
    min_units: int,
    max_units: int,
) -> List[Tuple[int, Tuple[str, ...]]]:
    findings: List[Tuple[int, Tuple[str, ...]]] = []
    index = 0
    while index + min_units * 2 <= len(tokens):
        largest = min(max_units, (len(tokens) - index) // 2)
        match: Tuple[str, ...] = ()
        for size in range(largest, min_units - 1, -1):
            first = tuple(tokens[index:index + size])
            if first == tuple(tokens[index + size:index + size * 2]):
                match = first
                break
        if match:
            findings.append((index, match))
            index += len(match) * 2
        else:
            index += 1
    return findings


def _same_speaker(previous: TranscriptSegment, current: TranscriptSegment, include_changes: bool) -> bool:
    if include_changes or not previous.speaker or not current.speaker:
        return True
    return previous.speaker == current.speaker


def _finding(
    kind: str,
    *,
    start: float,
    end: float,
    repeated_text: str,
    repeat_units: int,
    previous: Optional[TranscriptSegment] = None,
    current: Optional[TranscriptSegment] = None,
    similarity: Optional[float] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "kind": kind,
        "severity": "block",
        "start": round(start, 3),
        "end": round(end, 3),
        "repeated_text": repeated_text,
        "repeat_units": repeat_units,
    }
    if previous is not None:
        row.update({
            "previous_segment_id": previous.segment_id,
            "previous_text": previous.text,
        })
    if current is not None:
        row.update({
            "current_segment_id": current.segment_id,
            "current_text": current.text,
        })
    if previous is not None and current is not None:
        row.update({
            "boundary_time": current.start,
            "gap_seconds": round(current.start - previous.end, 3),
        })
    if similarity is not None:
        row["similarity"] = round(similarity, 3)
    return row


def analyze_segments(
    segments: Sequence[TranscriptSegment],
    *,
    min_repeat_units: int = 3,
    max_repeat_units: int = 12,
    similarity_threshold: float = 0.9,
    min_similarity_units: int = 5,
    max_boundary_gap: float = 2.0,
    include_speaker_changes: bool = False,
) -> List[Dict[str, Any]]:
    if min_repeat_units < 1:
        raise ValueError("min_repeat_units must be at least 1")
    if max_repeat_units < min_repeat_units:
        raise ValueError("max_repeat_units must be >= min_repeat_units")
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be between 0 and 1")
    if min_similarity_units < 1:
        raise ValueError("min_similarity_units must be at least 1")
    if max_boundary_gap < 0:
        raise ValueError("max_boundary_gap must be non-negative")

    findings: List[Dict[str, Any]] = []
    for segment in segments:
        for _, repeated in find_internal_repeats(
            segment.tokens,
            min_units=min_repeat_units,
            max_units=max_repeat_units,
        ):
            findings.append(_finding(
                "internal_immediate_repeat",
                start=segment.start,
                end=segment.end,
                repeated_text=_display_tokens(repeated),
                repeat_units=len(repeated),
                current=segment,
            ))

    for previous, current in zip(segments, segments[1:]):
        if current.start - previous.end > max_boundary_gap:
            continue
        if not _same_speaker(previous, current, include_speaker_changes):
            continue
        overlap = find_boundary_overlap(
            previous.tokens,
            current.tokens,
            min_units=min_repeat_units,
            max_units=max_repeat_units,
        )
        if overlap:
            findings.append(_finding(
                "boundary_exact_repeat",
                start=previous.start,
                end=current.end,
                repeated_text=_display_tokens(overlap),
                repeat_units=len(overlap),
                previous=previous,
                current=current,
            ))
            continue

        if min(len(previous.tokens), len(current.tokens)) < min_similarity_units:
            continue
        length_ratio = min(len(previous.tokens), len(current.tokens)) / max(len(previous.tokens), len(current.tokens))
        if length_ratio < 0.6:
            continue
        similarity = SequenceMatcher(None, previous.tokens, current.tokens, autojunk=False).ratio()
        if similarity >= similarity_threshold:
            findings.append(_finding(
                "adjacent_near_duplicate",
                start=previous.start,
                end=current.end,
                repeated_text=current.text,
                repeat_units=min(len(previous.tokens), len(current.tokens)),
                previous=previous,
                current=current,
                similarity=similarity,
            ))

    findings.sort(key=lambda item: (float(item.get("start", 0.0)), str(item.get("kind", ""))))
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"speech-{index:03d}"
        finding["recommendation"] = (
            "Inspect this time range in the rendered master and adjust the source edit/cut list; "
            "then re-render, re-transcribe, and rerun this QA."
        )
    return findings


def _source_media(payload: Mapping[str, Any]) -> str:
    for key in ("source_media", "source_audio", "source_path", "video", "audio", "input"):
        if payload.get(key):
            return str(payload[key])
    source = payload.get("source")
    if isinstance(source, Mapping):
        for key in ("path", "media", "video", "audio"):
            if source.get(key):
                return str(source[key])
    return ""


def build_report(
    transcript_path: str,
    *,
    min_repeat_units: int = 3,
    max_repeat_units: int = 12,
    similarity_threshold: float = 0.9,
    min_similarity_units: int = 5,
    max_boundary_gap: float = 2.0,
    include_speaker_changes: bool = False,
) -> Dict[str, Any]:
    with open(transcript_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("transcript must be a JSON object")
    segments = load_segments(payload)
    findings = analyze_segments(
        segments,
        min_repeat_units=min_repeat_units,
        max_repeat_units=max_repeat_units,
        similarity_threshold=similarity_threshold,
        min_similarity_units=min_similarity_units,
        max_boundary_gap=max_boundary_gap,
        include_speaker_changes=include_speaker_changes,
    )
    counts = {
        kind: sum(1 for finding in findings if finding["kind"] == kind)
        for kind in ("boundary_exact_repeat", "adjacent_near_duplicate", "internal_immediate_repeat")
    }
    return {
        "version": VERSION,
        "created_at": utc_now(),
        "transcript": os.path.abspath(transcript_path),
        "source_media": _source_media(payload),
        "config": {
            "min_repeat_units": min_repeat_units,
            "max_repeat_units": max_repeat_units,
            "similarity_threshold": similarity_threshold,
            "min_similarity_units": min_similarity_units,
            "max_boundary_gap": max_boundary_gap,
            "include_speaker_changes": include_speaker_changes,
        },
        "segments_analyzed": len(segments),
        "findings": findings,
        "summary": {
            "status": "blocked" if findings else "ready",
            "blocking": len(findings),
            "warnings": 0,
            "findings": len(findings),
            **counts,
        },
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Speech Continuity QA",
        "",
        f"- Status: **{str(summary.get('status', 'unknown')).upper()}**",
        f"- Transcript: `{report.get('transcript', '')}`",
        f"- Rendered source: `{report.get('source_media') or 'not recorded in transcript'}`",
        f"- Segments analyzed: {report.get('segments_analyzed', 0)}",
        f"- Blocking findings: {summary.get('blocking', 0)}",
        "",
        "## Findings",
        "",
        "| ID | Time | Kind | Repeated speech | Evidence |",
        "|---|---:|---|---|---|",
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if not findings:
        lines.append("| - | - | ready | No repeated speech detected | - |")
    for finding in findings:
        evidence = finding.get("similarity")
        evidence_text = _clean_text(finding.get("message"))
        if not evidence_text:
            evidence_text = f"similarity={evidence}" if evidence is not None else f"units={finding.get('repeat_units', 0)}"
        evidence_text = evidence_text.replace("|", "\\|")
        repeated = _clean_text(finding.get("repeated_text")).replace("|", "\\|")
        lines.append(
            f"| {finding.get('id')} | {float(finding.get('start', 0.0)):.3f}-{float(finding.get('end', 0.0)):.3f}s "
            f"| `{finding.get('kind')}` | {repeated} | {evidence_text} |"
        )
    lines += [
        "",
        "Fix blocking ranges in the source edit or cut list, re-render the master, re-transcribe it, and rerun this QA.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a rendered video's transcript for repeated speech and stutter leaks."
    )
    parser.add_argument("transcript", help="Transcript JSON made from the rendered master")
    parser.add_argument("--output", help="Write speech_continuity_qa.v1 JSON")
    parser.add_argument("--markdown", help="Write a human-readable Markdown report")
    parser.add_argument("--min-repeat-units", type=int, default=3, help="Minimum repeated CJK characters/words")
    parser.add_argument("--max-repeat-units", type=int, default=12, help="Maximum exact phrase length to compare")
    parser.add_argument("--similarity-threshold", type=float, default=0.9, help="Adjacent take similarity blocker")
    parser.add_argument("--min-similarity-units", type=int, default=5, help="Minimum units per near-duplicate segment")
    parser.add_argument("--max-boundary-gap", type=float, default=2.0, help="Maximum seconds between compared segments")
    parser.add_argument(
        "--include-speaker-changes",
        action="store_true",
        help="Compare adjacent segments even when both have different speaker labels",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when repeated speech is found")
    return parser


def _error_report(transcript_path: str, message: str) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "created_at": utc_now(),
        "transcript": os.path.abspath(transcript_path),
        "source_media": "",
        "config": {},
        "segments_analyzed": 0,
        "findings": [{
            "id": "speech-001",
            "kind": "input_error",
            "severity": "block",
            "start": 0.0,
            "end": 0.0,
            "repeated_text": "",
            "repeat_units": 0,
            "message": message,
        }],
        "summary": {
            "status": "blocked",
            "blocking": 1,
            "warnings": 0,
            "findings": 1,
            "boundary_exact_repeat": 0,
            "adjacent_near_duplicate": 0,
            "internal_immediate_repeat": 0,
        },
    }


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            args.transcript,
            min_repeat_units=args.min_repeat_units,
            max_repeat_units=args.max_repeat_units,
            similarity_threshold=args.similarity_threshold,
            min_similarity_units=args.min_similarity_units,
            max_boundary_gap=args.max_boundary_gap,
            include_speaker_changes=args.include_speaker_changes,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = _error_report(args.transcript, str(exc))

    if args.output:
        _write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.markdown:
        _write(args.markdown, emit_markdown(report))
    if not args.output and not args.markdown:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and int((report.get("summary") or {}).get("blocking", 0)):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
