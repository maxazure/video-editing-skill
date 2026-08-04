#!/usr/bin/env python3
"""Create source-bound, reversible revisions for text editing artifacts.

The tool deliberately manages plans and sidecars, not source media or rendered
video. A proposal is audited against live SHA-256 fingerprints, approved in a
separate file, committed as one recoverable operation, and can then be undone
or redone while the journal still matches disk state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PROPOSAL_VERSION = "edit_revision_proposal.v1"
AUDIT_VERSION = "edit_revision_audit.v1"
APPROVAL_VERSION = "edit_revision_approval.v1"
HISTORY_VERSION = "edit_revision_history.v1"
VERIFICATION_VERSION = "edit_revision_verification.v1"

MAX_ARTIFACTS = 16
MAX_DEPENDENCIES = 32
MAX_TEXT_BYTES = 2 * 1024 * 1024
ALLOWED_SUFFIXES = {".json", ".md", ".txt", ".srt", ".vtt", ".ass"}
BLOCKED_WRITE_ROOTS = {
    ".git",
    ".venv",
    "docs",
    "media",
    "node_modules",
    "origin",
    "output",
    "remotion-standup",
    "research-archive",
    "scripts",
    "tests",
    "verify",
}
BLOCKED_READ_PARTS = {".git", ".venv", "node_modules", "research-archive", "__pycache__"}
VOLATILE_NAME_PARTS = {
    "approval_receipt",
    "edit_revision_audit",
    "edit_revision_history",
    "edit_revision_proposal",
    "pipeline_manifest",
    "publish_package",
    "review_dashboard",
}
BLOCKED_PROJECT_FILES = {"AGENTS.md", "CLAUDE.md", "README.md", "REMOTION_VOICEOVER.md", "SKILL.md"}


class RevisionError(ValueError):
    """Raised when a revision operation cannot be completed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevisionError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RevisionError(f"JSON root must be an object: {path}")
    return value


