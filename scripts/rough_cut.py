#!/usr/bin/env python3
"""
Transcript-aware rough cut planner/renderer for talking-head videos.

This complements jump_cut.py: jump_cut removes acoustic silence, while
rough_cut.py removes transcript-level filler-only segments and adjacent repeat
sentences that ASR already timestamped.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jump_cut import (  # noqa: E402
    Segment,
    build_ffmpeg_command,
    has_stream,
    media_duration,
    probe_media,
    run_ffmpeg_with_fallback,
    write_json,
)
from transcribe import FILLER_PATTERNS  # noqa: E402


@dataclass(frozen=True)
class TranscriptSegment:
    id: int
    start: float
    end: float
    duration: float
    text: str


@dataclass(frozen=True)
class RoughCutDecision:
    segment_id: int
    start: float
    end: float
    duration: float
    text: str
    reason: str
    confidence: float
    keep_segment_id: Optional[int] = None
    details: str = ""


@dataclass(frozen=True)
class RemovedRange:
    start: float
    end: float
    duration: float
    segment_ids: List[int]
    reasons: List[str]


def _round4(value: float) -> float:
    return round(max(0.0, value), 4)


def language_key(language: Optional[str]) -> str:
    return "zh" if language and language.lower().startswith("zh") else "en"


def infer_language(transcript: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return language_key(override)
    raw = transcript.get("language") or transcript.get("detected_language")
    if raw:
        return language_key(str(raw))
    text = "".join(str(s.get("text", "")) for s in transcript.get("segments", []))
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def normalize_segments(transcript: Dict[str, Any]) -> List[TranscriptSegment]:
    segments: List[TranscriptSegment] = []
    for pos, raw in enumerate(transcript.get("segments") or [], start=1):
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad transcript segment: {raw!r}") from exc
        if end <= start:
            continue
        try:
            seg_id = int(raw.get("id", pos))
        except (TypeError, ValueError):
            seg_id = pos
        text = str(raw.get("text", "")).strip()
        segments.append(TranscriptSegment(
            id=seg_id,
            start=_round4(start),
            end=_round4(end),
            duration=_round4(end - start),
            text=text,
        ))
    return sorted(segments, key=lambda s: (s.start, s.end, s.id))


def find_fillers(text: str, language: str) -> List[str]:
    patterns = FILLER_PATTERNS.get(language_key(language), FILLER_PATTERNS["en"])
    text_lower = text.lower()
    found: List[str] = []
    for filler in patterns:
        if language_key(language) == "zh":
            if filler in text:
                found.append(filler)
        elif re.search(r"\b" + re.escape(filler) + r"\b", text_lower):
            found.append(filler)
    return found


def normalize_text(text: str, language: str) -> str:
    normalized = text.lower()
    for filler in FILLER_PATTERNS.get(language_key(language), FILLER_PATTERNS["en"]):
        if language_key(language) == "zh":
            normalized = normalized.replace(filler, "")
        else:
            normalized = re.sub(r"\b" + re.escape(filler) + r"\b", " ", normalized)
    if language_key(language) == "zh":
        return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", normalized))
    return "".join(re.findall(r"[a-z0-9]+", normalized))


def transcript_duration(transcript: Dict[str, Any], segments: Sequence[TranscriptSegment]) -> float:
    for key in ("duration", "audio_duration", "video_duration"):
        try:
            value = float(transcript.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return _round4(value)
    return _round4(max((s.end for s in segments), default=0.0))


def detect_filler_only_segments(
    transcript: Dict[str, Any],
    segments: Sequence[TranscriptSegment],
    language: str,
) -> List[RoughCutDecision]:
    by_id = {s.id: s for s in segments}
    decisions: Dict[int, RoughCutDecision] = {}

    for item in transcript.get("filler_words") or []:
        if not item.get("is_filler_only"):
            continue
        try:
            seg_id = int(item["segment_id"])
        except (KeyError, TypeError, ValueError):
            continue
        segment = by_id.get(seg_id)
        if not segment:
            continue
        fillers = item.get("fillers_found") or find_fillers(segment.text, language)
        decisions[seg_id] = RoughCutDecision(
            segment_id=segment.id,
            start=segment.start,
            end=segment.end,
            duration=segment.duration,
            text=segment.text,
            reason="filler_only",
            confidence=1.0,
            details=f"fillers={','.join(map(str, fillers))}",
        )

    for segment in segments:
        if segment.id in decisions:
            continue
        fillers = find_fillers(segment.text, language)
        if fillers and not normalize_text(segment.text, language):
            decisions[segment.id] = RoughCutDecision(
                segment_id=segment.id,
                start=segment.start,
                end=segment.end,
                duration=segment.duration,
                text=segment.text,
                reason="filler_only",
                confidence=0.95,
                details=f"fillers={','.join(fillers)}",
            )

    return sorted(decisions.values(), key=lambda d: (d.start, d.end, d.segment_id))


def _repeat_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    containment = len(shorter) / len(longer) if shorter in longer else 0.0
    return max(ratio, containment)


def _choose_repeat_skip(
    previous: TranscriptSegment,
    current: TranscriptSegment,
    previous_norm: str,
    current_norm: str,
) -> Tuple[TranscriptSegment, TranscriptSegment, str]:
    if len(current_norm) >= max(1, int(len(previous_norm) * 1.15)):
        return previous, current, "repeated_before_retry"
    return current, previous, "repeated_duplicate"


def detect_adjacent_repeats(
    segments: Sequence[TranscriptSegment],
    *,
    language: str,
    skip_ids: Sequence[int] = (),
    threshold: float = 0.88,
    max_gap: float = 2.5,
    min_chars: int = 6,
) -> List[RoughCutDecision]:
    skipped = set(skip_ids)
    decisions: List[RoughCutDecision] = []
    candidates: List[TranscriptSegment] = []

    for segment in segments:
        if segment.id in skipped:
            continue
        current_norm = normalize_text(segment.text, language)
        if len(current_norm) < min_chars:
            candidates.append(segment)
            continue
        if not candidates:
            candidates.append(segment)
            continue

        previous = candidates[-1]
        previous_norm = normalize_text(previous.text, language)
        gap = segment.start - previous.end
        if len(previous_norm) < min_chars or gap > max_gap:
            candidates.append(segment)
            continue

        score = _repeat_score(previous_norm, current_norm)
        if score < threshold:
            candidates.append(segment)
            continue

        skip, keep, reason = _choose_repeat_skip(previous, segment, previous_norm, current_norm)
        decisions.append(RoughCutDecision(
            segment_id=skip.id,
            start=skip.start,
            end=skip.end,
            duration=skip.duration,
            text=skip.text,
            reason=reason,
            confidence=round(score, 3),
            keep_segment_id=keep.id,
            details=f"similarity={score:.3f}; gap={gap:.3f}s",
        ))
        skipped.add(skip.id)
        if skip.id == previous.id:
            candidates[-1] = segment

    return sorted(decisions, key=lambda d: (d.start, d.end, d.segment_id))


def merge_removed_ranges(decisions: Sequence[RoughCutDecision], boundary_pad: float = 0.0) -> List[RemovedRange]:
    ranges: List[Tuple[float, float, RoughCutDecision]] = []
    for decision in decisions:
        start = min(decision.end, decision.start + boundary_pad)
        end = max(start, decision.end - boundary_pad)
        if end <= start:
            continue
        ranges.append((_round4(start), _round4(end), decision))

    merged: List[RemovedRange] = []
    for start, end, decision in sorted(ranges, key=lambda item: (item[0], item[1])):
        if not merged or start > merged[-1].end:
            merged.append(RemovedRange(
                start=start,
                end=end,
                duration=_round4(end - start),
                segment_ids=[decision.segment_id],
                reasons=[decision.reason],
            ))
            continue
        previous = merged[-1]
        new_end = max(previous.end, end)
        merged[-1] = RemovedRange(
            start=previous.start,
            end=_round4(new_end),
            duration=_round4(new_end - previous.start),
            segment_ids=previous.segment_ids + [decision.segment_id],
            reasons=sorted(set(previous.reasons + [decision.reason])),
        )
    return merged


def build_keep_segments(duration: float, removed_ranges: Sequence[RemovedRange],
                        min_keep: float = 0.15) -> List[Segment]:
    keep: List[Segment] = []
    cursor = 0.0
    for item in sorted(removed_ranges, key=lambda r: (r.start, r.end)):
        start = max(cursor, min(duration, item.start))
        end = max(start, min(duration, item.end))
        if start - cursor >= min_keep:
            keep.append(Segment(_round4(cursor), _round4(start), _round4(start - cursor)))
        cursor = max(cursor, end)
    if duration - cursor >= min_keep:
        keep.append(Segment(_round4(cursor), _round4(duration), _round4(duration - cursor)))
    return keep


def build_rough_cut_plan(
    transcript: Dict[str, Any],
    *,
    transcript_path: Optional[str] = None,
    input_path: Optional[str] = None,
    output_path: Optional[str] = None,
    language: Optional[str] = None,
    remove_filler_only: bool = True,
    remove_repeats: bool = True,
    repeat_threshold: float = 0.88,
    max_repeat_gap: float = 2.5,
    min_repeat_chars: int = 6,
    boundary_pad: float = 0.0,
    min_keep: float = 0.15,
    duration: Optional[float] = None,
) -> Dict[str, Any]:
    segments = normalize_segments(transcript)
    lang = infer_language(transcript, language)
    media_duration_seconds = _round4(duration) if duration else transcript_duration(transcript, segments)

    decisions: List[RoughCutDecision] = []
    if remove_filler_only:
        decisions.extend(detect_filler_only_segments(transcript, segments, lang))
    if remove_repeats:
        decisions.extend(detect_adjacent_repeats(
            segments,
            language=lang,
            skip_ids=[d.segment_id for d in decisions],
            threshold=repeat_threshold,
            max_gap=max_repeat_gap,
            min_chars=min_repeat_chars,
        ))
    decisions = sorted({d.segment_id: d for d in decisions}.values(), key=lambda d: (d.start, d.end, d.segment_id))

    removed_ranges = merge_removed_ranges(decisions, boundary_pad=boundary_pad)
    keep_segments = build_keep_segments(media_duration_seconds, removed_ranges, min_keep=min_keep)
    removed_seconds = sum(item.duration for item in removed_ranges)
    kept_seconds = sum(item.duration for item in keep_segments)

    return {
        "kind": "rough_cut",
        "input": os.path.abspath(input_path) if input_path else None,
        "output": os.path.abspath(output_path) if output_path else None,
        "transcript": os.path.abspath(transcript_path) if transcript_path else None,
        "language": lang,
        "duration": media_duration_seconds,
        "settings": {
            "remove_filler_only": remove_filler_only,
            "remove_repeats": remove_repeats,
            "repeat_threshold": repeat_threshold,
            "max_repeat_gap": max_repeat_gap,
            "min_repeat_chars": min_repeat_chars,
            "boundary_pad": boundary_pad,
            "min_keep": min_keep,
        },
        "decisions": [asdict(d) for d in decisions],
        "removed_segments": [asdict(r) for r in removed_ranges],
        "keep_segments": [asdict(s) for s in keep_segments],
        "removed_seconds": _round4(removed_seconds),
        "output_duration_estimate": _round4(kept_seconds),
        "speedup_ratio": round(media_duration_seconds / kept_seconds, 3) if kept_seconds else None,
        "review_hint": "Run timeline_view.py with this cut list before final render when decisions look aggressive.",
    }


def load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan/render ASR rough cuts from transcript filler and repeat signals."
    )
    parser.add_argument("--transcript", required=True, help="Whisper transcript JSON.")
    parser.add_argument("--input", help="Source video/audio to render. Optional for plan-only runs.")
    parser.add_argument("--output", help="Rendered rough-cut media path.")
    parser.add_argument("--cut-list", help="Output rough-cut JSON path.")
    parser.add_argument("--language", help="Override transcript language, e.g. zh or en.")
    parser.add_argument("--repeat-threshold", type=float, default=0.88,
                        help="Adjacent repeat similarity threshold.")
    parser.add_argument("--max-repeat-gap", type=float, default=2.5,
                        help="Max seconds between adjacent transcript segments for repeat detection.")
    parser.add_argument("--min-repeat-chars", type=int, default=6,
                        help="Minimum normalized characters before repeat detection is considered.")
    parser.add_argument("--boundary-pad", type=float, default=0.0,
                        help="Seconds to preserve at both edges of removed transcript segments.")
    parser.add_argument("--min-keep", type=float, default=0.15,
                        help="Drop keep ranges shorter than this many seconds.")
    parser.add_argument("--no-filler-only", action="store_true",
                        help="Do not remove segments marked as filler-only.")
    parser.add_argument("--no-repeat-detect", action="store_true",
                        help="Do not remove adjacent repeated transcript segments.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write/print the plan but do not render even when --output is set.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    transcript = load_json(args.transcript)

    duration_override: Optional[float] = None
    has_video = True
    if args.output and not args.input:
        print("Error: --output requires --input.", file=sys.stderr)
        return 2
    if args.input and os.path.exists(args.input):
        metadata = probe_media(args.input)
        duration_override = media_duration(metadata)
        has_video = has_stream(metadata, "video")
    elif args.output and not args.dry_run:
        print("Error: --output rendering requires an existing --input file.", file=sys.stderr)
        return 2

    plan = build_rough_cut_plan(
        transcript,
        transcript_path=args.transcript,
        input_path=args.input,
        output_path=args.output,
        language=args.language,
        remove_filler_only=not args.no_filler_only,
        remove_repeats=not args.no_repeat_detect,
        repeat_threshold=args.repeat_threshold,
        max_repeat_gap=args.max_repeat_gap,
        min_repeat_chars=args.min_repeat_chars,
        boundary_pad=args.boundary_pad,
        min_keep=args.min_keep,
        duration=duration_override,
    )

    if args.output:
        keep_segments = [Segment(**item) for item in plan["keep_segments"]]
        if not keep_segments:
            print("Error: no keep segments available; refusing to render empty output.", file=sys.stderr)
            return 2
        cmd = build_ffmpeg_command(args.input, args.output, keep_segments, has_video=has_video)
        plan["ffmpeg_command"] = cmd
        if not args.dry_run:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
            run_ffmpeg_with_fallback(cmd, has_video=has_video)

    if args.cut_list:
        write_json(args.cut_list, plan)
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))

    removed = len(plan["removed_segments"])
    print(
        f"Rough cut plan: {removed} removed ranges, "
        f"{plan['removed_seconds']:.2f}s removed, "
        f"estimate {plan['output_duration_estimate']:.2f}s.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
