import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import delivery_encode as delivery  # noqa: E402


MEDIA = {
    "duration": 4.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
    "format_names": ["mov", "mp4"],
}


def _source(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source-video")
    return path


def _patch_probe(monkeypatch, media=None):
    monkeypatch.setattr(delivery, "probe_media", lambda _path: dict(media or MEDIA))


def _plan(tmp_path, monkeypatch, **kwargs):
    _patch_probe(monkeypatch)
    return delivery.build_plan(
        str(_source(tmp_path)),
        str(tmp_path / "output" / "share.mp4"),
        max_size_mib=kwargs.pop("max_size_mib", 0.5),
        **kwargs,
    )


def test_plan_builds_source_bound_two_pass_budget(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)

    assert plan["version"] == delivery.VERSION
    assert plan["source"]["sha256"] == delivery._sha256(tmp_path / "source.mp4")
    assert plan["settings"]["target_size_bytes"] == int(0.5 * 1024 * 1024)
    assert plan["settings"]["video_bitrate_bps"] > 0
    assert plan["settings"]["audio_bitrate_bps"] == 128_000
    assert plan["blockers"] == [delivery.PENDING_APPLY]
    assert plan["status"] == "blocked"


def test_plan_rejects_impossible_size_budget(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)

    with pytest.raises(ValueError, match="target is too small"):
        delivery.build_plan(
            str(_source(tmp_path)),
            str(tmp_path / "share.mp4"),
            max_size_mib=0.05,
        )


def test_downscale_preserves_aspect_and_refuses_fps_upsampling(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch, max_width=320, max_height=320, fps=24)

    assert plan["settings"]["target_width"] == 320
    assert plan["settings"]["target_height"] == 180
    assert plan["settings"]["target_fps"] == 24

    with pytest.raises(ValueError, match="will not synthesize frames"):
        _plan(tmp_path / "higher", monkeypatch, fps=60)


def test_commands_are_two_pass_h264_aac_with_faststart(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    commands = delivery.build_commands(
        plan,
        tmp_path / "temporary.mp4",
        tmp_path / "ffmpeg2pass",
    )

    assert len(commands) == 2
    assert commands[0][commands[0].index("-pass") + 1] == "1"
    assert commands[1][commands[1].index("-pass") + 1] == "2"
    assert "libx264" in commands[1]
    assert "aac" in commands[1]
    assert "+faststart" in commands[1]
    assert "yuv420p" in commands[1]


def test_verify_detects_source_drift(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    (tmp_path / "source.mp4").write_bytes(b"changed-source-video")

    verified = delivery.verify_plan(plan)

    assert verified["summary"]["blocking"] > 0
    assert any("source" in item and "changed" in item for item in verified["blockers"])


def test_verify_rejects_rewritten_noncanonical_settings(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan["settings"]["video_bitrate_bps"] *= 2
    plan["plan_id"] = delivery._plan_id(plan)

    verified = delivery.verify_plan(plan)

    assert any("settings do not match" in item for item in verified["blockers"])


def test_apply_validates_and_atomically_promotes_delivery(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "delivery_encode_plan.json"
    delivery._atomic_write_json(plan_path, plan)

    def fake_run(command):
        if "-pass" in command and command[command.index("-pass") + 1] == "2":
            Path(command[-1]).write_bytes(b"x" * 380_000)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(delivery, "_run_command", fake_run)
    applied = delivery.apply_plan(str(plan_path))
    output = tmp_path / "output" / "share.mp4"

    assert output.is_file()
    assert output.stat().st_size == 380_000
    assert (tmp_path / "source.mp4").read_bytes() == b"source-video"
    assert applied["summary"]["blocking"] == 0
    assert applied["application"]["validation"]["decode_checked"] is True
    assert applied["application"]["validation"]["output_sha256"] == delivery._sha256(output)
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["plan_id"] == applied["plan_id"]
    assert delivery.verify_plan(persisted)["summary"]["blocking"] == 0


def test_hard_size_ceiling_blocks_oversized_applied_output(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    output = tmp_path / "output" / "share.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"x" * 600_000)
    fingerprint = {**delivery._fingerprint(output), **MEDIA}
    plan["application"] = {
        "applied_at": delivery.utc_now(),
        "output": fingerprint,
        "validation": {
            "verified_at": delivery.utc_now(),
            "decode_checked": True,
            "decode_command": delivery._decode_command(output),
            "output_sha256": fingerprint["sha256"],
        },
    }
    delivery._set_derived(plan)

    verified = delivery.verify_plan(plan)

    assert any("hard maximum size" in item for item in verified["blockers"])


def test_apply_refuses_existing_delivery_without_force(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "delivery_encode_plan.json"
    delivery._atomic_write_json(plan_path, plan)
    output = tmp_path / "output" / "share.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"user-file")

    with pytest.raises(ValueError, match="already exists"):
        delivery.apply_plan(str(plan_path))

    assert output.read_bytes() == b"user-file"


def test_plan_cli_refuses_silent_artifact_overwrite(tmp_path, monkeypatch, capsys):
    _patch_probe(monkeypatch)
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "delivery_encode_plan.json"
    args = [
        "plan",
        str(source),
        "--delivery",
        str(tmp_path / "output" / "share.mp4"),
        "--max-size-mib",
        "0.5",
        "--output",
        str(plan_path),
    ]

    assert delivery.main(args) == 0
    original = plan_path.read_bytes()
    assert delivery.main(args) == 1
    assert plan_path.read_bytes() == original
    assert "pass --force to replace" in capsys.readouterr().err


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "delivery_encode.py"), "apply", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "after validation" in result.stdout
