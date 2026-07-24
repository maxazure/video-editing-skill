#!/usr/bin/env python3
"""Audit critical vertical-video elements against platform UI safe areas.

The tool is local and deterministic. It reads declared render/enrich layout
data, estimates the positions used by ``render_final.py``, and emits a
reviewable JSON/Markdown/SVG artifact. It does not upload media, OCR frames, or
claim that community-derived platform UI measurements are permanent.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "platform_safe_area_qa.v1"
CJK_RE = re.compile(r"[\u3400-\u9fff]")

# Fractions are intentionally conservative and scale to the selected canvas.
# TikTok/Reels/Shorts values are adapted from the GitHub skills cited in the
# README automation record. XHS and WeChat Channels use the universal preset
# until a project supplies measured custom margins.
PROFILES: Mapping[str, Mapping[str, Any]] = {
    "universal": {
        "label": "Universal vertical social",
        "canvas": (1080, 1920),
        "margins": {"left": 60 / 1080, "top": 210 / 1920, "right": 120 / 1080, "bottom": 440 / 1920},
        "basis": "community-derived conservative 9:16 union",
    },
    "xhs": {
        "label": "Xiaohongshu / RedNote (conservative)",
        "canvas": (1080, 1440),
        "margins": {"left": 60 / 1080, "top": 210 / 1920, "right": 120 / 1080, "bottom": 440 / 1920},
        "basis": "universal vertical preset scaled to the project 3:4 export",
    },
    "douyin": {
        "label": "Douyin (TikTok-derived conservative)",
        "canvas": (1080, 1920),
        "margins": {"left": 60 / 1080, "top": 150 / 1920, "right": 120 / 1080, "bottom": 440 / 1920},
        "basis": "TikTok community safe-area guidance used as a conservative proxy",
    },
    "wxch": {
        "label": "WeChat Channels (conservative)",
        "canvas": (1080, 1920),
        "margins": {"left": 60 / 1080, "top": 210 / 1920, "right": 120 / 1080, "bottom": 440 / 1920},
        "basis": "universal vertical preset; override margins for a measured UI",
    },
    "tiktok": {
        "label": "TikTok",
        "canvas": (1080, 1920),
        "margins": {"left": 60 / 1080, "top": 150 / 1920, "right": 120 / 1080, "bottom": 440 / 1920},
        "basis": "community green-zone measurements",
    },
    "reels": {
        "label": "Instagram Reels",
        "canvas": (1080, 1920),
        "margins": {"left": 44 / 1080, "top": 210 / 1920, "right": 84 / 1080, "bottom": 310 / 1920},
        "basis": "community green-zone measurements",
    },
    "shorts": {
        "label": "YouTube Shorts",
        "canvas": (1080, 1920),
        "margins": {"left": 60 / 1080, "top": 170 / 1920, "right": 96 / 1080, "bottom": 390 / 1920},
        "basis": "community green-zone measurements",
    },
    "landscape": {
        "label": "16:9 title-safe",
        "canvas": (1920, 1080),
        "margins": {"left": 0.10, "top": 0.10, "right": 0.10, "bottom": 0.10},
        "basis": "traditional 10% title-safe area",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: str, text: str) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float) -> float:
    return round(float(value), 3)


def _profile_canvas(platform: str, width: Optional[int], height: Optional[int]) -> tuple[int, int]:
    default_width, default_height = PROFILES[platform]["canvas"]
    canvas_width = int(width or default_width)
    canvas_height = int(height or default_height)
    if canvas_width < 2 or canvas_height < 2:
        raise ValueError("canvas width and height must be at least 2 pixels")
    return canvas_width, canvas_height


def build_safe_zone(
    platform: str,
    width: int,
    height: int,
    *,
    overrides: Optional[Mapping[str, Optional[float]]] = None,
) -> Dict[str, float]:
    profile = PROFILES[platform]
    fractions = profile["margins"]
    margins = {
        "left": width * float(fractions["left"]),
        "top": height * float(fractions["top"]),
        "right": width * float(fractions["right"]),
        "bottom": height * float(fractions["bottom"]),
    }
    for key, value in (overrides or {}).items():
        if value is not None:
            if value < 0:
                raise ValueError(f"safe-area {key} margin cannot be negative")
            margins[key] = float(value)
    if margins["left"] + margins["right"] >= width:
        raise ValueError("safe-area horizontal margins consume the whole canvas")
    if margins["top"] + margins["bottom"] >= height:
        raise ValueError("safe-area vertical margins consume the whole canvas")
    return {
        "left": _round(margins["left"]),
        "top": _round(margins["top"]),
        "right": _round(width - margins["right"]),
        "bottom": _round(height - margins["bottom"]),
        "width": _round(width - margins["left"] - margins["right"]),
        "height": _round(height - margins["top"] - margins["bottom"]),
        "margins": {key: _round(value) for key, value in margins.items()},
    }


def _display_units(text: str) -> float:
    units = 0.0
    for character in str(text or ""):
        if character.isspace():
            units += 0.3
        elif CJK_RE.match(character):
            units += 1.0
        else:
            units += 0.55
    return units


def _centered_text_bbox(
    text: str,
    *,
    width: int,
    height: int,
    font_size: float,
    padding: float = 24.0,
) -> Dict[str, float]:
    lines = str(text or "").splitlines() or [""]
    line_units = max((_display_units(line) for line in lines), default=1.0)
    box_width = min(width * 0.90, max(font_size * 2.0, line_units * font_size + padding * 2))
    box_height = min(height * 0.60, max(font_size * 1.25, len(lines) * font_size * 1.25 + padding * 2))
    return {
        "x": _round((width - box_width) / 2),
        "y": _round((height - box_height) / 2),
        "width": _round(box_width),
        "height": _round(box_height),
    }


def _subtitle_bbox(
    *,
    width: int,
    height: int,
    font_size: float,
    max_lines: int,
    margin_lr: float,
    margin_v: float,
) -> Dict[str, float]:
    line_height = font_size * 1.25
    box_height = max_lines * line_height + font_size * 0.30
    bottom = height - margin_v
    return {
        "x": _round(margin_lr),
        "y": _round(bottom - box_height),
        "width": _round(width - 2 * margin_lr),
        "height": _round(box_height),
    }


def _parse_aspect_ratio(value: Any, default: float = 9 / 16) -> float:
    if isinstance(value, str) and ":" in value:
        left, right = value.split(":", 1)
        numerator = _finite_float(left)
        denominator = _finite_float(right)
        if numerator and denominator and numerator > 0 and denominator > 0:
            return numerator / denominator
    numeric = _finite_float(value)
    if numeric and numeric > 0:
        return numeric
    return default


def _pip_bbox(cue: Mapping[str, Any], *, width: int, height: int) -> Dict[str, float]:
    width_ratio = _finite_float(cue.get("width_ratio", cue.get("size")))
    width_ratio = min(0.55, max(0.12, width_ratio if width_ratio is not None else 0.24))
    overlay_width = width * width_ratio
    overlay_height = overlay_width / _parse_aspect_ratio(cue.get("aspect_ratio"))
    overlay_height = min(height, overlay_height)
    margin_ratio = _finite_float(cue.get("margin_ratio"))
    margin_ratio = min(0.16, max(0.0, margin_ratio if margin_ratio is not None else 0.035))
    margin = _finite_float(cue.get("margin_px"))
    if margin is None:
        margin = min(width, height) * margin_ratio
    position = str(cue.get("position") or "bottom_right").lower().replace("-", "_")
    if position in {"center", "middle"}:
        x = (width - overlay_width) / 2
        y = (height - overlay_height) / 2
    else:
        x = margin if position.endswith("left") else width - overlay_width - margin
        y = margin if position.startswith("top") else height - overlay_height - margin
    return {"x": _round(x), "y": _round(y), "width": _round(overlay_width), "height": _round(overlay_height)}


def _focus_bbox(cue: Mapping[str, Any], *, width: int, height: int) -> Optional[Dict[str, float]]:
    if cue.get("marker", True) is False:
        return None
    x = _finite_float(cue.get("x", cue.get("norm_x")))
    y = _finite_float(cue.get("y", cue.get("norm_y")))
    if x is None or y is None:
        return None
    source_width = _finite_float(cue.get("source_width"))
    source_height = _finite_float(cue.get("source_height"))
    if (x > 1 or y > 1) and source_width and source_height:
        x /= source_width
        y /= source_height
    marker_size = _finite_float(cue.get("marker_size"))
    box_size = min(width, height) * min(0.35, max(0.04, marker_size if marker_size is not None else 0.13))
    return {
        "x": _round(x * width - box_size / 2),
        "y": _round(y * height - box_size / 2),
        "width": _round(box_size),
        "height": _round(box_size),
    }


def _element(
    element_id: str,
    source: str,
    kind: str,
    *,
    bbox: Optional[Mapping[str, Any]],
    critical: bool = True,
    text: str = "",
    timing: Optional[Mapping[str, Any]] = None,
    assumption: str = "",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": element_id,
        "source": source,
        "kind": kind,
        "critical": bool(critical),
        "bbox": dict(bbox) if bbox is not None else None,
    }
    if text:
        result["text"] = text
    if timing:
        result["timing"] = dict(timing)
    if assumption:
        result["assumption"] = assumption
    return result


def _timing(item: Mapping[str, Any]) -> Dict[str, Any]:
    result = {}
    for key in ("start", "end", "duration", "time", "timing_seconds"):
        if key in item:
            result[key] = item[key]
    return result


def elements_from_render_config(
    config: Mapping[str, Any],
    *,
    width: int,
    height: int,
    font_size: float = 120.0,
    subtitle_lines: int = 2,
) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    if config.get("subtitles", True) is not False and config.get("no_subtitles") is not True:
        configured_font = _finite_float(config.get("font_size"))
        actual_font = configured_font if configured_font is not None else font_size
        margin_lr = _finite_float(config.get("subtitle_margin_lr"))
        margin_v = _finite_float(config.get("subtitle_margin_v"))
        margin_lr = 60.0 if margin_lr is None else margin_lr
        margin_v = height * 0.28 if margin_v is None else margin_v
        elements.append(
            _element(
                "render-subtitles",
                "render_config.subtitles",
                "subtitle",
                bbox=_subtitle_bbox(
                    width=width,
                    height=height,
                    font_size=actual_font,
                    max_lines=subtitle_lines,
                    margin_lr=margin_lr,
                    margin_v=margin_v,
                ),
                assumption="render_final.py bottom-center ASS style; pass matching --font-size when rendering",
            )
        )

    badge_font = (_finite_float(config.get("font_size")) or font_size) * 1.2
    for index, badge in enumerate(config.get("text_badges") or [], start=1):
        if not isinstance(badge, Mapping):
            continue
        text = str(badge.get("text") or "").strip()
        elements.append(
            _element(
                f"render-badge-{index}",
                f"render_config.text_badges[{index}]",
                "text_badge",
                bbox=_centered_text_bbox(text, width=width, height=height, font_size=badge_font),
                text=text,
                timing=_timing(badge),
                assumption="render_final.py Badge ASS style is screen-centered",
            )
        )

    for index, card in enumerate(config.get("end_cards") or [], start=1):
        if not isinstance(card, Mapping):
            continue
        text = str(card.get("text") or "").strip()
        elements.append(
            _element(
                f"render-end-card-{index}",
                f"render_config.end_cards[{index}]",
                "end_card_text",
                bbox=_centered_text_bbox(text, width=width, height=height, font_size=font_size * 1.4),
                text=text,
                timing=_timing(card),
                assumption="render_final.py EndCard ASS style is screen-centered",
            )
        )

    for index, cue in enumerate(config.get("pip_overlays") or [], start=1):
        if isinstance(cue, Mapping):
            elements.append(
                _element(
                    f"render-pip-{index}",
                    f"render_config.pip_overlays[{index}]",
                    "pip_subject",
                    bbox=_pip_bbox(cue, width=width, height=height),
                    timing=_timing(cue),
                    assumption="PIP aspect defaults to 9:16 unless aspect_ratio is declared",
                )
            )

    for index, cue in enumerate(config.get("focus_events") or [], start=1):
        if not isinstance(cue, Mapping) or cue.get("marker", True) is False:
            continue
        elements.append(
            _element(
                f"render-focus-{index}",
                f"render_config.focus_events[{index}]",
                "focus_marker",
                bbox=_focus_bbox(cue, width=width, height=height),
                timing=_timing(cue),
                assumption="matches render_final.py marker_size and normalized x/y handling",
            )
        )
    return elements


def elements_from_enrich_plan(
    plan: Mapping[str, Any],
    *,
    width: int,
    height: int,
    font_size: float = 120.0,
) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    badge_font = font_size * 1.2
    badge_groups = [
        ("stickers", plan.get("stickers") or [], ("sticker", "text"), "sticker"),
        ("emphasis_cues", plan.get("emphasis_cues") or plan.get("emphasis") or [], ("label", "text", "matched_text"), "emphasis_badge"),
    ]
    for group, items, text_keys, kind in badge_groups:
        for index, cue in enumerate(items, start=1):
            if not isinstance(cue, Mapping):
                continue
            if kind == "emphasis_badge" and cue.get("show_badge", True) is False:
                continue
            text = next((str(cue.get(key) or "").strip() for key in text_keys if cue.get(key)), "")
            if not text:
                continue
            elements.append(
                _element(
                    f"enrich-{group}-{index}",
                    f"enrich_plan.{group}[{index}]",
                    kind,
                    bbox=_centered_text_bbox(text, width=width, height=height, font_size=badge_font),
                    text=text,
                    timing=_timing(cue),
                    assumption="render_final.py converts this cue to a centered Badge ASS event",
                )
            )

    for index, cue in enumerate(plan.get("chapter_cards") or [], start=1):
        if not isinstance(cue, Mapping):
            continue
        text = str(cue.get("title") or cue.get("text") or "").strip()
        has_image = any(cue.get(key) for key in ("png", "image_path", "asset_path"))
        elements.append(
            _element(
                f"enrich-chapter-{index}",
                f"enrich_plan.chapter_cards[{index}]",
                "chapter_card_text" if not has_image else "chapter_card_image",
                bbox=(
                    _centered_text_bbox(text, width=width, height=height, font_size=badge_font)
                    if not has_image
                    else None
                ),
                text=text,
                timing=_timing(cue),
                assumption=(
                    "centered Badge fallback when no local image exists"
                    if not has_image
                    else "text placement inside the full-frame image cannot be inferred from JSON"
                ),
            )
        )

    for index, cue in enumerate(plan.get("pip_overlays") or [], start=1):
        if isinstance(cue, Mapping):
            elements.append(
                _element(
                    f"enrich-pip-{index}",
                    f"enrich_plan.pip_overlays[{index}]",
                    "pip_subject",
                    bbox=_pip_bbox(cue, width=width, height=height),
                    timing=_timing(cue),
                    assumption="PIP aspect defaults to 9:16 unless aspect_ratio is declared",
                )
            )

    for index, cue in enumerate(plan.get("focus_events") or [], start=1):
        if not isinstance(cue, Mapping) or cue.get("marker", True) is False:
            continue
        elements.append(
            _element(
                f"enrich-focus-{index}",
                f"enrich_plan.focus_events[{index}]",
                "focus_marker",
                bbox=_focus_bbox(cue, width=width, height=height),
                timing=_timing(cue),
                assumption="matches render_final.py marker_size and normalized x/y handling",
            )
        )
    return elements


def _normalized_custom_bbox(raw: Mapping[str, Any], *, width: int, height: int) -> Optional[Dict[str, float]]:
    bbox = raw.get("bbox", raw)
    units = str(raw.get("units") or (bbox.get("units") if isinstance(bbox, Mapping) else "") or "px").lower()
    if isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes)) and len(bbox) == 4:
        x, y, box_width, box_height = (_finite_float(value) for value in bbox)
    elif isinstance(bbox, Mapping):
        if all(key in bbox for key in ("left", "top", "right", "bottom")):
            x = _finite_float(bbox.get("left"))
            y = _finite_float(bbox.get("top"))
            right = _finite_float(bbox.get("right"))
            bottom = _finite_float(bbox.get("bottom"))
            box_width = right - x if right is not None and x is not None else None
            box_height = bottom - y if bottom is not None and y is not None else None
        else:
            x = _finite_float(bbox.get("x"))
            y = _finite_float(bbox.get("y"))
            box_width = _finite_float(bbox.get("width", bbox.get("w")))
            box_height = _finite_float(bbox.get("height", bbox.get("h")))
    else:
        return None
    if None in {x, y, box_width, box_height}:
        return None
    if units in {"normalized", "norm", "fraction", "ratio"}:
        x *= width
        box_width *= width
        y *= height
        box_height *= height
    if box_width <= 0 or box_height <= 0:
        return None
    return {"x": _round(x), "y": _round(y), "width": _round(box_width), "height": _round(box_height)}


def elements_from_file(path: str, *, width: int, height: int) -> List[Dict[str, Any]]:
    payload = _read_json(path)
    items = payload.get("elements") if isinstance(payload, Mapping) else payload
    if not isinstance(items, list):
        raise ValueError(f"custom element file must contain elements[]: {path}")
    elements: List[Dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"custom element #{index} must be an object: {path}")
        elements.append(
            _element(
                str(item.get("id") or f"custom-{index}"),
                str(item.get("source") or f"{path}:elements[{index}]"),
                str(item.get("kind") or "custom"),
                bbox=_normalized_custom_bbox(item, width=width, height=height),
                critical=item.get("critical", True) is not False,
                text=str(item.get("text") or ""),
                timing=item.get("timing") if isinstance(item.get("timing"), Mapping) else _timing(item),
                assumption=str(item.get("assumption") or ""),
            )
        )
    return elements


def _breaches(bbox: Mapping[str, Any], safe_zone: Mapping[str, Any], width: int, height: int) -> List[str]:
    x = float(bbox["x"])
    y = float(bbox["y"])
    right = x + float(bbox["width"])
    bottom = y + float(bbox["height"])
    areas: List[str] = []
    if x < 0 or y < 0 or right > width or bottom > height:
        areas.append("outside_canvas")
    if x < float(safe_zone["left"]):
        areas.append("left_ui")
    if y < float(safe_zone["top"]):
        areas.append("top_ui")
    if right > float(safe_zone["right"]):
        areas.append("right_ui")
    if bottom > float(safe_zone["bottom"]):
        areas.append("bottom_ui")
    return areas


def analyze_elements(
    elements: Sequence[Mapping[str, Any]],
    *,
    platform: str,
    width: int,
    height: int,
    safe_zone: Mapping[str, Any],
) -> Dict[str, Any]:
    analyzed: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    for raw in elements:
        element = dict(raw)
        bbox = element.get("bbox")
        if not isinstance(bbox, Mapping):
            element["status"] = "uncheckable"
            severity = "warn" if element.get("critical", True) else "info"
            findings.append(
                {
                    "severity": severity,
                    "code": "missing_bbox",
                    "element_id": element.get("id"),
                    "source": element.get("source"),
                    "message": "Critical placement cannot be inferred from the declared artifact.",
                    "action": "Declare a custom bbox or inspect the SVG guide/rendered frame manually.",
                }
            )
            analyzed.append(element)
            continue

        areas = _breaches(bbox, safe_zone, width, height)
        element["breaches"] = areas
        if areas:
            severity = "block" if element.get("critical", True) else "warn"
            element["status"] = "blocked" if severity == "block" else "review"
            findings.append(
                {
                    "severity": severity,
                    "code": "critical_element_outside_safe_area" if severity == "block" else "element_outside_safe_area",
                    "element_id": element.get("id"),
                    "source": element.get("source"),
                    "kind": element.get("kind"),
                    "bbox": dict(bbox),
                    "breaches": areas,
                    "message": f"Element crosses {', '.join(areas)} for the {platform} profile.",
                    "action": "Move or resize the element until its full bounding box stays inside the safe rectangle.",
                }
            )
        else:
            element["status"] = "safe"
        analyzed.append(element)

    blocking = sum(item["severity"] == "block" for item in findings)
    warnings = sum(item["severity"] == "warn" for item in findings)
    uncheckable = sum(item.get("status") == "uncheckable" for item in analyzed)
    unsafe = sum(item.get("status") in {"blocked", "review"} for item in analyzed)
    status = "blocked" if blocking else ("review" if warnings else "ready")
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": status,
        "platform": {
            "id": platform,
            "label": PROFILES[platform]["label"],
            "basis": PROFILES[platform]["basis"],
        },
        "canvas": {"width": width, "height": height},
        "safe_zone": dict(safe_zone),
        "summary": {
            "elements": len(analyzed),
            "checked": len(analyzed) - uncheckable,
            "safe": sum(item.get("status") == "safe" for item in analyzed),
            "unsafe": unsafe,
            "uncheckable": uncheckable,
            "blocking": blocking,
            "warnings": warnings,
        },
        "findings": findings,
        "elements": analyzed,
        "notes": [
            "Safe-area presets are conservative community guidance, not permanent official platform specifications.",
            "This audit checks declared or renderer-default bounding boxes; it does not OCR or detect subjects in encoded frames.",
            "Use custom pixel margins and elements[] when a current platform UI or full-frame graphic has measured bounds.",
        ],
    }


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _bbox_text(bbox: Any) -> str:
    if not isinstance(bbox, Mapping):
        return "-"
    return "x={x}, y={y}, w={width}, h={height}".format(**bbox)


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    platform = report.get("platform") or {}
    canvas = report.get("canvas") or {}
    safe_zone = report.get("safe_zone") or {}
    lines = [
        "# Platform Safe Area QA",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Platform profile: `{platform.get('id')}` — {_cell(platform.get('label'))}",
        f"- Canvas: {canvas.get('width')}×{canvas.get('height')}",
        f"- Safe rectangle: left {safe_zone.get('left')}, top {safe_zone.get('top')}, right {safe_zone.get('right')}, bottom {safe_zone.get('bottom')}",
        f"- Elements: {summary.get('elements', 0)} ({summary.get('safe', 0)} safe, {summary.get('unsafe', 0)} unsafe, {summary.get('uncheckable', 0)} uncheckable)",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        f"> Profile basis: {_cell(platform.get('basis'))}. Treat this as a conservative review gate and override measured margins when the app UI changes.",
        "",
        "## Findings",
        "",
        "| severity | code | element | source | breaches | action |",
        "|---|---|---|---|---|---|",
    ]
    for finding in report.get("findings") or []:
        lines.append(
            "| {severity} | `{code}` | `{element}` | `{source}` | {breaches} | {action} |".format(
                severity=_cell(finding.get("severity")),
                code=_cell(finding.get("code")),
                element=_cell(finding.get("element_id")),
                source=_cell(finding.get("source")),
                breaches=", ".join(finding.get("breaches") or []) or "-",
                action=_cell(finding.get("action")),
            )
        )
    if not report.get("findings"):
        lines.append("| info | `ready` | - | - | - | Every checked element is inside the safe rectangle. |")

    lines.extend(
        [
            "",
            "## Elements",
            "",
            "| id | kind | status | bbox | assumption |",
            "|---|---|---|---|---|",
        ]
    )
    for element in report.get("elements") or []:
        lines.append(
            "| `{id}` | {kind} | **{status}** | `{bbox}` | {assumption} |".format(
                id=_cell(element.get("id")),
                kind=_cell(element.get("kind")),
                status=_cell(element.get("status")),
                bbox=_cell(_bbox_text(element.get("bbox"))),
                assumption=_cell(element.get("assumption")),
            )
        )
    lines.extend(
        [
            "",
            "## Review Limits",
            "",
            "- Full-frame B-roll and generated/image chapter cards need manual frame review unless their critical text/subject bbox is declared in `--elements`.",
            "- Re-run this QA for every platform export because the canvas and UI rails differ.",
            "- After layout changes, inspect the SVG guide or a rendered still at phone size before publishing.",
        ]
    )
    return "\n".join(lines)


def emit_svg(report: Mapping[str, Any]) -> str:
    canvas = report.get("canvas") or {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    safe = report.get("safe_zone") or {}
    left = float(safe.get("left") or 0)
    top = float(safe.get("top") or 0)
    right = float(safe.get("right") or width)
    bottom = float(safe.get("bottom") or height)
    rectangles = [
        f'<rect x="0" y="0" width="{width}" height="{top}" fill="#ef4444" fill-opacity="0.25"/>',
        f'<rect x="0" y="{bottom}" width="{width}" height="{max(0, height - bottom)}" fill="#ef4444" fill-opacity="0.25"/>',
        f'<rect x="0" y="{top}" width="{left}" height="{max(0, bottom - top)}" fill="#ef4444" fill-opacity="0.25"/>',
        f'<rect x="{right}" y="{top}" width="{max(0, width - right)}" height="{max(0, bottom - top)}" fill="#ef4444" fill-opacity="0.25"/>',
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" fill="none" stroke="#22c55e" stroke-width="6" stroke-dasharray="18 12"/>',
    ]
    element_shapes: List[str] = []
    for element in report.get("elements") or []:
        bbox = element.get("bbox")
        if not isinstance(bbox, Mapping):
            continue
        status = str(element.get("status") or "")
        color = "#ef4444" if status == "blocked" else ("#f59e0b" if status == "review" else "#38bdf8")
        x = float(bbox["x"])
        y = float(bbox["y"])
        box_width = float(bbox["width"])
        box_height = float(bbox["height"])
        label = html.escape(str(element.get("id") or "")[:48])
        element_shapes.extend(
            [
                f'<rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" fill="{color}" fill-opacity="0.10" stroke="{color}" stroke-width="4"/>',
                f'<text x="{max(8, x + 8)}" y="{max(28, y + 28)}" fill="{color}" font-family="sans-serif" font-size="24">{label}</text>',
            ]
        )
    profile_label = html.escape(str((report.get("platform") or {}).get("label") or "Safe area"))
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<rect width="{width}" height="{height}" fill="#111827"/>',
            *rectangles,
            f'<text x="{left + 18}" y="{top + 38}" fill="#86efac" font-family="sans-serif" font-size="30">{profile_label} safe area</text>',
            *element_shapes,
            "</svg>",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit declared subtitles, badges, PIP, focus markers, and custom elements against platform UI safe areas."
    )
    parser.add_argument("--config", help="render_final.py render_config JSON")
    parser.add_argument("--enrich-plan", action="append", default=[], help="auto_enrich/screen_focus/PIP plan JSON; repeatable")
    parser.add_argument("--elements", action="append", default=[], help="Custom JSON list or object with elements[] and pixel/normalized bboxes; repeatable")
    parser.add_argument("--platform", choices=sorted(PROFILES), default="universal")
    parser.add_argument("--width", type=int, help="Canvas width; defaults from --platform")
    parser.add_argument("--height", type=int, help="Canvas height; defaults from --platform")
    parser.add_argument("--font-size", type=float, default=120.0, help="Subtitle render font size at 1080px short edge (default: 120)")
    parser.add_argument("--subtitle-lines", type=int, default=2, help="Maximum subtitle lines used for the estimated bbox")
    parser.add_argument("--safe-left", type=float, help="Override left unsafe margin in pixels")
    parser.add_argument("--safe-top", type=float, help="Override top unsafe margin in pixels")
    parser.add_argument("--safe-right", type=float, help="Override right unsafe margin in pixels")
    parser.add_argument("--safe-bottom", type=float, help="Override bottom unsafe margin in pixels")
    parser.add_argument("--output", required=True, help="Output platform_safe_area_qa.v1 JSON")
    parser.add_argument("--markdown", help="Optional Markdown review report")
    parser.add_argument("--guide", help="Optional SVG safe-area overlay/review guide")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when a critical element crosses the safe rectangle")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.font_size <= 0:
        parser.error("--font-size must be positive")
    if args.subtitle_lines < 1:
        parser.error("--subtitle-lines must be at least 1")

    width, height = _profile_canvas(args.platform, args.width, args.height)
    safe_zone = build_safe_zone(
        args.platform,
        width,
        height,
        overrides={
            "left": args.safe_left,
            "top": args.safe_top,
            "right": args.safe_right,
            "bottom": args.safe_bottom,
        },
    )
    elements: List[Dict[str, Any]] = []
    input_paths: Dict[str, Any] = {"config": None, "enrich_plans": [], "element_files": []}

    try:
        if args.config:
            config_path = str(Path(args.config).expanduser().resolve())
            config = _read_json(config_path)
            if not isinstance(config, Mapping):
                raise ValueError("render config root must be a JSON object")
            elements.extend(
                elements_from_render_config(
                    config,
                    width=width,
                    height=height,
                    font_size=args.font_size,
                    subtitle_lines=args.subtitle_lines,
                )
            )
            input_paths["config"] = config_path
        for raw_path in args.enrich_plan:
            plan_path = str(Path(raw_path).expanduser().resolve())
            plan = _read_json(plan_path)
            if not isinstance(plan, Mapping):
                raise ValueError(f"enrich plan root must be a JSON object: {plan_path}")
            elements.extend(elements_from_enrich_plan(plan, width=width, height=height, font_size=args.font_size))
            input_paths["enrich_plans"].append(plan_path)
        for raw_path in args.elements:
            element_path = str(Path(raw_path).expanduser().resolve())
            elements.extend(elements_from_file(element_path, width=width, height=height))
            input_paths["element_files"].append(element_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    report = analyze_elements(
        elements,
        platform=args.platform,
        width=width,
        height=height,
        safe_zone=safe_zone,
    )
    report["inputs"] = input_paths
    write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))
    if args.guide:
        write_text(args.guide, emit_svg(report))

    summary = report["summary"]
    print(
        f"Platform safe area QA: {report['status']} "
        f"({summary['blocking']} blocking, {summary['warnings']} warnings, "
        f"{summary['safe']} safe)"
    )
    return 2 if args.strict and summary["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
