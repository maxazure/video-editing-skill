#!/usr/bin/env python3
"""Turn SRT subtitles plus a human edit guide into auditable edit artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from import_capcut_subtitles import SubtitleSegment, parse_srt


VERSION = "srt_edit_plan.v1"
KEEP_ACTIONS = {"keep", "include", "use", "select"}
DROP_ACTIONS = {"drop", "skip", "exclude", "remove"}
METADATA_KEYS = {"title", "subtitle", "platform", "cover_style", "profile"}


@dataclass(frozen=True)
class GuideDirective:
    action: str
    segment_ids: Tuple[int, ...]
    note: str
    line: int
    raw: str


@dataclass(frozen=True)
class EditClip:
    output_index: int
    segment_ids: Tuple[int, ...]
    start: float
    end: float
    duration: float
    text: str
    note: str
    guide_line: int


def _round4(value: float) -> float:
    return round(max(0.0, float(value)), 4)


def _normalize_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:[-*+]|\d+[.)])\s*", "", line)
    line = re.sub(r"^\[[ xX]\]\s*", "", line)
    return line.strip()


def _expand_ranges(value: str) -> Tuple[int, ...]:
    ids: List[int] = []
    for raw in re.split(r"\s*,\s*", value.strip()):
        raw = raw.strip()
        if not raw:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*[-–—]\s*(\d+))?", raw)
        if not match:
            raise ValueError(f"bad segment range: {raw!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        step = 1 if end >= start else -1
        ids.extend(range(start, end + step, step))
    return tuple(ids)


def _parse_directive(line: str, line_number: int) -> Optional[GuideDirective]:
    normalized = _normalize_line(line)
    if not normalized or normalized.startswith("#"):
        return None

    match = re.match(r"^(keep|include|use|select|drop|skip|exclude|remove)\b\s*:?\s*(.+)$", normalized, re.I)
    if not match:
        return None

    action = match.group(1).lower()
    rest = match.group(2).strip()
    id_match = re.match(r"^([0-9][0-9,\s\-–—]*)(?:[:：]?\s*(.*))?$", rest)
    if not id_match:
        raise ValueError(f"line {line_number}: expected subtitle ids after {action!r}")
    ids = _expand_ranges(id_match.group(1))
    if not ids:
        raise ValueError(f"line {line_number}: no subtitle ids found")
    note = (id_match.group(2) or "").strip()
    normalized_action = "keep" if action in KEEP_ACTIONS else "drop"
    return GuideDirective(
        action=normalized_action,
        segment_ids=ids,
        note=note,
        line=line_number,
        raw=line.rstrip("\n"),
    )


def parse_guide(path: str) -> Tuple[List[GuideDirective], Dict[str, str]]:
    directives: List[GuideDirective] = []
    metadata: Dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            normalized = _normalize_line(line)
            if not normalized or normalized.startswith("#"):
                continue
            if ":" in normalized:
                key, value = normalized.split(":", 1)
                key = key.strip().lower().replace("-", "_")
                if key in METADATA_KEYS and value.strip():
                    metadata[key] = value.strip()
                    continue
            directive = _parse_directive(line, line_number)
            if directive:
                directives.append(directive)
                continue
            raise ValueError(f"line {line_number}: unsupported edit guide line: {line.rstrip()!r}")
    if not any(item.action == "keep" for item in directives):
        raise ValueError("edit guide must contain at least one keep/include/use/select directive")
    return directives, metadata


def _contiguous_groups(ids: Sequence[int]) -> List[Tuple[int, ...]]:
    groups: List[List[int]] = []
    for seg_id in ids:
        if not groups or seg_id != groups[-1][-1] + 1:
            groups.append([seg_id])
        else:
            groups[-1].append(seg_id)
    return [tuple(group) for group in groups]


def _text_for_segments(segments: Sequence[SubtitleSegment]) -> str:
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip())


def build_edit_plan(
    segments: Sequence[SubtitleSegment],
    directives: Sequence[GuideDirective],
    *,
    srt_path: str,
    guide_path: str,
    source_media: Optional[str] = None,
    metadata: Optional[Mapping[str, str]] = None,
    require_all_reviewed: bool = False,
) -> Dict[str, Any]:
    by_id = {segment.id: segment for segment in segments}
    missing: List[Dict[str, Any]] = []
    kept_ids: List[int] = []
    dropped_ids: List[int] = []
    clips: List[EditClip] = []

    for directive in directives:
        unknown = [seg_id for seg_id in directive.segment_ids if seg_id not in by_id]
        if unknown:
            missing.append({"line": directive.line, "ids": unknown, "raw": directive.raw})
            continue
        if directive.action == "drop":
            dropped_ids.extend(directive.segment_ids)
            continue
        for group in _contiguous_groups(directive.segment_ids):
            group_segments = [by_id[seg_id] for seg_id in group]
            start = group_segments[0].start
            end = group_segments[-1].end
            clips.append(EditClip(
                output_index=len(clips) + 1,
                segment_ids=group,
                start=_round4(start),
                end=_round4(end),
                duration=_round4(end - start),
                text=_text_for_segments(group_segments),
                note=directive.note,
                guide_line=directive.line,
            ))
            kept_ids.extend(group)

    if not clips:
        raise ValueError("edit guide selected no valid keep segments")

    kept_set = set(kept_ids)
    dropped_set = set(dropped_ids)
    all_ids = {segment.id for segment in segments}
    duplicate_keeps = sorted(seg_id for seg_id in kept_set if kept_ids.count(seg_id) > 1)
    conflicts = sorted(kept_set & dropped_set)
    unused = sorted(all_ids - kept_set - dropped_set)

    blocking: List[str] = []
    if missing:
        blocking.append("missing_segment_refs")
    if duplicate_keeps:
        blocking.append("duplicate_keeps")
    if conflicts:
        blocking.append("keep_drop_conflicts")
    if require_all_reviewed and unused:
        blocking.append("unreviewed_segments")

    warnings: List[str] = []
    if unused:
        warnings.append("unreviewed subtitle segments were neither kept nor dropped")

    total_duration = _round4(max((segment.end for segment in segments), default=0.0))
    output_duration = _round4(sum(clip.duration for clip in clips))
    return {
        "kind": "srt_edit_plan",
        "version": VERSION,
        "source_srt": os.path.abspath(srt_path),
        "guide": os.path.abspath(guide_path),
        "source_media": os.path.abspath(source_media) if source_media else None,
        "metadata": dict(metadata or {}),
        "segments": [asdict(segment) for segment in segments],
        "directives": [
            {
                "action": item.action,
                "segment_ids": list(item.segment_ids),
                "note": item.note,
                "line": item.line,
                "raw": item.raw,
            }
            for item in directives
        ],
        "clips": [
            {
                "output_index": clip.output_index,
                "segment_ids": list(clip.segment_ids),
                "start": clip.start,
                "end": clip.end,
                "duration": clip.duration,
                "text": clip.text,
                "note": clip.note,
                "guide_line": clip.guide_line,
            }
            for clip in clips
        ],
        "dropped_segments": [
            asdict(by_id[seg_id]) | {"note": next((d.note for d in directives if d.action == "drop" and seg_id in d.segment_ids), "")}
            for seg_id in sorted(dropped_set)
            if seg_id in by_id
        ],
        "missing_refs": missing,
        "summary": {
            "subtitle_segments": len(segments),
            "kept_segments": len(kept_set),
            "dropped_segments": len(dropped_set),
            "unused_segments": len(unused),
            "clips": len(clips),
            "input_duration": total_duration,
            "output_duration_estimate": output_duration,
            "compression_ratio": round(total_duration / output_duration, 3) if output_duration else None,
            "duplicate_keeps": duplicate_keeps,
            "keep_drop_conflicts": conflicts,
            "unused_segment_ids": unused,
            "warnings": warnings,
            "blocking": blocking,
        },
    }


def build_render_config(plan: Mapping[str, Any], *, source_media: str, title: Optional[str] = None) -> Dict[str, Any]:
    metadata = dict(plan.get("metadata") or {})
    if title:
        metadata["title"] = title
    clips = []
    source = os.path.abspath(source_media)
    for clip in plan.get("clips") or []:
        clips.append({
            "video": source,
            "start": clip["start"],
            "end": clip["end"],
            "label": f"srt_edit_{clip['output_index']:03d}",
            "srt_segment_ids": clip["segment_ids"],
            "text": clip["text"],
            "edit_note": clip.get("note", ""),
        })
    render_config: Dict[str, Any] = {"clips": clips}
    for key in ("title", "subtitle", "cover_style", "profile"):
        if metadata.get(key):
            render_config[key] = metadata[key]
    if metadata.get("platform"):
        render_config["platform"] = metadata["platform"]
    return render_config


def build_cut_list(plan: Mapping[str, Any], *, source_media: str) -> Dict[str, Any]:
    clips = sorted(plan.get("clips") or [], key=lambda item: (item["start"], item["end"], item["output_index"]))
    keep_segments = []
    for clip in clips:
        keep_segments.append({
            "start": clip["start"],
            "end": clip["end"],
            "duration": clip["duration"],
            "label": f"srt_edit_{clip['output_index']:03d}",
            "segment_ids": clip["segment_ids"],
            "text": clip["text"],
        })
    return {
        "kind": "srt_edit_plan_cut",
        "version": VERSION,
        "input": os.path.abspath(source_media),
        "source_srt": plan.get("source_srt"),
        "guide": plan.get("guide"),
        "keep_segments": keep_segments,
        "output_duration_estimate": _round4(sum(item["duration"] for item in keep_segments)),
        "review_hint": "This cut list is sorted by source time for timeline_view review. Use render_config for reordered output.",
    }


def render_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    lines = [
        "# SRT Edit Plan Review",
        "",
        f"- Clips: {summary.get('clips', 0)}",
        f"- Kept subtitle segments: {summary.get('kept_segments', 0)}",
        f"- Dropped subtitle segments: {summary.get('dropped_segments', 0)}",
        f"- Unreviewed subtitle segments: {summary.get('unused_segments', 0)}",
        f"- Output duration estimate: {summary.get('output_duration_estimate', 0)}s",
    ]
    if summary.get("blocking"):
        lines.append(f"- Blocking: {', '.join(summary['blocking'])}")
    if summary.get("warnings"):
        lines.append(f"- Warnings: {', '.join(summary['warnings'])}")
    lines.extend([
        "",
        "## Kept Clips",
        "",
        "| Out | SRT IDs | Source | Duration | Note | Text |",
        "|---:|---|---:|---:|---|---|",
    ])
    for clip in plan.get("clips") or []:
        ids = ",".join(str(item) for item in clip["segment_ids"])
        source = f"{clip['start']:.2f}-{clip['end']:.2f}"
        lines.append(
            f"| {clip['output_index']} | {ids} | {source} | {clip['duration']:.2f}s | "
            f"{clip.get('note', '')} | {clip.get('text', '')} |"
        )

    dropped = plan.get("dropped_segments") or []
    if dropped:
        lines.extend(["", "## Dropped Segments", "", "| # | Source | Note | Text |", "|---:|---:|---|---|"])
        for segment in dropped:
            lines.append(
                f"| {segment['id']} | {segment['start']:.2f}-{segment['end']:.2f} | "
                f"{segment.get('note', '')} | {segment.get('text', '')} |"
            )

    unused = summary.get("unused_segment_ids") or []
    if unused:
        lines.extend(["", "## Unreviewed Segment IDs", "", ", ".join(str(item) for item in unused)])
    return "\n".join(lines) + "\n"


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an edit plan from SRT subtitles and a human guide.")
    parser.add_argument("--srt", required=True, help="Input SRT file.")
    parser.add_argument("--guide", required=True, help="Markdown/plain-text guide with keep/drop lines.")
    parser.add_argument("--source-media", help="Original video/audio file for render_config and cut-list outputs.")
    parser.add_argument("--output", help="Output srt_edit_plan JSON path. Prints to stdout when omitted.")
    parser.add_argument("--render-config", help="Optional render_config.json output path.")
    parser.add_argument("--cut-list", help="Optional source-time cut list for timeline_view review.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--title", help="Override render_config title.")
    parser.add_argument("--platform", help="Override guide platform metadata.")
    parser.add_argument("--require-all-reviewed", action="store_true",
                        help="Mark unused SRT segments as blocking.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 2 when the plan has blocking issues.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        segments = parse_srt(args.srt)
        directives, metadata = parse_guide(args.guide)
        if args.platform:
            metadata["platform"] = args.platform
        plan = build_edit_plan(
            segments,
            directives,
            srt_path=args.srt,
            guide_path=args.guide,
            source_media=args.source_media,
            metadata=metadata,
            require_all_reviewed=args.require_all_reviewed,
        )
        if args.render_config and not args.source_media:
            raise ValueError("--render-config requires --source-media")
        if args.cut_list and not args.source_media:
            raise ValueError("--cut-list requires --source-media")
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.output:
        write_json(args.output, plan)
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))

    if args.render_config:
        write_json(args.render_config, build_render_config(plan, source_media=args.source_media, title=args.title))
    if args.cut_list:
        write_json(args.cut_list, build_cut_list(plan, source_media=args.source_media))
    if args.markdown:
        write_text(args.markdown, render_markdown(plan))

    blocking = plan.get("summary", {}).get("blocking") or []
    print(
        f"Built SRT edit plan with {plan['summary']['clips']} clips"
        + (f"; blocking: {', '.join(blocking)}" if blocking else ""),
        file=sys.stderr,
    )
    return 2 if args.strict and blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
