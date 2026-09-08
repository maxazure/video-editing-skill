import json
import os
import shutil
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import stream_coverage_qa  # noqa: E402
from stream_coverage_qa import (  # noqa: E402
    build_report,
    emit_markdown,
    evaluate_analysis,
    normalize_settings,
    summarize_frames,
    verify_report,
)


def _stream(kind, *, frames=None):
    base = {
        "index": 0 if kind == "video" else 1,
        "codec_type": kind,
        "codec_name": "h264" if kind == "video" else "aac",
        "time_base": "1/1000",
        "start_time": 0.0,
        "duration": 1.0,
        "nb_frames": frames,
        "avg_frame_rate": "10/1" if kind == "video" else "0/0",
        "r_frame_rate": "10/1" if kind == "video" else "0/0",
        "sample_rate": 48000 if kind == "audio" else None,
        "channels": 2 if kind == "audio" else None,
        "width": 320 if kind == "video" else None,
        "height": 180 if kind == "video" else None,
    }
    return base


def _media(*, audio=True, video_frames=10):
    return {
        "container": {"format_name": "mov,mp4", "start_time": 0.0, "duration": 1.0, "end_time": 1.0},
        "video_streams": [_stream("video", frames=video_frames)],
        "audio_streams": [_stream("audio")] if audio else [],
    }


def _rows(count=10, *, step=0.1, duration=0.1, start=0.0):
    return [
        {
            "best_effort_timestamp_time": f"{start + index * step:.6f}",
            "pkt_duration_time": f"{duration:.6f}",
        }
        for index in range(count)
    ]


def _probe(_path):
    return _media()


def _measure(_path, selector):
    assert selector in {"v:0", "a:0"}
    return _rows()


def _decode(_path):
    return {"passed": True, "returncode": 0, "error": ""}


def test_summarize_frames_uses_decoded_pts_and_detects_regression():
    rows = _rows(3)
    rows[2]["best_effort_timestamp_time"] = "0.050000"
    result = summarize_frames(rows, kind="video", stream=_stream("video"))

    assert result["frame_count"] == 3
    assert result["first_pts"] == 0.0
    assert result["timestamp_regressions"] == 1
    assert len(result["timeline_sha256"]) == 64


def test_summarize_audio_uses_nb_samples_when_packet_duration_is_missing():
    rows = [
        {"best_effort_timestamp_time": "0.000000", "nb_samples": "1024", "sample_rate": "48000"},
        {"best_effort_timestamp_time": "0.021333", "nb_samples": "1024", "sample_rate": "48000"},
    ]
    result = summarize_frames(rows, kind="audio", stream=_stream("audio"))

    assert result["frame_count"] == 2
    assert result["decoded_end"] == pytest.approx(0.042666, abs=1e-6)


def test_clean_decoded_streams_are_ready(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"media bytes")
    report = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )

    assert report["summary"] == {"status": "ready", "blocking": 0, "warnings": 0, "checks": 10}
    assert report["analysis"]["video"]["decoded_end"] == 1.0
    assert report["analysis"]["audio"]["decoded_end"] == 1.0


def test_truncated_video_blocks_av_and_container_tail_coverage():
    media = _media()
    analysis = {
        "full_decode": _decode(None),
        "video": summarize_frames(_rows(5), kind="video", stream=media["video_streams"][0]),
        "audio": summarize_frames(_rows(), kind="audio", stream=media["audio_streams"][0]),
    }
    checks = {item["name"]: item for item in evaluate_analysis(media, analysis, normalize_settings())}

    assert checks["av_end_coverage"]["status"] == "block"
    assert checks["video_container_coverage"]["status"] == "block"
    assert checks["audio_container_coverage"]["status"] == "pass"


def test_start_skew_and_failed_full_decode_block():
    media = _media()
    analysis = {
        "full_decode": {"passed": False, "returncode": 1, "error": "corrupt packet"},
        "video": summarize_frames(_rows(), kind="video", stream=media["video_streams"][0]),
        "audio": summarize_frames(_rows(start=0.2), kind="audio", stream=media["audio_streams"][0]),
    }
    checks = {item["name"]: item for item in evaluate_analysis(media, analysis, normalize_settings())}

    assert checks["full_decode"]["status"] == "block"
    assert checks["av_start_coverage"]["status"] == "block"


