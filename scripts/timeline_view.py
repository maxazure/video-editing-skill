#!/usr/bin/env python3
"""
Generate a visual timeline drill-down image for a short video window.

The output is a PNG contact sheet: filmstrip frames on top, audio waveform
below when an audio stream exists. Use it around jump-cut boundaries or after
render QA finds a suspicious section.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class TimelineWindow:
    start: float
    end: float
    duration: float
    label: str = ""


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_media(path: str) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    return json.loads(result.stdout or "{}")


def _stream(meta: Dict[str, Any], codec_type: str) -> Optional[Dict[str, Any]]:
    for item in meta.get("streams", []):
        if item.get("codec_type") == codec_type:
            return item
    return None


def has_stream(meta: Dict[str, Any], codec_type: str) -> bool:
    return _stream(meta, codec_type) is not None


def media_duration(meta: Dict[str, Any]) -> Optional[float]:
    values: List[Any] = [meta.get("format", {}).get("duration")]
    values += [s.get("duration") for s in meta.get("streams", [])]
    parsed = []
    for value in values:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds > 0:
            parsed.append(seconds)
    return max(parsed) if parsed else None


def clamp_window(center: float, radius: float, duration: Optional[float] = None) -> TimelineWindow:
    if radius <= 0:
        raise ValueError("radius must be greater than 0")
    start = max(0.0, center - radius)
    end = center + radius
    if duration is not None:
        end = min(duration, end)
        if end - start < radius * 2 and start > 0:
            start = max(0.0, end - radius * 2)
    if end <= start:
        raise ValueError("timeline window is empty")
    return TimelineWindow(round(start, 4), round(end, 4), round(end - start, 4), f"at_{center:.2f}s")


def explicit_window(start: float, end: float, duration: Optional[float] = None) -> TimelineWindow:
    if start < 0:
        start = 0.0
    if duration is not None:
        end = min(duration, end)
    if end <= start:
        raise ValueError("--end must be greater than --start")
    return TimelineWindow(round(start, 4), round(end, 4), round(end - start, 4), f"{start:.2f}-{end:.2f}s")


def grid_for_frames(frame_count: int, max_columns: int = 6) -> Tuple[int, int]:
    if frame_count <= 0:
        raise ValueError("frame_count must be greater than 0")
    columns = min(max_columns, frame_count)
    rows = int(math.ceil(frame_count / columns))
    return columns, rows


def build_filter(
    window: TimelineWindow,
    *,
    frame_count: int,
    width: int,
    waveform_height: int,
    has_audio: bool,
) -> str:
    columns, rows = grid_for_frames(frame_count)
    padding = 6
    margin = 6
    thumb_width = max(96, int((width - (columns - 1) * padding - margin * 2) / columns))
    fps = frame_count / max(window.duration, 0.001)
    film = (
        f"[0:v]fps={fps:.6f},scale={thumb_width}:-2,"
        f"tile={columns}x{rows}:padding={padding}:margin={margin}:color=0x111827,"
        f"scale={width}:-2,format=rgb24[film]"
    )
    if not has_audio:
        return film + ";[film]null[out]"
    wave = f"[0:a]showwavespic=s={width}x{waveform_height}:colors=0x38bdf8,format=rgb24[wave]"
    return ";".join([film, wave, "[film][wave]vstack=inputs=2[out]"])


def build_ffmpeg_command(
    input_path: str,
    output_path: str,
    window: TimelineWindow,
    *,
    frame_count: int = 12,
    width: int = 1600,
    waveform_height: int = 180,
    has_audio: bool = True,
) -> List[str]:
    if frame_count <= 0:
        raise ValueError("frame_count must be greater than 0")
    if width < 320:
        raise ValueError("width must be at least 320")
    filter_complex = build_filter(
        window,
        frame_count=frame_count,
        width=width,
        waveform_height=waveform_height,
        has_audio=has_audio,
    )
    return [
        "ffmpeg", "-y", "-hide_banner",
        "-ss", f"{window.start:.4f}",
        "-t", f"{window.duration:.4f}",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-frames:v", "1",
        output_path,
    ]


def load_cut_windows(
    cut_list_path: str,
    *,
    key: str,
    radius: float,
    duration: Optional[float],
    limit: int,
) -> List[TimelineWindow]:
    with open(cut_list_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    segments = plan.get(key)
    if not isinstance(segments, list):
        raise ValueError(f"cut list does not contain a {key!r} segment list")
    windows: List[TimelineWindow] = []
    for idx, segment in enumerate(segments[:limit], start=1):
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad segment in {key}: {segment!r}") from exc
        center = (start + end) / 2
        window = clamp_window(center, radius, duration)
        windows.append(TimelineWindow(window.start, window.end, window.duration, f"{key}_{idx:03d}_{start:.2f}-{end:.2f}s"))
    return windows


def render_window(cmd: List[str]) -> None:
    result = _run(cmd)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError("ffmpeg timeline render failed")


def write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _safe_label(label: str) -> str:
    keep = []
    for ch in label:
        keep.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(keep).strip("_") or "window"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render filmstrip + waveform timeline views for video QA.")
    p.add_argument("input", help="Input video path")
    p.add_argument("--output", help="Output PNG for a single window")
    p.add_argument("--at", type=float, help="Center time in seconds for a single window")
    p.add_argument("--start", type=float, help="Window start in seconds")
    p.add_argument("--end", type=float, help="Window end in seconds")
    p.add_argument("--radius", type=float, default=1.5, help="Seconds before/after --at or cut-list segment center")
    p.add_argument("--frames", type=int, default=12, help="Number of filmstrip frames")
    p.add_argument("--width", type=int, default=1600, help="Output image width before waveform stacking")
    p.add_argument("--waveform-height", type=int, default=180, help="Waveform height when audio exists")
    p.add_argument("--cut-list", help="Jump-cut JSON from scripts/jump_cut.py")
    p.add_argument("--cut-source", choices=["removed_segments", "keep_segments"], default="removed_segments")
    p.add_argument("--output-dir", help="Output directory when using --cut-list")
    p.add_argument("--limit", type=int, default=20, help="Maximum cut-list windows to render")
    p.add_argument("--json", dest="json_path", help="Write metadata about rendered windows")
    p.add_argument("--dry-run", action="store_true", help="Print commands/metadata without rendering")
    return p


def _single_window(args: argparse.Namespace, duration: Optional[float]) -> TimelineWindow:
    if args.start is not None or args.end is not None:
        if args.start is None or args.end is None:
            raise ValueError("--start and --end must be provided together")
        return explicit_window(args.start, args.end, duration)
    if args.at is None:
        raise ValueError("provide --at, --start/--end, or --cut-list")
    return clamp_window(args.at, args.radius, duration)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.input):
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 1
    try:
        meta = probe_media(args.input)
        if not has_stream(meta, "video"):
            raise ValueError("input has no video stream")
        duration = media_duration(meta)
        has_audio = has_stream(meta, "audio")

        if args.cut_list:
            if not args.output_dir:
                raise ValueError("--output-dir is required with --cut-list")
            windows = load_cut_windows(
                args.cut_list,
                key=args.cut_source,
                radius=args.radius,
                duration=duration,
                limit=args.limit,
            )
            os.makedirs(args.output_dir, exist_ok=True)
            outputs = [
                os.path.join(args.output_dir, f"{idx:03d}_{_safe_label(window.label)}.png")
                for idx, window in enumerate(windows, start=1)
            ]
        else:
            if not args.output:
                raise ValueError("--output is required for a single window")
            windows = [_single_window(args, duration)]
            outputs = [args.output]

        rendered = []
        for window, output_path in zip(windows, outputs):
            os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
            cmd = build_ffmpeg_command(
                args.input,
                output_path,
                window,
                frame_count=args.frames,
                width=args.width,
                waveform_height=args.waveform_height,
                has_audio=has_audio,
            )
            rendered.append({"output": os.path.abspath(output_path), "window": asdict(window), "command": cmd})
            if not args.dry_run:
                render_window(cmd)
                print(f"Timeline view written: {output_path}")

        payload = {
            "input": os.path.abspath(args.input),
            "has_audio": has_audio,
            "duration": duration,
            "views": rendered,
        }
        if args.json_path:
            write_json(args.json_path, payload)
        if args.dry_run or not args.json_path:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
