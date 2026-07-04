#!/usr/bin/env python3
"""Pack one or more transcripts into a phrase-level multi-take reading view.

The output is deliberately small and local: a Markdown file for agent/human
review, plus an optional JSON artifact that other scripts can discover.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "takes_pack.v1"


@dataclass(frozen=True)
class TimedUnit:
    source_label: str
    segment_id: Any
    start: float
    end: float
    text: str
    speaker: str = ""


def _round3(value: float) -> float:
    return round(float(value), 3)


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("\ufeff", "").strip()


def _escape_md(value: Any) -> str:
    text = _clean_text(value)
    return text.replace("|", "\\|")


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remaining = seconds - minutes * 60
    return f"{minutes:02d}:{remaining:05.2f}"


def _label_from_path(path: str) -> str:
    stem = Path(path).stem
    for suffix in ("_transcript_reviewed", "_reviewed_transcript", "_transcript", "-transcript"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)] or stem
    return stem


def _parse_transcript_arg(value: str) -> Tuple[Optional[str], str]:
    if "=" in value:
        label, path = value.split("=", 1)
        if label.strip() and path.strip():
            return label.strip(), path.strip()
    return None, value


def _join_tokens(tokens: Iterable[str]) -> str:
    output = ""
    for raw in tokens:
        token = _clean_text(raw)
        if not token:
            continue
        if not output:
            output = token
            continue
        if re.match(r"^[,.;:!?，。！？、；：）)\]}%]", token):
            output += token
        elif re.search(r"[\u4e00-\u9fff]$", output) or re.match(r"^[\u4e00-\u9fff]", token):
            output += token
        else:
            output += " " + token
    return output.strip()


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"transcript must be a JSON object: {path}")
    return payload


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _word_units(segment: Mapping[str, Any], source_label: str, segment_id: Any) -> List[TimedUnit]:
    units: List[TimedUnit] = []
    for word in segment.get("words") or []:
        if not isinstance(word, Mapping):
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = _clean_text(word.get("word", word.get("text", "")))
        if end <= start or not text:
            continue
        speaker = _clean_text(word.get("speaker") or segment.get("speaker") or "")
        units.append(
            TimedUnit(
                source_label=source_label,
                segment_id=segment_id,
                start=_round3(start),
                end=_round3(end),
                text=text,
                speaker=speaker,
            )
        )
    return units


def timed_units_from_transcript(transcript: Mapping[str, Any], source_label: str) -> List[TimedUnit]:
    units: List[TimedUnit] = []
    for pos, segment in enumerate(transcript.get("segments") or [], start=1):
        if not isinstance(segment, Mapping):
            continue
        segment_id = segment.get("id", pos)
        words = _word_units(segment, source_label, segment_id)
        if words:
            units.extend(words)
            continue
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad transcript segment in {source_label}: {segment!r}") from exc
        text = _clean_text(segment.get("text", ""))
        if end <= start or not text:
            continue
        units.append(
            TimedUnit(
                source_label=source_label,
                segment_id=segment_id,
                start=_round3(start),
                end=_round3(end),
                text=text,
                speaker=_clean_text(segment.get("speaker", "")),
            )
        )
    return sorted(units, key=lambda item: (item.start, item.end, str(item.segment_id)))


def _phrase_text(units: Sequence[TimedUnit]) -> str:
    return _join_tokens(unit.text for unit in units)


def _segment_ids(units: Sequence[TimedUnit]) -> List[Any]:
    seen: List[Any] = []
    for unit in units:
        if unit.segment_id not in seen:
            seen.append(unit.segment_id)
    return seen


def _phrase_speaker(units: Sequence[TimedUnit]) -> str:
    speakers = [unit.speaker for unit in units if unit.speaker]
    if not speakers:
        return ""
    return speakers[0] if all(speaker == speakers[0] for speaker in speakers) else "mixed"


def _phrase_dict(units: Sequence[TimedUnit], local_index: int) -> Dict[str, Any]:
    start = units[0].start
    end = units[-1].end
    source_label = units[0].source_label
    return {
        "id": f"{source_label}-{local_index:03d}",
        "source_label": source_label,
        "index": local_index,
        "start": _round3(start),
        "end": _round3(end),
        "duration": _round3(end - start),
        "speaker": _phrase_speaker(units),
        "segment_ids": _segment_ids(units),
        "text": _phrase_text(units),
    }


def pack_source_phrases(
    units: Sequence[TimedUnit],
    *,
    break_gap: float = 0.5,
    max_phrase_chars: int = 220,
) -> List[Dict[str, Any]]:
    phrases: List[Dict[str, Any]] = []
    current: List[TimedUnit] = []

    for unit in units:
        should_break = False
        if current:
            previous = current[-1]
            gap = unit.start - previous.end
            current_text = _phrase_text(current)
            current_speaker = _phrase_speaker(current)
            should_break = (
                gap >= break_gap
                or bool(current_speaker and unit.speaker and current_speaker != unit.speaker)
                or bool(max_phrase_chars and len(current_text) + len(unit.text) + 1 > max_phrase_chars)
            )
        if should_break and current:
            phrases.append(_phrase_dict(current, len(phrases) + 1))
            current = []
        current.append(unit)

    if current:
        phrases.append(_phrase_dict(current, len(phrases) + 1))
    return phrases


def _source_media(transcript: Mapping[str, Any]) -> str:
    source = transcript.get("source")
    if isinstance(source, Mapping):
        for key in ("path", "media", "video", "audio"):
            if source.get(key):
                return str(source[key])
    for key in ("source_path", "media_path", "video", "audio", "input"):
        if transcript.get(key):
            return str(transcript[key])
    return ""


def build_takes_pack(
    transcript_args: Sequence[str],
    *,
    break_gap: float = 0.5,
    max_phrase_chars: int = 220,
) -> Dict[str, Any]:
    sources: List[Dict[str, Any]] = []
    phrases: List[Dict[str, Any]] = []

    if not transcript_args:
        raise ValueError("at least one --transcript or --transcripts-dir result is required")

    for value in transcript_args:
        explicit_label, path = _parse_transcript_arg(value)
        transcript = _load_json(path)
        label = explicit_label or _clean_text(transcript.get("label")) or _label_from_path(path)
        units = timed_units_from_transcript(transcript, label)
        source_phrases = pack_source_phrases(
            units,
            break_gap=break_gap,
            max_phrase_chars=max_phrase_chars,
        )
        phrases.extend(source_phrases)
        sources.append(
            {
                "label": label,
                "transcript": os.path.abspath(path),
                "source_media": _source_media(transcript),
                "duration": _round3(float(transcript.get("duration") or (units[-1].end if units else 0.0))),
                "segments": len(transcript.get("segments") or []),
                "timed_units": len(units),
                "phrases": len(source_phrases),
            }
        )

    warnings: List[str] = []
    if len(sources) == 1:
        warnings.append("only one transcript supplied; pack is still valid but multi-take comparison is limited")
    if not phrases:
        warnings.append("no timed phrases could be built from the supplied transcripts")

    return {
        "version": VERSION,
        "params": {
            "break_gap": break_gap,
            "max_phrase_chars": max_phrase_chars,
        },
        "summary": {
            "sources": len(sources),
            "phrases": len(phrases),
            "warnings": len(warnings),
        },
        "sources": sources,
        "phrases": phrases,
        "warnings": warnings,
    }


def emit_markdown(pack: Mapping[str, Any]) -> str:
    summary = pack.get("summary") or {}
    params = pack.get("params") or {}
    lines = [
        "# Takes Pack",
        "",
        f"- Sources: {summary.get('sources', 0)}",
        f"- Phrases: {summary.get('phrases', 0)}",
        f"- Break gap: {params.get('break_gap', 0.5)}s",
        f"- Max phrase chars: {params.get('max_phrase_chars', 220)}",
        "",
        "Use phrase IDs or source time ranges when building highlight candidates, render_config clips, EDL/FCPXML/OTIO handoff, or a human review brief.",
        "",
    ]
    warnings = pack.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    phrases_by_source: Dict[str, List[Mapping[str, Any]]] = {}
    for phrase in pack.get("phrases") or []:
        if isinstance(phrase, Mapping):
            phrases_by_source.setdefault(str(phrase.get("source_label") or ""), []).append(phrase)

    for source in pack.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        label = str(source.get("label") or "")
        lines.extend(
            [
                f"## {label}",
                "",
                f"- Transcript: `{source.get('transcript', '')}`",
                f"- Source media: `{source.get('source_media', '')}`" if source.get("source_media") else "- Source media: not recorded",
                f"- Duration: {_format_time(float(source.get('duration') or 0.0))}",
                f"- Segments / timed units / phrases: {source.get('segments', 0)} / {source.get('timed_units', 0)} / {source.get('phrases', 0)}",
                "",
                "| ID | Time | Speaker | Segments | Text |",
                "|---|---:|---|---|---|",
            ]
        )
        for phrase in phrases_by_source.get(label, []):
            time_range = f"{_format_time(float(phrase.get('start') or 0.0))}-{_format_time(float(phrase.get('end') or 0.0))}"
            segment_ids = ", ".join(str(item) for item in phrase.get("segment_ids") or [])
            lines.append(
                f"| `{_escape_md(phrase.get('id', ''))}` | {time_range} | {_escape_md(phrase.get('speaker', ''))} | {_escape_md(segment_ids)} | {_escape_md(phrase.get('text', ''))} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_transcript_args(transcripts: Sequence[str], transcript_dirs: Sequence[str]) -> List[str]:
    values = list(transcripts)
    for directory in transcript_dirs:
        root = Path(directory)
        if not root.is_dir():
            raise FileNotFoundError(f"--transcripts-dir is not a directory: {directory}")
        for path in sorted(root.rglob("*transcript*.json")):
            values.append(str(path))

    deduped: List[str] = []
    seen_paths = set()
    for value in values:
        label, path = _parse_transcript_arg(value)
        resolved = str(Path(path).resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        deduped.append(f"{label}={path}" if label else path)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a phrase-level Markdown/JSON reading pack from one or more transcript JSON files."
    )
    parser.add_argument(
        "--transcript",
        action="append",
        default=[],
        help="Transcript JSON path. Repeatable. Use label=/path/transcript.json to set a take label.",
    )
    parser.add_argument(
        "--transcripts-dir",
        action="append",
        default=[],
        help="Directory to scan recursively for *transcript*.json files.",
    )
    parser.add_argument("--output", required=True, help="Output Markdown path.")
    parser.add_argument("--json", dest="json_output", help="Optional output JSON path.")
    parser.add_argument("--break-gap", type=float, default=0.5, help="Start a new phrase after this silence gap in seconds.")
    parser.add_argument("--max-phrase-chars", type=int, default=220, help="Start a new phrase when text exceeds this length.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when no phrases can be built.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        transcript_args = collect_transcript_args(args.transcript, args.transcripts_dir)
        pack = build_takes_pack(
            transcript_args,
            break_gap=args.break_gap,
            max_phrase_chars=args.max_phrase_chars,
        )
    except Exception as exc:
        print(f"takes_pack failed: {exc}", file=sys.stderr)
        return 1

    _write_text(args.output, emit_markdown(pack))
    if args.json_output:
        _write_json(args.json_output, pack)

    summary = pack["summary"]
    print(
        f"Wrote takes pack: sources={summary['sources']} phrases={summary['phrases']} warnings={summary['warnings']}",
        file=sys.stderr,
    )
    if args.strict and summary["phrases"] == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
