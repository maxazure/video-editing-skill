#!/usr/bin/env python3
"""Create, verify, and apply portable creator-owned edit style profiles.

The profile is a creative-default contract, not a timeline template.  It keeps
portable direction, pacing, render, and publishing preferences in one JSON
artifact.  Applying a profile only fills missing render-config fields: explicit
project settings and CLI flags remain authoritative.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SPEC_VERSION = "edit_style_profile_spec.v1"
PROFILE_VERSION = "edit_style_profile.v1"
APPLY_VERSION = "edit_style_profile_apply.v1"

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

TOP_LEVEL_KEYS = {
    "version",
    "name",
    "description",
    "creative_direction",
    "pacing",
    "render_defaults",
    "caption_defaults",
    "approval",
    "evidence",
}
PROFILE_DERIVED_KEYS = {"generated_at", "profile_id", "status", "summary", "notes"}
CREATIVE_KEYS = {"primary", "accent", "principles", "avoid"}
PACING_KEYS = {
    "cut_aggressiveness",
    "broll_density",
    "hook_interval_seconds",
    "body_interval_seconds",
    "target_duration_seconds",
}
CAPTION_KEYS = {"preferred_windows", "force_spelling"}
APPROVAL_KEYS = {"basis", "approved_by", "approved_at", "note"}
EVIDENCE_KEYS = {"label", "role", "sha256", "note"}

SUBTITLE_STYLES = {"normal", "karaoke", "bold_pop", "neon", "minimal", "yellow_pop"}
COVER_STYLES = {"bold", "news", "frame", "gradient", "minimal", "white", "techcard"}
COLOR_GRADES = {"natural", "warm", "cool", "punchy", "soft", "cinematic", "screen"}
SPEECH_DENOISE = {"off", "light", "medium", "strong"}
APPROVAL_BASES = {"manual_direction", "approved_outputs", "reference_study"}
EVIDENCE_ROLES = {"master", "cover", "caption", "review", "reference", "other"}

RENDER_DEFAULT_SPECS: Mapping[str, Tuple[str, Any]] = {
    "subtitle_style": ("choice", SUBTITLE_STYLES),
    "cover_style": ("choice", COVER_STYLES),
    "cover_duration": ("number", (0.0, 10.0)),
    "bgm_volume": ("number", (0.0, 1.0)),
    "bgm_fade_out": ("number", (0.0, 30.0)),
    "bgm_ducking": ("bool", None),
    "bgm_ducking_threshold": ("number", (0.00097563, 1.0)),
    "bgm_ducking_ratio": ("number", (1.0, 20.0)),
    "bgm_ducking_attack_ms": ("number", (0.01, 2000.0)),
    "bgm_ducking_release_ms": ("number", (0.01, 9000.0)),
    "speech_denoise": ("choice", SPEECH_DENOISE),
    "color_grade": ("choice", COLOR_GRADES),
    "versioned_output": ("bool", None),
}

PROFILE_ID_FIELDS = (
    "version",
    "name",
    "description",
    "creative_direction",
    "pacing",
    "render_defaults",
    "caption_defaults",
    "approval",
    "evidence",
)


class StyleProfileError(ValueError):
    """Raised when a style profile cannot be safely created or applied."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StyleProfileError(f"value is not canonical JSON: {exc}") from exc


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def profile_id(profile: Mapping[str, Any]) -> str:
    payload = {field: profile.get(field) for field in PROFILE_ID_FIELDS}
    return "esp_" + hashlib.sha256(_canonical(payload)).hexdigest()


