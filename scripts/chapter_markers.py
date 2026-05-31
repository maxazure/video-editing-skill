#!/usr/bin/env python3
"""Export chapter markers in platform and FFmpeg-friendly formats.

Inputs can be:
  - transcript JSON with segments
  - clean_script.md with `## ` section headings
  - explicit chapter JSON from an LLM or manual review

Outputs:
  - chapters.json
  - chapters.md
  - chapters.ffmetadata
  - chapters-youtube.txt
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "chapter_markers.v1"

TRANSITION_MARKERS = (
    "next",
    "now",
    "moving on",
    "another",
    "finally",
    "first",
    "second",
    "third",
    "接下来",
    "然后",
    "另外",
    "还有",
    "最后",
    "第一",
    "第二",
    "第三",
    "重点",
)


@dataclasses.dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclasses.dataclass(frozen=True)
class ChapterMarker:
    id: str
    title: str
    start: float
    end: float
    duration: float
    description: str = ""
    source: str = "inferred"


def parse_timecode(value: Any) -> Optional[float]:
    """Parse seconds or HH:MM:SS(.ms) into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return max(0.0, nums[0])
    if len(nums) == 2:
        minutes, seconds = nums
        return max(0.0, minutes * 60 + seconds)
    hours, minutes, seconds = nums
    return max(0.0, hours * 3600 + minutes * 60 + seconds)


