import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import interlace_conform as conform  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


SOURCE_MEDIA = {
    "duration": 4.0,
    "video_duration": 4.0,
    "audio_duration": 4.0,
    "avg_frame_rate": "30000/1001",
    "r_frame_rate": "30000/1001",
    "avg_fps": 29.97002997,
    "nominal_fps": 29.97002997,
    "field_order": "tb",
    "width": 640,
    "height": 360,
    "rotation": 0,
    "video_start_time": 0.0,
    "audio_start_time": 0.0,
    "has_audio": True,
    "video_codec": "h264",
    "audio_codec": "aac",
    "sample_rate": 48000,
    "channels": 2,
    "pixel_format": "yuv420p",
    "bit_depth": 8,
    "sample_aspect_ratio": "1:1",
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "format_names": ["matroska", "webm"],
}

INTERLACED_ANALYSIS = {
    "algorithm": conform.DETECTION_ALGORITHM,
    "sample_seconds": 8.0,
    "windows": [
        {
            "start": 0.0,
            "duration": 4.0,
            "counts": {
                "repeated": {"neither": 120, "top": 0, "bottom": 0},
                "single": {"tff": 118, "bff": 2, "progressive": 0, "undetermined": 0},
                "multiple": {"tff": 120, "bff": 0, "progressive": 0, "undetermined": 0},
            },
        }
    ],
    "counts": {
        "repeated": {"neither": 120, "top": 0, "bottom": 0},
        "single": {"tff": 118, "bff": 2, "progressive": 0, "undetermined": 0},
        "multiple": {"tff": 120, "bff": 0, "progressive": 0, "undetermined": 0},
    },
    "classification": "interlaced_tff",
    "confidence": 1.0,
    "ratios": {
        "repeated_fields": 0.0,
        "interlaced_frames": 1.0,
        "progressive_frames": 0.0,
        "parity_dominance": 1.0,
    },
    "reasons": ["idet reports a dominant field order across most sampled frames"],
    "limitations": [
        "idet is a heuristic sample, not a proof of source capture history or telecine cadence.",
        "Mixed edits, animation, static frames, bad stream flags, or cadence breaks can require frame-by-frame review.",
        "A telecine candidate must use an IVTC-specific workflow; this script deliberately does not deinterlace it.",
    ],
}

PROGRESSIVE_ANALYSIS = {
    **INTERLACED_ANALYSIS,
    "counts": {
        "repeated": {"neither": 120, "top": 0, "bottom": 0},
        "single": {"tff": 51, "bff": 49, "progressive": 20, "undetermined": 0},
        "multiple": {"tff": 50, "bff": 50, "progressive": 20, "undetermined": 0},
    },
    "classification": "progressive",
    "confidence": 0.8,
    "ratios": {
        "repeated_fields": 0.0,
        "interlaced_frames": 0.833333,
        "progressive_frames": 0.166667,
        "parity_dominance": 0.5,
    },
    "reasons": ["stream flags and sampled field evidence are consistent with progressive video"],
}

OUTPUT_MEDIA = {
    **SOURCE_MEDIA,
    "avg_frame_rate": "30000/1001",
    "r_frame_rate": "30000/1001",
    "field_order": "progressive",
    "format_names": ["mov", "mp4"],
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
}

COMPARISON_MEDIA = {
    **OUTPUT_MEDIA,
    "width": 1280,
    "height": 360,
}


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "origin" / "broadcast.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"interlaced-source")
    return source


def _patch_detection(monkeypatch, source: Path, analysis=INTERLACED_ANALYSIS):
    monkeypatch.setattr(conform, "probe_media", lambda _path: dict(SOURCE_MEDIA))
    monkeypatch.setattr(conform, "analyze_interlace", lambda _path, _media, sample_seconds=8.0: {**analysis, "sample_seconds": sample_seconds})
    monkeypatch.setattr(conform, "_available_filters", lambda: {"idet", "bwdif", "yadif"})
    monkeypatch.setattr(conform, "_ffmpeg_version", lambda: "ffmpeg version test")


