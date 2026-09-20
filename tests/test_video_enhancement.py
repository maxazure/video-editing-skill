import json
import os
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import video_enhancement as enhancement  # noqa: E402


FILTERS = {"scale", "setsar", "hstack", "fps", "minterpolate"}
SOURCE_MEDIA = {
    "duration": 2.0,
    "fps": 24.0,
    "width": 320,
    "height": 180,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}
OUTPUT_MEDIA = {
    **SOURCE_MEDIA,
    "fps": 48.0,
    "width": 640,
    "height": 360,
}


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source video bytes")
    return path


def _patch_source_probe(monkeypatch):
    monkeypatch.setattr(enhancement, "probe_media", lambda _path: dict(SOURCE_MEDIA))


def test_plan_binds_resize_interpolation_and_review_gate(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_source_probe(monkeypatch)

    plan = enhancement.build_plan(
        str(source),
        str(tmp_path / "enhanced.mp4"),
        str(tmp_path / "comparison.mp4"),
        scale=2,
        target_fps=48,
        filters=FILTERS,
    )

    assert plan["version"] == enhancement.VERSION
    assert plan["settings"]["target_width"] == 640
    assert plan["settings"]["target_height"] == 360
    assert plan["settings"]["interpolate"] is True
    assert plan["blockers"] == [enhancement.PENDING_APPLY]
    assert plan["status"] == "blocked"
    assert plan["review_contract"]["fields"] == list(enhancement.REVIEW_FIELDS)


def test_target_short_edge_preserves_landscape_and_portrait_aspect(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_source_probe(monkeypatch)
    landscape = enhancement.build_plan(
        str(source),
        str(tmp_path / "landscape.mp4"),
        str(tmp_path / "landscape-ab.mp4"),
        target_short_edge=360,
        filters=FILTERS,
    )
    assert (landscape["settings"]["target_width"], landscape["settings"]["target_height"]) == (640, 360)

    monkeypatch.setattr(
        enhancement,
        "probe_media",
        lambda _path: {**SOURCE_MEDIA, "width": 180, "height": 320},
    )
    portrait = enhancement.build_plan(
        str(source),
        str(tmp_path / "portrait.mp4"),
        str(tmp_path / "portrait-ab.mp4"),
        target_short_edge=360,
        filters=FILTERS,
    )
    assert (portrait["settings"]["target_width"], portrait["settings"]["target_height"]) == (360, 640)


def test_plan_rejects_noop_downscale_and_excessive_targets(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_source_probe(monkeypatch)
    paths = (str(tmp_path / "enhanced.mp4"), str(tmp_path / "ab.mp4"))
    with pytest.raises(ValueError, match="no-op"):
        enhancement.build_plan(str(source), *paths, target_fps=24, filters=FILTERS)
    with pytest.raises(ValueError, match="larger than"):
        enhancement.build_plan(str(source), *paths, target_short_edge=180, filters=FILTERS)
    with pytest.raises(ValueError, match="no more than 4"):
        enhancement.build_plan(str(source), *paths, scale=5, filters=FILTERS)


def test_filter_contract_uses_motion_interpolation_before_lanczos():
    settings = {
        "interpolate": True,
        "target_fps": 60.0,
        "target_width": 1920,
        "target_height": 1080,
    }
    video_filter = enhancement.build_filter(settings)
    assert video_filter.startswith("minterpolate=fps=60.000000")
    assert ",scale=1920:1080:flags=lanczos,setsar=1" in video_filter


def test_verify_detects_source_and_canonical_setting_drift(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_source_probe(monkeypatch)
    plan = enhancement.build_plan(
        str(source),
        str(tmp_path / "enhanced.mp4"),
        str(tmp_path / "comparison.mp4"),
        scale=2,
        filters=FILTERS,
    )
    source.write_bytes(b"changed")
    drift = enhancement.verify_plan(plan, FILTERS)
    assert any("source" in item and ("size" in item or "sha256" in item) for item in drift["blockers"])

    source.write_bytes(b"source video bytes")
    plan["settings"]["crf"] = 30
    rewritten = enhancement.verify_plan(plan, FILTERS)
    assert any("canonical request/source contract" in item for item in rewritten["blockers"])


def test_apply_confirm_and_live_verify(tmp_path, monkeypatch):
    source = _source(tmp_path)

    def probe(path):
        name = Path(path).name
        return dict(SOURCE_MEDIA if name == "source.mp4" else OUTPUT_MEDIA)

    monkeypatch.setattr(enhancement, "probe_media", probe)
    plan_path = tmp_path / "plan.json"
    enhanced_path = tmp_path / "enhanced.mp4"
    comparison_path = tmp_path / "comparison.mp4"
    plan = enhancement.build_plan(
        str(source),
        str(enhanced_path),
        str(comparison_path),
        scale=2,
        target_fps=48,
        filters=FILTERS,
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def fake_run(command, _label):
        candidate = Path(command[-1])
        if candidate.suffix == ".mp4":
            candidate.write_bytes(b"comparison" if "comparison" in candidate.name else b"enhanced")

    monkeypatch.setattr(enhancement, "_run_checked", fake_run)
    applied = enhancement.apply_plan(str(plan_path), filters=FILTERS)
    assert applied["blockers"] == [enhancement.PENDING_REVIEW]
    assert enhanced_path.is_file()
    assert comparison_path.is_file()

    checks = {field: "pass" for field in enhancement.REVIEW_FIELDS}
    confirmed = enhancement.confirm_plan(
        str(plan_path),
        checks=checks,
        reviewed_by_label="editor",
        note="Watched complete A/B and enhanced output; detail, motion, and sync pass.",
        filters=FILTERS,
    )
    assert confirmed["blockers"] == []
    assert confirmed["status"] == "warn"
    live = enhancement.verify_plan(
        json.loads(plan_path.read_text(encoding="utf-8")), FILTERS
    )
    assert live["blockers"] == []


def test_failed_review_remains_blocked(tmp_path, monkeypatch):
    source = _source(tmp_path)

    def probe(path):
        return dict(SOURCE_MEDIA if Path(path).name == "source.mp4" else OUTPUT_MEDIA)

    monkeypatch.setattr(enhancement, "probe_media", probe)
    plan_path = tmp_path / "plan.json"
    plan = enhancement.build_plan(
        str(source), str(tmp_path / "enhanced.mp4"), str(tmp_path / "comparison.mp4"),
        scale=2, target_fps=48, filters=FILTERS,
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def fake_run(command, _label):
        candidate = Path(command[-1])
        if candidate.suffix == ".mp4":
            candidate.write_bytes(b"render")

    monkeypatch.setattr(enhancement, "_run_checked", fake_run)
    enhancement.apply_plan(str(plan_path), filters=FILTERS)
    checks = {field: "pass" for field in enhancement.REVIEW_FIELDS}
    checks["motion_cadence"] = "fail"
    rejected = enhancement.confirm_plan(
        str(plan_path), checks=checks, reviewed_by_label="editor", note="Ghosting at 00:01.",
        filters=FILTERS,
    )
    assert enhancement.REJECTED_REVIEW in rejected["blockers"]


def test_cli_refuses_to_overwrite_existing_plan(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    _patch_source_probe(monkeypatch)
    monkeypatch.setattr(enhancement, "_available_filters", lambda: set(FILTERS))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("user data", encoding="utf-8")
    result = enhancement.main(
        [
            "plan", str(source), "--scale", "2", "--enhanced", str(tmp_path / "enhanced.mp4"),
            "--comparison", str(tmp_path / "comparison.mp4"), "--output", str(plan_path),
        ]
    )
    assert result == 2
    assert "already exists" in capsys.readouterr().err
    assert plan_path.read_text(encoding="utf-8") == "user data"
