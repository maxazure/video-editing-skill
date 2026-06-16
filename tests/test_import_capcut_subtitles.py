import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from import_capcut_subtitles import (  # noqa: E402
    build_subtitle_gap_cut_list,
    extract_draft_subtitles,
    parse_srt,
    transcript_from_segments,
)


def _text_material(material_id, text, mat_type="subtitle"):
    return {
        "id": material_id,
        "type": mat_type,
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }


def _text_segment(material_id, start, duration):
    return {
        "material_id": material_id,
        "target_timerange": {
            "start": int(start * 1_000_000),
            "duration": int(duration * 1_000_000),
        },
        "visible": True,
    }


def test_extract_draft_subtitles_skips_title_overlays_by_default():
    draft = {
        "duration": 4_000_000,
        "materials": {
            "texts": [
                _text_material("cover", "封面标题", mat_type="text"),
                _text_material("s1", "第一句字幕"),
                _text_material("s2", "第二句字幕"),
            ]
        },
        "tracks": [
            {"id": "title-track", "type": "text", "segments": [_text_segment("cover", 0.0, 1.0)]},
            {"id": "subtitle-track", "type": "text", "segments": [
                _text_segment("s1", 0.5, 0.8),
                _text_segment("s2", 2.6, 0.9),
            ]},
        ],
    }

    segments = extract_draft_subtitles(draft)

    assert [segment.text for segment in segments] == ["第一句字幕", "第二句字幕"]
    assert segments[0].start == 0.5
    assert segments[1].end == 3.5


def test_transcript_from_segments_is_pipeline_compatible():
    segments = extract_draft_subtitles({
        "materials": {"texts": [_text_material("s1", "剪映字幕")]},
        "tracks": [{"type": "text", "segments": [_text_segment("s1", 0.0, 1.25)]}],
    })

    transcript = transcript_from_segments(
        segments,
        source_path="draft_content.json",
        source_type="capcut_draft",
        duration=2.0,
    )

    assert transcript["version"] == "capcut_subtitle_import.v1"
    assert transcript["language"] == "zh"
    assert transcript["duration"] == 2.0
    assert transcript["segments"][0] == {
        "id": 1,
        "start": 0.0,
        "end": 1.25,
        "duration": 1.25,
        "text": "剪映字幕",
    }


def test_build_subtitle_gap_cut_list_uses_subtitle_gaps():
    segments = extract_draft_subtitles({
        "materials": {"texts": [_text_material("s1", "第一句"), _text_material("s2", "第二句")]},
        "tracks": [{"type": "text", "segments": [
            _text_segment("s1", 0.5, 0.5),
            _text_segment("s2", 2.6, 0.6),
        ]}],
    })

    plan = build_subtitle_gap_cut_list(
        segments,
        duration=4.0,
        gap_threshold=0.75,
        pad=0.1,
        min_keep=0.1,
        source_media="origin/talking.mp4",
    )

    assert plan["kind"] == "capcut_subtitle_gap_cut"
    assert len(plan["keep_segments"]) == 2
    assert plan["keep_segments"][0] == {"start": 0.4, "end": 1.1, "duration": 0.7}
    assert plan["removed_segments"][1] == {"start": 1.1, "end": 2.5, "duration": 1.4}
    assert plan["input"].endswith(os.path.join("origin", "talking.mp4"))


def test_parse_srt_imports_multiline_cues(tmp_path):
    srt = tmp_path / "capcut.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,200\nHello\nworld\n\n"
        "2\n00:00:02.000 --> 00:00:03.000\n第二句\n",
        encoding="utf-8",
    )

    segments = parse_srt(str(srt))

    assert [segment.text for segment in segments] == ["Hello world", "第二句"]
    assert segments[0].duration == 1.2
    assert segments[1].start == 2.0


def test_cli_writes_transcript_cut_list_srt_and_markdown(tmp_path):
    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    (draft_dir / "draft_content.json").write_text(json.dumps({
        "duration": 4_000_000,
        "materials": {"texts": [_text_material("s1", "第一句"), _text_material("s2", "第二句")]},
        "tracks": [{"type": "text", "segments": [
            _text_segment("s1", 0.0, 1.0),
            _text_segment("s2", 2.5, 0.8),
        ]}],
    }, ensure_ascii=False), encoding="utf-8")

    transcript = tmp_path / "transcript.json"
    cut_list = tmp_path / "cut.json"
    srt = tmp_path / "subtitles.srt"
    markdown = tmp_path / "review.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "import_capcut_subtitles.py"),
            "--draft", str(draft_dir),
            "--transcript", str(transcript),
            "--cut-list", str(cut_list),
            "--srt-output", str(srt),
            "--markdown", str(markdown),
            "--gap-threshold", "0.75",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Imported 2 subtitle segments" in result.stderr
    assert json.loads(transcript.read_text(encoding="utf-8"))["segments"][1]["text"] == "第二句"
    assert json.loads(cut_list.read_text(encoding="utf-8"))["kind"] == "capcut_subtitle_gap_cut"
    assert srt.read_text(encoding="utf-8").startswith("1\n00:00:00,000")
    assert "CapCut Subtitle Import Review" in markdown.read_text(encoding="utf-8")
