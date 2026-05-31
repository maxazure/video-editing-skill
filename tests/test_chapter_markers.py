"""chapter_markers.py chapter sidecar export."""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from chapter_markers import (  # noqa: E402
    ChapterMarker,
    TranscriptSegment,
    build_chapter_markers,
    chapters_to_ffmetadata,
    chapters_to_youtube,
    format_youtube_timestamp,
    parse_chapter_headings,
    parse_timecode,
)


def test_parse_timecode_and_youtube_format():
    assert parse_timecode("1:02") == 62
    assert parse_timecode("1:02:03.5") == 3723.5
    assert format_youtube_timestamp(0) == "0:00"
    assert format_youtube_timestamp(62.9) == "1:02"
    assert format_youtube_timestamp(3723.5) == "1:02:03"


def test_parse_chapter_headings_ignores_subheadings(tmp_path):
    script = tmp_path / "clean_script.md"
    script.write_text(
        "# Script\n\n## Hook\ntext\n\n### Internal note\n\n## Value | Proof\n",
        encoding="utf-8",
    )
    assert parse_chapter_headings(str(script)) == ["Hook", "Value | Proof"]


def test_build_from_clean_script_titles_uses_zero_start_and_duration():
    segments = [
        TranscriptSegment(0, 12, "opening"),
        TranscriptSegment(70, 86, "next we cover setup"),
        TranscriptSegment(145, 160, "finally ship it"),
    ]
    chapters, warnings = build_chapter_markers(
        segments=segments,
        duration=180,
        titles=["Hook", "Setup", "Ship"],
        min_chapter_duration=30,
    )
    assert warnings == []
    assert [chapter.start for chapter in chapters] == [0.0, 70, 145]
    assert [chapter.end for chapter in chapters] == [70, 145, 180]
    assert chapters[1].source == "clean_script"


def test_explicit_chapters_are_sorted_and_first_starts_at_zero():
    chapters, warnings = build_chapter_markers(
        duration=240,
        explicit_chapters=[
            {"timestamp": 130, "title": "Second"},
            {"timestamp": 12, "title": "Opening"},
        ],
        min_chapter_duration=30,
    )
    assert [chapter.title for chapter in chapters] == ["Opening", "Second"]
    assert chapters[0].start == 0.0
    assert "first chapter start adjusted" in warnings[0]


def test_ffmetadata_escapes_reserved_title_characters():
    metadata = chapters_to_ffmetadata([
        ChapterMarker("ch01", "A=B; C#D", 0, 10, 10),
    ])
    assert ";FFMETADATA1" in metadata
    assert "START=0" in metadata
    assert "END=10000" in metadata
    assert r"title=A\=B\; C\#D" in metadata


def test_youtube_timestamps_start_at_zero():
    text = chapters_to_youtube([
        ChapterMarker("ch01", "Intro", 0, 90, 90),
        ChapterMarker("ch02", "Deep Dive", 90, 180, 90),
    ])
    assert text.splitlines() == ["0:00 Intro", "1:30 Deep Dive"]


def test_cli_writes_all_chapter_marker_formats(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({
        "duration": 180,
        "segments": [
            {"start": 0, "end": 20, "text": "Intro"},
            {"start": 70, "end": 90, "text": "接下来讲工作流"},
            {"start": 140, "end": 170, "text": "最后总结"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    script = tmp_path / "clean_script.md"
    script.write_text("## 开场\n\n## 工作流\n\n## 总结\n", encoding="utf-8")
    out_dir = tmp_path / "chapters"

    out = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/chapter_markers.py"),
            "--transcript", str(transcript),
            "--clean-script", str(script),
            "--output-dir", str(out_dir),
            "--min-chapter-duration", "30",
        ],
        capture_output=True,
        text=True,
    )

    assert out.returncode == 0, out.stderr
    assert "chapter markers: 3" in out.stdout
    manifest = json.loads((out_dir / "chapters.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "chapter_markers.v1"
    assert manifest["stats"]["chapter_count"] == 3
    assert (out_dir / "chapters.md").read_text(encoding="utf-8").startswith("# Chapters")
    assert "[CHAPTER]" in (out_dir / "chapters.ffmetadata").read_text(encoding="utf-8")
    assert (out_dir / "chapters-youtube.txt").read_text(encoding="utf-8").splitlines()[0].startswith("0:00")
