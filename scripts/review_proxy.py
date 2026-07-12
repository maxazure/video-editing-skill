#!/usr/bin/env python3
"""Create a lightweight, timecoded MP4 for human review.

The proxy is deliberately separate from the publish master: it downscales,
uses a fast web-friendly encode, and burns a visible review label/timecode so
feedback can cite exact moments. The source file is never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "review_proxy.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_paths(input_path: str, output_path: Optional[str] = None) -> Tuple[str, str, str]:
    source = Path(input_path).resolve()
    output = Path(output_path).resolve() if output_path else source.with_name(f"{source.stem}_review_proxy.mp4")
    manifest = output.with_suffix(".json")
    markdown = output.with_suffix(".md")
    return str(output), str(manifest), str(markdown)


def probe_media(path: str) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    return json.loads(result.stdout or "{}")


def media_summary(meta: Mapping[str, Any]) -> Dict[str, Any]:
    video = next((item for item in meta.get("streams", []) if item.get("codec_type") == "video"), {})
    audio = next((item for item in meta.get("streams", []) if item.get("codec_type") == "audio"), None)
    duration_raw = meta.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = round(float(duration_raw), 3)
    except (TypeError, ValueError):
        duration = None
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "duration_seconds": duration,
    }


def escape_drawtext_label(label: str) -> str:
    """Escape user text for FFmpeg's drawtext text= value."""
    return (
        label.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


def build_video_filter(*, max_height: int, fps: float, label: str, timecode: bool) -> str:
    filters = [
        f"scale=-2:'min(ih,{max_height})':flags=lanczos",
        f"fps={fps:g}",
    ]
    if label.strip():
        text = escape_drawtext_label(label.strip())
        filters.append(
            "drawtext="
            f"text='{text}':"
            "fontcolor=white:fontsize=h/30:"
            "box=1:boxcolor=black@0.68:boxborderw=10:"
            "x=20:y=20"
        )
    if timecode:
        y = "20" if not label.strip() else "20+h/30+18"
        filters.append(
            "drawtext="
            "text='%{pts\\:hms}':"
            "fontcolor=white:fontsize=h/30:"
            "box=1:boxcolor=black@0.68:boxborderw=10:"
            f"x=20:y={y}"
        )
    return ",".join(filters)


def build_ffmpeg_command(
    input_path: str,
    output_path: str,
    *,
    max_height: int = 720,
    fps: float = 24.0,
    crf: int = 28,
    preset: str = "veryfast",
    audio_bitrate: str = "96k",
    label: str = "REVIEW PROXY",
    timecode: bool = True,
) -> List[str]:
    video_filter = build_video_filter(max_height=max_height, fps=fps, label=label, timecode=timecode)
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        os.path.abspath(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-metadata",
        "comment=Review proxy - not for publication",
        os.path.abspath(output_path),
    ]


def build_plan(
    input_path: str,
    output_path: str,
    meta: Mapping[str, Any],
    command: Sequence[str],
    *,
    max_height: int,
    fps: float,
    crf: int,
    preset: str,
    audio_bitrate: str,
    label: str,
    timecode: bool,
    status: str = "planned",
) -> Dict[str, Any]:
    source = media_summary(meta)
    warnings = []
    if not source["has_audio"]:
        warnings.append("Source has no audio; proxy will be video-only.")
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": status,
        "source": {
            "path": os.path.abspath(input_path),
            **source,
        },
        "proxy": {
            "path": os.path.abspath(output_path),
            "max_height": max_height,
            "fps": fps,
            "video_codec": "libx264",
            "crf": crf,
            "preset": preset,
            "audio_codec": "aac" if source["has_audio"] else None,
            "audio_bitrate": audio_bitrate if source["has_audio"] else None,
            "faststart": True,
            "review_label": label,
            "timecode_burn_in": timecode,
        },
        "command": {
            "argv": list(command),
            "shell": shlex.join(command),
        },
        "summary": {
            "blocking": 0,
            "warnings": len(warnings),
        },
        "warnings": warnings,
        "review_notes": [
            "This file is a low-resolution review proxy, not a publish master.",
            "Use the visible elapsed timecode when reporting feedback.",
            "Keep the original master as the source of truth for final QA and publishing.",
        ],
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source", {})
    proxy = plan.get("proxy", {})
    lines = [
        "# Review Proxy",
        "",
        f"- Status: **{str(plan.get('status', '')).upper()}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Proxy: `{proxy.get('path', '')}`",
        f"- Source size: {source.get('width', '?')}x{source.get('height', '?')}",
        f"- Source duration: {source.get('duration_seconds', '?')}s",
        f"- Proxy profile: max {proxy.get('max_height', '?')}p / {proxy.get('fps', '?')} fps / CRF {proxy.get('crf', '?')}",
        f"- Burn-in: label `{proxy.get('review_label', '')}`; timecode `{proxy.get('timecode_burn_in', False)}`",
        "",
        "## Review Instructions",
        "",
    ]
    lines.extend(f"- {note}" for note in plan.get("review_notes", []))
    warnings = plan.get("warnings", [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", "## Reproduce", "", "```bash", plan.get("command", {}).get("shell", ""), "```", ""])
    return "\n".join(lines)


def write_outputs(plan: Mapping[str, Any], manifest_path: str, markdown_path: str) -> None:
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(markdown_path).write_text(emit_markdown(plan), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a low-resolution, timecoded MP4 review proxy.")
    parser.add_argument("input", help="Rendered master or platform MP4 to proxy.")
    parser.add_argument("--output", help="Proxy MP4 path; defaults to <input>_review_proxy.mp4.")
    parser.add_argument("--manifest", help="JSON manifest path; defaults beside the proxy.")
    parser.add_argument("--markdown", help="Markdown review note path; defaults beside the proxy.")
    parser.add_argument("--max-height", type=int, default=720, help="Maximum proxy height without upscaling (default: 720).")
    parser.add_argument("--fps", type=float, default=24.0, help="Proxy frame rate (default: 24).")
    parser.add_argument("--crf", type=int, default=28, help="H.264 CRF (default: 28).")
    parser.add_argument("--preset", default="veryfast", help="libx264 preset (default: veryfast).")
    parser.add_argument("--audio-bitrate", default="96k", help="AAC bitrate (default: 96k).")
    parser.add_argument("--label", default="REVIEW PROXY", help="Visible ASCII review label.")
    parser.add_argument("--no-timecode", action="store_true", help="Do not burn elapsed timecode into the proxy.")
    parser.add_argument("--dry-run", action="store_true", help="Write the plan without invoking FFmpeg.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 2
    if args.max_height <= 0 or args.fps <= 0 or not 0 <= args.crf <= 51:
        print("Error: --max-height/--fps must be positive and --crf must be 0-51.", file=sys.stderr)
        return 2

    default_output, default_manifest, default_markdown = default_paths(input_path, args.output)
    output_path = os.path.abspath(default_output)
    manifest_path = os.path.abspath(args.manifest or default_manifest)
    markdown_path = os.path.abspath(args.markdown or default_markdown)
    if output_path == input_path:
        print("Error: review proxy output must not overwrite the source video.", file=sys.stderr)
        return 2

    try:
        source_meta = probe_media(input_path)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: could not inspect input: {exc}", file=sys.stderr)
        return 2

    source_summary = media_summary(source_meta)
    if not source_summary.get("width") or not source_summary.get("height"):
        print("Error: input has no readable video stream.", file=sys.stderr)
        return 2

    command = build_ffmpeg_command(
        input_path,
        output_path,
        max_height=args.max_height,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        label=args.label,
        timecode=not args.no_timecode,
    )
    plan = build_plan(
        input_path,
        output_path,
        source_meta,
        command,
        max_height=args.max_height,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        label=args.label,
        timecode=not args.no_timecode,
    )

    if not args.dry_run:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"FFmpeg error:\n{result.stderr[-2000:]}", file=sys.stderr)
            return 2
        plan["status"] = "ready"
        plan["proxy"]["size_bytes"] = os.path.getsize(output_path)
        try:
            plan["proxy"]["media"] = media_summary(probe_media(output_path))
        except (RuntimeError, json.JSONDecodeError) as exc:
            plan["warnings"].append(f"Proxy rendered but post-render probe failed: {exc}")
            plan["summary"]["warnings"] = len(plan["warnings"])

    write_outputs(plan, manifest_path, markdown_path)
    print(
        f"Review proxy {plan['status']}: {output_path}; "
        f"manifest={manifest_path}; warnings={plan['summary']['warnings']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
