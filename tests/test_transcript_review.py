import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from transcript_review import (  # noqa: E402
    apply_review_edits,
    apply_text_corrections,
    build_review_lines,
    load_corrections,
    parse_review,
    redistribute_words,
)


def sample_transcript():
    return {
        "language": "zh",
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 2.0,
                "text": "今天聊 cloud",
                "words": [
                    {"word": "今天", "start": 0.0, "end": 0.6},
                    {"word": "聊", "start": 0.6, "end": 0.9},
                    {"word": "cloud", "start": 0.9, "end": 1.8},
                ],
            },
            {
                "id": 2,
                "start": 2.2,
                "end": 4.0,
                "text": "然后打开 Excalibro",
            },
        ],
    }


def test_apply_text_corrections_counts_word_and_cjk_replacements():
    text, applied = apply_text_corrections(
        "今天聊 cloud，不是 cloudiness，也不是 注意力机。",
        {"cloud": "Claude", "注意力机": "注意力机制"},
    )
    assert text == "今天聊 Claude，不是 cloudiness，也不是 注意力机制。"
    assert applied == {"cloud": 1, "注意力机": 1}


def test_build_review_lines_include_corrections_and_segment_prefix():
    lines, applied = build_review_lines(
        "/tmp/transcript.json",
        sample_transcript()["segments"],
        {"cloud": "Claude", "Excalibro": "Excalidraw"},
    )
    body = "\n".join(lines)
    assert "[seg:1 start:00:00.000 end:00:02.000] 今天聊 Claude" in body
    assert "[seg:2 start:00:02.200 end:00:04.000] 然后打开 Excalidraw" in body
    assert "# cloud => Claude (x1)" in body
    assert applied == {"cloud": 1, "Excalibro": 1}


def test_parse_review_reads_segment_and_time_only_formats(tmp_path):
    review = tmp_path / "transcript_review.txt"
    review.write_text(
        "# Transcript Review\n"
        "[seg:1 start:00:00.000 end:00:02.000] 今天聊 Claude\n"
        "[00:02.200] 然后打开 Excalidraw\n",
        encoding="utf-8",
    )
    edits = parse_review(str(review))
    assert edits[0]["id"] == "1"
    assert edits[0]["start"] == 0.0
    assert edits[1]["id"] is None
    assert edits[1]["start"] == 2.2


def test_apply_review_edits_preserves_segments_and_redistributes_words():
    transcript = sample_transcript()
    edits = [
        {"line": 1, "id": "1", "start": 0.0, "text": "今天聊 Claude 和 Codex"},
        {"line": 2, "id": "2", "start": 2.2, "text": "然后打开 Excalidraw"},
    ]
    updated, summary = apply_review_edits(transcript, edits)
    assert updated["segments"][0]["text"] == "今天聊 Claude 和 Codex"
    assert summary["changed_segments"] == 2
    words = updated["segments"][0]["words"]
    assert [w["word"] for w in words] == ["今", "天", "聊", "Claude", "和", "Codex"]
    assert words[0]["start"] == 0.0
    assert words[-1]["end"] == 1.8
    assert updated["review"]["version"] == "transcript_review.v1"


def test_redistribute_words_falls_back_to_segment_span_without_words():
    segment = {"start": 10.0, "end": 12.0}
    words = redistribute_words("AI ship better", segment)
    assert words[0]["start"] == 10.0
    assert words[-1]["end"] == 12.0
    assert [w["word"] for w in words] == ["AI", "ship", "better"]


def test_load_corrections_supports_text_file(tmp_path):
    path = tmp_path / "corrections.txt"
    path.write_text("# known ASR fixes\ncloud => Claude\nExcalibro=Excalidraw\n", encoding="utf-8")
    assert load_corrections(str(path)) == {"cloud": "Claude", "Excalibro": "Excalidraw"}


def test_load_corrections_missing_file_is_empty(tmp_path):
    assert load_corrections(str(tmp_path / "missing.json")) == {}


def test_cli_export_and_apply_round_trip(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(sample_transcript(), ensure_ascii=False), encoding="utf-8")
    corrections_path = tmp_path / "corrections.json"
    corrections_path.write_text(json.dumps({"cloud": "Claude"}, ensure_ascii=False), encoding="utf-8")
    review_path = tmp_path / "review.txt"
    output_path = tmp_path / "reviewed.json"

    export = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/transcript_review.py"),
            "export",
            "--transcript", str(transcript_path),
            "--review", str(review_path),
            "--corrections", str(corrections_path),
        ],
        capture_output=True,
        text=True,
    )
    assert export.returncode == 0, export.stderr
    review_text = review_path.read_text(encoding="utf-8")
    assert "今天聊 Claude" in review_text
    review_path.write_text(review_text.replace("然后打开 Excalibro", "然后打开 Excalidraw"), encoding="utf-8")

    apply = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/transcript_review.py"),
            "apply",
            "--transcript", str(transcript_path),
            "--review", str(review_path),
            "--output", str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    assert apply.returncode == 0, apply.stderr
    reviewed = json.loads(output_path.read_text(encoding="utf-8"))
    assert reviewed["segments"][0]["text"] == "今天聊 Claude"
    assert reviewed["segments"][1]["text"] == "然后打开 Excalidraw"
    assert reviewed["review"]["changed_segments"] == 2
