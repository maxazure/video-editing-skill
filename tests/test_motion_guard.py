import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from motion_guard import build_motion_guard, emit_markdown  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


def test_motion_required_storyboard_fails_when_still_heavy():
    plan = {
        "shots": [
            {
                "id": "shot_001",
                "start": 0.0,
                "end": 4.0,
                "duration": 4.0,
                "generation_route": {"primary": "codex_imagegen"},
            },
            {
                "id": "shot_002",
                "start": 4.0,
                "end": 8.0,
                "duration": 4.0,
                "generation_route": {"primary": "codex_imagegen"},
            },
            {
                "id": "shot_003",
                "start": 8.0,
                "end": 10.0,
                "duration": 2.0,
                "generation_route": {"primary": "remotion_hyperframes"},
            },
        ]
    }

    report = build_motion_guard(
        storyboard_plan=plan,
        motion_required=True,
        min_motion_ratio=0.5,
        max_still_run=6.0,
    )

    assert report["status"] == "fail"
    assert report["summary"]["motion_ratio"] == 0.2
    assert report["summary"]["blocking"] == 2
    assert {finding["code"] for finding in report["findings"]} == {
        "low_motion_ratio",
        "long_static_run",
    }


def test_asset_manifest_video_items_pass_motion_guard(tmp_path):
    manifest = {
        "items": [
            {
                "shot_id": "shot_001",
                "time": {"start": 0.0, "end": 3.0, "duration": 3.0},
                "route": "media_library_broll",
                "kind": "broll",
                "status": "ready",
                "resolved_path": str(tmp_path / "broll.mp4"),
            },
            {
                "shot_id": "shot_002",
                "time": {"start": 3.0, "end": 6.0, "duration": 3.0},
                "route": "dreamina_video",
                "kind": "video",
                "status": "ready",
                "resolved_path": str(tmp_path / "generated.mov"),
            },
        ]
    }

    report = build_motion_guard(asset_manifest=manifest, motion_required=True)

    assert report["status"] == "pass"
    assert report["summary"]["motion_ratio"] == 1.0
    assert report["summary"]["blocking"] == 0


def test_render_config_resolves_segment_duration_from_transcript(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps({"segments": [{"id": 7, "start": 2.0, "end": 5.5, "text": "demo"}]}),
        encoding="utf-8",
    )
    config = {
        "clips": [
            {
                "video": "talking.mp4",
                "transcript": str(transcript),
                "segment_id": 7,
            }
        ]
    }

    report = build_motion_guard(render_config=config, render_config_base_dir=str(tmp_path))

    assert report["status"] == "pass"
    assert report["summary"]["total_seconds"] == 3.5
    assert report["segments"][0]["classification"] == "motion"


def test_pipeline_manifest_blocks_on_failed_motion_guard(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "motion_guard.json").write_text(
        json.dumps({"version": "motion_guard.v1", "summary": {"blocking": 1}}),
        encoding="utf-8",
    )

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert manifest["status"] == "blocked"
    assert "motion_guard" in manifest["blocked_gates"]


def test_markdown_and_cli_strict_exit(tmp_path):
    plan_path = tmp_path / "storyboard_plan.json"
    out_json = tmp_path / "motion_guard.json"
    out_md = tmp_path / "motion_guard.md"
    plan_path.write_text(
        json.dumps({
            "shots": [
                {
                    "id": "shot_001",
                    "start": 0.0,
                    "end": 7.0,
                    "duration": 7.0,
                    "generation_route": {"primary": "codex_imagegen"},
                }
            ]
        }),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "motion_guard.py"),
            "--storyboard-plan",
            str(plan_path),
            "--motion-required",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["version"] == "motion_guard.v1"
    md = out_md.read_text(encoding="utf-8")
    assert "# Motion Guard" in md
    assert "| severity | code | segments | message |" in md
    assert "low_motion_ratio" in emit_markdown(report)
