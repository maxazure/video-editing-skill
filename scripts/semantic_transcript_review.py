#!/usr/bin/env python3
"""Prepare, audit, and apply context-aware transcript correction proposals.

The script is provider-neutral by design.  It never calls a model.  A model or
human reviewer may fill the prepared response schema, but deterministic checks
derive coverage from the source transcript and only an independent choices file
can authorize a validated patch.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from transcript_review import load_transcript, redistribute_words


VERSION = "semantic_transcript_review.v1"
ALLOWED_CATEGORIES = {
    "asr_split",
    "domain_term",
    "grammar_term",
    "homophone",
    "idiom",
    "name",
    "transliteration",
    "typo",
    "word_choice",
}
RECOMMENDATIONS = {"accept", "uncertain", "reject"}
CHOICES = {"approve", "reject"}
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*")


class SemanticReviewError(ValueError):
    """Raised for malformed or stale semantic-review artifacts."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticReviewError(f"could not read JSON {path}: {exc}") from exc


def _write_json(path: str, value: Any) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_text(path: str, value: str) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(value.rstrip() + "\n", encoding="utf-8")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _segment_id(segment: Mapping[str, Any]) -> str:
    return str(segment.get("id", "")).strip()


def _public_segment(segment: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "segment_id": _segment_id(segment),
        "start": round(float(segment.get("start", 0.0)), 3),
        "end": round(float(segment.get("end", 0.0)), 3),
        "text": _clean_text(segment.get("text", "")),
    }


def transcript_sha256(transcript: Mapping[str, Any]) -> str:
    public = [_public_segment(item) for item in transcript.get("segments", []) if isinstance(item, Mapping)]
    payload = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_review_request(
    transcript: Mapping[str, Any],
    *,
    context_radius: int = 2,
) -> Dict[str, Any]:
    if not isinstance(context_radius, int) or not 1 <= context_radius <= 4:
        raise SemanticReviewError("context_radius must be an integer from 1 to 4")
    segments = [
        _public_segment(item)
        for item in transcript.get("segments", [])
        if isinstance(item, Mapping) and _segment_id(item) and _clean_text(item.get("text"))
    ]
    if not segments:
        raise SemanticReviewError("transcript has no readable timed segments")
    units: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments):
        previous = segments[max(0, index - context_radius) : index]
        following = segments[index + 1 : index + context_radius + 1]
        units.append(
            {
                **segment,
                "previous": [
                    {"segment_id": item["segment_id"], "text": item["text"]} for item in previous
                ],
                "next": [
                    {"segment_id": item["segment_id"], "text": item["text"]} for item in following
                ],
            }
        )
    source_hash = transcript_sha256(transcript)
    segment_ids = [item["segment_id"] for item in segments]
    return {
        "version": VERSION,
        "artifact_type": "request",
        "generated_at": _now_iso(),
        "source": {
            "sha256": source_hash,
            "hash_basis": "normalized segment id/start/end/text",
            "segment_count": len(segments),
            "context_radius": context_radius,
        },
        "instructions": {
            "task": "Review every target segment with its previous/next context and propose only certain ASR corrections.",
            "rules": [
                "Copy segment_id exactly and include every segment_id in reviewed_segment_ids.",
                "Use zero-based Python character offsets into the target segment; source must equal text[span_start:span_end].",
                "Propose the smallest erroneous span only; do not rewrite sentences, remove spoken style, or change meaning.",
                "Do not change numbers or punctuation. Do not guess English spelling unless context clearly identifies the term.",
                "Confidence and recommendation are evidence, not approval; an independent human choices file is still required.",
            ],
            "allowed_categories": sorted(ALLOWED_CATEGORIES),
            "recommendations": sorted(RECOMMENDATIONS),
        },
        "units": units,
        "response_template": {
            "version": VERSION,
            "source_sha256": source_hash,
            "reviewed_segment_ids": segment_ids,
            "proposals": [],
        },
        "summary": {
            "segments": len(segments),
            "blocking": 0,
        },
    }


