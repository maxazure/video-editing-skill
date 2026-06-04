import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from smart_reframe import (  # noqa: E402
    build_reframe_filter_complex,
    build_reframe_plan,
    build_reframe_vf,
    emit_markdown,
    normalize_detections,
    target_crop_dimensions,
)


def test_target_crop_dimensions_for_wide_source_to_vertical():
    crop_w, crop_h = target_crop_dimensions(1920, 1080, 1080, 1920)

    assert crop_w == 608
    assert crop_h == 1080


def test_normalize_detections_accepts_frame_objects_and_normalized_bbox():
    payload = {
        "frames": [
            {
                "time": 1.5,
                "objects": [
                    {"label": "face", "bbox": [0.4, 0.2, 0.5, 0.4], "confidence": 0.8},
                ],
            }
        ]
    }

    detections = normalize_detections(payload, 1920, 1080)

    assert len(detections) == 1
    assert detections[0]["time"] == 1.5
    assert detections[0]["label"] == "face"
    assert detections[0]["bbox"] == [768.0, 216.0, 960.0, 432.0]


def test_build_plan_tracks_off_center_subject():
    plan = build_reframe_plan(
        video_path="talk.mp4",
        src_w=1920,
        src_h=1080,
        duration=6.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[
            {"time": 2.0, "label": "face", "bbox": [1450, 250, 1600, 430], "confidence": 0.9},
        ],
    )

    segment = plan["segments"][0]
    assert segment["strategy"] == "track"
    assert segment["crop"]["width"] == 608
    assert segment["crop"]["x"] > 1100
    assert plan["summary"]["strategies"] == {"track": 1}


def test_build_plan_letterboxes_when_group_is_too_wide_for_crop():
    plan = build_reframe_plan(
        video_path="panel.mp4",
        src_w=1920,
        src_h=1080,
        duration=5.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[
            {"time": 1.0, "label": "person", "bbox": [100, 100, 500, 900]},
            {"time": 1.0, "label": "person", "bbox": [1400, 120, 1800, 920]},
        ],
    )

    assert plan["segments"][0]["strategy"] == "letterbox"
    assert plan["summary"]["letterbox_segments"] == 1
    assert build_reframe_vf(plan).startswith("scale=1080:1920:force_original_aspect_ratio=decrease")


def test_build_plan_uses_center_fallback_without_detections():
    plan = build_reframe_plan(
        video_path="empty.mp4",
        src_w=1920,
        src_h=1080,
        duration=5.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[],
    )

    segment = plan["segments"][0]
    assert segment["strategy"] == "center"
    assert segment["crop"]["x"] == 656
    assert plan["summary"]["fallback_center_segments"] == 1
    assert "no_subject_detection" in segment["warnings"]


def test_filter_complex_uses_trim_concat_for_multiple_scene_segments():
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "start": 0, "end": 3},
            {"scene_id": "scene_002", "start": 3, "end": 6},
        ]
    }
    plan = build_reframe_plan(
        video_path="talk.mp4",
        src_w=1920,
        src_h=1080,
        duration=6.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[
            {"time": 1.0, "label": "face", "bbox": [100, 100, 220, 260]},
            {"time": 4.0, "label": "face", "bbox": [1500, 100, 1650, 260]},
        ],
        scene_plan=scene_plan,
        merge_tolerance_px=0,
    )

    assert len(plan["segments"]) == 2
    assert build_reframe_vf(plan) is None
    fc = build_reframe_filter_complex(plan)
    assert "[0:v]trim=start=0.000:end=3.000" in fc
    assert "concat=n=2:v=1:a=0[vout]" in fc
    assert "crop=608:1080" in fc


def test_emit_markdown_mentions_strategy_table():
    plan = build_reframe_plan(
        video_path="talk.mp4",
        src_w=1920,
        src_h=1080,
        duration=5.0,
        dst_w=1080,
        dst_h=1920,
        platform="douyin",
        detections_payload=[],
    )
    markdown = emit_markdown(plan)

    assert "# Smart Reframe Plan" in markdown
    assert "center crop fallback" in markdown
    assert "multi_export.py --reframe-plan" in markdown


def test_cli_writes_plan_and_markdown(tmp_path):
    detections = tmp_path / "detections.json"
    scenes = tmp_path / "scenes.json"
    out_json = tmp_path / "reframe.json"
    out_md = tmp_path / "reframe.md"
    detections.write_text(
        json.dumps({"detections": [{"time": 1.0, "label": "face", "bbox": [1500, 200, 1650, 360]}]}),
        encoding="utf-8",
    )
    scenes.write_text(
        json.dumps({"scenes": [{"scene_id": "scene_001", "start": 0, "end": 3}]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/smart_reframe.py"),
            "--source-width",
            "1920",
            "--source-height",
            "1080",
            "--duration",
            "3",
            "--detections",
            str(detections),
            "--scene-boundaries",
            str(scenes),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["version"] == "smart_reframe.v1"
    assert payload["segments"][0]["strategy"] == "track"
    assert "Smart Reframe Plan" in out_md.read_text(encoding="utf-8")
