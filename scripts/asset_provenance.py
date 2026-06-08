#!/usr/bin/env python3
"""Build a publish gate for asset source, license, and attribution metadata.

The script is local-first: it does not search stock providers or download files.
It reads existing production artifacts, optional media_index metadata, and asset
sidecars, then emits a provenance manifest plus credits text for publication.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MEDIA_EXTS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
}

PATH_KEYS = {
    "path",
    "file",
    "video",
    "audio",
    "image",
    "asset",
    "src",
    "uri",
    "broll",
    "bgm",
    "music",
    "source",
    "media_path",
    "asset_path",
    "resolved_path",
    "expected_path",
    "render_output",
    "output_path",
}

METADATA_KEYS = {
    "provider",
    "source",
    "source_url",
    "landing_url",
    "url",
    "creator",
    "author",
    "artist",
    "photographer",
    "license",
    "license_url",
    "attribution",
    "attribution_text",
    "attribution_required",
    "credit",
    "commercial_ok",
    "cleared_usage",
    "usage_rights",
    "model",
    "prompt",
}

PROVIDER_POLICIES: Mapping[str, Mapping[str, Any]] = {
    "pexels": {
        "license": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "attribution_required": False,
        "credit_recommended": True,
        "source_url_recommended": True,
        "note": "Free commercial use; attribution not required but appreciated.",
    },
    "pixabay": {
        "license": "Pixabay Content License",
        "license_url": "https://pixabay.com/service/license/",
        "attribution_required": False,
        "credit_recommended": True,
        "source_url_recommended": True,
        "note": "Free commercial use subject to Pixabay prohibited uses.",
    },
    "unsplash": {
        "license": "Unsplash License",
        "license_url": "https://unsplash.com/license",
        "attribution_required": False,
        "credit_recommended": True,
        "source_url_recommended": True,
        "note": "Attribution is appreciated; API usage may have separate attribution guidance.",
    },
    "owned": {
        "license": "Owned / self-produced",
        "attribution_required": False,
        "credit_recommended": False,
        "source_url_recommended": False,
    },
    "self": {
        "license": "Owned / self-produced",
        "attribution_required": False,
        "credit_recommended": False,
        "source_url_recommended": False,
    },
    "generated": {
        "license": "Generated asset",
        "attribution_required": False,
        "credit_recommended": False,
        "source_url_recommended": False,
    },
    "codex_imagegen": {
        "license": "Generated with Codex image_gen / gpt-image-2",
        "attribution_required": False,
        "credit_recommended": False,
        "source_url_recommended": False,
    },
    "dreamina": {
        "license": "Generated with Dreamina/Jimeng provider",
        "attribution_required": False,
        "credit_recommended": False,
        "source_url_recommended": False,
    },
}

EXTERNAL_PROVIDERS = {
    "pexels",
    "pixabay",
    "unsplash",
    "giphy",
    "bing",
    "google",
    "youtube",
    "tiktok",
    "instagram",
    "web",
}

BLOCKED_LICENSE_RE = re.compile(
    r"\b(all rights reserved|editorial only|non[- ]?commercial|personal use only|"
    r"no commercial|rights managed|unknown license)\b",
    re.I,
)
ATTRIBUTION_LICENSE_RE = re.compile(
    r"\b(cc[- ]?by|creative commons attribution|by-sa|by-nc|by-nd)\b",
    re.I,
)
NO_ATTRIBUTION_LICENSE_RE = re.compile(r"\b(cc0|public domain|pexels|pixabay|unsplash)\b", re.I)
URL_RE = re.compile(r"^https?://", re.I)


@dataclass
class AssetRef:
    path: str
    usage: str
    source_artifact: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "required", "require"}:
        return True
    if text in {"0", "false", "no", "n", "none", "not_required"}:
        return False
    return None


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _normalise_provider(value: Any) -> str:
    text = _norm_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "pexel": "pexels",
        "pixabay_video": "pixabay",
        "unsplash_api": "unsplash",
        "openai": "generated",
        "gpt_image_2": "codex_imagegen",
        "gpt-image-2": "codex_imagegen",
        "imagegen": "codex_imagegen",
        "dreamina_video": "dreamina",
        "jimeng": "dreamina",
        "local": "owned",
        "self_owned": "owned",
        "original": "owned",
    }
    return aliases.get(text, text)


def _provider_from_url(url: str) -> str:
    lowered = url.lower()
    for provider in ("pexels", "pixabay", "unsplash", "giphy", "youtube", "tiktok", "instagram"):
        if provider in lowered:
            return provider
    return "web" if URL_RE.match(url) else ""


def _looks_like_media_path(value: str, *, allow_url: bool = True) -> bool:
    if not value:
        return False
    if URL_RE.match(value) and allow_url:
        return True
    suffix = Path(value.split("?", 1)[0]).suffix.lower()
    return suffix in MEDIA_EXTS


def _resolve_path(value: str, base: Path) -> str:
    if URL_RE.match(value):
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _merge_metadata(*parts: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            if value in (None, "", [], {}):
                continue
            if key == "metadata" and isinstance(_maybe_json(value), Mapping):
                nested = _maybe_json(value)
                for nested_key, nested_value in nested.items():
                    if nested_value not in (None, "", [], {}) and nested_key not in merged:
                        merged[nested_key] = nested_value
                continue
            if key not in merged:
                merged[key] = value
    return merged


def _extract_metadata(raw: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _maybe_json(raw.get("metadata"))
    nested = metadata if isinstance(metadata, Mapping) else {}
    selected = {key: raw.get(key) for key in METADATA_KEYS if key in raw}
    return _merge_metadata(selected, nested)


def _read_sidecar(abs_path: str) -> Dict[str, Any]:
    if URL_RE.match(abs_path):
        return {}
    path = Path(abs_path)
    candidates = [
        path.with_suffix(path.suffix + ".provenance.json"),
        path.with_suffix(".provenance.json"),
        path.with_suffix(".meta.json"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = load_json(str(candidate))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping):
            return _extract_metadata(data)
    return {}


def _load_media_index(project_dir: str) -> Dict[str, Dict[str, Any]]:
    root = Path(project_dir).expanduser().resolve()
    by_path: Dict[str, Dict[str, Any]] = {}
    json_path = root / "media_index.json"
    db_path = root / "media_index.db"

    items: List[Mapping[str, Any]] = []
    if json_path.is_file():
        data = load_json(str(json_path))
        if isinstance(data, Mapping):
            raw_items = data.get("items") or []
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, Mapping))
    elif db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM media").fetchall()
            items.extend(dict(row) for row in rows)
        finally:
            conn.close()

    for item in items:
        raw_path = _norm_text(item.get("path"))
        if not raw_path:
            continue
        abs_path = _resolve_path(raw_path, root)
        by_path[abs_path] = _merge_metadata(
            {
                "provider": item.get("provider"),
                "source": item.get("source"),
                "source_url": item.get("source_url"),
                "creator": item.get("creator"),
                "license": item.get("license"),
                "attribution": item.get("attribution"),
                "category": item.get("category"),
                "type": item.get("type"),
            },
            _extract_metadata(item),
        )
    return by_path


def _walk_paths(value: Any, *, base: Path, source_artifact: str, usage: str = "json") -> List[AssetRef]:
    refs: List[AssetRef] = []
    if isinstance(value, Mapping):
        inline_metadata = _extract_metadata(value)
        for key, item in value.items():
            key_l = str(key).lower()
            if isinstance(item, str) and key_l in PATH_KEYS:
                if _looks_like_media_path(item, allow_url=True):
                    refs.append(
                        AssetRef(
                            path=_resolve_path(item, base),
                            usage=key_l if key_l in PATH_KEYS else usage,
                            source_artifact=source_artifact,
                            metadata=inline_metadata,
                        )
                    )
            elif isinstance(item, str) and key_l not in METADATA_KEYS and _looks_like_media_path(item, allow_url=False):
                refs.append(
                    AssetRef(
                        path=_resolve_path(item, base),
                        usage=usage,
                        source_artifact=source_artifact,
                        metadata=inline_metadata,
                    )
                )
            elif isinstance(item, list) and key_l in {"candidate_paths", "paths", "assets"}:
                for child in item:
                    if isinstance(child, str) and _looks_like_media_path(child, allow_url=True):
                        refs.append(
                            AssetRef(
                                path=_resolve_path(child, base),
                                usage=key_l,
                                source_artifact=source_artifact,
                                metadata=inline_metadata,
                            )
                        )
                    else:
                        refs.extend(_walk_paths(child, base=base, source_artifact=source_artifact, usage=key_l))
            else:
                refs.extend(_walk_paths(item, base=base, source_artifact=source_artifact, usage=usage))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_walk_paths(item, base=base, source_artifact=source_artifact, usage=usage))
    return refs


def collect_from_asset_manifest(path: str, *, include_candidates: bool = False) -> List[AssetRef]:
    data = load_json(path)
    base = Path(path).expanduser().resolve().parent
    refs: List[AssetRef] = []
    if not isinstance(data, Mapping):
        return refs
    for item in data.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        metadata = _extract_metadata(item)
        for key in ("resolved_path", "asset_path", "path"):
            raw = _norm_text(item.get(key))
            if raw and _looks_like_media_path(raw, allow_url=True):
                refs.append(AssetRef(_resolve_path(raw, base), str(item.get("route") or key), path, metadata))
                break
        if include_candidates:
            for raw in item.get("candidate_paths") or []:
                if isinstance(raw, str) and _looks_like_media_path(raw, allow_url=True):
                    refs.append(AssetRef(_resolve_path(raw, base), "candidate", path, metadata))
    return refs


def collect_from_json_artifact(path: str) -> List[AssetRef]:
    data = load_json(path)
    base = Path(path).expanduser().resolve().parent
    return _walk_paths(data, base=base, source_artifact=path)


def collect_explicit_assets(paths: Sequence[str], *, base: Optional[str] = None) -> List[AssetRef]:
    root = Path(base or os.getcwd()).expanduser().resolve()
    refs = []
    for raw in paths:
        if _looks_like_media_path(raw, allow_url=True):
            refs.append(AssetRef(_resolve_path(raw, root), "explicit", "cli"))
    return refs


def _credit_line(
    *,
    provider: str,
    creator: str,
    source_url: str,
    license_text: str,
    attribution_text: str,
) -> str:
    if attribution_text:
        return attribution_text
    provider_label = provider.title().replace("_", " ") if provider else "source"
    bits = []
    if creator:
        bits.append(f"Asset by {creator}")
    else:
        bits.append("Asset")
    if provider:
        bits.append(f"on {provider_label}")
    if source_url:
        bits.append(source_url)
    if license_text:
        bits.append(f"({license_text})")
    return " ".join(bits)


def evaluate_asset(
    ref: AssetRef,
    metadata: Mapping[str, Any],
    *,
    require_known_license: bool = False,
    allow_missing_files: bool = False,
) -> Dict[str, Any]:
    source_url = _norm_text(
        metadata.get("source_url")
        or metadata.get("landing_url")
        or (metadata.get("url") if URL_RE.match(_norm_text(metadata.get("url"))) else "")
    )
    provider = _normalise_provider(metadata.get("provider") or metadata.get("source") or "")
    if not provider:
        provider = _provider_from_url(source_url or ref.path)

    policy = PROVIDER_POLICIES.get(provider, {})
    license_text = _norm_text(metadata.get("license") or policy.get("license"))
    license_url = _norm_text(metadata.get("license_url") or policy.get("license_url"))
    creator = _norm_text(metadata.get("creator") or metadata.get("author") or metadata.get("artist") or metadata.get("photographer"))
    attribution_text = _norm_text(metadata.get("attribution_text") or metadata.get("attribution") or metadata.get("credit"))
    attribution_required = _as_bool(metadata.get("attribution_required"))
    if attribution_required is None:
        attribution_required = bool(ATTRIBUTION_LICENSE_RE.search(license_text)) and not bool(
            NO_ATTRIBUTION_LICENSE_RE.search(license_text)
        )
    if provider and policy:
        attribution_required = bool(policy.get("attribution_required", attribution_required))
    commercial_ok = _as_bool(metadata.get("commercial_ok") or metadata.get("cleared_usage"))

    issues: List[str] = []
    warnings: List[str] = []
    path_is_url = bool(URL_RE.match(ref.path))
    file_exists = path_is_url or Path(ref.path).exists()

    if not file_exists and not allow_missing_files:
        issues.append("file_missing")
    if path_is_url:
        warnings.append("remote_asset_url_not_local_file")
    if BLOCKED_LICENSE_RE.search(license_text):
        issues.append("license_not_publish_safe")
    if not license_text:
        if require_known_license:
            issues.append("license_missing")
        else:
            warnings.append("license_missing")
    if provider in EXTERNAL_PROVIDERS and not source_url:
        warnings.append("source_url_missing")
    if provider in {"youtube", "tiktok", "instagram", "web", "bing", "google", "giphy"} and not license_text and not commercial_ok:
        issues.append("external_asset_needs_usage_clearance")
    if attribution_required and not attribution_text and not (creator and source_url):
        issues.append("attribution_required_but_incomplete")
    elif bool(policy.get("credit_recommended")) and not (attribution_text or creator or source_url):
        warnings.append("credit_metadata_missing")

    status = "blocked" if issues else ("warn" if warnings else "ready")
    credit = ""
    if attribution_required or policy.get("credit_recommended") or attribution_text:
        credit = _credit_line(
            provider=provider,
            creator=creator,
            source_url=source_url,
            license_text=license_text,
            attribution_text=attribution_text,
        )

    return {
        "path": ref.path,
        "usage": ref.usage,
        "source_artifact": ref.source_artifact,
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "file_exists": file_exists,
        "provider": provider,
        "source_url": source_url,
        "creator": creator,
        "license": license_text,
        "license_url": license_url,
        "attribution_required": bool(attribution_required),
        "credit_recommended": bool(policy.get("credit_recommended")) or bool(credit),
        "credit": credit,
        "policy_note": _norm_text(policy.get("note")),
    }


def build_provenance_manifest(
    refs: Sequence[AssetRef],
    *,
    media_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
    require_known_license: bool = False,
    allow_missing_files: bool = False,
    source_info: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    index = media_index or {}
    deduped: Dict[str, AssetRef] = {}
    for ref in refs:
        existing = deduped.get(ref.path)
        if existing:
            existing.usage = ",".join(dict.fromkeys((existing.usage + "," + ref.usage).split(",")))
            existing.source_artifact = ",".join(
                dict.fromkeys((existing.source_artifact + "," + ref.source_artifact).split(","))
            )
            existing.metadata = _merge_metadata(existing.metadata, ref.metadata)
        else:
            deduped[ref.path] = AssetRef(ref.path, ref.usage, ref.source_artifact, dict(ref.metadata))

    items: List[Dict[str, Any]] = []
    for ref in deduped.values():
        sidecar = _read_sidecar(ref.path)
        metadata = _merge_metadata(ref.metadata, index.get(ref.path, {}), sidecar)
        items.append(
            evaluate_asset(
                ref,
                metadata,
                require_known_license=require_known_license,
                allow_missing_files=allow_missing_files,
            )
        )

    status_counts = {"ready": 0, "warn": 0, "blocked": 0}
    for item in items:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1

    credits = sorted({item["credit"] for item in items if item.get("credit")})
    next_steps: List[str] = []
    if status_counts.get("blocked"):
        next_steps.append("Resolve blocked provenance items before publishing.")
    if any("license_missing" in item.get("issues", []) + item.get("warnings", []) for item in items):
        next_steps.append("Add license metadata to media_index.json or a .provenance.json sidecar.")
    if any("source_url_missing" in item.get("warnings", []) for item in items):
        next_steps.append("Add source_url landing pages for stock or external assets.")
    if credits:
        next_steps.append("Review the generated credits before posting.")

    return {
        "version": "asset_provenance.v1",
        "generated_at": utc_now(),
        "source": dict(source_info or {}),
        "summary": {
            "items": len(items),
            "ready": status_counts.get("ready", 0),
            "warn": status_counts.get("warn", 0),
            "blocking": status_counts.get("blocked", 0),
            "missing_files": sum(1 for item in items if "file_missing" in item.get("issues", [])),
            "missing_license": sum(
                1
                for item in items
                if "license_missing" in item.get("issues", []) or "license_missing" in item.get("warnings", [])
            ),
            "missing_source_url": sum(1 for item in items if "source_url_missing" in item.get("warnings", [])),
            "attribution_required": sum(1 for item in items if item.get("attribution_required")),
            "credits": len(credits),
        },
        "items": sorted(items, key=lambda item: (item["status"], item["path"])),
        "credits": credits,
        "next_steps": next_steps,
    }


def emit_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Asset Provenance Review",
        "",
        f"- Status: **{('BLOCKED' if summary.get('blocking') else 'READY' if not summary.get('warn') else 'WARN')}**",
        f"- Items: {summary.get('items', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warn', 0)}",
        f"- Credits: {summary.get('credits', 0)}",
        "",
        "| asset | usage | status | provider | license | issues/warnings |",
        "|---|---|---|---|---|---|",
    ]
    for item in manifest.get("items") or []:
        asset = os.path.basename(str(item.get("path") or "")) or str(item.get("path") or "")
        notes = "; ".join((item.get("issues") or []) + (item.get("warnings") or [])) or "-"
        license_text = item.get("license") or "-"
        if item.get("license_url"):
            license_text = f"{license_text} ({item['license_url']})"
        lines.append(
            "| {asset} | {usage} | {status} | {provider} | {license} | {notes} |".format(
                asset=f"`{asset}`",
                usage=str(item.get("usage") or "-").replace("|", "/"),
                status=item.get("status", ""),
                provider=item.get("provider") or "-",
                license=str(license_text).replace("|", "/"),
                notes=notes.replace("|", "/"),
            )
        )

    credits = manifest.get("credits") or []
    if credits:
        lines.extend(["", "## Credits", ""])
        lines.extend(f"- {credit}" for credit in credits)

    steps = manifest.get("next_steps") or []
    if steps:
        lines.extend(["", "## Next Steps", ""])
        lines.extend(f"- {step}" for step in steps)

    return "\n".join(lines).rstrip() + "\n"


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an asset provenance/credits review packet before publishing."
    )
    parser.add_argument("--media-library", help="Project directory with media_index.json/db metadata.")
    parser.add_argument("--asset-manifest", action="append", default=[], help="storyboard_assets.json; can repeat.")
    parser.add_argument("--render-config", action="append", default=[], help="render_config JSON; can repeat.")
    parser.add_argument("--enrich-plan", action="append", default=[], help="enrich_plan JSON; can repeat.")
    parser.add_argument("--json-artifact", action="append", default=[], help="Any JSON artifact to scan for media paths.")
    parser.add_argument("--asset", action="append", default=[], help="Explicit asset path or URL; can repeat.")
    parser.add_argument("--include-candidates", action="store_true", help="Include storyboard candidate_paths, not only resolved assets.")
    parser.add_argument("--require-known-license", action="store_true", help="Block when an asset has no license metadata.")
    parser.add_argument("--allow-missing-files", action="store_true", help="Warn via provenance only; do not block missing local files.")
    parser.add_argument("--output", required=True, help="Output asset_provenance.json path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when provenance has blocking items.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    refs: List[AssetRef] = []
    source_info: Dict[str, Any] = {
        "media_library": os.path.abspath(args.media_library) if args.media_library else "",
        "asset_manifests": [os.path.abspath(path) for path in args.asset_manifest],
        "render_configs": [os.path.abspath(path) for path in args.render_config],
        "enrich_plans": [os.path.abspath(path) for path in args.enrich_plan],
        "json_artifacts": [os.path.abspath(path) for path in args.json_artifact],
        "explicit_assets": list(args.asset or []),
    }

    media_index = _load_media_index(args.media_library) if args.media_library else {}
    for path in args.asset_manifest:
        refs.extend(collect_from_asset_manifest(path, include_candidates=args.include_candidates))
    for path in list(args.render_config) + list(args.enrich_plan) + list(args.json_artifact):
        refs.extend(collect_from_json_artifact(path))
    refs.extend(collect_explicit_assets(args.asset))

    manifest = build_provenance_manifest(
        refs,
        media_index=media_index,
        require_known_license=args.require_known_license,
        allow_missing_files=args.allow_missing_files,
        source_info=source_info,
    )
    write_json(args.output, manifest)
    if args.markdown:
        write_text(args.markdown, emit_markdown(manifest))

    summary = manifest["summary"]
    print(
        "Asset provenance: "
        f"items={summary['items']} ready={summary['ready']} "
        f"warn={summary['warn']} blocking={summary['blocking']} credits={summary['credits']}",
        file=sys.stderr,
    )
    if args.strict and summary["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
