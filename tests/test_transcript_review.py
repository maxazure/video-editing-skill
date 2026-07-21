import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from transcript_review import (  # noqa: E402
    apply_review_edits,
    apply_text_corrections,
    build_html_payload,
    build_review_lines,
    emit_review_html,
    load_corrections,
    parse_review,
    redistribute_words,
    TranscriptReviewError,
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


def test_build_html_payload_applies_corrections_and_preloads_local_video(tmp_path):
    video = tmp_path / "source clip.mp4"
    video.write_bytes(b"placeholder")
    payload = build_html_payload(
        str(tmp_path / "transcript.json"),
        sample_transcript()["segments"],
        {"cloud": "Claude"},
        video_path=str(video),
        max_cps=18.5,
        review_name="../approved.txt",
    )
    assert payload["segments"][0]["text"] == "今天聊 Claude"
    assert payload["video"]["path"] == str(video)
    assert payload["video"]["uri"].startswith("file://")
    assert payload["max_cps"] == 18.5
    assert payload["review_name"] == "approved.txt"
    assert payload["summary"]["corrections_applied"] == 1


def test_build_html_payload_changes_draft_key_when_transcript_changes(tmp_path):
    first = build_html_payload(
        str(tmp_path / "transcript.json"),
        sample_transcript()["segments"],
        {},
    )
    changed = sample_transcript()["segments"]
    changed[0]["text"] = "new transcription"
    second = build_html_payload(
        str(tmp_path / "transcript.json"),
        changed,
        {},
    )
    assert first["transcript_signature"] != second["transcript_signature"]
    assert first["storage_key"] != second["storage_key"]


def test_emit_review_html_escapes_transcript_payload_and_keeps_local_controls(tmp_path):
    transcript = sample_transcript()
    transcript["segments"][0]["text"] = "safe </script><script>alert(1)</script>"
    payload = build_html_payload(
        str(tmp_path / "transcript.json"),
        transcript["segments"],
        {},
    )
    page = emit_review_html(payload)
    assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "showSaveFilePicker" in page
    assert "localStorage" in page
    assert "CPS" in page
    match = re.search(
        r'<script id="review-data" type="application/json">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert match
    restored = json.loads(html_lib.unescape(match.group(1)))
    assert restored["segments"][0]["text"].endswith("<script>alert(1)</script>")


def test_emit_review_html_inline_javascript_parses_with_node(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    payload = build_html_payload(
        str(tmp_path / "transcript.json"),
        sample_transcript()["segments"],
        {},
    )
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", emit_review_html(payload), re.DOTALL)
    result = subprocess.run(["node", "--check", "-"], input=scripts[-1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_build_html_payload_rejects_invalid_cps(tmp_path):
    try:
        build_html_payload(
            str(tmp_path / "transcript.json"),
            sample_transcript()["segments"],
            {},
            max_cps=0,
        )
    except TranscriptReviewError as exc:
        assert "max_cps" in str(exc)
    else:
        raise AssertionError("expected TranscriptReviewError")


def test_cli_html_writes_interactive_review_page(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(sample_transcript(), ensure_ascii=False), encoding="utf-8")
    corrections_path = tmp_path / "corrections.json"
    corrections_path.write_text(json.dumps({"cloud": "Claude"}, ensure_ascii=False), encoding="utf-8")
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")
    html_path = tmp_path / "review" / "transcript_review.html"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/transcript_review.py"),
            "html",
            "--transcript", str(transcript_path),
            "--video", str(video_path),
            "--corrections", str(corrections_path),
            "--output", str(html_path),
            "--max-cps", "19",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    page = html_path.read_text(encoding="utf-8")
    assert "今天聊 Claude" in html_lib.unescape(page)
    assert video_path.resolve().as_uri() in html_lib.unescape(page)
    assert "interactive review:" in result.stdout


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
