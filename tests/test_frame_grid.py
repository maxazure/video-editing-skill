import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from frame_grid import create, parse_showinfo, verify  # noqa: E402


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=s=160x90:r=12:d=2", "-c:v", "libx264", "-bf", "2",
                    "-y", str(path)], check=True)
    return path


def test_showinfo_parser_rejects_gaps():
    assert parse_showinfo("[Parsed_showinfo_1 @ 0x1] n:   0 pts: 5 pts_time:0.4\n") == [0.4]
    with pytest.raises(ValueError, match="order"):
        parse_showinfo("[Parsed_showinfo_1 @ 0x1] n:   1 pts: 5 pts_time:0.4\n")


def test_grid_maps_exact_decoded_indices_and_pts(source, tmp_path):
    image, receipt = tmp_path / "grid.png", tmp_path / "grid.json"
    result = create(source=str(source), output=str(image), receipt=str(receipt),
                    start_frame=3, step=2, count=6, columns=3)
    assert [entry["decoded_frame"] for entry in result["frames"]] == [3, 5, 7, 9, 11, 13]
    assert [entry["pts_seconds"] for entry in result["frames"]] == pytest.approx(
        [3 / 12, 5 / 12, 7 / 12, 9 / 12, 11 / 12, 13 / 12], abs=0.00001)
    assert result["frames"][-1]["row"] == 2
    assert verify(str(receipt))["status"] == "ready"


def test_single_frame_export(source, tmp_path):
    result = create(source=str(source), output=str(tmp_path / "frame.png"),
                    receipt=str(tmp_path / "frame.json"), start_frame=5, grid=False)
    assert result["frames"][0]["decoded_frame"] == 5
    assert result["frames"][0]["pts_seconds"] == pytest.approx(5 / 12, abs=0.00001)


def test_out_of_range_does_not_publish_image(source, tmp_path):
    image = tmp_path / "grid.png"
    with pytest.raises(ValueError, match="found 0"):
        create(source=str(source), output=str(image), receipt=str(tmp_path / "grid.json"),
               start_frame=1000)
    assert not image.exists()


def test_verify_detects_image_and_receipt_drift(source, tmp_path):
    image, receipt = tmp_path / "grid.png", tmp_path / "grid.json"
    create(source=str(source), output=str(image), receipt=str(receipt), start_frame=0)
    image.write_bytes(image.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="image changed"):
        verify(str(receipt))
    data = json.loads(receipt.read_text())
    data["selection"]["start_frame"] = 8
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="receipt content changed"):
        verify(str(receipt))


def test_verify_detects_source_drift(source, tmp_path):
    receipt = tmp_path / "grid.json"
    create(source=str(source), output=str(tmp_path / "grid.png"),
           receipt=str(receipt), start_frame=0)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source changed"):
        verify(str(receipt))


def test_rejects_source_alias_and_output_symlink(source, tmp_path):
    with pytest.raises(ValueError, match="differ"):
        create(source=str(source), output=str(source), receipt=str(tmp_path / "map.json"), start_frame=0)
    link = tmp_path / "link.png"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        create(source=str(source), output=str(link), receipt=str(tmp_path / "map.json"), start_frame=0)


def test_cli_round_trip(source, tmp_path):
    receipt = tmp_path / "grid.json"
    script = os.path.join(REPO, "scripts", "frame_grid.py")
    run = subprocess.run([sys.executable, script, "grid", str(source), "--start-frame", "2",
                          "--count", "4", "--output", str(tmp_path / "grid.png"),
                          "--receipt", str(receipt)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    check = subprocess.run([sys.executable, script, "verify", str(receipt)],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["frames"] == 4
