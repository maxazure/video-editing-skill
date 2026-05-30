#!/usr/bin/env python3
"""Plan transition bridge shots between adjacent storyboard shots.

This script never submits generation jobs. It creates an auditable JSON/Markdown
plan for optional AI transition clips, including frame references and paid-credit
approval notes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from storyboard_assets import ROUTE_SPECS
from storyboard_plan import ROUTING_SENTENCE


PAID_APPROVAL_NOTE = (
    "Dreamina/即梦 transition generation may consume credits; confirm before submitting."
)

DEFAULT_TRANSITION_STYLE = "smooth narrative continuity, natural motion, clean edit point"


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _round2(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return round(default, 2)


def _shot_id(shot: Dict[str, Any], index: int) -> str:
    return str(shot.get("id") or f"shot_{index + 1:03d}")


def _shot_route(shot: Dict[str, Any]) -> str:
    route = shot.get("generation_route") or {}
    return str(route.get("primary") or "media_library_broll")


def _sort_shots(shots: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexed = list(enumerate(shots))

    def key(item: Tuple[int, Dict[str, Any]]) -> Tuple[float, int]:
        idx, shot = item
        try:
            start = float(shot.get("start"))
        except (TypeError, ValueError):
            start = float(idx)
        return start, idx

    return [shot for _, shot in sorted(indexed, key=key)]


def _asset_items_by_shot(asset_manifest: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not asset_manifest:
        return {}
    return {
        str(item.get("shot_id")): item
        for item in asset_manifest.get("items") or []
        if item.get("shot_id")
    }


def _expected_asset_path(asset_root: Path, shot: Dict[str, Any], shot_id: str) -> str:
    route = _shot_route(shot)
    spec = ROUTE_SPECS.get(route, ROUTE_SPECS["media_library_broll"])
    ext = ".png" if spec["kind"] == "image" else ".mp4"
    return str(asset_root / str(spec["dir"]) / f"{shot_id}{ext}")


def _explicit_asset_path(shot: Dict[str, Any]) -> str:
    for key in ("asset_path", "resolved_asset_path", "media_path", "output_path"):
        value = shot.get(key)
        if value:
            return str(value)
    asset = shot.get("asset")
    if isinstance(asset, dict):
        for key in ("path", "file", "output_path"):
            value = asset.get(key)
            if value:
                return str(value)
    return ""


def _frame_reference(
    shot: Dict[str, Any],
    *,
    shot_id: str,
    role: str,
    asset_item: Optional[Dict[str, Any]],
    asset_root: Path,
) -> Dict[str, Any]:
    path = ""
    status = "expected"
    source = "storyboard_expected_path"
    candidate_scores: List[Dict[str, Any]] = []

    explicit = _explicit_asset_path(shot)
    if explicit:
        path = explicit
        status = "explicit"
        source = "storyboard_shot"
    elif asset_item:
        if asset_item.get("resolved_path"):
            path = str(asset_item.get("resolved_path"))
            status = "resolved"
            source = "storyboard_assets"
        elif asset_item.get("candidate_paths"):
            path = str((asset_item.get("candidate_paths") or [""])[0])
            status = "candidate"
            source = "storyboard_assets"
            candidate_scores = list(asset_item.get("candidate_scores") or [])
        elif asset_item.get("expected_path"):
            path = str(asset_item.get("expected_path"))
            status = str(asset_item.get("status") or "expected")
            source = "storyboard_assets"
    if not path:
        path = _expected_asset_path(asset_root, shot, shot_id)

    instruction = (
        "Use the ending frame of this shot as the first frame reference."
        if role == "from"
        else "Use the opening frame of this shot as the last frame reference."
    )

    return {
        "shot_id": shot_id,
        "role": "ending_frame" if role == "from" else "opening_frame",
        "asset_path": path,
        "asset_status": status,
        "source": source,
        "candidate_scores": candidate_scores[:3],
        "frame_instruction": instruction,
    }


def _shared_keywords(prev: Dict[str, Any], nxt: Dict[str, Any]) -> List[str]:
    prev_keywords = {str(k).lower() for k in prev.get("keywords") or []}
    next_keywords = {str(k).lower() for k in nxt.get("keywords") or []}
    return sorted(prev_keywords & next_keywords)


def _transition_need_score(prev: Dict[str, Any], nxt: Dict[str, Any]) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    if prev.get("section") != nxt.get("section"):
        score += 1.0
        reasons.append("section-change")
    if _shot_route(prev) != _shot_route(nxt):
        score += 1.0
        reasons.append("route-change")
    if not _shared_keywords(prev, nxt):
        score += 0.75
        reasons.append("keyword-shift")
    if nxt.get("section") == "cta":
        score -= 0.75
        reasons.append("cta-prefers-deterministic")
    if prev.get("section") == "hook" and nxt.get("section") in {"pain", "turn"}:
        score += 0.5
        reasons.append("hook-to-story-turn")

    return round(max(0.0, score), 2), reasons


def _bridge_route(
    *,
    mode: str,
    need_score: float,
    ai_count: int,
    max_ai_bridges: int,
) -> Tuple[str, str, bool, str]:
    if mode == "skip":
        return "skip", "straight_cut", False, "Transition planning skipped by mode."
    if mode == "default":
        return (
            "deterministic_crossfade",
            "straight_cut",
            False,
            "Use a local edit transition; no generated media needed.",
        )
    if mode == "ai" or (mode == "auto" and need_score >= 1.75 and ai_count < max_ai_bridges):
        return (
            "dreamina_video",
            "deterministic_crossfade",
            True,
            "Generate a short bridge only after approval; fall back to a local transition.",
        )
    return (
        "deterministic_crossfade",
        "straight_cut",
        False,
        "Adjacent shots can use a simple local transition.",
    )


def _anchor_text(prev: Dict[str, Any], nxt: Dict[str, Any]) -> List[str]:
    anchors: List[str] = []
    for shot in (prev, nxt):
        continuity = shot.get("continuity") or {}
        for anchor in continuity.get("anchors") or []:
            text = str(anchor).strip()
            if text and text not in anchors:
                anchors.append(text)
    return anchors[:8]


def _bridge_prompt(
    *,
    bridge_id: str,
    prev: Dict[str, Any],
    nxt: Dict[str, Any],
    prev_id: str,
    next_id: str,
    target_aspect: str,
    duration: float,
    style: str,
) -> str:
    prev_keywords = ", ".join(str(k) for k in (prev.get("keywords") or [])) or str(prev.get("section") or prev_id)
    next_keywords = ", ".join(str(k) for k in (nxt.get("keywords") or [])) or str(nxt.get("section") or next_id)
    prev_narration = str(prev.get("narration") or "").strip()
    next_narration = str(nxt.get("narration") or "").strip()
    return (
        f"{bridge_id}: Create a short transition video for a {target_aspect} social edit. "
        f"Start from the exact ending frame of {prev_id} ({prev_keywords}) and end on the exact "
        f"opening frame of {next_id} ({next_keywords}). "
        f"Motion style: {style}. Suggested duration: {duration:.2f}s. "
        "Preserve subject scale, color palette, lighting direction, and subtitle-safe lower third. "
        "No readable embedded text, no watermark, no UI chrome, no logo unless present in source frames. "
        f"Narrative handoff: previous beat \"{prev_narration[:90]}\" -> next beat \"{next_narration[:90]}\"."
    )


def build_transition_bridge_plan(
    storyboard_plan: Dict[str, Any],
    *,
    asset_manifest: Optional[Dict[str, Any]] = None,
    asset_root: str = "work",
    mode: str = "auto",
    max_ai_bridges: int = 3,
    duration: float = 1.2,
    style: str = DEFAULT_TRANSITION_STYLE,
) -> Dict[str, Any]:
    shots = _sort_shots(storyboard_plan.get("shots") or [])
    target = storyboard_plan.get("target") or {}
    target_aspect = str(target.get("aspect") or "9:16")
    asset_root_path = Path(asset_root).expanduser().resolve()
    asset_by_shot = _asset_items_by_shot(asset_manifest)

    bridges: List[Dict[str, Any]] = []
    ai_count = 0
    status_counts: Dict[str, int] = {}

    if mode != "skip":
        for idx in range(max(0, len(shots) - 1)):
            prev = shots[idx]
            nxt = shots[idx + 1]
            prev_id = _shot_id(prev, idx)
            next_id = _shot_id(nxt, idx + 1)
            need_score, need_reasons = _transition_need_score(prev, nxt)
            route, fallback, paid, next_action = _bridge_route(
                mode=mode,
                need_score=need_score,
                ai_count=ai_count,
                max_ai_bridges=max(0, max_ai_bridges),
            )
            if paid:
                ai_count += 1
            status = "needs_approval" if paid else "planned_local_transition"
            status_counts[status] = status_counts.get(status, 0) + 1
            bridge_id = f"transition_{idx + 1:03d}"
            expected_path = asset_root_path / "generated_video" / f"{bridge_id}.mp4"

            bridges.append({
                "id": bridge_id,
                "from_shot": prev_id,
                "to_shot": next_id,
                "time": {
                    "insert_after": _round2(prev.get("end")),
                    "before": _round2(nxt.get("start"), _round2(prev.get("end"))),
                    "suggested_duration": _round2(duration, 1.2),
                },
                "route": route,
                "fallback_route": fallback,
                "status": status,
                "blocking": paid,
                "requires_paid_credits": paid,
                "approval_note": PAID_APPROVAL_NOTE if paid else "",
                "need_score": need_score,
                "need_reasons": need_reasons,
                "continuity_anchors": _anchor_text(prev, nxt),
                "reference_frames": {
                    "first_frame": _frame_reference(
                        prev,
                        shot_id=prev_id,
                        role="from",
                        asset_item=asset_by_shot.get(prev_id),
                        asset_root=asset_root_path,
                    ),
                    "last_frame": _frame_reference(
                        nxt,
                        shot_id=next_id,
                        role="to",
                        asset_item=asset_by_shot.get(next_id),
                        asset_root=asset_root_path,
                    ),
                },
                "expected_path": str(expected_path),
                "prompt": _bridge_prompt(
                    bridge_id=bridge_id,
                    prev=prev,
                    nxt=nxt,
                    prev_id=prev_id,
                    next_id=next_id,
                    target_aspect=target_aspect,
                    duration=_round2(duration, 1.2),
                    style=style,
                ),
                "negative_prompt": (
                    "Avoid readable text, flicker, aspect-ratio jumps, watermarks, hard subtitles, "
                    "new logos, distorted faces, or sudden color changes."
                ),
                "next_action": next_action,
            })

    blocking = sum(1 for bridge in bridges if bridge["blocking"])
    return {
        "version": "transition_bridge_plan.v1",
        "routing_note": ROUTING_SENTENCE,
        "source": {
            "storyboard_version": storyboard_plan.get("version"),
            "shots": len(shots),
            "asset_manifest_version": (asset_manifest or {}).get("version"),
        },
        "target": {
            "platform": target.get("platform") or "xhs",
            "aspect": target_aspect,
            "mode": mode,
            "style": style,
            "max_ai_bridges": max_ai_bridges,
        },
        "summary": {
            "bridges": len(bridges),
            "blocking": blocking,
            "paid_credit_tasks": ai_count,
            **{f"status_{key}": value for key, value in sorted(status_counts.items())},
        },
        "bridges": bridges,
        "next_steps": [
            "Review bridge prompts and frame references before generation.",
            "Confirm Dreamina/即梦 credits before submitting any dreamina_video bridge.",
            "Save approved generated transition clips at expected_path.",
            "Fallback to deterministic_crossfade or straight cuts when bridge risk/cost is not worth it.",
            "After inserting transition clips, run render_qa.py and timeline_view.py.",
        ],
    }


def emit_markdown(plan: Dict[str, Any]) -> str:
    summary = plan.get("summary") or {}
    lines = [
        "# Transition Bridge Plan",
        "",
        plan.get("routing_note", ROUTING_SENTENCE),
        "",
        f"- Mode: `{(plan.get('target') or {}).get('mode', '')}`",
        f"- Bridges: {summary.get('bridges', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Paid-credit tasks: {summary.get('paid_credit_tasks', 0)}",
        "",
        "| bridge | from -> to | route | status | references | next action |",
        "|---|---|---|---|---|---|",
    ]
    for bridge in plan.get("bridges") or []:
        refs = bridge.get("reference_frames") or {}
        first = refs.get("first_frame") or {}
        last = refs.get("last_frame") or {}
        ref_text = (
            f"first `{first.get('asset_path', '')}` ({first.get('asset_status', '')})<br>"
            f"last `{last.get('asset_path', '')}` ({last.get('asset_status', '')})"
        )
        action = str(bridge.get("next_action") or "").replace("|", "/")
        lines.append(
            "| {id} | {from_shot} -> {to_shot} | {route} | {status} | {refs} | {action} |".format(
                id=bridge.get("id", ""),
                from_shot=bridge.get("from_shot", ""),
                to_shot=bridge.get("to_shot", ""),
                route=bridge.get("route", ""),
                status=bridge.get("status", ""),
                refs=ref_text,
                action=action,
            )
        )
    lines.append("")

    for bridge in plan.get("bridges") or []:
        lines.extend([
            f"## {bridge.get('id')} · {bridge.get('from_shot')} -> {bridge.get('to_shot')}",
            "",
            f"- Need score: {bridge.get('need_score')} ({', '.join(bridge.get('need_reasons') or [])})",
            f"- Fallback: `{bridge.get('fallback_route')}`",
        ])
        if bridge.get("approval_note"):
            lines.append(f"- Approval: {bridge['approval_note']}")
        anchors = bridge.get("continuity_anchors") or []
        if anchors:
            lines.append(f"- Continuity: {', '.join(anchors)}")
        lines.extend([
            "",
            "```text",
            str(bridge.get("prompt") or ""),
            "```",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build optional transition bridge prompts between storyboard shots."
    )
    parser.add_argument("--storyboard-plan", required=True, help="Input storyboard_plan.json.")
    parser.add_argument("--asset-manifest", help="Optional storyboard_assets.json for frame references.")
    parser.add_argument("--asset-root", default="work", help="Root for expected/generated assets.")
    parser.add_argument("--output", required=True, help="Output transition bridge plan JSON.")
    parser.add_argument("--markdown", help="Optional Markdown review table.")
    parser.add_argument(
        "--mode",
        choices=("auto", "ai", "default", "skip"),
        default="auto",
        help="auto recommends AI bridges only for stronger visual jumps; ai marks every bridge for approval.",
    )
    parser.add_argument("--max-ai-bridges", type=int, default=3, help="Maximum AI bridge tasks in auto mode.")
    parser.add_argument("--duration", type=float, default=1.2, help="Suggested bridge duration in seconds.")
    parser.add_argument("--style", default=DEFAULT_TRANSITION_STYLE, help="Natural-language transition style.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when any bridge needs approval.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    storyboard_plan = load_json(args.storyboard_plan)
    asset_manifest = load_json(args.asset_manifest) if args.asset_manifest else None
    plan = build_transition_bridge_plan(
        storyboard_plan,
        asset_manifest=asset_manifest,
        asset_root=args.asset_root,
        mode=args.mode,
        max_ai_bridges=args.max_ai_bridges,
        duration=args.duration,
        style=args.style,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    summary = plan["summary"]
    print(
        "Wrote transition bridge plan: "
        f"{args.output}; bridges={summary['bridges']} blocking={summary['blocking']}"
    )
    if args.markdown:
        print(f"Wrote transition bridge markdown: {args.markdown}")
    if args.strict and summary["blocking"]:
        print("Transition bridge strict check failed: some bridges need approval.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
