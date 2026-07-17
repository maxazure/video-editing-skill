import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from retention_rhythm_qa import (  # noqa: E402
    analyze_rhythm,
    emit_markdown,
    load_scene_boundaries,
    load_timed_text,
)


def _cues(duration, step=3.0):
    result = []
    start = 0.0
    index = 1
    while start < duration:
        end = min(duration, start + step)
        result.append({"id": str(index), "start": start, "end": end, "text": f"cue {index}"})
        start = end
        index += 1
    return result


def test_load_timed_text_supports_subtitle_pack_and_filters_invalid(tmp_path):
    path = tmp_path / "subtitles.json"
    path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [
                    {"index": 1, "start": 0, "end": 2, "text": "第一句"},
                    {"index": 2, "start": 2, "end": 2, "text": "bad"},
                    {"index": 3, "start": 2, "end": 4, "text": "第二句"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cues = load_timed_text(str(path))

    assert [cue["text"] for cue in cues] == ["第一句", "第二句"]
    assert cues[1]["duration"] == 2.0


def test_load_scene_boundaries_uses_source_duration(tmp_path):
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "source": {"duration": 12.0},
                "boundaries": [0, 2, 2, 5.5, 20],
            }
        ),
        encoding="utf-8",
    )

    duration, boundaries = load_scene_boundaries(str(path))

    assert duration == 12.0
    assert boundaries == [2.0, 5.5]


def test_varied_active_rhythm_is_ready():
    report = analyze_rhythm(
        "output/final.mp4",
        duration=18.0,
        boundaries=[1.0, 3.5, 5.0, 8.0, 10.0, 12.8, 14.0, 17.5],
        cues=_cues(18.0),
        timed_text_provided=True,
    )

    assert report["version"] == "retention_rhythm_qa.v1"
    assert report["summary"]["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert report["metrics"]["hook"]["attention_events"] >= 1


def test_long_visual_hold_blocks_but_inactive_hook_without_text_only_warns():
    report = analyze_rhythm(
        "output/final.mp4",
        duration=20.0,
        boundaries=[12.0],
    )

    kinds = {(item["kind"], item["severity"]) for item in report["findings"]}
    assert ("long_visual_hold", "block") in kinds
    assert ("inactive_hook", "warn") in kinds
    assert report["summary"]["blocking"] == 1


def test_timed_text_makes_empty_hook_a_blocker():
    report = analyze_rhythm(
        "output/final.mp4",
        duration=8.0,
        boundaries=[4.0, 6.0],
        cues=[{"id": "1", "start": 4.0, "end": 6.0, "text": "late"}],
        timed_text_provided=True,
    )

    hook = next(item for item in report["findings"] if item["kind"] == "inactive_hook")
    assert hook["severity"] == "block"
    assert report["summary"]["blocking"] >= 1


def test_metronomic_and_rapid_patterns_are_review_warnings():
    metronomic = analyze_rhythm(
        "output/final.mp4",
        duration=10.0,
        boundaries=[2.0, 4.0, 6.0, 8.0],
    )
    rapid = analyze_rhythm(
        "output/final.mp4",
        duration=3.0,
        boundaries=[0.2, 0.4, 0.6, 0.8, 1.0, 2.0],
    )

    assert any(item["kind"] == "metronomic_cadence" for item in metronomic["findings"])
    assert any(item["kind"] == "rapid_cut_burst" for item in rapid["findings"])
    assert metronomic["summary"]["blocking"] == 0
    assert rapid["summary"]["blocking"] == 0


def test_subtitle_hold_gap_and_attention_gap_are_reported():
    report = analyze_rhythm(
        "output/final.mp4",
        duration=18.0,
        boundaries=[2.0],
        cues=[
            {"id": "1", "start": 0.0, "end": 6.0, "text": "long"},
            {"id": "2", "start": 9.0, "end": 12.0, "text": "late"},
        ],
        timed_text_provided=True,
    )

    kinds = {item["kind"] for item in report["findings"]}
    assert {"stale_subtitle", "subtitle_gap", "attention_gap"} <= kinds
    assert report["summary"]["longest_attention_gap"] == 9.0


def test_emit_markdown_has_metrics_and_review_contract():
    report = analyze_rhythm("output/final.mp4", duration=12.0, boundaries=[8.0])

    markdown = emit_markdown(report)

    assert "# Retention Rhythm QA" in markdown
    assert "long_visual_hold" in markdown
    assert "not a retention or virality forecast" in markdown


def test_cli_strict_uses_precomputed_artifacts_and_exits_two(tmp_path):
    scene_path = tmp_path / "scene_boundaries.json"
    subtitle_path = tmp_path / "subtitles.json"
    output_path = tmp_path / "retention_rhythm_qa.json"
    markdown_path = tmp_path / "retention_rhythm_qa.md"
    scene_path.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "source": {"duration": 20.0},
                "boundaries": [12.0],
            }
        ),
        encoding="utf-8",
    )
    subtitle_path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [{"index": 1, "start": 12.0, "end": 14.0, "text": "late"}],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "retention_rhythm_qa.py"),
            "output/final.mp4",
            "--scene-boundaries",
            str(scene_path),
            "--timed-text",
            str(subtitle_path),
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary"]["blocking"] >= 1
    assert "Retention Rhythm QA" in markdown_path.read_text(encoding="utf-8")


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "retention_rhythm_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "hook activity" in result.stdout.lower()
