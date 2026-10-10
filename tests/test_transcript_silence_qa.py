import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from transcript_silence_qa import analyze, segments_from, verify  # noqa: E402


def fixture(tmp_path):
    audio = tmp_path / "speech.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=1", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=44100:d=1", "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", str(audio),
    ], check=True)
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"segments": [
        {"id": 1, "start": 0.2, "end": 0.8, "text": "audible"},
        {"id": 2, "start": 1.2, "end": 1.8, "text": "suspect"},
    ]}), encoding="utf-8")
    return audio, transcript


def test_real_audio_flags_silent_segment_and_verifies(tmp_path):
    audio, transcript = fixture(tmp_path)
    report = analyze(audio, transcript)
    assert report["summary"] == {"total": 2, "review": 1}
    assert [row["status"] for row in report["segments"]] == ["clear", "review"]
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    assert verify(saved) == report
    report["segments"][0]["text"] = "tampered"
    saved.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify(saved)


def test_source_drift_and_invalid_timing(tmp_path):
    audio, transcript = fixture(tmp_path)
    report = analyze(audio, transcript)
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    payload["segments"][0]["text"] = "revised"
    transcript.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify(saved)
    transcript.write_text('{"segments": [{"start": 3, "end": 4, "text": "bad"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        verify(saved)
    with pytest.raises(ValueError, match="invalid"):
        segments_from(transcript, 2)


def test_cli_roundtrip(tmp_path):
    audio, transcript = fixture(tmp_path)
    report = tmp_path / "report.json"
    script = ROOT / "scripts" / "transcript_silence_qa.py"
    subprocess.run([sys.executable, str(script), "analyze", str(audio), str(transcript),
                    "--output", str(report)], check=True)
    subprocess.run([sys.executable, str(script), "verify", str(report)], check=True)
