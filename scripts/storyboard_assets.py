#!/usr/bin/env python3
"""Build an auditable asset manifest from storyboard shot cards.

This script never submits generation jobs. It turns storyboard_plan.py output
into a concrete queue of local files to create, review, or link before render.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from media_library import recommend_assets
from storyboard_plan import ROUTING_SENTENCE


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v")

ROUTE_SPECS = {
    "codex_imagegen": {
        "kind": "image",
        "dir": "imagegen",
        "exts": IMAGE_EXTS,
        "missing_status": "needs_generation",
        "prompt_keys": ("image_prompt_en", "fallback_image_prompt_en"),
        "next_action": "Generate with Codex built-in image_gen, then save the file at the expected path.",
    },
    "dreamina_video": {
        "kind": "video",
        "dir": "generated_video",
        "exts": VIDEO_EXTS,
        "missing_status": "needs_approval",
        "prompt_keys": ("video_prompt_en",),
        "next_action": "Confirm paid-credit use before submitting Dreamina/即梦, then save the output video.",
    },
    "remotion_hyperframes": {
        "kind": "motion_graphics",
        "dir": "motion",
        "exts": VIDEO_EXTS,
        "missing_status": "needs_render",
        "prompt_keys": ("motion_graphics_brief",),
        "next_action": "Render the deterministic motion card locally and link the output.",
    },
    "media_library_broll": {
        "kind": "broll",
        "dir": "broll",
        "exts": VIDEO_EXTS + IMAGE_EXTS,
        "missing_status": "search_needed",
        "prompt_keys": ("broll_query",),
        "next_action": "Search the local media library, choose a candidate, and link it into render_config/enrich_plan.",
    },
}


def load_plan(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _abs(path: str, base: Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = base / p
    return p


def _first_prompt(shot: Dict[str, Any], prompt_keys: Sequence[str]) -> Tuple[str, str]:
    prompts = shot.get("prompts") or {}
    for key in prompt_keys:
        value = str(prompts.get(key) or "").strip()
        if value:
            return key, value
    return "", ""


def _explicit_asset_path(shot: Dict[str, Any], base: Path) -> Optional[Path]:
    for key in ("asset_path", "resolved_asset_path", "media_path", "output_path"):
        value = shot.get(key)
        if value:
            return _abs(str(value), base)
    asset = shot.get("asset")
    if isinstance(asset, dict):
        for key in ("path", "file", "output_path"):
            value = asset.get(key)
            if value:
                return _abs(str(value), base)
    return None


def _expected_path(asset_root: Path, shot_id: str, route: str) -> Path:
    spec = ROUTE_SPECS.get(route, ROUTE_SPECS["media_library_broll"])
    ext = ".png" if spec["kind"] == "image" else ".mp4"
    return asset_root / str(spec["dir"]) / f"{shot_id}{ext}"


def _existing_variant(expected: Path, exts: Sequence[str]) -> Optional[Path]:
    if expected.exists():
        return expected
    stem = expected.with_suffix("")
    for ext in exts:
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def _candidate_tokens(shot: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    for token in shot.get("keywords") or []:
        text = str(token).strip().lower()
        if len(text) >= 2:
            tokens.append(text)
    prompts = shot.get("prompts") or {}
    query = str(prompts.get("broll_query") or "").strip().lower()
    if query:
        tokens.extend(t for t in query.replace(",", " ").split() if len(t) >= 2)
    tokens.append(str(shot.get("id") or "").lower())
    deduped: List[str] = []
    for token in tokens:
        if token and token not in deduped:
            deduped.append(token)
    return deduped[:8]


def _scan_candidates(
    shot: Dict[str, Any],
    dirs: Sequence[Path],
    exts: Sequence[str],
    limit: int = 5,
) -> List[str]:
    tokens = _candidate_tokens(shot)
    if not tokens:
        return []
    candidates: List[str] = []
    for directory in dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            name = path.stem.lower()
            if any(token in name for token in tokens):
                candidates.append(str(path))
                if len(candidates) >= limit:
                    return candidates
    return candidates


def _media_index_candidates(
    shot: Dict[str, Any],
    *,
    media_project_dir: Optional[str],
    target_aspect: Optional[str],
    limit: int = 5,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    if not media_project_dir:
        return [], []
    prompts = shot.get("prompts") or {}
    query = str(prompts.get("broll_query") or "").strip()
    if not query:
        query = " ".join(str(token) for token in (shot.get("keywords") or []))
    if not query.strip():
        return [], []

    results = recommend_assets(
        media_project_dir,
        query,
        category="broll",
        limit=limit,
        target_duration=shot.get("duration"),
        target_aspect=target_aspect,
    )
    paths = [str(result.get("absolute_path")) for result in results if result.get("absolute_path")]
    scored = [
        {
            "path": result.get("absolute_path"),
            "score": result.get("score"),
            "reasons": result.get("reasons") or [],
            "tags": result.get("tags") or [],
        }
        for result in results
    ]
    return paths, scored


def _default_broll_dirs(asset_root: Path) -> List[Path]:
    return [
        asset_root / "broll",
        asset_root / "media",
        asset_root / "origin" / "broll",
        asset_root.parent / "broll",
        asset_root.parent / "origin" / "broll",
    ]


def build_asset_manifest(
    plan: Dict[str, Any],
    *,
    asset_root: str,
    broll_dirs: Optional[Sequence[str]] = None,
    media_library_project: Optional[str] = None,
) -> Dict[str, Any]:
    root = Path(asset_root).expanduser().resolve()
    explicit_broll_dirs = [_abs(d, root).resolve() for d in (broll_dirs or [])]
    search_dirs = explicit_broll_dirs or _default_broll_dirs(root)
    target_aspect = str((plan.get("target") or {}).get("aspect") or "")
    items: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    paid_count = 0

    for shot in plan.get("shots") or []:
        shot_id = str(shot.get("id") or f"shot_{len(items) + 1:03d}")
        route_info = shot.get("generation_route") or {}
        route = str(route_info.get("primary") or "media_library_broll")
        spec = ROUTE_SPECS.get(route, ROUTE_SPECS["media_library_broll"])
        prompt_key, prompt = _first_prompt(shot, spec["prompt_keys"])  # type: ignore[index]
        expected = _expected_path(root, shot_id, route)
        explicit_path = _explicit_asset_path(shot, root)
        resolved = None
        candidates: List[str] = []
        candidate_scores: List[Dict[str, Any]] = []

        if explicit_path and explicit_path.exists():
            resolved = explicit_path
            status = "ready"
            next_action = "Asset path already exists; keep it linked for render."
        else:
            resolved = _existing_variant(expected, spec["exts"])  # type: ignore[index]
            if resolved:
                status = "ready"
                next_action = "Expected asset exists; link it into render_config/enrich_plan if not already linked."
            elif route == "media_library_broll":
                candidates, candidate_scores = _media_index_candidates(
                    shot,
                    media_project_dir=media_library_project,
                    target_aspect=target_aspect,
                )
                if not candidates:
                    candidates = _scan_candidates(shot, search_dirs, spec["exts"])  # type: ignore[index]
                if candidates:
                    status = "candidate_found"
                    next_action = "Review the candidate file and link the chosen path into render_config/enrich_plan."
                else:
                    status = str(spec["missing_status"])
                    next_action = str(spec["next_action"])
            else:
                status = str(spec["missing_status"])
                next_action = str(spec["next_action"])

        paid = bool(route_info.get("requires_paid_credits"))
        if paid and status != "ready":
            paid_count += 1

        status_counts[status] = status_counts.get(status, 0) + 1
        items.append({
            "shot_id": shot_id,
            "section": shot.get("section"),
            "time": {
                "start": shot.get("start"),
                "end": shot.get("end"),
                "duration": shot.get("duration"),
            },
            "route": route,
            "fallback_route": route_info.get("fallback"),
            "kind": spec["kind"],
            "status": status,
            "blocking": status != "ready",
            "expected_path": str(expected),
            "resolved_path": str(resolved) if resolved else "",
            "candidate_paths": candidates,
            "candidate_scores": candidate_scores,
            "requires_paid_credits": paid,
            "approval_note": route_info.get("approval_note") or (
                "Dreamina/即梦 generation may consume credits; confirm before submitting."
                if paid else ""
            ),
            "prompt_key": prompt_key,
            "prompt": prompt,
            "keywords": shot.get("keywords") or [],
            "next_action": next_action,
        })

    blocking_count = sum(1 for item in items if item["blocking"])
    return {
        "version": "storyboard_asset_manifest.v1",
        "routing_note": ROUTING_SENTENCE,
        "asset_root": str(root),
        "source": {
            "storyboard_version": plan.get("version"),
            "shots": len(plan.get("shots") or []),
            "target": plan.get("target") or {},
        },
        "summary": {
            "items": len(items),
            "blocking": blocking_count,
            "paid_credit_tasks": paid_count,
            **{f"status_{key}": value for key, value in sorted(status_counts.items())},
        },
        "items": items,
        "next_steps": [
            "Review non-ready items before render.",
            "Use Codex image_gen for codex_imagegen image prompts.",
            "Confirm Dreamina/即梦 credits before submitting dreamina_video tasks.",
            "Render remotion_hyperframes locally before final assembly.",
            "Link resolved files back into enrich_plan or render_config, then run render_qa.py.",
        ],
    }


def emit_markdown(manifest: Dict[str, Any]) -> str:
    lines = [
        "# Storyboard Asset Manifest",
        "",
        manifest.get("routing_note", ROUTING_SENTENCE),
        "",
        f"- Asset root: `{manifest.get('asset_root', '')}`",
        f"- Items: {manifest.get('summary', {}).get('items', 0)}",
        f"- Blocking: {manifest.get('summary', {}).get('blocking', 0)}",
        f"- Paid-credit tasks: {manifest.get('summary', {}).get('paid_credit_tasks', 0)}",
        "",
        "| shot | route | status | asset/candidates | next action |",
        "|---|---|---|---|---|",
    ]
    for item in manifest.get("items") or []:
        asset_bits = []
        if item.get("resolved_path"):
            asset_bits.append(f"`{item['resolved_path']}`")
        else:
            asset_bits.append(f"`{item.get('expected_path', '')}`")
        if item.get("candidate_paths"):
            scores = {score.get("path"): score for score in item.get("candidate_scores") or []}
            preview_bits = []
            for path in item["candidate_paths"][:3]:
                score = scores.get(path, {}).get("score")
                suffix = f" ({score})" if score is not None else ""
                preview_bits.append(f"`{path}`{suffix}")
            preview = ", ".join(preview_bits)
            asset_bits.append(f"candidates: {preview}")
        if item.get("approval_note"):
            asset_bits.append(str(item["approval_note"]))
        lines.append(
            "| {shot} | {route} | {status} | {assets} | {action} |".format(
                shot=item.get("shot_id", ""),
                route=item.get("route", ""),
                status=item.get("status", ""),
                assets="<br>".join(asset_bits),
                action=str(item.get("next_action", "")).replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an asset readiness manifest from storyboard_plan JSON."
    )
    parser.add_argument("--storyboard-plan", required=True, help="Input storyboard_plan.json.")
    parser.add_argument("--asset-root", default="work", help="Root for generated/reviewed assets.")
    parser.add_argument("--broll-dir", action="append", default=[], help="Optional B-roll search directory.")
    parser.add_argument(
        "--media-library",
        help="Optional project directory containing media_index.json/db for ranked B-roll candidates.",
    )
    parser.add_argument("--output", required=True, help="Output manifest JSON.")
    parser.add_argument("--markdown", help="Optional Markdown review table.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when any asset is not ready yet.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = load_plan(args.storyboard_plan)
    manifest = build_asset_manifest(
        plan,
        asset_root=args.asset_root,
        broll_dirs=args.broll_dir,
        media_library_project=args.media_library,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(manifest))

    summary = manifest["summary"]
    print(
        "Wrote storyboard asset manifest: "
        f"{args.output}; items={summary['items']} blocking={summary['blocking']}"
    )
    if args.markdown:
        print(f"Wrote storyboard asset markdown: {args.markdown}")
    if args.strict and summary["blocking"]:
        print("Asset manifest strict check failed: some storyboard assets are not ready.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