def _plan(tmp_path: Path, monkeypatch, *, mode="frame"):
    source = _source(tmp_path)
    _patch_detection(monkeypatch, source)
    plan = conform.build_plan(
        "origin/broadcast.mkv",
        "work/source-progressive.mp4",
        "verify/interlace-compare.mp4",
        project_dir=str(tmp_path),
        mode=mode,
        reviewed_by="editor",
        note="motion combing is continuous and field order is TFF",
    )
    return source, plan


def test_parse_idet_and_classify_true_interlace_telecine_and_progressive():
    text = """
    Repeated Fields: Neither: 240 Top: 0 Bottom: 0
    Single frame detection: TFF: 238 BFF: 2 Progressive: 0 Undetermined: 0
    Multi frame detection: TFF: 240 BFF: 0 Progressive: 0 Undetermined: 0
    """
    counts = conform._parse_idet_summary(text)
    true_interlace = conform._classify_idet(counts, SOURCE_MEDIA)
    assert true_interlace["classification"] == "interlaced_tff"

    bff_counts = {
        **counts,
        "multiple": {"tff": 0, "bff": 240, "progressive": 0, "undetermined": 0},
    }
    assert conform._classify_idet(bff_counts, SOURCE_MEDIA)["classification"] == "interlaced_bff"

    telecine_counts = {
        **counts,
        "repeated": {"neither": 144, "top": 48, "bottom": 48},
    }
    telecine = conform._classify_idet(telecine_counts, SOURCE_MEDIA)
    assert telecine["classification"] == "telecine_candidate"

    progressive_counts = {
        **counts,
        "multiple": {"tff": 93, "bff": 86, "progressive": 13, "undetermined": 0},
    }
    progressive = conform._classify_idet(progressive_counts, {**SOURCE_MEDIA, "field_order": "progressive", "avg_fps": 23.976})
    assert progressive["classification"] == "progressive"


def test_sample_windows_cover_head_middle_and_tail_without_duplicates():
    assert conform._sample_windows(4.0, 8.0) == [{"start": 0.0, "duration": 4.0}]
    windows = conform._sample_windows(100.0, 8.0)
    assert windows == [
        {"start": 0.0, "duration": 8.0},
        {"start": 46.0, "duration": 8.0},
        {"start": 92.0, "duration": 8.0},
    ]


def test_plan_binds_detection_backend_parity_and_pending_lifecycle(tmp_path, monkeypatch):
    _source_path, plan = _plan(tmp_path, monkeypatch)

    assert plan["version"] == conform.VERSION
    assert plan["source"]["interlace_analysis"]["effective_classification"] == "interlaced_tff"
    assert plan["settings"]["backend"] == "bwdif"
    assert plan["settings"]["resolved_parity"] == "tff"
    assert plan["settings"]["output_rate"]["rational"] == "30000/1001"
    assert plan["blockers"] == [conform.PENDING_APPLY]
    assert plan["status"] == "blocked"


