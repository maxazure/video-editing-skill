#!/usr/bin/env python3
"""Create and verify local hash-bound approval receipts.

The receipt binds a human-reviewed delivery set to exact file bytes. It is a
local consistency check, not an authenticated identity or digital signature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "approval_receipt.v1"
VERIFICATION_VERSION = "approval_receipt_verification.v1"
VOLATILE_ARTIFACT_TOKENS = (
    "approval_receipt",
    "pipeline_manifest",
    "publish_package",
    "review_dashboard",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mtime_from_stat(stat_result: os.stat_result) -> str:
    return (
        datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_project(project_dir: str) -> Path:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    return root


def _within_project(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path must stay inside project directory: {path}") from exc


def resolve_output_path(project_dir: str, output: str) -> Path:
    root = _resolve_project(project_dir)
    candidate = Path(output).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        relative_parent = lexical.parent.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output path must stay inside project directory: {candidate}") from exc
    current = root
    for part in relative_parent.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"output path must not contain symlink components: {candidate}")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError(f"output path must not be a symlink: {candidate}")
    parent = lexical.parent.resolve()
    _within_project(parent, root)
    return parent / lexical.name


def _resolve_existing_artifact(raw_path: str, root: Path) -> Tuple[Path, str]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        relative_lexical = lexical.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"artifact path must stay inside project directory: {candidate}"
        ) from exc
    current = root
    for part in relative_lexical.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"artifact path must not contain symlink components: {candidate}")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"artifact does not exist or is unreadable: {candidate}") from exc
    relative_path = _within_project(resolved, root)
    if not resolved.is_file():
        raise ValueError(f"artifact must be a regular file: {resolved}")
    return resolved, relative_path


def _stable_sha256(path: Path) -> Tuple[str, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"artifact could not be opened without following symlinks: {path}") from exc
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"artifact must be a regular file: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise ValueError(f"artifact changed while hashing: {path}")
    return digest.hexdigest(), after


def _artifact_record(path: Path, relative_path: str) -> Dict[str, Any]:
    digest, stat_result = _stable_sha256(path)
    return {
        "path": relative_path,
        "sha256": digest,
        "size_bytes": stat_result.st_size,
        "modified_at": _mtime_from_stat(stat_result),
    }


def create_receipt(
    project_dir: str,
    artifacts: Sequence[str],
    *,
    approved_by: str,
    note: str = "",
    receipt_path: Optional[str] = None,
) -> Dict[str, Any]:
    root = _resolve_project(project_dir)
    reviewer = approved_by.strip()
    if not reviewer:
        raise ValueError("approved_by must not be empty")
    if not artifacts:
        raise ValueError("at least one --artifact is required")

    receipt_resolved = resolve_output_path(str(root), receipt_path) if receipt_path else None
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in artifacts:
        path, relative_path = _resolve_existing_artifact(raw_path, root)
        if receipt_resolved is not None and path == receipt_resolved:
            raise ValueError("approval receipt cannot include itself")
        if any(token in path.name.lower() for token in VOLATILE_ARTIFACT_TOKENS):
            raise ValueError(f"volatile generated artifact cannot be approved: {relative_path}")
        if relative_path in seen:
            raise ValueError(f"duplicate artifact: {relative_path}")
        seen.add(relative_path)
        records.append(_artifact_record(path, relative_path))

    records.sort(key=lambda item: item["path"])
    return {
        "version": VERSION,
        "recorded_at": utc_now(),
        "approval_scope": "listed_artifacts_only",
        "approved_by_label": reviewer,
        "assurance": {
            "identity": "unverified_user_supplied_label",
            "signature": "none",
            "timestamp": "local_system_clock",
        },
        "note": note.strip(),
        "hash_algorithm": "sha256",
        "artifacts": records,
        "summary": {
            "artifacts": len(records),
        },
        "notes": [
            "This receipt binds reviewed files to exact bytes using SHA-256.",
            "SHA-256 identifies file content; this receipt is not digitally signed and does not authenticate the approver.",
            "Create a new receipt after any approved artifact changes.",
        ],
    }


def _invalid_result(
    relative_path: str,
    status: str,
    issue: str,
    *,
    expected_sha256: str = "",
    actual_sha256: str = "",
) -> Dict[str, Any]:
    return {
        "path": relative_path,
        "status": status,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "issue": issue,
    }


def _safe_record_path(raw_path: Any, root: Path) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "path must be a non-empty project-relative string"
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None, "path must be normalized and project-relative"
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
        current_relative = _within_project(resolved, root)
    except (OSError, ValueError):
        return None, "path is missing or resolves outside the project"
    if current_relative != pure.as_posix():
        return None, "path now resolves through a symlink or different canonical location"
    if not resolved.is_file():
        return None, "path is not a regular file"
    return resolved, None


def verify_receipt(
    receipt: Mapping[str, Any],
    project_dir: str,
    *,
    receipt_path: Optional[str] = None,
) -> Dict[str, Any]:
    root = _resolve_project(project_dir)
    results: List[Dict[str, Any]] = []
    schema_issues: List[str] = []

    if receipt.get("version") != VERSION:
        schema_issues.append(f"unsupported receipt version: {receipt.get('version')!r}")
    if receipt.get("approval_scope") != "listed_artifacts_only":
        schema_issues.append("approval_scope must be listed_artifacts_only")
    if receipt.get("hash_algorithm") != "sha256":
        schema_issues.append("hash_algorithm must be sha256")
    if not str(receipt.get("approved_by_label") or "").strip():
        schema_issues.append("approved_by_label must not be empty")
    records = receipt.get("artifacts")
    if not isinstance(records, list) or not records:
        schema_issues.append("receipt artifacts must be a non-empty list")
        records = []

    receipt_resolved: Optional[Path] = None
    if receipt_path:
        candidate = Path(receipt_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            receipt_resolved = candidate.resolve(strict=True)
        except OSError:
            receipt_resolved = None

    seen: set[str] = set()
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            results.append(_invalid_result("", "invalid", "artifact entry must be an object"))
            continue

        relative_path = str(raw_record.get("path") or "")
        expected_sha256 = str(raw_record.get("sha256") or "").lower()
        expected_size = raw_record.get("size_bytes")
        if relative_path in seen:
            results.append(
                _invalid_result(
                    relative_path,
                    "invalid",
                    "duplicate artifact path",
                    expected_sha256=expected_sha256,
                )
            )
            continue
        seen.add(relative_path)

        if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
            results.append(
                _invalid_result(
                    relative_path,
                    "invalid",
                    "sha256 must be 64 lowercase hexadecimal characters",
                    expected_sha256=expected_sha256,
                )
            )
            continue
        if not isinstance(expected_size, int) or expected_size < 0:
            results.append(
                _invalid_result(
                    relative_path,
                    "invalid",
                    "size_bytes must be a non-negative integer",
                    expected_sha256=expected_sha256,
                )
            )
            continue

        path, path_issue = _safe_record_path(relative_path, root)
        if path is None:
            status = "missing" if path_issue and path_issue.startswith("path is missing") else "unsafe"
            results.append(
                _invalid_result(
                    relative_path,
                    status,
                    path_issue or "unsafe path",
                    expected_sha256=expected_sha256,
                )
            )
            continue
        if any(token in path.name.lower() for token in VOLATILE_ARTIFACT_TOKENS):
            results.append(
                _invalid_result(
                    relative_path,
                    "invalid",
                    "volatile generated artifact cannot be approved",
                    expected_sha256=expected_sha256,
                )
            )
            continue
        if receipt_resolved is not None and path == receipt_resolved:
            results.append(
                _invalid_result(
                    relative_path,
                    "invalid",
                    "approval receipt cannot include itself",
                    expected_sha256=expected_sha256,
                )
            )
            continue

        try:
            actual_sha256, stat_result = _stable_sha256(path)
            resolved_after = path.resolve(strict=True)
        except (OSError, ValueError) as exc:
            results.append(
                _invalid_result(
                    relative_path,
                    "unsafe",
                    str(exc),
                    expected_sha256=expected_sha256,
                )
            )
            continue
        if resolved_after != path:
            results.append(
                _invalid_result(
                    relative_path,
                    "unsafe",
                    "path changed canonical location while hashing",
                    expected_sha256=expected_sha256,
                    actual_sha256=actual_sha256,
                )
            )
            continue

        issues: List[str] = []
        if stat_result.st_size != expected_size:
            issues.append(f"size changed: expected {expected_size}, got {stat_result.st_size}")
        if actual_sha256 != expected_sha256:
            issues.append("sha256 changed")
        results.append(
            {
                "path": relative_path,
                "status": "changed" if issues else "current",
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": stat_result.st_size,
                "issue": "; ".join(issues),
            }
        )

    blocking = len(schema_issues) + sum(item["status"] != "current" for item in results)
    status = "current" if blocking == 0 else ("invalid" if schema_issues else "stale")
    return {
        "version": VERIFICATION_VERSION,
        "verified_at": utc_now(),
        "status": status,
        "receipt_recorded_at": receipt.get("recorded_at"),
        "approval": {
            "approved_by_label": receipt.get("approved_by_label"),
            "assurance": receipt.get("assurance") if isinstance(receipt.get("assurance"), Mapping) else {},
        },
        "artifacts": results,
        "schema_issues": schema_issues,
        "summary": {
            "artifacts": len(results),
            "current": sum(item["status"] == "current" for item in results),
            "changed": sum(item["status"] == "changed" for item in results),
            "missing": sum(item["status"] == "missing" for item in results),
            "unsafe": sum(item["status"] == "unsafe" for item in results),
            "invalid": sum(item["status"] == "invalid" for item in results) + len(schema_issues),
            "blocking": blocking,
        },
        "notes": [
            "Verification re-hashes current files; it does not authenticate the approved_by label.",
        ],
    }


def emit_receipt_markdown(receipt: Mapping[str, Any]) -> str:
    assurance = receipt.get("assurance") if isinstance(receipt.get("assurance"), Mapping) else {}
    lines = [
        "# Approval Receipt",
        "",
        f"- Recorded: `{receipt.get('recorded_at', '')}`",
        f"- Approved by label: `{receipt.get('approved_by_label', '')}`",
        f"- Identity: `{assurance.get('identity', '')}`",
        f"- Signature: `{assurance.get('signature', '')}`",
        f"- Artifacts: {len(receipt.get('artifacts') or [])}",
        "",
    ]
    note = str(receipt.get("note") or "").strip()
    if note:
        lines.extend([f"- Review note: {note}", ""])
    lines.extend([
        "| path | bytes | sha256 |",
        "|---|---:|---|",
    ])
    for item in receipt.get("artifacts") or []:
        lines.append(f"| `{item.get('path', '')}` | {item.get('size_bytes', 0)} | `{item.get('sha256', '')}` |")
    lines.extend([
        "",
        "> Local consistency receipt only. The reviewer label is not an authenticated identity or digital signature.",
        "",
    ])
    return "\n".join(lines)


def emit_verification_markdown(verification: Mapping[str, Any]) -> str:
    summary = verification.get("summary") if isinstance(verification.get("summary"), Mapping) else {}
    lines = [
        "# Approval Receipt Verification",
        "",
        f"- Status: **{str(verification.get('status') or '').upper()}**",
        f"- Current: {summary.get('current', 0)}/{summary.get('artifacts', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "| path | status | issue |",
        "|---|---|---|",
    ]
    for item in verification.get("artifacts") or []:
        lines.append(f"| `{item.get('path', '')}` | {item.get('status', '')} | {item.get('issue', '') or '-'} |")
    for issue in verification.get("schema_issues") or []:
        lines.append(f"| `receipt` | invalid | {issue} |")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, data: Mapping[str, Any], *, replace: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise ValueError(f"output already exists; pass --replace to overwrite: {path}")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"receipt is missing or unreadable: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("receipt root must be an object")
    return data


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bind reviewed delivery files to SHA-256 and detect stale approval.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a receipt for explicit reviewed files.")
    create.add_argument("--project-dir", default=".", help="Project root used for safe relative paths.")
    create.add_argument("--artifact", action="append", required=True, help="Reviewed file path; repeat for every delivery artifact.")
    create.add_argument("--approved-by", required=True, help="Reviewer label; not an authenticated identity.")
    create.add_argument("--note", default="", help="Optional human review note.")
    create.add_argument("--output", default="verify/approval_receipt.json", help="Receipt JSON path inside the project.")
    create.add_argument("--markdown", help="Optional receipt Markdown path inside the project.")
    create.add_argument("--replace", action="store_true", help="Replace an existing receipt after renewed review.")

    verify = subparsers.add_parser("verify", help="Re-hash receipt files and report stale approval.")
    verify.add_argument("--project-dir", default=".", help="Project root used to resolve receipt paths.")
    verify.add_argument("--receipt", default="verify/approval_receipt.json", help="Receipt JSON path.")
    verify.add_argument("--output", help="Optional verification JSON path inside the project.")
    verify.add_argument("--markdown", help="Optional verification Markdown path inside the project.")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when the receipt is stale or invalid.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "create":
            receipt_output = resolve_output_path(args.project_dir, args.output)
            receipt = create_receipt(
                args.project_dir,
                args.artifact,
                approved_by=args.approved_by,
                note=args.note,
                receipt_path=str(receipt_output),
            )
            write_json(receipt_output, receipt, replace=args.replace)
            if args.markdown:
                write_text(resolve_output_path(args.project_dir, args.markdown), emit_receipt_markdown(receipt))
            print(f"Approval receipt: current artifacts={receipt['summary']['artifacts']}", file=sys.stderr)
            return 0

        receipt_path = Path(args.receipt).expanduser()
        if not receipt_path.is_absolute():
            receipt_path = _resolve_project(args.project_dir) / receipt_path
        receipt_path = receipt_path.resolve(strict=True)
        receipt = _load_json(receipt_path)
        verification = verify_receipt(receipt, args.project_dir, receipt_path=str(receipt_path))
        if args.output:
            write_json(resolve_output_path(args.project_dir, args.output), verification)
        if args.markdown:
            write_text(
                resolve_output_path(args.project_dir, args.markdown),
                emit_verification_markdown(verification),
            )
        print(
            "Approval receipt verification: "
            f"{verification['status']} blocking={verification['summary']['blocking']}",
            file=sys.stderr,
        )
        if args.strict and verification["status"] != "current":
            return 2
        return 0
    except (OSError, ValueError) as exc:
        print(f"approval_receipt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