def test_expected_duration_and_frame_count_are_explicit_gates(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"media bytes")
    report = build_report(
        source,
        project_dir=tmp_path,
        settings={"expected_duration_seconds": 1.2, "expected_video_frames": 12},
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    by_name = {item["name"]: item for item in report["checks"]}

    assert by_name["container_expected_duration"]["status"] == "block"
    assert by_name["video_expected_duration"]["status"] == "block"
    assert by_name["video_expected_frame_count"]["status"] == "block"


def test_missing_audio_can_be_blocked_or_explicitly_allowed(tmp_path):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"video bytes")

    def no_audio_probe(_path):
        return _media(audio=False)

    def video_only_measure(_path, selector):
        assert selector == "v:0"
        return _rows()

    blocked = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=no_audio_probe,
        measure_fn=video_only_measure,
        decode_fn=_decode,
    )
    allowed = build_report(
        source,
        project_dir=tmp_path,
        settings={"require_audio": False},
        probe_fn=no_audio_probe,
        measure_fn=video_only_measure,
        decode_fn=_decode,
    )

    assert blocked["summary"]["blocking"] == 2
    assert allowed["summary"]["blocking"] == 0
    assert allowed["summary"]["warnings"] == 1


def test_live_verify_detects_source_and_derived_drift(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"media bytes")
    report = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    current = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    assert current["summary"]["blocking"] == 0

    tampered = json.loads(json.dumps(report))
    tampered["analysis"]["video"]["decoded_end"] = 0.5
    tampered["report_id"] = stream_coverage_qa._canonical_sha256(
        stream_coverage_qa._report_snapshot(tampered)
    )
    stale = verify_report(
        tampered,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    assert "live decoded timeline measurements drifted" in stale["blockers"]

    source.write_bytes(b"changed bytes")
    changed = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    assert "source bytes or media contract drifted" in changed["blockers"]


def test_settings_and_project_path_validation(tmp_path):
    with pytest.raises(ValueError, match="unknown settings"):
        normalize_settings({"mystery": 1})
    with pytest.raises(ValueError, match="positive"):
        normalize_settings({"expected_duration_seconds": 0})

    outside = tmp_path.parent / "outside-video.mp4"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="inside the project"):
        build_report(outside, project_dir=tmp_path, probe_fn=_probe, measure_fn=_measure, decode_fn=_decode)
    outside.unlink()


def test_symlink_source_and_hardlink_output_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"media bytes")
    link = tmp_path / "source-link.mp4"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        build_report(link, project_dir=tmp_path, probe_fn=_probe, measure_fn=_measure, decode_fn=_decode)

    output = tmp_path / "report.json"
    os.link(source, output)
    monkeypatch.setattr(stream_coverage_qa, "probe_media", _probe)
    monkeypatch.setattr(stream_coverage_qa, "measure_stream_frames", _measure)
    monkeypatch.setattr(stream_coverage_qa, "full_decode", _decode)
    result = stream_coverage_qa.main(
        [
            "analyze",
            str(source),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(output),
            "--force",
        ]
    )
    assert result == 1
    assert source.read_bytes() == b"media bytes"


def test_markdown_and_cli_help_explain_the_container_gap(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"media bytes")
    report = build_report(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
        decode_fn=_decode,
    )
    markdown = emit_markdown(report)
    assert "Decoded coverage" in markdown
    assert "playable container can hide" in markdown

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "stream_coverage_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "container" in result.stdout and "duration alone" in result.stdout


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is unavailable")
def test_real_ffmpeg_round_trip(tmp_path):
    source = tmp_path / "final.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    report = build_report(source, project_dir=tmp_path)
    verification = verify_report(report, tmp_path)

    assert report["summary"]["blocking"] == 0
    assert report["analysis"]["video"]["frame_count"] == 10
    assert verification["summary"]["blocking"] == 0
