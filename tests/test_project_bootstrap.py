import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from project_bootstrap import bootstrap_project, emit_markdown  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value=b"media"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def test_bootstrap_copies_and_classifies_sources(tmp_path):
    source_dir = tmp_path / "sources"
    _write(source_dir / "talking.mp4")
    _write(source_dir / "broll" / "city.mov")
    _write(source_dir / "audio" / "lav.wav")
    _write(source_dir / "bgm" / "theme.mp3")
    _write(source_dir / "cover" / "thumb.png")
    _write(source_dir / "captions.srt")

    project_dir = tmp_path / "project"
    inventory = bootstrap_project([str(source_dir)], project_dir=str(project_dir), title="Launch Day")

    assert inventory["version"] == "project_bootstrap.v1"
    assert inventory["status"] == "ready"
    assert inventory["title"] == "Launch Day"
    assert inventory["summary"]["files"] == 6
    assert inventory["summary"]["categories"] == {
        "raw": 1,
        "broll": 1,
        "audio": 1,
        "bgm": 1,
        "asset": 1,
        "sidecar": 1,
    }
    assert (project_dir / "origin" / "raw" / "talking.mp4").exists()
    assert (project_dir / "origin" / "broll" / "city.mov").exists()
    assert (project_dir / "origin" / "audio" / "lav.wav").exists()
    assert (project_dir / "origin" / "bgm" / "theme.mp3").exists()
    assert (project_dir / "origin" / "assets" / "thumb.png").exists()
    assert (project_dir / "origin" / "sidecars" / "captions.srt").exists()
    assert (source_dir / "talking.mp4").exists()


def test_duplicate_filenames_get_unique_project_paths(tmp_path):
    _write(tmp_path / "camera_a" / "clip.mp4", b"a")
    _write(tmp_path / "camera_b" / "clip.mp4", b"b")

    inventory = bootstrap_project(
        [str(tmp_path / "camera_a"), str(tmp_path / "camera_b")],
        project_dir=str(tmp_path / "project"),
    )

    names = sorted(item["filename"] for item in inventory["files"])
    assert names == ["clip-2.mp4", "clip.mp4"]
    assert len({item["project_path"] for item in inventory["files"]}) == 2


def test_hardlink_mode_records_action(tmp_path):
    source = tmp_path / "src" / "talk.mp4"
    _write(source)

    inventory = bootstrap_project([str(source)], project_dir=str(tmp_path / "project"), mode="hardlink")
    item = inventory["files"][0]

    assert item["action"] in {"hardlinked", "copied_fallback"}
    if item["action"] == "hardlinked":
        assert os.stat(source).st_ino == os.stat(item["project_path"]).st_ino


def test_markdown_contains_source_table_and_next_actions(tmp_path):
    source = tmp_path / "src" / "talk.mp4"
    _write(source)
    inventory = bootstrap_project([str(source)], project_dir=str(tmp_path / "project"))

    markdown = emit_markdown(inventory)

    assert "# Project Bootstrap" in markdown
    assert "| video | raw | copied | `origin/raw/talk.mp4` |" in markdown
    assert "## Next Actions" in markdown
    assert "transcribe.py" in markdown


def test_cli_writes_inventory_memory_and_strict_warns_on_empty_sources(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    project = tmp_path / "project"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "project_bootstrap.py"),
            "--source",
            str(empty),
            "--project-dir",
            str(project),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    inventory_path = project / "work" / "source_inventory.json"
    assert inventory_path.exists()
    assert (project / "work" / "source_inventory.md").exists()
    assert (project / "project.md").exists()
    assert (project / "next_steps.md").exists()
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory["status"] == "warn"
    assert inventory["summary"]["files"] == 0


def test_cli_writes_hash_when_requested(tmp_path):
    source = tmp_path / "src" / "talk.mp4"
    _write(source, b"abc")
    project = tmp_path / "project"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "project_bootstrap.py"),
            "--source",
            str(source),
            "--project-dir",
            str(project),
            "--hash",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    inventory = json.loads((project / "work" / "source_inventory.json").read_text(encoding="utf-8"))
    assert len(inventory["files"][0]["sha256"]) == 64
