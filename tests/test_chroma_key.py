import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import chroma_key  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _media():
    return {
        "duration": 4.0,
        "fps": 24.0,
        "width": 320,
        "height": 180,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "has_audio": True,
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }


def _stub_runtime(monkeypatch):
    monkeypatch.setattr(chroma_key, "probe_media", lambda _path: dict(_media()))
    monkeypatch.setattr(
        chroma_key,
        "probe_background",
        lambda _path: {
            "kind": "image",
            "duration": 0.0,
            "fps": 0.0,
            "width": 640,
            "height": 360,
            "video_codec": "png",
            "pixel_format": "rgb24",
            "has_audio": False,
            "audio_codec": "",
            "sample_rate": 0,
            "channels": 0,
        },
    )
    monkeypatch.setattr(
        chroma_key,
        "_available_filters",
        lambda: {"alphaextract", "chromakey", "despill", "overlay"},
    )

    def fake_preview(_foreground, _background, **kwargs):
        kwargs["composite_path"].write_bytes(f"composite-{kwargs['time_s']}".encode())
        kwargs["matte_path"].write_bytes(f"matte-{kwargs['time_s']}".encode())

    monkeypatch.setattr(chroma_key, "_render_preview_pair", fake_preview)


def _prepare(tmp_path, monkeypatch, **kwargs):
    foreground = tmp_path / "origin" / "presenter.mp4"
    background = tmp_path / "origin" / "studio.png"
    foreground.parent.mkdir(parents=True)
    foreground.write_bytes(b"foreground")
    background.write_bytes(b"background")
    _stub_runtime(monkeypatch)
    report = chroma_key.prepare_report(
        str(foreground),
        str(background),
        project_dir=str(tmp_path),
        output_video="output/chroma-key.mp4",
        **kwargs,
    )
    return report


def _approve(report):
    return chroma_key.record_review(
        report,
        reviewer="Jay",
        note="Hair, hands, clothing, matte, spill, and background perspective checked on every preview.",
        edge_quality="pass",
        subject_integrity="pass",
        spill_control="pass",
        background_fit="pass",
    )


def test_prepare_renders_composite_and_matte_evidence(tmp_path, monkeypatch):
    report = _prepare(tmp_path, monkeypatch)

    assert report["version"] == chroma_key.VERSION
    assert report["settings"]["sample_times"] == [0.6, 2.0, 3.4]
    assert [item["kind"] for item in report["previews"]] == [
        "composite", "matte", "composite", "matte", "composite", "matte",
    ]
    assert all((tmp_path / item["path"]).is_file() for item in report["previews"])
    assert report["summary"]["blocking"] == 1
    assert report["blockers"] == ["preview review has not been recorded"]
    assert chroma_key.verify_report(report)["summary"]["blocking"] == 1


def test_review_apply_verify_ready_lifecycle(tmp_path, monkeypatch):
    report = _approve(_prepare(tmp_path, monkeypatch))
    assert report["blockers"] == ["approved chroma-key composite has not been rendered"]

    def fake_render(_foreground, _background, destination, **_kwargs):
        destination.write_bytes(b"rendered-composite")

    monkeypatch.setattr(chroma_key, "_render_composite", fake_render)
    applied = chroma_key.apply_report(report)
    verification = chroma_key.verify_report(applied)

    assert applied["status"] == "ready"
    assert applied["summary"] == {
        "preview_frames": 6,
        "reviewed": 1,
        "applied": 1,
        "blocking": 0,
        "warnings": 0,
    }
    assert (tmp_path / applied["application"]["path"]).read_bytes() == b"rendered-composite"
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0


def test_failed_preview_check_blocks_full_render(tmp_path, monkeypatch):
    report = chroma_key.record_review(
        _prepare(tmp_path, monkeypatch),
        reviewer="Jay",
        note="Green fringe remains around hair.",
        edge_quality="pass",
        subject_integrity="pass",
        spill_control="fail",
        background_fit="pass",
    )

    assert "preview review failed spill_control" in report["blockers"]
    with pytest.raises(ValueError, match="cannot apply before a passing preview review"):
        chroma_key.apply_report(report)


