import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from localization_pack import build_pack, load_source, load_translations  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_load_translations_accepts_mapping_and_segment_lists(tmp_path):
    mapping = tmp_path / "translations.json"
    mapping.write_text(json.dumps({
        "translations": {
            "loc_001": "Do not rush the edit.",
            "2": {"target_text": "Check the subtitles first."},
        },
        "segments": [
            {"id": "ignored", "target_text": "ignored"},
        ],
    }), encoding="utf-8")

    translations = load_translations(str(mapping))

    assert translations["loc_001"] == "Do not rush the edit."
    assert translations["2"] == "Check the subtitles first."


def test_build_pack_flags_missing_translation_when_required(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {
                "id": "s1",
                "speaker": "host",
                "start": 0.0,
                "end": 2.0,
                "text": "先别急着剪。",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    clips, meta = load_source(str(transcript), source_type="transcript")

    pack = build_pack(
        clips,
        meta,
        source_path=str(transcript),
        source_type="transcript",
        source_language="zh",
        target_language="en",
        translations={},
        voice_map={},
        default_voice="narrator",
        mode="source",
        speed=1.0,
        offset=0.0,
        max_chars=42,
        max_duration=4.5,
        max_cps=18,
        dubbing=True,
        require_translations=True,
        require_voices=False,
        fail_on_readability=False,
        max_tts_speed=1.25,
    )

    assert pack["version"] == "localization_pack.v1"
    assert pack["summary"]["missing_translations"] == 1
    assert pack["summary"]["blocking"] == 1
    assert pack["segments"][0]["review_status"] == "blocked"
    assert pack["dubbing_tasks"][0]["voice"] == "narrator"


def test_build_pack_estimates_tts_speed_and_voice_map(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {
                "id": "s1",
                "speaker": "guest",
                "start": 0.0,
                "end": 1.5,
                "text": "短句",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    clips, meta = load_source(str(transcript), source_type="transcript")

    pack = build_pack(
        clips,
        meta,
        source_path=str(transcript),
        source_type="transcript",
        source_language="zh",
        target_language="en",
        translations={"loc_001": "This translation is intentionally much too long for the tiny timing window."},
        voice_map={"guest": "en-US-GuyNeural"},
        default_voice="default",
        mode="source",
        speed=1.0,
        offset=0.0,
        max_chars=42,
        max_duration=4.5,
        max_cps=18,
        dubbing=True,
        require_translations=True,
        require_voices=True,
        fail_on_readability=True,
        max_tts_speed=1.1,
    )

    segment = pack["segments"][0]
    assert segment["voice"] == "en-US-GuyNeural"
    assert "target_over_max_chars" in segment["warnings"]
    assert "target_cps_high" in segment["warnings"]
    assert "tts_speed_over_limit" in segment["warnings"]
    assert pack["summary"]["blocking"] >= 3


def test_cli_writes_json_markdown_srt_and_strict_exit(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {"id": "1", "start": 0.0, "end": 2.0, "text": "字幕要先翻译再配音。"}
        ]
    }, ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "localization_pack.json"
    out_md = tmp_path / "localization_pack.md"
    out_srt = tmp_path / "localization_en.todo.srt"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "localization_pack.py"),
            "--transcript",
            str(transcript),
            "--target-language",
            "en",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--srt",
            str(out_srt),
            "--require-translations",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    assert out_srt.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["blocking"] == 1
    assert "TODO" in out_srt.read_text(encoding="utf-8")


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "localization_pack.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "localization/dubbing review package" in result.stdout
