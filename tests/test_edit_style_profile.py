import json
import os
import subprocess
import sys
from datetime import date

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from edit_style_profile import (  # noqa: E402
    StyleProfileError,
    apply_profile,
    create_profile,
    profile_id,
    template_spec,
    verify_profile,
)
from cover_variants import build_plan as build_cover_plan  # noqa: E402
from generate_caption import generate_caption  # noqa: E402


def _spec():
    spec = template_spec()
    spec["name"] = "jay-tech"
    spec["approval"]["approved_by"] = "Jay"
    spec["approval"]["approved_at"] = "2026-08-23"
    spec["caption_defaults"]["force_spelling"] = {"open ai": "OpenAI"}
    return spec


def test_create_profile_is_portable_and_canonical():
    profile = create_profile(_spec(), generated_at="2026-08-23T00:00:00Z", today=date(2026, 8, 23))

    assert profile["version"] == "edit_style_profile.v1"
    assert profile["status"] == "ready"
    assert profile["profile_id"].startswith("esp_")
    assert profile["profile_id"] == profile_id(profile)
    assert verify_profile(profile, today=date(2026, 8, 23))["status"] == "ready"
    assert "/" not in json.dumps(profile["render_defaults"])


def test_profile_tampering_is_blocked():
    profile = create_profile(_spec(), today=date(2026, 8, 23))
    profile["render_defaults"]["subtitle_style"] = "neon"

    verification = verify_profile(profile, today=date(2026, 8, 23))

    assert verification["status"] == "blocked"
    assert "profile_id_mismatch" in verification["blockers"]


def test_unknown_or_unsafe_render_default_is_rejected():
    spec = _spec()
    spec["render_defaults"]["font_path"] = "/private/font.ttf"

    with pytest.raises(StyleProfileError, match="unknown key"):
        create_profile(spec, today=date(2026, 8, 23))


def test_reference_or_approved_output_basis_requires_evidence():
    spec = _spec()
    spec["approval"]["basis"] = "approved_outputs"

    with pytest.raises(StyleProfileError, match="requires at least one evidence"):
        create_profile(spec, today=date(2026, 8, 23))


def test_apply_only_fills_missing_fields_and_records_profile():
    profile = create_profile(_spec(), today=date(2026, 8, 23))
    config = {
        "clips": [{"video": "origin/talk.mp4", "start": 0, "end": 3}],
        "subtitle_style": "minimal",
        "bgm_volume": None,
    }

    result = apply_profile(profile, config, today=date(2026, 8, 23))

    assert result["config"]["subtitle_style"] == "minimal"
    assert result["preserved"]["subtitle_style"] == "minimal"
    assert result["config"]["bgm_volume"] == 0.12
    assert result["applied"]["cover_style"] == "techcard"
    assert result["config"]["style_profile"] == {
        "name": "jay-tech",
        "profile_id": profile["profile_id"],
    }
    assert config["bgm_volume"] is None


def test_caption_uses_creator_spelling_and_publish_window():
    profile = create_profile(_spec(), today=date(2026, 8, 23))

    caption = generate_caption(
        "open ai 工作流可以帮助创作者提高效率。" * 8,
        style_profile=profile,
        strict=False,
    )

    assert "OpenAI" in caption["caption_body"]
    assert caption["publish_time_hint"] == "weekday 21:00-22:30"


def test_cover_variants_use_profile_style_but_explicit_style_wins(tmp_path):
    profile = create_profile(_spec(), today=date(2026, 8, 23))

    styled = build_cover_plan(
        str(tmp_path / "talk.mp4"),
        title="AI 工作流",
        style_profile=profile,
    )
    overridden = build_cover_plan(
        str(tmp_path / "talk.mp4"),
        title="AI 工作流",
        style="minimal",
        style_profile=profile,
    )

    assert styled["variants"][0]["style"] == "techcard"
    assert styled["input"]["style_source"] == "style_profile"
    assert styled["style_profile"]["profile_id"] == profile["profile_id"]
    assert overridden["variants"][0]["style"] == "minimal"
    assert overridden["input"]["style_source"] == "cli"


def test_cli_template_create_verify_and_apply_round_trip(tmp_path):
    script = os.path.join(REPO, "scripts", "edit_style_profile.py")
    spec_path = tmp_path / "edit_style_profile_spec.json"
    profile_path = tmp_path / "edit_style_profile.json"
    profile_md = tmp_path / "edit_style_profile.md"
    config_path = tmp_path / "render_config.json"
    styled_path = tmp_path / "render_config_styled.json"
    receipt_path = tmp_path / "edit_style_profile_apply.json"

    template = subprocess.run(
        [sys.executable, script, "template", "--output", str(spec_path)],
        capture_output=True,
        text=True,
    )
    assert template.returncode == 0, template.stderr
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["name"] = "jay-tech"
    spec["approval"]["approved_by"] = "Jay"
    spec["approval"]["approved_at"] = date.today().isoformat()
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    create = subprocess.run(
        [
            sys.executable,
            script,
            "create",
            "--spec",
            str(spec_path),
            "--output",
            str(profile_path),
            "--markdown",
            str(profile_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert create.returncode == 0, create.stderr

    verify = subprocess.run(
        [sys.executable, script, "verify", "--profile", str(profile_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr

    config_path.write_text(json.dumps({"clips": [], "subtitle_style": "minimal"}), encoding="utf-8")
    apply = subprocess.run(
        [
            sys.executable,
            script,
            "apply",
            "--profile",
            str(profile_path),
            "--config",
            str(config_path),
            "--output",
            str(styled_path),
            "--receipt",
            str(receipt_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert apply.returncode == 0, apply.stderr
    styled = json.loads(styled_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert styled["subtitle_style"] == "minimal"
    assert styled["cover_style"] == "techcard"
    assert receipt["status"] == "ready"
    assert receipt["preserved"] == {"subtitle_style": "minimal"}
    assert "Edit Style Profile" in profile_md.read_text(encoding="utf-8")


def test_force_cannot_overwrite_bound_input_or_hardlink(tmp_path):
    script = os.path.join(REPO, "scripts", "edit_style_profile.py")
    spec_path = tmp_path / "spec.json"
    profile_path = tmp_path / "profile.json"
    spec = _spec()
    spec["approval"]["approved_at"] = date.today().isoformat()
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    profile_path.write_text(json.dumps(create_profile(spec)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--profile",
            str(profile_path),
            "--output",
            str(profile_path),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must not overwrite an input" in result.stderr

    hardlink_path = tmp_path / "profile-hardlink.json"
    os.link(profile_path, hardlink_path)
    hardlink_result = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--profile",
            str(profile_path),
            "--output",
            str(hardlink_path),
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert hardlink_result.returncode == 2
    assert "hard-linked input" in hardlink_result.stderr


def test_render_caption_and_cover_help_expose_style_profile():
    for script_name in ("render_final.py", "generate_caption.py", "cover_variants.py"):
        result = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts", script_name), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "--style-profile" in result.stdout
