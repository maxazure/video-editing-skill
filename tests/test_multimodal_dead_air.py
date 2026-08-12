import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import multimodal_dead_air as dead_air  # noqa: E402
from jump_cut import Segment  # noqa: E402


SOURCE_MEDIA = {
    "duration": 10.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "has_video": True,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
}


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    return source


def _ready_plan(tmp_path: Path):
    source = _source(tmp_path)
    plan = dead_air.build_plan(
        str(source),
        str(tmp_path / "output" / "dead-air.mp4"),
        media=SOURCE_MEDIA,
        silences=[Segment(2.0, 5.0, 3.0)],
        freezes=[Segment(2.5, 5.0, 2.5)],
        noise_db=-35.0,
        pad_seconds=0.1,
        max_removal_ratio=0.3,
    )
    return source, plan


def test_parse_freezedetect_handles_complete_and_trailing_intervals():
    log = "\n".join(
        [
            "[freezedetect @ x] lavfi.freezedetect.freeze_start: 1.25",
            "[freezedetect @ x] lavfi.freezedetect.freeze_duration: 1.5",
            "[freezedetect @ x] lavfi.freezedetect.freeze_end: 2.75",
            "[freezedetect @ x] lavfi.freezedetect.freeze_start: 8.5",
        ]
    )

    assert dead_air.parse_freezedetect(log, duration=10.0) == [
        Segment(1.25, 2.75, 1.5),
        Segment(8.5, 10.0, 1.5),
    ]


def test_analysis_requires_sixty_percent_static_coverage_and_removes_only_overlap():
    analysis = dead_air.derive_analysis(
        10.0,
        [Segment(2.0, 6.0, 4.0), Segment(7.0, 9.0, 2.0)],
        [Segment(3.0, 5.6, 2.6), Segment(7.0, 7.8, 0.8)],
        min_static_overlap_ratio=0.6,
        pad_seconds=0.1,
        min_keep_seconds=0.15,
        max_removal_ratio=0.3,
        allow_over_budget=False,
    )

    assert analysis["summary"]["candidates"] == 1
    assert analysis["summary"]["rejected_silences"] == 1
    assert analysis["candidates"][0]["static_overlap_ratio"] == 0.65
    assert analysis["shared_intervals"] == [{"start": 3.0, "end": 5.6, "duration": 2.6}]
    assert analysis["removed_segments"] == [{"start": 3.1, "end": 5.5, "duration": 2.4}]


def test_removal_budget_blocks_until_explicit_override():
    common = dict(
        duration=10.0,
        silences=[Segment(2.0, 6.0, 4.0)],
        freezes=[Segment(2.0, 6.0, 4.0)],
        min_static_overlap_ratio=0.6,
        pad_seconds=0.0,
        min_keep_seconds=0.15,
        max_removal_ratio=0.2,
    )

    blocked = dead_air.derive_analysis(**common, allow_over_budget=False)
    approved = dead_air.derive_analysis(**common, allow_over_budget=True)

    assert blocked["status"] == "blocked"
    assert blocked["summary"]["blocking"] == 1
    assert blocked["removal_budget"]["proposed_ratio"] == 0.4
    assert approved["status"] == "ready"
    assert approved["removal_budget"]["override"] is True
    assert approved["summary"]["warnings"] == 1


def test_plan_binds_source_and_emits_reviewable_markdown(tmp_path):
    source, plan = _ready_plan(tmp_path)
    plan_path = tmp_path / "review plan.json"
    markdown = dead_air.emit_markdown(plan, str(plan_path))

    assert plan["version"] == dead_air.VERSION
    assert plan["source"]["sha256"] == dead_air._sha256(source)
    assert plan["settings"]["min_static_overlap_ratio"] == 0.6
    assert plan["summary"]["candidates"] == 1
    assert plan["plan_id"] == dead_air._plan_id(plan)
    assert "Static coverage" in markdown
    assert "timeline_view.py" in markdown
    assert f"--cut-list '{plan_path}'" in markdown
    assert "--limit 1" in markdown


def test_verify_propagates_canonical_budget_blockers_and_override_warning(tmp_path, monkeypatch):
    source = _source(tmp_path)
    common = dict(
        source_path=str(source),
        delivery_path=str(tmp_path / "output.mp4"),
        media=SOURCE_MEDIA,
        silences=[Segment(2.0, 6.0, 4.0)],
        freezes=[Segment(2.0, 6.0, 4.0)],
        noise_db=-35.0,
        max_removal_ratio=0.2,
    )
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))

    blocked = dead_air.build_plan(**common)
    approved = dead_air.build_plan(**common, allow_over_budget=True)

    assert dead_air.verify_plan(blocked)["summary"]["blocking"] == 1
    assert dead_air.verify_plan(approved)["summary"]["blocking"] == 0
    assert dead_air.verify_plan(approved)["summary"]["warnings"] == 1


