import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "transcript_lookup.py"
sys.path.insert(0, str(SCRIPT.parent))

from transcript_lookup import load_segments, search, span  # noqa: E402


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps({"segments": [
        {"id": "a", "start": 0, "end": 1, "text": "今天介绍，"},
        {"id": "b", "start": 1.2, "end": 2, "text": "新的产品！"},
        {"id": "c", "start": 8, "end": 9, "text": "Another Product."},
        {"id": "d", "start": 9.2, "end": 10, "text": "新 的 产品"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_search_crosses_nearby_segments_and_normalizes_punctuation(transcript):
    segments, _ = load_segments(transcript)
    matches, total = search(segments, "介绍 新的产品")
    assert total == 1
    assert matches[0]["segment_ids"] == ["a", "b"]
    assert matches[0]["start"] == 0
    assert matches[0]["end"] == 2
    assert len(matches[0]["context"]) == 3


def test_search_does_not_bridge_long_gap(transcript):
    segments, _ = load_segments(transcript)
    assert search(segments, "产品 Another")[1] == 0
    assert search(segments, "ANOTHER product")[1] == 1


def test_search_limit_and_span_overlap(transcript):
    segments, _ = load_segments(transcript)
    matches, total = search(segments, "新的产品", limit=1)
    assert total == 2
    assert len(matches) == 1
    hits, count = span(segments, 1.5, 8.2, limit=1)
    assert count == 2
    assert [item["id"] for item in hits] == ["b"]


def test_cli_bounded_json_and_source_hash(transcript):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(transcript), "search", "新的产品", "--limit", "1"],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["total"] == 2
    assert payload["returned"] == 1
    assert payload["truncated"] is True
    assert len(payload["transcript_sha256"]) == 64


def test_invalid_timing_and_query_fail_cleanly(tmp_path, transcript):
    bad = tmp_path / "bad.json"
    bad.write_text('{"segments":[{"start":0,"end":NaN,"text":"bad"}]}')
    with pytest.raises(ValueError, match="invalid timing"):
        load_segments(bad)
    segments, _ = load_segments(transcript)
    with pytest.raises(ValueError, match="query"):
        search(segments, "!?.")
    with pytest.raises(ValueError, match="span"):
        span(segments, 5, 5)
