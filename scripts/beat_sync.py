#!/usr/bin/env python3
"""Snap cut points to the nearest BGM beat (±200 ms by default).

Uses librosa.beat.beat_track when available; falls back to a fixed-interval
"pseudo-beat" grid (default 0.5 s) so the rest of the pipeline still works
on minimal installs.

CLI:
    python3 scripts/beat_sync.py --bgm <path.mp3> --cues <cues.json> --output <snapped.json>
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import List, Tuple


SNAP_WINDOW_DEFAULT_SECONDS = 0.20  # ±200 ms


def detect_beats(audio_path: str) -> Tuple[float, List[float]]:
    """Return (tempo_bpm, [beat_time_seconds]). Falls back to a 120 bpm grid
    if librosa isn't installed or the file fails to load."""
    try:
        import librosa  # type: ignore
    except ImportError:
        return _fallback_grid(audio_path, bpm=120.0)

    try:
        y, sr = librosa.load(audio_path, sr=None)
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
        times = librosa.frames_to_time(frames, sr=sr).tolist()
        return float(tempo), times
    except Exception as exc:  # noqa: BLE001
        print(f"[beat-sync] librosa failed ({exc}); falling back to fixed grid",
              file=sys.stderr)
        return _fallback_grid(audio_path, bpm=120.0)


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


def main() -> int:
    p = argparse.ArgumentParser(description="Snap cut points to BGM beats")
    p.add_argument("--bgm", required=True, help="BGM audio file")
    p.add_argument("--cuts", required=True,
                   help="JSON with either a flat list of seconds or [{'start': float, ...}, ...]")
    p.add_argument("--window", type=float, default=SNAP_WINDOW_DEFAULT_SECONDS,
                   help="Snap window in seconds (default 0.2)")
    p.add_argument("--output", default=None, help="Output JSON path; stdout if omitted")
    p.add_argument("--print-beats", action="store_true", help="Also list detected beats")
    args = p.parse_args()

    with open(args.cuts, encoding="utf-8") as f:
        raw = json.load(f)

    if raw and isinstance(raw[0], dict):
        cut_times = [float(item["start"]) for item in raw]
        was_dicts = True
    else:
        cut_times = [float(t) for t in raw]
        was_dicts = False

    tempo, beats = detect_beats(args.bgm)
    print(f"[beat-sync] tempo≈{tempo:.1f} bpm, {len(beats)} beats", file=sys.stderr)
    if args.print_beats:
        print(json.dumps(beats[:50], indent=2), file=sys.stderr)

    snapped = snap_to_beats(cut_times, beats, window_seconds=args.window)

    if was_dicts:
        for orig, new in zip(raw, snapped):
            orig["start"] = new
        payload = raw
    else:
        payload = snapped

    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ snapped {len(snapped)} cuts → {args.output}", file=sys.stderr)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
