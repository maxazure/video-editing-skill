"""freeze_punch — source-bound freeze emphasis with unchanged audio timing."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import freeze_punch  # noqa: E402
from freeze_punch import (  # noqa: E402
    PENDING_APPLY,
    _output_contract_blockers,
    _plan_id,
    apply_plan_file,
    build_filter_graph,
    build_plan,
    parse_freeze,
    render_markdown,
    verify_plan,
)


MEDIA = {
    "duration": 4.0,
    "fps": 24.0,
    "width": 320,
    "height": 180,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}


def _source(tmp_path: Path, value: bytes = b"source bytes") -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(value)
    return source


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, events=None, media=None):
    source = _source(tmp_path)
    current_media = dict(MEDIA if media is None else media)
    monkeypatch.setattr(freeze_punch, "probe_media", lambda _path: dict(current_media))
    return build_plan(
        str(source),
        str(tmp_path / "freeze.mp4"),
        media=current_media,
        events=events or [parse_freeze("1,0.8,1.08,0.5,0.4")],
    )


def _make_media(path: Path, *, duration: float = 2.5, fps: int = 24, audio: bool = True) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=160x90:rate={fps}:duration={duration}",
    ]
    if audio:
        command.extend(
            ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
        )
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio:
        command.extend(["-shortest", "-c:a", "aac"])
    command.append(str(path))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_parse_freeze_defaults_and_explicit_anchor():
    assert parse_freeze("1.25,0.8") == {
        "time": 1.25,
        "duration": 0.8,
        "scale": 1.08,
        "anchor_x": 0.5,
        "anchor_y": 0.5,
    }
    assert parse_freeze("1.25,0.8,1.12,0.3,0.7")["anchor_y"] == 0.7


def test_plan_compiles_complete_unchanged_timeline(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)

    assert plan["version"] == "freeze_punch_plan.v1"
    assert [piece["kind"] for piece in plan["pieces"]] == ["normal", "freeze", "normal"]
    assert plan["pieces"][0]["source_start"] == 0.0
    assert plan["pieces"][-1]["source_end"] == 4.0
    assert plan["delivery"]["duration"] == 4.0
    assert plan["summary"]["timeline_changed"] is False
    assert plan["blockers"] == [PENDING_APPLY]
    assert plan["status"] == "blocked"
    assert len(plan["plan_id"]) == 64


def test_plan_builds_even_anchor_crop(tmp_path, monkeypatch):
    plan = _plan(
        tmp_path,
        monkeypatch,
        events=[parse_freeze("1,0.8,1.25,0,1")],
    )
    crop = plan["events"][0]["crop"]

    assert crop == {"x": 0, "y": 36, "width": 256, "height": 144}
    assert all(value % 2 == 0 for value in crop.values())


@pytest.mark.parametrize(
    "events,error",
    [
        ([parse_freeze("1,0.8"), parse_freeze("1.7,0.5")], "overlap"),
        ([parse_freeze("3.5,0.8")], "after source duration"),
        ([parse_freeze("1,0.05")], "duration must be between"),
        ([parse_freeze("1,0.8,1.6")], "scale must be between"),
        ([parse_freeze("1,0.8,1.1,-0.1,0.5")], "anchors must be between"),
    ],
)
def test_plan_rejects_invalid_freeze_windows(tmp_path, monkeypatch, events, error):
    monkeypatch.setattr(freeze_punch, "probe_media", lambda _path: dict(MEDIA))
    with pytest.raises(ValueError, match=error):
        build_plan(
            str(_source(tmp_path)),
            str(tmp_path / "freeze.mp4"),
            media=MEDIA,
            events=events,
        )


def test_filter_graph_freezes_picture_but_keeps_audio_timeline(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    graph = build_filter_graph(plan)

    assert "select='eq(n\\,0)'" in graph
    assert "crop=296:166:12:4" in graph
    assert "tpad=stop_mode=clone:stop_duration=0.800000" in graph
    assert "atrim=start=0:end=4.000000" in graph
    assert "atempo" not in graph
    assert "concat=n=3:v=1:a=0[vconcat]" in graph


def test_verify_recompiles_events_after_digest_rewrite(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan["events"][0]["crop"]["x"] += 2
    plan["plan_id"] = _plan_id(plan)

    result = verify_plan(plan)

    assert result["status"] == "blocked"
    assert "events are not in canonical normalized form" in result["blockers"]


def test_verify_blocks_stale_source(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    Path(plan["source"]["path"]).write_bytes(b"changed source bytes")

    result = verify_plan(plan)

    assert result["summary"]["blocking"] >= 1
    assert any("source size changed" in blocker or "source sha256 changed" in blocker for blocker in result["blockers"])


def test_output_contract_rejects_duration_and_audio_drift():
    delivery = {
        "video_codec": "h264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "has_audio": True,
        "width": 320,
        "height": 180,
        "fps": 24.0,
        "duration": 4.0,
        "duration_tolerance_seconds": 0.15,
    }
    output = {**delivery, "has_audio": False, "audio_codec": None, "duration": 3.5}

    blockers = _output_contract_blockers(output, delivery)

    assert "delivery audio presence does not match the source contract" in blockers
    assert "delivery duration does not match the unchanged-timeline contract" in blockers


def test_markdown_requires_frame_and_audio_review(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    markdown = render_markdown(plan)

    assert "# Freeze-Punch Plan" in markdown
    assert "audio and total duration stay unchanged" in markdown
    assert "frozen speaking mouth" in markdown
    assert "freeze_punch.py apply" in markdown
    assert "does not detect peak frames" in markdown


def test_plan_refuses_delivery_hardlink_to_source(tmp_path, monkeypatch):
    source = _source(tmp_path)
    delivery = tmp_path / "alias.mp4"
    os.link(source, delivery)

    with pytest.raises(ValueError, match="alias the source"):
        build_plan(str(source), str(delivery), media=MEDIA, events=[parse_freeze("1,0.5")])


def test_plan_refuses_source_and_delivery_symlinks(tmp_path):
    source = _source(tmp_path)
    source_link = tmp_path / "source-link.mp4"
    source_link.symlink_to(source)
    delivery_target = tmp_path / "delivery-target.mp4"
    delivery_target.write_bytes(b"existing delivery")
    delivery_link = tmp_path / "delivery-link.mp4"
    delivery_link.symlink_to(delivery_target)

    with pytest.raises(ValueError, match="source video must not be a symlink"):
        build_plan(
            str(source_link),
            str(tmp_path / "freeze.mp4"),
            media=MEDIA,
            events=[parse_freeze("1,0.5")],
        )
    with pytest.raises(ValueError, match="delivery must not be a symlink"):
        build_plan(
            str(source),
            str(delivery_link),
            media=MEDIA,
            events=[parse_freeze("1,0.5")],
        )


def test_apply_refuses_delivery_replaced_with_symlink(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "freeze_punch_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    delivery = Path(plan["delivery"]["path"])
    victim = tmp_path / "victim.mp4"
    victim.write_bytes(b"do not overwrite")
    delivery.symlink_to(victim)

    verification = verify_plan(plan)
    assert "delivery.path must not be a symlink" in verification["blockers"]
    with pytest.raises(ValueError, match="not ready to apply"):
        apply_plan_file(str(plan_path), force=True)
    assert victim.read_bytes() == b"do not overwrite"


def test_real_apply_updates_plan_and_live_verify(tmp_path):
    source = tmp_path / "real-source.mp4"
    delivery = tmp_path / "freeze.mp4"
    plan_path = tmp_path / "freeze_punch_plan.json"
    _make_media(source)
    media = freeze_punch.probe_media(source)
    plan = build_plan(
        str(source),
        str(delivery),
        media=media,
        events=[parse_freeze("0.6,0.7,1.1,0.5,0.4")],
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    applied = apply_plan_file(str(plan_path))
    verified = verify_plan(json.loads(plan_path.read_text(encoding="utf-8")))

    assert delivery.is_file()
    assert applied["application"]["validation"]["decode_checked"] is True
    assert applied["application"]["output"]["duration"] == pytest.approx(2.5, abs=0.15)
    assert applied["application"]["output"]["fps"] == pytest.approx(24.0, abs=0.01)
    assert applied["application"]["output"]["has_audio"] is True
    assert verified["summary"]["blocking"] == 0
    assert not list(tmp_path.glob(".freeze.*.tmp.mp4"))


def test_cli_plan_apply_verify_round_trip(tmp_path):
    source = tmp_path / "cli-source.mp4"
    delivery = tmp_path / "cli-freeze.mp4"
    plan_path = tmp_path / "freeze_punch_plan.json"
    markdown = tmp_path / "freeze_punch_plan.md"
    script = str(REPO / "scripts" / "freeze_punch.py")
    _make_media(source, duration=2.0, audio=False)

    planned = subprocess.run(
        [
            sys.executable,
            script,
            "plan",
            str(source),
            "--freeze",
            "0.5,0.6,1.08",
            "--delivery",
            str(delivery),
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown),
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    pending = subprocess.run(
        [sys.executable, script, "verify", str(plan_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert pending.returncode == 2
    applied = subprocess.run(
        [sys.executable, script, "apply", str(plan_path)],
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    verified = subprocess.run(
        [sys.executable, script, "verify", str(plan_path), "--strict"],
        capture_output=True,
        text=True,
    )

    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(plan_path.read_text(encoding="utf-8"))["status"] == "ready"
    assert "# Freeze-Punch Plan" in markdown.read_text(encoding="utf-8")
