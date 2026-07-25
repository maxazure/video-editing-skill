#!/usr/bin/env python3
"""Render a source-time-aligned original-versus-final review video."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "edit_compare.v1"
MODE = "original-vs-final-source-time"


@dataclass(frozen=True)
class MediaInfo:
    path: str
    width: int
    height: int
    duration: float
    fps_num: int
    fps_den: int
    has_audio: bool
    rotation: int = 0

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


@dataclass(frozen=True)
class ComparePart:
    index: int
    kind: str
    source_start: float
    source_end: float
    start_frame: int
    end_frame: int
    frame_count: int
    program_start: Optional[float] = None
    program_end: Optional[float] = None

    @property
    def source_duration(self) -> float:
        return self.source_end - self.source_start


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("cut list must be a JSON object")
    return data


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _parse_rate(value: Any) -> Fraction:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        rate = Fraction(0, 1)
    if rate <= 0:
        raise ValueError(f"invalid video frame rate: {value!r}")
    return rate


def _rotation_degrees(stream: Mapping[str, Any]) -> int:
    values: List[Any] = []
    tags = stream.get("tags")
    if isinstance(tags, Mapping):
        values.append(tags.get("rotate"))
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, Mapping):
            values.append(side_data.get("rotation"))
    for value in values:
        try:
            return int(round(float(value))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def probe_media(path: str) -> MediaInfo:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration:"
                "stream_tags=rotate:stream_side_data=rotation:format=duration"
            ),
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video, Mapping):
        raise ValueError(f"no video stream found: {path}")

    rate_value = video.get("avg_frame_rate")
    if not rate_value or rate_value == "0/0":
        rate_value = video.get("r_frame_rate")
    rate = _parse_rate(rate_value)

    durations: List[float] = []
    for value in [video.get("duration"), data.get("format", {}).get("duration")]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            durations.append(parsed)
    if not durations:
        raise ValueError(f"could not determine video duration: {path}")

    rotation = _rotation_degrees(video)
    width = int(video["width"])
    height = int(video["height"])
    if rotation in {90, 270}:
        width, height = height, width
    return MediaInfo(
        path=os.path.abspath(path),
        width=width,
        height=height,
        duration=durations[0],
        fps_num=rate.numerator,
        fps_den=rate.denominator,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        rotation=rotation,
    )


def load_keep_segments(path: str) -> List[Dict[str, float]]:
    data = _read_json(path)
    if data.get("status") == "blocked":
        raise ValueError("cut list status is blocked; approve or repair it before comparison")
    summary = data.get("summary")
    if isinstance(summary, Mapping) and int(summary.get("blocking") or 0) > 0:
        raise ValueError("cut list has unresolved summary.blocking items")
    raw_segments = data.get("keep_segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("cut list must contain a non-empty keep_segments list")

    segments: List[Dict[str, float]] = []
    cursor = 0.0
    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"keep segment #{index} must be an object")
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad keep segment #{index}: {raw!r}") from exc
        if start < 0 or end <= start:
            raise ValueError(f"bad keep segment #{index}: start={start} end={end}")
        if start < cursor - 1e-6:
            raise ValueError("keep_segments must be chronological and non-overlapping")
        segments.append({"start": start, "end": end})
        cursor = end
    return segments


def build_parts(
    keep_segments: Sequence[Mapping[str, float]],
    *,
    source_duration: float,
    fps_num: int,
    fps_den: int,
    output_speed: float = 1.0,
    output_offset: float = 0.0,
) -> List[ComparePart]:
    if source_duration <= 0:
        raise ValueError("source duration must be greater than 0")
    if fps_num <= 0 or fps_den <= 0:
        raise ValueError("fps must be greater than 0")
    if output_speed <= 0:
        raise ValueError("output speed must be greater than 0")
    if output_offset < 0:
        raise ValueError("output offset must be non-negative")
    if not keep_segments:
        raise ValueError("at least one keep segment is required")

    fps = fps_num / fps_den
    raw_parts: List[Dict[str, Optional[float]]] = []
    source_cursor = 0.0
    program_cursor = output_offset
    for index, segment in enumerate(keep_segments, start=1):
        start = float(segment["start"])
        end = float(segment["end"])
        if start < source_cursor - 1e-6:
            raise ValueError("keep_segments must be chronological and non-overlapping")
        if end <= start:
            raise ValueError(f"keep segment #{index} has no duration")
        if end > source_duration + 1e-6:
            raise ValueError(
                f"keep segment #{index} ends at {end:.4f}s beyond source duration "
                f"{source_duration:.4f}s"
            )
        if start > source_cursor + 1e-6:
            raw_parts.append({
                "kind": "dropped",
                "source_start": source_cursor,
                "source_end": start,
                "program_start": None,
                "program_end": None,
            })
        program_duration = (end - start) / output_speed
        raw_parts.append({
            "kind": "kept",
            "source_start": start,
            "source_end": end,
            "program_start": program_cursor,
            "program_end": program_cursor + program_duration,
        })
        source_cursor = end
        program_cursor += program_duration
    if source_cursor < source_duration - 1e-6:
        raw_parts.append({
            "kind": "dropped",
            "source_start": source_cursor,
            "source_end": source_duration,
            "program_start": None,
            "program_end": None,
        })

    parts: List[ComparePart] = []
    for index, raw in enumerate(raw_parts, start=1):
        start = float(raw["source_start"])
        end = float(raw["source_end"])
        start_frame = round(start * fps)
        end_frame = round(end * fps)
        frame_count = end_frame - start_frame
        if frame_count <= 0:
            raise ValueError(
                f"{raw['kind']} source range {start:.4f}-{end:.4f}s is shorter than one frame"
            )
        parts.append(ComparePart(
            index=index,
            kind=str(raw["kind"]),
            source_start=start,
            source_end=end,
            start_frame=start_frame,
            end_frame=end_frame,
            frame_count=frame_count,
            program_start=raw["program_start"],
            program_end=raw["program_end"],
        ))
    return parts


def required_final_end(parts: Sequence[ComparePart]) -> float:
    ends = [part.program_end for part in parts if part.program_end is not None]
    return max(ends, default=0.0)


def _fit_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


def build_filtergraph(
    parts: Sequence[ComparePart],
    *,
    width: int,
    height: int,
    fps_num: int,
    fps_den: int,
    output_speed: float,
) -> str:
    if not parts:
        raise ValueError("comparison needs at least one part")
    fps_text = f"{fps_num}/{fps_den}"
    frame_pts = f"N*{fps_den}/({fps_num}*TB)"
    total_frames = parts[-1].end_frame
    fit = _fit_filter(width, height)
    graph = [
        (
            f"[0:v:0]fps={fps_text},{fit},trim=end_frame={total_frames},"
            f"settb=AVTB,setpts={frame_pts},format=yuv420p[left]"
        )
    ]
    labels: List[str] = []
    for part in parts:
        label = f"right-{part.index}"
        if part.kind == "dropped":
            duration = (part.frame_count + 1) * fps_den / fps_num
            graph.append(
                f"color=c=black:s={width}x{height}:r={fps_text}:d={duration:.9f},"
                f"trim=end_frame={part.frame_count},settb=AVTB,setpts={frame_pts},"
                f"format=yuv420p[{label}]"
            )
        else:
            graph.append(
                f"[1:v:0]trim=start={part.program_start:.6f}:end={part.program_end:.6f},"
                f"setpts=(PTS-STARTPTS)*{output_speed:.8f},fps={fps_text},{fit},"
                f"trim=end_frame={part.frame_count},settb=AVTB,setpts={frame_pts},"
                f"format=yuv420p[{label}]"
            )
        labels.append(f"[{label}]")
    if len(labels) == 1:
        graph.append(f"{labels[0]}null[right]")
    else:
        graph.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[right]")
    graph.append(
        f"[left][right]hstack=inputs=2:shortest=1,"
        f"trim=end_frame={total_frames},format=yuv420p[compare]"
    )
    return ";".join(graph)


def build_ffmpeg_command(
    source: str,
    final: str,
    output: str,
    parts: Sequence[ComparePart],
    source_info: MediaInfo,
    *,
    output_speed: float,
    include_audio: bool = True,
) -> List[str]:
    width = max(2, source_info.width // 2 * 2)
    height = max(2, source_info.height // 2 * 2)
    graph = build_filtergraph(
        parts,
        width=width,
        height=height,
        fps_num=source_info.fps_num,
        fps_den=source_info.fps_den,
        output_speed=output_speed,
    )
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source,
        "-i",
        final,
        "-filter_complex",
        graph,
        "-map",
        "[compare]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-frames:v",
        str(parts[-1].end_frame),
    ]
    if include_audio and source_info.has_audio:
        command += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
    else:
        command += ["-an"]
    output_duration = parts[-1].end_frame * source_info.fps_den / source_info.fps_num
    command += ["-t", f"{output_duration:.9f}", "-movflags", "+faststart", output]
    return command


def _select_sample_parts(parts: Sequence[ComparePart], limit: int) -> List[ComparePart]:
    if limit <= 0 or len(parts) <= limit:
        return list(parts)
    if limit == 1:
        return [parts[len(parts) // 2]]
    indexes = {
        round(position * (len(parts) - 1) / (limit - 1))
        for position in range(limit)
    }
    for kind in ("kept", "dropped"):
        match = next((index for index, part in enumerate(parts) if part.kind == kind), None)
        if match is not None:
            indexes.add(match)
    return [parts[index] for index in sorted(indexes)]


def _sample_rgb(path: str, at_seconds: float, filtergraph: str) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-ss",
            f"{at_seconds:.6f}",
            "-vf",
            filtergraph,
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    if not result.stdout:
        raise RuntimeError(f"could not sample frame at {at_seconds:.4f}s from {path}")
    return result.stdout


def _mean_error(left: bytes, right: bytes) -> float:
    if not left or len(left) != len(right):
        return math.inf
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)


def verify_output(
    source: str,
    final: str,
    output: str,
    parts: Sequence[ComparePart],
    source_info: MediaInfo,
    *,
    include_audio: bool,
    sample_limit: int,
) -> Dict[str, Any]:
    output_info = probe_media(output)
    width = max(2, source_info.width // 2 * 2)
    height = max(2, source_info.height // 2 * 2)
    tolerance = source_info.fps_den / source_info.fps_num + 0.02
    errors: List[str] = []
    if output_info.width != width * 2 or output_info.height != height:
        errors.append(
            f"output geometry {output_info.width}x{output_info.height} "
            f"does not match expected {width * 2}x{height}"
        )
    if abs(output_info.duration - source_info.duration) > tolerance:
        errors.append(
            f"output duration {output_info.duration:.4f}s does not match "
            f"source {source_info.duration:.4f}s"
        )
    expected_audio = include_audio and source_info.has_audio
    if output_info.has_audio != expected_audio:
        errors.append(
            f"output audio presence {output_info.has_audio} does not match expected {expected_audio}"
        )

    sample_size = max(2, min(48, width // 4, height // 4))
    crop_x = max(0, (width - sample_size) // 2)
    crop_y = max(0, (height - sample_size) // 2)
    small = min(16, sample_size)
    samples: List[Dict[str, Any]] = []
    for part in _select_sample_parts(parts, sample_limit):
        source_time = (part.source_start + part.source_end) / 2
        projected = _sample_rgb(
            output,
            source_time,
            (
                f"crop={sample_size}:{sample_size}:{width + crop_x}:{crop_y},"
                f"scale={small}:{small}:flags=area"
            ),
        )
        sample: Dict[str, Any] = {
            "part": part.index,
            "kind": part.kind,
            "source_time": round(source_time, 6),
            "status": "pass",
        }
        if part.kind == "dropped":
            peak = max(projected)
            sample["peak_rgb"] = peak
            if peak > 24:
                sample["status"] = "fail"
                errors.append(
                    f"dropped range sample at {source_time:.4f}s is not black (peak={peak})"
                )
        else:
            program_time = (part.program_start + part.program_end) / 2
            expected = _sample_rgb(
                final,
                program_time,
                (
                    f"{_fit_filter(width, height)},"
                    f"crop={sample_size}:{sample_size}:{crop_x}:{crop_y},"
                    f"scale={small}:{small}:flags=area"
                ),
            )
            error = _mean_error(projected, expected)
            sample["program_time"] = round(program_time, 6)
            sample["mean_rgb_error"] = round(error, 4)
            if error > 24:
                sample["status"] = "fail"
                errors.append(
                    f"kept range sample mismatch at source {source_time:.4f}s / "
                    f"program {program_time:.4f}s (mean error={error:.2f})"
                )
        samples.append(sample)

    return {
        "status": "pass" if not errors else "fail",
        "duration_tolerance_seconds": round(tolerance, 6),
        "expected_geometry": {"width": width * 2, "height": height},
        "output_media": asdict(output_info),
        "sample_limit": sample_limit,
        "samples": samples,
        "errors": errors,
    }


def _part_payload(part: ComparePart) -> Dict[str, Any]:
    payload = asdict(part)
    payload["source_duration"] = round(part.source_duration, 6)
    return payload


def build_report(
    *,
    status: str,
    source_info: MediaInfo,
    final_info: MediaInfo,
    cut_list: str,
    output: str,
    parts: Sequence[ComparePart],
    output_speed: float,
    output_offset: float,
    include_audio: bool,
    verification: Optional[Mapping[str, Any]] = None,
    blockers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    blocker_list = list(blockers or [])
    dropped = [part for part in parts if part.kind == "dropped"]
    kept = [part for part in parts if part.kind == "kept"]
    return {
        "version": VERSION,
        "mode": MODE,
        "status": status,
        "inputs": {
            "source": asdict(source_info),
            "final": asdict(final_info),
            "cut_list": os.path.abspath(cut_list),
        },
        "settings": {
            "output_speed": output_speed,
            "output_offset": output_offset,
            "audio": "source" if include_audio and source_info.has_audio else "none",
        },
        "output": os.path.abspath(output),
        "parts": [_part_payload(part) for part in parts],
        "verification": dict(verification or {}),
        "blockers": blocker_list,
        "summary": {
            "status": status,
            "blocking": len(blocker_list),
            "warnings": 0,
            "kept_ranges": len(kept),
            "dropped_ranges": len(dropped),
            "source_duration": round(source_info.duration, 6),
            "required_final_end": round(required_final_end(parts), 6),
            "verification_samples": len((verification or {}).get("samples", [])),
        },
        "next_actions": (
            ["Open the comparison video: original is left; final pixels are right; black means dropped."]
            if not blocker_list and status == "pass"
            else ["Resolve the blockers, rerender the final, and rerun edit_compare.py."]
        ),
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Source-time edit comparison",
        "",
        f"- Status: **{str(report.get('status', 'unknown')).upper()}**",
        f"- Mode: `{report.get('mode', MODE)}`",
        f"- Source duration: {float(summary.get('source_duration', 0)):.3f}s",
        f"- Kept ranges: {summary.get('kept_ranges', 0)}",
        f"- Dropped ranges: {summary.get('dropped_ranges', 0)}",
        f"- Verification samples: {summary.get('verification_samples', 0)}",
        f"- Video: `{report.get('output', '')}`",
        "",
        "Left is the original source on its continuous clock. Right is final-delivery pixels",
        "projected back to the same source time; black on the right means that range was cut.",
        "",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines += ["## Blockers", ""]
        lines += [f"- {item}" for item in blockers]
        lines.append("")

    lines += [
        "## Timeline mapping",
        "",
        "| # | kind | source range | final program range | frames |",
        "|---:|---|---:|---:|---:|",
    ]
    for part in report.get("parts") or []:
        program = "-"
        if part.get("program_start") is not None:
            program = f"{part['program_start']:.3f}-{part['program_end']:.3f}s"
        lines.append(
            f"| {part['index']} | {part['kind']} | "
            f"{part['source_start']:.3f}-{part['source_end']:.3f}s | "
            f"{program} | {part['frame_count']} |"
        )

    samples = (report.get("verification") or {}).get("samples") or []
    if samples:
        lines += [
            "",
            "## Verification samples",
            "",
            "| part | kind | source time | program time | result | metric |",
            "|---:|---|---:|---:|---|---:|",
        ]
        for sample in samples:
            metric = (
                f"peak={sample.get('peak_rgb')}"
                if sample.get("kind") == "dropped"
                else f"mean_rgb_error={sample.get('mean_rgb_error')}"
            )
            program = (
                f"{sample['program_time']:.3f}s"
                if sample.get("program_time") is not None
                else "-"
            )
            lines.append(
                f"| {sample['part']} | {sample['kind']} | "
                f"{sample['source_time']:.3f}s | {program} | "
                f"{sample['status']} | {metric} |"
            )
    lines.append("")
    return "\n".join(lines)


def _default_sidecar(output: str, suffix: str) -> str:
    path = Path(output)
    return str(path.with_name(f"{path.stem}_edit_compare{suffix}"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render source-time-aligned original vs final video comparison"
    )
    parser.add_argument("source", help="Original source video")
    parser.add_argument("final", help="Actual final rendered video")
    parser.add_argument("--cut-list", required=True, help="rough_cut/jump_cut JSON with keep_segments")
    parser.add_argument("--output", required=True, help="Output side-by-side comparison MP4")
    parser.add_argument("--report", help="Output JSON report; defaults beside --output")
    parser.add_argument("--markdown", help="Output Markdown report; defaults beside --output")
    parser.add_argument(
        "--output-speed",
        type=float,
        default=1.0,
        help="Global speed applied to kept source ranges in the final (default: 1.0)",
    )
    parser.add_argument(
        "--output-offset",
        type=float,
        default=0.0,
        help="Seconds before the source-derived program in the final (default: 0)",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=12,
        help="Maximum representative kept/dropped ranges to pixel-check (default: 12; 0 = all)",
    )
    parser.add_argument("--no-audio", action="store_true", help="Do not copy source-clock audio")
    parser.add_argument("--dry-run", action="store_true", help="Write a blocked plan without rendering")
    parser.add_argument("--strict", action="store_true", help="Return 2 when a dry-run report is blocked")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = args.report or _default_sidecar(args.output, ".json")
    markdown_path = args.markdown or _default_sidecar(args.output, ".md")
    report: Optional[Dict[str, Any]] = None
    try:
        if args.sample_limit < 0:
            raise ValueError("--sample-limit must be non-negative")
        source_info = probe_media(args.source)
        final_info = probe_media(args.final)
        keep_segments = load_keep_segments(args.cut_list)
        parts = build_parts(
            keep_segments,
            source_duration=source_info.duration,
            fps_num=source_info.fps_num,
            fps_den=source_info.fps_den,
            output_speed=args.output_speed,
            output_offset=args.output_offset,
        )
        final_tolerance = source_info.fps_den / source_info.fps_num + 0.02
        final_end = required_final_end(parts)
        if final_info.duration + final_tolerance < final_end:
            raise ValueError(
                f"final video ends at {final_info.duration:.4f}s but mapping needs "
                f"{final_end:.4f}s; check --output-speed/--output-offset"
            )

        include_audio = not args.no_audio
        if args.dry_run:
            report = build_report(
                status="planned",
                source_info=source_info,
                final_info=final_info,
                cut_list=args.cut_list,
                output=args.output,
                parts=parts,
                output_speed=args.output_speed,
                output_offset=args.output_offset,
                include_audio=include_audio,
                blockers=["comparison video has not been rendered (--dry-run)"],
            )
            _write_json(report_path, report)
            _write_text(markdown_path, emit_markdown(report))
            print(f"[edit_compare] PLANNED -> {report_path}")
            return 2 if args.strict else 0

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        command = build_ffmpeg_command(
            args.source,
            args.final,
            args.output,
            parts,
            source_info,
            output_speed=args.output_speed,
            include_audio=include_audio,
        )
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg comparison render failed")

        verification = verify_output(
            args.source,
            args.final,
            args.output,
            parts,
            source_info,
            include_audio=include_audio,
            sample_limit=args.sample_limit,
        )
        blockers = verification.get("errors") or []
        report = build_report(
            status="pass" if not blockers else "blocked",
            source_info=source_info,
            final_info=final_info,
            cut_list=args.cut_list,
            output=args.output,
            parts=parts,
            output_speed=args.output_speed,
            output_offset=args.output_offset,
            include_audio=include_audio,
            verification=verification,
            blockers=blockers,
        )
        _write_json(report_path, report)
        _write_text(markdown_path, emit_markdown(report))
        if blockers:
            print("[edit_compare] BLOCKED: " + "; ".join(blockers), file=sys.stderr)
            return 2
        print(f"[edit_compare] PASS -> {args.output}")
        print(f"[edit_compare] REPORT -> {report_path}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[edit_compare] ERROR: {exc}", file=sys.stderr)
        if report is not None:
            _write_json(report_path, report)
            _write_text(markdown_path, emit_markdown(report))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
