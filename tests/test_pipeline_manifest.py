import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from pipeline_manifest import build_manifest, emit_markdown  # noqa: E402
from approval_receipt import create_receipt  # noqa: E402
from edit_revision import APPROVAL_VERSION, apply_revision, audit_proposal, prepare_proposal  # noqa: E402
from edit_recipe import export_recipe  # noqa: E402
import delivery_encode  # noqa: E402
import generated_clip_review  # noqa: E402
import generation_lessons  # noqa: E402
import hdr_sdr  # noqa: E402
import multimodal_dead_air  # noqa: E402
from jump_cut import Segment  # noqa: E402
from speed_ramp import build_speed_ramp_plan, parse_hold  # noqa: E402
from video_stabilization import build_plan as build_stabilization_plan  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _publish_ready_project(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "day58_master.mp4", "fake video")
    _write(tmp_path / "output" / "day58_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "day58_caption.json", {"title": "demo"})


def test_publish_ready_manifest_passes_when_required_artifacts_exist(tmp_path):
    _publish_ready_project(tmp_path)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    assert manifest["summary"]["required_ready"] == manifest["summary"]["required"]
    assert manifest["missing_required"] == []


def test_missing_required_artifacts_block_publish_ready(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "master_video" in manifest["missing_required"]
    assert any("render_final.py" in action for action in manifest["next_actions"])


def test_generation_lesson_library_is_live_verified_and_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    library_path = tmp_path / "work" / "generation_lessons.json"
    _write(library_path, generation_lessons.new_library())

    current = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in current["gates"] if g["category"] == "generation_lessons")
    assert gate["status"] == "ready"

    library = json.loads(library_path.read_text(encoding="utf-8"))
    library["library_id"] = "0" * 64
    _write(library_path, library)
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "generation_lessons")
    assert gate["status"] == "blocked"
    assert "generation_lessons" in stale["blocked_gates"]

    library_path.unlink()
    required = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["generation_lessons"],
    )
    assert "generation_lessons" in required["missing_required"]


def test_multimodal_dead_air_plan_is_live_verified_and_can_be_required(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    source = tmp_path / "origin" / "talk.mp4"
    _write(source, "source video")
    media = {
        "duration": 10.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "rotation": 0,
        "has_audio": True,
        "has_video": True,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(multimodal_dead_air, "probe_media", lambda _path: dict(media))
    plan = multimodal_dead_air.build_plan(
        str(source),
        str(tmp_path / "output" / "talk-tight.mp4"),
        media=media,
        silences=[Segment(2.0, 3.0, 1.0)],
        freezes=[Segment(2.0, 3.0, 1.0)],
        noise_db=-35.0,
    )
    _write(tmp_path / "work" / "multimodal_dead_air_plan.json", plan)

    current = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in current["gates"] if g["category"] == "multimodal_dead_air_plan")
    assert gate["status"] == "ready"

    source.write_text("changed source video", encoding="utf-8")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "multimodal_dead_air_plan")
    assert gate["status"] == "blocked"
    assert "multimodal_dead_air_plan" in stale["blocked_gates"]

    (tmp_path / "work" / "multimodal_dead_air_plan.json").unlink()
    required = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["multimodal_dead_air_plan"],
    )
    assert "multimodal_dead_air_plan" in required["missing_required"]


