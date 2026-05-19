#!/usr/bin/env python3
"""
Post-render quality checks for short-form video outputs.

Runs ffprobe/ffmpeg-based checks after rendering:
  - container duration, dimensions, fps, video/audio stream presence
  - platform aspect expectations (xhs/douyin/wxch)
  - black frames via blackdetect
  - frozen video via freezedetect
  - long silence via silencedetect

Usage:
  python3 scripts/render_qa.py output/day58_master.mp4 --platform douyin
  python3 scripts/render_qa.py output/*.mp4 --json qa_report.json
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional


PLATFORM_DIMENSIONS = {
    "xhs": (1080, 1440),
    "douyin": (1080, 1920),
    "wxch": (1080, 1920),
}


@dataclass
class Segment:
    start: float
    end: float
    duration: float


@dataclass
class Check:
    name: str
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def probe_media(path: str) -> Dict[str, Any]:
    """Return ffprobe JSON metadata for a media file."""
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


def _video_stream(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def _audio_stream(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


def _duration(meta: Dict[str, Any]) -> Optional[float]:
    duration = _float_or_none(meta.get("format", {}).get("duration"))
    if duration is not None:
        return duration
    streams = [_float_or_none(s.get("duration")) for s in meta.get("streams", [])]
    streams = [s for s in streams if s is not None]
    return max(streams) if streams else None


def _fps(stream: Dict[str, Any]) -> Optional[float]:
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        n, d = rate.split("/", 1)
        n_f = _float_or_none(n)
        d_f = _float_or_none(d)
        if not n_f or not d_f:
            return None
        return n_f / d_f
    return _float_or_none(rate)


def parse_blackdetect(log: str) -> List[Segment]:
    pattern = re.compile(
        r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
    )
    return [
        Segment(float(m.group("start")), float(m.group("end")), float(m.group("duration")))
        for m in pattern.finditer(log)
    ]


def parse_freezedetect(log: str) -> List[Segment]:
    starts: List[float] = []
    segments: List[Segment] = []
    for line in log.splitlines():
        m_start = re.search(r"freeze_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = re.search(r"freeze_end:\s*([0-9.]+)\s*\|\s*freeze_duration:\s*([0-9.]+)", line)
        if m_end:
            start = starts.pop(0) if starts else float(m_end.group(1)) - float(m_end.group(2))
            end = float(m_end.group(1))
            duration = float(m_end.group(2))
            segments.append(Segment(start, end, duration))
    return segments


def parse_silencedetect(log: str) -> List[Segment]:
    starts: List[float] = []
    segments: List[Segment] = []
    for line in log.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if m_end:
            start = starts.pop(0) if starts else float(m_end.group(1)) - float(m_end.group(2))
            end = float(m_end.group(1))
            duration = float(m_end.group(2))
            segments.append(Segment(start, end, duration))
    return segments


def _run_filter(path: str, filter_arg: str, audio: bool = False) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path]
    if audio:
        cmd += ["-af", filter_arg, "-vn"]
    else:
        cmd += ["-vf", filter_arg, "-an"]
    cmd += ["-f", "null", "-"]
    result = _run(cmd)
    # Detection filters usually return 0; keep stderr even if ffmpeg exits nonzero
    # so callers can surface the real diagnostic.
    return result.stderr or result.stdout


def run_blackdetect(path: str, min_duration: float, pix_threshold: float) -> List[Segment]:
    log = _run_filter(path, f"blackdetect=d={min_duration}:pix_th={pix_threshold}")
    return parse_blackdetect(log)


def run_freezedetect(path: str, min_duration: float, noise_db: str) -> List[Segment]:
    log = _run_filter(path, f"freezedetect=n={noise_db}:d={min_duration}")
    return parse_freezedetect(log)


def run_silencedetect(path: str, min_duration: float, noise_db: str) -> List[Segment]:
    log = _run_filter(path, f"silencedetect=n={noise_db}:d={min_duration}", audio=True)
    return parse_silencedetect(log)


def _check_segment_budget(name: str, segments: List[Segment], limit: float) -> Check:
    total = sum(s.duration for s in segments)
    if total > limit:
        return Check(
            name=name,
            status="fail",
            message=f"{name} detected {total:.2f}s, over {limit:.2f}s budget",
            details={"segments": [asdict(s) for s in segments], "total_seconds": round(total, 3)},
        )
    if segments:
        return Check(
            name=name,
            status="warn",
            message=f"{name} detected {total:.2f}s within budget",
            details={"segments": [asdict(s) for s in segments], "total_seconds": round(total, 3)},
        )
    return Check(name=name, status="pass", message=f"No {name} segments detected")


def evaluate_media(
    path: str,
    meta: Dict[str, Any],
    *,
    platform: Optional[str],
    allow_no_audio: bool,
    min_duration: float,
    black_segments: Optional[List[Segment]],
    freeze_segments: Optional[List[Segment]],
    silence_segments: Optional[List[Segment]],
    max_black_seconds: float,
    max_freeze_seconds: float,
    max_silence_seconds: float,
) -> Dict[str, Any]:
    checks: List[Check] = []
    video = _video_stream(meta)
    audio = _audio_stream(meta)
    duration = _duration(meta)

    if video is None:
        checks.append(Check("video_stream", "fail", "No video stream found"))
    else:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        fps = _fps(video)
        checks.append(
            Check(
                "video_stream",
                "pass" if width and height else "fail",
                f"Video stream {width}x{height}" + (f" @ {fps:.2f}fps" if fps else ""),
                {"width": width, "height": height, "fps": fps},
            )
        )
        if platform:
            expected = PLATFORM_DIMENSIONS.get(platform)
            if expected and (width, height) != expected:
                checks.append(
                    Check(
                        "platform_dimensions",
                        "fail",
                        f"{platform} expects {expected[0]}x{expected[1]}, got {width}x{height}",
                        {"expected": list(expected), "actual": [width, height]},
                    )
                )
            elif expected:
                checks.append(Check("platform_dimensions", "pass", f"{platform} dimensions match"))

    if duration is None:
        checks.append(Check("duration", "fail", "Could not determine duration"))
    elif duration < min_duration:
        checks.append(Check("duration", "fail", f"Duration {duration:.2f}s below minimum {min_duration:.2f}s"))
    else:
        checks.append(Check("duration", "pass", f"Duration {duration:.2f}s", {"seconds": duration}))

    if audio is None:
        status = "warn" if allow_no_audio else "fail"
        checks.append(Check("audio_stream", status, "No audio stream found"))
    else:
        checks.append(Check("audio_stream", "pass", "Audio stream present", {
            "codec": audio.get("codec_name"),
            "channels": audio.get("channels"),
            "sample_rate": audio.get("sample_rate"),
        }))

    if black_segments is not None:
        checks.append(_check_segment_budget("black_frames", black_segments, max_black_seconds))
    if freeze_segments is not None:
        checks.append(_check_segment_budget("frozen_video", freeze_segments, max_freeze_seconds))
    if silence_segments is not None:
        checks.append(_check_segment_budget("silence", silence_segments, max_silence_seconds))

    statuses = [c.status for c in checks]
    status = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "path": path,
        "status": status,
        "checks": [asdict(c) for c in checks],
    }


def qa_file(path: str, args: argparse.Namespace) -> Dict[str, Any]:
    meta = probe_media(path)
    audio = _audio_stream(meta)

    black_segments = None if args.no_filters else run_blackdetect(path, args.black_min_duration, args.black_pix_threshold)
    freeze_segments = None if args.no_filters else run_freezedetect(path, args.freeze_min_duration, args.freeze_noise_db)
    silence_segments = None
    if not args.no_filters and audio is not None:
        silence_segments = run_silencedetect(path, args.silence_min_duration, args.silence_noise_db)

    return evaluate_media(
        path,
        meta,
        platform=args.platform,
        allow_no_audio=args.allow_no_audio,
        min_duration=args.min_duration,
        black_segments=black_segments,
        freeze_segments=freeze_segments,
        silence_segments=silence_segments,
        max_black_seconds=args.max_black_seconds,
        max_freeze_seconds=args.max_freeze_seconds,
        max_silence_seconds=args.max_silence_seconds,
    )


def _print_human_report(report: Dict[str, Any]) -> None:
    for item in report["files"]:
        symbol = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[item["status"]]
        print(f"{symbol} {item['path']}")
        for check in item["checks"]:
            print(f"  [{check['status'].upper()}] {check['name']}: {check['message']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run post-render QA checks on video outputs.")
    p.add_argument("videos", nargs="+", help="Video file(s) to check")
    p.add_argument("--platform", choices=sorted(PLATFORM_DIMENSIONS), help="Expected platform dimensions")
    p.add_argument("--json", dest="json_path", help="Write full QA report to this JSON path")
    p.add_argument("--no-filters", action="store_true", help="Only run ffprobe metadata checks")
    p.add_argument("--allow-no-audio", action="store_true", help="Warn instead of failing when audio is absent")
    p.add_argument("--min-duration", type=float, default=1.0, help="Minimum acceptable duration in seconds")
    p.add_argument("--black-min-duration", type=float, default=0.25, help="blackdetect minimum segment length")
    p.add_argument("--black-pix-threshold", type=float, default=0.10, help="blackdetect pixel threshold")
    p.add_argument("--freeze-min-duration", type=float, default=1.0, help="freezedetect minimum segment length")
    p.add_argument("--freeze-noise-db", default="-60dB", help="freezedetect noise threshold")
    p.add_argument("--silence-min-duration", type=float, default=1.5, help="silencedetect minimum segment length")
    p.add_argument("--silence-noise-db", default="-35dB", help="silencedetect noise threshold")
    p.add_argument("--max-black-seconds", type=float, default=0.5, help="Fail if total black frames exceed this")
    p.add_argument("--max-freeze-seconds", type=float, default=2.0, help="Fail if total frozen video exceeds this")
    p.add_argument("--max-silence-seconds", type=float, default=3.0, help="Fail if total silence exceeds this")
    return p


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    files = []
    for video in args.videos:
        if not os.path.isfile(video):
            files.append({
                "path": video,
                "status": "fail",
                "checks": [asdict(Check("file_exists", "fail", "File does not exist"))],
            })
            continue
        try:
            files.append(qa_file(video, args))
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            files.append({
                "path": video,
                "status": "fail",
                "checks": [asdict(Check("probe", "fail", str(exc)))],
            })

    status = "fail" if any(f["status"] == "fail" for f in files) else "warn" if any(f["status"] == "warn" for f in files) else "pass"
    report = {"status": status, "files": files}
    _print_human_report(report)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return 1 if status == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
