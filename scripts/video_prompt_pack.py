#!/usr/bin/env python3
"""Build provider-specific video generation prompts from storyboard shots.

The pack is deliberately local-only: it normalizes prompts, reference paths,
approval gates, and model-specific instructions, but never submits a paid
generation job.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from storyboard_plan import ROUTING_SENTENCE


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

GENERATED_VIDEO_PROVIDERS = {"dreamina_seedance", "veo", "ltx", "wan", "sora"}

PROVIDER_LABELS: Mapping[str, str] = {
    "dreamina_seedance": "Dreamina/即梦 Seedance image/video generation",
    "veo": "Google Veo video generation",
    "ltx": "LTX video generation",
    "wan": "Wan video generation",
    "sora": "Sora video generation",
    "codex_imagegen": "Codex image_gen still reference",
    "remotion_hyperframes": "Local Remotion/HyperFrames motion graphics",
    "media_library_broll": "Local media-library B-roll search",
}

ROUTE_PROVIDER: Mapping[str, str] = {
    "dreamina_video": "dreamina_seedance",
    "codex_imagegen": "codex_imagegen",
    "remotion_hyperframes": "remotion_hyperframes",
    "media_library_broll": "media_library_broll",
}

DEFAULT_NEGATIVE_PROMPT = (
    "no subtitles, no hard-coded Chinese text, no watermark, no logo, no UI chrome, "
    "no distorted hands, no flicker, no jump cuts inside the generated clip"
)


def load_plan(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("storyboard plan must be a JSON object")
    return data


def _round2(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _clamp_duration(value: Any, *, default: float, max_duration: float) -> float:
    duration = _round2(value)
    if duration <= 0:
        duration = default
    return round(min(max(duration, 2.0), max_duration), 2)


def _route(shot: Mapping[str, Any]) -> str:
    generation_route = shot.get("generation_route")
    if isinstance(generation_route, Mapping):
        return str(generation_route.get("primary") or "media_library_broll")
    return "media_library_broll"


def _prompt_value(shot: Mapping[str, Any], keys: Sequence[str]) -> str:
    prompts = shot.get("prompts")
    if not isinstance(prompts, Mapping):
        return ""
    for key in keys:
        value = str(prompts.get(key) or "").strip()
        if value:
            return value
    return ""


def _expected_reference(asset_root: Optional[str], shot_id: str) -> Dict[str, str]:
    if not asset_root:
        return {"expected_path": "", "resolved_path": ""}
    root = Path(asset_root).expanduser().resolve()
    expected = root / "imagegen" / f"{shot_id}.png"
    resolved = ""
    for ext in IMAGE_EXTS:
        candidate = expected.with_suffix(ext)
        if candidate.exists():
            resolved = str(candidate)
            break
    return {"expected_path": str(expected), "resolved_path": resolved}


def _provider_for_shot(shot: Mapping[str, Any], provider: str, *, animate_stills: bool) -> str:
    if provider != "auto":
        return provider
    route = _route(shot)
    if route == "codex_imagegen" and animate_stills:
        return "dreamina_seedance"
    return ROUTE_PROVIDER.get(route, "media_library_broll")


def _mode_for_shot(
    *,
    mode: str,
    provider: str,
    route: str,
    reference: Mapping[str, str],
    animate_stills: bool,
) -> str:
    if mode != "auto":
        return mode
    if provider == "remotion_hyperframes":
        return "motion_graphics"
    if provider == "media_library_broll":
        return "broll_search"
    if provider == "codex_imagegen":
        return "still_reference"
    if provider in GENERATED_VIDEO_PROVIDERS and (reference.get("resolved_path") or route == "codex_imagegen" or animate_stills):
        return "image_to_video"
    return "text_to_video"


def _shot_subject(shot: Mapping[str, Any]) -> str:
    keywords = [str(item).strip() for item in (shot.get("keywords") or []) if str(item).strip()]
    if keywords:
        return ", ".join(keywords[:5])
    narration = str(shot.get("narration") or "").strip()
    return narration[:80] if narration else str(shot.get("id") or "shot")


def _continuity_text(shot: Mapping[str, Any], brand_anchors: Sequence[str]) -> str:
    continuity = shot.get("continuity")
    anchors: List[str] = []
    if isinstance(continuity, Mapping):
        anchors.extend(str(item) for item in (continuity.get("anchors") or []) if str(item).strip())
    anchors.extend(str(item).strip() for item in brand_anchors if str(item).strip())
    return "; ".join(dict.fromkeys(anchors))


def _visual_field(shot: Mapping[str, Any], key: str, default: str) -> str:
    visual = shot.get("visual")
    if isinstance(visual, Mapping):
        return str(visual.get(key) or default)
    return default


def _provider_prompt(
    *,
    provider: str,
    mode: str,
    shot: Mapping[str, Any],
    aspect: str,
    duration: float,
    continuity: str,
    characters: Sequence[str],
) -> str:
    route_prompt = _prompt_value(
        shot,
        ("video_prompt_en", "image_prompt_en", "motion_graphics_brief", "fallback_image_prompt_en", "broll_query"),
    )
    subject = _shot_subject(shot)
    narration = str(shot.get("narration") or "").strip()
    first = _visual_field(shot, "first_frame", f"Open on {subject}.")
    motion = _visual_field(shot, "motion", "gentle camera movement")
    last = _visual_field(shot, "last_frame", "Hold a stable final frame for editing.")
    character_text = "; ".join(characters) if characters else "generic non-identifying people only if needed"

    if provider == "dreamina_seedance":
        return (
            f"{aspect} {mode.replace('_', '-')} short-video clip, {duration:g}s. "
            f"Subject: {subject}. First frame: {first}. Motion: {motion}. Last frame: {last}. "
            f"Continuity: {continuity or 'match the previous storyboard shot'}. "
            f"Characters: {character_text}. Visual brief: {route_prompt or narration}. "
            "Natural motion, clean composition, subtitle-safe lower third."
        )
    if provider == "veo":
        return (
            f"Create a {duration:g}s {aspect} cinematic social clip. "
            f"Subject/action: {subject}; {narration}. Camera: {motion}. "
            f"Opening frame: {first}. Ending frame: {last}. "
            f"Style and continuity: {continuity}. Keep the frame free of readable text."
        )
    if provider == "ltx":
        return (
            f"{duration:g}s {aspect} clip, one clear action only: {subject}. "
            f"{route_prompt or narration}. Camera movement: {motion}. "
            f"Start: {first}. End: {last}. Consistency anchors: {continuity}. No text in frame."
        )
    if provider == "wan":
        return (
            f"Reference-consistent {aspect} video, {duration:g}s. "
            f"Use stable identity and scene anchors: {continuity}. "
            f"Main beat: {route_prompt or narration}. Motion strength: medium, camera: {motion}. "
            f"Begin with {first}; finish with {last}."
        )
    if provider == "sora":
        return (
            f"Generate a {duration:g}s {aspect} vertical short-form shot for this narration beat: {narration}. "
            f"Show {subject} with {motion}. Start frame: {first}. End frame: {last}. "
            f"Maintain continuity: {continuity}. Avoid generated captions or brand marks."
        )
    if provider == "remotion_hyperframes":
        return route_prompt or (
            f"Build a deterministic {aspect} motion-graphics card for {subject}; "
            "subtitle-safe lower third, readable type, one focal idea."
        )
    if provider == "codex_imagegen":
        return route_prompt or (
            f"{subject}. {aspect} short-form still reference, clean composition, subtitle-safe lower third."
        )
    return route_prompt or subject


def _character_sheet_prompt(characters: Sequence[str], brand_anchors: Sequence[str], aspect: str) -> str:
    character_text = "; ".join(characters) if characters else "No fixed character supplied; use only generic non-identifying people if needed."
    brand_text = "; ".join(brand_anchors) if brand_anchors else "Use the storyboard continuity anchors as the visual system."
    return (
        f"Create a reusable {aspect} character and style reference sheet for image-to-video handoff. "
        f"Characters: {character_text}. Brand/style anchors: {brand_text}. "
        "Include neutral front, 3/4, side, expression variations, wardrobe/prop notes, and clean background. "
        "Avoid embedded subtitles or platform UI. "
        f"{ROUTING_SENTENCE}"
    )


def build_video_prompt_pack(
    plan: Mapping[str, Any],
    *,
    provider: str = "auto",
    mode: str = "auto",
    asset_root: Optional[str] = None,
    characters: Optional[Sequence[str]] = None,
    brand_anchors: Optional[Sequence[str]] = None,
    approved: bool = False,
    animate_stills: bool = False,
    default_duration: float = 4.0,
    max_duration: float = 8.0,
) -> Dict[str, Any]:
    characters = list(characters or [])
    brand_anchors = list(brand_anchors or [])
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    aspect = str(target.get("aspect") or "9:16")

    items: List[Dict[str, Any]] = []
    provider_counts: Dict[str, int] = {}
    approval_required = 0

    for pos, shot in enumerate(plan.get("shots") or []):
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id") or f"shot_{pos + 1:03d}")
        route = _route(shot)
        selected_provider = _provider_for_shot(shot, provider, animate_stills=animate_stills)
        reference = _expected_reference(asset_root, shot_id)
        selected_mode = _mode_for_shot(
            mode=mode,
            provider=selected_provider,
            route=route,
            reference=reference,
            animate_stills=animate_stills,
        )
        duration = _clamp_duration(shot.get("duration"), default=default_duration, max_duration=max_duration)
        continuity = _continuity_text(shot, brand_anchors)
        requires_approval = selected_provider in GENERATED_VIDEO_PROVIDERS
        if requires_approval and not approved:
            approval_required += 1
        provider_counts[selected_provider] = provider_counts.get(selected_provider, 0) + 1

        prompt = _provider_prompt(
            provider=selected_provider,
            mode=selected_mode,
            shot=shot,
            aspect=aspect,
            duration=duration,
            continuity=continuity,
            characters=characters,
        )
        items.append({
            "shot_id": shot_id,
            "section": shot.get("section"),
            "time": {
                "start": shot.get("start"),
                "end": shot.get("end"),
                "duration": shot.get("duration"),
            },
            "source_route": route,
            "provider": selected_provider,
            "provider_label": PROVIDER_LABELS.get(selected_provider, selected_provider),
            "mode": selected_mode,
            "aspect": aspect,
            "duration_seconds": duration,
            "reference": reference,
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "continuity_anchors": continuity,
            "approval_required": requires_approval,
            "approval_status": "approved" if (requires_approval and approved) else ("needs_approval" if requires_approval else "not_required"),
            "approval_note": (
                "Video generation may consume provider credits; confirm before submitting and keep batches small."
                if requires_approval and not approved else ""
            ),
            "submit_hint": _submit_hint(selected_provider),
            "review_checks": [
                "Generated clip matches the narration beat.",
                "No hard-coded subtitles, watermark, or platform UI appear in frame.",
                "First and last frames are stable enough for editing.",
                "Subject, palette, and framing stay consistent with adjacent shots.",
            ],
        })

    return {
        "version": "video_prompt_pack.v1",
        "routing_note": ROUTING_SENTENCE,
        "source": {
            "storyboard_version": plan.get("version"),
            "shots": len(plan.get("shots") or []),
            "target": dict(target),
        },
        "global": {
            "aspect": aspect,
            "provider": provider,
            "mode": mode,
            "animate_stills": animate_stills,
            "characters": characters,
            "brand_anchors": brand_anchors,
            "character_sheet_prompt": _character_sheet_prompt(characters, brand_anchors, aspect),
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
        },
        "summary": {
            "items": len(items),
            "approval_required": approval_required,
            "blocking": approval_required,
            **{f"provider_{key}": value for key, value in sorted(provider_counts.items())},
        },
        "items": items,
        "next_steps": [
            "Review prompts and reference paths before submitting any generated-video job.",
            "Use Codex image_gen first for still references and character sheets.",
            "Confirm provider credits before running Dreamina/即梦, Veo, LTX, Wan, or Sora jobs.",
            "Save generated clips under work/generated_video/<shot_id>.mp4 and rerun storyboard_assets.py.",
            "Run render_qa.py and timeline_view.py after final render.",
        ],
    }


def _submit_hint(provider: str) -> str:
    if provider == "dreamina_seedance":
        return "After approval, use the local dreamina CLI/skill; save submit_id and downloaded output."
    if provider in GENERATED_VIDEO_PROVIDERS:
        return f"After approval, submit this prompt to {provider}; save the provider job id and output path."
    if provider == "codex_imagegen":
        return "Use Codex built-in image_gen for the still reference, then save it under work/imagegen/."
    if provider == "remotion_hyperframes":
        return "Render this deterministic motion card locally before final assembly."
    return "Search/link a local B-roll candidate before final assembly."


def emit_markdown(pack: Mapping[str, Any]) -> str:
    lines = [
        "# Video Prompt Pack",
        "",
        str(pack.get("routing_note") or ROUTING_SENTENCE),
        "",
        f"- Items: {pack.get('summary', {}).get('items', 0)}",
        f"- Approval required: {pack.get('summary', {}).get('approval_required', 0)}",
        f"- Blocking: {pack.get('summary', {}).get('blocking', 0)}",
        "",
        "## Character / Style Reference",
        "",
        "```text",
        str(pack.get("global", {}).get("character_sheet_prompt") or ""),
        "```",
        "",
        "| shot | provider | mode | approval | reference |",
        "|---|---|---|---|---|",
    ]
    for item in pack.get("items") or []:
        reference = item.get("reference") or {}
        ref = reference.get("resolved_path") or reference.get("expected_path") or "-"
        lines.append(
            "| {shot} | {provider} | {mode} | {approval} | `{ref}` |".format(
                shot=item.get("shot_id", ""),
                provider=item.get("provider", ""),
                mode=item.get("mode", ""),
                approval=item.get("approval_status", ""),
                ref=ref,
            )
        )

    for item in pack.get("items") or []:
        lines.extend([
            "",
            f"## {item.get('shot_id', '')} · {item.get('provider', '')} · {item.get('mode', '')}",
            "",
        ])
        if item.get("approval_note"):
            lines.extend([f"> {item['approval_note']}", ""])
        lines.extend([
            "**Prompt**",
            "",
            "```text",
            str(item.get("prompt") or ""),
            "```",
            "",
            "**Negative Prompt**",
            "",
            "```text",
            str(item.get("negative_prompt") or ""),
            "```",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build provider-specific video generation prompts from storyboard_plan JSON."
    )
    parser.add_argument("--storyboard-plan", required=True, help="Input storyboard_plan.json.")
    parser.add_argument("--output", required=True, help="Output prompt-pack JSON.")
    parser.add_argument("--markdown", help="Optional Markdown review file.")
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "dreamina_seedance", "veo", "ltx", "wan", "sora", "codex_imagegen", "remotion_hyperframes", "media_library_broll"],
        help="Provider override. auto keeps storyboard route intent.",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "text_to_video", "image_to_video", "still_reference", "motion_graphics", "broll_search"],
        help="Generation mode override.",
    )
    parser.add_argument("--asset-root", default="work", help="Root containing imagegen/generated_video assets.")
    parser.add_argument("--character", action="append", default=[], help="Reusable character identity/style note; can repeat.")
    parser.add_argument("--brand-anchor", action="append", default=[], help="Reusable visual-system anchor; can repeat.")
    parser.add_argument("--animate-stills", action="store_true", help="Turn codex_imagegen still routes into image-to-video prompts.")
    parser.add_argument("--approved", action="store_true", help="Mark generated-video provider credit use as already approved.")
    parser.add_argument("--default-duration", type=float, default=4.0, help="Fallback clip duration when a shot has no duration.")
    parser.add_argument("--max-duration", type=float, default=8.0, help="Clamp provider prompt duration to this many seconds.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when generated-video approvals are still pending.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = load_plan(args.storyboard_plan)
    pack = build_video_prompt_pack(
        plan,
        provider=args.provider,
        mode=args.mode,
        asset_root=args.asset_root,
        characters=args.character,
        brand_anchors=args.brand_anchor,
        approved=args.approved,
        animate_stills=args.animate_stills,
        default_duration=args.default_duration,
        max_duration=args.max_duration,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(pack))

    summary = pack["summary"]
    print(
        "Wrote video prompt pack: "
        f"{args.output}; items={summary['items']} approval_required={summary['approval_required']}"
    )
    if args.markdown:
        print(f"Wrote video prompt markdown: {args.markdown}")
    if args.strict and summary["blocking"]:
        print("Video prompt pack strict check failed: generated-video approvals are pending.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
