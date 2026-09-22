import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ping_pong_loop  # noqa: E402


SOURCE_MEDIA = {
    "duration": 1.0,
    "video_duration": 1.0,
    "audio_duration": 1.0,
    "avg_frame_rate": "6/1",
    "r_frame_rate": "6/1",
    "avg_fps": 6.0,
    "nominal_fps": 6.0,
    "width": 160,
    "height": 90,
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
    "format_names": ["mp4"],
}


def _cadence(frames, *, variable=False):
    interval = 1 / 6
    return {
        "algorithm": ping_pong_loop.CADENCE_ALGORITHM,
        "tolerance_ratio": 0.02,
        "tolerance_seconds": interval * 0.02,
        "frame_count": frames,
        "interval_count": max(0, frames - 1),
        "non_monotonic_intervals": 0,
        "variable_intervals": 1 if variable else 0,
        "variable_ratio": 0.1 if variable else 0.0,
        "is_variable": variable,
        "interval_seconds": {
            "min": interval,
            "p05": interval,
            "median": interval,
            "mean": interval,
            "p95": interval,
            "max": interval,
        },
    }


def _source(tmp_path):
    source = tmp_path / "origin" / "gesture.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"ping-pong-source")
    return source


def _patch_media(monkeypatch, source, *, source_overrides=None, variable=False):
    source_overrides = source_overrides or {}

    def fake_media(path):
        path = Path(path).resolve()
        fingerprint = ping_pong_loop._fingerprint(path)
        if path == source.resolve():
            media = {**SOURCE_MEDIA, **source_overrides}
            return {**fingerprint, **media, "cadence": _cadence(6, variable=variable)}
        proof = "turnaround" in path.name or "loop-seam" in path.name or "turn" in path.name or "seam" in path.name
        frames = 6 if proof else 20
        media = {
            **SOURCE_MEDIA,
            "duration": frames / 6,
            "video_duration": frames / 6,
            "audio_duration": None,
            "has_audio": False,
            "audio_codec": None,
            "sample_rate": None,
            "channels": None,
        }
        return {**fingerprint, **media, "cadence": _cadence(frames)}

    monkeypatch.setattr(ping_pong_loop, "_media_info", fake_media)


