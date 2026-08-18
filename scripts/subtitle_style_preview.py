#!/usr/bin/env python3
"""Render source-bound subtitle style previews before the final encode.

The generated JPEGs use the same ASS builders and caption presets as
``render_final.py``.  A report binds the source video, font, render settings,
and preview bytes so an old selection cannot silently survive later changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from generated_clip_review import probe_media
from render_final import (
    CAPTION_PRESETS,
    build_karaoke_ass,
    build_merged_ass,
    build_reformat_filter,
)
from utils import escape_ffmpeg_path, find_chinese_font, get_video_info


VERSION = "subtitle_style_preview.v1"
DEFAULT_STYLES = ("normal", "minimal", "bold_pop")
ALL_STYLES = tuple(CAPTION_PRESETS) + ("karaoke",)
STYLE_LABELS = {
    "normal": "Recommended / high-contrast",
    "minimal": "Clean / narrative",
    "bold_pop": "Bold / social",
    "neon": "Neon / tech",
    "yellow_pop": "Yellow / high visibility",
    "karaoke": "Karaoke / word highlight",
}
PLATFORMS = {
    "xhs": (1080, 1440),
    "douyin": (1080, 1920),
    "wxch": (1080, 1920),
    "tiktok": (1080, 1920),
    "reels": (1080, 1920),
    "youtube_shorts": (1080, 1920),
    "youtube": (1920, 1080),
}


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


def _ensure_distinct_paths(paths: Mapping[str, Path]) -> None:
    seen: Dict[Path, str] = {}
    for label, path in paths.items():
        resolved = path.resolve()
        previous = seen.get(resolved)
        if previous is not None:
            raise ValueError(f"{label} must not overwrite {previous}: {resolved}")
        seen[resolved] = label


def _fingerprint(path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
    record = {
        "path": _relative(path, root) if root is not None else str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }
    return record


def _media_signature(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "duration": round(float(value.get("duration") or 0), 6),
        "fps": round(float(value.get("fps") or 0), 6),
        "width": int(value.get("width") or 0),
        "height": int(value.get("height") or 0),
        "video_codec": str(value.get("video_codec") or ""),
        "pixel_format": str(value.get("pixel_format") or ""),
        "has_audio": bool(value.get("has_audio")),
        "audio_codec": str(value.get("audio_codec") or ""),
        "sample_rate": int(value.get("sample_rate") or 0),
        "channels": int(value.get("channels") or 0),
    }


def _normalize_styles(styles: Sequence[str]) -> List[str]:
    normalized = [str(style).strip().lower() for style in styles if str(style).strip()]
    if not normalized:
        raise ValueError("at least one subtitle style is required")
    if len(normalized) > 6:
        raise ValueError("at most six subtitle styles can be previewed")
    if len(normalized) != len(set(normalized)):
        raise ValueError("subtitle styles must be unique")
    unknown = [style for style in normalized if style not in ALL_STYLES]
    if unknown:
        raise ValueError(f"unknown subtitle style(s): {', '.join(unknown)}")
    return normalized


def select_sample_times(duration: float, requested: Sequence[float] = ()) -> List[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source duration must be positive")
    raw = list(requested) if requested else [duration * 0.15, duration * 0.50, duration * 0.85]
    if not raw or len(raw) > 6:
        raise ValueError("supply between one and six sample times")
    limit = max(0.0, duration - min(0.04, duration / 2))
    times: List[float] = []
    for value in raw:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("sample times must be finite and non-negative")
        times.append(round(min(value, limit), 3))
    if len(times) != len(set(times)):
        raise ValueError("sample times collapse to duplicate source frames")
    return times


def _karaoke_words(text: str, duration: float = 4.0) -> List[Dict[str, Any]]:
    units = [character for character in text if not character.isspace()] or [text]
    step = duration / len(units)
    return [
        {"word": unit, "start": round(index * step, 6), "end": round((index + 1) * step, 6)}
        for index, unit in enumerate(units)
    ]


def build_preview_ass(
    *,
    style: str,
    text: str,
    font_name: str,
    font_size: int,
    width: int,
    height: int,
) -> str:
    clip: Dict[str, Any] = {"start": 0.0, "end": 4.0, "text": text}
    if style == "karaoke":
        clip["words"] = _karaoke_words(text)
        content, _, _ = build_karaoke_ass(
            [clip], font_name, font_size, width, height,
            highlight_color="#FFFF00", base_color="#FFFFFF", base_alpha="80",
        )
        return content
    content, _, _ = build_merged_ass(
        [clip], font_name, font_size, width, height, subtitle_style=style,
    )
    return content


def _run(command: Sequence[str]) -> None:
    result = subprocess.run(list(command), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "FFmpeg failed").strip()
        raise ValueError(detail.splitlines()[-1])


def _render_variant(
    source: Path,
    destination: Path,
    *,
    ass_content: str,
    font_path: Optional[Path],
    times: Sequence[float],
    source_width: int,
    source_height: int,
    width: int,
    height: int,
) -> Dict[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    thumb_width = min(480, width)
    thumb_height = max(2, round(height * thumb_width / width / 2) * 2)
    with tempfile.TemporaryDirectory(prefix="subtitle-preview-", dir=str(destination.parent)) as temp_name:
        temp = Path(temp_name)
        ass_path = temp / "preview.ass"
        ass_path.write_text(ass_content, encoding="utf-8")
        ass_filter = f"ass='{escape_ffmpeg_path(str(ass_path))}'"
        if font_path is not None:
            ass_filter += f":fontsdir='{escape_ffmpeg_path(str(font_path.parent))}'"
        frame_paths: List[Path] = []
        for index, time_s in enumerate(times):
            frame_path = temp / f"frame-{index:02d}.png"
            filters = [
                build_reformat_filter(source_width, source_height, width, height),
                "setpts=PTS-STARTPTS+1/TB",
                ass_filter,
            ]
            _run([
                "ffmpeg", "-v", "error", "-ss", f"{time_s:.3f}", "-i", str(source),
                "-frames:v", "1", "-vf", ",".join(filters), "-y", str(frame_path),
            ])
            frame_paths.append(frame_path)

        command: List[str] = ["ffmpeg", "-v", "error"]
        for frame_path in frame_paths:
            command.extend(["-i", str(frame_path)])
        scaled = "".join(
            f"[{index}:v]scale={thumb_width}:{thumb_height}[v{index}];"
            for index in range(len(frame_paths))
        )
        if len(frame_paths) == 1:
            stack = "[v0]null[out]"
        else:
            inputs = "".join(f"[v{index}]" for index in range(len(frame_paths)))
            stack = f"{inputs}hstack=inputs={len(frame_paths)}[out]"
        command.extend([
            "-filter_complex", scaled + stack,
            "-map", "[out]", "-frames:v", "1", "-q:v", "2", "-y", str(destination),
        ])
        _run(command)
    return {
        "width": thumb_width * len(times),
        "height": thumb_height,
        "sample_frames": len(times),
    }


def _derive_state(report: Mapping[str, Any]) -> Dict[str, Any]:
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    variants = report.get("variants") if isinstance(report.get("variants"), list) else []
    selected = str(report.get("selected_style") or "")
    rendered_styles = [str(item.get("style") or "") for item in variants if isinstance(item, Mapping)]
    blockers: List[str] = []
    warnings: List[str] = []
    if not variants:
        blockers.append("no subtitle preview variants were rendered")
    if len(rendered_styles) != len(set(rendered_styles)):
        blockers.append("subtitle preview variants contain duplicate styles")
    if selected and selected not in rendered_styles:
        blockers.append(f"selected subtitle style was not rendered: {selected}")
    if bool(settings.get("require_selection")) and not selected:
        blockers.append("subtitle style selection is required before rendering")
    if not selected:
        warnings.append("no subtitle style is selected; review the JPEG variants before final rendering")
    summary = {
        "variants": len(variants),
        "sample_frames": len(settings.get("sample_times") or []),
        "selected": 1 if selected else 0,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else ("ready" if selected else "needs_review")
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def create_report(
    source_path: str,
    *,
    project_dir: str,
    preview_dir: str,
    platform: str = "xhs",
    width: Optional[int] = None,
    height: Optional[int] = None,
    text: str = "字幕样式预览 Subtitle preview",
    styles: Sequence[str] = DEFAULT_STYLES,
    times: Sequence[float] = (),
    font_path: Optional[str] = None,
    font_size_at_1080p: int = 48,
    selected_style: str = "",
    require_selection: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    source = _project_file(source_path, root=root, label="source video")
    destination_dir = _project_output(preview_dir, root=root, label="preview directory")
    if source == destination_dir or destination_dir in source.parents:
        raise ValueError("preview directory must not collide with the source video")
    normalized_styles = _normalize_styles(styles)
    if selected_style and selected_style not in normalized_styles:
        raise ValueError("selected style must be included in --styles")
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("preview text must not be empty")
    if font_size_at_1080p < 12 or font_size_at_1080p > 160:
        raise ValueError("font size must be between 12 and 160 at a 1080px short edge")

    if (width is None) != (height is None):
        raise ValueError("preview width and height must be supplied together")
    if width is None or height is None:
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform: {platform}")
        width, height = PLATFORMS[platform]
    if width < 160 or height < 160 or width % 2 or height % 2:
        raise ValueError("preview width and height must be even integers of at least 160")

    media = probe_media(str(source))
    sample_times = select_sample_times(float(media["duration"]), times)
    _, source_width, source_height, _, _ = get_video_info(str(source))
    resolved_font_path, font_name = find_chinese_font(font_path)
    if not font_name:
        raise ValueError("no usable subtitle font was found")
    font = Path(resolved_font_path).expanduser().resolve() if resolved_font_path else None
    if font is not None and (not font.exists() or not font.is_file()):
        raise ValueError(f"subtitle font does not exist: {font}")
    font_size = max(1, round(font_size_at_1080p * min(width, height) / 1080))

    destination_dir.mkdir(parents=True, exist_ok=True)
    final_paths = [destination_dir / f"subtitle-style-{style}.jpg" for style in normalized_styles]
    if not force:
        existing = [path for path in final_paths if path.exists()]
        if existing:
            raise ValueError(f"refusing to overwrite existing preview without --force: {existing[0]}")

    variants: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="subtitle-style-set-", dir=str(destination_dir.parent)) as temp_name:
        temp = Path(temp_name)
        staged: List[Path] = []
        for style, final_path in zip(normalized_styles, final_paths):
            ass_content = build_preview_ass(
                style=style,
                text=text,
                font_name=font_name,
                font_size=font_size,
                width=width,
                height=height,
            )
            staged_path = temp / final_path.name
            geometry = _render_variant(
                source,
                staged_path,
                ass_content=ass_content,
                font_path=font,
                times=sample_times,
                source_width=source_width,
                source_height=source_height,
                width=width,
                height=height,
            )
            staged.append(staged_path)
            variants.append({
                "id": f"subtitle-{style}",
                "style": style,
                "label": STYLE_LABELS.get(style, style),
                "ass_sha256": hashlib.sha256(ass_content.encode("utf-8")).hexdigest(),
                "preview": {**_fingerprint(staged_path), "path": _relative(final_path, root), **geometry},
                "render_argument": f"--subtitle-style {style}",
            })
        for staged_path, final_path in zip(staged, final_paths):
            os.replace(staged_path, final_path)

    source_record = {
        **_fingerprint(source, root=root),
        "media": _media_signature(media),
        "display_width": int(source_width),
        "display_height": int(source_height),
    }
    font_record = {
        "name": font_name,
        "path": str(font) if font is not None else "",
        "sha256": _sha256(font) if font is not None else "",
        "size_bytes": font.stat().st_size if font is not None else 0,
    }
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": source_record,
        "font": font_record,
        "settings": {
            "platform": platform,
            "width": int(width),
            "height": int(height),
            "font_size_at_1080p": int(font_size_at_1080p),
            "font_size": int(font_size),
            "text": text,
            "styles": normalized_styles,
            "sample_times": sample_times,
            "require_selection": bool(require_selection),
        },
        "variants": variants,
        "selected_style": selected_style,
        "selected_preview": (
            _relative(final_paths[normalized_styles.index(selected_style)], root) if selected_style else ""
        ),
    }
    report.update(_derive_state(report))
    report["report_id"] = canonical_report_id(report)
    return report


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported subtitle preview version: {report.get('version')!r}")
    raw_project_dir = report.get("project_dir")
    if not isinstance(raw_project_dir, str) or not raw_project_dir or not Path(raw_project_dir).is_absolute():
        blockers.append("project_dir must be a non-empty absolute path")
    root = Path(str(raw_project_dir or ".")).expanduser().resolve()
    if project_dir is not None:
        expected_root = Path(project_dir).expanduser().resolve()
        if root != expected_root:
            blockers.append("report project_dir does not match the verification project")
        root = expected_root
    if not root.is_dir():
        blockers.append(f"project directory does not exist: {root}")

    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    source_record = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    font_record = report.get("font") if isinstance(report.get("font"), Mapping) else {}
    variants = report.get("variants") if isinstance(report.get("variants"), list) else []
    try:
        styles = _normalize_styles(settings.get("styles") or [])
    except ValueError as exc:
        styles = []
        blockers.append(str(exc))
    try:
        width = int(settings.get("width") or 0)
        height = int(settings.get("height") or 0)
        font_size = int(settings.get("font_size") or 0)
    except (TypeError, ValueError):
        width = height = font_size = 0
        blockers.append("preview canvas and font size must be integers")
    text = str(settings.get("text") or "")
    sample_times = settings.get("sample_times") if isinstance(settings.get("sample_times"), list) else []

    source: Optional[Path] = None
    try:
        source = _project_file(str(source_record.get("path") or ""), root=root, label="source video")
        current_source = _fingerprint(source, root=root)
        if current_source != {key: source_record.get(key) for key in ("path", "sha256", "size_bytes")}:
            blockers.append("source video bytes changed after subtitle previews were rendered")
        if _media_signature(probe_media(str(source))) != source_record.get("media"):
            blockers.append("source video media contract changed after subtitle previews were rendered")
    except (OSError, ValueError, TypeError) as exc:
        blockers.append(f"source video verification failed: {exc}")

    font_path = str(font_record.get("path") or "")
    font: Optional[Path] = None
    if font_path:
        font = Path(font_path).expanduser().resolve()
        if not font.exists() or not font.is_file():
            blockers.append(f"subtitle font is missing: {font}")
        else:
            if _sha256(font) != font_record.get("sha256") or font.stat().st_size != font_record.get("size_bytes"):
                blockers.append("subtitle font bytes changed after previews were rendered")
    else:
        warnings.append("subtitle preview did not bind a concrete font file")

    rendered_styles: List[str] = []
    for index, raw_variant in enumerate(variants):
        if not isinstance(raw_variant, Mapping):
            blockers.append(f"variant #{index + 1} must be an object")
            continue
        style = str(raw_variant.get("style") or "")
        rendered_styles.append(style)
        preview = raw_variant.get("preview") if isinstance(raw_variant.get("preview"), Mapping) else {}
        try:
            preview_path = _project_file(str(preview.get("path") or ""), root=root, label=f"{style} preview")
            current_preview = _fingerprint(preview_path, root=root)
            if current_preview != {key: preview.get(key) for key in ("path", "sha256", "size_bytes")}:
                blockers.append(f"{style} subtitle preview bytes changed after rendering")
        except (OSError, ValueError, TypeError) as exc:
            blockers.append(f"{style or index + 1} preview verification failed: {exc}")
        if style in ALL_STYLES and text and width > 0 and height > 0 and font_size > 0:
            ass_content = build_preview_ass(
                style=style,
                text=text,
                font_name=str(font_record.get("name") or ""),
                font_size=font_size,
                width=width,
                height=height,
            )
            expected_ass = hashlib.sha256(ass_content.encode("utf-8")).hexdigest()
            if raw_variant.get("ass_sha256") != expected_ass:
                blockers.append(f"{style} ASS style contract changed after preview rendering")

    if rendered_styles != styles:
        blockers.append("variant order/styles do not match settings.styles")
    if source is not None:
        try:
            source_media = source_record.get("media")
            if not isinstance(source_media, Mapping):
                raise ValueError("source media contract must be an object")
            expected_times = select_sample_times(float(source_media.get("duration") or 0), sample_times)
            if expected_times != sample_times:
                blockers.append("sample times are not canonical for the bound source duration")
        except (TypeError, ValueError) as exc:
            blockers.append(f"sample time verification failed: {exc}")

    derived = _derive_state(report)
    for key in ("blockers", "warnings", "summary", "status"):
        if report.get(key) != derived[key]:
            blockers.append(f"stored {key} does not match derived subtitle preview state")
    expected_selected_preview = ""
    selected = str(report.get("selected_style") or "")
    if selected in rendered_styles:
        selected_variant = variants[rendered_styles.index(selected)]
        if isinstance(selected_variant, Mapping):
            preview = selected_variant.get("preview")
            if isinstance(preview, Mapping):
                expected_selected_preview = str(preview.get("path") or "")
    if report.get("selected_preview") != expected_selected_preview:
        blockers.append("selected_preview does not match selected_style")
    if report.get("report_id") != canonical_report_id(report):
        blockers.append("subtitle preview report_id does not match canonical report content")

    combined_blockers = list(dict.fromkeys(blockers + list(derived["blockers"])))
    combined_warnings = list(dict.fromkeys(warnings + list(derived["warnings"])))
    return {
        "status": "blocked" if combined_blockers else derived["status"],
        "blockers": combined_blockers,
        "warnings": combined_warnings,
        "summary": {"blocking": len(combined_blockers), "warnings": len(combined_warnings)},
    }


def select_style(report: Mapping[str, Any], style: str) -> Dict[str, Any]:
    verification = verify_report(report)
    integrity_blockers = [
        blocker for blocker in verification["blockers"]
        if blocker != "subtitle style selection is required before rendering"
    ]
    if integrity_blockers:
        raise ValueError("cannot select from an invalid subtitle preview report: " + integrity_blockers[0])
    variants = report.get("variants") if isinstance(report.get("variants"), list) else []
    styles = [str(item.get("style") or "") for item in variants if isinstance(item, Mapping)]
    if style not in styles:
        raise ValueError(f"subtitle style was not rendered: {style}")
    updated = dict(report)
    updated["generated_at"] = utc_now()
    updated["selected_style"] = style
    preview = variants[styles.index(style)].get("preview")
    updated["selected_preview"] = str(preview.get("path") or "") if isinstance(preview, Mapping) else ""
    updated.update(_derive_state(updated))
    updated["report_id"] = canonical_report_id(updated)
    return updated


def emit_markdown(report: Mapping[str, Any]) -> str:
    settings = report.get("settings") or {}
    lines = [
        "# Subtitle Style Preview",
        "",
        f"- Status: **{report.get('status', 'unknown')}**",
        f"- Source: `{(report.get('source') or {}).get('path', '')}`",
        f"- Canvas: {settings.get('width')}×{settings.get('height')} ({settings.get('platform')})",
        f"- Font: {(report.get('font') or {}).get('name', '')} / {settings.get('font_size')} px",
        f"- Sample times: {', '.join(f'{float(value):.3f}s' for value in settings.get('sample_times') or [])}",
        f"- Selected style: `{report.get('selected_style') or 'not selected'}`",
        "",
        "## Variants",
        "",
        "| Style | Review JPEG | Final render argument |",
        "|---|---|---|",
    ]
    for variant in report.get("variants") or []:
        preview = variant.get("preview") or {}
        lines.append(
            f"| `{variant.get('style')}` — {variant.get('label')} | `{preview.get('path')}` | "
            f"`{variant.get('render_argument')}` |"
        )
    lines.extend([
        "",
        "Review every JPEG at phone size and full size. Check contrast over early/middle/late frames, "
        "line wrapping, lower-third UI clearance, and whether the style matches the content tone.",
        "",
        "After choosing a style, record it with:",
        "",
        "```bash",
        "python3 scripts/subtitle_style_preview.py select --report work/subtitle_style_preview.json --style <style>",
        "```",
    ])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", "", *[f"- {item}" for item in report["blockers"]]])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in report["warnings"]]])
    return "\n".join(lines).rstrip() + "\n"


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("subtitle preview report must be a JSON object")
    return payload


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _write_json(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", force=force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render and verify source-bound subtitle style previews")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Render subtitle styles on representative source frames")
    create.add_argument("--project-dir", default=".")
    create.add_argument("--video", required=True)
    create.add_argument("--preview-dir", default="verify/subtitle_styles")
    create.add_argument("--platform", choices=sorted(PLATFORMS), default="xhs")
    create.add_argument("--width", type=int)
    create.add_argument("--height", type=int)
    create.add_argument("--text", default="字幕样式预览 Subtitle preview")
    create.add_argument("--styles", nargs="+", choices=ALL_STYLES, default=list(DEFAULT_STYLES))
    create.add_argument("--time", action="append", type=float, default=[])
    create.add_argument("--font-path")
    create.add_argument("--font-size", type=int, default=48, help="Font size at a 1080px short edge")
    create.add_argument("--select", default="", choices=ALL_STYLES)
    create.add_argument("--require-selection", action="store_true")
    create.add_argument("--output", required=True)
    create.add_argument("--markdown")
    create.add_argument("--force", action="store_true")
    create.add_argument("--strict", action="store_true")

    select = subparsers.add_parser("select", help="Record the reviewed style without re-rendering previews")
    select.add_argument("--report", required=True)
    select.add_argument("--style", required=True, choices=ALL_STYLES)
    select.add_argument("--markdown")

    verify = subparsers.add_parser("verify", help="Verify source, font, ASS, preview, and selection bindings")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            root = Path(args.project_dir).expanduser().resolve()
            output = _project_output(args.output, root=root, label="report output")
            markdown = _project_output(args.markdown, root=root, label="Markdown output") if args.markdown else None
            source = _project_file(args.video, root=root, label="source video")
            preview_dir = _project_output(args.preview_dir, root=root, label="preview directory")
            styles = _normalize_styles(args.styles)
            protected_paths = {
                "source video": source,
                **{
                    f"{style} preview": preview_dir / f"subtitle-style-{style}.jpg"
                    for style in styles
                },
                "report output": output,
            }
            if markdown is not None:
                protected_paths["Markdown output"] = markdown
            _ensure_distinct_paths(protected_paths)
            if output.exists() and not args.force:
                raise ValueError(f"refusing to overwrite existing file without --force: {output}")
            if markdown is not None and markdown.exists() and not args.force:
                raise ValueError(f"refusing to overwrite existing file without --force: {markdown}")
            report = create_report(
                args.video,
                project_dir=str(root),
                preview_dir=args.preview_dir,
                platform=args.platform,
                width=args.width,
                height=args.height,
                text=args.text,
                styles=styles,
                times=args.time,
                font_path=args.font_path,
                font_size_at_1080p=args.font_size,
                selected_style=args.select,
                require_selection=args.require_selection,
                force=args.force,
            )
            _write_json(output, report, force=args.force)
            if markdown is not None:
                _write_text(markdown, emit_markdown(report), force=args.force)
            print(json.dumps({"status": report["status"], "report": str(output), "summary": report["summary"]}, ensure_ascii=False))
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path = Path(args.report).expanduser().resolve()
        report = _load_json(str(report_path))
        if args.command == "select":
            raw_root = report.get("project_dir")
            if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
                raise ValueError("report project_dir must be a non-empty absolute path")
            root = Path(raw_root).expanduser().resolve()
            report_path = _project_file(str(report_path), root=root, label="subtitle preview report")
            markdown_path = _project_output(
                args.markdown or str(report_path.with_suffix(".md")),
                root=root,
                label="Markdown output",
            )
            protected_paths = {
                "subtitle preview report": report_path,
                "Markdown output": markdown_path,
            }
            source_record = report.get("source") if isinstance(report.get("source"), Mapping) else {}
            protected_paths["source video"] = _project_output(
                str(source_record.get("path") or ""), root=root, label="source video path"
            )
            variants = report.get("variants") if isinstance(report.get("variants"), list) else []
            for index, variant in enumerate(variants):
                preview = variant.get("preview") if isinstance(variant, Mapping) else None
                if isinstance(preview, Mapping):
                    protected_paths[f"preview #{index + 1}"] = _project_output(
                        str(preview.get("path") or ""), root=root, label=f"preview #{index + 1} path"
                    )
            _ensure_distinct_paths(protected_paths)
            updated = select_style(report, args.style)
            _write_json(report_path, updated, force=True)
            _write_text(markdown_path, emit_markdown(updated), force=True)
            print(json.dumps({"status": updated["status"], "selected_style": args.style, "report": str(report_path)}, ensure_ascii=False))
            return 0

        verification = verify_report(report)
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
