"""HLS VOD packages remain locally playable and tied to their source."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hls_vod.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=3", "-f", "lavfi", "-i", "sine=d=3",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def package(source, tmp_path):
    directory, receipt = tmp_path / "hls", tmp_path / "hls.json"
    result = call("package", source, "--output-dir", directory, "--receipt", receipt,
                  "--segment-seconds", 2)
    assert result.returncode == 0, result.stderr
    return directory, receipt


def test_package_and_live_verify(source, tmp_path):
    directory, receipt = package(source, tmp_path)
    payload = json.loads(receipt.read_text())
    assert payload["media"]["segment_count"] == 2
    assert payload["media"]["video_frames"] == 60
    assert payload["media"]["has_audio"] is True
    assert set(payload["files"]) == {"index.m3u8", "seg_0000.ts", "seg_0001.ts"}
    assert "#EXT-X-INDEPENDENT-SEGMENTS" in (directory / "index.m3u8").read_text()
    assert call("verify", receipt).returncode == 0


def test_silent_video_supported(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=3", "-c:v", "libx264", str(source)], check=True)
    _, receipt = package(source, tmp_path)
    assert json.loads(receipt.read_text())["media"]["has_audio"] is False
    assert call("verify", receipt).returncode == 0


def test_source_and_segment_drift_fail(source, tmp_path):
    directory, receipt = package(source, tmp_path)
    original = source.read_bytes()
    source.write_bytes(original + b"drift")
    assert "source changed" in call("verify", receipt).stderr
    source.write_bytes(original)
    segment = directory / "seg_0000.ts"
    segment.write_bytes(segment.read_bytes() + b"drift")
    assert "HLS files changed" in call("verify", receipt).stderr


def test_tampered_receipt_and_unsafe_playlist_fail(source, tmp_path):
    directory, receipt = package(source, tmp_path)
    original = receipt.read_text()
    payload = json.loads(original)
    payload["media"]["video_frames"] = 1
    receipt.write_text(json.dumps(payload))
    assert "receipt digest differs" in call("verify", receipt).stderr
    receipt.write_text(original)
    playlist = directory / "index.m3u8"
    playlist.write_text(playlist.read_text().replace("seg_0000.ts", "../outside.ts"))
    assert call("verify", receipt).returncode == 2


def test_bad_input_and_output_collisions(source, tmp_path):
    output, receipt = tmp_path / "hls", tmp_path / "hls.json"
    assert call("package", source, "--output-dir", output, "--receipt", receipt,
                "--segment-seconds", 1).returncode == 2
    assert not output.exists()
    alias = tmp_path / "alias.mp4"
    os.symlink(source, alias)
    assert call("package", alias, "--output-dir", output, "--receipt", receipt).returncode == 2
    package(source, tmp_path)
    assert call("package", source, "--output-dir", output, "--receipt", receipt).returncode == 2
