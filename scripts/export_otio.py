#!/usr/bin/env python3
"""Export local edit decisions to an OpenTimelineIO .otio JSON timeline.

The exporter accepts the same inputs as export_edl.py: either render_config.json
clips or a rough_cut/jump_cut cut-list with keep_segments. It writes a simple
single video track plus an optional linked audio track for NLE handoff, and a
JSON manifest that preserves exact source seconds.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

from export_edl import (
    EdlEvent,
    _abs_path,
    _round4,
    build_events,
    load_cut_list_segments,
    load_render_config_segments,
    seconds_to_frames,
    write_json,
    write_text,
)


def _rational_time(frames: int, fps: float) -> Dict[str, Any]:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "value": max(0, int(frames)),
        "rate": fps,
    }


def _time_range(start_frames: int, duration_frames: int, fps: float) -> Dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start_frames, fps),
        "duration": _rational_time(duration_frames, fps),
    }


def _source_uri(path: str) -> str:
    return Path(_abs_path(path)).as_uri()


def _available_ranges(events: Sequence[EdlEvent], fps: float) -> Dict[str, Dict[str, Any]]:
    ranges: Dict[str, Dict[str, Any]] = {}
    for event in events:
        ranges[event.source] = _time_range(
            0,
            max(
                seconds_to_frames(event.source_end, fps),
                ranges.get(event.source, {})
                .get("duration", {})
                .get("value", 0),
                1,
            ),
            fps,
        )
    return ranges


def _clip(
    event: EdlEvent,
    *,
    fps: float,
    track_kind: str,
    available_range: Dict[str, Any],
    include_transcript: bool,
) -> Dict[str, Any]:
    source_start = seconds_to_frames(event.source_start, fps)
    duration = seconds_to_frames(event.source_end - event.source_start, fps)
    metadata: Dict[str, Any] = {
        "video_editing_skill": {
            "event_number": event.number,
            "track_kind": track_kind,
            "source_path": event.source,
            "source_start": event.source_start,
            "source_end": event.source_end,
            "record_start": event.record_start,
            "record_end": event.record_end,
            "label": event.label,
        }
    }
    if include_transcript and event.text:
        metadata["video_editing_skill"]["transcript"] = " ".join(event.text.split())

    return {
        "OTIO_SCHEMA": "Clip.2",
        "name": event.label or os.path.splitext(os.path.basename(event.source))[0],
        "media_reference": {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": _source_uri(event.source),
            "available_range": available_range,
            "metadata": {
                "video_editing_skill": {
                    "source_path": event.source,
                }
            },
        },
        "source_range": _time_range(source_start, duration, fps),
        "effects": [],
        "markers": [],
        "metadata": metadata,
    }


def _track(
    events: Sequence[EdlEvent],
    *,
    name: str,
    kind: str,
    fps: float,
    available_ranges: Dict[str, Dict[str, Any]],
    include_transcript: bool,
) -> Dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Track.1",
        "name": name,
        "kind": kind,
        "children": [
            _clip(
                event,
                fps=fps,
                track_kind=kind,
                available_range=available_ranges[event.source],
                include_transcript=include_transcript,
            )
            for event in events
        ],
        "metadata": {
            "video_editing_skill": {
                "contiguous_record_timeline": True,
                "source": "export_otio.py",
            }
        },
        "source_range": None,
    }


def build_otio_document(
    events: Sequence[EdlEvent],
    *,
    title: str,
    fps: float,
    include_audio_track: bool = True,
    include_transcript: bool = False,
) -> Dict[str, Any]:
    """Build a minimal OTIO timeline dictionary."""
    if not events:
        raise ValueError("no events were provided")
    if fps <= 0:
        raise ValueError("fps must be greater than zero")

    safe_title = title.strip() or "Video Edit"
    available_ranges = _available_ranges(events, fps)
    tracks: List[Dict[str, Any]] = [
        _track(
            events,
            name="V1",
            kind="Video",
            fps=fps,
            available_ranges=available_ranges,
            include_transcript=include_transcript,
        )
    ]
    if include_audio_track:
        tracks.append(
            _track(
                events,
                name="A1",
                kind="Audio",
                fps=fps,
                available_ranges=available_ranges,
                include_transcript=include_transcript,
            )
        )

    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": safe_title,
        "global_start_time": _rational_time(0, fps),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": tracks,
            "metadata": {
                "video_editing_skill": {
                    "format": "opentimelineio_single_track_json",
                    "fps": fps,
                    "duration_seconds": _round4(events[-1].record_end),
                }
            },
            "source_range": None,
        },
        "metadata": {
            "video_editing_skill": {
                "exporter": "export_otio.py",
                "event_count": len(events),
                "duration_seconds": _round4(events[-1].record_end),
                "notes": [
                    "Simple contiguous OTIO timeline for NLE review handoff.",
                    "Complex overlays, subtitles, B-roll layers, and generated assets still belong in render_final.py or export_capcut.py.",
                ],
            }
        },
    }


def render_otio(
    events: Sequence[EdlEvent],
    *,
    title: str,
    fps: float,
    include_audio_track: bool = True,
    include_transcript: bool = False,
) -> str:
    document = build_otio_document(
        events,
        title=title,
        fps=fps,
        include_audio_track=include_audio_track,
        include_transcript=include_transcript,
    )
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def build_manifest(
    events: Sequence[EdlEvent],
    *,
    title: str,
    fps: float,
    output: str,
    include_audio_track: bool,
) -> Dict[str, Any]:
    return {
        "kind": "nle_handoff_otio",
        "format": "opentimelineio_single_track_json",
        "title": title,
        "fps": fps,
        "output": _abs_path(output),
        "event_count": len(events),
        "track_count": 2 if include_audio_track else 1,
        "includes_audio_track": include_audio_track,
        "duration_seconds": _round4(events[-1].record_end if events else 0.0),
        "events": [asdict(event) for event in events],
        "notes": [
            "OpenTimelineIO .otio JSON for NLE review handoff.",
            "Clip order is contiguous on the record timeline; source ranges preserve exact seconds.",
            "Use export_edl.py for legacy CMX 3600 handoff or export_fcpxml.py for FCPXML-specific workflows.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export render_config or cut-list keep segments as an OpenTimelineIO .otio timeline."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="render_config.json with clips.")
    source.add_argument("--cut-list", help="rough_cut.py or jump_cut.py JSON with keep_segments.")
    parser.add_argument("--source", help="Override/define source media path for --cut-list.")
    parser.add_argument("--output", required=True, help="Output .otio path.")
    parser.add_argument("--manifest", help="Output manifest JSON path (default: <output>.json).")
    parser.add_argument("--no-manifest", action="store_true", help="Only write the OTIO file.")
    parser.add_argument("--fps", type=float, default=30.0, help="Timeline frame rate.")
    parser.add_argument("--title", default=None, help="Timeline title (default: output filename stem).")
    parser.add_argument("--no-audio-track", action="store_true", help="Only write the video track.")
    parser.add_argument("--include-transcript-metadata", action="store_true",
                        help="Include transcript text in OTIO clip metadata.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    title = args.title or os.path.splitext(os.path.basename(args.output))[0]
    include_audio_track = not args.no_audio_track

    try:
        if args.config:
            segments = load_render_config_segments(args.config)
        else:
            segments = load_cut_list_segments(args.cut_list, source_override=args.source)
        events = build_events(segments, fps=args.fps)
        if not events:
            raise ValueError("no OTIO events were generated")
        otio_text = render_otio(
            events,
            title=title,
            fps=args.fps,
            include_audio_track=include_audio_track,
            include_transcript=args.include_transcript_metadata,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    write_text(args.output, otio_text)
    manifest_path = args.manifest or f"{args.output}.json"
    if not args.no_manifest:
        write_json(
            manifest_path,
            build_manifest(
                events,
                title=title,
                fps=args.fps,
                output=args.output,
                include_audio_track=include_audio_track,
            ),
        )

    print(f"OTIO written: {args.output}", file=sys.stderr)
    if not args.no_manifest:
        print(f"Manifest written: {manifest_path}", file=sys.stderr)
    print(f"Events: {len(events)}, duration: {events[-1].record_end:.2f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
