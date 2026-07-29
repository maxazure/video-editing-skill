"""beat_sync — fallback grid, cut snapping, and beat edit skeletons."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from beat_sync import (  # noqa: E402
    _fallback_grid,
    _tempo_float,
    build_beat_edit_plan,
    render_plan_markdown,
    snap_to_beats,
)


class _OneElementTempo:
    def item(self):
        return 120.0


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


def test_tempo_float_accepts_librosa_one_element_array_shape():
    assert _tempo_float(_OneElementTempo()) == 120.0


def _regular_beats(duration=8.0, interval=0.5):
    return [round(index * interval, 3) for index in range(int(duration / interval) + 1)]


def test_generate_plan_selects_every_fourth_beat():
    plan = build_beat_edit_plan(
        "music.wav",
        duration=8.0,
        tempo_bpm=120.0,
        beats=_regular_beats(),
        detection_method="librosa",
    )

    assert plan["status"] == "ready"
    assert plan["cut_times"] == [2.0, 4.0, 6.0]
    assert [
        item["beat_index"] for item in plan["boundary_evidence"]
    ] == [4, 8, 12]
    assert [segment["duration"] for segment in plan["segments"]] == [2.0] * 4
    assert plan["summary"] == {
        "segments": 4,
        "cuts": 3,
        "beat_aligned_cuts": 3,
        "duration_guard_cuts": 0,
        "blocking": 0,
        "warnings": 0,
    }


def test_generate_plan_uses_duration_guards_for_sparse_grid():
    plan = build_beat_edit_plan(
        "ambient.wav",
        duration=7.0,
        tempo_bpm=60.0,
        beats=[],
        detection_method="librosa",
    )

    assert plan["status"] == "review"
    assert plan["cut_times"] == [3.0, 6.0]
    assert plan["summary"]["duration_guard_cuts"] == 2
    assert all(segment["duration"] <= 3.0 for segment in plan["segments"])
    assert any("duration guard" in warning for warning in plan["warnings"])


def test_generate_plan_protects_minimum_tail_duration():
    plan = build_beat_edit_plan(
        "music.wav",
        duration=4.2,
        tempo_bpm=120.0,
        beats=[0.0, 1.0, 2.0, 3.0, 4.0],
        detection_method="librosa",
        beats_per_cut=2,
        min_segment=0.75,
        max_segment=2.1,
    )

    assert plan["cut_times"] == [2.0, 3.0]
    assert min(segment["duration"] for segment in plan["segments"]) >= 0.75
    assert plan["boundary_evidence"][-1]["selected_by"] == "duration_guard"


def test_fallback_plan_is_explicitly_marked_for_review():
    plan = build_beat_edit_plan(
        "music.wav",
        duration=4.0,
        tempo_bpm=120.0,
        beats=_regular_beats(duration=4.0),
        detection_method="fallback_grid",
    )

    assert plan["status"] == "review"
    assert plan["detection"]["method"] == "fallback_grid"
    assert "fixed 120 BPM fallback grid" in plan["warnings"][0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration": 0},
        {"beats_per_cut": 0},
        {"min_segment": 0},
        {"min_segment": 2.0, "max_segment": 1.0},
    ],
)
def test_generate_plan_rejects_invalid_constraints(kwargs):
    base = {
        "audio_path": "music.wav",
        "duration": 4.0,
        "tempo_bpm": 120.0,
        "beats": _regular_beats(duration=4.0),
        "detection_method": "librosa",
    }
    base.update(kwargs)

    with pytest.raises(ValueError):
        build_beat_edit_plan(**base)


def test_markdown_exposes_slots_and_non_destructive_boundary():
    plan = build_beat_edit_plan(
        "music.wav",
        duration=4.0,
        tempo_bpm=120.0,
        beats=_regular_beats(duration=4.0),
        detection_method="librosa",
    )

    markdown = render_plan_markdown(plan)

    assert "# Beat Edit Plan" in markdown
    assert "| 1 | 0.000s | 2.000s |" in markdown
    assert "does not select footage, render media, or modify source files" in markdown


def test_cli_generate_plan_writes_json_and_markdown(tmp_path):
    output = tmp_path / "beat_edit_plan.json"
    markdown = tmp_path / "beat_edit_plan.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "beat_sync.py",
            ),
            "--bgm",
            str(tmp_path / "missing.wav"),
            "--generate-plan",
            "--duration",
            "4",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"] == "beat_edit_plan.v1"
    assert payload["status"] == "review"
    assert payload["detection"]["method"] == "fallback_grid"
    assert "# Beat Edit Plan" in markdown.read_text(encoding="utf-8")


def test_cli_generate_plan_rejects_zero_duration(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "beat_sync.py",
            ),
            "--bgm",
            str(tmp_path / "missing.wav"),
            "--generate-plan",
            "--duration",
            "0",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "duration must be greater than zero" in result.stderr
