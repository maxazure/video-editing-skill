import copy
import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generated_clip_review as clip_review  # noqa: E402
import generation_lessons as lessons  # noqa: E402


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
    return {"sample_fps": 2.0, "estimated_frames": 8, "columns": 8, "rows": 1, "thumb_width": 320}


def _failed_report(tmp_path, monkeypatch):
    clip = tmp_path / "work" / "generated_video" / "shot_001.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"generated-video")
    monkeypatch.setattr(clip_review, "probe_media", lambda _path: dict(MEDIA))
    monkeypatch.setattr(clip_review, "generate_contact_sheet", _fake_contact_sheet)
    request = clip_review.prepare_request(
        [{
            "clip_id": "shot_001",
            "path": str(clip),
            "provider_route": "dreamina_seedance",
            "expected_beat": "host opens a heavy door",
        }],
        project_dir=str(tmp_path),
        contact_sheet_dir="verify/generated_clips",
    )
    response = {
        "version": clip_review.RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "visual-review-agent",
        "reviews": [{
            "clip_id": "shot_001",
            "verdict": "fail",
            "story_readability": "partial",
            "scores": {key: 5 for key in clip_review.SCORE_WEIGHTS},
            "hard_fail_codes": ["anatomy_or_physics_failure"],
            "keep_ranges": [],
            "remove_ranges": [],
            "regenerate": True,
            "prompt_fix": "Split the hand contact into a separate shot and keep the hand visible until release.",
            "notes": "At 2.4 seconds the hand crosses through the door before the handle releases.",
        }],
    }
    return clip, clip_review.build_report(request, response)


def _entry(report, **overrides):
    values = {
        "clip_id": "shot_001",
        "category": "hand_contact",
        "lesson": "For hand-to-prop contact, isolate one interaction and keep the hand visible through release.",
        "approved_by": "Jay",
    }
    values.update(overrides)
    return lessons.build_entry(report, **values)


def _rescope(entry, *, provider=None, model=None, category=None, created_at=None):
    value = copy.deepcopy(entry)
    if provider is not None:
        value["scope"]["provider"] = provider
    if model is not None:
        value["scope"]["model"] = model
    if category is not None:
        value["scope"]["category"] = category
    if created_at is not None:
        value["created_at"] = created_at
    value["lesson_id"] = lessons._entry_id(value)
    return value


def test_failed_review_can_create_approved_source_bound_lesson(tmp_path, monkeypatch):
    _clip, report = _failed_report(tmp_path, monkeypatch)

    entry = _entry(report, model="seedance-2.0")

    assert entry["scope"] == {
        "provider": "dreamina_seedance",
        "model": "seedance-2.0",
        "category": "hand_contact",
    }
    assert entry["source"]["report_id"] == report["report_id"]
    assert entry["source"]["hard_fail_codes"] == ["anatomy_or_physics_failure"]
    assert entry["prompt_fix"].startswith("Split the hand contact")
    assert entry["lesson_id"] == lessons._entry_id(entry)


def test_lesson_capture_rejects_stale_review_source(tmp_path, monkeypatch):
    clip, report = _failed_report(tmp_path, monkeypatch)
    clip.write_bytes(b"changed-generated-video")

    try:
        _entry(report)
    except ValueError as exc:
        assert "clip bytes changed" in str(exc)
    else:
        raise AssertionError("stale generated-clip evidence must not enter the lesson library")


def test_library_append_verify_and_tamper_detection(tmp_path, monkeypatch):
    _clip, report = _failed_report(tmp_path, monkeypatch)
    entry = _entry(report)
    library = lessons.add_entry(lessons.new_library(), entry)

    assert lessons.verify_library(library)["status"] == "ready"
    assert library["summary"] == {
        "entries": 1,
        "providers": 1,
        "models": 1,
        "categories": 1,
        "superseded": 0,
    }

    tampered = copy.deepcopy(library)
    tampered["entries"][0]["lesson"] = "Use a different interaction."
    verification = lessons.verify_library(tampered)
    assert verification["status"] == "blocked"
    assert any("lesson_id" in item for item in verification["blockers"])
    assert any("library_id" in item for item in verification["blockers"])


def test_selection_respects_provider_model_category_and_specificity(tmp_path, monkeypatch):
    _clip, report = _failed_report(tmp_path, monkeypatch)
    base = _entry(report)
    global_entry = _rescope(base, provider="*", category="physics", created_at="2026-08-14T00:00:00Z")
    provider_entry = _rescope(base, created_at="2026-08-13T00:00:00Z")
    model_entry = _rescope(base, model="seedance-2.0", created_at="2026-08-15T00:00:00Z")
    library = lessons.new_library()
    for entry in (global_entry, provider_entry, model_entry):
        library = lessons.add_entry(library, entry)

    provider_wide = lessons.select_lessons(library, provider="dreamina_seedance", limit=10)
    explicit_provider_wide = lessons.select_lessons(
        library,
        provider="dreamina_seedance",
        model="*",
        limit=10,
    )
    assert [item["lesson_id"] for item in provider_wide] == [
        provider_entry["lesson_id"],
        global_entry["lesson_id"],
    ]
    assert explicit_provider_wide == provider_wide

    model_specific = lessons.select_lessons(
        library,
        provider="dreamina_seedance",
        model="seedance-2.0",
        categories=["hand_contact"],
        limit=10,
    )
    assert [item["lesson_id"] for item in model_specific] == [
        model_entry["lesson_id"],
        provider_entry["lesson_id"],
    ]


def test_new_lesson_can_supersede_old_rule_without_deleting_evidence(tmp_path, monkeypatch):
    _clip, report = _failed_report(tmp_path, monkeypatch)
    old = _entry(report)
    replacement = copy.deepcopy(old)
    replacement["created_at"] = "2026-08-16T00:00:00Z"
    replacement["lesson"] = (
        "For hand-to-prop contact, cut before contact and reveal the stable completed grip in the next shot."
    )
    replacement["supersedes"] = [old["lesson_id"]]
    replacement["lesson_id"] = lessons._entry_id(replacement)
    library = lessons.add_entry(lessons.new_library(), old)
    library = lessons.add_entry(library, replacement)

    selected = lessons.select_lessons(library, provider="dreamina_seedance", limit=10)

    assert [item["lesson_id"] for item in selected] == [replacement["lesson_id"]]
    assert library["summary"]["entries"] == 2
    assert library["summary"]["superseded"] == 1


def test_cli_verify_and_select_round_trip(tmp_path, monkeypatch):
    _clip, report = _failed_report(tmp_path, monkeypatch)
    library = lessons.add_entry(lessons.new_library(), _entry(report))
    library_path = tmp_path / "generation_lessons.json"
    library_path.write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")
    selection_path = tmp_path / "selected.json"
    markdown_path = tmp_path / "selected.md"

    verify = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generation_lessons.py"),
            "verify",
            "--library",
            str(library_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr

    select = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generation_lessons.py"),
            "select",
            "--library",
            str(library_path),
            "--provider",
            "dreamina_seedance",
            "--output",
            str(selection_path),
            "--markdown",
            str(markdown_path),
            "--require-match",
        ],
        capture_output=True,
        text=True,
    )
    assert select.returncode == 0, select.stderr
    assert json.loads(selection_path.read_text(encoding="utf-8"))["summary"]["selected"] == 1
    assert "hand_contact" in markdown_path.read_text(encoding="utf-8")


def test_verify_does_not_treat_a_missing_library_as_empty_and_ready(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generation_lessons.py"),
            "verify",
            "--library",
            str(tmp_path / "missing-generation-lessons.json"),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not exist" in result.stderr
