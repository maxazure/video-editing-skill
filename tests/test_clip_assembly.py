import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import clip_assembly  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


BASE_MEDIA = {
    "duration": 1.5,
    "video_duration": 1.5,
    "audio_duration": 1.5,
    "avg_frame_rate": "30/1",
    "r_frame_rate": "30/1",
    "avg_fps": 30.0,
    "nominal_fps": 30.0,
    "width": 320,
    "height": 180,
    "rotation": 0,
    "video_start_time": 0.0,
    "audio_start_time": 0.0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "sample_rate": 44100,
    "channels": 1,
    "pixel_format": "yuv420p",
    "bit_depth": 8,
    "sample_aspect_ratio": "1:1",
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "format_names": ["mov", "mp4"],
}


def _cadence(duration):
    frames = round(duration * 30)
    return {
        "algorithm": clip_assembly.CADENCE_ALGORITHM,
        "tolerance_ratio": 0.02,
        "tolerance_seconds": 0.000666667,
        "frame_count": frames,
        "interval_count": frames - 1,
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


def _source_info(path, **overrides):
    media = {**BASE_MEDIA, **overrides}
    return {
        **clip_assembly._fingerprint(path),
        **media,
        "cadence": _cadence(float(media["video_duration"])),
    }


def _project(tmp_path):
    first = tmp_path / "origin" / "one.mp4"
    second = tmp_path / "origin" / "two.mp4"
    third = tmp_path / "origin" / "three.mp4"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    third.write_bytes(b"three")
    return first, second, third


def _patch_sources(monkeypatch, first, second, third=None):
    source_records = {
        first.resolve(): _source_info(first, width=640, height=360),
        second.resolve(): _source_info(
            second,
            duration=1.0,
            video_duration=1.0,
            audio_duration=None,
            width=240,
            height=320,
            has_audio=False,
            audio_codec=None,
            sample_rate=None,
            channels=None,
            audio_start_time=None,
            sample_aspect_ratio="4:3",
        ),
    }
    if third is not None:
        source_records[third.resolve()] = _source_info(
            third,
            duration=2.0,
            video_duration=2.0,
            audio_duration=None,
            has_audio=False,
            audio_codec=None,
            sample_rate=None,
            channels=None,
            audio_start_time=None,
        )

    def fake_media_info(path):
        path = Path(path).resolve()
        if path in source_records:
            return {**source_records[path], **clip_assembly._fingerprint(path)}
        is_proof = "proof" in path.name
        duration = 1.0 if is_proof else 2.5
        return {
            **clip_assembly._fingerprint(path),
            **BASE_MEDIA,
            "duration": duration,
            "video_duration": duration,
            "audio_duration": duration,
            "width": 320,
            "height": 180,
            "sample_rate": 48000,
            "channels": 2,
            "cadence": _cadence(duration),
        }

    monkeypatch.setattr(clip_assembly, "_media_info", fake_media_info)
    return source_records


def _plan(tmp_path, monkeypatch):
    first, second, _third = _project(tmp_path)
    _patch_sources(monkeypatch, first, second)
    plan = clip_assembly.build_plan(
        ["origin/one.mp4", "origin/two.mp4"],
        "work/assembled.mp4",
        "verify/assembly-proof.mp4",
        width=320,
        height=180,
        fps="30/1",
        proof_context_seconds=0.5,
        project_dir=str(tmp_path),
    )
    return first, second, plan


def test_plan_binds_order_normalization_and_all_boundary_windows(tmp_path, monkeypatch):
    first, second, plan = _plan(tmp_path, monkeypatch)

    assert [item["path"] for item in plan["sources"]] == [str(first), str(second)]
    assert plan["settings"]["expected_duration_seconds"] == 2.5
    assert plan["settings"]["target_rate"]["rational"] == "30/1"
    assert plan["settings"]["output_has_audio"] is True
    assert plan["settings"]["normalizations"][1]["audio"] == "synthetic_silence"
    assert plan["settings"]["boundaries"] == [
        {
            "id": "boundary-001",
            "before_source_index": 1,
            "after_source_index": 2,
            "output_time_seconds": 1.5,
            "window_start_seconds": 1.0,
            "window_end_seconds": 2.0,
            "window_duration_seconds": 1.0,
            "proof_start_seconds": 0.0,
        }
    ]
    assert plan["blockers"] == [clip_assembly.PENDING_APPLY]
    assert "synthetic 48 kHz" in plan["warnings"][0]


def test_command_normalizes_every_stream_and_indexes_multiple_silent_inputs(tmp_path, monkeypatch):
    first, second, third = _project(tmp_path)
    _patch_sources(monkeypatch, first, second, third)
    plan = clip_assembly.build_plan(
        [str(first), str(second), str(third)],
        "work/assembled.mp4",
        "verify/assembly-proof.mp4",
        width=320,
        height=180,
        fps="30000/1001",
        fit="crop",
        project_dir=str(tmp_path),
    )

    command = clip_assembly.build_command(plan, tmp_path / "work" / "temporary.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert graph.count("force_original_aspect_ratio=increase") == 3
    assert graph.count("setsar=1") == 3
    assert graph.count("fps=fps=30000/1001") == 3
    assert "[3:a:0]" in graph
    assert "[4:a:0]" in graph
    assert "concat=n=3:v=1:a=1[vout][aout]" in graph


def test_plan_rejects_unsafe_sources_dimensions_duplicates_and_escape(tmp_path, monkeypatch):
    first, second, _third = _project(tmp_path)
    records = _patch_sources(monkeypatch, first, second)

    records[first.resolve()]["color_transfer"] = "smpte2084"
    with pytest.raises(ValueError, match="hdr_sdr.py"):
        clip_assembly.build_plan(
            [str(first), str(second)], "work/out.mp4", "verify/proof.mp4", project_dir=str(tmp_path)
        )
    records[first.resolve()]["color_transfer"] = "bt709"
    with pytest.raises(ValueError, match="even"):
        clip_assembly.build_plan(
            [str(first), str(second)],
            "work/out.mp4",
            "verify/proof.mp4",
            width=319,
            height=180,
            project_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="unique"):
        clip_assembly.build_plan(
            [str(first), str(first)], "work/out.mp4", "verify/proof.mp4", project_dir=str(tmp_path)
        )
    with pytest.raises(ValueError, match="inside"):
        clip_assembly.build_plan(
            [str(first), str(second)],
            str(tmp_path.parent / "out.mp4"),
            "verify/proof.mp4",
            project_dir=str(tmp_path),
        )

    records[first.resolve()]["audio_duration"] = 0.5
    with pytest.raises(ValueError, match="boundaries differ"):
        clip_assembly.build_plan(
            [str(first), str(second)], "work/out.mp4", "verify/proof.mp4", project_dir=str(tmp_path)
        )
    dropped = clip_assembly.build_plan(
        [str(first), str(second)],
        "work/out.mp4",
        "verify/proof.mp4",
        audio_mode="drop",
        project_dir=str(tmp_path),
    )
    assert dropped["settings"]["output_has_audio"] is False


def test_apply_confirm_and_manifest_live_gate(tmp_path, monkeypatch):
    first, second, plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "clip_assembly_plan.json"
    clip_assembly._atomic_write_json(plan_path, plan)
    commands = []

    def fake_checked(command, _label):
        command = list(command)
        commands.append(command)
        if command[-1] != "-":
            target = Path(command[-1])
            target.write_bytes(b"proof" if "proof" in target.name else b"delivery")

    monkeypatch.setattr(clip_assembly, "_run_checked", fake_checked)
    applied = clip_assembly.apply_plan(str(plan_path))
    assert applied["blockers"] == [clip_assembly.PENDING_CONFIRM]
    render_command = next(command for command in commands if "anullsrc=r=48000:cl=stereo" in command)
    assert "concat=n=2:v=1:a=1[vout][aout]" in render_command[render_command.index("-filter_complex") + 1]

    confirmed = clip_assembly.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="watched the complete output and all hard-cut proof windows",
        full_playback="completed",
        proof_playback="completed",
        checks={field: "pass" for field in clip_assembly.REVIEW_FIELDS},
    )
    assert confirmed["summary"]["blocking"] == 0
    assert confirmed["status"] == "warn"

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["clip_assembly_plan"])
    gate = next(item for item in manifest["gates"] if item["category"] == "clip_assembly_plan")
    assert gate["status"] == "warn"
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