def _load_json(path: str) -> Dict[str, Any]:
    source = Path(path).expanduser()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StyleProfileError(f"cannot read JSON object {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise StyleProfileError(f"JSON root must be an object: {source}")
    return value


def load_profile(path: str) -> Dict[str, Any]:
    """Load a profile JSON object for renderer/caption integrations."""
    return _load_json(path)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unknown_keys(value: Any, allowed: Iterable[str], label: str, blockers: List[str]) -> None:
    if not isinstance(value, Mapping):
        blockers.append(f"{label} must be an object")
        return
    unknown = sorted(set(str(key) for key in value).difference(allowed))
    if unknown:
        blockers.append(f"{label} has unknown key: {unknown[0]}")


def _string_list(
    value: Any,
    *,
    label: str,
    blockers: List[str],
    minimum: int = 0,
    maximum: int = 24,
) -> List[str]:
    if not isinstance(value, list):
        blockers.append(f"{label} must be a list")
        return []
    items = [_text(item) for item in value]
    if any(not item for item in items):
        blockers.append(f"{label} contains an empty item")
    if len(items) < minimum:
        blockers.append(f"{label} must contain at least {minimum} item(s)")
    if len(items) > maximum:
        blockers.append(f"{label} must contain at most {maximum} item(s)")
    if len(set(items)) != len(items):
        blockers.append(f"{label} contains duplicate items")
    return items


def _finite_number(
    value: Any,
    *,
    label: str,
    low: float,
    high: float,
    blockers: List[str],
) -> Optional[float]:
    if isinstance(value, bool):
        blockers.append(f"{label} must be a number")
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        blockers.append(f"{label} must be a number")
        return None
    if not math.isfinite(parsed) or not low <= parsed <= high:
        blockers.append(f"{label} must be between {low:g} and {high:g}")
        return None
    return parsed


def _parse_date(value: Any, *, label: str, blockers: List[str]) -> Optional[date]:
    text = _text(value)
    try:
        return date.fromisoformat(text)
    except ValueError:
        blockers.append(f"{label} must be YYYY-MM-DD")
        return None


def _validate_spec(
    spec: Mapping[str, Any],
    *,
    today: Optional[date] = None,
) -> Tuple[List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    today = today or date.today()

    _unknown_keys(spec, TOP_LEVEL_KEYS, "style spec", blockers)
    if spec.get("version") != SPEC_VERSION:
        blockers.append(f"style spec version must be {SPEC_VERSION}")

    name = _text(spec.get("name"))
    if not NAME_RE.fullmatch(name):
        blockers.append("name must be lowercase kebab-case")
    if not _text(spec.get("description")):
        blockers.append("description is required")

    creative = spec.get("creative_direction")
    _unknown_keys(creative, CREATIVE_KEYS, "creative_direction", blockers)
    if isinstance(creative, Mapping):
        if not _text(creative.get("primary")):
            blockers.append("creative_direction.primary is required")
        _string_list(
            creative.get("principles"),
            label="creative_direction.principles",
            blockers=blockers,
            minimum=1,
            maximum=12,
        )
        avoid = _string_list(
            creative.get("avoid"),
            label="creative_direction.avoid",
            blockers=blockers,
            maximum=12,
        )
        if not avoid:
            warnings.append("creative_direction.avoid is empty; generic-style drift is harder to review")

    pacing = spec.get("pacing")
    _unknown_keys(pacing, PACING_KEYS, "pacing", blockers)
    if isinstance(pacing, Mapping):
        if pacing.get("cut_aggressiveness") not in {"gentle", "medium", "tight"}:
            blockers.append("pacing.cut_aggressiveness must be gentle, medium, or tight")
        if pacing.get("broll_density") not in {"light", "medium", "heavy"}:
            blockers.append("pacing.broll_density must be light, medium, or heavy")
        _finite_number(
            pacing.get("hook_interval_seconds"),
            label="pacing.hook_interval_seconds",
            low=0.2,
            high=5.0,
            blockers=blockers,
        )
        _finite_number(
            pacing.get("body_interval_seconds"),
            label="pacing.body_interval_seconds",
            low=0.5,
            high=15.0,
            blockers=blockers,
        )
        _finite_number(
            pacing.get("target_duration_seconds"),
            label="pacing.target_duration_seconds",
            low=5.0,
            high=600.0,
            blockers=blockers,
        )

    render = spec.get("render_defaults")
    _unknown_keys(render, RENDER_DEFAULT_SPECS, "render_defaults", blockers)
    if isinstance(render, Mapping):
        if not render:
            blockers.append("render_defaults must not be empty")
        for key, value in render.items():
            if key not in RENDER_DEFAULT_SPECS:
                continue
            kind, constraint = RENDER_DEFAULT_SPECS[key]
            if kind == "bool" and not isinstance(value, bool):
                blockers.append(f"render_defaults.{key} must be true or false")
            elif kind == "choice" and value not in constraint:
                blockers.append(
                    f"render_defaults.{key} must be one of: {', '.join(sorted(constraint))}"
                )
            elif kind == "number":
                low, high = constraint
                _finite_number(
                    value,
                    label=f"render_defaults.{key}",
                    low=low,
                    high=high,
                    blockers=blockers,
                )

    captions = spec.get("caption_defaults")
    _unknown_keys(captions, CAPTION_KEYS, "caption_defaults", blockers)
    if isinstance(captions, Mapping):
        _string_list(
            captions.get("preferred_windows"),
            label="caption_defaults.preferred_windows",
            blockers=blockers,
            maximum=12,
        )
        spellings = captions.get("force_spelling")
        if not isinstance(spellings, Mapping):
            blockers.append("caption_defaults.force_spelling must be an object")
        else:
            if len(spellings) > 100:
                blockers.append("caption_defaults.force_spelling must contain at most 100 entries")
            normalized_sources: List[str] = []
            for source, replacement in spellings.items():
                source_text = _text(source)
                replacement_text = _text(replacement)
                if not source_text or not replacement_text:
                    blockers.append("caption_defaults.force_spelling cannot contain empty text")
                    break
                normalized_sources.append(source_text.casefold())
            if len(set(normalized_sources)) != len(normalized_sources):
                blockers.append("caption_defaults.force_spelling contains case-insensitive duplicate keys")

    approval = spec.get("approval")
    _unknown_keys(approval, APPROVAL_KEYS, "approval", blockers)
    basis = ""
    if isinstance(approval, Mapping):
        basis = _text(approval.get("basis"))
        if basis not in APPROVAL_BASES:
            blockers.append(f"approval.basis must be one of: {', '.join(sorted(APPROVAL_BASES))}")
        if not _text(approval.get("approved_by")):
            blockers.append("approval.approved_by is required")
        approved_at = _parse_date(approval.get("approved_at"), label="approval.approved_at", blockers=blockers)
        if approved_at is not None and approved_at > today:
            blockers.append("approval.approved_at cannot be in the future")
        if not _text(approval.get("note")):
            blockers.append("approval.note is required")

    evidence = spec.get("evidence")
    if not isinstance(evidence, list):
        blockers.append("evidence must be a list")
        evidence = []
    seen_evidence: set[Tuple[str, str]] = set()
    for index, item in enumerate(evidence):
        label = f"evidence[{index}]"
        _unknown_keys(item, EVIDENCE_KEYS, label, blockers)
        if not isinstance(item, Mapping):
            continue
        if not _text(item.get("label")):
            blockers.append(f"{label}.label is required")
        role = _text(item.get("role"))
        if role not in EVIDENCE_ROLES:
            blockers.append(f"{label}.role must be one of: {', '.join(sorted(EVIDENCE_ROLES))}")
        digest = _text(item.get("sha256"))
        if not DIGEST_RE.fullmatch(digest):
            blockers.append(f"{label}.sha256 must be sha256:<64 lowercase hex>")
        if not _text(item.get("note")):
            blockers.append(f"{label}.note is required")
        identity = (role, digest)
        if identity in seen_evidence:
            blockers.append(f"{label} duplicates an earlier role/digest pair")
        seen_evidence.add(identity)
    if basis in {"approved_outputs", "reference_study"} and not evidence:
        blockers.append(f"approval basis {basis} requires at least one evidence record")

    return sorted(set(blockers)), sorted(set(warnings))


def _normalized_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    creative = spec.get("creative_direction") if isinstance(spec.get("creative_direction"), Mapping) else {}
    pacing = spec.get("pacing") if isinstance(spec.get("pacing"), Mapping) else {}
    render = spec.get("render_defaults") if isinstance(spec.get("render_defaults"), Mapping) else {}
    captions = spec.get("caption_defaults") if isinstance(spec.get("caption_defaults"), Mapping) else {}
    approval = spec.get("approval") if isinstance(spec.get("approval"), Mapping) else {}
    evidence = spec.get("evidence") if isinstance(spec.get("evidence"), list) else []

    return {
        "version": SPEC_VERSION,
        "name": _text(spec.get("name")),
        "description": _text(spec.get("description")),
        "creative_direction": {
            "primary": _text(creative.get("primary")),
            "accent": _text(creative.get("accent")),
            "principles": [_text(item) for item in creative.get("principles", [])],
            "avoid": [_text(item) for item in creative.get("avoid", [])],
        },
        "pacing": {
            "cut_aggressiveness": _text(pacing.get("cut_aggressiveness")),
            "broll_density": _text(pacing.get("broll_density")),
            "hook_interval_seconds": float(pacing.get("hook_interval_seconds", 0)),
            "body_interval_seconds": float(pacing.get("body_interval_seconds", 0)),
            "target_duration_seconds": float(pacing.get("target_duration_seconds", 0)),
        },
        "render_defaults": copy.deepcopy(dict(render)),
        "caption_defaults": {
            "preferred_windows": [_text(item) for item in captions.get("preferred_windows", [])],
            "force_spelling": {
                _text(source): _text(replacement)
                for source, replacement in (captions.get("force_spelling") or {}).items()
            },
        },
        "approval": {
            "basis": _text(approval.get("basis")),
            "approved_by": _text(approval.get("approved_by")),
            "approved_at": _text(approval.get("approved_at")),
            "note": _text(approval.get("note")),
        },
        "evidence": [
            {
                "label": _text(item.get("label")),
                "role": _text(item.get("role")),
                "sha256": _text(item.get("sha256")),
                "note": _text(item.get("note")),
            }
            for item in evidence
            if isinstance(item, Mapping)
        ],
    }


def create_profile(
    spec: Mapping[str, Any],
    *,
    generated_at: Optional[str] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    blockers, warnings = _validate_spec(spec, today=today)
    if blockers:
        raise StyleProfileError("; ".join(blockers))
    normalized = _normalized_spec(spec)
    profile: Dict[str, Any] = {
        **normalized,
        "version": PROFILE_VERSION,
        "generated_at": generated_at or utc_now(),
    }
    profile["profile_id"] = profile_id(profile)
    profile["status"] = "review" if warnings else "ready"
    profile["summary"] = {"blocking": 0, "warnings": len(warnings)}
    profile["notes"] = warnings + [
        "Profile approval labels and evidence hashes are self-attested provenance, not identity authentication, a digital signature, or live rights verification.",
        "Profile defaults never override explicit project render-config values or CLI flags.",
    ]
    return profile


def _profile_as_spec(profile: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "name": profile.get("name"),
        "description": profile.get("description"),
        "creative_direction": profile.get("creative_direction"),
        "pacing": profile.get("pacing"),
        "render_defaults": profile.get("render_defaults"),
        "caption_defaults": profile.get("caption_defaults"),
        "approval": profile.get("approval"),
        "evidence": profile.get("evidence"),
    }


def verify_profile(
    profile: Mapping[str, Any],
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    allowed = TOP_LEVEL_KEYS.union(PROFILE_DERIVED_KEYS)
    unknown = sorted(set(str(key) for key in profile).difference(allowed))
    if unknown:
        blockers.append(f"profile_unknown_key:{unknown[0]}")
    if profile.get("version") != PROFILE_VERSION:
        blockers.append(f"profile_version_must_be:{PROFILE_VERSION}")

    spec_blockers, spec_warnings = _validate_spec(_profile_as_spec(profile), today=today)
    blockers.extend(f"schema:{item}" for item in spec_blockers)
    warnings.extend(spec_warnings)

    try:
        expected_id = profile_id(profile)
    except StyleProfileError as exc:
        blockers.append(f"profile_id_unavailable:{exc}")
        expected_id = ""
    if _text(profile.get("profile_id")) != expected_id:
        blockers.append("profile_id_mismatch")

    expected_status = "review" if warnings else "ready"
    expected_summary = {"blocking": 0, "warnings": len(warnings)}
    if profile.get("status") != expected_status:
        blockers.append("stored_status_mismatch")
    if profile.get("summary") != expected_summary:
        blockers.append("stored_summary_mismatch")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    status = "blocked" if blockers else ("review" if warnings else "ready")
    return {
        "version": "edit_style_profile_verification.v1",
        "profile_id": expected_id,
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {"blocking": len(blockers), "warnings": len(warnings)},
    }


def apply_profile(
    profile: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    verification = verify_profile(profile, today=today)
    if verification["summary"]["blocking"]:
        raise StyleProfileError(
            "style profile is blocked: " + "; ".join(verification["blockers"])
        )
    if not isinstance(config, Mapping):
        raise StyleProfileError("render config must be an object")

    output = copy.deepcopy(dict(config))
    defaults = profile.get("render_defaults") or {}
    applied: Dict[str, Any] = {}
    preserved: Dict[str, Any] = {}
    for key in RENDER_DEFAULT_SPECS:
        if key not in defaults:
            continue
        if key not in output or output[key] is None:
            output[key] = copy.deepcopy(defaults[key])
            applied[key] = copy.deepcopy(defaults[key])
        else:
            preserved[key] = copy.deepcopy(output[key])
    output["style_profile"] = {
        "name": profile.get("name"),
        "profile_id": profile.get("profile_id"),
    }
    return {
        "config": output,
        "applied": applied,
        "preserved": preserved,
        "verification": verification,
    }


def apply_force_spelling(text: str, profile: Mapping[str, Any]) -> str:
    """Apply creator-owned spelling corrections longest-key first."""
    defaults = profile.get("caption_defaults")
    spellings = defaults.get("force_spelling") if isinstance(defaults, Mapping) else {}
    if not isinstance(spellings, Mapping):
        return text
    result = text
    ordered = sorted(spellings.items(), key=lambda item: len(str(item[0])), reverse=True)
    for source, replacement in ordered:
        source_text = _text(source)
        if not source_text:
            continue
        result = re.sub(re.escape(source_text), _text(replacement), result, flags=re.IGNORECASE)
    return result


def preferred_windows(profile: Mapping[str, Any]) -> List[str]:
    defaults = profile.get("caption_defaults")
    values = defaults.get("preferred_windows") if isinstance(defaults, Mapping) else []
    return [_text(item) for item in values if _text(item)] if isinstance(values, list) else []


def template_spec() -> Dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "name": "creator-tech",
        "description": "Clean editorial tech explainers with restrained motion and legible mobile-first typography.",
        "creative_direction": {
            "primary": "clean editorial technology",
            "accent": "warm human detail",
            "principles": [
                "One clear visual idea per beat",
                "Use motion to explain, not decorate",
                "Keep captions readable over every background",
            ],
            "avoid": [
                "generic AI-purple gradients",
                "decorative transitions without narrative purpose",
                "dense text competing with subtitles",
            ],
        },
        "pacing": {
            "cut_aggressiveness": "medium",
            "broll_density": "medium",
            "hook_interval_seconds": 0.8,
            "body_interval_seconds": 2.5,
            "target_duration_seconds": 90,
        },
        "render_defaults": {
            "subtitle_style": "bold_pop",
            "cover_style": "techcard",
            "cover_duration": 2.0,
            "bgm_volume": 0.12,
            "bgm_fade_out": 3.0,
            "bgm_ducking": True,
            "speech_denoise": "off",
            "color_grade": "natural",
            "versioned_output": True,
        },
        "caption_defaults": {
            "preferred_windows": ["weekday 21:00-22:30"],
            "force_spelling": {"open ai": "OpenAI"},
        },
        "approval": {
            "basis": "manual_direction",
            "approved_by": "<reviewer-label>",
            "approved_at": date.today().isoformat(),
            "note": "Reviewed as the creator-owned default; project-specific decisions may override it.",
        },
        "evidence": [],
    }


def emit_markdown(profile: Mapping[str, Any], verification: Mapping[str, Any]) -> str:
    creative = profile.get("creative_direction") or {}
    pacing = profile.get("pacing") or {}
    approval = profile.get("approval") or {}
    lines = [
        "# Edit Style Profile",
        "",
        f"- Name: `{profile.get('name', '')}`",
        f"- Profile ID: `{profile.get('profile_id', '')}`",
        f"- Status: **{verification.get('status', '')}**",
        f"- Description: {profile.get('description', '')}",
        f"- Primary direction: {creative.get('primary', '')}",
        f"- Accent direction: {creative.get('accent', '') or 'none'}",
        f"- Approval basis: `{approval.get('basis', '')}`",
        f"- Approved by: `{approval.get('approved_by', '')}` on `{approval.get('approved_at', '')}`",
        "",
        "## Creative rules",
        "",
    ]
    for item in creative.get("principles") or []:
        lines.append(f"- DO: {item}")
    for item in creative.get("avoid") or []:
        lines.append(f"- AVOID: {item}")
    lines.extend(
        [
            "",
            "## Pacing",
            "",
            "| setting | value |",
            "|---|---|",
        ]
    )
    for key, value in pacing.items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## Render defaults",
            "",
            "These values only fill missing config fields; explicit project config and CLI flags win.",
            "",
            "| setting | value |",
            "|---|---|",
        ]
    )
    for key, value in (profile.get("render_defaults") or {}).items():
        lines.append(f"| `{key}` | `{json.dumps(value, ensure_ascii=False)}` |")
    if verification.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in verification["blockers"])
    if verification.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in verification["warnings"])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "Approval labels and evidence hashes are self-attested provenance, not identity authentication, a digital signature, or live rights verification.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_apply_markdown(receipt: Mapping[str, Any]) -> str:
    lines = [
        "# Edit Style Profile Apply",
        "",
        f"- Profile: `{receipt.get('profile_name', '')}`",
        f"- Profile ID: `{receipt.get('profile_id', '')}`",
        f"- Input config SHA-256: `{receipt.get('input_config_sha256', '')}`",
        f"- Output config SHA-256: `{receipt.get('output_config_sha256', '')}`",
        f"- Applied defaults: {len(receipt.get('applied', {}))}",
        f"- Preserved project overrides: {len(receipt.get('preserved', {}))}",
        "",
        "## Applied",
        "",
    ]
    for key, value in (receipt.get("applied") or {}).items():
        lines.append(f"- `{key}` = `{json.dumps(value, ensure_ascii=False)}`")
    lines.extend(["", "## Preserved explicit config", ""])
    for key, value in (receipt.get("preserved") or {}).items():
        lines.append(f"- `{key}` = `{json.dumps(value, ensure_ascii=False)}`")
    lines.extend(
        [
            "",
            "The styled config still requires edit_preflight, render, QA, and human review.",
            "",
        ]
    )
    return "\n".join(lines)