def test_apply_refuses_canonical_over_budget_plan(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan = dead_air.build_plan(
        str(source),
        str(tmp_path / "output.mp4"),
        media=SOURCE_MEDIA,
        silences=[Segment(2.0, 6.0, 4.0)],
        freezes=[Segment(2.0, 6.0, 4.0)],
        noise_db=-35.0,
        max_removal_ratio=0.2,
    )
    plan_path = tmp_path / "plan.json"
    dead_air._write_json(plan_path, plan)
    rendered = []
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))
    monkeypatch.setattr(dead_air, "run_ffmpeg_with_fallback", lambda *_args, **_kwargs: rendered.append(True))

    try:
        dead_air.apply_plan(str(plan_path))
    except ValueError as exc:
        assert "above the 20.0% safety budget" in str(exc)
    else:
        raise AssertionError("blocked over-budget plan should not render")

    assert rendered == []


def test_verify_detects_source_drift_and_rewritten_derived_state(tmp_path, monkeypatch):
    source, plan = _ready_plan(tmp_path)
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))
    source.write_bytes(b"changed-source")

    drifted = dead_air.verify_plan(plan)

    assert any("source bytes changed" in item for item in drifted["blockers"])

    source.write_bytes(b"source-video")
    plan["candidates"][0]["static_overlap_ratio"] = 1.0
    plan["plan_id"] = dead_air._plan_id(plan)
    rewritten = dead_air.verify_plan(plan)

    assert any("stored candidates" in item for item in rewritten["blockers"])


