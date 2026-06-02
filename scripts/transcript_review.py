#!/usr/bin/env python3
"""Editable transcript review round trip for Whisper JSON.

The tool keeps this pipeline CLI-first: export a human-editable review file,
let the user fix ASR mistakes, then apply those edits back into transcript JSON
while preserving segment timings and optionally redistributing word timings.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "transcript_review.v1"
REVIEW_LINE = re.compile(
    r"^\[seg:(?P<id>[^\s\]]+)\s+start:(?P<start>[0-9:.]+)\s+end:(?P<end>[0-9:.]+)\]\s*(?P<text>.*?)\s*$"
)
TIME_ONLY_LINE = re.compile(r"^\[(?P<start>[0-9:.]+)\]\s*(?P<text>.*?)\s*$")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?|[^\s]"
)


class TranscriptReviewError(ValueError):
    """Raised for user-fixable transcript review errors."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def parse_time(value: str) -> float:
    raw = str(value).strip()
    if not raw:
        raise TranscriptReviewError("empty timecode")
    if ":" not in raw:
        return float(raw)
    parts = raw.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise TranscriptReviewError(f"bad timecode: {value!r}")


def load_transcript(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    data = _read_json(path)
    if isinstance(data, list):
        wrapper: Dict[str, Any] = {"segments": data}
    elif isinstance(data, dict):
        wrapper = data
    else:
        raise TranscriptReviewError("transcript must be a JSON object or segment list")
    segments = wrapper.get("segments")
    if not isinstance(segments, list):
        raise TranscriptReviewError("transcript must contain a segments list")
    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(segments, start=1):
        if not isinstance(raw, dict):
            continue
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"), start)
        if end <= start:
            continue
        segment = copy.deepcopy(raw)
        segment.setdefault("id", idx)
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
        segment["text"] = _clean_text(segment.get("text", ""))
        normalized.append(segment)
    if not normalized:
        raise TranscriptReviewError("transcript has no valid timed segments")
    wrapper = copy.deepcopy(wrapper)
    wrapper["segments"] = normalized
    return wrapper, normalized


def _parse_text_correction_line(line: str) -> Optional[Tuple[str, str]]:
    for sep in ("=>", "->", "="):
        if sep in line:
            left, right = line.split(sep, 1)
            wrong = left.strip()
            correct = right.strip()
            if wrong:
                return wrong, correct
    return None


def load_corrections(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    raw = _read_json(path) if path.lower().endswith(".json") else None
    corrections: Dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            wrong = str(key).strip()
            if wrong:
                corrections[wrong] = str(value).strip()
        return corrections
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            wrong = str(item.get("wrong") or item.get("from") or "").strip()
            right = str(item.get("right") or item.get("to") or "").strip()
            if wrong:
                corrections[wrong] = right
        return corrections
    if raw is not None:
        raise TranscriptReviewError("corrections JSON must be an object or a list of wrong/right objects")

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = _parse_text_correction_line(line)
            if parsed:
                wrong, right = parsed
                corrections[wrong] = right
    return corrections


def _needs_word_boundary(pattern: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_ -]*", pattern))


def apply_text_corrections(text: str, corrections: Mapping[str, str]) -> Tuple[str, Dict[str, int]]:
    result = str(text)
    applied: Dict[str, int] = {}
    for wrong, right in corrections.items():
        if not wrong:
            continue
        if _needs_word_boundary(wrong):
            pattern = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)")
            result, count = pattern.subn(right, result)
        else:
            count = result.count(wrong)
            result = result.replace(wrong, right)
        if count:
            applied[wrong] = applied.get(wrong, 0) + count
    return result, applied


def _merge_counts(base: Dict[str, int], extra: Mapping[str, int]) -> None:
    for key, count in extra.items():
        base[key] = base.get(key, 0) + int(count)


def _default_review_path(transcript_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(transcript_path)), "transcript_review.txt")


