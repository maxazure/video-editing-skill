"""Metadata scrub removes container identifiers while copying decoded streams."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "metadata_scrub.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture
def tagged_media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "tagged.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=2", "-f", "lavfi", "-i", "sine=d=2",
                    "-metadata", "title=Private", "-metadata", "creation_time=2020-01-01T00:00:00Z",
                    "-metadata", "location=+37.0000-122.0000/",
                    "-metadata:s:v:0", "comment=Camera serial", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(source)], check=True)
    source_probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format",
                                                       "-show_streams", "-of", "json", str(source)]))
    assert source_probe["format"]["tags"]["title"] == "Private"
    assert "2020-01-01" in source_probe["format"]["tags"]["creation_time"]
    assert "37.0000" in str(source_probe["format"]["tags"])
    return source


def test_scrub_and_verify(tagged_media, tmp_path):
    output, receipt = tmp_path / "share.mp4", tmp_path / "share.json"
    result = call("scrub", tagged_media, "--output", output, "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["media"]["full_decode"] == "passed"
    assert len(payload["media"]["av_stream_sha256"]) == 2
    assert call("verify", receipt).returncode == 0
    probed = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format",
                                                 "-show_streams", "-of", "json", str(output)]))
    tags = [probed["format"].get("tags", {})] + [s.get("tags", {}) for s in probed["streams"]]
    assert all("Private" not in str(tag) and "37.0000" not in str(tag)
               and "Camera serial" not in str(tag) for tag in tags)


def test_input_and_output_drift_rejected(tagged_media, tmp_path):
    output, receipt = tmp_path / "share.mp4", tmp_path / "share.json"
    assert call("scrub", tagged_media, "--output", output, "--receipt", receipt).returncode == 0
    original = tagged_media.read_bytes()
    tagged_media.write_bytes(original + b"changed")
    assert "changed" in call("verify", receipt).stderr
    tagged_media.write_bytes(original)
    output.write_bytes(output.read_bytes() + b"changed")
    assert "changed" in call("verify", receipt).stderr


def test_source_alias_and_existing_output_rejected(tagged_media, tmp_path):
    alias = tmp_path / "alias.mp4"
    os.link(tagged_media, alias)
    receipt = tmp_path / "share.json"
    assert call("scrub", tagged_media, "--output", alias, "--receipt", receipt,
                "--force").returncode == 2
    assert not receipt.exists()
    output = tmp_path / "share.mp4"
    output.write_bytes(b"existing")
    assert "exists" in call("scrub", tagged_media, "--output", output,
                            "--receipt", receipt).stderr
    assert output.read_bytes() == b"existing"


def test_chapters_are_removed(tagged_media, tmp_path):
    metadata = tmp_path / "chapters.txt"
    metadata.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Secret\n")
    chaptered = tmp_path / "chaptered.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tagged_media), "-f", "ffmetadata",
                    "-i", str(metadata), "-map", "0", "-map_chapters", "1", "-c", "copy",
                    str(chaptered)], check=True)
    output, receipt = tmp_path / "share.mp4", tmp_path / "share.json"
    result = call("scrub", chaptered, "--output", output, "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    assert json.loads(receipt.read_text())["media"]["removed_chapters"] == 1
    assert call("verify", receipt).returncode == 0


def test_silent_mp4_is_supported(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=1", "-metadata", "title=Private",
                    "-c:v", "libx264", str(source)], check=True)
    output, receipt = tmp_path / "share.mp4", tmp_path / "share.json"
    result = call("scrub", source, "--output", output, "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(receipt.read_text())["media"]["av_stream_sha256"]) == 1
    assert call("verify", receipt).returncode == 0
