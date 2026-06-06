import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from privacy_redact import RedactionEvent, build_filter_complex, build_plan, emit_markdown, load_events  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_load_events_accepts_normalized_detector_boxes(tmp_path):
    detections = tmp_path / "detections.json"
    detections.write_text(json.dumps({
        "detections": [
            {
                "start": 1.0,
                "end": 2.0,
                "bbox": [0.1, 0.2, 0.3, 0.4],
                "unit": "normalized",
                "label": "face",
                "score": 0.91,
                "reviewed": True,
            }
        ]
    }), encoding="utf-8")

    events, warnings = load_events(
        [str(detections)],
        [],
        width=1000,
        height=500,
        scale=1.0,
        frame_hold=0.25,
        min_score=0.5,
        include_labels=[],
        exclude_labels=[],
    )

    assert warnings == []
    assert len(events) == 1
    assert events[0].x == 100
    assert events[0].y == 100
    assert events[0].w == 200
    assert events[0].h == 100
    assert events[0].label == "face"


def test_manual_box_builds_blur_ffmpeg_filter():
    plan = build_plan(
        video=None,
        detection_paths=[],
        manual_boxes=["0:2:10,20,120,80:screen:true"],
        width=1920,
        height=1080,
        method="blur",
        scale=1.0,
        render_output=None,
    )

    filter_complex = plan["ffmpeg"]["filter_complex"]
    assert plan["version"] == "privacy_redaction_plan.v1"
    assert plan["summary"]["total_events"] == 1
    assert "split=2" in filter_complex
    assert "crop=120:80:10:20" in filter_complex
    assert "boxblur=" in filter_complex
    assert "overlay=10:20:enable='between(t,0.0000,2.0000)'" in filter_complex


def test_pixelate_filter_uses_scale_down_and_up():
    event = RedactionEvent(
        id="redact_001",
        start=1.0,
        end=3.0,
        x=100,
        y=50,
        w=200,
        h=100,
        label="plate",
        reviewed=True,
    )

    filter_complex = build_filter_complex(
        [event],
        method="pixelate",
        blur_radius=0,
        pixel_blocks=10,
        mask_color="black@1.0",
    )

    assert "scale=20:10:flags=neighbor" in filter_complex
    assert "scale=200:100:flags=neighbor" in filter_complex


def test_require_reviewed_blocks_unreviewed_detector_event(tmp_path):
    detections = tmp_path / "detections.json"
    detections.write_text(json.dumps({
        "events": [
            {"start": 0, "end": 1, "x": 1, "y": 2, "w": 30, "h": 40, "label": "face"}
        ]
    }), encoding="utf-8")

    plan = build_plan(
        video=None,
        detection_paths=[str(detections)],
        manual_boxes=[],
        width=320,
        height=240,
        require_reviewed=True,
    )

    assert plan["summary"]["unreviewed"] == 1
    assert plan["summary"]["blocking"] == 1
    markdown = emit_markdown(plan)
    assert "Privacy Redaction Review" in markdown
    assert "BLOCKED" in markdown


def test_cli_writes_plan_and_markdown_and_strict_exit(tmp_path):
    out_json = tmp_path / "privacy_redaction.json"
    out_md = tmp_path / "privacy_redaction.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "privacy_redact.py"),
            "--width",
            "640",
            "--height",
            "360",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--require-redactions",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["blocking"] == 1


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "privacy_redact.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "privacy redaction plan" in result.stdout.lower()