def test_multimodal_dead_air_budget_blocker_reaches_manifest(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    source = tmp_path / "origin" / "talk.mp4"
    _write(source, "source video")
    media = {
        "duration": 10.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "rotation": 0,
        "has_audio": True,
        "has_video": True,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(multimodal_dead_air, "probe_media", lambda _path: dict(media))
    plan = multimodal_dead_air.build_plan(
        str(source),
        str(tmp_path / "output" / "talk-tight.mp4"),
        media=media,
        silences=[Segment(2.0, 6.0, 4.0)],
        freezes=[Segment(2.0, 6.0, 4.0)],
        noise_db=-35.0,
        max_removal_ratio=0.2,
    )
    _write(tmp_path / "work" / "multimodal_dead_air_plan.json", plan)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in manifest["gates"] if g["category"] == "multimodal_dead_air_plan")

    assert gate["status"] == "blocked"
    assert "multimodal_dead_air_plan" in manifest["blocked_gates"]


def test_generated_clip_review_is_live_verified_and_can_be_required(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    clip = tmp_path / "work" / "generated_video" / "shot_001.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"generated-video")
    media = {
        "duration": 4.0,
        "fps": 24.0,
        "width": 640,
        "height": 360,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "has_audio": True,
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(generated_clip_review, "probe_media", lambda _path: dict(media))

    def fake_sheet(_clip, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"contact-sheet")
        return {
            "sample_fps": 2.0,
            "estimated_frames": 8,
            "columns": 8,
            "rows": 1,
            "thumb_width": 320,
        }

    monkeypatch.setattr(generated_clip_review, "generate_contact_sheet", fake_sheet)
    request = generated_clip_review.prepare_request(
        [{"clip_id": "shot_001", "path": str(clip)}],
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    response = {
        "version": generated_clip_review.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "visual-review-agent",
        "reviews": [
            {
                "clip_id": "shot_001",
                "verdict": "pass",
                "story_readability": "clear",
                "scores": {key: 5 for key in generated_clip_review.SCORE_WEIGHTS},
                "hard_fail_codes": [],
                "keep_ranges": [],
                "remove_ranges": [],
                "regenerate": False,
                "prompt_fix": "",
                "notes": "Full-speed, slow, muted, and audio-only review passes are clean.",
            }
        ],
    }
    report_path = tmp_path / "work" / "generated_clip_review.json"
    _write(report_path, generated_clip_review.build_report(request, response))

    ready = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["generated_clip_review"],
    )
    gate = next(g for g in ready["gates"] if g["category"] == "generated_clip_review")
    assert gate["status"] == "ready"

    clip.write_bytes(b"changed-generated-video")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "generated_clip_review")
    assert gate["status"] == "blocked"
    assert "generated_clip_review" in stale["blocked_gates"]

    report_path.unlink()
    missing = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["generated_clip_review"],
    )
    assert "generated_clip_review" in missing["missing_required"]


def test_current_approval_receipt_can_be_required_for_publish(tmp_path):
    _publish_ready_project(tmp_path)
    receipt_path = tmp_path / "verify" / "approval_receipt.json"
    receipt = create_receipt(
        str(tmp_path),
        [
            str(tmp_path / "output" / "day58_master.mp4"),
            str(tmp_path / "output" / "day58_qa.json"),
            str(tmp_path / "output" / "day58_caption.json"),
        ],
        approved_by="Jay",
        receipt_path=str(receipt_path),
    )
    _write(receipt_path, receipt)

    manifest = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["approval_receipt"],
    )

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "approval_receipt")
    assert gate["status"] == "ready"
    assert gate["required"] is True


def test_stale_approval_receipt_blocks_when_present(tmp_path):
    _publish_ready_project(tmp_path)
    receipt_path = tmp_path / "verify" / "approval_receipt.json"
    master = tmp_path / "output" / "day58_master.mp4"
    receipt = create_receipt(
        str(tmp_path),
        [str(master)],
        approved_by="Jay",
        receipt_path=str(receipt_path),
    )
    _write(receipt_path, receipt)
    master.write_text("changed after review", encoding="utf-8")

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "approval_receipt" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "approval_receipt")
    assert "approval receipt is stale" in gate["notes"][0]


def test_edit_recipe_is_live_verified_when_present(tmp_path):
    _publish_ready_project(tmp_path)
    video = tmp_path / "origin" / "take.mp4"
    transcript = tmp_path / "work" / "source_transcript.json"
    _write(video, "video")
    _write(transcript, {"segments": [{"id": 1, "start": 0, "end": 2, "text": "hello"}]})
    config = tmp_path / "work" / "recipe_source_config.json"
    _write(config, {"clips": [{"video": str(video), "transcript": str(transcript), "segment_id": 1}]})
    recipe = export_recipe(str(config), name="talking-head")
    _write(tmp_path / "work" / "talking-head_edit_recipe.json", recipe)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    gate = next(g for g in manifest["gates"] if g["category"] == "edit_recipe")
    assert gate["status"] == "ready"


def test_tampered_edit_recipe_blocks_manifest_even_if_summary_is_ready(tmp_path):
    _publish_ready_project(tmp_path)
    video = tmp_path / "origin" / "take.mp4"
    transcript = tmp_path / "work" / "source_transcript.json"
    _write(video, "video")
    _write(transcript, {"segments": [{"id": 1, "start": 0, "end": 2, "text": "hello"}]})
    config = tmp_path / "work" / "recipe_source_config.json"
    _write(config, {"clips": [{"video": str(video), "transcript": str(transcript), "segment_id": 1}]})
    recipe = export_recipe(str(config), name="talking-head")
    recipe["template"]["subtitle_style"] = "neon"
    recipe["summary"]["blocking"] = 0
    _write(tmp_path / "work" / "talking-head_edit_recipe.json", recipe)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "edit_recipe" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_recipe")
    assert "1 blocking item" in gate["notes"][0]


