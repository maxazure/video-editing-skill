import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import multicam_switch  # noqa: E402


def _sync_plan(path_a: Path, path_b: Path):
    return {
        "version": "multicam_sync_plan.v1",
        "status": "ready",
        "summary": {"blocking": 0},
        "common_overlap_in_reference": {"start": 0.0, "end": 4.0, "duration": 4.0},
        "angles": [
            {
                "id": "angle_00_cam-a",
                "role": "reference",
                "status": "ready",
                "media": {"path": str(path_a), "duration": 4.0},
                "audio_stream": {"index": 0},
                "alignment": {"offset_seconds": 0.0},
                "clock_drift": {"status": "reference"},
            },
            {
                "id": "angle_01_cam-b",
                "role": "angle",
                "status": "ready",
                "media": {"path": str(path_b), "duration": 4.0},
                "audio_stream": {"index": 0},
                "alignment": {"offset_seconds": 0.0},
                "clock_drift": {"status": "not_requested"},
            },
        ],
    }


def _stub_media():
    return {
        "duration": 4.0,
        "has_video": True,
        "has_audio": True,
        "audio_streams": 1,
        "width": 160,
        "height": 90,
        "fps": 12.0,
        "rotation": 0,
        "video_codec": "h264",
        "audio_codec": "aac",
    }


def _build_stub_plan(tmp_path, monkeypatch, *, correlated=False, allow_correlated=False):
    camera_a = tmp_path / "cam-a.mp4"
    camera_b = tmp_path / "cam-b.mp4"
    camera_a.write_bytes(b"camera-a")
    camera_b.write_bytes(b"camera-b")
    sync = tmp_path / "multicam_sync_plan.json"
    sync.write_text(json.dumps(_sync_plan(camera_a, camera_b)), encoding="utf-8")
    monkeypatch.setattr(multicam_switch, "probe_media", lambda _path: _stub_media())

    if correlated:
        values = [1.0, 2.0, 8.0, 20.0, 9.0, 3.0, 1.0, 1.0]

        def fake_decode(_path, **_kwargs):
            return values

    else:
        def fake_decode(path, **_kwargs):
            if str(path).endswith("cam-a.mp4"):
                return [1000.0] * 4 + [1.0] * 4
            return [1.0] * 4 + [1000.0] * 4

    monkeypatch.setattr(multicam_switch, "decode_audio_envelope", fake_decode)
    return multicam_switch.build_plan(
        str(sync),
        speaker_map={"angle_00_cam-a": "Host", "angle_01_cam-b": "Guest"},
        delivery=str(tmp_path / "draft.mp4"),
        window_seconds=0.5,
        min_shot_seconds=1.0,
        dominance_margin=0.1,
        allow_correlated_audio=allow_correlated,
    )


def test_choose_windows_holds_ambiguity_and_selects_both_angles():
    windows, metrics = multicam_switch.choose_windows(
        {
            "a": [1000, 1000, 500, 10, 10, 10],
            "b": [10, 10, 500, 1000, 1000, 10],
        },
        start_time=0.0,
        end_time=3.0,
        window_seconds=0.5,
        fallback_angle_id="a",
        min_activity_score=0.2,
        dominance_margin=0.1,
    )

    assert windows[0]["selected_angle_id"] == "a"
    assert windows[2]["confident"] is False
    assert windows[2]["selected_angle_id"] == "a"
    assert windows[3]["selected_angle_id"] == "b"
    assert metrics["raw_confident_winners"]["a"] > 0
    assert metrics["raw_confident_winners"]["b"] > 0


def test_fold_short_runs_absorbs_flash_cut_into_previous_angle():
    folded = multicam_switch.fold_short_runs(
        [
            {"start": 0.0, "end": 2.0, "angle_id": "a", "windows": 4, "confident_windows": 4},
            {"start": 2.0, "end": 2.5, "angle_id": "b", "windows": 1, "confident_windows": 1},
            {"start": 2.5, "end": 4.0, "angle_id": "a", "windows": 3, "confident_windows": 3},
        ],
        1.0,
    )

    assert len(folded) == 1
    assert folded[0]["angle_id"] == "a"
    assert folded[0]["end"] == 4.0


def test_correlated_shared_mix_blocks_without_explicit_override(tmp_path, monkeypatch):
    blocked = _build_stub_plan(tmp_path, monkeypatch, correlated=True)

    assert any("highly correlated" in item for item in blocked["analysis"]["blockers"])

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    allowed = _build_stub_plan(allowed_dir, monkeypatch, correlated=True, allow_correlated=True)
    assert not any("highly correlated" in item for item in allowed["analysis"]["blockers"])
    assert any("highly correlated" in item for item in allowed["analysis"]["warnings"])


