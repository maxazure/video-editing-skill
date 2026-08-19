import copy
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import final_audio_storyboard as fas  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_bytes(value if isinstance(value, bytes) else str(value).encode())


def _project(tmp_path, *, unmapped=False, gap=False, outside_source=None):
    source1 = tmp_path / "origin" / ("clip_one.mp4" if unmapped else "shot_001.mp4")
    source2 = tmp_path / "origin" / "shot_002.mp4"
    _write(source1, b"clip-one")
    _write(source2, b"clip-two")
    source_for_second = outside_source or source2
    storyboard = {
        "version": "storyboard_plan.v1",
        "shots": [
            {
                "id": "shot_001",
                "duration": 4.0,
                "narration": "第一句旁白",
                "visual": {"first_frame": "门打开", "motion": "向前推进", "last_frame": "人物回头"},
            },
            {
                "id": "shot_002",
                "duration": 3.0,
                "narration": "第二句旁白",
                "visual": {"first_frame": "城市远景", "motion": "横移", "last_frame": "灯光亮起"},
            },
            {
                "id": "shot_003",
                "duration": 2.0,
                "narration": "被删除的旁白",
                "visual": {"first_frame": "旧剧情", "motion": "静止", "last_frame": "旧剧情结束"},
            },
        ],
    }
    storyboard_path = tmp_path / "work" / "storyboard_plan.json"
    _write(storyboard_path, storyboard)
    second_start = 4.5 if gap else 4.0
    edl = {
        "kind": "nle_handoff_edl",
        "event_count": 2,
        "duration_seconds": second_start + 3.0,
        "events": [
            {
                "number": 1,
                "source": str(source1),
                "source_start": 0.0,
                "source_end": 4.0,
                "record_start": 0.0,
                "record_end": 4.0,
                "label": "clip_001" if unmapped else "shot_001",
            },
            {
                "number": 2,
                "source": str(source_for_second),
                "source_start": 1.0,
                "source_end": 4.0,
                "record_start": second_start,
                "record_end": second_start + 3.0,
                "label": "shot_002",
            },
        ],
    }
    edl_path = tmp_path / "work" / "locked_visual.edl.json"
    _write(edl_path, edl)
    return edl_path, storyboard_path, source1


def _response(request):
    response = copy.deepcopy(request["response_template"])
    response.update(
        {
            "reviewed_by": "editor-jay",
            "audio_strategy": "stems",
            "shared_tone": "克制、自然、同一室内声学空间",
            "review_notes": "按锁定画面重建，不沿用删除镜头的声音。",
        }
    )
    for index, section in enumerate(response["sections"], start=1):
        section["sound_design"] = "连续室内底噪与轻微动作 Foley"
        section["music"] = "低沉音乐床连续，不在切点重新起乐"
        section["stems"] = ["narration", "ambience", "foley", "music_like_bed"]
        section["decision_note"] = f"按最终第 {index} 段时长压缩旁白。"
    for omitted in response["omitted_story"]:
        omitted.update(
            {
                "disposition": "remove",
                "target_section_id": "",
                "note": "画面已删除，对应旁白、脚步与音乐转折一并删除。",
            }
        )
    return response


def _ready_report(tmp_path):
    edl_path, storyboard_path, source = _project(tmp_path)
    root = tmp_path.resolve()
    request = fas.build_request(root=root, edl_path=edl_path, storyboard_path=storyboard_path)
    response = _response(request)
    request_path = tmp_path / "work" / "final_audio_storyboard_request.json"
    response_path = tmp_path / "work" / "final_audio_storyboard_response.json"
    report_path = tmp_path / "work" / "final_audio_storyboard.json"
    _write(request_path, request)
    _write(response_path, response)
    report = fas.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )
    _write(report_path, report)
    return report, report_path, source, request, response, request_path, response_path


def test_ready_report_uses_final_timeline_and_voice_ledger(tmp_path):
    report, report_path, _source, _request, _response_data, _request_path, _response_path = _ready_report(tmp_path)

    assert report["status"] == "ready"
    assert report["timeline_duration"] == 7.0
    assert [row["final_start"] for row in report["sections"]] == [0.0, 4.0]
    assert [row["text"] for row in report["voice_ledger"]] == ["第一句旁白", "第二句旁白"]
    assert report["omitted_story"][0]["story_id"] == "shot_003"
    assert report["omitted_story"][0]["disposition"] == "remove"
    assert fas.verify_report(str(report_path), project_dir=str(tmp_path))["status"] == "ready"


