#!/usr/bin/env python3
"""Find review-only structural breaks where internal black video and silence overlap."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from black_edge_trim import parse_blackdetect, parse_silencedetect


VERSION = "program_breaks.v1"


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise RuntimeError("command failed: " + " ".join(command[:3]) + " — " + (result.stderr or result.stdout)[-1200:])
    return result.stdout if command[0] == "ffprobe" else result.stderr


def _fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def _probe(path: Path) -> tuple[float, list[int]]:
    raw = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    data = json.loads(raw)
    streams = data.get("streams", [])
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("source needs a video stream")
    audio_rows = [row for row in streams if row.get("codec_type") == "audio"]
    audio = [int(row["index"]) for row in audio_rows]
    if not audio:
        raise ValueError("source needs an audio stream to prove silent breaks")
    video_start = float(video.get("start_time") or 0)
    if not math.isfinite(video_start):
        raise ValueError("video stream start is unavailable")
    for row in audio_rows:
        audio_start = float(row.get("start_time") or 0)
        if not math.isfinite(audio_start) or abs(audio_start - video_start) > 0.05:
            raise ValueError("audio/video stream starts differ; conform timeline before analysis")
    duration = float(video.get("duration") or data.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source duration is unavailable")
    return duration, audio


def _intersections(intervals: list[dict], other: list[dict]) -> list[dict]:
    result = []
    for left in intervals:
        for right in other:
            start = max(left["start"], right["start"])
            end = min(left["end"], right["end"])
            if end > start:
                result.append({"start": start, "end": end})
    return result


def _validate_threshold(value: float, name: str, minimum: float, maximum: float) -> float:
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def analyze(source: Path, *, black_min: float = 0.3, silence_min: float = 0.3,
            min_overlap: float = 0.2, edge_guard: float = 0.5,
            pix_threshold: float = 0.10, picture_ratio: float = 0.98,
            silence_noise: str = "-35dB") -> dict:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be a file")
    black_min = _validate_threshold(black_min, "black-min", 0.05, 60)
    silence_min = _validate_threshold(silence_min, "silence-min", 0.05, 60)
    min_overlap = _validate_threshold(min_overlap, "min-overlap", 0.05, 60)
    edge_guard = _validate_threshold(edge_guard, "edge-guard", 0, 60)
    pix_threshold = _validate_threshold(pix_threshold, "pix-threshold", 0.001, 0.5)
    picture_ratio = _validate_threshold(picture_ratio, "picture-ratio", 0.5, 1)
    if not silence_noise.endswith("dB"):
        raise ValueError("silence-noise must be a dB value, for example -35dB")
    try:
        noise_value = float(silence_noise[:-2])
    except ValueError as exc:
        raise ValueError("silence-noise must be a dB value") from exc
    _validate_threshold(noise_value, "silence-noise", -100, 0)
    before = _fingerprint(source)
    duration, audio_streams = _probe(source)
    black_log = _run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-i", str(source),
                      "-map", "0:v:0", "-vf", f"setpts=PTS-STARTPTS,blackdetect=d={black_min}:pic_th={picture_ratio}:pix_th={pix_threshold}",
                      "-an", "-f", "null", "-"])
    black = parse_blackdetect(black_log, duration=duration)
    silent_by_stream = []
    for index in audio_streams:
        log = _run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-i", str(source),
                    "-map", f"0:{index}", "-af", f"asetpts=PTS-STARTPTS,silencedetect=noise={silence_noise}:d={silence_min}",
                    "-vn", "-f", "null", "-"])
        silent_by_stream.append({"stream_index": index, "intervals": parse_silencedetect(log, duration=duration)})
    if _fingerprint(source) != before:
        raise RuntimeError("source changed during analysis")
    all_silent = silent_by_stream[0]["intervals"]
    for item in silent_by_stream[1:]:
        all_silent = _intersections(all_silent, item["intervals"])
    candidates = []
    for interval in black:
        if interval["start"] < edge_guard or interval["end"] > duration - edge_guard:
            continue
        overlaps = _intersections([interval], all_silent)
        if not overlaps:
            continue
        best = max(overlaps, key=lambda row: (row["end"] - row["start"], -row["start"]))
        length = best["end"] - best["start"]
        if length + 1e-6 < min_overlap:
            continue
        candidates.append({"break_seconds": round((best["start"] + best["end"]) / 2, 6),
                           "black_interval": interval,
                           "all_audio_silent_interval": {"start": round(best["start"], 6), "end": round(best["end"], 6)},
                           "overlap_seconds": round(length, 6)})
    return {"version": VERSION, "source": before, "duration_seconds": round(duration, 6),
            "settings": {"black_min": black_min, "silence_min": silence_min, "min_overlap": min_overlap,
                         "edge_guard": edge_guard, "pix_threshold": pix_threshold,
                         "picture_ratio": picture_ratio, "silence_noise": silence_noise},
            "audio_streams": silent_by_stream, "black_intervals": black,
            "candidates": candidates, "review_required": True}


def _markdown(report: dict) -> str:
    lines = ["# Internal program break candidates", "", f"Source: `{report['source']['path']}`",
             f"Duration: {report['duration_seconds']:.3f} s", "",
             "| Candidate time (s) | Black interval (s) | All-audio silence overlap (s) | Overlap (s) |",
             "|---:|---|---|---:|"]
    for row in report["candidates"]:
        black = row["black_interval"]
        silent = row["all_audio_silent_interval"]
        lines.append(f"| {row['break_seconds']:.3f} | {black['start']:.3f}–{black['end']:.3f} | "
                     f"{silent['start']:.3f}–{silent['end']:.3f} | {row['overlap_seconds']:.3f} |")
    if not report["candidates"]:
        lines.append("| None | — | — | — |")
    lines += ["", "These are review points only. Play each boundary with picture and all audio tracks before splitting or naming segments.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("analyze", help="Analyze a source and write JSON plus Markdown review evidence")
    create.add_argument("source", type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--markdown", type=Path)
    for action in (create,):
        action.add_argument("--black-min", type=float, default=0.3)
        action.add_argument("--silence-min", type=float, default=0.3)
        action.add_argument("--min-overlap", type=float, default=0.2)
        action.add_argument("--edge-guard", type=float, default=0.5)
        action.add_argument("--pix-threshold", type=float, default=0.10)
        action.add_argument("--picture-ratio", type=float, default=0.98)
        action.add_argument("--silence-noise", default="-35dB")
    verify = sub.add_parser("verify", help="Rerun analysis and detect source/report drift")
    verify.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            output = args.output.expanduser().resolve()
            markdown = (args.markdown or output.with_suffix(".md")).expanduser().resolve()
            source = args.source.expanduser().resolve(strict=True)
            if len({source, output, markdown}) != 3:
                raise ValueError("source, JSON, and Markdown paths must differ")
            if output.exists() or markdown.exists():
                raise FileExistsError("output exists; choose new paths")
            report = analyze(source, black_min=args.black_min, silence_min=args.silence_min,
                             min_overlap=args.min_overlap, edge_guard=args.edge_guard,
                             pix_threshold=args.pix_threshold, picture_ratio=args.picture_ratio,
                             silence_noise=args.silence_noise)
            report["markdown_path"] = str(markdown)
            output.parent.mkdir(parents=True, exist_ok=True)
            markdown.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            markdown.write_text(_markdown(report), encoding="utf-8")
            print(json.dumps({"status": "review", "candidates": len(report["candidates"]), "report": str(output)}, ensure_ascii=False))
        else:
            report = json.loads(args.report.read_text(encoding="utf-8"))
            if report.get("version") != VERSION:
                raise ValueError("unsupported report version")
            expected = analyze(Path(report["source"]["path"]), **report["settings"])
            expected["markdown_path"] = report.get("markdown_path")
            if expected != report:
                raise ValueError("source or report drift")
            markdown_path = Path(report["markdown_path"])
            if markdown_path.read_text(encoding="utf-8") != _markdown(report):
                raise ValueError("Markdown drift")
            print(json.dumps({"status": "verified", "candidates": len(report["candidates"])}))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
