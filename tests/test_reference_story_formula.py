import json
import os
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import reference_story_formula  # noqa: E402
from storyboard_plan import build_storyboard_plan  # noqa: E402


MEDIA = {
    "duration": 10.0,
    "fps": 24.0,
    "width": 1080,
    "height": 1920,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "has_audio": True,
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
}


def _reference_transcript():
    return {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "I assumed the label told the whole story."},
            {"id": 2, "start": 2.0, "end": 4.0, "text": "Then one scan exposed the hidden additives."},
            {"id": 3, "start": 4.0, "end": 6.0, "text": "The ordinary package suddenly felt risky."},
            {"id": 4, "start": 6.0, "end": 8.0, "text": "A clearer choice restored control."},
            {"id": 5, "start": 8.0, "end": 10.0, "text": "Check the next label before you buy."},
        ]
    }


def _target_storyboard():
    transcript = {
        "segments": [
            {"id": 1, "start": 0.0, "end": 3.0, "text": "Freelancers think every invoice is already covered."},
            {"id": 2, "start": 3.0, "end": 6.0, "text": "A tax estimate reveals the missing reserve."},
            {"id": 3, "start": 6.0, "end": 9.0, "text": "Set the reserve before the next payment arrives."},
        ]
    }
    return build_storyboard_plan(transcript, max_shots=3)


def _prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_story_formula, "probe_media", lambda _: dict(MEDIA))
    origin = tmp_path / "origin"
    work = tmp_path / "work"
    origin.mkdir()
    work.mkdir()
    video = origin / "reference.mp4"
    transcript = origin / "reference_transcript.json"
    storyboard = work / "storyboard_plan.json"
    video.write_bytes(b"reference-video")
    transcript.write_text(json.dumps(_reference_transcript()), encoding="utf-8")
    storyboard.write_text(json.dumps(_target_storyboard()), encoding="utf-8")
    request = reference_story_formula.build_request(
        root=tmp_path,
        reference_video=video,
        reference_transcript=transcript,
        target_storyboard=storyboard,
        beat_count=3,
        generated_at="2026-09-20T00:00:00Z",
    )
    request_path = work / "reference_story_formula_request.json"
    response_path = work / "reference_story_formula_response.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    response = json.loads(json.dumps(request["response_template"]))
    response_path.write_text(json.dumps(response), encoding="utf-8")
    return video, transcript, storyboard, request, response, request_path, response_path


def _approve(response):
    response["reviewed_by"] = "Jay"
    response["review_notes"] = "Reviewed against the complete reference and target storyboard."
    response["formula_name"] = "assumption to reveal to control"
    response["formula_summary"] = "Begin with a comfortable assumption, reveal a hidden risk, and resolve with a concrete action."
    for field in reference_story_formula.COPY_POLICY_FIELDS:
        response["copy_policy"][field] = True
    mechanisms = ["hook", "reveal", "payoff"]
    before = ["comfortable", "curious", "uneasy"]
    after = ["curious", "uneasy", "in control"]
    for index, beat in enumerate(response["formula_beats"]):
        beat["mechanism"] = mechanisms[index]
        beat["viewer_state_before"] = before[index]
        beat["viewer_state_after"] = after[index]
        beat["trigger"] = "a concrete contradiction appears"
        beat["camera_function"] = "move from familiar context to visible proof"
        beat["transferable_rule"] = "show evidence before explaining the corrective action"
        beat["do_not_copy"] = "package, scan gesture, wording, brand, and exact reveal"
        beat["decision"] = "approve"
        beat["review_note"] = "The beat is supported by its timecoded transcript evidence."
    for index, mapping in enumerate(response["shot_mappings"]):
        mapping["beat_id"] = f"beat_{index + 1:03d}"
        mapping["content_anchor"] = f"invoice reserve feature at target beat {index + 1}"
        mapping["viewer_shift"] = f"target audience shift {index + 1}"
        mapping["surface_change"] = "replace food packaging with a freelancer invoice workflow"
        mapping["visual_action"] = "show the target-specific evidence and one clear action"
        mapping["decision"] = "approve"
        mapping["review_note"] = "Specific to the target subject and safe to prompt."
    return response


