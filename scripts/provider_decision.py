#!/usr/bin/env python3
"""Build an auditable provider decision log from storyboard asset tasks.

The script scores local/generation routes before any paid or external work is
submitted. It does not call image, video, stock, or renderer providers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from storyboard_plan import ROUTING_SENTENCE


SCORE_WEIGHTS = {
    "task_fit": 0.30,
    "output_quality": 0.20,
    "control": 0.15,
    "reliability": 0.15,
    "cost_efficiency": 0.10,
    "latency": 0.05,
    "continuity": 0.05,
}


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    label: str
    route: str
    provider: str
    estimated_usd: float
    paid_credit: bool
    approval_note: str
    dimensions: Dict[str, float]
    command_requirements: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "existing_asset": ProviderSpec(
        provider_id="existing_asset",
        label="Existing reviewed asset",
        route="ready",
        provider="local_file",
        estimated_usd=0.0,
        paid_credit=False,
        approval_note="",
        dimensions={
            "task_fit": 1.0,
            "output_quality": 0.9,
            "control": 0.8,
            "reliability": 1.0,
            "cost_efficiency": 1.0,
            "latency": 1.0,
            "continuity": 0.9,
        },
        notes=("No provider call needed; an asset already exists.",),
    ),
    "local_media_candidate": ProviderSpec(
        provider_id="local_media_candidate",
        label="Ranked local media candidate",
        route="media_library_broll",
        provider="media_library",
        estimated_usd=0.0,
        paid_credit=False,
        approval_note="",
        dimensions={
            "task_fit": 0.82,
            "output_quality": 0.72,
            "control": 0.55,
            "reliability": 0.92,
            "cost_efficiency": 1.0,
            "latency": 0.95,
            "continuity": 0.76,
        },
        notes=("Uses already indexed local footage or images.",),
    ),
    "codex_imagegen": ProviderSpec(
        provider_id="codex_imagegen",
        label="Codex image_gen / gpt-image-2",
        route="codex_imagegen",
        provider="openai_gpt_image_2",
        estimated_usd=0.0,
        paid_credit=False,
        approval_note="",
        dimensions={
            "task_fit": 0.88,
            "output_quality": 0.86,
            "control": 0.72,
            "reliability": 0.84,
            "cost_efficiency": 0.96,
            "latency": 0.72,
            "continuity": 0.74,
        },
        notes=("Best for abstract metaphors, stills, covers, and fallback B-roll.",),
    ),
    "dreamina_video": ProviderSpec(
        provider_id="dreamina_video",
        label="Dreamina/即梦 generated video",
        route="dreamina_video",
        provider="dreamina_cli",
        estimated_usd=0.75,
        paid_credit=True,
        approval_note="Dreamina/即梦 generation may consume credits; confirm before submitting.",
        dimensions={
            "task_fit": 0.9,
            "output_quality": 0.82,
            "control": 0.68,
            "reliability": 0.62,
            "cost_efficiency": 0.48,
            "latency": 0.42,
            "continuity": 0.64,
        },
        command_requirements=("dreamina",),
        notes=("Useful for motion shots, but async and paid-credit gated.",),
    ),
    "remotion_hyperframes": ProviderSpec(
        provider_id="remotion_hyperframes",
        label="Local Remotion/HyperFrames motion card",
        route="remotion_hyperframes",
        provider="local_renderer",
        estimated_usd=0.0,
        paid_credit=False,
        approval_note="",
        dimensions={
            "task_fit": 0.84,
            "output_quality": 0.78,
            "control": 0.92,
            "reliability": 0.82,
            "cost_efficiency": 1.0,
            "latency": 0.68,
            "continuity": 0.88,
        },
        command_requirements=("node",),
        notes=("Best for deterministic text, stats, cards, and charts.",),
    ),
    "media_library_broll": ProviderSpec(
        provider_id="media_library_broll",
        label="Local media library B-roll search",
        route="media_library_broll",
        provider="media_library",
        estimated_usd=0.0,
        paid_credit=False,
        approval_note="",
        dimensions={
            "task_fit": 0.76,
            "output_quality": 0.68,
            "control": 0.5,
            "reliability": 0.88,
            "cost_efficiency": 1.0,
            "latency": 0.9,
            "continuity": 0.7,
        },
        notes=("Prefer existing footage before generating new media.",),
    ),
}


ROUTE_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "codex_imagegen": ("codex_imagegen", "media_library_broll", "remotion_hyperframes"),
    "dreamina_video": ("dreamina_video", "media_library_broll", "codex_imagegen", "remotion_hyperframes"),
    "remotion_hyperframes": ("remotion_hyperframes", "codex_imagegen", "media_library_broll"),
    "media_library_broll": ("media_library_broll", "codex_imagegen", "remotion_hyperframes"),
}


def load_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _weighted_score(dimensions: Mapping[str, float]) -> float:
    return round(
        sum(float(dimensions.get(k, 0.0)) * weight for k, weight in SCORE_WEIGHTS.items()),
        4,
    )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _command_exists(name: str, command_lookup: Optional[Callable[[str], bool]]) -> bool:
    if command_lookup is not None:
        return bool(command_lookup(name))
    return shutil.which(name) is not None


def _apply_cost_overrides(
    specs: Dict[str, ProviderSpec],
    cost_overrides: Optional[Mapping[str, float]],
) -> Dict[str, ProviderSpec]:
    if not cost_overrides:
        return specs
    updated = dict(specs)
    for key, cost in cost_overrides.items():
        spec = updated.get(key)
        if spec is None:
            continue
        updated[key] = ProviderSpec(
            provider_id=spec.provider_id,
            label=spec.label,
            route=spec.route,
            provider=spec.provider,
            estimated_usd=max(0.0, float(cost)),
            paid_credit=spec.paid_credit,
            approval_note=spec.approval_note,
            dimensions=dict(spec.dimensions),
            command_requirements=spec.command_requirements,
            notes=spec.notes,
        )
    return updated


def _score_option(
    *,
    item: Mapping[str, Any],
    spec: ProviderSpec,
    requested_route: str,
    action_approval_usd: float,
    command_lookup: Optional[Callable[[str], bool]],
) -> Dict[str, Any]:
    dimensions = dict(spec.dimensions)
    reasons: List[str] = list(spec.notes)

    if spec.route == requested_route:
        dimensions["task_fit"] = _clamp(dimensions.get("task_fit", 0.0) + 0.08)
        reasons.append("Matches the storyboard primary route.")
    elif spec.route == item.get("fallback_route"):
        dimensions["continuity"] = _clamp(dimensions.get("continuity", 0.0) + 0.08)
        reasons.append("Matches the storyboard fallback route.")
    else:
        dimensions["task_fit"] = _clamp(dimensions.get("task_fit", 0.0) - 0.05)

    if item.get("candidate_paths") and spec.route == "media_library_broll":
        dimensions["reliability"] = _clamp(dimensions.get("reliability", 0.0) + 0.08)
        dimensions["latency"] = _clamp(dimensions.get("latency", 0.0) + 0.06)
        reasons.append("Manifest already contains ranked local candidates.")

    missing_commands = [
        cmd for cmd in spec.command_requirements
        if not _command_exists(cmd, command_lookup)
    ]
    available = not missing_commands
    rejected_because = ""
    if missing_commands:
        dimensions["reliability"] = _clamp(dimensions.get("reliability", 0.0) - 0.45)
        dimensions["latency"] = _clamp(dimensions.get("latency", 0.0) - 0.15)
        rejected_because = "Missing command(s): " + ", ".join(missing_commands)
        reasons.append(rejected_because)

    approval_required = spec.paid_credit or spec.estimated_usd > action_approval_usd
    if approval_required and spec.approval_note:
        reasons.append(spec.approval_note)
    if spec.estimated_usd > action_approval_usd:
        reasons.append(
            f"Estimated cost ${spec.estimated_usd:.2f} exceeds single-action threshold ${action_approval_usd:.2f}."
        )

    score = _weighted_score(dimensions)
    return {
        "option_id": spec.provider_id,
        "label": spec.label,
        "route": spec.route,
        "provider": spec.provider,
        "score": score,
        "dimensions": {k: round(float(v), 3) for k, v in dimensions.items()},
        "estimated_usd": round(float(spec.estimated_usd), 4),
        "paid_credit": bool(spec.paid_credit),
        "approval_required": bool(approval_required),
        "available": available,
        "missing_requirements": missing_commands,
        "reason": " ".join(reasons),
        "rejected_because": rejected_because,
    }


def _options_for_item(item: Mapping[str, Any]) -> Tuple[str, ...]:
    status = str(item.get("status") or "")
    if status == "ready":
        return ("existing_asset",)
    if status == "candidate_found":
        return ("local_media_candidate", "media_library_broll", "codex_imagegen")
    route = str(item.get("route") or "media_library_broll")
    return ROUTE_OPTIONS.get(route, ("media_library_broll", "codex_imagegen", "remotion_hyperframes"))


def _select_option(options: Sequence[Dict[str, Any]], preferred_route: str) -> Dict[str, Any]:
    available = [opt for opt in options if opt.get("available")]
    preferred = [opt for opt in available if opt.get("route") == preferred_route]
    pool = preferred or available or list(options)
    return max(pool, key=lambda opt: float(opt.get("score") or 0.0))


def parse_cost_overrides(values: Optional[Sequence[str]]) -> Dict[str, float]:
    overrides: Dict[str, float] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --route-cost value {raw!r}; expected provider_id=usd")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --route-cost value {raw!r}; provider_id is empty")
        overrides[key] = float(value)
    return overrides


def build_provider_decision_log(
    manifest: Mapping[str, Any],
    *,
    budget_cap_usd: float = 10.0,
    action_approval_usd: float = 0.50,
    cost_overrides: Optional[Mapping[str, float]] = None,
    command_lookup: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    specs = _apply_cost_overrides(PROVIDER_SPECS, cost_overrides)
    decisions: List[Dict[str, Any]] = []
    estimated_total = 0.0
    approval_count = 0
    paid_count = 0
    budget_blocked = 0
    unavailable_selected = 0
    fallback_selected = 0

    for idx, item in enumerate(manifest.get("items") or [], start=1):
        requested_route = str(item.get("route") or "media_library_broll")
        option_ids = _options_for_item(item)
        options = [
            _score_option(
                item=item,
                spec=specs[option_id],
                requested_route=requested_route,
                action_approval_usd=action_approval_usd,
                command_lookup=command_lookup,
            )
            for option_id in option_ids
            if option_id in specs
        ]
        selected = _select_option(options, requested_route)
        estimated_total += float(selected.get("estimated_usd") or 0.0)

        over_cap = estimated_total > budget_cap_usd
        approval_required = bool(selected.get("approval_required"))
        if approval_required:
            approval_count += 1
        if selected.get("paid_credit"):
            paid_count += 1
        if over_cap:
            budget_blocked += 1
        if not selected.get("available"):
            unavailable_selected += 1
        is_fallback = (
            item.get("status") != "ready"
            and selected.get("route") not in (requested_route, "ready")
        )
        if is_fallback:
            fallback_selected += 1

        status = "ready_to_execute"
        if over_cap:
            status = "budget_blocked"
        elif approval_required:
            status = "needs_approval"
        elif not selected.get("available"):
            status = "missing_requirement"
        elif is_fallback:
            status = "fallback_selected"
        elif item.get("status") == "ready":
            status = "ready"
        elif item.get("status") == "candidate_found":
            status = "candidate_found"

        next_action = str(item.get("next_action") or "")
        if over_cap:
            next_action = "Lower scope, choose a cheaper fallback, or raise the budget cap before generation."
        elif approval_required and selected.get("paid_credit"):
            next_action = "Ask for explicit paid-credit approval before submitting this generation task."
        elif selected.get("missing_requirements"):
            next_action = "Install or configure missing requirements before using this provider."
        elif is_fallback:
            next_action = "Review the fallback downgrade before generating or linking this asset."
        elif selected["option_id"] == "codex_imagegen":
            next_action = "Generate with Codex built-in image_gen and save at the expected path."
        elif selected["option_id"] == "remotion_hyperframes":
            next_action = "Render the local motion card, then link it into render_config or enrich_plan."

        decisions.append({
            "decision_id": f"pd_{idx:03d}",
            "stage": "asset_generation",
            "category": "provider_selection",
            "subject": f"{item.get('shot_id', f'shot_{idx:03d}')} {requested_route}",
            "shot_id": item.get("shot_id"),
            "asset_status": item.get("status"),
            "expected_path": item.get("expected_path"),
            "selected": selected["option_id"],
            "selected_label": selected["label"],
            "selected_route": selected["route"],
            "status": status,
            "approval_required": approval_required,
            "paid_credit": bool(selected.get("paid_credit")),
            "budget_status": "over_cap" if over_cap else "within_cap",
            "estimated_usd": selected.get("estimated_usd"),
            "confidence": round(float(selected.get("score") or 0.0), 4),
            "reason": selected.get("reason", ""),
            "next_action": next_action,
            "options_considered": options,
        })

    summary = {
        "items": len(decisions),
        "approval_required": approval_count,
        "paid_credit_tasks": paid_count,
        "budget_blocked": budget_blocked,
        "selected_missing_requirements": unavailable_selected,
        "fallback_selected": fallback_selected,
        "estimated_total_usd": round(estimated_total, 4),
    }
    return {
        "version": "provider_decision_log.v1",
        "routing_note": ROUTING_SENTENCE,
        "source": {
            "asset_manifest_version": manifest.get("version"),
            "asset_root": manifest.get("asset_root"),
            "items": len(manifest.get("items") or []),
        },
        "policy": {
            "score_weights": SCORE_WEIGHTS,
            "budget_cap_usd": round(float(budget_cap_usd), 4),
            "single_action_approval_usd": round(float(action_approval_usd), 4),
            "cost_overrides": {k: round(float(v), 4) for k, v in (cost_overrides or {}).items()},
        },
        "summary": summary,
        "decisions": decisions,
        "next_steps": [
            "Review decisions with status needs_approval or budget_blocked before generation.",
            "Use Codex image_gen for codex_imagegen prompts; do not call paid providers implicitly.",
            "Confirm Dreamina/即梦 credits before submitting dreamina_video tasks.",
            "After assets are linked, rerun storyboard_assets.py --strict and render_qa.py.",
        ],
    }


def emit_markdown(log: Mapping[str, Any]) -> str:
    summary = log.get("summary") or {}
    lines = [
        "# Provider Decision Log",
        "",
        str(log.get("routing_note") or ROUTING_SENTENCE),
        "",
        f"- Items: {summary.get('items', 0)}",
        f"- Estimated total: `${float(summary.get('estimated_total_usd') or 0.0):.2f}`",
        f"- Needs approval: {summary.get('approval_required', 0)}",
        f"- Budget blocked: {summary.get('budget_blocked', 0)}",
        "",
        "| shot | selected | status | score | estimate | next action |",
        "|---|---|---|---:|---:|---|",
    ]
    for decision in log.get("decisions") or []:
        lines.append(
            "| {shot} | {selected} | {status} | {score:.2f} | ${cost:.2f} | {action} |".format(
                shot=decision.get("shot_id", ""),
                selected=decision.get("selected_label", decision.get("selected", "")),
                status=decision.get("status", ""),
                score=float(decision.get("confidence") or 0.0),
                cost=float(decision.get("estimated_usd") or 0.0),
                action=str(decision.get("next_action") or "").replace("|", "/"),
            )
        )
    lines.append("")

    for decision in log.get("decisions") or []:
        lines.extend([
            f"## {decision.get('decision_id')} · {decision.get('shot_id')}",
            "",
            f"- Selected: `{decision.get('selected')}`",
            f"- Status: `{decision.get('status')}`",
            f"- Reason: {decision.get('reason', '')}",
            "",
            "| option | score | available | approval | reason |",
            "|---|---:|---|---|---|",
        ])
        for option in decision.get("options_considered") or []:
            lines.append(
                "| {label} | {score:.2f} | {available} | {approval} | {reason} |".format(
                    label=option.get("label", option.get("option_id", "")),
                    score=float(option.get("score") or 0.0),
                    available="yes" if option.get("available") else "no",
                    approval="yes" if option.get("approval_required") else "no",
                    reason=str(option.get("reason") or "").replace("|", "/"),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score storyboard asset providers and write an auditable decision log."
    )
    parser.add_argument("--asset-manifest", required=True, help="Input storyboard_assets.json.")
    parser.add_argument("--output", required=True, help="Output provider decision JSON.")
    parser.add_argument("--markdown", help="Optional Markdown decision report.")
    parser.add_argument("--budget-cap", type=float, default=10.0, help="Total estimate cap in USD.")
    parser.add_argument(
        "--single-action-approval",
        type=float,
        default=0.50,
        help="Require approval when one selected option exceeds this estimate.",
    )
    parser.add_argument(
        "--route-cost",
        action="append",
        default=[],
        help="Override provider estimate, e.g. dreamina_video=1.20.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when a selected task needs approval, exceeds budget, or lacks requirements.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = load_manifest(args.asset_manifest)
    overrides = parse_cost_overrides(args.route_cost)
    log = build_provider_decision_log(
        manifest,
        budget_cap_usd=args.budget_cap,
        action_approval_usd=args.single_action_approval,
        cost_overrides=overrides,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(log))

    summary = log["summary"]
    print(
        "Wrote provider decision log: "
        f"{args.output}; items={summary['items']} approvals={summary['approval_required']} "
        f"estimate=${summary['estimated_total_usd']:.2f}"
    )
    if args.markdown:
        print(f"Wrote provider decision markdown: {args.markdown}")
    if args.strict and (
        summary["approval_required"]
        or summary["budget_blocked"]
        or summary["selected_missing_requirements"]
        or summary["fallback_selected"]
    ):
        print("Provider decision strict check failed: approval, budget, or requirements need attention.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
