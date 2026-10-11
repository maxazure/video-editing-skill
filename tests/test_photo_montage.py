import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import photo_montage  # noqa: E402


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required"
)


def _photo(path: Path, color: str) -> Path:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x160",
         "-frames:v", "1", "-threads", "1", "-y", str(path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return path


def _music(path: Path) -> Path:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-y", str(path)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return path


def _render(tmp_path: Path, *, effect: str = "fade", music: bool = False):
    photos = [_photo(tmp_path / f"{index}.png", color) for index, color in enumerate(("red", "green", "blue"))]
    args = [sys.executable, str(REPO / "scripts" / "photo_montage.py"), "render",
            *(str(photo) for photo in photos), "--output", str(tmp_path / "montage.mp4"),
            "--receipt", str(tmp_path / "montage.json"), "--width", "160", "--height", "160",
            "--fps", "24", "--each", "0.5", "--transition", "0.125", "--effect", effect]
    if music:
        args += ["--music", str(_music(tmp_path / "music.wav"))]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return photos, tmp_path / "montage.mp4", tmp_path / "montage.json"


@pytest.mark.parametrize("effect,music", [("fade", False), ("fadewhite", True)])
def test_real_montage_has_exact_frames_and_live_receipt(tmp_path, effect, music):
    photos, output, receipt = _render(tmp_path, effect=effect, music=music)
    payload = json.loads(receipt.read_text())
    assert payload["media"]["frames"] == 36
    assert payload["settings"]["effect"] == effect
    assert abs(payload["media"]["duration_seconds"] - 1.5) < 0.05
    assert (payload["music"] is not None) == music
    assert photo_montage.verify(str(receipt))["status"] == "ready_for_human_review"
    assert output.is_file() and all(photo.is_file() for photo in photos)
    # Sample clear holds between transitions to prove the requested order rendered.
    for second, dominant in ((0.25, 0), (0.75, 1), (1.18, 2)):
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(output),
             "-frames:v", "1", "-vf", "crop=2:2:80:80", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"], capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert len(result.stdout) == 12
        assert result.stdout[dominant] == max(result.stdout[:3])


def test_rejects_source_and_receipt_drift(tmp_path):
    photos, _, receipt = _render(tmp_path)
    original = receipt.read_text()
    payload = json.loads(original)
    payload["settings"]["zoom"] = 1.2
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="digest"):
        photo_montage.verify(str(receipt))
    receipt.write_text(original)
    assert photo_montage.verify(str(receipt))["status"] == "ready_for_human_review"
    photos[0].write_bytes(b"changed")
    with pytest.raises(ValueError, match="photo bytes"):
        photo_montage.verify(str(receipt))


def test_rejects_bad_timing_duplicate_photo_and_source_overwrite(tmp_path):
    photo = _photo(tmp_path / "one.png", "red")
    other = _photo(tmp_path / "two.png", "blue")
    settings = {"width": 160, "height": 160, "fps": 24, "each": 0.5,
                "transition": 0.4, "zoom": 1.08, "effect": "fade"}
    with pytest.raises(ValueError, match="transition"):
        photo_montage.validate_settings([photo, other], settings)
    settings["transition"] = 0.125
    with pytest.raises(ValueError, match="distinct"):
        photo_montage.validate_settings([photo, photo], settings)
    with pytest.raises(ValueError, match="overwrite"):
        photo_montage.safe_output(str(photo), [photo, other], force=True, suffix=".png")
