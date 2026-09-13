import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import black_edge_trim as trim  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


SOURCE_MEDIA = {
    "duration": 10.0,
    "video_duration": 10.0,
    "audio_duration": 10.0,
    "avg_frame_rate": "30/1",
    "r_frame_rate": "30/1",
    "avg_fps": 30.0,
    "nominal_fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "video_start_time": 0.0,
    "audio_start_time": 0.0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
    "pixel_format": "yuv420p",
    "bit_depth": 8,
    "sample_aspect_ratio": "1:1",
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "format_names": ["mov", "mp4"],
}


def _cadence(duration: float):
    frames = round(duration * 30)
    return {
        "algorithm": trim.CADENCE_ALGORITHM,
        "tolerance_ratio": 0.02,
        "tolerance_seconds": 0.000666667,
        "frame_count": frames,
        "interval_count": max(0, frames - 1),
        "non_monotonic_intervals": 0,
        "variable_intervals": 0,
        "variable_ratio": 0.0,
        "is_variable": False,
        "interval_seconds": {
            "min": 0.033333333,
            "p05": 0.033333333,
            "median": 0.033333333,
            "mean": 0.033333333,
            "p95": 0.033333333,
            "max": 0.033333333,
        },
    }


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "origin" / "capture.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    return source


def _patch_analysis(monkeypatch, *, silence=None):
    black = [
        {"start": 0.0, "end": 1.0, "duration": 1.0},
        {"start": 9.0, "end": 10.0, "duration": 1.0},
    ]
    monkeypatch.setattr(trim, "_detect_black", lambda *_args, **_kwargs: black)
    monkeypatch.setattr(
        trim,
        "_detect_silence",
        lambda *_args, **_kwargs: silence
        if silence is not None
        else [
            {"start": 0.0, "end": 1.0, "duration": 1.0},
            {"start": 9.0, "end": 10.0, "duration": 1.0},
        ],
    )


def _patch_media(monkeypatch, source: Path):
    def fake_media_info(path):
        path = Path(path).resolve()
        record = trim._fingerprint(path)
        if path == source.resolve():
            return {**record, **SOURCE_MEDIA, "cadence": _cadence(10.0)}
        duration = 3.3 if path.parent.name == "verify" else 8.2
        return {
            **record,
            **SOURCE_MEDIA,
            "duration": duration,
            "video_duration": duration,
            "audio_duration": duration,
            "cadence": _cadence(duration),
        }

    monkeypatch.setattr(trim, "_media_info", fake_media_info)


def _plan(tmp_path: Path, monkeypatch, **kwargs):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    _patch_analysis(monkeypatch)
    plan = trim.build_plan(
        "origin/capture.mp4",
        "work/capture-trimmed.mp4",
        "verify/capture-edge-proof.mp4",
        project_dir=str(tmp_path),
        trim_padding=0.1,
        **kwargs,
    )
    return source, plan


def test_parse_detectors_close_open_eof_ranges():
    black = trim.parse_blackdetect(
        "\n".join(
            [
                "[blackdetect] black_start:0 black_end:0.8 black_duration:0.8",
                "[blackdetect] black_start:9.2",
            ]
        ),
        duration=10.0,
    )
    silence = trim.parse_silencedetect(
        "\n".join(["silence_start: 0", "silence_end: 1.0", "silence_start: 9.0"]),
        duration=10.0,
    )
    assert black[-1] == {"start": 9.2, "end": 10.0, "duration": 0.8}
    assert silence[-1] == {"start": 9.0, "end": 10.0, "duration": 1.0}


def test_interval_normalization_merges_overlap_before_audio_coverage():
    intervals = trim._normalized_intervals(
        [
            {"start": 0.0, "end": 0.6},
            {"start": 0.4, "end": 0.8},
        ],
        1.0,
    )

    assert intervals == [{"start": 0.0, "end": 0.8, "duration": 0.8}]
    assert trim._coverage(0.0, 1.0, intervals) == pytest.approx(0.8)


