import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from scene_boundaries import (  # noqa: E402
    adaptive_scene_evidence,
    build_scene_plan,
    emit_markdown,
    ffmpeg_scene_command,
    ffmpeg_scene_scores_command,
    parse_scene_scores,
    parse_scene_times,
)


def test_parse_scene_times_from_ffmpeg_showinfo_log():
    log = """
    [Parsed_showinfo_1 @ 0x123] n:   0 pts:  21000 pts_time:0.875 pos: -1 fmt:yuv420p
    [Parsed_showinfo_1 @ 0x123] n:   1 pts:  48000 pts_time:2 pos: -1 fmt:yuv420p
    [Parsed_showinfo_1 @ 0x123] n:   2 pts:  48000 pts_time:2.0004 pos: -1 fmt:yuv420p
    """

    assert parse_scene_times(log) == [0.875, 2.0]


def test_parse_scene_scores_from_ffmpeg_metadata_log():
    log = """
    [Parsed_metadata_1 @ x] frame:0 pts:0 pts_time:0
    [Parsed_metadata_1 @ x] lavfi.scene_score=0.000000
    [Parsed_metadata_1 @ x] frame:1 pts:10 pts_time:1.25
    [Parsed_metadata_1 @ x] lavfi.scene_score=4.25e-1
    """

    assert parse_scene_scores(log) == [
        {"time": 0.0, "score": 0.0},
        {"time": 1.25, "score": 0.425},
    ]


def test_adaptive_scene_evidence_keeps_local_spike():
    samples = [
        {"time": index, "score": score}
        for index, score in enumerate([0.02, 0.08, 0.09, 0.8, 0.1, 0.08, 0.03])
    ]

    evidence = adaptive_scene_evidence(samples, adaptive_threshold=3.0, window_width=2, min_scene_score=0.15)

    assert [item["time"] for item in evidence] == [3.0]
    assert evidence[0]["adaptive_ratio"] > 8
    assert evidence[0]["score"] == 0.8


def test_adaptive_scene_evidence_rejects_sustained_camera_motion():
    samples = [
        {"time": index, "score": score}
        for index, score in enumerate([0.2, 0.21, 0.19, 0.22, 0.2, 0.18, 0.21])
    ]

    assert adaptive_scene_evidence(samples, adaptive_threshold=3.0, window_width=2, min_scene_score=0.15) == []


def test_adaptive_scene_evidence_handles_zero_neighbor_average():
    samples = [
        {"time": index, "score": score}
        for index, score in enumerate([0.0, 0.0, 0.4, 0.0, 0.0])
    ]

    evidence = adaptive_scene_evidence(samples, window_width=2, min_scene_score=0.15)

    assert evidence[0]["adaptive_ratio"] == 255.0


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


def test_build_adaptive_plan_keeps_cut_evidence():
    evidence = [
        {"time": 8.0, "score": 0.55, "adaptive_ratio": 5.5, "local_average": 0.1},
        {"time": 8.4, "score": 0.4, "adaptive_ratio": 4.0, "local_average": 0.1},
    ]
    plan = build_scene_plan(
        "origin/long.mp4",
        [item["time"] for item in evidence],
        duration=30,
        threshold=0.35,
        method="adaptive",
        boundary_evidence=evidence,
    )

    assert plan["params"]["mode"] == "adaptive"
    assert plan["params"]["method"] == "ffmpeg_scene_score_adaptive"
    assert plan["boundary_evidence"] == [evidence[0]]


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


def test_ffmpeg_scene_scores_command_prints_every_frame_score():
    cmd = ffmpeg_scene_scores_command("origin/long.mp4")

    assert "gte(scene,0)" in " ".join(cmd)
    assert "metadata=print:key=lavfi.scene_score" in " ".join(cmd)


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


def test_cli_adaptive_mode_parses_saved_score_log(tmp_path):
    log_path = tmp_path / "scene-scores.log"
    out_path = tmp_path / "scene_boundaries.json"
    lines = []
    for index, score in enumerate([0.0, 0.02, 0.4, 0.01, 0.0]):
        lines.append(f"[Parsed_metadata_1 @ x] frame:{index} pts:{index} pts_time:{index}")
        lines.append(f"[Parsed_metadata_1 @ x] lavfi.scene_score={score}")
    log_path.write_text("\n".join(lines), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/scene_boundaries.py"),
            "origin/long.mp4",
            "--method",
            "adaptive",
            "--ffmpeg-log",
            str(log_path),
            "--duration",
            "5",
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["boundaries"] == [2.0]
    assert payload["boundary_evidence"][0]["adaptive_ratio"] > 10