def build_review_lines(
    transcript_path: str,
    segments: Sequence[Mapping[str, Any]],
    corrections: Mapping[str, str],
) -> Tuple[List[str], Dict[str, int]]:
    applied_total: Dict[str, int] = {}
    lines = [
        "# Transcript Review",
        "# Edit only the text after the prefix. Keep [seg:<id> start:<time> end:<time>] unchanged.",
        "# After review, run: python3 scripts/transcript_review.py apply --transcript <json> --review <this-file> --output <reviewed.json>",
        f"# Source: {os.path.abspath(transcript_path)}",
        f"# Generated: {_now_iso()}",
        f"# Version: {VERSION}",
        "",
    ]
    for segment in segments:
        text, applied = apply_text_corrections(_clean_text(segment.get("text", "")), corrections)
        _merge_counts(applied_total, applied)
        lines.append(
            "[seg:{id} start:{start} end:{end}] {text}".format(
                id=segment.get("id"),
                start=format_time(_as_float(segment.get("start"))),
                end=format_time(_as_float(segment.get("end"))),
                text=text,
            )
        )
    lines.extend(["", "# === CORRECTIONS APPLIED ==="])
    if applied_total:
        for wrong, count in sorted(applied_total.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"# {wrong} => {corrections[wrong]} (x{count})")
    else:
        lines.append("# (none)")
    return lines, applied_total


