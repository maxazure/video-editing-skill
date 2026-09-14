#!/usr/bin/env python3
"""Design and verify shot-to-shot handoffs before multi-clip generation.

The workflow is local-only.  It binds a human-reviewed handoff and edit-boundary
decision to the exact storyboard bytes so provider prompts can carry explicit
receive-in and handoff-out instructions.  It never generates media, submits a
provider job, spends credits, edits footage, or claims that a text plan proves
visual continuity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


REQUEST_VERSION = "sequence_handoff_request.v1"
RESPONSE_VERSION = "sequence_handoff_response.v1"
REPORT_VERSION = "sequence_handoff.v1"

CARRIER_TYPES = {
    "action",
    "eyeline",
    "screen_direction",
    "composition",
    "prop",
    "space",
    "motion",
    "light",
    "color",
    "sound",
    "occlusion",
    "deliberate_rupture",
}
EDIT_TYPES = {
    "hard_cut",
    "action_match",
    "eyeline_match",
    "screen_direction_match",
    "composition_match",
    "motion_match",
    "cutaway",
    "insert",
    "reaction",
    "occlusion_cut",
    "j_cut",
    "l_cut",
    "deliberate_jump_cut",
}
AXIS_DECISIONS = {"not_applicable", "maintain", "reset"}
SCREEN_DIRECTION_DECISIONS = {"not_applicable", "maintain", "reverse_with_reset"}
REVIEW_DECISIONS = {"approve", "revise"}


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return round(result, 4)


def _visual(shot: Mapping[str, Any], field: str) -> str:
    visual = shot.get("visual") if isinstance(shot.get("visual"), Mapping) else {}
    return _text(visual.get(field))


def _route(shot: Mapping[str, Any]) -> str:
    route = shot.get("generation_route") if isinstance(shot.get("generation_route"), Mapping) else {}
    return _text(route.get("primary")) or "unspecified"


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _anchors(shot: Mapping[str, Any]) -> List[str]:
    continuity = shot.get("continuity") if isinstance(shot.get("continuity"), Mapping) else {}
    return _strings(continuity.get("anchors"))


def _shot_snapshot(raw: Mapping[str, Any], index: int, blockers: List[str]) -> Dict[str, Any]:
    shot_id = _text(raw.get("id"))
    if not shot_id:
        shot_id = f"invalid_shot_{index:03d}"
        blockers.append(f"storyboard shot #{index} has no id")
    try:
        start = _number(raw.get("start", 0), field=f"shot {shot_id} start")
        end = _number(raw.get("end", start), field=f"shot {shot_id} end")
    except ValueError as exc:
        blockers.append(str(exc))
        start = 0.0
        end = 0.0
    if end < start:
        blockers.append(f"shot {shot_id} ends before it starts")
    return {
        "id": shot_id,
        "section": _text(raw.get("section")),
        "start": start,
        "end": end,
        "route": _route(raw),
        "narration": _text(raw.get("narration")),
        "keywords": _strings(raw.get("keywords")),
        "continuity_anchors": _anchors(raw),
        "first_frame": _visual(raw, "first_frame"),
        "motion": _visual(raw, "motion"),
        "last_frame": _visual(raw, "last_frame"),
    }


def _shared(left: Sequence[str], right: Sequence[str]) -> List[str]:
    right_index = {item.casefold() for item in right}
    return [item for item in left if item.casefold() in right_index]


def _suggestion(previous: Mapping[str, Any], following: Mapping[str, Any]) -> Dict[str, Any]:
    shared_keywords = _shared(previous.get("keywords") or [], following.get("keywords") or [])
    shared_anchors = _shared(
        previous.get("continuity_anchors") or [],
        following.get("continuity_anchors") or [],
    )
    route_change = previous.get("route") != following.get("route")
    section_change = previous.get("section") != following.get("section")

    if shared_keywords:
        token = shared_keywords[0]
        carrier_type = "prop"
        edit_type = "composition_match"
        offer = f"End with the shared visual token '{token}' readable in the composition."
        receive = f"Open by receiving or compositionally echoing the same token '{token}'."
        match = f"Match the position, scale, or action relationship of '{token}' across the cut."
    elif following.get("section") == "cta":
        carrier_type = "sound"
        edit_type = "hard_cut"
        offer = "End on a neutral held frame while the final content beat resolves."
        receive = "Open the CTA on the planned narration or sound cue with a deliberate graphic change."
        match = "Keep the CTA cut deliberate; preserve palette, aspect, and subtitle-safe area."
    elif route_change:
        carrier_type = "composition"
        edit_type = "cutaway"
        offer = _text(previous.get("last_frame")) or "End on a stable, uncluttered edit-out frame."
        receive = _text(following.get("first_frame")) or "Open on a clearly motivated cutaway composition."
        match = "Use a cutaway that preserves the story beat without pretending unrelated frames match."
    else:
        carrier_type = "composition"
        edit_type = "composition_match"
        offer = _text(previous.get("last_frame")) or "End on a stable edit-out composition."
        receive = _text(following.get("first_frame")) or "Open on a composition that clearly receives the prior frame."
        match = "Choose one visible composition feature to carry across the cut and verify it in the generated frames."

    risk_parts = []
    if route_change:
        risk_parts.append("generation route changes")
    if section_change:
        risk_parts.append("story section changes")
    if not shared_keywords:
        risk_parts.append("no shared keyword is encoded")
    risk = "; ".join(risk_parts) or "The automatic suggestion cannot see the final pixels or performance."
    return {
        "carrier_type": carrier_type,
        "offer_from": offer,
        "receive_in": receive,
        "edit_type": edit_type,
        "match_requirement": match,
        "audio_bridge": "No automatic audio bridge; choose silence, room tone, narration carry, SFX, J-cut, or L-cut during review.",
        "axis_decision": "not_applicable",
        "axis_note": "No explicit 180-degree axis evidence exists in the storyboard; change this if people or spaces are directionally linked.",
        "screen_direction_decision": "not_applicable",
        "screen_direction_note": "No explicit entrance, exit, or travel direction exists; change this when movement direction carries story meaning.",
        "head_handle_seconds": 0.5,
        "tail_handle_seconds": 0.5,
        "risk": risk,
        "fallback_cut": "Use a clean hard cut on neutral frames and remove any false continuity claim.",
        "shared_keywords": shared_keywords,
        "shared_anchors": shared_anchors,
    }


def build_request(
    *,
    root: Path,
    storyboard_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    storyboard = _load_json(storyboard_path)
    blockers: List[str] = []
    warnings: List[str] = []
    if storyboard.get("version") != "storyboard_plan.v1":
        blockers.append("storyboard version must be storyboard_plan.v1")
    raw_shots = storyboard.get("shots")
    if not isinstance(raw_shots, list):
        blockers.append("storyboard shots must be a list")
        raw_shots = []

    shots: List[Dict[str, Any]] = []
    shot_ids: set[str] = set()
    previous_start = -1.0
    for index, raw in enumerate(raw_shots, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"storyboard shot #{index} is not an object")
            continue
        shot = _shot_snapshot(raw, index, blockers)
        shot_id = shot["id"]
        if shot_id in shot_ids:
            blockers.append(f"duplicate storyboard shot id: {shot_id}")
        shot_ids.add(shot_id)
        if shot["start"] < previous_start:
            blockers.append(f"storyboard shot order moves backward at {shot_id}")
        previous_start = shot["start"]
        shots.append(shot)

    boundaries: List[Dict[str, Any]] = []
    response_rows: List[Dict[str, Any]] = []
    for index, (previous, following) in enumerate(zip(shots, shots[1:]), start=1):
        boundary_id = f"boundary_{index:03d}"
        suggestion = _suggestion(previous, following)
        boundary = {
            "id": boundary_id,
            "from_shot": previous["id"],
            "to_shot": following["id"],
            "context": {
                "from": dict(previous),
                "to": dict(following),
                "shared_keywords": suggestion.pop("shared_keywords"),
                "shared_anchors": suggestion.pop("shared_anchors"),
            },
            "suggestion": suggestion,
        }
        boundaries.append(boundary)
        response_rows.append(
            {
                "boundary_id": boundary_id,
                "decision": "",
                **suggestion,
                "review_note": "",
            }
        )

    if len(shots) < 2:
        warnings.append("storyboard has fewer than two shots; there is no sequence boundary to design")

    response_template = {
        "version": RESPONSE_VERSION,
        "request_id": "",
        "reviewed_by": "",
        "boundary_decisions": response_rows,
        "review_notes": "",
    }
    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": {"storyboard": _file_record(root, storyboard_path)},
        "target": dict(storyboard.get("target") or {}),
        "shots": shots,
        "boundaries": boundaries,
        "review_rules": [
            "Review every neighboring pair before final provider prompts or image generation.",
            "State what the earlier shot visibly or audibly offers and how the next shot receives it.",
            "Choose one declared edit type; a vague smooth/natural transition does not define a boundary.",
            "Mark the 180-degree axis and screen direction as maintain, reset, or not applicable with a concrete note.",
            "Leave 0.5-1.0 seconds of usable head/tail handles when the provider and action allow it.",
            "A text decision does not prove generated pixels, action, identity, sound, or timing; review clips and the assembled sequence later.",
        ],
        "response_template": response_template,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "shots": len(shots),
            "boundaries": len(boundaries),
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
        },
    }
    request["request_id"] = _digest(
        "sh_request",
        {key: value for key, value in request.items() if key != "request_id"},
    )
    response_template["request_id"] = request["request_id"]
    return request


def _verify_request(request: Mapping[str, Any], root: Path) -> List[str]:
    errors: List[str] = []
    if request.get("version") != REQUEST_VERSION:
        return [f"unsupported request version: {request.get('version')}"]
    if _text(request.get("project_root")) != str(root):
        errors.append("request project_root does not match the live project")
    inputs = request.get("inputs") if isinstance(request.get("inputs"), Mapping) else {}
    try:
        record = inputs.get("storyboard") if isinstance(inputs.get("storyboard"), Mapping) else {}
        storyboard = _project_file(root, _text(record.get("path")), label="storyboard")
        expected = build_request(
            root=root,
            storyboard_path=storyboard,
            generated_at=_text(request.get("generated_at")),
        )
        if expected != request:
            errors.append("request or bound storyboard bytes have drifted")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return errors


def _decision_index(value: Any, blockers: List[str]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        blockers.append("response boundary_decisions must be a list")
        return {}
    indexed: Dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append("response boundary_decisions contains a non-object row")
            continue
        boundary_id = _text(item.get("boundary_id"))
        if not boundary_id:
            blockers.append("response boundary decision has no boundary_id")
        elif boundary_id in indexed:
            blockers.append(f"duplicate response boundary decision: {boundary_id}")
        else:
            indexed[boundary_id] = item
    return indexed


def _required_text(
    raw: Mapping[str, Any],
    field: str,
    *,
    boundary_id: str,
    blockers: List[str],
) -> str:
    value = _text(raw.get(field))
    if not value:
        blockers.append(f"boundary {boundary_id} requires {field}")
    return value


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

    expected = {
        _text(item.get("id")): item
        for item in request.get("boundaries") or []
        if isinstance(item, Mapping) and _text(item.get("id"))
    }
    indexed = _decision_index(response.get("boundary_decisions"), blockers)
    extras = sorted(set(indexed) - set(expected))
    if extras:
        blockers.append(f"response contains unknown boundaries: {', '.join(extras)}")

    decisions: List[Dict[str, Any]] = []
    for boundary_id, boundary in expected.items():
        raw = indexed.get(boundary_id)
        if raw is None:
            blockers.append(f"missing boundary decision: {boundary_id}")
            continue
        decision = _text(raw.get("decision"))
        if decision not in REVIEW_DECISIONS:
            blockers.append(f"boundary {boundary_id} decision must be approve or revise")
        if decision == "revise":
            blockers.append(f"boundary requires revision: {boundary_id}")

        carrier_type = _text(raw.get("carrier_type"))
        edit_type = _text(raw.get("edit_type"))
        axis_decision = _text(raw.get("axis_decision"))
        screen_decision = _text(raw.get("screen_direction_decision"))
        if carrier_type not in CARRIER_TYPES:
            blockers.append(f"boundary {boundary_id} has unsupported carrier_type: {carrier_type or '<empty>'}")
        if edit_type not in EDIT_TYPES:
            blockers.append(f"boundary {boundary_id} has unsupported edit_type: {edit_type or '<empty>'}")
        if axis_decision not in AXIS_DECISIONS:
            blockers.append(f"boundary {boundary_id} has unsupported axis_decision: {axis_decision or '<empty>'}")
        if screen_decision not in SCREEN_DIRECTION_DECISIONS:
            blockers.append(
                f"boundary {boundary_id} has unsupported screen_direction_decision: {screen_decision or '<empty>'}"
            )

        text_fields = {
            field: _required_text(raw, field, boundary_id=boundary_id, blockers=blockers)
            for field in (
                "offer_from",
                "receive_in",
                "match_requirement",
                "audio_bridge",
                "axis_note",
                "screen_direction_note",
                "risk",
                "fallback_cut",
                "review_note",
            )
        }
        try:
            head_handle = _number(raw.get("head_handle_seconds"), field=f"boundary {boundary_id} head_handle_seconds")
            tail_handle = _number(raw.get("tail_handle_seconds"), field=f"boundary {boundary_id} tail_handle_seconds")
        except ValueError as exc:
            blockers.append(str(exc))
            head_handle = 0.0
            tail_handle = 0.0
        if head_handle == 0 or tail_handle == 0:
            warnings.append(f"boundary {boundary_id} has a zero-length edit handle")
        if axis_decision == "reset" and edit_type not in {
            "hard_cut", "cutaway", "insert", "reaction", "occlusion_cut", "deliberate_jump_cut"
        }:
            blockers.append(f"boundary {boundary_id} resets the axis without a declared reset-capable edit type")
        if screen_decision == "reverse_with_reset" and edit_type not in {
            "hard_cut", "cutaway", "insert", "reaction", "occlusion_cut", "deliberate_jump_cut"
        }:
            blockers.append(f"boundary {boundary_id} reverses screen direction without a reset-capable edit type")
        if carrier_type == "deliberate_rupture" and edit_type != "deliberate_jump_cut":
            blockers.append(f"boundary {boundary_id} uses deliberate_rupture without deliberate_jump_cut")
        if edit_type == "deliberate_jump_cut" and carrier_type != "deliberate_rupture":
            blockers.append(f"boundary {boundary_id} uses deliberate_jump_cut without deliberate_rupture")

        decisions.append(
            {
                "boundary_id": boundary_id,
                "from_shot": boundary.get("from_shot"),
                "to_shot": boundary.get("to_shot"),
                "decision": decision,
                "carrier_type": carrier_type,
                "edit_type": edit_type,
                "axis_decision": axis_decision,
                "screen_direction_decision": screen_decision,
                "head_handle_seconds": head_handle,
                "tail_handle_seconds": tail_handle,
                **text_fields,
                "context": dict(boundary.get("context") or {}),
            }
        )

    unique_blockers = sorted(set(blockers))
    unique_warnings = sorted(set(warnings))
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "request_id": request.get("request_id"),
        "reviewed_by": reviewed_by,
        "review_notes": _text(response.get("review_notes")),
        "inputs": {
            "storyboard": dict((request.get("inputs") or {}).get("storyboard") or {}),
            "request": _file_record(root, request_path),
            "response": _file_record(root, response_path),
        },
        "target": dict(request.get("target") or {}),
        "boundaries": decisions,
        "limitations": [
            "This report records a reviewed boundary design; it does not generate or edit media.",
            "The report must be passed into prompt generation or followed manually to affect provider output.",
            "Text plans do not prove identity, action, axis, screen direction, sound, or pixel continuity.",
            "Review every generated clip, then review all adjacent boundaries again in the assembled sequence.",
        ],
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "status": "blocked" if unique_blockers else ("review" if unique_warnings else "ready"),
        "summary": {
            "boundaries": len(expected),
            "approved": sum(1 for item in decisions if item.get("decision") == "approve"),
            "blocking": len(unique_blockers),
            "warnings": len(unique_warnings),
        },
    }
    report["report_id"] = _digest(
        "sh_report",
        {key: value for key, value in report.items() if key != "report_id"},
    )
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
        request_record = inputs.get("request") if isinstance(inputs.get("request"), Mapping) else {}
        response_record = inputs.get("response") if isinstance(inputs.get("response"), Mapping) else {}
        request_path = _project_file(root, _text(request_record.get("path")), label="request")
        response_path = _project_file(root, _text(response_record.get("path")), label="response")
        if _file_record(root, request_path) != request_record:
            errors.append("request file has drifted")
        if _file_record(root, response_path) != response_record:
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
            errors.append("stored report, decisions, or derived boundary state has drifted")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    if not errors:
        return report
    result = dict(report)
    result["verification_errors"] = sorted(set(errors))
    result["blockers"] = sorted(
        set(list(report.get("blockers") or []) + [f"verification: {item}" for item in errors])
    )
    result["status"] = "blocked"
    summary = dict(report.get("summary") or {})
    summary["blocking"] = len(result["blockers"])
    result["summary"] = summary
    return result


def emit_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if payload.get("version") == REQUEST_VERSION:
        lines = [
            "# Sequence Handoff Request",
            "",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Shots: {summary.get('shots', 0)}",
            f"- Boundaries: {summary.get('boundaries', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "| boundary | from → to | suggested carrier | suggested edit | shared evidence |",
            "|---|---|---|---|---|",
        ]
        for item in payload.get("boundaries") or []:
            context = item.get("context") or {}
            suggestion = item.get("suggestion") or {}
            evidence = ", ".join((context.get("shared_keywords") or []) + (context.get("shared_anchors") or [])) or "none"
            lines.append(
                f"| {item.get('id')} | {item.get('from_shot')} → {item.get('to_shot')} | "
                f"{suggestion.get('carrier_type')} | {suggestion.get('edit_type')} | {evidence} |"
            )
        lines.extend(["", "## Review Rules", ""])
        lines.extend(f"- {rule}" for rule in payload.get("review_rules") or [])
    else:
        lines = [
            "# Sequence Handoff Report",
            "",
            f"- Status: `{payload.get('status', '')}`",
            f"- Reviewer label: `{payload.get('reviewed_by', '')}`",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Report ID: `{payload.get('report_id', '')}`",
            f"- Approved: {summary.get('approved', 0)}/{summary.get('boundaries', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "| boundary | from → to | carrier | edit | axis | direction | handles head/tail |",
            "|---|---|---|---|---|---|---|",
        ]
        for item in payload.get("boundaries") or []:
            lines.append(
                f"| {item.get('boundary_id')} | {item.get('from_shot')} → {item.get('to_shot')} | "
                f"{item.get('carrier_type')} | {item.get('edit_type')} | {item.get('axis_decision')} | "
                f"{item.get('screen_direction_decision')} | {item.get('head_handle_seconds')}s / "
                f"{item.get('tail_handle_seconds')}s |"
            )
        for item in payload.get("boundaries") or []:
            lines.extend(
                [
                    "",
                    f"## {item.get('boundary_id')} · {item.get('from_shot')} → {item.get('to_shot')}",
                    "",
                    f"- Offer: {item.get('offer_from')}",
                    f"- Receive: {item.get('receive_in')}",
                    f"- Match: {item.get('match_requirement')}",
                    f"- Audio: {item.get('audio_bridge')}",
                    f"- Axis: {item.get('axis_note')}",
                    f"- Screen direction: {item.get('screen_direction_note')}",
                    f"- Risk: {item.get('risk')}",
                    f"- Fallback: {item.get('fallback_cut')}",
                    f"- Review note: {item.get('review_note')}",
                ]
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
        description="Prepare, audit, and live-verify storyboard-bound sequence handoffs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Bind storyboard boundaries and write a review template.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--storyboard", required=True, help="storyboard_plan.v1 JSON file.")
    prepare.add_argument("--output", required=True, help="Request JSON file.")
    prepare.add_argument("--markdown", help="Optional request Markdown.")
    prepare.add_argument("--response-template", required=True, help="Response JSON template to complete.")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true")

    audit = sub.add_parser("audit", help="Audit reviewed decisions and write the handoff report.")
    audit.add_argument("--project-dir", default=".")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Re-read storyboard, request, response, and derived state.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _root(args.project_dir)
        if args.command == "prepare":
            storyboard = _project_file(root, args.storyboard, label="storyboard")
            request = build_request(root=root, storyboard_path=storyboard)
            output = _output_file(root, args.output, protected=[storyboard], force=args.force)
            response = _output_file(
                root,
                args.response_template,
                protected=[storyboard, output],
                force=args.force,
            )
            markdown = (
                _output_file(
                    root,
                    args.markdown,
                    protected=[storyboard, output, response],
                    force=args.force,
                )
                if args.markdown
                else None
            )
            _write_json(output, request)
            _write_json(response, request["response_template"])
            if markdown:
                markdown.write_text(emit_markdown(request), encoding="utf-8")
            print(
                f"sequence handoff request: shots={request['summary']['shots']} "
                f"boundaries={request['summary']['boundaries']} blocking={request['summary']['blocking']}"
            )
            return 2 if args.strict and request["summary"]["blocking"] else 0

        if args.command == "audit":
            request_path = _project_file(root, args.request, label="request")
            response_path = _project_file(root, args.response, label="response")
            request = _load_json(request_path)
            response = _load_json(response_path)
            errors = _verify_request(request, root)
            if errors:
                raise ValueError("; ".join(errors))
            storyboard_record = (request.get("inputs") or {}).get("storyboard") or {}
            storyboard = _project_file(root, _text(storyboard_record.get("path")), label="storyboard")
            protected = [storyboard, request_path, response_path]
            output = _output_file(root, args.output, protected=protected, force=args.force)
            markdown = (
                _output_file(root, args.markdown, protected=[*protected, output], force=args.force)
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
                f"sequence handoff: status={report['status']} "
                f"approved={report['summary']['approved']}/{report['summary']['boundaries']} "
                f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report = verify_report(args.report, project_dir=str(root))
        print(
            f"sequence handoff verify: status={report['status']} "
            f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
        )
        return 2 if args.strict and report["summary"]["blocking"] else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