def write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path).expanduser()
    if target.is_symlink():
        raise RevisionError(f"refusing to replace symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write_batch([(target, payload, target.read_bytes() if target.exists() else None)])


def write_text(path: str | Path, value: str) -> None:
    target = Path(path).expanduser()
    if target.is_symlink():
        raise RevisionError(f"refusing to replace symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = value.encode("utf-8")
    _atomic_write_batch([(target, payload, target.read_bytes() if target.exists() else None)])


def _project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise RevisionError(f"project directory not found: {project_dir}")
    return root


def _candidate_path(root: Path, raw_path: str | Path) -> Tuple[Path, Path]:
    raw = Path(raw_path).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    if candidate.is_symlink():
        raise RevisionError(f"symlink paths are not allowed: {raw_path}")
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RevisionError(f"path escapes project root: {raw_path}") from exc
    if not relative.parts:
        raise RevisionError("project root is not an artifact path")
    return resolved, relative


def _check_hidden_or_internal(relative: Path) -> None:
    for part in relative.parts:
        if part in BLOCKED_READ_PARTS or part.startswith("."):
            raise RevisionError(f"hidden/internal paths are not allowed: {relative.as_posix()}")
    lowered = relative.name.lower()
    if any(token in lowered for token in VOLATILE_NAME_PARTS):
        raise RevisionError(f"revision control artifacts cannot manage themselves: {relative.as_posix()}")


def safe_read_path(root: Path, raw_path: str | Path) -> Tuple[Path, str]:
    resolved, relative = _candidate_path(root, raw_path)
    _check_hidden_or_internal(relative)
    if not resolved.exists() or not resolved.is_file():
        raise RevisionError(f"dependency is not a regular file: {relative.as_posix()}")
    return resolved, relative.as_posix()


def safe_artifact_path(root: Path, raw_path: str | Path) -> Tuple[Path, str]:
    resolved, relative = _candidate_path(root, raw_path)
    _check_hidden_or_internal(relative)
    if relative.parts[0] in BLOCKED_WRITE_ROOTS:
        raise RevisionError(f"source, code, output, and verification folders are read-only: {relative.as_posix()}")
    if len(relative.parts) == 1 and relative.name in BLOCKED_PROJECT_FILES:
        raise RevisionError(f"project instruction/document files are read-only: {relative.as_posix()}")
    if len(relative.parts) > 1 and relative.parts[0] != "work":
        raise RevisionError(f"managed artifacts must be in work/ or project root: {relative.as_posix()}")
    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise RevisionError(f"unsupported managed artifact type: {relative.as_posix()}")
    if not resolved.exists() or not resolved.is_file():
        raise RevisionError(f"managed artifact is not a regular file: {relative.as_posix()}")
    size = resolved.stat().st_size
    if size > MAX_TEXT_BYTES:
        raise RevisionError(f"managed artifact exceeds {MAX_TEXT_BYTES} bytes: {relative.as_posix()}")
    return resolved, relative.as_posix()


def safe_journal_path(root: Path, raw_path: str | Path) -> Tuple[Path, str]:
    resolved, relative = _candidate_path(root, raw_path)
    if any(part in BLOCKED_READ_PARTS for part in relative.parts):
        raise RevisionError(f"journal path is internal or excluded: {relative.as_posix()}")
    if len(relative.parts) > 1 and relative.parts[0] != "work":
        raise RevisionError("journal must be in work/ or project root")
    if resolved.exists() and (resolved.is_symlink() or not resolved.is_file()):
        raise RevisionError(f"journal is not a regular file: {relative.as_posix()}")
    return resolved, relative.as_posix()


def _file_state(path: Path, relative: str) -> Dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": relative,
        "exists": True,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _read_text_artifact(path: Path, relative: str) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_TEXT_BYTES:
        raise RevisionError(f"managed artifact exceeds {MAX_TEXT_BYTES} bytes: {relative}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RevisionError(f"managed artifact is not UTF-8 text: {relative}") from exc


def prepare_proposal(
    project_dir: str,
    artifacts: Sequence[str],
    *,
    dependencies: Optional[Sequence[str]] = None,
    title: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    root = _project_root(project_dir)
    if not artifacts:
        raise RevisionError("at least one --artifact is required")
    if len(artifacts) > MAX_ARTIFACTS:
        raise RevisionError(f"at most {MAX_ARTIFACTS} managed artifacts are allowed")
    if len(dependencies or []) > MAX_DEPENDENCIES:
        raise RevisionError(f"at most {MAX_DEPENDENCIES} dependencies are allowed")

    managed: List[Dict[str, Any]] = []
    managed_paths: set[str] = set()
    for raw_path in artifacts:
        path, relative = safe_artifact_path(root, raw_path)
        if relative in managed_paths:
            raise RevisionError(f"duplicate managed artifact: {relative}")
        managed_paths.add(relative)
        state = _file_state(path, relative)
        managed.append(
            {
                "path": relative,
                "base": state,
                "proposed_content": _read_text_artifact(path, relative),
            }
        )

    based_on: List[Dict[str, Any]] = []
    dependency_paths: set[str] = set()
    for raw_path in dependencies or []:
        path, relative = safe_read_path(root, raw_path)
        if relative in managed_paths:
            raise RevisionError(f"artifact cannot also be its own dependency: {relative}")
        if relative in dependency_paths:
            raise RevisionError(f"duplicate dependency: {relative}")
        dependency_paths.add(relative)
        based_on.append(_file_state(path, relative))

    return {
        "version": PROPOSAL_VERSION,
        "created_at": utc_now(),
        "title": title.strip(),
        "reason": reason.strip(),
        "instructions": [
            "Edit only artifacts[].proposed_content; do not change path or base fingerprints.",
            "Fill title and reason before audit. JSON proposed_content must remain valid JSON.",
            "Audit and approval do not authenticate approved_by_label; keep the approval file as review evidence.",
        ],
        "artifacts": managed,
        "dependencies": based_on,
    }


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _review_identity(
    *,
    title: str,
    reason: str,
    artifacts: Sequence[Mapping[str, Any]],
    dependencies: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    return {
        "title": title,
        "reason": reason,
        "artifacts": [
            {
                "path": item.get("path"),
                "before_sha256": item.get("before_sha256"),
                "after_sha256": item.get("after_sha256"),
            }
            for item in artifacts
        ],
        "dependencies": [
            {"path": item.get("path"), "sha256": item.get("sha256")}
            for item in dependencies
        ],
    }


def audit_proposal(project_dir: str, proposal: Mapping[str, Any]) -> Dict[str, Any]:
    root = _project_root(project_dir)
    issues: List[str] = []
    title = str(proposal.get("title") or "").strip()
    reason = str(proposal.get("reason") or "").strip()
    if proposal.get("version") != PROPOSAL_VERSION:
        issues.append(f"unsupported proposal version: {proposal.get('version')!r}")
    if not title:
        issues.append("title must not be empty")
    if not reason:
        issues.append("reason must not be empty")

    raw_artifacts = proposal.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        issues.append("artifacts must be a non-empty array")
        raw_artifacts = []
    if len(raw_artifacts) > MAX_ARTIFACTS:
        issues.append(f"artifacts exceeds maximum count {MAX_ARTIFACTS}")

    normalized_artifacts: List[Dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    for index, raw_item in enumerate(raw_artifacts):
        item_issues: List[str] = []
        if not isinstance(raw_item, Mapping):
            normalized_artifacts.append({"path": f"invalid-{index + 1}", "issues": ["artifact must be an object"]})
            continue
        raw_path = raw_item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            normalized_artifacts.append({"path": f"invalid-{index + 1}", "issues": ["artifact path is required"]})
            continue
        try:
            path, relative = safe_artifact_path(root, raw_path)
        except RevisionError as exc:
            normalized_artifacts.append({"path": raw_path, "issues": [str(exc)]})
            continue
        if relative in seen_artifacts:
            item_issues.append("duplicate artifact path")
        seen_artifacts.add(relative)

        base = raw_item.get("base")
        base = base if isinstance(base, Mapping) else {}
        current = _file_state(path, relative)
        base_hash = base.get("sha256")
        if not _valid_sha(base_hash):
            item_issues.append("base.sha256 must be a lowercase SHA-256")
        elif base_hash != current["sha256"]:
            item_issues.append("base artifact changed after proposal preparation")
        if base.get("exists") is not True:
            item_issues.append("base.exists must be true")
        if base.get("size_bytes") != current["size_bytes"]:
            item_issues.append("base.size_bytes does not match current artifact")

        content = raw_item.get("proposed_content")
        if not isinstance(content, str):
            item_issues.append("proposed_content must be a UTF-8 string")
            content = ""
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            item_issues.append(f"proposed_content exceeds {MAX_TEXT_BYTES} bytes")
        if path.suffix.lower() == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                item_issues.append(f"proposed JSON is invalid: line {exc.lineno} column {exc.colno}")
        after_hash = sha256_bytes(encoded)
        if after_hash == current["sha256"]:
            item_issues.append("proposed_content does not change the artifact")

        normalized_artifacts.append(
            {
                "path": relative,
                "before_sha256": current["sha256"],
                "before_size_bytes": current["size_bytes"],
                "after_sha256": after_hash,
                "after_size_bytes": len(encoded),
                "issues": item_issues,
            }
        )

    raw_dependencies = proposal.get("dependencies")
    if raw_dependencies is None:
        raw_dependencies = []
    if not isinstance(raw_dependencies, list):
        issues.append("dependencies must be an array")
        raw_dependencies = []
    if len(raw_dependencies) > MAX_DEPENDENCIES:
        issues.append(f"dependencies exceeds maximum count {MAX_DEPENDENCIES}")

    normalized_dependencies: List[Dict[str, Any]] = []
    seen_dependencies: set[str] = set()
    for index, raw_item in enumerate(raw_dependencies):
        item_issues: List[str] = []
        if not isinstance(raw_item, Mapping):
            normalized_dependencies.append({"path": f"invalid-{index + 1}", "issues": ["dependency must be an object"]})
            continue
        raw_path = raw_item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            normalized_dependencies.append({"path": f"invalid-{index + 1}", "issues": ["dependency path is required"]})
            continue
        try:
            path, relative = safe_read_path(root, raw_path)
        except RevisionError as exc:
            normalized_dependencies.append({"path": raw_path, "issues": [str(exc)]})
            continue
        if relative in seen_dependencies:
            item_issues.append("duplicate dependency path")
        if relative in seen_artifacts:
            item_issues.append("managed artifact cannot also be a dependency")
        seen_dependencies.add(relative)
        current = _file_state(path, relative)
        expected_hash = raw_item.get("sha256")
        if not _valid_sha(expected_hash):
            item_issues.append("dependency sha256 must be a lowercase SHA-256")
        elif expected_hash != current["sha256"]:
            item_issues.append("dependency changed after proposal preparation")
        if raw_item.get("size_bytes") != current["size_bytes"]:
            item_issues.append("dependency size_bytes does not match current file")
        normalized_dependencies.append({**current, "issues": item_issues})

    item_issues = [issue for item in [*normalized_artifacts, *normalized_dependencies] for issue in item.get("issues", [])]
    issues.extend(item_issues)
    identity = _review_identity(
        title=title,
        reason=reason,
        artifacts=normalized_artifacts,
        dependencies=normalized_dependencies,
    )
    review_id = f"revision-{sha256_bytes(canonical_json(identity))[:16]}"
    status = "blocked" if issues else "pending_approval"
    blocking = len(issues) if issues else 1
    return {
        "version": AUDIT_VERSION,
        "created_at": utc_now(),
        "status": status,
        "review_id": review_id,
        "title": title,
        "reason": reason,
        "artifacts": normalized_artifacts,
        "dependencies": normalized_dependencies,
        "issues": sorted(set(issues)),
        "approval_template": {
            "version": APPROVAL_VERSION,
            "review_id": review_id,
            "decision": "approve|reject",
            "approved_by_label": "",
        },
        "summary": {
            "artifacts": len(normalized_artifacts),
            "dependencies": len(normalized_dependencies),
            "issues": len(set(issues)),
            "pending_approval": 1 if not issues else 0,
            "blocking": blocking,
        },
    }


def emit_audit_markdown(audit: Mapping[str, Any]) -> str:
    summary = audit.get("summary") if isinstance(audit.get("summary"), Mapping) else {}
    lines = [
        "# Edit Revision Audit",
        "",
        f"- Status: **{audit.get('status', 'blocked')}**",
        f"- Review ID: `{audit.get('review_id', '')}`",
        f"- Title: {audit.get('title', '')}",
        f"- Reason: {audit.get('reason', '')}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "| artifact | before | after | status |",
        "|---|---|---|---|",
    ]
    for item in audit.get("artifacts", []):
        if not isinstance(item, Mapping):
            continue
        before = str(item.get("before_sha256") or "")[:12]
        after = str(item.get("after_sha256") or "")[:12]
        status = "; ".join(str(value) for value in item.get("issues", [])) or "ready for approval"
        lines.append(f"| `{item.get('path', '')}` | `{before}` | `{after}` | {status} |")
    if audit.get("dependencies"):
        lines.extend(["", "## Based-on dependencies", ""])
        for item in audit.get("dependencies", []):
            if not isinstance(item, Mapping):
                continue
            status = "; ".join(str(value) for value in item.get("issues", [])) or "current"
            lines.append(f"- `{item.get('path', '')}` `{str(item.get('sha256') or '')[:12]}` — {status}")
    if audit.get("issues"):
        lines.extend(["", "## Blocking issues", ""])
        lines.extend(f"- {issue}" for issue in audit.get("issues", []))
    else:
        template = audit.get("approval_template", {})
        lines.extend(
            [
                "",
                "## Approval required",
                "",
                "Review the complete proposed contents, then save a separate approval JSON bound to this review ID:",
                "",
                "```json",
                json.dumps(template, ensure_ascii=False, indent=2),
                "```",
                "",
                "`approved_by_label` is a local review label, not identity authentication or a digital signature.",
            ]
        )
    return "\n".join(lines) + "\n"


def _new_history(journal_relative: str) -> Dict[str, Any]:
    blob_dir = Path(journal_relative).parent / ".edit-revisions" / "blobs"
    return {
        "version": HISTORY_VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "journal_path": journal_relative,
        "blob_store": blob_dir.as_posix(),
        "cursor": 0,
        "operations": [],
        "summary": {"operations": 0, "applied": 0, "redo_available": 0, "blocking": 0},
    }


def _load_or_new_history(journal_path: Path, journal_relative: str) -> Dict[str, Any]:
    if not journal_path.exists():
        return _new_history(journal_relative)
    return load_json(journal_path)


def _history_summary(history: Mapping[str, Any], *, blocking: int = 0) -> Dict[str, int]:
    operations = history.get("operations") if isinstance(history.get("operations"), list) else []
    try:
        cursor = int(history.get("cursor", 0))
    except (TypeError, ValueError):
        cursor = 0
    cursor = max(0, min(cursor, len(operations)))
    return {
        "operations": len(operations),
        "applied": cursor,
        "redo_available": len(operations) - cursor,
        "blocking": blocking,
    }


def _expected_artifacts(history: Mapping[str, Any], cursor: int) -> Dict[str, str]:
    operations = history.get("operations") if isinstance(history.get("operations"), list) else []
    paths: set[str] = set()
    for operation in operations:
        if isinstance(operation, Mapping):
            for item in operation.get("artifacts", []):
                if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                    paths.add(str(item["path"]))
    expected: Dict[str, str] = {}
    for path in paths:
        for operation in reversed(operations[:cursor]):
            if not isinstance(operation, Mapping):
                continue
            match = next(
                (item for item in operation.get("artifacts", []) if isinstance(item, Mapping) and item.get("path") == path),
                None,
            )
            if match is not None:
                expected[path] = str(match.get("after_sha256") or "")
                break
        if path in expected:
            continue
        for operation in operations[cursor:]:
            if not isinstance(operation, Mapping):
                continue
            match = next(
                (item for item in operation.get("artifacts", []) if isinstance(item, Mapping) and item.get("path") == path),
                None,
            )
            if match is not None:
                expected[path] = str(match.get("before_sha256") or "")
                break
    return expected


def _safe_blob_path(root: Path, blob_store: str, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise RevisionError("blob path is missing")
    path, relative = _candidate_path(root, raw_path)
    store = Path(blob_store)
    try:
        Path(relative).relative_to(store)
    except ValueError as exc:
        raise RevisionError(f"blob path escapes configured store: {relative.as_posix()}") from exc
    if path.is_symlink():
        raise RevisionError(f"blob path is a symlink: {relative.as_posix()}")
    return path


def verify_history(history: Mapping[str, Any], project_dir: str) -> Dict[str, Any]:
    root = _project_root(project_dir)
    issues: List[str] = []
    if history.get("version") != HISTORY_VERSION:
        issues.append(f"unsupported history version: {history.get('version')!r}")
    operations = history.get("operations")
    if not isinstance(operations, list):
        operations = []
        issues.append("operations must be an array")
    try:
        cursor = int(history.get("cursor"))
    except (TypeError, ValueError):
        cursor = -1
        issues.append("cursor must be an integer")
    if cursor < 0 or cursor > len(operations):
        issues.append("cursor is outside operation history")
        cursor = max(0, min(cursor, len(operations)))
    blob_store = history.get("blob_store")
    if not isinstance(blob_store, str) or not blob_store:
        issues.append("blob_store must be a project-relative path")
        blob_store = "work/.edit-revisions/blobs"
    journal_path = history.get("journal_path")
    if not isinstance(journal_path, str) or not journal_path:
        issues.append("journal_path must be a project-relative path")
    else:
        try:
            _, normalized_journal = safe_journal_path(root, journal_path)
        except RevisionError as exc:
            issues.append(str(exc))
        else:
            expected_blob_store = (Path(normalized_journal).parent / ".edit-revisions" / "blobs").as_posix()
            if blob_store != expected_blob_store:
                issues.append("blob_store does not match journal_path")

    operation_ids: set[str] = set()
    blobs: List[Dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            issues.append(f"operation {index + 1} must be an object")
            continue
        if operation.get("revision") != index + 1:
            issues.append(f"operation {index + 1} has invalid revision")
        if operation.get("parent_revision") != index:
            issues.append(f"operation {index + 1} has invalid parent_revision")
        operation_id = operation.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id or operation_id in operation_ids:
            issues.append(f"operation {index + 1} has missing or duplicate operation_id")
        else:
            operation_ids.add(operation_id)
        artifacts = operation.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            issues.append(f"operation {index + 1} has no artifacts")
            continue
        seen: set[str] = set()
        for item in artifacts:
            if not isinstance(item, Mapping):
                issues.append(f"operation {index + 1} contains an invalid artifact")
                continue
            relative = item.get("path")
            if not isinstance(relative, str) or not relative or relative in seen:
                issues.append(f"operation {index + 1} contains a missing or duplicate artifact path")
                continue
            seen.add(relative)
            for side in ("before", "after"):
                expected_hash = item.get(f"{side}_sha256")
                if not _valid_sha(expected_hash):
                    issues.append(f"operation {index + 1} {relative} has invalid {side} hash")
                    continue
                try:
                    blob_path = _safe_blob_path(root, blob_store, item.get(f"{side}_blob"))
                except RevisionError as exc:
                    issues.append(str(exc))
                    continue
                if not blob_path.is_file():
                    status = "missing"
                else:
                    status = "current" if sha256_file(blob_path) == expected_hash else "changed"
                blobs.append({"path": str(item.get(f"{side}_blob") or ""), "status": status})
                if status != "current":
                    issues.append(f"{side} blob is {status}: {relative}")

    artifact_states: List[Dict[str, Any]] = []
    for relative, expected_hash in sorted(_expected_artifacts(history, cursor).items()):
        try:
            path, normalized = safe_artifact_path(root, relative)
        except RevisionError as exc:
            artifact_states.append({"path": relative, "status": "unsafe", "issue": str(exc)})
            continue
        current_hash = sha256_file(path)
        status = "current" if current_hash == expected_hash else "changed"
        artifact_states.append(
            {
                "path": normalized,
                "status": status,
                "expected_sha256": expected_hash,
                "current_sha256": current_hash,
            }
        )
        if status != "current":
            issues.append(f"managed artifact changed outside revision history: {normalized}")

    dependency_states: List[Dict[str, Any]] = []
    for operation in operations[:cursor]:
        if not isinstance(operation, Mapping):
            continue
        for item in operation.get("dependencies", []):
            if not isinstance(item, Mapping):
                continue
            relative = item.get("path")
            expected_hash = item.get("sha256")
            if not isinstance(relative, str) or not _valid_sha(expected_hash):
                issues.append("applied operation contains an invalid dependency fingerprint")
                continue
            try:
                path, normalized = safe_read_path(root, relative)
            except RevisionError as exc:
                dependency_states.append({"path": relative, "status": "unsafe", "issue": str(exc)})
                issues.append(f"dependency is unavailable: {relative}")
                continue
            current_hash = sha256_file(path)
            status = "current" if current_hash == expected_hash else "changed"
            dependency_states.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "path": normalized,
                    "status": status,
                    "expected_sha256": expected_hash,
                    "current_sha256": current_hash,
                }
            )
            if status != "current":
                issues.append(f"applied revision dependency changed: {normalized}")

    unique_issues = sorted(set(issues))
    summary = _history_summary(history, blocking=len(unique_issues))
    return {
        "version": VERIFICATION_VERSION,
        "created_at": utc_now(),
        "status": "current" if not unique_issues else "stale",
        "cursor": cursor,
        "artifacts": artifact_states,
        "dependencies": dependency_states,
        "blobs": blobs,
        "issues": unique_issues,
        "summary": summary,
    }


def emit_history_markdown(verification: Mapping[str, Any], history: Mapping[str, Any]) -> str:
    summary = verification.get("summary") if isinstance(verification.get("summary"), Mapping) else {}
    lines = [
        "# Edit Revision History",
        "",
        f"- Status: **{verification.get('status', 'stale')}**",
        f"- Applied revisions: {summary.get('applied', 0)} / {summary.get('operations', 0)}",
        f"- Redo available: {summary.get('redo_available', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "| revision | operation | title | state | artifacts |",
        "|---:|---|---|---|---:|",
    ]
    cursor = int(verification.get("cursor", 0) or 0)
    for index, operation in enumerate(history.get("operations", [])):
        if not isinstance(operation, Mapping):
            continue
        state = "applied" if index < cursor else "redo"
        lines.append(
            f"| {operation.get('revision', index + 1)} | `{operation.get('operation_id', '')}` | "
            f"{operation.get('title', '')} | {state} | {len(operation.get('artifacts', []))} |"
        )
    if verification.get("issues"):
        lines.extend(["", "## Blocking issues", ""])
        lines.extend(f"- {issue}" for issue in verification.get("issues", []))
    return "\n".join(lines) + "\n"


def _atomic_write_batch(writes: Sequence[Tuple[Path, bytes, Optional[bytes]]]) -> None:
    staged: List[Tuple[Path, Path, Optional[bytes]]] = []
    replaced: List[Tuple[Path, Optional[bytes]]] = []
    try:
        for target, payload, old_payload in writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.is_symlink():
                raise RevisionError(f"refusing to replace symlink: {target}")
            with tempfile.NamedTemporaryFile(prefix=".edit-revision-", dir=target.parent, delete=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                staged.append((target, Path(handle.name), old_payload))
        for target, _, old_payload in staged:
            current = target.read_bytes() if target.exists() else None
            if current != old_payload:
                raise RevisionError(f"target changed before grouped write: {target}")
        for target, staged_path, old_payload in staged:
            os.replace(staged_path, target)
            replaced.append((target, old_payload))
    except Exception:
        for target, old_payload in reversed(replaced):
            if old_payload is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                with tempfile.NamedTemporaryFile(prefix=".edit-revision-rollback-", dir=target.parent, delete=False) as handle:
                    handle.write(old_payload)
                    rollback_path = Path(handle.name)
                os.replace(rollback_path, target)
        raise
    finally:
        for _, staged_path, _ in staged:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass


def _blob_path(root: Path, blob_store: str, digest: str) -> Tuple[Path, str]:
    relative = (Path(blob_store) / f"{digest}.blob").as_posix()
    return _safe_blob_path(root, blob_store, relative), relative


def _store_blob(root: Path, blob_store: str, payload: bytes) -> str:
    digest = sha256_bytes(payload)
    path, relative = _blob_path(root, blob_store, digest)
    if path.exists():
        if not path.is_file() or sha256_file(path) != digest:
            raise RevisionError(f"content-addressed blob is invalid: {relative}")
        return relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_batch([(path, payload, None)])
    return relative


def _load_blob(root: Path, blob_store: str, relative: Any, expected_hash: Any) -> bytes:
    if not _valid_sha(expected_hash):
        raise RevisionError("operation contains an invalid blob hash")
    path = _safe_blob_path(root, blob_store, relative)
    if not path.is_file():
        raise RevisionError(f"revision blob is missing: {relative}")
    payload = path.read_bytes()
    if sha256_bytes(payload) != expected_hash:
        raise RevisionError(f"revision blob hash mismatch: {relative}")
    return payload


def _validate_live_audit(
    project_dir: str,
    proposal: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> Dict[str, Any]:
    live = audit_proposal(project_dir, proposal)
    if live.get("status") != "pending_approval" or live.get("issues"):
        raise RevisionError("proposal is blocked or became stale; run audit again")
    if audit.get("version") != AUDIT_VERSION:
        raise RevisionError("audit has an unsupported version")
    if audit.get("review_id") != live.get("review_id"):
        raise RevisionError("audit review_id does not match the live proposal")
    return live


def _approval_label(approval: Mapping[str, Any], review_id: str) -> str:
    if approval.get("version") != APPROVAL_VERSION:
        raise RevisionError("approval has an unsupported version")
    if approval.get("review_id") != review_id:
        raise RevisionError("approval is bound to a different review_id")
    decision = str(approval.get("decision") or "").strip().lower()
    if decision != "approve":
        raise RevisionError("approval decision must be approve")
    label = str(approval.get("approved_by_label") or "").strip()
    if not label:
        raise RevisionError("approved_by_label must not be empty")
    return label


def apply_revision(
    project_dir: str,
    proposal: Mapping[str, Any],
    audit: Mapping[str, Any],
    approval: Mapping[str, Any],
    *,
    journal: str = "work/edit_revision_history.json",
    fork_history: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    root = _project_root(project_dir)
    journal_path, journal_relative = safe_journal_path(root, journal)
    live_audit = _validate_live_audit(str(root), proposal, audit)
    approved_by = _approval_label(approval, str(live_audit["review_id"]))
    history = _load_or_new_history(journal_path, journal_relative)
    if history.get("operations"):
        verification = verify_history(history, str(root))
        if verification.get("status") != "current":
            raise RevisionError("revision history is stale; resolve status blockers before applying")
    if history.get("version") != HISTORY_VERSION:
        raise RevisionError("journal has an unsupported history version")
    operations = history.get("operations")
    if not isinstance(operations, list):
        raise RevisionError("journal operations must be an array")
    cursor = history.get("cursor")
    if not isinstance(cursor, int) or cursor < 0 or cursor > len(operations):
        raise RevisionError("journal cursor is invalid")
    archived_branches = history.get("archived_branches")
    if archived_branches is None:
        archived_branches = []
    if not isinstance(archived_branches, list):
        raise RevisionError("journal archived_branches must be an array")
    if cursor != len(operations):
        if not fork_history:
            raise RevisionError(
                "redo revisions are pending; redo them or explicitly use --fork-history to archive that redo branch"
            )
        archived_branches = [
            *archived_branches,
            {
                "archived_at": utc_now(),
                "base_revision": cursor,
                "operations": operations[cursor:],
            },
        ]
        operations = operations[:cursor]
    blob_store = history.get("blob_store")
    if not isinstance(blob_store, str) or not blob_store:
        raise RevisionError("journal blob_store is invalid")

    proposal_items = {
        str(item.get("path")): item
        for item in proposal.get("artifacts", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    operation_artifacts: List[Dict[str, Any]] = []
    writes: List[Tuple[Path, bytes, Optional[bytes]]] = []
    for audited in live_audit.get("artifacts", []):
        relative = str(audited.get("path") or "")
        proposal_item = proposal_items.get(relative)
        if proposal_item is None:
            raise RevisionError(f"proposal content missing for audited artifact: {relative}")
        path, normalized = safe_artifact_path(root, relative)
        before = path.read_bytes()
        after = str(proposal_item.get("proposed_content")).encode("utf-8")
        if sha256_bytes(before) != audited.get("before_sha256") or sha256_bytes(after) != audited.get("after_sha256"):
            raise RevisionError(f"artifact hashes changed after audit: {normalized}")
        before_blob = _store_blob(root, blob_store, before)
        after_blob = _store_blob(root, blob_store, after)
        operation_artifacts.append(
            {
                "path": normalized,
                "before_sha256": sha256_bytes(before),
                "after_sha256": sha256_bytes(after),
                "before_blob": before_blob,
                "after_blob": after_blob,
            }
        )
        writes.append((path, after, before))

    revision = len(operations) + 1
    operation_id = f"edit-{revision:04d}-{str(live_audit['review_id']).removeprefix('revision-')}"
    operation = {
        "operation_id": operation_id,
        "revision": revision,
        "parent_revision": cursor,
        "review_id": live_audit["review_id"],
        "title": live_audit["title"],
        "reason": live_audit["reason"],
        "approved_by_label": approved_by,
        "applied_at": utc_now(),
        "artifacts": operation_artifacts,
        "dependencies": [
            {"path": item.get("path"), "sha256": item.get("sha256"), "size_bytes": item.get("size_bytes")}
            for item in live_audit.get("dependencies", [])
        ],
    }
    updated = dict(history)
    updated_operations = [*operations, operation]
    updated.update(
        {
            "updated_at": utc_now(),
            "cursor": revision,
            "operations": updated_operations,
            "archived_branches": archived_branches,
        }
    )
    updated["summary"] = _history_summary(updated)
    journal_before = journal_path.read_bytes() if journal_path.exists() else None
    journal_after = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    writes.append((journal_path, journal_after, journal_before))
    _atomic_write_batch(writes)
    return updated, operation


def _operation_artifact_writes(
    root: Path,
    operation: Mapping[str, Any],
    *,
    blob_store: str,
    direction: str,
) -> List[Tuple[Path, bytes, Optional[bytes]]]:
    if direction not in {"before", "after"}:
        raise RevisionError(f"unknown revision direction: {direction}")
    expected_current = "after" if direction == "before" else "before"
    writes: List[Tuple[Path, bytes, Optional[bytes]]] = []
    for item in operation.get("artifacts", []):
        if not isinstance(item, Mapping):
            raise RevisionError("operation contains an invalid artifact")
        path, relative = safe_artifact_path(root, str(item.get("path") or ""))
        current = path.read_bytes()
        if sha256_bytes(current) != item.get(f"{expected_current}_sha256"):
            raise RevisionError(f"managed artifact changed outside history: {relative}")
        target = _load_blob(root, blob_store, item.get(f"{direction}_blob"), item.get(f"{direction}_sha256"))
        writes.append((path, target, current))
    return writes


def _verify_operation_dependencies(root: Path, operation: Mapping[str, Any]) -> None:
    for item in operation.get("dependencies", []):
        if not isinstance(item, Mapping):
            raise RevisionError("operation contains an invalid dependency")
        path, relative = safe_read_path(root, str(item.get("path") or ""))
        if sha256_file(path) != item.get("sha256"):
            raise RevisionError(f"cannot redo because dependency changed: {relative}")


def _move_cursor(project_dir: str, journal: str, *, redo: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    root = _project_root(project_dir)
    journal_path, journal_relative = safe_journal_path(root, journal)
    if not journal_path.exists():
        raise RevisionError(f"revision journal not found: {journal_relative}")
    history = load_json(journal_path)
    if history.get("version") != HISTORY_VERSION:
        raise RevisionError("journal has an unsupported history version")
    operations = history.get("operations")
    cursor = history.get("cursor")
    if not isinstance(operations, list) or not isinstance(cursor, int) or cursor < 0 or cursor > len(operations):
        raise RevisionError("journal operations or cursor are invalid")
    verification = verify_history(history, str(root))
    if redo:
        unsafe_issues = list(verification.get("issues", []))
    else:
        unsafe_issues = [
            issue
            for issue in verification.get("issues", [])
            if not str(issue).startswith("applied revision dependency changed:")
            and not str(issue).startswith("dependency is unavailable:")
        ]
    if unsafe_issues:
        raise RevisionError(f"revision history is stale: {unsafe_issues[0]}")
    if redo:
        if cursor >= len(operations):
            raise RevisionError("no revision is available to redo")
        operation = operations[cursor]
        _verify_operation_dependencies(root, operation)
        direction = "after"
        new_cursor = cursor + 1
    else:
        if cursor <= 0:
            raise RevisionError("no applied revision is available to undo")
        operation = operations[cursor - 1]
        direction = "before"
        new_cursor = cursor - 1
    blob_store = history.get("blob_store")
    if not isinstance(blob_store, str) or not blob_store:
        raise RevisionError("journal blob_store is invalid")
    writes = _operation_artifact_writes(root, operation, blob_store=blob_store, direction=direction)
    updated = dict(history)
    updated.update({"updated_at": utc_now(), "cursor": new_cursor})
    updated["summary"] = _history_summary(updated)
    journal_before = journal_path.read_bytes()
    journal_after = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    writes.append((journal_path, journal_after, journal_before))
    _atomic_write_batch(writes)
    return updated, dict(operation)


def undo_revision(project_dir: str, *, journal: str = "work/edit_revision_history.json") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return _move_cursor(project_dir, journal, redo=False)


def redo_revision(project_dir: str, *, journal: str = "work/edit_revision_history.json") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return _move_cursor(project_dir, journal, redo=True)


def _write_optional_result(
    result: Mapping[str, Any],
    *,
    output: Optional[str],
    markdown: Optional[str],
    markdown_text: str,
) -> None:
    if output:
        write_json(output, result)
    if markdown:
        write_text(markdown, markdown_text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit, apply as one recoverable operation, undo, and redo text editing artifact revisions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create a source-bound editable proposal.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--artifact", action="append", required=True)
    prepare.add_argument("--depends-on", action="append", default=[])
    prepare.add_argument("--title", default="")
    prepare.add_argument("--reason", default="")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--markdown")

    audit = subparsers.add_parser("audit", help="Validate proposal hashes and emit an approval template.")
    audit.add_argument("--project-dir", default=".")
    audit.add_argument("--proposal", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown")
    audit.add_argument("--strict", action="store_true")

    apply = subparsers.add_parser("apply", help="Apply an audited, separately approved revision.")
    apply.add_argument("--project-dir", default=".")
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--audit", required=True)
    apply.add_argument("--approval", required=True)
    apply.add_argument("--journal", default="work/edit_revision_history.json")
    apply.add_argument("--output")
    apply.add_argument("--markdown")
    apply.add_argument(
        "--fork-history",
        action="store_true",
        help="After undo, archive the pending redo branch before applying this new revision.",
    )
    apply.add_argument("--strict", action="store_true")

    for name in ("undo", "redo"):
        action = subparsers.add_parser(name, help=f"{name.title()} one complete revision operation.")
        action.add_argument("--project-dir", default=".")
        action.add_argument("--journal", default="work/edit_revision_history.json")
        action.add_argument("--output")
        action.add_argument("--markdown")
        action.add_argument("--strict", action="store_true")

    status = subparsers.add_parser("status", help="Verify journal, blobs, artifacts, and applied dependencies.")
    status.add_argument("--project-dir", default=".")
    status.add_argument("--journal", default="work/edit_revision_history.json")
    status.add_argument("--output")
    status.add_argument("--markdown")
    status.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            proposal = prepare_proposal(
                args.project_dir,
                args.artifact,
                dependencies=args.depends_on,
                title=args.title,
                reason=args.reason,
            )
            write_json(args.output, proposal)
            if args.markdown:
                lines = [
                    "# Edit Revision Proposal",
                    "",
                    f"- Title: {proposal.get('title') or '(fill before audit)'}",
                    f"- Reason: {proposal.get('reason') or '(fill before audit)'}",
                    "- Edit only `artifacts[].proposed_content`, then run `audit`.",
                    "",
                ]
                lines.extend(f"- `{item['path']}` `{item['base']['sha256'][:12]}`" for item in proposal["artifacts"])
                write_text(args.markdown, "\n".join(lines) + "\n")
            print(json.dumps({"status": "draft", "output": args.output, "artifacts": len(proposal["artifacts"])}, ensure_ascii=False))
            return 0

        if args.command == "audit":
            audit = audit_proposal(args.project_dir, load_json(args.proposal))
            write_json(args.output, audit)
            if args.markdown:
                write_text(args.markdown, emit_audit_markdown(audit))
            print(json.dumps(audit["summary"], ensure_ascii=False))
            return 2 if args.strict and audit["summary"]["blocking"] else 0

        if args.command == "apply":
            history, operation = apply_revision(
                args.project_dir,
                load_json(args.proposal),
                load_json(args.audit),
                load_json(args.approval),
                journal=args.journal,
                fork_history=args.fork_history,
            )
            verification = verify_history(history, args.project_dir)
            result = {"status": verification["status"], "operation": operation, "summary": verification["summary"]}
            _write_optional_result(
                result,
                output=args.output,
                markdown=args.markdown,
                markdown_text=emit_history_markdown(verification, history),
            )
            print(json.dumps(result["summary"], ensure_ascii=False))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        if args.command in {"undo", "redo"}:
            mover = redo_revision if args.command == "redo" else undo_revision
            history, operation = mover(args.project_dir, journal=args.journal)
            verification = verify_history(history, args.project_dir)
            result = {"status": verification["status"], "operation": operation, "summary": verification["summary"]}
            _write_optional_result(
                result,
                output=args.output,
                markdown=args.markdown,
                markdown_text=emit_history_markdown(verification, history),
            )
            print(json.dumps(result["summary"], ensure_ascii=False))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        root = _project_root(args.project_dir)
        journal_path, _ = safe_journal_path(root, args.journal)
        history = load_json(journal_path)
        verification = verify_history(history, args.project_dir)
        _write_optional_result(
            verification,
            output=args.output,
            markdown=args.markdown,
            markdown_text=emit_history_markdown(verification, history),
        )
        print(json.dumps(verification["summary"], ensure_ascii=False))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (RevisionError, OSError) as exc:
        print(f"edit_revision: {exc}", file=sys.stderr)
        return 2 if getattr(args, "strict", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
