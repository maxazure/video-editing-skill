import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gif_preview.py"


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_render_verify_and_source_drift(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "preview.gif"
    receipt = tmp_path / "receipt.json"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=20:d=3", "-c:v", "libx264", str(source)], check=True)

    result = call("render", source, "--start", "0.5", "--duration", "1.5",
                  "--width", "120", "--fps", "10", "--output", output, "--receipt", receipt)
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["media"]["codec"] == "gif"
    assert payload["media"]["width"] == 120
    assert payload["media"]["frames"] == 15
    assert payload["media"]["full_decode"] == "passed"
    verified = call("verify", receipt)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "ready_for_human_review"

    collision = call("render", source, "--start", "0.5", "--duration", "1.5",
                     "--width", "120", "--fps", "10", "--output", output, "--receipt", receipt)
    assert collision.returncode == 2
    assert "--force" in collision.stderr

    too_long = call("render", source, "--start", "0", "--duration", "11",
                    "--output", tmp_path / "long.gif", "--receipt", tmp_path / "long.json")
    assert too_long.returncode == 2
    assert "at most 10 seconds" in too_long.stderr

    output.write_bytes(output.read_bytes() + b"changed")
    output_drift = call("verify", receipt)
    assert output_drift.returncode == 2
    assert "bound source or GIF changed" in output_drift.stderr
    output.write_bytes(output.read_bytes()[:-7])

    source.write_bytes(source.read_bytes() + b"changed")
    drift = call("verify", receipt)
    assert drift.returncode == 2
    assert "bound source or GIF changed" in drift.stderr


def test_rejects_aliases_and_invalid_settings(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import gif_preview

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    hardlink = tmp_path / "hardlink.mp4"
    os.link(source, hardlink)
    with pytest.raises(ValueError, match="overwrite"):
        gif_preview.safe_output(str(hardlink), [source], True)
    alias = tmp_path / "alias.mp4"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        gif_preview.safe_input(str(alias))
