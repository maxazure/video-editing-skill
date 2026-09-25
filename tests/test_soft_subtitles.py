import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "soft_subtitles.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "source.mp4"
    srt = tmp_path / "captions.srt"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=2", "-f", "lavfi", "-i", "sine=d=2",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    srt.write_text("1\n00:00:00,100 --> 00:00:00,800\n你好 world\n\n"
                   "2\n00:00:01,000 --> 00:00:01,800\n第二句\n", encoding="utf-8")
    return source, srt


def test_mux_verify_stream_copy_and_subtitle_roundtrip(media, tmp_path):
    source, srt = media
    output, receipt = tmp_path / "soft.mp4", tmp_path / "soft.json"
    result = call("mux", source, srt, "--language", "zho", "--output", output, "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["media"]["cue_count"] == 2
    assert len(payload["media"]["av_stream_sha256"]) == 2
    verified = call("verify", receipt)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "ready_for_human_review"


def test_source_and_subtitle_drift_block_verification(media, tmp_path):
    source, srt = media
    receipt = tmp_path / "soft.json"
    result = call("mux", source, srt, "--output", tmp_path / "soft.mp4", "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    srt.write_text(srt.read_text() + "\n", encoding="utf-8")
    assert "changed" in call("verify", receipt).stderr
    srt.write_text(srt.read_text()[:-1], encoding="utf-8")
    source.write_bytes(source.read_bytes() + b"change")
    assert "changed" in call("verify", receipt).stderr


def test_output_drift_blocks_verification(media, tmp_path):
    source, srt = media
    output, receipt = tmp_path / "soft.mp4", tmp_path / "soft.json"
    assert call("mux", source, srt, "--output", output, "--receipt", receipt).returncode == 0
    output.write_bytes(output.read_bytes() + b"change")
    assert "changed" in call("verify", receipt).stderr


def test_receipt_tamper_blocks_verification(media, tmp_path):
    source, srt = media
    receipt = tmp_path / "soft.json"
    assert call("mux", source, srt, "--output", tmp_path / "soft.mp4", "--receipt", receipt).returncode == 0
    payload = json.loads(receipt.read_text())
    payload["language"] = "eng"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    assert "receipt schema or digest" in call("verify", receipt).stderr


def test_video_without_audio_is_supported(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    source, srt = tmp_path / "silent.mp4", tmp_path / "captions.srt"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=1", "-c:v", "libx264", str(source)], check=True)
    srt.write_text("1\n00:00:00,100 --> 00:00:00,800\nSilent title\n", encoding="utf-8")
    receipt = tmp_path / "soft.json"
    result = call("mux", source, srt, "--output", tmp_path / "soft.mp4", "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(receipt.read_text())["media"]["av_stream_sha256"]) == 1
    assert call("verify", receipt).returncode == 0


@pytest.mark.parametrize("contents", [
    "1\n00:00:00,100 --> 00:00:02,500\ntoo late\n",
    "1\n00:00:00,100 --> 00:00:00,800\nfirst\n\n2\n00:00:00,700 --> 00:00:01,000\noverlap\n",
    "1\n00:00:00,100 --> 00:00:00,800\n\n",
    "2\n00:00:00,100 --> 00:00:00,800\nwrong number\n",
])
def test_invalid_srt_rejected(media, tmp_path, contents):
    source, srt = media
    srt.write_text(contents, encoding="utf-8")
    result = call("mux", source, srt, "--output", tmp_path / "soft.mp4",
                  "--receipt", tmp_path / "soft.json")
    assert result.returncode == 2
    assert not (tmp_path / "soft.mp4").exists()


def test_output_aliases_and_existing_output_rejected(media, tmp_path):
    source, srt = media
    alias = tmp_path / "alias.mp4"
    os.link(source, alias)
    result = call("mux", source, srt, "--output", alias, "--receipt", tmp_path / "soft.json", "--force")
    assert result.returncode == 2
    assert "overwrite" in result.stderr
    output = tmp_path / "soft.mp4"
    output.write_bytes(b"existing")
    assert "--force" in call("mux", source, srt, "--output", output,
                             "--receipt", tmp_path / "soft.json").stderr


def test_source_with_existing_subtitle_is_rejected(media, tmp_path):
    source, srt = media
    with_sub = tmp_path / "with-sub.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(srt),
                    "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text", str(with_sub)], check=True)
    result = call("mux", with_sub, srt, "--output", tmp_path / "soft.mp4",
                  "--receipt", tmp_path / "soft.json")
    assert result.returncode == 2
    assert "no other streams" in result.stderr
