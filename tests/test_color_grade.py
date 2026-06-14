import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from color_grade import (  # noqa: E402
    VERSION,
    build_ffmpeg_filter,
    build_plan,
    color_grade_filter_from_value,
    emit_markdown,
    normalize_adjustments,
)
from render_final import append_color_grade_filter  # noqa: E402


def test_preset_builds_bounded_ffmpeg_filter():
    adjustments, warnings = normalize_adjustments(preset="warm")
    vf = build_ffmpeg_filter(adjustments)

    assert warnings == []
    assert adjustments["temperature"] > 0
    assert "eq=brightness=" in vf
    assert "colorbalance=" in vf


def test_custom_overrides_are_clamped_with_warning():
    adjustments, warnings = normalize_adjustments(
        preset="natural",
        overrides={"contrast": 9, "saturation": 0.1, "sharpness": 2},
    )

    assert adjustments["contrast"] == 1.35
    assert adjustments["saturation"] == 0.65
    assert adjustments["sharpness"] == 0.8
    assert len(warnings) == 3


def test_plan_markdown_contains_render_config_snippet():
    plan = build_plan(preset="screen", notes=["for software walkthrough"])
    md = emit_markdown(plan)

    assert plan["version"] == VERSION
    assert plan["render_config"]["color_grade"]["preset"] == "screen"
    assert "Color Grade Plan" in md
    assert "render_final.py Config Snippet" in md


def test_render_final_resolves_plan_path_and_appends_filter(tmp_path):
    plan = build_plan(preset="punchy")
    path = tmp_path / "color_grade.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    vf = color_grade_filter_from_value("color_grade.json", base_dir=str(tmp_path))
    lines = []
    label, stage = append_color_grade_filter(lines, "[merged_v]", 3, vf)

    assert "unsharp=" in vf
    assert label == "[vstage3]"
    assert stage == 4
    assert lines == [f"[merged_v]{vf}[vstage3]"]


def test_missing_plan_path_is_not_treated_as_filter(tmp_path):
    try:
        color_grade_filter_from_value("work/missing_color_grade.json", base_dir=str(tmp_path))
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing plan path should fail early")


def test_cli_writes_json_markdown_and_strict_reports_clamp(tmp_path):
    out_json = tmp_path / "color_grade.json"
    out_md = tmp_path / "color_grade.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/color_grade.py"),
            "--preset",
            "natural",
            "--contrast",
            "4",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["warnings"]
    assert "Color Grade Plan" in out_md.read_text(encoding="utf-8")


def test_render_help_exposes_color_grade():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--color-grade" in result.stdout
