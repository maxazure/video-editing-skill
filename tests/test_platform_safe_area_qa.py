import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from platform_safe_area_qa import (  # noqa: E402
    analyze_elements,
    build_safe_zone,
    elements_from_enrich_plan,
    elements_from_file,
    elements_from_render_config,
)


def test_xhs_profile_scales_conservative_margins_to_three_by_four():
    safe = build_safe_zone("xhs", 1080, 1440)

    assert safe["left"] == 60.0
    assert safe["right"] == 960.0
    assert safe["top"] == 157.5
    assert safe["bottom"] == 1110.0


def test_centered_badge_is_safe_when_subtitles_are_disabled():
    elements = elements_from_render_config(
        {
            "subtitles": False,
            "text_badges": [{"text": "关键结论", "start": 1.0, "end": 2.0}],
        },
        width=1080,
        height=1920,
    )
    report = analyze_elements(
        elements,
        platform="universal",
        width=1080,
        height=1920,
        safe_zone=build_safe_zone("universal", 1080, 1920),
    )

    assert report["status"] == "ready"
    assert report["summary"]["safe"] == 1
    assert report["elements"][0]["kind"] == "text_badge"


def test_default_bottom_right_pip_blocks_right_and_bottom_ui():
    elements = elements_from_render_config(
        {
            "subtitles": False,
            "pip_overlays": [{"video": "camera.mp4", "start": 0, "end": 5}],
        },
        width=1080,
        height=1920,
    )
    report = analyze_elements(
        elements,
        platform="tiktok",
        width=1080,
        height=1920,
        safe_zone=build_safe_zone("tiktok", 1080, 1920),
    )

    assert report["status"] == "blocked"
    finding = report["findings"][0]
    assert finding["code"] == "critical_element_outside_safe_area"
    assert {"right_ui", "bottom_ui"} <= set(finding["breaches"])


def test_enrich_image_chapter_card_is_reported_as_uncheckable():
    elements = elements_from_enrich_plan(
        {
            "chapter_cards": [
                {
                    "title": "核心方法",
                    "png": "chapter.png",
                    "start": 3,
                    "end": 5,
                }
            ]
        },
        width=1080,
        height=1920,
    )
    report = analyze_elements(
        elements,
        platform="universal",
        width=1080,
        height=1920,
        safe_zone=build_safe_zone("universal", 1080, 1920),
    )

    assert report["status"] == "review"
    assert report["summary"]["uncheckable"] == 1
    assert report["findings"][0]["code"] == "missing_bbox"


def test_focus_marker_uses_renderer_default_size_and_blocks_bottom_ui():
    elements = elements_from_render_config(
        {
            "subtitles": False,
            "focus_events": [{"start": 2, "end": 3, "x": 0.5, "y": 0.92}],
        },
        width=1080,
        height=1920,
    )
    report = analyze_elements(
        elements,
        platform="tiktok",
        width=1080,
        height=1920,
        safe_zone=build_safe_zone("tiktok", 1080, 1920),
    )

    assert report["status"] == "blocked"
    assert report["elements"][0]["bbox"]["width"] == 140.4
    assert "bottom_ui" in report["findings"][0]["breaches"]


def test_custom_normalized_bbox_can_be_checked(tmp_path):
    path = tmp_path / "elements.json"
    path.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "id": "cta",
                        "kind": "cta",
                        "units": "normalized",
                        "bbox": {"x": 0.1, "y": 0.82, "width": 0.5, "height": 0.1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    elements = elements_from_file(str(path), width=1080, height=1920)
    report = analyze_elements(
        elements,
        platform="universal",
        width=1080,
        height=1920,
        safe_zone=build_safe_zone("universal", 1080, 1920),
    )

    assert report["status"] == "blocked"
    assert report["findings"][0]["breaches"] == ["bottom_ui"]


def test_custom_margin_override_changes_safe_rectangle():
    safe = build_safe_zone(
        "tiktok",
        1080,
        1920,
        overrides={"left": 80, "top": 220, "right": 140, "bottom": 360},
    )

    assert safe["left"] == 80.0
    assert safe["top"] == 220.0
    assert safe["right"] == 940.0
    assert safe["bottom"] == 1560.0


def test_cli_strict_writes_json_markdown_and_svg(tmp_path):
    elements_path = tmp_path / "elements.json"
    output_path = tmp_path / "platform_safe_area_qa.json"
    markdown_path = tmp_path / "platform_safe_area_qa.md"
    guide_path = tmp_path / "platform_safe_area_guide.svg"
    elements_path.write_text(
        json.dumps(
            [
                {
                    "id": "unsafe-cta",
                    "kind": "cta",
                    "bbox": [100, 1650, 600, 140],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "platform_safe_area_qa.py"),
            "--elements",
            str(elements_path),
            "--platform",
            "tiktok",
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
            "--guide",
            str(guide_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["version"] == "platform_safe_area_qa.v1"
    assert report["summary"]["blocking"] == 1
    assert "# Platform Safe Area QA" in markdown_path.read_text(encoding="utf-8")
    assert "<svg" in guide_path.read_text(encoding="utf-8")


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "platform_safe_area_qa.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "safe areas" in result.stdout.lower()
