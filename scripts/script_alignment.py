#!/usr/bin/env python3
"""Align a reviewed target script to timestamped source transcripts.

The matcher is deliberately local and auditable. It does not call an LLM or
render media. Instead, it ranks word/segment-boundary source ranges, exposes
score components and alternatives, and can emit a render_final.py config after
ambiguous matches have been confirmed with a small choices JSON file.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from takes_pack import TimedUnit, timed_units_from_transcript


VERSION = "script_alignment.v1"


@dataclass(frozen=True)
class TargetUnit:
    id: str
    index: int
    text: str
    section: str = ""


@dataclass(frozen=True)
class TranscriptSource:
    label: str
    transcript: str
    source_media: str
    units: Tuple[TimedUnit, ...]
    timing_granularity: str


def _round3(value: float) -> float:
    return round(float(value), 3)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", " ")).strip()


def _escape_md(value: Any) -> str:
    return _clean_text(value).replace("|", "\\|").replace("`", "\\`")


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:05.2f}"


def _parse_labeled_path(value: str) -> Tuple[Optional[str], str]:
    if "=" in value:
        label, path = value.split("=", 1)
        if label.strip() and path.strip():
            return label.strip(), path.strip()
    return None, value


def _label_from_path(path: str) -> str:
    stem = Path(path).stem
    for suffix in ("_transcript_reviewed", "_reviewed_transcript", "_transcript", "-transcript"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)] or stem
    return stem


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")
    return slug or "source"


def _normalize_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).casefold()
    return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _join_tokens(values: Iterable[str]) -> str:
    output = ""
    for raw in values:
        token = _clean_text(raw)
        if not token:
            continue
        if not output:
            output = token
        elif re.match(r"^[,.;:!?，。！？、；：）)\]}%]", token):
            output += token
        elif re.search(r"[\u4e00-\u9fff]$", output) or re.match(r"^[\u4e00-\u9fff]", token):
            output += token
        else:
            output += " " + token
    return output.strip()


def _strip_script_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*)", "", line)
    line = re.sub(r"^\[[ xX]\]\s*", "", line)
    line = line.replace("**", "").replace("__", "").replace("`", "")
    return _clean_text(line)


def parse_target_script(path: str, *, unit_mode: str = "line") -> List[TargetUnit]:
    raw = Path(path).expanduser().read_text(encoding="utf-8")
    entries: List[Tuple[str, str]] = []
    section = ""
    in_fence = False

    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            section = _clean_text(stripped.lstrip("#"))
            continue
        text = _strip_script_line(raw_line)
        if text:
            entries.append((section, text))

    expanded: List[Tuple[str, str]] = []
    if unit_mode == "sentence":
        for entry_section, text in entries:
            parts = re.findall(r".+?(?:[。！？!?；;]|\.(?=\s|$)|$)", text)
            expanded.extend((entry_section, _clean_text(part)) for part in parts if _clean_text(part))
    else:
        expanded = entries

    targets = [
        TargetUnit(id=f"target-{index:03d}", index=index, text=text, section=entry_section)
        for index, (entry_section, text) in enumerate(expanded, start=1)
        if _normalize_match_text(text)
    ]
    if not targets:
        raise ValueError("target script contains no matchable spoken lines")
    return targets


def _source_media_from_transcript(payload: Mapping[str, Any]) -> str:
    source = payload.get("source")
    if isinstance(source, Mapping):
        for key in ("path", "media", "video", "audio"):
            if source.get(key):
                return str(source[key])
    for key in ("source_path", "media_path", "video", "audio", "input"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _resolve_media_path(raw_path: str, transcript_path: str) -> str:
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(transcript_path).expanduser().resolve().parent / path
    return str(path.resolve())


def _has_word_timing(payload: Mapping[str, Any]) -> bool:
    if isinstance(payload.get("words"), list):
        return any(isinstance(item, Mapping) and "start" in item and "end" in item for item in payload["words"])
    for segment in payload.get("segments") or []:
        if isinstance(segment, Mapping) and isinstance(segment.get("words"), list):
            if any(isinstance(item, Mapping) and "start" in item and "end" in item for item in segment["words"]):
                return True
    return False


def collect_transcript_args(transcripts: Sequence[str], transcript_dirs: Sequence[str]) -> List[str]:
    values = list(transcripts)
    for directory in transcript_dirs:
        root = Path(directory).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"--transcripts-dir is not a directory: {directory}")
        values.extend(str(path) for path in sorted(root.rglob("*transcript*.json")))

    deduped: List[str] = []
    seen_paths = set()
    for value in values:
        label, path = _parse_labeled_path(value)
        resolved = str(Path(path).expanduser().resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        deduped.append(f"{label}={path}" if label else path)
    if not deduped:
        raise ValueError("at least one --transcript or --transcripts-dir result is required")
    return deduped


def parse_media_overrides(values: Sequence[str]) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for value in values:
        label, path = _parse_labeled_path(value)
        if not label:
            raise ValueError("--media must use label=/path/to/media.mp4")
        if label in overrides:
            raise ValueError(f"duplicate --media label: {label}")
        overrides[label] = str(Path(path).expanduser().resolve())
    return overrides


def load_sources(transcript_args: Sequence[str], media_overrides: Mapping[str, str]) -> List[TranscriptSource]:
    sources: List[TranscriptSource] = []
    seen_labels = set()
    for value in transcript_args:
        explicit_label, raw_path = _parse_labeled_path(value)
        transcript_path = str(Path(raw_path).expanduser().resolve())
        with open(transcript_path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError(f"transcript must be a JSON object: {transcript_path}")
        label = explicit_label or _clean_text(payload.get("label")) or _label_from_path(transcript_path)
        if label in seen_labels:
            raise ValueError(f"duplicate transcript label: {label}; use label=path to disambiguate")
        seen_labels.add(label)
        units = tuple(
            unit
            for unit in timed_units_from_transcript(payload, label)
            if unit.kind != "audio_event" and _normalize_match_text(unit.text)
        )
        if not units:
            raise ValueError(f"transcript has no timed spoken units: {transcript_path}")
        media = media_overrides.get(label)
        if not media:
            media = _resolve_media_path(_source_media_from_transcript(payload), transcript_path)
        sources.append(
            TranscriptSource(
                label=label,
                transcript=transcript_path,
                source_media=media or "",
                units=units,
                timing_granularity="word" if _has_word_timing(payload) else "segment",
            )
        )
    unknown_media = sorted(set(media_overrides) - seen_labels)
    if unknown_media:
        raise ValueError(f"--media labels have no matching transcript: {', '.join(unknown_media)}")
    return sources


def _ngram_set(text: str, size: int = 2) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def score_match(target_text: str, candidate_text: str) -> Dict[str, Any]:
    target = _normalize_match_text(target_text)
    candidate = _normalize_match_text(candidate_text)
    if not target or not candidate:
        return {
            "score": 0.0,
            "sequence": 0.0,
            "target_coverage": 0.0,
            "source_coverage": 0.0,
            "ngram_overlap": 0.0,
            "length_fit": 0.0,
            "exact": False,
        }

    matcher = difflib.SequenceMatcher(None, target, candidate, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    target_coverage = min(1.0, matched / len(target))
    source_coverage = min(1.0, matched / len(candidate))
    target_ngrams = _ngram_set(target)
    candidate_ngrams = _ngram_set(candidate)
    union = target_ngrams | candidate_ngrams
    ngram_overlap = len(target_ngrams & candidate_ngrams) / len(union) if union else 0.0
    length_fit = min(len(target), len(candidate)) / max(len(target), len(candidate))
    exact = target == candidate
    weighted = (
        0.45 * matcher.ratio()
        + 0.25 * target_coverage
        + 0.15 * source_coverage
        + 0.10 * ngram_overlap
        + 0.05 * length_fit
    )
    score = 100.0 if exact else round(100.0 * weighted, 2)
    return {
        "score": score,
        "sequence": round(matcher.ratio(), 4),
        "target_coverage": round(target_coverage, 4),
        "source_coverage": round(source_coverage, 4),
        "ngram_overlap": round(ngram_overlap, 4),
        "length_fit": round(length_fit, 4),
        "exact": exact,
    }


def _tighten_exact(units: Sequence[TimedUnit], target_norm: str) -> Sequence[TimedUnit]:
    normalized = [_normalize_match_text(unit.text) for unit in units]
    boundaries = [0]
    for text in normalized:
        boundaries.append(boundaries[-1] + len(text))
    joined = "".join(normalized)
    start = joined.find(target_norm)
    while start >= 0:
        end = start + len(target_norm)
        if start in boundaries and end in boundaries:
            first = boundaries.index(start)
            last = boundaries.index(end)
            if last > first:
                return units[first:last]
        start = joined.find(target_norm, start + 1)
    return units


def _candidate_id(target_id: str, source_label: str, start: float, end: float) -> str:
    start_ms = int(round(start * 1000))
    end_ms = int(round(end * 1000))
    label_hash = hashlib.sha1(source_label.encode("utf-8")).hexdigest()[:6]
    return f"{target_id}:{_slug(source_label)}-{label_hash}:{start_ms}-{end_ms}"


def generate_candidates(
    target: TargetUnit,
    source: TranscriptSource,
    *,
    max_gap: float = 3.0,
    per_source_limit: int = 40,
) -> List[Dict[str, Any]]:
    target_norm = _normalize_match_text(target.text)
    target_length = len(target_norm)
    min_chars = max(1, int(math.floor(target_length * 0.55)))
    max_chars = max(target_length + 12, int(math.ceil(target_length * 1.8)))
    units = source.units
    candidates: Dict[Tuple[float, float], Dict[str, Any]] = {}

    def candidate_sort_key(item: Mapping[str, Any]) -> Tuple[float, int, float, float]:
        return (
            -float(item["score"]),
            abs(len(_normalize_match_text(item["text"])) - target_length),
            float(item["duration"]),
            float(item["start"]),
        )

    for start_index in range(len(units)):
        char_count = 0
        for end_index in range(start_index, len(units)):
            if end_index > start_index:
                gap = units[end_index].start - units[end_index - 1].end
                if gap > max_gap:
                    break
            char_count += len(_normalize_match_text(units[end_index].text))
            oversized_single = end_index == start_index and char_count > max_chars
            if char_count >= min_chars or oversized_single:
                window = units[start_index : end_index + 1]
                tightened = _tighten_exact(window, target_norm)
                text = _join_tokens(unit.text for unit in tightened)
                score = score_match(target.text, text)
                start = _round3(tightened[0].start)
                end = _round3(tightened[-1].end)
                segment_ids: List[Any] = []
                for unit in tightened:
                    if unit.segment_id not in segment_ids:
                        segment_ids.append(unit.segment_id)
                candidate = {
                    "id": _candidate_id(target.id, source.label, start, end),
                    "source_label": source.label,
                    "transcript": source.transcript,
                    "source_media": source.source_media,
                    "timing_granularity": source.timing_granularity,
                    "start": start,
                    "end": end,
                    "duration": _round3(end - start),
                    "segment_ids": segment_ids,
                    "text": text,
                    "score": score["score"],
                    "score_breakdown": {key: value for key, value in score.items() if key != "score"},
                }
                key = (start, end)
                previous = candidates.get(key)
                if previous is None or candidate["score"] > previous["score"]:
                    candidates[key] = candidate
                if len(candidates) > max(500, per_source_limit * 100):
                    retained = sorted(candidates.values(), key=candidate_sort_key)[: per_source_limit * 5]
                    candidates = {(float(item["start"]), float(item["end"])): item for item in retained}
            if char_count > max_chars:
                break

    ranked = sorted(candidates.values(), key=candidate_sort_key)
    return ranked[:per_source_limit]


def load_choices(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("--choices must be a JSON object")
    raw = payload.get("choices", payload)
    if not isinstance(raw, Mapping):
        raise ValueError("--choices JSON must contain an object mapping target ids to candidate ids")
    choices: Dict[str, str] = {}
    for target_id, value in raw.items():
        if isinstance(value, Mapping):
            value = value.get("candidate_id") or value.get("id")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"choice for {target_id} must be a candidate id string")
        choices[str(target_id)] = value.strip()
    return choices


def _overlap(candidate: Mapping[str, Any], used: Mapping[str, Sequence[Tuple[float, float]]]) -> bool:
    for start, end in used.get(str(candidate.get("source_label") or ""), []):
        if min(float(candidate["end"]), end) - max(float(candidate["start"]), start) > 0.001:
            return True
    return False


def build_alignment(
    targets: Sequence[TargetUnit],
    sources: Sequence[TranscriptSource],
    *,
    target_script: str,
    choices: Optional[Mapping[str, str]] = None,
    top_k: int = 3,
    min_score: float = 65.0,
    review_score: float = 82.0,
    ambiguity_margin: float = 3.0,
    max_gap: float = 3.0,
    allow_reuse: bool = False,
) -> Dict[str, Any]:
    choices = dict(choices or {})
    known_targets = {target.id for target in targets}
    unknown_choices = sorted(set(choices) - known_targets)
    if unknown_choices:
        raise ValueError(f"choices reference unknown target ids: {', '.join(unknown_choices)}")

    decisions: List[Dict[str, Any]] = []
    used: Dict[str, List[Tuple[float, float]]] = {}
    blocking_counter: Counter[str] = Counter()
    warnings: List[str] = []

    for target in targets:
        candidates: List[Dict[str, Any]] = []
        for source in sources:
            candidates.extend(generate_candidates(target, source, max_gap=max_gap))
        candidates.sort(
            key=lambda item: (
                -float(item["score"]),
                str(item["source_label"]),
                float(item["start"]),
                float(item["end"]),
            )
        )

        explicit_id = choices.get(target.id)
        explicit = explicit_id is not None
        available = [candidate for candidate in candidates if allow_reuse or not _overlap(candidate, used)]
        chosen: Optional[Dict[str, Any]] = None
        reasons: List[str] = []

        if explicit:
            chosen = next((candidate for candidate in candidates if candidate["id"] == explicit_id), None)
            if chosen is None:
                reasons.append("invalid_choice")
            elif not allow_reuse and _overlap(chosen, used):
                reasons.append("overlap_conflict")
        elif available:
            chosen = available[0]

        if chosen is None and not reasons:
            reasons.append("no_candidate")

        alternative = next(
            (
                candidate
                for candidate in available
                if chosen is not None and candidate["id"] != chosen["id"]
            ),
            None,
        )
        status = "unmatched"
        selection_origin = "human_choice" if explicit else "automatic"

        if chosen is not None and not reasons:
            score = float(chosen["score"])
            if explicit:
                status = "matched"
                if score < min_score:
                    warnings.append(f"{target.id}: human choice accepted below automatic match threshold")
            elif score < min_score:
                reasons.append("low_score")
            elif score < review_score:
                status = "review"
                reasons.append("review_score")
            elif alternative is not None and score - float(alternative["score"]) <= ambiguity_margin:
                status = "review"
                reasons.append("ambiguous_match")
            else:
                status = "matched"

            if not chosen.get("source_media"):
                reasons.append("source_media_unset")
            elif not Path(str(chosen["source_media"])).is_file():
                reasons.append("source_media_missing")

            if status == "unmatched" and explicit:
                status = "review"
            if reasons and status == "matched" and any(
                reason in {"source_media_unset", "source_media_missing", "overlap_conflict"}
                for reason in reasons
            ):
                status = "review"

        if reasons:
            for reason in set(reasons):
                blocking_counter[reason] += 1

        include_in_render = bool(
            chosen is not None
            and status in {"matched", "review"}
            and not any(reason in {"invalid_choice", "overlap_conflict", "source_media_unset", "source_media_missing"} for reason in reasons)
        )
        if include_in_render and chosen is not None and not allow_reuse:
            used.setdefault(str(chosen["source_label"]), []).append(
                (float(chosen["start"]), float(chosen["end"]))
            )

        visible = candidates[: max(1, top_k)]
        if chosen is not None and all(item["id"] != chosen["id"] for item in visible):
            visible.append(chosen)
        decisions.append(
            {
                "target_id": target.id,
                "target_index": target.index,
                "section": target.section,
                "target_text": target.text,
                "status": status,
                "selection_origin": selection_origin,
                "chosen": chosen,
                "alternative_score_delta": (
                    round(float(chosen["score"]) - float(alternative["score"]), 2)
                    if chosen is not None and alternative is not None
                    else None
                ),
                "blocking_reasons": sorted(set(reasons)),
                "include_in_render": include_in_render,
                "candidates": visible,
            }
        )

    matched = sum(1 for item in decisions if item["status"] == "matched")
    review = sum(1 for item in decisions if item["status"] == "review")
    unmatched = sum(1 for item in decisions if item["status"] == "unmatched")
    blocking = sum(1 for item in decisions if item["blocking_reasons"])
    return {
        "version": VERSION,
        "status": "blocked" if blocking else "ready",
        "target_script": str(Path(target_script).expanduser().resolve()),
        "params": {
            "top_k": top_k,
            "min_score": min_score,
            "review_score": review_score,
            "ambiguity_margin": ambiguity_margin,
            "max_gap": max_gap,
            "allow_reuse": allow_reuse,
        },
        "sources": [
            {
                "label": source.label,
                "transcript": source.transcript,
                "source_media": source.source_media,
                "timing_granularity": source.timing_granularity,
                "timed_units": len(source.units),
            }
            for source in sources
        ],
        "targets": [
            {"id": target.id, "index": target.index, "section": target.section, "text": target.text}
            for target in targets
        ],
        "decisions": decisions,
        "warnings": warnings,
        "summary": {
            "targets": len(targets),
            "matched": matched,
            "review": review,
            "unmatched": unmatched,
            "renderable_clips": sum(1 for item in decisions if item["include_in_render"]),
            "human_choices": sum(1 for item in decisions if item["selection_origin"] == "human_choice"),
            "blocking": blocking,
            "blocking_reasons": dict(sorted(blocking_counter.items())),
            "warnings": len(warnings),
        },
    }


def build_render_config(plan: Mapping[str, Any]) -> Dict[str, Any]:
    clips = []
    for decision in plan.get("decisions") or []:
        if not isinstance(decision, Mapping) or not decision.get("include_in_render"):
            continue
        chosen = decision.get("chosen")
        if not isinstance(chosen, Mapping):
            continue
        clips.append(
            {
                "video": chosen["source_media"],
                "start": chosen["start"],
                "end": chosen["end"],
                "label": decision["target_id"],
                "text": decision["target_text"],
                "source_text": chosen["text"],
                "script_alignment": {
                    "candidate_id": chosen["id"],
                    "source_label": chosen["source_label"],
                    "score": chosen["score"],
                    "selection_origin": decision["selection_origin"],
                },
            }
        )
    return {
        "clips": clips,
        "script_alignment": {
            "version": plan.get("version"),
            "target_script": plan.get("target_script"),
            "status": plan.get("status"),
            "blocking": (plan.get("summary") or {}).get("blocking", 0),
        },
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    params = plan.get("params") or {}
    lines = [
        "# Target Script Alignment",
        "",
        f"- Status: `{plan.get('status', 'unknown')}`",
        f"- Target units: {summary.get('targets', 0)}",
        f"- Matched / review / unmatched: {summary.get('matched', 0)} / {summary.get('review', 0)} / {summary.get('unmatched', 0)}",
        f"- Renderable clips: {summary.get('renderable_clips', 0)}",
        f"- Blocking decisions: {summary.get('blocking', 0)}",
        f"- Thresholds: min {params.get('min_score', 65)} / auto-ready {params.get('review_score', 82)} / ambiguity margin {params.get('ambiguity_margin', 3)}",
        "",
        "## Decisions",
        "",
        "| Target | Status | Chosen source | Time | Score | Origin | Blocking | Text |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for decision in plan.get("decisions") or []:
        if not isinstance(decision, Mapping):
            continue
        chosen = decision.get("chosen") if isinstance(decision.get("chosen"), Mapping) else {}
        time_range = ""
        if chosen:
            time_range = f"{_format_time(float(chosen.get('start') or 0.0))}-{_format_time(float(chosen.get('end') or 0.0))}"
        lines.append(
            "| `{target}` | {status} | {source} | {time} | {score} | {origin} | {blocking} | {text} |".format(
                target=_escape_md(decision.get("target_id", "")),
                status=_escape_md(decision.get("status", "")),
                source=_escape_md(chosen.get("source_label", "")),
                time=_escape_md(time_range),
                score=_escape_md(chosen.get("score", "")),
                origin=_escape_md(decision.get("selection_origin", "")),
                blocking=_escape_md(", ".join(decision.get("blocking_reasons") or [])),
                text=_escape_md(decision.get("target_text", "")),
            )
        )

    for decision in plan.get("decisions") or []:
        if not isinstance(decision, Mapping):
            continue
        lines.extend(["", f"## {decision.get('target_id')} candidates", "", f"> {_escape_md(decision.get('target_text', ''))}", ""])
        lines.extend(["| Candidate ID | Source | Time | Score | Exact | Source text |", "|---|---|---:|---:|---|---|"])
        for candidate in decision.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            breakdown = candidate.get("score_breakdown") or {}
            time_range = f"{_format_time(float(candidate.get('start') or 0.0))}-{_format_time(float(candidate.get('end') or 0.0))}"
            lines.append(
                f"| `{_escape_md(candidate.get('id', ''))}` | {_escape_md(candidate.get('source_label', ''))} | {time_range} | {candidate.get('score', '')} | {breakdown.get('exact', False)} | {_escape_md(candidate.get('text', ''))} |"
            )

    if summary.get("blocking"):
        lines.extend(
            [
                "",
                "## Resolve review decisions",
                "",
                "Create a JSON file that maps each reviewed target id to one candidate id, then rerun with `--choices`:",
                "",
                "```json",
                '{"choices": {"target-001": "<candidate-id-from-the-table>"}}',
                "```",
                "",
                "A human choice resolves score/ambiguity review, but missing media and overlapping source ranges remain blocking.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def emit_clean_script(targets: Sequence[TargetUnit]) -> str:
    lines: List[str] = []
    active_section = ""
    for target in targets:
        if target.section and target.section != active_section:
            if lines:
                lines.append("")
            lines.extend([f"## {target.section}", ""])
            active_section = target.section
        lines.append(target.text)
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path: str, text: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align a target script to one or more timestamped transcripts and emit an auditable edit plan."
    )
    parser.add_argument("--target-script", required=True, help="Reviewed target script Markdown/text path; one spoken unit per line is recommended.")
    parser.add_argument("--target-unit", choices=("line", "sentence"), default="line", help="How to split the target script.")
    parser.add_argument("--transcript", action="append", default=[], help="Transcript JSON. Repeatable; label=path sets the source label.")
    parser.add_argument("--transcripts-dir", action="append", default=[], help="Directory to scan recursively for *transcript*.json files.")
    parser.add_argument("--media", action="append", default=[], help="Source media override in label=/path/video.mp4 form. Repeatable.")
    parser.add_argument("--choices", help="Optional reviewed target-id to candidate-id JSON mapping.")
    parser.add_argument("--output", required=True, help="Output script_alignment.v1 JSON path.")
    parser.add_argument("--markdown", help="Optional human review Markdown path.")
    parser.add_argument("--render-config", help="Optional render_final.py config path in target-script order.")
    parser.add_argument("--clean-script", help="Optional normalized reviewed target copy for downstream clean_script consumers.")
    parser.add_argument("--top-k", type=int, default=3, help="Candidate alternatives to retain per target unit.")
    parser.add_argument("--min-score", type=float, default=65.0, help="Minimum automatic lexical match score (0-100).")
    parser.add_argument("--review-score", type=float, default=82.0, help="Automatic ready threshold (0-100).")
    parser.add_argument("--ambiguity-margin", type=float, default=3.0, help="Require review when the top two scores differ by at most this value.")
    parser.add_argument("--max-gap", type=float, default=3.0, help="Maximum silence gap inside one candidate range.")
    parser.add_argument("--allow-reuse", action="store_true", help="Allow the same source time range to satisfy multiple target units.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when any target unit still has a blocking decision.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")
    if not (0 <= args.min_score <= args.review_score <= 100):
        parser.error("thresholds must satisfy 0 <= --min-score <= --review-score <= 100")
    if args.ambiguity_margin < 0 or args.max_gap < 0:
        parser.error("--ambiguity-margin and --max-gap must be non-negative")

    try:
        transcript_args = collect_transcript_args(args.transcript, args.transcripts_dir)
        sources = load_sources(transcript_args, parse_media_overrides(args.media))
        targets = parse_target_script(args.target_script, unit_mode=args.target_unit)
        plan = build_alignment(
            targets,
            sources,
            target_script=args.target_script,
            choices=load_choices(args.choices),
            top_k=args.top_k,
            min_score=args.min_score,
            review_score=args.review_score,
            ambiguity_margin=args.ambiguity_margin,
            max_gap=args.max_gap,
            allow_reuse=args.allow_reuse,
        )
    except Exception as exc:
        print(f"script_alignment failed: {exc}", file=sys.stderr)
        return 1

    _write_json(args.output, plan)
    if args.markdown:
        _write_text(args.markdown, emit_markdown(plan))
    if args.render_config:
        _write_json(args.render_config, build_render_config(plan))
    if args.clean_script:
        _write_text(args.clean_script, emit_clean_script(targets))

    summary = plan["summary"]
    print(
        f"Wrote script alignment: targets={summary['targets']} matched={summary['matched']} "
        f"review={summary['review']} unmatched={summary['unmatched']} blocking={summary['blocking']}",
        file=sys.stderr,
    )
    if args.strict and summary["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
