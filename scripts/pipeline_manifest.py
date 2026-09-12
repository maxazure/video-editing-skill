#!/usr/bin/env python3
"""Build a lightweight run manifest for a video production folder.

This is intentionally local-first: it scans known artifact names from this
skill, summarizes readiness, and exits non-zero in strict mode when publish or
render gates are not satisfied. It does not enqueue work or call providers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


STATUS_ORDER = {"missing": 0, "ready": 1, "warn": 2, "blocked": 3}
DEFAULT_EXCLUDES = {".git", ".venv", "node_modules", "research-archive", "__pycache__"}


@dataclass(frozen=True)
class ArtifactDef:
    category: str
    label: str
    patterns: Sequence[str]
    next_action: str
    blocks_when_present: bool = False


@dataclass(frozen=True)
class ArtifactRecord:
    category: str
    path: str
    size_bytes: int
    modified_at: str
    sha256: Optional[str] = None


ARTIFACTS: Sequence[ArtifactDef] = (
    ArtifactDef(
        "source_inventory",
        "Source Inventory",
        ("**/source_inventory.json", "**/*_source_inventory.json"),
        "Run project_bootstrap.py to create origin/, work/, output/, verify/, source inventory, and project memory.",
    ),
    ArtifactDef(
        "runtime_preflight",
        "Runtime Preflight",
        ("**/runtime_preflight.json", "**/*_runtime_preflight.json"),
        "Run runtime_preflight.py analyze for the selected workflow profiles; install or change any missing/unknown component, then live-verify the report.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "frame_rate_conform_plan",
        "Frame-rate Conform Plan",
        ("**/frame_rate_conform_plan.json", "**/*_frame_rate_conform_plan.json"),
        "Run frame_rate_conform.py apply/verify, then use the validated CFR working copy for every downstream edit.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "clip_assembly_plan",
        "Clip Assembly Plan",
        ("**/clip_assembly_plan.json", "**/*_clip_assembly_plan.json"),
        "Run clip_assembly.py apply, watch the complete delivery and every boundary proof at 1x, confirm the review, then live-verify the plan.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "loop_fill_plan",
        "Loop Fill Plan",
        ("**/loop_fill_plan.json", "**/*_loop_fill_plan.json"),
        "Run loop_fill.py apply, review the exact first-seam proof and complete delivery at 1x, confirm every continuity check, then live-verify the plan.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "interlace_conform_plan",
        "Interlace Conform Plan",
        ("**/interlace_conform_plan.json", "**/*_interlace_conform_plan.json"),
        "Run interlace_conform.py apply, review the full-length A/B at 1x, confirm every field/motion check, then live-verify the plan.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "edit_brief_plan",
        "Edit Brief Plan",
        ("**/edit_brief_plan.json", "**/*_edit_brief_plan.json"),
        "Run edit_brief_plan.py to turn the natural-language edit request into an ordered local runbook.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "production_authorization",
        "Production Authorization",
        ("**/production_authorization.json", "**/*_production_authorization.json"),
        "Run production_authorization.py prepare/audit, then live-verify the exact assets, actions, providers, and rights basis before acting.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "transcript",
        "Transcript",
        ("**/transcript.json", "**/*_transcript.json"),
        "Run transcribe.py and save work/transcript.json.",
    ),
    ArtifactDef(
        "semantic_transcript_review",
        "Semantic Transcript Review",
        ("**/transcript_semantic_review.json", "**/*_transcript_semantic_review.json"),
        "Run semantic_transcript_review.py audit, resolve every proposal with a source-bound human choices file, then apply it.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "takes_pack",
        "Takes Pack",
        ("**/takes_pack.json", "**/*_takes_pack.json", "**/takes_packed.md", "**/*_takes_packed.md"),
        "Run takes_pack.py when multiple transcripts need a compact phrase-level review view.",
    ),
    ArtifactDef(
        "script_alignment",
        "Target Script Alignment",
        ("**/script_alignment.json", "**/*_script_alignment.json"),
        "Run script_alignment.py, review low-score or ambiguous matches, and record candidate choices before rendering.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "clean_script",
        "Clean Script",
        ("**/clean_script.md", "**/*_clean_script.md"),
        "Run rewrite_script.py or save a reviewed clean_script.md.",
    ),
    ArtifactDef(
        "hook_variants",
        "Hook Variants",
        ("**/hook_variants.json", "**/*_hook_variants.json"),
        "Run hook_variants.py after transcription when the opening 3 seconds need multiple testable angles.",
    ),
    ArtifactDef(
        "cover_variants",
        "Cover Variants",
        ("**/cover_variants.json", "**/*_cover_variants.json"),
        "Run cover_variants.py, review feed-size previews, and record the selected publish cover.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitle_style_preview",
        "Subtitle Style Preview",
        ("**/subtitle_style_preview.json", "**/*_subtitle_style_preview.json"),
        "Run subtitle_style_preview.py verify; review real-frame JPEG variants and record the selected final-render style.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitle_glyph_qa",
        "Subtitle Glyph QA",
        ("**/subtitle_glyph_qa.json", "**/*_subtitle_glyph_qa.json"),
        "Run subtitle_glyph_qa.py verify; add an explicit font or fallback for every missing subtitle character before rendering.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitle_render_review",
        "Subtitle Render Review",
        ("**/subtitle_render_review.json", "**/*_subtitle_render_review.json"),
        "Run subtitle_render_review.py verify; review the exact final video at 1x and fix missing, stale, unreadable, clipped, or obscured rendered captions.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "framing_preview",
        "Platform Framing Preview",
        ("**/framing_preview.json", "**/*_framing_preview.json"),
        "Run framing_preview.py verify; review cover/contain/blur variants and select one treatment per platform before export.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "rough_cut",
        "Rough / Jump Cut",
        ("**/rough_cut.json", "**/jump_cut.json", "**/*_cut_list.json"),
        "Review the rough/jump cut plan and resolve any removal-budget blocker before rendering.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "multimodal_dead_air_plan",
        "Multimodal Dead-Air Plan",
        ("**/multimodal_dead_air_plan.json", "**/*_multimodal_dead_air_plan.json"),
        "Run multimodal_dead_air.py verify, review every proposed source cut, then apply and watch the full output at 1x.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "scene_boundaries",
        "Scene Boundaries",
        ("**/scene_boundaries.json", "**/*_scene_boundaries.json"),
        "Run scene_boundaries.py when highlight candidates should snap to visual cuts.",
    ),
    ArtifactDef(
        "visual_dedupe",
        "Visual Dedupe",
        ("**/visual_dedupe.json", "**/*_visual_dedupe.json"),
        "Run visual_dedupe.py, compare every duplicate group, and exclude repeats only from the downstream edit plan.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "video_understanding",
        "Video Understanding",
        ("**/video_understanding.json", "**/*_video_understanding.json"),
        "Run video_understanding.py when visual objects, tracks, crops, or privacy boxes matter.",
    ),
    ArtifactDef(
        "highlight_candidates",
        "Highlight Candidates",
        ("**/highlight_candidates.json", "**/*_highlight_candidates.json"),
        "Run highlight_picker.py for long-to-short candidate review.",
    ),
    ArtifactDef(
        "audio_boundary_plan",
        "Audio Boundary Plan",
        ("**/audio_boundary_plan.json", "**/*_audio_boundary_plan.json"),
        "Run audio_boundary_snap.py after highlight review to align cuts with words, sentence endings, and silence.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "audio_transition_plan",
        "J-cut / L-cut Audio Transition Plan",
        ("**/audio_transition_plan.json", "**/*_audio_transition_plan.json"),
        "Run audio_transition.py verify, apply through render_final.py, then review every changed boundary at 1x.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "shorts_batch",
        "Shorts Batch",
        ("**/shorts_batch.json", "**/*_shorts_batch.json"),
        "Run shorts_batch.py after highlight_picker.py to create per-short render jobs and QA commands.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "enrich_plan",
        "Enrich Plan",
        (
            "**/enrich_plan.json",
            "**/*_enrich_plan.json",
            "**/emphasis_plan.json",
            "**/*_emphasis_plan.json",
            "**/screen_focus_plan.json",
            "**/speaker_badges.json",
        ),
        "Run auto_enrich.py and optional screen_focus.py before final render.",
    ),
    ArtifactDef(
        "speaker_turns",
        "Speaker Turns",
        ("**/speaker_turns.json", "**/*_speaker_turns.json"),
        "Run speaker_turns.py for podcast/interview speaker review and resolve unlabeled speaker blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "storyboard_plan",
        "Storyboard Plan",
        ("**/storyboard_plan.json", "**/*_storyboard_plan.json"),
        "Run storyboard_plan.py and review the Markdown shot cards.",
    ),
    ArtifactDef(
        "storyboard_assets",
        "Storyboard Assets",
        ("**/storyboard_assets.json", "**/*_storyboard_assets.json"),
        "Run storyboard_assets.py; resolve every blocking asset before render.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "video_prompt_pack",
        "Video Prompt Pack",
        ("**/video_prompt_pack.json", "**/*_video_prompt_pack.json"),
        "Run video_prompt_pack.py and clear generated-video approval blockers before submitting provider jobs.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "provider_capabilities",
        "Video Provider Capabilities",
        ("**/provider_capabilities.json", "**/*_provider_capabilities.json"),
        "Run provider_capability.py verify; refresh stale UI/API capability evidence before generation.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "generation_lessons",
        "Generation Lessons",
        ("**/generation_lessons.json", "**/*_generation_lessons.json"),
        "Run generation_lessons.py verify and repair invalid or unapproved learned constraints before prompt reuse.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "reference_frame_preflight",
        "Reference Frame Preflight",
        ("**/reference_frame_preflight.json", "**/*_reference_frame_preflight.json"),
        "Run reference_frame_preflight.py and resolve missing, unreadable, or aspect-conflicting generation references.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "provider_decision",
        "Provider Decision",
        ("**/provider_decision.json", "**/*_provider_decision.json"),
        "Run provider_decision.py and clear paid-credit, budget, or dependency blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "generation_task_log",
        "Generation Task Log",
        ("**/generation_tasks.json", "**/*_generation_tasks.json"),
        "Run generation_task_log.py report and finish, download, or relink all async provider tasks.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "generated_clip_review",
        "Generated Clip Review",
        ("**/generated_clip_review.json", "**/*_generated_clip_review.json"),
        "Run generated_clip_review.py prepare/audit, regenerate failed clips, and verify the source-bound report before assembly.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "generated_motion_window",
        "Generated Motion Window",
        (
            "**/generated_motion_window.json",
            "**/*_generated_motion_window.json",
            "**/generated_motion_window/*.json",
        ),
        "Run generated_motion_window.py confirm/apply/verify; start each selected generated clip inside reviewed active motion.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "scoped_video_edit_review",
        "Scoped AI Video Edit Review",
        ("**/scoped_video_edit_review.json", "**/*_scoped_video_edit_review.json"),
        "Run scoped_video_edit_review.py prepare/audit; confirm the exact target changed and every named invariant survived before assembly.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "generated_sequence_review",
        "Generated Sequence Continuity Review",
        ("**/generated_sequence_review.json", "**/*_generated_sequence_review.json"),
        "Run generated_sequence_review.py prepare/audit and repair failed adjacent clip boundaries before assembly.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "transition_bridge",
        "Transition Bridge",
        ("**/transition_bridge_plan.json", "**/*_transition_bridge*.json"),
        "Run transition_bridge.py; approve or skip any paid transition tasks.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "video_stabilization_plan",
        "Video Stabilization Plan",
        ("**/video_stabilization_plan.json", "**/*_video_stabilization_plan.json"),
        "Run video_stabilization.py verify; apply the approved working copy and confirm the full A/B comparison.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "chroma_key",
        "Chroma-key Composite",
        ("**/chroma_key.json", "**/*_chroma_key.json"),
        "Run chroma_key.py review/apply/verify; approve representative composite and matte frames before the full render.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "speed_ramp_plan",
        "Speed Ramp Plan",
        ("**/speed_ramp_plan.json", "**/*_speed_ramp_plan.json"),
        "Run speed_ramp.py verify, fix stale source/digest errors, then apply and review the render at 1x with audio.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "freeze_punch_plan",
        "Freeze-Punch Plan",
        ("**/freeze_punch_plan.json", "**/*_freeze_punch_plan.json"),
        "Run freeze_punch.py apply, live-verify the bound delivery, then review every freeze entry/exit at 1x with audio.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "motion_guard",
        "Motion Guard",
        ("**/motion_guard.json", "**/*_motion_guard.json"),
        "Run motion_guard.py and replace still-heavy runs before render when motion is required.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "render_config",
        "Render Config",
        ("**/render_config.json", "**/*_render_config.json"),
        "Create render_config.json or export one from highlight_picker.py.",
    ),
    ArtifactDef(
        "edit_style_profile",
        "Edit Style Profile",
        ("**/edit_style_profile.json", "**/*_edit_style_profile.json"),
        "Run edit_style_profile.py verify; repair schema, approval, evidence, or canonical profile-id drift before reuse.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "edit_recipe",
        "Portable Edit Recipe",
        ("**/edit_recipe.json", "**/*_edit_recipe.json"),
        "Run edit_recipe.py verify; re-export any recipe whose schema, slots, or portable digest no longer match.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "edit_revision_history",
        "Edit Revision History",
        ("**/edit_revision_history.json", "**/*_edit_revision_history.json"),
        "Run edit_revision.py status; restore external changes, redo/undo safely, or create a fresh source-bound revision.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "edit_preflight",
        "Edit Preflight",
        ("**/edit_preflight.json", "**/*_edit_preflight.json"),
        "Run edit_preflight.py and resolve missing media, invalid timing, or risky edit parameters before render.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "color_grade",
        "Color Grade",
        ("**/color_grade.json", "**/*_color_grade.json"),
        "Run color_grade.py when the master needs a bounded color look before final render.",
    ),
    ArtifactDef(
        "master_video",
        "Master Video",
        (
            "**/output/*master*.mp4",
            "**/output/final*.mp4",
            "**/*_master*.mp4",
            "**/final.mp4",
        ),
        "Run render_final.py to produce a final/master MP4.",
    ),
    ArtifactDef(
        "render_qa",
        "Render QA",
        (
            "**/render_qa.json",
            "**/*_qa.json",
            "**/render_qa_review.json",
        ),
        "Run render_qa.py and fix any FAIL segments before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "stream_coverage_qa",
        "Stream Coverage QA",
        (
            "**/stream_coverage_qa.json",
            "**/*_stream_coverage_qa.json",
        ),
        "Run stream_coverage_qa.py verify; rerender any truncated, offset, short, or stale decoded stream before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "encode_quality_qa",
        "Encode Quality QA",
        (
            "**/encode_quality_qa.json",
            "**/*_encode_quality_qa.json",
        ),
        "Run encode_quality_qa.py verify; raise bitrate or reduce downscaling when the same-timeline derivative falls below its SSIM/PSNR floors.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "flash_safety_qa",
        "Flash Safety QA",
        (
            "**/flash_safety_qa.json",
            "**/*_flash_safety_qa.json",
        ),
        "Run flash_safety_qa.py verify; remove or reduce flagged luminance/red flashing and escalate regulated delivery to an accredited analyzer.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "temporal_artifact_qa",
        "Temporal Artifact QA",
        (
            "**/temporal_artifact_qa.json",
            "**/*_temporal_artifact_qa.json",
        ),
        "Run temporal_artifact_qa.py verify; inspect every before/suspect/after candidate and repair confirmed or uncertain transient artifacts.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "shot_color_qa",
        "Shot Color QA",
        (
            "**/shot_color_qa.json",
            "**/*_shot_color_qa.json",
        ),
        "Run shot_color_qa.py on the rendered master and resolve broadcast-range blockers or review flagged shot changes.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "edit_compare",
        "Source-time Edit Compare",
        (
            "**/edit_compare.json",
            "**/*_edit_compare.json",
        ),
        "Run edit_compare.py against the original, final render, and approved cut list; resolve mapping or pixel-check blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "retention_rhythm_qa",
        "Retention Rhythm QA",
        (
            "**/retention_rhythm_qa.json",
            "**/*_retention_rhythm_qa.json",
        ),
        "Run retention_rhythm_qa.py and review inactive hooks, long holds, attention gaps, or cadence warnings.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "reference_edit_rhythm",
        "Reference Edit Rhythm",
        (
            "**/reference_edit_rhythm.json",
            "**/*_reference_edit_rhythm.json",
        ),
        "Run reference_edit_rhythm.py verify; review structural differences and repair any required-match or source/evidence drift blocker.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "speech_continuity_qa",
        "Speech Continuity QA",
        (
            "**/speech_continuity_qa.json",
            "**/*_speech_continuity_qa.json",
        ),
        "Re-transcribe the rendered master, run speech_continuity_qa.py, and fix repeated speech before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "lip_sync_review",
        "Final-master Lip-sync Review",
        (
            "**/lip_sync_review.json",
            "**/*_lip_sync_review.json",
        ),
        "Run lip_sync_review.py verify; regenerate proofs and review again after any final-master, audio, timing, or evidence drift.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "review_proxy",
        "Review Proxy",
        (
            "**/review_proxy.json",
            "**/*_review_proxy.json",
            "**/review_proxy.mp4",
            "**/*_review_proxy.mp4",
        ),
        "Run review_proxy.py when a lightweight, timecoded full-video review copy is needed.",
    ),
    ArtifactDef(
        "audio_master_report",
        "Audio Master Report",
        (
            "**/audio_master_report.json",
            "**/*_audio_master_report.json",
        ),
        "Run audio_master_report.py and fix loudness, true-peak, LRA, or silence blockers before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "audio_channel_qa",
        "Audio Channel QA",
        (
            "**/audio_channel_qa.json",
            "**/*_audio_channel_qa.json",
        ),
        "Run audio_channel_qa.py verify; fix missing-channel activity, onset skew, balance, phase, or mono fold-down blockers before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "audio_dropout_qa",
        "Audio Dropout QA",
        (
            "**/audio_dropout_qa.json",
            "**/*_audio_dropout_qa.json",
        ),
        "Run audio_dropout_qa.py audit and verify; repair confirmed or uncertain brief gaps before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "narration_loudness_qa",
        "Narration Loudness QA",
        (
            "**/narration_loudness_qa.json",
            "**/*_narration_loudness_qa.json",
        ),
        "Run narration_loudness_qa.py verify; fix phrase target/spread/peak/LRA blockers before mixing or publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "privacy_redaction",
        "Privacy Redaction",
        (
            "**/privacy_redaction.json",
            "**/*_privacy_redaction.json",
            "**/privacy_redaction_plan.json",
            "**/*_privacy_redaction_plan.json",
        ),
        "Run privacy_redact.py and review/resolve visual privacy blockers before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitles",
        "Subtitle Pack",
        ("**/subtitles/*.srt", "**/subtitles/*.vtt", "**/subtitles/*.ass", "**/*_subtitles.json"),
        "Run subtitle_pack.py when platform sidecar subtitles are needed.",
    ),
    ArtifactDef(
        "caption_speech_qa",
        "Caption / Speech QA",
        (
            "**/caption_speech_qa.json",
            "**/*_caption_speech_qa.json",
        ),
        "Run caption_speech_qa.py verify with the isolated speech track; fix orphan, offset, edge, or long-pause caption risks before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "subtitle_readability_qa",
        "Subtitle Readability QA",
        (
            "**/subtitle_readability_qa.json",
            "**/*_subtitle_readability_qa.json",
        ),
        "Run subtitle_readability_qa.py on output-aligned subtitle JSON and resolve timing/readability blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "platform_safe_area_qa",
        "Platform Safe Area QA",
        (
            "**/platform_safe_area_qa.json",
            "**/*_platform_safe_area_qa.json",
        ),
        "Run platform_safe_area_qa.py for each platform export and move critical text, PIP, CTA, or focus markers out of UI rails.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "localization_pack",
        "Localization Pack",
        (
            "**/localization_pack.json",
            "**/*_localization_pack.json",
        ),
        "Run localization_pack.py and clear missing translation, readability, dubbing, or voice blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "asset_provenance",
        "Asset Provenance",
        (
            "**/asset_provenance.json",
            "**/*_asset_provenance.json",
        ),
        "Run asset_provenance.py and clear missing source, license, attribution, or file blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "source_receipts",
        "Source Receipts",
        (
            "**/source_receipts.json",
            "**/*_source_receipts.json",
        ),
        "Run source_receipts.py and clear missing proof URL, screenshot, or primary-source blockers.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "audio_cue_sheet",
        "Audio Cue Sheet",
        (
            "**/audio_cue_sheet.json",
            "**/*_audio_cue_sheet.json",
        ),
        "Run audio_cue_sheet.py and resolve missing local BGM/SFX or generated-audio approvals.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "final_audio_storyboard",
        "Locked-EDL Final Audio Storyboard",
        (
            "**/final_audio_storyboard.json",
            "**/*_final_audio_storyboard.json",
        ),
        "Run final_audio_storyboard.py verify; rebuild the final-timeline audio plan after any EDL, storyboard, source, response, or report drift.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "audio_sync",
        "Audio Sync",
        (
            "**/audio_sync.json",
            "**/*_audio_sync.json",
            "**/audio_sync_plan.json",
            "**/*_audio_sync_plan.json",
        ),
        "Run audio_sync.py and review low-confidence external-audio alignment before replacing production audio.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "multicam_sync",
        "Multicam Sync",
        (
            "**/multicam_sync.json",
            "**/*_multicam_sync.json",
            "**/multicam_sync_plan.json",
            "**/*_multicam_sync_plan.json",
        ),
        "Run multicam_sync.py and resolve low-confidence, missing, or pair-inconsistent angles before editing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "chapter_markers",
        "Chapter Markers",
        ("**/chapters.json", "**/chapters-youtube.txt", "**/chapters.ffmetadata"),
        "Run chapter_markers.py for long-form or YouTube/Bilibili chapter sidecars.",
    ),
    ArtifactDef(
        "caption",
        "Caption Copy",
        ("**/caption.json", "**/*_caption.json"),
        "Run generate_caption.py and review title/body/tags.",
    ),
    ArtifactDef(
        "platform_exports",
        "Platform Exports",
        ("**/multi_export_manifest.json", "**/output/*_xhs.mp4", "**/output/*_douyin.mp4", "**/output/*_wxch.mp4"),
        "Run multi_export.py when separate platform deliverables are required.",
    ),
    ArtifactDef(
        "hdr_sdr_plan",
        "HDR to Rec.709 SDR Delivery",
        ("**/hdr_sdr_plan.json", "**/*_hdr_sdr_plan.json"),
        "Run hdr_sdr.py apply, then live-verify BT.709 tags, source/output hashes, and the full-decode receipt.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "delivery_encode_plan",
        "Target-size Delivery Encode",
        ("**/delivery_encode_plan.json", "**/*_delivery_encode_plan.json"),
        "Run delivery_encode.py apply, then verify the source-bound size and decode contract before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "approval_receipt",
        "Approval Receipt",
        ("**/approval_receipt.json", "**/*_approval_receipt.json"),
        "Create a new approval_receipt.py receipt after final review, then re-run verification before publishing.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "publish_package",
        "Publish Package",
        ("**/publish_package.json", "**/*_publish_package.json"),
        "Run publish_package.py and resolve missing platform files or gate blockers before upload.",
        blocks_when_present=True,
    ),
    ArtifactDef(
        "review_dashboard",
        "Review Dashboard",
        (
            "**/review_dashboard.json",
            "**/*_review_dashboard.json",
            "**/review_dashboard.html",
            "**/*_review_dashboard.html",
        ),
        "Run review_dashboard.py when a browser-readable human review queue is needed.",
    ),
    ArtifactDef(
        "nle_handoff",
        "NLE Handoff",
        (
            "**/*.edl",
            "**/*.edl.json",
            "**/*.fcpxml",
            "**/*.fcpxml.json",
            "**/*.otio",
            "**/*.otio.json",
        ),
        "Run export_edl.py, export_fcpxml.py, or export_otio.py if an editor needs Premiere/FCP/Resolve handoff files.",
    ),
)

ARTIFACT_BY_CATEGORY = {item.category: item for item in ARTIFACTS}

STAGE_REQUIREMENTS: Mapping[str, Sequence[str]] = {
    "analysis": ("transcript",),
    "plan_review": ("transcript", "clean_script", "storyboard_plan"),
    "render_ready": ("transcript", "clean_script", "render_config"),
    "publish_ready": ("transcript", "clean_script", "render_config", "master_video", "render_qa", "caption"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mtime(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_excluded(path: Path, project_dir: Path, excludes: Iterable[str]) -> bool:
    try:
        rel = path.relative_to(project_dir)
    except ValueError:
        rel = path
    excluded = set(excludes)
    return any(part in excluded for part in rel.parts)


def find_artifacts(
    project_dir: Path,
    definition: ArtifactDef,
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    include_hash: bool = False,
) -> List[ArtifactRecord]:
    records: Dict[str, ArtifactRecord] = {}
    for pattern in definition.patterns:
        for path in project_dir.glob(pattern):
            if not path.is_file() or _is_excluded(path, project_dir, excludes):
                continue
            if definition.category == "master_video" and any(
                token in path.stem.lower()
                for token in ("review_proxy", "edit_compare", "source_vs_final")
            ):
                continue
            abs_path = path.resolve()
            records[str(abs_path)] = ArtifactRecord(
                category=definition.category,
                path=str(abs_path),
                size_bytes=abs_path.stat().st_size,
                modified_at=_mtime(abs_path),
                sha256=_sha256(abs_path) if include_hash else None,
            )
    return sorted(records.values(), key=lambda item: (item.modified_at, item.path), reverse=True)


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _int_at(data: Mapping[str, Any], *keys: str) -> int:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return 0
        current = current.get(key)
    try:
        return int(current or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_category(
    definition: ArtifactDef,
    artifacts: Sequence[ArtifactRecord],
    *,
    project_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if not artifacts:
        return {
            "category": definition.category,
            "label": definition.label,
            "status": "missing",
            "artifact_count": 0,
            "latest_path": None,
            "notes": [],
            "next_action": definition.next_action,
        }

    status = "ready"
    notes: List[str] = []

    if definition.category == "runtime_preflight":
        from runtime_preflight import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable runtime preflight: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"runtime preflight live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"missing, unknown, or stale runtime capability {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"runtime preflight has {warnings} warning(s): {artifact.path}"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "production_authorization":
        from production_authorization import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable production authorization: {artifact.path}")
                continue
            if project_dir is None:
                status = "blocked"
                notes.append("project root unavailable for live production authorization verification")
                continue
            try:
                verification = verify_report(artifact.path, project_dir=str(project_dir))
            except Exception as exc:
                status = "blocked"
                notes.append(f"production authorization verification failed: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or stale production authorization {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"production authorization needs review {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category in {"approval_receipt", "edit_revision_history"}:
        latest = artifacts[0]
        if len(artifacts) > 1:
            status = "blocked"
            noun = "approval receipts" if definition.category == "approval_receipt" else f"{definition.label.lower()} artifacts"
            notes.append(f"multiple {noun} are ambiguous: {len(artifacts)} found")
        else:
            data = _load_json(latest.path)
            if data is None:
                status = "blocked"
                notes.append(f"{definition.label.lower()} is unreadable")
        if status != "blocked" and project_dir is None:
            status = "blocked"
            notes.append(f"project root unavailable for live {definition.label.lower()} verification")
        elif status != "blocked":
            try:
                if definition.category == "approval_receipt":
                    from approval_receipt import verify_receipt

                    verification = verify_receipt(
                        data,
                        str(project_dir),
                        receipt_path=latest.path,
                    )
                else:
                    from edit_revision import verify_history

                    verification = verify_history(data, str(project_dir))
            except Exception as exc:
                status = "blocked"
                notes.append(f"{definition.label.lower()} verification failed: {exc}")
            else:
                blocking = _int_at(verification, "summary", "blocking")
                if blocking:
                    status = "blocked"
                    notes.append(
                        f"{definition.label.lower()} is {verification.get('status', 'stale')}: "
                        f"{blocking} blocking item(s)"
                    )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": latest.path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "edit_style_profile":
        from edit_style_profile import verify_profile

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable edit style profile: {artifact.path}")
                continue
            verification = verify_profile(data)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid edit style profile {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"edit style profile needs review {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "edit_recipe":
        from edit_recipe import verify_recipe

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable edit recipe: {artifact.path}")
                continue
            verification = verify_recipe(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid edit recipe {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"edit recipe source preflight had warnings: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "reference_edit_rhythm":
        from reference_edit_rhythm import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable reference edit rhythm report: {artifact.path}")
                continue
            verification = verify_report(data)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid reference edit rhythm {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"reference edit rhythm needs review {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "final_audio_storyboard":
        from final_audio_storyboard import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable final audio storyboard: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    artifact.path,
                    project_dir=str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"final audio storyboard verification failed: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid final audio storyboard {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"final audio storyboard needs review {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "lip_sync_review":
        from lip_sync_review import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable lip-sync review: {artifact.path}")
                continue
            verification = verify_report(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid lip-sync review {artifact.path}: {blocking} blocking item(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "subtitle_style_preview":
        from subtitle_style_preview import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable subtitle style preview: {artifact.path}")
                continue
            try:
                verification = verify_report(data, str(project_dir) if project_dir is not None else None)
            except Exception as exc:
                status = "blocked"
                notes.append(f"subtitle style preview verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid subtitle style preview {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"subtitle style preview needs review {artifact.path}: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "subtitle_glyph_qa":
        from subtitle_glyph_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable subtitle glyph report: {artifact.path}")
                continue
            try:
                verification = verify_report(data, str(project_dir) if project_dir is not None else None)
            except Exception as exc:
                status = "blocked"
                notes.append(f"subtitle glyph live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid subtitle glyph report {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"subtitle glyph report uses explicit fallback {artifact.path}: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "subtitle_render_review":
        from subtitle_render_review import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable subtitle render review: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data, str(project_dir) if project_dir is not None else None
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"subtitle render live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid subtitle render review {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"subtitle render review retains warnings {artifact.path}: {warnings}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "framing_preview":
        from framing_preview import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable platform framing preview: {artifact.path}")
                continue
            try:
                verification = verify_report(data, str(project_dir) if project_dir is not None else None)
            except Exception as exc:
                status = "blocked"
                notes.append(f"platform framing preview verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid platform framing preview {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"platform framing preview needs review {artifact.path}: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "flash_safety_qa":
        from flash_safety_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable flash-safety report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"flash-safety live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid or risky flash-safety report {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"flash-safety report needs review {artifact.path}: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "temporal_artifact_qa":
        from temporal_artifact_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable temporal-artifact report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"temporal-artifact live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid or unresolved temporal-artifact report {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"temporal-artifact report contains approved intentional edits: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "stream_coverage_qa":
        from stream_coverage_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable stream coverage report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"stream coverage live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or incomplete stream coverage report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"stream coverage report has scoped-stream warnings {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "encode_quality_qa":
        from encode_quality_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable encode-quality report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"encode-quality live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or degraded encode-quality report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"encode-quality report needs visual review {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "narration_loudness_qa":
        from narration_loudness_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable narration loudness report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"narration loudness live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or inconsistent narration loudness report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"narration loudness report contains documented exceptions {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "audio_channel_qa":
        from audio_channel_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable audio channel report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"audio channel live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or risky audio channel report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"audio channel report needs listening review {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "audio_dropout_qa":
        from audio_dropout_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable audio dropout report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"audio dropout live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or unresolved audio dropout report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"audio dropout report contains intentional-pause review {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "caption_speech_qa":
        from caption_speech_qa import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable caption/speech report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data,
                    str(project_dir) if project_dir is not None else None,
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"caption/speech live verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or mistimed caption/speech report {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"caption/speech report needs normal-speed review {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "generated_clip_review":
        from generated_clip_review import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable generated clip review: {artifact.path}")
                continue
            verification = verify_report(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid generated clip review {artifact.path}: {blocking} blocking item(s)"
                )
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"generated clip review retains approved trim-only edits: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "generated_motion_window":
        from generated_motion_window import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable generated motion-window plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or unapplied generated motion window {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"generated motion window requires full-speed review: {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "generated_sequence_review":
        from generated_sequence_review import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable generated sequence review: {artifact.path}")
                continue
            verification = verify_report(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid generated sequence review {artifact.path}: {blocking} blocking item(s)"
                )
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"generated sequence review contains accepted intentional changes: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "scoped_video_edit_review":
        from scoped_video_edit_review import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable scoped video edit review: {artifact.path}")
                continue
            verification = verify_report(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid scoped video edit review {artifact.path}: {blocking} blocking item(s)"
                )
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"scoped video edit review retains warning(s): {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "generation_lessons":
        from generation_lessons import verify_library

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable generation lesson library: {artifact.path}")
                continue
            verification = verify_library(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid generation lesson library {artifact.path}: {blocking} blocking item(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "provider_capabilities":
        from provider_capability import verify_bundle

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable provider capability bundle: {artifact.path}")
                continue
            verification = verify_bundle(data, max_age_days=30, require_fresh=True)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or stale provider capabilities {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"provider capabilities need source review {artifact.path}: {warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "video_prompt_pack":
        from video_prompt_pack import verify_prompt_pack

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable video prompt pack: {artifact.path}")
                continue
            policy = data.get("global", {}).get("capability_policy") or {}
            bundles: List[Mapping[str, Any]] = []
            if policy.get("required") or policy.get("bundle_ids"):
                if project_dir is None:
                    status = "blocked"
                    notes.append("project root unavailable for provider capability live verification")
                    continue
                capability_artifacts = find_artifacts(
                    project_dir,
                    ARTIFACT_BY_CATEGORY["provider_capabilities"],
                )
                bundles = [
                    bundle
                    for record in capability_artifacts
                    if (bundle := _load_json(record.path)) is not None
                ]
            verification = verify_prompt_pack(data, capability_bundles=bundles)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid video prompt pack {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"video prompt pack capability evidence needs review: {warnings} warning(s)")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "chroma_key":
        from chroma_key import verify_report

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable chroma-key report: {artifact.path}")
                continue
            try:
                verification = verify_report(
                    data, str(project_dir) if project_dir is not None else None
                )
            except Exception as exc:
                status = "blocked"
                notes.append(f"chroma-key verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(f"invalid chroma-key report {artifact.path}: {blocking} blocking item(s)")
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"chroma-key composite retains {warnings} review warning(s): {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "frame_rate_conform_plan":
        from frame_rate_conform import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable frame-rate conform plan: {artifact.path}")
                continue
            try:
                verification = verify_plan(data)
            except Exception as exc:
                status = "blocked"
                notes.append(f"frame-rate conform verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or unapplied frame-rate conform {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"frame-rate conform requires full-speed motion review: {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "clip_assembly_plan":
        from clip_assembly import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable clip-assembly plan: {artifact.path}")
                continue
            try:
                verification = verify_plan(data)
            except Exception as exc:
                status = "blocked"
                notes.append(f"clip-assembly verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or unreviewed clip assembly {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"clip assembly retains {warnings} normalization warning(s): {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "loop_fill_plan":
        from loop_fill import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable loop-fill plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid or unreviewed loop-fill plan {artifact.path}: {blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(f"loop-fill proof has {warnings} warning(s): {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "interlace_conform_plan":
        from interlace_conform import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable interlace conform plan: {artifact.path}")
                continue
            try:
                verification = verify_plan(data)
            except Exception as exc:
                status = "blocked"
                notes.append(f"interlace conform verification failed {artifact.path}: {exc}")
                continue
            blocking = _int_at(verification, "summary", "blocking")
            warnings = _int_at(verification, "summary", "warnings")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid, unapplied, or unconfirmed interlace conform {artifact.path}: "
                    f"{blocking} blocking item(s)"
                )
            elif warnings:
                status = "warn" if status != "blocked" else status
                notes.append(
                    f"interlace conform retains a reviewed fallback/override warning: {artifact.path}: "
                    f"{warnings} warning(s)"
                )
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "video_stabilization_plan":
        from video_stabilization import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable video stabilization plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid video stabilization plan {artifact.path}: {blocking} blocking item(s)"
                )
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"video stabilization plan retains a reviewed backend warning: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "multimodal_dead_air_plan":
        from multimodal_dead_air import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable multimodal dead-air plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(
                    f"invalid multimodal dead-air plan {artifact.path}: {blocking} blocking item(s)"
                )
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"multimodal dead-air plan retains a removal-budget override: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "speed_ramp_plan":
        from speed_ramp import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable speed-ramp plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid speed-ramp plan {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"speed-ramp plan requires full-speed audio review: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "freeze_punch_plan":
        from freeze_punch import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable freeze-punch plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid or unapplied freeze-punch plan {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"freeze-punch delivery requires full-speed audio review: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "audio_transition_plan":
        from audio_transition import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable audio-transition plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid audio-transition plan {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"J-cut/L-cut boundaries require full-speed listening review: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "delivery_encode_plan":
        from delivery_encode import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable delivery encode plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid delivery encode plan {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"delivery encode requires compression-quality review: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    if definition.category == "hdr_sdr_plan":
        from hdr_sdr import verify_plan

        for artifact in artifacts:
            data = _load_json(artifact.path)
            if data is None:
                status = "blocked"
                notes.append(f"unreadable HDR-to-SDR plan: {artifact.path}")
                continue
            verification = verify_plan(data)
            blocking = _int_at(verification, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"invalid HDR-to-SDR plan {artifact.path}: {blocking} blocking item(s)")
            elif _int_at(verification, "summary", "warnings"):
                status = "warn" if status != "blocked" else status
                notes.append(f"HDR-to-SDR delivery requires full visual color review: {artifact.path}")
        return {
            "category": definition.category,
            "label": definition.label,
            "status": status,
            "artifact_count": len(artifacts),
            "latest_path": artifacts[0].path,
            "notes": sorted(set(notes)),
            "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
        }

    for artifact in artifacts:
        data = _load_json(artifact.path)
        if data is None:
            continue

        if definition.category in {
            "storyboard_assets",
            "video_prompt_pack",
            "reference_frame_preflight",
            "generation_task_log",
            "transition_bridge",
            "motion_guard",
            "speaker_turns",
            "privacy_redaction",
            "localization_pack",
            "asset_provenance",
            "source_receipts",
            "audio_cue_sheet",
            "audio_sync",
            "multicam_sync",
            "audio_master_report",
            "publish_package",
            "edit_preflight",
            "shorts_batch",
            "edit_brief_plan",
            "audio_boundary_plan",
            "rough_cut",
            "visual_dedupe",
            "edit_compare",
            "shot_color_qa",
            "retention_rhythm_qa",
            "speech_continuity_qa",
            "subtitle_readability_qa",
            "platform_safe_area_qa",
            "cover_variants",
            "script_alignment",
            "semantic_transcript_review",
        }:
            blocking = _int_at(data, "summary", "blocking")
            if blocking:
                status = "blocked"
                notes.append(f"{blocking} blocking item(s) in summary.blocking")

        elif definition.category == "provider_decision":
            summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
            blockers = {
                "approval_required": _int_at(summary, "approval_required"),
                "budget_blocked": _int_at(summary, "budget_blocked"),
                "selected_missing_requirements": _int_at(summary, "selected_missing_requirements"),
            }
            active = {k: v for k, v in blockers.items() if v}
            if active:
                status = "blocked"
                notes.extend(f"{key}={value}" for key, value in active.items())

        elif definition.category == "render_qa":
            qa_status = str(data.get("status") or "").lower()
            file_statuses = [
                str(item.get("status") or "").lower()
                for item in data.get("files", [])
                if isinstance(item, Mapping)
            ]
            if qa_status == "fail" or "fail" in file_statuses:
                status = "blocked"
                notes.append("render QA status is fail")
            elif qa_status == "warn" or "warn" in file_statuses:
                status = "warn" if status != "blocked" else status
                notes.append("render QA status is warn")

    return {
        "category": definition.category,
        "label": definition.label,
        "status": status,
        "artifact_count": len(artifacts),
        "latest_path": artifacts[0].path,
        "notes": sorted(set(notes)),
        "next_action": definition.next_action if status in {"missing", "blocked", "warn"} else "",
    }


def build_manifest(
    project_dir: str,
    *,
    target_stage: str = "publish_ready",
    required: Optional[Sequence[str]] = None,
    include_hash: bool = False,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    ignored_categories: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if target_stage not in STAGE_REQUIREMENTS:
        raise ValueError(f"unknown target stage: {target_stage}")

    required_categories = list(STAGE_REQUIREMENTS[target_stage])
    ignored = set(ignored_categories or [])
    unknown_ignored = sorted(ignored.difference(ARTIFACT_BY_CATEGORY))
    if unknown_ignored:
        raise ValueError(f"unknown ignored artifact category: {unknown_ignored[0]}")
    for category in required or []:
        if category not in ARTIFACT_BY_CATEGORY:
            raise ValueError(f"unknown artifact category: {category}")
        if category not in required_categories:
            required_categories.append(category)

    artifacts_by_category: Dict[str, List[ArtifactRecord]] = {}
    gates: List[Dict[str, Any]] = []
    all_artifacts: List[ArtifactRecord] = []

    for definition in ARTIFACTS:
        records = (
            []
            if definition.category in ignored
            else find_artifacts(root, definition, excludes=excludes, include_hash=include_hash)
        )
        artifacts_by_category[definition.category] = records
        all_artifacts.extend(records)

        gate = evaluate_category(definition, records, project_dir=root)
        gate["required"] = definition.category in required_categories
        gate["blocks_when_present"] = definition.blocks_when_present
        gate["ignored"] = definition.category in ignored
        gates.append(gate)

    missing_required = [g["category"] for g in gates if g["required"] and g["status"] == "missing"]
    blocked = [
        g["category"]
        for g in gates
        if g["status"] == "blocked" and (g["required"] or g["blocks_when_present"])
    ]
    warned = [g["category"] for g in gates if g["status"] == "warn" and (g["required"] or g["blocks_when_present"])]

    if missing_required or blocked:
        status = "blocked"
    elif warned:
        status = "warn"
    else:
        status = "ready"

    next_actions: List[str] = []
    for gate in gates:
        if gate["category"] in missing_required or gate["category"] in blocked or gate["category"] in warned:
            action = gate.get("next_action")
            if action:
                next_actions.append(action)
            for note in gate.get("notes") or []:
                next_actions.append(f"{gate['label']}: {note}")

    manifest_notes = [
        "This manifest is a local run-state summary, not a render queue.",
        "Optional storyboard/provider/transition/audio artifacts block when present and unresolved.",
    ]
    if ignored:
        manifest_notes.append(f"Ignored categories: {', '.join(sorted(ignored))}")

    return {
        "version": "pipeline_manifest.v1",
        "generated_at": utc_now(),
        "project_dir": str(root),
        "target_stage": target_stage,
        "required_categories": required_categories,
        "status": status,
        "summary": {
            "required": len(required_categories),
            "required_ready": sum(
                1 for g in gates if g["required"] and g["status"] in {"ready", "warn"}
            ),
            "missing_required": len(missing_required),
            "blocked_gates": len(blocked),
            "warn_gates": len(warned),
            "artifact_count": len(all_artifacts),
        },
        "missing_required": missing_required,
        "blocked_gates": blocked,
        "warn_gates": warned,
        "gates": gates,
        "artifacts": [asdict(item) for item in sorted(all_artifacts, key=lambda a: (a.category, a.path))],
        "next_actions": list(dict.fromkeys(next_actions)),
        "notes": manifest_notes,
    }


def emit_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Pipeline Manifest",
        "",
        f"- Project: `{manifest.get('project_dir', '')}`",
        f"- Target stage: `{manifest.get('target_stage', '')}`",
        f"- Status: **{str(manifest.get('status', '')).upper()}**",
        f"- Required ready: {summary.get('required_ready', 0)}/{summary.get('required', 0)}",
        f"- Blocking gates: {summary.get('blocked_gates', 0)}",
        f"- Warnings: {summary.get('warn_gates', 0)}",
        f"- Artifacts found: {summary.get('artifact_count', 0)}",
        "",
        "## Gates",
        "",
        "| category | required | status | artifacts | latest | notes |",
        "|---|---:|---|---:|---|---|",
    ]

    for gate in manifest.get("gates") or []:
        latest = os.path.basename(str(gate.get("latest_path") or "")) or "-"
        notes = "; ".join(gate.get("notes") or []) or "-"
        lines.append(
            "| {category} | {required} | {status} | {count} | `{latest}` | {notes} |".format(
                category=gate.get("category", ""),
                required="yes" if gate.get("required") else "no",
                status=gate.get("status", ""),
                count=gate.get("artifact_count", 0),
                latest=latest,
                notes=notes,
            )
        )

    actions = manifest.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

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
        description="Scan a video project folder and emit a pipeline readiness manifest."
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder to scan (default: current directory).")
    parser.add_argument("--output", default="pipeline_manifest.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument(
        "--target-stage",
        default="publish_ready",
        choices=sorted(STAGE_REQUIREMENTS),
        help="Readiness gate to evaluate.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=sorted(ARTIFACT_BY_CATEGORY),
        help="Additional artifact category required for this run; can repeat.",
    )
    parser.add_argument("--hash", action="store_true", help="Include SHA-256 for matched files.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory name to exclude while scanning; can repeat.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when required or blocking gates fail.")
    parser.add_argument("--fail-on-warn", action="store_true", help="With --strict, also exit 2 on warning gates.")
    parser.add_argument("--list-categories", action="store_true", help="Print artifact categories and exit.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.list_categories:
        for item in ARTIFACTS:
            print(f"{item.category}\t{item.label}")
        return 0

    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    manifest = build_manifest(
        args.project_dir,
        target_stage=args.target_stage,
        required=args.require,
        include_hash=args.hash,
        excludes=excludes,
    )
    write_json(args.output, manifest)
    if args.markdown:
        write_text(args.markdown, emit_markdown(manifest))

    summary = manifest["summary"]
    print(
        "Pipeline manifest: "
        f"{manifest['status']} "
        f"required={summary['required_ready']}/{summary['required']} "
        f"blocked={summary['blocked_gates']} "
        f"warn={summary['warn_gates']} "
        f"artifacts={summary['artifact_count']}",
        file=sys.stderr,
    )

    if args.strict and (manifest["status"] == "blocked" or (args.fail_on_warn and manifest["status"] == "warn")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
