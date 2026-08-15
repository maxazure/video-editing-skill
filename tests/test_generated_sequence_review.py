import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generated_clip_review  # noqa: E402
import generated_sequence_review as sequence  # noqa: E402


MEDIA = {
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


def _fake_contact_sheet(_clip, output, **_kwargs):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"contact-sheet")
    return {
        "sample_fps": 2.0,
        "estimated_frames": 8,
        "columns": 8,
        "rows": 1,
        "thumb_width": 320,
    }


def _fake_boundary_evidence(_from, _to, output_dir, *, boundary_id, **_kwargs):
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {"canvas": {"width": 640, "height": 360, "fps": 24.0}}
    for key, suffix in (
        ("outgoing_frame", "outgoing.jpg"),
        ("incoming_frame", "incoming.jpg"),
        ("comparison", "comparison.jpg"),
        ("preview", "preview.mp4"),
    ):
        path = output_dir / f"{boundary_id}_{suffix}"
        path.write_bytes(f"{boundary_id}:{key}".encode("utf-8"))
        result[key] = path
    return result


def _clip_response(request):
    reviews = []
    for clip in request["clips"]:
        reviews.append(
            {
                "clip_id": clip["clip_id"],
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
        )
    return {
        "version": generated_clip_review.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "clip-review-agent",
        "reviews": reviews,
    }


def _ready_clip_review(tmp_path, monkeypatch, *, clips=2):
    monkeypatch.setattr(generated_clip_review, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(generated_clip_review, "generate_contact_sheet", _fake_contact_sheet)
    specs = []
    paths = []
    for index in range(clips):
        clip_id = f"shot_{index + 1:03d}"
        path = tmp_path / "work" / "generated_video" / f"{clip_id}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"generated-video-{index}".encode("utf-8"))
        paths.append(path)
        specs.append({"clip_id": clip_id, "shot_id": clip_id, "path": str(path)})
    request = generated_clip_review.prepare_request(
        specs,
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    report = generated_clip_review.build_report(request, _clip_response(request))
    report_path = tmp_path / "work" / "generated_clip_review.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return paths, report_path


def _storyboard(tmp_path):
    path = tmp_path / "work" / "storyboard_plan.json"
    path.write_text(
        json.dumps(
            {
                "version": "storyboard_plan.v1",
                "shots": [
                    {
                        "id": "shot_001",
                        "visual": {"last_frame": "Host holds the red cup at chest height."},
                        "continuity": {"anchors": ["navy jacket"]},
                    },
                    {
                        "id": "shot_002",
                        "visual": {"first_frame": "Host still holds the red cup at chest height."},
                        "continuity": {
                            "reuse_reference_from": "shot_001",
                            "anchors": ["navy jacket", "red cup"],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _sequence_request(tmp_path, monkeypatch):
    clips, clip_review = _ready_clip_review(tmp_path, monkeypatch)
    monkeypatch.setattr(sequence, "generate_boundary_evidence", _fake_boundary_evidence)
    request = sequence.prepare_request(
        str(clip_review),
        project_dir=str(tmp_path),
        evidence_dir="verify/generated_sequence",
        storyboard_plan_path=str(_storyboard(tmp_path)),
    )
    return clips, clip_review, request


def _sequence_response(request, **overrides):
    review = {
        "boundary_id": request["boundaries"][0]["boundary_id"],
        "verdict": "pass",
        "checks": {key: "match" for key in sequence.CHECK_KEYS},
        "failure_codes": [],
        "observed_transition": "The same host carries the red cup through a clean camera cut.",
        "repair_action": "",
        "notes": "Identity, wardrobe, cup state, screen direction, framing, and palette remain coherent.",
    }
    review.update(overrides)
    return {
        "version": sequence.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "sequence-review-agent",
        "reviews": [review],
    }


def test_prepare_binds_adjacent_frames_preview_and_storyboard(tmp_path, monkeypatch):
    _clips, _clip_review, request = _sequence_request(tmp_path, monkeypatch)

    assert request["version"] == sequence.REQUEST_VERSION
    assert request["clip_order"] == ["shot_001", "shot_002"]
    boundary = request["boundaries"][0]
    assert boundary["boundary_id"] == "shot_001__shot_002"
    assert boundary["storyboard_context"]["mode"] == "linked"
    assert boundary["storyboard_context"]["continuity_anchors"] == ["navy jacket", "red cup"]
    assert boundary["evidence"]["preview"]["sha256"]
    assert request["request_id"] == sequence._request_id(request)
    assert request["response_template"]["request_id"] == request["request_id"]


def test_prepare_requires_multiple_ready_clips(tmp_path, monkeypatch):
    _clips, clip_review = _ready_clip_review(tmp_path, monkeypatch, clips=1)
    monkeypatch.setattr(sequence, "generate_boundary_evidence", _fake_boundary_evidence)

    try:
        sequence.prepare_request(
            str(clip_review),
            project_dir=str(tmp_path),
            evidence_dir="verify/generated_sequence",
        )
    except ValueError as exc:
        assert "at least two" in str(exc)
    else:
        raise AssertionError("single-clip sequence review must be rejected")


def test_ready_report_is_live_verifiable_and_detects_clip_drift(tmp_path, monkeypatch):
    clips, _clip_review, request = _sequence_request(tmp_path, monkeypatch)
    report = sequence.build_report(request, _sequence_response(request))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert sequence.verify_report(report)["summary"]["blocking"] == 0

    clips[0].write_bytes(b"changed-generated-video")
    verification = sequence.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("clip bytes changed" in item or "clip review" in item for item in verification["blockers"])


def test_mismatch_requires_failure_code_and_blocks_assembly(tmp_path, monkeypatch):
    _clips, _clip_review, request = _sequence_request(tmp_path, monkeypatch)
    checks = {key: "match" for key in sequence.CHECK_KEYS}
    checks["prop_state"] = "mismatch"
    response = _sequence_response(
        request,
        verdict="fail",
        checks=checks,
        failure_codes=["prop_state_drift"],
        repair_action="Regenerate shot_002 from the accepted outgoing frame with the red cup locked.",
        notes="The cup changes from red ceramic to clear glass across the cut.",
    )

    report = sequence.build_report(request, response)

    assert report["status"] == "blocked"
    assert report["summary"]["fail"] == 1
    assert any("requires repair" in item for item in report["blockers"])

    response["reviews"][0]["failure_codes"] = []
    invalid = sequence.build_report(request, response)
    assert any("require at least one failure_code" in item for item in invalid["blockers"])


def test_intentional_change_passes_with_warning(tmp_path, monkeypatch):
    _clips, _clip_review, request = _sequence_request(tmp_path, monkeypatch)
    checks = {key: "match" for key in sequence.CHECK_KEYS}
    checks["camera_framing"] = "intentional_change"

    report = sequence.build_report(request, _sequence_response(request, checks=checks))

    assert report["status"] == "warn"
    assert report["summary"]["blocking"] == 0
    assert report["summary"]["warnings"] == 1


def test_evidence_or_stored_summary_tampering_is_rejected(tmp_path, monkeypatch):
    _clips, _clip_review, request = _sequence_request(tmp_path, monkeypatch)
    report = sequence.build_report(request, _sequence_response(request))
    preview = tmp_path / request["boundaries"][0]["evidence"]["preview"]["path"]
    preview.write_bytes(b"tampered-preview")

    evidence_check = sequence.verify_report(report)
    assert any("preview bytes changed" in item for item in evidence_check["blockers"])

    preview.write_bytes(b"shot_001__shot_002:preview")
    report["summary"]["blocking"] = 99
    stored_check = sequence.verify_report(report)
    assert any("stored summary" in item for item in stored_check["blockers"])
    assert any("report_id" in item for item in stored_check["blockers"])

    request["clips"][0]["approved_ranges"] = [{"start": 0.5, "end": 4.0}]
    request["request_id"] = sequence._request_id(request)
    request["response_template"] = sequence._response_template(request)
    contract_check = sequence.verify_request(request)
    assert any("clip contract does not match" in item for item in contract_check["blockers"])


def test_storyboard_must_cover_every_reviewed_generated_shot(tmp_path, monkeypatch):
    _clips, clip_review = _ready_clip_review(tmp_path, monkeypatch)
    storyboard = _storyboard(tmp_path)
    data = json.loads(storyboard.read_text(encoding="utf-8"))
    data["shots"] = data["shots"][:1]
    storyboard.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(sequence, "generate_boundary_evidence", _fake_boundary_evidence)

    try:
        sequence.prepare_request(
            str(clip_review),
            project_dir=str(tmp_path),
            evidence_dir="verify/generated_sequence",
            storyboard_plan_path=str(storyboard),
        )
    except ValueError as exc:
        assert "missing reviewed generated shots" in str(exc)
    else:
        raise AssertionError("incomplete storyboard ordering must be rejected")


def test_cli_real_ffmpeg_round_trip(tmp_path):
    clips = []
    for index, color in enumerate(("red", "blue"), start=1):
        clip = tmp_path / "work" / "generated_video" / f"shot_{index:03d}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        make = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:size=160x90:rate=24:duration=0.7",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=0.7",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            ],
            capture_output=True,
            text=True,
        )
        assert make.returncode == 0, make.stderr
        clips.append(clip)

    clip_request = generated_clip_review.prepare_request(
        [
            {"clip_id": "shot_001", "shot_id": "shot_001", "path": str(clips[0])},
            {"clip_id": "shot_002", "shot_id": "shot_002", "path": str(clips[1])},
        ],
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    clip_report_path = tmp_path / "work" / "generated_clip_review.json"
    clip_report_path.write_text(
        json.dumps(generated_clip_review.build_report(clip_request, _clip_response(clip_request))),
        encoding="utf-8",
    )
    request_path = tmp_path / "work" / "generated_sequence_review_request.json"
    response_path = tmp_path / "work" / "generated_sequence_review_response.json"
    report_path = tmp_path / "work" / "generated_sequence_review.json"
    prepare = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generated_sequence_review.py"),
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--clip-review",
            str(clip_report_path),
            "--evidence-dir",
            "verify/generated_sequence",
            "--preview-seconds",
            "0.25",
            "--output",
            str(request_path),
            "--response-template",
            str(response_path),
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    request = json.loads(request_path.read_text(encoding="utf-8"))
    response_path.write_text(json.dumps(_sequence_response(request)), encoding="utf-8")

    audit = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generated_sequence_review.py"),
            "audit",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--output",
            str(report_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 0, audit.stderr
    verify = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generated_sequence_review.py"),
            "verify",
            "--report",
            str(report_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert request["boundaries"][0]["evidence"]["comparison"]["size_bytes"] > 0
