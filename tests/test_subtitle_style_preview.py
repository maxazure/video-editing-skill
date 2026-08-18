import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import subtitle_style_preview as preview  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub_runtime(monkeypatch, font_path):
    media = {
        "duration": 10.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "has_audio": True,
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(preview, "probe_media", lambda _path: dict(media))
    monkeypatch.setattr(preview, "get_video_info", lambda _path: (10.0, 640, 360, 30.0, 0))
    monkeypatch.setattr(preview, "find_chinese_font", lambda _path=None: (str(font_path), "Test Sans"))

    def fake_render(_source, destination, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(kwargs["ass_content"].encode("utf-8"))
        return {"width": 1080, "height": 480, "sample_frames": len(kwargs["times"])}

    monkeypatch.setattr(preview, "_render_variant", fake_render)


def _create(tmp_path, monkeypatch, **kwargs):
    source = tmp_path / "origin" / "talk.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    font = tmp_path / "fonts" / "test.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"font-bytes")
    _stub_runtime(monkeypatch, font)
    return preview.create_report(
        str(source),
        project_dir=str(tmp_path),
        preview_dir="verify/subtitle_styles",
        **kwargs,
    )


def test_create_renders_three_exact_ass_style_variants(tmp_path, monkeypatch):
    report = _create(tmp_path, monkeypatch, selected_style="normal", require_selection=True)

    assert report["version"] == preview.VERSION
    assert report["status"] == "ready"
    assert report["summary"] == {
        "variants": 3,
        "sample_frames": 3,
        "selected": 1,
        "blocking": 0,
        "warnings": 0,
    }
    assert [item["style"] for item in report["variants"]] == ["normal", "minimal", "bold_pop"]
    assert report["settings"]["sample_times"] == [1.5, 5.0, 8.5]
    assert all((tmp_path / item["preview"]["path"]).is_file() for item in report["variants"])
    assert preview.verify_report(report)["summary"]["blocking"] == 0


def test_selection_gate_and_post_review_selection(tmp_path, monkeypatch):
    report = _create(tmp_path, monkeypatch, require_selection=True)

    assert report["status"] == "blocked"
    assert report["blockers"] == ["subtitle style selection is required before rendering"]

    selected = preview.select_style(report, "bold_pop")
    assert selected["status"] == "ready"
    assert selected["selected_style"] == "bold_pop"
    assert selected["selected_preview"].endswith("subtitle-style-bold_pop.jpg")
    assert preview.verify_report(selected)["summary"]["blocking"] == 0


def test_live_verify_detects_changed_source_preview_and_ass_contract(tmp_path, monkeypatch):
    report = _create(tmp_path, monkeypatch, selected_style="normal")
    source = tmp_path / report["source"]["path"]
    font = tmp_path / "fonts" / "test.ttf"
    rendered = tmp_path / report["variants"][0]["preview"]["path"]
    source.write_bytes(b"changed-source")
    font.write_bytes(b"changed-font")
    rendered.write_bytes(b"changed-preview")
    report["variants"][1]["ass_sha256"] = "0" * 64
    report["report_id"] = preview.canonical_report_id(report)

    verification = preview.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("source video bytes changed" in item for item in verification["blockers"])
    assert any("subtitle font bytes changed" in item for item in verification["blockers"])
    assert any("normal subtitle preview bytes changed" in item for item in verification["blockers"])
    assert any("minimal ASS style contract changed" in item for item in verification["blockers"])


def test_verify_fails_closed_on_malformed_canvas_and_wrong_project(tmp_path, monkeypatch):
    report = _create(tmp_path, monkeypatch, selected_style="normal")
    report["settings"]["width"] = {"not": "an integer"}
    report["report_id"] = preview.canonical_report_id(report)

    verification = preview.verify_report(report, str(tmp_path / "another-project"))

    assert verification["status"] == "blocked"
    assert any("does not match the verification project" in item for item in verification["blockers"])
    assert any("canvas and font size must be integers" in item for item in verification["blockers"])


def test_create_cli_refuses_to_overwrite_source_with_report(tmp_path, monkeypatch):
    source = tmp_path / "origin" / "talk.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    monkeypatch.setattr(
        preview,
        "create_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("create must not run")),
    )

    result = preview.main([
        "create",
        "--project-dir", str(tmp_path),
        "--video", str(source),
        "--preview-dir", "verify/subtitle_styles",
        "--output", str(source),
        "--force",
    ])

    assert result == 1
    assert source.read_bytes() == b"source-video"


def test_select_cli_refuses_markdown_collision_with_report(tmp_path, monkeypatch):
    report = _create(tmp_path, monkeypatch, selected_style="normal")
    report_path = tmp_path / "work" / "subtitle_style_preview.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    original = report_path.read_bytes()

    result = preview.main([
        "select",
        "--report", str(report_path),
        "--style", "bold_pop",
        "--markdown", str(report_path),
    ])

    assert result == 1
    assert report_path.read_bytes() == original


def test_preview_ass_reuses_final_renderer_styles():
    normal = preview.build_preview_ass(
        style="normal", text="字幕预览", font_name="Test Sans", font_size=48, width=1080, height=1920,
    )
    karaoke = preview.build_preview_ass(
        style="karaoke", text="字幕预览", font_name="Test Sans", font_size=48, width=1080, height=1920,
    )

    assert "Style: Default,Test Sans,48" in normal
    assert "Style: Karaoke,Test Sans,48" in karaoke
    assert "\\kf" in karaoke


def test_sample_times_reject_duplicates_after_clamping():
    try:
        preview.select_sample_times(1.0, [5.0, 6.0])
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate clamped sample times must be rejected")


def test_cli_help_lists_create_select_and_verify():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/subtitle_style_preview.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "create" in result.stdout
    assert "select" in result.stdout
    assert "verify" in result.stdout
