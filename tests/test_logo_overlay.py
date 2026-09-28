import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "logo_overlay.py"
FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def png(path, width=40, height=20):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\0" + bytes((255, 0, 0, 255)) * width for _ in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def source(path, audio=True):
    command = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=12:d=1"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(command, check=True)


def render(tmp_path, audio=True, *extra):
    video, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    source(video, audio=audio)
    png(logo)
    output, receipt = tmp_path / "marked.mp4", tmp_path / "receipt.json"
    result = call("render", video, logo, "--margin", 8, "--opacity", 0.5,
                  "--output", output, "--receipt", receipt, *extra)
    return video, logo, output, receipt, result


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg unavailable")
def test_overlay_pixels_audio_and_live_verify(tmp_path):
    video, logo, output, receipt, result = render(tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["media"]["frames"] == 12
    assert payload["media"]["audio_codec"] == "aac"
    assert payload["media"]["full_decode"] == "passed"
    assert call("verify", receipt).returncode == 0
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    def pixel(x, y):
        position = (y * 160 + x) * 3
        return frame[position:position + 3]
    assert pixel(20, 20)[0] < 15
    assert 90 < pixel(140, 75)[0] < 165
    assert pixel(140, 75)[1] < 20


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg unavailable")
def test_muted_source_and_top_left(tmp_path):
    *_, output, receipt, result = render(tmp_path, False, "--corner", "top-left")
    assert result.returncode == 0, result.stderr
    assert json.loads(receipt.read_text())["media"]["audio_codec"] is None
    assert call("verify", receipt).returncode == 0


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg unavailable")
def test_rejects_invalid_opacity_size_and_corner_fit(tmp_path):
    video, logo, output, receipt, _ = render(tmp_path)
    for flags, expected in [(["--opacity", "0"], "opacity"),
                            (["--width-fraction", "0.9"], "width fraction"),
                            (["--margin", "45"], "do not fit")]:
        result = call("render", video, logo, "--output", output, "--receipt", receipt, *flags)
        assert result.returncode == 2
        assert expected in result.stderr


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg unavailable")
def test_collision_and_bound_file_drift(tmp_path):
    video, logo, output, receipt, result = render(tmp_path)
    assert result.returncode == 0, result.stderr
    collision = call("render", video, logo, "--output", output, "--receipt", receipt)
    assert collision.returncode == 2 and "--force" in collision.stderr
    logo.write_bytes(logo.read_bytes() + b"changed")
    drift = call("verify", receipt)
    assert drift.returncode == 2 and "changed" in drift.stderr


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg unavailable")
def test_receipt_tamper_and_output_drift(tmp_path):
    video, logo, output, receipt, result = render(tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    payload["settings"]["opacity"] = 1
    receipt.write_text(json.dumps(payload))
    assert "receipt schema or digest" in call("verify", receipt).stderr
    payload["settings"]["opacity"] = 0.5
    receipt.write_text(json.dumps(payload))
    output.write_bytes(output.read_bytes() + b"changed")
    assert "changed" in call("verify", receipt).stderr


def test_rejects_symlinks_and_hardlink_aliases(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import logo_overlay

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    link = tmp_path / "alias.mp4"
    os.link(source_path, link)
    with pytest.raises(ValueError, match="overwrite"):
        logo_overlay.safe_output(str(link), [source_path], True)
    symlink = tmp_path / "symlink.mp4"
    symlink.symlink_to(source_path)
    with pytest.raises(ValueError, match="symlink"):
        logo_overlay.safe_input(str(symlink))
