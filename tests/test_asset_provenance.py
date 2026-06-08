import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from asset_provenance import (  # noqa: E402
    AssetRef,
    _load_media_index,
    build_provenance_manifest,
    collect_from_asset_manifest,
    collect_from_json_artifact,
    emit_markdown,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def test_media_index_pexels_metadata_defaults_to_publish_ready(tmp_path):
    asset = tmp_path / "broll" / "city.mp4"
    _write(asset, "fake video")
    _write(tmp_path / "media_index.json", {
        "items": [
            {
                "path": "broll/city.mp4",
                "type": "video",
                "category": "broll",
                "metadata": {
                    "provider": "pexels",
                    "source_url": "https://www.pexels.com/video/city-123/",
                    "creator": "Alex Example",
                },
            }
        ]
    })
    asset_manifest = tmp_path / "storyboard_assets.json"
    _write(asset_manifest, {
        "items": [
            {
                "shot_id": "shot_001",
                "route": "media_library_broll",
                "resolved_path": str(asset),
            }
        ]
    })

    refs = collect_from_asset_manifest(str(asset_manifest))
    manifest = build_provenance_manifest(refs, media_index=_load_media_index(str(tmp_path)))

    assert manifest["version"] == "asset_provenance.v1"
    assert manifest["summary"]["blocking"] == 0
    assert manifest["summary"]["credits"] == 1
    item = manifest["items"][0]
    assert item["provider"] == "pexels"
    assert item["license"] == "Pexels License"
    assert item["status"] == "ready"
    assert "Alex Example" in manifest["credits"][0]


def test_unknown_license_blocks_when_known_license_required(tmp_path):
    asset = tmp_path / "broll" / "unknown.mp4"
    _write(asset, "fake video")

    manifest = build_provenance_manifest(
        [AssetRef(path=str(asset), usage="broll", source_artifact="test")],
        require_known_license=True,
    )

    assert manifest["summary"]["blocking"] == 1
    assert "license_missing" in manifest["items"][0]["issues"]


def test_cc_by_without_creator_or_attribution_blocks(tmp_path):
    asset = tmp_path / "image.png"
    _write(asset, "fake image")

    manifest = build_provenance_manifest([
        AssetRef(
            path=str(asset),
            usage="image",
            source_artifact="test",
            metadata={
                "provider": "wikimedia",
                "license": "CC BY 4.0",
                "source_url": "https://commons.wikimedia.org/wiki/File:Example.png",
            },
        )
    ])

    item = manifest["items"][0]
    assert item["attribution_required"] is True
    assert item["status"] == "blocked"
    assert "attribution_required_but_incomplete" in item["issues"]


def test_render_config_paths_are_collected_relative_to_artifact(tmp_path):
    asset = tmp_path / "broll" / "city.mp4"
    _write(asset, "fake video")
    render_config = tmp_path / "work" / "render_config.json"
    _write(render_config, {"clips": [{"video": "../broll/city.mp4", "start": 0, "end": 2}]})

    refs = collect_from_json_artifact(str(render_config))

    assert len(refs) == 1
    assert refs[0].path == str(asset.resolve())
    assert refs[0].usage == "video"


def test_json_scanner_does_not_collect_source_url_metadata(tmp_path):
    artifact = tmp_path / "enrich_plan.json"
    _write(artifact, {
        "broll_overlays": [
            {
                "path": "clip.mp4",
                "source_url": "https://www.pexels.com/video/city-123/",
                "license_url": "https://www.pexels.com/license/",
            }
        ]
    })

    refs = collect_from_json_artifact(str(artifact))

    assert len(refs) == 1
    assert refs[0].path.endswith("clip.mp4")


def test_markdown_lists_credits(tmp_path):
    asset = tmp_path / "image.png"
    _write(asset, "fake image")
    manifest = build_provenance_manifest([
        AssetRef(
            path=str(asset),
            usage="image",
            source_artifact="test",
            metadata={
                "provider": "pixabay",
                "source_url": "https://pixabay.com/photos/demo-123/",
                "creator": "Pixabay Creator",
            },
        )
    ])

    markdown = emit_markdown(manifest)

    assert "# Asset Provenance Review" in markdown
    assert "## Credits" in markdown
    assert "Pixabay Creator" in markdown


def test_cli_writes_json_markdown_and_strict_exit(tmp_path):
    out_json = tmp_path / "asset_provenance.json"
    out_md = tmp_path / "asset_provenance.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "asset_provenance.py"),
            "--asset",
            str(tmp_path / "missing.mp4"),
            "--require-known-license",
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
    assert out_json.exists()
    assert out_md.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["blocking"] >= 1


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "asset_provenance.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "asset provenance" in result.stdout.lower()
