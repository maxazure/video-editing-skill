#!/usr/bin/env python3
"""Build provider-specific video generation prompts from storyboard shots.

The pack is deliberately local-only: it normalizes prompts, reference paths,
approval gates, and model-specific instructions, but never submits a paid
generation job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from generation_lessons import load_library, select_lessons, verify_library
from generation_chain_handoff import VERSION as GENERATION_CHAIN_HANDOFF_VERSION
from generation_chain_handoff import verify_plan as verify_generation_chain_handoff_plan
from generation_chain_handoff import verify_report as verify_generation_chain_handoff_report
from provider_capability import (
    load_bundle as load_capability_bundle,
    profile_index,
    profile_support_issues,
    verify_bundle as verify_capability_bundle,
)
from reference_story_formula import REPORT_VERSION as REFERENCE_STORY_FORMULA_VERSION
from reference_story_formula import verify_report as verify_reference_story_formula_report
from sequence_handoff import REPORT_VERSION as SEQUENCE_HANDOFF_VERSION
from sequence_handoff import verify_report as verify_sequence_handoff_report
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


def _explicit_reference(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {"expected_path": "", "resolved_path": ""}
    expected = Path(path).expanduser().resolve()
    return {
        "expected_path": str(expected),
        "resolved_path": str(expected) if expected.exists() else "",
    }


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
    style_reference: Optional[Mapping[str, str]] = None,
) -> str:
    if mode != "auto":
        return mode
    if provider == "remotion_hyperframes":
        return "motion_graphics"
    if provider == "media_library_broll":
        return "broll_search"
    if provider == "codex_imagegen":
        return "still_reference"
    if provider in GENERATED_VIDEO_PROVIDERS and (reference.get("resolved_path") or animate_stills):
        return "image_to_video"
    if provider in GENERATED_VIDEO_PROVIDERS and (style_reference or {}).get("resolved_path"):
        return "reference_to_video"
    if provider in GENERATED_VIDEO_PROVIDERS and route == "codex_imagegen":
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


def _sequence_handoff_index(
    report: Optional[Mapping[str, Any]],
    *,
    shot_ids: Sequence[str],
) -> Dict[str, Dict[str, Mapping[str, Any]]]:
    index: Dict[str, Dict[str, Mapping[str, Any]]] = {"incoming": {}, "outgoing": {}}
    if report is None:
        return index
    if report.get("version") != SEQUENCE_HANDOFF_VERSION:
        raise ValueError("sequence handoff must be a sequence_handoff.v1 report")
    if int((report.get("summary") or {}).get("blocking") or 0):
        raise ValueError("sequence handoff report is blocked")
    if not str(report.get("report_id") or "").startswith("sh_report_"):
        raise ValueError("sequence handoff report has no valid report_id")
    actual_pairs: List[tuple[str, str]] = []
    for position, raw in enumerate(report.get("boundaries") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"sequence handoff boundary #{position} is not an object")
        from_shot = str(raw.get("from_shot") or "").strip()
        to_shot = str(raw.get("to_shot") or "").strip()
        if not from_shot or not to_shot:
            raise ValueError(f"sequence handoff boundary #{position} has no shot pair")
        if from_shot in index["outgoing"]:
            raise ValueError(f"duplicate outgoing sequence handoff for {from_shot}")
        if to_shot in index["incoming"]:
            raise ValueError(f"duplicate incoming sequence handoff for {to_shot}")
        index["outgoing"][from_shot] = raw
        index["incoming"][to_shot] = raw
        actual_pairs.append((from_shot, to_shot))
    expected_pairs = list(zip(shot_ids, shot_ids[1:]))
    if actual_pairs != expected_pairs:
        raise ValueError(
            "sequence handoff boundaries do not match the current storyboard shot order: "
            f"expected {expected_pairs}, got {actual_pairs}"
        )
    return index


def _generation_chain_handoff_index(
    reports: Sequence[Mapping[str, Any]],
    *,
    shot_ids: Sequence[str],
) -> Dict[str, Mapping[str, Any]]:
    indexed: Dict[str, Mapping[str, Any]] = {}
    adjacent_pairs = set(zip(shot_ids, shot_ids[1:]))
    for position, report in enumerate(reports, start=1):
        if report.get("version") != GENERATION_CHAIN_HANDOFF_VERSION:
            raise ValueError(
                f"generation chain handoff #{position} must be a {GENERATION_CHAIN_HANDOFF_VERSION} report"
            )
        verification = verify_generation_chain_handoff_plan(report)
        if int((verification.get("summary") or {}).get("blocking") or 0):
            raise ValueError(
                f"generation chain handoff #{position} is blocked: "
                + "; ".join(verification.get("blockers") or [])
            )
        if str(report.get("status") or "") != "ready":
            raise ValueError(f"generation chain handoff #{position} is not ready")
        if not str(report.get("artifact_id") or "").startswith("gch_"):
            raise ValueError(f"generation chain handoff #{position} has no valid artifact_id")
        review = report.get("review") if isinstance(report.get("review"), Mapping) else {}
        if review.get("decision") != "use_exact_start_frame":
            raise ValueError(f"generation chain handoff #{position} is not approved for exact-frame use")
        boundary = report.get("boundary") if isinstance(report.get("boundary"), Mapping) else {}
        pair = (str(boundary.get("from_shot") or ""), str(boundary.get("to_shot") or ""))
        if pair not in adjacent_pairs:
            raise ValueError(
                f"generation chain handoff #{position} does not match the current storyboard order: {pair}"
            )
        target = pair[1]
        if target in indexed:
            raise ValueError(f"duplicate generation chain handoff for {target}")
        frame = report.get("handoff_frame") if isinstance(report.get("handoff_frame"), Mapping) else {}
        root = Path(str(report.get("project_root") or "")).expanduser()
        frame_path = root / str(frame.get("path") or "")
        if not frame.get("path") or not frame_path.is_file():
            raise ValueError(f"generation chain handoff #{position} has no live handoff frame")
        indexed[target] = report
    return indexed


def _chain_handoff_prompt(report: Mapping[str, Any]) -> str:
    boundary = report.get("boundary") or {}
    contract = report.get("prompt_contract") or {}
    return (
        "EXACT CHAIN START from {source}: use the supplied reviewed tail frame as the exact opening "
        "composition; continue its pose, object state, environment, lighting, and spatial relationships "
        "without resetting the scene. {identity} {motion} Receive-in: {receive} Match rule: {match}"
    ).format(
        source=boundary.get("from_shot", ""),
        identity=contract.get("identity_policy", ""),
        motion=contract.get("motion_budget", ""),
        receive=boundary.get("receive_in", ""),
        match=boundary.get("match_requirement", ""),
    )


def _story_formula_index(
    report: Optional[Mapping[str, Any]],
    *,
    shot_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    if report is None:
        return {}
    if report.get("version") != REFERENCE_STORY_FORMULA_VERSION:
        raise ValueError(f"reference story formula must be a {REFERENCE_STORY_FORMULA_VERSION} report")
    if int((report.get("summary") or {}).get("blocking") or 0):
        raise ValueError("reference story formula report is blocked")
    if not str(report.get("report_id") or "").startswith("rsf_"):
        raise ValueError("reference story formula report has no valid report_id")
    beats = {
        str(row.get("beat_id") or ""): dict(row)
        for row in (report.get("formula") or {}).get("beats") or []
        if isinstance(row, Mapping) and row.get("beat_id")
    }
    mappings = [
        dict(row)
        for row in (report.get("target") or {}).get("shot_mappings") or []
        if isinstance(row, Mapping)
    ]
    if [str(row.get("shot_id") or "") for row in mappings] != list(shot_ids):
        raise ValueError("reference story formula does not match the current storyboard shot order")
    indexed: Dict[str, Dict[str, Any]] = {}
    for mapping in mappings:
        shot_id = str(mapping.get("shot_id") or "")
        beat_id = str(mapping.get("beat_id") or "")
        beat = beats.get(beat_id)
        if beat is None:
            raise ValueError(f"reference story formula shot {shot_id} maps to unknown beat {beat_id}")
        indexed[shot_id] = {"mapping": mapping, "beat": beat}
    return indexed


def _story_formula_prompt(entry: Mapping[str, Any]) -> str:
    mapping = entry.get("mapping") or {}
    beat = entry.get("beat") or {}
    return (
        "REFERENCE STORY FORMULA — STRUCTURE ONLY: beat {beat_id} uses {mechanism}; move the viewer "
        "from '{before}' to '{after}' through {trigger}. Transferable rule: {rule} Target content "
        "anchor: {anchor}. Target viewer shift: {shift}. Surface change: {surface}. Visual action: "
        "{action}. Do not copy: {excluded}. Do not reuse reference pixels, audio, wording, branding, "
        "or specific plot events."
    ).format(
        beat_id=beat.get("beat_id", ""),
        mechanism=beat.get("mechanism", ""),
        before=beat.get("viewer_state_before", ""),
        after=beat.get("viewer_state_after", ""),
        trigger=beat.get("trigger", ""),
        rule=beat.get("transferable_rule", ""),
        anchor=mapping.get("content_anchor", ""),
        shift=mapping.get("viewer_shift", ""),
        surface=mapping.get("surface_change", ""),
        action=mapping.get("visual_action", ""),
        excluded=beat.get("do_not_copy", ""),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sequence_handoff_storyboard(
    report: Mapping[str, Any],
    *,
    storyboard_plan: str,
    project_dir: str,
) -> None:
    root = Path(project_dir).expanduser().resolve(strict=True)
    candidate = Path(storyboard_plan).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    storyboard = candidate.resolve(strict=True)
    try:
        relative_path = storyboard.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("storyboard plan must be inside the project when sequence handoff is used") from exc
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    record = inputs.get("storyboard") if isinstance(inputs.get("storyboard"), Mapping) else {}
    expected = {
        "path": relative_path,
        "size_bytes": storyboard.stat().st_size,
        "sha256": _sha256_file(storyboard),
    }
    if record != expected:
        raise ValueError("sequence handoff is bound to a different storyboard plan")


def _verify_story_formula_storyboard(
    report: Mapping[str, Any],
    *,
    storyboard_plan: str,
    project_dir: str,
) -> None:
    root = Path(project_dir).expanduser().resolve(strict=True)
    candidate = Path(storyboard_plan).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    storyboard = candidate.resolve(strict=True)
    try:
        relative_path = storyboard.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("storyboard plan must be inside the project when reference story formula is used") from exc
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    record = inputs.get("target_storyboard") if isinstance(inputs.get("target_storyboard"), Mapping) else {}
    expected = {
        "path": relative_path,
        "size_bytes": storyboard.stat().st_size,
        "sha256": _sha256_file(storyboard),
    }
    actual = {key: record.get(key) for key in expected}
    if actual != expected:
        raise ValueError("reference story formula is bound to a different storyboard plan")


def _handoff_prompt(shot_id: str, handoff: Mapping[str, Mapping[str, Any]]) -> str:
    clauses: List[str] = []
    incoming = handoff.get("incoming")
    if incoming:
        clauses.append(
            "RECEIVE IN from {source}: {receive} Edit boundary: {edit}; {match} "
            "Axis: {axis}; {axis_note} Screen direction: {direction}; {direction_note} "
            "Preserve at least {handle:g}s of usable head handle.".format(
                source=incoming.get("from_shot"),
                receive=incoming.get("receive_in"),
                edit=incoming.get("edit_type"),
                match=incoming.get("match_requirement"),
                axis=incoming.get("axis_decision"),
                axis_note=incoming.get("axis_note"),
                direction=incoming.get("screen_direction_decision"),
                direction_note=incoming.get("screen_direction_note"),
                handle=float(incoming.get("head_handle_seconds") or 0),
            )
        )
    outgoing = handoff.get("outgoing")
    if outgoing:
        clauses.append(
            "HANDOFF OUT to {target} via {carrier}: {offer} Planned edit: {edit}; {match} "
            "Audio bridge: {audio} Axis: {axis}; {axis_note} Screen direction: {direction}; {direction_note} "
            "Preserve at least {handle:g}s of usable tail handle.".format(
                target=outgoing.get("to_shot"),
                carrier=outgoing.get("carrier_type"),
                offer=outgoing.get("offer_from"),
                edit=outgoing.get("edit_type"),
                match=outgoing.get("match_requirement"),
                audio=outgoing.get("audio_bridge"),
                axis=outgoing.get("axis_decision"),
                axis_note=outgoing.get("axis_note"),
                direction=outgoing.get("screen_direction_decision"),
                direction_note=outgoing.get("screen_direction_note"),
                handle=float(outgoing.get("tail_handle_seconds") or 0),
            )
        )
    return f"SEQUENCE HANDOFF FOR {shot_id}: " + " ".join(clauses) if clauses else ""


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
    style_reference: Optional[str] = None,
    lesson_library: Optional[Mapping[str, Any]] = None,
    lesson_model: str = "",
    lesson_categories: Optional[Sequence[str]] = None,
    lesson_limit: int = 3,
    capability_bundles: Optional[Sequence[Mapping[str, Any]]] = None,
    require_capability_profile: bool = False,
    capability_max_age_days: int = 30,
    resolution: str = "",
    default_duration: float = 4.0,
    max_duration: float = 8.0,
    sequence_handoff_report: Optional[Mapping[str, Any]] = None,
    generation_chain_handoff_reports: Optional[Sequence[Mapping[str, Any]]] = None,
    story_formula_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if lesson_limit < 0 or lesson_limit > 10:
        raise ValueError("lesson_limit must be between 0 and 10")
    if capability_max_age_days < 0:
        raise ValueError("capability_max_age_days must be non-negative")
    characters = list(characters or [])
    brand_anchors = list(brand_anchors or [])
    capability_bundles = list(capability_bundles or [])
    generation_chain_handoff_reports = list(generation_chain_handoff_reports or [])
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    aspect = str(target.get("aspect") or "9:16")
    shared_style_reference = _explicit_reference(style_reference)
    raw_shots = plan.get("shots") or []
    shot_ids = [
        str(shot.get("id") or f"shot_{pos + 1:03d}")
        for pos, shot in enumerate(raw_shots)
        if isinstance(shot, Mapping)
    ]
    if sequence_handoff_report is not None and len(set(shot_ids)) != len(shot_ids):
        raise ValueError("storyboard shot ids must be unique when sequence handoff is used")
    sequence_handoffs = _sequence_handoff_index(sequence_handoff_report, shot_ids=shot_ids)
    generation_chain_handoffs = _generation_chain_handoff_index(
        generation_chain_handoff_reports,
        shot_ids=shot_ids,
    )
    story_formula = _story_formula_index(story_formula_report, shot_ids=shot_ids)
    lesson_library_id = ""
    if lesson_library is not None:
        lesson_verification = verify_library(lesson_library)
        if lesson_verification["summary"]["blocking"]:
            raise ValueError(
                "generation lesson library is invalid: "
                + "; ".join(lesson_verification["blockers"])
            )
        lesson_library_id = str(lesson_library.get("library_id") or "")

    capability_reports = [
        verify_capability_bundle(
            bundle,
            max_age_days=capability_max_age_days,
            require_fresh=True,
        )
        for bundle in capability_bundles
    ]
    capabilities_by_provider = profile_index(
        capability_bundles,
        max_age_days=capability_max_age_days,
        require_fresh=True,
    )
    bundle_capability_issues = sorted({
        issue
        for report in capability_reports
        for issue in report.get("blockers") or []
    })

    items: List[Dict[str, Any]] = []
    provider_counts: Dict[str, int] = {}
    approval_required = 0
    applied_lesson_ids = set()
    applied_lesson_count = 0
    item_capability_blocking = 0

    for pos, shot in enumerate(raw_shots):
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id") or f"shot_{pos + 1:03d}")
        route = _route(shot)
        selected_provider = _provider_for_shot(shot, provider, animate_stills=animate_stills)
        reference = _expected_reference(asset_root, shot_id)
        chain_handoff = generation_chain_handoffs.get(shot_id)
        formula_entry = story_formula.get(shot_id)
        if chain_handoff is not None:
            if selected_provider not in GENERATED_VIDEO_PROVIDERS:
                raise ValueError(
                    f"generation chain handoff target {shot_id} requires a generated-video provider"
                )
            if mode not in {"auto", "image_to_video"}:
                raise ValueError(
                    f"generation chain handoff target {shot_id} requires mode=auto or image_to_video"
                )
            frame = chain_handoff.get("handoff_frame") or {}
            frame_path = (
                Path(str(chain_handoff.get("project_root") or ""))
                / str(frame.get("path") or "")
            ).resolve()
            reference = {
                "expected_path": str(frame_path),
                "resolved_path": str(frame_path),
            }
        selected_mode = _mode_for_shot(
            mode=mode,
            provider=selected_provider,
            route=route,
            reference=reference,
            animate_stills=animate_stills,
            style_reference=shared_style_reference,
        )
        if chain_handoff is not None:
            selected_mode = "image_to_video"
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
        if (
            shared_style_reference["expected_path"]
            and selected_provider in GENERATED_VIDEO_PROVIDERS | {"codex_imagegen", "remotion_hyperframes"}
        ):
            prompt = (
                f"{prompt} STYLE LOCK: Match the shared style reference and the same palette, "
                "line/fill treatment, texture, lighting, and finish across every generated shot."
            )
        matched_lessons = (
            select_lessons(
                lesson_library,
                provider=selected_provider,
                model=lesson_model,
                categories=lesson_categories,
                limit=lesson_limit,
            )
            if lesson_library is not None and selected_provider in GENERATED_VIDEO_PROVIDERS
            else []
        )
        if matched_lessons:
            constraints = " ".join(
                f"[{(entry.get('scope') or {}).get('category', '')}] {entry.get('lesson', '')}"
                for entry in matched_lessons
            )
            prompt = f"{prompt} LEARNED CONSTRAINTS: {constraints}"
            applied_lesson_count += len(matched_lessons)
            applied_lesson_ids.update(str(entry.get("lesson_id") or "") for entry in matched_lessons)

        shot_handoff = {
            "incoming": sequence_handoffs["incoming"].get(shot_id),
            "outgoing": sequence_handoffs["outgoing"].get(shot_id),
        }
        handoff_instruction = _handoff_prompt(shot_id, shot_handoff)
        if handoff_instruction:
            prompt = f"{prompt} {handoff_instruction}"
        if chain_handoff is not None:
            prompt = f"{prompt} {_chain_handoff_prompt(chain_handoff)}"
        if formula_entry is not None:
            prompt = f"{prompt} {_story_formula_prompt(formula_entry)}"

        profile_entry = capabilities_by_provider.get(selected_provider)
        capability_issues: List[str] = []
        capability_profile: Dict[str, Any] = {}
        if selected_provider in GENERATED_VIDEO_PROVIDERS:
            if profile_entry is None:
                if require_capability_profile:
                    capability_issues.append("missing_capability_profile")
            else:
                verification = profile_entry.get("verification") or {}
                profile = profile_entry.get("profile") or {}
                capability_profile = {
                    "profile_id": verification.get("profile_id", ""),
                    "provider": verification.get("provider", ""),
                    "surface": verification.get("surface", ""),
                    "model": verification.get("model", ""),
                    "verified_at": verification.get("verified_at", ""),
                    "age_days": verification.get("age_days"),
                    "status": verification.get("status", ""),
                }
                if (verification.get("summary") or {}).get("blocking"):
                    capability_issues.append("invalid_or_stale_capability_profile")
                else:
                    image_references = int(
                        selected_mode in {"image_to_video", "first_last_frame"}
                        and bool(reference.get("expected_path"))
                    ) + int(
                        bool(shared_style_reference.get("expected_path"))
                    )
                    capability_issues.extend(
                        profile_support_issues(
                            profile,
                            provider=selected_provider,
                            mode=selected_mode,
                            aspect=aspect,
                            duration_seconds=duration,
                            resolution=resolution,
                            image_references=image_references,
                        )
                    )
        item_capability_blocking += len(set(capability_issues))
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
            "surface": capability_profile.get("surface", ""),
            "model": capability_profile.get("model", ""),
            "aspect": aspect,
            "resolution": resolution,
            "duration_seconds": duration,
            "reference": reference,
            "style_reference": dict(shared_style_reference),
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "generation_lessons": [
                {
                    "lesson_id": entry.get("lesson_id"),
                    "scope": dict(entry.get("scope") or {}),
                    "lesson": entry.get("lesson"),
                    "prompt_fix": entry.get("prompt_fix"),
                    "source": dict(entry.get("source") or {}),
                }
                for entry in matched_lessons
            ],
            "capability_profile": capability_profile,
            "capability_issues": sorted(set(capability_issues)),
            "continuity_anchors": continuity,
            "sequence_handoff": {
                key: dict(value) if isinstance(value, Mapping) else None
                for key, value in shot_handoff.items()
            },
            "generation_chain_handoff": (
                {
                    "artifact_id": chain_handoff.get("artifact_id"),
                    "boundary_id": (chain_handoff.get("boundary") or {}).get("boundary_id"),
                    "from_shot": (chain_handoff.get("boundary") or {}).get("from_shot"),
                    "to_shot": (chain_handoff.get("boundary") or {}).get("to_shot"),
                    "selected_frame_index": (chain_handoff.get("selected_frame") or {}).get("index"),
                    "selected_frame_pts_seconds": (chain_handoff.get("selected_frame") or {}).get("pts_seconds"),
                    "frame_sha256": (chain_handoff.get("handoff_frame") or {}).get("sha256"),
                    "reviewed_by": (chain_handoff.get("review") or {}).get("reviewed_by"),
                }
                if chain_handoff is not None
                else None
            ),
            "reference_story_formula": (
                {
                    "report_id": story_formula_report.get("report_id"),
                    "formula_name": (story_formula_report.get("formula") or {}).get("name"),
                    "beat_id": (formula_entry.get("beat") or {}).get("beat_id"),
                    "mechanism": (formula_entry.get("beat") or {}).get("mechanism"),
                    "content_anchor": (formula_entry.get("mapping") or {}).get("content_anchor"),
                    "viewer_shift": (formula_entry.get("mapping") or {}).get("viewer_shift"),
                }
                if formula_entry is not None and story_formula_report is not None
                else None
            ),
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
                "Shared style reference is attached unchanged to every generated shot when configured.",
                "Reviewed receive-in and handoff-out instructions are visible in the provider prompt when configured.",
                "A reviewed predecessor tail is the exact first frame and original identity/product/style anchors remain in force when chain handoff is configured.",
                "A reviewed reference formula transfers only abstract viewer-state structure and never reference pixels, audio, wording, branding, or specific plot events.",
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
            "style_reference": shared_style_reference,
            "lesson_library": {
                "library_id": lesson_library_id,
                "model": lesson_model or "*",
                "categories": list(lesson_categories or []),
                "limit_per_shot": lesson_limit,
            },
            "capability_policy": {
                "required": require_capability_profile,
                "max_age_days": capability_max_age_days,
                "resolution": resolution,
                "bundle_ids": [report.get("bundle_id", "") for report in capability_reports],
                "bundle_blockers": bundle_capability_issues,
            },
            "character_sheet_prompt": _character_sheet_prompt(characters, brand_anchors, aspect),
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "sequence_handoff": {
                "report_id": str((sequence_handoff_report or {}).get("report_id") or ""),
                "boundaries": len((sequence_handoff_report or {}).get("boundaries") or []),
            },
            "generation_chain_handoffs": [
                str(report.get("artifact_id") or "")
                for report in generation_chain_handoff_reports
            ],
            "reference_story_formula": {
                "report_id": str((story_formula_report or {}).get("report_id") or ""),
                "formula_name": str(((story_formula_report or {}).get("formula") or {}).get("name") or ""),
            },
        },
        "summary": {
            "items": len(items),
            "approval_required": approval_required,
            "blocking": approval_required + item_capability_blocking + len(bundle_capability_issues),
            "capability_blocking": item_capability_blocking + len(bundle_capability_issues),
            "capability_profiles": len(capabilities_by_provider),
            "style_reference_ready": int(bool(shared_style_reference["resolved_path"])),
            "generation_lessons_applied": applied_lesson_count,
            "unique_generation_lessons": len(applied_lesson_ids),
            "sequence_handoff_boundaries": len((sequence_handoff_report or {}).get("boundaries") or []),
            "generation_chain_handoffs": len(generation_chain_handoffs),
            "reference_story_formula_shots": len(story_formula),
            **{f"provider_{key}": value for key, value in sorted(provider_counts.items())},
        },
        "items": items,
        "next_steps": [
            "Review prompts and reference paths before submitting any generated-video job.",
            "Pass a live-verified sequence_handoff.v1 report so each adjacent shot receives and hands off an explicit edit baton.",
            "For genuinely continuous shots, pass a live-verified generation_chain_handoff.v1 report so the approved predecessor tail becomes the next exact first frame.",
            "When adapting a reference, pass a live-verified reference_story_formula.v1 report so prompts inherit the reviewed emotional mechanism without copying source expression.",
            "Verify the generation lesson library and review every learned constraint before reusing it.",
            "Verify dated provider capability profiles against the exact UI/API surface before selecting model settings.",
            "Use Codex image_gen first for still references and character sheets.",
            "Run reference_frame_preflight.py to verify first-frame and shared style-reference geometry.",
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


def verify_prompt_pack(
    pack: Mapping[str, Any],
    *,
    capability_bundles: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if pack.get("version") != "video_prompt_pack.v1":
        blockers.append("invalid_prompt_pack_version")

    capability_bundles = list(capability_bundles or [])
    policy = pack.get("global", {}).get("capability_policy") or {}
    max_age_days = int(policy.get("max_age_days") or 30)
    required = bool(policy.get("required"))
    reports = [
        verify_capability_bundle(bundle, max_age_days=max_age_days, require_fresh=True)
        for bundle in capability_bundles
    ]
    bundle_issues = sorted({
        issue
        for report in reports
        for issue in report.get("blockers") or []
    })
    blockers.extend(f"capability_bundle:{issue}" for issue in bundle_issues)
    warnings.extend(
        f"capability_bundle:{issue}"
        for report in reports
        for issue in report.get("warnings") or []
    )

    expected_bundle_ids = sorted(str(item) for item in policy.get("bundle_ids") or [])
    current_bundle_ids = sorted(str(report.get("bundle_id") or "") for report in reports)
    if expected_bundle_ids != current_bundle_ids:
        blockers.append("capability_bundle_ids_drift")

    capabilities_by_provider = profile_index(
        capability_bundles,
        max_age_days=max_age_days,
        require_fresh=True,
    )
    approval_blocking = 0
    item_capability_blocking = 0
    for position, item in enumerate(pack.get("items") or [], start=1):
        if not isinstance(item, Mapping):
            blockers.append(f"item_{position}_not_object")
            continue
        shot_id = str(item.get("shot_id") or position)
        if item.get("approval_required") and item.get("approval_status") != "approved":
            approval_blocking += 1

        provider = str(item.get("provider") or "")
        current_issues: List[str] = []
        current_profile_id = ""
        if provider in GENERATED_VIDEO_PROVIDERS:
            entry = capabilities_by_provider.get(provider)
            if entry is None:
                if required:
                    current_issues.append("missing_capability_profile")
            else:
                verification = entry.get("verification") or {}
                current_profile_id = str(verification.get("profile_id") or "")
                if (verification.get("summary") or {}).get("blocking"):
                    current_issues.append("invalid_or_stale_capability_profile")
                else:
                    reference = item.get("reference") or {}
                    style_reference = item.get("style_reference") or {}
                    image_references = int(
                        str(item.get("mode") or "") in {"image_to_video", "first_last_frame"}
                        and bool(reference.get("expected_path"))
                    ) + int(
                        bool(style_reference.get("expected_path"))
                    )
                    current_issues.extend(
                        profile_support_issues(
                            entry.get("profile") or {},
                            provider=provider,
                            mode=str(item.get("mode") or ""),
                            aspect=str(item.get("aspect") or ""),
                            duration_seconds=float(item.get("duration_seconds") or 0),
                            resolution=str(item.get("resolution") or ""),
                            image_references=image_references,
                        )
                    )

        current_issues = sorted(set(current_issues))
        item_capability_blocking += len(current_issues)
        stored_issues = sorted(str(issue) for issue in item.get("capability_issues") or [])
        if stored_issues != current_issues:
            blockers.append(f"capability_issues_drift:{shot_id}")
        stored_profile_id = str((item.get("capability_profile") or {}).get("profile_id") or "")
        if stored_profile_id != current_profile_id:
            blockers.append(f"capability_profile_id_drift:{shot_id}")

    expected_capability_blocking = len(bundle_issues) + item_capability_blocking
    stored_summary = pack.get("summary") or {}
    if int(stored_summary.get("capability_blocking") or 0) != expected_capability_blocking:
        blockers.append("capability_summary_drift")
    if int(stored_summary.get("blocking") or 0) != approval_blocking + expected_capability_blocking:
        blockers.append("blocking_summary_drift")
    if approval_blocking:
        blockers.append(f"approval_pending:{approval_blocking}")
    if expected_capability_blocking:
        blockers.append(f"capability_blockers:{expected_capability_blocking}")

    return {
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "summary": {
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
            "approval_blocking": approval_blocking,
            "capability_blocking": expected_capability_blocking,
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def emit_markdown(pack: Mapping[str, Any]) -> str:
    style_reference = pack.get("global", {}).get("style_reference") or {}
    style_reference_path = (
        style_reference.get("resolved_path")
        or style_reference.get("expected_path")
        or "-"
    )
    lines = [
        "# Video Prompt Pack",
        "",
        str(pack.get("routing_note") or ROUTING_SENTENCE),
        "",
        f"- Items: {pack.get('summary', {}).get('items', 0)}",
        f"- Approval required: {pack.get('summary', {}).get('approval_required', 0)}",
        f"- Blocking: {pack.get('summary', {}).get('blocking', 0)}",
        f"- Learned constraints applied: {pack.get('summary', {}).get('generation_lessons_applied', 0)}",
        f"- Capability blockers: {pack.get('summary', {}).get('capability_blocking', 0)}",
        f"- Shared style reference: `{style_reference_path}`",
        f"- Sequence handoff report: `{pack.get('global', {}).get('sequence_handoff', {}).get('report_id') or '-'}`",
        f"- Generation chain handoffs: {pack.get('summary', {}).get('generation_chain_handoffs', 0)}",
        f"- Reference story formula: `{pack.get('global', {}).get('reference_story_formula', {}).get('report_id') or '-'}`",
        "",
        "## Character / Style Reference",
        "",
        "```text",
        str(pack.get("global", {}).get("character_sheet_prompt") or ""),
        "```",
        "",
        "| shot | provider | surface/model | mode | resolution | approval | capability | reference |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in pack.get("items") or []:
        reference = item.get("reference") or {}
        ref = reference.get("resolved_path") or reference.get("expected_path") or "-"
        lines.append(
            "| {shot} | {provider} | {surface} / {model} | {mode} | {resolution} | {approval} | {capability} | `{ref}` |".format(
                shot=item.get("shot_id", ""),
                provider=item.get("provider", ""),
                surface=item.get("surface") or "-",
                model=item.get("model") or "-",
                mode=item.get("mode", ""),
                resolution=item.get("resolution") or "-",
                approval=item.get("approval_status", ""),
                capability="ready" if not item.get("capability_issues") else ", ".join(item.get("capability_issues") or []),
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
        if item.get("generation_lessons"):
            lines.extend([
                "**Approved generation lessons**",
                "",
                *[
                    f"- `{str(entry.get('lesson_id') or '')[:12]}` [{(entry.get('scope') or {}).get('category', '')}] {entry.get('lesson', '')}"
                    for entry in item.get("generation_lessons") or []
                ],
                "",
            ])
        if item.get("capability_issues"):
            lines.extend([
                "**Capability blockers**",
                "",
                *[f"- `{issue}`" for issue in item.get("capability_issues") or []],
                "",
            ])
        if item.get("generation_chain_handoff"):
            chain = item.get("generation_chain_handoff") or {}
            lines.extend([
                "**Generation chain handoff**",
                "",
                f"- `{chain.get('from_shot', '')}` → `{chain.get('to_shot', '')}` via `{chain.get('boundary_id', '')}`",
                f"- Artifact: `{chain.get('artifact_id', '')}`",
                f"- Approved source frame: #{chain.get('selected_frame_index', '')} at {chain.get('selected_frame_pts_seconds', '')}s",
                "",
            ])
        if item.get("reference_story_formula"):
            formula = item.get("reference_story_formula") or {}
            lines.extend([
                "**Reference story formula**",
                "",
                f"- Formula: {formula.get('formula_name', '')}",
                f"- Beat: `{formula.get('beat_id', '')}` / `{formula.get('mechanism', '')}`",
                f"- Content anchor: {formula.get('content_anchor', '')}",
                f"- Viewer shift: {formula.get('viewer_shift', '')}",
                "",
            ])
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
    parser.add_argument("--project-dir", default=".", help="Project root used to live-verify sequence handoff inputs.")
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
        choices=[
            "auto",
            "text_to_video",
            "image_to_video",
            "first_last_frame",
            "reference_to_video",
            "video_edit",
            "video_extension",
            "clip_stitching",
            "still_reference",
            "motion_graphics",
            "broll_search",
        ],
        help="Generation mode override.",
    )
    parser.add_argument("--asset-root", default="work", help="Root containing imagegen/generated_video assets.")
    parser.add_argument("--character", action="append", default=[], help="Reusable character identity/style note; can repeat.")
    parser.add_argument("--brand-anchor", action="append", default=[], help="Reusable visual-system anchor; can repeat.")
    parser.add_argument(
        "--style-reference",
        help="Shared local style-key image attached unchanged to every generated shot.",
    )
    parser.add_argument(
        "--sequence-handoff",
        help="Reviewed sequence_handoff.v1 report; live-verified before its boundary instructions enter prompts.",
    )
    parser.add_argument(
        "--generation-chain-handoff",
        action="append",
        default=[],
        help="Reviewed generation_chain_handoff.v1 report; repeat for each sequential boundary to inject an exact next-shot first frame.",
    )
    parser.add_argument(
        "--reference-story-formula",
        help="Reviewed reference_story_formula.v1 report; live-verified and injected as structure-only guidance per shot.",
    )
    parser.add_argument("--lesson-library", help="Approved generation_lessons.json to apply to generated-video prompts.")
    parser.add_argument("--lesson-model", default="", help="Exact model scope; omitted applies provider-wide lessons only.")
    parser.add_argument("--lesson-category", action="append", default=[], help="Lesson category filter; can repeat.")
    parser.add_argument("--lesson-limit", type=int, default=3, help="Maximum approved lessons per generated shot (0-10).")
    parser.add_argument(
        "--capability-profile",
        action="append",
        default=[],
        help="Dated provider_capabilities.json bundle; can repeat.",
    )
    parser.add_argument(
        "--require-capability-profile",
        action="store_true",
        help="Block every generated-video item without a matching verified provider profile.",
    )
    parser.add_argument(
        "--capability-max-age-days",
        type=int,
        default=30,
        help="Maximum accepted capability-profile age.",
    )
    parser.add_argument("--resolution", default="", help="Requested provider output resolution, e.g. 720p.")
    parser.add_argument("--animate-stills", action="store_true", help="Turn codex_imagegen still routes into image-to-video prompts.")
    parser.add_argument("--approved", action="store_true", help="Mark generated-video provider credit use as already approved.")
    parser.add_argument("--default-duration", type=float, default=4.0, help="Fallback clip duration when a shot has no duration.")
    parser.add_argument("--max-duration", type=float, default=8.0, help="Clamp provider prompt duration to this many seconds.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when generated-video approvals are still pending.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = load_plan(args.storyboard_plan)
    lesson_library = load_library(args.lesson_library) if args.lesson_library else None
    capability_bundles = [load_capability_bundle(path) for path in args.capability_profile]
    sequence_handoff_report = None
    if args.sequence_handoff:
        sequence_handoff_report = verify_sequence_handoff_report(
            args.sequence_handoff,
            project_dir=args.project_dir,
        )
        _verify_sequence_handoff_storyboard(
            sequence_handoff_report,
            storyboard_plan=args.storyboard_plan,
            project_dir=args.project_dir,
        )
    generation_chain_handoff_reports = []
    project_root = Path(args.project_dir).expanduser().resolve(strict=True)
    for raw_path in args.generation_chain_handoff:
        verification = verify_generation_chain_handoff_report(
            raw_path,
            project_dir=str(project_root),
        )
        if int((verification.get("summary") or {}).get("blocking") or 0):
            raise ValueError(
                "generation chain handoff is blocked: "
                + "; ".join(verification.get("blockers") or [])
            )
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        generation_chain_handoff_reports.append(load_plan(str(candidate.resolve(strict=True))))
    story_formula_report = None
    if args.reference_story_formula:
        verification = verify_reference_story_formula_report(
            args.reference_story_formula,
            project_dir=str(project_root),
        )
        if int((verification.get("summary") or {}).get("blocking") or 0):
            raise ValueError(
                "reference story formula is blocked: "
                + "; ".join(verification.get("blockers") or [])
            )
        candidate = Path(args.reference_story_formula).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        story_formula_report = load_plan(str(candidate.resolve(strict=True)))
        _verify_story_formula_storyboard(
            story_formula_report,
            storyboard_plan=args.storyboard_plan,
            project_dir=str(project_root),
        )
    pack = build_video_prompt_pack(
        plan,
        provider=args.provider,
        mode=args.mode,
        asset_root=args.asset_root,
        characters=args.character,
        brand_anchors=args.brand_anchor,
        approved=args.approved,
        animate_stills=args.animate_stills,
        style_reference=args.style_reference,
        lesson_library=lesson_library,
        lesson_model=args.lesson_model,
        lesson_categories=args.lesson_category,
        lesson_limit=args.lesson_limit,
        capability_bundles=capability_bundles,
        require_capability_profile=args.require_capability_profile,
        capability_max_age_days=args.capability_max_age_days,
        resolution=args.resolution,
        default_duration=args.default_duration,
        max_duration=args.max_duration,
        sequence_handoff_report=sequence_handoff_report,
        generation_chain_handoff_reports=generation_chain_handoff_reports,
        story_formula_report=story_formula_report,
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
        print("Video prompt pack strict check failed: approval or capability blockers remain.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
