#!/usr/bin/env python3
"""Plan stock-video sourcing before downloading or spending provider credits.

Inspired by MoneyPrinterTurbo's video_terms + Pexels/Pixabay/Coverr sourcing
flow, but kept artifact-first for this skill: this script never calls external
APIs and never downloads files. It emits a JSON/Markdown review packet that an
agent or editor can inspect before linking assets into storyboard_assets,
render_config, or an enrich_plan.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

try:
    from multi_export import PRESETS
except Exception:  # pragma: no cover - keeps --help usable if import context is odd
    PRESETS = {}

try:
    from media_library import recommend_assets
except Exception:  # pragma: no cover - optional integration for isolated use
    recommend_assets = None


SCHEMA = "stock_material_plan.v1"

PROVIDERS: Dict[str, Dict[str, str]] = {
    "pexels": {
        "label": "Pexels Videos",
        "api_key_env": "PEXELS_API_KEY",
        "mpt_config_key": "pexels_api_keys",
        "license_note": "Review Pexels license and source URL before publish.",
    },
    "pixabay": {
        "label": "Pixabay Videos",
        "api_key_env": "PIXABAY_API_KEY",
        "mpt_config_key": "pixabay_api_keys",
        "license_note": "Review Pixabay license and source URL before publish.",
    },
    "coverr": {
        "label": "Coverr Videos",
        "api_key_env": "COVERR_API_KEY",
        "mpt_config_key": "coverr_api_keys",
        "license_note": "Coverr is mostly 16:9; review license and crop result.",
    },
}

PLATFORM_FALLBACKS = {
    "xhs": {"width": 1080, "height": 1440, "aspect": "3:4", "max_duration": None},
    "douyin": {"width": 1080, "height": 1920, "aspect": "9:16", "max_duration": None},
    "wxch": {"width": 1080, "height": 1920, "aspect": "9:16", "max_duration": 60.0},
    "youtube": {"width": 1920, "height": 1080, "aspect": "16:9", "max_duration": None},
    "square": {"width": 1080, "height": 1080, "aspect": "1:1", "max_duration": None},
}

STOPWORDS = {
    "一个", "一种", "这个", "那个", "我们", "你们", "他们", "自己", "就是", "其实",
    "然后", "因为", "所以", "但是", "如果", "没有", "不是", "可以", "进行", "通过",
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "about",
    "how", "why", "what", "when", "where", "video", "short", "script",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}|[\u4e00-\u9fff]{2,}")


def split_terms(value: Optional[Iterable[str] | str]) -> List[str]:
    """Split comma/newline separated terms and keep order."""
    if value is None:
        return []
    raw_items: Iterable[str]
    if isinstance(value, str):
        raw_items = re.split(r"[,，;；\n]+", value)
    else:
        expanded: List[str] = []
        for item in value:
            expanded.extend(re.split(r"[,，;；\n]+", str(item)))
        raw_items = expanded

    terms: List[str] = []
    for item in raw_items:
        term = re.sub(r"\s+", " ", str(item)).strip()
        if term and term.lower() not in {t.lower() for t in terms}:
            terms.append(term)
    return terms


def load_text_path(path: Optional[str]) -> str:
    if not path:
        return ""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(payload, dict):
        segments = payload.get("segments")
        if isinstance(segments, list):
            return "\n".join(str(seg.get("text") or "") for seg in segments if isinstance(seg, dict))
        shots = payload.get("shots")
        if isinstance(shots, list):
            chunks = []
            for shot in shots:
                if not isinstance(shot, dict):
                    continue
                chunks.append(str(shot.get("narration") or shot.get("text") or ""))
                chunks.extend(str(k) for k in (shot.get("keywords") or []))
            return "\n".join(chunks)
    return raw


def _token_score(text: str) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for token in TOKEN_RE.findall(text):
        normalized = token.strip()
        lowered = normalized.lower()
        if lowered in STOPWORDS or normalized in STOPWORDS:
            continue
        if len(normalized) < 2:
            continue
        scores[normalized] = scores.get(normalized, 0.0) + 1.0
    return scores


def derive_search_terms(
    *,
    subject: str = "",
    script_text: str = "",
    explicit_terms: Optional[Iterable[str] | str] = None,
    amount: int = 5,
) -> List[Dict[str, Any]]:
    """Derive transparent stock-search terms from subject/script text."""
    amount = max(1, int(amount))
    terms: List[Dict[str, Any]] = []

    def add(term: str, source: str, score: float, reasons: Sequence[str]) -> None:
        cleaned = re.sub(r"\s+", " ", str(term)).strip()
        if not cleaned:
            return
        if cleaned.lower() in {str(t["term"]).lower() for t in terms}:
            return
        terms.append({
            "term": cleaned,
            "source": source,
            "score": round(float(score), 3),
            "reasons": list(reasons),
        })

    for term in split_terms(explicit_terms):
        add(term, "explicit", 100.0, ["provided-by-user"])

    if subject.strip():
        add(subject.strip(), "subject", 80.0, ["video-subject"])

    subject_scores = _token_score(subject)
    script_scores = _token_score(script_text)
    combined: Dict[str, float] = {}
    for token, score in subject_scores.items():
        combined[token] = combined.get(token, 0.0) + score * 2.5
    for token, score in script_scores.items():
        combined[token] = combined.get(token, 0.0) + score

    for token, score in sorted(combined.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(terms) >= amount:
            break
        reasons = []
        if token in subject_scores:
            reasons.append("subject-keyword")
        if token in script_scores:
            reasons.append("script-keyword")
        add(token, "derived", score, reasons)

    return terms[:amount]


def _target_from_platform(platform: str, target_aspect: Optional[str]) -> Dict[str, Any]:
    if platform in PRESETS:
        preset = PRESETS[platform]
        width = int(preset.width)
        height = int(preset.height)
        aspect = f"{width // math.gcd(width, height)}:{height // math.gcd(width, height)}"
        return {
            "platform": platform,
            "width": width,
            "height": height,
            "aspect": target_aspect or aspect,
            "max_duration": preset.max_duration_seconds,
        }
    base = dict(PLATFORM_FALLBACKS.get(platform, PLATFORM_FALLBACKS["douyin"]))
    base["platform"] = platform
    if target_aspect:
        base["aspect"] = target_aspect
    return base


def _orientation_for_aspect(aspect: str) -> str:
    parsed = _parse_aspect(aspect)
    if parsed is None:
        return "portrait"
    if parsed > 1.15:
        return "landscape"
    if parsed < 0.85:
        return "portrait"
    return "square"


def _parse_aspect(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = str(value).strip()
    if ":" in text:
        left, right = text.split(":", 1)
        try:
            width = float(left)
            height = float(right)
        except ValueError:
            return None
        return width / height if width > 0 and height > 0 else None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def build_provider_query(
    *,
    provider: str,
    term: str,
    target: Dict[str, Any],
    minimum_duration: float,
    per_page: int,
) -> Dict[str, Any]:
    """Build one provider search instruction without credentials."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    orientation = _orientation_for_aspect(str(target.get("aspect") or "9:16"))
    info = PROVIDERS[provider]

    if provider == "pexels":
        params = {"query": term, "per_page": per_page, "orientation": orientation}
        endpoint = f"https://api.pexels.com/videos/search?{urlencode(params)}"
        provider_filter = f"orientation={orientation}; filter duration >= {minimum_duration:g}s after response"
    elif provider == "pixabay":
        params = {"q": term, "video_type": "all", "per_page": per_page}
        endpoint = f"https://pixabay.com/api/videos/?{urlencode(params)}"
        provider_filter = (
            f"filter width/aspect toward {target.get('aspect')}; "
            f"filter duration >= {minimum_duration:g}s after response"
        )
    else:
        params = {"query": term, "page_size": per_page, "urls": "true", "sort": "popular"}
        endpoint = f"https://api.coverr.co/videos?{urlencode(params)}"
        provider_filter = (
            "Coverr is mostly landscape; use smart_reframe/multi_export crop review "
            f"for target {target.get('aspect')}; filter duration >= {minimum_duration:g}s"
        )

    return {
        "id": f"{provider}:{_slug(term)}",
        "provider": provider,
        "provider_label": info["label"],
        "term": term,
        "method": "GET",
        "endpoint": endpoint,
        "api_key_env": info["api_key_env"],
        "mpt_config_key": info["mpt_config_key"],
        "minimum_duration": minimum_duration,
        "target_aspect": target.get("aspect"),
        "target_size": {"width": target.get("width"), "height": target.get("height")},
        "filter": provider_filter,
        "license_note": info["license_note"],
        "next_action": (
            "Search this provider, download only reviewed clips, then add provider, "
            "source_url, creator, and license metadata to media_index or asset_provenance."
        ),
    }


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value.strip().lower())
    return text.strip("-")[:60] or "query"


