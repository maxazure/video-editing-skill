import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import cover_variants  # noqa: E402
from cover_variants import analyze_synergy, build_plan, emit_markdown, render_plan  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_build_plan_creates_three_distinct_review_variants(tmp_path):
    plan = build_plan(
        str(tmp_path / "talk.mp4"),
        title="20分钟出片",
        subtitle="AI剪辑完整流程",
        post_title="我用 AI 把剪辑时间从 3 小时缩到 20 分钟",
        platform="xhs",
        output_dir=str(tmp_path / "covers"),
    )

    assert plan["version"] == "cover_variants.v1"
    assert plan["summary"]["variants"] == 3
    assert plan["variants"][0]["style"] == "techcard"
    assert {item["test_variable"] for item in plan["variants"]} == {
        "baseline",
        "style_contrast",
        "visual_evidence",
    }
    assert all(item["width"] == 1080 and item["height"] == 1440 for item in plan["variants"])
    assert "--width 1080 --height 1440" in plan["variants"][0]["render_command"]


def test_synergy_warns_when_cover_duplicates_post_title():
    result = analyze_synergy("AI剪辑20分钟出片", "20分钟出片")

    assert result["status"] == "warn"
    assert result["overlap_terms"]


def test_selection_and_distinct_text_can_be_required(tmp_path):
    plan = build_plan(
        str(tmp_path / "talk.mp4"),
        title="20分钟出片",
        post_title="AI剪辑20分钟出片",
        selected="cover-b",
        require_selection=True,
        require_distinct_cover_text=True,
        output_dir=str(tmp_path / "covers"),
    )

    assert plan["selected_variant"] == "cover-b"
    assert plan["selected_cover"].endswith("cover-b_contrast.png")
    assert plan["summary"]["blocking"] == 1
    assert "distinct cover text" in plan["blockers"][0]


def test_render_plan_writes_all_variants_and_previews(tmp_path, monkeypatch):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    plan = build_plan(
        str(video),
        title="剪辑效率翻倍",
        platform="douyin",
        output_dir=str(tmp_path / "covers"),
        selected="cover-a",
    )

    def fake_generate(_video, _title, output_path, **_kwargs):
        with open(output_path, "wb") as f:
            f.write(b"png")
        return output_path

    previews = []

    def fake_preview(path, output_path, target_width):
        previews.append((path.name, output_path.name, target_width))
        output_path.write_bytes(b"preview")
        return None

    monkeypatch.setattr(cover_variants, "generate_cover", fake_generate)
    monkeypatch.setattr(cover_variants, "_write_mobile_preview", fake_preview)

    rendered = render_plan(plan)

    assert rendered["summary"]["rendered"] == 3
    assert all(item["rendered"] for item in rendered["variants"])
    assert rendered["selected_cover"].endswith("cover-a_primary.png")
    assert len(previews) == 3
    assert all(item[2] == 180 for item in previews)


def test_markdown_contains_synergy_variants_and_commands(tmp_path):
    plan = build_plan(
        str(tmp_path / "talk.mp4"),
        title="真实结果",
        post_title="我测试了三套 AI 剪辑流程",
        output_dir=str(tmp_path / "covers"),
    )

    markdown = emit_markdown(plan)

    assert "# Cover Variants" in markdown
    assert "Title-Cover Synergy" in markdown
    assert "| cover-a | Primary |" in markdown
    assert "generate_cover_image.py" in markdown


def test_cli_strict_requires_selection(tmp_path):
    output = tmp_path / "cover_variants.json"
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/cover_variants.py"),
            str(tmp_path / "talk.mp4"),
            "--title",
            "真实结果",
            "--require-selection",
            "--output",
            str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["blocking"] == 1
    assert "selection is required" in payload["blockers"][0]


def test_cli_reads_post_title_from_caption(tmp_path):
    caption = tmp_path / "caption.json"
    output = tmp_path / "cover_variants.json"
    caption.write_text(
        json.dumps({"title": "我测试了三套 AI 剪辑流程"}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/cover_variants.py"),
            str(tmp_path / "talk.mp4"),
            "--title",
            "真实结果",
            "--caption",
            str(caption),
            "--select",
            "cover-c",
            "--require-selection",
            "--output",
            str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["synergy"]["post_title"] == "我测试了三套 AI 剪辑流程"
    assert payload["selected_variant"] == "cover-c"