def _plan(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = ping_pong_loop.build_plan(
        "origin/gesture.mp4",
        "work/gesture-ping-pong.mp4",
        "verify/gesture-turnaround.mp4",
        "verify/gesture-loop-seam.mp4",
        cycles=2,
        project_dir=str(tmp_path),
    )
    return source, plan


def test_plan_builds_endpoint_deduplicated_cycle_and_memory_contract(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    settings = plan["settings"]

    assert plan["source"]["path"] == str(source.resolve())
    assert settings["selected_frame_count"] == 6
    assert settings["forward_frame_count"] == 6
    assert settings["reverse_frame_count"] == 4
    assert settings["cycle_frame_count"] == 10
    assert settings["target_frame_count"] == 20
    assert settings["turnaround_frame"] == 6
    assert settings["loop_seam_frame"] == 10
    assert settings["output_has_audio"] is False
    assert settings["algorithm"] == ping_pong_loop.ALGORITHM
    assert plan["blockers"] == [ping_pong_loop.PENDING_APPLY]
    assert any("audio is intentionally dropped" in item for item in plan["warnings"])


def test_frame_snapping_duration_and_render_graph_are_canonical(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source)
    plan = ping_pong_loop.build_plan(
        str(source),
        "work/out.mp4",
        "verify/turn.mp4",
        "verify/seam.mp4",
        start=0.16,
        end=0.84,
        duration="1.5",
        proof_context_seconds=0.2,
        project_dir=str(tmp_path),
    )
    settings = plan["settings"]
    assert settings["start_frame"] == 1
    assert settings["end_frame_exclusive"] == 5
    assert settings["cycle_frame_count"] == 6
    assert settings["target_frame_count"] == 9
    assert settings["partial_final_cycle"] is True

    command = ping_pong_loop.build_command(plan, tmp_path / "render.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=start_frame=1:end_frame=5" in graph
    assert "trim=start_frame=1:end_frame=3,reverse" in graph
    assert "loop=loop=1:size=6:start=0" in graph
    assert "trim=start_frame=0:end_frame=9" in graph
    assert command[command.index("-frames:v") + 1] == "9"
    assert "-an" in command


def test_plan_rejects_vfr_hdr_short_ranges_memory_and_path_escape(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_media(monkeypatch, source, variable=True)
    with pytest.raises(ValueError, match="CFR working copy"):
        ping_pong_loop.build_plan(
            str(source), "work/out.mp4", "verify/turn.mp4", "verify/seam.mp4", cycles=1, project_dir=str(tmp_path)
        )

    _patch_media(monkeypatch, source, source_overrides={"color_transfer": "smpte2084"})
    with pytest.raises(ValueError, match="hdr_sdr.py"):
        ping_pong_loop.build_plan(
            str(source), "work/out.mp4", "verify/turn.mp4", "verify/seam.mp4", cycles=1, project_dir=str(tmp_path)
        )

    _patch_media(monkeypatch, source)
    with pytest.raises(ValueError, match="at least three frames"):
        ping_pong_loop.build_plan(
            str(source), "work/out.mp4", "verify/turn.mp4", "verify/seam.mp4",
            start=0, end=0.3, cycles=1, project_dir=str(tmp_path)
        )
    with pytest.raises(ValueError, match="inside the project"):
        ping_pong_loop.build_plan(
            str(source), str(tmp_path.parent / "out.mp4"), "verify/turn.mp4", "verify/seam.mp4",
            cycles=1, project_dir=str(tmp_path)
        )

    _patch_media(monkeypatch, source, source_overrides={"width": 8000, "height": 8000})
    with pytest.raises(ValueError, match="reverse buffering"):
        ping_pong_loop.build_plan(
            str(source), "work/out.mp4", "verify/turn.mp4", "verify/seam.mp4",
            cycles=1, max_working_set_mib=64, project_dir=str(tmp_path)
        )


def test_apply_confirm_and_live_verify_bind_all_three_outputs(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "ping_pong_loop_plan.json"
    ping_pong_loop._atomic_write_json(plan_path, plan)
    commands = []

    def fake_checked(command, _label):
        command = list(command)
        commands.append(command)
        if command[-1] != "-":
            Path(command[-1]).write_bytes(b"rendered-media")

    monkeypatch.setattr(ping_pong_loop, "_run_checked", fake_checked)
    applied = ping_pong_loop.apply_plan(str(plan_path))

    assert applied["blockers"] == [ping_pong_loop.PENDING_CONFIRM]
    assert Path(applied["delivery"]["path"]).is_file()
    assert Path(applied["turnaround_proof"]["path"]).is_file()
    assert Path(applied["loop_seam_proof"]["path"]).is_file()
    render = next(command for command in commands if "-filter_complex" in command and "loop=loop=" in command[command.index("-filter_complex") + 1])
    assert "reverse" in render[render.index("-filter_complex") + 1]

    failed = ping_pong_loop.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="loop seam still pauses",
        full_playback="completed",
        turnaround_playback="completed",
        seam_playback="completed",
        checks={**{field: "pass" for field in ping_pong_loop.REVIEW_FIELDS}, "duplicate_hold": "fail"},
    )
    assert failed["status"] == "blocked"

    confirmed = ping_pong_loop.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="full delivery and both proofs reviewed at normal speed",
        full_playback="completed",
        turnaround_playback="completed",
        seam_playback="completed",
        checks={field: "pass" for field in ping_pong_loop.REVIEW_FIELDS},
    )
    assert confirmed["summary"]["blocking"] == 0
    assert confirmed["status"] == "warn"  # source audio was deliberately removed

    source.write_bytes(b"changed-source")
    stale = ping_pong_loop.verify_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    assert stale["status"] == "blocked"
    assert any("source bytes" in item for item in stale["blockers"])


def test_cli_help_exposes_all_workflow_stages():
    script = os.path.join(REPO, "scripts", "ping_pong_loop.py")
    for command in ("plan", "apply", "confirm", "verify"):
        result = subprocess.run([sys.executable, script, command, "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage:" in result.stdout


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_ffmpeg_round_trip_has_exact_frames_and_silent_proofs(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=6:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    plan = ping_pong_loop.build_plan(
        str(source), "output.mp4", "turnaround.mp4", "loop-seam.mp4", cycles=2, project_dir=str(tmp_path)
    )
    plan_path = tmp_path / "plan.json"
    ping_pong_loop._atomic_write_json(plan_path, plan)
    applied = ping_pong_loop.apply_plan(str(plan_path))

    assert applied["application"]["output"]["cadence"]["frame_count"] == 20
    assert applied["application"]["turnaround_proof"]["cadence"]["frame_count"] == 6
    assert applied["application"]["loop_seam_proof"]["cadence"]["frame_count"] == 6
    assert applied["application"]["output"]["has_audio"] is False
    assert applied["summary"]["blocking"] == 1
    assert ping_pong_loop.PENDING_CONFIRM in applied["blockers"]
