#!/usr/bin/env python3
"""
Automatic jump cut planner/renderer for talking-head videos.

The script detects silent pauses, emits an auditable cut list, and optionally
renders a tighter video in one ffmpeg concat pass.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_ffmpeg_encode_args  # noqa: E402


DEFAULT_NOISE_DB = -35.0


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    duration: float


def _round4(value: float) -> float:
    return round(max(0.0, value), 4)


def parse_loudnorm_threshold(log: str, fallback: float = DEFAULT_NOISE_DB) -> float:
    """Extract loudnorm input_thresh from ffmpeg stderr.

    loudnorm prints JSON-ish metadata to stderr. input_thresh is the EBU R128
    gating threshold and is a better silencedetect baseline than one fixed dB
    value across every microphone and room.
    """
    matches = re.findall(r"\{[^{}]*\"input_thresh\"\s*:\s*\"?(-?[0-9.]+)\"?[^{}]*\}", log, re.S)
    if not matches:
        return fallback
    try:
        threshold = float(matches[-1])
    except ValueError:
        return fallback
    # Guard against bad probes and extreme thresholds that would cut speech.
    return max(-60.0, min(-25.0, threshold))


def parse_silencedetect(log: str, duration: Optional[float] = None) -> List[Segment]:
    """Parse ffmpeg silencedetect stderr into segments."""
    segments: List[Segment] = []
    current_start: Optional[float] = None
    for line in log.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            current_start = float(m_start.group(1))
            continue

        m_end = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if m_end and current_start is not None:
            end = float(m_end.group(1))
            dur = float(m_end.group(2))
            segments.append(Segment(_round4(current_start), _round4(end), _round4(dur)))
            current_start = None

    if current_start is not None and duration is not None and duration > current_start:
        end = _round4(duration)
        segments.append(Segment(_round4(current_start), end, _round4(end - current_start)))

    return segments


def build_keep_segments(duration: float, silences: List[Segment], pad: float = 0.08,
                        min_keep: float = 0.15) -> List[Segment]:
    """Convert silence intervals into kept speaking intervals.

    pad keeps a small amount of room tone around each cut so consonants do not
    sound clipped. Silences shorter than 2 * pad are left untouched.
    """
    if duration <= 0:
        return []

    keep: List[Segment] = []
    cursor = 0.0
    for silence in sorted(silences, key=lambda s: s.start):
        remove_start = max(cursor, silence.start + pad)
        remove_end = min(duration, silence.end - pad)
        if remove_end <= remove_start:
            continue

        if remove_start - cursor >= min_keep:
            keep.append(Segment(_round4(cursor), _round4(remove_start), _round4(remove_start - cursor)))
        cursor = max(cursor, remove_end)

    if duration - cursor >= min_keep:
        keep.append(Segment(_round4(cursor), _round4(duration), _round4(duration - cursor)))

    return keep


def infer_removed_segments(duration: float, keep_segments: List[Segment]) -> List[Segment]:
    removed: List[Segment] = []
    cursor = 0.0
    for segment in keep_segments:
        if segment.start > cursor:
            removed.append(Segment(_round4(cursor), _round4(segment.start), _round4(segment.start - cursor)))
        cursor = max(cursor, segment.end)
    if duration > cursor:
        removed.append(Segment(_round4(cursor), _round4(duration), _round4(duration - cursor)))
    return removed


def probe_media(path: str) -> Dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate",
        "-of", "json", path,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def media_duration(metadata: Dict) -> float:
    duration = metadata.get("format", {}).get("duration")
    if duration is None:
        raise ValueError("ffprobe did not report a media duration")
    return float(duration)


def has_stream(metadata: Dict, codec_type: str) -> bool:
    return any(s.get("codec_type") == codec_type for s in metadata.get("streams", []))


def measure_adaptive_noise_db(input_path: str) -> float:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", input_path,
        "-map", "0:a:0", "-af", "loudnorm=print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return parse_loudnorm_threshold(result.stderr)


def detect_silences(input_path: str, noise_db: float, min_silence: float,
                    duration: Optional[float] = None) -> List[Segment]:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", input_path,
        "-map", "0:a:0", "-af", f"silencedetect=n={noise_db:.2f}dB:d={min_silence:.3f}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return parse_silencedetect(result.stderr, duration=duration)


def build_cut_plan(input_path: str, output_path: Optional[str], duration: float,
                   silences: List[Segment], noise_db: float, min_silence: float,
                   pad: float, min_keep: float) -> Dict:
    keep_segments = build_keep_segments(duration, silences, pad=pad, min_keep=min_keep)
    removed_segments = infer_removed_segments(duration, keep_segments)
    removed_seconds = sum(s.duration for s in removed_segments)
    kept_seconds = sum(s.duration for s in keep_segments)
    return {
        "input": os.path.abspath(input_path),
        "output": os.path.abspath(output_path) if output_path else None,
        "duration": _round4(duration),
        "noise_threshold_db": round(noise_db, 2),
        "min_silence_seconds": min_silence,
        "pad_seconds": pad,
        "min_keep_seconds": min_keep,
        "detected_silences": [asdict(s) for s in silences],
        "removed_segments": [asdict(s) for s in removed_segments],
        "keep_segments": [asdict(s) for s in keep_segments],
        "removed_seconds": _round4(removed_seconds),
        "output_duration_estimate": _round4(kept_seconds),
        "speedup_ratio": round(duration / kept_seconds, 3) if kept_seconds else None,
    }


def build_ffmpeg_command(input_path: str, output_path: str, keep_segments: List[Segment],
                         has_video: bool = True,
                         video_encode_args: Optional[List[str]] = None) -> List[str]:
    if not keep_segments:
        raise ValueError("No keep segments available; refusing to render an empty output")

    filters: List[str] = []
    concat_inputs: List[str] = []
    for i, segment in enumerate(keep_segments):
        if has_video:
            filters.append(
                f"[0:v]trim=start={segment.start:.4f}:end={segment.end:.4f},"
                f"setpts=PTS-STARTPTS[v{i}]"
            )
            concat_inputs.append(f"[v{i}]")
        filters.append(
            f"[0:a]atrim=start={segment.start:.4f}:end={segment.end:.4f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[a{i}]")

    if has_video:
        filters.append("".join(concat_inputs) + f"concat=n={len(keep_segments)}:v=1:a=1[outv][outa]")
        return [
            "ffmpeg", "-y", "-hide_banner", "-i", input_path,
            "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "[outa]",
        ] + (video_encode_args or get_ffmpeg_encode_args()) + [
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path,
        ]

    filters.append("".join(concat_inputs) + f"concat=n={len(keep_segments)}:v=0:a=1[outa]")
    return [
        "ffmpeg", "-y", "-hide_banner", "-i", input_path,
        "-filter_complex", ";".join(filters),
        "-map", "[outa]", "-c:a", "aac", "-b:a", "192k", output_path,
    ]


def run_ffmpeg_with_fallback(cmd: List[str], *, has_video: bool) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if not has_video or "libx264" in cmd:
            if exc.stderr:
                print(exc.stderr[-2000:], file=sys.stderr)
            raise
        print("Hardware video encoder failed; retrying with libx264 CPU encoder...", file=sys.stderr)
        retry = list(cmd)
        if "-c:v" in retry:
            idx = retry.index("-c:v")
            # Replace the existing video encode args up to the audio codec args.
            try:
                end = retry.index("-c:a", idx)
            except ValueError:
                end = idx + 2
            retry[idx:end] = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        try:
            subprocess.run(retry, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as retry_exc:
            if retry_exc.stderr:
                print(retry_exc.stderr[-2000:], file=sys.stderr)
            raise


def write_json(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_noise_db(value: str, input_path: str) -> float:
    if value == "auto":
        return measure_adaptive_noise_db(input_path)
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--noise-db must be 'auto' or a number like -35") from exc


def main() -> int:
    p = argparse.ArgumentParser(description="Remove silent pauses from a talking-head video/audio file")
    p.add_argument("input", help="Input video/audio path")
    p.add_argument("--output", help="Rendered jump-cut output path. Omit with --dry-run to only write a cut list")
    p.add_argument("--cut-list", help="Write cut list JSON to this path")
    p.add_argument("--noise-db", default="auto", help="'auto' via loudnorm input_thresh, or a fixed value like -35")
    p.add_argument("--min-silence", type=float, default=0.5, help="Minimum silence duration to remove")
    p.add_argument("--pad", type=float, default=0.08, help="Seconds preserved on both sides of each silence")
    p.add_argument("--min-keep", type=float, default=0.15, help="Drop accidental kept fragments shorter than this")
    p.add_argument("--dry-run", action="store_true", help="Only detect and write/print the cut list")
    args = p.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 1
    if not args.output and not args.dry_run:
        print("Error: --output is required unless --dry-run is set", file=sys.stderr)
        return 1

    metadata = probe_media(args.input)
    if not has_stream(metadata, "audio"):
        print("Error: input has no audio stream; silence-based jump cut cannot run", file=sys.stderr)
        return 1

    duration = media_duration(metadata)
    noise_db = parse_noise_db(args.noise_db, args.input)
    silences = detect_silences(args.input, noise_db, args.min_silence, duration=duration)
    plan = build_cut_plan(
        args.input, args.output, duration, silences,
        noise_db=noise_db, min_silence=args.min_silence,
        pad=args.pad, min_keep=args.min_keep,
    )

    if args.cut_list:
        write_json(args.cut_list, plan)
        print(f"Cut list written: {args.cut_list}")
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    keep_segments = [Segment(**s) for s in plan["keep_segments"]]
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    input_has_video = has_stream(metadata, "video")
    cmd = build_ffmpeg_command(args.input, args.output, keep_segments, has_video=input_has_video)
    run_ffmpeg_with_fallback(cmd, has_video=input_has_video)
    print(f"Jump cut complete: {args.output}")
    print(f"Removed {plan['removed_seconds']:.2f}s; estimate {plan['output_duration_estimate']:.2f}s output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
