import json
import os
import subprocess
import sys


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from speech_continuity_qa import (  # noqa: E402
    TranscriptSegment,
    analyze_segments,
    build_report,
    emit_markdown,
    find_boundary_overlap,
    find_internal_repeats,
    tokenize,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _segment(segment_id, start, end, text, speaker=""):
    return TranscriptSegment(
        segment_id=segment_id,
        start=start,
        end=end,
        text=text,
        speaker=speaker,
        tokens=tuple(tokenize(text)),
    )


def test_tokenize_handles_cjk_and_english_words():
    assert tokenize("现在用 GPT-5's workflow，开始！") == ["现", "在", "用", "gpt", "5's", "workflow", "开", "始"]


def test_find_boundary_overlap_returns_longest_exact_phrase():
    overlap = find_boundary_overlap(
        tokenize("先说重点我们现在开始"),
        tokenize("我们现在开始第二部分"),
        min_units=3,
        max_units=12,
    )

    assert overlap == tuple(tokenize("我们现在开始"))


def test_find_internal_repeats_detects_immediate_stutter():
    repeats = find_internal_repeats(
        tokenize("this is the fix this is the fix now"),
        min_units=3,
        max_units=8,
    )

    assert repeats == [(0, ("this", "is", "the", "fix"))]


def test_analyze_segments_records_internal_repeat_segment_evidence():
    findings = analyze_segments([
        _segment(7, 1.0, 4.0, "this is the fix this is the fix now"),
    ])

    assert findings[0]["kind"] == "internal_immediate_repeat"
    assert findings[0]["current_segment_id"] == 7
    assert findings[0]["current_text"] == "this is the fix this is the fix now"


def test_analyze_segments_blocks_boundary_repeat_without_double_counting_similarity():
    findings = analyze_segments([
        _segment(1, 0.0, 2.0, "先说重点我们现在开始"),
        _segment(2, 2.05, 4.0, "我们现在开始第二部分"),
    ])

    assert len(findings) == 1
    assert findings[0]["kind"] == "boundary_exact_repeat"
    assert findings[0]["repeated_text"] == "我们现在开始"
    assert findings[0]["boundary_time"] == 2.05


def test_analyze_segments_detects_adjacent_near_duplicate_takes():
    findings = analyze_segments([
        _segment(1, 0.0, 3.0, "we need to ship this exact feature to users today"),
        _segment(2, 3.1, 6.0, "we need to ship this useful feature to users today"),
    ], similarity_threshold=0.88)

    assert len(findings) == 1
    assert findings[0]["kind"] == "adjacent_near_duplicate"
    assert findings[0]["similarity"] >= 0.88


def test_analyze_segments_skips_different_speakers_by_default():
    segments = [
        _segment(1, 0.0, 2.0, "我们现在开始", speaker="host"),
        _segment(2, 2.1, 4.0, "我们现在开始", speaker="guest"),
    ]

    assert analyze_segments(segments) == []
    assert analyze_segments(segments, include_speaker_changes=True)[0]["kind"] == "boundary_exact_repeat"


def test_build_report_is_ready_for_clean_rendered_transcript(tmp_path):
    transcript = tmp_path / "final_transcript.json"
    transcript.write_text(json.dumps({
        "source_audio": "/tmp/final.mp4",
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "先说今天的问题"},
            {"id": 2, "start": 2.2, "end": 4.0, "text": "接下来给出解决方法"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    report = build_report(str(transcript))

    assert report["version"] == "speech_continuity_qa.v1"
    assert report["source_media"] == "/tmp/final.mp4"
    assert report["summary"]["status"] == "ready"
    assert report["summary"]["blocking"] == 0


def test_emit_markdown_lists_actionable_finding():
    report = {
        "transcript": "/tmp/final_transcript.json",
        "source_media": "/tmp/final.mp4",
        "segments_analyzed": 2,
        "findings": [{
            "id": "speech-001",
            "kind": "boundary_exact_repeat",
            "start": 1.0,
            "end": 3.0,
            "repeated_text": "我们现在开始",
            "repeat_units": 6,
        }],
        "summary": {"status": "blocked", "blocking": 1},
    }

    markdown = emit_markdown(report)

    assert "# Speech Continuity QA" in markdown
    assert "boundary_exact_repeat" in markdown
    assert "我们现在开始" in markdown
    assert "re-render" in markdown


def test_cli_strict_writes_reports_and_exits_two(tmp_path):
    transcript = tmp_path / "final_transcript.json"
    output = tmp_path / "speech_continuity_qa.json"
    markdown = tmp_path / "speech_continuity_qa.md"
    transcript.write_text(json.dumps({
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "先说重点我们现在开始"},
            {"id": 2, "start": 2.1, "end": 4.0, "text": "我们现在开始下一段"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run([
        sys.executable,
        os.path.join(REPO, "scripts", "speech_continuity_qa.py"),
        str(transcript),
        "--output", str(output),
        "--markdown", str(markdown),
        "--strict",
    ], capture_output=True, text=True)

    assert result.returncode == 2
    assert output.exists()
    assert markdown.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["blocking"] == 1


def test_cli_help_smoke():
    result = subprocess.run([
        sys.executable,
        os.path.join(REPO, "scripts", "speech_continuity_qa.py"),
        "--help",
    ], capture_output=True, text=True)

    assert result.returncode == 0
    assert "repeated speech" in result.stdout.lower()
