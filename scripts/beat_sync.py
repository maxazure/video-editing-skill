#!/usr/bin/env python3
"""Build a beat-aligned edit skeleton or snap existing cuts to BGM beats.

Uses librosa.beat.beat_track when available; falls back to a fixed-interval
"pseudo-beat" grid (default 0.5 s) so the rest of the pipeline still works
on minimal installs.

CLI:
    python3 scripts/beat_sync.py --bgm <path.mp3> --cuts <cuts.json> --output <snapped.json>
    python3 scripts/beat_sync.py --bgm <path.mp3> --generate-plan --output <plan.json>
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import List, Tuple


SNAP_WINDOW_DEFAULT_SECONDS = 0.20  # ±200 ms
BEATS_PER_CUT_DEFAULT = 4
MIN_SEGMENT_DEFAULT_SECONDS = 0.75
MAX_SEGMENT_DEFAULT_SECONDS = 3.0


def _tempo_float(value) -> float:
    """Normalize librosa scalar/one-element ndarray tempo values."""
    return float(value.item()) if hasattr(value, "item") else float(value)


def detect_beats_detailed(
    audio_path: str,
    *,
    fallback_bpm: float = 120.0,
) -> Tuple[float, List[float], str]:
    """Return tempo, beat times, and the detector method.

    A fixed grid remains available for minimal installs, but callers can now
    preserve that fact in review artifacts instead of presenting it as measured
    musical evidence.
    """
    if fallback_bpm <= 0:
        raise ValueError("fallback_bpm must be greater than zero")
    try:
        import librosa  # type: ignore
    except ImportError:
        tempo, times = _fallback_grid(audio_path, bpm=fallback_bpm)
        return tempo, times, "fallback_grid"

    try:
        y, sr = librosa.load(audio_path, sr=None)
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
        times = librosa.frames_to_time(frames, sr=sr).tolist()
        return _tempo_float(tempo), times, "librosa"
    except Exception as exc:  # noqa: BLE001
        print(f"[beat-sync] librosa failed ({exc}); falling back to fixed grid",
              file=sys.stderr)
        tempo, times = _fallback_grid(audio_path, bpm=fallback_bpm)
        return tempo, times, "fallback_grid"


def detect_beats(
    audio_path: str,
    *,
    fallback_bpm: float = 120.0,
) -> Tuple[float, List[float]]:
    """Return (tempo_bpm, beat times), preserving the original public API."""
    tempo, times, _method = detect_beats_detailed(
        audio_path,
        fallback_bpm=fallback_bpm,
    )
    return tempo, times


def _fallback_grid(audio_path: str, bpm: float) -> Tuple[float, List[float]]:
    """When librosa is unavailable, generate evenly-spaced beats at `bpm`.
    Duration comes from ffprobe if available, otherwise 60 s."""
    interval = 60.0 / bpm
    duration = _ffprobe_duration(audio_path) or 60.0
    times = []
    t = 0.0
    while t < duration:
        times.append(round(t, 3))
        t += interval
    return bpm, times


def _ffprobe_duration(audio_path: str):
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        return None


def snap_to_beats(
    cut_times: List[float],
    beats: List[float],
    *,
    window_seconds: float = SNAP_WINDOW_DEFAULT_SECONDS,
) -> List[float]:
    """Snap each cut time to the nearest beat if within `window_seconds`.

    Returns a new list of cut times; unsnappable cuts pass through unchanged.
    """
    if not beats:
        return list(cut_times)

    sorted_beats = sorted(beats)
    snapped = []
    for t in cut_times:
        # binary search would be nicer; linear is fine for small inputs
        nearest = min(sorted_beats, key=lambda b: abs(b - t))
        if abs(nearest - t) <= window_seconds:
            snapped.append(round(nearest, 3))
        else:
            snapped.append(round(t, 3))
    return snapped


def _round3(value: float) -> float:
    return round(max(0.0, float(value)), 3)


def build_beat_edit_plan(
    audio_path: str,
    *,
    duration: float,
    tempo_bpm: float,
    beats: List[float],
    detection_method: str,
    beats_per_cut: int = BEATS_PER_CUT_DEFAULT,
    min_segment: float = MIN_SEGMENT_DEFAULT_SECONDS,
    max_segment: float = MAX_SEGMENT_DEFAULT_SECONDS,
    fallback_bpm: float = 120.0,
) -> dict:
    """Create beat-aligned program slots without choosing or rendering footage."""
    if duration <= 0:
        raise ValueError("duration must be greater than zero")
    if isinstance(beats_per_cut, bool) or not isinstance(beats_per_cut, int) or beats_per_cut <= 0:
        raise ValueError("beats_per_cut must be a positive integer")
    if min_segment <= 0:
        raise ValueError("min_segment must be greater than zero")
    if max_segment < min_segment:
        raise ValueError("max_segment must be greater than or equal to min_segment")

    duration = _round3(duration)
    if duration <= 0:
        raise ValueError("duration must round to at least 0.001 seconds")
    grid = sorted({
        _round3(beat)
        for beat in beats
        if 0 < float(beat) < duration
    })
    internal_boundaries = []
    evidence = []
    warnings = []
    notes = []
    cursor = 0.0
    epsilon = 0.0005

    while duration - cursor > max_segment + epsilon:
        future = [
            (index, beat)
            for index, beat in enumerate(grid, start=1)
            if beat > cursor + epsilon
        ]
        eligible = [
            (index, beat)
            for index, beat in future
            if (
                min_segment - epsilon <= beat - cursor <= max_segment + epsilon
                and duration - beat >= min_segment - epsilon
            )
        ]

        if eligible:
            target_position = min(beats_per_cut - 1, len(future) - 1)
            target_index, target_time = future[target_position]
            beat_index, boundary = min(
                eligible,
                key=lambda item: (abs(item[1] - target_time), item[1]),
            )
            selected_by = (
                "beats_per_cut"
                if beat_index == target_index
                else "duration_guard"
            )
            meta = {
                "time": _round3(boundary),
                "alignment": "beat",
                "beat_index": beat_index,
                "selected_by": selected_by,
                "target_beat_index": target_index,
                "target_time": _round3(target_time),
            }
        else:
            boundary = _round3(
                min(cursor + max_segment, duration - min_segment)
            )
            meta = {
                "time": boundary,
                "alignment": "duration_guard",
                "beat_index": None,
                "selected_by": "max_segment",
                "target_beat_index": None,
                "target_time": None,
            }
            warnings.append(
                f"No detected beat kept the segment within {min_segment:g}-{max_segment:g}s; "
                f"inserted a duration guard at {boundary:.3f}s"
            )

        if boundary <= cursor + epsilon:
            raise ValueError("beat grid did not advance the edit cursor")
        internal_boundaries.append(boundary)
        evidence.append(meta)
        cursor = boundary

    boundaries = [0.0, *internal_boundaries, duration]
    if duration < min_segment - epsilon:
        warnings.append(
            f"Program duration is shorter than min_segment ({duration:.3f}s)"
        )

    segments = []
    evidence_by_time = {item["time"]: item for item in evidence}
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        end_meta = evidence_by_time.get(end)
        segments.append({
            "index": index,
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
            "end_alignment": (
                end_meta["alignment"] if end_meta else "program_end"
            ),
            "end_beat_index": (
                end_meta["beat_index"] if end_meta else None
            ),
        })

    if detection_method == "fallback_grid":
        warnings.insert(
            0,
            f"Beat detection used a fixed {fallback_bpm:g} BPM fallback grid; "
            "listen to the BGM and verify every proposed boundary",
        )

    beat_aligned = sum(item["alignment"] == "beat" for item in evidence)
    guard_cuts = sum(item["alignment"] != "beat" for item in evidence)
    return {
        "version": "beat_edit_plan.v1",
        "status": "review" if warnings else "ready",
        "bgm": os.path.abspath(audio_path),
        "duration": duration,
        "tempo_bpm": round(float(tempo_bpm), 2),
        "detection": {
            "method": detection_method,
            "beat_count": len(grid),
            "fallback_bpm": fallback_bpm if detection_method == "fallback_grid" else None,
        },
        "settings": {
            "beats_per_cut": beats_per_cut,
            "min_segment_seconds": float(min_segment),
            "max_segment_seconds": float(max_segment),
        },
        "beat_times": grid,
        "cut_times": [_round3(value) for value in boundaries[1:-1]],
        "segments": segments,
        "boundary_evidence": evidence,
        "blockers": [],
        "warnings": warnings,
        "notes": notes,
        "summary": {
            "segments": len(segments),
            "cuts": max(0, len(segments) - 1),
            "beat_aligned_cuts": beat_aligned,
            "duration_guard_cuts": guard_cuts,
            "blocking": 0,
            "warnings": len(warnings),
        },
    }


def render_plan_markdown(plan: dict) -> str:
    """Render a compact human review for a generated beat edit skeleton."""
    summary = plan["summary"]
    lines = [
        "# Beat Edit Plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- BGM: `{plan['bgm']}`",
        f"- Tempo: `{plan['tempo_bpm']:.2f} BPM` via `{plan['detection']['method']}`",
        f"- Program: `{plan['duration']:.3f}s`, {summary['segments']} slots / {summary['cuts']} cuts",
        (
            f"- Alignment: {summary['beat_aligned_cuts']} beat cuts, "
            f"{summary['duration_guard_cuts']} duration guards"
        ),
        "",
        "## Slots",
        "",
        "| # | Start | End | Duration | End alignment | Beat |",
        "|---:|---:|---:|---:|---|---:|",
    ]
    for segment in plan["segments"]:
        beat = (
            str(segment["end_beat_index"])
            if segment["end_beat_index"] is not None
            else "—"
        )
        lines.append(
            f"| {segment['index']} | {segment['start']:.3f}s | {segment['end']:.3f}s | "
            f"{segment['duration']:.3f}s | {segment['end_alignment']} | {beat} |"
        )

    if plan["warnings"]:
        lines.extend(["", "## Review warnings", ""])
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    if plan["notes"]:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in plan["notes"])

    lines.extend([
        "",
        "Map approved source clips into these program-time slots only after listening to the BGM.",
        "This plan does not select footage, render media, or modify source files.",
        "",
    ])
    return "\n".join(lines)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build a beat edit skeleton or snap existing cuts to BGM beats"
    )
    p.add_argument("--bgm", required=True, help="BGM audio file")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--cuts",
        help="JSON with either a flat list of seconds or [{'start': float, ...}, ...]",
    )
    mode.add_argument(
        "--generate-plan",
        action="store_true",
        help="Generate program-time edit slots directly from the beat grid",
    )
    p.add_argument("--window", type=float, default=SNAP_WINDOW_DEFAULT_SECONDS,
                   help="Snap window in seconds (default 0.2)")
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Program duration for --generate-plan; defaults to BGM duration",
    )
    p.add_argument(
        "--beats-per-cut",
        type=int,
        default=BEATS_PER_CUT_DEFAULT,
        help="Preferred number of beats per generated slot (default 4)",
    )
    p.add_argument(
        "--min-segment",
        type=float,
        default=MIN_SEGMENT_DEFAULT_SECONDS,
        help="Minimum generated slot duration in seconds (default 0.75)",
    )
    p.add_argument(
        "--max-segment",
        type=float,
        default=MAX_SEGMENT_DEFAULT_SECONDS,
        help="Maximum generated slot duration in seconds (default 3.0)",
    )
    p.add_argument(
        "--fallback-bpm",
        type=float,
        default=120.0,
        help="Fixed grid BPM when librosa detection is unavailable (default 120)",
    )
    p.add_argument("--output", default=None, help="Output JSON path; stdout if omitted")
    p.add_argument(
        "--markdown",
        default=None,
        help="Optional Markdown review path for --generate-plan",
    )
    p.add_argument("--print-beats", action="store_true", help="Also list detected beats")
    args = p.parse_args()

    try:
        tempo, beats, detection_method = detect_beats_detailed(
            args.bgm,
            fallback_bpm=args.fallback_bpm,
        )
    except ValueError as exc:
        p.error(str(exc))
    print(f"[beat-sync] tempo≈{tempo:.1f} bpm, {len(beats)} beats", file=sys.stderr)
    if args.print_beats:
        print(json.dumps(beats[:50], indent=2), file=sys.stderr)

    if args.generate_plan:
        duration = (
            args.duration
            if args.duration is not None
            else _ffprobe_duration(args.bgm)
        )
        if duration is None:
            p.error("--duration is required when ffprobe cannot read the BGM duration")
        try:
            payload = build_beat_edit_plan(
                args.bgm,
                duration=duration,
                tempo_bpm=tempo,
                beats=beats,
                detection_method=detection_method,
                beats_per_cut=args.beats_per_cut,
                min_segment=args.min_segment,
                max_segment=args.max_segment,
                fallback_bpm=args.fallback_bpm,
            )
        except ValueError as exc:
            p.error(str(exc))

        out_text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            _write_text(args.output, out_text + "\n")
            print(
                f"✅ generated {payload['summary']['segments']} beat slots → {args.output}",
                file=sys.stderr,
            )
        else:
            print(out_text)
        if args.markdown:
            _write_text(args.markdown, render_plan_markdown(payload))
            print(f"✅ review → {args.markdown}", file=sys.stderr)
        return 0

    with open(args.cuts, encoding="utf-8") as f:
        raw = json.load(f)

    if raw and isinstance(raw[0], dict):
        cut_times = [float(item["start"]) for item in raw]
        was_dicts = True
    else:
        cut_times = [float(t) for t in raw]
        was_dicts = False

    snapped = snap_to_beats(cut_times, beats, window_seconds=args.window)

    if was_dicts:
        for orig, new in zip(raw, snapped):
            orig["start"] = new
        payload = raw
    else:
        payload = snapped

    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        _write_text(args.output, out_text)
        print(f"✅ snapped {len(snapped)} cuts → {args.output}", file=sys.stderr)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
