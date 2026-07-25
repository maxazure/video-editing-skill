import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import edit_compare  # noqa: E402
from edit_compare import (  # noqa: E402
    _rotation_degrees,
    MediaInfo,
    build_filtergraph,
    build_parts,
    main,
    probe_media,
    required_final_end,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_build_parts_maps_final_back_to_source_clock():
    parts = build_parts(
        [{"start": 1.0, "end": 3.0}, {"start": 5.0, "end": 9.0}],
        source_duration=10.0,
        fps_num=30,
        fps_den=1,
        output_speed=2.0,
        output_offset=0.5,
    )

    assert [part.kind for part in parts] == ["dropped", "kept", "dropped", "kept", "dropped"]
    assert [part.frame_count for part in parts] == [30, 60, 60, 120, 30]
    assert parts[1].program_start == pytest.approx(0.5)
    assert parts[1].program_end == pytest.approx(1.5)
    assert parts[3].program_start == pytest.approx(1.5)
    assert parts[3].program_end == pytest.approx(3.5)
    assert required_final_end(parts) == pytest.approx(3.5)


def test_build_parts_rejects_overlapping_or_out_of_bounds_ranges():
    with pytest.raises(ValueError, match="chronological"):
        build_parts(
            [{"start": 1.0, "end": 3.0}, {"start": 2.0, "end": 4.0}],
            source_duration=5.0,
            fps_num=30,
            fps_den=1,
        )

    with pytest.raises(ValueError, match="beyond source duration"):
        build_parts(
            [{"start": 0.0, "end": 5.1}],
            source_duration=5.0,
            fps_num=30,
            fps_den=1,
        )


def test_filtergraph_contains_black_gaps_final_projection_and_hstack():
    parts = build_parts(
        [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}],
        source_duration=3.0,
        fps_num=30000,
        fps_den=1001,
        output_speed=1.25,
        output_offset=0.4,
    )

    graph = build_filtergraph(
        parts,
        width=1080,
        height=1920,
        fps_num=30000,
        fps_den=1001,
        output_speed=1.25,
    )

    assert "color=c=black:s=1080x1920" in graph
    assert "trim=start=0.400000:end=1.200000" in graph
    assert "setpts=(PTS-STARTPTS)*1.25000000" in graph
    assert "hstack=inputs=2" in graph
    assert "trim=end_frame=90" in graph


def test_rotation_metadata_is_normalized_for_display_geometry():
    assert _rotation_degrees({"tags": {"rotate": "-90"}}) == 270
    assert _rotation_degrees({"side_data_list": [{"rotation": 90.0}]}) == 90
    assert _rotation_degrees({"tags": {"rotate": "bad"}}) == 0


def test_dry_run_writes_blocked_plan_and_strict_returns_two(tmp_path, monkeypatch):
    media = MediaInfo(
        path="/tmp/fake.mp4",
        width=160,
        height=90,
        duration=2.0,
        fps_num=10,
        fps_den=1,
        has_audio=False,
    )
    monkeypatch.setattr(edit_compare, "probe_media", lambda _path: media)
    monkeypatch.setattr(
        edit_compare,
        "load_keep_segments",
        lambda _path: [{"start": 0.0, "end": 1.0}],
    )
    report = tmp_path / "planned_edit_compare.json"
    markdown = tmp_path / "planned_edit_compare.md"
    args = [
        "source.mp4",
        "final.mp4",
        "--cut-list",
        "cuts.json",
        "--output",
        str(tmp_path / "compare.mp4"),
        "--report",
        str(report),
        "--markdown",
        str(markdown),
        "--dry-run",
    ]

    assert main(args) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "planned"
    assert payload["summary"]["blocking"] == 1
    assert main([*args, "--strict"]) == 2


