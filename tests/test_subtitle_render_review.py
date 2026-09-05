import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import subtitle_render_review as render_review  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402
from subtitle_render_review import (  # noqa: E402
    audit_response,
    normalize_cues,
    prepare_request,
    select_review_cues,
    verify_report,
    verify_request,
)


SOURCE_MEDIA = {
    "duration": 12.0,
    "fps": 24.0,
    "width": 320,
    "height": 180,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "has_audio": True,
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 1,
}
STILL_MEDIA = {
    "width": 320,
    "height": 180,
    "codec": "mjpeg",
    "pixel_format": "yuvj420p",
}


def _write_pack(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [
                    {"index": 1, "start": 0.5, "end": 1.5, "text": "开场"},
                    {"index": 2, "start": 2.0, "end": 3.4, "text": "第二句字幕"},
                    {"index": 3, "start": 4.0, "end": 4.4, "text": "最快字幕内容很多"},
                    {"index": 4, "start": 5.5, "end": 7.5, "text": "这是全片最长的一条字幕内容"},
                    {"index": 5, "start": 8.0, "end": 9.0, "text": "中后段"},
                    {"index": 6, "start": 10.0, "end": 11.0, "text": "结尾"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _install_media_stubs(monkeypatch):
    media_by_path = {}
    still_by_path = {}

    def fake_probe(path):
        return dict(media_by_path.get(str(Path(path).resolve()), SOURCE_MEDIA))

    def fake_still(path):
        return dict(still_by_path.get(str(Path(path).resolve()), STILL_MEDIA))

    def fake_render(
        source,
        clip_output,
        frame_output,
        *,
        proof_start,
        proof_duration,
        midpoint,
        force,
    ):
        clip_output.parent.mkdir(parents=True, exist_ok=True)
        clip_output.write_bytes(f"clip:{proof_start}:{proof_duration}".encode("utf-8"))
        frame_output.write_bytes(f"frame:{midpoint}".encode("utf-8"))
        clip_media = {**SOURCE_MEDIA, "duration": round(proof_duration, 6)}
        media_by_path[str(clip_output.resolve())] = clip_media
        still_by_path[str(frame_output.resolve())] = dict(STILL_MEDIA)
        return dict(clip_media), dict(STILL_MEDIA)

    monkeypatch.setattr(render_review, "probe_media", fake_probe)
    monkeypatch.setattr(render_review, "_still_signature", fake_still)
    monkeypatch.setattr(render_review, "render_evidence", fake_render)
    return media_by_path, still_by_path


def _request(tmp_path, monkeypatch, *, max_samples=5):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"final-video")
    pack = tmp_path / "output" / "subtitles" / "final.json"
    _write_pack(pack)
    _install_media_stubs(monkeypatch)
    request = prepare_request(
        project_dir=str(tmp_path),
        video_path="output/final.mp4",
        subtitle_pack_path="output/subtitles/final.json",
        proof_dir="verify/subtitle_render",
        max_samples=max_samples,
    )
    return request, source, pack


def _passing_response(request):
    response = deepcopy(request["response_template"])
    response["reviewed_by"] = "editor-a"
    response["full_playback"] = "completed"
    response["full_video_verdict"] = "pass"
    response["full_video_notes"] = "Watched the exact final file from start to finish at 1x."
    for review in response["reviews"]:
        review.update(
            {
                "verdict": "pass",
                "caption_presence": "visible",
                "text_match": "matches",
                "readability": "readable",
                "layout": "clear",
                "repair_action": "none",
                "notes": "Checked context clip and midpoint frame.",
            }
        )
    return response


def test_selection_is_deterministic_and_covers_high_risk_cues():
    pack = {
        "cues": [
            {"index": 1, "start": 0, "end": 1, "text": "first"},
            {"index": 2, "start": 2, "end": 3, "text": "middle"},
            {"index": 3, "start": 4, "end": 4.2, "text": "very fast subtitle"},
            {"index": 4, "start": 6, "end": 8, "text": "the longest subtitle in this fixture"},
            {"index": 5, "start": 9, "end": 10, "text": "last"},
        ]
    }
    cues = normalize_cues(pack, media_duration=10.0)
    selected = select_review_cues(cues, max_samples=5)

    assert [sample["sample_id"] for sample in selected] == [
        "sample-001",
        "sample-002",
        "sample-003",
        "sample-004",
        "sample-005",
    ]
    reasons = {reason for sample in selected for reason in sample["selection_reasons"]}
    assert {"first_cue", "last_cue", "longest_text", "highest_cps", "shortest_duration"} <= reasons


def test_explicit_selection_is_preserved_and_unknown_ids_fail():
    cues = normalize_cues(
        {"cues": [{"index": 1, "start": 0, "end": 1, "text": "one"}]},
        media_duration=1.0,
    )
    selected = select_review_cues(cues, max_samples=1, explicit_cue_ids=["1"])
    assert "explicit" in selected[0]["selection_reasons"]
    with pytest.raises(ValueError, match="unknown explicit cue ids"):
        select_review_cues(cues, max_samples=1, explicit_cue_ids=["missing"])


def test_prepare_binds_final_video_subtitles_and_pixel_evidence(tmp_path, monkeypatch):
    request, _, _ = _request(tmp_path, monkeypatch)

    assert request["version"] == "subtitle_render_review_request.v1"
    assert request["source"]["path"] == "output/final.mp4"
    assert request["subtitle_pack"]["path"] == "output/subtitles/final.json"
    assert request["cue_count"] == 6
    assert len(request["samples"]) == 5
    for sample in request["samples"]:
        assert sample["evidence"]["context_clip"]["sha256"]
        assert sample["evidence"]["midpoint_frame"]["sha256"]
    assert request["response_template"]["request_id"] == request["request_id"]
    assert verify_request(request)["status"] == "ready"


def test_prepare_rejects_duplicate_or_out_of_range_cues(tmp_path, monkeypatch):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final")
    pack = tmp_path / "subtitles.json"
    pack.write_text(
        json.dumps(
            {
                "version": "subtitle_pack.v1",
                "cues": [
                    {"index": 1, "start": 0, "end": 1, "text": "one"},
                    {"index": 1, "start": 11, "end": 13, "text": "duplicate"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _install_media_stubs(monkeypatch)

    with pytest.raises(ValueError, match="duplicated or empty"):
        prepare_request(
            project_dir=str(tmp_path),
            video_path="final.mp4",
            subtitle_pack_path="subtitles.json",
            proof_dir="verify/subtitle_render",
        )


def test_audit_passes_only_with_full_playback_and_all_samples(tmp_path, monkeypatch):
    request, _, _ = _request(tmp_path, monkeypatch)
    report = audit_response(request, _passing_response(request))

    assert report["status"] == "ready"
    assert report["summary"] == {
        "samples": 5,
        "passed": 5,
        "failed": 0,
        "full_playback_completed": True,
        "blocking": 0,
        "warnings": 0,
    }
    assert verify_report(report)["status"] == "ready"


def test_audit_fails_closed_on_missing_caption_or_skipped_playback(tmp_path, monkeypatch):
    request, _, _ = _request(tmp_path, monkeypatch)
    response = _passing_response(request)
    response["full_playback"] = "not_completed"
    response["reviews"][0].update(
        {
            "verdict": "fail",
            "caption_presence": "missing",
            "repair_action": "none",
            "notes": "Caption is absent from the final pixels.",
        }
    )

    report = audit_response(request, response)

    assert report["status"] == "blocked"
    assert any("full_playback must be completed" in item for item in report["blockers"])
    assert any("concrete repair_action" in item for item in report["blockers"])


def test_live_verify_detects_video_subtitle_evidence_and_report_drift(tmp_path, monkeypatch):
    request, source, pack = _request(tmp_path, monkeypatch)
    report = audit_response(request, _passing_response(request))

    source.write_bytes(b"changed-video")
    live = verify_report(report)
    assert live["status"] == "blocked"
    assert any("canonical audit state" in item for item in live["blockers"])

    source.write_bytes(b"final-video")
    pack.write_text(pack.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    subtitle_drift = verify_report(report)
    assert subtitle_drift["status"] == "blocked"

    _write_pack(pack)
    evidence_path = tmp_path / request["samples"][0]["evidence"]["midpoint_frame"]["path"]
    evidence_path.write_bytes(b"changed-frame")
    evidence_drift = verify_report(report)
    assert evidence_drift["status"] == "blocked"

    tampered = deepcopy(report)
    tampered["summary"]["passed"] = 0
    tampered["report_id"] = render_review._report_id(tampered)
    assert any("report summary" in item for item in verify_report(tampered)["blockers"])


def test_live_verify_rejects_boolean_sampling_values(tmp_path, monkeypatch):
    request, _, _ = _request(tmp_path, monkeypatch)
    request["sampling"]["max_samples"] = True
    request["request_id"] = render_review._request_id(request)
    request["response_template"] = render_review._response_template(request)

    verification = verify_request(request)

    assert verification["status"] == "blocked"
    assert any("max_samples must be an integer" in item for item in verification["blockers"])


def test_pipeline_manifest_live_verifies_subtitle_render_review(tmp_path, monkeypatch):
    request, _, _ = _request(tmp_path, monkeypatch, max_samples=2)
    report = audit_response(request, _passing_response(request))
    report_path = tmp_path / "verify" / "subtitle_render_review.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "work" / "transcript.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8"
    )

    manifest = build_manifest(
        str(tmp_path), target_stage="analysis", required=["subtitle_render_review"]
    )

    gate = next(
        gate for gate in manifest["gates"] if gate["category"] == "subtitle_render_review"
    )
    assert gate["status"] == "ready"


def test_main_writes_request_and_response_template(tmp_path, monkeypatch):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"final-video")
    pack = tmp_path / "output" / "subtitles" / "final.json"
    _write_pack(pack)
    _install_media_stubs(monkeypatch)

    result = render_review.main(
        [
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--video",
            "output/final.mp4",
            "--subtitle-pack",
            "output/subtitles/final.json",
            "--max-samples",
            "3",
        ]
    )

    assert result == 0
    assert (tmp_path / "work" / "subtitle_render_review_request.json").exists()
    assert (tmp_path / "work" / "subtitle_render_review_response.json").exists()
