import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from shorts_batch import build_shorts_batch, emit_markdown  # noqa: E402


def _highlights():
    return {
        "version": "highlight_candidates.v1",
        "params": {"platform": "douyin"},
        "selected": [
            {
                "id": "highlight_001",
                "rank": 1,
                "start": 8.0,
                "end": 42.0,
                "duration": 34.0,
                "score": 78.5,
                "title_suggestion": "为什么很多团队失败",
                "text": "为什么很多团队做 AI 自动化会失败？关键不是工具。",
                "segment_ids": [2, 3],
                "warnings": [],
                "audio_boundary_snap": {
                    "applied": True,
                    "original_start": 8.2,
                    "original_end": 41.8,
                    "start_delta": -0.2,
                    "end_delta": 0.2,
                },
            },
            {
                "id": "highlight_002",
                "rank": 2,
                "start": 52.0,
                "end": 88.0,
                "duration": 36.0,
                "score": 64.0,
                "title_suggestion": "检查清单",
                "text": "最后给你一个检查清单。",
                "segment_ids": [5, 6],
                "warnings": ["weak opening hook"],
            },
        ],
    }


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_shorts_batch_creates_planned_jobs(tmp_path):
    video = tmp_path / "origin" / "long.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("fake video", encoding="utf-8")

    batch = build_shorts_batch(
        _highlights(),
        video_path=str(video),
        highlights_path="work/highlights.json",
        output_dir=str(tmp_path / "output" / "shorts"),
        render_config_dir=str(tmp_path / "work" / "shorts_render_configs"),
        qa_dir=str(tmp_path / "verify" / "shorts"),
        basename="launch",
        profile="tech_pro",
    )

    assert batch["version"] == "shorts_batch.v1"
    assert batch["status"] == "ready"
    assert batch["summary"]["jobs"] == 2
    assert batch["summary"]["blocking"] == 0
    assert batch["jobs"][0]["id"] == "launch_001"
    assert batch["jobs"][0]["render_config"].endswith("launch_001_render_config.json")
    assert "--primary-speed 1.25" in batch["jobs"][0]["render_shell"]
    assert "--profile tech_pro" in batch["jobs"][0]["render_shell"]
    assert batch["jobs"][1]["warnings"] == ["weak opening hook"]


def test_missing_source_video_blocks_strict_batch(tmp_path):
    batch = build_shorts_batch(
        _highlights(),
        video_path=str(tmp_path / "missing.mp4"),
        highlights_path="work/highlights.json",
    )

    assert batch["status"] == "blocked"
    assert batch["summary"]["blocking"] == 2
    assert "source video not found" in batch["jobs"][0]["blockers"][0]


def test_emit_markdown_lists_render_and_qa_commands(tmp_path):
    video = tmp_path / "origin" / "long.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("fake video", encoding="utf-8")
    batch = build_shorts_batch(_highlights(), video_path=str(video))

    markdown = emit_markdown(batch)

    assert "# Shorts Batch" in markdown
    assert "scripts/render_final.py" in markdown
    assert "scripts/render_qa.py" in markdown
    assert "short_001.mp4" in markdown


def test_cli_writes_batch_markdown_and_render_configs(tmp_path):
    video = tmp_path / "origin" / "long.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("fake video", encoding="utf-8")
    highlights = tmp_path / "work" / "highlight_candidates.json"
    output = tmp_path / "work" / "shorts_batch.json"
    markdown = tmp_path / "work" / "shorts_batch.md"
    render_dir = tmp_path / "work" / "short_configs"
    _write_json(highlights, _highlights())

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "shorts_batch.py"),
            "--highlights",
            str(highlights),
            "--video",
            str(video),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--render-config-dir",
            str(render_dir),
            "--output-dir",
            str(tmp_path / "output" / "shorts"),
            "--qa-dir",
            str(tmp_path / "verify" / "shorts"),
            "--basename",
            "daily",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    batch = json.loads(output.read_text(encoding="utf-8"))
    assert batch["summary"]["planned"] == 2
    config_path = render_dir / "daily_001_render_config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["source"] == "shorts_batch.py"
    assert config["clips"][0]["start"] == 8.0
    assert config["clips"][0]["highlight_score"] == 78.5
    assert config["clips"][0]["audio_boundary_snap"]["applied"] is True
    assert "daily_001" in markdown.read_text(encoding="utf-8")


def test_cli_strict_returns_two_when_source_missing(tmp_path):
    highlights = tmp_path / "work" / "highlight_candidates.json"
    output = tmp_path / "work" / "shorts_batch.json"
    _write_json(highlights, _highlights())

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "shorts_batch.py"),
            "--highlights",
            str(highlights),
            "--video",
            str(tmp_path / "missing.mp4"),
            "--output",
            str(output),
            "--render-config-dir",
            str(tmp_path / "render_configs"),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    batch = json.loads(output.read_text(encoding="utf-8"))
    assert batch["status"] == "blocked"
