"""render_final.py blocks export when title triggers a HARD content-guard rule."""
import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_render_rejects_diversion_in_title(tmp_path):
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "title": "加微信 wx123 详聊",
        "clips": [{"video": str(fake_video), "start": 0.0, "end": 1.0}],
    }))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"),
         "--config", str(cfg), "--output", str(tmp_path / "out.mp4")],
        capture_output=True, text=True,
    )
    combined = out.stdout + out.stderr
    assert "Content guard refused" in combined or "diversion" in combined.lower(), (
        f"stdout: {out.stdout}\nstderr: {out.stderr}"
    )
    assert out.returncode != 0


def test_render_rejects_extreme_word_in_title(tmp_path):
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "title": "全网最低价的 AI 课程",
        "clips": [{"video": str(fake_video), "start": 0.0, "end": 1.0}],
    }))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"),
         "--config", str(cfg), "--output", str(tmp_path / "out.mp4")],
        capture_output=True, text=True,
    )
    combined = out.stdout + out.stderr
    assert "Content guard refused" in combined or "extreme" in combined.lower(), (
        f"stdout: {out.stdout}\nstderr: {out.stderr}"
    )


def test_render_no_content_guard_bypasses_check(tmp_path):
    """--no-content-guard should let questionable titles through (still hits other errors)."""
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "title": "全网最低价的 AI 课程",
        "clips": [{"video": str(fake_video), "start": 0.0, "end": 1.0}],
    }))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"),
         "--config", str(cfg), "--output", str(tmp_path / "out.mp4"),
         "--no-content-guard"],
        capture_output=True, text=True,
    )
    combined = out.stdout + out.stderr
    assert "Content guard refused" not in combined, (
        f"--no-content-guard should bypass.\nstderr: {out.stderr}"
    )
