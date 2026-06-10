import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_media_library_import_copies_and_records_provenance(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")
    project = tmp_path / "project"
    project.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/media_library.py"),
            "import",
            str(source),
            "--project-dir",
            str(project),
            "--category",
            "broll",
            "--copy",
            "--provider",
            "pexels",
            "--source-url",
            "https://www.pexels.com/video/demo-123/",
            "--creator",
            "Demo Creator",
            "--license",
            "Pexels License",
            "--tag",
            "workflow,dashboard",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    item = payload["item"]
    assert item["path"] == "media/broll/source.mp4"
    assert item["category"] == "broll"
    assert item["tags"] == ["workflow", "dashboard"]
    assert item["metadata"]["provider"] == "pexels"
    assert (project / "media" / "broll" / "source.mp4").exists()

    index = json.loads((project / "media_index.json").read_text(encoding="utf-8"))
    assert index["items"][0]["metadata"]["source_url"].endswith("demo-123/")


def test_media_library_annotate_updates_existing_item(tmp_path):
    project = tmp_path / "project"
    media = project / "media" / "broll"
    media.mkdir(parents=True)
    clip = media / "clip.mp4"
    clip.write_bytes(b"fake video")

    (project / "media_index.json").write_text(
        json.dumps({
            "items": [
                {
                    "path": "media/broll/clip.mp4",
                    "type": "video",
                    "category": "broll",
                    "tags": ["old"],
                    "metadata": {"provider": "owned"},
                }
            ]
        }),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/media_library.py"),
            "annotate",
            str(clip),
            "--project-dir",
            str(project),
            "--tag",
            "new",
            "--source-url",
            "https://example.com/source",
            "--license",
            "owned",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    item = payload["item"]
    assert item["tags"] == ["old", "new"]
    assert item["metadata"]["provider"] == "owned"
    assert item["metadata"]["source_url"] == "https://example.com/source"
    assert item["metadata"]["license"] == "owned"


def test_media_library_annotate_missing_item_returns_one(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/media_library.py"),
            "annotate",
            "missing.mp4",
            "--project-dir",
            str(project),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "not found" in result.stderr
