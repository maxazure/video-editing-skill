import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from takes_pack import build_takes_pack, emit_markdown, timed_units_from_transcript  # noqa: E402


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_word_level_transcript_breaks_on_gap_and_speaker_change(tmp_path):
    transcript = {
        "duration": 4.0,
        "segments": [
            {
                "id": 7,
                "start": 0.0,
                "end": 2.2,
                "speaker": "S0",
                "text": "Hello world Again yes",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.25},
                    {"word": "world", "start": 0.28, "end": 0.55},
                    {"word": "Again", "start": 1.2, "end": 1.5},
                    {"word": "yes", "start": 1.55, "end": 1.8, "speaker": "S1"},
                ],
            }
        ],
    }
    path = tmp_path / "take1_transcript.json"
    _write_json(path, transcript)

    pack = build_takes_pack([f"take1={path}"], break_gap=0.5)

    assert pack["version"] == "takes_pack.v1"
    assert pack["summary"]["phrases"] == 3
    assert [phrase["text"] for phrase in pack["phrases"]] == ["Hello world", "Again", "yes"]
    assert pack["phrases"][2]["speaker"] == "S1"
    assert pack["phrases"][0]["segment_ids"] == [7]


def test_segment_transcripts_are_grouped_by_silence_gap(tmp_path):
    first = tmp_path / "take_a_transcript.json"
    second = tmp_path / "take_b_transcript.json"
    _write_json(first, {
        "duration": 8.0,
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "第一句"},
            {"id": 2, "start": 1.2, "end": 2.0, "text": "第二句"},
            {"id": 3, "start": 3.0, "end": 4.0, "text": "第三句"},
        ],
    })
    _write_json(second, {
        "duration": 5.0,
        "segments": [{"id": 1, "start": 0.0, "end": 1.2, "text": "alternate take"}],
    })

    pack = build_takes_pack([str(first), f"alt={second}"], break_gap=0.5)
    markdown = emit_markdown(pack)

    assert pack["summary"] == {"sources": 2, "phrases": 3, "audio_events": 0, "warnings": 0}
    assert pack["sources"][0]["label"] == "take_a"
    assert [phrase["text"] for phrase in pack["phrases"][:2]] == ["第一句第二句", "第三句"]
    assert "## take_a" in markdown
    assert "`alt-001`" in markdown


def test_timed_units_fall_back_to_segment_when_words_are_missing_timing():
    transcript = {
        "segments": [
            {
                "id": "a",
                "start": 2.0,
                "end": 3.5,
                "text": "fallback segment",
                "words": [{"word": "fallback"}],
            }
        ]
    }

    units = timed_units_from_transcript(transcript, "take")

    assert len(units) == 1
    assert units[0].text == "fallback segment"
    assert units[0].start == 2.0


def test_single_source_pack_warns_but_stays_usable(tmp_path):
    transcript = tmp_path / "solo_transcript.json"
    _write_json(transcript, {
        "duration": 1.5,
        "segments": [{"id": 1, "start": 0.0, "end": 1.0, "text": "single take"}],
    })

    pack = build_takes_pack([str(transcript)])

    assert pack["summary"] == {"sources": 1, "phrases": 1, "audio_events": 0, "warnings": 1}
    assert "only one transcript supplied" in pack["warnings"][0]
    assert "single take" in emit_markdown(pack)


def test_top_level_scribe_words_preserve_audio_events_and_speaker_ids(tmp_path):
    transcript = tmp_path / "scribe_transcript.json"
    _write_json(transcript, {
        "duration": 2.2,
        "words": [
            {"type": "word", "text": "Hello", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "audio_event", "text": "laughter", "start": 0.35, "end": 0.7, "speaker_id": "speaker_0"},
            {"type": "spacing", "text": " ", "start": 0.7, "end": 1.3},
            {"type": "word", "text": "Again", "start": 1.3, "end": 1.6, "speaker_id": "speaker_0"},
            {"type": "word", "text": "Hi", "start": 1.65, "end": 2.0, "speaker_id": "speaker_1"},
        ],
    })

    pack = build_takes_pack([f"launch={transcript}"], break_gap=0.5)
    markdown = emit_markdown(pack)

    assert pack["summary"]["phrases"] == 3
    assert pack["summary"]["audio_events"] == 1
    assert pack["sources"][0]["segments"] == 0
    assert pack["sources"][0]["audio_events"] == 1
    assert [phrase["speaker"] for phrase in pack["phrases"]] == ["speaker_0", "speaker_0", "speaker_1"]
    assert pack["phrases"][0]["text"] == "Hello (laughter)"
    assert pack["phrases"][0]["audio_events"] == [
        {"label": "laughter", "start": 0.35, "end": 0.7}
    ]
    assert "| Events | Text |" in markdown
    assert "laughter" in markdown


def test_cli_writes_markdown_and_json(tmp_path):
    transcript = tmp_path / "launch_transcript.json"
    output_md = tmp_path / "takes_packed.md"
    output_json = tmp_path / "takes_pack.json"
    _write_json(transcript, {
        "duration": 3.0,
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "Hook question?"},
            {"id": 2, "start": 1.7, "end": 2.5, "text": "Proof point."},
        ],
    })

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "takes_pack.py"),
            "--transcript",
            str(transcript),
            "--output",
            str(output_md),
            "--json",
            str(output_json),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote takes pack" in result.stderr
    assert "# Takes Pack" in output_md.read_text(encoding="utf-8")
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["summary"]["phrases"] == 2
