#!/usr/bin/env python3
"""Build a static human review dashboard from local video artifacts.

The dashboard is intentionally dependency-free: it wraps pipeline_manifest.py,
prioritizes gates that need attention, and emits both machine-readable JSON and
a single HTML file. It does not render, upload, or call provider APIs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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


VERSION = "review_dashboard.v1"


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


def _latest_artifacts(
    manifest: Mapping[str, Any],
    root: Path,
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    artifacts = [_artifact_summary(item, root) for item in manifest.get("artifacts") or []]
    artifacts.sort(
        key=lambda item: (
            str(item.get("modified_at") or ""),
            str(item.get("relative_path") or ""),
        ),
        reverse=True,
    )
    return artifacts[: max(0, limit)]


def _priority_for_gate(gate: Mapping[str, Any]) -> Tuple[int, str]:
    status = str(gate.get("status") or "")
    if status == "blocked":
        return 1, "blocker"
    if gate.get("required") and status == "missing":
        return 2, "missing_required"
    if status == "warn":
        return 3, "warning"
    if gate.get("required"):
        return 4, "required_review"
    return 5, "optional_review"


def _review_items(manifest: Mapping[str, Any], root: Path, *, max_actions: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for gate in manifest.get("gates") or []:
        status = str(gate.get("status") or "")
        required = bool(gate.get("required"))
        blocks_when_present = bool(gate.get("blocks_when_present"))
        should_show = (status == "missing" and required) or status in {"blocked", "warn"}
        if not should_show:
            continue

        priority, priority_label = _priority_for_gate(gate)
        latest = str(gate.get("latest_path") or "")
        notes = [str(note) for note in gate.get("notes") or []]
        action = str(gate.get("next_action") or "")
        items.append(
            {
                "category": gate.get("category"),
                "label": gate.get("label"),
                "status": status,
                "required": required,
                "blocks_when_present": blocks_when_present,
                "priority": priority,
                "priority_label": priority_label,
                "artifact_count": gate.get("artifact_count", 0),
                "latest_path": latest,
                "latest_relative_path": _rel_path(latest, root) if latest else "",
                "notes": notes,
                "next_action": action,
            }
        )

    items.sort(
        key=lambda item: (
            int(item.get("priority") or 9),
            str(item.get("category") or ""),
        )
    )
    return items[: max(0, max_actions)]


def _gate_snapshot(manifest: Mapping[str, Any], root: Path) -> List[Dict[str, Any]]:
    snapshot: List[Dict[str, Any]] = []
    for gate in manifest.get("gates") or []:
        latest = str(gate.get("latest_path") or "")
        snapshot.append(
            {
                "category": gate.get("category"),
                "label": gate.get("label"),
                "status": gate.get("status"),
                "required": gate.get("required"),
                "blocks_when_present": gate.get("blocks_when_present"),
                "artifact_count": gate.get("artifact_count", 0),
                "latest_path": latest,
                "latest_relative_path": _rel_path(latest, root) if latest else "",
                "notes": gate.get("notes") or [],
                "next_action": gate.get("next_action") or "",
            }
        )
    return snapshot


def _review_state(manifest: Mapping[str, Any]) -> str:
    if manifest.get("missing_required") or manifest.get("blocked_gates"):
        return "needs_fix"
    if manifest.get("warn_gates"):
        return "needs_warning_review"
    return "ready_for_final_review"


def build_dashboard_packet(
    project_dir: str,
    *,
    target_stage: str = "publish_ready",
    required: Optional[Sequence[str]] = None,
    include_hash: bool = False,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    max_artifacts: int = 24,
    max_actions: int = 16,
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
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "target_stage": target_stage,
        "status": manifest.get("status"),
        "review_state": _review_state(manifest),
        "summary": manifest.get("summary") or {},
        "missing_required": manifest.get("missing_required") or [],
        "blocked_gates": manifest.get("blocked_gates") or [],
        "warn_gates": manifest.get("warn_gates") or [],
        "review_items": _review_items(manifest, root, max_actions=max_actions),
        "next_actions": actions[: max(0, max_actions)],
        "latest_artifacts": _latest_artifacts(manifest, root, limit=max_artifacts),
        "gate_snapshot": _gate_snapshot(manifest, root),
        "notes": [
            "This dashboard is a static local review artifact, not a server or render queue.",
            "Open the HTML file in a browser before approving render or publish handoff.",
        ],
    }


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _file_href(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().as_uri()
    except ValueError:
        return ""


def _path_cell(path: str, label: str) -> str:
    if not path:
        return "-"
    href = _file_href(path)
    text = _esc(label or path)
    if href:
        return f'<a href="{_esc(href)}">{text}</a>'
    return f"<code>{text}</code>"


def emit_html(packet: Mapping[str, Any]) -> str:
    summary = packet.get("summary") if isinstance(packet.get("summary"), Mapping) else {}
    review_items = [item for item in packet.get("review_items") or [] if isinstance(item, Mapping)]
    artifacts = [item for item in packet.get("latest_artifacts") or [] if isinstance(item, Mapping)]
    gates = [item for item in packet.get("gate_snapshot") or [] if isinstance(item, Mapping)]
    actions = [str(item) for item in packet.get("next_actions") or []]

    def metric(label: str, value: Any) -> str:
        return f"<div class=\"metric\"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></div>"

    review_rows = []
    for item in review_items:
        notes = "; ".join(str(note) for note in item.get("notes") or []) or "-"
        path = _path_cell(str(item.get("latest_path") or ""), str(item.get("latest_relative_path") or ""))
        review_rows.append(
            "<tr>"
            f"<td><strong>{_esc(item.get('priority_label'))}</strong></td>"
            f"<td>{_esc(item.get('category'))}</td>"
            f"<td><span class=\"status { _esc(item.get('status')) }\">{_esc(item.get('status'))}</span></td>"
            f"<td>{path}</td>"
            f"<td>{_esc(notes)}</td>"
            f"<td>{_esc(item.get('next_action') or '-')}</td>"
            "</tr>"
        )
    if not review_rows:
        review_rows.append("<tr><td colspan=\"6\">No blocking or warning gates found.</td></tr>")

    artifact_rows = []
    for item in artifacts:
        path = _path_cell(str(item.get("path") or ""), str(item.get("relative_path") or ""))
        artifact_rows.append(
            "<tr>"
            f"<td>{_esc(item.get('category'))}</td>"
            f"<td>{path}</td>"
            f"<td>{_esc(item.get('modified_at'))}</td>"
            f"<td>{_esc(item.get('size_bytes'))}</td>"
            "</tr>"
        )
    if not artifact_rows:
        artifact_rows.append("<tr><td colspan=\"4\">No artifacts found.</td></tr>")

    gate_rows = []
    for gate in gates:
        path = _path_cell(str(gate.get("latest_path") or ""), str(gate.get("latest_relative_path") or ""))
        notes = "; ".join(str(note) for note in gate.get("notes") or []) or "-"
        gate_rows.append(
            "<tr>"
            f"<td>{_esc(gate.get('category'))}</td>"
            f"<td>{'yes' if gate.get('required') else 'no'}</td>"
            f"<td><span class=\"status { _esc(gate.get('status')) }\">{_esc(gate.get('status'))}</span></td>"
            f"<td>{_esc(gate.get('artifact_count'))}</td>"
            f"<td>{path}</td>"
            f"<td>{_esc(notes)}</td>"
            "</tr>"
        )

    action_items = "".join(f"<li>{_esc(action)}</li>" for action in actions) or "<li>No next actions.</li>"
    status = _esc(packet.get("status"))
    review_state = _esc(packet.get("review_state"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Review Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --ink: #1f2328;
      --muted: #667085;
      --line: #d7d9d0;
      --panel: #ffffff;
      --bad: #b42318;
      --warn: #b54708;
      --good: #027a48;
      --blue: #175cd3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 48px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 28px 0 10px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .subtle {{ color: var(--muted); }}
    .badge {{
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 10px;
      background: var(--panel);
      font-weight: 650;
      white-space: nowrap;
    }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin: 20px 0; }}
    .metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; font-size: 20px; margin-top: 4px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ font-size: 12px; color: var(--muted); background: #fbfbf9; }}
    tr:last-child td {{ border-bottom: 0; }}
    ul {{ margin: 0; padding: 12px 28px 14px; }}
    .status {{ font-weight: 650; }}
    .status.blocked, .status.missing {{ color: var(--bad); }}
    .status.warn {{ color: var(--warn); }}
    .status.ready {{ color: var(--good); }}
    @media (max-width: 760px) {{
      header {{ display: block; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel {{ overflow-x: auto; }}
      table {{ min-width: 760px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Review Dashboard</h1>
        <div class="subtle"><code>{_esc(packet.get('project_dir'))}</code></div>
        <div class="subtle">Generated {_esc(packet.get('generated_at'))} for target stage <code>{_esc(packet.get('target_stage'))}</code></div>
      </div>
      <div class="badge">{status} / {review_state}</div>
    </header>

    <section class="metrics">
      {metric("Required", summary.get("required", 0))}
      {metric("Required ready", summary.get("required_ready", 0))}
      {metric("Missing required", summary.get("missing_required", 0))}
      {metric("Blocked gates", summary.get("blocked_gates", 0))}
      {metric("Warnings", summary.get("warn_gates", 0))}
      {metric("Artifacts", summary.get("artifact_count", 0))}
    </section>

    <h2>Review Queue</h2>
    <div class="panel">
      <table>
        <thead><tr><th>priority</th><th>category</th><th>status</th><th>latest</th><th>notes</th><th>next action</th></tr></thead>
        <tbody>
          {''.join(review_rows)}
        </tbody>
      </table>
    </div>

    <h2>Next Actions</h2>
    <div class="panel"><ul>{action_items}</ul></div>

    <h2>Latest Artifacts</h2>
    <div class="panel">
      <table>
        <thead><tr><th>category</th><th>path</th><th>modified</th><th>bytes</th></tr></thead>
        <tbody>
          {''.join(artifact_rows)}
        </tbody>
      </table>
    </div>

    <h2>Gate Snapshot</h2>
    <div class="panel">
      <table>
        <thead><tr><th>category</th><th>required</th><th>status</th><th>artifacts</th><th>latest</th><th>notes</th></tr></thead>
        <tbody>
          {''.join(gate_rows)}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a static HTML/JSON review dashboard from local video pipeline artifacts."
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder to scan.")
    parser.add_argument("--output", default="review_dashboard.json", help="Output JSON path.")
    parser.add_argument("--html", default="review_dashboard.html", help="Output static HTML dashboard path.")
    parser.add_argument(
        "--target-stage",
        default="publish_ready",
        choices=sorted(STAGE_REQUIREMENTS),
        help="Readiness stage to evaluate.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=sorted(ARTIFACT_BY_CATEGORY),
        help="Additional artifact category required for this review; can repeat.",
    )
    parser.add_argument("--hash", action="store_true", help="Include SHA-256 for matched files in JSON.")
    parser.add_argument("--max-artifacts", type=int, default=24, help="Maximum latest artifacts to list.")
    parser.add_argument("--max-actions", type=int, default=16, help="Maximum review/next-action rows to list.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory name to exclude while scanning; can repeat.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when status is blocked.")
    parser.add_argument("--fail-on-warn", action="store_true", help="With --strict, also exit 2 on warnings.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    packet = build_dashboard_packet(
        args.project_dir,
        target_stage=args.target_stage,
        required=args.require,
        include_hash=args.hash,
        excludes=excludes,
        max_artifacts=args.max_artifacts,
        max_actions=args.max_actions,
    )
    write_json(args.output, packet)
    if args.html:
        write_text(args.html, emit_html(packet))

    print(
        "Review dashboard: {status}, {items} review item(s), wrote {json_path}{html_path}".format(
            status=packet["status"],
            items=len(packet.get("review_items") or []),
            json_path=args.output,
            html_path=f" and {args.html}" if args.html else "",
        )
    )

    if args.strict and (
        packet.get("status") == "blocked" or (args.fail_on_warn and packet.get("status") == "warn")
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
