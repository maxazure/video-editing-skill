"""audio_transition — explicit source-bound J-cut/L-cut plans and render integration."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from audio_transition import (  # noqa: E402
    _plan_id,
    apply_plan,
    build_audio_transition_plan,
    build_filter_graph,
    parse_transition,
    render_markdown,
    verify_plan,
    verify_receipt,
)


def _make_media(path: Path, *, frequency: int, duration: float = 3.0) -> None:
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
            f"testsrc2=size=160x90:rate=24:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
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


def _project(tmp_path: Path, *, first_start: float = 0.4, second_start: float = 0.5):
    left = tmp_path / "left.mp4"
    right = tmp_path / "right.mp4"
    transcript = tmp_path / "transcript.json"
    config = tmp_path / "render_config.json"
    _make_media(left, frequency=440)
    _make_media(right, frequency=880)
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"id": 1, "start": first_start, "end": 1.6, "text": "left phrase"},
                    {"id": 2, "start": second_start, "end": 1.7, "text": "right phrase"},
                ]
            }
        ),
        encoding="utf-8",
    )
    config.write_text(
        json.dumps(
            {
                "clips": [
                    {"video": str(left), "transcript": str(transcript), "segment_id": 1},
                    {"video": str(right), "transcript": str(transcript), "segment_id": 2},
                ],
                "title": "",
            }
        ),
        encoding="utf-8",
    )
    return config, left, right


def test_parse_transition_aliases():
    assert parse_transition("1,j,0.5") == {"after_clip": 1, "kind": "j_cut", "duration": 0.5}
    assert parse_transition("2,L-cut,0.25") == {"after_clip": 2, "kind": "l_cut", "duration": 0.25}
    with pytest.raises(ValueError, match="expects"):
        parse_transition("1,j")


def test_j_cut_compiles_incoming_audio_before_picture(tmp_path):
    config, _, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.3")])

    assert plan["version"] == "audio_transition_plan.v1"
    assert plan["summary"]["j_cuts"] == 1
    assert plan["audio_layers"][0]["fade_out"] == pytest.approx(0.3)
    assert plan["audio_layers"][1]["source_start"] == pytest.approx(0.2)
    assert plan["audio_layers"][1]["output_start"] == pytest.approx(0.9)
    assert plan["audio_layers"][1]["fade_in"] == pytest.approx(0.3)
    assert plan["status"] == "review"
    assert len(plan["plan_id"]) == 64


def test_l_cut_extends_outgoing_audio_and_skips_incoming_handle(tmp_path):
    config, _, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,l_cut,0.3")])

    assert plan["summary"]["l_cuts"] == 1
    assert plan["audio_layers"][0]["source_end"] == pytest.approx(1.9)
    assert plan["audio_layers"][0]["output_end"] == pytest.approx(1.5)
    assert plan["audio_layers"][1]["source_start"] == pytest.approx(0.8)
    assert plan["audio_layers"][1]["output_start"] == pytest.approx(1.5)
    assert any("skips the incoming clip" in item for item in plan["warnings"])


def test_plan_rejects_missing_j_cut_handle(tmp_path):
    config, _, _ = _project(tmp_path, second_start=0.1)
    with pytest.raises(ValueError, match="more incoming audio handle"):
        build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.2")])


def test_plan_rejects_missing_l_cut_handle(tmp_path):
    config, _, _ = _project(tmp_path)
    payload = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    payload["segments"][0]["end"] = 2.9
    (tmp_path / "transcript.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="more outgoing audio handle"):
        build_audio_transition_plan(str(config), [parse_transition("1,l_cut,0.2")])


def test_filter_graph_uses_video_hard_cut_and_audio_mix(tmp_path):
    config, _, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.25")])
    graph, inputs = build_filter_graph(plan, target_w=160, target_h=90, target_fps=24)

    assert len(inputs) == 2
    assert "concat=n=2:v=1:a=0[merged_v]" in graph
    assert "afade=t=out" in graph
    assert "afade=t=in" in graph
    assert "adelay=950:all=1" in graph
    assert "amix=inputs=2:duration=longest" in graph
    assert graph.endswith("asetpts=PTS-STARTPTS[merged_a]")


def test_verify_rebuilds_live_inputs_even_if_digest_is_rewritten(tmp_path):
    config, _, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.25")])
    assert verify_plan(plan)["summary"]["blocking"] == 0

    plan["audio_layers"][1]["source_start"] = 0.1
    plan["plan_id"] = _plan_id(plan)
    verification = verify_plan(plan)

    assert verification["status"] == "blocked"
    assert any("compiled plan no longer matches" in item for item in verification["blockers"])


def test_verify_blocks_changed_source(tmp_path):
    config, left, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.25")])
    left.write_bytes(b"changed")

    verification = verify_plan(plan)

    assert verification["summary"]["blocking"] >= 1
    assert any("cannot rebuild" in item for item in verification["blockers"])


def test_markdown_explains_single_pass_and_review(tmp_path):
    config, _, _ = _project(tmp_path)
    plan = build_audio_transition_plan(str(config), [parse_transition("1,l_cut,0.25")])
    markdown = render_markdown(plan)

    assert "# J-cut / L-cut Audio Transition Plan" in markdown
    assert "one FFmpeg encode" in markdown
    assert "Play every changed boundary at 1×" in markdown
    assert "cannot decide whether overlapping dialogue" in markdown


@pytest.mark.parametrize("kind", ["j_cut", "l_cut"])
def test_apply_renders_transactionally_and_receipt_verifies(tmp_path, kind):
    config, _, _ = _project(tmp_path)
    plan_path = tmp_path / f"audio_transition_plan_{kind}.json"
    output = tmp_path / f"joined-{kind}.mp4"
    receipt_path = tmp_path / f"audio_transition_apply_{kind}.json"
    plan = build_audio_transition_plan(str(config), [parse_transition(f"1,{kind},0.2")])
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    receipt = apply_plan(
        str(plan_path),
        str(output),
        receipt_path=str(receipt_path),
        no_subtitles=True,
        no_cover=True,
        no_loudnorm=True,
        no_content_guard=True,
    )

    assert output.is_file()
    assert receipt["version"] == "audio_transition_apply.v1"
    assert receipt["output"]["media"]["has_audio"] is True
    assert receipt["output"]["media"]["video_codec"] == "h264"
    assert receipt["output"]["media"]["audio_codec"] == "aac"
    assert receipt["output"]["media"]["width"] == 160
    assert receipt["output"]["media"]["height"] == 90
    assert receipt["output"]["media"]["fps"] == pytest.approx(24.0, abs=0.01)
    assert receipt["output"]["media"]["duration"] == pytest.approx(2.4, abs=0.12)
    assert verify_receipt(plan, receipt) == []
    assert not list(tmp_path.glob(f".joined-{kind}-*.mp4"))
    with pytest.raises(ValueError, match="already exists"):
        apply_plan(str(plan_path), str(output))


def test_cli_plan_verify_and_apply(tmp_path):
    config, _, _ = _project(tmp_path)
    plan_path = tmp_path / "cli_plan.json"
    markdown_path = tmp_path / "cli_plan.md"
    output = tmp_path / "cli_joined.mp4"
    receipt = tmp_path / "cli_receipt.json"
    script = str(REPO / "scripts" / "audio_transition.py")

    planned = subprocess.run(
        [
            sys.executable,
            script,
            "plan",
            str(config),
            "--transition",
            "1,j,0.2",
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown_path),
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert json.loads(plan_path.read_text(encoding="utf-8"))["summary"]["j_cuts"] == 1

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
            "--no-subtitles",
            "--no-cover",
            "--no-loudnorm",
            "--no-content-guard",
        ],
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr

    receipt_verified = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            str(plan_path),
            "--receipt",
            str(receipt),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert receipt_verified.returncode == 0, receipt_verified.stdout + receipt_verified.stderr


def test_cli_refuses_existing_plan_markdown_and_receipt_targets(tmp_path):
    config, _, _ = _project(tmp_path)
    plan_path = tmp_path / "cli_plan.json"
    markdown_path = tmp_path / "cli_plan.md"
    script = str(REPO / "scripts" / "audio_transition.py")
    markdown_path.write_text("keep me", encoding="utf-8")

    planned = subprocess.run(
        [
            sys.executable,
            script,
            "plan",
            str(config),
            "--transition",
            "1,j,0.2",
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown_path),
        ],
        capture_output=True,
        text=True,
    )
    assert planned.returncode == 2
    assert "refusing to overwrite" in planned.stderr
    assert markdown_path.read_text(encoding="utf-8") == "keep me"
    assert not plan_path.exists()

    plan = build_audio_transition_plan(str(config), [parse_transition("1,j_cut,0.2")])
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    receipt_path = tmp_path / "existing_receipt.json"
    receipt_path.write_text("keep receipt", encoding="utf-8")
    output = tmp_path / "must-not-render.mp4"

    with pytest.raises(ValueError, match="refusing to overwrite"):
        apply_plan(str(plan_path), str(output), receipt_path=str(receipt_path))
    assert receipt_path.read_text(encoding="utf-8") == "keep receipt"
    assert not output.exists()
