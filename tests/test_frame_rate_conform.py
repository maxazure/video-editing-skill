import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import frame_rate_conform as conform  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


SOURCE_MEDIA = {
    "duration": 4.0,
    "video_duration": 4.0,
    "audio_duration": 4.0,
    "avg_frame_rate": "96/5",
    "r_frame_rate": "30/1",
    "avg_fps": 19.2,
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

SOURCE_CADENCE = {
    "algorithm": conform.CADENCE_ALGORITHM,
    "tolerance_ratio": 0.02,
    "tolerance_seconds": 0.000666667,
    "frame_count": 78,
    "interval_count": 77,
    "non_monotonic_intervals": 0,
    "variable_intervals": 20,
    "variable_ratio": 0.25974026,
    "is_variable": True,
    "interval_seconds": {
        "min": 0.033333333,
        "p05": 0.033333333,
        "median": 0.033333333,
        "mean": 0.051515152,
        "p95": 0.1,
        "max": 0.133333333,
    },
}

OUTPUT_MEDIA = {
    **SOURCE_MEDIA,
    "avg_frame_rate": "30/1",
    "r_frame_rate": "30/1",
    "avg_fps": 30.0,
    "nominal_fps": 30.0,
    "audio_codec": "aac",
    "sample_rate": 48000,
}

OUTPUT_CADENCE = {
    **SOURCE_CADENCE,
    "frame_count": 120,
    "interval_count": 119,
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
    source = tmp_path / "origin" / "phone.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"vfr-source")
    return source


def _patch_media(monkeypatch, source: Path):
    def fake_probe(path):
        return dict(SOURCE_MEDIA if Path(path).resolve() == source.resolve() else OUTPUT_MEDIA)

    def fake_cadence(path, *, tolerance_ratio=0.02):
        value = SOURCE_CADENCE if Path(path).resolve() == source.resolve() else OUTPUT_CADENCE
        return {**value, "tolerance_ratio": tolerance_ratio}

    monkeypatch.setattr(conform, "probe_media", fake_probe)
    monkeypatch.setattr(conform, "analyze_cadence", fake_cadence)


def _plan(tmp_path: Path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    return conform.build_plan(
        "origin/phone.mp4",
        "work/phone-cfr.mp4",
        "30",
        project_dir=str(tmp_path),
    )


def test_parse_rate_normalizes_ntsc_aliases_and_keeps_rational():
    assert conform.parse_rate("29.97")["rational"] == "30000/1001"
    assert conform.parse_rate("23.976")["rational"] == "24000/1001"
    assert conform.parse_rate("30")["rational"] == "30/1"
    with pytest.raises(ValueError, match="at most 240"):
        conform.parse_rate("300")


def test_cadence_analysis_uses_decoded_pts_intervals(monkeypatch, tmp_path):
    timestamps = "\n".join(["0.000000", "0.033333", "0.066667", "0.133333", "0.166667"])

    def fake_run(command):
        return subprocess.CompletedProcess(command, 0, timestamps, "")

    monkeypatch.setattr(conform, "_run_command", fake_run)
    cadence = conform.analyze_cadence(tmp_path / "clip.mp4")

    assert cadence["frame_count"] == 5
    assert cadence["variable_intervals"] == 1
    assert cadence["is_variable"] is True
    assert cadence["interval_seconds"]["max"] == pytest.approx(0.066666)


def test_plan_binds_source_cadence_exact_rate_and_pending_apply(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)

    assert plan["version"] == conform.VERSION
    assert plan["source"]["sha256"] == conform._sha256(tmp_path / "origin" / "phone.mp4")
    assert plan["source"]["cadence"]["variable_intervals"] == 20
    assert plan["settings"]["target_rate"]["rational"] == "30/1"
    assert "fps=fps=30/1:start_time=0" in plan["settings"]["video_filter"]
    assert plan["blockers"] == [conform.PENDING_APPLY]
    assert plan["status"] == "blocked"


def test_hdr_and_material_source_av_offsets_fail_closed(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(conform, "analyze_cadence", lambda _path: dict(SOURCE_CADENCE))
    monkeypatch.setattr(
        conform,
        "probe_media",
        lambda _path: {**SOURCE_MEDIA, "bit_depth": 10, "color_transfer": "smpte2084"},
    )
    with pytest.raises(ValueError, match="explicit color workflow"):
        conform.build_plan(
            str(source), "work/cfr.mp4", "30", project_dir=str(tmp_path)
        )

    monkeypatch.setattr(
        conform,
        "probe_media",
        lambda _path: {**SOURCE_MEDIA, "audio_start_time": 0.5, "audio_duration": 3.5},
    )
    plan = conform.build_plan(
        str(source), "work/cfr.mp4", "30", project_dir=str(tmp_path)
    )
    assert any("start offset" in item for item in plan["blockers"])


def test_command_forces_cfr_bakes_rotation_and_resets_audio_timestamps(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    command = conform.build_command(plan, tmp_path / "temporary.mp4")

    assert command[command.index("-fps_mode") + 1] == "cfr"
    assert command[command.index("-r") + 1] == "30/1"
    assert "setsar=1" in command[command.index("-vf") + 1]
    assert "aresample=async=1:first_pts=0" in command[command.index("-af") + 1]
    assert command[command.index("-map_metadata") + 1] == "-1"
    assert "+faststart" in command


def test_verify_detects_source_drift_and_rewritten_settings(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    (tmp_path / "origin" / "phone.mp4").write_bytes(b"changed-source")

    drifted = conform.verify_plan(plan)
    assert any("source" in item and "changed" in item for item in drifted["blockers"])

    (tmp_path / "origin" / "phone.mp4").write_bytes(b"vfr-source")
    plan["settings"]["video_crf"] = 30
    plan["plan_id"] = conform._plan_id(plan)
    rewritten = conform.verify_plan(plan)
    assert any("settings do not match" in item for item in rewritten["blockers"])


def test_apply_validates_cadence_full_decode_and_atomically_promotes(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = conform.build_plan(
        str(source), "work/phone-cfr.mp4", "30", project_dir=str(tmp_path)
    )
    plan_path = tmp_path / "work" / "frame_rate_conform_plan.json"
    conform._atomic_write_json(plan_path, plan)
    commands = []

    def fake_run(command):
        commands.append(list(command))
        if "-vf" in command:
            Path(command[-1]).write_bytes(b"validated-cfr-output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(conform, "_run_command", fake_run)
    applied = conform.apply_plan(str(plan_path))
    delivery = tmp_path / "work" / "phone-cfr.mp4"

    assert delivery.read_bytes() == b"validated-cfr-output"
    assert source.read_bytes() == b"vfr-source"
    assert any("-xerror" in command for command in commands)
    assert applied["summary"]["blocking"] == 0
    assert applied["application"]["validation"]["cadence_algorithm"] == conform.CADENCE_ALGORITHM
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert conform.verify_plan(persisted)["summary"]["blocking"] == 0
    tampered = json.loads(plan_path.read_text(encoding="utf-8"))
    tampered["application"]["validation"]["output_frame_count"] = 1
    tampered["plan_id"] = conform._plan_id(tampered)
    assert any("frame count" in item for item in conform.verify_plan(tampered)["blockers"])
    manifest = build_manifest(
        str(tmp_path), target_stage="analysis", required=["frame_rate_conform_plan"]
    )
    gate = next(
        item for item in manifest["gates"] if item["category"] == "frame_rate_conform_plan"
    )
    assert gate["status"] == "ready"


def test_output_contract_rejects_variable_or_mistimed_working_copy(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    wrong = {
        **OUTPUT_MEDIA,
        "audio_start_time": 0.2,
        "cadence": {**OUTPUT_CADENCE, "is_variable": True, "variable_intervals": 2},
    }

    blockers = conform._output_contract_blockers(wrong, plan["source"], plan["settings"])
    assert any("not constant" in item for item in blockers)
    assert any("start" in item for item in blockers)


def test_apply_does_not_promote_when_source_changes_during_encode(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = conform.build_plan(
        str(source), "work/phone-cfr.mp4", "30", project_dir=str(tmp_path)
    )
    plan_path = tmp_path / "work" / "frame_rate_conform_plan.json"
    conform._atomic_write_json(plan_path, plan)

    def fake_run(command):
        if "-vf" in command:
            Path(command[-1]).write_bytes(b"validated-cfr-output")
        elif "-xerror" in command:
            source.write_bytes(b"changed-during-encode")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(conform, "_run_command", fake_run)
    with pytest.raises(RuntimeError, match="source changed during"):
        conform.apply_plan(str(plan_path))

    assert not (tmp_path / "work" / "phone-cfr.mp4").exists()
    assert json.loads(plan_path.read_text(encoding="utf-8"))["application"] is None


def test_project_containment_and_plan_overwrite_are_enforced(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    with pytest.raises(ValueError, match="inside the project"):
        conform.build_plan(
            str(source), str(tmp_path.parent / "escaped.mp4"), "30", project_dir=str(tmp_path)
        )

    args = [
        "plan",
        str(source),
        "--fps",
        "30",
        "--delivery",
        "work/cfr.mp4",
        "--project-dir",
        str(tmp_path),
        "--output",
        "work/frame_rate_conform_plan.json",
    ]
    assert conform.main(args) == 0
    plan_path = tmp_path / "work" / "frame_rate_conform_plan.json"
    original = plan_path.read_bytes()
    assert conform.main(args) == 1
    assert plan_path.read_bytes() == original
    assert "pass --force to replace" in capsys.readouterr().err


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "frame_rate_conform.py"), "apply", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "atomically promote" in result.stdout
