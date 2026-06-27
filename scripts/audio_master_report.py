#!/usr/bin/env python3
"""Measure final audio loudness and publishability gates.

The script is read-only: it does not rewrite media or call any provider. It
uses FFmpeg's ebur128 and silencedetect filters to produce JSON/Markdown that
can be reviewed or used as a strict publish gate.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value


def _round_or_none(value: Optional[float], digits: int = 2) -> Optional[float]:
    value = _finite(value)
    if value is None:
        return None
    return round(value, digits)


def _parse_number(value: str) -> Optional[float]:
    value = value.strip().lower()
    if value in {"-inf", "inf", "+inf"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return _finite(parsed)


def probe_media(path: str) -> Dict[str, Any]:
    result = _run([
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    return json.loads(result.stdout or "{}")


def audio_stream(meta: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "audio":
            return dict(stream)
    return None


def _duration(meta: Mapping[str, Any]) -> Optional[float]:
    try:
        return _finite(float(meta.get("format", {}).get("duration")))
    except (TypeError, ValueError):
        return None


def _summary_block(log: str) -> str:
    idx = log.rfind("Summary:")
    return log[idx:] if idx >= 0 else log


def _section(summary: str, heading: str) -> str:
    pattern = re.compile(
        rf"{re.escape(heading)}:\s*(.*?)(?=\n\s*(?:Integrated loudness|Loudness range|True peak):|\Z)",
        re.S,
    )
    match = pattern.search(summary)
    return match.group(1) if match else ""


def _field(section: str, label: str, unit: str) -> Optional[float]:
    match = re.search(rf"^\s*{re.escape(label)}:\s*([-+]?(?:\d+(?:\.\d+)?|inf)|-inf)\s*{re.escape(unit)}", section, re.M)
    return _parse_number(match.group(1)) if match else None


def parse_ebur128_summary(log: str) -> Dict[str, Optional[float]]:
    """Parse FFmpeg ebur128 summary measurements."""
    summary = _summary_block(log)
    integrated = _section(summary, "Integrated loudness")
    loudness_range = _section(summary, "Loudness range")
    true_peak = _section(summary, "True peak")
    return {
        "integrated_lufs": _round_or_none(_field(integrated, "I", "LUFS")),
        "integrated_threshold_lufs": _round_or_none(_field(integrated, "Threshold", "LUFS")),
        "lra_lu": _round_or_none(_field(loudness_range, "LRA", "LU")),
        "lra_low_lufs": _round_or_none(_field(loudness_range, "LRA low", "LUFS")),
        "lra_high_lufs": _round_or_none(_field(loudness_range, "LRA high", "LUFS")),
        "true_peak_dbfs": _round_or_none(_field(true_peak, "Peak", "dBFS")),
    }


def run_ebur128(path: str) -> Dict[str, Optional[float]]:
    result = _run([
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-af",
        "ebur128=peak=true",
        "-vn",
        "-f",
        "null",
        "-",
    ])
    log = result.stderr or result.stdout
    if result.returncode != 0 and "Summary:" not in log:
        raise RuntimeError(log.strip() or "ffmpeg ebur128 failed")
    return parse_ebur128_summary(log)


def parse_silencedetect(log: str) -> List[Dict[str, float]]:
    starts: List[float] = []
    segments: List[Dict[str, float]] = []
    for line in log.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if m_end:
            duration = float(m_end.group(2))
            end = float(m_end.group(1))
            start = starts.pop(0) if starts else end - duration
            segments.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
            })
    return segments


def run_silencedetect(path: str, *, min_duration: float, noise_db: str) -> List[Dict[str, float]]:
    result = _run([
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-af",
        f"silencedetect=n={noise_db}:d={min_duration}",
        "-vn",
        "-f",
        "null",
        "-",
    ])
    return parse_silencedetect(result.stderr or result.stdout)


def _check(name: str, status: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {"name": name, "status": status, "message": message}
    if details:
        row["details"] = details
    return row


def evaluate_measurements(
    measurements: Mapping[str, Optional[float]],
    silence_segments: List[Mapping[str, float]],
    *,
    target_lufs: float,
    tolerance: float,
    max_true_peak_dbfs: float,
    max_lra_lu: float,
    max_silence_seconds: float,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    integrated = measurements.get("integrated_lufs")
    if integrated is None:
        checks.append(_check("integrated_loudness", "fail", "Could not measure integrated loudness"))
    else:
        delta = abs(float(integrated) - target_lufs)
        status = "pass" if delta <= tolerance else "fail"
        checks.append(_check(
            "integrated_loudness",
            status,
            f"Integrated loudness {integrated:.2f} LUFS; target {target_lufs:.2f} +/- {tolerance:.2f}",
            {"delta_lu": round(delta, 2), "target_lufs": target_lufs, "tolerance_lu": tolerance},
        ))

    true_peak = measurements.get("true_peak_dbfs")
    if true_peak is None:
        checks.append(_check("true_peak", "warn", "Could not measure true peak"))
    else:
        status = "pass" if float(true_peak) <= max_true_peak_dbfs else "fail"
        checks.append(_check(
            "true_peak",
            status,
            f"True peak {true_peak:.2f} dBFS; max {max_true_peak_dbfs:.2f} dBFS",
            {"max_true_peak_dbfs": max_true_peak_dbfs},
        ))

    lra = measurements.get("lra_lu")
    if lra is None:
        checks.append(_check("loudness_range", "warn", "Could not measure loudness range"))
    else:
        status = "pass" if float(lra) <= max_lra_lu else "fail"
        checks.append(_check(
            "loudness_range",
            status,
            f"Loudness range {lra:.2f} LU; max {max_lra_lu:.2f} LU",
            {"max_lra_lu": max_lra_lu},
        ))

    silence_total = round(sum(float(seg.get("duration", 0.0)) for seg in silence_segments), 3)
    if silence_total > max_silence_seconds:
        status = "fail"
        message = f"Long silence totals {silence_total:.2f}s; max {max_silence_seconds:.2f}s"
    elif silence_segments:
        status = "warn"
        message = f"Long silence totals {silence_total:.2f}s within budget"
    else:
        status = "pass"
        message = "No long silence detected"
    checks.append(_check(
        "long_silence",
        status,
        message,
        {"segments": silence_segments, "total_seconds": silence_total, "max_silence_seconds": max_silence_seconds},
    ))
    return checks


def summarize_checks(checks: List[Mapping[str, Any]]) -> Dict[str, Any]:
    blocking = sum(1 for check in checks if check.get("status") == "fail")
    warnings = sum(1 for check in checks if check.get("status") == "warn")
    return {
        "status": "blocked" if blocking else "warn" if warnings else "ready",
        "blocking": blocking,
        "warnings": warnings,
        "checks": len(checks),
    }


def build_report(path: str, args: argparse.Namespace) -> Dict[str, Any]:
    target = {
        "integrated_lufs": args.target_lufs,
        "tolerance_lu": args.tolerance,
        "max_true_peak_dbfs": args.max_true_peak,
        "max_lra_lu": args.max_lra,
        "silence_noise_db": args.silence_noise_db,
        "silence_min_duration": args.silence_min_duration,
        "max_silence_seconds": args.max_silence_seconds,
    }
    report: Dict[str, Any] = {
        "version": "audio_master_report.v1",
        "created_at": utc_now(),
        "source_path": path,
        "target": target,
        "audio_stream": None,
        "measurements": {},
        "silence": {"segments": [], "total_seconds": 0.0},
        "checks": [],
    }

    if not os.path.isfile(path):
        report["checks"] = [_check("file_exists", "fail", "File does not exist")]
        report["summary"] = summarize_checks(report["checks"])
        return report

    meta = probe_media(path)
    stream = audio_stream(meta)
    report["duration_seconds"] = _round_or_none(_duration(meta), 3)
    if stream is None:
        report["checks"] = [_check("audio_stream", "fail", "No audio stream found")]
        report["summary"] = summarize_checks(report["checks"])
        return report

    report["audio_stream"] = {
        "codec": stream.get("codec_name"),
        "channels": stream.get("channels"),
        "sample_rate": stream.get("sample_rate"),
    }
    measurements = run_ebur128(path)
    silence_segments = [] if args.no_silence else run_silencedetect(
        path,
        min_duration=args.silence_min_duration,
        noise_db=args.silence_noise_db,
    )
    report["measurements"] = measurements
    report["silence"] = {
        "segments": silence_segments,
        "total_seconds": round(sum(seg["duration"] for seg in silence_segments), 3),
    }
    report["checks"] = evaluate_measurements(
        measurements,
        silence_segments,
        target_lufs=args.target_lufs,
        tolerance=args.tolerance,
        max_true_peak_dbfs=args.max_true_peak,
        max_lra_lu=args.max_lra,
        max_silence_seconds=args.max_silence_seconds,
    )
    report["summary"] = summarize_checks(report["checks"])
    report["suggested_filter"] = (
        f"loudnorm=I={args.target_lufs:g}:TP={args.max_true_peak:g}:LRA={min(args.max_lra, 11):g}"
    )
    return report


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    measurements = report.get("measurements") or {}
    lines = [
        "# Audio Master Report",
        "",
        f"- Status: **{str(summary.get('status', 'unknown')).upper()}**",
        f"- Source: `{report.get('source_path', '')}`",
        f"- Blocking checks: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Measurements",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Integrated loudness | {measurements.get('integrated_lufs', 'n/a')} LUFS |",
        f"| True peak | {measurements.get('true_peak_dbfs', 'n/a')} dBFS |",
        f"| Loudness range | {measurements.get('lra_lu', 'n/a')} LU |",
        f"| Long silence total | {(report.get('silence') or {}).get('total_seconds', 0.0)} s |",
        "",
        "## Checks",
        "",
        "| Status | Check | Message |",
        "|---|---|---|",
    ]
    for check in report.get("checks", []):
        lines.append(f"| {check.get('status')} | `{check.get('name')}` | {check.get('message')} |")
    lines += [
        "",
        f"Suggested corrective filter if needed: `{report.get('suggested_filter', 'n/a')}`",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create an audio master loudness report for a rendered video or audio file.")
    p.add_argument("media", help="Rendered video/audio file to inspect")
    p.add_argument("--target-lufs", type=float, default=-16.0, help="Integrated loudness target, default -16 LUFS")
    p.add_argument("--tolerance", type=float, default=2.0, help="Allowed LU distance from target")
    p.add_argument("--max-true-peak", type=float, default=-1.0, help="Maximum true peak in dBFS")
    p.add_argument("--max-lra", type=float, default=18.0, help="Maximum loudness range in LU")
    p.add_argument("--silence-noise-db", default="-35dB", help="silencedetect noise threshold")
    p.add_argument("--silence-min-duration", type=float, default=1.5, help="Minimum silence segment length")
    p.add_argument("--max-silence-seconds", type=float, default=3.0, help="Maximum total long silence seconds")
    p.add_argument("--no-silence", action="store_true", help="Skip long-silence detection")
    p.add_argument("--output", help="Write JSON report")
    p.add_argument("--markdown", help="Write Markdown report")
    p.add_argument("--strict", action="store_true", help="Exit 2 when any blocking check fails")
    return p


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(args.media, args)
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        report = {
            "version": "audio_master_report.v1",
            "created_at": utc_now(),
            "source_path": args.media,
            "target": {},
            "audio_stream": None,
            "measurements": {},
            "silence": {"segments": [], "total_seconds": 0.0},
            "checks": [_check("analysis", "fail", str(exc))],
        }
        report["summary"] = summarize_checks(report["checks"])

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(report))

    if not args.output and not args.markdown:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and (report.get("summary") or {}).get("blocking", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