def test_verify_detects_pending_work_and_source_drift(tmp_path, monkeypatch):
    plan = _build_stub_plan(tmp_path, monkeypatch)
    pending = multicam_switch.verify_plan(plan)

    assert multicam_switch.PENDING_APPLY in pending["blockers"]
    assert multicam_switch.PENDING_REVIEW in pending["blockers"]

    Path(plan["sources"][0]["media"]["path"]).write_bytes(b"changed")
    drifted = multicam_switch.verify_plan(plan)
    assert any("source angle_00_cam-a" in item and "changed" in item for item in drifted["blockers"])


def test_confirm_requires_all_review_fields(tmp_path, monkeypatch):
    plan = _build_stub_plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "plan.json"
    output = tmp_path / "draft.mp4"
    output.write_bytes(b"draft")
    plan["application"] = {
        "status": "applied",
        "output": {"path": str(output), "sha256": multicam_switch._sha256(output), "size_bytes": output.stat().st_size},
    }
    multicam_switch._atomic_write_json(plan_path, plan)

    with pytest.raises(ValueError, match="every canonical review field"):
        multicam_switch.confirm_plan(
            plan_path,
            reviewer="tester",
            checks={"speaker_selection": "pass"},
        )


def test_render_command_maps_reference_time_to_each_source_offset(tmp_path, monkeypatch):
    plan = _build_stub_plan(tmp_path, monkeypatch)
    plan["sources"][1]["offset_seconds"] = 0.25
    for switch in plan["switches"]:
        if switch["angle_id"] == "angle_01_cam-b":
            switch["source_start"] = round(switch["start"] - 0.25, 4)
            switch["source_end"] = round(switch["end"] - 0.25, 4)

    command = multicam_switch.build_render_command(plan, str(tmp_path / "out.mp4"))
    filtergraph = command[command.index("-filter_complex") + 1]
    assert "[1:v:0]trim=start=" in filtergraph
    assert "[0:a:0]atrim=start=0.000000:end=4.000000" in filtergraph
    assert command[-1].endswith("out.mp4")


def _make_camera(path: Path, color: str, first_half_active: bool):
    if first_half_active:
        expression = "if(lt(t,2),0.7*sin(2*PI*440*t),0.002*sin(2*PI*440*t))"
    else:
        expression = "if(lt(t,2),0.002*sin(2*PI*660*t),0.7*sin(2*PI*660*t))"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=12:d=4",
            "-f", "lavfi", "-i", f"aevalsrc='{expression}':s=8000:d=4",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg suite unavailable")
def test_real_cli_plan_apply_confirm_verify(tmp_path):
    camera_a = tmp_path / "cam-a.mp4"
    camera_b = tmp_path / "cam-b.mp4"
    _make_camera(camera_a, "red", True)
    _make_camera(camera_b, "blue", False)
    sync = tmp_path / "multicam_sync_plan.json"
    sync.write_text(json.dumps(_sync_plan(camera_a, camera_b)), encoding="utf-8")
    plan = tmp_path / "multicam_switch_plan.json"
    markdown = tmp_path / "multicam_switch_plan.md"
    draft = tmp_path / "draft.mp4"
    script = REPO / "scripts" / "multicam_switch.py"

    planned = subprocess.run(
        [
            sys.executable, str(script), "plan", "--sync-plan", str(sync),
            "--speaker", "angle_00_cam-a=Host", "--speaker", "angle_01_cam-b=Guest",
            "--window", "0.25", "--min-shot", "1.0", "--dominance-margin", "0.1",
            "--delivery", str(draft), "--output", str(plan), "--markdown", str(markdown), "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 0, planned.stderr
    payload = json.loads(plan.read_text(encoding="utf-8"))
    assert {item["angle_id"] for item in payload["switches"]} == {"angle_00_cam-a", "angle_01_cam-b"}

    applied = subprocess.run([sys.executable, str(script), "apply", str(plan)], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert draft.is_file()

    confirmed = subprocess.run(
        [
            sys.executable, str(script), "confirm", str(plan), "--reviewer", "integration-test",
            "--speaker-selection", "pass", "--cut-timing", "pass", "--sync", "pass",
            "--audio-continuity", "pass", "--markdown", str(markdown),
        ],
        capture_output=True,
        text=True,
    )
    assert confirmed.returncode == 0, confirmed.stderr

    verified = subprocess.run(
        [sys.executable, str(script), "verify", str(plan), "--strict"],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "ready"
