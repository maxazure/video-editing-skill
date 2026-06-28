import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from import_capcut_subtitles import parse_srt  # noqa: E402
from srt_edit_plan import build_edit_plan, build_render_config, parse_guide  # noqa: E402


def _write_srt(path):
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n开场铺垫\n\n"
        "2\n00:00:02,400 --> 00:00:04,000\n真正的结论\n\n"
        "3\n00:00:05,000 --> 00:00:07,000\n产品发布现场\n\n"
        "4\n00:00:07,000 --> 00:00:09,000\n用户反应很强\n\n"
        "5\n00:00:10,000 --> 00:00:12,000\n重复解释一遍\n",
        encoding="utf-8",
    )


def test_parse_guide_accepts_metadata_keep_and_drop(tmp_path):
    guide = tmp_path / "guide.md"
    guide.write_text(
        "title: 发布会高光\n"
        "platform: xhs\n"
        "- keep 3-4: 先用产品和用户反应\n"
        "- skip 1: 慢热开场\n"
        "- keep 2: 再补结论\n",
        encoding="utf-8",
    )

    directives, metadata = parse_guide(str(guide))

    assert metadata == {"title": "发布会高光", "platform": "xhs"}
    assert [item.action for item in directives] == ["keep", "drop", "keep"]
    assert directives[0].segment_ids == (3, 4)
    assert directives[0].note == "先用产品和用户反应"


def test_build_edit_plan_preserves_keep_order_for_reordered_cut(tmp_path):
    srt = tmp_path / "captions.srt"
    guide = tmp_path / "guide.md"
    _write_srt(srt)
    guide.write_text(
        "title: 发布会高光\n"
        "platform: douyin\n"
        "keep 3-4: 画面最强\n"
        "drop 1: 铺垫慢\n"
        "drop 5: 重复\n"
        "keep 2: 结论前置后补\n",
        encoding="utf-8",
    )

    segments = parse_srt(str(srt))
    directives, metadata = parse_guide(str(guide))
    plan = build_edit_plan(
        segments,
        directives,
        srt_path=str(srt),
        guide_path=str(guide),
        source_media="origin/launch.mp4",
        metadata=metadata,
    )
    render_config = build_render_config(plan, source_media="origin/launch.mp4")

    assert plan["version"] == "srt_edit_plan.v1"
    assert [clip["segment_ids"] for clip in plan["clips"]] == [[3, 4], [2]]
    assert plan["summary"]["dropped_segments"] == 2
    assert plan["summary"]["unused_segments"] == 0
    assert render_config["title"] == "发布会高光"
    assert render_config["platform"] == "douyin"
    assert render_config["clips"][0]["start"] == 5.0
    assert render_config["clips"][0]["end"] == 9.0
    assert render_config["clips"][1]["srt_segment_ids"] == [2]


def test_require_all_reviewed_marks_unused_segments_blocking(tmp_path):
    srt = tmp_path / "captions.srt"
    guide = tmp_path / "guide.md"
    _write_srt(srt)
    guide.write_text("keep 2: 核心结论\n", encoding="utf-8")

    segments = parse_srt(str(srt))
    directives, metadata = parse_guide(str(guide))
    plan = build_edit_plan(
        segments,
        directives,
        srt_path=str(srt),
        guide_path=str(guide),
        metadata=metadata,
        require_all_reviewed=True,
    )

    assert "unreviewed_segments" in plan["summary"]["blocking"]
    assert plan["summary"]["unused_segment_ids"] == [1, 3, 4, 5]


def test_cli_writes_plan_render_config_cut_list_and_markdown(tmp_path):
    srt = tmp_path / "captions.srt"
    guide = tmp_path / "guide.md"
    output = tmp_path / "srt_edit_plan.json"
    render_config = tmp_path / "render_config.json"
    cut_list = tmp_path / "cut_list.json"
    markdown = tmp_path / "review.md"
    _write_srt(srt)
    guide.write_text("title: 发布会高光\nkeep 3-4: 开头高光\nskip 1,5: 删除\nkeep 2: 补结论\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "srt_edit_plan.py"),
            "--srt", str(srt),
            "--guide", str(guide),
            "--source-media", "origin/launch.mp4",
            "--output", str(output),
            "--render-config", str(render_config),
            "--cut-list", str(cut_list),
            "--markdown", str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Built SRT edit plan with 2 clips" in result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["clips"] == 2
    assert json.loads(render_config.read_text(encoding="utf-8"))["clips"][0]["srt_segment_ids"] == [3, 4]
    assert json.loads(cut_list.read_text(encoding="utf-8"))["kind"] == "srt_edit_plan_cut"
    assert "SRT Edit Plan Review" in markdown.read_text(encoding="utf-8")