def test_final_too_short_reports_mapping_error(tmp_path, monkeypatch, capsys):
    source = MediaInfo(
        path="/tmp/source.mp4",
        width=160,
        height=90,
        duration=2.0,
        fps_num=10,
        fps_den=1,
        has_audio=False,
    )
    final = MediaInfo(
        path="/tmp/final.mp4",
        width=160,
        height=90,
        duration=0.5,
        fps_num=10,
        fps_den=1,
        has_audio=False,
    )
    monkeypatch.setattr(
        edit_compare,
        "probe_media",
        lambda path: source if path == "source.mp4" else final,
    )
    monkeypatch.setattr(
        edit_compare,
        "load_keep_segments",
        lambda _path: [{"start": 0.0, "end": 1.0}],
    )

    exit_code = main([
        "source.mp4",
        "final.mp4",
        "--cut-list",
        "cuts.json",
        "--output",
        str(tmp_path / "compare.mp4"),
    ])

    assert exit_code == 1
    assert "final video ends at 0.5000s but mapping needs 1.0000s" in capsys.readouterr().err


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe required",
)
def test_real_ffmpeg_render_verifies_kept_and_dropped_ranges(tmp_path):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    output = tmp_path / "compare.mp4"
    report = tmp_path / "compare_edit_compare.json"
    markdown = tmp_path / "compare_edit_compare.md"
    cut_list = tmp_path / "jump_cut.json"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            (
                "color=c=magenta:s=160x90:r=10:d=0.2[intro];"
                "[0:v]trim=start=0:end=0.8,setpts=0.5*(PTS-STARTPTS),fps=10[v0];"
                "[0:v]trim=start=1.2:end=2.0,setpts=0.5*(PTS-STARTPTS),fps=10[v1];"
                "[intro][v0][v1]concat=n=3:v=1:a=0[outv]"
            ),
            "-map",
            "[outv]",
            "-an",
            "-r",
            "10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(final),
        ],
        check=True,
    )
    cut_list.write_text(
        json.dumps({
            "version": "jump_cut_plan.v2",
            "status": "ready",
            "summary": {"blocking": 0},
            "keep_segments": [
                {"start": 0.0, "end": 0.8},
                {"start": 1.2, "end": 2.0},
            ],
        }),
        encoding="utf-8",
    )

    exit_code = main([
        str(source),
        str(final),
        "--cut-list",
        str(cut_list),
        "--output",
        str(output),
        "--report",
        str(report),
        "--markdown",
        str(markdown),
        "--output-speed",
        "2",
        "--output-offset",
        "0.2",
        "--sample-limit",
        "0",
    ])

    assert exit_code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["version"] == "edit_compare.v1"
    assert payload["status"] == "pass"
    assert payload["summary"]["blocking"] == 0
    assert payload["summary"]["kept_ranges"] == 2
    assert payload["summary"]["dropped_ranges"] == 1
    assert payload["summary"]["required_final_end"] == pytest.approx(1.0)
    assert payload["verification"]["status"] == "pass"
    assert {sample["kind"] for sample in payload["verification"]["samples"]} == {"kept", "dropped"}
    assert probe_media(str(output)).width == 320
    assert probe_media(str(output)).has_audio is True
    assert "black on the right means that range was cut" in markdown.read_text(encoding="utf-8")

    no_audio_output = tmp_path / "compare_no_audio.mp4"
    no_audio_report = tmp_path / "compare_no_audio_edit_compare.json"
    assert main([
        str(source),
        str(final),
        "--cut-list",
        str(cut_list),
        "--output",
        str(no_audio_output),
        "--report",
        str(no_audio_report),
        "--output-speed",
        "2",
        "--output-offset",
        "0.2",
        "--sample-limit",
        "1",
        "--no-audio",
    ]) == 0
    assert probe_media(str(no_audio_output)).has_audio is False
    assert json.loads(no_audio_report.read_text(encoding="utf-8"))["verification"]["status"] == "pass"


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "edit_compare.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "source-time-aligned original vs final" in result.stdout
