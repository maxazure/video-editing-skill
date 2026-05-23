#!/usr/bin/env python3
"""Export local edit decisions to a lightweight CMX 3600-style EDL.

The exporter accepts either render_config.json clips or a rough_cut/jump_cut
cut-list with keep_segments. It writes a single video-track EDL for NLE handoff
plus a JSON manifest that preserves absolute paths and exact seconds.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class EditSegment:
    source: str
    start: float
    end: float
    label: str = ""
    text: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class EdlEvent:
    number: int
    reel: str
    source: str
    source_start: float
    source_end: float
    record_start: float
    record_end: float
    source_in_tc: str
    source_out_tc: str
    record_in_tc: str
    record_out_tc: str
    label: str = ""
    text: str = ""


def _round4(value: float) -> float:
    return round(max(0.0, value), 4)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _abs_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _segment_map(transcript_path: str) -> Dict[Any, Dict[str, Any]]:
    transcript = _load_json(transcript_path)
    lookup: Dict[Any, Dict[str, Any]] = {}
    for index, segment in enumerate(transcript.get("segments") or [], start=1):
        keys = [segment.get("id"), str(segment.get("id")), index, str(index)]
        for key in keys:
            if key is not None:
                lookup[key] = segment
    return lookup


def _coerce_time(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number, got {value!r}") from exc
    if result < 0:
        raise ValueError(f"{field} must be non-negative, got {result}")
    return _round4(result)


def _validate_segment(segment: EditSegment) -> EditSegment:
    if segment.end <= segment.start:
        raise ValueError(
            f"segment end must be after start: {segment.source} {segment.start}-{segment.end}"
        )
    return segment


def load_render_config_segments(config_path: str) -> List[EditSegment]:
    """Load primary clip ranges from render_config.json."""
    config = _load_json(config_path)
    clips = config.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("render config must contain a non-empty clips list")

    transcript_cache: Dict[str, Dict[Any, Dict[str, Any]]] = {}
    segments: List[EditSegment] = []

    for index, entry in enumerate(clips, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"clip #{index} must be an object")
        if "video" not in entry:
            raise ValueError(f"clip #{index} is missing video")
        source = _abs_path(str(entry["video"]))

        if "start" in entry and "end" in entry:
            start = _coerce_time(entry["start"], field=f"clip #{index} start")
            end = _coerce_time(entry["end"], field=f"clip #{index} end")
            text = str(entry.get("text", ""))
        else:
            if "transcript" not in entry or "segment_id" not in entry:
                raise ValueError(
                    f"clip #{index} needs start/end or transcript + segment_id"
                )
            transcript = _abs_path(str(entry["transcript"]))
            if transcript not in transcript_cache:
                transcript_cache[transcript] = _segment_map(transcript)
            seg_id = entry["segment_id"]
            segment = transcript_cache[transcript].get(seg_id)
            if segment is None:
                segment = transcript_cache[transcript].get(str(seg_id))
            if segment is None:
                raise ValueError(
                    f"clip #{index}: segment_id {seg_id!r} not found in {transcript}"
                )
            start = _coerce_time(segment.get("start"), field=f"clip #{index} segment start")
            end = _coerce_time(segment.get("end"), field=f"clip #{index} segment end")
            text = str(segment.get("text", ""))

        label = str(entry.get("name") or entry.get("label") or f"clip_{index:03d}")
        segments.append(_validate_segment(EditSegment(source, start, end, label, text)))

    return segments


def load_cut_list_segments(cut_list_path: str, source_override: Optional[str] = None) -> List[EditSegment]:
    """Load keep_segments from rough_cut.py or jump_cut.py JSON."""
    data = _load_json(cut_list_path)
    source = source_override or data.get("input") or data.get("source") or data.get("video")
    if not source:
        raise ValueError("cut list has no input/source path; pass --source")
    source_path = _abs_path(str(source))

    keep_segments = data.get("keep_segments")
    if not isinstance(keep_segments, list) or not keep_segments:
        raise ValueError("cut list must contain a non-empty keep_segments list")

    segments: List[EditSegment] = []
    for index, item in enumerate(keep_segments, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"keep segment #{index} must be an object")
        start = _coerce_time(item.get("start"), field=f"keep segment #{index} start")
        end = _coerce_time(item.get("end"), field=f"keep segment #{index} end")
        label = str(item.get("label") or f"keep_{index:03d}")
        segments.append(_validate_segment(EditSegment(source_path, start, end, label)))
    return segments


def seconds_to_frames(seconds: float, fps: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    return max(0, int(round(seconds * fps)))


def frames_to_timecode(frames: int, fps: float) -> str:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    nominal_fps = int(round(fps))
    if nominal_fps <= 0:
        raise ValueError("fps rounds to zero")
    frames = max(0, int(frames))
    hours, remainder = divmod(frames, nominal_fps * 3600)
    minutes, remainder = divmod(remainder, nominal_fps * 60)
    seconds, frame = divmod(remainder, nominal_fps)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def seconds_to_timecode(seconds: float, fps: float) -> str:
    return frames_to_timecode(seconds_to_frames(seconds, fps), fps)


def reel_name(source_path: str) -> str:
    stem = os.path.splitext(os.path.basename(source_path))[0].upper()
    token = re.sub(r"[^A-Z0-9]", "", stem)
    return (token or "AX")[:8]


def build_events(segments: Sequence[EditSegment], fps: float) -> List[EdlEvent]:
    events: List[EdlEvent] = []
    record_cursor = 0
    for number, segment in enumerate(segments, start=1):
        source_in = seconds_to_frames(segment.start, fps)
        source_out = seconds_to_frames(segment.end, fps)
        duration = source_out - source_in
        if duration <= 0:
            continue
        record_in = record_cursor
        record_out = record_in + duration
        record_cursor = record_out
        events.append(EdlEvent(
            number=number,
            reel=reel_name(segment.source),
            source=segment.source,
            source_start=_round4(segment.start),
            source_end=_round4(segment.end),
            record_start=_round4(record_in / fps),
            record_end=_round4(record_out / fps),
            source_in_tc=frames_to_timecode(source_in, fps),
            source_out_tc=frames_to_timecode(source_out, fps),
            record_in_tc=frames_to_timecode(record_in, fps),
            record_out_tc=frames_to_timecode(record_out, fps),
            label=segment.label,
            text=segment.text,
        ))
    return events


def _comment_lines(event: EdlEvent, include_text: bool) -> Iterable[str]:
    yield f"* FROM CLIP NAME: {os.path.basename(event.source)}"
    yield f"* SOURCE FILE: {event.source}"
    yield f"* SOURCE RANGE: {event.source_start:.4f} {event.source_end:.4f}"
    if event.label:
        yield f"* LABEL: {event.label}"
    if include_text and event.text:
        one_line = " ".join(event.text.split())
        yield f"* TRANSCRIPT: {one_line}"


def render_edl(events: Sequence[EdlEvent], *, title: str, include_text: bool = False) -> str:
    safe_title = title.strip() or "VIDEO_EDIT"
    lines = [
        f"TITLE: {safe_title}",
        "FCM: NON-DROP FRAME",
        "",
    ]
    for event in events:
        lines.append(
            f"{event.number:03d}  {event.reel:<8} V     C        "
            f"{event.source_in_tc} {event.source_out_tc} "
            f"{event.record_in_tc} {event.record_out_tc}"
        )
        lines.extend(_comment_lines(event, include_text))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_manifest(events: Sequence[EdlEvent], *, title: str, fps: float, output: str) -> Dict[str, Any]:
    return {
        "kind": "nle_handoff_edl",
        "format": "cmx3600_style_single_track",
        "title": title,
        "fps": fps,
        "timecode": "non_drop_frame",
        "output": _abs_path(output),
        "event_count": len(events),
        "duration_seconds": _round4(events[-1].record_end if events else 0.0),
        "events": [asdict(event) for event in events],
        "notes": [
            "Single video-track EDL for review/editor handoff.",
            "Exact source paths and seconds are preserved in this manifest.",
            "Use export_capcut.py when a JianYing/CapCut draft is required.",
        ],
    }


def write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_abs_path(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(_abs_path(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export render_config or cut-list keep segments as a single-track EDL."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="render_config.json with clips.")
    source.add_argument("--cut-list", help="rough_cut.py or jump_cut.py JSON with keep_segments.")
    parser.add_argument("--source", help="Override/define source media path for --cut-list.")
    parser.add_argument("--output", required=True, help="Output .edl path.")
    parser.add_argument("--manifest", help="Output manifest JSON path (default: <output>.json).")
    parser.add_argument("--no-manifest", action="store_true", help="Only write the EDL file.")
    parser.add_argument("--fps", type=float, default=30.0, help="Timeline frame rate for timecode.")
    parser.add_argument("--title", default=None, help="EDL title (default: output filename stem).")
    parser.add_argument("--include-transcript-comments", action="store_true",
                        help="Include transcript text as EDL comments.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    title = args.title or os.path.splitext(os.path.basename(args.output))[0]

    try:
        if args.config:
            segments = load_render_config_segments(args.config)
        else:
            segments = load_cut_list_segments(args.cut_list, source_override=args.source)
        events = build_events(segments, fps=args.fps)
        if not events:
            raise ValueError("no EDL events were generated")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    edl_text = render_edl(
        events,
        title=title,
        include_text=args.include_transcript_comments,
    )
    write_text(args.output, edl_text)

    manifest_path = args.manifest or f"{args.output}.json"
    if not args.no_manifest:
        write_json(manifest_path, build_manifest(events, title=title, fps=args.fps, output=args.output))

    print(f"EDL written: {args.output}", file=sys.stderr)
    if not args.no_manifest:
        print(f"Manifest written: {manifest_path}", file=sys.stderr)
    print(f"Events: {len(events)}, duration: {events[-1].record_end:.2f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