def test_plan_keeps_padding_and_binds_silence_coverage(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)

    assert plan["analysis"]["trim_start_seconds"] == 0.9
    assert plan["analysis"]["trim_end_seconds"] == 9.1
    assert plan["analysis"]["output_duration_seconds"] == 8.2
    assert plan["analysis"]["proof_duration_seconds"] == 3.3
    assert all(item["silence_coverage"] == 1.0 for item in plan["analysis"]["removal_ranges"])
    assert plan["blockers"] == [trim.PENDING_APPLY]
    assert plan["source"]["path"] == str(source.resolve())


def test_silent_only_blocks_audible_black_and_override_warns(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    _patch_analysis(monkeypatch, silence=[{"start": 0.0, "end": 0.2, "duration": 0.2}])

    blocked = trim.build_plan(
        str(source),
        "work/blocked.mp4",
        "verify/blocked.mp4",
        project_dir=str(tmp_path),
        trim_padding=0.1,
    )
    assert any("silent_only requires" in item for item in blocked["blockers"])

    allowed = trim.build_plan(
        str(source),
        "work/allowed.mp4",
        "verify/allowed.mp4",
        project_dir=str(tmp_path),
        trim_padding=0.1,
        audio_policy="allow_audible",
    )
    assert allowed["blockers"] == [trim.PENDING_APPLY]
    assert any("allow_audible" in item for item in allowed["warnings"])


def test_apply_confirm_and_manifest_live_gate(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "black_edge_trim_plan.json"
    trim._atomic_write_json(plan_path, plan)
    commands = []

    def fake_checked(command, _label):
        command = list(command)
        commands.append(command)
        if command[-1] != "-":
            target = Path(command[-1])
            target.write_bytes(b"proof" if target.parent.name == "verify" else b"delivery")

    monkeypatch.setattr(trim, "_run_checked", fake_checked)
    applied = trim.apply_plan(str(plan_path))
    assert applied["blockers"] == [trim.PENDING_CONFIRM]
    render_command = next(command for command in commands if "edge-black trim" not in command and "-filter_complex" in command)
    assert "trim=start=0.900000:end=9.100000" in render_command[render_command.index("-filter_complex") + 1]
    assert any("concat=n=2" in token for command in commands for token in command)
    assert any("-xerror" in command for command in commands)

    confirmed = trim.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="watched both original edges and the complete working copy",
        full_playback="completed",
        proof_playback="completed",
        checks={field: "pass" for field in trim.REVIEW_FIELDS},
    )
    assert confirmed["status"] == "ready"
    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["black_edge_trim_plan"])
    gate = next(item for item in manifest["gates"] if item["category"] == "black_edge_trim_plan")
    assert gate["status"] == "ready"
    assert source.read_bytes() == b"source"


def test_verify_rejects_analysis_and_output_drift(tmp_path, monkeypatch):
    _source_file, plan = _plan(tmp_path, monkeypatch)
    plan["analysis"]["trim_start_seconds"] = 0.5
    plan["plan_id"] = trim._plan_id(plan)
    result = trim.verify_plan(plan)
    assert any("analysis changed" in item for item in result["blockers"])


def test_plan_rejects_hdr_path_escape_and_invalid_threshold(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_analysis(monkeypatch)

    def hdr_media(path):
        return {
            **trim._fingerprint(Path(path)),
            **SOURCE_MEDIA,
            "color_transfer": "smpte2084",
            "cadence": _cadence(10.0),
        }

    monkeypatch.setattr(trim, "_media_info", hdr_media)
    with pytest.raises(ValueError, match="hdr_sdr.py"):
        trim.build_plan(str(source), "work/out.mp4", "verify/proof.mp4", project_dir=str(tmp_path))
    with pytest.raises(ValueError, match="inside the project"):
        trim.build_plan(
            str(source),
            str(tmp_path.parent / "out.mp4"),
            "verify/proof.mp4",
            project_dir=str(tmp_path),
        )

    _patch_media(monkeypatch, source)
    with pytest.raises(ValueError, match="picture_black_ratio"):
        trim.build_plan(
            str(source),
            "work/out.mp4",
            "verify/proof.mp4",
            project_dir=str(tmp_path),
            picture_black_ratio=0.2,
        )
    with pytest.raises(ValueError, match="silence_noise"):
        trim.build_plan(
            str(source),
            "work/out.mp4",
            "verify/proof.mp4",
            project_dir=str(tmp_path),
            silence_noise="6dB",
        )


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "black_edge_trim.py"), "confirm", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "normal-speed" in result.stdout
