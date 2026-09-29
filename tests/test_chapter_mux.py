"""Embedded MP4 chapters survive a stream-copy roundtrip and bind to their inputs."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chapter_mux.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=2", "-f", "lavfi", "-i", "sine=d=2",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    chapters = tmp_path / "chapters.json"
    chapters.write_text(json.dumps({"version": "chapter_markers.v1", "chapters": [
        {"start": 0, "end": 1, "title": "Intro"},
        {"start": 1, "end": 2, "title": "第二章 = #1"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return source, chapters


def test_mux_roundtrip_and_live_verify(media, tmp_path):
    source, chapters = media
    output, receipt = tmp_path / "chaptered.mp4", tmp_path / "chaptered.json"
    made = call("mux", source, chapters, "--output", output, "--receipt", receipt)
    assert made.returncode == 0, made.stderr
    payload = json.loads(receipt.read_text())
    assert payload["media"]["chapter_count"] == 2
    assert len(payload["media"]["av_stream_sha256"]) == 2
    assert call("verify", receipt).returncode == 0
    probed = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_chapters",
                                                 "-of", "json", str(output)]))
    assert [item["tags"]["title"] for item in probed["chapters"]] == ["Intro", "第二章 = #1"]


@pytest.mark.parametrize("bad", [
    [{"start": 0, "end": 1, "title": "Intro"}, {"start": 0.9, "end": 2, "title": "Overlap"}],
    [{"start": 0.1, "end": 2, "title": "Late"}],
    [{"start": 0, "end": 3, "title": "Too long"}],
    [{"start": 0, "end": 2, "title": ""}],
])
def test_bad_chapters_rejected_before_output(media, tmp_path, bad):
    source, chapters = media
    chapters.write_text(json.dumps({"version": "chapter_markers.v1", "chapters": bad}))
    output = tmp_path / "chaptered.mp4"
    assert call("mux", source, chapters, "--output", output,
                "--receipt", tmp_path / "receipt.json").returncode == 2
    assert not output.exists()


def test_drift_and_tampered_receipt_fail(media, tmp_path):
    source, chapters = media
    output, receipt = tmp_path / "chaptered.mp4", tmp_path / "receipt.json"
    assert call("mux", source, chapters, "--output", output, "--receipt", receipt).returncode == 0
    source_bytes = source.read_bytes()
    source.write_bytes(source_bytes + b"changed")
    assert "changed" in call("verify", receipt).stderr
    source.write_bytes(source_bytes)
    chapters.write_text(chapters.read_text() + "\n")
    assert "changed" in call("verify", receipt).stderr
    chapters.write_text(chapters.read_text()[:-1])
    output.write_bytes(output.read_bytes() + b"changed")
    assert "changed" in call("verify", receipt).stderr
    payload = json.loads(receipt.read_text())
    payload["media"]["chapter_count"] = 3
    receipt.write_text(json.dumps(payload))
    assert "digest differs" in call("verify", receipt).stderr


def test_output_alias_rejected(media, tmp_path):
    source, chapters = media
    alias = tmp_path / "alias.mp4"
    os.link(source, alias)
    result = call("mux", source, chapters, "--output", alias,
                  "--receipt", tmp_path / "receipt.json", "--force")
    assert result.returncode == 2
    assert "overwrite" in result.stderr


def test_video_without_audio_is_supported(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=2", "-c:v", "libx264", str(source)], check=True)
    chapters = tmp_path / "chapters.json"
    chapters.write_text(json.dumps({"version": "chapter_markers.v1", "chapters": [
        {"start": 0, "end": 2, "title": "Silent"}]}))
    receipt = tmp_path / "receipt.json"
    assert call("mux", source, chapters, "--output", tmp_path / "out.mp4",
                "--receipt", receipt).returncode == 0
    assert len(json.loads(receipt.read_text())["media"]["av_stream_sha256"]) == 1
    assert call("verify", receipt).returncode == 0
