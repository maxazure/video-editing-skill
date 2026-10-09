import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from color_grade import color_grade_filter_from_value  # noqa: E402
from lut_grade import build_plan, filter_from_plan  # noqa: E402


def cube(path: Path, *, invert: bool = False) -> None:
    rows = ["LUT_3D_SIZE 2", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for blue in (0, 1):
        for green in (0, 1):
            for red in (0, 1):
                values = (red, green, blue)
                rows.append(" ".join(str(1 - x if invert else x) for x in values))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_lut_plan_loads_into_color_grade_and_detects_drift(tmp_path):
    lut = tmp_path / "test look.cube"
    cube(lut)
    plan = build_plan(str(lut))
    plan_path = tmp_path / "look.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    vf = color_grade_filter_from_value("look.json", base_dir=str(tmp_path))
    assert vf == f"lut3d=file='{lut}':interp=tetrahedral"
    assert filter_from_plan(plan) == vf

    cube(lut, invert=True)
    with pytest.raises(ValueError, match="changed"):
        color_grade_filter_from_value(str(plan_path))


def test_plan_tampering_and_invalid_lut_fail(tmp_path):
    lut = tmp_path / "look.cube"
    cube(lut)
    plan = build_plan(str(lut))
    plan["ffmpeg"]["vf"] += ",hflip"
    with pytest.raises(ValueError, match="changed"):
        filter_from_plan(plan)

    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="could not load LUT"):
        build_plan(str(lut))


def test_inverted_lut_changes_decoded_pixels(tmp_path):
    lut = tmp_path / "invert.cube"
    cube(lut, invert=True)
    vf = filter_from_plan(build_plan(str(lut)))
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=16x16:d=0.04", "-filter_complex",
         f"[0:v]{vf},format=rgb24[graded]", "-map", "[graded]",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) == 16 * 16 * 3
    red, green, blue = result.stdout[:3]
    assert red < 20 and green > 230 and blue > 230


def test_cli_roundtrip_and_unsafe_path(tmp_path):
    lut = tmp_path / "look.cube"
    cube(lut)
    output = tmp_path / "look.json"
    script = str(REPO / "scripts" / "lut_grade.py")
    result = subprocess.run([sys.executable, script, "plan", str(lut), "--output", str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, script, "verify", str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    bad_path = tmp_path / "bad,name.cube"
    cube(bad_path)
    with pytest.raises(ValueError, match="delimiter"):
        build_plan(str(bad_path))