def test_field_mode_doubles_exact_rate_and_yadif_is_declared_fallback(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_detection(monkeypatch, source)
    monkeypatch.setattr(conform, "_available_filters", lambda: {"idet", "yadif"})
    plan = conform.build_plan(
        str(source),
        "work/progressive.mp4",
        "verify/compare.mp4",
        project_dir=str(tmp_path),
        mode="field",
        reviewed_by="editor",
        note="preserve field-time motion",
    )

    assert plan["settings"]["backend"] == "yadif"
    assert plan["settings"]["output_rate"]["rational"] == "60000/1001"
    assert "send_field" in plan["settings"]["video_filter"]
    assert plan["summary"]["warnings"] == 2


def test_telecine_and_progressive_sources_are_refused(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_detection(monkeypatch, source, {**INTERLACED_ANALYSIS, "classification": "telecine_candidate"})
    with pytest.raises(ValueError, match="inverse-telecine"):
        conform.build_plan(
            str(source), "work/out.mp4", "verify/compare.mp4",
            project_dir=str(tmp_path), reviewed_by="editor", note="telecine evidence",
        )

    _patch_detection(monkeypatch, source, PROGRESSIVE_ANALYSIS)
    with pytest.raises(ValueError, match="progressive"):
        conform.build_plan(
            str(source), "work/out.mp4", "verify/compare.mp4",
            project_dir=str(tmp_path), reviewed_by="editor", note="progressive evidence",
        )


def test_verify_detects_source_and_settings_drift(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    source.write_bytes(b"changed-source")
    assert any("source" in item and "changed" in item for item in conform.verify_plan(plan)["blockers"])

    source.write_bytes(b"interlaced-source")
    plan["settings"]["video_crf"] = 30
    plan["plan_id"] = conform._plan_id(plan)
    assert any("settings do not match" in item for item in conform.verify_plan(plan)["blockers"])


def test_apply_then_confirm_produces_live_ready_gate(tmp_path, monkeypatch):
    source, plan = _plan(tmp_path, monkeypatch)
    plan_path = tmp_path / "work" / "interlace_conform_plan.json"
    conform._atomic_write_json(plan_path, plan)
    commands = []

    def fake_run(command):
        command = list(command)
        commands.append(command)
        if command[-1] != "-" and ("-vf" in command or "-filter_complex" in command):
            Path(command[-1]).write_bytes(b"comparison" if "-filter_complex" in command else b"progressive-output")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_output_info(path, *, sample_seconds):
        return {
            **conform._fingerprint(Path(path)),
            **OUTPUT_MEDIA,
            "interlace_analysis": {**PROGRESSIVE_ANALYSIS, "sample_seconds": sample_seconds},
        }

    monkeypatch.setattr(conform, "_run_command", fake_run)
    monkeypatch.setattr(conform, "_output_info", fake_output_info)
    monkeypatch.setattr(conform, "probe_media", lambda path: dict(COMPARISON_MEDIA if "compare" in Path(path).name else SOURCE_MEDIA))

    applied = conform.apply_plan(str(plan_path))
    assert applied["summary"]["blocking"] == 1
    assert applied["blockers"] == [conform.PENDING_CONFIRM]
    assert (tmp_path / "work" / "source-progressive.mp4").read_bytes() == b"progressive-output"
    assert (tmp_path / "verify" / "interlace-compare.mp4").read_bytes() == b"comparison"
    assert source.read_bytes() == b"interlaced-source"
    assert any("-xerror" in command for command in commands)

    checks = {field: "pass" for field in conform.REVIEW_FIELDS}
    confirmed = conform.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="watched the full comparison at normal speed",
        full_playback="completed",
        checks=checks,
    )
    assert confirmed["summary"]["blocking"] == 0
    assert confirmed["status"] == "ready"

    tampered = json.loads(plan_path.read_text(encoding="utf-8"))
    tampered["review"]["comparison_sha256"] = "0" * 64
    tampered["plan_id"] = conform._plan_id(tampered)
    assert any("current comparison sha256" in item for item in conform.verify_plan(tampered)["blockers"])

    manifest = build_manifest(str(tmp_path), target_stage="analysis", required=["interlace_conform_plan"])
    gate = next(item for item in manifest["gates"] if item["category"] == "interlace_conform_plan")
    assert gate["status"] == "ready"


def test_project_containment_and_output_collisions_are_rejected(tmp_path, monkeypatch):
    source = _source(tmp_path)
    _patch_detection(monkeypatch, source)
    with pytest.raises(ValueError, match="inside the project"):
        conform.build_plan(
            str(source), str(tmp_path.parent / "escaped.mp4"), "verify/compare.mp4",
            project_dir=str(tmp_path), reviewed_by="editor", note="field evidence",
        )
    with pytest.raises(ValueError, match="all be different"):
        conform.build_plan(
            str(source), "work/same.mp4", "work/same.mp4",
            project_dir=str(tmp_path), reviewed_by="editor", note="field evidence",
        )


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "interlace_conform.py"), "confirm", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "full-length normal-speed" in result.stdout
