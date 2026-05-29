import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from highlight_picker import (  # noqa: E402
    apply_scene_snap,
    build_highlight_candidates,
    build_render_config,
    dedupe_candidates,
    emit_markdown,
    normalize_segments,
)


def _sample_transcript():
    return {
        "language": "zh",
        "duration": 120.0,
        "segments": [
            {"id": 1, "start": 0.0, "end": 8.0, "text": "今天我们先介绍一下背景"},
            {"id": 2, "start": 8.0, "end": 18.0, "text": "为什么很多人做 AI 自动化会失败？"},
            {"id": 3, "start": 18.0, "end": 29.0, "text": "关键不是工具，而是没有把流程拆成步骤。"},
            {"id": 4, "start": 29.0, "end": 40.0, "text": "我用这个方法把交付时间降低了 50%。"},
            {"id": 5, "start": 40.0, "end": 52.0, "text": "最后给你一个检查清单，照着做就不会漏。"},
            {"id": 6, "start": 52.0, "end": 64.0, "text": "然后我们看第二个普通案例"},
            {"id": 7, "start": 64.0, "end": 76.0, "text": "这里没有特别强的结论，只是补充说明"},
        ],
    }


def test_normalize_segments_keeps_timing_and_ids():
    segments = normalize_segments(_sample_transcript())
    assert [s.idx for s in segments[:2]] == [1, 2]
    assert segments[1].start == 8.0


def test_build_highlight_candidates_scores_hook_and_value():
    plan = build_highlight_candidates(
        _sample_transcript(),
        platform="douyin",
        min_duration=25,
        max_duration=55,
        target_duration=40,
        num_clips=2,
    )

    assert plan["version"] == "highlight_candidates.v1"
    assert plan["selected"]
    top = plan["selected"][0]
    assert top["score"] >= 55
    assert 2 in top["segment_ids"]
    assert "strong question hook" in top["signals"]
    assert "specific data point" in top["signals"]


def test_dedupe_candidates_removes_lower_scored_overlap():
    candidates = [
        {"start": 0, "end": 40, "duration": 40, "score": 80},
        {"start": 5, "end": 42, "duration": 37, "score": 70},
        {"start": 50, "end": 80, "duration": 30, "score": 65},
    ]

    kept = dedupe_candidates(candidates, overlap_threshold=0.5)
    assert len(kept) == 2
    assert kept[0]["score"] == 80
    assert kept[1]["start"] == 50


def test_emit_markdown_and_render_config_include_selected_clip():
    plan = build_highlight_candidates(
        _sample_transcript(),
        min_duration=25,
        max_duration=55,
        num_clips=1,
    )
    md = emit_markdown(plan)
    render_config = build_render_config(plan, "origin/long.mp4")

    assert "# Highlight Candidates" in md
    assert "origin/long.mp4" == render_config["clips"][0]["video"]
    assert render_config["clips"][0]["highlight_score"] == plan["selected"][0]["score"]


def test_scene_snap_expands_candidate_to_visual_boundaries():
    candidate = {
        "start": 8.0,
        "end": 40.0,
        "duration": 32.0,
        "score": 70,
    }

    snapped = apply_scene_snap(
        candidate,
        boundary_points=[0.0, 7.4, 40.8, 80.0],
        tolerance=1.0,
        max_duration=35.0,
    )

    assert snapped["start"] == 7.4
    assert snapped["end"] == 40.8
    assert snapped["duration"] == 33.4
    assert snapped["scene_snap"]["applied"] is True


def test_build_highlight_candidates_uses_scene_boundaries():
    scene_boundaries = {
        "version": "scene_boundaries.v1",
        "source": {"duration": 120},
        "boundaries": [7.5, 52.5],
        "scenes": [
            {"start": 0.0, "end": 7.5},
            {"start": 7.5, "end": 52.5},
            {"start": 52.5, "end": 120.0},
        ],
    }

    plan = build_highlight_candidates(
        _sample_transcript(),
        min_duration=25,
        max_duration=55,
        target_duration=40,
        num_clips=1,
        scene_boundaries=scene_boundaries,
        scene_snap_tolerance=1.0,
    )

    selected = plan["selected"][0]
    assert selected["start"] == 7.5
    assert selected["end"] == 52.5
    assert selected["scene_snap"]["applied"] is True
    assert plan["summary"]["scene_snapped"] == 1

    render_config = build_render_config(plan, "origin/long.mp4")
    assert render_config["clips"][0]["start"] == 7.5
    assert render_config["clips"][0]["scene_snap"]["snapped_end"] == 52.5


def test_cli_writes_json_markdown_and_render_config(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    output_path = tmp_path / "highlights.json"
    markdown_path = tmp_path / "highlights.md"
    render_path = tmp_path / "render_config.json"
    scene_path = tmp_path / "scene_boundaries.json"
    transcript_path.write_text(json.dumps(_sample_transcript(), ensure_ascii=False), encoding="utf-8")
    scene_path.write_text(json.dumps({
        "version": "scene_boundaries.v1",
        "source": {"duration": 120},
        "boundaries": [7.5, 52.5],
        "scenes": [
            {"start": 0.0, "end": 7.5},
            {"start": 7.5, "end": 52.5},
            {"start": 52.5, "end": 120.0},
        ],
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/highlight_picker.py"),
            "--transcript",
            str(transcript_path),
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
            "--video",
            "origin/long.mp4",
            "--render-config",
            str(render_path),
            "--scene-boundaries",
            str(scene_path),
            "--scene-snap-tolerance",
            "1.0",
            "--num-clips",
            "2",
            "--min-duration",
            "25",
            "--max-duration",
            "55",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"]["selected"] == 2
    assert "Highlight Candidates" in markdown_path.read_text(encoding="utf-8")
    render_config = json.loads(render_path.read_text(encoding="utf-8"))
    assert len(render_config["clips"]) == 2
    assert "scene_snap" in render_config["clips"][0]
