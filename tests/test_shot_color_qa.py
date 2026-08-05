import json
import os
import shutil
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from shot_color_qa import (  # noqa: E402
    build_qa_report,
    emit_markdown,
    load_scene_intervals,
    parse_signalstats,
    signalstats_command,
    validate_args,
    parse_args,
)


def _params(**overrides):
    params = {
        "sample_fps": 2.0,
        "sample_width": 320,
        "scene_threshold": 0.35,
        "min_scene_duration": 1.0,
        "boundary_margin": 0.15,
        "dark_luma": 32.0,
        "bright_luma": 220.0,
        "low_contrast_spread": 18.0,
        "high_saturation": 95.0,
        "max_broadcast_range_ratio": 0.01,
        "max_luma_jump": 45.0,
        "max_chroma_jump": 55.0,
        "ignore_broadcast_range": False,
        "fail_on_extremes": False,
        "fail_on_jumps": False,
    }
    params.update(overrides)
    return params


def _sample(time, *, y=100, low=50, high=180, u=128, v=128, sat=25, brng=0):
    return {
        "time": time,
        "ylow": low,
        "yavg": y,
        "yhigh": high,
        "uavg": u,
        "vavg": v,
        "satavg": sat,
        "brng": brng,
    }


def _scenes():
    return [
        {"scene_id": "scene_001", "start": 0.0, "end": 2.0},
        {"scene_id": "scene_002", "start": 2.0, "end": 4.0},
    ]


def _video_info(color_range="tv"):
    return {
        "duration": 4.0,
        "width": 1080,
        "height": 1920,
        "color_range": color_range,
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
    }


def test_parse_signalstats_keeps_complete_frames_only():
    log = """
[Parsed_metadata_3] frame:0 pts:0 pts_time:0
[Parsed_metadata_3] lavfi.signalstats.YLOW=20
[Parsed_metadata_3] lavfi.signalstats.YAVG=80
[Parsed_metadata_3] lavfi.signalstats.YHIGH=180
[Parsed_metadata_3] lavfi.signalstats.UAVG=120
[Parsed_metadata_3] lavfi.signalstats.VAVG=130
[Parsed_metadata_3] lavfi.signalstats.SATAVG=22.5
[Parsed_metadata_3] lavfi.signalstats.BRNG=0.002
[Parsed_metadata_3] frame:1 pts:1 pts_time:0.5
[Parsed_metadata_3] lavfi.signalstats.YAVG=81
"""

    assert parse_signalstats(log) == [
        {
            "time": 0.0,
            "ylow": 20.0,
            "yavg": 80.0,
            "yhigh": 180.0,
            "uavg": 120.0,
            "vavg": 130.0,
            "satavg": 22.5,
            "brng": 0.002,
        }
    ]


def test_signalstats_command_downscales_and_enables_broadcast_range():
    command = signalstats_command("master.mp4", sample_fps=1.5, sample_width=360)

    vf = command[command.index("-vf") + 1]
    assert "fps=1.500000" in vf
    assert "scale=360:-2:flags=area" in vf
    assert "signalstats=stat=brng" in vf


def test_build_report_blocks_broadcast_range_but_only_warns_cut_changes():
    samples = [
        _sample(0.5),
        _sample(1.5),
        _sample(2.5, y=170, low=160, high=180, u=210, v=50, sat=110, brng=0.2),
        _sample(3.5, y=170, low=160, high=180, u=210, v=50, sat=110, brng=0.2),
    ]

    report = build_qa_report(
        video_path="output/master.mp4",
        video_info=_video_info(),
        scenes=_scenes(),
        samples=samples,
        params=_params(),
        scene_source={"mode": "test"},
    )

    assert report["status"] == "blocked"
    assert report["summary"]["blocking"] == 1
    assert report["summary"]["broadcast_range_exceeded"] == 1
    assert report["summary"]["abrupt_luma_change"] == 1
    assert report["transitions"][0]["blocking_flags"] == []


