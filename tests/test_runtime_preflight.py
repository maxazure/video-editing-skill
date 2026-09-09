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
