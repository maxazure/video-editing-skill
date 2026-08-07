"""speed_ramp — source-bound local velocity-edit plans and FFmpeg apply."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from speed_ramp import (  # noqa: E402
    _plan_id,
    apply_plan,
    build_filter_graph,
    build_speed_ramp_plan,
    curve_progress,
    parse_hold,
    parse_ramp,
    render_markdown,
    verify_plan,
)


def _source(tmp_path, value=b"source bytes"):
    source = tmp_path / "source.mp4"
    source.write_bytes(value)
    return source


def _events():
    return [
        parse_ramp("2,3,1,0.25,s-curve"),
        parse_hold("3,4,0.25"),
        parse_ramp("4,5,0.25,1,ease"),
    ]


def _plan(tmp_path, **overrides):
    settings = {
        "duration": 8.0,
        "fps": 30.0,
        "has_audio": True,
        "events": _events(),
    }
    settings.update(overrides)
    return build_speed_ramp_plan(str(_source(tmp_path)), **settings)


def _make_media(path, duration=2.0, fps=24):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=160x90:rate={fps}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_parse_events_and_curves():
    assert parse_hold("1,2,0.5") == {"kind": "hold", "start": 1.0, "end": 2.0, "speed": 0.5}
    ramp = parse_ramp("2,3,1,0.25,s-curve")
    assert ramp["curve"] == "s_curve"
    assert curve_progress("linear", 0.25) == pytest.approx(0.25)
    assert curve_progress("snap", 0.49) == 0.0
    assert curve_progress("snap", 0.5) == 1.0
    assert curve_progress("s_curve", 0.5) == pytest.approx(0.5)


def test_plan_compiles_complete_source_and_output_timelines(tmp_path):
    plan = _plan(tmp_path)

    assert plan["version"] == "speed_ramp_plan.v1"
    assert plan["events"][0]["curve"] == "s_curve"
    assert plan["pieces"][0]["source_start"] == 0.0
    assert plan["pieces"][-1]["source_end"] == 8.0
    assert plan["pieces"][0]["output_start"] == 0.0
    assert plan["pieces"][-1]["output_end"] == plan["output"]["duration"]
    assert plan["summary"]["minimum_speed"] == pytest.approx(0.25)
    assert plan["summary"]["output_duration"] > 8.0
    assert len(plan["plan_id"]) == 64


def test_plan_surfaces_low_frame_rate_and_audio_review(tmp_path):
    plan = _plan(tmp_path)

    assert plan["status"] == "review"
    assert any("native unique fps" in warning for warning in plan["warnings"])
    assert any("Audio below 0.5x" in warning for warning in plan["warnings"])
    assert plan["review_contract"]["required"] is True


def test_interpolation_warning_recommends_sufficient_rate(tmp_path):
    plan = _plan(tmp_path, interpolate_fps=60)

    assert any("consider at least 120 fps" in warning for warning in plan["warnings"])


@pytest.mark.parametrize(
    "events,error",
    [
        ([parse_hold("1,2,0.5"), parse_hold("1.9,3,1")], "overlap"),
        ([parse_hold("1,9,0.5")], "after source duration"),
        ([parse_hold("1,2,0.05")], "between 0.1x and 4x"),
        ([parse_ramp("1,2,1,0.5,unknown")], "curve must be one of"),
    ],
)
def test_plan_rejects_invalid_events(tmp_path, events, error):
    with pytest.raises(ValueError, match=error):
        build_speed_ramp_plan(
            str(_source(tmp_path)),
            duration=8.0,
            fps=30.0,
            has_audio=True,
            events=events,
        )


def test_filter_graph_keeps_video_audio_and_interpolation_in_sync(tmp_path):
    plan = _plan(tmp_path, interpolate_fps=120)
    graph = build_filter_graph(plan)

    assert "minterpolate=fps=120" in graph
    assert "setpts=PTS/0.25000000" in graph
    assert "atempo=0.50000000,atempo=0.50000000" in graph
    assert f"concat=n={len(plan['pieces'])}:v=1:a=1[vconcat][aout]" in graph
    assert graph.endswith("[vconcat]fps=30.000000[vout]")


def test_verify_recomputes_digest_and_source_hash(tmp_path):
    plan = _plan(tmp_path)
    assert verify_plan(plan)["summary"]["blocking"] == 0

    plan["pieces"][0]["speed"] = 2.0
    result = verify_plan(plan)
    assert result["status"] == "blocked"
    assert "plan_id does not match canonical plan content" in result["blockers"]


def test_verify_recompiles_plan_even_after_digest_is_rewritten(tmp_path):
    plan = _plan(tmp_path)
    plan["events"][0]["from_speed"] = 2.0
    plan["plan_id"] = _plan_id(plan)

    result = verify_plan(plan)

    assert result["status"] == "blocked"
    assert "pieces do not match events and ramp_steps" in result["blockers"]


def test_verify_blocks_stale_source(tmp_path):
    plan = _plan(tmp_path)
    Path(plan["source"]["path"]).write_bytes(b"changed")

    result = verify_plan(plan)

    assert result["summary"]["blocking"] >= 1
    assert any("source size changed" in blocker or "source sha256 changed" in blocker for blocker in result["blockers"])


def test_markdown_requires_full_speed_audio_review(tmp_path):
    plan = _plan(tmp_path)
    markdown = render_markdown(plan)

    assert "# Speed Ramp Plan" in markdown
    assert "Watch the result at 1× with audio" in markdown
    assert "speed_ramp.py apply" in markdown
    assert "does not detect impacts" in markdown


def test_apply_renders_transactionally_and_refuses_overwrite(tmp_path):
    source = tmp_path / "real-source.mp4"
    output = tmp_path / "speed-ramped.mp4"
    _make_media(source)
    plan = build_speed_ramp_plan(
        str(source),
        duration=2.0,
        fps=24.0,
        has_audio=True,
        events=[parse_hold("0.5,1.0,2")],
    )

    receipt = apply_plan(plan, str(output))

    assert output.is_file()
    assert receipt["version"] == "speed_ramp_apply.v1"
    assert receipt["output"]["has_audio"] is True
    assert receipt["output"]["duration"] == pytest.approx(1.75, abs=0.08)
    assert receipt["output"]["fps"] == pytest.approx(24.0, abs=0.01)
    assert not list(tmp_path.glob(".speed-ramped-*.mp4"))
    with pytest.raises(ValueError, match="already exists"):
        apply_plan(plan, str(output))


def test_cli_plan_verify_and_apply(tmp_path):
    source = tmp_path / "cli-source.mp4"
    plan_path = tmp_path / "speed_ramp_plan.json"
    markdown = tmp_path / "speed_ramp_plan.md"
    output = tmp_path / "cli-output.mp4"
    receipt = tmp_path / "speed_ramp_apply.json"
    script = str(REPO / "scripts" / "speed_ramp.py")
    _make_media(source, duration=1.5)

    planned = subprocess.run(
        [
            sys.executable,
            script,
            "plan",
            str(source),
            "--hold",
            "0.25,0.75,2",
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown),
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["summary"]["events"] == 1
    assert "# Speed Ramp Plan" in markdown.read_text(encoding="utf-8")

    verified = subprocess.run(
        [sys.executable, script, "verify", str(plan_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr

    applied = subprocess.run(
        [
            sys.executable,
            script,
            "apply",
            str(plan_path),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ],
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert output.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["plan_id"] == payload["plan_id"]
