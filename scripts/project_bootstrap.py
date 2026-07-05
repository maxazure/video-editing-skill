#!/usr/bin/env python3
"""Bootstrap a local video project from raw source files.

The script creates a stable project workspace, copies or hardlinks source
media into origin/, and writes reviewable project memory artifacts. It does
not transcode, render, upload, or call any generation provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


VERSION = "project_bootstrap.v1"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".flv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SIDECAR_EXTS = {".srt", ".vtt", ".ass", ".json", ".md", ".txt", ".csv"}

DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "origin",
    "work",
    "output",
    "verify",
    "edit",
}

CATEGORY_DIRS = {
    "raw": "origin/raw",
    "broll": "origin/broll",
    "audio": "origin/audio",
    "bgm": "origin/bgm",
    "image": "origin/images",
    "asset": "origin/assets",
    "sidecar": "origin/sidecars",
}

KEYWORD_CATEGORY = {
    "broll": "broll",
    "b-roll": "broll",
    "stock": "broll",
    "cutaway": "broll",
    "bgm": "bgm",
    "music": "bgm",
    "soundtrack": "bgm",
    "logo": "asset",
    "watermark": "asset",
    "cover": "asset",
    "thumbnail": "asset",
    "asset": "asset",
    "assets": "asset",
}

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _mtime(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in SIDECAR_EXTS:
        return "sidecar"
    return None


def _path_tokens(path: Path) -> Set[str]:
    tokens: Set[str] = set()
    for part in path.parts:
        lowered = part.lower()
        tokens.add(lowered)
        tokens.update(token for token in re.split(r"[\s._-]+", lowered) if token)
    return tokens


def classify_source(path: Path, media_type: str) -> str:
    tokens = _path_tokens(path)
    for token, category in KEYWORD_CATEGORY.items():
        if token in tokens:
            if media_type == "audio" and category in {"bgm", "audio"}:
                return category
            if media_type == "image" and category == "asset":
                return category
            if media_type == "video" and category == "broll":
                return category

    if media_type == "video":
        return "raw"
    if media_type == "audio":
        return "audio"
    if media_type == "image":
        return "image"
    return "sidecar"


def _iter_source_files(source: Path, excludes: Iterable[str]) -> Iterable[Path]:
    excluded = set(excludes)
    if source.is_file():
        if _media_type(source):
            yield source
        return

    if not source.is_dir():
        return

    for root, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if name not in excluded]
        base = Path(root)
        for filename in files:
            path = base / filename
            if _media_type(path):
                yield path


def collect_source_files(sources: Sequence[str], *, excludes: Iterable[str] = DEFAULT_EXCLUDES) -> List[Path]:
    files: Dict[str, Path] = {}
    for raw_source in sources:
        source = Path(raw_source).expanduser()
        for path in _iter_source_files(source, excludes):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            files[str(resolved)] = resolved
    return sorted(files.values(), key=lambda item: str(item).lower())


def _safe_filename(filename: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", filename.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "source"


def _unique_destination(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stem = Path(safe).stem or "source"
    suffix = Path(safe).suffix
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def _transfer_source(source: Path, destination: Path, mode: str) -> Dict[str, Any]:
    warnings: List[str] = []
    if source.resolve() == destination.resolve():
        return {"action": "already_in_project", "warnings": warnings}

    if mode == "hardlink":
        try:
            os.link(source, destination)
            return {"action": "hardlinked", "warnings": warnings}
        except OSError as exc:
            shutil.copy2(source, destination)
            warnings.append(f"hardlink failed; copied instead: {exc}")
            return {"action": "copied_fallback", "warnings": warnings}

    shutil.copy2(source, destination)
    return {"action": "copied", "warnings": warnings}


def _directory_map(project_dir: Path) -> Dict[str, str]:
    directories = {
        "project": str(project_dir.resolve()),
        "origin": str((project_dir / "origin").resolve()),
        "work": str((project_dir / "work").resolve()),
        "output": str((project_dir / "output").resolve()),
        "verify": str((project_dir / "verify").resolve()),
        "edit": str((project_dir / "edit").resolve()),
    }
    for category, rel_path in CATEGORY_DIRS.items():
        directories[f"origin_{category}"] = str((project_dir / rel_path).resolve())
    return directories


def _ensure_project_dirs(project_dir: Path) -> Dict[str, str]:
    directories = _directory_map(project_dir)
    for path in directories.values():
        Path(path).mkdir(parents=True, exist_ok=True)
    return directories


def _next_actions(inventory: Mapping[str, Any]) -> List[str]:
    summary = inventory.get("summary") if isinstance(inventory.get("summary"), Mapping) else {}
    files = int(summary.get("files") or 0)
    project_dir = inventory.get("project_dir") or "."
    if files == 0:
        return [
            "Add source media files under origin/raw or rerun project_bootstrap.py with --source.",
            "When source media exists, rerun project_bootstrap.py before transcription.",
        ]

    return [
        "Review work/source_inventory.md and confirm the primary talking-head or long-form source.",
        "Run transcribe.py on the primary source and save work/transcript.json.",
        (
            "Run pipeline_manifest.py --project-dir "
            f"{project_dir} --target-stage analysis --require source_inventory --strict."
        ),
        "Continue with video_understanding.py or takes_pack.py when visual review or multi-take selection is needed.",
    ]


def bootstrap_project(
    sources: Sequence[str],
    *,
    project_dir: str,
    title: Optional[str] = None,
    mode: str = "copy",
    include_hash: bool = False,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    directories = _ensure_project_dirs(root)
    source_files = collect_source_files(sources, excludes=excludes)

    files: List[Dict[str, Any]] = []
    warnings: List[str] = []
    missing_sources = [str(Path(item).expanduser()) for item in sources if not Path(item).expanduser().exists()]
    warnings.extend(f"source does not exist: {item}" for item in missing_sources)

    for source in source_files:
        media_type = _media_type(source)
        if media_type is None:
            continue
        category = classify_source(source, media_type)
        target_dir = root / CATEGORY_DIRS[category]
        destination = _unique_destination(target_dir, source.name)
        transfer = _transfer_source(source, destination, mode)
        item_warnings = transfer["warnings"]
        warnings.extend(item_warnings)

        record: Dict[str, Any] = {
            "source_path": str(source),
            "project_path": str(destination.resolve()),
            "relative_path": _relative(destination, root),
            "filename": destination.name,
            "media_type": media_type,
            "category": category,
            "size_bytes": source.stat().st_size,
            "modified_at": _mtime(source),
            "action": transfer["action"],
            "warnings": item_warnings,
        }
        if include_hash:
            record["sha256"] = _sha256(source)
        files.append(record)

    category_counts: Dict[str, int] = {}
    media_counts: Dict[str, int] = {}
    action_counts: Dict[str, int] = {}
    for item in files:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1
        media_counts[item["media_type"]] = media_counts.get(item["media_type"], 0) + 1
        action_counts[item["action"]] = action_counts.get(item["action"], 0) + 1

    summary = {
        "files": len(files),
        "total_size_bytes": sum(int(item["size_bytes"]) for item in files),
        "categories": category_counts,
        "media_types": media_counts,
        "actions": action_counts,
        "warnings": len(warnings),
    }

    inventory: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "title": title or root.name,
        "project_dir": str(root),
        "mode": mode,
        "status": "ready" if files else "warn",
        "sources": [str(Path(item).expanduser()) for item in sources],
        "directories": directories,
        "summary": summary,
        "files": files,
        "warnings": warnings,
        "notes": [
            "This bootstrap copied or linked local source files only.",
            "It did not render, transcode, upload, call an LLM, or submit paid generation tasks.",
            "Treat origin/ files as project working copies and keep external source paths untouched.",
        ],
    }
    inventory["next_actions"] = _next_actions(inventory)
    return inventory


def emit_markdown(inventory: Mapping[str, Any]) -> str:
    summary = inventory.get("summary") if isinstance(inventory.get("summary"), Mapping) else {}
    lines = [
        "# Project Bootstrap",
        "",
        f"- Project: `{inventory.get('project_dir', '')}`",
        f"- Title: {inventory.get('title', '')}",
        f"- Status: **{str(inventory.get('status', '')).upper()}**",
        f"- Mode: `{inventory.get('mode', '')}`",
        f"- Files: {summary.get('files', 0)}",
        f"- Total size: {summary.get('total_size_bytes', 0)} bytes",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Source Files",
        "",
        "| type | category | action | project path | source path |",
        "|---|---|---|---|---|",
    ]

    for item in inventory.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "| {type} | {category} | {action} | `{project}` | `{source}` |".format(
                type=item.get("media_type", ""),
                category=item.get("category", ""),
                action=item.get("action", ""),
                project=item.get("relative_path", ""),
                source=item.get("source_path", ""),
            )
        )

    warnings = inventory.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)

    actions = inventory.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    return "\n".join(lines).rstrip() + "\n"


def emit_project_note(inventory: Mapping[str, Any]) -> str:
    lines = [
        "# Project Memory",
        "",
        f"- Project: `{inventory.get('project_dir', '')}`",
        f"- Title: {inventory.get('title', '')}",
        f"- Bootstrapped at: {inventory.get('generated_at', '')}",
        f"- Source inventory: `work/source_inventory.json`",
        f"- Status: {inventory.get('status', '')}",
        "",
        "## Source Rules",
        "",
        "- Work inside this project directory.",
        "- Treat `origin/` files as project working copies; keep external source paths untouched.",
        "- Do not submit paid image or video generation jobs without explicit approval.",
        "- Prefer local artifacts over stale chat context.",
        "",
        "## Next Actions",
        "",
    ]
    lines.extend(f"- {action}" for action in inventory.get("next_actions") or [])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a local project workspace from raw video/audio/image sources."
    )
    parser.add_argument("--source", action="append", required=True, help="Source file or directory; can repeat.")
    parser.add_argument("--project-dir", required=True, help="Project directory to create or update.")
    parser.add_argument("--title", help="Human-readable project title.")
    parser.add_argument(
        "--mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="How to place source files into origin/ (default: copy).",
    )
    parser.add_argument("--output", help="Inventory JSON path (default: <project-dir>/work/source_inventory.json).")
    parser.add_argument("--markdown", help="Inventory Markdown path (default: <project-dir>/work/source_inventory.md).")
    parser.add_argument("--project-note", help="Project memory path (default: <project-dir>/project.md).")
    parser.add_argument("--next-steps", help="Next-steps Markdown path (default: <project-dir>/next_steps.md).")
    parser.add_argument("--hash", action="store_true", help="Include SHA-256 for each source file.")
    parser.add_argument("--exclude", action="append", default=[], help="Directory name to skip while scanning; repeatable.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when no supported source files were found.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    project_dir = Path(args.project_dir).expanduser().resolve()
    inventory = bootstrap_project(
        args.source,
        project_dir=str(project_dir),
        title=args.title,
        mode=args.mode,
        include_hash=args.hash,
        excludes=set(DEFAULT_EXCLUDES) | set(args.exclude or []),
    )

    output = args.output or str(project_dir / "work" / "source_inventory.json")
    markdown = args.markdown or str(project_dir / "work" / "source_inventory.md")
    project_note = args.project_note or str(project_dir / "project.md")
    next_steps = args.next_steps or str(project_dir / "next_steps.md")

    write_json(output, inventory)
    review = emit_markdown(inventory)
    write_text(markdown, review)
    write_text(project_note, emit_project_note(inventory))
    write_text(next_steps, "\n".join(f"- {action}" for action in inventory.get("next_actions") or []).rstrip() + "\n")

    summary = inventory["summary"]
    print(
        "Project bootstrap: "
        f"{inventory['status']} "
        f"files={summary['files']} "
        f"warnings={summary['warnings']} "
        f"project={inventory['project_dir']}",
        file=sys.stderr,
    )
    if args.strict and inventory["status"] != "ready":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
