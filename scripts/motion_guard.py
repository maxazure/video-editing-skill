#!/usr/bin/env python3
"""Pre-render motion-density guard for storyboard and render artifacts.

The guard checks whether a planned video still satisfies a motion-led delivery
promise before rendering. It is intentionally local-first: no provider calls,
no media decoding, and no paid generation submission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}
MOTION_ROUTES = {"dreamina_video", "remotion_hyperframes", "media_library_broll"}
STILL_ROUTES = {"codex_imagegen"}
NON_BLOCKING_STATUSES = {"", "ready", "candidate_found"}


def load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


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


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float) -> float:
    return round(float(value), 3)


def _duration(item: Mapping[str, Any]) -> float:
    duration = _float(item.get("duration"), -1.0)
    if duration >= 0:
        return duration
    start = _float(item.get("start"), 0.0)
    end = _float(item.get("end"), start)
    return max(0.0, end - start)


def _range_from_time(item: Mapping[str, Any]) -> Tuple[float, float, float]:
    time = item.get("time") if isinstance(item.get("time"), Mapping) else item
    start = _float(time.get("start"), 0.0) if isinstance(time, Mapping) else 0.0
    duration = _duration(time) if isinstance(time, Mapping) else 0.0
    end = _float(time.get("end"), start + duration) if isinstance(time, Mapping) else start + duration
    if end < start:
        end = start
    duration = max(0.0, duration if duration else end - start)
    return start, end, duration


def _path_kind(path: str) -> str:
    suffix = Path(str(path)).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "motion"
    if suffix in IMAGE_EXTS:
        return "still"
    return ""


def _first_path_kind(paths: Iterable[str]) -> str:
    for path in paths:
        kind = _path_kind(path)
        if kind:
            return kind
    return ""


def classify_visual(
    *,
    route: str = "",
    kind: str = "",
    resolved_path: str = "",
    candidate_paths: Optional[Sequence[str]] = None,
) -> str:
    """Return motion, still, or unknown for a planned visual item."""
    candidate_paths = candidate_paths or []
    resolved_kind = _path_kind(resolved_path)
    if resolved_kind:
        return resolved_kind

    candidate_kind = _first_path_kind(candidate_paths)
    if candidate_kind:
        return candidate_kind

    if kind in {"video", "motion_graphics"}:
        return "motion"
    if kind == "image":
        return "still"
    if route in MOTION_ROUTES:
        return "motion"
    if route in STILL_ROUTES:
        return "still"
    return "unknown"


def _load_transcript_segment(transcript_path: str, segment_id: Any) -> Optional[Mapping[str, Any]]:
    try:
        transcript = load_json(transcript_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    wanted = str(segment_id)
    for segment in transcript.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        if str(segment.get("id")) == wanted or str(segment.get("segment_id")) == wanted:
            return segment
    return None


def _clip_range(clip: Mapping[str, Any], base_dir: Path) -> Tuple[float, float, float]:
    if clip.get("duration") is not None or clip.get("end") is not None:
        return _range_from_time(clip)

    transcript_path = clip.get("transcript")
    if transcript_path and clip.get("segment_id") is not None:
        path = Path(str(transcript_path)).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        segment = _load_transcript_segment(str(path), clip.get("segment_id"))
        if segment:
            return _range_from_time(segment)

    start = _float(clip.get("start"), 0.0)
    return start, start, 0.0


def _segment(
    *,
    item_id: str,
    source: str,
    start: float,
    end: float,
    duration: float,
    route: str,
    kind: str,
    classification: str,
    status: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    unresolved_motion = classification == "motion" and status not in NON_BLOCKING_STATUSES
    return {
        "id": item_id,
        "source": source,
        "start": _round(start),
        "end": _round(end),
        "duration": _round(duration),
        "route": route,
        "kind": kind,
        "classification": classification,
        "status": status,
        "unresolved_motion": unresolved_motion,
        "reason": reason,
    }


def collect_from_storyboard(
    plan: Mapping[str, Any],
    asset_manifest: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    items_by_shot = {
        str(item.get("shot_id")): item
        for item in (asset_manifest or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    segments: List[Dict[str, Any]] = []
    for pos, shot in enumerate(plan.get("shots") or [], start=1):
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id") or f"shot_{pos:03d}")
        route_info = shot.get("generation_route") if isinstance(shot.get("generation_route"), Mapping) else {}
        item = items_by_shot.get(shot_id, {})
        route = str(item.get("route") or route_info.get("primary") or "")
        kind = str(item.get("kind") or "")
        status = str(item.get("status") or "")
        resolved_path = str(item.get("resolved_path") or "")
        candidate_paths = [str(p) for p in item.get("candidate_paths") or []]
        classification = classify_visual(
            route=route,
            kind=kind,
            resolved_path=resolved_path,
            candidate_paths=candidate_paths,
        )
        start, end, duration = _range_from_time(shot)
        segments.append(_segment(
            item_id=shot_id,
            source="storyboard_plan",
            start=start,
            end=end,
            duration=duration,
            route=route,
            kind=kind,
            classification=classification,
            status=status,
            reason=str(route_info.get("why") or item.get("next_action") or ""),
        ))
    return segments


def collect_from_asset_manifest(asset_manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for pos, item in enumerate(asset_manifest.get("items") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("shot_id") or f"asset_{pos:03d}")
        start, end, duration = _range_from_time(item)
        route = str(item.get("route") or "")
        kind = str(item.get("kind") or "")
        classification = classify_visual(
            route=route,
            kind=kind,
            resolved_path=str(item.get("resolved_path") or ""),
            candidate_paths=[str(p) for p in item.get("candidate_paths") or []],
        )
        segments.append(_segment(
            item_id=item_id,
            source="storyboard_assets",
            start=start,
            end=end,
            duration=duration,
            route=route,
            kind=kind,
            classification=classification,
            status=str(item.get("status") or ""),
            reason=str(item.get("next_action") or ""),
        ))
    return segments


def collect_from_render_config(config: Mapping[str, Any], *, base_dir: str = ".") -> List[Dict[str, Any]]:
    base = Path(base_dir).expanduser().resolve()
    segments: List[Dict[str, Any]] = []

    for pos, clip in enumerate(config.get("clips") or [], start=1):
        if not isinstance(clip, Mapping):
            continue
        start, end, duration = _clip_range(clip, base)
        route = "render_config_clip"
        if clip.get("broll"):
            route = "render_config_broll"
        classification = "motion" if clip.get("video") or clip.get("broll") else "unknown"
        segments.append(_segment(
            item_id=f"clip_{pos:03d}",
            source="render_config",
            start=start,
            end=end,
            duration=duration,
            route=route,
            kind="video" if classification == "motion" else "",
            classification=classification,
            reason=str(clip.get("text") or ""),
        ))

    for pos, overlay in enumerate(config.get("broll_overlays") or [], start=1):
        if not isinstance(overlay, Mapping):
            continue
        start, end, duration = _range_from_time(overlay)
        segments.append(_segment(
            item_id=f"broll_overlay_{pos:03d}",
            source="render_config",
            start=start,
            end=end,
            duration=duration,
            route="broll_overlay",
            kind="video",
            classification="motion",
            reason=str(overlay.get("reason") or ""),
        ))

    for pos, overlay in enumerate(config.get("image_overlays") or [], start=1):
        if not isinstance(overlay, Mapping):
            continue
        start, end, duration = _range_from_time(overlay)
        segments.append(_segment(
            item_id=f"image_overlay_{pos:03d}",
            source="render_config",
            start=start,
            end=end,
            duration=duration,
            route="image_overlay",
            kind="image",
            classification="still",
            reason=str(overlay.get("reason") or ""),
        ))

    return segments


def collect_from_enrich_plans(plans: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for plan_index, plan in enumerate(plans, start=1):
        prefix = f"enrich_{plan_index}"
        for pos, cue in enumerate(plan.get("broll") or [], start=1):
            if not isinstance(cue, Mapping):
                continue
            start, end, duration = _range_from_time({
                "start": cue.get("start", cue.get("timing_seconds", 0.0)),
                "end": cue.get("end"),
                "duration": cue.get("duration", 2.0),
            })
            asset = str(cue.get("suggested_asset") or cue.get("asset") or cue.get("path") or "")
            classification = classify_visual(route="media_library_broll", resolved_path=asset)
            segments.append(_segment(
                item_id=f"{prefix}_broll_{pos:03d}",
                source="enrich_plan",
                start=start,
                end=end,
                duration=duration,
                route="media_library_broll",
                kind="broll",
                classification=classification,
                reason=str(cue.get("reason") or ""),
            ))

        for pos, cue in enumerate(plan.get("imagegen") or [], start=1):
            if not isinstance(cue, Mapping):
                continue
            start = _float(cue.get("timing_seconds", cue.get("start")), 0.0)
            duration = _float(cue.get("duration"), 2.5)
            segments.append(_segment(
                item_id=f"{prefix}_imagegen_{pos:03d}",
                source="enrich_plan",
                start=start,
                end=start + duration,
                duration=duration,
                route="codex_imagegen",
                kind="image",
                classification="still",
                reason=str(cue.get("reason") or cue.get("concept") or ""),
            ))

        for pos, cue in enumerate(plan.get("chapter_cards") or [], start=1):
            if not isinstance(cue, Mapping):
                continue
            start, end, duration = _range_from_time(cue)
            segments.append(_segment(
                item_id=f"{prefix}_chapter_{pos:03d}",
                source="enrich_plan",
                start=start,
                end=end,
                duration=duration,
                route="chapter_card",
                kind="text_card",
                classification="still",
                reason=str(cue.get("title") or ""),
            ))

        for pos, cue in enumerate(plan.get("focus_events") or [], start=1):
            if not isinstance(cue, Mapping):
                continue
            start, end, duration = _range_from_time(cue)
            segments.append(_segment(
                item_id=f"{prefix}_focus_{pos:03d}",
                source="enrich_plan",
                start=start,
                end=end,
                duration=duration,
                route="screen_focus",
                kind="camera_motion",
                classification="motion",
                reason=str(cue.get("label") or ""),
            ))

    return segments


def _class_seconds(segments: Sequence[Mapping[str, Any]], classification: str) -> float:
    return sum(_float(segment.get("duration")) for segment in segments if segment.get("classification") == classification)


def _max_still_run(segments: Sequence[Mapping[str, Any]]) -> Tuple[float, List[str]]:
    max_run = 0.0
    current = 0.0
    current_ids: List[str] = []
    max_ids: List[str] = []
    for segment in sorted(segments, key=lambda item: (_float(item.get("start")), _float(item.get("end")))):
        classification = str(segment.get("classification") or "unknown")
        duration = _float(segment.get("duration"))
        if classification in {"still", "unknown"}:
            current += duration
            current_ids.append(str(segment.get("id")))
            if current > max_run:
                max_run = current
                max_ids = list(current_ids)
        else:
            current = 0.0
            current_ids = []
    return max_run, max_ids


def build_motion_guard(
    *,
    storyboard_plan: Optional[Mapping[str, Any]] = None,
    asset_manifest: Optional[Mapping[str, Any]] = None,
    render_config: Optional[Mapping[str, Any]] = None,
    render_config_base_dir: str = ".",
    enrich_plans: Optional[Sequence[Mapping[str, Any]]] = None,
    motion_required: bool = False,
    min_motion_ratio: float = 0.55,
    max_still_run: float = 6.0,
) -> Dict[str, Any]:
    primary_segments: List[Dict[str, Any]]
    if storyboard_plan:
        primary_segments = collect_from_storyboard(storyboard_plan, asset_manifest)
        primary_source = "storyboard_plan"
    elif asset_manifest:
        primary_segments = collect_from_asset_manifest(asset_manifest)
        primary_source = "storyboard_assets"
    elif render_config:
        primary_segments = collect_from_render_config(render_config, base_dir=render_config_base_dir)
        primary_source = "render_config"
    else:
        primary_segments = []
        primary_source = "none"

    enrichment_segments = collect_from_enrich_plans(enrich_plans or [])
    considered = primary_segments if primary_segments else enrichment_segments
    total_seconds = sum(_float(segment.get("duration")) for segment in considered)
    motion_seconds = _class_seconds(considered, "motion")
    still_seconds = _class_seconds(considered, "still")
    unknown_seconds = _class_seconds(considered, "unknown")
    motion_ratio = motion_seconds / total_seconds if total_seconds else 0.0
    still_run, still_run_ids = _max_still_run(considered)
    unresolved_motion = [
        segment for segment in considered
        if segment.get("unresolved_motion")
    ]

    findings: List[Dict[str, Any]] = []
    if total_seconds <= 0:
        findings.append({
            "severity": "fail",
            "code": "empty_timeline",
            "message": "No timed visual segments were found.",
            "segments": [],
        })

    if motion_required and total_seconds > 0 and motion_ratio < min_motion_ratio:
        findings.append({
            "severity": "fail",
            "code": "low_motion_ratio",
            "message": (
                f"Motion ratio {_round(motion_ratio)} is below required minimum "
                f"{_round(min_motion_ratio)}."
            ),
            "segments": [str(segment.get("id")) for segment in considered if segment.get("classification") != "motion"],
        })
    elif total_seconds > 0 and motion_ratio < min_motion_ratio:
        findings.append({
            "severity": "warn",
            "code": "low_motion_ratio",
            "message": (
                f"Motion ratio {_round(motion_ratio)} is below suggested minimum "
                f"{_round(min_motion_ratio)}."
            ),
            "segments": [str(segment.get("id")) for segment in considered if segment.get("classification") != "motion"],
        })

    if motion_required and still_run > max_still_run:
        findings.append({
            "severity": "fail",
            "code": "long_static_run",
            "message": (
                f"Longest still/unknown run {_round(still_run)}s exceeds "
                f"{_round(max_still_run)}s."
            ),
            "segments": still_run_ids,
        })
    elif still_run > max_still_run:
        findings.append({
            "severity": "warn",
            "code": "long_static_run",
            "message": (
                f"Longest still/unknown run {_round(still_run)}s exceeds "
                f"{_round(max_still_run)}s."
            ),
            "segments": still_run_ids,
        })

    if motion_required and unresolved_motion:
        findings.append({
            "severity": "fail",
            "code": "unresolved_motion_assets",
            "message": "Motion-classified shots still need generation, approval, search, or render work.",
            "segments": [str(segment.get("id")) for segment in unresolved_motion],
        })
    elif unresolved_motion:
        findings.append({
            "severity": "warn",
            "code": "unresolved_motion_assets",
            "message": "Motion-classified shots are not ready yet.",
            "segments": [str(segment.get("id")) for segment in unresolved_motion],
        })

    fail_count = sum(1 for finding in findings if finding["severity"] == "fail")
    warn_count = sum(1 for finding in findings if finding["severity"] == "warn")
    status = "fail" if fail_count else "warn" if warn_count else "pass"

    next_actions: List[str] = []
    codes = {finding["code"] for finding in findings}
    if "low_motion_ratio" in codes:
        next_actions.append("Replace some still/imagegen shots with B-roll, generated video, or local motion cards.")
    if "long_static_run" in codes:
        next_actions.append("Break long still runs with camera movement, screen focus, B-roll, or animated text cards.")
    if "unresolved_motion_assets" in codes:
        next_actions.append("Resolve motion-classified storyboard assets before render; confirm paid credits when needed.")
    if "empty_timeline" in codes:
        next_actions.append("Provide storyboard_plan, storyboard_assets, render_config, or enrich_plan with timed items.")

    return {
        "version": "motion_guard.v1",
        "status": status,
        "primary_source": primary_source,
        "settings": {
            "motion_required": motion_required,
            "min_motion_ratio": min_motion_ratio,
            "max_still_run": max_still_run,
        },
        "summary": {
            "blocking": fail_count,
            "warnings": warn_count,
            "segments": len(considered),
            "motion_required": motion_required,
            "total_seconds": _round(total_seconds),
            "motion_seconds": _round(motion_seconds),
            "still_seconds": _round(still_seconds),
            "unknown_seconds": _round(unknown_seconds),
            "motion_ratio": _round(motion_ratio),
            "still_ratio": _round(still_seconds / total_seconds if total_seconds else 0.0),
            "max_still_run": _round(still_run),
            "unresolved_motion_assets": len(unresolved_motion),
            "enrichment_segments": len(enrichment_segments),
        },
        "findings": findings,
        "segments": considered,
        "enrichment_segments": enrichment_segments,
        "next_actions": next_actions,
        "notes": [
            "This guard is a pre-render planning check; render_qa.py remains the post-render media-quality check.",
            "Candidate B-roll counts as motion for density, but storyboard_assets.py may still block until a candidate is linked.",
        ],
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Motion Guard",
        "",
        f"- Status: **{str(report.get('status', '')).upper()}**",
        f"- Primary source: `{report.get('primary_source', '')}`",
        f"- Motion required: {summary.get('motion_required', False)}",
        f"- Motion ratio: {summary.get('motion_ratio', 0)}",
        f"- Motion seconds: {summary.get('motion_seconds', 0)}/{summary.get('total_seconds', 0)}",
        f"- Max still run: {summary.get('max_still_run', 0)}s",
        f"- Blocking findings: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Findings",
        "",
    ]

    findings = report.get("findings") or []
    if findings:
        lines.extend([
            "| severity | code | segments | message |",
            "|---|---|---|---|",
        ])
        for finding in findings:
            lines.append(
                "| {severity} | `{code}` | {segments} | {message} |".format(
                    severity=finding.get("severity", ""),
                    code=finding.get("code", ""),
                    segments=", ".join(finding.get("segments") or []) or "-",
                    message=str(finding.get("message") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("No motion-density findings.")

    lines.extend([
        "",
        "## Segments",
        "",
        "| id | class | seconds | route | status |",
        "|---|---|---:|---|---|",
    ])
    for segment in report.get("segments") or []:
        lines.append(
            "| {id} | {classification} | {duration} | `{route}` | {status} |".format(
                id=segment.get("id", ""),
                classification=segment.get("classification", ""),
                duration=segment.get("duration", 0),
                route=segment.get("route", ""),
                status=segment.get("status", "") or "-",
            )
        )

    actions = report.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check pre-render motion density from storyboard/render artifacts."
    )
    parser.add_argument("--storyboard-plan", help="storyboard_plan.json from storyboard_plan.py.")
    parser.add_argument("--asset-manifest", help="storyboard_assets.json from storyboard_assets.py.")
    parser.add_argument("--render-config", help="render_config.json for render_final.py.")
    parser.add_argument("--enrich-plan", action="append", default=[], help="Optional enrich_plan JSON; repeatable.")
    parser.add_argument("--motion-required", action="store_true", help="Fail when a motion-led delivery is too still-heavy.")
    parser.add_argument("--min-motion-ratio", type=float, default=0.55, help="Minimum motion seconds / total seconds.")
    parser.add_argument("--max-still-run", type=float, default=6.0, help="Maximum consecutive still/unknown seconds.")
    parser.add_argument("--output", help="Write JSON report.")
    parser.add_argument("--markdown", help="Write Markdown report.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when status is fail.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not any([args.storyboard_plan, args.asset_manifest, args.render_config, args.enrich_plan]):
        print("Error: provide at least one storyboard/render/enrich artifact.", file=sys.stderr)
        return 2

    storyboard_plan = load_json(args.storyboard_plan) if args.storyboard_plan else None
    asset_manifest = load_json(args.asset_manifest) if args.asset_manifest else None
    render_config = load_json(args.render_config) if args.render_config else None
    enrich_plans = [load_json(path) for path in args.enrich_plan]
    render_config_base_dir = os.path.dirname(os.path.abspath(args.render_config)) if args.render_config else "."

    report = build_motion_guard(
        storyboard_plan=storyboard_plan,
        asset_manifest=asset_manifest,
        render_config=render_config,
        render_config_base_dir=render_config_base_dir,
        enrich_plans=enrich_plans,
        motion_required=args.motion_required,
        min_motion_ratio=args.min_motion_ratio,
        max_still_run=args.max_still_run,
    )

    if args.output:
        write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))

    summary = report["summary"]
    print(
        "motion_guard: {status} motion_ratio={ratio} max_still_run={still}s blocking={blocking} warnings={warnings}".format(
            status=report["status"],
            ratio=summary["motion_ratio"],
            still=summary["max_still_run"],
            blocking=summary["blocking"],
            warnings=summary["warnings"],
        )
    )
    return 2 if args.strict and report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
