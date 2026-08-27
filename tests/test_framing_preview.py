import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import framing_preview as framing  # noqa: E402
from multi_export import PRESETS, build_ffmpeg_command, load_framing_strategies  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


MEDIA = {
    "duration": 10.0,
    "fps": 30.0,
    "width": 1920,
    "height": 1080,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "has_audio": True,
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
}


def _fake_render(_source, destination, *, strategy, width, height, times):
    destination.write_bytes(f"{strategy}:{width}x{height}:{times}".encode())
    return {"width": 360 * len(times), "height": round(height * 360 / width / 2) * 2, "sample_frames": len(times)}


def _create_report(tmp_path, monkeypatch, *, platforms=("xhs", "douyin"), require_selection=True):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"master video")
    monkeypatch.setattr(framing, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(
        framing,
        "get_video_info",
        lambda _path: (MEDIA["duration"], MEDIA["width"], MEDIA["height"], MEDIA["fps"], 0),
    )
    monkeypatch.setattr(framing, "_render_variant", _fake_render)
    report = framing.create_report(
        str(source),
        project_dir=str(tmp_path),
        preview_dir="verify/framing",
        platforms=platforms,
        require_selection=require_selection,
    )
    return source, report


def test_filter_graphs_preserve_aspect_and_emit_vout():
    cover = framing.build_filter_complex("cover", 1080, 1920)
    contain = framing.build_filter_complex("contain", 1080, 1920)
    blur = framing.build_filter_complex("blur", 1080, 1920)

    assert "force_original_aspect_ratio=increase" in cover and "crop=1080:1920" in cover
    assert "force_original_aspect_ratio=decrease" in contain and "pad=1080:1920" in contain
    assert "split=2" in blur and "gblur=sigma=30" in blur and "overlay=" in blur
    assert all(graph.endswith("[vout]") for graph in (cover, contain, blur))


def test_create_select_verify_and_preview_drift(tmp_path, monkeypatch):
    _source, report = _create_report(tmp_path, monkeypatch)

    assert report["status"] == "blocked"
    assert report["summary"]["blocking"] == 2
    assert [item["strategy"] for item in report["platforms"][0]["variants"]] == list(framing.STRATEGIES)

    report = framing.select_strategy(report, "xhs", "contain")
    report = framing.select_strategy(report, "douyin", "blur")
    verification = framing.verify_report(report)
    assert verification["status"] == "ready"
    assert verification["summary"]["blocking"] == 0

    preview_path = tmp_path / report["platforms"][0]["variants"][0]["preview"]["path"]
    preview_path.write_bytes(b"changed")
    stale = framing.verify_report(report)
    assert stale["status"] == "blocked"
    assert any("preview bytes changed" in item for item in stale["blockers"])


def test_matching_aspect_auto_selects_native(tmp_path, monkeypatch):
    monkeypatch.setitem(MEDIA, "width", 1080)
    monkeypatch.setitem(MEDIA, "height", 1920)
    _source, report = _create_report(tmp_path, monkeypatch, platforms=("douyin",))

    entry = report["platforms"][0]
    assert entry["aspect_mismatch"] is False
    assert entry["selected_strategy"] == "native"
    assert [item["strategy"] for item in entry["variants"]] == ["native"]
    assert report["status"] == "ready"


def test_source_drift_and_derived_state_tampering_block(tmp_path, monkeypatch):
    source, report = _create_report(tmp_path, monkeypatch, platforms=("xhs",), require_selection=False)
    source.write_bytes(b"replacement master")
    stale = framing.verify_report(report)
    assert any("source video bytes changed" in item for item in stale["blockers"])

    source.write_bytes(b"master video")
    report["status"] = "ready"
    tampered = framing.verify_report(report)
    assert any("stored status" in item for item in tampered["blockers"])

    report["source"]["display_width"] = 0
    malformed = framing.verify_report(report)
    assert any("source display dimensions are invalid" in item for item in malformed["blockers"])


def test_multi_export_builds_reviewed_blur_graph():
    cmd = build_ffmpeg_command(
        "in.mp4", "out.mp4", PRESETS["douyin"],
        src_w=1920, src_h=1080, src_duration=10.0,
        framing_strategy="blur",
    )

    assert "-filter_complex" in cmd
    assert "gblur=sigma=30" in cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert "0:a:0?" in cmd
    assert "-vf" not in cmd


def test_multi_export_loads_exact_source_choices(tmp_path, monkeypatch):
    source, report = _create_report(tmp_path, monkeypatch, platforms=("xhs",), require_selection=True)
    report = framing.select_strategy(report, "xhs", "cover")
    report_path = tmp_path / "work" / "framing_preview.json"
    report_path.parent.mkdir()
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert load_framing_strategies(str(report_path), str(source), ["xhs"]) == {"xhs": "cover"}
    try:
        load_framing_strategies(str(report_path), str(tmp_path / "other.mp4"), ["xhs"])
    except ValueError as exc:
        assert "different source" in str(exc)
    else:
        raise AssertionError("a framing report must not be reusable for another source")


def test_pipeline_manifest_live_verifies_framing_report(tmp_path, monkeypatch):
    _source, report = _create_report(tmp_path, monkeypatch, platforms=("xhs",), require_selection=True)
    report = framing.select_strategy(report, "xhs", "contain")
    report_path = tmp_path / "work" / "framing_preview.json"
    report_path.parent.mkdir()
    report_path.write_text(json.dumps(report), encoding="utf-8")

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["framing_preview"])
    gate = next(item for item in manifest["gates"] if item["category"] == "framing_preview")
    assert gate["status"] == "ready"

    preview_path = tmp_path / report["platforms"][0]["variants"][0]["preview"]["path"]
    preview_path.write_bytes(b"stale")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(item for item in stale["gates"] if item["category"] == "framing_preview")
    assert gate["status"] == "blocked"


def test_cli_refuses_report_hardlink_to_source(tmp_path, monkeypatch):
    source = tmp_path / "master.mp4"
    source.write_bytes(b"master video")
    report_path = tmp_path / "framing_preview.json"
    os.link(source, report_path)
    monkeypatch.setattr(framing, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(
        framing,
        "get_video_info",
        lambda _path: (MEDIA["duration"], MEDIA["width"], MEDIA["height"], MEDIA["fps"], 0),
    )

    result = framing.main([
        "create", "--project-dir", str(tmp_path), "--video", str(source),
        "--platforms", "xhs", "--preview-dir", "verify/framing",
        "--output", str(report_path), "--force",
    ])

    assert result == 1
    assert source.read_bytes() == b"master video"


def test_preview_directory_must_not_traverse_symlink(tmp_path):
    source = tmp_path / "master.mp4"
    source.write_bytes(b"master video")
    target = tmp_path / "real-preview"
    target.mkdir()
    (tmp_path / "linked-preview").symlink_to(target, target_is_directory=True)

    try:
        framing.create_report(
            str(source), project_dir=str(tmp_path), preview_dir="linked-preview",
            platforms=("xhs",),
        )
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("preview output must not traverse a symlink")


def test_cli_help_lists_create_select_and_verify():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/framing_preview.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "create" in result.stdout
    assert "select" in result.stdout
    assert "verify" in result.stdout
