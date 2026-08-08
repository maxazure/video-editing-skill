import json
import os
import subprocess
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import video_stabilization as stabilization  # noqa: E402


MEDIA = {
    "duration": 4.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "has_audio": True,
}


def _source(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source-video")
    return path


def _patch_probe(monkeypatch):
    monkeypatch.setattr(stabilization, "probe_media", lambda _path: dict(MEDIA))


def test_auto_backend_prefers_two_pass_vidstab():
    filters = {"vidstabdetect", "vidstabtransform", "deshake"}

    assert stabilization.select_backend("auto", filters) == "vidstab"


def test_auto_backend_uses_explicit_deshake_fallback():
    assert stabilization.select_backend("auto", {"deshake"}) == "deshake"


def test_plan_defaults_to_blocked_review_decision(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    plan = stabilization.build_plan(str(_source(tmp_path)), filters={"deshake"})

    assert plan["backend"]["name"] == "deshake"
    assert plan["status"] == "blocked"
    assert plan["summary"]["blocking"] == 1
    assert plan["blockers"] == ["stabilization decision still needs review"]
    assert plan["summary"]["warnings"] == 1


def test_keep_decision_is_ready_without_render(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    plan = stabilization.build_plan(
        str(_source(tmp_path)),
        decision="keep",
        reviewed_by_label="editor",
        note="Intentional handheld movement",
        filters={"deshake"},
    )
    verified = stabilization.verify_plan(plan, {"deshake"})

    assert verified["summary"]["blocking"] == 0
    assert verified["status"] == "warn"
    assert verified["application"] is None


def test_stabilize_decision_requires_reviewer_label(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)

    with pytest.raises(ValueError, match="reviewed-by"):
        stabilization.build_plan(
            str(_source(tmp_path)),
            decision="stabilize",
            filters={"deshake"},
        )


def test_verify_rejects_rewritten_noncanonical_settings(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    plan = stabilization.build_plan(
        str(_source(tmp_path)),
        decision="keep",
        reviewed_by_label="editor",
        filters={"deshake"},
    )
    plan["settings"]["rx"] = 64
    plan["plan_id"] = stabilization._plan_id(plan)

    verified = stabilization.verify_plan(plan, {"deshake"})

    assert verified["summary"]["blocking"] > 0
    assert any("settings do not match" in item for item in verified["blockers"])


def test_verify_detects_source_drift(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    source = _source(tmp_path)
    plan = stabilization.build_plan(
        str(source),
        decision="keep",
        reviewed_by_label="editor",
        filters={"deshake"},
    )
    source.write_bytes(b"changed-source-video")

    verified = stabilization.verify_plan(plan, {"deshake"})

    assert any("source size changed" in item for item in verified["blockers"])


def test_apply_then_confirm_requires_full_comparison_review(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "video_stabilization_plan.json"
    plan = stabilization.build_plan(
        str(source),
        decision="stabilize",
        reviewed_by_label="editor",
        note="The visible handheld jitter is unintended",
        filters={"deshake"},
    )
    stabilization._atomic_write_json(plan_path, plan)

    rendered = []

    def fake_run(command):
        destination = command[-1]
        if destination != "-":
            with open(destination, "wb") as handle:
                handle.write(f"render-{len(rendered)}".encode("utf-8"))
            rendered.append(destination)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(stabilization, "_run_command", fake_run)
    output = tmp_path / "output" / "stable.mp4"
    comparison = tmp_path / "verify" / "stable_compare.mp4"
    applied = stabilization.apply_plan(
        str(plan_path),
        str(output),
        str(comparison),
        filters={"deshake"},
    )

    assert output.is_file()
    assert comparison.is_file()
    assert source.read_bytes() == b"source-video"
    assert applied["status"] == "blocked"
    assert applied["blockers"] == [stabilization.PENDING_REVIEW]
    assert applied["application"]["review"]["status"] == "pending"

    confirmed = stabilization.confirm_plan(
        str(plan_path),
        reviewed_by_label="editor",
        note="Watched the full A/B at 1x; edges and intentional pan are acceptable",
        filters={"deshake"},
    )
    verified = stabilization.verify_plan(confirmed, {"deshake"})

    assert confirmed["status"] == "warn"
    assert confirmed["summary"]["blocking"] == 0
    assert confirmed["application"]["review"]["status"] == "approved"
    assert verified["summary"]["blocking"] == 0
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["plan_id"] == confirmed["plan_id"]


def test_apply_rejects_existing_output_without_force(tmp_path, monkeypatch):
    _patch_probe(monkeypatch)
    source = _source(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan = stabilization.build_plan(
        str(source),
        decision="stabilize",
        reviewed_by_label="editor",
        filters={"deshake"},
    )
    stabilization._atomic_write_json(plan_path, plan)
    output = tmp_path / "stable.mp4"
    output.write_bytes(b"user-output")

    with pytest.raises(ValueError, match="already exists"):
        stabilization.apply_plan(
            str(plan_path),
            str(output),
            str(tmp_path / "compare.mp4"),
            filters={"deshake"},
        )


def test_filter_profiles_compile_exact_backend_contracts():
    deshake = stabilization._settings_for("deshake", "balanced", 30.0)
    vidstab = stabilization._settings_for("vidstab", "conservative", 30.0)

    assert "deshake=rx=16:ry=16" in stabilization.build_filter("deshake", deshake, "")
    assert "smoothing=15" in stabilization.build_filter("vidstab", vidstab, "/tmp/a.trf")


def test_plan_cli_refuses_silent_artifact_overwrite(tmp_path, monkeypatch, capsys):
    _patch_probe(monkeypatch)
    monkeypatch.setattr(stabilization, "_available_filters", lambda: {"deshake"})
    source = _source(tmp_path)
    output = tmp_path / "plan.json"
    markdown = tmp_path / "plan.md"
    args = [
        "plan",
        str(source),
        "--output",
        str(output),
        "--markdown",
        str(markdown),
    ]

    assert stabilization.main(args) == 0
    original = output.read_bytes()
    assert stabilization.main(args) == 1
    assert output.read_bytes() == original
    assert "pass --force to replace" in capsys.readouterr().err