def emit_request_markdown(request: Mapping[str, Any]) -> str:
    source = request.get("source", {}) if isinstance(request.get("source"), Mapping) else {}
    lines = [
        "# Semantic Transcript Review Request",
        "",
        f"- Version: `{request.get('version', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Segments: {source.get('segment_count', 0)}",
        f"- Context radius: {source.get('context_radius', 0)}",
        "- This file is a review packet, not an approval or reviewed transcript.",
        "",
        "## Hard rules",
        "",
    ]
    instructions = request.get("instructions", {}) if isinstance(request.get("instructions"), Mapping) else {}
    for rule in instructions.get("rules", []):
        lines.append(f"- {rule}")
    lines.extend(
        [
            "",
            "## Response shape",
            "",
            "```json",
            json.dumps(request.get("response_template", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "Each proposal must add `segment_id`, `span_start`, `span_end`, `source`, `replacement`, "
            "`category`, `confidence`, `recommendation`, and `reason`.",
            "",
            "## Context units",
            "",
        ]
    )
    for unit in request.get("units", []):
        if not isinstance(unit, Mapping):
            continue
        previous = " / ".join(str(item.get("text", "")) for item in unit.get("previous", []) if isinstance(item, Mapping))
        following = " / ".join(str(item.get("text", "")) for item in unit.get("next", []) if isinstance(item, Mapping))
        lines.extend(
            [
                f"### Segment {unit.get('segment_id')} · {float(unit.get('start', 0)):.3f}–{float(unit.get('end', 0)):.3f}",
                "",
                f"- Previous: {previous or '(none)'}",
                f"- Target: **{unit.get('text', '')}**",
                f"- Next: {following or '(none)'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_confidence(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 1:
        return None
    return round(number, 4)


def _punctuation_signature(value: str) -> List[str]:
    return [character for character in value if unicodedata.category(character)[:1] in {"P", "S"}]


def _latin_only(value: str) -> bool:
    tokens = LATIN_TOKEN_RE.findall(value)
    remainder = LATIN_TOKEN_RE.sub("", value)
    return bool(tokens) and CJK_RE.search(value) is None and not remainder.strip()


def _has_unchanged_edges(source: str, replacement: str) -> bool:
    if _latin_only(source) and _latin_only(replacement):
        return False
    return bool(source and replacement and (source[0] == replacement[0] or source[-1] == replacement[-1]))


def _proposal_id(proposal: Mapping[str, Any]) -> str:
    identity = {
        "segment_id": proposal.get("segment_id"),
        "span_start": proposal.get("span_start"),
        "span_end": proposal.get("span_end"),
        "source": proposal.get("source"),
        "replacement": proposal.get("replacement"),
        "category": proposal.get("category"),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "patch-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _edge_duplicate_issue(
    *,
    index: int,
    segments: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
    source: str,
    replacement: str,
) -> Optional[str]:
    text = str(segments[index].get("text", ""))
    if start == 0 and index > 0 and replacement:
        previous = str(segments[index - 1].get("text", "")).rstrip()
        if previous and replacement[0] == previous[-1] and source[0] != replacement[0]:
            return "replacement would duplicate the previous segment boundary"
    if end == len(text) and index + 1 < len(segments) and replacement:
        following = str(segments[index + 1].get("text", "")).lstrip()
        if following and replacement[-1] == following[0] and source[-1] != replacement[-1]:
            return "replacement would duplicate the next segment boundary"
    return None


def validate_proposal(
    raw: Mapping[str, Any],
    *,
    segments: Sequence[Mapping[str, Any]],
    segment_index: Mapping[str, int],
    max_patch_chars: int = 40,
) -> Dict[str, Any]:
    segment_id = str(raw.get("segment_id", "")).strip()
    start = raw.get("span_start")
    end = raw.get("span_end")
    source = str(raw.get("source", ""))
    replacement = str(raw.get("replacement", ""))
    category = str(raw.get("category", "")).strip().lower()
    confidence = _finite_confidence(raw.get("confidence"))
    recommendation = str(raw.get("recommendation", "")).strip().lower()
    reason = _clean_text(raw.get("reason", ""))[:500]
    normalized = {
        "segment_id": segment_id,
        "span_start": start,
        "span_end": end,
        "source": source,
        "replacement": replacement,
        "category": category,
        "confidence": confidence,
        "recommendation": recommendation,
        "reason": reason,
    }
    normalized["proposal_id"] = _proposal_id(normalized)
    issues: List[str] = []
    if segment_id not in segment_index:
        issues.append("unknown segment_id")
    if not _is_int(start) or not _is_int(end):
        issues.append("span_start and span_end must be integers")
    if not source or not source.strip() or "\n" in source or "\r" in source:
        issues.append("source must be a non-empty single-line string")
    if not replacement or not replacement.strip() or "\n" in replacement or "\r" in replacement:
        issues.append("replacement must be a non-empty single-line string")
    if source == replacement:
        issues.append("source and replacement are identical")
    if source != source.strip() or replacement != replacement.strip():
        issues.append("patch edges must not contain whitespace")
    if max(len(source), len(replacement)) > max_patch_chars:
        issues.append(f"patch exceeds {max_patch_chars} characters")
    if category not in ALLOWED_CATEGORIES:
        issues.append("unsupported category")
    if confidence is None:
        issues.append("confidence must be a finite number from 0 to 1")
    if recommendation not in RECOMMENDATIONS:
        issues.append("recommendation must be accept, uncertain, or reject")
    if not reason:
        issues.append("reason must explain the contextual evidence")
    if NUMBER_RE.findall(source) != NUMBER_RE.findall(replacement):
        issues.append("patch changes numbers")
    if _punctuation_signature(source) != _punctuation_signature(replacement):
        issues.append("patch changes punctuation or symbols")
    if _has_unchanged_edges(source, replacement):
        issues.append("patch is not minimal; trim unchanged prefix/suffix context")
    if segment_id in segment_index and _is_int(start) and _is_int(end):
        segment_position = segment_index[segment_id]
        text = str(segments[segment_position].get("text", ""))
        if not (0 <= start < end <= len(text)):
            issues.append("patch span is outside the target segment")
        elif text[start:end] != source:
            issues.append("source does not match the exact target character span")
        elif source and replacement:
            boundary_issue = _edge_duplicate_issue(
                index=segment_position,
                segments=segments,
                start=start,
                end=end,
                source=source,
                replacement=replacement,
            )
            if boundary_issue:
                issues.append(boundary_issue)
    normalized["validation_status"] = "valid" if not issues else "invalid"
    normalized["issues"] = issues
    return normalized


def _mark_overlaps(proposals: List[Dict[str, Any]]) -> None:
    by_segment: Dict[str, List[Dict[str, Any]]] = {}
    for proposal in proposals:
        if proposal.get("validation_status") == "valid":
            by_segment.setdefault(str(proposal.get("segment_id")), []).append(proposal)
    for items in by_segment.values():
        ordered = sorted(items, key=lambda item: (int(item["span_start"]), int(item["span_end"])))
        for first, second in zip(ordered, ordered[1:]):
            if int(second["span_start"]) < int(first["span_end"]):
                for proposal in (first, second):
                    proposal["validation_status"] = "invalid"
                    proposal["issues"].append("proposal overlaps another patch in the same segment")


def _review_id(source_hash: str, proposals: Sequence[Mapping[str, Any]]) -> str:
    identity = [
        {
            "proposal_id": item.get("proposal_id"),
            "validation_status": item.get("validation_status"),
            "issues": item.get("issues", []),
        }
        for item in proposals
    ]
    payload = json.dumps(
        {"source_sha256": source_hash, "proposals": identity},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "review-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def audit_response(
    transcript: Mapping[str, Any],
    response: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(response, Mapping):
        raise SemanticReviewError("response must be a JSON object")
    segments = [
        _public_segment(item)
        for item in transcript.get("segments", [])
        if isinstance(item, Mapping) and _segment_id(item) and _clean_text(item.get("text"))
    ]
    source_hash = transcript_sha256(transcript)
    expected_ids = [item["segment_id"] for item in segments]
    expected_set = set(expected_ids)
    blockers: List[str] = []
    if response.get("version") != VERSION:
        blockers.append(f"response version must be {VERSION}")
    response_hash = str(response.get("source_sha256", "")).strip()
    if response_hash != source_hash:
        blockers.append("response source_sha256 does not match the current transcript")
    reviewed_raw = response.get("reviewed_segment_ids")
    if not isinstance(reviewed_raw, list):
        reviewed_ids: List[str] = []
        blockers.append("reviewed_segment_ids must be an array")
    else:
        reviewed_ids = [str(value).strip() for value in reviewed_raw]
        if len(reviewed_ids) != len(set(reviewed_ids)):
            blockers.append("reviewed_segment_ids contains duplicates")
    reviewed_set = set(reviewed_ids)
    missing_ids = [segment_id for segment_id in expected_ids if segment_id not in reviewed_set]
    unknown_ids = sorted(reviewed_set.difference(expected_set))
    if missing_ids:
        blockers.append(f"semantic coverage is partial; {len(missing_ids)} segment(s) were not reviewed")
    if unknown_ids:
        blockers.append(f"reviewed_segment_ids contains {len(unknown_ids)} unknown segment(s)")
    raw_proposals = response.get("proposals")
    if not isinstance(raw_proposals, list):
        raw_proposals = []
        blockers.append("proposals must be an array")
    segment_index = {item["segment_id"]: index for index, item in enumerate(segments)}
    proposals: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_proposals):
        if not isinstance(raw, Mapping):
            proposals.append(
                {
                    "proposal_id": f"invalid-{index + 1:03d}",
                    "segment_id": "",
                    "validation_status": "invalid",
                    "issues": ["proposal must be a JSON object"],
                }
            )
            continue
        proposals.append(
            validate_proposal(raw, segments=segments, segment_index=segment_index)
        )
    seen: Dict[str, int] = {}
    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id", ""))
        seen[proposal_id] = seen.get(proposal_id, 0) + 1
    for proposal in proposals:
        if seen.get(str(proposal.get("proposal_id", "")), 0) > 1:
            proposal["validation_status"] = "invalid"
            if "duplicate proposal" not in proposal["issues"]:
                proposal["issues"].append("duplicate proposal")
    _mark_overlaps(proposals)
    invalid = [item for item in proposals if item.get("validation_status") != "valid"]
    if invalid:
        blockers.append(f"{len(invalid)} proposal(s) failed deterministic validation")
    review_id = _review_id(source_hash, proposals)
    valid_count = len(proposals) - len(invalid)
    validation_blocking = len(blockers)
    pending_choices = valid_count if validation_blocking == 0 else 0
    total_blocking = validation_blocking + pending_choices
    return {
        "version": VERSION,
        "artifact_type": "audit",
        "generated_at": _now_iso(),
        "status": "blocked" if total_blocking else "ready",
        "review_id": review_id,
        "source": {
            "sha256": source_hash,
            "hash_basis": "normalized segment id/start/end/text",
            "segments": len(expected_ids),
        },
        "coverage": {
            "reviewed_segment_ids": reviewed_ids,
            "reviewed": len(expected_set.intersection(reviewed_set)),
            "total": len(expected_ids),
            "missing_segment_ids": missing_ids,
            "unknown_segment_ids": unknown_ids,
            "complete": not missing_ids and not unknown_ids and len(reviewed_ids) == len(expected_ids),
        },
        "proposals": proposals,
        "validation_blockers": blockers,
        "human_review": {
            "required": bool(valid_count),
            "choices_schema": {
                "version": VERSION,
                "source_sha256": source_hash,
                "review_id": review_id,
                "reviewer": "human reviewer label",
                "choices": {item["proposal_id"]: "approve|reject" for item in proposals if item.get("validation_status") == "valid"},
            },
            "disclaimer": "reviewer is a local label, not authentication or a digital signature",
        },
        "summary": {
            "proposals": len(proposals),
            "valid": valid_count,
            "invalid": len(invalid),
            "validation_blocking": validation_blocking,
            "pending_choices": pending_choices,
            "approved": 0,
            "rejected": 0,
            "applied": 0,
            "blocking": total_blocking,
        },
    }


def emit_audit_markdown(audit: Mapping[str, Any]) -> str:
    summary = audit.get("summary", {}) if isinstance(audit.get("summary"), Mapping) else {}
    coverage = audit.get("coverage", {}) if isinstance(audit.get("coverage"), Mapping) else {}
    lines = [
        "# Semantic Transcript Review",
        "",
        f"- Status: **{str(audit.get('status', '')).upper()}**",
        f"- Review ID: `{audit.get('review_id', '')}`",
        f"- Coverage: {coverage.get('reviewed', 0)}/{coverage.get('total', 0)}",
        f"- Proposals: {summary.get('proposals', 0)} (valid {summary.get('valid', 0)}, invalid {summary.get('invalid', 0)})",
        f"- Pending choices: {summary.get('pending_choices', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "- Model recommendations are proposals only; apply requires an independent choices file.",
        "",
    ]
    blockers = audit.get("validation_blockers", [])
    if blockers:
        lines.extend(["## Validation blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
        lines.append("")
    lines.extend(
        [
            "## Proposals",
            "",
            "| proposal | segment | span | correction | category | confidence | recommendation | choice | validation |",
            "|---|---:|---:|---|---|---:|---|---|---|",
        ]
    )
    for proposal in audit.get("proposals", []):
        if not isinstance(proposal, Mapping):
            continue
        correction = f"`{proposal.get('source', '')}` → `{proposal.get('replacement', '')}`"
        issues = "; ".join(str(item) for item in proposal.get("issues", []))
        validation = str(proposal.get("validation_status", ""))
        if issues:
            validation += f": {issues}"
        lines.append(
            "| {proposal_id} | {segment_id} | {start}:{end} | {correction} | {category} | {confidence} | {recommendation} | {choice} | {validation} |".format(
                proposal_id=proposal.get("proposal_id", ""),
                segment_id=proposal.get("segment_id", ""),
                start=proposal.get("span_start", ""),
                end=proposal.get("span_end", ""),
                correction=correction.replace("|", "\\|"),
                category=proposal.get("category", ""),
                confidence=proposal.get("confidence", ""),
                recommendation=proposal.get("recommendation", ""),
                choice=proposal.get("choice", "pending"),
                validation=validation.replace("|", "\\|"),
            )
        )
    if audit.get("artifact_type") == "audit" and summary.get("pending_choices"):
        schema = audit.get("human_review", {}).get("choices_schema", {}) if isinstance(audit.get("human_review"), Mapping) else {}
        lines.extend(
            [
                "",
                "## Choices template",
                "",
                "```json",
                json.dumps(schema, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    if audit.get("artifact_type") == "result":
        lines.extend(
            [
                "",
                "## Applied result",
                "",
                f"- Reviewer label: {audit.get('reviewer_label', '')}",
                f"- Approved: {summary.get('approved', 0)}",
                f"- Rejected: {summary.get('rejected', 0)}",
                f"- Applied: {summary.get('applied', 0)}",
                f"- Output canonical segment SHA-256: `{audit.get('output_transcript_sha256', '')}`",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _choices_payload(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticReviewError("choices must be a JSON object")
    if not isinstance(value.get("choices"), Mapping):
        raise SemanticReviewError("choices.choices must be an object keyed by proposal_id")
    return value


def apply_choices(
    transcript: Mapping[str, Any],
    audit: Mapping[str, Any],
    choices_payload: Mapping[str, Any],
    *,
    redistribute: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if audit.get("version") != VERSION or audit.get("artifact_type") != "audit":
        raise SemanticReviewError("audit must be an unapplied semantic_transcript_review audit")
    source_hash = transcript_sha256(transcript)
    audit_source = audit.get("source", {}) if isinstance(audit.get("source"), Mapping) else {}
    blockers: List[str] = []
    if str(audit_source.get("sha256", "")) != source_hash:
        blockers.append("audit source does not match the current transcript")
    validation_blockers = audit.get("validation_blockers")
    if not isinstance(validation_blockers, list) or validation_blockers:
        blockers.append("audit still has deterministic validation blockers")
    if str(choices_payload.get("source_sha256", "")) != source_hash:
        blockers.append("choices source_sha256 does not match the current transcript")
    if choices_payload.get("version") != VERSION:
        blockers.append(f"choices version must be {VERSION}")
    if str(choices_payload.get("review_id", "")) != str(audit.get("review_id", "")):
        blockers.append("choices review_id does not match the current audit")
    reviewer = _clean_text(choices_payload.get("reviewer", ""))
    if not reviewer:
        blockers.append("choices must include a non-empty reviewer label")
    raw_choices = choices_payload.get("choices", {})
    valid_proposals = [
        item
        for item in audit.get("proposals", [])
        if isinstance(item, Mapping) and item.get("validation_status") == "valid"
    ]
    expected_ids = {str(item.get("proposal_id", "")) for item in valid_proposals}
    actual_ids = {str(key) for key in raw_choices}
    missing = sorted(expected_ids.difference(actual_ids))
    unknown = sorted(actual_ids.difference(expected_ids))
    if missing:
        blockers.append(f"choices are missing {len(missing)} proposal decision(s)")
    if unknown:
        blockers.append(f"choices contain {len(unknown)} unknown proposal id(s)")
    normalized_choices: Dict[str, str] = {}
    for proposal_id in sorted(expected_ids.intersection(actual_ids)):
        decision = str(raw_choices.get(proposal_id, "")).strip().lower()
        if decision not in CHOICES:
            blockers.append(f"choice for {proposal_id} must be approve or reject")
        else:
            normalized_choices[proposal_id] = decision
    if blockers:
        raise SemanticReviewError("; ".join(blockers))

    updated = copy.deepcopy(dict(transcript))
    segments = [item for item in updated.get("segments", []) if isinstance(item, dict)]
    by_id = {_segment_id(item): item for item in segments}
    approved = [
        dict(item)
        for item in valid_proposals
        if normalized_choices[str(item.get("proposal_id"))] == "approve"
    ]
    rejected = [
        dict(item)
        for item in valid_proposals
        if normalized_choices[str(item.get("proposal_id"))] == "reject"
    ]
    by_segment: Dict[str, List[Dict[str, Any]]] = {}
    for proposal in approved:
        by_segment.setdefault(str(proposal["segment_id"]), []).append(proposal)
    changes: List[Dict[str, Any]] = []
    for segment_id, patches in by_segment.items():
        segment = by_id.get(segment_id)
        if segment is None:
            raise SemanticReviewError(f"approved patch references missing segment {segment_id}")
        before = _clean_text(segment.get("text", ""))
        after = before
        for patch in sorted(patches, key=lambda item: int(item["span_start"]), reverse=True):
            start = int(patch["span_start"])
            end = int(patch["span_end"])
            if after[start:end] != patch["source"]:
                raise SemanticReviewError(f"approved patch {patch['proposal_id']} no longer matches transcript text")
            after = after[:start] + str(patch["replacement"]) + after[end:]
        segment["text"] = after
        if redistribute:
            segment["words"] = redistribute_words(after, segment)
        changes.append({"segment_id": segment_id, "before": before, "after": after})

    result_proposals: List[Dict[str, Any]] = []
    for proposal in audit.get("proposals", []):
        if not isinstance(proposal, Mapping):
            continue
        result = dict(proposal)
        proposal_id = str(result.get("proposal_id", ""))
        choice = normalized_choices.get(proposal_id, "invalid")
        result["choice"] = choice
        result["applied"] = choice == "approve"
        result_proposals.append(result)
    metadata = {
        "version": VERSION,
        "applied_at": _now_iso(),
        "review_id": str(audit.get("review_id", "")),
        "source_sha256": source_hash,
        "reviewer_label": reviewer,
        "reviewer_disclaimer": "local label only; not authentication or a digital signature",
        "approved": len(approved),
        "rejected": len(rejected),
        "word_timing": "redistributed" if redistribute else "unchanged",
        "changes": changes,
    }
    updated["semantic_review"] = metadata
    output_hash = transcript_sha256(updated)
    result_audit = copy.deepcopy(dict(audit))
    result_audit.update(
        {
            "artifact_type": "result",
            "generated_at": _now_iso(),
            "status": "ready",
            "reviewer_label": reviewer,
            "reviewer_disclaimer": "local label only; not authentication or a digital signature",
            "output_transcript_sha256": output_hash,
            "proposals": result_proposals,
            "validation_blockers": [],
            "human_review": {
                "required": False,
                "resolved": True,
                "reviewer_label": reviewer,
                "disclaimer": "reviewer is a local label, not authentication or a digital signature",
            },
            "summary": {
                "proposals": len(result_proposals),
                "valid": len(valid_proposals),
                "invalid": 0,
                "validation_blocking": 0,
                "pending_choices": 0,
                "approved": len(approved),
                "rejected": len(rejected),
                "applied": len(approved),
                "blocking": 0,
            },
        }
    )
    return updated, result_audit


def cmd_prepare(args: argparse.Namespace) -> int:
    transcript, _segments = load_transcript(args.transcript)
    request = build_review_request(transcript, context_radius=args.context_radius)
    _write_json(args.output, request)
    if args.markdown:
        _write_text(args.markdown, emit_request_markdown(request))
    print(f"semantic review request: {args.output}")
    print(f"segments: {request['summary']['segments']}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    transcript, _segments = load_transcript(args.transcript)
    response = _read_json(args.response)
    audit = audit_response(transcript, response)
    _write_json(args.output, audit)
    if args.markdown:
        _write_text(args.markdown, emit_audit_markdown(audit))
    summary = audit["summary"]
    print(f"semantic review audit: {args.output}")
    print(
        f"coverage={audit['coverage']['reviewed']}/{audit['coverage']['total']} "
        f"valid={summary['valid']} invalid={summary['invalid']} "
        f"pending_choices={summary['pending_choices']} blocking={summary['blocking']}"
    )
    if args.strict and summary["blocking"]:
        return 2
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    transcript, _segments = load_transcript(args.transcript)
    audit = _read_json(args.audit)
    choices = _choices_payload(_read_json(args.choices))
    updated, result = apply_choices(
        transcript,
        audit,
        choices,
        redistribute=not args.keep_words,
    )
    _write_json(args.output, updated)
    audit_output = args.audit_output or args.audit
    _write_json(audit_output, result)
    if args.markdown:
        _write_text(args.markdown, emit_audit_markdown(result))
    print(f"semantic reviewed transcript: {args.output}")
    print(f"semantic review result: {audit_output}")
    print(
        f"approved={result['summary']['approved']} rejected={result['summary']['rejected']} "
        f"word_timing={updated['semantic_review']['word_timing']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, audit, and apply context-aware transcript correction proposals."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Write a provider-neutral context review packet.")
    prepare.add_argument("--transcript", required=True, help="Source transcript JSON.")
    prepare.add_argument("--output", required=True, help="Review request JSON.")
    prepare.add_argument("--markdown", help="Optional readable request Markdown.")
    prepare.add_argument("--context-radius", type=int, default=2, help="Previous/next segments per target (1-4).")
    prepare.set_defaults(func=cmd_prepare)

    audit = subparsers.add_parser("audit", help="Validate model/human proposals without applying them.")
    audit.add_argument("--transcript", required=True, help="Unchanged source transcript JSON.")
    audit.add_argument("--response", required=True, help="Filled response JSON from the review packet.")
    audit.add_argument("--output", required=True, help="Validated audit JSON.")
    audit.add_argument("--markdown", help="Optional audit Markdown with choices template.")
    audit.add_argument("--strict", action="store_true", help="Exit 2 while validation or human choices remain blocking.")
    audit.set_defaults(func=cmd_audit)

    apply = subparsers.add_parser("apply", help="Apply validated patches authorized by a separate choices file.")
    apply.add_argument("--transcript", required=True, help="Unchanged source transcript JSON.")
    apply.add_argument("--audit", required=True, help="Validated audit JSON from the audit command.")
    apply.add_argument("--choices", required=True, help="Human approve/reject choices bound to review_id.")
    apply.add_argument("--output", required=True, help="Reviewed transcript JSON.")
    apply.add_argument("--audit-output", help="Final result JSON; defaults to overwriting --audit after success.")
    apply.add_argument("--markdown", help="Optional final result Markdown.")
    apply.add_argument("--keep-words", action="store_true", help="Keep old words arrays instead of redistributing changed segments.")
    apply.set_defaults(func=cmd_apply)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (SemanticReviewError, ValueError) as exc:
        print(f"semantic transcript review failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
