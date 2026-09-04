#!/usr/bin/env python3
"""Audit exact subtitle characters against explicit OpenType font files.

The tool reads a ``subtitle_pack.v1`` JSON manifest and the Unicode cmap tables
from project-local TTF/OTF/TTC/OTC files.  It never trusts font fallback chosen
implicitly by a renderer: every fallback must be supplied explicitly and is
bound into the report.  ``verify`` rebuilds the analysis from live bytes so a
changed subtitle pack, font, setting, or derived result fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

SCHEMA = "subtitle_glyph_qa.v1"
VERIFY_SCHEMA = "subtitle_glyph_qa.verify.v1"
FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}
VARIATION_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {key: value for key, value in report.items() if key not in {"generated_at", "report_id"}}
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_project_path(raw_path: str, *, root: Path, label: str) -> Path:
    lexical = Path(raw_path).expanduser()
    if not lexical.is_absolute():
        lexical = root / lexical
    lexical = Path(os.path.abspath(str(lexical)))
    if not _within(lexical, root):
        raise ValueError(f"{label} must stay inside the project directory: {lexical}")
    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return lexical


def _project_file(raw_path: str, *, root: Path, label: str) -> Path:
    path = _lexical_project_path(raw_path, root=root, label=label).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


def _project_output(raw_path: str, *, root: Path, label: str) -> Path:
    return _lexical_project_path(raw_path, root=root, label=label).resolve()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _ensure_distinct(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (left_label, left) in enumerate(items):
        for right_label, right in items[index + 1 :]:
            if _same_file(left, right):
                raise ValueError(f"{right_label} must not overwrite or hardlink {left_label}: {right}")


def _write_new(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists():
        raise ValueError(f"temporary output already exists: {temporary}")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("font table is truncated")
    return struct.unpack_from(">H", data, offset)[0]


def _i16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("font table is truncated")
    return struct.unpack_from(">h", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("font table is truncated")
    return struct.unpack_from(">I", data, offset)[0]


def _font_faces(data: bytes) -> Tuple[str, List[int]]:
    signature = data[:4]
    if signature == b"ttcf":
        count = _u32(data, 8)
        if count < 1 or count > 256:
            raise ValueError("invalid OpenType collection face count")
        offsets = [_u32(data, 12 + index * 4) for index in range(count)]
        return "collection", offsets
    if signature in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"}:
        return "sfnt", [0]
    raise ValueError("unsupported font container; expected TTF, OTF, TTC, or OTC")


def _table(data: bytes, face_offset: int, tag: bytes) -> Tuple[int, int]:
    num_tables = _u16(data, face_offset + 4)
    if num_tables < 1 or num_tables > 4096:
        raise ValueError("invalid OpenType table directory")
    directory = face_offset + 12
    for index in range(num_tables):
        record = directory + index * 16
        if record + 16 > len(data):
            raise ValueError("font table directory is truncated")
        if data[record : record + 4] == tag:
            offset = _u32(data, record + 8)
            length = _u32(data, record + 12)
            if offset + length > len(data):
                raise ValueError(f"font {tag.decode('ascii', errors='replace')} table is truncated")
            return offset, length
    raise ValueError(f"font has no {tag.decode('ascii', errors='replace')} table")


def _format_0(data: bytes, offset: int, limit: int) -> Set[int]:
    length = _u16(data, offset + 2)
    end = min(limit, offset + length)
    if offset + 262 > end:
        raise ValueError("format 0 cmap is truncated")
    return {codepoint for codepoint in range(256) if data[offset + 6 + codepoint] != 0}


def _format_4(data: bytes, offset: int, limit: int) -> Set[int]:
    length = _u16(data, offset + 2)
    end = min(limit, offset + length)
    seg_count = _u16(data, offset + 6) // 2
    if seg_count < 1:
        raise ValueError("format 4 cmap has no segments")
    end_codes = offset + 14
    start_codes = end_codes + seg_count * 2 + 2
    deltas = start_codes + seg_count * 2
    range_offsets = deltas + seg_count * 2
    if range_offsets + seg_count * 2 > end:
        raise ValueError("format 4 cmap is truncated")
    covered: Set[int] = set()
    for index in range(seg_count):
        start = _u16(data, start_codes + index * 2)
        finish = _u16(data, end_codes + index * 2)
        delta = _i16(data, deltas + index * 2)
        range_offset_position = range_offsets + index * 2
        range_offset = _u16(data, range_offset_position)
        if start > finish:
            raise ValueError("format 4 cmap has a reversed segment")
        for codepoint in range(start, finish + 1):
            if codepoint == 0xFFFF:
                continue
            if range_offset == 0:
                glyph = (codepoint + delta) & 0xFFFF
            else:
                glyph_position = range_offset_position + range_offset + (codepoint - start) * 2
                if glyph_position + 2 > end:
                    raise ValueError("format 4 cmap glyph array is truncated")
                glyph = _u16(data, glyph_position)
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                covered.add(codepoint)
    return covered


def _format_6(data: bytes, offset: int, limit: int) -> Set[int]:
    length = _u16(data, offset + 2)
    end = min(limit, offset + length)
    first = _u16(data, offset + 6)
    count = _u16(data, offset + 8)
    if offset + 10 + count * 2 > end:
        raise ValueError("format 6 cmap is truncated")
    return {
        first + index
        for index in range(count)
        if _u16(data, offset + 10 + index * 2) != 0
    }


def _format_12(data: bytes, offset: int, limit: int) -> Set[int]:
    length = _u32(data, offset + 4)
    end = min(limit, offset + length)
    groups = _u32(data, offset + 12)
    if groups > 200000 or offset + 16 + groups * 12 > end:
        raise ValueError("format 12/13 cmap is truncated or unreasonable")
    covered: Set[int] = set()
    for index in range(groups):
        position = offset + 16 + index * 12
        start = _u32(data, position)
        finish = _u32(data, position + 4)
        glyph = _u32(data, position + 8)
        if start > finish or finish > 0x10FFFF:
            raise ValueError("format 12/13 cmap has an invalid range")
        first = start if glyph else start + 1
        if first <= finish:
            covered.update(range(first, finish + 1))
    return covered


def _cmap_codepoints(data: bytes, cmap_offset: int, cmap_length: int) -> Tuple[Set[int], List[int]]:
    limit = cmap_offset + cmap_length
    num_tables = _u16(data, cmap_offset + 2)
    if cmap_offset + 4 + num_tables * 8 > limit:
        raise ValueError("cmap encoding records are truncated")
    covered: Set[int] = set()
    formats: Set[int] = set()
    seen_offsets: Set[int] = set()
    for index in range(num_tables):
        record = cmap_offset + 4 + index * 8
        platform_id = _u16(data, record)
        encoding_id = _u16(data, record + 2)
        if platform_id != 0 and not (platform_id == 3 and encoding_id in {1, 10}):
            continue
        subtable = cmap_offset + _u32(data, record + 4)
        if subtable in seen_offsets or subtable + 2 > limit:
            continue
        seen_offsets.add(subtable)
        fmt = _u16(data, subtable)
        if fmt == 0:
            points = _format_0(data, subtable, limit)
        elif fmt == 4:
            points = _format_4(data, subtable, limit)
        elif fmt == 6:
            points = _format_6(data, subtable, limit)
        elif fmt == 12:
            points = _format_12(data, subtable, limit)
        elif fmt == 13:
            # Format 13 is a many-to-one mapping used by last-resort/tofu
            # fonts.  Counting it as real coverage would defeat this gate.
            formats.add(fmt)
            continue
        else:
            continue
        formats.add(fmt)
        covered.update(points)
    if not formats:
        raise ValueError("font has no supported Unicode cmap format (0, 4, 6, 12, or 13)")
    return covered, sorted(formats)


def read_font_codepoints(path: Path) -> Tuple[Set[int], Dict[str, Any]]:
    data = path.read_bytes()
    container, faces = _font_faces(data)
    covered: Set[int] = set()
    formats: Set[int] = set()
    for face in faces:
        cmap_offset, cmap_length = _table(data, face, b"cmap")
        points, face_formats = _cmap_codepoints(data, cmap_offset, cmap_length)
        covered.update(points)
        formats.update(face_formats)
    return covered, {
        "container": container,
        "face_count": len(faces),
        "cmap_formats": sorted(formats),
    }


def _ignored_codepoint(codepoint: int) -> bool:
    character = chr(codepoint)
    if character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
        return True
    return any(start <= codepoint <= finish for start, finish in VARIATION_RANGES)


def collect_required_codepoints(cues: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    locations: Dict[int, Set[str]] = {}
    ignored: Set[int] = set()
    seen_indices: Set[str] = set()
    for position, cue in enumerate(cues, start=1):
        if not isinstance(cue, Mapping):
            raise ValueError(f"subtitle cue {position} must be an object")
        cue_index = str(cue.get("index", position))
        if cue_index in seen_indices:
            raise ValueError(f"subtitle cue index is duplicated: {cue_index}")
        seen_indices.add(cue_index)
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"subtitle cue {cue_index} must contain non-empty text")
        for character in text:
            codepoint = ord(character)
            if _ignored_codepoint(codepoint):
                ignored.add(codepoint)
                continue
            locations.setdefault(codepoint, set()).add(cue_index)
    required = [
        {
            "codepoint": f"U+{codepoint:04X}",
            "value": codepoint,
            "character": chr(codepoint),
            "name": unicodedata.name(chr(codepoint), "UNNAMED"),
            "cue_indices": sorted(locations[codepoint], key=lambda item: (len(item), item)),
        }
        for codepoint in sorted(locations)
    ]
    return required, [f"U+{codepoint:04X}" for codepoint in sorted(ignored)]


def _load_subtitle_pack(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read subtitle pack: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("version") != "subtitle_pack.v1":
        raise ValueError("subtitle pack must use version subtitle_pack.v1")
    cues = payload.get("cues")
    if not isinstance(cues, list) or not cues:
        raise ValueError("subtitle pack must contain at least one cue")
    return payload


def _font_record(path: Path, *, root: Path, role: str, order: int) -> Tuple[Dict[str, Any], Set[int]]:
    if path.suffix.lower() not in FONT_SUFFIXES:
        raise ValueError(f"font must be TTF/OTF/TTC/OTC: {path}")
    covered, metadata = read_font_codepoints(path)
    return {
        "role": role,
        "order": order,
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **metadata,
    }, covered


def _check(name: str, status: str, message: str) -> Dict[str, str]:
    return {"name": name, "status": status, "message": message}


def build_report(
    *,
    project_dir: str,
    subtitle_pack: str,
    font: str,
    fallback_fonts: Sequence[str] = (),
    require_primary: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    subtitle_path = _project_file(subtitle_pack, root=root, label="subtitle pack")
    font_paths = [_project_file(font, root=root, label="primary font")]
    font_paths.extend(
        _project_file(value, root=root, label=f"fallback font {index}")
        for index, value in enumerate(fallback_fonts, start=1)
    )
    if len({path.resolve() for path in font_paths}) != len(font_paths):
        raise ValueError("primary and fallback font paths must be unique")
    _ensure_distinct({"subtitle pack": subtitle_path, **{f"font {i}": p for i, p in enumerate(font_paths)}})

    subtitle_data = _load_subtitle_pack(subtitle_path)
    required, ignored = collect_required_codepoints(subtitle_data["cues"])
    if not required:
        raise ValueError("subtitle pack contains no visible characters to audit")

    font_records: List[Dict[str, Any]] = []
    coverages: List[Set[int]] = []
    for index, path in enumerate(font_paths):
        record, coverage = _font_record(
            path,
            root=root,
            role="primary" if index == 0 else "fallback",
            order=index,
        )
        record["unicode_codepoints"] = len(coverage)
        font_records.append(record)
        coverages.append(coverage)

    assignments: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    primary_missing: List[Dict[str, Any]] = []
    fallback_used = 0
    for item in required:
        codepoint = int(item["value"])
        selected = next((index for index, coverage in enumerate(coverages) if codepoint in coverage), None)
        base = {key: item[key] for key in ("codepoint", "value", "character", "name", "cue_indices")}
        if selected is None:
            missing.append(base)
            primary_missing.append(base)
            continue
        assignments.append({
            **base,
            "font_order": selected,
            "font_path": font_records[selected]["path"],
        })
        if selected > 0:
            fallback_used += 1
            primary_missing.append(base)

    blockers: List[str] = []
    warnings: List[str] = []
    if missing:
        blockers.append(f"{len(missing)} subtitle character(s) are missing from every explicit font")
    if require_primary and fallback_used:
        blockers.append(f"{fallback_used} subtitle character(s) require fallback while --require-primary is set")
    elif fallback_used:
        warnings.append(f"{fallback_used} subtitle character(s) use an explicit fallback font")
    if any(record["face_count"] > 1 for record in font_records):
        warnings.append("font collection coverage is the union of its faces; verify the final renderer face visually")
    if any(13 in record["cmap_formats"] for record in font_records):
        warnings.append("format 13 many-to-one cmap mappings are detected but not counted as real glyph coverage")

    checks = [
        _check(
            "complete_coverage",
            "block" if missing else "pass",
            f"{len(required) - len(missing)}/{len(required)} visible subtitle character(s) covered",
        ),
        _check(
            "primary_font_coverage",
            "block" if require_primary and fallback_used else ("warn" if fallback_used else "pass"),
            f"{len(required) - len(primary_missing)}/{len(required)} character(s) covered by the primary font",
        ),
    ]
    status = "blocked" if blockers else ("warn" if warnings else "ready")
    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "subtitle_pack": {
            "path": _relative(subtitle_path, root),
            "sha256": _sha256(subtitle_path),
            "size_bytes": subtitle_path.stat().st_size,
            "cue_count": len(subtitle_data["cues"]),
        },
        "fonts": font_records,
        "settings": {"require_primary": bool(require_primary)},
        "inventory": {
            "required": required,
            "ignored_codepoints": ignored,
        },
        "coverage": {
            "assignments": assignments,
            "primary_missing": primary_missing,
            "missing": missing,
        },
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "status": status,
            "cues": len(subtitle_data["cues"]),
            "characters": len(required),
            "primary_covered": len(required) - len(primary_missing),
            "fallback_covered": fallback_used,
            "missing": len(missing),
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
    }
    report["report_id"] = canonical_report_id(report)
    return report


def verify_report(report: Mapping[str, Any], project_dir: Optional[str] = None) -> Dict[str, Any]:
    checks: List[Dict[str, str]] = []
    if not isinstance(report, Mapping) or report.get("schema") != SCHEMA:
        return {
            "schema": VERIFY_SCHEMA,
            "checks": [_check("schema", "block", f"report must use schema {SCHEMA}")],
            "summary": {"status": "blocked", "blocking": 1, "warnings": 0},
        }
    stored_id = str(report.get("report_id") or "")
    if stored_id != canonical_report_id(report):
        checks.append(_check("stored_integrity", "block", "stored report id does not match its contents"))
    else:
        checks.append(_check("stored_integrity", "pass", "stored report id matches its contents"))

    root = Path(project_dir or ".").expanduser().resolve()
    subtitle = report.get("subtitle_pack") if isinstance(report.get("subtitle_pack"), Mapping) else {}
    fonts = report.get("fonts") if isinstance(report.get("fonts"), list) else []
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    if not subtitle.get("path") or not fonts or not all(isinstance(item, Mapping) and item.get("path") for item in fonts):
        checks.append(_check("live_rebuild", "block", "report is missing bound subtitle/font paths"))
        current = None
    else:
        try:
            current = build_report(
                project_dir=str(root),
                subtitle_pack=str(subtitle["path"]),
                font=str(fonts[0]["path"]),
                fallback_fonts=[str(item["path"]) for item in fonts[1:]],
                require_primary=bool(settings.get("require_primary")),
            )
        except Exception as exc:
            current = None
            checks.append(_check("live_rebuild", "block", f"live report rebuild failed: {exc}"))
    if current is not None:
        if current["report_id"] != stored_id:
            checks.append(_check("live_match", "block", "subtitle, font, settings, or derived coverage changed"))
        else:
            checks.append(_check("live_match", "pass", "live subtitle/font coverage matches the stored report"))

    verification_blocking = sum(1 for item in checks if item["status"] == "block")
    current_blocking = int((current or report).get("summary", {}).get("blocking") or 0)
    current_warnings = int((current or report).get("summary", {}).get("warnings") or 0)
    blocking = verification_blocking + current_blocking
    return {
        "schema": VERIFY_SCHEMA,
        "report_id": stored_id,
        "checks": checks,
        "summary": {
            "status": "blocked" if blocking else ("warn" if current_warnings else "ready"),
            "blocking": blocking,
            "warnings": current_warnings,
            "missing": int((current or report).get("summary", {}).get("missing") or 0),
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Subtitle Glyph QA",
        "",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Visible characters: `{summary.get('characters', 0)}`",
        f"- Primary covered: `{summary.get('primary_covered', 0)}`",
        f"- Explicit fallback covered: `{summary.get('fallback_covered', 0)}`",
        f"- Missing: `{summary.get('missing', 0)}`",
        f"- Report id: `{report.get('report_id', '')}`",
        "",
        "## Fonts",
        "",
        "| order | role | file | faces | cmap formats |",
        "|---:|---|---|---:|---|",
    ]
    for font in report.get("fonts") or []:
        lines.append(
            f"| {font.get('order')} | {font.get('role')} | `{font.get('path')}` | "
            f"{font.get('face_count')} | `{','.join(str(v) for v in font.get('cmap_formats') or [])}` |"
        )
    missing = (report.get("coverage") or {}).get("missing") or []
    primary_missing = (report.get("coverage") or {}).get("primary_missing") or []
    lines.extend(["", "## Coverage exceptions", ""])
    if not primary_missing:
        lines.append("All visible subtitle characters are covered by the primary font.")
    else:
        lines.extend([
            "| character | codepoint | Unicode name | cue(s) | result |",
            "|---|---|---|---|---|",
        ])
        missing_values = {item["value"] for item in missing}
        assignments = {
            item["value"]: item for item in (report.get("coverage") or {}).get("assignments") or []
        }
        for item in primary_missing:
            assignment = assignments.get(item["value"])
            result = "MISSING" if item["value"] in missing_values else f"fallback: `{assignment['font_path']}`"
            character = str(item["character"]).replace("|", "\\|")
            lines.append(
                f"| {character} | `{item['codepoint']}` | {item['name']} | "
                f"{', '.join(item['cue_indices'])} | {result} |"
            )
    lines.extend([
        "",
        "## Review boundary",
        "",
        "- This checks Unicode cmap coverage in the exact bound font files; it does not prove shaping, kerning, color emoji, ASS layout, or visual legibility.",
        "- Explicit fallback files are auditable, but the renderer must still be configured to use them; implicit system fallback is never counted.",
        "- Watch the complete final captions at 1× and inspect representative full-resolution frames before publishing.",
        "",
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit exact subtitle Unicode coverage in explicit font files.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Build a source-bound subtitle glyph coverage report")
    analyze.add_argument("--subtitle-pack", required=True, help="subtitle_pack.v1 JSON")
    analyze.add_argument("--font", required=True, help="Primary project-local TTF/OTF/TTC/OTC used by the final renderer")
    analyze.add_argument("--fallback-font", action="append", default=[], help="Explicit fallback font; repeat as needed")
    analyze.add_argument("--require-primary", action="store_true", help="Block if any visible character needs fallback")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Rebuild and compare a stored report against live inputs")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.project_dir).expanduser().resolve()
        if args.command == "analyze":
            subtitle_path = _project_file(args.subtitle_pack, root=root, label="subtitle pack")
            output = _project_output(args.output, root=root, label="report output")
            markdown = _project_output(args.markdown, root=root, label="Markdown output") if args.markdown else None
            font_path = _project_file(args.font, root=root, label="primary font")
            fallback_paths = [
                _project_file(value, root=root, label=f"fallback font {index}")
                for index, value in enumerate(args.fallback_font, start=1)
            ]
            path_map = {
                "subtitle pack": subtitle_path,
                "primary font": font_path,
                **{f"fallback font {index}": path for index, path in enumerate(fallback_paths, start=1)},
                "report output": output,
            }
            if markdown is not None:
                path_map["Markdown output"] = markdown
            _ensure_distinct(path_map)
            report = build_report(
                project_dir=str(root),
                subtitle_pack=str(subtitle_path),
                font=str(font_path),
                fallback_fonts=[str(path) for path in fallback_paths],
                require_primary=args.require_primary,
            )
            _write_new(output, json.dumps(report, ensure_ascii=False, indent=2) + "\n", force=args.force)
            if markdown is not None:
                _write_new(markdown, render_markdown(report), force=args.force)
            summary = report["summary"]
            print(
                f"subtitle glyph QA: {summary['status']} characters={summary['characters']} "
                f"fallback={summary['fallback_covered']} missing={summary['missing']} "
                f"blocking={summary['blocking']} warnings={summary['warnings']}"
            )
            return 2 if args.strict and summary["blocking"] else 0

        report_path = _project_file(args.report, root=root, label="glyph QA report")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        verification = verify_report(payload, str(root))
        summary = verification["summary"]
        print(
            f"subtitle glyph QA verify: {summary['status']} "
            f"blocking={summary['blocking']} warnings={summary['warnings']}"
        )
        return 2 if args.strict and summary["blocking"] else 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"subtitle_glyph_qa failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
