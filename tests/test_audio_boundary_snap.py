import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from audio_boundary_snap import (  # noqa: E402
    build_audio_boundary_plan,
    emit_markdown,
    normalize_words,
    parse_silencedetect,
)


def _candidates():
    return {
        "version": "highlight_candidates.v1",
        "params": {"platform": "douyin", "min_duration": 2.0, "max_duration": 10.0},
        "selected": [
            {
                "id": "highlight_001",
                "rank": 1,
                "start": 1.1,
                "end": 3.0,
                "duration": 1.9,
                "score": 78,
                "text": "Why now? First idea continues.",
                "segment_ids": [1],
            }
        ],
    }


def _transcript():
    return {
        "duration": 8.0,
        "segments": [
            {
                "id": 1,
                "start": 1.0,
                "end": 4.0,
                "text": "Why now? First idea continues.",
                "words": [
                    {"word": "Why", "start": 1.0, "end": 1.3},
                    {"word": "now?", "start": 1.4, "end": 1.8},
                    {"word": "First", "start": 2.2, "end": 2.5},
                    {"word": "idea", "start": 2.6, "end": 3.0},
                    {"word": "continues.", "start": 3.1, "end": 4.0},
                ],
            }
        ],
        "silences": [
            {"start": 0.4, "end": 0.9},
            {"start": 4.0, "end": 4.6},
        ],
    }


def test_normalize_words_supports_scribe_top_level_tokens():
    words = normalize_words({
        "words": [
            {"type": "word", "text": "你好", "start": 0.0, "end": 0.4},
            {"type": "spacing", "text": " ", "start": 0.4, "end": 0.4},
            {"type": "audio_event", "text": "(laughs)", "start": 0.4, "end": 0.8},
            {"type": "word", "text": "世界！", "start": 0.8, "end": 1.2},
        ]
    })

    assert [word.text for word in words] == ["你好", "世界！"]


def test_plan_snaps_to_word_sentence_and_silence_boundaries():
    transcript = _transcript()
    plan = build_audio_boundary_plan(
        _candidates(),
        transcript,
        silences=transcript["silences"],
    )

    clip = plan["selected"][0]
    snap = clip["audio_boundary_snap"]
    assert plan["version"] == "audio_boundary_plan.v1"
    assert plan["status"] == "ready"
    assert clip["start"] == 0.65
    assert clip["end"] == 4.3
    assert snap["start_reason"] == "silence_midpoint_before_word"
    assert snap["end_reason"] == "silence_midpoint_after_word"
    assert snap["sentence_extended"] is True
    assert snap["last_word"] == "continues."
    assert plan["summary"]["sentence_extended"] == 1
    assert plan["summary"]["silence_snapped"] == 1
    assert plan["params"]["platform"] == "douyin"


def test_missing_word_timestamps_blocks_without_changing_candidate():
    plan = build_audio_boundary_plan(
        _candidates(),
        {"duration": 8.0, "segments": [{"start": 1.0, "end": 4.0, "text": "No words"}]},
    )

    clip = plan["selected"][0]
    assert plan["status"] == "blocked"
    assert plan["summary"]["blocking"] == 1
    assert clip["start"] == 1.1
    assert "--word-timestamps" in clip["audio_boundary_snap"]["blockers"][0]


def test_invalid_candidate_time_becomes_blocker_without_duration():
    candidates = {"selected": [{"id": "bad", "start": 1.0, "end": "later"}]}

    plan = build_audio_boundary_plan(candidates, _transcript() | {"duration": None})

    assert plan["status"] == "blocked"
    assert plan["summary"]["blocking"] == 1
    assert "numeric start and end" in plan["selected"][0]["audio_boundary_snap"]["blockers"][0]


def test_parse_silencedetect_keeps_trailing_silence():
    log = "\n".join([
        "[silencedetect @ x] silence_start: 0.4",
        "[silencedetect @ x] silence_end: 0.9 | silence_duration: 0.5",
        "[silencedetect @ x] silence_start: 7.2",
    ])

    assert parse_silencedetect(log, duration=8.0) == [
        {"start": 0.4, "end": 0.9},
        {"start": 7.2, "end": 8.0},
    ]


def test_markdown_reports_adjustment_deltas():
    transcript = _transcript()
    plan = build_audio_boundary_plan(_candidates(), transcript, silences=transcript["silences"])
    markdown = emit_markdown(plan)

    assert "# Audio Boundary Plan" in markdown
    assert "highlight_001" in markdown
    assert "silence_midpoint_before_word" in markdown
    assert "Pass the reviewed JSON" in markdown


def test_cli_writes_short_batch_compatible_plan(tmp_path):
    candidates = tmp_path / "highlight_candidates.json"
    transcript = tmp_path / "transcript.json"
    output = tmp_path / "audio_boundary_plan.json"
    markdown = tmp_path / "audio_boundary_plan.md"
    candidates.write_text(json.dumps(_candidates()), encoding="utf-8")
    transcript.write_text(json.dumps(_transcript()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "audio_boundary_snap.py"),
            "--candidates", str(candidates),
            "--transcript", str(transcript),
            "--output", str(output),
            "--markdown", str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selected"][0]["audio_boundary_snap"]["applied"] is True
    assert payload["params"]["platform"] == "douyin"
    assert markdown.exists()


def test_cli_strict_returns_two_without_word_timestamps(tmp_path):
    candidates = tmp_path / "highlight_candidates.json"
    transcript = tmp_path / "transcript.json"
    output = tmp_path / "audio_boundary_plan.json"
    candidates.write_text(json.dumps(_candidates()), encoding="utf-8")
    transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "audio_boundary_snap.py"),
            "--candidates", str(candidates),
            "--transcript", str(transcript),
            "--output", str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
