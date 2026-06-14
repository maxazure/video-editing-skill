import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from video_understanding import (  # noqa: E402
    build_tracks,
    build_understanding_plan,
    emit_markdown,
    normalize_external_detections,
    sample_times,
    scene_id_for_time,
)


def test_sample_times_uses_interval_and_scene_points():
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "start": 0, "end": 4},
            {"scene_id": "scene_002", "start": 4, "end": 10},
        ]
    }

    times = sample_times(10, sample_interval=3, max_frames=12, scene_plan=scene_plan)

    assert 0.5 in times
    assert 2.0 in times
    assert 7.0 in times
    assert times == sorted(times)
    assert len(times) <= 12


def test_sample_times_respects_max_frames():
    times = sample_times(60, sample_interval=1, max_frames=8)

    assert len(times) == 8
    assert times[0] == 0.5
    assert times[-1] > 58


def test_scene_id_for_time_uses_boundaries():
    plan = {"boundaries": [3.0, 7.0]}

    assert scene_id_for_time(1.0, plan, 10.0) == "scene_001"
    assert scene_id_for_time(5.0, plan, 10.0) == "scene_002"
    assert scene_id_for_time(8.0, plan, 10.0) == "scene_003"


def test_normalize_external_detections_accepts_nested_frames_and_normalized_bbox():
    payload = {
        "frames": [
            {
                "frame_id": "frame_0001",
                "time": 1.5,
                "detections": [
                    {"label": "person", "bbox": [0.4, 0.2, 0.6, 0.9], "confidence": 0.88},
                ],
            }
        ]
    }

    detections = normalize_external_detections(payload, width=1920, height=1080, duration=6, source="test")

    assert detections == [
        {
            "id": "det_00001",
            "frame_id": "frame_0001",
            "scene_id": "",
            "time": 1.5,
            "start": None,
            "end": None,
            "label": "person",
            "bbox": [768.0, 216.0, 1152.0, 972.0],
            "confidence": 0.88,
            "source": "test",
        }
    ]


def test_build_tracks_links_overlapping_sampled_boxes():
    detections = [
        {"id": "a", "frame_id": "frame_0001", "time": 1.0, "label": "person", "bbox": [100, 100, 300, 700], "confidence": 0.8},
        {"id": "b", "frame_id": "frame_0002", "time": 3.0, "label": "person", "bbox": [120, 110, 320, 710], "confidence": 0.9},
        {"id": "c", "frame_id": "frame_0002", "time": 3.0, "label": "cell phone", "bbox": [1500, 500, 1600, 650], "confidence": 0.7},
    ]

    tracks = build_tracks(detections, width=1920, height=1080)

    assert len(tracks) == 2
    person = next(track for track in tracks if track["label"] == "person")
    assert person["detections"] == 2
    assert person["frames"] == 2
    assert detections[0]["track_id"] == detections[1]["track_id"]


def test_build_understanding_plan_attaches_detections_and_tags():
    frames = [
        {"frame_id": "frame_0001", "time": 1.0, "path": "", "scene_id": "scene_001", "width": 1920, "height": 1080, "detections": []},
        {"frame_id": "frame_0002", "time": 3.0, "path": "", "scene_id": "scene_001", "width": 1920, "height": 1080, "detections": []},
    ]
    detections = [
        {"id": "det_1", "frame_id": "frame_0001", "scene_id": "scene_001", "time": 1.0, "label": "person", "bbox": [100, 100, 300, 700], "confidence": 0.8, "source": "test"},
        {"id": "det_2", "frame_id": "frame_0002", "scene_id": "scene_001", "time": 3.0, "label": "person", "bbox": [120, 120, 320, 720], "confidence": 0.8, "source": "test"},
    ]

    plan = build_understanding_plan(
        video_path="talk.mp4",
        duration=5,
        width=1920,
        height=1080,
        fps=30,
        frames=frames,
        detections=detections,
        detector="none",
        model="",
        sample_interval=2,
        max_frames=4,
        sample_mode="interval",
    )

    assert plan["version"] == "video_understanding.v1"
    assert plan["summary"]["detections"] == 2
    assert plan["summary"]["tracks"] == 1
    assert plan["frames"][0]["detections"][0]["label"] == "person"
    assert any(tag["tag"] == "person" for tag in plan["scene_tags"])


def test_emit_markdown_mentions_downstream_usage():
    plan = build_understanding_plan(
        video_path="talk.mp4",
        duration=5,
        width=1920,
        height=1080,
        fps=30,
        frames=[],
        detections=[],
        detector="none",
        model="",
        sample_interval=2,
        max_frames=4,
        sample_mode="interval",
    )

    markdown = emit_markdown(plan)

    assert "# Video Understanding" in markdown
    assert "smart_reframe.py --detections" in markdown
    assert "detector_disabled" in markdown


def test_cli_writes_json_and_markdown_without_extracting_frames(tmp_path):
    detections_path = tmp_path / "detections.json"
    out_json = tmp_path / "video_understanding.json"
    out_md = tmp_path / "video_understanding.md"
    detections_path.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "frame_id": "frame_0001",
                        "time": 0.5,
                        "detections": [
                            {"label": "person", "bbox": [100, 100, 320, 820], "confidence": 0.91}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/video_understanding.py"),
            "origin/talk.mp4",
            "--duration",
            "6",
            "--source-width",
            "1920",
            "--source-height",
            "1080",
            "--no-extract",
            "--external-detections",
            str(detections_path),
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
    assert payload["version"] == "video_understanding.v1"
    assert payload["summary"]["detections"] == 1
    assert "Video Understanding" in out_md.read_text(encoding="utf-8")