def write_review(path: str, lines: Sequence[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def parse_review(path: str) -> List[Dict[str, Any]]:
    edits: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            raw = line.rstrip("\n")
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = REVIEW_LINE.match(stripped)
            if match:
                edits.append({
                    "line": lineno,
                    "id": match.group("id"),
                    "start": parse_time(match.group("start")),
                    "end": parse_time(match.group("end")),
                    "text": _clean_text(match.group("text")),
                    "format": "seg",
                })
                continue
            match = TIME_ONLY_LINE.match(stripped)
            if match:
                edits.append({
                    "line": lineno,
                    "id": None,
                    "start": parse_time(match.group("start")),
                    "end": None,
                    "text": _clean_text(match.group("text")),
                    "format": "time",
                })
                continue
            raise TranscriptReviewError(f"review line {lineno} is not a recognized transcript line: {raw!r}")
    if not edits:
        raise TranscriptReviewError("review file contains no transcript lines")
    return edits


def tokenize_text(text: str) -> List[str]:
    return TOKEN_RE.findall(_clean_text(text))


def _timed_word_span(segment: Mapping[str, Any]) -> Tuple[float, float]:
    words = segment.get("words")
    if isinstance(words, list):
        timed = [
            word for word in words
            if isinstance(word, dict) and "start" in word and "end" in word
        ]
        if timed:
            start = _as_float(timed[0].get("start"), _as_float(segment.get("start")))
            end = _as_float(timed[-1].get("end"), _as_float(segment.get("end"), start))
            if end > start:
                return start, end
    return _as_float(segment.get("start")), _as_float(segment.get("end"))


def redistribute_words(text: str, segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    tokens = tokenize_text(text)
    if not tokens:
        return []
    start, end = _timed_word_span(segment)
    if end <= start:
        end = start + 0.001
    span = end - start
    weights = [max(1, len(token)) for token in tokens]
    total_weight = float(sum(weights)) or 1.0
    out: List[Dict[str, Any]] = []
    cursor = start
    for token, weight in zip(tokens, weights):
        duration = span * weight / total_weight
        next_time = cursor + duration
        out.append({
            "word": token,
            "start": round(cursor, 3),
            "end": round(next_time, 3),
        })
        cursor = next_time
    out[-1]["end"] = round(end, 3)
    return out


def _segment_lookup(segments: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    by_id = {str(segment.get("id")): segment for segment in segments}
    sorted_segments = sorted(segments, key=lambda seg: (_as_float(seg.get("start")), _as_float(seg.get("end"))))
    return by_id, sorted_segments


def _match_segment(
    edit: Mapping[str, Any],
    by_id: Mapping[str, Dict[str, Any]],
    sorted_segments: Sequence[Dict[str, Any]],
    tolerance: float,
) -> Optional[Dict[str, Any]]:
    edit_id = edit.get("id")
    if edit_id is not None and str(edit_id) in by_id:
        return by_id[str(edit_id)]
    start = _as_float(edit.get("start"))
    if not sorted_segments:
        return None
    best = min(sorted_segments, key=lambda seg: abs(_as_float(seg.get("start")) - start))
    if abs(_as_float(best.get("start")) - start) <= tolerance:
        return best
    return None


def apply_review_edits(
    transcript: Dict[str, Any],
    edits: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 0.75,
    redistribute: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    updated = copy.deepcopy(transcript)
    segments: List[Dict[str, Any]] = updated["segments"]
    by_id, sorted_segments = _segment_lookup(segments)
    changes: List[Dict[str, Any]] = []
    matched_ids = set()
    for edit in edits:
        segment = _match_segment(edit, by_id, sorted_segments, tolerance)
        if segment is None:
            raise TranscriptReviewError(
                f"review line {edit.get('line')} did not match any segment within {tolerance:.2f}s"
            )
        seg_key = str(segment.get("id"))
        if seg_key in matched_ids:
            raise TranscriptReviewError(f"review line {edit.get('line')} duplicates segment {seg_key}")
        matched_ids.add(seg_key)
        before = _clean_text(segment.get("text", ""))
        after = _clean_text(edit.get("text", ""))
        if before != after:
            changes.append({
                "id": segment.get("id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "before": before,
                "after": after,
            })
        segment["text"] = after
        if redistribute:
            segment["words"] = redistribute_words(after, segment)

    summary = {
        "version": VERSION,
        "applied_at": _now_iso(),
        "segments_in_review": len(edits),
        "changed_segments": len(changes),
        "word_timing": "redistributed" if redistribute else "unchanged",
        "changes": changes,
    }
    updated["review"] = summary
    return updated, summary


def cmd_export(args: argparse.Namespace) -> int:
    _transcript, segments = load_transcript(args.transcript)
    corrections = load_corrections(args.corrections)
    lines, applied = build_review_lines(args.transcript, segments, corrections)
    review_path = args.review or _default_review_path(args.transcript)
    write_review(review_path, lines)
    print(f"review file: {review_path}")
    print(f"segments: {len(segments)}")
    print(f"corrections applied: {sum(applied.values())}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    transcript, _segments = load_transcript(args.transcript)
    edits = parse_review(args.review)
    output = args.transcript if args.in_place else args.output
    if not output:
        base, ext = os.path.splitext(args.transcript)
        output = f"{base}_reviewed{ext or '.json'}"
    updated, summary = apply_review_edits(
        transcript,
        edits,
        tolerance=args.tolerance,
        redistribute=not args.keep_words,
    )
    _write_json(output, updated)
    print(f"reviewed transcript: {output}")
    print(f"review lines: {summary['segments_in_review']}")
    print(f"changed segments: {summary['changed_segments']}")
    print(f"word timing: {summary['word_timing']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export/apply editable transcript review files.")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Write transcript_review.txt from transcript JSON.")
    export.add_argument("--transcript", required=True, help="Whisper transcript JSON with segments.")
    export.add_argument("--review", help="Output review text path. Defaults to transcript_review.txt next to transcript.")
    export.add_argument("--corrections", help="Optional corrections JSON/text file: wrong => right.")
    export.set_defaults(func=cmd_export)

    apply = sub.add_parser("apply", help="Apply transcript_review.txt edits back to transcript JSON.")
    apply.add_argument("--transcript", required=True, help="Original transcript JSON.")
    apply.add_argument("--review", required=True, help="Edited review text file.")
    apply.add_argument("--output", help="Reviewed transcript JSON. Defaults to <transcript>_reviewed.json.")
    apply.add_argument("--in-place", action="store_true", help="Overwrite --transcript instead of writing a reviewed copy.")
    apply.add_argument("--tolerance", type=float, default=0.75,
                       help="Fallback start-time matching tolerance in seconds when no segment id is present.")
    apply.add_argument("--keep-words", action="store_true",
                       help="Keep existing words arrays unchanged instead of redistributing timings.")
    apply.set_defaults(func=cmd_apply)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TranscriptReviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
