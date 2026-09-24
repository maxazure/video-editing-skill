import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from runtime_preflight import (  # noqa: E402
    build_report,
    emit_markdown,
    parse_component_listing,
    verify_report,
)


CORE_FILTERS = {
    "scale",
    "crop",
    "overlay",
    "concat",
    "aresample",
    "loudnorm",
}


def snapshot(*, filters=(), encoders=("libx264", "aac"), filter_status="parsed", encoder_status="parsed"):
    filter_names = sorted(set(filters))
    encoder_names = sorted(set(encoders))
    return {
        "python": {"status": "available", "version": "3.12.1", "implementation": "CPython"},
        "commands": {
            "ffmpeg": {"status": "available", "version": "ffmpeg version test", "detail": "ok"},
            "ffprobe": {"status": "available", "version": "ffprobe version test", "detail": "ok"},
            "node": {"status": "available", "version": "v22.1.0", "detail": "ok"},
            "npx": {"status": "available", "version": "10.1.0", "detail": "ok"},
        },
        "detections": {
            "filter": {"status": filter_status, "row_count": len(filter_names), "detail": "test filters"},
            "encoder": {"status": encoder_status, "row_count": len(encoder_names), "detail": "test encoders"},
        },
        "components": {"filter": filter_names, "encoder": encoder_names},
    }


def test_component_parser_accepts_ffmpeg_six_and_eight_flag_widths():
    listing = """
 Filters:
  T.. subtitles         V->V       Render text subtitles onto input video using the libass library.
  TS. overlay           VV->V      Overlay a video source on top of the input.
  V....D libx264        libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
  A..... aac            AAC (Advanced Audio Coding)
 """

    assert parse_component_listing(listing) == ["aac", "libx264", "overlay", "subtitles"]


def test_core_profile_passes_with_declared_commands_encoders_and_filters():
    report = build_report(["core_edit"], snapshot=snapshot(filters=CORE_FILTERS))

    assert report["status"] == "ready"
    assert report["summary"] == {
        "profiles": 1,
        "ready_profiles": 1,
        "capabilities": 11,
        "blocking": 0,
        "warnings": 0,
    }
    assert set(report["runtime"]["commands"]) == {"ffmpeg", "ffprobe"}
    assert set(report["runtime"]["detections"]) == {"encoder", "filter"}


def test_ping_pong_loop_profile_requires_reverse_loop_and_frame_filters():
    filters = {"fps", "trim", "setpts", "split", "reverse", "concat", "loop", "setsar", "format"}
    report = build_report(["ping_pong_loop"], snapshot=snapshot(filters=filters, encoders=("libx264",)))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert report["summary"]["capabilities"] == 13
    assert "filter:reverse" in report["capabilities"]
    assert "filter:loop" in report["capabilities"]


def test_podcast_audiogram_profile_requires_waveform_and_caption_filters():
    filters = {"scale", "pad", "setsar", "asplit", "volume", "showwaves", "overlay", "subtitles"}
    ready = build_report(["podcast_audiogram"], snapshot=snapshot(filters=filters))
    assert ready["status"] == "ready"
    missing = build_report(["podcast_audiogram"], snapshot=snapshot(filters=filters - {"subtitles"}))
    assert missing["status"] == "blocked"
    assert missing["capabilities"]["filter:subtitles"]["status"] == "missing"


def test_gif_preview_profile_requires_palette_filters_and_encoder():
    filters = {"fps", "scale", "split", "palettegen", "paletteuse"}
    ready = build_report(["gif_preview"], snapshot=snapshot(filters=filters, encoders=("gif",)))
    assert ready["status"] == "ready"
    missing = build_report(["gif_preview"], snapshot=snapshot(filters=filters - {"paletteuse"}, encoders=("gif",)))
    assert missing["status"] == "blocked"
    assert missing["capabilities"]["filter:paletteuse"]["status"] == "missing"


def test_storyboard_animatic_profile_requires_timing_label_and_audio_filters():
    filters = {
        "scale", "pad", "crop", "setsar", "fps", "format", "drawtext",
        "concat", "apad", "atrim", "asetpts",
    }
    report = build_report(["storyboard_animatic"], snapshot=snapshot(filters=filters))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert "filter:drawtext" in report["capabilities"]
    assert "filter:apad" in report["capabilities"]


def test_video_enhancement_profile_requires_resize_interpolation_and_ab_filters():
    filters = {"scale", "setsar", "fps", "hstack", "minterpolate"}
    report = build_report(["video_enhancement"], snapshot=snapshot(filters=filters))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert "filter:minterpolate" in report["capabilities"]
    assert "filter:hstack" in report["capabilities"]


def test_multicam_switch_profile_requires_video_and_audio_timeline_filters():
    filters = {
        "trim", "setpts", "scale", "pad", "setsar", "fps", "format",
        "concat", "atrim", "asetpts", "aresample", "aformat",
    }
    report = build_report(["multicam_switch"], snapshot=snapshot(filters=filters))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert "filter:trim" in report["capabilities"]
    assert "filter:aformat" in report["capabilities"]


