import json
import os
import struct
import subprocess
import sys
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from reference_frame_preflight import build_preflight, emit_markdown, parse_aspect  # noqa: E402


def _image(path, size=(1080, 1920), mode="RGB", color=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if color is None:
        color = (20, 30, 40, 255) if mode == "RGBA" else (20, 30, 40)
    width, height = size
    color_type = 6 if mode == "RGBA" else 2
    pixel = bytes(color)
    raw = b"".join(b"\x00" + pixel * width for _ in range(height))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _pack(reference, *, style_reference=None, mode="image_to_video", aspect="9:16"):
    return {
        "version": "video_prompt_pack.v1",
        "global": {
            "aspect": aspect,
            "style_reference": (
                {
                    "expected_path": str(style_reference),
                    "resolved_path": str(style_reference),
                }
                if style_reference
                else {"expected_path": "", "resolved_path": ""}
            ),
        },
        "items": [
            {
                "shot_id": "shot_001",
                "provider": "dreamina_seedance",
                "mode": mode,
                "reference": {
                    "expected_path": str(reference),
                    "resolved_path": str(reference) if reference and reference.exists() else "",
                },
            }
        ],
    }


def test_matching_first_frame_and_style_reference_pass(tmp_path):
    first = _image(tmp_path / "first.png")
    style = _image(tmp_path / "style.png")
    pack_path = tmp_path / "video_prompt_pack.json"
    pack = _pack(first, style_reference=style)

    result = build_preflight(
        pack,
        prompt_pack_path=str(pack_path),
        require_style_reference=True,
    )

    assert result["version"] == "reference_frame_preflight.v1"
    assert result["status"] == "ready"
    assert result["summary"]["blocking"] == 0
    assert result["summary"]["references"] == 2
    assert result["style_lock"]["applies_to"] == ["shot_001"]
    assert all(item["media"]["orientation"] == "portrait" for item in result["references"])


def test_landscape_first_frame_blocks_vertical_generation(tmp_path):
    first = _image(tmp_path / "landscape.png", size=(1920, 1080))
    result = build_preflight(
        _pack(first),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
    )

    assert result["status"] == "blocked"
    assert result["summary"]["blocking"] == 1
    assert result["summary"]["aspect_mismatches"] == 1
    assert "conflicts with 9:16 portrait output" in result["blockers"][0]
    assert any("extend the scene vertically" in step for step in result["next_steps"])


def test_transparent_low_resolution_reference_warns_without_blocking(tmp_path):
    first = _image(
        tmp_path / "transparent.png",
        size=(270, 480),
        mode="RGBA",
        color=(20, 30, 40, 0),
    )
    result = build_preflight(
        _pack(first),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
        min_short_edge=512,
    )

    assert result["status"] == "warn"
    assert result["summary"]["blocking"] == 0
    assert result["summary"]["warnings"] == 2
    assert result["summary"]["transparent_backgrounds"] == 1
    assert any("intentional background" in step for step in result["next_steps"])


def test_opaque_rgba_reference_does_not_report_transparency(tmp_path):
    first = _image(
        tmp_path / "opaque-alpha.png",
        mode="RGBA",
        color=(20, 30, 40, 255),
    )
    result = build_preflight(
        _pack(first),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
    )

    assert result["status"] == "ready"
    assert result["summary"]["transparent_backgrounds"] == 0


def test_missing_required_first_frame_blocks(tmp_path):
    missing = tmp_path / "missing.png"
    result = build_preflight(
        _pack(missing),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
    )

    assert result["summary"]["blocking"] == 1
    assert "does not exist" in result["blockers"][0]


def test_style_reference_can_be_required(tmp_path):
    first = _image(tmp_path / "first.png")
    result = build_preflight(
        _pack(first),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
        require_style_reference=True,
    )

    assert result["style_lock"]["status"] == "blocked"
    assert result["summary"]["blocking"] == 1
    assert "global_style_reference" in result["blockers"][0]


def test_configured_missing_style_reference_blocks_without_require_flag(tmp_path):
    first = _image(tmp_path / "first.png")
    missing_style = tmp_path / "missing-style.png"
    result = build_preflight(
        _pack(first, style_reference=missing_style),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
    )

    assert result["style_lock"]["status"] == "blocked"
    assert result["summary"]["blocking"] == 1
    assert "missing-style.png" in result["blockers"][0]


def test_aspect_parser_and_markdown(tmp_path):
    ratio, orientation = parse_aspect("16:9")
    assert round(ratio, 3) == 1.778
    assert orientation == "landscape"

    first = _image(tmp_path / "first.png")
    result = build_preflight(
        _pack(first),
        prompt_pack_path=str(tmp_path / "video_prompt_pack.json"),
    )
    markdown = emit_markdown(result)
    assert "# Reference Frame Preflight" in markdown
    assert "| shot_001 | first_frame | 1080×1920 | portrait | no | ready |" in markdown


def test_cli_strict_writes_artifacts_and_returns_two_for_blocker(tmp_path):
    missing = tmp_path / "missing.png"
    pack_path = tmp_path / "video_prompt_pack.json"
    pack_path.write_text(json.dumps(_pack(missing)), encoding="utf-8")
    output = tmp_path / "reference_frame_preflight.json"
    markdown = tmp_path / "reference_frame_preflight.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "reference_frame_preflight.py"),
            "--prompt-pack",
            str(pack_path),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["blocking"] == 1
    assert "# Reference Frame Preflight" in markdown.read_text(encoding="utf-8")
