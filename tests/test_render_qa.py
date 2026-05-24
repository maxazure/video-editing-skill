import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from render_qa import (  # noqa: E402
    Segment,
    build_review_segments,
    evaluate_media,
    parse_blackdetect,
    parse_freezedetect,
    parse_silencedetect,
    write_review_packet,
)


def _meta(width=1080, height=1920, duration="45.0", audio=True):
    streams = [{
        "codec_type": "video",
        "width": width,
        "height": height,
        "avg_frame_rate": "30/1",
        "duration": duration,
    }]
    if audio:
        streams.append({
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "sample_rate": "48000",
            "duration": duration,
        })
    return {"format": {"duration": duration}, "streams": streams}


def test_parse_blackdetect_segments():
    log = "[blackdetect @ x] black_start:0 black_end:0.52 black_duration:0.52"
    segments = parse_blackdetect(log)
    assert segments == [Segment(start=0.0, end=0.52, duration=0.52)]


def test_parse_freezedetect_segments():
    log = "\n".join([
        "[freezedetect @ x] freeze_start: 3.24",
        "[freezedetect @ x] freeze_end: 5.80 | freeze_duration: 2.56",
    ])
    segments = parse_freezedetect(log)
    assert segments == [Segment(start=3.24, end=5.80, duration=2.56)]


def test_parse_silencedetect_segments():
    log = "\n".join([
        "[silencedetect @ x] silence_start: 10.1",
        "[silencedetect @ x] silence_end: 14.4 | silence_duration: 4.3",
    ])
    segments = parse_silencedetect(log)
    assert segments == [Segment(start=10.1, end=14.4, duration=4.3)]


def test_evaluate_passes_clean_douyin_video():
    report = evaluate_media(
        "out.mp4",
        _meta(),
        platform="douyin",
        allow_no_audio=False,
        min_duration=1.0,
        black_segments=[],
        freeze_segments=[],
        silence_segments=[],
        max_black_seconds=0.5,
        max_freeze_seconds=2.0,
        max_silence_seconds=3.0,
    )
    assert report["status"] == "pass"


def test_evaluate_fails_wrong_platform_dimensions():
    report = evaluate_media(
        "out.mp4",
        _meta(width=1080, height=1440),
        platform="douyin",
        allow_no_audio=False,
        min_duration=1.0,
        black_segments=[],
        freeze_segments=[],
        silence_segments=[],
        max_black_seconds=0.5,
        max_freeze_seconds=2.0,
        max_silence_seconds=3.0,
    )
    assert report["status"] == "fail"
    assert any(c["name"] == "platform_dimensions" and c["status"] == "fail" for c in report["checks"])


def test_evaluate_fails_black_frame_budget():
    report = evaluate_media(
        "out.mp4",
        _meta(),
        platform=None,
        allow_no_audio=False,
        min_duration=1.0,
        black_segments=[Segment(0.0, 1.2, 1.2)],
        freeze_segments=[],
        silence_segments=[],
        max_black_seconds=0.5,
        max_freeze_seconds=2.0,
        max_silence_seconds=3.0,
    )
    assert report["status"] == "fail"
    assert any(c["name"] == "black_frames" and c["status"] == "fail" for c in report["checks"])


def test_evaluate_no_audio_can_be_allowed_as_warning():
    report = evaluate_media(
        "out.mp4",
        _meta(audio=False),
        platform=None,
        allow_no_audio=True,
        min_duration=1.0,
        black_segments=[],
        freeze_segments=[],
        silence_segments=[],
        max_black_seconds=0.5,
        max_freeze_seconds=2.0,
        max_silence_seconds=3.0,
    )
    assert report["status"] == "warn"
    assert any(c["name"] == "audio_stream" and c["status"] == "warn" for c in report["checks"])


def test_build_review_segments_adds_padded_evidence_windows():
    report = {
        "status": "fail",
        "files": [{
            "path": "/tmp/final.mp4",
            "status": "fail",
            "checks": [
                {"name": "duration", "status": "pass", "message": "Duration 12.00s", "details": {"seconds": 12.0}},
                {
                    "name": "black_frames",
                    "status": "fail",
                    "message": "black_frames detected 0.80s, over 0.50s budget",
                    "details": {"segments": [{"start": 0.2, "end": 1.0, "duration": 0.8}]},
                },
            ],
        }],
    }

    segments = build_review_segments(report, padding=0.5)

    assert len(segments) == 1
    assert segments[0]["id"] == "final_black_frames_01"
    assert segments[0]["review_start"] == 0.0
    assert segments[0]["review_end"] == 1.5
    assert segments[0]["issue"] == "black_frames"


def test_write_review_packet_without_clips(tmp_path):
    report = {
        "status": "warn",
        "files": [{
            "path": "/tmp/final.mp4",
            "status": "warn",
            "checks": [
                {"name": "duration", "status": "pass", "message": "Duration 20.00s", "details": {"seconds": 20.0}},
                {
                    "name": "silence",
                    "status": "warn",
                    "message": "silence detected 2.20s within budget",
                    "details": {"segments": [{"start": 10.0, "end": 12.2, "duration": 2.2}]},
                },
            ],
        }],
    }

    paths = write_review_packet(report, str(tmp_path), padding=1.0, make_clips=False)

    assert os.path.isfile(paths["json"])
    assert os.path.isfile(paths["markdown"])
    with open(paths["json"], encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["schema"] == "render_qa_review.v1"
    assert manifest["segments"][0]["clip_path"] is None
    with open(paths["markdown"], encoding="utf-8") as f:
        markdown = f.read()
    assert "Segment Evidence" in markdown
    assert "final_silence_01" in markdown
