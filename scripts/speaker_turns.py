#!/usr/bin/env python3
"""Align speaker diarization artifacts to transcript text.

This script does not run diarization models. It accepts local artifacts from
pyannote, WhisperX, diarize, Scribe, Gemini, or any tool that can emit
start/end/speaker segments, then produces reviewable speaker turns and optional
speaker badge cues for render_final.py --enrich-plan.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


VERSION = "speaker_turns.v1"
UNKNOWN_SPEAKER = "UNKNOWN"


@dataclasses.dataclass(frozen=True)
class DiarizationSegment:
    start: float
    end: float
    speaker: str
    source: str = "diarization"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclasses.dataclass(frozen=True)
class TranscriptUnit:
    id: str
    start: float
    end: float
    text: str
    source: str
    existing_speaker: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.001, self.end - self.start)


@dataclasses.dataclass
class AssignedUnit:
    unit: TranscriptUnit
    speaker: str
    coverage: float
    speaker_scores: dict[str, float]
    warnings: list[str]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalize_speaker(value: Any) -> Optional[str]:
    text = _clean_text(value)
    return text if text else None


def _speaker_from(raw: Mapping[str, Any]) -> Optional[str]:
    for key in ("speaker", "speaker_id", "speakerId", "label", "name", "who"):
        speaker = _normalize_speaker(raw.get(key))
        if speaker:
            return speaker
    return None


def _start_from(raw: Mapping[str, Any]) -> Optional[float]:
    for key in ("start", "start_time", "startTime", "begin", "from", "timestamp"):
        value = _as_optional_float(raw.get(key))
        if value is not None:
            return value
    return None


def _end_from(raw: Mapping[str, Any], start: float) -> Optional[float]:
    for key in ("end", "end_time", "endTime", "stop", "to"):
        value = _as_optional_float(raw.get(key))
        if value is not None:
            return value
    duration = _as_optional_float(raw.get("duration"))
    if duration is not None:
        return start + duration
    return None


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _candidate_segment_lists(data: Any) -> list[Any]:
    if isinstance(data, list):
        return [data]
    if not isinstance(data, Mapping):
        return []
    candidates = []
    for key in (
        "segments",
        "speaker_segments",
        "speakerSegments",
        "diarization",
        "turns",
        "entries",
        "items",
    ):
        value = data.get(key)
        if isinstance(value, list):
            candidates.append(value)
    result = data.get("result")
    if isinstance(result, Mapping):
        candidates.extend(_candidate_segment_lists(result))
    return candidates


def normalize_diarization_json(data: Any, *, source: str = "diarization") -> list[DiarizationSegment]:
    segments: list[DiarizationSegment] = []
    for candidate in _candidate_segment_lists(data):
        for idx, raw in enumerate(candidate):
            if not isinstance(raw, Mapping):
                continue
            start = _start_from(raw)
            if start is None:
                continue
            end = _end_from(raw, start)
            if end is None or end <= start:
                continue
            speaker = _speaker_from(raw)
            if not speaker:
                speaker = f"SPEAKER_{idx:02d}"
            segments.append(DiarizationSegment(start=start, end=end, speaker=speaker, source=source))
    return sorted(segments, key=lambda item: (item.start, item.end, item.speaker))


def parse_rttm_lines(lines: Iterable[str], *, source: str = "rttm") -> list[DiarizationSegment]:
    segments: list[DiarizationSegment] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 8 or parts[0].upper() != "SPEAKER":
            continue
        start = _as_optional_float(parts[3])
        duration = _as_optional_float(parts[4])
        if start is None or duration is None or duration < 0:
            continue
        speaker = _normalize_speaker(parts[7]) or UNKNOWN_SPEAKER
        segments.append(DiarizationSegment(start=start, end=start + duration, speaker=speaker, source=source))
    return sorted(segments, key=lambda item: (item.start, item.end, item.speaker))


def load_diarization(path: Optional[str], rttm_path: Optional[str]) -> list[DiarizationSegment]:
    segments: list[DiarizationSegment] = []
    if path:
        segments.extend(normalize_diarization_json(_read_json(path), source=os.path.basename(path)))
    if rttm_path:
        with open(rttm_path, encoding="utf-8") as f:
            segments.extend(parse_rttm_lines(f, source=os.path.basename(rttm_path)))
    return sorted(segments, key=lambda item: (item.start, item.end, item.speaker))


def _word_text(raw: Mapping[str, Any]) -> str:
    text = raw.get("word")
    if text is None:
        text = raw.get("text")
    if text is None:
        text = raw.get("content")
    text = _clean_text(text)
    token_type = str(raw.get("type") or "word")
    if token_type == "audio_event" and text and not text.startswith("("):
        return f"({text})"
    return text


def _word_units_from(raw_words: Iterable[Mapping[str, Any]], *, prefix: str) -> list[TranscriptUnit]:
    units: list[TranscriptUnit] = []
    for idx, raw in enumerate(raw_words):
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("type") or "word") == "spacing":
            continue
        text = _word_text(raw)
        if not text:
            continue
        start = _start_from(raw)
        if start is None:
            continue
        end = _end_from(raw, start)
        if end is None or end <= start:
            end = start + 0.05
        units.append(TranscriptUnit(
            id=f"{prefix}.w{idx + 1}",
            start=start,
            end=end,
            text=text,
            source="word",
            existing_speaker=_speaker_from(raw),
        ))
    return units


def extract_transcript_units(data: Mapping[str, Any]) -> list[TranscriptUnit]:
    word_units: list[TranscriptUnit] = []
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    for seg_idx, seg in enumerate(segments):
        if not isinstance(seg, Mapping):
            continue
        words = seg.get("words")
        if isinstance(words, list):
            word_units.extend(_word_units_from(words, prefix=f"s{seg_idx + 1}"))

    top_words = data.get("words")
    if isinstance(top_words, list):
        word_units.extend(_word_units_from(top_words, prefix="top"))

    if word_units:
        return sorted(word_units, key=lambda item: (item.start, item.end, item.id))

    units: list[TranscriptUnit] = []
    for seg_idx, seg in enumerate(segments):
        if not isinstance(seg, Mapping):
            continue
        text = _clean_text(seg.get("text") or seg.get("transcript"))
        if not text:
            continue
        start = _start_from(seg)
        if start is None:
            continue
        end = _end_from(seg, start)
        if end is None or end <= start:
            continue
        units.append(TranscriptUnit(
            id=str(seg.get("id") or seg_idx + 1),
            start=start,
            end=end,
            text=text,
            source="segment",
            existing_speaker=_speaker_from(seg),
        ))

    if not units and _clean_text(data.get("text")):
        start = _start_from(data) or 0.0
        end = _end_from(data, start) or start
        if end > start:
            units.append(TranscriptUnit(
                id="1",
                start=start,
                end=end,
                text=_clean_text(data.get("text")),
                source="transcript",
                existing_speaker=_speaker_from(data),
            ))

    return sorted(units, key=lambda item: (item.start, item.end, item.id))


def _overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _nearest_speaker(unit: TranscriptUnit, diarization: Sequence[DiarizationSegment]) -> Optional[str]:
    if not diarization:
        return None
    mid = (unit.start + unit.end) / 2.0
    nearest = min(diarization, key=lambda seg: abs(((seg.start + seg.end) / 2.0) - mid))
    return nearest.speaker


def assign_unit_speaker(
    unit: TranscriptUnit,
    diarization: Sequence[DiarizationSegment],
    *,
    fill_nearest: bool = False,
    low_coverage_threshold: float = 0.50,
    mixed_threshold: float = 0.25,
) -> AssignedUnit:
    warnings: list[str] = []
    scores: dict[str, float] = defaultdict(float)

    for segment in diarization:
        if segment.start >= unit.end:
            break
        if segment.end <= unit.start:
            continue
        overlap = _overlap_seconds(unit.start, unit.end, segment.start, segment.end)
        if overlap > 0:
            scores[segment.speaker] += overlap

    if scores:
        speaker = max(scores.items(), key=lambda item: item[1])[0]
        coverage = min(1.0, sum(scores.values()) / unit.duration)
        if coverage < low_coverage_threshold:
            warnings.append("low_diarization_overlap")
        ordered = sorted(scores.values(), reverse=True)
        if len(ordered) > 1 and ordered[1] / max(sum(ordered), 0.001) >= mixed_threshold:
            warnings.append("mixed_speakers")
    elif unit.existing_speaker:
        speaker = unit.existing_speaker
        coverage = 1.0
    elif fill_nearest:
        nearest = _nearest_speaker(unit, diarization)
        if nearest:
            speaker = nearest
            coverage = 0.0
            warnings.append("nearest_speaker_fill")
        else:
            speaker = UNKNOWN_SPEAKER
            coverage = 0.0
            warnings.append("unlabeled")
    else:
        speaker = UNKNOWN_SPEAKER
        coverage = 0.0
        warnings.append("unlabeled")

    return AssignedUnit(
        unit=unit,
        speaker=speaker,
        coverage=coverage,
        speaker_scores=dict(scores),
        warnings=warnings,
    )


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _join_text_parts(parts: Sequence[str]) -> str:
    out = ""
    closing = set(",.!?;:%)]}，。！？；：、）》】")
    opening = set("([{（《【")
    for raw in parts:
        part = _clean_text(raw)
        if not part:
            continue
        if not out:
            out = part
            continue
        if part[0] in closing or out[-1] in opening or _has_cjk(out[-1] + part[0]):
            out += part
        else:
            out += " " + part
    out = re.sub(r"\s+([,.;:!?%])", r"\1", out)
    return out.strip()


def build_turns(
    assigned_units: Sequence[AssignedUnit],
    *,
    merge_gap: float = 0.50,
) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        current["text"] = _join_text_parts(current.pop("_parts"))
        duration = max(0.0, current["end"] - current["start"])
        current["duration"] = round(duration, 3)
        weighted = current.pop("_coverage_weight")
        current["confidence"] = round(current.pop("_coverage_sum") / weighted, 3) if weighted else 0.0
        current["warnings"] = sorted(set(current.get("warnings") or []))
        current["source_unit_ids"] = list(current["source_unit_ids"])
        turns.append(current)
        current = None

    for assigned in sorted(assigned_units, key=lambda item: (item.unit.start, item.unit.end)):
        unit = assigned.unit
        gap = unit.start - current["end"] if current else 0.0
        if (
            current
            and assigned.speaker == current["speaker"]
            and gap <= merge_gap
        ):
            current["end"] = max(current["end"], unit.end)
            current["_parts"].append(unit.text)
            current["source_unit_ids"].append(unit.id)
            current["_coverage_sum"] += assigned.coverage * unit.duration
            current["_coverage_weight"] += unit.duration
            current["warnings"].extend(assigned.warnings)
        else:
            flush()
            current = {
                "id": f"turn_{len(turns) + 1:03d}",
                "speaker": assigned.speaker,
                "start": round(unit.start, 3),
                "end": round(unit.end, 3),
                "_parts": [unit.text],
                "source_unit_ids": [unit.id],
                "_coverage_sum": assigned.coverage * unit.duration,
                "_coverage_weight": unit.duration,
                "warnings": list(assigned.warnings),
            }

    flush()
    return turns


def load_speaker_map(path: Optional[str]) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    data = _read_json(path)
    raw = data.get("speakers") if isinstance(data, Mapping) and "speakers" in data else data
    mapping: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(value, Mapping):
                mapping[str(key)] = dict(value)
            else:
                mapping[str(key)] = {"name": str(value)}
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            label = _speaker_from(item) or _normalize_speaker(item.get("id"))
            if label:
                mapping[label] = dict(item)
    return mapping


def decorate_turns(turns: list[dict[str, Any]], speaker_map: Mapping[str, Mapping[str, Any]]) -> None:
    for turn in turns:
        speaker = str(turn.get("speaker") or UNKNOWN_SPEAKER)
        raw_meta = speaker_map.get(speaker, {})
        meta = raw_meta if isinstance(raw_meta, Mapping) else {"name": raw_meta}
        turn["display_name"] = str(
            meta.get("display_name")
            or meta.get("name")
            or meta.get("label")
            or speaker
        )
        if meta.get("role"):
            turn["role"] = meta.get("role")
        if meta.get("color"):
            turn["color"] = meta.get("color")


def detect_crosstalk(
    diarization: Sequence[DiarizationSegment],
    *,
    threshold: float = 0.20,
) -> list[dict[str, Any]]:
    segments = sorted(diarization, key=lambda item: (item.start, item.end))
    events: list[dict[str, Any]] = []
    for idx, left in enumerate(segments):
        for right in segments[idx + 1:]:
            if right.start >= left.end:
                break
            if right.speaker == left.speaker:
                continue
            overlap = _overlap_seconds(left.start, left.end, right.start, right.end)
            if overlap >= threshold:
                events.append({
                    "start": round(max(left.start, right.start), 3),
                    "end": round(min(left.end, right.end), 3),
                    "duration": round(overlap, 3),
                    "speakers": sorted([left.speaker, right.speaker]),
                })
    return events


def summarize(
    turns: Sequence[Mapping[str, Any]],
    units: Sequence[TranscriptUnit],
    crosstalk: Sequence[Mapping[str, Any]],
    *,
    min_speakers: int = 1,
    max_unlabeled_ratio: float = 0.20,
) -> dict[str, Any]:
    durations: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    words: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    for turn in turns:
        speaker = str(turn.get("speaker") or UNKNOWN_SPEAKER)
        duration = _as_float(turn.get("duration"))
        durations[speaker] += duration
        counts[speaker] += 1
        words[speaker] += len(str(turn.get("text") or "").split())
        for warning in turn.get("warnings") or []:
            warning_counts[str(warning)] += 1

    total_duration = sum(durations.values())
    unlabeled_duration = durations.get(UNKNOWN_SPEAKER, 0.0)
    known_speakers = sorted(s for s in durations if s != UNKNOWN_SPEAKER)
    detected_speakers = len(known_speakers)
    unlabeled_ratio = unlabeled_duration / total_duration if total_duration else 0.0

    blocking_reasons: list[str] = []
    if detected_speakers < min_speakers:
        blocking_reasons.append("speaker_count_below_minimum")
    if total_duration and unlabeled_ratio > max_unlabeled_ratio:
        blocking_reasons.append("unlabeled_ratio_too_high")

    speaker_stats = []
    for speaker, duration in sorted(durations.items(), key=lambda item: item[1], reverse=True):
        speaker_stats.append({
            "speaker": speaker,
            "duration": round(duration, 3),
            "turns": counts[speaker],
            "words": words[speaker],
            "talk_ratio": round(duration / total_duration, 3) if total_duration else 0.0,
        })

    return {
        "turns": len(turns),
        "transcript_units": len(units),
        "detected_speakers": detected_speakers,
        "known_speakers": known_speakers,
        "total_turn_duration": round(total_duration, 3),
        "unlabeled_duration": round(unlabeled_duration, 3),
        "unlabeled_ratio": round(unlabeled_ratio, 3),
        "crosstalk_events": len(crosstalk),
        "crosstalk_seconds": round(sum(_as_float(item.get("duration")) for item in crosstalk), 3),
        "warnings": dict(sorted(warning_counts.items())),
        "speaker_stats": speaker_stats,
        "blocking": len(blocking_reasons),
        "blocking_reasons": blocking_reasons,
    }


def build_speaker_turns(
    transcript_data: Mapping[str, Any],
    diarization: Optional[Sequence[DiarizationSegment]] = None,
    *,
    speaker_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
    merge_gap: float = 0.50,
    fill_nearest: bool = False,
    low_coverage_threshold: float = 0.50,
    mixed_threshold: float = 0.25,
    crosstalk_threshold: float = 0.20,
    min_speakers: int = 1,
    max_unlabeled_ratio: float = 0.20,
) -> dict[str, Any]:
    units = extract_transcript_units(transcript_data)
    diarization = list(diarization or [])
    assigned = [
        assign_unit_speaker(
            unit,
            diarization,
            fill_nearest=fill_nearest,
            low_coverage_threshold=low_coverage_threshold,
            mixed_threshold=mixed_threshold,
        )
        for unit in units
    ]
    turns = build_turns(assigned, merge_gap=merge_gap)
    decorate_turns(turns, speaker_map or {})
    crosstalk = detect_crosstalk(diarization, threshold=crosstalk_threshold)
    summary = summarize(
        turns,
        units,
        crosstalk,
        min_speakers=min_speakers,
        max_unlabeled_ratio=max_unlabeled_ratio,
    )
    return {
        "version": VERSION,
        "summary": summary,
        "turns": turns,
        "crosstalk": crosstalk,
        "diarization_segments": [
            dataclasses.asdict(segment) for segment in diarization
        ],
    }


def build_enrich_plan(
    report: Mapping[str, Any],
    *,
    badge_duration: float = 2.20,
    min_gap: float = 4.0,
) -> dict[str, Any]:
    badges = []
    last_by_speaker: dict[str, float] = {}
    previous_speaker: Optional[str] = None
    for turn in report.get("turns") or []:
        speaker = str(turn.get("speaker") or UNKNOWN_SPEAKER)
        if speaker == UNKNOWN_SPEAKER:
            previous_speaker = speaker
            continue
        start = _as_float(turn.get("start"))
        end = _as_float(turn.get("end"), start)
        if end <= start:
            continue
        last_start = last_by_speaker.get(speaker)
        if speaker == previous_speaker and last_start is not None and start - last_start < min_gap:
            continue
        duration = min(badge_duration, max(0.6, end - start))
        badges.append({
            "text": str(turn.get("display_name") or speaker),
            "start": round(start, 3),
            "end": round(start + duration, 3),
            "speaker": speaker,
            "source": "speaker_turns",
            "fade_in": 120,
            "fade_out": 160,
        })
        last_by_speaker[speaker] = start
        previous_speaker = speaker
    return {
        "version": "speaker_turns.enrich_plan.v1",
        "text_badges": badges,
        "speaker_turns": [
            {
                "id": turn.get("id"),
                "speaker": turn.get("speaker"),
                "display_name": turn.get("display_name"),
                "start": turn.get("start"),
                "end": turn.get("end"),
            }
            for turn in report.get("turns") or []
        ],
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Speaker Turns",
        "",
        f"- Version: `{report.get('version', VERSION)}`",
        f"- Turns: {summary.get('turns', 0)}",
        f"- Detected speakers: {summary.get('detected_speakers', 0)}",
        f"- Crosstalk events: {summary.get('crosstalk_events', 0)}",
        f"- Unlabeled ratio: {summary.get('unlabeled_ratio', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "## Speaker Stats",
        "",
        "| speaker | duration | turns | talk ratio | words |",
        "|---|---:|---:|---:|---:|",
    ]

    for item in summary.get("speaker_stats") or []:
        lines.append(
            "| {speaker} | {duration:.1f}s | {turns} | {ratio:.1%} | {words} |".format(
                speaker=item.get("speaker", ""),
                duration=_as_float(item.get("duration")),
                turns=int(item.get("turns") or 0),
                ratio=_as_float(item.get("talk_ratio")),
                words=int(item.get("words") or 0),
            )
        )

    lines.extend([
        "",
        "## Turns",
        "",
        "| id | time | speaker | text | warnings |",
        "|---|---|---|---|---|",
    ])
    for turn in report.get("turns") or []:
        text = str(turn.get("text") or "").replace("|", "\\|")
        if len(text) > 120:
            text = text[:117].rstrip() + "..."
        warnings = ", ".join(turn.get("warnings") or []) or "-"
        speaker = str(turn.get("display_name") or turn.get("speaker") or "")
        lines.append(
            "| {id} | {start:.2f}-{end:.2f} | {speaker} | {text} | {warnings} |".format(
                id=turn.get("id", ""),
                start=_as_float(turn.get("start")),
                end=_as_float(turn.get("end")),
                speaker=speaker.replace("|", "\\|"),
                text=text,
                warnings=warnings.replace("|", "\\|"),
            )
        )

    if report.get("crosstalk"):
        lines.extend(["", "## Crosstalk", "", "| time | speakers | duration |", "|---|---|---:|"])
        for item in report.get("crosstalk") or []:
            lines.append(
                "| {start:.2f}-{end:.2f} | {speakers} | {duration:.2f}s |".format(
                    start=_as_float(item.get("start")),
                    end=_as_float(item.get("end")),
                    speakers=", ".join(item.get("speakers") or []),
                    duration=_as_float(item.get("duration")),
                )
            )

    blockers = summary.get("blocking_reasons") or []
    if blockers:
        lines.extend(["", "## Blocking Reasons", ""])
        lines.extend(f"- {reason}" for reason in blockers)

    return "\n".join(lines).rstrip() + "\n"


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align speaker diarization to transcript JSON and emit review artifacts."
    )
    parser.add_argument("--transcript", required=True, help="Transcript JSON path.")
    parser.add_argument("--diarization", help="Diarization JSON path with start/end/speaker segments.")
    parser.add_argument("--rttm", help="RTTM diarization path.")
    parser.add_argument("--speaker-map", help="Optional JSON mapping speaker IDs to names/colors.")
    parser.add_argument("--output", default="speaker_turns.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--enrich-plan", help="Optional speaker badge enrich-plan JSON path.")
    parser.add_argument("--merge-gap", type=float, default=0.50, help="Merge same-speaker units across gaps <= seconds.")
    parser.add_argument("--fill-nearest", action="store_true", help="Assign nearest diarization speaker when no overlap exists.")
    parser.add_argument("--min-speakers", type=int, default=1, help="Minimum expected detected speakers.")
    parser.add_argument("--max-unlabeled-ratio", type=float, default=0.20, help="Strict-mode max unlabeled duration ratio.")
    parser.add_argument("--low-coverage-threshold", type=float, default=0.50, help="Warn below this overlap coverage.")
    parser.add_argument("--mixed-threshold", type=float, default=0.25, help="Warn when runner-up speaker overlap share crosses this.")
    parser.add_argument("--crosstalk-threshold", type=float, default=0.20, help="Minimum diarization overlap to record crosstalk.")
    parser.add_argument("--badge-duration", type=float, default=2.20, help="Speaker badge duration in enrich plan.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    transcript = _read_json(args.transcript)
    if not isinstance(transcript, Mapping):
        print("Transcript JSON must be an object.", file=sys.stderr)
        return 2

    diarization = load_diarization(args.diarization, args.rttm)
    speaker_map = load_speaker_map(args.speaker_map)
    report = build_speaker_turns(
        transcript,
        diarization,
        speaker_map=speaker_map,
        merge_gap=args.merge_gap,
        fill_nearest=args.fill_nearest,
        low_coverage_threshold=args.low_coverage_threshold,
        mixed_threshold=args.mixed_threshold,
        crosstalk_threshold=args.crosstalk_threshold,
        min_speakers=args.min_speakers,
        max_unlabeled_ratio=args.max_unlabeled_ratio,
    )

    write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))
    if args.enrich_plan:
        write_json(args.enrich_plan, build_enrich_plan(report, badge_duration=args.badge_duration))

    summary = report["summary"]
    print(
        "Speaker turns: "
        f"turns={summary['turns']} "
        f"speakers={summary['detected_speakers']} "
        f"crosstalk={summary['crosstalk_events']} "
        f"blocking={summary['blocking']}",
        file=sys.stderr,
    )
    if args.strict and summary.get("blocking"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
