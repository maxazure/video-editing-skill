"""publish_package.py upload package assembly."""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from publish_package import build_publish_package, emit_markdown  # noqa: E402


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _ready_project(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "day58_master.mp4", "master")
    _write(tmp_path / "output" / "day58_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "day58_caption.json", {
        "title": "AI效率工具",
        "caption_body": "这条视频讲清楚怎么把 AI 用到日常交付。",
        "tags": ["#AI工具", "效率"],
        "publish_time_hint": "weekday 21:00-22:30",
    })
    _write(tmp_path / "output" / "day58_xhs.mp4", "xhs")
    _write(tmp_path / "output" / "day58_douyin.mp4", "douyin")
    _write(tmp_path / "output" / "day58_wxch.mp4", "wxch")
    _write(tmp_path / "output" / "cover.png", "cover")
    _write(tmp_path / "output" / "subtitles" / "day58.srt", "1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    _write(tmp_path / "output" / "chapters-youtube.txt", "0:00 Intro\n")


def test_build_publish_package_ready_for_default_platforms(tmp_path):
    _ready_project(tmp_path)

    package = build_publish_package(str(tmp_path))

    assert package["version"] == "publish_package.v1"
    assert package["status"] == "ready"
    assert package["summary"]["ready_platforms"] == 3
    assert package["summary"]["blocking"] == 0
    assert {item["platform"] for item in package["platforms"]} == {"xhs", "douyin", "wxch"}
    xhs = next(item for item in package["platforms"] if item["platform"] == "xhs")
    assert xhs["video"].endswith("day58_xhs.mp4")
    assert xhs["caption"]["title"] == "AI效率工具"
    assert "#效率" in xhs["caption"]["tags"]
    assert "srt" in xhs["subtitles"]


def test_missing_platform_video_blocks_package(tmp_path):
    _ready_project(tmp_path)
    os.remove(tmp_path / "output" / "day58_wxch.mp4")

    package = build_publish_package(str(tmp_path))

    assert package["status"] == "blocked"
    assert "wxch: missing video export" in package["blockers"]
    wxch = next(item for item in package["platforms"] if item["platform"] == "wxch")
    assert wxch["status"] == "blocked"


def test_pipeline_manifest_blocker_is_propagated(tmp_path):
    _ready_project(tmp_path)
    manifest = {
        "version": "pipeline_manifest.v1",
        "status": "blocked",
        "blocked_gates": ["asset_provenance"],
        "warn_gates": [],
    }
    _write(tmp_path / "work" / "pipeline_manifest.json", manifest)

    package = build_publish_package(
        str(tmp_path),
        pipeline_manifest_path=str(tmp_path / "work" / "pipeline_manifest.json"),
    )

    assert package["status"] == "blocked"
    assert "pipeline_manifest blocked: asset_provenance" in package["blockers"]
    assert package["pipeline_status"] == "blocked"


def test_video_override_supports_extra_platform(tmp_path):
    _ready_project(tmp_path)
    override = tmp_path / "custom" / "shorts.mp4"
    _write(override, "shorts")

    package = build_publish_package(
        str(tmp_path),
        platforms=["youtube_shorts"],
        video_overrides={"youtube_shorts": override},
    )

    assert package["status"] == "ready"
    assert package["platforms"][0]["video"].endswith("shorts.mp4")
    assert package["platforms"][0]["chapter_text"] == "0:00 Intro"


def test_markdown_contains_platform_copy_and_checklist(tmp_path):
    _ready_project(tmp_path)

    package = build_publish_package(str(tmp_path))
    markdown = emit_markdown(package)

    assert "# Publish Package" in markdown
    assert "AI效率工具" in markdown
    assert "Checklist:" in markdown
    assert "day58_xhs.mp4" in markdown


def test_selected_cover_variant_is_used_before_generic_cover(tmp_path):
    _ready_project(tmp_path)
    selected = tmp_path / "output" / "cover-b_contrast.png"
    _write(selected, "selected")
    _write(tmp_path / "work" / "cover_variants.json", {
        "version": "cover_variants.v1",
        "status": "ready",
        "selected_variant": "cover-b",
        "selected_cover": str(selected),
        "summary": {"variants": 3, "selected": 1, "blocking": 0},
    })

    package = build_publish_package(str(tmp_path))

    assert package["cover_image"] == str(selected.resolve())
    assert all(item["cover_image"] == str(selected.resolve()) for item in package["platforms"])


def test_unselected_cover_variant_is_not_used_as_publish_cover(tmp_path):
    _ready_project(tmp_path)
    os.remove(tmp_path / "output" / "cover.png")
    _write(tmp_path / "output" / "covers" / "cover-a_primary.png", "variant")
    _write(tmp_path / "output" / "covers" / "cover-a_primary_preview.png", "preview")
    _write(tmp_path / "work" / "cover_variants.json", {
        "version": "cover_variants.v1",
        "status": "ready",
        "selected_variant": None,
        "selected_cover": None,
        "summary": {"variants": 3, "selected": 0, "blocking": 0},
    })

    package = build_publish_package(str(tmp_path))

    assert package["cover_image"] is None
    assert any("has no selected_cover" in warning for warning in package["warnings"])


def test_cli_writes_json_and_markdown(tmp_path):
    _ready_project(tmp_path)
    out_json = tmp_path / "work" / "publish_package.json"
    out_md = tmp_path / "work" / "publish_package.md"

    out = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/publish_package.py"),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert out.returncode == 0, out.stderr
    package = json.loads(out_json.read_text(encoding="utf-8"))
    assert package["status"] == "ready"
    assert out_md.read_text(encoding="utf-8").startswith("# Publish Package")


def test_cli_strict_returns_two_when_blocked(tmp_path):
    _ready_project(tmp_path)
    os.remove(tmp_path / "output" / "day58_xhs.mp4")

    out = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/publish_package.py"),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "publish_package.json"),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert out.returncode == 2
    assert "blocked" in out.stderr
