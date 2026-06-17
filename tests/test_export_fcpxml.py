import json
import os
import subprocess
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from export_edl import build_events, load_cut_list_segments  # noqa: E402
from export_fcpxml import render_fcpxml, seconds_to_fcpx_time  # noqa: E402


def _parse_fcpxml(text):
    cleaned = "\n".join(line for line in text.splitlines() if not line.startswith("<!DOCTYPE"))
    return ET.fromstring(cleaned)


def test_seconds_to_fcpx_time_uses_frame_rational():
    assert seconds_to_fcpx_time(0, 30.0) == "0s"
    assert seconds_to_fcpx_time(1.5, 30.0) == "45/30s"
    assert seconds_to_fcpx_time(1.0, 24.0) == "24/24s"


def test_render_fcpxml_builds_single_spine_timeline(tmp_path):
    source = tmp_path / "A Cam.mov"
    cut_list = tmp_path / "rough_cut.json"
    cut_list.write_text(json.dumps({
        "input": str(source),
        "keep_segments": [
            {"start": 1.0, "end": 2.0, "label": "hook"},
            {"start": 4.0, "end": 4.5, "label": "payoff"},
        ],
    }), encoding="utf-8")

    events = build_events(load_cut_list_segments(str(cut_list)), fps=30.0)
    text = render_fcpxml(events, title="DAY58", fps=30.0, width=1080, height=1920)
    root = _parse_fcpxml(text)

    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>')
    assert root.attrib["version"] == "1.10"
    fmt = root.find("./resources/format")
    assert fmt is not None
    assert fmt.attrib["frameDuration"] == "1/30s"
    assert fmt.attrib["width"] == "1080"
    assert fmt.attrib["height"] == "1920"

    assets = root.findall("./resources/asset")
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(assets) == 1
    assert len(clips) == 2
    assert clips[0].attrib["name"] == "hook"
    assert clips[0].attrib["offset"] == "0s"
    assert clips[0].attrib["start"] == "30/30s"
    assert clips[0].attrib["duration"] == "30/30s"
    assert clips[1].attrib["offset"] == "30/30s"
    assert clips[1].attrib["duration"] == "15/30s"


def test_render_fcpxml_uses_one_asset_per_source(tmp_path):
    cut_list = tmp_path / "jump_cut.json"
    cut_list.write_text(json.dumps({
        "input": str(tmp_path / "talking.mp4"),
        "keep_segments": [
            {"start": 0.0, "end": 1.0},
            {"start": 2.0, "end": 3.0},
        ],
    }), encoding="utf-8")
    events = build_events(load_cut_list_segments(str(cut_list)), fps=24.0)
    root = _parse_fcpxml(render_fcpxml(events, title="Reuse", fps=24.0))

    assets = root.findall("./resources/asset")
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(assets) == 1
    assert {clip.attrib["ref"] for clip in clips} == {assets[0].attrib["id"]}


def test_cli_writes_fcpxml_and_manifest(tmp_path):
    cut_list = tmp_path / "rough_cut.json"
    output = tmp_path / "rough_cut.fcpxml"
    cut_list.write_text(json.dumps({
        "input": str(tmp_path / "talking.mp4"),
        "keep_segments": [
            {"start": 0.0, "end": 1.0},
            {"start": 2.0, "end": 2.5},
        ],
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "export_fcpxml.py"),
            "--cut-list", str(cut_list),
            "--output", str(output),
            "--fps", "30",
            "--title", "ROUGH",
            "--width", "1920",
            "--height", "1080",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert output.with_suffix(".fcpxml.json").exists()
    manifest = json.loads(output.with_suffix(".fcpxml.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "nle_handoff_fcpxml"
    assert manifest["event_count"] == 2
    assert manifest["duration_seconds"] == 1.5
    assert manifest["width"] == 1920
    assert manifest["height"] == 1080
