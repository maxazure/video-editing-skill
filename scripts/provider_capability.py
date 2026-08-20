#!/usr/bin/env python3
"""Validate dated, surface-specific video-generation capability profiles.

The profile is an operator-supplied contract. This module does not browse a
provider, infer UI controls, submit generation work, or claim that a community
source is official documentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


BUNDLE_VERSION = "video_provider_capabilities.v1"
SOURCE_TYPES = {
    "official_documentation",
    "official_model_card",
    "official_ui",
    "provider_support",
    "first_party_test",
    "community",
}
OFFICIAL_SOURCE_TYPES = {
    "official_documentation",
    "official_model_card",
    "official_ui",
    "provider_support",
}
KNOWN_MODES = {
    "text_to_video",
    "image_to_video",
    "first_last_frame",
    "reference_to_video",
    "video_edit",
    "video_extension",
    "clip_stitching",
}
TRISTATE = {True, False, "unknown"}


def load_bundle(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("provider capability bundle must be a JSON object")
    return data


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


def _profile_id(profile: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in profile.items() if key != "profile_id"}
    return _canonical_id("vpc", canonical)


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _string_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None
    items = [str(item).strip() for item in value]
    if not items or any(not item for item in items) or len(set(items)) != len(items):
        return None
    return items


def verify_profile(
    profile: Mapping[str, Any],
    *,
    max_age_days: int = 30,
    require_fresh: bool = True,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    today = today or datetime.now(timezone.utc).date()

    provider = str(profile.get("provider") or "").strip()
    surface = str(profile.get("surface") or "").strip()
    model = str(profile.get("model") or "").strip()
    if not provider:
        blockers.append("missing_provider")
    if not surface:
        blockers.append("missing_surface")
    if not model:
        blockers.append("missing_model")

    verified_at = _parse_date(profile.get("verified_at"))
    age_days: Optional[int] = None
    if verified_at is None:
        blockers.append("invalid_verified_at")
    else:
        age_days = (today - verified_at).days
        if age_days < 0:
            blockers.append("verified_at_in_future")
        elif age_days > max_age_days:
            issue = f"stale_profile:{age_days}d>{max_age_days}d"
            (blockers if require_fresh else warnings).append(issue)

    sources = profile.get("sources")
    official_sources = 0
    if not isinstance(sources, list) or not sources:
        blockers.append("missing_sources")
    else:
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, Mapping):
                blockers.append(f"source_{index}_not_object")
                continue
            source_type = str(source.get("source_type") or "").strip()
            url = str(source.get("url") or "").strip()
            if source_type not in SOURCE_TYPES:
                blockers.append(f"source_{index}_invalid_source_type")
            elif source_type in OFFICIAL_SOURCE_TYPES:
                official_sources += 1
            if not (url.startswith("https://") or url.startswith("http://")):
                blockers.append(f"source_{index}_invalid_url")
        if official_sources == 0:
            warnings.append("no_official_source")

    capabilities = profile.get("capabilities")
    modes: Optional[List[str]] = None
    aspects: Optional[List[str]] = None
    resolutions: Optional[List[str]] = None
    if not isinstance(capabilities, Mapping):
        blockers.append("missing_capabilities")
        capabilities = {}
    else:
        modes = _string_list(capabilities.get("modes"))
        if modes is None:
            blockers.append("invalid_modes")
        else:
            unknown_modes = sorted(set(modes) - KNOWN_MODES)
            if unknown_modes:
                blockers.append("unknown_modes:" + ",".join(unknown_modes))

        aspects = _string_list(capabilities.get("aspect_ratios"))
        if aspects is None:
            blockers.append("invalid_aspect_ratios")

        resolutions = _string_list(capabilities.get("resolutions"))
        if resolutions is None:
            blockers.append("invalid_resolutions")

        duration = capabilities.get("duration")
        if not isinstance(duration, Mapping):
            blockers.append("invalid_duration")
        else:
            kind = str(duration.get("kind") or "")
            if kind == "range":
                minimum = duration.get("min_seconds")
                maximum = duration.get("max_seconds")
                if not (_is_number(minimum) and _is_number(maximum)):
                    blockers.append("invalid_duration_range")
                elif float(minimum) <= 0 or float(maximum) < float(minimum):
                    blockers.append("invalid_duration_range")
            elif kind == "fixed":
                values = duration.get("values_seconds")
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not _is_number(value) or float(value) <= 0 for value in values)
                    or len({float(value) for value in values}) != len(values)
                ):
                    blockers.append("invalid_duration_values")
            else:
                blockers.append("invalid_duration_kind")

        limits = capabilities.get("reference_limits")
        if not isinstance(limits, Mapping):
            warnings.append("reference_limits_unverified")
        else:
            for media_type in ("images", "videos", "audio"):
                value = limits.get(media_type)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    blockers.append(f"invalid_reference_limit:{media_type}")

        audio = capabilities.get("audio")
        if not isinstance(audio, Mapping):
            warnings.append("audio_controls_unverified")
        else:
            for control in ("generate", "reference", "preserve_source"):
                if audio.get(control) not in TRISTATE:
                    blockers.append(f"invalid_audio_control:{control}")

    expected_id = _profile_id(profile)
    stored_id = str(profile.get("profile_id") or "").strip()
    if stored_id and stored_id != expected_id:
        blockers.append("profile_id_mismatch")

    return {
        "profile_id": expected_id,
        "provider": provider,
        "surface": surface,
        "model": model,
        "verified_at": str(profile.get("verified_at") or ""),
        "age_days": age_days,
        "official_sources": official_sources,
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "summary": {
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def verify_bundle(
    bundle: Mapping[str, Any],
    *,
    max_age_days: int = 30,
    require_fresh: bool = True,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if bundle.get("version") != BUNDLE_VERSION:
        blockers.append("invalid_bundle_version")

    raw_profiles = bundle.get("profiles")
    profile_results: List[Dict[str, Any]] = []
    providers: List[str] = []
    if not isinstance(raw_profiles, list) or not raw_profiles:
        blockers.append("missing_profiles")
    else:
        for index, profile in enumerate(raw_profiles, start=1):
            if not isinstance(profile, Mapping):
                blockers.append(f"profile_{index}_not_object")
                continue
            result = verify_profile(
                profile,
                max_age_days=max_age_days,
                require_fresh=require_fresh,
                today=today,
            )
            profile_results.append(result)
            provider = result["provider"]
            if provider:
                providers.append(provider)
            blockers.extend(f"{provider or index}:{issue}" for issue in result["blockers"])
            warnings.extend(f"{provider or index}:{issue}" for issue in result["warnings"])

    duplicates = sorted({provider for provider in providers if providers.count(provider) > 1})
    if duplicates:
        blockers.append("duplicate_provider_profiles:" + ",".join(duplicates))

    canonical_bundle = {
        "version": bundle.get("version"),
        "profiles": raw_profiles if isinstance(raw_profiles, list) else [],
    }
    return {
        "version": "video_provider_capability_verification.v1",
        "bundle_id": _canonical_id("vpcb", canonical_bundle),
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "policy": {
            "max_age_days": max_age_days,
            "require_fresh": require_fresh,
        },
        "summary": {
            "profiles": len(profile_results),
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
            "official_profiles": sum(1 for result in profile_results if result["official_sources"] > 0),
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "profiles": profile_results,
    }


def profile_index(
    bundles: Sequence[Mapping[str, Any]],
    *,
    max_age_days: int = 30,
    require_fresh: bool = True,
    today: Optional[date] = None,
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for bundle in bundles:
        verification = verify_bundle(
            bundle,
            max_age_days=max_age_days,
            require_fresh=require_fresh,
            today=today,
        )
        results = {item["provider"]: item for item in verification["profiles"] if item["provider"]}
        for profile in bundle.get("profiles") or []:
            if not isinstance(profile, Mapping):
                continue
            provider = str(profile.get("provider") or "").strip()
            if not provider or provider in indexed:
                continue
            indexed[provider] = {
                "profile": dict(profile),
                "verification": results.get(provider, {}),
            }
    return indexed


def profile_support_issues(
    profile: Mapping[str, Any],
    *,
    provider: str,
    mode: str,
    aspect: str,
    duration_seconds: float,
    resolution: str,
    image_references: int = 0,
) -> List[str]:
    issues: List[str] = []
    if str(profile.get("provider") or "") != provider:
        return ["profile_provider_mismatch"]
    capabilities = profile.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return ["invalid_capability_profile"]

    modes = capabilities.get("modes") or []
    if mode not in modes:
        issues.append(f"unsupported_mode:{mode}")

    aspects = capabilities.get("aspect_ratios") or []
    if "*" not in aspects and aspect not in aspects:
        issues.append(f"unsupported_aspect:{aspect}")

    duration = capabilities.get("duration") or {}
    if duration.get("kind") == "range":
        minimum = float(duration.get("min_seconds") or 0)
        maximum = float(duration.get("max_seconds") or 0)
        if duration_seconds < minimum or duration_seconds > maximum:
            issues.append(f"unsupported_duration:{duration_seconds:g}")
    elif duration.get("kind") == "fixed":
        values = [float(value) for value in duration.get("values_seconds") or []]
        if not any(abs(duration_seconds - value) <= 1e-6 for value in values):
            issues.append(f"unsupported_duration:{duration_seconds:g}")

    resolutions = capabilities.get("resolutions") or []
    if not resolution:
        issues.append("resolution_not_selected")
    elif "*" not in resolutions and resolution not in resolutions:
        issues.append(f"unsupported_resolution:{resolution}")

    limits = capabilities.get("reference_limits")
    if isinstance(limits, Mapping):
        image_limit = limits.get("images")
        if isinstance(image_limit, int) and image_references > image_limit:
            issues.append(f"image_reference_limit_exceeded:{image_references}>{image_limit}")
    return issues


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Video Provider Capability Verification",
        "",
        f"- Status: `{report.get('status', '')}`",
        f"- Profiles: {summary.get('profiles', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Freshness policy: {report.get('policy', {}).get('max_age_days', 0)} days",
        "",
        "| provider | surface | model | verified | age | official sources | status |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for profile in report.get("profiles") or []:
        lines.append(
            "| {provider} | {surface} | {model} | {verified} | {age} | {official} | {status} |".format(
                provider=profile.get("provider", ""),
                surface=profile.get("surface", ""),
                model=profile.get("model", ""),
                verified=profile.get("verified_at", ""),
                age=profile.get("age_days", "-"),
                official=profile.get("official_sources", 0),
                status=profile.get("status", ""),
            )
        )
    if report.get("blockers"):
        lines.extend(["", "## Blockers", "", *[f"- `{item}`" for item in report["blockers"]]])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", "", *[f"- `{item}`" for item in report["warnings"]]])
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate dated video-provider capability profiles.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify", help="Verify one provider capability bundle.")
    verify_parser.add_argument("--bundle", required=True, help="provider_capabilities.json bundle.")
    verify_parser.add_argument("--max-age-days", type=int, default=30, help="Maximum profile age.")
    verify_parser.add_argument("--allow-stale", action="store_true", help="Warn instead of block when a profile is stale.")
    verify_parser.add_argument("--output", help="Optional JSON verification report.")
    verify_parser.add_argument("--markdown", help="Optional Markdown verification report.")
    verify_parser.add_argument("--strict", action="store_true", help="Exit 2 when verification has blockers.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.max_age_days < 0:
        parser.error("--max-age-days must be non-negative")
    bundle = load_bundle(args.bundle)
    report = verify_bundle(
        bundle,
        max_age_days=args.max_age_days,
        require_fresh=not args.allow_stale,
    )
    if args.output:
        _write_json(args.output, report)
    if args.markdown:
        output = Path(args.markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(emit_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and report["summary"]["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
