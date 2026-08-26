import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generated_motion_window as motion  # noqa: E402


MEDIA = {
    "duration": 4.0,
    "fps": 24.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}

FREEZES = [
    {"start": 0.0, "end": 0.5, "duration": 0.5},
    {"start": 2.0, "end": 2.4, "duration": 0.4},
    {"start": 3.5, "end": 4.0, "duration": 0.5},
]


def _probe(_path):
    return dict(MEDIA)


def _detect(_path, **_kwargs):
    return [dict(item) for item in FREEZES]


def _build(tmp_path):
    source = tmp_path / "work" / "generated" / "shot-001.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"generated-video")
    plan = motion.build_plan(
        source,
        project_dir=tmp_path,
        probe_fn=_probe,
        detect_fn=_detect,
    )
    return source, plan


def test_parse_freezedetect_includes_trailing_interval():
    log = "\n".join(
        [
            "[freezedetect] lavfi.freezedetect.freeze_start: 0",
            "[freezedetect] lavfi.freezedetect.freeze_duration: 0.5",
            "[freezedetect] lavfi.freezedetect.freeze_end: 0.5",
            "[freezedetect] lavfi.freezedetect.freeze_start: 3.5",
        ]
    )

    assert motion.parse_freezedetect(log, duration=4.0) == [
        {"start": 0.0, "end": 0.5, "duration": 0.5},
        {"start": 3.5, "end": 4.0, "duration": 0.5},
    ]


def test_derive_analysis_finds_edge_and_interior_motion_windows():
    analysis = motion.derive_analysis(
        duration=4.0,
        fps=24.0,
        freezes=FREEZES,
        min_active=0.25,
    )

    assert analysis["active_intervals"] == [
        {"start": 0.5, "end": 2.0, "duration": 1.5},
        {"start": 2.4, "end": 3.5, "duration": 1.1},
    ]
    assert analysis["interior_freezes"] == [FREEZES[1]]
    assert analysis["recommendation"]["action"] == "trim"
    assert analysis["recommendation"]["start"] == 0.5
    assert analysis["recommendation"]["end"] == 3.5


def test_build_plan_binds_source_and_blocks_for_review(tmp_path):
    source, plan = _build(tmp_path)

    assert plan["source"]["sha256"] == motion._sha256(source)
    assert plan["detection"]["freezes"] == FREEZES
    assert plan["recommendation"]["action"] == "trim"
    assert plan["status"] == "blocked"
    assert plan["blockers"] == [motion.PENDING_REVIEW]
    assert plan["plan_id"] == motion._plan_id(plan)


def test_confirm_trim_uses_recommendation_and_waits_for_apply(tmp_path):
    _source, plan = _build(tmp_path)

    confirmed = motion.confirm_plan(
        plan,
        decision="trim",
        reviewed_by="editor",
        note="Full source played at 1x; start and end are inside motion.",
        probe_fn=_probe,
        detect_fn=_detect,
    )
    verification = motion.verify_plan(confirmed, probe_fn=_probe, detect_fn=_detect)

    assert confirmed["decision"]["start"] == 0.5
    assert confirmed["decision"]["end"] == 3.5
    assert confirmed["blockers"] == [motion.PENDING_APPLY]
    assert verification["blockers"] == [motion.PENDING_APPLY]


def test_confirm_trim_rejects_boundary_inside_freeze(tmp_path):
    _source, plan = _build(tmp_path)

    with pytest.raises(ValueError, match="inside an active interval"):
        motion.confirm_plan(
            plan,
            decision="trim",
            start=0.25,
            end=3.5,
            reviewed_by="editor",
            note="Bad boundary test.",
            probe_fn=_probe,
            detect_fn=_detect,
        )


def test_confirm_keep_is_nonblocking_but_warns_about_edge_freeze(tmp_path):
    _source, plan = _build(tmp_path)

    confirmed = motion.confirm_plan(
        plan,
        decision="keep",
        reviewed_by="editor",
        note="The opening hold is an intentional product beat.",
        probe_fn=_probe,
        detect_fn=_detect,
    )
    verification = motion.verify_plan(confirmed, probe_fn=_probe, detect_fn=_detect)

    assert verification["summary"]["blocking"] == 0
    assert verification["status"] == "warn"
    assert any("intentionally kept" in item for item in verification["warnings"])


def test_live_verify_detects_source_drift(tmp_path):
    source, plan = _build(tmp_path)
    source.write_bytes(b"changed-generated-video")

    verification = motion.verify_plan(plan, probe_fn=_probe, detect_fn=_detect)

    assert verification["status"] == "blocked"
    assert any("source bytes changed" in item for item in verification["blockers"])


def test_rewritten_plan_id_cannot_hide_noncanonical_detection(tmp_path):
    _source, plan = _build(tmp_path)
    plan["detection"]["active_intervals"][0]["start"] = 0.75
    motion._set_derived(plan)

    verification = motion.verify_plan(plan, probe_fn=_probe, detect_fn=_detect)

    assert any("canonical derivation" in item for item in verification["blockers"])
    assert any("live freeze evidence changed" in item for item in verification["blockers"])


def test_source_must_stay_in_project_and_not_be_symlinked(tmp_path):
    outside = tmp_path.parent / "outside-generated.mp4"
    outside.write_bytes(b"outside")
    linked = tmp_path / "linked.mp4"
    linked.symlink_to(outside)

    for source in (outside, linked):
        with pytest.raises(ValueError, match="project directory|symlink"):
            motion.build_plan(
                source,
                project_dir=tmp_path,
                probe_fn=_probe,
                detect_fn=_detect,
            )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_real_ffmpeg_analyze_apply_and_verify(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=24:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=24:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=24:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2.4",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    plan = motion.build_plan(source, project_dir=tmp_path)
    assert plan["recommendation"]["action"] == "trim"
    plan = motion.confirm_plan(
        plan,
        decision="trim",
        reviewed_by="test",
        note="Synthetic clip reviewed at full speed.",
    )
    plan_path = tmp_path / "motion.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    applied = motion.apply_plan(plan_path, output=tmp_path / "active.mp4")
    verification = motion.verify_plan(applied)

    assert verification["summary"]["blocking"] == 0
    assert applied["application"]["output"]["video_codec"] == "h264"
    assert applied["application"]["output"]["pixel_format"] == "yuv420p"
    assert applied["application"]["output"]["has_audio"] is True
    assert applied["application"]["output"]["audio_codec"] == "aac"
    assert applied["application"]["output"]["duration"] == pytest.approx(
        applied["application"]["selected_range"]["duration"], abs=0.12
    )


def test_output_guard_refuses_hardlink_collision(tmp_path):
    source, _plan = _build(tmp_path)
    plan_path = tmp_path / "motion.json"
    plan_path.write_text("{}", encoding="utf-8")
    collision = tmp_path / "collision.mp4"
    os.link(source, collision)

    with pytest.raises(ValueError, match="bound input"):
        motion._safe_output(
            collision,
            root=tmp_path.resolve(),
            label="trimmed output",
            forbidden=[source, plan_path],
            force=True,
        )


def test_cli_help_and_pending_strict_exit(tmp_path):
    source, plan = _build(tmp_path)
    plan_path = tmp_path / "motion.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    script = Path(REPO) / "scripts" / "generated_motion_window.py"

    help_result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert help_result.returncode == 0
    assert "active motion windows" in help_result.stdout

    # CLI uses real ffprobe, so only assert argument parsing here; the real
    # lifecycle test above covers strict live verification.
    result = subprocess.run(
        [sys.executable, str(script), "verify", str(plan_path), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 or "ffprobe" in result.stderr
    assert source.exists()
