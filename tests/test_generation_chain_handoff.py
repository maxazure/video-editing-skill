import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generated_clip_review  # noqa: E402
import generation_chain_handoff  # noqa: E402
import sequence_handoff  # noqa: E402
from storyboard_plan import build_storyboard_plan  # noqa: E402
from video_prompt_pack import build_video_prompt_pack  # noqa: E402


def _storyboard():
    return build_storyboard_plan(
        {
            "segments": [
                {"id": 1, "start": 0.0, "end": 1.0, "text": "人物推开门"},
                {"id": 2, "start": 1.0, "end": 2.0, "text": "人物继续走进房间"},
            ]
        },
        max_shots=2,
    )


def _make_clip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _clip_report(tmp_path):
    clip = tmp_path / "work" / "generated_video" / "shot_001.mp4"
    _make_clip(clip)
    request = generated_clip_review.prepare_request(
        [
            {
                "clip_id": "shot_001",
                "shot_id": "shot_001",
                "path": str(clip),
                "provider_route": "dreamina_video",
                "expected_beat": "人物推开门后保持动作连续",
            }
        ],
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    response = {
        "version": generated_clip_review.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "Jay",
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
                "notes": "完整看过正常速度、慢放、静音画面和独立音频，末帧可用。",
            }
        ],
    }
    report = generated_clip_review.build_report(request, response)
    path = tmp_path / "work" / "generated_clip_review.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return clip, path


def _sequence_report(tmp_path):
    storyboard = _storyboard()
    storyboard_path = tmp_path / "work" / "storyboard_plan.json"
    storyboard_path.parent.mkdir(parents=True, exist_ok=True)
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
    request = sequence_handoff.build_request(root=tmp_path, storyboard_path=storyboard_path)
    request_path = tmp_path / "work" / "sequence_handoff_request.json"
    response_path = tmp_path / "work" / "sequence_handoff_response.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    response = json.loads(json.dumps(request["response_template"]))
    response["reviewed_by"] = "Jay"
    response["review_notes"] = "逐边界确认同一空间、动作方向和可用接力方式。"
    for row in response["boundary_decisions"]:
        row["decision"] = "approve"
        row["carrier_type"] = "motion"
        row["edit_type"] = "motion_match"
        row["review_note"] = "同一场景连续动作，允许用审核末帧接力。"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    report = sequence_handoff.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    path = tmp_path / "work" / "sequence_handoff.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return storyboard, storyboard_path, path


def _prepared(tmp_path):
    clip, clip_review_path = _clip_report(tmp_path)
    storyboard, storyboard_path, sequence_path = _sequence_report(tmp_path)
    frame = tmp_path / "work" / "generation_chain" / "boundary_001_tail.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    plan = generation_chain_handoff.build_plan(
        root=tmp_path,
        clip_review_path=clip_review_path,
        sequence_handoff_path=sequence_path,
        boundary_id="boundary_001",
        frame_output=frame,
        generated_at="2026-09-19T00:00:00Z",
    )
    return clip, storyboard, storyboard_path, plan, frame


def _confirm(plan):
    return generation_chain_handoff.confirm_plan(
        plan,
        decision="use_exact_start_frame",
        reviewed_by="Jay",
        same_scene_confirmed=True,
        tail_frame_accepted=True,
        original_anchors_preserved=True,
        notes="同一房间的连续动作；保留原角色和风格锚点。",
    )


def test_prepare_extracts_exact_approved_terminal_frame_and_blocks_before_confirmation(tmp_path):
    _clip, _storyboard_data, _storyboard_path, plan, frame = _prepared(tmp_path)

    assert plan["version"] == generation_chain_handoff.VERSION
    assert plan["boundary"]["from_shot"] == "shot_001"
    assert plan["boundary"]["to_shot"] == "shot_002"
    assert plan["selected_frame"]["index"] == 23
    assert plan["selected_frame"]["pts_seconds"] > 0.9
    assert frame.is_file()
    assert plan["selected_frame"]["pixel_sha256"] == plan["handoff_frame"]["pixel_sha256"]
    assert plan["status"] == "blocked"
    assert any("confirm" in item for item in plan["blockers"])


def test_confirmed_handoff_live_verifies_and_detects_frame_drift(tmp_path):
    _clip, _storyboard_data, _storyboard_path, plan, frame = _prepared(tmp_path)
    confirmed = _confirm(plan)

    current = generation_chain_handoff.verify_plan(confirmed)
    frame.write_bytes(b"changed")
    stale = generation_chain_handoff.verify_plan(confirmed)

    assert current["status"] == "ready"
    assert current["summary"]["blocking"] == 0
    assert stale["status"] == "blocked"
    assert any("handoff frame" in item for item in stale["blockers"])


