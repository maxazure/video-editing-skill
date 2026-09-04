import json
import os
import struct
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import subtitle_glyph_qa as glyph_qa  # noqa: E402


def _write_font(path, characters):
    points = sorted({ord(character) for character in characters})
    groups = b"".join(struct.pack(">III", point, point, index + 1) for index, point in enumerate(points))
    subtable = struct.pack(">HHIII", 12, 0, 16 + len(groups), 0, len(points)) + groups
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + subtable
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0)
    record = b"cmap" + struct.pack(">III", 0, 28, len(cmap))
    path.write_bytes(header + record + cmap)


def _write_format_13_font(path):
    group = struct.pack(">III", 0, 0x10FFFF, 1)
    subtable = struct.pack(">HHIII", 13, 0, 16 + len(group), 0, 1) + group
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + subtable
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0)
    record = b"cmap" + struct.pack(">III", 0, 28, len(cmap))
    path.write_bytes(header + record + cmap)


def _write_pack(path, texts=("你好 AI",)):
    path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [
                    {"index": index, "start": index - 1, "end": index, "text": text}
                    for index, text in enumerate(texts, start=1)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_reads_format_12_unicode_cmap(tmp_path):
    font = tmp_path / "font.ttf"
    _write_font(font, "A你😀")

    covered, metadata = glyph_qa.read_font_codepoints(font)

    assert {ord("A"), ord("你"), ord("😀")} <= covered
    assert metadata == {"container": "sfnt", "face_count": 1, "cmap_formats": [12]}


def test_format_13_last_resort_mapping_does_not_fake_coverage(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "last-resort.ttf"
    _write_pack(pack, ("字幕",))
    _write_format_13_font(font)

    report = glyph_qa.build_report(
        project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
    )

    assert report["fonts"][0]["cmap_formats"] == [13]
    assert report["summary"]["missing"] == 2
    assert any("format 13" in warning for warning in report["warnings"])


def test_primary_font_covers_all_visible_subtitle_characters(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    _write_pack(pack, ("你好 AI", "2026！"))
    _write_font(font, "你好AI2026！")

    report = glyph_qa.build_report(
        project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
    )

    assert report["summary"]["status"] == "ready"
    assert report["summary"]["missing"] == 0
    assert report["summary"]["fallback_covered"] == 0
    assert report["report_id"] == glyph_qa.canonical_report_id(report)


def test_explicit_fallback_is_bound_and_warned(tmp_path):
    pack = tmp_path / "subtitles.json"
    primary = tmp_path / "primary.ttf"
    fallback = tmp_path / "emoji.ttf"
    _write_pack(pack, ("你好😀",))
    _write_font(primary, "你好")
    _write_font(fallback, "😀")

    report = glyph_qa.build_report(
        project_dir=str(tmp_path),
        subtitle_pack=str(pack),
        font=str(primary),
        fallback_fonts=[str(fallback)],
    )

    assert report["summary"]["status"] == "warn"
    assert report["summary"]["fallback_covered"] == 1
    emoji = next(item for item in report["coverage"]["assignments"] if item["character"] == "😀")
    assert emoji["font_path"] == "emoji.ttf"


def test_require_primary_blocks_explicit_fallback(tmp_path):
    pack = tmp_path / "subtitles.json"
    primary = tmp_path / "primary.ttf"
    fallback = tmp_path / "emoji.ttf"
    _write_pack(pack, ("A😀",))
    _write_font(primary, "A")
    _write_font(fallback, "😀")

    report = glyph_qa.build_report(
        project_dir=str(tmp_path),
        subtitle_pack=str(pack),
        font=str(primary),
        fallback_fonts=[str(fallback)],
        require_primary=True,
    )

    assert report["summary"]["status"] == "blocked"
    assert report["summary"]["blocking"] == 1
    assert "--require-primary" in report["blockers"][0]


def test_missing_character_blocks_and_names_cues(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    _write_pack(pack, ("AB", "BC"))
    _write_font(font, "AC")

    report = glyph_qa.build_report(
        project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
    )

    assert report["summary"]["missing"] == 1
    assert report["coverage"]["missing"][0]["character"] == "B"
    assert report["coverage"]["missing"][0]["cue_indices"] == ["1", "2"]


def test_whitespace_controls_and_variation_selectors_are_not_required():
    required, ignored = glyph_qa.collect_required_codepoints(
        [{"index": 1, "text": "A B\nC\ufe0f\u200d"}]
    )

    assert [item["character"] for item in required] == ["A", "B", "C"]
    assert "U+FE0F" in ignored
    assert "U+200D" in ignored


def test_duplicate_cue_index_is_rejected(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    pack.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [
                    {"index": 1, "text": "A"},
                    {"index": 1, "text": "B"},
                ],
            }
        )
    )
    _write_font(font, "AB")

    with pytest.raises(ValueError, match="duplicated"):
        glyph_qa.build_report(
            project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
        )


def test_verify_fails_when_subtitle_bytes_change(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    _write_pack(pack, ("AB",))
    _write_font(font, "AB")
    report = glyph_qa.build_report(
        project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
    )
    _write_pack(pack, ("AC",))

    verification = glyph_qa.verify_report(report, str(tmp_path))

    assert verification["summary"]["status"] == "blocked"
    assert any(item["name"] == "live_match" and item["status"] == "block" for item in verification["checks"])


def test_verify_fails_when_font_bytes_change(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    _write_pack(pack, ("AB",))
    _write_font(font, "AB")
    report = glyph_qa.build_report(
        project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(font)
    )
    _write_font(font, "A")

    verification = glyph_qa.verify_report(report, str(tmp_path))

    assert verification["summary"]["blocking"] >= 2
    assert verification["summary"]["missing"] == 1


def test_cli_analyze_and_verify_round_trip(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    output = tmp_path / "glyph-qa.json"
    markdown = tmp_path / "glyph-qa.md"
    _write_pack(pack, ("字幕 OK",))
    _write_font(font, "字幕OK")
    script = os.path.join(REPO, "scripts", "subtitle_glyph_qa.py")

    analyze = subprocess.run(
        [
            sys.executable,
            script,
            "analyze",
            "--project-dir",
            str(tmp_path),
            "--subtitle-pack",
            str(pack),
            "--font",
            str(font),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    verify = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--project-dir",
            str(tmp_path),
            "--report",
            str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert analyze.returncode == 0, analyze.stderr
    assert verify.returncode == 0, verify.stderr
    assert "Status: **ready**" in markdown.read_text(encoding="utf-8")


def test_cli_strict_returns_two_for_missing_character(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    output = tmp_path / "glyph-qa.json"
    _write_pack(pack, ("AB",))
    _write_font(font, "A")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "subtitle_glyph_qa.py"),
            "analyze",
            "--project-dir",
            str(tmp_path),
            "--subtitle-pack",
            str(pack),
            "--font",
            str(font),
            "--output",
            str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stderr
    assert "missing=1" in result.stdout


def test_cli_force_cannot_overwrite_a_bound_font(tmp_path):
    pack = tmp_path / "subtitles.json"
    font = tmp_path / "font.ttf"
    _write_pack(pack, ("A",))
    _write_font(font, "A")
    original = font.read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "subtitle_glyph_qa.py"),
            "analyze",
            "--project-dir",
            str(tmp_path),
            "--subtitle-pack",
            str(pack),
            "--font",
            str(font),
            "--output",
            str(font),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert font.read_bytes() == original


def test_project_escape_and_symlink_font_are_rejected(tmp_path):
    pack = tmp_path / "subtitles.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside.ttf"
    link = tmp_path / "font.ttf"
    _write_pack(pack, ("A",))
    _write_font(outside, "A")
    link.symlink_to(outside)
    try:
        with pytest.raises(ValueError, match="symlink"):
            glyph_qa.build_report(
                project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(link)
            )
        with pytest.raises(ValueError, match="inside the project"):
            glyph_qa.build_report(
                project_dir=str(tmp_path), subtitle_pack=str(pack), font=str(outside)
            )
    finally:
        outside.unlink(missing_ok=True)
