"""render_final.py must abort early when the config title contains internal tokens."""
import json
import os
import subprocess
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_minimal_config(title: str, video_path: str) -> str:
    """Write a minimal config JSON that render_final.py can parse before crashing."""
    config = {
        "title": title,
        "clips": [{"video": video_path, "start": 0.0, "end": 1.0}],
    }
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
    return p


def test_render_rejects_speed_label_in_title(tmp_path):
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00")
    cfg = _make_minimal_config("DAY 58 1.25x AI 失业焦虑", str(fake_video))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"),
         "--config", cfg, "--output", str(tmp_path / "out.mp4")],
        capture_output=True, text=True,
    )
    combined = out.stdout + out.stderr
    assert "InternalTextLeak" in combined or "pipeline-internal" in combined, (
        "Render should refuse a title that contains a speed multiplier.\n"
        f"stdout: {out.stdout}\nstderr: {out.stderr}"
    )
    assert out.returncode != 0


def test_render_rejects_model_name_in_subtitle(tmp_path):
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00")
    config = {
        "title": "DAY 58",
        "subtitle": "powered by whisper-large-v3-turbo",
        "clips": [{"video": str(fake_video), "start": 0.0, "end": 1.0}],
    }
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(config))
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"),
         "--config", str(cfg), "--output", str(tmp_path / "out.mp4")],
        capture_output=True, text=True,
    )
    combined = out.stdout + out.stderr
    assert "InternalTextLeak" in combined or "pipeline-internal" in combined, (
        f"stdout: {out.stdout}\nstderr: {out.stderr}"
    )
    assert out.returncode != 0
