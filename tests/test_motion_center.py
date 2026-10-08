import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from motion_center import WIDTH, HEIGHT, analyze, measure_pair, verify  # noqa: E402


def frame_with_box(x, y, size=8):
    pixels = bytearray(WIDTH * HEIGHT)
    for row in range(y, y + size):
        for col in range(x, x + size):
            pixels[row * WIDTH + col] = 255
    return bytes(pixels)


def test_local_motion_center_and_static():
    first = frame_with_box(10, 20)
    second = frame_with_box(18, 20)
    result = measure_pair(first, second)
    assert result["status"] == "local_motion"
    assert 0.15 < result["center"][0] < 0.22
    assert 0.40 < result["center"][1] < 0.48
    assert measure_pair(first, first)["status"] == "static"


def test_global_change_has_no_crop_focus():
    result = measure_pair(bytes(WIDTH * HEIGHT), bytes([255]) * (WIDTH * HEIGHT))
    assert result["status"] == "global_change"
    assert result["center"] is None


def test_invalid_frame_and_settings(tmp_path):
    with pytest.raises(ValueError, match="frame size"):
        measure_pair(b"", b"")
    source = tmp_path / "empty.mp4"
    source.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="sample-fps"):
        analyze(str(source), sample_fps=0)
    with pytest.raises(ValueError, match="max-coverage"):
        analyze(str(source), max_coverage=1)


def make_motion_video(path):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:s=192x108:r=8:d=3",
        "-f", "lavfi", "-i", "color=white:s=24x24:r=8:d=3",
        "-filter_complex", "[0:v][1:v]overlay=x=20+15*t:y=40:eval=frame:shortest=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
    ], check=True)


def test_real_video_cli_and_live_verify(tmp_path):
    source = tmp_path / "moving.mp4"
    make_motion_video(source)
    report_path = tmp_path / "motion.json"
    script = os.path.join(REPO, "scripts", "motion_center.py")
    run = subprocess.run([sys.executable, script, "analyze", str(source), "--output", str(report_path)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    report = json.loads(report_path.read_text())
    assert report["summary"]["local_motion"] >= 2
    centers = [row["center"][0] for row in report["samples"] if row["center"]]
    assert centers == sorted(centers)
    assert verify(str(report_path))["status"] == "verified"
    repeat = subprocess.run([sys.executable, script, "analyze", str(source), "--output", str(report_path)],
                            capture_output=True, text=True)
    assert repeat.returncode == 2
    report["samples"][0]["center"] = [0.99, 0.99]
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="report or source changed"):
        verify(str(report_path))


def test_source_drift(tmp_path):
    source = tmp_path / "moving.mp4"
    make_motion_video(source)
    report_path = tmp_path / "motion.json"
    report_path.write_text(json.dumps(analyze(str(source))))
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="report or source changed"):
        verify(str(report_path))
