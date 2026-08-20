import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from storyboard_plan import ROUTING_SENTENCE, build_storyboard_plan  # noqa: E402
import generation_lessons  # noqa: E402
from video_prompt_pack import build_video_prompt_pack, emit_markdown, verify_prompt_pack  # noqa: E402


def _sample_transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "今天聊 AI 的注意力机制"},
            {"id": 2, "start": 2.0, "end": 5.0, "text": "很多人因此产生失业焦虑"},
            {"id": 3, "start": 5.0, "end": 8.0, "text": "但是我发现客户付费意愿增长了 50%"},
            {"id": 4, "start": 8.0, "end": 11.0, "text": "打开电脑演示这个自动化流程"},
            {"id": 5, "start": 11.0, "end": 14.0, "text": "评论区告诉我你怎么看"},
        ]
    }


def _lesson_library(*, provider="veo", model="*"):
    entry = {
        "version": generation_lessons.ENTRY_VERSION,
        "created_at": "2026-08-15T00:00:00Z",
        "scope": {"provider": provider, "model": model, "category": "hand_contact"},
        "lesson": "For hand-to-prop contact, isolate one interaction and keep the hand visible through release.",
        "prompt_fix": "Use one contact action in a dedicated shot.",
        "evidence": "The hand crossed through a door before release in the reviewed clip.",
        "supersedes": [],
        "source": {
            "report_id": "0" * 64,
            "request_id": "1" * 64,
            "clip_id": "shot_001",
            "clip_sha256": "2" * 64,
            "contact_sheet_sha256": "3" * 64,
            "verdict": "fail",
            "weighted_score": 100.0,
            "hard_fail_codes": ["anatomy_or_physics_failure"],
        },
        "approval": {
            "approved_by": "Jay",
            "note": "Label only; not identity authentication or a digital signature.",
        },
    }
    entry["lesson_id"] = generation_lessons._entry_id(entry)
    return generation_lessons.add_entry(generation_lessons.new_library(), entry)


def _capability_bundle():
    return {
        "version": "video_provider_capabilities.v1",
        "profiles": [
            {
                "provider": "dreamina_seedance",
                "surface": "Dreamina web",
                "model": "Seedance verified test model",
                "verified_at": datetime.now(timezone.utc).date().isoformat(),
                "sources": [
                    {
                        "source_type": "official_documentation",
                        "url": "https://example.com/provider-docs",
                    }
                ],
                "capabilities": {
                    "modes": ["text_to_video", "image_to_video"],
                    "aspect_ratios": ["9:16", "16:9"],
                    "resolutions": ["720p"],
                    "duration": {"kind": "range", "min_seconds": 2, "max_seconds": 8},
                    "reference_limits": {"images": 2, "videos": 0, "audio": 1},
                    "audio": {"generate": True, "reference": True, "preserve_source": "unknown"},
                },
            }
        ],
    }


def test_video_prompt_pack_auto_routes_and_blocks_paid_approval(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    pack = build_video_prompt_pack(
        plan,
        asset_root=str(tmp_path),
        characters=["same Chinese founder-host, navy jacket"],
        brand_anchors=["palette=charcoal,white,signal yellow"],
    )

    assert pack["routing_note"] == ROUTING_SENTENCE
    assert pack["summary"]["approval_required"] == 1
    assert pack["summary"]["blocking"] == 1
    assert "same Chinese founder-host" in pack["global"]["character_sheet_prompt"]

    dreamina_items = [item for item in pack["items"] if item["provider"] == "dreamina_seedance"]
    assert len(dreamina_items) == 1
    assert dreamina_items[0]["approval_status"] == "needs_approval"
    assert "may consume provider credits" in dreamina_items[0]["approval_note"]
    assert "no hard-coded Chinese text" in dreamina_items[0]["negative_prompt"]


def test_animate_stills_turns_codex_imagegen_into_i2v_prompts(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=5)
    (tmp_path / "imagegen").mkdir()
    (tmp_path / "imagegen" / "shot_001.png").write_bytes(b"fake image")

    pack = build_video_prompt_pack(plan, asset_root=str(tmp_path), animate_stills=True, approved=True)
    shot_001 = next(item for item in pack["items"] if item["shot_id"] == "shot_001")

    assert shot_001["provider"] == "dreamina_seedance"
    assert shot_001["mode"] == "image_to_video"
    assert shot_001["reference"]["resolved_path"].endswith("shot_001.png")
    assert shot_001["approval_status"] == "approved"
    assert pack["summary"]["blocking"] == 0


def test_provider_override_builds_veo_prompts_for_all_shots():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=3)
    pack = build_video_prompt_pack(plan, provider="veo", approved=True, max_duration=6)

    assert pack["summary"]["provider_veo"] == 3
    assert pack["summary"]["blocking"] == 0
    assert all(item["provider"] == "veo" for item in pack["items"])
    assert all(item["duration_seconds"] <= 6 for item in pack["items"])
    assert "Create a" in pack["items"][0]["prompt"]


def test_emit_markdown_includes_prompt_table_and_character_sheet(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=4)
    pack = build_video_prompt_pack(plan, asset_root=str(tmp_path))
    md = emit_markdown(pack)

    assert "# Video Prompt Pack" in md
    assert "| shot | provider | surface/model | mode | resolution | approval | capability | reference |" in md
    assert "## Character / Style Reference" in md
    assert ROUTING_SENTENCE in md