def test_apply_uses_single_pass_temp_render_and_records_validated_delivery(tmp_path, monkeypatch):
    source, plan = _ready_plan(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    dead_air._write_json(plan_path, plan)
    output_media = {**SOURCE_MEDIA, "duration": plan["output_duration_estimate"]}

    def fake_probe(path):
        return dict(SOURCE_MEDIA if Path(path).resolve() == source.resolve() else output_media)

    commands = []

    def fake_render(command, *, has_video):
        assert has_video is True
        commands.append(list(command))
        Path(command[-1]).write_bytes(b"rendered-delivery")

    monkeypatch.setattr(dead_air, "probe_media", fake_probe)
    monkeypatch.setattr(dead_air, "run_ffmpeg_with_fallback", fake_render)
    monkeypatch.setattr(
        dead_air,
        "_run_checked",
        lambda command, label: subprocess.CompletedProcess(command, 0, "", ""),
    )

    applied = dead_air.apply_plan(str(plan_path))
    delivery = tmp_path / "output" / "dead-air.mp4"

    assert delivery.read_bytes() == b"rendered-delivery"
    assert source.read_bytes() == b"source-video"
    assert "concat=n=2:v=1:a=1" in " ".join(commands[0])
    assert applied["application"]["full_decode_checked"] is True
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["application"]["sha256"] == dead_air._sha256(delivery)
    assert dead_air.verify_plan(persisted)["summary"]["blocking"] == 0


def test_apply_rejects_output_contract_drift_before_promotion(tmp_path, monkeypatch):
    source, plan = _ready_plan(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    dead_air._write_json(plan_path, plan)
    wrong_output = {
        **SOURCE_MEDIA,
        "duration": plan["output_duration_estimate"],
        "video_codec": "hevc",
    }

    def fake_probe(path):
        return dict(SOURCE_MEDIA if Path(path).resolve() == source.resolve() else wrong_output)

    def fake_render(command, *, has_video):
        assert has_video is True
        Path(command[-1]).write_bytes(b"wrong-codec-delivery")

    monkeypatch.setattr(dead_air, "probe_media", fake_probe)
    monkeypatch.setattr(dead_air, "run_ffmpeg_with_fallback", fake_render)

    try:
        dead_air.apply_plan(str(plan_path))
    except RuntimeError as exc:
        assert "must be H.264/AAC" in str(exc)
    else:
        raise AssertionError("wrong output codec should block promotion")

    assert not Path(plan["delivery"]).exists()


def test_apply_rejects_non_social_pixel_format_before_promotion(tmp_path, monkeypatch):
    source, plan = _ready_plan(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    dead_air._write_json(plan_path, plan)
    wrong_output = {
        **SOURCE_MEDIA,
        "duration": plan["output_duration_estimate"],
        "pixel_format": "yuv444p",
    }

    def fake_probe(path):
        return dict(SOURCE_MEDIA if Path(path).resolve() == source.resolve() else wrong_output)

    def fake_render(command, *, has_video):
        assert has_video is True
        Path(command[-1]).write_bytes(b"wrong-pixel-format")

    monkeypatch.setattr(dead_air, "probe_media", fake_probe)
    monkeypatch.setattr(dead_air, "run_ffmpeg_with_fallback", fake_render)

    try:
        dead_air.apply_plan(str(plan_path))
    except RuntimeError as exc:
        assert "must be yuv420p" in str(exc)
    else:
        raise AssertionError("non-social pixel format should block promotion")

    assert not Path(plan["delivery"]).exists()


def test_apply_refuses_noop_plan(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan = dead_air.build_plan(
        str(source),
        str(tmp_path / "output.mp4"),
        media=SOURCE_MEDIA,
        silences=[],
        freezes=[Segment(0.0, 10.0, 10.0)],
        noise_db=-35.0,
    )
    plan_path = tmp_path / "plan.json"
    dead_air._write_json(plan_path, plan)
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))

    try:
        dead_air.apply_plan(str(plan_path))
    except ValueError as exc:
        assert "nothing to apply" in str(exc)
    else:
        raise AssertionError("no-op plan should not render")


def test_plan_cli_refuses_existing_artifacts_before_detection(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    markdown_path = tmp_path / "work" / "multimodal_dead_air_plan.md"
    markdown_path.parent.mkdir(parents=True)
    markdown_path.write_text("user review", encoding="utf-8")
    detected = []
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: detected.append(True))

    result = dead_air.main(
        [
            "plan",
            str(source),
            "--delivery",
            str(tmp_path / "output.mp4"),
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown_path),
        ]
    )

    assert result == 1
    assert detected == []
    assert markdown_path.read_text(encoding="utf-8") == "user review"
    assert "pass --force to replace" in capsys.readouterr().err


def test_plan_cli_refuses_symlink_artifact_without_touching_target(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    target = tmp_path / "user-plan.json"
    target.write_text("user data", encoding="utf-8")
    linked_plan = tmp_path / "multimodal_dead_air_plan.json"
    linked_plan.symlink_to(target)
    detected = []
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: detected.append(True))

    result = dead_air.main(
        [
            "plan",
            str(source),
            "--delivery",
            str(tmp_path / "output.mp4"),
            "--output",
            str(linked_plan),
        ]
    )

    assert result == 1
    assert detected == []
    assert target.read_text(encoding="utf-8") == "user data"
    assert "refusing symlink output" in capsys.readouterr().err


def test_plan_force_cannot_overwrite_source_with_plan(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    original = source.read_bytes()
    detected = []
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: detected.append(True))

    result = dead_air.main(
        [
            "plan",
            str(source),
            "--delivery",
            str(tmp_path / "output.mp4"),
            "--output",
            str(source),
            "--force",
        ]
    )

    assert result == 1
    assert detected == []
    assert source.read_bytes() == original
    assert "must not overwrite source" in capsys.readouterr().err


def test_apply_markdown_cannot_overwrite_source(tmp_path, monkeypatch):
    source, plan = _ready_plan(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    dead_air._write_json(plan_path, plan)
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))
    rendered = []
    monkeypatch.setattr(dead_air, "run_ffmpeg_with_fallback", lambda *_args, **_kwargs: rendered.append(True))

    try:
        dead_air.apply_plan(str(plan_path), markdown_path=str(source))
    except ValueError as exc:
        assert "must not overwrite source" in str(exc)
    else:
        raise AssertionError("markdown collision should be rejected")

    assert rendered == []
    assert source.read_bytes() == b"source-video"


def test_cli_plan_verify_round_trip(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "multimodal_dead_air_plan.json"
    markdown_path = tmp_path / "work" / "multimodal_dead_air_plan.md"
    monkeypatch.setattr(dead_air, "probe_media", lambda _path: dict(SOURCE_MEDIA))
    monkeypatch.setattr(dead_air, "_noise_db", lambda _value, _source: -35.0)
    monkeypatch.setattr(
        dead_air,
        "detect_silences",
        lambda *_args: [Segment(2.0, 4.0, 2.0)],
    )
    monkeypatch.setattr(
        dead_air,
        "detect_freezes",
        lambda *_args: [Segment(2.0, 4.0, 2.0)],
    )

    assert dead_air.main(
        [
            "plan",
            str(source),
            "--delivery",
            str(tmp_path / "output.mp4"),
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown_path),
        ]
    ) == 0
    assert dead_air.main(["verify", str(plan_path), "--strict"]) == 0
    assert json.loads(plan_path.read_text(encoding="utf-8"))["summary"]["candidates"] == 1
    assert "Multimodal Dead-Air Plan" in markdown_path.read_text(encoding="utf-8")
