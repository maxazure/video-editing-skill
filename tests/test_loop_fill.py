import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import loop_fill  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


SOURCE_MEDIA = {
    "duration": 2.0,
    "video_duration": 2.0,
    "audio_duration": 2.0,
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


def _cadence(duration: float, *, variable: bool = False):
    frames = round(duration * 30)
    return {
        "algorithm": loop_fill.CADENCE_ALGORITHM,
        "tolerance_ratio": 0.02,
        "tolerance_seconds": 0.000666667,
        "frame_count": frames,
        "interval_count": max(0, frames - 1),
        "non_monotonic_intervals": 0,
        "variable_intervals": 1 if variable else 0,
        "variable_ratio": 0.01 if variable else 0.0,
        "is_variable": variable,
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
    source = tmp_path / "origin" / "background.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"loop-source")
    return source


def _patch_media(monkeypatch, source: Path, *, source_overrides=None, variable=False):
    source_overrides = source_overrides or {}

    def fake_media_info(path):
        path = Path(path).resolve()
        fingerprint = loop_fill._fingerprint(path)
        if path == source.resolve():
            media = {**SOURCE_MEDIA, **source_overrides}
            return {**fingerprint, **media, "cadence": _cadence(float(media["duration"]), variable=variable)}
        is_proof = path.parent.name == "verify"
        duration = 2.0 if is_proof else 5.0
        media = {
            **SOURCE_MEDIA,
            "duration": duration,
            "video_duration": duration,
            "audio_duration": duration,
        }
        return {**fingerprint, **media, "cadence": _cadence(duration)}

    monkeypatch.setattr(loop_fill, "_media_info", fake_media_info)


def _plan(tmp_path: Path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = loop_fill.build_plan(
        "origin/background.mp4",
        "work/background-5s.mp4",
        "verify/background-seam.mp4",
        duration="5",
        project_dir=str(tmp_path),
    )
    return source, plan


def test_parse_duration_and_plan_exact_repeat_contract(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)

    assert loop_fill.parse_duration("01:30") == 90
    assert loop_fill.parse_duration("1:02:03.5") == 3723.5
    assert plan["settings"]["target_duration_seconds"] == 5.0
    assert plan["settings"]["source_reads"] == 3
    assert plan["settings"]["stream_loop"] == 2
    assert plan["settings"]["seam_count"] == 2
    assert plan["settings"]["partial_final_cycle"] is True
    assert plan["blockers"] == [loop_fill.PENDING_APPLY]
    assert plan["source"]["path"] == str(source.resolve())


def test_times_mode_and_audio_drop_are_canonical(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = loop_fill.build_plan(
        str(source),
        "work/background-4s.mp4",
        "verify/background-seam.mp4",
        times=2,
        audio_mode="drop",
        project_dir=str(tmp_path),
    )

    assert plan["settings"]["request_kind"] == "times"
    assert plan["settings"]["target_duration_seconds"] == 4.0
    assert plan["settings"]["output_has_audio"] is False
    assert plan["review_contract"]["audio_transition_expected"] is False


def test_plan_rejects_vfr_hdr_short_target_and_path_escape(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source, variable=True)
    with pytest.raises(ValueError, match="CFR working copy"):
        loop_fill.build_plan(
            str(source), "work/out.mp4", "verify/seam.mp4", times=2, project_dir=str(tmp_path)
        )

    _patch_media(monkeypatch, source, source_overrides={"color_transfer": "smpte2084"})
    with pytest.raises(ValueError, match="hdr_sdr.py"):
        loop_fill.build_plan(
            str(source), "work/out.mp4", "verify/seam.mp4", times=2, project_dir=str(tmp_path)
        )

    _patch_media(monkeypatch, source)
    with pytest.raises(ValueError, match="longer than the source"):
        loop_fill.build_plan(
            str(source), "work/out.mp4", "verify/seam.mp4", duration="2", project_dir=str(tmp_path)
        )
    with pytest.raises(ValueError, match="integer"):
        loop_fill.build_plan(
            str(source), "work/out.mp4", "verify/seam.mp4", times=2.5, project_dir=str(tmp_path)
        )
    with pytest.raises(ValueError, match="inside the project"):
        loop_fill.build_plan(
            str(source), str(tmp_path.parent / "out.mp4"), "verify/seam.mp4", times=2, project_dir=str(tmp_path)
        )


def test_apply_then_confirm_produces_live_ready_manifest_gate(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "loop_fill_plan.json"
    loop_fill._atomic_write_json(plan_path, plan)
    commands = []

    def fake_checked(command, _label):
        command = list(command)
        commands.append(command)
        if command[-1] != "-":
            target = Path(command[-1])
            target.write_bytes(b"proof" if target.parent.name == "verify" else b"delivery")

    monkeypatch.setattr(loop_fill, "_run_checked", fake_checked)

    applied = loop_fill.apply_plan(str(plan_path))
    assert applied["blockers"] == [loop_fill.PENDING_CONFIRM]
    assert (tmp_path / "work" / "background-5s.mp4").read_bytes() == b"delivery"
    assert (tmp_path / "verify" / "background-seam.mp4").read_bytes() == b"proof"
    render_command = next(command for command in commands if "-stream_loop" in command)
    assert render_command[render_command.index("-stream_loop") + 1] == "2"
    assert any("-xerror" in command for command in commands)

    failed_checks = {field: "pass" for field in loop_fill.REVIEW_FIELDS}
    failed_checks["visual_transition"] = "fail"
    failed_review = loop_fill.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="first review found a visible jump",
        full_playback="completed",
        seam_playback="completed",
        checks=failed_checks,
    )
    assert failed_review["status"] == "blocked"

    confirmed = loop_fill.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="re-reviewed the replacement source and first seam at normal speed",
        full_playback="completed",
        seam_playback="completed",
        checks={field: "pass" for field in loop_fill.REVIEW_FIELDS},
    )
    assert confirmed["status"] == "ready"
    assert confirmed["summary"]["blocking"] == 0

    manifest = build_manifest(
        str(tmp_path), target_stage="analysis", required=["loop_fill_plan"]
    )
    gate = next(item for item in manifest["gates"] if item["category"] == "loop_fill_plan")
    assert gate["status"] == "ready"
    assert source.read_bytes() == b"loop-source"


def test_confirm_without_delivered_audio_requires_not_applicable(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = loop_fill.build_plan(
        str(source),
        "work/background-4s.mp4",
        "verify/background-seam.mp4",
        times=2,
        audio_mode="drop",
        project_dir=str(tmp_path),
    )
    output = tmp_path / "work" / "background-4s.mp4"
    proof = tmp_path / "verify" / "background-seam.mp4"
    output.parent.mkdir(parents=True)
    proof.parent.mkdir(parents=True)
    output.write_bytes(b"delivery")
    proof.write_bytes(b"proof")

    def silent_media_info(path):
        record = loop_fill._fingerprint(Path(path))
        if Path(path).resolve() == source.resolve():
            return {
                **record,
                **SOURCE_MEDIA,
                "cadence": _cadence(2.0),
            }
        duration = 2.0 if Path(path).parent.name == "verify" else 4.0
        return {
            **record,
            **SOURCE_MEDIA,
            "duration": duration,
            "video_duration": duration,
            "audio_duration": None,
            "has_audio": False,
            "audio_codec": None,
            "sample_rate": None,
            "channels": None,
            "cadence": _cadence(duration),
        }

    monkeypatch.setattr(loop_fill, "_media_info", silent_media_info)
    plan["application"] = {
        "applied_at": loop_fill.utc_now(),
        "output": silent_media_info(output),
        "seam_proof": silent_media_info(proof),
        "validation": {
            "validated_at": loop_fill.utc_now(),
            "output_decode_checked": True,
            "output_decode_command": loop_fill._decode_command(output),
            "proof_decode_checked": True,
            "proof_decode_command": loop_fill._decode_command(proof),
            "output_sha256": loop_fill._sha256(output),
            "seam_proof_sha256": loop_fill._sha256(proof),
            "first_seam_seconds": 2.0,
            "cadence_algorithm": loop_fill.CADENCE_ALGORITHM,
        },
    }
    loop_fill._set_derived(plan)
    plan_path = tmp_path / "work" / "loop_fill_plan.json"
    loop_fill._atomic_write_json(plan_path, plan)
    checks = {field: "pass" for field in loop_fill.REVIEW_FIELDS}
    checks["audio_transition"] = "not_applicable"

    confirmed = loop_fill.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="silent background reviewed",
        full_playback="completed",
        seam_playback="completed",
        checks=checks,
    )
    assert confirmed["status"] == "ready"


def test_verify_rejects_output_review_and_settings_drift(tmp_path, monkeypatch):
    _source_file, plan = _plan(tmp_path, monkeypatch)
    plan["settings"]["video_crf"] = 30
    plan["plan_id"] = loop_fill._plan_id(plan)

    result = loop_fill.verify_plan(plan)
    assert any("settings do not match" in item for item in result["blockers"])


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "loop_fill.py"), "confirm", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "normal-speed" in result.stdout
