#!/usr/bin/env python3
"""Assemble platform upload packages from local video production artifacts.

This script is deliberately local-first. It does not call social APIs or upload
files. It gathers the final videos, cover, caption copy, subtitle sidecars,
chapter text, and pipeline gate status into JSON + Markdown so the final upload
step is repeatable and auditable.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "publish_package.v1"
DEFAULT_EXCLUDES = {".git", ".venv", "node_modules", "research-archive", "__pycache__"}
DEFAULT_PLATFORMS = ("xhs", "douyin", "wxch")


@dataclasses.dataclass(frozen=True)
class PlatformPreset:
    name: str
    label: str
    video_patterns: Sequence[str]
    video_requirement: str
    upload_notes: Sequence[str]
    preferred_subtitles: Sequence[str] = ("srt", "vtt")


PLATFORMS: Mapping[str, PlatformPreset] = {
    "xhs": PlatformPreset(
        name="xhs",
        label="Xiaohongshu / RED",
        video_patterns=("**/output/*_xhs.mp4", "**/*_xhs.mp4"),
        video_requirement="3:4 feed export from multi_export.py.",
        upload_notes=(
            "Use the short title as the note title.",
            "Paste caption_body first, then tags.",
            "Use the cover image if it is already reviewed.",
        ),
    ),
    "douyin": PlatformPreset(
        name="douyin",
        label="Douyin / TikTok CN",
        video_patterns=("**/output/*_douyin.mp4", "**/*_douyin.mp4"),
        video_requirement="9:16 full-screen export from multi_export.py.",
        upload_notes=(
            "Paste the title into the hook/title field when available.",
            "Keep tags after the body copy.",
            "Check captions after upload because platform compression can affect readability.",
        ),
    ),
    "wxch": PlatformPreset(
        name="wxch",
        label="WeChat Channels",
        video_patterns=("**/output/*_wxch.mp4", "**/*_wxch.mp4"),
        video_requirement="9:16 export, ideally capped around 60s.",
        upload_notes=(
            "Use a concise first sentence because preview text is short.",
            "Keep tags modest; do not overstuff.",
            "If the video exceeds the intended cap, re-run multi_export.py for wxch.",
        ),
    ),
    "youtube_shorts": PlatformPreset(
        name="youtube_shorts",
        label="YouTube Shorts",
        video_patterns=(
            "**/output/*_douyin.mp4",
            "**/output/final*.mp4",
            "**/*_master*.mp4",
            "**/final.mp4",
        ),
        video_requirement="9:16 vertical MP4; chapter text is optional for Shorts.",
        upload_notes=(
            "Use title as the YouTube title.",
            "Append chapter text only for long-form or mixed uploads.",
            "Upload SRT/VTT sidecar if the platform flow asks for captions.",
        ),
    ),
    "tiktok": PlatformPreset(
        name="tiktok",
        label="TikTok",
        video_patterns=("**/output/*_douyin.mp4", "**/*_douyin.mp4", "**/output/final*.mp4"),
        video_requirement="9:16 vertical MP4.",
        upload_notes=(
            "Use the same 9:16 master as Douyin unless a separate TikTok export exists.",
            "Review text overlays after upload.",
            "Keep hashtags platform-specific if the topic is local.",
        ),
    ),
    "instagram_reels": PlatformPreset(
        name="instagram_reels",
        label="Instagram Reels",
        video_patterns=("**/output/*_douyin.mp4", "**/*_douyin.mp4", "**/output/final*.mp4"),
        video_requirement="9:16 vertical MP4.",
        upload_notes=(
            "Use the caption body first; keep tags after a line break.",
            "Check cover crop in the profile grid.",
            "Use SRT/VTT only if uploading through a workflow that supports sidecars.",
        ),
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_excluded(path: Path, root: Path, excludes: Iterable[str]) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    excluded = set(excludes)
    return any(part in excluded for part in rel.parts)


def _find_latest(root: Path, patterns: Sequence[str], *, excludes: Iterable[str] = DEFAULT_EXCLUDES) -> Optional[Path]:
    matches: Dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not _is_excluded(path, root, excludes):
                resolved = path.resolve()
                matches[str(resolved)] = resolved
    if not matches:
        return None
    return sorted(matches.values(), key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)[0]


def _find_all(root: Path, patterns: Sequence[str], *, excludes: Iterable[str] = DEFAULT_EXCLUDES) -> List[Path]:
    matches: Dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not _is_excluded(path, root, excludes):
                resolved = path.resolve()
                matches[str(resolved)] = resolved
    return sorted(matches.values(), key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Optional[Path], *, limit: int = 12000) -> str:
    if not path or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit].rstrip()


def _display_path(path: Optional[Path]) -> Optional[str]:
    return str(path) if path else None


def _normalize_tags(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(",", " ").split()]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        parts = []
    tags: List[str] = []
    for part in parts:
        if not part:
            continue
        tags.append(part if part.startswith("#") else f"#{part}")
    return list(dict.fromkeys(tags))


def _caption_copy(caption: Mapping[str, Any]) -> Dict[str, Any]:
    title = str(caption.get("title") or caption.get("headline") or "").strip()
    body = str(
        caption.get("caption_body")
        or caption.get("body")
        or caption.get("description")
        or caption.get("text")
        or ""
    ).strip()
    tags = _normalize_tags(caption.get("tags") or caption.get("hashtags"))
    upload_copy = body
    if tags:
        tag_line = " ".join(tags)
        upload_copy = f"{body}\n\n{tag_line}".strip() if body else tag_line
    return {
        "title": title,
        "body": body,
        "tags": tags,
        "upload_copy": upload_copy,
        "publish_time_hint": str(caption.get("publish_time_hint") or "").strip(),
    }


def _subtitle_files(root: Path) -> Dict[str, str]:
    files = _find_all(
        root,
        (
            "**/subtitles/*.srt",
            "**/subtitles/*.vtt",
            "**/subtitles/*.ass",
            "**/*_subtitles.srt",
            "**/*_subtitles.vtt",
            "**/*_subtitles.ass",
        ),
    )
    out: Dict[str, str] = {}
    for path in files:
        ext = path.suffix.lower().lstrip(".")
        out.setdefault(ext, str(path))
    return out


def _selected_cover_from_variants(root: Path) -> Tuple[Optional[Path], List[str]]:
    warnings: List[str] = []
    plan_path = _find_latest(root, ("**/cover_variants.json", "**/*_cover_variants.json"))
    if not plan_path:
        return None, warnings
    plan = _load_json(plan_path)
    if plan is None:
        return None, [f"cover variants unreadable: {plan_path}"]
    raw_selected = str(plan.get("selected_cover") or "").strip()
    if not raw_selected:
        warnings.append(f"cover variants has no selected_cover: {plan_path}")
        return None, warnings
    selected = Path(raw_selected).expanduser()
    if not selected.is_absolute():
        selected = (plan_path.parent / selected).resolve()
    else:
        selected = selected.resolve()
    if not selected.is_file():
        warnings.append(f"selected cover does not exist: {selected}")
        return None, warnings
    return selected, warnings


def _find_latest_reviewed_cover(root: Path) -> Optional[Path]:
    covers = _find_all(
        root,
        (
            "**/cover*.png",
            "**/*cover*.png",
            "**/cover*.jpg",
            "**/*cover*.jpg",
            "**/cover*.jpeg",
            "**/*cover*.jpeg",
        ),
    )
    for path in covers:
        stem = path.stem.lower()
        if stem.endswith("_preview"):
            continue
        if re.match(r"cover-[a-d]_", stem):
            continue
        return path
    return None


def _parse_video_overrides(values: Optional[Sequence[str]]) -> Dict[str, Path]:
    overrides: Dict[str, Path] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--video must use platform=path syntax: {value}")
        platform, raw_path = value.split("=", 1)
        platform = platform.strip()
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform for --video: {platform}")
        path = Path(raw_path).expanduser().resolve()
        overrides[platform] = path
    return overrides


def _load_or_build_pipeline_manifest(root: Path, explicit: Optional[Path]) -> Tuple[Optional[Dict[str, Any]], Optional[Path], List[str]]:
    warnings: List[str] = []
    snapshot: Optional[Dict[str, Any]] = None
    snapshot_path: Optional[Path] = None
    if explicit:
        snapshot = _load_json(explicit)
        snapshot_path = explicit
        if snapshot is None:
            warnings.append(f"pipeline manifest unreadable: {explicit}")
    else:
        found = _find_latest(root, ("**/pipeline_manifest.json", "**/*_pipeline_manifest.json"))
        if found:
            snapshot = _load_json(found)
            snapshot_path = found
            if snapshot is None:
                warnings.append(f"pipeline manifest unreadable: {found}")

    try:
        from pipeline_manifest import build_manifest

        live = build_manifest(
            str(root),
            target_stage="publish_ready",
            ignored_categories=["publish_package"],
        )
    except Exception as exc:  # pragma: no cover - defensive for broken local imports
        warnings.append(f"could not build live pipeline manifest: {exc}")
        return None, snapshot_path, warnings

    if snapshot is not None and snapshot.get("status") != live.get("status"):
        warnings.append(
            "saved pipeline manifest status differs from live state: "
            f"saved={snapshot.get('status')} live={live.get('status')}"
        )
    return live, snapshot_path, warnings


def _pipeline_blockers(manifest: Optional[Mapping[str, Any]]) -> Tuple[List[str], List[str]]:
    if not manifest:
        return ["live pipeline manifest unavailable"], []
    status = str(manifest.get("status") or "").lower()
    blockers: List[str] = []
    warnings: List[str] = []
    if status == "blocked":
        blocked = ", ".join(str(item) for item in manifest.get("blocked_gates") or []) or "unknown gate"
        blockers.append(f"pipeline_manifest blocked: {blocked}")
    elif status == "warn":
        warned = ", ".join(str(item) for item in manifest.get("warn_gates") or []) or "warning gate"
        warnings.append(f"pipeline_manifest warning: {warned}")
    return blockers, warnings


def _verify_approval_receipt(
    root: Path,
    explicit: Optional[Path],
    *,
    required: bool,
) -> Tuple[Optional[Dict[str, Any]], Optional[Path], List[str]]:
    receipt_path = explicit
    if receipt_path is not None and not receipt_path.is_absolute():
        receipt_path = root / receipt_path
    if receipt_path is None:
        matches = _find_all(root, ("**/approval_receipt.json", "**/*_approval_receipt.json"))
        if len(matches) > 1:
            return None, None, [f"multiple approval receipts are ambiguous: {len(matches)} found"]
        receipt_path = matches[0] if matches else None
    if receipt_path is None:
        return None, None, ["approval receipt missing"] if required else []
    try:
        receipt_path = receipt_path.expanduser().resolve(strict=True)
        receipt_path.relative_to(root)
    except (OSError, ValueError):
        return None, receipt_path, [f"approval receipt must be a readable file inside the project: {receipt_path}"]
    receipt = _load_json(receipt_path)
    if receipt is None:
        return None, receipt_path, [f"approval receipt unreadable: {receipt_path}"]

    from approval_receipt import verify_receipt

    try:
        verification = verify_receipt(receipt, str(root), receipt_path=str(receipt_path))
    except Exception as exc:
        return None, receipt_path, [f"approval receipt verification failed: {exc}"]
    if verification.get("status") != "current":
        blocking = (verification.get("summary") or {}).get("blocking", 0)
        return verification, receipt_path, [
            f"approval receipt {verification.get('status', 'stale')}: {blocking} blocking item(s)"
        ]
    return verification, receipt_path, []


def _approval_coverage(
    root: Path,
    verification: Mapping[str, Any],
    selected_paths: Sequence[Path],
) -> Tuple[List[str], List[str], List[str]]:
    approved = {
        str(item.get("path") or "")
        for item in verification.get("artifacts") or []
        if isinstance(item, Mapping) and item.get("status") == "current"
    }
    selected: List[str] = []
    errors: List[str] = []
    for path in selected_paths:
        try:
            resolved = path.expanduser().resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            errors.append(f"selected publish artifact is outside the project or unreadable: {path}")
            continue
        selected.append(relative)
    selected = list(dict.fromkeys(selected))
    uncovered = [path for path in selected if path not in approved]
    covered = [path for path in selected if path in approved]
    return covered, uncovered, errors


def _platform_checklist(platform: PlatformPreset, *, has_cover: bool, has_subtitles: bool, has_chapters: bool) -> List[str]:
    checklist = [
        f"Upload the selected {platform.label} video file.",
        "Paste the reviewed title and upload copy from this package.",
        "Run a final in-platform preview before publishing.",
    ]
    if has_cover:
        checklist.insert(1, "Set the reviewed cover image if the platform supports it.")
    if has_subtitles:
        checklist.append("Attach SRT/VTT sidecar only when the platform flow supports subtitle upload.")
    if has_chapters and platform.name == "youtube_shorts":
        checklist.append("For non-Shorts YouTube uploads, append chapter timestamps to the description.")
    checklist.extend(platform.upload_notes)
    return list(dict.fromkeys(checklist))


def build_publish_package(
    project_dir: str,
    *,
    platforms: Sequence[str] = DEFAULT_PLATFORMS,
    video_overrides: Optional[Mapping[str, Path]] = None,
    caption_path: Optional[str] = None,
    cover_path: Optional[str] = None,
    chapters_path: Optional[str] = None,
    pipeline_manifest_path: Optional[str] = None,
    approval_receipt_path: Optional[str] = None,
    require_approval_receipt: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    selected_platforms = list(platforms)
    unknown = [item for item in selected_platforms if item not in PLATFORMS]
    if unknown:
        raise ValueError(f"unknown platform(s): {', '.join(unknown)}")

    overrides = dict(video_overrides or {})
    caption_file = Path(caption_path).expanduser().resolve() if caption_path else _find_latest(
        root, ("**/caption.json", "**/*_caption.json")
    )
    cover_warnings: List[str] = []
    if cover_path:
        cover_file = Path(cover_path).expanduser().resolve()
    else:
        cover_file, cover_warnings = _selected_cover_from_variants(root)
        if cover_file is None:
            cover_file = _find_latest_reviewed_cover(root)
    chapter_file = Path(chapters_path).expanduser().resolve() if chapters_path else _find_latest(
        root, ("**/chapters-youtube.txt", "**/chapters.json")
    )
    pipeline_file = Path(pipeline_manifest_path).expanduser().resolve() if pipeline_manifest_path else None
    pipeline_manifest, pipeline_source, pipeline_warnings = _load_or_build_pipeline_manifest(root, pipeline_file)
    receipt_file = Path(approval_receipt_path).expanduser() if approval_receipt_path else None

    blockers: List[str] = []
    warnings: List[str] = [*cover_warnings, *pipeline_warnings]

    caption_raw = _load_json(caption_file) if caption_file else None
    if caption_raw is None:
        blockers.append("caption missing or unreadable: run generate_caption.py before publishing")
        caption = {"title": "", "body": "", "tags": [], "upload_copy": "", "publish_time_hint": ""}
    else:
        caption = _caption_copy(caption_raw)
        if not caption["title"]:
            blockers.append("caption title is empty")
        if not caption["upload_copy"]:
            blockers.append("caption upload copy is empty")

    pipeline_blockers, pipeline_warns = _pipeline_blockers(pipeline_manifest)
    blockers.extend(pipeline_blockers)
    warnings.extend(pipeline_warns)

    subtitles = _subtitle_files(root)
    chapter_text = _read_text(chapter_file)

    platform_items: List[Dict[str, Any]] = []
    selected_publish_paths: List[Path] = []
    if caption_file and caption_file.is_file():
        selected_publish_paths.append(caption_file)
    if cover_file and cover_file.is_file():
        selected_publish_paths.append(cover_file)

    for platform_name in selected_platforms:
        preset = PLATFORMS[platform_name]
        video_file = overrides.get(platform_name)
        if video_file is None:
            video_file = _find_latest(root, preset.video_patterns)
        status = "ready"
        notes: List[str] = []
        if video_file is None or not video_file.is_file():
            status = "blocked"
            notes.append(f"missing video: {preset.video_requirement}")
            blockers.append(f"{platform_name}: missing video export")
        else:
            selected_publish_paths.append(video_file)

        preferred_subtitles = {
            ext: path for ext, path in subtitles.items()
            if ext in set(preset.preferred_subtitles)
        }
        selected_publish_paths.extend(Path(path) for path in preferred_subtitles.values())
        if platform_name == "youtube_shorts" and chapter_text and chapter_file and chapter_file.is_file():
            selected_publish_paths.append(chapter_file)
        platform_items.append({
            "platform": platform_name,
            "label": preset.label,
            "status": status,
            "video": _display_path(video_file if video_file and video_file.is_file() else None),
            "video_requirement": preset.video_requirement,
            "cover_image": _display_path(cover_file if cover_file and cover_file.is_file() else None),
            "subtitles": preferred_subtitles,
            "caption": caption,
            "chapter_text": chapter_text if platform_name == "youtube_shorts" else "",
            "upload_checklist": _platform_checklist(
                preset,
                has_cover=bool(cover_file and cover_file.is_file()),
                has_subtitles=bool(preferred_subtitles),
                has_chapters=bool(chapter_text),
            ),
            "notes": notes,
        })

    approval_verification, approval_source, approval_blockers = _verify_approval_receipt(
        root,
        receipt_file,
        required=require_approval_receipt,
    )
    blockers.extend(approval_blockers)
    approval_active = bool(
        require_approval_receipt
        or approval_source is not None
        or approval_blockers
        or approval_verification is not None
    )
    approval_status = "not_configured"
    approval_covered: List[str] = []
    approval_uncovered: List[str] = []
    approval_errors = list(approval_blockers)
    if isinstance(approval_verification, Mapping):
        approval_status = str(approval_verification.get("status") or "invalid")
        if approval_status == "current":
            approval_covered, approval_uncovered, coverage_errors = _approval_coverage(
                root,
                approval_verification,
                selected_publish_paths,
            )
            approval_errors.extend(coverage_errors)
            blockers.extend(coverage_errors)
            for relative_path in approval_uncovered:
                blocker = f"approval receipt does not cover selected artifact: {relative_path}"
                approval_errors.append(blocker)
                blockers.append(blocker)
            if coverage_errors or approval_uncovered:
                approval_status = "incomplete"
    elif approval_active:
        approval_status = (
            "missing"
            if approval_source is None and approval_blockers == ["approval receipt missing"]
            else "invalid"
        )

    blocker_count = len(list(dict.fromkeys(blockers)))
    warning_count = len(list(dict.fromkeys(warnings)))
    status = "blocked" if blocker_count else ("warn" if warning_count else "ready")
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "status": status,
        "summary": {
            "platforms": len(platform_items),
            "ready_platforms": sum(1 for item in platform_items if item["status"] == "ready"),
            "blocking": blocker_count,
            "warnings": warning_count,
        },
        "caption_path": _display_path(caption_file if caption_file and caption_file.is_file() else None),
        "cover_image": _display_path(cover_file if cover_file and cover_file.is_file() else None),
        "chapters_path": _display_path(chapter_file if chapter_file and chapter_file.is_file() else None),
        "pipeline_manifest_path": _display_path(pipeline_source),
        "pipeline_status": pipeline_manifest.get("status") if isinstance(pipeline_manifest, Mapping) else None,
        "approval_receipt_path": _display_path(approval_source),
        "approval_receipt_status": approval_status,
        "approval": {
            "required": require_approval_receipt,
            "active": approval_active,
            "status": approval_status,
            "receipt_path": _display_path(approval_source),
            "covered_artifacts": approval_covered,
            "uncovered_artifacts": approval_uncovered,
            "verification_errors": list(dict.fromkeys(approval_errors)),
        },
        "platforms": platform_items,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "notes": [
            "This package is an upload checklist and artifact manifest; it does not publish to platforms.",
            "Resolve blockers before manual upload or before passing paths to a publishing connector.",
        ],
    }


def emit_markdown(package: Mapping[str, Any]) -> str:
    summary = package.get("summary") or {}
    lines = [
        "# Publish Package",
        "",
        f"- Project: `{package.get('project_dir', '')}`",
        f"- Status: **{str(package.get('status', '')).upper()}**",
        f"- Ready platforms: {summary.get('ready_platforms', 0)}/{summary.get('platforms', 0)}",
        f"- Blockers: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Approval receipt: {package.get('approval_receipt_status') or 'not required'}",
        "",
        "## Platform Files",
        "",
        "| platform | status | video | cover | subtitles |",
        "|---|---|---|---|---|",
    ]
    for item in package.get("platforms") or []:
        subtitles = ", ".join(sorted((item.get("subtitles") or {}).keys())) or "-"
        lines.append(
            "| {platform} | {status} | `{video}` | `{cover}` | {subtitles} |".format(
                platform=item.get("platform", ""),
                status=item.get("status", ""),
                video=os.path.basename(str(item.get("video") or "")) or "-",
                cover=os.path.basename(str(item.get("cover_image") or "")) or "-",
                subtitles=subtitles,
            )
        )

    lines.extend(["", "## Upload Copy", ""])
    for item in package.get("platforms") or []:
        caption = item.get("caption") or {}
        tags = " ".join(caption.get("tags") or [])
        lines.extend([
            f"### {item.get('label', item.get('platform', 'Platform'))}",
            "",
            f"Title: {caption.get('title', '') or '-'}",
            "",
            caption.get("body", "") or "-",
        ])
        if tags:
            lines.extend(["", tags])
        if caption.get("publish_time_hint"):
            lines.extend(["", f"Publish window: {caption.get('publish_time_hint')}"])
        checklist = item.get("upload_checklist") or []
        if checklist:
            lines.extend(["", "Checklist:"])
            lines.extend(f"- {entry}" for entry in checklist)
        if item.get("notes"):
            lines.extend(["", "Notes:"])
            lines.extend(f"- {entry}" for entry in item.get("notes") or [])
        lines.append("")

    blockers = package.get("blockers") or []
    if blockers:
        lines.extend(["## Blockers", ""])
        lines.extend(f"- {entry}" for entry in blockers)
        lines.append("")

    warnings = package.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {entry}" for entry in warnings)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble a local publish package from final videos, caption copy, and gate artifacts."
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder to scan.")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=list(DEFAULT_PLATFORMS),
        choices=sorted(PLATFORMS),
        help="Platforms to include in the package.",
    )
    parser.add_argument(
        "--video",
        action="append",
        default=[],
        help="Override a platform video path as platform=/path/to/file.mp4; can repeat.",
    )
    parser.add_argument("--caption", help="Caption JSON path from generate_caption.py.")
    parser.add_argument("--cover", help="Optional cover image path.")
    parser.add_argument("--chapters", help="Optional chapters-youtube.txt or chapters.json path.")
    parser.add_argument("--pipeline-manifest", help="Optional pipeline_manifest.json path.")
    parser.add_argument("--approval-receipt", help="Optional approval_receipt.json path; verified when present.")
    parser.add_argument(
        "--require-approval-receipt",
        action="store_true",
        help="Block when no current hash-bound approval receipt exists.",
    )
    parser.add_argument("--output", default="publish_package.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when package status is blocked.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        package = build_publish_package(
            args.project_dir,
            platforms=args.platforms,
            video_overrides=_parse_video_overrides(args.video),
            caption_path=args.caption,
            cover_path=args.cover,
            chapters_path=args.chapters,
            pipeline_manifest_path=args.pipeline_manifest,
            approval_receipt_path=args.approval_receipt,
            require_approval_receipt=args.require_approval_receipt,
        )
    except ValueError as exc:
        print(f"publish package error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, package)
    if args.markdown:
        write_text(args.markdown, emit_markdown(package))

    summary = package["summary"]
    print(
        "Publish package: "
        f"{package['status']} "
        f"platforms={summary['ready_platforms']}/{summary['platforms']} "
        f"blocking={summary['blocking']} "
        f"warnings={summary['warnings']}",
        file=sys.stderr,
    )
    if args.strict and package["status"] == "blocked":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
