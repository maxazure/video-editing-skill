#!/usr/bin/env python3
"""Bind explicit production permissions to the exact project scope and assets.

This module records operator decisions before external uploads, invasive edits,
paid generation, voice cloning, or publishing.  It is local-only: it never
uploads media, calls a provider, spends credits, edits footage, or publishes.
Reviewer labels and notes are self-attested records, not authentication,
digital signatures, or legal advice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


SCOPE_VERSION = "production_authorization_scope.v1"
REQUEST_VERSION = "production_authorization_request.v1"
RESPONSE_VERSION = "production_authorization_response.v1"
REPORT_VERSION = "production_authorization.v1"

ACTION_KINDS = {
    "external_upload",
    "editorial_reorder",
    "content_removal",
    "creative_addition",
    "paid_generation",
    "voice_clone",
    "publish",
}
PROVIDER_ACTIONS = {"external_upload", "paid_generation", "voice_clone"}
SURFACE_ACTIONS = PROVIDER_ACTIONS | {"publish"}
RIGHTS_BASES: Mapping[str, Sequence[str]] = {
    "real_person_likeness": (
        "subject_self",
        "explicit_subject_permission",
        "licensed_performer",
    ),
    "minor_likeness": (
        "guardian_permission",
        "licensed_performer_with_guardian",
    ),
    "public_figure_likeness": (
        "explicit_subject_permission",
        "licensed_material",
    ),
    "voice_clone": (
        "speaker_self",
        "explicit_speaker_permission",
    ),
    "brand_or_trademark": (
        "brand_owner",
        "licensed_use",
        "explicit_brand_permission",
    ),
    "protected_character": (
        "rights_owner",
        "licensed_use",
        "explicit_rights_permission",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _root(project_dir: str) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project directory is not a directory: {root}")
    return root


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _project_file(root: Path, raw: str, *, label: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not _inside(root, resolved):
        raise ValueError(f"{label} must be inside the project: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"{label} is not a file: {resolved}")
    return resolved


def _output_file(root: Path, raw: str, *, protected: Iterable[Path], force: bool) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError(f"output must not be a symlink: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=False)
    if not _inside(root, resolved):
        raise ValueError(f"output must be inside the project: {resolved}")
    protected_paths = [path.resolve(strict=False) for path in protected]
    if resolved in protected_paths:
        raise ValueError(f"output must not overwrite an input: {resolved}")
    if resolved.exists() and any(resolved.samefile(path) for path in protected_paths if path.exists()):
        raise ValueError(f"output must not overwrite a hard-linked input: {resolved}")
    if resolved.exists() and not force:
        raise ValueError(f"output already exists; pass --force to replace: {resolved}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve(strict=True).relative_to(root).as_posix()


def _file_record(root: Path, path: Path) -> Dict[str, Any]:
    return {
        "path": _relative(root, path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _bound_input_files(root: Path, request: Mapping[str, Any]) -> List[Path]:
    inputs = request.get("inputs") if isinstance(request.get("inputs"), Mapping) else {}
    paths: List[Path] = []
    scope = inputs.get("scope")
    if isinstance(scope, Mapping) and _text(scope.get("path")):
        paths.append(
            _project_file(
                root,
                _text(scope.get("path")),
                label="scope",
            )
        )
    assets = inputs.get("assets") if isinstance(inputs.get("assets"), list) else []
    for record in assets:
        if not isinstance(record, Mapping) or record.get("status") != "ready":
            continue
        paths.append(_project_file(root, _text(record.get("path")), label="source asset"))
    return paths


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_ids(value: Any, *, label: str, blockers: List[str]) -> List[str]:
    if not isinstance(value, list):
        blockers.append(f"{label} must be a list")
        return []
    ids = [_text(item) for item in value]
    if any(not item for item in ids):
        blockers.append(f"{label} contains an empty id")
    if len(set(ids)) != len(ids):
        blockers.append(f"{label} contains duplicate ids")
    return [item for item in ids if item]


def _asset_record(root: Path, raw_path: Any, blockers: List[str], *, asset_id: str) -> Dict[str, Any]:
    value = _text(raw_path)
    if not value:
        blockers.append(f"asset {asset_id} has no path")
        return {"path": "", "status": "missing"}
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        blockers.append(f"asset {asset_id} must not be a symlink: {value}")
        return {"path": value, "status": "unsafe"}
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        blockers.append(f"asset {asset_id} is missing: {value}")
        return {"path": value, "status": "missing"}
    if not _inside(root, resolved):
        blockers.append(f"asset {asset_id} must be inside the project: {resolved}")
        return {"path": str(resolved), "status": "outside_project"}
    if not resolved.is_file():
        blockers.append(f"asset {asset_id} is not a file: {resolved}")
        return {"path": _relative(root, resolved), "status": "invalid"}
    record = _file_record(root, resolved)
    record["status"] = "ready"
    return record


def build_request(
    *,
    root: Path,
    scope_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    scope = _load_json(scope_path)
    blockers: List[str] = []
    warnings: List[str] = []
    if scope.get("version") != SCOPE_VERSION:
        blockers.append(f"scope version must be {SCOPE_VERSION}")

    raw_assets = scope.get("assets")
    if not isinstance(raw_assets, list):
        blockers.append("scope assets must be a list")
        raw_assets = []
    assets: List[Dict[str, Any]] = []
    asset_ids: set[str] = set()
    asset_paths: set[str] = set()
    for index, raw in enumerate(raw_assets, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"asset #{index} is not an object")
            continue
        asset_id = _text(raw.get("id"))
        if not asset_id:
            blockers.append(f"asset #{index} has no id")
            asset_id = f"invalid_asset_{index}"
        elif asset_id in asset_ids:
            blockers.append(f"duplicate asset id: {asset_id}")
        asset_ids.add(asset_id)
        file_record = _asset_record(root, raw.get("path"), blockers, asset_id=asset_id)
        path_key = _text(file_record.get("path"))
        if path_key and path_key in asset_paths:
            blockers.append(f"duplicate asset path: {path_key}")
        asset_paths.add(path_key)
        assets.append(
            {
                "id": asset_id,
                "role": _text(raw.get("role")),
                **file_record,
            }
        )

    raw_actions = scope.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        blockers.append("scope must declare at least one proposed action")
        raw_actions = []
    actions: List[Dict[str, Any]] = []
    action_ids: set[str] = set()
    for index, raw in enumerate(raw_actions, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"action #{index} is not an object")
            continue
        action_id = _text(raw.get("id"))
        kind = _text(raw.get("kind"))
        description = _text(raw.get("description"))
        purpose = _text(raw.get("purpose"))
        provider = _text(raw.get("provider"))
        cost_note = _text(raw.get("cost_or_quota"))
        refs = _string_ids(raw.get("asset_ids", []), label=f"action {action_id or index} asset_ids", blockers=blockers)
        if not action_id:
            blockers.append(f"action #{index} has no id")
            action_id = f"invalid_action_{index}"
        elif action_id in action_ids:
            blockers.append(f"duplicate action id: {action_id}")
        action_ids.add(action_id)
        if kind not in ACTION_KINDS:
            blockers.append(f"action {action_id} has unsupported kind: {kind or '<empty>'}")
        if not description:
            blockers.append(f"action {action_id} needs a plain-language description")
        if not purpose:
            blockers.append(f"action {action_id} needs a purpose")
        unknown_assets = sorted(set(refs) - asset_ids)
        if unknown_assets:
            blockers.append(f"action {action_id} references unknown assets: {', '.join(unknown_assets)}")
        if kind == "external_upload" and not refs:
            blockers.append(f"external upload action {action_id} must name uploaded asset_ids")
        if kind in SURFACE_ACTIONS:
            if not provider:
                blockers.append(f"action {action_id} must name the exact provider or surface")
        if kind in PROVIDER_ACTIONS:
            if not cost_note:
                blockers.append(f"action {action_id} must state potential cost or quota impact")
        actions.append(
            {
                "id": action_id,
                "kind": kind,
                "description": description,
                "purpose": purpose,
                "provider": provider,
                "cost_or_quota": cost_note,
                "asset_ids": refs,
            }
        )

    raw_rights = scope.get("rights_items", [])
    if not isinstance(raw_rights, list):
        blockers.append("scope rights_items must be a list")
        raw_rights = []
    rights_items: List[Dict[str, Any]] = []
    rights_ids: set[str] = set()
    for index, raw in enumerate(raw_rights, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"rights item #{index} is not an object")
            continue
        rights_id = _text(raw.get("id"))
        kind = _text(raw.get("kind"))
        subject = _text(raw.get("subject"))
        intended_use = _text(raw.get("intended_use"))
        refs = _string_ids(raw.get("asset_ids", []), label=f"rights item {rights_id or index} asset_ids", blockers=blockers)
        if not rights_id:
            blockers.append(f"rights item #{index} has no id")
            rights_id = f"invalid_rights_{index}"
        elif rights_id in rights_ids:
            blockers.append(f"duplicate rights item id: {rights_id}")
        rights_ids.add(rights_id)
        if kind not in RIGHTS_BASES:
            blockers.append(f"rights item {rights_id} has unsupported kind: {kind or '<empty>'}")
        if not subject:
            blockers.append(f"rights item {rights_id} must name the subject or protected property")
        if not intended_use:
            blockers.append(f"rights item {rights_id} must state the intended use")
        unknown_assets = sorted(set(refs) - asset_ids)
        if unknown_assets:
            blockers.append(f"rights item {rights_id} references unknown assets: {', '.join(unknown_assets)}")
        rights_items.append(
            {
                "id": rights_id,
                "kind": kind,
                "subject": subject,
                "intended_use": intended_use,
                "asset_ids": refs,
                "allowed_bases": list(RIGHTS_BASES.get(kind, ())),
            }
        )

    if any(item.get("kind") == "voice_clone" for item in actions) and not any(
        item.get("kind") == "voice_clone" for item in rights_items
    ):
        blockers.append("voice_clone action requires a voice_clone rights item")

    response_template = {
        "version": RESPONSE_VERSION,
        "request_id": "",
        "reviewed_by": "",
        "action_decisions": [
            {"action_id": item["id"], "decision": "", "note": ""} for item in actions
        ],
        "rights_decisions": [
            {"rights_id": item["id"], "decision": "", "basis": "", "evidence_note": ""}
            for item in rights_items
        ],
        "review_notes": "",
    }
    payload: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": {
            "scope": _file_record(root, scope_path),
            "assets": assets,
        },
        "actions": actions,
        "rights_items": rights_items,
        "review_rules": [
            "Approve only the named action, provider/surface, purpose, assets, and cost/quota statement.",
            "Rejecting an action blocks this scope; remove or revise it, prepare a new request, and review again.",
            "External upload approval is per named asset and provider; it does not authorize later providers or reuse.",
            "Voice cloning requires the speaker to be the reviewer or explicit speaker permission; public availability is not consent.",
            "Minor, public-figure, brand, trademark, and protected-character use requires one of the listed explicit bases.",
            "Reviewer labels, basis choices, and evidence notes are self-attested records, not authentication, signatures, or legal conclusions.",
        ],
        "response_template": response_template,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "assets": len(assets),
            "actions": len(actions),
            "rights_items": len(rights_items),
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
        },
    }
    payload["request_id"] = _digest("pa_request", {key: value for key, value in payload.items() if key != "request_id"})
    response_template["request_id"] = payload["request_id"]
    return payload


def _verify_request(request: Mapping[str, Any], root: Path) -> List[str]:
    errors: List[str] = []
    if request.get("version") != REQUEST_VERSION:
        return [f"unsupported request version: {request.get('version')}"]
    if _text(request.get("project_root")) != str(root):
        errors.append("request project_root does not match the live project")
    inputs = request.get("inputs") if isinstance(request.get("inputs"), Mapping) else {}
    try:
        scope = _project_file(root, _text((inputs.get("scope") or {}).get("path")), label="scope")
        expected = build_request(
            root=root,
            scope_path=scope,
            generated_at=_text(request.get("generated_at")),
        )
        if expected != request:
            errors.append("request, scope, or a bound source asset has drifted")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    return errors


def _decision_index(
    value: Any,
    *,
    id_key: str,
    label: str,
    blockers: List[str],
) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        blockers.append(f"response {label} must be a list")
        return {}
    indexed: Dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append(f"response {label} contains a non-object decision")
            continue
        item_id = _text(item.get(id_key))
        if not item_id:
            blockers.append(f"response {label} contains a decision without {id_key}")
        elif item_id in indexed:
            blockers.append(f"duplicate response decision: {item_id}")
        else:
            indexed[item_id] = item
    return indexed


def build_report(
    *,
    root: Path,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    request_path: Path,
    response_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    blockers = list(request.get("blockers") or [])
    warnings = list(request.get("warnings") or [])
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if response.get("request_id") != request.get("request_id"):
        blockers.append("response request_id does not match the live request")
    reviewed_by = _text(response.get("reviewed_by"))
    if not reviewed_by:
        blockers.append("reviewed_by is required")

    expected_actions = {
        _text(item.get("id")): item
        for item in request.get("actions") or []
        if isinstance(item, Mapping) and _text(item.get("id"))
    }
    action_index = _decision_index(
        response.get("action_decisions"),
        id_key="action_id",
        label="action_decisions",
        blockers=blockers,
    )
    unknown_actions = sorted(set(action_index) - set(expected_actions))
    if unknown_actions:
        blockers.append(f"response contains unknown action decisions: {', '.join(unknown_actions)}")
    actions: List[Dict[str, Any]] = []
    for action_id, action in expected_actions.items():
        decision = action_index.get(action_id)
        if decision is None:
            blockers.append(f"missing action decision: {action_id}")
            actions.append({**dict(action), "decision": "", "note": ""})
            continue
        choice = _text(decision.get("decision"))
        note = _text(decision.get("note"))
        if choice not in {"approve", "reject"}:
            blockers.append(f"action {action_id} decision must be approve or reject")
        if not note:
            blockers.append(f"action {action_id} decision note is required")
        if choice == "reject":
            blockers.append(f"action rejected: {action_id}")
        actions.append({**dict(action), "decision": choice, "note": note})

    expected_rights = {
        _text(item.get("id")): item
        for item in request.get("rights_items") or []
        if isinstance(item, Mapping) and _text(item.get("id"))
    }
    rights_index = _decision_index(
        response.get("rights_decisions"),
        id_key="rights_id",
        label="rights_decisions",
        blockers=blockers,
    )
    unknown_rights = sorted(set(rights_index) - set(expected_rights))
    if unknown_rights:
        blockers.append(f"response contains unknown rights decisions: {', '.join(unknown_rights)}")
    rights_items: List[Dict[str, Any]] = []
    for rights_id, rights in expected_rights.items():
        decision = rights_index.get(rights_id)
        if decision is None:
            blockers.append(f"missing rights decision: {rights_id}")
            rights_items.append(
                {
                    **dict(rights),
                    "decision": "",
                    "basis": "",
                    "evidence_note": "",
                }
            )
            continue
        choice = _text(decision.get("decision"))
        basis = _text(decision.get("basis"))
        evidence_note = _text(decision.get("evidence_note"))
        if choice not in {"approve", "reject"}:
            blockers.append(f"rights item {rights_id} decision must be approve or reject")
        if choice == "approve" and basis not in set(rights.get("allowed_bases") or []):
            blockers.append(f"rights item {rights_id} has an unsupported approval basis: {basis or '<empty>'}")
        if not evidence_note:
            blockers.append(f"rights item {rights_id} evidence_note is required")
        if choice == "reject":
            blockers.append(f"rights item rejected: {rights_id}")
        rights_items.append(
            {
                **dict(rights),
                "decision": choice,
                "basis": basis,
                "evidence_note": evidence_note,
            }
        )

    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "request_id": request.get("request_id"),
        "reviewed_by": reviewed_by,
        "review_notes": _text(response.get("review_notes")),
        "inputs": {
            "request": _file_record(root, request_path),
            "response": _file_record(root, response_path),
            "scope": dict((request.get("inputs") or {}).get("scope") or {}),
            "assets": list((request.get("inputs") or {}).get("assets") or []),
        },
        "actions": actions,
        "rights_items": rights_items,
        "request": dict(request),
        "response": dict(response),
        "limitations": [
            "This report records a reviewed local scope; it does not perform any approved action.",
            "Reviewer labels and evidence notes are self-attested and do not authenticate identity or consent.",
            "SHA-256 detects byte drift; it is not a digital signature or a legal-rights determination.",
            "Any provider, purpose, asset, edit scope, rights subject, or source-byte change requires a new request.",
        ],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "summary": {
            "assets": len((request.get("inputs") or {}).get("assets") or []),
            "actions": len(actions),
            "actions_approved": sum(1 for item in actions if item.get("decision") == "approve"),
            "rights_items": len(rights_items),
            "rights_approved": sum(1 for item in rights_items if item.get("decision") == "approve"),
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
        },
    }
    report["report_id"] = _digest("pa_report", {key: value for key, value in report.items() if key != "report_id"})
    return report


def verify_report(report_path: str, *, project_dir: str = ".") -> Dict[str, Any]:
    root = _root(project_dir)
    report_file = _project_file(root, report_path, label="report")
    report = _load_json(report_file)
    errors: List[str] = []
    if report.get("version") != REPORT_VERSION:
        errors.append(f"unsupported report version: {report.get('version')}")
    if _text(report.get("project_root")) != str(root):
        errors.append("report project_root does not match the live project")
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    try:
        request_path = _project_file(root, _text((inputs.get("request") or {}).get("path")), label="request")
        response_path = _project_file(root, _text((inputs.get("response") or {}).get("path")), label="response")
        if _file_record(root, request_path) != inputs.get("request"):
            errors.append("request file has drifted")
        if _file_record(root, response_path) != inputs.get("response"):
            errors.append("response file has drifted")
        request = _load_json(request_path)
        response = _load_json(response_path)
        errors.extend(_verify_request(request, root))
        expected = build_report(
            root=root,
            request=request,
            response=response,
            request_path=request_path,
            response_path=response_path,
            generated_at=_text(report.get("generated_at")),
        )
        if expected != report:
            errors.append("stored report or derived authorization state has drifted")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    if not errors:
        return report
    result = dict(report)
    original_blockers = list(report.get("blockers") or [])
    result["verification_errors"] = sorted(set(errors))
    result["blockers"] = sorted(set(original_blockers + [f"verification: {item}" for item in errors]))
    result["status"] = "blocked"
    summary = dict(report.get("summary") or {})
    summary["blocking"] = len(result["blockers"])
    result["summary"] = summary
    return result


def emit_markdown(payload: Mapping[str, Any]) -> str:
    if payload.get("version") == REQUEST_VERSION:
        summary = payload.get("summary") or {}
        lines = [
            "# Production Authorization Request",
            "",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Assets: {summary.get('assets', 0)}",
            f"- Proposed actions: {summary.get('actions', 0)}",
            f"- Rights items: {summary.get('rights_items', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "## Proposed Actions",
            "",
            "| id | kind | provider/surface | assets | purpose | cost/quota |",
            "|---|---|---|---|---|---|",
        ]
        for item in payload.get("actions") or []:
            lines.append(
                f"| {item.get('id')} | {item.get('kind')} | {item.get('provider') or '-'} | "
                f"{', '.join(item.get('asset_ids') or []) or '-'} | {item.get('purpose')} | "
                f"{item.get('cost_or_quota') or '-'} |"
            )
        lines.extend(["", "## Rights Items", "", "| id | kind | subject | intended use | allowed bases |", "|---|---|---|---|---|"])
        for item in payload.get("rights_items") or []:
            lines.append(
                f"| {item.get('id')} | {item.get('kind')} | {item.get('subject')} | "
                f"{item.get('intended_use')} | {', '.join(item.get('allowed_bases') or [])} |"
            )
        lines.extend(["", "## Review Rules", ""])
        lines.extend(f"- {item}" for item in payload.get("review_rules") or [])
    else:
        summary = payload.get("summary") or {}
        lines = [
            "# Production Authorization",
            "",
            f"- Status: `{payload.get('status', '')}`",
            f"- Reviewer label: `{payload.get('reviewed_by', '')}`",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Report ID: `{payload.get('report_id', '')}`",
            f"- Approved actions: {summary.get('actions_approved', 0)}/{summary.get('actions', 0)}",
            f"- Approved rights items: {summary.get('rights_approved', 0)}/{summary.get('rights_items', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "## Action Decisions",
            "",
            "| id | kind | decision | provider/surface | note |",
            "|---|---|---|---|---|",
        ]
        for item in payload.get("actions") or []:
            lines.append(
                f"| {item.get('id')} | {item.get('kind')} | {item.get('decision')} | "
                f"{item.get('provider') or '-'} | {item.get('note')} |"
            )
        lines.extend(["", "## Rights Decisions", "", "| id | kind | subject | decision | basis | evidence note |", "|---|---|---|---|---|---|"])
        for item in payload.get("rights_items") or []:
            lines.append(
                f"| {item.get('id')} | {item.get('kind')} | {item.get('subject')} | "
                f"{item.get('decision')} | {item.get('basis') or '-'} | {item.get('evidence_note')} |"
            )
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in payload.get("limitations") or [])
    if payload.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in payload.get("blockers") or [])
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in payload.get("warnings") or [])
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, audit, and live-verify source-bound video production authorization."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Bind a production scope and source assets to a response template.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--scope", required=True, help=f"{SCOPE_VERSION} JSON file.")
    prepare.add_argument("--output", required=True, help="Authorization request JSON.")
    prepare.add_argument("--markdown", help="Optional request Markdown.")
    prepare.add_argument("--response-template", required=True, help="Response JSON template to complete.")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true")

    audit = sub.add_parser("audit", help="Validate decisions and write the final authorization report.")
    audit.add_argument("--project-dir", default=".")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Re-read scope, source assets, decisions, and derived report state.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _root(args.project_dir)
        if args.command == "prepare":
            scope = _project_file(root, args.scope, label="scope")
            request = build_request(root=root, scope_path=scope)
            bound_inputs = _bound_input_files(root, request)
            output = _output_file(root, args.output, protected=bound_inputs, force=args.force)
            response_path = _output_file(
                root,
                args.response_template,
                protected=[*bound_inputs, output],
                force=args.force,
            )
            markdown = (
                _output_file(
                    root,
                    args.markdown,
                    protected=[*bound_inputs, output, response_path],
                    force=args.force,
                )
                if args.markdown
                else None
            )
            _write_json(output, request)
            _write_json(response_path, request["response_template"])
            if markdown:
                markdown.write_text(emit_markdown(request), encoding="utf-8")
            print(
                f"production authorization request: assets={request['summary']['assets']} "
                f"actions={request['summary']['actions']} rights={request['summary']['rights_items']} "
                f"blocking={request['summary']['blocking']}"
            )
            return 2 if args.strict and request["summary"]["blocking"] else 0

        if args.command == "audit":
            request_path = _project_file(root, args.request, label="request")
            response_path = _project_file(root, args.response, label="response")
            request = _load_json(request_path)
            response = _load_json(response_path)
            request_errors = _verify_request(request, root)
            if request_errors:
                raise ValueError("; ".join(request_errors))
            protected_inputs = [request_path, response_path, *_bound_input_files(root, request)]
            output = _output_file(root, args.output, protected=protected_inputs, force=args.force)
            markdown = (
                _output_file(
                    root,
                    args.markdown,
                    protected=[*protected_inputs, output],
                    force=args.force,
                )
                if args.markdown
                else None
            )
            report = build_report(
                root=root,
                request=request,
                response=response,
                request_path=request_path,
                response_path=response_path,
            )
            _write_json(output, report)
            if markdown:
                markdown.write_text(emit_markdown(report), encoding="utf-8")
            print(
                f"production authorization: status={report['status']} "
                f"actions={report['summary']['actions_approved']}/{report['summary']['actions']} "
                f"rights={report['summary']['rights_approved']}/{report['summary']['rights_items']} "
                f"blocking={report['summary']['blocking']}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report = verify_report(args.report, project_dir=str(root))
        print(
            f"production authorization verify: status={report['status']} "
            f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
        )
        return 2 if args.strict and report["summary"]["blocking"] else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
