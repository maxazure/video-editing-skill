import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from pip_overlay import build_pip_plan, build_segments, emit_markdown, parse_segment  # noqa: E402
from render_final import build_pip_filter_ops, merge_enrich_plan, normalize_pip_overlay  # noqa: E402


def test_build_pip_plan_creates_overlay_segments(tmp_path):
    camera = tmp_path / "facecam.mp4"
    camera.write_bytes(b"fake")
    segments = build_segments(
        [parse_segment("0,4,bottom_left"), parse_segment("4,8,top_right")],
        start=0,
        end=None,
        duration=None,
        position="bottom_right",
    )

    plan = build_pip_plan(
        camera=str(camera),
        segments=segments,
        width_ratio=0.3,
        sync_offset=0.25,
    )

    assert plan["version"] == "pip_overlay_plan.v1"
    assert plan["summary"]["pip_overlays"] == 2
    assert plan["summary"]["camera_exists"] is True
    assert plan["pip_overlays"][0]["position"] == "bottom_left"
    assert plan["pip_overlays"][0]["source_start"] == 0.25
    assert plan["pip_overlays"][1]["source_start"] == 4.25
    assert plan["pip_overlays"][0]["audio"] is False


def test_emit_markdown_contains_review_table(tmp_path):
    camera = tmp_path / "facecam.mp4"
    camera.write_bytes(b"fake")
    plan = build_pip_plan(
        camera=str(camera),
        segments=[{"start": 1.0, "end": 3.0, "position": "bottom_right"}],
    )
    md = emit_markdown(plan)

    assert "PIP Overlay Plan" in md
    assert "| overlay | time | source | position | size | opacity |" in md
    assert "bottom_right" in md


def test_merge_enrich_plan_adds_pip_overlay(tmp_path):
    camera = tmp_path / "facecam.mp4"
    camera.write_bytes(b"fake")
    plan = {
        "pip_overlays": [
            {"video": "facecam.mp4", "start": 2.0, "end": 5.0, "position": "top_left"}
        ]
    }

    merged = merge_enrich_plan({"clips": []}, plan, plan_base_dir=str(tmp_path))

    assert merged["pip_overlays"][0]["video"] == str(camera)
    assert merged["pip_overlays"][0]["position"] == "top_left"
    assert merged["_enrich_plan_stats"]["pip_overlays"] == 1


def test_merge_enrich_plan_skips_missing_pip_asset(tmp_path):
    merged = merge_enrich_plan(
        {"clips": []},
        {"pip_overlays": [{"video": "missing.mp4", "start": 0, "end": 1}]},
        plan_base_dir=str(tmp_path),
    )

    assert "pip_overlays" not in merged
    assert merged["_enrich_plan_stats"]["missing_pip_assets"] == 1


def test_normalize_pip_overlay_accounts_for_speed_and_position():
    overlay = normalize_pip_overlay(
        {
            "start": 2.0,
            "end": 6.0,
            "sync_offset": 0.4,
            "position": "bottom_left",
            "width_ratio": 0.25,
        },
        width=1920,
        height=1080,
        cover_duration=1.0,
        speed=2.0,
    )

    assert overlay["start_out"] == 2.0
    assert overlay["end_out"] == 4.0
    assert overlay["source_start"] == 2.4
    assert overlay["source_duration"] == 4.0
    assert overlay["width"] == 480
    assert overlay["x"] == "38"
    assert overlay["y"] == "main_h-overlay_h-38"


def test_normalize_pip_overlay_falls_back_for_invalid_speed():
    overlay = normalize_pip_overlay(
        {"start": 1.0, "end": 3.0},
        width=1000,
        height=1000,
        cover_duration=0.0,
        speed=0.0,
    )

    assert overlay["start_out"] == 1.0
    assert overlay["end_out"] == 3.0


def test_pip_filter_ops_builds_timed_overlay_with_speed_sync():
    lines, label, next_stage = build_pip_filter_ops(
        "[merged_v]",
        [
            (
                {
                    "start": 2.0,
                    "end": 6.0,
                    "source_start": 0.5,
                    "position": "top_right",
                    "width_ratio": 0.2,
                    "opacity": 0.8,
                },
                3,
            )
        ],
        width=1920,
        height=1080,
        cover_duration=1.0,
        speed=2.0,
        stage_idx=7,
    )
    rendered = ";\n".join(lines)

    assert label == "[vstage7]"
    assert next_stage == 8
    assert "[3:v]trim=start=0.5000:duration=4.0000" in rendered
    assert "setpts=(PTS-STARTPTS)/2.0000+2.0000/TB" in rendered
    assert "scale=384:-2:force_original_aspect_ratio=decrease" in rendered
    assert "colorchannelmixer=aa=0.800" in rendered
    assert "overlay=main_w-overlay_w-38:38" in rendered
    assert "between(t,2.0000,4.0000)" in rendered


def test_cli_writes_json_and_markdown(tmp_path):
    camera = tmp_path / "facecam.mp4"
    out_json = tmp_path / "pip.json"
    out_md = tmp_path / "pip.md"
    camera.write_bytes(b"fake")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/pip_overlay.py"),
            "--camera",
            str(camera),
            "--segment",
            "0,2,bottom_right",
            "--segment",
            "2,4,top_left",
            "--sync-offset",
            "0.1",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["pip_overlays"] == 2
    assert payload["pip_overlays"][1]["position"] == "top_left"
    assert "PIP Overlay Plan" in out_md.read_text(encoding="utf-8")
