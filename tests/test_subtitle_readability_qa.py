import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import subtitle_readability_qa as subtitle_qa  # noqa: E402
from subtitle_readability_qa import (  # noqa: E402
    analyze_cues,
    emit_markdown,
    load_subtitle_pack,
)


def _analyze(tmp_path, cues, **kwargs):
    path = tmp_path / "final_subtitles.json"
    payload = {
        "version": "subtitle_pack.v1",
        "settings": {"language": "zh"},
        "cues": cues,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded_payload, loaded_cues = load_subtitle_pack(str(path))
    return analyze_cues(str(path), loaded_payload, loaded_cues, **kwargs)


def test_clean_output_aligned_pack_is_ready(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {"index": 1, "start": 0.0, "end": 1.5, "text": "先看字幕"},
            {"index": 2, "start": 1.5, "end": 3.2, "text": "再检查成片"},
        ],
    )

    assert report["version"] == "subtitle_readability_qa.v1"
    assert report["summary"]["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert report["metrics"]["cues"][1]["visible_chars"] == 5


def test_high_cps_and_long_line_are_review_warnings(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {
                "index": 1,
                "start": 0.0,
                "end": 1.0,
                "text": "这是一条需要人工复核阅读速度和换行长度的字幕",
            },
        ],
        max_cps=30.0,
    )

    kinds = {(finding["kind"], finding["severity"]) for finding in report["findings"]}
    assert ("cps_high", "warn") in kinds
    assert ("line_too_long", "warn") in kinds
    assert report["summary"]["status"] == "review"
    assert report["summary"]["blocking"] == 0


def test_extreme_cps_and_flash_cue_block(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {"index": 1, "start": 0.0, "end": 0.1, "text": "这段字幕完全来不及读"},
        ],
    )

    kinds = {finding["kind"] for finding in report["findings"] if finding["severity"] == "block"}
    assert {"flash_cue", "cps_over_max"} <= kinds
    assert report["summary"]["status"] == "blocked"


def test_overlap_and_non_monotonic_file_order_block(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {"index": 1, "start": 1.0, "end": 2.5, "text": "第一句"},
            {"index": 2, "start": 0.5, "end": 1.5, "text": "第二句"},
        ],
    )

    kinds = {finding["kind"] for finding in report["findings"]}
    assert {"cue_overlap", "non_monotonic_order"} <= kinds
    assert report["summary"]["overlaps"] == 1


def test_media_duration_bounds_block_late_cue(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {"index": 1, "start": 3.0, "end": 5.2, "text": "字幕超过成片结尾"},
        ],
        media_path="output/final.mp4",
        media_duration=5.0,
    )

    finding = next(item for item in report["findings"] if item["kind"] == "media_out_of_bounds")
    assert finding["severity"] == "block"
    assert report["summary"]["out_of_bounds"] == 1
    assert report["source"]["media_duration"] == 5.0


def test_multiline_limit_and_long_duration_warn(tmp_path):
    report = _analyze(
        tmp_path,
        [
            {"index": 1, "start": 0.0, "end": 8.0, "text": "第一行\n第二行\n第三行"},
        ],
    )

    kinds = {finding["kind"] for finding in report["findings"]}
    assert {"too_many_lines", "long_duration"} <= kinds
    assert report["summary"]["blocking"] == 0


def test_markdown_contains_actionable_timing_evidence(tmp_path):
    report = _analyze(
        tmp_path,
        [{"index": 1, "start": 0.0, "end": 0.1, "text": "闪现字幕"}],
    )

    markdown = emit_markdown(report)

    assert "# Subtitle Readability QA" in markdown
    assert "flash_cue" in markdown
    assert "rendered master" in markdown


def test_cli_strict_writes_reports_and_exits_two(tmp_path):
    subtitle_path = tmp_path / "final_subtitles.json"
    output_path = tmp_path / "subtitle_readability_qa.json"
    markdown_path = tmp_path / "subtitle_readability_qa.md"
    subtitle_path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "settings": {"language": "zh"},
                "cues": [
                    {"index": 1, "start": 0.0, "end": 1.0, "text": "第一句"},
                    {"index": 2, "start": 0.8, "end": 2.0, "text": "第二句"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "subtitle_readability_qa.py"),
            str(subtitle_path),
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary"]["blocking"] == 1
    assert "Subtitle Readability QA" in markdown_path.read_text(encoding="utf-8")


def test_cli_turns_media_probe_failure_into_blocking_report(tmp_path, monkeypatch):
    subtitle_path = tmp_path / "final_subtitles.json"
    media_path = tmp_path / "broken.mp4"
    output_path = tmp_path / "subtitle_readability_qa.json"
    subtitle_path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [{"index": 1, "start": 0.0, "end": 1.0, "text": "第一句"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    media_path.write_bytes(b"not a video")
    monkeypatch.setattr(
        subtitle_qa,
        "probe_duration",
        lambda _path: (_ for _ in ()).throw(RuntimeError("ffprobe failed")),
    )

    exit_code = subtitle_qa.main(
        [
            str(subtitle_path),
            "--media",
            str(media_path),
            "--output",
            str(output_path),
            "--strict",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["findings"][0]["kind"] == "input_error"
    assert report["findings"][0]["message"] == "ffprobe failed"


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "subtitle_readability_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "reading speed" in result.stdout.lower()
