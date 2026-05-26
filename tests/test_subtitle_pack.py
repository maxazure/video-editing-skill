"""subtitle_pack.py sidecar subtitle export."""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from subtitle_pack import (  # noqa: E402
    SourceClip,
    TimedWord,
    build_cues,
    split_text,
)


def test_split_text_prefers_zh_punctuation():
    chunks = split_text(
        "先别急着剪，先把字幕切到一行能看懂。然后再导出给平台。",
        max_chars=12,
        language="zh",
    )
    assert len(chunks) >= 3
    assert all(len(chunk) <= 12 for chunk in chunks)
    assert chunks[0].endswith("，")


def test_config_concat_timing_honors_speed_and_offset():
    clips = [
        SourceClip(id="a", start=10.0, end=14.0, text="第一段内容"),
        SourceClip(id="b", start=20.0, end=22.0, text="第二段内容"),
    ]
    cues = build_cues(clips, mode="concat", speed=2.0, offset=1.5, language="zh", max_chars=20)
    assert [cue.source_id for cue in cues] == ["a", "b"]
    assert cues[0].start == 1.5
    assert cues[0].end == 3.5
    assert cues[1].start == 3.5
    assert cues[1].end == 4.5


def test_word_timestamps_split_on_word_boundaries():
    clip = SourceClip(
        id="1",
        start=0.0,
        end=4.0,
        text="AI helps teams ship better videos",
        words=(
            TimedWord("AI", 0.0, 0.4),
            TimedWord("helps", 0.5, 1.0),
            TimedWord("teams", 1.1, 1.6),
            TimedWord("ship", 1.7, 2.2),
            TimedWord("better", 2.3, 2.9),
            TimedWord("videos", 3.0, 3.8),
        ),
    )
    cues = build_cues([clip], mode="source", language="en", max_chars=13, max_duration=4.5)
    assert [cue.text for cue in cues] == ["AI helps", "teams ship", "better videos"]
    assert cues[1].start == 1.1
    assert cues[2].end == 3.8


def test_cli_writes_srt_vtt_ass_and_json(tmp_path):
    transcript = tmp_path / "demo_transcript.json"
    transcript.write_text(json.dumps({
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 3.0,
                "text": "字幕交付要有 SRT，也要有 VTT 和 ASS。",
            }
        ]
    }, ensure_ascii=False))
    out_dir = tmp_path / "subs"

    out = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/subtitle_pack.py"),
            "--transcript", str(transcript),
            "--output-dir", str(out_dir),
            "--basename", "demo",
            "--formats", "srt,vtt,ass,json",
            "--language", "zh",
            "--max-chars", "10",
        ],
        capture_output=True,
        text=True,
    )

    assert out.returncode == 0, out.stderr
    assert "subtitle cues:" in out.stdout
    assert (out_dir / "demo.srt").read_text().startswith("1\n00:00:00,000")
    assert (out_dir / "demo.vtt").read_text().startswith("WEBVTT")
    assert "[Script Info]" in (out_dir / "demo.ass").read_text()
    manifest = json.loads((out_dir / "demo.json").read_text())
    assert manifest["version"] == "subtitle_pack.v1"
    assert manifest["stats"]["cue_count"] >= 2
