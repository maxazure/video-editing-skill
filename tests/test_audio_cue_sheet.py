import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from audio_cue_sheet import (  # noqa: E402
    build_audio_cue_sheet,
    choose_music_mood,
    emit_markdown,
    scan_audio_assets,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "今天讲一个 AI 自动化 workflow"},
            {"id": 2, "start": 4.0, "end": 6.0, "text": "但是重点来了，这里有一个关键转折"},
            {"id": 3, "start": 9.5, "end": 11.0, "text": "最后这个方法可以提升效率"},
        ]
    }


def test_choose_music_mood_detects_tech_pulse():
    mood = choose_music_mood("AI 自动化 workflow 和系统效率")

    assert mood["mood"] == "tech_pulse"
    assert mood["bpm_range"] == [112, 128]


def test_scan_audio_assets_classifies_music_and_sfx(tmp_path):
    bgm = tmp_path / "media" / "bgm" / "tech-pulse.mp3"
    sfx = tmp_path / "media" / "sfx" / "whoosh.wav"
    _write(bgm, "fake")
    _write(sfx, "fake")

    assets = scan_audio_assets([str(tmp_path)])

    by_path = {os.path.basename(item.path): item.kind for item in assets}
    assert by_path["tech-pulse.mp3"] == "music"
    assert by_path["whoosh.wav"] == "sfx"


def test_build_audio_cue_sheet_uses_local_assets(tmp_path):
    _write(tmp_path / "media" / "bgm" / "tech-pulse.mp3", "fake")
    _write(tmp_path / "media" / "sfx" / "transition-whoosh.wav", "fake")

    sheet = build_audio_cue_sheet(
        transcript=_transcript(),
        asset_roots=[str(tmp_path)],
        require_local_music=True,
        require_local_sfx=True,
    )

    assert sheet["version"] == "audio_cue_sheet.v1"
    assert sheet["summary"]["blocking"] == 0
    assert sheet["summary"]["sfx_cues"] >= 1
    assert sheet["music"][0]["status"] == "ready"
    assert sheet["sfx"][0]["status"] == "ready"
    assert sheet["sfx"][0]["matched_token"] in {"但是", "重点", "关键"}


def test_missing_required_audio_blocks(tmp_path):
    sheet = build_audio_cue_sheet(
        transcript=_transcript(),
        asset_roots=[str(tmp_path)],
        require_local_music=True,
        require_local_sfx=True,
    )

    assert sheet["summary"]["blocking"] >= 2
    assert sheet["music"][0]["status"] == "blocked"
    assert any(cue["status"] == "blocked" for cue in sheet["sfx"])


def test_emit_markdown_lists_actions(tmp_path):
    sheet = build_audio_cue_sheet(transcript=_transcript(), asset_roots=[str(tmp_path)])

    markdown = emit_markdown(sheet)

    assert "# Audio Cue Sheet" in markdown
    assert "## Music" in markdown
    assert "## SFX" in markdown
    assert "Review provider credits" in markdown


def test_cli_writes_json_markdown_and_strict_exit(tmp_path):
    transcript = tmp_path / "transcript.json"
    out_json = tmp_path / "audio_cue_sheet.json"
    out_md = tmp_path / "audio_cue_sheet.md"
    _write(transcript, _transcript())

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "audio_cue_sheet.py"),
            "--transcript",
            str(transcript),
            "--asset-root",
            str(tmp_path / "missing-assets"),
            "--require-local-music",
            "--require-local-sfx",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["blocking"] >= 1


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "audio_cue_sheet.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "audio cue sheet" in result.stdout.lower()