def _ensure_distinct(inputs: Sequence[str], outputs: Sequence[Optional[str]]) -> None:
    input_paths = [Path(path).expanduser().resolve(strict=True) for path in inputs if path]
    output_paths = [Path(path).expanduser().resolve(strict=False) for path in outputs if path]
    if len(output_paths) != len(set(output_paths)):
        raise StyleProfileError("output paths must be distinct")
    for output in output_paths:
        for source in input_paths:
            if output == source:
                raise StyleProfileError(f"output must not overwrite an input: {output}")
            if output.exists() and source.exists() and output.samefile(source):
                raise StyleProfileError(f"output must not overwrite a hard-linked input: {output}")


def _write(path: str, payload: str, *, force: bool = False) -> None:
    destination = Path(path).expanduser()
    if destination.is_symlink():
        raise StyleProfileError(f"refusing to write through symlink: {destination}")
    if destination.exists() and not force:
        raise StyleProfileError(f"refusing to overwrite existing file without --force: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")


def _write_json(path: str, value: Mapping[str, Any], *, force: bool = False) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", force=force)


def _exit_for(verification: Mapping[str, Any], strict: bool) -> int:
    if verification.get("summary", {}).get("blocking"):
        return 2
    if strict and verification.get("summary", {}).get("warnings"):
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create, verify, and apply portable edit style profiles")
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="Write an editable edit-style spec template")
    template.add_argument("--output", required=True)
    template.add_argument("--force", action="store_true")

    create = sub.add_parser("create", help="Validate a spec and create a canonical profile")
    create.add_argument("--spec", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--markdown")
    create.add_argument("--force", action="store_true")
    create.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Recompute schema, summary, and canonical profile id")
    verify.add_argument("--profile", required=True)
    verify.add_argument("--output")
    verify.add_argument("--markdown")
    verify.add_argument("--force", action="store_true")
    verify.add_argument("--strict", action="store_true")

    apply_cmd = sub.add_parser("apply", help="Fill missing render-config fields from a verified profile")
    apply_cmd.add_argument("--profile", required=True)
    apply_cmd.add_argument("--config", required=True)
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.add_argument("--receipt", required=True)
    apply_cmd.add_argument("--markdown")
    apply_cmd.add_argument("--force", action="store_true")
    apply_cmd.add_argument("--strict", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            _write_json(args.output, template_spec(), force=args.force)
            print(f"edit style spec template -> {args.output}")
            return 0

        if args.command == "create":
            _ensure_distinct([args.spec], [args.output, args.markdown])
            profile = create_profile(_load_json(args.spec))
            verification = verify_profile(profile)
            _write_json(args.output, profile, force=args.force)
            if args.markdown:
                _write(args.markdown, emit_markdown(profile, verification), force=args.force)
            print(
                f"edit style profile -> {args.output} "
                f"({verification['status']}, profile_id={profile['profile_id']})"
            )
            return _exit_for(verification, args.strict)

        if args.command == "verify":
            _ensure_distinct([args.profile], [args.output, args.markdown])
            profile = _load_json(args.profile)
            verification = verify_profile(profile)
            if args.output:
                _write_json(args.output, verification, force=args.force)
            if args.markdown:
                _write(args.markdown, emit_markdown(profile, verification), force=args.force)
            print(
                f"edit style profile {verification['status']}: "
                f"blocking={verification['summary']['blocking']} "
                f"warnings={verification['summary']['warnings']}"
            )
            return _exit_for(verification, args.strict)

        _ensure_distinct(
            [args.profile, args.config],
            [args.output, args.receipt, args.markdown],
        )
        profile = _load_json(args.profile)
        config = _load_json(args.config)
        result = apply_profile(profile, config)
        output_config = result["config"]
        receipt: Dict[str, Any] = {
            "version": APPLY_VERSION,
            "generated_at": utc_now(),
            "profile_name": profile.get("name"),
            "profile_id": profile.get("profile_id"),
            "input_config_sha256": sha256_json(config),
            "output_config_sha256": sha256_json(output_config),
            "applied": result["applied"],
            "preserved": result["preserved"],
            "status": "ready",
            "summary": {
                "blocking": 0,
                "warnings": result["verification"]["summary"]["warnings"],
                "applied": len(result["applied"]),
                "preserved": len(result["preserved"]),
            },
        }
        receipt["receipt_id"] = "espa_" + hashlib.sha256(_canonical(receipt)).hexdigest()
        _write_json(args.output, output_config, force=args.force)
        _write_json(args.receipt, receipt, force=args.force)
        if args.markdown:
            _write(args.markdown, emit_apply_markdown(receipt), force=args.force)
        print(
            f"styled render config -> {args.output} "
            f"(applied={len(result['applied'])}, preserved={len(result['preserved'])})"
        )
        return _exit_for(result["verification"], args.strict)
    except (OSError, StyleProfileError, ValueError) as exc:
        print(f"edit style profile error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