def test_prepare_binds_reference_and_target_with_review_scaffold(tmp_path, monkeypatch):
    _, _, _, request, _, _, _ = _prepared(tmp_path, monkeypatch)

    assert request["version"] == reference_story_formula.REQUEST_VERSION
    assert request["request_id"].startswith("rsfr_")
    assert request["summary"] == {
        "reference_segments": 5,
        "formula_beats": 3,
        "target_shots": 3,
        "blocking": 0,
        "warnings": 0,
    }
    assert [row["evidence_segment_ids"] for row in request["response_template"]["formula_beats"]] == [
        ["1"],
        ["2", "3"],
        ["4", "5"],
    ]


def test_approved_formula_builds_ready_report_and_injectable_mapping(tmp_path, monkeypatch):
    _, _, _, request, response, request_path, response_path = _prepared(tmp_path, monkeypatch)
    _approve(response)
    response_path.write_text(json.dumps(response), encoding="utf-8")

    report = reference_story_formula.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
        generated_at="2026-09-20T00:10:00Z",
    )
    report_path = tmp_path / "work" / "reference_story_formula.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    verification = reference_story_formula.verify_report(str(report_path), project_dir=str(tmp_path))

    assert report["status"] == "ready"
    assert report["report_id"].startswith("rsf_")
    assert report["summary"]["blocking"] == 0
    assert [row["beat_id"] for row in report["target"]["shot_mappings"]] == [
        "beat_001",
        "beat_002",
        "beat_003",
    ]
    assert verification["status"] == "ready"


def test_missing_copy_policy_and_backward_mapping_fail_closed(tmp_path, monkeypatch):
    _, _, _, request, response, request_path, response_path = _prepared(tmp_path, monkeypatch)
    _approve(response)
    response["copy_policy"]["reference_words_excluded"] = False
    response["shot_mappings"][1]["beat_id"] = "beat_003"
    response["shot_mappings"][2]["beat_id"] = "beat_002"
    response_path.write_text(json.dumps(response), encoding="utf-8")

    report = reference_story_formula.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "blocked"
    assert "copy_policy reference_words_excluded must be true" in report["blockers"]
    assert any("moves backward" in item for item in report["blockers"])


def test_live_verify_detects_bound_transcript_drift(tmp_path, monkeypatch):
    _, transcript, _, request, response, request_path, response_path = _prepared(tmp_path, monkeypatch)
    _approve(response)
    response_path.write_text(json.dumps(response), encoding="utf-8")
    report = reference_story_formula.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    report_path = tmp_path / "work" / "reference_story_formula.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    transcript.write_text(transcript.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    verification = reference_story_formula.verify_report(str(report_path), project_dir=str(tmp_path))

    assert verification["status"] == "blocked"
    assert any("transcript" in item and "drifted" in item for item in verification["blockers"])


def test_long_reference_wording_overlap_is_visible_warning(tmp_path, monkeypatch):
    _, _, storyboard, request, response, request_path, response_path = _prepared(tmp_path, monkeypatch)
    target = json.loads(storyboard.read_text(encoding="utf-8"))
    target["shots"][0]["narration"] = "I assumed the label told the whole story before checking anything."
    storyboard.write_text(json.dumps(target), encoding="utf-8")
    request = reference_story_formula.build_request(
        root=tmp_path,
        reference_video=tmp_path / "origin" / "reference.mp4",
        reference_transcript=tmp_path / "origin" / "reference_transcript.json",
        target_storyboard=storyboard,
        beat_count=3,
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")
    response = _approve(json.loads(json.dumps(request["response_template"])))
    response_path.write_text(json.dumps(response), encoding="utf-8")

    report = reference_story_formula.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "review"
    assert report["summary"]["warnings"] == 1
    assert report["copy_review"]["possible_wording_reuse"][0]["shot_id"] == "shot_001"


def test_markdown_shows_formula_and_target_mapping(tmp_path, monkeypatch):
    _, _, _, request, response, request_path, response_path = _prepared(tmp_path, monkeypatch)
    request_md = reference_story_formula.emit_markdown(request)
    _approve(response)
    response_path.write_text(json.dumps(response), encoding="utf-8")
    report = reference_story_formula.build_report(
        root=tmp_path,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    report_md = reference_story_formula.emit_markdown(report)

    assert "# Reference Story Formula Request" in request_md
    assert "# Reference Story Formula Report" in report_md
    assert "assumption to reveal to control" in report_md
    assert "shot_001 → beat_001" in report_md
