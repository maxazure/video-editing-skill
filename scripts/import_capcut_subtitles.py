#!/usr/bin/env python3
"""
Import JianYing/CapCut subtitle tracks back into this pipeline.

CapCut's auto captions are often a useful human-reviewed transcript source.
This script reads draft_content.json (or an exported SRT), emits a compatible
transcript.json, and can also create a non-destructive cut list from subtitle
gaps for timeline_view.py / export_edl.py review.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

SEC = 1_000_000


@dataclass(frozen=True)
class SubtitleSegment:
    id: int
    start: float
    end: float
    duration: float
    text: str
    material_id: Optional[str] = None
    track_id: Optional[str] = None
    source: str = "capcut"


@dataclass(frozen=True)
class TimelineSegment:
    start: float
    end: float
    duration: float


def _round4(value: float) -> float:
    return round(max(0.0, value), 4)


def _seconds_from_timerange(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 10_000:
        return number / SEC
    return number


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("\ufeff", "").strip()


def _extract_material_text(material: Dict[str, Any]) -> str:
    for key in ("text", "name"):
        if material.get(key):
            return _clean_text(str(material[key]))

    content = material.get("content")
    if isinstance(content, dict):
        return _clean_text(str(content.get("text", "")))
    if not isinstance(content, str) or not content.strip():
        return ""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _clean_text(content)
    if isinstance(parsed, dict):
        return _clean_text(str(parsed.get("text", "")))
    return _clean_text(str(parsed))


def _is_subtitle_material(material: Dict[str, Any]) -> bool:
    mat_type = str(material.get("type", "")).lower()
    category = str(material.get("category_name", "")).lower()
    return mat_type in {"subtitle", "captions", "caption"} or "subtitle" in category


def _resolve_draft_content_path(path: str) -> str:
    if os.path.isdir(path):
        path = os.path.join(path, "draft_content.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CapCut draft_content.json not found: {path}")
    return path


def load_draft_content(path: str) -> Dict[str, Any]:
    draft_path = _resolve_draft_content_path(path)
    with open(draft_path, encoding="utf-8") as f:
        return json.load(f)


def extract_draft_subtitles(
    draft: Dict[str, Any],
    *,
    include_overlays: bool = False,
) -> List[SubtitleSegment]:
    texts = {
        str(item.get("id")): item
        for item in draft.get("materials", {}).get("texts", [])
        if item.get("id")
    }
    subtitle_segments: List[SubtitleSegment] = []
    overlay_candidates: List[SubtitleSegment] = []

    for track in draft.get("tracks", []) or []:
        if str(track.get("type", "")).lower() != "text":
            continue
        track_id = str(track.get("id") or "")
        for raw in track.get("segments", []) or []:
            if raw.get("visible", True) is False:
                continue
            material_id = str(raw.get("material_id") or "")
            material = texts.get(material_id)
            if not material:
                continue
            text = _extract_material_text(material)
            if not text:
                continue
            timerange = raw.get("target_timerange") or {}
            start = _seconds_from_timerange(timerange.get("start"))
            duration = _seconds_from_timerange(timerange.get("duration"))
            if duration <= 0:
                source_timerange = raw.get("source_timerange") or {}
                duration = _seconds_from_timerange(source_timerange.get("duration"))
            if duration <= 0:
                continue
            segment = SubtitleSegment(
                id=0,
                start=_round4(start),
                end=_round4(start + duration),
                duration=_round4(duration),
                text=text,
                material_id=material_id,
                track_id=track_id or None,
                source="capcut_draft",
            )
            if _is_subtitle_material(material):
                subtitle_segments.append(segment)
            elif include_overlays:
                overlay_candidates.append(segment)

    chosen = subtitle_segments or overlay_candidates
    return [
        SubtitleSegment(
            id=index,
            start=item.start,
            end=item.end,
            duration=item.duration,
            text=item.text,
            material_id=item.material_id,
            track_id=item.track_id,
            source=item.source,
        )
        for index, item in enumerate(sorted(chosen, key=lambda s: (s.start, s.end, s.text)), start=1)
    ]


def _parse_srt_time(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value.strip())
    if not match:
        raise ValueError(f"bad SRT timecode: {value!r}")
    hours, minutes, seconds, millis = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis[:3].ljust(3, "0")) / 1000
    )


def parse_srt(path: str) -> List[SubtitleSegment]:
    with open(path, encoding="utf-8-sig") as f:
        blocks = re.split(r"\n\s*\n", f.read().strip())

    segments: List[SubtitleSegment] = []
    for block in blocks:
        lines = [line.rstrip("\n") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[0].split("-->", 1)]
        start = _parse_srt_time(start_raw)
        end = _parse_srt_time(end_raw)
        text = _clean_text(" ".join(lines[1:]))
        if end <= start or not text:
            continue
        segments.append(SubtitleSegment(
            id=len(segments) + 1,
            start=_round4(start),
            end=_round4(end),
            duration=_round4(end - start),
            text=text,
            source="srt",
        ))
    return segments


def infer_language(segments: Sequence[SubtitleSegment], override: Optional[str] = None) -> str:
    if override:
        return override
    text = "".join(segment.text for segment in segments)
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def transcript_from_segments(
    segments: Sequence[SubtitleSegment],
    *,
    source_path: Optional[str],
    source_type: str,
    language: Optional[str] = None,
    duration: Optional[float] = None,
) -> Dict[str, Any]:
    inferred_duration = max((s.end for s in segments), default=0.0)
    total_duration = _round4(duration if duration is not None else inferred_duration)
    return {
        "kind": "capcut_subtitle_transcript",
        "version": "capcut_subtitle_import.v1",
        "source_type": source_type,
        "source": os.path.abspath(source_path) if source_path else None,
        "language": infer_language(segments, language),
        "duration": total_duration,
        "segments": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "duration": segment.duration,
                "text": segment.text,
            }
            for segment in segments
        ],
        "review": {
            "origin": "Imported from CapCut/JianYing subtitles; review wording before final render.",
            "segment_count": len(segments),
        },
    }


def _merge_timeline_segments(
    segments: Iterable[TimelineSegment],
    *,
    max_gap: float,
) -> List[TimelineSegment]:
    merged: List[TimelineSegment] = []
    for segment in sorted(segments, key=lambda s: (s.start, s.end)):
        if segment.end <= segment.start:
            continue
        if not merged or segment.start - merged[-1].end > max_gap:
            merged.append(segment)
            continue
        previous = merged[-1]
        end = max(previous.end, segment.end)
        merged[-1] = TimelineSegment(previous.start, _round4(end), _round4(end - previous.start))
    return merged


def _infer_removed_segments(duration: float, keep_segments: Sequence[TimelineSegment]) -> List[TimelineSegment]:
    removed: List[TimelineSegment] = []
    cursor = 0.0
    for segment in keep_segments:
        if segment.start > cursor:
            removed.append(TimelineSegment(_round4(cursor), segment.start, _round4(segment.start - cursor)))
        cursor = max(cursor, segment.end)
    if duration > cursor:
        removed.append(TimelineSegment(_round4(cursor), _round4(duration), _round4(duration - cursor)))
    return removed


def build_subtitle_gap_cut_list(
    segments: Sequence[SubtitleSegment],
    *,
    source_media: Optional[str] = None,
    transcript_path: Optional[str] = None,
    duration: Optional[float] = None,
    gap_threshold: float = 1.0,
    pad: float = 0.08,
    min_keep: float = 0.15,
) -> Dict[str, Any]:
    if gap_threshold < 0:
        raise ValueError("gap_threshold must be >= 0")
    inferred_duration = max((s.end for s in segments), default=0.0)
    total_duration = _round4(duration if duration is not None else inferred_duration)
    speech_blocks = _merge_timeline_segments(
        (
            TimelineSegment(
                start=_round4(max(0.0, segment.start - pad)),
                end=_round4(min(total_duration, segment.end + pad)),
                duration=_round4(segment.end - segment.start + 2 * pad),
            )
            for segment in segments
        ),
        max_gap=gap_threshold,
    )
    keep_segments = [
        TimelineSegment(block.start, block.end, _round4(block.end - block.start))
        for block in speech_blocks
        if block.end - block.start >= min_keep
    ]
    removed_segments = _infer_removed_segments(total_duration, keep_segments)
    removed_seconds = _round4(sum(item.duration for item in removed_segments))
    kept_seconds = _round4(sum(item.duration for item in keep_segments))
    return {
        "kind": "capcut_subtitle_gap_cut",
        "version": "capcut_subtitle_import.v1",
        "input": os.path.abspath(source_media) if source_media else None,
        "transcript": os.path.abspath(transcript_path) if transcript_path else None,
        "duration": total_duration,
        "settings": {
            "gap_threshold": gap_threshold,
            "pad": pad,
            "min_keep": min_keep,
        },
        "subtitle_segments": [asdict(segment) for segment in segments],
        "removed_segments": [asdict(segment) for segment in removed_segments],
        "keep_segments": [asdict(segment) for segment in keep_segments],
        "removed_seconds": removed_seconds,
        "output_duration_estimate": kept_seconds,
        "speedup_ratio": round(total_duration / kept_seconds, 3) if kept_seconds else None,
        "review_hint": "Review with timeline_view.py before rendering; CapCut subtitle gaps are a speech proxy, not frame-accurate edit decisions.",
    }


def _format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        whole_seconds += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def render_srt(segments: Sequence[SubtitleSegment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n"
            f"{_format_srt_time(segment.start)} --> {_format_srt_time(segment.end)}\n"
            f"{segment.text}\n"
        )
    return "\n".join(blocks)


def render_markdown(segments: Sequence[SubtitleSegment], cut_list: Optional[Dict[str, Any]] = None) -> str:
    lines = [
        "# CapCut Subtitle Import Review",
        "",
        f"- Subtitle segments: {len(segments)}",
    ]
    if cut_list:
        lines.extend([
            f"- Gap cut keep segments: {len(cut_list['keep_segments'])}",
            f"- Removed seconds: {cut_list['removed_seconds']}",
            "",
        ])
    lines.extend([
        "| # | Start | End | Text |",
        "|---:|---:|---:|---|",
    ])
    for segment in segments:
        lines.append(f"| {segment.id} | {segment.start:.2f} | {segment.end:.2f} | {segment.text} |")
    return "\n".join(lines) + "\n"


def write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _draft_duration(draft: Dict[str, Any]) -> Optional[float]:
    duration = _seconds_from_timerange(draft.get("duration"))
    return duration if duration > 0 else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import CapCut/JianYing subtitle tracks into transcript and cut-list artifacts."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft", help="CapCut/JianYing draft folder or draft_content.json.")
    source.add_argument("--srt", help="SRT exported from CapCut/JianYing.")
    parser.add_argument("--transcript", help="Output transcript JSON path. Prints to stdout when omitted.")
    parser.add_argument("--cut-list", help="Optional subtitle-gap cut list JSON output path.")
    parser.add_argument("--srt-output", help="Optional normalized SRT output path.")
    parser.add_argument("--markdown", help="Optional Markdown review output path.")
    parser.add_argument("--source-media", help="Original video/audio path to record in cut list metadata.")
    parser.add_argument("--duration", type=float, help="Override timeline duration in seconds.")
    parser.add_argument("--language", help="Override transcript language, e.g. zh or en.")
    parser.add_argument("--gap-threshold", type=float, default=1.0,
                        help="Merge subtitle spans separated by <= this many seconds.")
    parser.add_argument("--pad", type=float, default=0.08,
                        help="Seconds of context to keep before/after subtitle spans.")
    parser.add_argument("--min-keep", type=float, default=0.15,
                        help="Drop keep ranges shorter than this many seconds.")
    parser.add_argument("--include-overlays", action="store_true",
                        help="Import generic text overlays when no subtitle materials are present.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_path = args.draft or args.srt
    source_type = "capcut_draft" if args.draft else "srt"
    default_duration = None

    if args.draft:
        draft = load_draft_content(args.draft)
        default_duration = _draft_duration(draft)
        segments = extract_draft_subtitles(draft, include_overlays=args.include_overlays)
        if not segments and not args.include_overlays:
            print(
                "Error: no subtitle text materials found. Retry with --include-overlays if this draft uses generic text tracks.",
                file=sys.stderr,
            )
            return 2
    else:
        segments = parse_srt(args.srt)

    duration = args.duration if args.duration is not None else default_duration
    transcript = transcript_from_segments(
        segments,
        source_path=source_path,
        source_type=source_type,
        language=args.language,
        duration=duration,
    )
    transcript_path = args.transcript
    cut_list = None
    if args.cut_list:
        cut_list = build_subtitle_gap_cut_list(
            segments,
            source_media=args.source_media,
            transcript_path=transcript_path,
            duration=transcript["duration"],
            gap_threshold=args.gap_threshold,
            pad=args.pad,
            min_keep=args.min_keep,
        )

    if transcript_path:
        write_json(transcript_path, transcript)
    else:
        print(json.dumps(transcript, ensure_ascii=False, indent=2))

    if args.cut_list and cut_list:
        write_json(args.cut_list, cut_list)
    if args.srt_output:
        write_text(args.srt_output, render_srt(segments))
    if args.markdown:
        write_text(args.markdown, render_markdown(segments, cut_list=cut_list))

    print(
        f"Imported {len(segments)} subtitle segments"
        + (f", wrote cut list with {len(cut_list['keep_segments'])} keep ranges" if cut_list else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
