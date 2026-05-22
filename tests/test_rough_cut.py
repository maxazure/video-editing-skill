import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from rough_cut import (  # noqa: E402
    build_keep_segments,
    build_rough_cut_plan,
    detect_adjacent_repeats,
    detect_filler_only_segments,
    merge_removed_ranges,
    normalize_segments,
)


def _sample_transcript():
    return {
        "language": "zh",
        "duration": 8.0,
        "segments": [
            {"id": 1, "start": 0.0, "end": 0.6, "text": "嗯那个"},
            {"id": 2, "start": 0.7, "end": 2.0, "text": "今天我们讲注意力机"},
            {"id": 3, "start": 2.2, "end": 3.1, "text": "今天我们讲注意力机制"},
            {"id": 4, "start": 3.2, "end": 5.0, "text": "今天我们讲注意力机制的三个坑"},
            {"id": 5, "start": 5.5, "end": 8.0, "text": "最后给你一个清单"},
        ],
        "filler_words": [
            {"segment_id": 1, "text": "嗯那个", "fillers_found": ["嗯", "那个"], "is_filler_only": True},
        ],
    }


def test_detect_filler_only_segments_from_transcribe_metadata():
    segments = normalize_segments(_sample_transcript())
    decisions = detect_filler_only_segments(_sample_transcript(), segments, "zh")
    assert [d.segment_id for d in decisions] == [1]
    assert decisions[0].reason == "filler_only"


def test_detect_adjacent_repeats_prefers_longer_retry():
    transcript = _sample_transcript()
    segments = normalize_segments(transcript)
    decisions = detect_adjacent_repeats(segments, language="zh", skip_ids=[1], threshold=0.65)
    assert decisions
    assert decisions[0].segment_id == 2
    assert decisions[0].keep_segment_id == 3
    assert decisions[0].reason == "repeated_before_retry"


def test_merge_removed_ranges_combines_overlaps():
    transcript = _sample_transcript()
    plan = build_rough_cut_plan(transcript, repeat_threshold=0.65)
    removed_ids = [seg_id for item in plan["removed_segments"] for seg_id in item["segment_ids"]]
    assert 1 in removed_ids
    assert 2 in removed_ids
    assert plan["removed_seconds"] > 0
    assert plan["output_duration_estimate"] < transcript["duration"]


def test_build_keep_segments_inverts_removed_ranges():
    transcript = _sample_transcript()
    plan = build_rough_cut_plan(transcript, repeat_threshold=0.65)
    removed = merge_removed_ranges(
        detect_filler_only_segments(transcript, normalize_segments(transcript), "zh")
    )
    keep = build_keep_segments(8.0, removed)
    assert keep[0].start == 0.6
    assert keep[-1].end == 8.0
    assert plan["speedup_ratio"] is not None


def test_cli_writes_cut_list_without_media(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    output_path = tmp_path / "rough.json"
    transcript_path.write_text(json.dumps(_sample_transcript(), ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "rough_cut.py"),
            "--transcript", str(transcript_path),
            "--cut-list", str(output_path),
            "--repeat-threshold", "0.65",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["kind"] == "rough_cut"
    assert data["removed_segments"]