def test_live_verify_rejects_source_preview_and_filter_drift(tmp_path, monkeypatch):
    report = _approve(_prepare(tmp_path, monkeypatch))
    (tmp_path / report["foreground"]["path"]).write_bytes(b"changed-foreground")
    (tmp_path / report["previews"][0]["path"]).write_bytes(b"changed-preview")
    report["filter_contract"]["matte_sha256"] = "0" * 64
    report["report_id"] = chroma_key.canonical_report_id(report)

    verification = chroma_key.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("foreground video bytes changed" in item for item in verification["blockers"])
    assert any("preview bytes changed" in item for item in verification["blockers"])
    assert any("matte filter contract changed" in item for item in verification["blockers"])


def test_verify_fails_closed_on_malformed_settings(tmp_path, monkeypatch):
    report = _prepare(tmp_path, monkeypatch)
    report["settings"]["similarity"] = {"invalid": True}
    report["report_id"] = chroma_key.canonical_report_id(report)

    verification = chroma_key.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("invalid chroma-key settings" in item for item in verification["blockers"])


def test_filter_graph_keys_descpills_scales_and_overlays():
    settings = chroma_key._settings(
        key_color="green",
        similarity=0.12,
        blend=0.08,
        despill=0.5,
        sample_times=[1.0],
    )
    graph = chroma_key.build_composite_filter(settings, _media())
    matte = chroma_key.build_matte_filter(settings)

    assert "chromakey=0x00FF00:0.120000:0.080000" in graph
    assert "despill=type=green:mix=0.500000" in graph
    assert "scale=320:180:force_original_aspect_ratio=increase" in graph
    assert "crop=320:180" in graph
    assert "overlay=shortest=1" in graph
    assert "alphaextract" in matte


def test_available_filters_accepts_ffmpeg_eight_two_character_flags(monkeypatch):
    monkeypatch.setattr(
        chroma_key,
        "_run",
        lambda _command: subprocess.CompletedProcess(
            _command,
            0,
            stdout=" .. alphaextract V->V Extract alpha\n TS chromakey V->V Key color\n TS despill V->V Despill\n TS overlay VV->V Overlay\n",
            stderr="",
        ),
    )

    assert chroma_key._available_filters() == {"alphaextract", "chromakey", "despill", "overlay"}


def test_project_output_accepts_external_alias_that_resolves_inside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "project-alias"
    alias.symlink_to(project, target_is_directory=True)

    resolved = chroma_key._project_output(
        str(alias / "work" / "report.md"),
        root=project.resolve(),
        label="report",
    )

    assert resolved == project / "work" / "report.md"


def test_prepare_cli_refuses_output_collision_with_source(tmp_path, monkeypatch):
    foreground = tmp_path / "origin" / "presenter.mp4"
    background = tmp_path / "origin" / "studio.png"
    foreground.parent.mkdir(parents=True)
    foreground.write_bytes(b"foreground")
    background.write_bytes(b"background")
    monkeypatch.setattr(
        chroma_key,
        "prepare_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prepare must not run")),
    )

    result = chroma_key.main([
        "prepare",
        "--project-dir", str(tmp_path),
        "--foreground", str(foreground),
        "--background", str(background),
        "--output-video", str(foreground),
        "--report", "work/chroma_key.json",
        "--force",
    ])

    assert result == 1
    assert foreground.read_bytes() == b"foreground"


def test_prepare_cli_refuses_report_collision_with_planned_preview(tmp_path, monkeypatch):
    foreground = tmp_path / "origin" / "presenter.mp4"
    background = tmp_path / "origin" / "studio.png"
    foreground.parent.mkdir(parents=True)
    foreground.write_bytes(b"foreground")
    background.write_bytes(b"background")
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(
        chroma_key,
        "prepare_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prepare must not run")),
    )

    result = chroma_key.main([
        "prepare",
        "--project-dir", str(tmp_path),
        "--foreground", str(foreground),
        "--background", str(background),
        "--output-video", "output/composite.mp4",
        "--preview-dir", "verify/chroma_key",
        "--report", "verify/chroma_key/chroma-key-01-0_600s-composite.png",
        "--force",
    ])

    assert result == 1
    assert not (tmp_path / "verify" / "chroma_key").exists()


def test_cli_help_lists_full_lifecycle():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/chroma_key.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("prepare", "review", "apply", "verify"))