def test_unapplied_delivery_encode_plan_blocks_when_present(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    media = {
        "duration": 4.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "rotation": 0,
        "has_audio": True,
        "video_codec": "h264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "format_names": ["mov", "mp4"],
    }
    monkeypatch.setattr(delivery_encode, "probe_media", lambda _path: dict(media))
    plan = delivery_encode.build_plan(
        str(tmp_path / "output" / "day58_master.mp4"),
        str(tmp_path / "output" / "day58_delivery.mp4"),
        max_size_mib=0.5,
    )
    _write(tmp_path / "work" / "delivery_encode_plan.json", plan)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "delivery_encode_plan" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "delivery_encode_plan")
    assert gate["status"] == "blocked"
    assert "1 blocking item" in gate["notes"][0]


def test_hdr_sdr_plan_is_live_verified_and_can_be_required(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    media = {
        "duration": 4.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "rotation": 0,
        "has_audio": True,
        "video_codec": "hevc",
        "audio_codec": "aac",
        "pixel_format": "yuv420p10le",
        "bit_depth": 10,
        "color_primaries": "bt2020",
        "color_transfer": "arib-std-b67",
        "color_space": "bt2020nc",
        "color_range": "tv",
        "side_data_types": [],
        "format_names": ["mov", "mp4"],
    }
    monkeypatch.setattr(hdr_sdr, "probe_media", lambda _path: dict(media))
    monkeypatch.setattr(hdr_sdr, "_available_filters", lambda: set(hdr_sdr.REQUIRED_FILTERS))
    plan = hdr_sdr.build_plan(
        str(tmp_path / "output" / "day58_master.mp4"),
        str(tmp_path / "output" / "day58_sdr.mp4"),
    )
    _write(tmp_path / "work" / "hdr_sdr_plan.json", plan)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "hdr_sdr_plan" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "hdr_sdr_plan")
    assert gate["status"] == "blocked"
    assert "1 blocking item" in gate["notes"][0]

    (tmp_path / "work" / "hdr_sdr_plan.json").unlink()
    required = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["hdr_sdr_plan"],
    )
    assert "hdr_sdr_plan" in required["missing_required"]


def test_multiple_approval_receipts_block_as_ambiguous(tmp_path):
    _publish_ready_project(tmp_path)
    master = tmp_path / "output" / "day58_master.mp4"
    for name in ("first_approval_receipt.json", "second_approval_receipt.json"):
        receipt_path = tmp_path / "verify" / name
        receipt = create_receipt(
            str(tmp_path),
            [str(master)],
            approved_by="Jay",
            receipt_path=str(receipt_path),
        )
        _write(receipt_path, receipt)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    gate = next(g for g in manifest["gates"] if g["category"] == "approval_receipt")
    assert gate["status"] == "blocked"
    assert gate["artifact_count"] == 2
    assert "multiple approval receipts are ambiguous" in gate["notes"][0]


def test_edit_revision_history_is_live_verified_when_present(tmp_path):
    _publish_ready_project(tmp_path)
    config = tmp_path / "work" / "render_config.json"
    proposal = prepare_proposal(
        str(tmp_path),
        [str(config)],
        title="Select the reviewed clip",
        reason="Record the approved render plan as a reversible revision.",
    )
    proposal["artifacts"][0]["proposed_content"] = json.dumps(
        {"clips": [{"video": "origin/take.mp4", "start": 0, "end": 3}]},
        ensure_ascii=False,
    )
    audit = audit_proposal(str(tmp_path), proposal)
    apply_revision(
        str(tmp_path),
        proposal,
        audit,
        {
            "version": APPROVAL_VERSION,
            "review_id": audit["review_id"],
            "decision": "approve",
            "approved_by_label": "Jay",
        },
    )

    current = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in current["gates"] if g["category"] == "edit_revision_history")
    assert gate["status"] == "ready"

    _write(config, {"clips": [{"start": 99, "end": 100}]})
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "edit_revision_history")
    assert stale["status"] == "blocked"
    assert gate["status"] == "blocked"
    assert "edit_revision_history" in stale["blocked_gates"]