def _duration_from_text_payload(text: str) -> Optional[float]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    max_end = 0.0
    for key in ("segments", "shots"):
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            try:
                end = float(item.get("end") or 0.0)
            except (TypeError, ValueError):
                end = 0.0
            max_end = max(max_end, end)
    return max_end if max_end > 0 else None


def _local_candidates(
    media_library_project: Optional[str],
    term: str,
    *,
    target_aspect: str,
    clip_duration: float,
    limit: int,
) -> List[Dict[str, Any]]:
    if not media_library_project or recommend_assets is None:
        return []
    return recommend_assets(
        media_library_project,
        term,
        category="broll",
        limit=limit,
        target_duration=clip_duration,
        target_aspect=target_aspect,
    )


def build_stock_material_plan(
    *,
    subject: str = "",
    script_text: str = "",
    explicit_terms: Optional[Iterable[str] | str] = None,
    providers: Sequence[str] = ("pexels", "pixabay", "coverr"),
    platform: str = "douyin",
    target_aspect: Optional[str] = None,
    clip_duration: float = 5.0,
    video_count: int = 1,
    term_count: int = 5,
    per_page: int = 10,
    required_duration: Optional[float] = None,
    media_library_project: Optional[str] = None,
    local_limit: int = 3,
) -> Dict[str, Any]:
    target = _target_from_platform(platform, target_aspect)
    target_aspect_value = str(target.get("aspect") or "9:16")
    clip_duration = max(0.5, float(clip_duration))
    video_count = max(1, int(video_count))
    terms = derive_search_terms(
        subject=subject,
        script_text=script_text,
        explicit_terms=explicit_terms,
        amount=term_count,
    )

    inferred_duration = _duration_from_text_payload(script_text)
    if required_duration is None:
        required_duration = inferred_duration
    if required_duration is None:
        required_duration = clip_duration * max(1, min(len(terms) or 1, term_count))
    required_duration = float(required_duration) * video_count
    estimated_clips = max(1, math.ceil(required_duration / clip_duration))

    provider_queries: List[Dict[str, Any]] = []
    provider_names = [p for p in providers if p in PROVIDERS]
    for term in terms:
        for provider in provider_names:
            provider_queries.append(
                build_provider_query(
                    provider=provider,
                    term=str(term["term"]),
                    target=target,
                    minimum_duration=clip_duration,
                    per_page=per_page,
                )
            )

    local_candidates: Dict[str, List[Dict[str, Any]]] = {}
    local_candidate_paths = set()
    for term in terms:
        candidates = _local_candidates(
            media_library_project,
            str(term["term"]),
            target_aspect=target_aspect_value,
            clip_duration=clip_duration,
            limit=local_limit,
        )
        if candidates:
            local_candidates[str(term["term"])] = candidates
            for candidate in candidates:
                local_candidate_paths.add(candidate.get("absolute_path") or candidate.get("path"))

    warnings: List[str] = []
    if not terms:
        warnings.append("No search terms were derived; pass --subject, --terms, or --script.")
    if "coverr" in provider_names and _orientation_for_aspect(target_aspect_value) == "portrait":
        warnings.append("Coverr is mostly 16:9; plan smart_reframe or manual crop review for portrait output.")
    if not provider_names:
        warnings.append("No supported stock providers selected.")

    blocking = 0
    if not terms or not provider_names:
        blocking += 1

    return {
        "schema": SCHEMA,
        "source_inspiration": {
            "project": "harry0703/MoneyPrinterTurbo",
            "borrowed_patterns": [
                "video_terms-driven material search",
                "Pexels/Pixabay/Coverr provider routing",
                "video_count coverage planning",
                "clip_duration minimum material filter",
                "license-aware material review before render",
            ],
        },
        "target": target,
        "summary": {
            "term_count": len(terms),
            "provider_query_count": len(provider_queries),
            "local_candidate_count": len([path for path in local_candidate_paths if path]),
            "required_coverage_seconds": round(required_duration, 3),
            "clip_duration_seconds": round(clip_duration, 3),
            "estimated_clips_needed": estimated_clips,
            "video_count": video_count,
            "blocking": blocking,
        },
        "terms": terms,
        "local_candidates": local_candidates,
        "provider_queries": provider_queries,
        "warnings": warnings,
        "next_actions": [
            "Review local_candidates first; prefer already indexed owned footage.",
            "Use provider_queries only when local footage cannot cover the cue.",
            "After downloading reviewed clips, run media_library.py scan and asset_provenance.py before publish.",
            "Use storyboard_assets.py --strict or pipeline_manifest.py gates before final render.",
        ],
    }


