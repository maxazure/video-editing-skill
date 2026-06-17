#!/usr/bin/env python3
"""Export local edit decisions to a simple single-spine FCPXML file.

The exporter accepts the same inputs as export_edl.py: either render_config.json
clips or a rough_cut/jump_cut cut-list with keep_segments. It writes FCPXML for
Final Cut Pro / DaVinci Resolve handoff plus a JSON manifest that preserves the
exact source seconds used to build the timeline.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Sequence
from xml.dom import minidom
from xml.etree import ElementTree as ET

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


def _frame_duration(fps: float) -> str:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    nominal = int(round(fps))
    if abs(fps - nominal) < 0.0001:
        return f"1/{nominal}s"
    # FCPXML accepts rational seconds. This covers common rates such as 29.97.
    from fractions import Fraction

    rate = Fraction(str(fps)).limit_denominator(100000)
    return f"{rate.denominator}/{rate.numerator}s"


def seconds_to_fcpx_time(seconds: float, fps: float) -> str:
    """Return an FCPXML rational time snapped to the target frame rate."""
    frames = seconds_to_frames(seconds, fps)
    if frames == 0:
        return "0s"
    nominal = int(round(fps))
    if abs(fps - nominal) < 0.0001:
        return f"{frames}/{nominal}s"
    from fractions import Fraction

    rate = Fraction(str(fps)).limit_denominator(100000)
    numerator = frames * rate.denominator
    denominator = rate.numerator
    return f"{numerator}/{denominator}s"


def _source_uri(path: str) -> str:
    return Path(_abs_path(path)).as_uri()


def _asset_resources(events: Sequence[EdlEvent], fps: float) -> Dict[str, Dict[str, Any]]:
    assets: Dict[str, Dict[str, Any]] = {}
    for event in events:
        asset = assets.setdefault(event.source, {
            "id": f"r{len(assets) + 2}",
            "source": event.source,
            "name": os.path.basename(event.source),
            "duration_frames": 0,
        })
        asset["duration_frames"] = max(
            int(asset["duration_frames"]),
            seconds_to_frames(event.source_end, fps),
        )
    return assets


def render_fcpxml(
    events: Sequence[EdlEvent],
    *,
    title: str,
    fps: float,
    width: int = 1080,
    height: int = 1920,
    fcpxml_version: str = "1.10",
) -> str:
    """Build a minimal FCPXML timeline from contiguous EDL-style events."""
    if not events:
        raise ValueError("no events were provided")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be greater than zero")

    safe_title = title.strip() or "Video Edit"
    assets = _asset_resources(events, fps)
    total_duration = seconds_to_fcpx_time(events[-1].record_end, fps)

    root = ET.Element("fcpxml", {"version": fcpxml_version})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": f"FFVideoFormat{height}p{int(round(fps))}",
        "frameDuration": _frame_duration(fps),
        "width": str(width),
        "height": str(height),
        "colorSpace": "1-1-1 (Rec. 709)",
    })

    for asset in assets.values():
        duration_frames = max(1, int(asset["duration_frames"]))
        ET.SubElement(resources, "asset", {
            "id": str(asset["id"]),
            "name": str(asset["name"]),
            "src": _source_uri(str(asset["source"])),
            "start": "0s",
            "duration": seconds_to_fcpx_time(duration_frames / fps, fps),
            "hasVideo": "1",
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        })

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": safe_title})
    project = ET.SubElement(event, "project", {"name": safe_title})
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": total_duration,
        "tcStart": "0s",
        "tcFormat": "NDF",
    })
    spine = ET.SubElement(sequence, "spine")

    for event_item in events:
        asset = assets[event_item.source]
        attrs = {
            "name": event_item.label or os.path.splitext(os.path.basename(event_item.source))[0],
            "ref": str(asset["id"]),
            "offset": seconds_to_fcpx_time(event_item.record_start, fps),
            "start": seconds_to_fcpx_time(event_item.source_start, fps),
            "duration": seconds_to_fcpx_time(event_item.source_end - event_item.source_start, fps),
        }
        ET.SubElement(spine, "asset-clip", attrs)

    raw = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
    return pretty.replace(
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>',
        1,
    )


def build_manifest(
    events: Sequence[EdlEvent],
    *,
    title: str,
    fps: float,
    output: str,
    width: int,
    height: int,
) -> Dict[str, Any]:
    return {
        "kind": "nle_handoff_fcpxml",
        "format": "fcpxml_single_spine",
        "title": title,
        "fps": fps,
        "width": width,
        "height": height,
        "output": _abs_path(output),
        "event_count": len(events),
        "duration_seconds": _round4(events[-1].record_end if events else 0.0),
        "events": [asdict(event) for event in events],
        "notes": [
            "Single-spine FCPXML for Final Cut Pro / DaVinci Resolve review handoff.",
            "Clip offsets are frame-snapped to the requested fps.",
            "Complex overlays, subtitles, B-roll layers, and generated assets still belong in render_final.py or export_capcut.py.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export render_config or cut-list keep segments as single-spine FCPXML."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="render_config.json with clips.")
    source.add_argument("--cut-list", help="rough_cut.py or jump_cut.py JSON with keep_segments.")
    parser.add_argument("--source", help="Override/define source media path for --cut-list.")
    parser.add_argument("--output", required=True, help="Output .fcpxml path.")
    parser.add_argument("--manifest", help="Output manifest JSON path (default: <output>.json).")
    parser.add_argument("--no-manifest", action="store_true", help="Only write the FCPXML file.")
    parser.add_argument("--fps", type=float, default=30.0, help="Timeline frame rate.")
    parser.add_argument("--width", type=int, default=1080, help="Timeline width in pixels.")
    parser.add_argument("--height", type=int, default=1920, help="Timeline height in pixels.")
    parser.add_argument("--title", default=None, help="Project title (default: output filename stem).")
    parser.add_argument("--fcpxml-version", default="1.10", help="FCPXML version attribute.")
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
            raise ValueError("no FCPXML events were generated")
        xml_text = render_fcpxml(
            events,
            title=title,
            fps=args.fps,
            width=args.width,
            height=args.height,
            fcpxml_version=args.fcpxml_version,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    write_text(args.output, xml_text)
    manifest_path = args.manifest or f"{args.output}.json"
    if not args.no_manifest:
        write_json(
            manifest_path,
            build_manifest(
                events,
                title=title,
                fps=args.fps,
                output=args.output,
                width=args.width,
                height=args.height,
            ),
        )

    print(f"FCPXML written: {args.output}", file=sys.stderr)
    if not args.no_manifest:
        print(f"Manifest written: {manifest_path}", file=sys.stderr)
    print(f"Events: {len(events)}, duration: {events[-1].record_end:.2f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
