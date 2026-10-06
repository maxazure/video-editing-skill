import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from border_crop import analyze, decide, parse_samples, verify  # noqa: E402


def make_video(path, filter_graph=None):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc2=s=160x90:r=8:d=3"]
    if filter_graph:
        command += ["-vf", filter_graph]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)]
    subprocess.run(command, check=True)


def test_detects_persistent_letterbox_and_verifies(tmp_path):
    source = tmp_path / "bars.mp4"
    make_video(source, "pad=160:122:0:16:black")
    report = analyze(str(source))
    decision = report["decision"]
    assert decision["status"] == "ready"
    assert decision["sample_count"] >= 6
    assert decision["crop"][0] == 160
    assert decision["crop"][1] < 122
    assert decision["borders_px"]["top"] >= 8
    assert decision["third_consensus"] == [1.0, 1.0, 1.0]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert verify(str(path))["filter"] == decision["filter"]
    report["decision"]["filter"] = "crop=2:2:0:0"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="report or source changed"):
        verify(str(path))


def test_full_frame_does_not_suggest_crop(tmp_path):
    source = tmp_path / "full.mp4"
    make_video(source)
    assert analyze(str(source))["decision"]["status"] == "no_crop"


def test_detects_persistent_pillarbox(tmp_path):
    source = tmp_path / "side-bars.mp4"
    make_video(source, "pad=200:90:20:0:black")
    decision = analyze(str(source))["decision"]
    assert decision["status"] == "ready"
    assert decision["borders_px"]["left"] >= 8
    assert decision["borders_px"]["right"] >= 8
    assert decision["filter"].startswith("crop=160:90:")


def test_unstable_borders_require_review():
    rects = [[160, 90, 0, 16]] * 4 + [[160, 122, 0, 0]] * 4 + [[160, 90, 0, 16]] * 4
    samples = [{"time_seconds": i, "crop": rect} for i, rect in enumerate(rects)]
    result = decide(samples, 160, 122, 8, 0.8)
    assert result["status"] == "review"
    assert result["filter"] is None
    assert result["third_consensus"] == [1.0, 0.0, 1.0]


def test_parser_rejects_invalid_rectangle():
    assert parse_samples("t:0.000 limit:24 crop=160:90:0:16", 160, 122) == [
        {"time_seconds": 0.0, "crop": [160, 90, 0, 16]}]
    with pytest.raises(ValueError, match="outside"):
        parse_samples("t:0.000 crop=160:90:4:16", 160, 122)


def test_source_drift_and_cli(tmp_path):
    source = tmp_path / "bars.mp4"
    make_video(source, "pad=160:122:0:16:black")
    report = tmp_path / "crop.json"
    script = os.path.join(REPO, "scripts", "border_crop.py")
    run = subprocess.run([sys.executable, script, "analyze", str(source), "--output", str(report)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    check = subprocess.run([sys.executable, script, "verify", str(report)],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    repeat = subprocess.run([sys.executable, script, "analyze", str(source), "--output", str(report)],
                            capture_output=True, text=True)
    assert repeat.returncode == 2
    source.write_bytes(source.read_bytes() + b"modified")
    with pytest.raises(ValueError, match="report or source changed"):
        verify(str(report))