def test_source_inventory_can_be_required_for_analysis(tmp_path):
    _write(tmp_path / "work" / "source_inventory.json", {
        "version": "project_bootstrap.v1",
        "status": "ready",
        "summary": {"files": 1, "warnings": 0},
        "files": [{"relative_path": "origin/raw/talk.mp4"}],
    })
    _write(tmp_path / "work" / "transcript.json", {"segments": []})

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["source_inventory"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "source_inventory")
    assert gate["required"] is True
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_edit_brief_plan_can_be_required_for_analysis(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "edit_brief_plan.json", {
        "version": "edit_brief_plan.v1",
        "status": "ready",
        "summary": {"steps": 6, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["edit_brief_plan"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_brief_plan")
    assert gate["required"] is True
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_optional_edit_brief_plan_blocks_when_brief_unusable(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "edit_brief_plan.json", {
        "version": "edit_brief_plan.v1",
        "status": "blocked",
        "summary": {"steps": 0, "blocking": 1},
        "blockers": ["brief is empty"],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "edit_brief_plan" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_brief_plan")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_hook_variants_can_be_required_for_review(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "hook_variants.json", {
        "version": "hook_variants.v1",
        "summary": {"variants": 2, "usable": 2, "blocking": 0},
        "variants": [{"id": "hook_01", "hook": "别再卡开头了"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["hook_variants"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "hook_variants")
    assert gate["required"] is True
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_visual_dedupe_blocks_until_duplicate_groups_are_reviewed(tmp_path):
    _write(tmp_path / "work" / "visual_dedupe.json", {
        "version": "visual_dedupe.v1",
        "status": "blocked",
        "summary": {"duplicate_groups": 2, "blocking": 2},
        "duplicate_groups": [
            {"group_id": "duplicate_group_001"},
            {"group_id": "duplicate_group_002"},
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "visual_dedupe" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "visual_dedupe")
    assert gate["status"] == "blocked"
    assert "2 blocking item(s)" in gate["notes"][0]


def test_visual_dedupe_can_be_required_for_analysis(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["visual_dedupe"],
    )

    assert "visual_dedupe" in manifest["missing_required"]


def test_cover_variants_can_be_required_when_selected(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "cover_variants.json", {
        "version": "cover_variants.v1",
        "status": "ready",
        "selected_variant": "cover-b",
        "selected_cover": str(tmp_path / "output" / "cover-b.png"),
        "summary": {"variants": 3, "selected": 1, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["cover_variants"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "cover_variants")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_cover_variants_blocks_when_selection_is_required(tmp_path):
    _write(tmp_path / "work" / "cover_variants.json", {
        "version": "cover_variants.v1",
        "status": "blocked",
        "selected_variant": None,
        "summary": {"variants": 3, "selected": 0, "blocking": 1},
        "blockers": ["cover selection is required before publish handoff"],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "cover_variants" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "cover_variants")
    assert "1 blocking item(s)" in gate["notes"][0]


def test_render_qa_fail_blocks_even_when_file_exists(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "output" / "day58_qa.json", {
        "status": "fail",
        "files": [{"path": "final.mp4", "status": "fail"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "render_qa" in manifest["blocked_gates"]


def test_retention_rhythm_qa_blocks_when_rhythm_risk_is_present(tmp_path):
    _write(tmp_path / "verify" / "retention_rhythm_qa.json", {
        "version": "retention_rhythm_qa.v1",
        "summary": {"status": "blocked", "blocking": 2, "warnings": 1},
        "findings": [
            {"kind": "inactive_hook", "severity": "block"},
            {"kind": "long_visual_hold", "severity": "block"},
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "retention_rhythm_qa" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "retention_rhythm_qa")
    assert gate["status"] == "blocked"
    assert "2 blocking item(s)" in gate["notes"][0]


def test_retention_rhythm_qa_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["retention_rhythm_qa"],
    )

    assert "retention_rhythm_qa" in manifest["missing_required"]


def test_subtitle_readability_qa_blocks_when_timing_is_invalid(tmp_path):
    _write(tmp_path / "verify" / "subtitle_readability_qa.json", {
        "version": "subtitle_readability_qa.v1",
        "summary": {"status": "blocked", "blocking": 1, "warnings": 2},
        "findings": [
            {"kind": "cue_overlap", "severity": "block"},
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "subtitle_readability_qa" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "subtitle_readability_qa")
    assert gate["status"] == "blocked"
    assert "1 blocking item(s)" in gate["notes"][0]


def test_subtitle_readability_qa_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["subtitle_readability_qa"],
    )

    assert "subtitle_readability_qa" in manifest["missing_required"]


def test_platform_safe_area_qa_blocks_when_critical_element_hits_ui(tmp_path):
    _write(tmp_path / "verify" / "platform_safe_area_qa.json", {
        "version": "platform_safe_area_qa.v1",
        "status": "blocked",
        "summary": {"status": "blocked", "blocking": 2, "warnings": 1},
        "findings": [
            {"code": "critical_element_outside_safe_area", "severity": "block"},
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "platform_safe_area_qa" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "platform_safe_area_qa")
    assert gate["status"] == "blocked"
    assert "2 blocking item(s)" in gate["notes"][0]


def test_platform_safe_area_qa_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["platform_safe_area_qa"],
    )

    assert "platform_safe_area_qa" in manifest["missing_required"]


def test_edit_compare_blocks_when_render_or_verification_is_incomplete(tmp_path):
    _write(tmp_path / "verify" / "day74_edit_compare.json", {
        "version": "edit_compare.v1",
        "status": "planned",
        "summary": {"status": "planned", "blocking": 1, "warnings": 0},
        "blockers": ["comparison video has not been rendered (--dry-run)"],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "edit_compare" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_compare")
    assert gate["status"] == "blocked"
    assert "1 blocking item(s)" in gate["notes"][0]


def test_edit_compare_can_be_required_for_publish(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "verify" / "day74_edit_compare.json", {
        "version": "edit_compare.v1",
        "status": "pass",
        "summary": {"status": "pass", "blocking": 0, "warnings": 0},
    })

    manifest = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["edit_compare"],
    )

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_compare")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_optional_provider_decision_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "provider_decision.json", {
        "version": "provider_decision_log.v1",
        "summary": {
            "approval_required": 1,
            "budget_blocked": 0,
            "selected_missing_requirements": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "provider_decision" in manifest["blocked_gates"]
    provider_gate = next(g for g in manifest["gates"] if g["category"] == "provider_decision")
    assert "approval_required=1" in provider_gate["notes"]


def test_optional_privacy_redaction_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "privacy_redaction.json", {
        "version": "privacy_redaction_plan.v1",
        "summary": {
            "total_events": 1,
            "unreviewed": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "privacy_redaction" in manifest["blocked_gates"]
    privacy_gate = next(g for g in manifest["gates"] if g["category"] == "privacy_redaction")
    assert "1 blocking item(s) in summary.blocking" in privacy_gate["notes"]


def test_video_understanding_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "video_understanding.json", {
        "version": "video_understanding.v1",
        "summary": {"frames": 2, "detections": 1, "tracks": 1},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["video_understanding"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "video_understanding")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_color_grade_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "color_grade.json", {
        "version": "color_grade.v1",
        "preset": "screen",
        "ffmpeg": {"vf": "eq=contrast=1.0800"},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["color_grade"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "color_grade")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_takes_pack_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "takes_pack.json", {
        "version": "takes_pack.v1",
        "summary": {"sources": 2, "phrases": 8, "warnings": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "takes_pack")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1
    assert gate["blocks_when_present"] is False


def test_script_alignment_blocks_until_ambiguous_matches_are_reviewed(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "script_alignment.json", {
        "version": "script_alignment.v1",
        "status": "blocked",
        "summary": {"targets": 2, "matched": 1, "review": 1, "blocking": 1},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    gate = next(g for g in manifest["gates"] if g["category"] == "script_alignment")
    assert gate["status"] == "blocked"
    assert gate["blocks_when_present"] is True


def test_reviewed_script_alignment_is_ready(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "script_alignment.json", {
        "version": "script_alignment.v1",
        "status": "ready",
        "summary": {"targets": 2, "matched": 2, "review": 0, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["script_alignment"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "script_alignment")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_shorts_batch_artifact_is_discovered_without_blocking_when_ready(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "shorts_batch.json", {
        "version": "shorts_batch.v1",
        "status": "ready",
        "summary": {"jobs": 2, "planned": 2, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "shorts_batch")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1
    assert gate["blocks_when_present"] is True


def test_audio_boundary_plan_can_be_required_for_analysis(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "audio_boundary_plan.json", {
        "version": "audio_boundary_plan.v1",
        "status": "ready",
        "summary": {"selected": 2, "adjusted": 2, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["audio_boundary_plan"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_boundary_plan")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_optional_audio_boundary_plan_blocks_when_word_timestamps_missing(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "audio_boundary_plan.json", {
        "version": "audio_boundary_plan.v1",
        "status": "blocked",
        "summary": {"selected": 1, "adjusted": 0, "blocking": 1},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_boundary_plan" in manifest["blocked_gates"]


def test_optional_jump_cut_blocks_when_removal_budget_is_exceeded(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "jump_cut.json", {
        "version": "jump_cut_plan.v2",
        "status": "blocked",
        "summary": {"blocking": 1, "warnings": 0},
        "removal_budget": {"max_ratio": 0.2, "proposed_ratio": 0.35, "over_budget": True},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "rough_cut" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "rough_cut")
    assert gate["blocks_when_present"] is True
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_shorts_batch_blocks_when_source_missing(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "shorts_batch.json", {
        "version": "shorts_batch.v1",
        "status": "blocked",
        "summary": {"jobs": 2, "planned": 0, "blocking": 2},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "shorts_batch" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "shorts_batch")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_emphasis_plan_is_discovered_as_enrich_plan(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "emphasis_plan.json", {
        "version": "auto_emphasis_plan.v1",
        "emphasis_cues": [{"start": 1.0, "end": 2.0, "label": "重点"}],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    gate = next(g for g in manifest["gates"] if g["category"] == "enrich_plan")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_optional_edit_preflight_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "edit_preflight.json", {
        "version": "edit_preflight.v1",
        "status": "blocked",
        "summary": {
            "blocking": 2,
            "warnings": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "edit_preflight" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "edit_preflight")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_publish_package_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "publish_package.json", {
        "version": "publish_package.v1",
        "summary": {
            "platforms": 3,
            "ready_platforms": 2,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "publish_package" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "publish_package")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_publish_package_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "publish_package.json", {
        "version": "publish_package.v1",
        "summary": {
            "platforms": 3,
            "ready_platforms": 3,
            "blocking": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["publish_package"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "publish_package")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_review_dashboard_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "review_dashboard.json", {
        "version": "review_dashboard.v1",
        "status": "ready",
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "review_dashboard")
    assert gate["status"] == "ready"
    assert gate["blocks_when_present"] is False


def test_review_proxy_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "verify" / "day66_review_proxy.json", {
        "version": "review_proxy.v1",
        "status": "ready",
        "summary": {"blocking": 0, "warnings": 0},
    })
    _write(tmp_path / "verify" / "day66_review_proxy.mp4", "fake review proxy")

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "review_proxy")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 2
    assert gate["blocks_when_present"] is False


def test_review_proxy_cannot_satisfy_master_video_gate(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "day66_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "day66_caption.json", {"title": "demo"})
    _write(tmp_path / "output" / "day66_master_review_proxy.mp4", "fake proxy")
    _write(tmp_path / "output" / "day66_master_review_proxy.json", {
        "version": "review_proxy.v1",
        "status": "ready",
        "summary": {"blocking": 0, "warnings": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "master_video" in manifest["missing_required"]
    proxy_gate = next(g for g in manifest["gates"] if g["category"] == "review_proxy")
    assert proxy_gate["status"] == "ready"


def test_speech_continuity_qa_blocks_when_repeat_is_present(tmp_path):
    _write(tmp_path / "verify" / "speech_continuity_qa.json", {
        "version": "speech_continuity_qa.v1",
        "summary": {"status": "blocked", "blocking": 2, "warnings": 0},
        "findings": [
            {"kind": "boundary_exact_repeat"},
            {"kind": "internal_immediate_repeat"},
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="analysis")

    assert "speech_continuity_qa" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "speech_continuity_qa")
    assert gate["status"] == "blocked"
    assert gate["artifact_count"] == 1
    assert "2 blocking item(s)" in gate["notes"][0]


def test_speech_continuity_qa_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["speech_continuity_qa"],
    )

    assert "speech_continuity_qa" in manifest["missing_required"]


def test_otio_handoff_artifact_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "day58_edit.otio", {
        "OTIO_SCHEMA": "Timeline.1",
        "name": "DAY58",
    })
    _write(tmp_path / "work" / "day58_edit.otio.json", {
        "kind": "nle_handoff_otio",
        "event_count": 2,
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "nle_handoff")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 2
    assert gate["blocks_when_present"] is False


def test_optional_localization_pack_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "localization_pack.json", {
        "version": "localization_pack.v1",
        "summary": {
            "cue_count": 2,
            "missing_translations": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "localization_pack" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "localization_pack")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_asset_provenance_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "asset_provenance.json", {
        "version": "asset_provenance.v1",
        "summary": {
            "items": 1,
            "blocking": 1,
            "missing_license": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "asset_provenance" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "asset_provenance")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_source_receipts_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "source_receipts.json", {
        "version": "source_receipts.v1",
        "summary": {
            "claims": 2,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "source_receipts" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "source_receipts")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_source_receipts_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "source_receipts.json", {
        "version": "source_receipts.v1",
        "summary": {
            "claims": 1,
            "blocking": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["source_receipts"])

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "source_receipts")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_optional_audio_cue_sheet_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "audio_cue_sheet.json", {
        "version": "audio_cue_sheet.v1",
        "summary": {
            "music_cues": 1,
            "sfx_cues": 2,
            "blocking": 2,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_cue_sheet" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_cue_sheet")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_audio_sync_blocks_when_low_confidence(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "audio_sync_plan.json", {
        "version": "audio_sync_plan.v1",
        "summary": {
            "status": "review",
            "blocking": 1,
            "offset_seconds": 0.36,
            "confidence": 0.22,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_sync" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_sync")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_multicam_sync_blocks_when_an_angle_needs_review(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "multicam_sync_plan.json", {
        "version": "multicam_sync_plan.v1",
        "summary": {
            "blocking": 1,
            "review": 1,
            "common_overlap_seconds": 42.0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "multicam_sync" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "multicam_sync")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_ready_multicam_sync_is_discovered_without_blocking(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "multicam_sync_plan.json", {
        "version": "multicam_sync_plan.v1",
        "summary": {
            "blocking": 0,
            "ready": 3,
            "common_overlap_seconds": 42.0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "multicam_sync" not in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "multicam_sync")
    assert gate["status"] == "ready"
    assert gate["artifact_count"] == 1


def test_optional_audio_master_report_blocks_when_unresolved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "output" / "day58_audio_master_report.json", {
        "version": "audio_master_report.v1",
        "summary": {
            "blocking": 1,
            "warnings": 0,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "audio_master_report" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "audio_master_report")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_video_prompt_pack_blocks_when_unapproved(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "video_prompt_pack.json", {
        "version": "video_prompt_pack.v1",
        "summary": {
            "items": 2,
            "approval_required": 1,
            "blocking": 1,
        },
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "video_prompt_pack" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "video_prompt_pack")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_reference_frame_preflight_can_be_required(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "reference_frame_preflight.json", {
        "version": "reference_frame_preflight.v1",
        "status": "ready",
        "summary": {"references": 2, "blocking": 0, "warnings": 0},
    })

    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["reference_frame_preflight"],
    )

    assert manifest["status"] == "ready"
    gate = next(g for g in manifest["gates"] if g["category"] == "reference_frame_preflight")
    assert gate["required"] is True
    assert gate["status"] == "ready"


def test_reference_frame_preflight_blocks_on_aspect_conflict(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "reference_frame_preflight.json", {
        "version": "reference_frame_preflight.v1",
        "status": "blocked",
        "summary": {"references": 2, "blocking": 1, "warnings": 0},
        "blockers": ["shot_001: landscape reference conflicts with 9:16 portrait output"],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "reference_frame_preflight" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "reference_frame_preflight")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_optional_generation_task_log_blocks_when_async_task_unfinished(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "generation_tasks.json", {
        "version": "generation_task_log.v1",
        "summary": {
            "tasks": 1,
            "blocking": 1,
            "pending": 1,
        },
        "tasks": [
            {
                "task_key": "dreamina:submit_123",
                "provider": "dreamina",
                "provider_task_id": "submit_123",
                "status": "processing",
            }
        ],
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert manifest["status"] == "blocked"
    assert "generation_task_log" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "generation_task_log")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]


def test_privacy_redaction_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready", required=["privacy_redaction"])

    assert manifest["status"] == "blocked"
    assert "privacy_redaction" in manifest["missing_required"]


def test_semantic_transcript_review_blocks_while_choices_are_pending(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "transcript_semantic_review.json", {
        "version": "semantic_transcript_review.v1",
        "artifact_type": "audit",
        "summary": {"valid": 2, "pending_choices": 2, "blocking": 2},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "semantic_transcript_review" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "semantic_transcript_review")
    assert "2 blocking item(s) in summary.blocking" in gate["notes"]


def test_applied_semantic_transcript_review_is_ready(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "work" / "transcript_semantic_review.json", {
        "version": "semantic_transcript_review.v1",
        "artifact_type": "result",
        "summary": {"approved": 1, "rejected": 1, "blocking": 0},
    })

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "semantic_transcript_review" not in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "semantic_transcript_review")
    assert gate["status"] == "ready"


def test_shot_color_qa_blocks_when_present_and_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)
    _write(tmp_path / "output" / "day58_shot_color_qa.json", {
        "version": "shot_color_qa.v1",
        "status": "blocked",
        "summary": {"shots": 3, "blocking": 1, "warnings": 2},
    })

    blocked = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "shot_color_qa" in blocked["blocked_gates"]
    gate = next(g for g in blocked["gates"] if g["category"] == "shot_color_qa")
    assert "1 blocking item(s) in summary.blocking" in gate["notes"]

    (tmp_path / "output" / "day58_shot_color_qa.json").unlink()
    missing = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["shot_color_qa"],
    )
    assert "shot_color_qa" in missing["missing_required"]


def test_speed_ramp_plan_is_live_verified_and_detects_stale_source(tmp_path):
    _publish_ready_project(tmp_path)
    source = tmp_path / "origin" / "action.mp4"
    _write(source, "source bytes")
    plan = build_speed_ramp_plan(
        str(source),
        duration=4.0,
        fps=30.0,
        has_audio=False,
        events=[parse_hold("1,2,2")],
    )
    _write(tmp_path / "work" / "speed_ramp_plan.json", plan)

    current = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in current["gates"] if g["category"] == "speed_ramp_plan")
    assert gate["status"] == "warn"
    assert "speed_ramp_plan" not in current["blocked_gates"]

    _write(source, "changed source bytes")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "speed_ramp_plan")
    assert gate["status"] == "blocked"
    assert "speed_ramp_plan" in stale["blocked_gates"]


def test_speed_ramp_plan_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["speed_ramp_plan"],
    )

    assert "speed_ramp_plan" in manifest["missing_required"]


def test_audio_transition_plan_is_live_verified_and_can_be_required(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    plan_path = tmp_path / "work" / "audio_transition_plan.json"
    _write(plan_path, {
        "version": "audio_transition_plan.v1",
        "plan_id": "planned",
        "summary": {"blocking": 0, "warnings": 2},
    })

    monkeypatch.setattr(
        "audio_transition.verify_plan",
        lambda _plan: {"summary": {"blocking": 0, "warnings": 2}, "blockers": []},
    )
    current = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["audio_transition_plan"],
    )
    gate = next(g for g in current["gates"] if g["category"] == "audio_transition_plan")
    assert gate["status"] == "warn"
    assert gate["required"] is True
    assert "audio_transition_plan" not in current["blocked_gates"]

    monkeypatch.setattr(
        "audio_transition.verify_plan",
        lambda _plan: {"summary": {"blocking": 1, "warnings": 0}, "blockers": ["stale source"]},
    )
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(g for g in stale["gates"] if g["category"] == "audio_transition_plan")
    assert gate["status"] == "blocked"
    assert "audio_transition_plan" in stale["blocked_gates"]


def test_audio_transition_plan_can_be_required(tmp_path):
    manifest = build_manifest(
        str(tmp_path),
        target_stage="analysis",
        required=["audio_transition_plan"],
    )

    assert "audio_transition_plan" in manifest["missing_required"]


def test_video_stabilization_plan_is_live_verified(tmp_path, monkeypatch):
    _publish_ready_project(tmp_path)
    source = tmp_path / "origin" / "handheld.mp4"
    _write(source, "fake handheld video")
    media = {
        "duration": 4.0,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "has_audio": True,
    }
    monkeypatch.setattr("video_stabilization.probe_media", lambda _path: dict(media))
    monkeypatch.setattr("video_stabilization._available_filters", lambda: {"deshake"})
    plan = build_stabilization_plan(
        str(source),
        decision="review",
        filters={"deshake"},
    )
    _write(tmp_path / "work" / "video_stabilization_plan.json", plan)

    manifest = build_manifest(str(tmp_path), target_stage="publish_ready")

    assert "video_stabilization_plan" in manifest["blocked_gates"]
    gate = next(g for g in manifest["gates"] if g["category"] == "video_stabilization_plan")
    assert gate["status"] == "blocked"
    assert "1 blocking item" in gate["notes"][0]


def test_video_stabilization_plan_can_be_required(tmp_path):
    _publish_ready_project(tmp_path)

    manifest = build_manifest(
        str(tmp_path),
        target_stage="publish_ready",
        required=["video_stabilization_plan"],
    )

    assert "video_stabilization_plan" in manifest["missing_required"]


def test_markdown_contains_gate_table_and_next_actions(tmp_path):
    manifest = build_manifest(str(tmp_path), target_stage="render_ready")

    markdown = emit_markdown(manifest)

    assert "# Pipeline Manifest" in markdown
    assert "| category | required | status | artifacts | latest | notes |" in markdown
    assert "## Next Actions" in markdown


def test_cli_writes_json_and_markdown_and_strict_exit_code(tmp_path):
    out_json = tmp_path / "pipeline_manifest.json"
    out_md = tmp_path / "pipeline_manifest.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "pipeline_manifest.py"),
            "--project-dir",
            str(tmp_path),
            "--target-stage",
            "publish_ready",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    manifest = json.loads(out_json.read_text(encoding="utf-8"))
    assert manifest["version"] == "pipeline_manifest.v1"