def test_shared_style_reference_is_attached_to_every_item(tmp_path):
    style_reference = tmp_path / "style-key.png"
    style_reference.write_bytes(b"fake style key")
    plan = build_storyboard_plan(_sample_transcript(), max_shots=4)

    pack = build_video_prompt_pack(
        plan,
        asset_root=str(tmp_path),
        style_reference=str(style_reference),
    )

    assert pack["global"]["style_reference"]["resolved_path"] == str(style_reference)
    assert pack["summary"]["style_reference_ready"] == 1
    assert all(
        item["style_reference"]["resolved_path"] == str(style_reference)
        for item in pack["items"]
    )
    generated = {
        "dreamina_seedance",
        "veo",
        "ltx",
        "wan",
        "sora",
        "codex_imagegen",
        "remotion_hyperframes",
    }
    assert all(
        ("STYLE LOCK:" in item["prompt"]) == (item["provider"] in generated)
        for item in pack["items"]
    )


def test_approved_generation_lessons_are_scoped_and_added_to_prompts():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=3)
    library = _lesson_library(provider="veo")

    pack = build_video_prompt_pack(
        plan,
        provider="veo",
        approved=True,
        lesson_library=library,
    )

    assert pack["global"]["lesson_library"]["library_id"] == library["library_id"]
    assert pack["summary"]["generation_lessons_applied"] == 3
    assert pack["summary"]["unique_generation_lessons"] == 1
    assert all("LEARNED CONSTRAINTS:" in item["prompt"] for item in pack["items"])
    assert all(len(item["generation_lessons"]) == 1 for item in pack["items"])
    assert "Approved generation lessons" in emit_markdown(pack)


def test_model_specific_lesson_requires_explicit_model_scope():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=1)
    library = _lesson_library(provider="veo", model="veo-3.1")

    provider_only = build_video_prompt_pack(plan, provider="veo", approved=True, lesson_library=library)
    exact_model = build_video_prompt_pack(
        plan,
        provider="veo",
        approved=True,
        lesson_library=library,
        lesson_model="veo-3.1",
    )

    assert provider_only["summary"]["generation_lessons_applied"] == 0
    assert exact_model["summary"]["generation_lessons_applied"] == 1


def test_required_capability_profile_validates_surface_settings(tmp_path):
    plan = build_storyboard_plan(_sample_transcript(), max_shots=3)
    pack = build_video_prompt_pack(
        plan,
        provider="dreamina_seedance",
        approved=True,
        capability_bundles=[_capability_bundle()],
        require_capability_profile=True,
        resolution="720p",
    )

    assert pack["summary"]["capability_profiles"] == 1
    assert pack["summary"]["capability_blocking"] == 0
    assert pack["summary"]["blocking"] == 0
    assert all(item["surface"] == "Dreamina web" for item in pack["items"])
    assert all(item["model"] == "Seedance verified test model" for item in pack["items"])
    assert all(not item["capability_issues"] for item in pack["items"])


def test_required_capability_profile_blocks_missing_or_unsupported_settings():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=1)

    missing = build_video_prompt_pack(
        plan,
        provider="veo",
        approved=True,
        require_capability_profile=True,
        resolution="720p",
    )
    unsupported = build_video_prompt_pack(
        plan,
        provider="dreamina_seedance",
        approved=True,
        capability_bundles=[_capability_bundle()],
        require_capability_profile=True,
        resolution="1080p",
    )

    assert missing["items"][0]["capability_issues"] == ["missing_capability_profile"]
    assert missing["summary"]["blocking"] == 1
    assert unsupported["items"][0]["capability_issues"] == ["unsupported_resolution:1080p"]
    assert unsupported["summary"]["blocking"] == 1


def test_prompt_pack_live_verify_detects_capability_profile_drift():
    plan = build_storyboard_plan(_sample_transcript(), max_shots=1)
    bundle = _capability_bundle()
    pack = build_video_prompt_pack(
        plan,
        provider="dreamina_seedance",
        approved=True,
        capability_bundles=[bundle],
        require_capability_profile=True,
        resolution="720p",
    )

    current = verify_prompt_pack(pack, capability_bundles=[bundle])
    bundle["profiles"][0]["model"] = "Changed model"
    stale = verify_prompt_pack(pack, capability_bundles=[bundle])

    assert current["status"] == "ready"
    assert stale["status"] == "blocked"
    assert "capability_bundle_ids_drift" in stale["blockers"]
    assert "capability_profile_id_drift:shot_001" in stale["blockers"]


def test_cli_writes_prompt_pack_and_strict_fails_until_approved(tmp_path):
    plan_path = tmp_path / "storyboard_plan.json"
    plan_path.write_text(
        json.dumps(build_storyboard_plan(_sample_transcript(), max_shots=5), ensure_ascii=False),
        encoding="utf-8",
    )
    out_json = tmp_path / "video_prompt_pack.json"
    out_md = tmp_path / "video_prompt_pack.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/video_prompt_pack.py"),
            "--storyboard-plan",
            str(plan_path),
            "--asset-root",
            str(tmp_path),
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
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] == 1
    assert ROUTING_SENTENCE in out_md.read_text(encoding="utf-8")

    approved = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/video_prompt_pack.py"),
            "--storyboard-plan",
            str(plan_path),
            "--output",
            str(out_json),
            "--approved",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert approved.returncode == 0
