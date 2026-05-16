"""beat_sync — fallback grid + snap_to_beats."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from beat_sync import snap_to_beats, _fallback_grid  # noqa: E402


def test_snap_within_window():
    beats = [1.0, 2.0, 3.0]
    out = snap_to_beats([0.95, 2.10, 2.95], beats, window_seconds=0.2)
    assert out == [1.0, 2.0, 3.0]


def test_no_snap_outside_window():
    beats = [1.0, 2.0]
    out = snap_to_beats([0.5, 2.5], beats, window_seconds=0.2)
    # Neither 0.5 nor 2.5 are within 0.2 of any beat → unchanged
    assert out == [0.5, 2.5]


def test_empty_beats_passes_through():
    out = snap_to_beats([1.0, 2.0, 3.0], [], window_seconds=0.2)
    assert out == [1.0, 2.0, 3.0]


def test_fallback_grid_returns_evenly_spaced():
    tempo, beats = _fallback_grid("/nonexistent.mp3", bpm=120.0)
    assert tempo == 120.0
    # at 120 bpm beat interval = 0.5s; default duration 60s = ~120 beats
    assert len(beats) > 100
    assert abs(beats[1] - beats[0] - 0.5) < 1e-6


def test_fallback_grid_respects_custom_bpm():
    _, beats = _fallback_grid("/nonexistent.mp3", bpm=60.0)
    # 60 bpm = 1s interval
    assert abs(beats[1] - beats[0] - 1.0) < 1e-6
