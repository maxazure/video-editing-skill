#!/usr/bin/env python3
"""Build screen-focus cues for software tutorial recordings.

The script consumes manually exported click/hotspot events and emits an
auditable enrich plan that render_final.py can consume via --enrich-plan.
It does not capture the screen or control the desktop.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "screen_focus_plan.v1"


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


def _bool_or_default(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _first_value(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def load_events(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load events from JSON or CSV.

    JSON may be either a list, or an object with events/clicks/focus_events and
    optional screen metadata. CSV should include time/start, x, y, and optional
    label/duration/zoom columns.
    """
    event_path = Path(path)
    suffix = event_path.suffix.lower()
    if suffix == ".csv":
        with event_path.open("r", encoding="utf-8-sig", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)], {}

    with event_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return [dict(item) for item in payload], {}
    if isinstance(payload, dict):
        raw_events = (
            payload.get("events")
            or payload.get("clicks")
            or payload.get("focus_events")
            or []
        )
        if not isinstance(raw_events, list):
            raise ValueError("events/clicks/focus_events must be a list")
        metadata = {
            "screen": payload.get("screen") or payload.get("source_screen") or {},
            "source": payload.get("source") or {},
        }
        return [dict(item) for item in raw_events], metadata
    raise ValueError("events file must be a JSON list/object or CSV table")


def parse_manual_event(value: str) -> Dict[str, Any]:
    """Parse --event time,x,y[,label]."""
    parts = [part.strip() for part in value.split(",", 3)]
    if len(parts) < 3:
        raise ValueError("--event must be time,x,y[,label]")
    event: Dict[str, Any] = {"time": parts[0], "x": parts[1], "y": parts[2]}
    if len(parts) == 4:
        event["label"] = parts[3]
    return event