def test_prepare_blocks_unmapped_edl_event_and_timeline_gap(tmp_path):
    edl_path, storyboard_path, _source = _project(tmp_path, unmapped=True, gap=True)
    request = fas.build_request(root=tmp_path.resolve(), edl_path=edl_path, storyboard_path=storyboard_path)

    assert request["summary"]["blocking"] == 2
    assert any("not mapped" in item for item in request["blockers"])
    assert any("gap" in item for item in request["blockers"])


def test_prepare_blocks_source_outside_project(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    _write(outside, b"outside")
    edl_path, storyboard_path, _source = _project(tmp_path, outside_source=outside)

    request = fas.build_request(root=tmp_path.resolve(), edl_path=edl_path, storyboard_path=storyboard_path)

    assert any("inside the project" in item for item in request["blockers"])


def test_audit_rejects_duplicate_voiced_line_and_immutable_time_change(tmp_path):
    edl_path, storyboard_path, _source = _project(tmp_path)
    root = tmp_path.resolve()
    request = fas.build_request(root=root, edl_path=edl_path, storyboard_path=storyboard_path)
    response = _response(request)
    response["sections"][1]["narration"] = response["sections"][0]["narration"]
    response["sections"][1]["final_start"] = 3.5
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    _write(request_path, request)
    _write(response_path, response)

    report = fas.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "blocked"
    assert any("duplicated" in item for item in report["blockers"])
    assert any("immutable field final_start" in item for item in report["blockers"])


def test_audit_requires_explicit_omitted_story_decision(tmp_path):
    edl_path, storyboard_path, _source = _project(tmp_path)
    root = tmp_path.resolve()
    request = fas.build_request(root=root, edl_path=edl_path, storyboard_path=storyboard_path)
    response = _response(request)
    response["omitted_story"] = []
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    _write(request_path, request)
    _write(response_path, response)

    report = fas.build_report(
        root=root,
        request=request,
        response=response,
        request_path=request_path,
        response_path=response_path,
    )

    assert report["status"] == "blocked"
    assert any("coverage" in item for item in report["blockers"])


def test_live_verify_detects_source_and_report_drift(tmp_path):
    report, report_path, source, _request, _response_data, _request_path, _response_path = _ready_report(tmp_path)
    source.write_bytes(b"changed-source")

    stale = fas.verify_report(str(report_path), project_dir=str(tmp_path))

    assert stale["status"] == "blocked"
    assert any("drifted" in item for item in stale["verification_errors"])

    source.write_bytes(b"clip-one")
    tampered = dict(report)
    tampered["shared_tone"] = "tampered"
    _write(report_path, tampered)
    invalid = fas.verify_report(str(report_path), project_dir=str(tmp_path))
    assert invalid["status"] == "blocked"
    assert any("derived fields" in item for item in invalid["verification_errors"])


def test_cli_prepare_audit_verify_round_trip(tmp_path):
    edl_path, storyboard_path, _source = _project(tmp_path)
    request_path = tmp_path / "work" / "request.json"
    response_path = tmp_path / "work" / "response.json"
    report_path = tmp_path / "work" / "final_audio_storyboard.json"
    script = os.path.join(REPO, "scripts", "final_audio_storyboard.py")
    prepare = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--edl",
            str(edl_path),
            "--storyboard",
            str(storyboard_path),
            "--output",
            str(request_path),
            "--response-template",
            str(response_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    request = json.loads(request_path.read_text(encoding="utf-8"))
    _write(response_path, _response(request))
    audit = subprocess.run(
        [
            sys.executable,
            script,
            "audit",
            "--project-dir",
            str(tmp_path),
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
            script,
            "verify",
            "--project-dir",
            str(tmp_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr


def test_pipeline_manifest_live_verifies_final_audio_storyboard(tmp_path):
    _report, _report_path, source, _request, _response_data, _request_path, _response_path = _ready_report(tmp_path)

    ready = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(item for item in ready["gates"] if item["category"] == "final_audio_storyboard")
    assert gate["status"] == "ready"

    source.write_bytes(b"changed")
    stale = build_manifest(str(tmp_path), target_stage="publish_ready")
    gate = next(item for item in stale["gates"] if item["category"] == "final_audio_storyboard")
    assert gate["status"] == "blocked"
    assert "final_audio_storyboard" in stale["blocked_gates"]
