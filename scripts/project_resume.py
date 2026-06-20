#!/usr/bin/env python3
"""Create a compact handoff packet for resuming a video project.

This wraps pipeline_manifest.py with an agent-facing resume view: current
stage, latest artifacts, blockers, and the first next action. It does not
render, upload, or call generation providers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_manifest import (  # noqa: E402
    ARTIFACT_BY_CATEGORY,
    DEFAULT_EXCLUDES,
    STAGE_REQUIREMENTS,
    build_manifest,
    utc_now,
    write_json,
    write_text,
)


VERSION = "project_resume.v1"


def _rel_path(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root))
    except (OSError, ValueError):
        return path


def _artifact_summary(artifact: Mapping[str, Any], root: Path) -> Dict[str, Any]:
    path = str(artifact.get("path") or "")
    return {
        "category": artifact.get("category"),
        "path": path,
        "relative_path": _rel_path(path, root) if path else "",
        "modified_at": artifact.get("modified_at"),
        "size_bytes": artifact.get("size_bytes", 0),
        "sha256": artifact.get("sha256"),
    }


def _latest_artifacts(manifest: Mapping[str, Any], root: Path, *, limit: int) -> List[Dict[str, Any]]:
    artifacts = [_artifact_summary(item, root) for item in manifest.get("artifacts") or []]
    artifacts.sort(
        key=lambda item: (
            str(item.get("modified_at") or ""),
            str(item.get("relative_path") or ""),
        ),
        reverse=True,
    )
    return artifacts[: max(0, limit)]


def _ready_artifacts(manifest: Mapping[str, Any], root: Path) -> List[Dict[str, Any]]:
    ready: List[Dict[str, Any]] = []
    for gate in manifest.get("gates") or []:
        latest = gate.get("latest_path")
        if gate.get("status") != "ready" or not latest:
            continue
        ready.append(
            {
                "category": gate.get("category"),
                "label": gate.get("label"),
                "path": latest,
                "relative_path": _rel_path(str(latest), root),
                "artifact_count": gate.get("artifact_count", 0),
            }
        )
    return ready


def infer_phase(manifest: Mapping[str, Any]) -> str:
    missing = set(manifest.get("missing_required") or [])
    blocked = set(manifest.get("blocked_gates") or [])
    target_stage = str(manifest.get("target_stage") or "")

    if manifest.get("status") == "ready":
        return f"{target_stage}_ready" if target_stage else "ready"
    if "transcript" in missing:
        return "needs_transcription"
    if "clean_script" in missing:
        return "needs_clean_script"
    if "storyboard_plan" in missing:
        return "needs_storyboard_plan"
    if "render_config" in missing:
        return "needs_render_config"
    if "master_video" in missing:
        return "needs_render"
    if "render_qa" in missing:
        return "needs_render_qa"
    if "caption" in missing:
        return "needs_caption"
    if "publish_package" in missing:
        return "needs_publish_package"
    if "generation_task_log" in blocked:
        return "waiting_on_generation_tasks"
    if "provider_decision" in blocked or "video_prompt_pack" in blocked:
        return "needs_generation_approval"
    if "storyboard_assets" in blocked:
        return "needs_asset_resolution"
    if "render_qa" in blocked:
        return "needs_qa_fix"
    if "publish_package" in blocked:
        return "needs_publish_package_fix"
    if blocked:
        return f"blocked_by_{sorted(blocked)[0]}"
    if manifest.get("status") == "warn":
        return "needs_warning_review"
    return "in_progress"


def _suggested_prompt(packet: Mapping[str, Any]) -> str:
    project_dir = packet.get("project_dir", ".")
    status = packet.get("status", "unknown")
    phase = packet.get("phase", "in_progress")
    first_action = packet.get("recommended_first_action") or "Review the resume packet and continue the next pipeline step."
    return (
        f"Continue the video project at {project_dir}. "
        f"Current status: {status}, phase: {phase}. "
        f"Start with: {first_action}"
    )


def build_resume_packet(
    project_dir: str,
    *,
    target_stage: str = "publish_ready",
    required: Optional[Sequence[str]] = None,
    include_hash: bool = False,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    max_artifacts: int = 12,
) -> Dict[str, Any]:
    manifest = build_manifest(
        project_dir,
        target_stage=target_stage,
        required=required,
        include_hash=include_hash,
        excludes=excludes,
    )
    root = Path(manifest["project_dir"]).resolve()
    actions = list(manifest.get("next_actions") or [])
    packet: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "target_stage": target_stage,
        "status": manifest.get("status"),
        "phase": infer_phase(manifest),
        "summary": manifest.get("summary") or {},
        "missing_required": manifest.get("missing_required") or [],
        "blocked_gates": manifest.get("blocked_gates") or [],
        "warn_gates": manifest.get("warn_gates") or [],
        "recommended_first_action": actions[0] if actions else "",
        "next_actions": actions,
        "ready_artifacts": _ready_artifacts(manifest, root),
        "latest_artifacts": _latest_artifacts(manifest, root, limit=max_artifacts),
        "gates": [
            {
                "category": gate.get("category"),
                "required": gate.get("required"),
                "status": gate.get("status"),
                "artifact_count": gate.get("artifact_count", 0),
                "latest_path": gate.get("latest_path"),
                "latest_relative_path": _rel_path(str(gate.get("latest_path")), root)
                if gate.get("latest_path")
                else "",
                "notes": gate.get("notes") or [],
            }
            for gate in manifest.get("gates") or []
            if gate.get("required") or gate.get("status") in {"blocked", "warn"}
        ],
        "pipeline_manifest": {
            "version": manifest.get("version"),
            "generated_at": manifest.get("generated_at"),
            "required_categories": manifest.get("required_categories") or [],
        },
        "agent_handoff": {
            "read_first": [
                "project_resume.md or the --agent-note output",
                "pipeline_manifest.md if a full gate table exists",
                "the latest transcript, clean_script, render_config, and QA/caption artifacts listed below",
            ],
            "do_not": [
                "Do not submit paid image/video generation jobs without explicit approval.",
                "Do not upload or publish from this packet; use publish_package.py as handoff.",
                "Do not trust stale chat context over files listed in latest_artifacts.",
            ],
        },
    }
    packet["suggested_prompt"] = _suggested_prompt(packet)
    return packet


def emit_markdown(packet: Mapping[str, Any]) -> str:
    summary = packet.get("summary") or {}
    lines = [
        "# Project Resume",
        "",
        f"- Project: `{packet.get('project_dir', '')}`",
        f"- Target stage: `{packet.get('target_stage', '')}`",
        f"- Status: **{str(packet.get('status', '')).upper()}**",
        f"- Phase: `{packet.get('phase', '')}`",
        f"- Required ready: {summary.get('required_ready', 0)}/{summary.get('required', 0)}",
        f"- Blocking gates: {summary.get('blocked_gates', 0)}",
        f"- Warnings: {summary.get('warn_gates', 0)}",
        f"- Artifacts found: {summary.get('artifact_count', 0)}",
        "",
        "## Suggested Prompt",
        "",
        packet.get("suggested_prompt", ""),
    ]

    actions = packet.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    gates = packet.get("gates") or []
    if gates:
        lines.extend(
            [
                "",
                "## Gate Snapshot",
                "",
                "| category | required | status | artifacts | latest | notes |",
                "|---|---:|---|---:|---|---|",
            ]
        )
        for gate in gates:
            notes = "; ".join(gate.get("notes") or []) or "-"
            latest = gate.get("latest_relative_path") or "-"
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

    artifacts = packet.get("latest_artifacts") or []
    if artifacts:
        lines.extend(
            [
                "",
                "## Latest Artifacts",
                "",
                "| category | modified | size | path |",
                "|---|---|---:|---|",
            ]
        )
        for item in artifacts:
            lines.append(
                "| {category} | {modified} | {size} | `{path}` |".format(
                    category=item.get("category", ""),
                    modified=item.get("modified_at", ""),
                    size=item.get("size_bytes", 0),
                    path=item.get("relative_path") or item.get("path") or "",
                )
            )

    handoff = packet.get("agent_handoff") if isinstance(packet.get("agent_handoff"), Mapping) else {}
    read_first = handoff.get("read_first") or []
    do_not = handoff.get("do_not") or []
    if read_first:
        lines.extend(["", "## Read First", ""])
        lines.extend(f"- {item}" for item in read_first)
    if do_not:
        lines.extend(["", "## Guardrails", ""])
        lines.extend(f"- {item}" for item in do_not)

    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an agent handoff packet from local video project artifacts."
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder to scan (default: current directory).")
    parser.add_argument("--output", default="project_resume.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown resume path.")
    parser.add_argument(
        "--agent-note",
        nargs="?",
        const="",
        help="Optional agent note path; pass without a value to write CLAUDE.md in --project-dir.",
    )
    parser.add_argument(
        "--target-stage",
        default="publish_ready",
        choices=sorted(STAGE_REQUIREMENTS),
        help="Readiness gate to summarize.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=sorted(ARTIFACT_BY_CATEGORY),
        help="Additional artifact category required for this resume; can repeat.",
    )
    parser.add_argument("--max-artifacts", type=int, default=12, help="Maximum recent artifacts to list.")
    parser.add_argument("--hash", action="store_true", help="Include SHA-256 for matched files.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory name to exclude while scanning; can repeat.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when the summarized pipeline is blocked.")
    parser.add_argument("--fail-on-warn", action="store_true", help="With --strict, also exit 2 on warning status.")
    return parser.parse_args(argv)


def _agent_note_path(value: Optional[str], root: Path) -> Optional[str]:
    if value is None:
        return None
    if value == "":
        return str(root / "CLAUDE.md")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    packet = build_resume_packet(
        args.project_dir,
        target_stage=args.target_stage,
        required=args.require,
        include_hash=args.hash,
        excludes=excludes,
        max_artifacts=args.max_artifacts,
    )
    write_json(args.output, packet)
    markdown = emit_markdown(packet)
    if args.markdown:
        write_text(args.markdown, markdown)

    note_path = _agent_note_path(args.agent_note, Path(packet["project_dir"]))
    if note_path:
        write_text(note_path, markdown)

    summary = packet["summary"]
    print(
        "Project resume: "
        f"{packet['status']} "
        f"phase={packet['phase']} "
        f"required={summary.get('required_ready', 0)}/{summary.get('required', 0)} "
        f"blocked={summary.get('blocked_gates', 0)} "
        f"warn={summary.get('warn_gates', 0)} "
        f"artifacts={summary.get('artifact_count', 0)}",
        file=sys.stderr,
    )

    if args.strict and (
        packet["status"] == "blocked" or (args.fail_on_warn and packet["status"] == "warn")
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
