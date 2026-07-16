#!/usr/bin/env python3
"""Plan and optionally render reviewable A/B cover variants.

The workflow is local-first and provider-free. It creates a JSON/Markdown
review artifact, can render each variant through generate_cover_image.py, and
records one selected cover for publish_package.py to reuse.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from content_guard import ViolationLevel, scan_text  # noqa: E402
from generate_cover_image import STYLES, generate_cover  # noqa: E402


VERSION = "cover_variants.v1"

PLATFORMS: Mapping[str, Mapping[str, Any]] = {
    "xhs": {"width": 1080, "height": 1440, "preview_width": 240, "label": "Xiaohongshu / RED"},
    "douyin": {"width": 1080, "height": 1920, "preview_width": 180, "label": "Douyin"},
    "wxch": {"width": 1080, "height": 1920, "preview_width": 180, "label": "WeChat Channels"},
    "tiktok": {"width": 1080, "height": 1920, "preview_width": 180, "label": "TikTok"},
    "reels": {"width": 1080, "height": 1920, "preview_width": 180, "label": "Instagram Reels"},
    "youtube_shorts": {"width": 1080, "height": 1920, "preview_width": 180, "label": "YouTube Shorts"},
    "youtube": {"width": 1280, "height": 720, "preview_width": 168, "label": "YouTube"},
}

STYLE_CONTRAST = {
    "bold": "news",
    "news": "white",
    "white": "news",
    "minimal": "gradient",
    "gradient": "bold",
    "frame": "bold",
    "techcard": "news",
}

TUTORIAL_HINTS = ("教程", "步骤", "方法", "工具", "workflow", "tutorial", "demo", "ai", "软件", "录屏")
NEWS_HINTS = ("真相", "为什么", "数据", "结果", "观点", "新闻", "变化", "对比", "揭秘", "警告")
LIFESTYLE_HINTS = ("旅行", "生活", "vlog", "探店", "日常", "城市", "开箱", "体验")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Optional[str]) -> Mapping[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.lower())


def _terms(value: str) -> List[str]:
    terms = [item.lower() for item in re.findall(r"[a-z0-9]{2,}", value, flags=re.I)]
    for chunk in re.findall(r"[\u3400-\u9fff]{2,}", value):
        if len(chunk) <= 4:
            terms.append(chunk)
        else:
            terms.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return list(dict.fromkeys(terms))


def _choose_primary_style(title: str, subtitle: str, requested: Optional[str]) -> str:
    if requested:
        return requested
    text = f"{title} {subtitle}".lower()
    if any(hint in text for hint in TUTORIAL_HINTS):
        return "techcard"
    if any(hint in text for hint in NEWS_HINTS):
        return "news"
    if any(hint in text for hint in LIFESTYLE_HINTS):
        return "frame"
    return "bold"


def _text_readability_warning(title: str) -> Optional[str]:
    if re.search(r"[\u3400-\u9fff]", title):
        if len(_normalized_text(title)) > 12:
            return "cover text exceeds 12 compact characters; shorten it for feed-size readability"
        return None
    words = re.findall(r"\b[\w'-]+\b", title)
    if len(words) > 5:
        return "cover text exceeds 5 words; shorten it for feed-size readability"
    return None


def analyze_synergy(post_title: str, cover_text: str) -> Dict[str, Any]:
    if not post_title:
        return {
            "status": "not_checked",
            "post_title": "",
            "cover_text": cover_text,
            "overlap_terms": [],
            "recommendation": "Pass --post-title or --caption to check title-cover information split.",
        }

    normalized_post = _normalized_text(post_title)
    normalized_cover = _normalized_text(cover_text)
    post_terms = set(_terms(post_title))
    cover_terms = set(_terms(cover_text))
    overlap = sorted(post_terms & cover_terms)
    exact_or_contained = bool(
        normalized_post
        and normalized_cover
        and (
            normalized_post == normalized_cover
            or normalized_cover in normalized_post
            or normalized_post in normalized_cover
        )
    )
    overlap_ratio = len(overlap) / max(1, len(cover_terms))
    status = "warn" if exact_or_contained or overlap_ratio >= 0.6 else "pass"
    recommendation = (
        "Make cover text add a result, emotion, contrast, or proof point that the post title does not repeat."
        if status == "warn"
        else "Title carries context while the cover adds a distinct visual hook."
    )
    return {
        "status": status,
        "post_title": post_title,
        "cover_text": cover_text,
        "overlap_terms": overlap,
        "overlap_ratio": round(overlap_ratio, 3),
        "recommendation": recommendation,
    }


def _content_findings(title: str, subtitle: str) -> Tuple[List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    for text in (title, subtitle):
        if not text:
            continue
        for violation in scan_text(text, context="title"):
            message = f"{violation.category}: {violation.match}"
            if violation.level == ViolationLevel.HARD:
                blockers.append(message)
            else:
                warnings.append(message)
    return blockers, warnings


def _variant_specs(primary_style: str, subtitle: str, count: int) -> List[Dict[str, Any]]:
    evidence_style = "techcard" if primary_style == "frame" else "frame"
    specs = [
        {
            "id": "cover-a",
            "name": "Primary",
            "style": primary_style,
            "subtitle": subtitle or None,
            "test_variable": "baseline",
            "hypothesis": "Best-fit style for the topic and platform.",
        },
        {
            "id": "cover-b",
            "name": "Contrast",
            "style": STYLE_CONTRAST[primary_style],
            "subtitle": subtitle or None,
            "test_variable": "style_contrast",
            "hypothesis": "Tests whether a different palette and hierarchy stop the scroll faster.",
        },
        {
            "id": "cover-c",
            "name": "Evidence frame",
            "style": evidence_style,
            "subtitle": subtitle or None,
            "test_variable": "visual_evidence",
            "hypothesis": "Tests whether a real source frame increases trust and topic clarity.",
        },
        {
            "id": "cover-d",
            "name": "Reduced copy",
            "style": primary_style,
            "subtitle": None,
            "test_variable": "subtitle_density",
            "hypothesis": "Tests whether removing secondary copy improves small-preview legibility.",
        },
    ]
    return specs[:count]


def _render_command(
    video: Path,
    *,
    title: str,
    subtitle: Optional[str],
    style: str,
    frame_timestamp: Optional[str],
    width: int,
    height: int,
    output_path: Path,
) -> str:
    command = [
        "python3",
        "scripts/generate_cover_image.py",
        str(video),
        "--title",
        title,
        "--style",
        style,
        "--width",
        str(width),
        "--height",
        str(height),
        "--output",
        str(output_path),
    ]
    if subtitle:
        command.extend(["--subtitle", subtitle])
    if style in {"frame", "techcard"}:
        command.append("--use-frame")
        if frame_timestamp:
            command.extend(["--frame-timestamp", frame_timestamp])
    return shlex.join(command)


def build_plan(
    video_path: str,
    *,
    title: str,
    subtitle: str = "",
    post_title: str = "",
    platform: str = "xhs",
    count: int = 3,
    style: Optional[str] = None,
    frame_timestamp: Optional[str] = None,
    output_dir: Optional[str] = None,
    selected: Optional[str] = None,
    require_selection: bool = False,
    require_distinct_cover_text: bool = False,
) -> Dict[str, Any]:
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform}")
    if count < 2 or count > 4:
        raise ValueError("count must be between 2 and 4")

    title = _compact(title)
    subtitle = _compact(subtitle)
    post_title = _compact(post_title)
    if not title:
        raise ValueError("title must not be empty")

    video = Path(video_path).expanduser().resolve()
    out_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else (video.parent / "cover_variants").resolve()
    )
    preset = PLATFORMS[platform]
    primary_style = _choose_primary_style(title, subtitle, style)
    specs = _variant_specs(primary_style, subtitle, count)

    variants: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        output_path = out_dir / f"{spec['id']}_{spec['name'].lower().replace(' ', '_')}.png"
        preview_path = output_path.with_name(f"{output_path.stem}_preview.png")
        uses_frame = spec["style"] in {"frame", "techcard"}
        variants.append({
            **spec,
            "rank": index,
            "title": title,
            "use_frame": uses_frame,
            "frame_timestamp": frame_timestamp if uses_frame else None,
            "width": preset["width"],
            "height": preset["height"],
            "output_path": str(output_path),
            "mobile_preview_path": str(preview_path),
            "rendered": False,
            "render_command": _render_command(
                video,
                title=title,
                subtitle=spec["subtitle"],
                style=spec["style"],
                frame_timestamp=frame_timestamp,
                width=int(preset["width"]),
                height=int(preset["height"]),
                output_path=output_path,
            ),
        })

    blockers, warnings = _content_findings(title, subtitle)
    readability = _text_readability_warning(title)
    if readability:
        warnings.append(readability)
    if not video.is_file():
        warnings.append(f"source video does not exist yet: {video}")

    synergy = analyze_synergy(post_title, title)
    if synergy["status"] == "warn":
        warnings.append("post title and cover text overlap too heavily")
        if require_distinct_cover_text:
            blockers.append("distinct cover text is required but title-cover synergy check warned")

    variant_ids = {item["id"] for item in variants}
    if selected and selected not in variant_ids:
        blockers.append(f"selected variant is not available: {selected}")
    if require_selection and not selected:
        blockers.append("cover selection is required before publish handoff")

    selected_variant = next((item for item in variants if item["id"] == selected), None)
    blocking = len(list(dict.fromkeys(blockers)))
    warning_count = len(list(dict.fromkeys(warnings)))
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": "blocked" if blocking else ("warn" if warning_count else "ready"),
        "source_video": str(video),
        "platform": platform,
        "platform_label": preset["label"],
        "output_dir": str(out_dir),
        "input": {
            "post_title": post_title,
            "cover_text": title,
            "subtitle": subtitle,
            "requested_style": style,
            "frame_timestamp": frame_timestamp,
        },
        "synergy": synergy,
        "variants": variants,
        "selected_variant": selected,
        "selected_cover": selected_variant["output_path"] if selected_variant else None,
        "summary": {
            "variants": len(variants),
            "rendered": 0,
            "selected": 1 if selected_variant else 0,
            "blocking": blocking,
            "warnings": warning_count,
        },
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "notes": [
            "Compare the small preview first; feed-size readability matters more than desktop detail.",
            "Change one test variable at a time when running a real A/B test.",
            "This artifact does not upload or publish anything.",
        ],
    }


def _write_mobile_preview(path: Path, output_path: Path, target_width: int) -> Optional[str]:
    try:
        from PIL import Image
    except ImportError:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-vf",
                f"scale={target_width}:-2",
                "-frames:v",
                "1",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output_path.is_file():
            return f"mobile preview failed: {result.stderr.strip()[:160]}"
        return None

    with Image.open(path) as image:
        width, height = image.size
        target_height = max(1, round(height * target_width / width))
        preview = image.convert("RGB").resize((target_width, target_height), Image.Resampling.LANCZOS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        preview.save(output_path)
    return None


def render_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    video = Path(str(plan["source_video"]))
    preset = PLATFORMS[str(plan["platform"])]
    render_failures: List[str] = []
    render_warnings: List[str] = []

    if not video.is_file():
        render_failures.append(f"cannot render without source video: {video}")
    else:
        Path(str(plan["output_dir"])).mkdir(parents=True, exist_ok=True)
        for variant in plan["variants"]:
            output_path = Path(str(variant["output_path"]))
            try:
                result = generate_cover(
                    str(video),
                    str(variant["title"]),
                    str(output_path),
                    width=int(variant["width"]),
                    height=int(variant["height"]),
                    style=str(variant["style"]),
                    subtitle=variant.get("subtitle"),
                    use_frame=bool(variant.get("use_frame")),
                    frame_timestamp=variant.get("frame_timestamp"),
                )
            except (OSError, RuntimeError) as exc:
                render_failures.append(f"{variant['id']}: cover render failed: {exc}")
                continue
            if not result or not output_path.is_file():
                render_failures.append(f"{variant['id']}: cover render failed")
                continue
            variant["rendered"] = True
            preview_warning = _write_mobile_preview(
                output_path,
                Path(str(variant["mobile_preview_path"])),
                int(preset["preview_width"]),
            )
            if preview_warning:
                render_warnings.append(f"{variant['id']}: {preview_warning}")

    plan["blockers"] = list(dict.fromkeys([*(plan.get("blockers") or []), *render_failures]))
    plan["warnings"] = list(dict.fromkeys([*(plan.get("warnings") or []), *render_warnings]))
    rendered = sum(1 for item in plan["variants"] if item.get("rendered"))
    plan["summary"]["rendered"] = rendered
    plan["summary"]["blocking"] = len(plan["blockers"])
    plan["summary"]["warnings"] = len(plan["warnings"])
    selected = next(
        (item for item in plan["variants"] if item["id"] == plan.get("selected_variant")),
        None,
    )
    if selected:
        plan["selected_cover"] = selected["output_path"]
    plan["status"] = "blocked" if plan["blockers"] else ("warn" if plan["warnings"] else "ready")
    return plan


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") or {}
    synergy = plan.get("synergy") or {}
    lines = [
        "# Cover Variants",
        "",
        f"- Platform: **{plan.get('platform_label', plan.get('platform', ''))}**",
        f"- Status: **{str(plan.get('status', '')).upper()}**",
        f"- Variants: {summary.get('variants', 0)}",
        f"- Rendered: {summary.get('rendered', 0)}",
        f"- Selected: `{plan.get('selected_variant') or '-'}`",
        "",
        "## Title-Cover Synergy",
        "",
        f"- Status: **{str(synergy.get('status', '')).upper()}**",
        f"- Post title: {synergy.get('post_title') or '-'}",
        f"- Cover text: {synergy.get('cover_text') or '-'}",
        f"- Recommendation: {synergy.get('recommendation') or '-'}",
        "",
        "## Variants",
        "",
        "| ID | Name | Style | Test variable | Subtitle | Rendered |",
        "|---|---|---|---|---|---|",
    ]
    for item in plan.get("variants") or []:
        lines.append(
            "| {id} | {name} | {style} | {variable} | {subtitle} | {rendered} |".format(
                id=item.get("id", ""),
                name=item.get("name", ""),
                style=item.get("style", ""),
                variable=item.get("test_variable", ""),
                subtitle=item.get("subtitle") or "-",
                rendered="yes" if item.get("rendered") else "no",
            )
        )
        lines.extend([
            "",
            f"**{item.get('id')} hypothesis:** {item.get('hypothesis')}",
            "",
            f"`{item.get('render_command')}`",
            "",
        ])

    blockers = plan.get("blockers") or []
    if blockers:
        lines.extend(["## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
        lines.append("")
    warnings = plan.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    lines.extend([
        "## Review",
        "",
        "Inspect the generated `*_preview.png` files at feed size, choose one variant, then rerun with `--select cover-x --require-selection` to record the publish cover.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: str, data: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: str, text: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan and optionally render A/B cover variants.")
    parser.add_argument("video", help="Source video used for frame-based variants.")
    parser.add_argument("--title", required=True, help="Short text displayed on the cover.")
    parser.add_argument("--subtitle", default="", help="Optional secondary cover line.")
    parser.add_argument("--post-title", default="", help="Published post/video title for synergy review.")
    parser.add_argument("--caption", help="Caption JSON; its title is used when --post-title is omitted.")
    parser.add_argument("--platform", default="xhs", choices=sorted(PLATFORMS))
    parser.add_argument("--count", type=int, default=3, choices=(2, 3, 4))
    parser.add_argument("--style", choices=STYLES, help="Override the primary cover style.")
    parser.add_argument("--frame-timestamp", help="Frame timestamp for evidence variants.")
    parser.add_argument("--output-dir", help="Directory for rendered covers and previews.")
    parser.add_argument("--select", help="Record one selected variant id, such as cover-a.")
    parser.add_argument("--render", action="store_true", help="Render PNG variants and small previews.")
    parser.add_argument("--require-selection", action="store_true", help="Block until --select is provided.")
    parser.add_argument(
        "--require-distinct-cover-text",
        action="store_true",
        help="Block when cover text substantially duplicates the published title.",
    )
    parser.add_argument("--output", default="cover_variants.json", help="Output JSON review artifact.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        caption = _load_json(args.caption)
        post_title = args.post_title or _compact(caption.get("title"))
        plan = build_plan(
            args.video,
            title=args.title,
            subtitle=args.subtitle,
            post_title=post_title,
            platform=args.platform,
            count=args.count,
            style=args.style,
            frame_timestamp=args.frame_timestamp,
            output_dir=args.output_dir,
            selected=args.select,
            require_selection=args.require_selection,
            require_distinct_cover_text=args.require_distinct_cover_text,
        )
        if args.render:
            plan = render_plan(plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cover variants error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, plan)
    if args.markdown:
        write_text(args.markdown, emit_markdown(plan))
    print(
        "Cover variants: "
        f"{plan['status']} "
        f"variants={plan['summary']['variants']} "
        f"rendered={plan['summary']['rendered']} "
        f"blocking={plan['summary']['blocking']}",
        file=sys.stderr,
    )
    if args.strict and plan["summary"]["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