def render_markdown(plan: Dict[str, Any]) -> str:
    summary = plan.get("summary") or {}
    target = plan.get("target") or {}
    lines = [
        "# Stock Material Plan",
        "",
        f"- Schema: `{plan.get('schema')}`",
        f"- Target: `{target.get('platform')}` {target.get('width')}x{target.get('height')} ({target.get('aspect')})",
        f"- Required coverage: {summary.get('required_coverage_seconds')}s across {summary.get('video_count')} variant(s)",
        f"- Estimated clips needed: {summary.get('estimated_clips_needed')}",
        f"- Local candidates: {summary.get('local_candidate_count')}",
        f"- Provider searches: {summary.get('provider_query_count')}",
        "",
        "## Terms",
        "",
        "| Term | Source | Score | Reasons |",
        "|---|---:|---:|---|",
    ]
    for term in plan.get("terms") or []:
        lines.append(
            f"| {term.get('term')} | {term.get('source')} | {term.get('score')} | "
            f"{', '.join(term.get('reasons') or [])} |"
        )

    lines.extend(["", "## Provider Queries", "", "| Provider | Term | Filter | Auth |", "|---|---|---|---|"])
    for query in plan.get("provider_queries") or []:
        lines.append(
            f"| {query.get('provider')} | {query.get('term')} | {query.get('filter')} | "
            f"`{query.get('api_key_env')}` / `{query.get('mpt_config_key')}` |"
        )

    candidates = plan.get("local_candidates") or {}
    lines.extend(["", "## Local Candidates"])
    if not candidates:
        lines.append("")
        lines.append("No indexed local B-roll candidates matched yet.")
    else:
        for term, items in candidates.items():
            lines.extend(["", f"### {term}", "", "| Score | Path | Reasons |", "|---:|---|---|"])
            for item in items:
                lines.append(
                    f"| {item.get('score')} | `{item.get('path')}` | "
                    f"{', '.join(item.get('reasons') or [])} |"
                )

    if plan.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in plan["warnings"])

    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {action}" for action in plan.get("next_actions") or [])
    lines.append("")
    return "\n".join(lines)