def test_declared_full_range_warns_instead_of_blocking():
    samples = [_sample(0.5, brng=0.5), _sample(1.5, brng=0.5)]
    report = build_qa_report(
        video_path="output/full-range.mp4",
        video_info={**_video_info("pc"), "duration": 2.0},
        scenes=[{"scene_id": "scene_001", "start": 0.0, "end": 2.0}],
        samples=samples,
        params=_params(),
        scene_source={"mode": "test"},
    )

    assert report["summary"]["blocking"] == 0
    assert report["summary"]["declared_full_range"] == 1
    assert report["status"] == "warn"


def test_fail_on_jumps_escalates_transition_review():
    report = build_qa_report(
        video_path="output/master.mp4",
        video_info=_video_info(),
        scenes=_scenes(),
        samples=[
            _sample(0.5, y=70, u=90, v=90),
            _sample(1.5, y=70, u=90, v=90),
            _sample(2.5, y=180, u=210, v=210),
            _sample(3.5, y=180, u=210, v=210),
        ],
        params=_params(fail_on_jumps=True),
        scene_source={"mode": "test"},
    )

    assert report["summary"]["blocking"] == 1
    assert report["transitions"][0]["blocking_flags"] == [
        "abrupt_luma_change",
        "abrupt_chroma_change",
    ]


def test_missing_scene_samples_fail_closed():
    report = build_qa_report(
        video_path="output/master.mp4",
        video_info=_video_info(),
        scenes=_scenes(),
        samples=[_sample(0.5), _sample(1.5)],
        params=_params(),
        scene_source={"mode": "test"},
    )

    assert report["summary"]["blocking"] == 1
    assert report["shots"][1]["blocking_flags"] == ["missing_samples"]


def test_load_scene_intervals_rejects_overlap(tmp_path):
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "scenes": [
                    {"start": 0, "end": 2},
                    {"start": 1.5, "end": 4},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="overlaps"):
        load_scene_intervals(str(path), duration=4.0)


def test_load_scene_intervals_rejects_uncovered_timeline(tmp_path):
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "scenes": [
                    {"start": 0, "end": 1},
                    {"start": 2, "end": 4},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="uncovered timeline gap"):
        load_scene_intervals(str(path), duration=4.0)


def test_markdown_explains_review_contract_and_commands():
    report = build_qa_report(
        video_path="output/master.mp4",
        video_info=_video_info(),
        scenes=_scenes(),
        samples=[
            _sample(0.5, y=60),
            _sample(1.5, y=60),
            _sample(2.5, y=180),
            _sample(3.5, y=180),
        ],
        params=_params(),
        scene_source={"mode": "test"},
    )

    markdown = emit_markdown(report)

    assert "# Shot Color QA" in markdown
    assert "timeline_view.py" in markdown
    assert "not automatically wrong" in markdown
    assert "not a calibrated waveform/vectorscope" in markdown


def test_invalid_cli_thresholds_are_rejected():
    args = parse_args(["master.mp4", "--output", "qa.json", "--sample-fps", "0"])

    with pytest.raises(ValueError, match="sample-fps"):
        validate_args(args)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg is required for the real CLI smoke",
)
def test_real_cli_writes_review_artifact(tmp_path):
    video = tmp_path / "master.mp4"
    scenes = tmp_path / "scene_boundaries.json"
    output = tmp_path / "shot_color_qa.json"
    markdown = tmp_path / "shot_color_qa.md"
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
            "nullsrc=s=160x90:r=12:d=2,geq=lum='80+80*X/W':cb=128:cr=128",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    scenes.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "scenes": [{"scene_id": "scene_001", "start": 0, "end": 2}],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "shot_color_qa.py"),
            str(video),
            "--scene-boundaries",
            str(scenes),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["version"] == "shot_color_qa.v1"
    assert report["summary"]["shots"] == 1
    assert report["summary"]["sampled_frames"] >= 3
    assert report["summary"]["blocking"] == 0
    assert markdown.exists()
