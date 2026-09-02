import json
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import caption_speech_qa  # noqa: E402
from caption_speech_qa import (  # noqa: E402
    active_intervals_from_silence,
    analyze_cues,
    build_report,
    emit_markdown,
    normalize_settings,
    parse_silence_log,
    verify_report,
)


MEDIA = {
    "duration": 3.0,
    "format_name": "wav",
    "audio_stream_index": 0,
    "audio_codec": "pcm_s16le",
    "sample_rate": 48000,
    "channels": 1,
    "channel_layout": "mono",
}


def _write_subtitles(path, cues):
    path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "source": "work/render_config.json",
                "settings": {"speed": 1.0, "offset": 0.0},
                "cues": cues,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _cue(index, start, end, text="字幕"):
    return {"index": index, "start": start, "end": end, "text": text}


def _probe(_path):
    return dict(MEDIA)


def _measure(_path, *, duration, settings):
    assert duration == 3.0
    assert settings["noise_db"] == -36.0
    return [[0.0, 0.2], [1.2, 1.8], [2.8, 3.0]]


def test_parse_silence_log_handles_open_and_merged_intervals():
    log = """
[silencedetect] silence_start: 0
[silencedetect] silence_end: 0.200000 | silence_duration: 0.2
[silencedetect] silence_start: 1.2
[silencedetect] silence_end: 1.8 | silence_duration: 0.6
[silencedetect] silence_start: 2.8
"""
    assert parse_silence_log(log, duration=3.0) == [
        [0.0, 0.2],
        [1.2, 1.8],
        [2.8, 3.0],
    ]


def test_active_intervals_are_silence_inverse():
    assert active_intervals_from_silence(
        [[0.0, 0.2], [1.2, 1.8], [2.8, 3.0]], duration=3.0
    ) == [[0.2, 1.2], [1.8, 2.8]]


def test_clean_cues_pass_against_active_speech():
    settings = normalize_settings()
    cues = [_cue(1, 0.25, 1.1), _cue(2, 1.85, 2.7)]
    analysis, checks = analyze_cues(
        cues,
        silences=[[0.0, 0.2], [1.2, 1.8], [2.8, 3.0]],
        duration=3.0,
        settings=settings,
    )

    assert analysis["active_intervals"] == [[0.2, 1.2], [1.8, 2.8]]
    assert analysis["captioned_active_ratio"] == pytest.approx(0.85)
    assert all(item["status"] == "pass" for item in checks)


def test_orphan_and_timeline_overflow_cues_block():
    settings = normalize_settings()
    cues = [_cue(1, 1.3, 1.7, "silent"), _cue(2, 2.9, 3.4, "outside")]
    analysis, checks = analyze_cues(
        cues,
        silences=[[1.2, 1.8], [2.8, 3.0]],
        duration=3.0,
        settings=settings,
    )

    assert analysis["cue_metrics"][0]["active_seconds"] == 0.0
    assert checks[0]["status"] == "block"
    assert "no measurable audio activity" in checks[0]["message"]
    assert checks[1]["status"] == "block"
    assert "leaves the 3.000s" in checks[1]["message"]


def test_long_edge_silence_blocks_and_internal_pause_warns():
    settings = normalize_settings()
    cues = [_cue(1, 0.0, 1.1), _cue(2, 1.2, 2.8)]
    _, checks = analyze_cues(
        cues,
        silences=[[0.0, 0.7], [1.6, 2.5]],
        duration=3.0,
        settings=settings,
    )

    assert checks[0]["status"] == "block"
    assert "leading silence is 0.700s" in checks[0]["message"]
    assert checks[1]["status"] == "warn"
    assert "internal silence reaches 0.900s" in checks[1]["message"]


