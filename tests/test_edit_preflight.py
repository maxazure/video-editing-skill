import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from edit_preflight import build_preflight, emit_markdown  # noqa: E402


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_bytes(value)


def test_direct_start_end_render_config_passes(tmp_path):
    video = tmp_path / "origin" / "talking.mp4"
    image = tmp_path / "work" / "chapter.png"
    _write(video, b"fake video")
    _write(image, b"fake image")
    config = tmp_path / "work" / "render_config.json"
    _write(config, {
        "clips": [
            {"video": "../origin/talking.mp4", "start": 0.0, "end": 4.0, "text": "hello"},
            {"video": "../origin/talking.mp4", "start": 6.0, "end": 9.5, "text": "world"},
        ],
        "image_overlays": [
            {"image": "chapter.png", "start": 1.0, "duration": 1.5},
        ],
    })

    report = build_preflight(render_config=str(config))

    assert report["status"] == "ready"
    assert report["summary"]["render_config_clips"] == 2
    assert report["summary"]["timeline_duration"] == 7.5
    assert report["checks"] == []


def test_transcript_segment_id_is_resolved(tmp_path):
    video = tmp_path / "talking.mp4"
    transcript = tmp_path / "transcript.json"
    _write(video, b"fake video")
    _write(transcript, {
        "segments": [
            {"id": 7, "start": 2.0, "end": 5.0, "text": "picked segment"},
        ],
    })
    config = tmp_path / "render_config.json"
    _write(config, {
        "clips": [
            {"video": "talking.mp4", "transcript": "transcript.json", "segment_id": 7},
        ],
    })

    report = build_preflight(render_config=str(config))

    assert report["status"] == "ready"
    assert report["summary"]["timeline_duration"] == 3.0


def test_missing_segment_and_overlay_block(tmp_path):
    video = tmp_path / "talking.mp4"
    transcript = tmp_path / "transcript.json"
    _write(video, b"fake video")
    _write(transcript, {"segments": [{"id": 1, "start": 0, "end": 2, "text": "ok"}]})
    config = tmp_path / "render_config.json"
    _write(config, {
        "clips": [
            {"video": "talking.mp4", "transcript": "transcript.json", "segment_id": 99},
        ],
        "pip_overlays": [
            {"video": "missing-facecam.mp4", "start": 0, "end": 2, "source_start": -1},
        ],
    })

    report = build_preflight(render_config=str(config))
    codes = {item["code"] for item in report["checks"]}

    assert report["status"] == "blocked"
    assert "segment_not_found" in codes
    assert "missing_file" in codes
    assert "negative_source_start" in codes


def test_enrich_plan_warns_for_unlinked_advisory_asset(tmp_path):
    video = tmp_path / "talking.mp4"
    _write(video, b"fake video")
    config = tmp_path / "render_config.json"
    plan = tmp_path / "enrich_plan.json"
    _write(config, {"clips": [{"video": "talking.mp4", "start": 0, "end": 3, "text": "ok"}]})
    _write(plan, {"imagegen": [{"prompt_en": "visual metaphor", "timing_seconds": 1.0, "duration": 1.0}]})

    report = build_preflight(render_config=str(config), enrich_plans=[str(plan)])

    assert report["status"] == "warn"
    assert any(item["code"] == "unlinked_advisory_asset" for item in report["checks"])


def test_focus_pixel_coordinate_without_source_size_blocks(tmp_path):
    video = tmp_path / "talking.mp4"
    _write(video, b"fake video")
    config = tmp_path / "render_config.json"
    _write(config, {
        "clips": [{"video": "talking.mp4", "start": 0, "end": 3, "text": "ok"}],
        "focus_events": [{"start": 0.5, "end": 1.5, "x": 500, "y": 300}],
    })

    report = build_preflight(render_config=str(config))

    assert report["status"] == "blocked"
    assert any(item["code"] == "pixel_coordinate_without_source_size" for item in report["checks"])


def test_cli_writes_json_markdown_and_strict_returns_two_on_warning(tmp_path):
    video = tmp_path / "talking.mp4"
    _write(video, b"fake video")
    config = tmp_path / "render_config.json"
    plan = tmp_path / "enrich_plan.json"
    out_json = tmp_path / "edit_preflight.json"
    out_md = tmp_path / "edit_preflight.md"
    _write(config, {"clips": [{"video": "talking.mp4", "start": 0, "end": 2, "text": "ok"}]})
    _write(plan, {"broll": [{"start": 0.4, "end": 1.0, "reason": "needs asset"}]})

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/edit_preflight.py"),
            "--config",
            str(config),
            "--enrich-plan",
            str(plan),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert result.returncode == 2
    assert payload["version"] == "edit_preflight.v1"
    assert payload["status"] == "warn"
    assert "Edit Preflight" in out_md.read_text(encoding="utf-8")


def test_emit_markdown_lists_next_actions(tmp_path):
    report = {
        "status": "blocked",
        "summary": {"blocking": 1, "warnings": 0, "render_config_clips": 1, "timeline_duration": 2.0},
        "checks": [
            {
                "severity": "block",
                "code": "missing_file",
                "source": "render_config.clips[1].video",
                "message": "Linked file does not exist.",
                "path": str(tmp_path / "missing.mp4"),
            }
        ],
        "next_actions": ["Relink missing media."],
    }

    md = emit_markdown(report)

    assert "| block | `missing_file` | `render_config.clips[1].video` |" in md
    assert "- Relink missing media." in md