def _infer_screen_size(
    metadata: Dict[str, Any],
    screen_width: Optional[int],
    screen_height: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    screen = metadata.get("screen") or {}
    source = metadata.get("source") or {}
    width = screen_width or screen.get("width") or source.get("width")
    height = screen_height or screen.get("height") or source.get("height")
    return (int(width) if width else None, int(height) if height else None)


def _normalise_xy(
    event: Dict[str, Any],
    screen_width: Optional[int],
    screen_height: Optional[int],
) -> Tuple[float, float, Optional[float], Optional[float], str]:
    raw_x = _first_value(event, ("x", "screen_x", "client_x", "mouse_x", "norm_x"))
    raw_y = _first_value(event, ("y", "screen_y", "client_y", "mouse_y", "norm_y"))
    if raw_x is None or raw_y is None:
        raise ValueError(f"event missing x/y: {event}")

    x = float(raw_x)
    y = float(raw_y)
    mode = str(event.get("coordinate_mode") or event.get("coordinates") or "").lower()
    source_w = event.get("source_width") or screen_width
    source_h = event.get("source_height") or screen_height

    if mode in {"normalised", "normalized", "ratio", "relative"}:
        return _clamp(x, 0.0, 1.0), _clamp(y, 0.0, 1.0), None, None, "normalized"

    if mode in {"pixel", "pixels", "absolute"} or x > 1.0 or y > 1.0:
        if not source_w or not source_h:
            raise ValueError("pixel coordinates require --screen-width and --screen-height")
        return (
            _clamp(x / float(source_w), 0.0, 1.0),
            _clamp(y / float(source_h), 0.0, 1.0),
            x,
            y,
            "pixels",
        )

    return _clamp(x, 0.0, 1.0), _clamp(y, 0.0, 1.0), None, None, "normalized"


def normalise_event(
    event: Dict[str, Any],
    *,
    index: int,
    screen_width: Optional[int],
    screen_height: Optional[int],
    default_duration: float,
    default_zoom: float,
    default_transition: float,
) -> Dict[str, Any]:
    start = _parse_time(_first_value(event, ("start", "time", "timestamp", "t")), 0.0)
    if any(key in event for key in ("end", "stop")):
        end = _parse_time(_first_value(event, ("end", "stop")), start + default_duration)
    else:
        duration = _float_or_default(event.get("duration"), default_duration)
        end = start + duration
    if end <= start:
        end = start + default_duration

    x, y, source_x, source_y, coordinate_mode = _normalise_xy(
        event, screen_width, screen_height,
    )
    zoom = _clamp(_float_or_default(event.get("zoom"), default_zoom), 1.05, 4.0)
    label = str(event.get("label") or event.get("text") or event.get("name") or "").strip()
    transition = _clamp(
        _float_or_default(event.get("transition"), default_transition), 0.0, 0.8,
    )

    normalised = {
        "id": str(event.get("id") or f"focus_{index:03d}"),
        "start": round(start, 4),
        "end": round(end, 4),
        "duration": round(end - start, 4),
        "x": round(x, 5),
        "y": round(y, 5),
        "zoom": round(zoom, 3),
        "transition": round(transition, 3),
        "label": label,
        "show_label": _bool_or_default(event.get("show_label"), bool(label)),
        "marker": _bool_or_default(event.get("marker"), True),
        "marker_color": str(event.get("marker_color") or "red@0.85"),
        "marker_size": _clamp(_float_or_default(event.get("marker_size"), 0.13), 0.04, 0.35),
        "reason": str(event.get("reason") or "screen-click-focus"),
        "source": str(event.get("source") or "screen_focus.py"),
    }
    if source_x is not None and source_y is not None:
        normalised["source_x"] = round(source_x, 2)
        normalised["source_y"] = round(source_y, 2)
        normalised["coordinate_mode"] = coordinate_mode
    return normalised


def build_focus_plan(
    events: Sequence[Dict[str, Any]],
    *,
    screen_width: Optional[int] = None,
    screen_height: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    default_duration: float = 1.2,
    default_zoom: float = 1.75,
    default_transition: float = 0.16,
) -> Dict[str, Any]:
    metadata = metadata or {}
    inferred_w, inferred_h = _infer_screen_size(metadata, screen_width, screen_height)
    focus_events = [
        normalise_event(
            event,
            index=i + 1,
            screen_width=inferred_w,
            screen_height=inferred_h,
            default_duration=default_duration,
            default_zoom=default_zoom,
            default_transition=default_transition,
        )
        for i, event in enumerate(events)
    ]
    focus_events.sort(key=lambda item: (item["start"], item["end"]))
    labelled = sum(1 for item in focus_events if item.get("label"))
    return {
        "version": VERSION,
        "screen": {
            "width": inferred_w,
            "height": inferred_h,
            "coordinate_space": "normalized_0_1",
        },
        "defaults": {
            "duration": default_duration,
            "zoom": default_zoom,
            "transition": default_transition,
        },
        "summary": {
            "focus_events": len(focus_events),
            "labelled_events": labelled,
            "first_start": focus_events[0]["start"] if focus_events else None,
            "last_end": focus_events[-1]["end"] if focus_events else None,
        },
        "focus_events": focus_events,
        "render_hint": (
            "Pass this JSON to render_final.py with --enrich-plan; focus_events "
            "will become timed zoom overlays and optional badges."
        ),
    }


def emit_markdown(plan: Dict[str, Any]) -> str:
    lines = [
        "# Screen Focus Plan",
        "",
        f"- version: `{plan.get('version')}`",
        f"- events: `{plan.get('summary', {}).get('focus_events', 0)}`",
        f"- render: `{plan.get('render_hint')}`",
        "",
        "| event | time | xy | zoom | label |",
        "|---|---:|---:|---:|---|",
    ]
    for item in plan.get("focus_events") or []:
        label = str(item.get("label") or "").replace("|", "\\|")
        lines.append(
            "| {id} | {start:.2f}-{end:.2f}s | {x:.3f},{y:.3f} | {zoom:.2f} | {label} |".format(
                id=item.get("id"),
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", 0.0)),
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                zoom=float(item.get("zoom", 1.0)),
                label=label,
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
        description="Create render_final.py focus_events from screen click/hotspot events.",
    )
    parser.add_argument("--events", help="JSON/CSV click event file.")
    parser.add_argument(
        "--event",
        action="append",
        default=[],
        help="Inline event as time,x,y[,label]. Repeatable.",
    )
    parser.add_argument("--screen-width", type=int, help="Source recording width for pixel coordinates.")
    parser.add_argument("--screen-height", type=int, help="Source recording height for pixel coordinates.")
    parser.add_argument("--default-duration", type=float, default=1.2)
    parser.add_argument("--default-zoom", type=float, default=1.75)
    parser.add_argument("--default-transition", type=float, default=0.16)
    parser.add_argument("--output", required=True, help="Output JSON enrich plan.")
    parser.add_argument("--markdown", help="Optional Markdown review table.")
    args = parser.parse_args(argv)

    events: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    if args.events:
        loaded, metadata = load_events(args.events)
        events.extend(loaded)
    for value in args.event:
        events.append(parse_manual_event(value))
    if not events:
        print("Error: provide --events or at least one --event", file=sys.stderr)
        return 1

    try:
        plan = build_focus_plan(
            events,
            screen_width=args.screen_width,
            screen_height=args.screen_height,
            metadata=metadata,
            default_duration=args.default_duration,
            default_zoom=args.default_zoom,
            default_transition=args.default_transition,
        )
    except (TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))
    print(f"screen focus plan -> {args.output} ({plan['summary']['focus_events']} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