def format_youtube_timestamp(seconds: float) -> str:
    total = max(0, int(math.floor(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_chapter_headings(md_path: str) -> List[str]:
    titles: List[str] = []
    if not os.path.isfile(md_path):
        return titles
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                title = sanitize_title(stripped[3:])
                if title:
                    titles.append(title)
    return titles


def sanitize_title(value: Any, *, fallback: str = "Chapter") -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" #|\t\r\n")
    return text[:80] or fallback


def _coerce_segment(raw: Dict[str, Any]) -> Optional[TranscriptSegment]:
    start = parse_timecode(
        raw.get("start", raw.get("start_seconds", raw.get("timestamp")))
    )
    end = parse_timecode(raw.get("end", raw.get("end_seconds")))
    text = str(raw.get("text", raw.get("transcript", raw.get("sentence", "")))).strip()
    if start is None:
        return None
    if end is None:
        end = start
    return TranscriptSegment(start=start, end=max(start, end), text=text)


def load_transcript(path: str) -> Tuple[List[TranscriptSegment], Optional[float]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw_segments = data if isinstance(data, list) else data.get("segments", [])
    segments = [
        seg for seg in (_coerce_segment(item) for item in raw_segments if isinstance(item, dict))
        if seg is not None
    ]
    segments.sort(key=lambda item: (item.start, item.end))
    duration = None if isinstance(data, list) else parse_timecode(data.get("duration"))
    if duration is None and segments:
        duration = max(seg.end for seg in segments)
    return segments, duration


def load_explicit_chapters(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        chapters = data.get("chapters", [])
        return [item for item in chapters if isinstance(item, dict)]
    return []


def text_title(text: str, *, index: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" 。.!?,，、：:;；")
    if not cleaned:
        return f"Chapter {index}"
    if " " in cleaned:
        return sanitize_title(" ".join(cleaned.split()[:7]), fallback=f"Chapter {index}")
    return sanitize_title(cleaned[:18], fallback=f"Chapter {index}")


def find_transition_starts(
    segments: Sequence[TranscriptSegment],
    *,
    min_gap: float,
    max_count: int,
) -> List[float]:
    starts: List[float] = []
    last = 0.0
    for seg in segments:
        if seg.start - last < min_gap:
            continue
        text = seg.text.lower()
        if any(marker in text for marker in TRANSITION_MARKERS):
            starts.append(seg.start)
            last = seg.start
            if len(starts) >= max_count:
                break
    return starts


def even_starts(count: int, duration: float) -> List[float]:
    if count <= 1:
        return [0.0]
    step = duration / count
    return [round(step * i, 3) for i in range(count)]


def _nearest_segment_text(segments: Sequence[TranscriptSegment], start: float) -> str:
    if not segments:
        return ""
    return min(segments, key=lambda seg: abs(seg.start - start)).text


def _chapter_count_for_duration(
    duration: float,
    *,
    min_chapters: int,
    max_chapters: int,
    min_chapter_duration: float,
    target_chapter_duration: float,
) -> int:
    if duration <= 0:
        return 1
    target = max(1, int(math.ceil(duration / max(1.0, target_chapter_duration))))
    if duration >= min_chapter_duration * min_chapters:
        target = max(target, min_chapters)
    return max(1, min(max_chapters, target))


def build_chapter_markers(
    *,
    segments: Sequence[TranscriptSegment] = (),
    duration: Optional[float] = None,
    titles: Sequence[str] = (),
    explicit_chapters: Sequence[Dict[str, Any]] = (),
    min_chapter_duration: float = 45.0,
    target_chapter_duration: float = 120.0,
    min_chapters: int = 3,
    max_chapters: int = 10,
) -> Tuple[List[ChapterMarker], List[str]]:
    warnings: List[str] = []
    total_duration = duration
    if total_duration is None and segments:
        total_duration = max(seg.end for seg in segments)

    if explicit_chapters:
        raw_markers: List[Dict[str, Any]] = []
        for idx, raw in enumerate(explicit_chapters, start=1):
            start = parse_timecode(
                raw.get("start", raw.get("timestamp", raw.get("start_seconds", raw.get("time"))))
            )
            if start is None:
                warnings.append(f"chapter {idx} skipped: missing start timestamp")
                continue
            raw_markers.append({
                "title": sanitize_title(raw.get("title"), fallback=f"Chapter {idx}"),
                "start": start,
                "description": str(raw.get("description", "")).strip(),
                "source": "explicit",
            })
    elif titles:
        if total_duration is None:
            raise ValueError("--duration or --transcript is required when using --clean-script")
        count = min(len(titles), max_chapters)
        transition_starts = find_transition_starts(
            segments,
            min_gap=min_chapter_duration,
            max_count=max(0, count - 1),
        )
        starts = [0.0] + transition_starts
        if len(starts) < count:
            starts = even_starts(count, total_duration)
        raw_markers = [
            {
                "title": sanitize_title(title, fallback=f"Chapter {idx}"),
                "start": starts[idx - 1],
                "description": "",
                "source": "clean_script",
            }
            for idx, title in enumerate(titles[:count], start=1)
        ]
    else:
        if total_duration is None:
            raise ValueError("--transcript or --chapters is required")
        count = _chapter_count_for_duration(
            total_duration,
            min_chapters=min_chapters,
            max_chapters=max_chapters,
            min_chapter_duration=min_chapter_duration,
            target_chapter_duration=target_chapter_duration,
        )
        starts = [0.0] + find_transition_starts(
            segments,
            min_gap=min_chapter_duration,
            max_count=max(0, count - 1),
        )
        if len(starts) < count:
            for start in even_starts(count, total_duration):
                if all(abs(start - existing) >= min_chapter_duration for existing in starts):
                    starts.append(start)
                if len(starts) >= count:
                    break
        starts = sorted(starts[:count])
        raw_markers = [
            {
                "title": text_title(_nearest_segment_text(segments, start), index=idx),
                "start": start,
                "description": "",
                "source": "transcript",
            }
            for idx, start in enumerate(starts, start=1)
        ]

    return normalize_chapters(
        raw_markers,
        duration=total_duration,
        min_chapter_duration=min_chapter_duration,
        warnings=warnings,
    )


def normalize_chapters(
    raw_markers: Sequence[Dict[str, Any]],
    *,
    duration: Optional[float],
    min_chapter_duration: float,
    warnings: Optional[List[str]] = None,
) -> Tuple[List[ChapterMarker], List[str]]:
    warnings = list(warnings or [])
    if not raw_markers:
        return [], warnings
    total_duration = duration
    markers = sorted(raw_markers, key=lambda item: float(item.get("start", 0.0)))
    if total_duration is None:
        total_duration = max(float(item.get("start", 0.0)) for item in markers) + min_chapter_duration
        warnings.append("duration missing; estimated final chapter end from min chapter duration")

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(markers, start=1):
        start = max(0.0, min(float(item.get("start", 0.0)), total_duration))
        if not normalized and start > 0.0:
            warnings.append("first chapter start adjusted to 0:00 for YouTube compatibility")
            start = 0.0
        if normalized and start - float(normalized[-1]["start"]) < min_chapter_duration:
            warnings.append(
                f"chapter {idx} skipped: starts less than {min_chapter_duration:g}s after previous"
            )
            continue
        normalized.append({**item, "start": start})

    chapters: List[ChapterMarker] = []
    for idx, item in enumerate(normalized, start=1):
        start = round(float(item["start"]), 3)
        next_start = (
            round(float(normalized[idx]["start"]), 3)
            if idx < len(normalized)
            else round(total_duration, 3)
        )
        end = max(start, next_start)
        chapters.append(
            ChapterMarker(
                id=f"ch{idx:02d}",
                title=sanitize_title(item.get("title"), fallback=f"Chapter {idx}"),
                start=start,
                end=end,
                duration=round(end - start, 3),
                description=str(item.get("description", "")).strip(),
                source=str(item.get("source", "inferred")),
            )
        )
    return chapters, warnings


def escape_markdown_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def escape_ffmetadata(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"([=;#\\])", r"\\\1", text)


def chapters_to_json(chapters: Sequence[ChapterMarker], *, warnings: Sequence[str]) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "warnings": list(warnings),
        "stats": {
            "chapter_count": len(chapters),
            "duration": round(chapters[-1].end, 3) if chapters else 0.0,
        },
        "chapters": [dataclasses.asdict(chapter) for chapter in chapters],
    }


def chapters_to_youtube(chapters: Sequence[ChapterMarker]) -> str:
    return "\n".join(
        f"{format_youtube_timestamp(chapter.start)} {chapter.title}"
        for chapter in chapters
    ) + ("\n" if chapters else "")


def chapters_to_markdown(chapters: Sequence[ChapterMarker], *, warnings: Sequence[str]) -> str:
    lines = [
        "# Chapters",
        "",
        "| Time | End | Title | Description | Source |",
        "|---|---|---|---|---|",
    ]
    for chapter in chapters:
        lines.append(
            "| "
            + " | ".join([
                format_youtube_timestamp(chapter.start),
                format_youtube_timestamp(chapter.end),
                escape_markdown_cell(chapter.title),
                escape_markdown_cell(chapter.description),
                escape_markdown_cell(chapter.source),
            ])
            + " |"
        )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def chapters_to_ffmetadata(chapters: Sequence[ChapterMarker]) -> str:
    lines = [";FFMETADATA1", ""]
    for chapter in chapters:
        lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(chapter.start * 1000))}",
            f"END={int(round(chapter.end * 1000))}",
            f"title={escape_ffmetadata(chapter.title)}",
            "",
        ])
    return "\n".join(lines)


