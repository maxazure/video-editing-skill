import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from scene_boundaries import (  # noqa: E402
    build_scene_plan,
    emit_markdown,
    ffmpeg_scene_command,
    parse_scene_times,
)


def test_parse_scene_times_from_ffmpeg_showinfo_log():
    log = """
    [Parsed_showinfo_1 @ 0x123] n:   0 pts:  21000 pts_time:0.875 pos: -1 fmt:yuv420p
    [Parsed_showinfo_1 @ 0x123] n:   1 pts:  48000 pts_time:2 pos: -1 fmt:yuv420p
    [Parsed_showinfo_1 @ 0x123] n:   2 pts:  48000 pts_time:2.0004 pos: -1 fmt:yuv420p
    """

    assert parse_scene_times(log) == [0.875, 2.0]


def test_build_scene_plan_dedupes_and_writes_scenes():
    plan = build_scene_plan(
        "origin/long.mp4",
        [0.2, 8.0, 8.4, 22.5, 99.0],
        duration=30.0,
        threshold=0.35,
        min_scene_duration=1.0,
    )

    assert plan["version"] == "scene_boundaries.v1"
    assert plan["boundaries"] == [8.0, 22.5]
    assert plan["summary"]["scenes"] == 3
    assert plan["scenes"][1]["start"] == 8.0
    assert plan["scenes"][-1]["end"] == 30.0


def test_emit_markdown_mentions_review_usage():
    plan = build_scene_plan("origin/long.mp4", [8, 22], duration=30, threshold=0.4)
    markdown = emit_markdown(plan)

    assert "# Scene Boundaries" in markdown
    assert "highlight_picker.py --scene-boundaries" in markdown
    assert "scene_002" in markdown


def test_ffmpeg_scene_command_contains_threshold():
    cmd = ffmpeg_scene_command("origin/long.mp4", 0.42)

    assert cmd[0] == "ffmpeg"
    assert "gt(scene,0.4200)" in " ".join(cmd)


def test_cli_parses_saved_ffmpeg_log(tmp_path):
    log_path = tmp_path / "scene.log"
    out_path = tmp_path / "scene_boundaries.json"
    md_path = tmp_path / "scene_boundaries.md"
    log_path.write_text(
        "[Parsed_showinfo_1 @ x] n:0 pts:10 pts_time:8.0\n"
        "[Parsed_showinfo_1 @ x] n:1 pts:20 pts_time:22.5\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/scene_boundaries.py"),
            "origin/long.mp4",
            "--ffmpeg-log",
            str(log_path),
            "--duration",
            "30",
            "--output",
            str(out_path),
            "--markdown",
            str(md_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["boundaries"] == [8.0, 22.5]
    assert "Scene Boundaries" in md_path.read_text(encoding="utf-8")
