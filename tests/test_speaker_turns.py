import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from pipeline_manifest import build_manifest  # noqa: E402
from speaker_turns import (  # noqa: E402
    DiarizationSegment,
    build_enrich_plan,
    build_speaker_turns,
    emit_markdown,
    normalize_diarization_json,
    parse_rttm_lines,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_assigns_diarization_to_word_turns_and_merges():
    transcript = {
        "segments": [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "hello there yes thanks",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "there", "start": 0.6, "end": 1.0},
                    {"word": "yes", "start": 2.0, "end": 2.4},
                    {"word": "thanks", "start": 2.5, "end": 3.0},
                ],
            }
        ]
    }
    diarization = [
        DiarizationSegment(0.0, 1.5, "SPEAKER_00"),
        DiarizationSegment(1.8, 3.4, "SPEAKER_01"),
    ]

    report = build_speaker_turns(
        transcript,
        diarization,
        speaker_map={"SPEAKER_00": {"name": "Host"}, "SPEAKER_01": {"name": "Guest"}},
    )

    assert report["summary"]["detected_speakers"] == 2
    assert [turn["speaker"] for turn in report["turns"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert report["turns"][0]["display_name"] == "Host"
    assert report["turns"][0]["text"] == "hello there"
    assert report["turns"][1]["text"] == "yes thanks"


def test_parses_rttm_and_flags_crosstalk():
    segments = parse_rttm_lines([
        "SPEAKER audio 1 0.000 2.000 <NA> <NA> SPEAKER_00 <NA> <NA>",
        "SPEAKER audio 1 1.500 1.000 <NA> <NA> SPEAKER_01 <NA> <NA>",
    ])
    transcript = {
        "segments": [
            {"start": 0.1, "end": 0.8, "text": "first"},
            {"start": 1.6, "end": 2.2, "text": "overlap"},
        ]
    }

    report = build_speaker_turns(transcript, segments, crosstalk_threshold=0.2)

    assert len(segments) == 2
    assert report["summary"]["crosstalk_events"] == 1
    assert report["crosstalk"][0]["speakers"] == ["SPEAKER_00", "SPEAKER_01"]


def test_normalizes_common_diarization_json_shapes():
    data = {
        "speaker_segments": [
            {"start_time": 0, "duration": 1.2, "speaker_id": "speaker_0"},
            {"begin": 1.2, "end_time": 2.0, "label": "speaker_1"},
        ]
    }

    segments = normalize_diarization_json(data)

    assert [(s.start, s.end, s.speaker) for s in segments] == [
        (0.0, 1.2, "speaker_0"),
        (1.2, 2.0, "speaker_1"),
    ]


def test_emits_markdown_and_enrich_badges():
    transcript = {
        "words": [
            {"text": "Hi", "start": 0.0, "end": 0.4, "speaker_id": "speaker_0"},
            {"text": "OK", "start": 1.0, "end": 1.4, "speaker_id": "speaker_1"},
        ]
    }
    report = build_speaker_turns(
        transcript,
        speaker_map={"speaker_0": "Alice", "speaker_1": "Bob"},
    )
    markdown = emit_markdown(report)
    enrich = build_enrich_plan(report)

    assert "Alice" in markdown
    assert [badge["text"] for badge in enrich["text_badges"]] == ["Alice", "Bob"]
    assert enrich["text_badges"][0]["source"] == "speaker_turns"


def test_cli_writes_review_artifacts(tmp_path):
    transcript = tmp_path / "transcript.json"
    diarization = tmp_path / "diarization.json"
    output = tmp_path / "speaker_turns.json"
    markdown = tmp_path / "speaker_turns.md"
    badges = tmp_path / "speaker_badges.json"
    transcript.write_text(json.dumps({
        "segments": [{"start": 0, "end": 1, "text": "hello"}]
    }))
    diarization.write_text(json.dumps({
        "segments": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]
    }))

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "speaker_turns.py"),
            "--transcript", str(transcript),
            "--diarization", str(diarization),
            "--output", str(output),
            "--markdown", str(markdown),
            "--enrich-plan", str(badges),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(output.read_text())["version"] == "speaker_turns.v1"
    assert "Speaker Turns" in markdown.read_text()
    assert json.loads(badges.read_text())["text_badges"][0]["speaker"] == "SPEAKER_00"


def test_pipeline_manifest_blocks_on_failed_speaker_turns(tmp_path):
    (tmp_path / "speaker_turns.json").write_text(json.dumps({
        "version": "speaker_turns.v1",
        "summary": {"blocking": 1},
    }))

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "speaker_turns" in manifest["blocked_gates"]