def _read_inputs(args: argparse.Namespace) -> str:
    chunks: List[str] = []
    for path in args.script or []:
        chunks.append(load_text_path(path))
    return "\n".join(chunk for chunk in chunks if chunk)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Plan stock-video sourcing without downloading files")
    parser.add_argument("--subject", default="", help="Video topic/subject")
    parser.add_argument("--script", action="append", help="Script, transcript JSON, or storyboard JSON path")
    parser.add_argument("--terms", action="append", help="Comma/newline separated search terms")
    parser.add_argument("--provider", action="append", choices=sorted(PROVIDERS),
                        help="Stock provider to plan. Repeatable. Default: all")
    parser.add_argument("--platform", default="douyin",
                        help="Target platform preset: xhs, douyin, wxch, youtube, square")
    parser.add_argument("--target-aspect", default=None,
                        help="Override aspect such as 9:16, 3:4, 16:9, or 1:1")
    parser.add_argument("--clip-duration", type=float, default=5.0,
                        help="Minimum usable stock clip duration in seconds")
    parser.add_argument("--video-count", type=int, default=1,
                        help="Number of creative variants to cover")
    parser.add_argument("--term-count", type=int, default=5,
                        help="Maximum number of terms to derive")
    parser.add_argument("--per-page", type=int, default=10,
                        help="Provider search page size to plan")
    parser.add_argument("--required-duration", type=float, default=None,
                        help="Override total voice/video duration before multiplying by --video-count")
    parser.add_argument("--media-library", default=None,
                        help="Optional project dir with media_index.json/db for local candidate ranking")
    parser.add_argument("--local-limit", type=int, default=3,
                        help="Local candidates per term")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--markdown", help="Optional Markdown review path")
    parser.add_argument("--strict", action="store_true",
                        help="Return 2 when required planning inputs are missing")
    args = parser.parse_args(argv)

    script_text = _read_inputs(args)
    plan = build_stock_material_plan(
        subject=args.subject,
        script_text=script_text,
        explicit_terms=args.terms,
        providers=args.provider or tuple(PROVIDERS),
        platform=args.platform,
        target_aspect=args.target_aspect,
        clip_duration=args.clip_duration,
        video_count=args.video_count,
        term_count=args.term_count,
        per_page=args.per_page,
        required_duration=args.required_duration,
        media_library_project=args.media_library,
        local_limit=args.local_limit,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.markdown:
        markdown = Path(args.markdown)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(plan), encoding="utf-8")

    print(f"Stock material plan: {output}")
    if args.markdown:
        print(f"Markdown review: {args.markdown}")
    if plan.get("warnings"):
        for warning in plan["warnings"]:
            print(f"Warning: {warning}")
    return 2 if args.strict and (plan.get("summary") or {}).get("blocking", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