def test_audio_drop_requires_not_applicable_review(tmp_path, monkeypatch):
    first, second, _third = _project(tmp_path)
    _patch_sources(monkeypatch, first, second)
    plan = clip_assembly.build_plan(
        [str(first), str(second)],
        "work/assembled.mp4",
        "verify/assembly-proof.mp4",
        width=320,
        height=180,
        fps="30",
        audio_mode="drop",
        proof_context_seconds=0.5,
        project_dir=str(tmp_path),
    )
    command = clip_assembly.build_command(plan, tmp_path / "work" / "temporary.mp4")
    assert "-an" in command
    assert "anullsrc=r=48000:cl=stereo" not in command
    assert plan["review_contract"]["audio_transition_expected"] is False


def test_verify_rejects_source_settings_output_and_review_drift(tmp_path, monkeypatch):
    _first, _second, plan = _plan(tmp_path, monkeypatch)
    plan["settings"]["expected_duration_seconds"] = 99.0
    result = clip_assembly.verify_plan(plan)
    assert result["status"] == "blocked"
    assert any("plan_id" in item for item in result["blockers"])
    assert any("canonical clip-assembly" in item for item in result["blockers"])


def test_cli_help_and_missing_input_error():
    help_result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "clip_assembly.py"), "plan", "--help"],
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "--boundary-proof" in help_result.stdout

    error = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "clip_assembly.py"),
            "plan",
            "missing.mp4",
            "--delivery",
            "out.mp4",
            "--boundary-proof",
            "proof.mp4",
            "--output",
            "plan.json",
        ],
        capture_output=True,
        text=True,
    )
    assert error.returncode == 2
    assert "provide at least two" in error.stderr
