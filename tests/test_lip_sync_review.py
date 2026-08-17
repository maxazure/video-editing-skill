import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import lip_sync_review  # noqa: E402
from lip_sync_review import (  # noqa: E402
    audit_response,
    parse_segment_specs,
    prepare_request,
    verify_report,
    verify_request,
)
from pipeline_manifest import build_manifest  # noqa: E402


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


def _install_media_stubs(monkeypatch, root: Path):
    def fake_probe(path):
        name = Path(path).name
        if name.endswith("_025x_silent.mp4"):
            return {**SOURCE_MEDIA, "duration": 16.8, "has_audio": False, "audio_codec": "", "sample_rate": 0, "channels": 0}
        if name.endswith("_1x.mp4"):
            return {**SOURCE_MEDIA, "duration": 4.2}
        return dict(SOURCE_MEDIA)

    def fake_render(source, output, *, start, duration, slow, force):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(("slow" if slow else "normal").encode("utf-8") + output.name.encode("utf-8"))
        return fake_probe(str(output))

    monkeypatch.setattr(lip_sync_review, "probe_media", fake_probe)
    monkeypatch.setattr(lip_sync_review, "render_proof", fake_render)


def _request(tmp_path, monkeypatch):
    source = tmp_path / "output" / "final.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"final-master")
    _install_media_stubs(monkeypatch, tmp_path)
    request = prepare_request(
        project_dir=str(tmp_path),
        video_path="output/final.mp4",
        segments=[
            {
                "segment_id": "hook",
                "start": 2.0,
                "end": 5.5,
                "anchor_text": "把重点说清楚",
                "speaker": "avatar-a",
            }
        ],
        proof_dir="verify/lip_sync",
    )
    return request, source


def _passing_response(request):
    response = deepcopy(request["response_template"])
    response["reviewed_by"] = "editor-a"
    response["reviews"][0].update(
        {
            "verdict": "pass",
            "plosive_closures": "aligned",
            "vowel_timing": "aligned",
            "frozen_mouth": "absent",
            "speaker_assignment": "correct",
            "audio_quality": "clean",
            "repair_action": "none",
            "notes": "Checked twice at 1x and once at 0.25x.",
        }
    )
    return response


def test_parse_segment_specs_requires_matching_anchor():
    assert parse_segment_specs(["hook=1.25:3.5"], ["hook=把重点说清楚"]) == [
        {
            "segment_id": "hook",
            "start": 1.25,
            "end": 3.5,
            "anchor_text": "把重点说清楚",
        }
    ]
    with pytest.raises(ValueError, match="missing --anchor"):
        parse_segment_specs(["hook=1:3"], [])


def test_prepare_binds_final_master_and_both_proofs(tmp_path, monkeypatch):
    request, _ = _request(tmp_path, monkeypatch)

    assert request["version"] == "lip_sync_review_request.v1"
    assert request["source"]["path"] == "output/final.mp4"
    assert request["source"]["sha256"]
    segment = request["segments"][0]
    assert segment["proof_start"] == 1.65
    assert segment["proof_end"] == 5.85
    assert segment["proofs"]["normal_speed"]["media"]["has_audio"] is True
    assert segment["proofs"]["quarter_speed_silent"]["media"]["has_audio"] is False
    assert request["response_template"]["request_id"] == request["request_id"]
    assert verify_request(request)["status"] == "ready"


def test_prepare_rejects_invalid_phrase_range(tmp_path, monkeypatch):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"final-master")
    _install_media_stubs(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="between 1 and 10 seconds"):
        prepare_request(
            project_dir=str(tmp_path),
            video_path="final.mp4",
            segments=[{"segment_id": "tiny", "start": 1, "end": 1.2, "anchor_text": "pa"}],
            proof_dir="verify/lip_sync",
        )


def test_audit_pass_and_live_verify(tmp_path, monkeypatch):
    request, _ = _request(tmp_path, monkeypatch)
    report = audit_response(request, _passing_response(request))

    assert report["status"] == "ready"
    assert report["summary"] == {
        "segments": 1,
        "passed": 1,
        "failed": 0,
        "blocking": 0,
        "warnings": 0,
    }
    assert verify_report(report)["status"] == "ready"


def test_audit_fails_closed_on_unobservable_or_missing_repair(tmp_path, monkeypatch):
    request, _ = _request(tmp_path, monkeypatch)
    response = _passing_response(request)
    response["reviews"][0].update(
        {
            "verdict": "fail",
            "plosive_closures": "not_observable",
            "repair_action": "none",
            "notes": "Mouth is covered by a caption.",
        }
    )

    report = audit_response(request, response)

    assert report["status"] == "blocked"
    assert report["summary"]["blocking"] >= 1
    assert any("concrete repair_action" in item for item in report["blockers"])


def test_verify_detects_master_drift_and_report_tampering(tmp_path, monkeypatch):
    request, source = _request(tmp_path, monkeypatch)
    report = audit_response(request, _passing_response(request))
    source.write_bytes(b"changed-master")

    live = verify_report(report)
    assert live["status"] == "blocked"
    assert any("canonical audit state" in item for item in live["blockers"])

    tampered = deepcopy(report)
    tampered["summary"]["passed"] = 0
    tampered["report_id"] = lip_sync_review._report_id(tampered)
    tampered_result = verify_report(tampered)
    assert any("report summary" in item for item in tampered_result["blockers"])

    malformed = deepcopy(report)
    malformed["request"]["segments"][0]["proofs"]["normal_speed"]["playback_speed"] = {"bad": 1}
    malformed["report_id"] = lip_sync_review._report_id(malformed)
    malformed_result = verify_report(malformed)
    assert malformed_result["status"] == "blocked"
    assert any("cannot be re-audited" in item for item in malformed_result["blockers"])


def test_pipeline_manifest_uses_live_lip_sync_verification(tmp_path, monkeypatch):
    request, _ = _request(tmp_path, monkeypatch)
    report = audit_response(request, _passing_response(request))
    report_path = tmp_path / "work" / "lip_sync_review.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "work" / "transcript.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8"
    )

    manifest = build_manifest(
        str(tmp_path), target_stage="analysis", required=["lip_sync_review"]
    )

    assert manifest["status"] == "ready"
    gate = next(gate for gate in manifest["gates"] if gate["category"] == "lip_sync_review")
    assert gate["status"] == "ready"
