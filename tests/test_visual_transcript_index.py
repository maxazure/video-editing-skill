import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "visual_transcript_index.py"
sys.path.insert(0, str(ROOT / "scripts"))
from visual_transcript_index import make_index, markdown  # noqa: E402


@pytest.fixture
def sources(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"sample video bytes")
    images = []
    for number in range(2):
        image = tmp_path / f"frame-{number}.png"
        image.write_bytes(f"image {number}".encode())
        images.append(image)
    keyframes = tmp_path / "keyframes.json"
    keyframes.write_text(json.dumps({
        "video": str(video), "duration": 10,
        "keyframes": [{"timestamp": 1, "path": str(images[0])},
                      {"timestamp": 8, "path": str(images[1])}],
    }), encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"segments": [
        {"id": "intro", "start": 0.5, "end": 1.5, "text": "开场"},
        {"id": "middle", "start": 4.5, "end": 5.5, "text": "中段"},
        {"id": "ending", "start": 7.5, "end": 8.5, "text": "结尾"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return video, images, keyframes, transcript


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_alignment_and_bounded_windows(sources):
    _, _, keyframes, transcript = sources
    report = make_index(keyframes, transcript, radius=1)
    assert [[part["id"] for part in row["speech"]] for row in report["frames"]] == [["intro"], ["ending"]]
    assert report["frames"][0]["speech_window"] == {"start": 0.0, "end": 2.0}
    assert len(report["video_sha256"]) == 64
    assert len(report["frames"][0]["image_sha256"]) == 64


def test_build_verify_and_source_drift(sources, tmp_path):
    _, images, keyframes, transcript = sources
    output, notes = tmp_path / "index.json", tmp_path / "index.md"
    built = run_cli("build", keyframes, transcript, "--radius", 1, "--output", output, "--markdown", notes)
    assert built.returncode == 0, built.stderr
    assert "开场" in notes.read_text(encoding="utf-8")
    assert run_cli("verify", output).returncode == 0
    notes.write_text(notes.read_text(encoding="utf-8") + "invented\n", encoding="utf-8")
    assert run_cli("verify", output).returncode == 2
    notes.write_text(notes.read_text(encoding="utf-8").removesuffix("invented\n"), encoding="utf-8")
    assert run_cli("verify", output).returncode == 0
    images[0].write_bytes(b"changed")
    assert run_cli("verify", output).returncode == 2


def test_report_tamper_and_output_protection(sources, tmp_path):
    _, _, keyframes, transcript = sources
    output, notes = tmp_path / "index.json", tmp_path / "index.md"
    assert run_cli("build", keyframes, transcript, "--output", output, "--markdown", notes).returncode == 0
    assert run_cli("build", keyframes, transcript, "--output", output, "--markdown", notes).returncode == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    report["frames"][0]["speech"][0]["text"] = "invented"
    output.write_text(json.dumps(report), encoding="utf-8")
    assert run_cli("verify", output).returncode == 2
    assert run_cli("build", keyframes, transcript, "--output", transcript,
                   "--markdown", tmp_path / "another.md").returncode == 2


def test_invalid_timing_and_segment_limit(sources):
    _, _, keyframes, transcript = sources
    metadata = json.loads(keyframes.read_text(encoding="utf-8"))
    metadata["keyframes"][1]["timestamp"] = 0.5
    keyframes.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="unordered timestamp"):
        make_index(keyframes, transcript)
    with pytest.raises(ValueError, match="radius"):
        make_index(keyframes, transcript, radius=float("nan"))
    with pytest.raises(ValueError, match="max-segments"):
        make_index(keyframes, transcript, max_segments=13)


def test_truncation_is_explicit(sources):
    _, _, keyframes, transcript = sources
    report = make_index(keyframes, transcript, radius=5, max_segments=1)
    assert report["frames"][0]["speech_total"] == 2
    assert report["frames"][0]["speech_truncated"] is True


def test_markdown_escapes_source_text_and_image_url(sources):
    _, images, keyframes, transcript = sources
    renamed = images[0].with_name("frame with space.png")
    images[0].rename(renamed)
    metadata = json.loads(keyframes.read_text(encoding="utf-8"))
    metadata["keyframes"][0]["path"] = str(renamed)
    keyframes.write_text(json.dumps(metadata), encoding="utf-8")
    words = json.loads(transcript.read_text(encoding="utf-8"))
    words["segments"][0]["text"] = "<img src=x>"
    transcript.write_text(json.dumps(words), encoding="utf-8")
    notes = markdown(make_index(keyframes, transcript))
    assert "frame%20with%20space.png" in notes
    assert "&lt;img src=x&gt;" in notes