def test_reject_and_missing_identity_anchor_confirmation_fail_closed(tmp_path):
    _clip, _storyboard_data, _storyboard_path, plan, _frame = _prepared(tmp_path)
    missing_anchor = generation_chain_handoff.confirm_plan(
        plan,
        decision="use_exact_start_frame",
        reviewed_by="Jay",
        same_scene_confirmed=True,
        tail_frame_accepted=True,
        original_anchors_preserved=False,
        notes="需要补回原始角色图。",
    )
    rejected = generation_chain_handoff.confirm_plan(
        plan,
        decision="reject",
        reviewed_by="Jay",
        same_scene_confirmed=False,
        tail_frame_accepted=False,
        original_anchors_preserved=False,
        notes="这里是换场，不应强行接力。",
    )

    assert any("original_anchors_preserved" in item for item in missing_anchor["blockers"])
    assert any("rejected" in item for item in rejected["blockers"])


def test_prompt_pack_uses_handoff_as_exact_first_frame_and_limits_motion(tmp_path):
    _clip, storyboard, _storyboard_path, plan, frame = _prepared(tmp_path)
    confirmed = _confirm(plan)

    pack = build_video_prompt_pack(
        storyboard,
        provider="veo",
        approved=True,
        characters=["same approved founder identity"],
        generation_chain_handoff_reports=[confirmed],
    )
    target = next(item for item in pack["items"] if item["shot_id"] == "shot_002")

    assert target["mode"] == "image_to_video"
    assert target["reference"]["resolved_path"] == str(frame)
    assert "EXACT CHAIN START from shot_001" in target["prompt"]
    assert "one primary subject action and one camera behavior" in target["prompt"]
    assert target["generation_chain_handoff"]["artifact_id"] == confirmed["artifact_id"]
    assert pack["summary"]["generation_chain_handoffs"] == 1


def test_prompt_pack_rejects_nonadjacent_or_incompatible_chain_mode(tmp_path):
    _clip, storyboard, _storyboard_path, plan, _frame = _prepared(tmp_path)
    confirmed = _confirm(plan)
    wrong = json.loads(json.dumps(confirmed))
    wrong["boundary"]["to_shot"] = "shot_999"

    try:
        build_video_prompt_pack(
            storyboard,
            provider="veo",
            approved=True,
            generation_chain_handoff_reports=[wrong],
        )
    except ValueError as exc:
        assert "boundary" in str(exc) or "artifact_id" in str(exc)
    else:
        raise AssertionError("non-adjacent chain handoff must be rejected")

    try:
        build_video_prompt_pack(
            storyboard,
            provider="veo",
            mode="reference_to_video",
            approved=True,
            generation_chain_handoff_reports=[confirmed],
        )
    except ValueError as exc:
        assert "mode=auto or image_to_video" in str(exc)
    else:
        raise AssertionError("exact-frame handoff must reject semantic-only mode")


def test_cli_prepare_confirm_verify_round_trip(tmp_path):
    _clip, _storyboard_data, _storyboard_path, _plan, _frame = _prepared(tmp_path)
    script = os.path.join(REPO, "scripts", "generation_chain_handoff.py")
    prepare = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--clip-review",
            "work/generated_clip_review.json",
            "--sequence-handoff",
            "work/sequence_handoff.json",
            "--boundary",
            "boundary_001",
            "--frame-output",
            "work/chain/boundary_001.png",
            "--output",
            "work/generation_chain_handoff.plan.json",
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr

    confirm = subprocess.run(
        [
            sys.executable,
            script,
            "confirm",
            "--project-dir",
            str(tmp_path),
            "--plan",
            "work/generation_chain_handoff.plan.json",
            "--output",
            "work/generation_chain_handoff.json",
            "--decision",
            "use_exact_start_frame",
            "--reviewed-by",
            "Jay",
            "--same-scene-confirmed",
            "--tail-frame-accepted",
            "--original-anchors-preserved",
            "--notes",
            "同一场景动作接力，保留原始身份锚点。",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    verify = subprocess.run(
        [
            sys.executable,
            script,
            "verify",
            "--project-dir",
            str(tmp_path),
            "--plan",
            "work/generation_chain_handoff.json",
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert confirm.returncode == 0, confirm.stderr
    assert verify.returncode == 0, verify.stderr
    assert "ready" in verify.stdout
