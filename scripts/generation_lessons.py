#!/usr/bin/env python3
"""Capture approved, evidence-bound lessons from generated-clip reviews.

The library is deliberately provider/model scoped. It stores only lessons that
an operator explicitly approved, and it never calls a generation provider or
regenerates a clip.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import generated_clip_review as clip_review


LIBRARY_VERSION = "generation_lessons.v1"
ENTRY_VERSION = "generation_lesson.v1"
MAX_ENTRIES = 500
MAX_SELECTED = 10
SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LIMITATIONS = [
    "Approval labels are audit metadata, not identity authentication or digital signatures.",
    "A digest detects accidental drift but does not stop a writer from replacing content and recomputing it.",
    "Lessons are prompt constraints, not guarantees that a provider will follow them.",
]
APPROVAL_NOTE = "Label only; not identity authentication or a digital signature."


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry_id(entry: Mapping[str, Any]) -> str:
    return _canonical_sha256({key: value for key, value in entry.items() if key != "lesson_id"})


def _library_id(library: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "version": library.get("version"),
            "created_at": library.get("created_at"),
            "updated_at": library.get("updated_at"),
            "entries": library.get("entries"),
            "limitations": library.get("limitations"),
        }
    )


def _summary(entries: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "entries": len(entries),
        "providers": len({str((item.get("scope") or {}).get("provider") or "") for item in entries}),
        "models": len({str((item.get("scope") or {}).get("model") or "") for item in entries}),
        "categories": len({str((item.get("scope") or {}).get("category") or "") for item in entries}),
        "superseded": len({target for item in entries for target in item.get("supersedes") or []}),
    }


def new_library() -> Dict[str, Any]:
    now = utc_now()
    library: Dict[str, Any] = {
        "version": LIBRARY_VERSION,
        "created_at": now,
        "updated_at": now,
        "entries": [],
        "summary": _summary([]),
        "limitations": list(LIMITATIONS),
    }
    library["library_id"] = _library_id(library)
    return library


def load_library(path: str, *, create: bool = False) -> Dict[str, Any]:
    source = Path(path).expanduser()
    if not source.exists():
        if create:
            return new_library()
        raise ValueError(f"generation lesson library does not exist: {source}")
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"generation lesson library must be a regular file: {source}")
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("generation lesson library root must be an object")
    return value


def _write_text_atomic(path: str, text: str) -> None:
    destination = Path(path).expanduser()
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"refusing to replace symlinked output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: str, data: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _bounded_text(value: Any, *, label: str, minimum: int, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) < minimum or len(text) > maximum:
        raise ValueError(f"{label} must contain {minimum}..{maximum} characters")
    return text


def _scope_value(value: Any, *, label: str, allow_global: bool = False) -> str:
    text = str(value or "").strip()
    if allow_global and text == "*":
        return text
    if not SCOPE_RE.fullmatch(text):
        raise ValueError(f"{label} must be a compact provider/model/category identifier")
    return text


def _canonical_review(report: Mapping[str, Any]) -> Dict[str, Any]:
    if report.get("version") != clip_review.REPORT_VERSION:
        raise ValueError(f"review report version must be {clip_review.REPORT_VERSION}")
    request = report.get("request") or {}
    response = report.get("response") or {}
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        raise ValueError("review report request and response must be objects")
    canonical = clip_review.build_report(request, response)
    errors: List[str] = []
    for key in ("status", "reviews", "summary", "blockers", "warnings"):
        if report.get(key) != canonical.get(key):
            errors.append(f"stored {key} does not match live canonical review")
    if str(report.get("report_id") or "") != clip_review._report_id(report):
        errors.append("stored report_id does not match report content")
    if str(report.get("report_id") or "") != str(canonical.get("report_id") or ""):
        errors.append("stored report_id does not match live canonical review")

    expected_failures = {
        f"{item.get('clip_id')}: generated clip requires regeneration"
        for item in canonical.get("reviews") or []
        if item.get("verdict") == "fail" and not item.get("validation_errors")
    }
    unexpected = sorted(set(canonical.get("blockers") or []).difference(expected_failures))
    errors.extend(unexpected)
    if errors:
        raise ValueError("review is not safe to learn from: " + "; ".join(sorted(set(errors))))
    return canonical


def build_entry(
    report: Mapping[str, Any],
    *,
    clip_id: str,
    category: str,
    lesson: str,
    approved_by: str,
    provider: Optional[str] = None,
    model: str = "*",
    global_scope: bool = False,
    supersedes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    canonical = _canonical_review(report)
    if global_scope and model not in {"", "*"}:
        raise ValueError("global provider lessons must use model=*")
    clip_key = _bounded_text(clip_id, label="clip_id", minimum=1, maximum=128)
    clips = {
        str(item.get("clip_id") or ""): item
        for item in (canonical.get("request") or {}).get("clips") or []
        if isinstance(item, Mapping)
    }
    reviews = {
        str(item.get("clip_id") or ""): item
        for item in canonical.get("reviews") or []
        if isinstance(item, Mapping)
    }
    if clip_key not in clips or clip_key not in reviews:
        raise ValueError(f"clip_id is not present in the canonical review: {clip_key}")
    clip = clips[clip_key]
    review = reviews[clip_key]
    if review.get("validation_errors"):
        raise ValueError(f"clip review is invalid: {clip_key}")

    route = "*" if global_scope else str(provider or clip.get("provider_route") or "").strip()
    if not route:
        raise ValueError("provider is required when the clip review has no provider_route")
    category_value = _scope_value(category, label="category")
    provider_value = _scope_value(route, label="provider", allow_global=True)
    model_value = _scope_value(model or "*", label="model", allow_global=True)
    lesson_text = _bounded_text(lesson, label="lesson", minimum=20, maximum=800)
    approver = _bounded_text(approved_by, label="approved_by", minimum=1, maximum=120)
    evidence = _bounded_text(review.get("notes"), label="review evidence", minimum=1, maximum=2000)
    prompt_fix = str(review.get("prompt_fix") or "").strip()
    if len(prompt_fix) > 1200:
        raise ValueError("prompt_fix must contain at most 1200 characters")
    superseded_ids = sorted(set(str(value or "").strip() for value in supersedes or []))
    if any(not SHA256_RE.fullmatch(value) for value in superseded_ids):
        raise ValueError("supersedes values must be lesson_id SHA-256 digests")

    contact_sheet = clip.get("contact_sheet") or {}
    source = {
        "report_id": str(canonical.get("report_id") or ""),
        "request_id": str((canonical.get("request") or {}).get("request_id") or ""),
        "clip_id": clip_key,
        "clip_sha256": str(clip.get("sha256") or ""),
        "contact_sheet_sha256": str(contact_sheet.get("sha256") or ""),
        "verdict": str(review.get("verdict") or ""),
        "weighted_score": review.get("weighted_score"),
        "hard_fail_codes": list(review.get("hard_fail_codes") or []),
    }
    for label in ("report_id", "request_id", "clip_sha256", "contact_sheet_sha256"):
        if not SHA256_RE.fullmatch(str(source[label])):
            raise ValueError(f"source {label} must be a SHA-256 digest")

    entry: Dict[str, Any] = {
        "version": ENTRY_VERSION,
        "created_at": utc_now(),
        "scope": {
            "provider": provider_value,
            "model": model_value,
            "category": category_value,
        },
        "lesson": lesson_text,
        "prompt_fix": prompt_fix,
        "evidence": evidence,
        "supersedes": superseded_ids,
        "source": source,
        "approval": {
            "approved_by": approver,
            "note": APPROVAL_NOTE,
        },
    }
    entry["lesson_id"] = _entry_id(entry)
    return entry


def _entry_errors(entry: Mapping[str, Any], index: int) -> List[str]:
    prefix = f"entries[{index}]"
    errors: List[str] = []
    expected_keys = {
        "version", "created_at", "scope", "lesson", "prompt_fix", "evidence", "supersedes",
        "source", "approval", "lesson_id",
    }
    if set(entry) != expected_keys:
        errors.append(f"{prefix} keys must exactly match the schema")
    if entry.get("version") != ENTRY_VERSION:
        errors.append(f"{prefix}.version must be {ENTRY_VERSION}")
    if not str(entry.get("created_at") or "").strip():
        errors.append(f"{prefix}.created_at is required")
    for key, minimum, maximum in (("lesson", 20, 800), ("evidence", 1, 2000), ("prompt_fix", 0, 1200)):
        value = str(entry.get(key) or "").strip()
        if len(value) < minimum or len(value) > maximum:
            errors.append(f"{prefix}.{key} must contain {minimum}..{maximum} characters")
    supersedes = entry.get("supersedes")
    if not isinstance(supersedes, list):
        errors.append(f"{prefix}.supersedes must be a list")
    elif supersedes != sorted(set(supersedes)) or any(not SHA256_RE.fullmatch(str(value)) for value in supersedes):
        errors.append(f"{prefix}.supersedes must contain sorted unique lesson ids")
    elif entry.get("lesson_id") in supersedes:
        errors.append(f"{prefix}.supersedes cannot contain its own lesson_id")

    scope = entry.get("scope") or {}
    if not isinstance(scope, Mapping) or set(scope) != {"provider", "model", "category"}:
        errors.append(f"{prefix}.scope must contain provider/model/category")
    else:
        for key in ("provider", "model", "category"):
            value = str(scope.get(key) or "")
            if value != "*" and not SCOPE_RE.fullmatch(value):
                errors.append(f"{prefix}.scope.{key} is invalid")
        if str(scope.get("category") or "") == "*":
            errors.append(f"{prefix}.scope.category cannot be global")
        if scope.get("provider") == "*" and scope.get("model") != "*":
            errors.append(f"{prefix}.scope global provider requires global model")

    source = entry.get("source") or {}
    source_keys = {
        "report_id", "request_id", "clip_id", "clip_sha256", "contact_sheet_sha256",
        "verdict", "weighted_score", "hard_fail_codes",
    }
    if not isinstance(source, Mapping) or set(source) != source_keys:
        errors.append(f"{prefix}.source keys must exactly match the schema")
    else:
        for key in ("report_id", "request_id", "clip_sha256", "contact_sheet_sha256"):
            if not SHA256_RE.fullmatch(str(source.get(key) or "")):
                errors.append(f"{prefix}.source.{key} must be a SHA-256 digest")
        if not str(source.get("clip_id") or "").strip():
            errors.append(f"{prefix}.source.clip_id is required")
        if source.get("verdict") not in clip_review.VERDICTS:
            errors.append(f"{prefix}.source.verdict is invalid")
        score = source.get("weighted_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
            errors.append(f"{prefix}.source.weighted_score must be 0..100")
        codes = source.get("hard_fail_codes")
        if not isinstance(codes, list) or any(code not in clip_review.HARD_FAIL_CODES for code in codes):
            errors.append(f"{prefix}.source.hard_fail_codes is invalid")
        elif codes != sorted(set(codes)):
            errors.append(f"{prefix}.source.hard_fail_codes must be sorted and unique")

    approval = entry.get("approval") or {}
    if not isinstance(approval, Mapping) or set(approval) != {"approved_by", "note"}:
        errors.append(f"{prefix}.approval must contain approved_by/note")
    elif not 1 <= len(str(approval.get("approved_by") or "").strip()) <= 120:
        errors.append(f"{prefix}.approval.approved_by is required")
    elif approval.get("note") != APPROVAL_NOTE:
        errors.append(f"{prefix}.approval.note must preserve the canonical safety boundary")
    if str(entry.get("lesson_id") or "") != _entry_id(entry):
        errors.append(f"{prefix}.lesson_id does not match canonical entry content")
    return errors


def verify_library(library: Mapping[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    if set(library) != {
        "version", "created_at", "updated_at", "entries", "summary", "limitations", "library_id"
    }:
        errors.append("library keys must exactly match the schema")
    if library.get("version") != LIBRARY_VERSION:
        errors.append(f"version must be {LIBRARY_VERSION}")
    for key in ("created_at", "updated_at"):
        if not str(library.get(key) or "").strip():
            errors.append(f"{key} is required")
    entries = library.get("entries") or []
    if not isinstance(entries, list):
        errors.append("entries must be a list")
        entries = []
    if len(entries) > MAX_ENTRIES:
        errors.append(f"entries must contain at most {MAX_ENTRIES} lessons")
    seen = set()
    valid_entries: List[Mapping[str, Any]] = []
    for index, item in enumerate(entries):
        if not isinstance(item, Mapping):
            errors.append(f"entries[{index}] must be an object")
            continue
        valid_entries.append(item)
        errors.extend(_entry_errors(item, index))
        lesson_id = str(item.get("lesson_id") or "")
        if lesson_id in seen:
            errors.append(f"duplicate lesson_id: {lesson_id}")
        seen.add(lesson_id)
    positions = {str(item.get("lesson_id") or ""): index for index, item in enumerate(valid_entries)}
    for index, item in enumerate(valid_entries):
        for target in item.get("supersedes") or []:
            if target not in seen:
                errors.append(f"entries[{index}].supersedes references unknown lesson_id: {target}")
            elif positions.get(target, index) >= index:
                errors.append(f"entries[{index}].supersedes must reference an earlier lesson_id: {target}")
    expected_summary = _summary(valid_entries)
    if library.get("summary") != expected_summary:
        errors.append("summary does not match canonical entries")
    if library.get("limitations") != LIMITATIONS:
        errors.append("limitations do not match the canonical safety boundary")
    if str(library.get("library_id") or "") != _library_id(library):
        errors.append("library_id does not match canonical library content")
    errors = sorted(set(errors))
    return {
        "status": "blocked" if errors else "ready",
        "blockers": errors,
        "summary": {**expected_summary, "blocking": len(errors)},
    }


def add_entry(library: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, Any]:
    verification = verify_library(library)
    if verification["blockers"]:
        raise ValueError("existing generation lesson library is invalid: " + "; ".join(verification["blockers"]))
    entry_errors = _entry_errors(entry, len(library.get("entries") or []))
    if entry_errors:
        raise ValueError("generation lesson entry is invalid: " + "; ".join(entry_errors))
    entries = [dict(item) for item in library.get("entries") or []]
    if any(item.get("lesson_id") == entry.get("lesson_id") for item in entries):
        raise ValueError(f"lesson already exists: {entry.get('lesson_id')}")
    entries.append(dict(entry))
    updated: Dict[str, Any] = {
        "version": LIBRARY_VERSION,
        "created_at": library.get("created_at"),
        "updated_at": utc_now(),
        "entries": entries,
        "summary": _summary(entries),
        "limitations": list(LIMITATIONS),
    }
    updated["library_id"] = _library_id(updated)
    updated_verification = verify_library(updated)
    if updated_verification["blockers"]:
        raise ValueError("updated generation lesson library is invalid: " + "; ".join(updated_verification["blockers"]))
    return updated


def select_lessons(
    library: Mapping[str, Any],
    *,
    provider: str,
    model: str = "",
    categories: Optional[Sequence[str]] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    verification = verify_library(library)
    if verification["blockers"]:
        raise ValueError("generation lesson library is invalid: " + "; ".join(verification["blockers"]))
    if limit < 0 or limit > MAX_SELECTED:
        raise ValueError(f"lesson limit must be between 0 and {MAX_SELECTED}")
    provider_value = _scope_value(provider, label="provider", allow_global=True)
    model_value = _scope_value(model, label="model") if model not in {"", "*"} else ""
    category_values = {_scope_value(value, label="category") for value in categories or []}
    matches: List[Dict[str, Any]] = []
    for raw in library.get("entries") or []:
        item = dict(raw)
        scope = item.get("scope") or {}
        if scope.get("provider") not in {"*", provider_value}:
            continue
        if model_value:
            if scope.get("model") not in {"*", model_value}:
                continue
        elif scope.get("model") != "*":
            continue
        if category_values and scope.get("category") not in category_values:
            continue
        matches.append(item)
    superseded_ids = {target for item in matches for target in item.get("supersedes") or []}
    matches = [item for item in matches if item.get("lesson_id") not in superseded_ids]
    matches.sort(
        key=lambda item: (
            int((item.get("scope") or {}).get("provider") == provider_value),
            int(bool(model_value) and (item.get("scope") or {}).get("model") == model_value),
            str(item.get("created_at") or ""),
        ),
        reverse=True,
    )
    return matches[:limit]


def selection_payload(
    library: Mapping[str, Any],
    *,
    provider: str,
    model: str = "",
    categories: Optional[Sequence[str]] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    entries = select_lessons(
        library,
        provider=provider,
        model=model,
        categories=categories,
        limit=limit,
    )
    return {
        "version": "generation_lesson_selection.v1",
        "library_id": library.get("library_id"),
        "scope": {
            "provider": provider,
            "model": model or "*",
            "categories": list(categories or []),
        },
        "summary": {"selected": len(entries), "limit": limit},
        "entries": entries,
    }


def emit_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Generation Lessons",
        "",
        f"- Library ID: `{payload.get('library_id', '')}`",
        f"- Provider / model: `{(payload.get('scope') or {}).get('provider', '')}` / `{(payload.get('scope') or {}).get('model', '*')}`",
        f"- Selected: {(payload.get('summary') or {}).get('selected', 0)}",
        "",
    ]
    for item in payload.get("entries") or []:
        scope = item.get("scope") or {}
        source = item.get("source") or {}
        lines.extend(
            [
                f"## {scope.get('category', '')} · `{str(item.get('lesson_id') or '')[:12]}`",
                "",
                str(item.get("lesson") or ""),
                "",
                f"- Scope: `{scope.get('provider', '')}` / `{scope.get('model', '')}`",
                f"- Evidence: `{source.get('clip_id', '')}` · `{source.get('verdict', '')}` · score `{source.get('weighted_score', '')}`",
                f"- Supersedes: `{', '.join(item.get('supersedes') or []) or '-'}`",
                f"- Approved by: `{(item.get('approval') or {}).get('approved_by', '')}` (label only)",
                "",
            ]
        )
    if not payload.get("entries"):
        lines.append("No approved lesson matched this scope.")
    return "\n".join(lines).rstrip() + "\n"


def _load_object(path: str) -> Dict[str, Any]:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"input must be a regular JSON file: {source}")
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {source}")
    return value


def _add_command(args: argparse.Namespace) -> int:
    report = _load_object(args.review)
    entry = build_entry(
        report,
        clip_id=args.clip_id,
        category=args.category,
        lesson=args.lesson,
        approved_by=args.approved_by,
        provider=args.provider,
        model=args.model,
        global_scope=args.global_scope,
        supersedes=args.supersedes,
    )
    library = add_entry(load_library(args.library, create=True), entry)
    _write_json_atomic(args.library, library)
    if args.markdown:
        payload = selection_payload(
            library,
            provider=(entry.get("scope") or {}).get("provider") or "*",
            model=(entry.get("scope") or {}).get("model") or "",
            limit=MAX_SELECTED,
        )
        _write_text_atomic(args.markdown, emit_markdown(payload))
    print(
        f"Added generation lesson {str(entry.get('lesson_id') or '')[:12]} to {args.library}; "
        f"entries={library['summary']['entries']}"
    )
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    verification = verify_library(load_library(args.library))
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 2 if args.strict and verification["summary"]["blocking"] else 0


def _select_command(args: argparse.Namespace) -> int:
    library = load_library(args.library)
    payload = selection_payload(
        library,
        provider=args.provider,
        model=args.model,
        categories=args.category,
        limit=args.limit,
    )
    if args.output:
        _write_json_atomic(args.output, payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.markdown:
        _write_text_atomic(args.markdown, emit_markdown(payload))
    return 2 if args.require_match and not payload["entries"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture and select approved lessons from source-bound generated-clip reviews."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Append one explicitly approved lesson from a clip review.")
    add.add_argument("--library", required=True, help="Generation lesson library JSON; created when missing.")
    add.add_argument("--review", required=True, help="Source generated_clip_review.json.")
    add.add_argument("--clip-id", required=True, help="Reviewed clip supplying the evidence.")
    add.add_argument("--category", required=True, help="Compact lesson category, for example hand_contact.")
    add.add_argument("--lesson", required=True, help="Generalizable prompt-ready cause/effect rule.")
    add.add_argument("--approved-by", required=True, help="Approval label; not identity authentication.")
    add.add_argument("--provider", help="Provider override when the review has no provider_route.")
    add.add_argument("--model", default="*", help="Exact model scope, or * for provider-wide use.")
    add.add_argument("--global", dest="global_scope", action="store_true", help="Apply across providers; use sparingly.")
    add.add_argument("--supersedes", action="append", default=[], help="Older lesson_id replaced in matching scopes; can repeat.")
    add.add_argument("--markdown", help="Optional refreshed Markdown view for the entry scope.")
    add.set_defaults(func=_add_command)

    verify = subparsers.add_parser("verify", help="Validate entry digests and derived library state.")
    verify.add_argument("--library", required=True, help="Generation lesson library JSON.")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when the library is invalid.")
    verify.set_defaults(func=_verify_command)

    select = subparsers.add_parser("select", help="Select applicable lessons for a provider/model prompt run.")
    select.add_argument("--library", required=True, help="Generation lesson library JSON.")
    select.add_argument("--provider", required=True, help="Exact target provider.")
    select.add_argument("--model", default="", help="Exact target model; omitted means provider-wide lessons only.")
    select.add_argument("--category", action="append", default=[], help="Category filter; can repeat.")
    select.add_argument("--limit", type=int, default=3, help=f"Maximum lessons, 0..{MAX_SELECTED}.")
    select.add_argument("--output", help="Optional selection JSON output; stdout when omitted.")
    select.add_argument("--markdown", help="Optional Markdown selection output.")
    select.add_argument("--require-match", action="store_true", help="Exit 2 when no lesson matches.")
    select.set_defaults(func=_select_command)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"generation_lessons: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
