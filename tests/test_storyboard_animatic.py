import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import storyboard_animatic  # noqa: E402
from storyboard_plan import build_storyboard_plan  # noqa: E402


def _transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "打开产品页面"},
            {"id": 2, "start": 1.0, "end": 2.0, "text": "演示自动化流程"},
            {"id": 3, "start": 2.0, "end": 3.0, "text": "评论区告诉我"},
        ]
    }


def _make_panel(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-y",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _fixture(tmp_path, *, shots=3):
    storyboard = tmp_path / "work" / "storyboard_plan.json"
    storyboard.parent.mkdir(parents=True, exist_ok=True)
    storyboard.write_text(
        json.dumps(
            build_storyboard_plan(_transcript(), max_shots=shots, target_aspect="16:9"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    colors = ["red", "green", "blue"]
    panels = []
    for index in range(shots):
        path = tmp_path / "work" / "storyboard" / f"shot_{index + 1:03d}.png"
        _make_panel(path, colors[index])
        panels.append(f"shot_{index + 1:03d}={path}")
    return storyboard, panels


def _plan(tmp_path, *, shots=3):
    storyboard, panels = _fixture(tmp_path, shots=shots)
    plan = storyboard_animatic.build_plan(
        str(storyboard),
        panels,
        "verify/storyboard_animatic.mp4",
        project_dir=str(tmp_path),
        width=320,
        height=180,
        fps=24,
    )
    plan_path = tmp_path / "work" / "storyboard_animatic.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return storyboard, panels, plan, plan_path


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_plan_binds_exact_panels_and_storyboard_timing(tmp_path):
    _, _, plan, _ = _plan(tmp_path)

    assert plan["version"] == storyboard_animatic.VERSION
    assert plan["settings"]["duration_seconds"] == 3.0
    assert [row["shot_id"] for row in plan["panels"]] == [
        "shot_001",
        "shot_002",
        "shot_003",
    ]
    assert [row["display_duration"] for row in plan["timeline"]] == [1.0, 1.0, 1.0]
    assert plan["plan_id"].startswith("animatic_plan_")
    assert storyboard_animatic.PENDING_APPLY in plan["blockers"]
    assert storyboard_animatic.PENDING_CONFIRM in plan["blockers"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_plan_requires_one_distinct_known_panel_per_shot(tmp_path):
    storyboard, panels = _fixture(tmp_path)

    with pytest.raises(ValueError, match="missing panel mappings"):
        storyboard_animatic.build_plan(
            str(storyboard), panels[:-1], "verify/animatic.mp4", project_dir=str(tmp_path)
        )
    with pytest.raises(ValueError, match="unknown panel mappings"):
        storyboard_animatic.build_plan(
            str(storyboard),
            [*panels, f"shot_999={panels[0].split('=', 1)[1]}"],
            "verify/animatic.mp4",
            project_dir=str(tmp_path),
        )
    same_path = panels[0].split("=", 1)[1]
    with pytest.raises(ValueError, match="distinct panel"):
        storyboard_animatic.build_plan(
            str(storyboard),
            [f"shot_001={same_path}", f"shot_002={same_path}", f"shot_003={same_path}"],
            "verify/animatic.mp4",
            project_dir=str(tmp_path),
        )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_filter_graph_preserves_order_and_burns_timing_labels(tmp_path):
    _, _, plan, _ = _plan(tmp_path)
    graph = storyboard_animatic.build_filter_graph(plan)

    assert graph.index("shot_001") < graph.index("shot_002") < graph.index("shot_003")
    assert "t=0.00-1.00s" in graph
    assert "scale=320:180:force_original_aspect_ratio=decrease" in graph
    assert "concat=n=3:v=1:a=0[vout]" in graph


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_apply_renders_valid_animatic_then_confirm_makes_it_ready(tmp_path):
    _, _, _, plan_path = _plan(tmp_path)

    applied = storyboard_animatic.apply_plan(str(plan_path))
    output = tmp_path / "verify" / "storyboard_animatic.mp4"

    assert output.exists()
    assert applied["application"]["validation"]["full_decode_checked"] is True
    assert applied["application"]["output"]["media"]["video_codec"] == "h264"
    assert applied["application"]["output"]["media"]["width"] == 320
    assert storyboard_animatic.PENDING_CONFIRM in applied["blockers"]

    confirmed = storyboard_animatic.confirm_plan(
        str(plan_path),
        reviewed_by="Jay",
        note="Watched the complete animatic at normal speed and checked every transition.",
        full_playback="completed",
        checks={
            "shot_order": "pass",
            "timing_rhythm": "pass",
            "panel_legibility": "pass",
            "visual_continuity": "pass",
            "audio_sync": "not_applicable",
        },
    )
    verified = storyboard_animatic.verify_plan(confirmed)

    assert verified["status"] == "ready"
    assert verified["summary"]["blocking"] == 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_live_verify_rejects_storyboard_and_panel_drift(tmp_path):
    storyboard, panels, plan, _ = _plan(tmp_path)

    current = storyboard_animatic.verify_plan(plan)
    storyboard.write_text(storyboard.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale_storyboard = storyboard_animatic.verify_plan(plan)
    storyboard.write_text(storyboard.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
    panel_path = Path(panels[0].split("=", 1)[1])
    panel_path.write_bytes(panel_path.read_bytes() + b"drift")
    stale_panel = storyboard_animatic.verify_plan(plan)

    assert current["summary"]["blocking"] == 2
    assert "storyboard bytes changed" in stale_storyboard["blockers"]
    assert any("panel bytes or metadata changed" in item for item in stale_panel["blockers"])


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_failed_or_incomplete_review_stays_blocked(tmp_path):
    _, _, _, plan_path = _plan(tmp_path, shots=2)
    storyboard_animatic.apply_plan(str(plan_path))

    reviewed = storyboard_animatic.confirm_plan(
        str(plan_path),
        reviewed_by="Jay",
        note="Second shot is held too long.",
        full_playback="not_completed",
        checks={
            "shot_order": "pass",
            "timing_rhythm": "fail",
            "panel_legibility": "pass",
            "visual_continuity": "pass",
            "audio_sync": "not_applicable",
        },
    )

    assert reviewed["status"] == "blocked"
    assert "animatic review requires completed full playback" in reviewed["blockers"]
    assert "animatic review failed: timing_rhythm" in reviewed["blockers"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_cli_plan_apply_and_strict_verify_round_trip(tmp_path):
    storyboard, panels = _fixture(tmp_path, shots=2)
    script = os.path.join(REPO, "scripts", "storyboard_animatic.py")
    plan_path = tmp_path / "work" / "storyboard_animatic.json"
    command = [
        sys.executable,
        script,
        "plan",
        "--project-dir",
        str(tmp_path),
        "--storyboard",
        str(storyboard),
    ]
    for panel in panels:
        command.extend(["--panel", panel])
    command.extend(
        [
            "--width",
            "320",
            "--height",
            "180",
            "--delivery",
            "verify/storyboard_animatic.mp4",
            "--output",
            str(plan_path),
            "--markdown",
            "work/storyboard_animatic.md",
        ]
    )
    planned = subprocess.run(command, capture_output=True, text=True)
    applied = subprocess.run(
        [sys.executable, script, "apply", str(plan_path)], capture_output=True, text=True
    )
    pending = subprocess.run(
        [sys.executable, script, "verify", str(plan_path), "--strict"],
        capture_output=True,
        text=True,
    )

    assert planned.returncode == 0, planned.stderr
    assert applied.returncode == 0, applied.stderr
    assert pending.returncode == 2
    assert storyboard_animatic.PENDING_CONFIRM in pending.stdout


def test_infer_dimensions_matches_storyboard_aspect():
    assert storyboard_animatic.infer_dimensions("9:16") == (720, 1280)
    assert storyboard_animatic.infer_dimensions("16:9") == (1280, 720)
    assert storyboard_animatic.infer_dimensions("3:4") == (960, 1280)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_explicit_canvas_aspect_mismatch_is_visible_as_warning(tmp_path):
    storyboard, panels = _fixture(tmp_path, shots=2)
    plan = storyboard_animatic.build_plan(
        str(storyboard),
        panels,
        "verify/storyboard_animatic.mp4",
        project_dir=str(tmp_path),
        width=180,
        height=320,
    )

    assert "animatic canvas aspect differs from the storyboard target" in plan["warnings"]
