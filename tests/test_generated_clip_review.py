import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generated_clip_review as review  # noqa: E402


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


def _request(tmp_path, monkeypatch):
    clip = tmp_path / "work" / "generated_video" / "shot_001.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"generated-video")
    monkeypatch.setattr(review, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(review, "generate_contact_sheet", _fake_contact_sheet)
    request = review.prepare_request(
        [
            {
                "clip_id": "shot_001",
                "shot_id": "shot_001",
                "path": str(clip),
                "provider_route": "dreamina_video",
                "expected_beat": "host opens the door and reacts",
            }
        ],
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    return clip, request


def _response(request, **overrides):
    item = {
        "clip_id": "shot_001",
        "verdict": "pass",
        "story_readability": "clear",
        "scores": {
            "identity_wardrobe": 5,
            "action_end_state": 4,
            "motion_anatomy_physics": 4,
            "camera_behavior": 4,
            "frame_integrity": 5,
            "look_consistency": 4,
        },
        "hard_fail_codes": [],
        "keep_ranges": [],
        "remove_ranges": [],
        "regenerate": False,
        "prompt_fix": "",
        "notes": "Full-speed, slow, muted, and audio-only passes are clean.",
    }
    item.update(overrides)
    return {
        "version": review.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "visual-review-agent",
        "reviews": [item],
    }


def test_prepare_binds_clip_media_and_contact_sheet(tmp_path, monkeypatch):
    clip, request = _request(tmp_path, monkeypatch)

    assert request["version"] == review.REQUEST_VERSION
    assert request["clips"][0]["sha256"] == review._sha256(clip)
    assert request["clips"][0]["media"] == MEDIA
    assert request["clips"][0]["contact_sheet"]["sha256"]
    assert request["request_id"] == review._request_id(request)
    assert request["response_template"]["request_id"] == request["request_id"]
    assert "anatomy_or_physics_failure" in request["review_protocol"]["hard_fail_codes"]


def test_prepare_rejects_external_and_symlinked_clips(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside-generated.mp4"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(review, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(review, "generate_contact_sheet", _fake_contact_sheet)

    for path in (outside, tmp_path / "linked.mp4"):
        if path.name == "linked.mp4":
            path.symlink_to(outside)
        try:
            review.prepare_request(
                [{"clip_id": "shot_001", "path": str(path)}],
                project_dir=str(tmp_path),
                contact_sheet_dir="verify/generated_clips",
            )
        except ValueError as exc:
            assert "project directory" in str(exc) or "symlink" in str(exc)
        else:
            raise AssertionError("external or symlinked clips must be rejected")


def test_asset_manifest_selects_only_generated_video_items(tmp_path):
    generated = tmp_path / "work" / "generated_video" / "shot_002.mp4"
    broll = tmp_path / "work" / "broll" / "shot_003.mp4"
    generated.parent.mkdir(parents=True)
    broll.parent.mkdir(parents=True)
    generated.write_bytes(b"generated")
    broll.write_bytes(b"broll")
    manifest = tmp_path / "work" / "storyboard_assets.json"
    manifest.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "shot_id": "shot_002",
                        "route": "dreamina_video",
                        "kind": "video",
                        "resolved_path": str(generated),
                        "prompt": "one coherent reveal",
                    },
                    {
                        "shot_id": "shot_003",
                        "route": "media_library_broll",
                        "kind": "broll",
                        "resolved_path": str(broll),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    clips = review.clips_from_asset_manifest(str(manifest))

    assert clips == [
        {
            "clip_id": "shot_002",
            "path": str(generated),
            "shot_id": "shot_002",
            "expected_beat": "one coherent reveal",
            "provider_route": "dreamina_video",
        }
    ]


def test_ready_report_is_live_verifiable_and_detects_source_drift(tmp_path, monkeypatch):
    clip, request = _request(tmp_path, monkeypatch)
    report = review.build_report(request, _response(request))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert report["reviews"][0]["weighted_score"] == 88.0
    assert review.verify_report(report)["summary"]["blocking"] == 0

    clip.write_bytes(b"changed-generated-video")
    verification = review.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("clip bytes changed" in item for item in verification["blockers"])


def test_pass_with_edits_requires_complete_nonoverlapping_ranges(tmp_path, monkeypatch):
    _clip, request = _request(tmp_path, monkeypatch)
    response = _response(
        request,
        verdict="pass_with_edits",
        story_readability="partial",
        scores={key: 4 for key in review.SCORE_WEIGHTS},
        keep_ranges=[
            {"start": 0.0, "end": 1.0, "reason": "clean setup"},
            {"start": 2.0, "end": 4.0, "reason": "clean consequence"},
        ],
        remove_ranges=[{"start": 1.0, "end": 2.0, "reason": "repeated action"}],
        notes="Trim the repeated middle beat, then use the two approved ranges.",
    )

    report = review.build_report(request, response)

    assert report["status"] == "warn"
    assert report["summary"]["blocking"] == 0
    assert report["summary"]["warnings"] == 1

    response["reviews"][0]["keep_ranges"][1]["start"] = 2.5
    invalid = review.build_report(request, response)
    assert invalid["status"] == "blocked"
    assert any("cover the complete clip" in item for item in invalid["blockers"])


def test_hard_fail_overrides_high_score_and_requires_regeneration(tmp_path, monkeypatch):
    _clip, request = _request(tmp_path, monkeypatch)
    response = _response(
        request,
        verdict="fail",
        scores={key: 5 for key in review.SCORE_WEIGHTS},
        hard_fail_codes=["anatomy_or_physics_failure"],
        regenerate=True,
        prompt_fix="Split the door contact onto a cut and keep the person outside its travel path.",
        notes="The door passes through the subject at 2.4 seconds.",
    )

    report = review.build_report(request, response)

    assert report["reviews"][0]["weighted_score"] == 100.0
    assert report["status"] == "blocked"
    assert report["summary"]["fail"] == 1
    assert any("requires regeneration" in item for item in report["blockers"])


def test_tampered_stored_summary_is_rejected(tmp_path, monkeypatch):
    _clip, request = _request(tmp_path, monkeypatch)
    report = review.build_report(request, _response(request))
    report["summary"]["blocking"] = 0
    report["status"] = "warn"

    verification = review.verify_report(report)

    assert verification["status"] == "blocked"
    assert any("stored status" in item for item in verification["blockers"])
    assert any("report_id" in item for item in verification["blockers"])


def test_cli_real_ffmpeg_round_trip(tmp_path):
    clip = tmp_path / "work" / "generated_video" / "shot_001.mp4"
    clip.parent.mkdir(parents=True)
    make = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
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

    request_path = tmp_path / "work" / "generated_clip_review_request.json"
    response_path = tmp_path / "work" / "generated_clip_review_response.json"
    report_path = tmp_path / "work" / "generated_clip_review.json"
    prepare = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generated_clip_review.py"),
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--clip",
            f"shot_001={clip}",
            "--contact-sheet-dir",
            "verify/generated_clips",
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
    response_path.write_text(json.dumps(_response(request), ensure_ascii=False), encoding="utf-8")

    audit = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generated_clip_review.py"),
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
            os.path.join(REPO, "scripts", "generated_clip_review.py"),
            "verify",
            "--report",
            str(report_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["status"] == "ready"
