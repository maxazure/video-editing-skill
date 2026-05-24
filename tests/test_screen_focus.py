import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from render_final import build_focus_filter_ops, merge_enrich_plan, normalize_focus_event  # noqa: E402
from screen_focus import build_focus_plan, emit_markdown  # noqa: E402


def test_build_focus_plan_normalises_pixel_clicks():
    plan = build_focus_plan(
        [
            {
                "time": "00:01.50",
                "x": 960,
                "y": 540,
                "label": "Open settings",
                "duration": 1.5,
            }
        ],
        screen_width=1920,
        screen_height=1080,
    )

    event = plan["focus_events"][0]
    assert plan["version"] == "screen_focus_plan.v1"
    assert event["start"] == 1.5
    assert event["end"] == 3.0
    assert event["x"] == 0.5
    assert event["y"] == 0.5
    assert event["source_x"] == 960
    assert event["label"] == "Open settings"
    assert plan["summary"]["labelled_events"] == 1


def test_emit_markdown_contains_review_table():
    plan = build_focus_plan(
        [{"time": 2.0, "x": 0.25, "y": 0.75, "label": "Export"}],
    )
    md = emit_markdown(plan)

    assert "| event | time | xy | zoom | label |" in md
    assert "Export" in md


def test_merge_enrich_plan_adds_focus_events_and_badges():
    plan = {
        "focus_events": [
            {"start": 3.0, "end": 4.0, "x": 0.7, "y": 0.2, "label": "Click export"}
        ]
    }
    merged = merge_enrich_plan({"clips": [], "text_badges": []}, plan, plan_base_dir=".")

    assert merged["focus_events"][0]["x"] == 0.7
    assert merged["text_badges"][0]["text"] == "Click export"
    assert merged["_enrich_plan_stats"]["focus_events"] == 1
    assert merged["_enrich_plan_stats"]["text_badges"] == 1


def test_focus_filter_ops_builds_zoom_crop_and_marker():
    lines, label, next_stage = build_focus_filter_ops(
        "[merged_v]",
        [
            {
                "start": 2.0,
                "end": 3.2,
                "x": 0.75,
                "y": 0.5,
                "zoom": 2.0,
                "marker_color": "yellow@0.8",
            }
        ],
        width=1920,
        height=1080,
        cover_duration=1.0,
        speed=1.0,
        stage_idx=4,
    )
    rendered = ";\n".join(lines)

    assert label == "[vstage4]"
    assert next_stage == 5
    assert "crop=960:540" in rendered
    assert "scale=1920:1080:flags=lanczos" in rendered
    assert "drawbox=" in rendered
    assert "between(t,3.0000,4.2000)" in rendered


def test_normalize_focus_event_clamps_values():
    event = normalize_focus_event(
        {"time": 1.0, "duration": -2, "x": 2200, "y": -10, "source_width": 2000, "source_height": 1000, "zoom": 8}
    )

    assert event["end"] > event["start"]
    assert event["x"] == 1.0
    assert event["y"] == 0.0
    assert event["zoom"] == 4.0


def test_cli_writes_json_and_markdown(tmp_path):
    events_path = tmp_path / "clicks.json"
    out_json = tmp_path / "focus.json"
    out_md = tmp_path / "focus.md"
    events_path.write_text(
        json.dumps({"screen": {"width": 1000, "height": 500}, "events": [{"time": 1, "x": 500, "y": 250}]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/screen_focus.py"),
            "--events",
            str(events_path),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["summary"]["focus_events"] == 1
    assert "Screen Focus Plan" in out_md.read_text(encoding="utf-8")
