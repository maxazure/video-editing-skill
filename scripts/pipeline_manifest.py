#!/usr/bin/env python3
"""Build a lightweight run manifest for a video production folder.

This is intentionally local-first: it scans known artifact names from this
skill, summarizes readiness, and exits non-zero in strict mode when publish or
render gates are not satisfied. It does not enqueue work or call providers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


STATUS_ORDER = {"missing": 0, "ready": 1, "warn": 2, "blocked": 3}
DEFAULT_EXCLUDES = {".git", ".venv", "node_modules", "research-archive", "__pycache__"}


@dataclass(frozen=True)
class ArtifactDef:
    category: str
    label: str
    patterns: Sequence[str]
    next_action: str
    blocks_when_present: bool = False


@dataclass(frozen=True)
class ArtifactRecord:
    category: str
    path: str
    size_bytes: int
    modified_at: str
    sha256: Optional[str] = None


ARTIFACTS: Sequence[ArtifactDef] = (
    ArtifactDef(
        "transcript",
        "Transcript",
        ("**/transcript.json", "**/*_transcript.json"),
        "Run transcribe.py and save work/transcript.json.",
    ),
    ArtifactDef(
        "clean_script",
        "Clean Script",
        ("**/clean_script.md", "**/*_clean_script.md"),
        "Run rewrite_script.py or save a reviewed clean_script.md.",
    ),
    ArtifactDef(
        "rough_cut",
        "Rough / Jump Cut",
        ("**/rough_cut.json", "**/jump_cut.json", "**/*_cut_list.json"),
        "Run rough_cut.py or jump_cut.py if pause removal is needed.",
    ),
    ArtifactDef(
        "scene_boundaries",
        "Scene Boundaries",
        ("**/scene_boundaries.json", "**/*_scene_boundaries.json"),
        "Run scene_boundaries.py when highlight candidates should snap to visual cuts.",
    ),
    ArtifactDef(
        "highlight_candidates",
        "Highlight Candidates",
        ("**/highlight_candidates.json", "**/*_highlight_candidates.json"),
        "Run highlight_picker.py for long-to-short candidate review.",
    ),
    ArtifactDef(
        "enrich_plan",
        "Enrich Plan",
        ("**/enrich_plan.json", "**/*_enrich_plan.json", "**/screen_focus_plan.json"),
        "Run auto_enrich.py and optional screen_focus.py before final render.",
    ),
    ArtifactDef(
        "storyboard_plan",
        "Storyboard Plan",
        ("**/storyboard_plan.json", "**/*_storyboard_plan.json"),
        "Run storyboard_plan.py and review the Markdown shot cards.",
    ),
    ArtifactDef(
        "storyboard_assets",
        "Storyboard Assets",
        ("**/storyboard_assets.json", "**/*_storyboard_assets.json"),
        "Run storyboard_assets.py; resolve every blocking asset before render.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "provider_decision",
        "Provider Decision",
        ("**/provider_decision.json", "**/*_provider_decision.json"),
        "Run provider_decision.py and clear paid-credit, budget, or dependency blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "transition_bridge",
        "Transition Bridge",
        ("**/transition_bridge_plan.json", "**/*_transition_bridge*.json"),
        "Run transition_bridge.py; approve or skip any paid transition tasks.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "motion_guard",
        "Motion Guard",
        ("**/motion_guard.json", "**/*_motion_guard.json"),
        "Run motion_guard.py and replace still-heavy runs before render when motion is required.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "render_config",
        "Render Config",
        ("**/render_config.json", "**/*_render_config.json"),
        "Create render_config.json or export one from highlight_picker.py.",
    ),
    ArtifactDef(
        "master_video",
        "Master Video",
        (
            "**/output/*master*.mp4",
            "**/output/final*.mp4",
            "**/*_master*.mp4",
            "**/final.mp4",
        ),
        "Run render_final.py to produce a final/master MP4.",
    ),
    ArtifactDef(
        "render_qa",
        "Render QA",
        (
            "**/render_qa.json",
            "**/*_qa.json",
            "**/render_qa_review.json",
        ),
        "Run render_qa.py and fix any FAIL segments before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitles",
        "Subtitle Pack",
        ("**/subtitles/*.srt", "**/subtitles/*.vtt", "**/subtitles/*.ass", "**/*_subtitles.json"),
        "Run subtitle_pack.py when platform sidecar subtitles are needed.",
    ),
    ArtifactDef(
        "chapter_markers",
        "Chapter Markers",
        ("**/chapters.json", "**/chapters-youtube.txt", "**/chapters.ffmetadata"),
        "Run chapter_markers.py for long-form or YouTube/Bilibili chapter sidecars.",
    ),
    ArtifactDef(
        "caption",
        "Caption Copy",
        ("**/caption.json", "**/*_caption.json"),
        "Run generate_caption.py and review title/body/tags.",
    ),
    ArtifactDef(
        "platform_exports",
        "Platform Exports",
        ("**/multi_export_manifest.json", "**/output/*_xhs.mp4", "**/output/*_douyin.mp4", "**/output/*_wxch.mp4"),
        "Run multi_export.py when separate platform deliverables are required.",
    ),
    ArtifactDef(
        "nle_handoff",
        "NLE Handoff",
        ("**/*.edl", "**/*.edl.json"),
        "Run export_edl.py if an editor needs Premiere/FCP/Resolve handoff files.",
    ),
)

ARTIFACT_BY_CATEGORY = {item.category: item for item in ARTIFACTS}

STAGE_REQUIREMENTS: Mapping[str, Sequence[str]] = {
    "analysis": ("transcript",),
    "plan_review": ("transcript", "clean_script", "storyboard_plan"),
    "render_ready": ("transcript", "clean_script", "render_config"),
    "publish_ready": ("transcript", "clean_script", "render_config", "master_video", "render_qa", "caption"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _is_excluded(path: Path, project_dir: Path, excludes: Iterable[str]) -> bool:
    try:
        rel = path.relative_to(project_dir)
    except ValueError:
        rel = path
    excluded = set(excludes)
    return any(part in excluded for part in rel.parts)


def find_artifacts(
    project_dir: Path,
    definition: ArtifactDef,
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    include_hash: bool = False,
) -> List[ArtifactRecord]:
    records: Dict[str, ArtifactRecord] = {}
    for pattern in definition.patterns:
        for path in project_dir.glob(pattern):
            if not path.is_file() or _is_excluded(path, project_dir, excludes):
                continue
            abs_path = path.resolve()
            records[str(abs_path)] = ArtifactRecord(
                category=definition.category,
                path=str(abs_path),
                size_bytes=abs_path.stat().st_size,
                modified_at=_mtime(abs_path),
                sha256=_sha256(abs_path) if include_hash else None,
            )
    return sorted(records.values(), key=lambda item: (item.modified_at, item.path), reverse=True)


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _int_at(data: Mapping[str, Any], *keys: str) -> int:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return 0
        current = current.get(key)
    try:
        return int(current or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_category(definition: ArtifactDef, artifacts: Sequence[ArtifactRecord]) -> Dict[str, Any]:
    if not artifacts:
        return {
            "category": definition.category,
            "label": definition.label,
            "status": "missing",
            "artifact_count": 0,
            "latest_path": None,
            "notes": [],
            "next_action": definition.next_action,
        }

    status = "ready"
    notes: List[str] = []

    for artifact in artifacts:
        data = _load_json(artifact.path)
        if data is None:
            continue

        if definition.category in {"storyboard_assets", "transition_bridge", "motion_guard"}:
            blocking = _int_at(data, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"{blocking} blocking item(s) in summary.blocking")

        elif definition.category == "provider_decision":
            summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
            blockers = {
                "approval_required": _int_at(summary, "approval_required"),
                "budget_blocked": _int_at(summary, "budget_blocked"),
                "selected_missing_requirements": _int_at(summary, "selected_missing_requirements"),
            }
            active = {k: v for k, v in blockers.items() if v}
            if active:
                status = "blocked"
                notes.extend(f"{key}={value}" for key, value in active.items())

        elif definition.category == "render_qa":
            qa_status = str(data.get("status") or "").lower()
            file_statuses = [
                str(item.get("status") or "").lower()
                for item in data.get("files", [])
                if isinstance(item, Mapping)
            ]
            if qa_status == "fail" or "fail" in file_statuses:
                status = "blocked"
                notes.append("render QA status is fail")
            elif qa_status == "warn" or "warn" in file_statuses:
                status = "warn" if status != "blocked" else status
                notes.append("render QA status is warn")

    return {
        "category": definition.category,
        "label": definition.label,
        "status": status,
        "artifact_count": len(artifacts),
        "latest_path": artifacts[0].path,
        "notes": sorted(set(notes)),
        "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
    }


def build_manifest(
    project_dir: str,
    *,
    target_stage: str = "publish_ready",
    required: Optional[Sequence[str]] = None,
    include_hash: bool = False,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if target_stage not in STAGE_REQUIREMENTS:
        raise ValueError(f"unknown target stage: {target_stage}")

    required_categories = list(STAGE_REQUIREMENTS[target_stage])
    for category in required or []:
        if category not in ARTIFACT_BY_CATEGORY:
            raise ValueError(f"unknown artifact category: {category}")
        if category not in required_categories:
            required_categories.append(category)

    artifacts_by_category: Dict[str, List[ArtifactRecord]] = {}
    gates: List[Dict[str, Any]] = []
    all_artifacts: List[ArtifactRecord] = []

    for definition in ARTIFACTS:
        records = find_artifacts(root, definition, excludes=excludes, include_hash=include_hash)
        artifacts_by_category[definition.category] = records
        all_artifacts.extend(records)

        gate = evaluate_category(definition, records)
        gate["required"] = definition.category in required_categories
        gate["blocks_when_present"] = definition.blocks_when_present
        gates.append(gate)

    missing_required = [g["category"] for g in gates if g["required"] and g["status"] == "missing"]
    blocked = [
        g["category"]
        for g in gates
        if g["status"] == "blocked" and (g["required"] or g["blocks_when_present"])
    ]
    warned = [g["category"] for g in gates if g["status"] == "warn" and (g["required"] or g["blocks_when_present"])]

    if missing_required or blocked:
        status = "blocked"
    elif warned:
        status = "warn"
    else:
        status = "ready"

    next_actions: List[str] = []
    for gate in gates:
        if gate["category"] in missing_required or gate["category"] in blocked or gate["category"] in warned:
            action = gate.get("next_action")
            if action:
                next_actions.append(action)
            for note in gate.get("notes") or []:
                next_actions.append(f"{gate['label']}: {note}")

    return {
        "version": "pipeline_manifest.v1",
        "generated_at": utc_now(),
        "project_dir": str(root),
        "target_stage": target_stage,
        "required_categories": required_categories,
        "status": status,
        "summary": {
            "required": len(required_categories),
            "required_ready": sum(
                1 for g in gates if g["required"] and g["status"] in {"ready", "warn"}
            ),
            "missing_required": len(missing_required),
            "blocked_gates": len(blocked),
            "warn_gates": len(warned),
            "artifact_count": len(all_artifacts),
        },
        "missing_required": missing_required,
        "blocked_gates": blocked,
        "warn_gates": warned,
        "gates": gates,
        "artifacts": [asdict(item) for item in sorted(all_artifacts, key=lambda a: (a.category, a.path))],
        "next_actions": list(dict.fromkeys(next_actions)),
        "notes": [
            "This manifest is a local run-state summary, not a render queue.",
            "Optional storyboard/provider/transition artifacts block when present and unresolved.",
        ],
    }


def emit_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Pipeline Manifest",
        "",
        f"- Project: `{manifest.get('project_dir', '')}`",
        f"- Target stage: `{manifest.get('target_stage', '')}`",
        f"- Status: **{str(manifest.get('status', '')).upper()}**",
        f"- Required ready: {summary.get('required_ready', 0)}/{summary.get('required', 0)}",
        f"- Blocking gates: {summary.get('blocked_gates', 0)}",
        f"- Warnings: {summary.get('warn_gates', 0)}",
        f"- Artifacts found: {summary.get('artifact_count', 0)}",
        "",
        "## Gates",
        "",
        "| category | required | status | artifacts | latest | notes |",
        "|---|---:|---|---:|---|---|",
    ]

    for gate in manifest.get("gates") or []:
        latest = os.path.basename(str(gate.get("latest_path") or "")) or "-"
        notes = "; ".join(gate.get("notes") or []) or "-"
        lines.append(
            "| {category} | {required} | {status} | {count} | `{latest}` | {notes} |".format(
                category=gate.get("category", ""),
                required="yes" if gate.get("required") else "no",
                status=gate.get("status", ""),
                count=gate.get("artifact_count", 0),
                latest=latest,
                notes=notes,
            )
        )

    actions = manifest.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

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
        description="Scan a video project folder and emit a pipeline readiness manifest."
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder to scan (default: current directory).")
    parser.add_argument("--output", default="pipeline_manifest.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument(
        "--target-stage",
        default="publish_ready",
        choices=sorted(STAGE_REQUIREMENTS),
        help="Readiness gate to evaluate.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=sorted(ARTIFACT_BY_CATEGORY),
        help="Additional artifact category required for this run; can repeat.",
    )
    parser.add_argument("--hash", action="store_true", help="Include SHA-256 for matched files.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory name to exclude while scanning; can repeat.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when required or blocking gates fail.")
    parser.add_argument("--fail-on-warn", action="store_true", help="With --strict, also exit 2 on warning gates.")
    parser.add_argument("--list-categories", action="store_true", help="Print artifact categories and exit.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.list_categories:
        for item in ARTIFACTS:
            print(f"{item.category}\t{item.label}")
        return 0

    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    manifest = build_manifest(
        args.project_dir,
        target_stage=args.target_stage,
        required=args.require,
        include_hash=args.hash,
        excludes=excludes,
    )
    write_json(args.output, manifest)
    if args.markdown:
        write_text(args.markdown, emit_markdown(manifest))

    summary = manifest["summary"]
    print(
        "Pipeline manifest: "
        f"{manifest['status']} "
        f"required={summary['required_ready']}/{summary['required']} "
        f"blocked={summary['blocked_gates']} "
        f"warn={summary['warn_gates']} "
        f"artifacts={summary['artifact_count']}",
        file=sys.stderr,
    )

    if args.strict and (manifest["status"] == "blocked" or (args.fail_on_warn and manifest["status"] == "warn")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
