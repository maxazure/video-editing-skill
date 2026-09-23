import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "podcast_audiogram.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_audiogram_render_live_verify_and_tamper(tmp_path):
    cover = tmp_path / "cover.png"
    audio = tmp_path / "audio.wav"
    subtitles = tmp_path / "captions.srt"
    output = tmp_path / "output.mp4"
    receipt = tmp_path / "receipt.json"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x31547a:s=160x160:d=1", "-frames:v", "1", str(cover)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "pcm_s16le", str(audio)], check=True)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nFirst line\n\n2\n00:00:01,000 --> 00:00:02,000\nSecond line\n", encoding="utf-8")

    rendered = call("render", audio, cover, subtitles, "--output", output, "--receipt", receipt, "--width", 160, "--height", 284, "--fps", 24)
    assert rendered.returncode == 0, rendered.stderr
    payload = json.loads(receipt.read_text())
    assert payload["settings"]["caption_cues"] == 2
    assert payload["media"]["full_decode"] == "passed"
    verified = call("verify", receipt)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "ready_for_human_review"

    subtitles.write_text(subtitles.read_text().replace("First", "Changed"), encoding="utf-8")
    drift = call("verify", receipt)
    assert drift.returncode == 2
    assert "bound file changed" in drift.stderr


def test_rejects_invalid_or_overlapping_srt(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import podcast_audiogram

    subtitles = tmp_path / "bad.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nOne\n\n2\n00:00:00,900 --> 00:00:02,000\nTwo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps"):
        podcast_audiogram.check_srt(subtitles, 2)


def test_rejects_output_alias_and_symlink(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import podcast_audiogram

    source = tmp_path / "audio.wav"
    source.write_bytes(b"input")
    with pytest.raises(ValueError, match="overwrite"):
        podcast_audiogram.safe_output(str(source), [source], False)
    alias = tmp_path / "alias.wav"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        podcast_audiogram.safe_input(str(alias))


def test_rejects_hardlinked_input(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import podcast_audiogram

    source = tmp_path / "audio.wav"
    source.write_bytes(b"input")
    hardlink = tmp_path / "hardlink.wav"
    os.link(source, hardlink)
    with pytest.raises(ValueError, match="overwrite"):
        podcast_audiogram.safe_output(str(hardlink), [source], True)


def test_rejects_caption_outside_excerpt(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import podcast_audiogram

    subtitles = tmp_path / "late.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\nToo long\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        podcast_audiogram.check_srt(subtitles, 1)
