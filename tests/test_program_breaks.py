import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from program_breaks import _intersections, analyze  # noqa: E402


def make_source(path: Path, *, second_audio: bool = False) -> None:
    audio = path.with_suffix(".wav")
    with wave.open(str(audio), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        samples = bytearray()
        for index in range(5 * 8000):
            seconds = index / 8000
            amplitude = 0 if 1 <= seconds < 2 else int(8000 * math.sin(2 * math.pi * 440 * seconds))
            samples += amplitude.to_bytes(2, "little", signed=True)
        writer.writeframes(samples)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "color=c=red:s=64x64:r=10:d=5", "-i", str(audio)]
    if second_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=550:sample_rate=8000:duration=5"]
    command += ["-map", "0:v:0", "-map", "1:a:0"]
    if second_audio:
        command += ["-map", "2:a:0"]
    command += ["-vf", "drawbox=c=black:t=fill:enable='between(t,1,1.9)+between(t,3,3.9)'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(path)]
    subprocess.run(command, check=True)


def test_overlap_intersection():
    assert _intersections([{"start": 1, "end": 3}], [{"start": 2, "end": 4}]) == [
        {"start": 2, "end": 3}]


def test_detects_only_internal_black_and_silent_break(tmp_path):
    source = tmp_path / "master.mp4"
    make_source(source)
    report = analyze(source)
    assert len(report["black_intervals"]) == 2
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert 1.2 < candidate["break_seconds"] < 1.8
    assert candidate["overlap_seconds"] >= 0.5
    assert report["review_required"] is True


def test_second_audio_track_blocks_break(tmp_path):
    source = tmp_path / "multi.mp4"
    make_source(source, second_audio=True)
    report = analyze(source)
    assert len(report["audio_streams"]) == 2
    assert report["candidates"] == []


def test_cli_verify_and_drift(tmp_path):
    source = tmp_path / "master.mp4"
    make_source(source)
    output = tmp_path / "breaks.json"
    command = [sys.executable, str(REPO / "scripts" / "program_breaks.py")]
    subprocess.run(command + ["analyze", str(source), "--output", str(output)], check=True)
    markdown = output.with_suffix(".md")
    assert output.is_file() and "Candidate time" in markdown.read_text()
    subprocess.run(command + ["verify", str(output)], check=True)
    markdown.write_text(markdown.read_text() + "changed\n")
    assert subprocess.run(command + ["verify", str(output)], capture_output=True).returncode == 1
    markdown.write_text(markdown.read_text().removesuffix("changed\n"))
    data = json.loads(output.read_text())
    data["candidates"][0]["break_seconds"] = 99
    output.write_text(json.dumps(data))
    assert subprocess.run(command + ["verify", str(output)], capture_output=True).returncode == 1


def test_source_drift(tmp_path):
    source = tmp_path / "master.mp4"
    make_source(source)
    report = analyze(source)
    source.write_bytes(source.read_bytes() + b"changed")
    assert analyze(source)["source"] != report["source"]


def test_invalid_and_no_audio_source(tmp_path):
    source = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x64:r=10:d=1", "-y", str(source)], check=True)
    with pytest.raises(ValueError, match="audio stream"):
        analyze(source)
    with pytest.raises(ValueError, match="min-overlap"):
        analyze(source, min_overlap=-1)
