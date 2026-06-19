#!/usr/bin/env python3
"""Build picture-in-picture camera overlay cues for screen/demo videos.

The script emits an auditable enrich plan that render_final.py can consume via
--enrich-plan. It does not record the screen, transcode the camera feed, or mix
camera audio into the final render.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


VERSION = "pip_overlay_plan.v1"
POSITIONS = {"bottom_right", "bottom_left", "top_right", "top_left", "center"}


def _parse_time(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if ":" not in text:
        return float(text)
    parts = [float(part) for part in text.split(":")]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_segment(value: str) -> Dict[str, Any]:
    """Parse --segment start,end[,position]."""
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) < 2:
        raise ValueError("--segment must be start,end[,position]")
    segment: Dict[str, Any] = {
        "start": _parse_time(parts[0]),
        "end": _parse_time(parts[1]),
    }
    if len(parts) == 3 and parts[2]:
        segment["position"] = parts[2].lower().replace("-", "_")
    return segment


def _normalise_position(value: Optional[str]) -> str:
    position = str(value or "bottom_right").strip().lower().replace("-", "_")
    if position not in POSITIONS:
        return "bottom_right"
    return position


def build_segments(
    raw_segments: Sequence[Dict[str, Any]],
    *,
    start: float,
    end: Optional[float],
    duration: Optional[float],
    position: str,
) -> List[Dict[str, Any]]:
    if raw_segments:
        segments = [dict(item) for item in raw_segments]
    else:
        if end is None:
            if duration is None:
                raise ValueError("provide --end, --duration, or at least one --segment")
            end = start + duration
        segments = [{"start": start, "end": end, "position": position}]

    normalised: List[Dict[str, Any]] = []
    for index, item in enumerate(segments, 1):
        seg_start = _parse_time(item.get("start"), 0.0)
        seg_end = _parse_time(item.get("end"), seg_start)
        if seg_end <= seg_start:
            raise ValueError(f"segment #{index} end must be greater than start")
        item["start"] = seg_start
        item["end"] = seg_end
        item["position"] = _normalise_position(item.get("position") or position)
        normalised.append(item)
    normalised.sort(key=lambda item: (item["start"], item["end"]))
    return normalised


def build_pip_plan(
    *,
    camera: str,
    segments: Sequence[Dict[str, Any]],
    width_ratio: float = 0.24,
    margin_ratio: float = 0.035,
    opacity: float = 1.0,
    transition: float = 0.12,
    sync_offset: float = 0.0,
    source_start: Optional[float] = None,
    reason: str = "facecam-pip",
) -> Dict[str, Any]:
    camera_path = os.path.abspath(os.path.expanduser(camera))
    first_start = segments[0]["start"] if segments else 0.0
    overlays = []
    for index, segment in enumerate(segments, 1):
        seg_start = float(segment["start"])
        if source_start is None:
            source = seg_start + sync_offset
        else:
            source = source_start + (seg_start - first_start)
        overlays.append({
            "id": str(segment.get("id") or f"pip_{index:03d}"),
            "video": camera_path,
            "start": round(seg_start, 4),
            "end": round(float(segment["end"]), 4),
            "source_start": round(max(0.0, source), 4),
            "sync_offset": round(sync_offset, 4),
            "position": _normalise_position(segment.get("position")),
            "width_ratio": round(_clamp(width_ratio, 0.12, 0.55), 4),
            "margin_ratio": round(_clamp(margin_ratio, 0.0, 0.16), 4),
            "opacity": round(_clamp(opacity, 0.2, 1.0), 4),
            "transition": round(_clamp(transition, 0.0, 0.8), 4),
            "audio": False,
            "reason": reason,
        })

    positions = sorted({item["position"] for item in overlays})
    return {
        "version": VERSION,
        "camera": {
            "path": camera_path,
            "audio": "ignored",
        },
        "defaults": {
            "width_ratio": round(_clamp(width_ratio, 0.12, 0.55), 4),
            "margin_ratio": round(_clamp(margin_ratio, 0.0, 0.16), 4),
            "opacity": round(_clamp(opacity, 0.2, 1.0), 4),
            "transition": round(_clamp(transition, 0.0, 0.8), 4),
            "sync_offset": round(sync_offset, 4),
        },
        "summary": {
            "pip_overlays": len(overlays),
            "positions": positions,
            "first_start": overlays[0]["start"] if overlays else None,
            "last_end": overlays[-1]["end"] if overlays else None,
            "camera_exists": Path(camera_path).is_file(),
        },
        "pip_overlays": overlays,
        "render_hint": (
            "Pass this JSON to render_final.py with --enrich-plan; pip_overlays "
            "will become timed camera picture-in-picture overlays. Camera audio "
            "is ignored by design."
        ),
    }


def emit_markdown(plan: Dict[str, Any]) -> str:
    lines = [
        "# PIP Overlay Plan",
        "",
        f"- version: `{plan.get('version')}`",
        f"- camera: `{plan.get('camera', {}).get('path', '')}`",
        f"- overlays: `{plan.get('summary', {}).get('pip_overlays', 0)}`",
        f"- render: `{plan.get('render_hint')}`",
        "",
        "| overlay | time | source | position | size | opacity |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for item in plan.get("pip_overlays") or []:
        lines.append(
            "| {id} | {start:.2f}-{end:.2f}s | {source_start:.2f}s | {position} | {size:.2f} | {opacity:.2f} |".format(
                id=item.get("id"),
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", 0.0)),
                source_start=float(item.get("source_start", 0.0)),
                position=item.get("position"),
                size=float(item.get("width_ratio", 0.0)),
                opacity=float(item.get("opacity", 1.0)),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create render_final.py pip_overlays from a camera/facecam recording.",
    )
    parser.add_argument("--camera", required=True, help="Camera/facecam video file.")
    parser.add_argument("--start", type=float, default=0.0, help="Timeline start in seconds.")
    parser.add_argument("--end", type=float, help="Timeline end in seconds.")
    parser.add_argument("--duration", type=float, help="Overlay duration when --end is omitted.")
    parser.add_argument(
        "--segment",
        action="append",
        default=[],
        help="Timeline segment as start,end[,position]. Repeat for layout changes.",
    )
    parser.add_argument(
        "--position",
        default="bottom_right",
        choices=sorted(POSITIONS),
        help="Default PIP position.",
    )
    parser.add_argument("--width-ratio", type=float, default=0.24)
    parser.add_argument("--margin-ratio", type=float, default=0.035)
    parser.add_argument("--opacity", type=float, default=1.0)
    parser.add_argument("--transition", type=float, default=0.12)
    parser.add_argument(
        "--sync-offset",
        type=float,
        default=0.0,
        help="Seconds added to timeline time when reading the camera source.",
    )
    parser.add_argument(
        "--source-start",
        type=float,
        help="Camera source time corresponding to the first overlay segment.",
    )
    parser.add_argument("--reason", default="facecam-pip")
    parser.add_argument("--output", required=True, help="Output JSON enrich plan.")
    parser.add_argument("--markdown", help="Optional Markdown review table.")
    args = parser.parse_args(argv)

    try:
        raw_segments = [parse_segment(item) for item in args.segment]
        segments = build_segments(
            raw_segments,
            start=args.start,
            end=args.end,
            duration=args.duration,
            position=args.position,
        )
        plan = build_pip_plan(
            camera=args.camera,
            segments=segments,
            width_ratio=args.width_ratio,
            margin_ratio=args.margin_ratio,
            opacity=args.opacity,
            transition=args.transition,
            sync_offset=args.sync_offset,
            source_start=args.source_start,
            reason=args.reason,
        )
    except (TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    exists = "exists" if plan["summary"]["camera_exists"] else "missing"
    print(f"pip overlay plan -> {args.output} ({plan['summary']['pip_overlays']} overlays, camera {exists})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
