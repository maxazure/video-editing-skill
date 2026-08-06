#!/usr/bin/env python3
"""Export and replay portable, content-addressed render-config recipes.

The recipe keeps the reviewed timeline and render settings while replacing
every local file reference with a typed parameter slot. Replays require exact
bindings, verify the recipe digest, hash the bound files, and run the existing
edit preflight before the generated render config is considered ready.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


VERSION = "edit_recipe.v1"
REPLAY_VERSION = "edit_recipe_replay.v1"
KIND = "portable_render_config"
MAX_CONFIG_BYTES = 4 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLACEHOLDER_RE = re.compile(r"^\$\{([a-z][a-z0-9_]*)\}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

VIDEO_EXTS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
AUDIO_EXTS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
IMAGE_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
SUBTITLE_EXTS = {".ass", ".srt", ".vtt"}
PORTABLE_FILE_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | SUBTITLE_EXTS | {
    ".3dl",
    ".cube",
    ".edl",
    ".fcpxml",
    ".json",
    ".md",
    ".otf",
    ".otio",
    ".ttf",
    ".txt",
    ".yaml",
    ".yml",
}

IDENTITY_FIELDS = (
    "version",
    "kind",
    "name",
    "description",
    "source",
    "template",
    "slots",
    "policies",
    "required_checks",
    "review_gates",
)


class RecipeError(ValueError):
    """Raised when a recipe or binding cannot be used safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Dict[str, Any]:
    source = Path(path).expanduser()
    if source.stat().st_size > MAX_CONFIG_BYTES:
        raise RecipeError(f"JSON exceeds {MAX_CONFIG_BYTES} bytes: {source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecipeError(f"cannot read JSON object {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecipeError(f"JSON root must be an object: {source}")
    return value


def write_json(path: str, payload: Mapping[str, Any], *, force: bool = False) -> None:
    destination = Path(path).expanduser()
    if destination.exists() and not force:
        raise RecipeError(f"refusing to overwrite existing file without --force: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: str, payload: str, *, force: bool = False) -> None:
    destination = Path(path).expanduser()
    if destination.exists() and not force:
        raise RecipeError(f"refusing to overwrite existing file without --force: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")


def ensure_distinct_paths(*, inputs: Sequence[str], outputs: Sequence[Optional[str]]) -> None:
    input_paths = {str(Path(path).expanduser().resolve()) for path in inputs if path}
    output_paths = [str(Path(path).expanduser().resolve()) for path in outputs if path]
    if len(output_paths) != len(set(output_paths)):
        raise RecipeError("output, receipt, and Markdown paths must be distinct")
    overlap = sorted(input_paths.intersection(output_paths))
    if overlap:
        raise RecipeError(f"refusing to overwrite an input file: {overlap[0]}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _identity_payload(recipe: Mapping[str, Any]) -> Dict[str, Any]:
    return {field: recipe.get(field) for field in IDENTITY_FIELDS}


def portable_sha256(recipe: Mapping[str, Any]) -> str:
    return sha256_json(_identity_payload(recipe))


def _location(parts: Sequence[Any]) -> str:
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + str(part)
    return text or "template"


def _looks_like_local_path(value: str) -> bool:
    text = value.strip()
    if not text or PLACEHOLDER_RE.fullmatch(text):
        return False
    if text.startswith(("/", "~/", "./", "../", "file://")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    suffix = Path(text).suffix.lower()
    return suffix in PORTABLE_FILE_EXTS


def _media_kind(path: Path, key: str) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in SUBTITLE_EXTS:
        return "subtitle"
    if suffix == ".json" and key.lower() in {"transcript", "transcript_path"}:
        return "transcript"
    return "file"


def _slot_name(kind: str, counts: MutableMapping[str, int]) -> str:
    counts[kind] = counts.get(kind, 0) + 1
    return f"{kind}_{counts[kind]}"


def _resolve_input_path(value: str, base_dir: Path, location: str) -> Path:
    text = value.strip()
    if text.startswith(("http://", "https://")):
        raise RecipeError(f"remote input is not portable; download and relink {location}: {text}")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RecipeError(f"linked file is missing at {location}: {path}") from exc
    if not resolved.is_file():
        raise RecipeError(f"linked input is not a regular file at {location}: {resolved}")
    return resolved


def _parameterize(
    value: Any,
    *,
    base_dir: Path,
    parts: Tuple[Any, ...] = (),
    key: str = "",
    by_path: Optional[MutableMapping[str, Dict[str, Any]]] = None,
    counts: Optional[MutableMapping[str, int]] = None,
) -> Tuple[Any, List[Dict[str, Any]]]:
    by_path = by_path if by_path is not None else {}
    counts = counts if counts is not None else {}

    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for child_key, child in value.items():
            result[str(child_key)], _ = _parameterize(
                child,
                base_dir=base_dir,
                parts=(*parts, str(child_key)),
                key=str(child_key),
                by_path=by_path,
                counts=counts,
            )
        return result, list(by_path.values())
    if isinstance(value, list):
        result_list = []
        for index, child in enumerate(value):
            converted, _ = _parameterize(
                child,
                base_dir=base_dir,
                parts=(*parts, index),
                key=key,
                by_path=by_path,
                counts=counts,
            )
            result_list.append(converted)
        return result_list, list(by_path.values())
    if not isinstance(value, str):
        return value, list(by_path.values())

    text = value.strip()
    if text.startswith(("http://", "https://")):
        if key.lower() in {"title", "subtitle", "text", "label", "description", "url"}:
            return value, list(by_path.values())
        raise RecipeError(f"remote input is not portable at {_location(parts)}: {text}")
    if not _looks_like_local_path(text):
        return value, list(by_path.values())

    location = _location(parts)
    resolved = _resolve_input_path(text, base_dir, location)
    path_key = str(resolved)
    existing = by_path.get(path_key)
    if existing is None:
        kind = _media_kind(resolved, key)
        existing = {
            "name": _slot_name(kind, counts),
            "media_kind": kind,
            "source_sha256": sha256_file(resolved),
            "size_bytes": resolved.stat().st_size,
            "suffix": resolved.suffix.lower(),
            "occurrences": [],
        }
        by_path[path_key] = existing
    existing["occurrences"].append(location)
    return "${" + str(existing["name"]) + "}", list(by_path.values())


def _walk_strings(value: Any, parts: Tuple[Any, ...] = ()) -> Iterable[Tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_strings(child, (*parts, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, (*parts, index))
    elif isinstance(value, str):
        yield _location(parts), value


def _check(code: str, message: str, *, location: str = "recipe") -> Dict[str, str]:
    return {"severity": "block", "code": code, "location": location, "message": message}


def verify_recipe(recipe: Mapping[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, str]] = []
    if recipe.get("version") != VERSION:
        checks.append(_check("invalid_version", f"version must be {VERSION}"))
    if recipe.get("kind") != KIND:
        checks.append(_check("invalid_kind", f"kind must be {KIND}"))

    name = recipe.get("name")
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        checks.append(_check("invalid_name", "name must use lowercase letters, digits, and hyphens"))
    if not isinstance(recipe.get("description"), str):
        checks.append(_check("invalid_description", "description must be a string"))
    source = recipe.get("source")
    if not isinstance(source, Mapping) or not DIGEST_RE.fullmatch(str(source.get("render_config_sha256") or "")):
        checks.append(_check("invalid_source", "source.render_config_sha256 must be a SHA-256 digest"))
    source_preflight = source.get("preflight") if isinstance(source, Mapping) else None
    if not isinstance(source_preflight, Mapping) or source_preflight.get("version") != "edit_preflight.v1":
        checks.append(_check("invalid_source_preflight", "source.preflight must summarize edit_preflight.v1"))
        source_warnings = 0
    else:
        try:
            source_blocking = int(source_preflight.get("blocking") or 0)
            source_warnings = int(source_preflight.get("warnings") or 0)
        except (TypeError, ValueError):
            source_blocking = 1
            source_warnings = 0
            checks.append(_check("invalid_source_preflight", "source preflight counts must be integers"))
        if source_blocking < 0 or source_warnings < 0:
            checks.append(_check("invalid_source_preflight", "source preflight counts cannot be negative"))
        if source_blocking:
            checks.append(_check("blocked_source_preflight", "source render config had blocking preflight findings"))
        expected_status = "warn" if source_warnings else "ready"
        if source_blocking == 0 and source_warnings >= 0 and source_preflight.get("status") != expected_status:
            checks.append(_check("invalid_source_preflight", "source preflight status does not match its counts"))
        if not isinstance(source_preflight.get("render_config_clips"), int) or int(source_preflight.get("render_config_clips") or 0) <= 0:
            checks.append(_check("invalid_source_preflight", "source preflight must report at least one render-config clip"))

    template = recipe.get("template")
    if not isinstance(template, Mapping):
        checks.append(_check("invalid_template", "template must be a JSON object"))
        template = {}
    clips = template.get("clips") if isinstance(template, Mapping) else None
    if not isinstance(clips, list) or not clips:
        checks.append(_check("empty_clips", "template must keep a non-empty clips list", location="template.clips"))

    raw_slots = recipe.get("slots")
    slots = raw_slots if isinstance(raw_slots, list) else []
    if not isinstance(raw_slots, list) or not slots:
        checks.append(_check("invalid_slots", "slots must be a non-empty list"))

    slot_names: List[str] = []
    expected_occurrences: Dict[str, List[str]] = {}
    for index, slot in enumerate(slots):
        location = f"slots[{index}]"
        if not isinstance(slot, Mapping):
            checks.append(_check("invalid_slot", "slot must be an object", location=location))
            continue
        slot_name = str(slot.get("name") or "")
        if re.fullmatch(r"[a-z][a-z0-9_]*", slot_name) is None:
            checks.append(_check("invalid_slot_name", "slot name is invalid", location=f"{location}.name"))
        slot_names.append(slot_name)
        if slot.get("media_kind") not in {"video", "audio", "image", "subtitle", "transcript", "file"}:
            checks.append(_check("invalid_media_kind", "slot media_kind is invalid", location=f"{location}.media_kind"))
        if not DIGEST_RE.fullmatch(str(slot.get("source_sha256") or "")):
            checks.append(_check("invalid_slot_digest", "slot source_sha256 is invalid", location=f"{location}.source_sha256"))
        if not isinstance(slot.get("size_bytes"), int) or int(slot.get("size_bytes", -1)) < 0:
            checks.append(_check("invalid_slot_size", "slot size_bytes must be a non-negative integer", location=f"{location}.size_bytes"))
        occurrences = slot.get("occurrences")
        if not isinstance(occurrences, list) or not all(isinstance(item, str) and item for item in occurrences):
            checks.append(_check("invalid_occurrences", "slot occurrences must be non-empty strings", location=f"{location}.occurrences"))
        else:
            expected_occurrences[slot_name] = sorted(occurrences)

    if len(slot_names) != len(set(slot_names)):
        checks.append(_check("duplicate_slot", "slot names must be unique"))

    actual_occurrences: Dict[str, List[str]] = {}
    for location, text in _walk_strings(template):
        match = PLACEHOLDER_RE.fullmatch(text)
        if match:
            actual_occurrences.setdefault(match.group(1), []).append(location)
        elif "${" in text:
            checks.append(_check("embedded_placeholder", "placeholders must occupy the entire string", location=location))
        elif text.strip().startswith(("http://", "https://")) and location.rsplit(".", 1)[-1] not in {
            "description",
            "label",
            "subtitle",
            "text",
            "title",
            "url",
        }:
            checks.append(_check("remote_path", "template contains a remote input instead of a local-file slot", location=location))
        elif _looks_like_local_path(text):
            checks.append(_check("path_leak", "template contains an unparameterized local path", location=location))

    if set(actual_occurrences) != set(slot_names):
        missing = sorted(set(slot_names) - set(actual_occurrences))
        unknown = sorted(set(actual_occurrences) - set(slot_names))
        checks.append(_check("slot_mismatch", f"unused slots={missing}; unknown placeholders={unknown}"))
    for slot_name in set(actual_occurrences) & set(expected_occurrences):
        if sorted(actual_occurrences[slot_name]) != expected_occurrences[slot_name]:
            checks.append(_check("occurrence_mismatch", f"recorded occurrences do not match template for {slot_name}"))

    policies = recipe.get("policies")
    if not isinstance(policies, Mapping) or policies.get("all_bindings_required") is not True or policies.get("local_files_only") is not True:
        checks.append(_check("invalid_policies", "recipe must require all local-file bindings"))
    required_checks = recipe.get("required_checks")
    if not isinstance(required_checks, list) or "edit_preflight" not in required_checks:
        checks.append(_check("missing_required_check", "required_checks must include edit_preflight"))
    review_gates = recipe.get("review_gates")
    if not isinstance(review_gates, list) or "human_preview" not in review_gates:
        checks.append(_check("missing_review_gate", "review_gates must include human_preview"))

    expected_digest = portable_sha256(recipe)
    if recipe.get("portable_sha256") != expected_digest:
        checks.append(_check("digest_mismatch", "portable_sha256 does not match recipe content"))

    blocking = len(checks)
    return {
        "version": "edit_recipe_verification.v1",
        "status": "blocked" if blocking else "ready",
        "summary": {
            "blocking": blocking,
            "warnings": source_warnings,
            "slots": len(slots),
            "placeholders": sum(len(items) for items in actual_occurrences.values()),
        },
        "portable_sha256": expected_digest,
        "checks": checks,
    }


def export_recipe(config_path: str, *, name: str, description: str = "") -> Dict[str, Any]:
    if NAME_RE.fullmatch(name) is None:
        raise RecipeError("name must use lowercase letters, digits, and hyphens")
    config_source = Path(config_path).expanduser().resolve(strict=True)
    config = load_json(str(config_source))
    if not isinstance(config.get("clips"), list) or not config.get("clips"):
        raise RecipeError("render config must contain a non-empty clips list")
    from edit_preflight import build_preflight

    source_preflight = build_preflight(render_config=str(config_source))
    if int(source_preflight["summary"].get("blocking") or 0):
        raise RecipeError(
            "render config failed edit preflight: "
            + "; ".join(str(item.get("message") or item.get("code")) for item in source_preflight.get("checks") or [])
        )

    template, slots = _parameterize(copy.deepcopy(config), base_dir=config_source.parent)
    if not slots:
        raise RecipeError("render config has no local file references to parameterize")
    recipe: Dict[str, Any] = {
        "version": VERSION,
        "kind": KIND,
        "created_at": utc_now(),
        "name": name,
        "description": description,
        "source": {
            "render_config_sha256": sha256_file(config_source),
            "preflight": {
                "version": source_preflight.get("version"),
                "status": source_preflight.get("status"),
                "blocking": int(source_preflight["summary"].get("blocking") or 0),
                "warnings": int(source_preflight["summary"].get("warnings") or 0),
                "render_config_clips": int(source_preflight["summary"].get("render_config_clips") or 0),
                "timeline_duration": source_preflight["summary"].get("timeline_duration"),
            },
        },
        "template": template,
        "slots": slots,
        "policies": {
            "all_bindings_required": True,
            "local_files_only": True,
            "exact_template_replay": True,
            "binding_hashes_recorded": True,
        },
        "required_checks": ["edit_preflight"],
        "review_gates": ["human_preview"],
    }
    recipe["portable_sha256"] = portable_sha256(recipe)
    verification = verify_recipe(recipe)
    if verification["status"] != "ready":
        messages = "; ".join(item["message"] for item in verification["checks"])
        raise RecipeError(f"exported recipe failed verification: {messages}")
    recipe["summary"] = verification["summary"]
    return recipe


def parse_bindings(items: Sequence[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise RecipeError(f"binding must be SLOT=PATH: {item}")
        name, path = item.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise RecipeError(f"binding must be SLOT=PATH: {item}")
        if name in result:
            raise RecipeError(f"duplicate binding: {name}")
        result[name] = path
    return result


def _kind_matches(kind: str, path: Path) -> bool:
    suffix = path.suffix.lower()
    if kind == "video":
        return suffix in VIDEO_EXTS
    if kind == "audio":
        return suffix in AUDIO_EXTS
    if kind == "image":
        return suffix in IMAGE_EXTS
    if kind == "subtitle":
        return suffix in SUBTITLE_EXTS
    if kind == "transcript":
        return suffix == ".json"
    return True


def replay_recipe(
    recipe: Mapping[str, Any],
    bindings: Mapping[str, str],
    *,
    binding_base_dir: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    verification = verify_recipe(recipe)
    if verification["status"] != "ready":
        raise RecipeError("recipe verification failed: " + "; ".join(item["message"] for item in verification["checks"]))

    slots = {str(slot["name"]): slot for slot in recipe["slots"]}
    supplied = set(bindings)
    required = set(slots)
    missing = sorted(required - supplied)
    unknown = sorted(supplied - required)
    if missing or unknown:
        raise RecipeError(f"binding mismatch: missing={missing}; unknown={unknown}")

    base_dir = Path(binding_base_dir or os.getcwd()).expanduser().resolve()
    resolved: Dict[str, str] = {}
    records: List[Dict[str, Any]] = []
    for name in sorted(slots):
        raw = str(bindings[name])
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise RecipeError(f"binding file is missing for {name}: {path}") from exc
        if not path.is_file():
            raise RecipeError(f"binding is not a regular file for {name}: {path}")
        kind = str(slots[name].get("media_kind"))
        if not _kind_matches(kind, path):
            raise RecipeError(f"binding type mismatch for {name}: expected {kind}, got {path.suffix.lower() or 'no extension'}")
        resolved[name] = str(path)
        records.append(
            {
                "slot": name,
                "media_kind": kind,
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        if isinstance(value, str):
            match = PLACEHOLDER_RE.fullmatch(value)
            if match:
                return resolved[match.group(1)]
        return value

    output = replace(recipe["template"])
    if not isinstance(output, dict):
        raise RecipeError("replayed render config is not a JSON object")
    return output, records


def emit_recipe_markdown(recipe: Mapping[str, Any], verification: Optional[Mapping[str, Any]] = None) -> str:
    verification = verification or verify_recipe(recipe)
    lines = [
        "# Portable Edit Recipe",
        "",
        f"- Name: `{recipe.get('name', '')}`",
        f"- Status: **{str(verification.get('status', '')).upper()}**",
        f"- Portable SHA-256: `{recipe.get('portable_sha256', '')}`",
        f"- Slots: {len(recipe.get('slots') or [])}",
        "",
        "## Bindings",
        "",
        "| slot | kind | original digest | size | occurrences |",
        "|---|---|---|---:|---|",
    ]
    for slot in recipe.get("slots") or []:
        occurrences = ", ".join(f"`{item}`" for item in slot.get("occurrences") or [])
        lines.append(
            f"| `{slot.get('name')}` | {slot.get('media_kind')} | `{slot.get('source_sha256')}` | "
            f"{slot.get('size_bytes')} | {occurrences} |"
        )
    checks = verification.get("checks") or []
    if checks:
        lines.extend(["", "## Blocking Checks", ""])
        lines.extend(f"- `{item.get('code')}` at `{item.get('location')}`: {item.get('message')}" for item in checks)
    lines.extend(
        [
            "",
            "## Replay Contract",
            "",
            "- Bind every slot exactly once to an existing local file.",
            "- Replay verifies this content digest, records new binding hashes, and runs `edit_preflight.py`.",
            "- The digest proves content identity, not authorship or human approval; preview the replayed video before publish.",
        ]
    )
    return "\n".join(lines) + "\n"


def emit_replay_markdown(receipt: Mapping[str, Any]) -> str:
    summary = receipt.get("summary") or {}
    lines = [
        "# Edit Recipe Replay",
        "",
        f"- Recipe: `{receipt.get('recipe_name', '')}`",
        f"- Status: **{str(receipt.get('status', '')).upper()}**",
        f"- Portable SHA-256: `{receipt.get('portable_sha256', '')}`",
        f"- Output config: `{receipt.get('output_render_config', '')}`",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Bound Inputs",
        "",
        "| slot | kind | path | SHA-256 |",
        "|---|---|---|---|",
    ]
    for binding in receipt.get("bindings") or []:
        lines.append(
            f"| `{binding.get('slot')}` | {binding.get('media_kind')} | `{binding.get('path')}` | `{binding.get('sha256')}` |"
        )
    preflight = receipt.get("preflight") or {}
    checks = preflight.get("checks") or []
    if checks:
        lines.extend(["", "## Preflight Checks", ""])
        lines.extend(
            f"- **{item.get('severity')}** `{item.get('code')}` at `{item.get('source')}`: {item.get('message')}"
            for item in checks
        )
    lines.extend(["", "## Next", "", "- Render the generated config, then inspect the result before treating the recipe as approved."])
    return "\n".join(lines) + "\n"


def _build_replay_receipt(
    recipe: Mapping[str, Any],
    *,
    output_path: str,
    bindings: Sequence[Mapping[str, Any]],
    preflight: Mapping[str, Any],
) -> Dict[str, Any]:
    preflight_summary = preflight.get("summary") if isinstance(preflight.get("summary"), Mapping) else {}
    blocking = int(preflight_summary.get("blocking") or 0)
    warnings = int(preflight_summary.get("warnings") or 0)
    return {
        "version": REPLAY_VERSION,
        "generated_at": utc_now(),
        "status": "blocked" if blocking else ("warn" if warnings else "ready"),
        "recipe_name": recipe.get("name"),
        "portable_sha256": recipe.get("portable_sha256"),
        "output_render_config": str(Path(output_path).expanduser().resolve()),
        "bindings": list(bindings),
        "preflight": preflight,
        "summary": {
            "blocking": blocking,
            "warnings": warnings,
            "slots": len(bindings),
        },
        "notes": [
            "Binding hashes describe this replay only; they do not alter the portable recipe identity.",
            "A ready preflight is not creative approval. Render and review the output before publishing.",
        ],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export, verify, and replay portable render-config recipes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Replace local render-config paths with typed slots.")
    export_parser.add_argument("--config", required=True, help="Reviewed render_config.json.")
    export_parser.add_argument("--name", required=True, help="Recipe name using lowercase letters, digits, and hyphens.")
    export_parser.add_argument("--description", default="", help="Short human-readable recipe purpose.")
    export_parser.add_argument("--output", required=True, help="Portable *_edit_recipe.json output.")
    export_parser.add_argument("--markdown", help="Path-safe Markdown review sheet.")
    export_parser.add_argument("--force", action="store_true", help="Overwrite explicit output paths.")

    verify_parser = subparsers.add_parser("verify", help="Recompute schema and content-identity checks.")
    verify_parser.add_argument("--recipe", required=True, help="Portable edit recipe JSON.")
    verify_parser.add_argument("--markdown", help="Write verification Markdown.")
    verify_parser.add_argument("--force", action="store_true", help="Overwrite the Markdown output.")

    replay_parser = subparsers.add_parser("replay", help="Bind new local files and emit a preflighted render config.")
    replay_parser.add_argument("--recipe", required=True, help="Portable edit recipe JSON.")
    replay_parser.add_argument("--bind", action="append", default=[], metavar="SLOT=PATH", help="Bind one recipe slot; repeat for every slot.")
    replay_parser.add_argument("--binding-base-dir", help="Resolve relative binding paths from this directory (default current directory).")
    replay_parser.add_argument("--output", required=True, help="Generated render_config.json.")
    replay_parser.add_argument("--receipt", required=True, help="Write edit_recipe_replay.v1 JSON.")
    replay_parser.add_argument("--markdown", help="Write replay review Markdown.")
    replay_parser.add_argument("--strict", action="store_true", help="Exit 2 on preflight warnings as well as blockers.")
    replay_parser.add_argument("--force", action="store_true", help="Overwrite explicit output paths.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "export":
            recipe = export_recipe(args.config, name=args.name, description=args.description)
            ensure_distinct_paths(inputs=[args.config], outputs=[args.output, args.markdown])
            write_json(args.output, recipe, force=args.force)
            if args.markdown:
                write_text(args.markdown, emit_recipe_markdown(recipe), force=args.force)
            print(f"Edit recipe: ready ({len(recipe['slots'])} slots, {recipe['portable_sha256']})")
            return 0

        recipe = load_json(args.recipe)
        if args.command == "verify":
            verification = verify_recipe(recipe)
            if args.markdown:
                ensure_distinct_paths(inputs=[args.recipe], outputs=[args.markdown])
                write_text(args.markdown, emit_recipe_markdown(recipe, verification), force=args.force)
            print(
                f"Edit recipe verification: {verification['status']} "
                f"({verification['summary']['blocking']} blocking)"
            )
            return 0 if verification["status"] == "ready" else 2

        bindings = parse_bindings(args.bind)
        config, binding_records = replay_recipe(recipe, bindings, binding_base_dir=args.binding_base_dir)
        ensure_distinct_paths(
            inputs=[args.recipe, *(str(item["path"]) for item in binding_records)],
            outputs=[args.output, args.receipt, args.markdown],
        )
        write_json(args.output, config, force=args.force)
        from edit_preflight import build_preflight

        preflight = build_preflight(render_config=args.output)
        receipt = _build_replay_receipt(
            recipe,
            output_path=args.output,
            bindings=binding_records,
            preflight=preflight,
        )
        write_json(args.receipt, receipt, force=args.force)
        if args.markdown:
            write_text(args.markdown, emit_replay_markdown(receipt), force=args.force)
        print(
            f"Edit recipe replay: {receipt['status']} "
            f"({receipt['summary']['blocking']} blocking, {receipt['summary']['warnings']} warnings)"
        )
        if receipt["status"] == "blocked" or (args.strict and receipt["status"] == "warn"):
            return 2
        return 0
    except (OSError, RecipeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