def test_build_and_live_verify_bind_both_inputs_and_measurements(tmp_path):
    speech = tmp_path / "narration.wav"
    speech.write_bytes(b"speech bytes")
    subtitles = tmp_path / "subtitles.json"
    _write_subtitles(subtitles, [_cue(1, 0.25, 1.1), _cue(2, 1.85, 2.7)])
    report = build_report(
        speech,
        subtitles,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )

    assert report["summary"]["status"] == "ready"
    current = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert current["summary"]["blocking"] == 0

    tampered = json.loads(json.dumps(report))
    tampered["analysis"]["cue_metrics"][0]["active_ratio"] = 0.0
    tampered["report_id"] = caption_speech_qa._canonical_sha256(
        caption_speech_qa._report_snapshot(tampered)
    )
    stale = verify_report(
        tampered,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert "live cue/audio measurements drifted" in stale["blockers"]

    subtitles.write_text(subtitles.read_text(encoding="utf-8").replace("字幕", "改字"), encoding="utf-8")
    changed = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    assert "subtitle pack bytes or cue contract drifted" in changed["blockers"]


def test_live_verify_preserves_current_orphan_blocker(tmp_path):
    speech = tmp_path / "narration.wav"
    speech.write_bytes(b"speech bytes")
    subtitles = tmp_path / "subtitles.json"
    _write_subtitles(subtitles, [_cue(1, 1.3, 1.7, "orphan")])
    report = build_report(
        speech,
        subtitles,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    verification = verify_report(
        report,
        tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )

    assert report["summary"]["status"] == "blocked"
    assert verification["summary"]["blocking"] >= 1
    assert any("cue 1" in item for item in verification["blockers"])


def test_settings_and_subtitle_schema_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="unknown settings"):
        normalize_settings({"mystery": 1})
    with pytest.raises(ValueError, match="active ratios"):
        normalize_settings({"min_active_ratio": 0.8, "warn_active_ratio": 0.5})

    speech = tmp_path / "narration.wav"
    speech.write_bytes(b"speech bytes")
    subtitles = tmp_path / "subtitles.json"
    subtitles.write_text('{"version":"other.v1","cues":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="subtitle_pack.v1"):
        build_report(
            speech,
            subtitles,
            project_dir=tmp_path,
            probe_fn=_probe,
            measure_fn=_measure,
        )

    _write_subtitles(subtitles, [_cue(1, 0.2, 0.8), _cue(1, 1.0, 1.6)])
    with pytest.raises(ValueError, match="cue id must be unique"):
        build_report(
            speech,
            subtitles,
            project_dir=tmp_path,
            probe_fn=_probe,
            measure_fn=_measure,
        )


def test_project_escape_symlink_and_hardlink_output_are_rejected(tmp_path, monkeypatch):
    speech = tmp_path / "narration.wav"
    speech.write_bytes(b"speech bytes")
    subtitles = tmp_path / "subtitles.json"
    _write_subtitles(subtitles, [_cue(1, 0.25, 1.1)])

    outside = tmp_path.parent / "outside-speech.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="inside the project"):
        build_report(
            outside,
            subtitles,
            project_dir=tmp_path,
            probe_fn=_probe,
            measure_fn=_measure,
        )
    outside.unlink()

    link = tmp_path / "speech-link.wav"
    link.symlink_to(speech)
    with pytest.raises(ValueError, match="symlink"):
        build_report(
            link,
            subtitles,
            project_dir=tmp_path,
            probe_fn=_probe,
            measure_fn=_measure,
        )

    output = tmp_path / "report.json"
    os.link(speech, output)
    monkeypatch.setattr(caption_speech_qa, "probe_audio_media", _probe)
    monkeypatch.setattr(caption_speech_qa, "measure_silences", _measure)
    result = caption_speech_qa.main(
        [
            "analyze",
            str(speech),
            "--subtitle-pack",
            str(subtitles),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(output),
            "--force",
        ]
    )
    assert result == 1
    assert speech.read_bytes() == b"speech bytes"


def test_markdown_and_cli_help_explain_isolated_speech_boundary(tmp_path):
    speech = tmp_path / "narration.wav"
    speech.write_bytes(b"speech bytes")
    subtitles = tmp_path / "subtitles.json"
    _write_subtitles(subtitles, [_cue(1, 0.25, 1.1)])
    report = build_report(
        speech,
        subtitles,
        project_dir=tmp_path,
        probe_fn=_probe,
        measure_fn=_measure,
    )
    markdown = emit_markdown(report)
    assert "Captioned active-audio ratio" in markdown
    assert "without BGM or SFX" in markdown

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "caption_speech_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "isolated speech track" in result.stdout