def test_audio_cue_mix_profile_requires_synthesis_mix_and_output_capabilities():
    filters = {
        "aresample", "aformat", "atrim", "apad", "asetpts", "afade",
        "volume", "adelay", "amix", "alimiter", "highpass", "lowpass",
        "anoisesrc", "sine",
    }
    report = build_report(
        ["audio_cue_mix"],
        snapshot=snapshot(filters=filters, encoders=("pcm_s24le", "aac")),
    )

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert "filter:anoisesrc" in report["capabilities"]
    assert "filter:amix" in report["capabilities"]
    assert "encoder:pcm_s24le" in report["capabilities"]


def test_caption_profile_blocks_a_proven_missing_subtitles_filter():
    report = build_report(["captions"], snapshot=snapshot(filters=CORE_FILTERS))

    assert report["status"] == "blocked"
    assert report["summary"]["blocking"] == 1
    check = report["checks"][0]
    assert check["status"] == "missing"
    assert "libass" in check["remedy"]


def test_unreadable_filter_listing_is_unknown_and_fails_closed():
    report = build_report(
        ["captions"],
        snapshot=snapshot(filters=(), filter_status="unparsed"),
    )

    assert report["capabilities"]["filter:subtitles"]["status"] == "unknown"
    assert report["summary"]["blocking"] == 1


def test_stabilization_accepts_deshake_when_vidstab_is_missing():
    report = build_report(
        ["stabilization"],
        snapshot=snapshot(filters={"deshake"}),
    )

    assert report["status"] == "ready"
    check = report["checks"][0]
    assert check["status"] == "available"
    assert [item["status"] for item in check["alternatives"]] == ["missing", "available"]


def test_interlace_profile_requires_idet_and_accepts_yadif_fallback():
    report = build_report(
        ["interlace"],
        snapshot=snapshot(filters={"idet", "fps", "scale", "setfield", "hstack", "yadif"}),
    )

    assert report["status"] == "ready"
    backend = next(item for item in report["checks"] if item["code"] == "interlace:backend")
    assert [item["status"] for item in backend["alternatives"]] == ["missing", "available"]


def test_edge_black_trim_profile_requires_both_detectors_and_trim_filters():
    filters = {
        "blackdetect", "silencedetect", "trim", "atrim", "setpts", "asetpts",
        "fps", "setsar", "format", "aresample", "aformat", "apad", "concat",
    }
    report = build_report(["edge_black_trim"], snapshot=snapshot(filters=filters))

    assert report["status"] == "ready"
    assert report["summary"]["blocking"] == 0
    assert "filter:blackdetect" in report["capabilities"]
    assert "filter:silencedetect" in report["capabilities"]


def test_remotion_profile_binds_only_node_and_npx_versions():
    report = build_report(["remotion"], snapshot=snapshot())

    assert report["status"] == "ready"
    assert set(report["runtime"]["commands"]) == {"node", "npx"}
    assert report["runtime"]["detections"] == {}


def test_generated_time_is_not_part_of_report_integrity():
    stored = build_report(["core_edit"], snapshot=snapshot(filters=CORE_FILTERS))
    stored["generated_at"] = "2099-01-01T00:00:00Z"

    verified = verify_report(stored, snapshot=snapshot(filters=CORE_FILTERS))

    assert verified["status"] == "ready"
    assert verified["verification"]["integrity"] is True
    assert verified["verification"]["drift"] is False


def test_verify_blocks_live_component_drift():
    stored = build_report(["captions"], snapshot=snapshot(filters={"subtitles"}))
    verified = verify_report(stored, snapshot=snapshot(filters=set()))

    codes = {item["code"] for item in verified["checks"]}
    assert verified["status"] == "blocked"
    assert "captions:filter_subtitles" in codes
    assert "runtime_environment_drift" in codes
    assert verified["verification"]["drift"] is True


def test_verify_blocks_hand_edited_stored_report():
    stored = build_report(["captions"], snapshot=snapshot(filters={"subtitles"}))
    stored["profiles"][0]["description"] = "tampered"

    verified = verify_report(stored, snapshot=snapshot(filters={"subtitles"}))

    codes = {item["code"] for item in verified["checks"]}
    assert "stored_report_integrity" in codes
    assert verified["verification"]["integrity"] is False


def test_markdown_names_missing_capability_and_remedy():
    report = build_report(["hdr_sdr"], snapshot=snapshot(filters={"tonemap"}))
    markdown = emit_markdown(report)

    assert "# Runtime Preflight" in markdown
    assert "`filter:zscale`" in markdown
    assert "libzimg" in markdown


def test_cli_lists_profiles():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/runtime_preflight.py"), "list-profiles"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "core_edit\tCore local edit/render" in result.stdout
    assert "stabilization\tVideo stabilization" in result.stdout


def test_cli_analyze_writes_real_machine_report(tmp_path):
    output = tmp_path / "runtime_preflight.json"
    markdown = tmp_path / "runtime_preflight.md"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/runtime_preflight.py"),
            "analyze",
            "--profile",
            "core_edit",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == "runtime_preflight.v1"
    assert report["status"] in {"ready", "blocked"}
    assert report["selected_profiles"] == ["core_edit"]
    assert "ffmpeg" in report["runtime"]["commands"]
    assert "Runtime Preflight" in markdown.read_text(encoding="utf-8")
