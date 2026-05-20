"""render_final.py versioned output paths."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from render_final import next_versioned_output_path  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_next_versioned_output_starts_at_v1(tmp_path):
    out = next_versioned_output_path(tmp_path / "master.mp4")
    assert out == str(tmp_path / "master_V1.mp4")


def test_next_versioned_output_skips_existing_versions(tmp_path):
    (tmp_path / "master_V1.mp4").write_bytes(b"old")
    (tmp_path / "master_V3.mp4").write_bytes(b"old")
    out = next_versioned_output_path(tmp_path / "master.mp4")
    assert out == str(tmp_path / "master_V4.mp4")


def test_next_versioned_output_normalizes_versioned_seed(tmp_path):
    (tmp_path / "master_V1.mp4").write_bytes(b"old")
    out = next_versioned_output_path(tmp_path / "master_V9.mp4")
    assert out == str(tmp_path / "master_V2.mp4")


def test_render_help_exposes_versioned_output():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--versioned-output" in out.stdout