def write_outputs(
    chapters: Sequence[ChapterMarker],
    *,
    output_dir: str,
    basename: str = "chapters",
    warnings: Sequence[str] = (),
) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"{basename}.json")
    md_path = os.path.join(output_dir, f"{basename}.md")
    ffmetadata_path = os.path.join(output_dir, f"{basename}.ffmetadata")
    youtube_path = os.path.join(output_dir, f"{basename}-youtube.txt")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(chapters_to_json(chapters, warnings=warnings), f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(chapters_to_markdown(chapters, warnings=warnings))
    with open(ffmetadata_path, "w", encoding="utf-8") as f:
        f.write(chapters_to_ffmetadata(chapters))
    with open(youtube_path, "w", encoding="utf-8") as f:
        f.write(chapters_to_youtube(chapters))
    return {
        "json": json_path,
        "markdown": md_path,
        "ffmetadata": ffmetadata_path,
        "youtube": youtube_path,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export video chapter markers")
    parser.add_argument("--transcript", help="Transcript JSON with segments")
    parser.add_argument("--clean-script", help="clean_script.md with `## ` headings")
    parser.add_argument("--chapters", help="Explicit chapter JSON array or {chapters: []}")
    parser.add_argument("--duration", type=float, help="Total video duration in seconds")
    parser.add_argument("--output-dir", required=True, help="Directory for chapter marker files")
    parser.add_argument("--basename", default="chapters", help="Output basename")
    parser.add_argument("--min-chapter-duration", type=float, default=45.0)
    parser.add_argument("--target-chapter-duration", type=float, default=120.0)
    parser.add_argument("--min-chapters", type=int, default=3)
    parser.add_argument("--max-chapters", type=int, default=10)
    parser.add_argument("--strict", action="store_true",
                        help="Return 2 when warnings were emitted")
    args = parser.parse_args(argv)

    if not (args.transcript or args.clean_script or args.chapters):
        parser.error("pass at least one of --transcript, --clean-script, or --chapters")

    segments: List[TranscriptSegment] = []
    duration = args.duration
    if args.transcript:
        segments, transcript_duration = load_transcript(args.transcript)
        duration = duration if duration is not None else transcript_duration

    titles = parse_chapter_headings(args.clean_script) if args.clean_script else []
    explicit = load_explicit_chapters(args.chapters) if args.chapters else []
    try:
        chapters, warnings = build_chapter_markers(
            segments=segments,
            duration=duration,
            titles=titles,
            explicit_chapters=explicit,
            min_chapter_duration=args.min_chapter_duration,
            target_chapter_duration=args.target_chapter_duration,
            min_chapters=args.min_chapters,
            max_chapters=args.max_chapters,
        )
    except ValueError as exc:
        print(f"chapter_markers: {exc}", file=sys.stderr)
        return 1

    if not chapters:
        print("chapter_markers: no chapters generated", file=sys.stderr)
        return 1

    paths = write_outputs(
        chapters,
        output_dir=args.output_dir,
        basename=args.basename,
        warnings=warnings,
    )
    print(f"chapter markers: {len(chapters)} -> {args.output_dir}")
    for key in ("json", "markdown", "ffmetadata", "youtube"):
        print(f"{key}: {paths[key]}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 2 if args.strict and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
