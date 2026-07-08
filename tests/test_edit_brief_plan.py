import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from edit_brief_plan import build_plan, emit_markdown, infer_source_media  # noqa: E402


def test_infer_source_media_from_brief():
    assert infer_source_media("把 origin/interview.mp4 剪成三条短视频") == "origin/interview.mp4"
    assert infer_source_media("use /tmp/raw/talk.MOV, add captions") == "/tmp/raw/talk.MOV"


def test_batch_short_brief_routes_highlights_before_batch(tmp_path):
    source = tmp_path / "origin" / "interview.mp4"
    source.parent.mkdir(parents=True)
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"把 {source} 剪成三条抖音短视频，去停顿，加B-roll、BGM和字幕，最后生成发布包",
        project_dir=str(tmp_path),
    )

    assert plan["status"] == "ready"
    ids = [step["id"] for step in plan["steps"]]
    assert "highlight_candidates" in ids
    assert "shorts_batch" in ids
    assert ids.index("highlight_candidates") < ids.index("shorts_batch")
    assert "jump_cut" in ids
    assert "audio_cue_sheet" in ids
    assert "publish_package" in ids
    assert plan["source"]["source_media"] == str(source)
    highlight = next(step for step in plan["steps"] if step["id"] == "highlight_candidates")
    assert "--platform douyin" in highlight["command"]


def test_generated_assets_note_and_prompt_pack_steps(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"{source} 做一条小红书口播，抽象概念需要生图，也要用即梦生成视频素材",
        project_dir=str(tmp_path),
    )

    ids = [step["id"] for step in plan["steps"]]
    assert "storyboard_plan" in ids
    assert "video_prompt_pack" in ids
    assert "enrich_plan" in ids
    assert any("gpt-image-2" in note for note in plan["notes"])


def test_empty_brief_is_blocked():
    plan = build_plan("")

    assert plan["status"] == "blocked"
    assert plan["summary"]["blocking"] == 1
    assert "brief is empty" in plan["blockers"]


def test_emit_markdown_includes_commands_and_manifest_check(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")
    plan = build_plan(f"{source} remove silence and render mp4", project_dir=str(tmp_path))

    markdown = emit_markdown(plan)

    assert "# Edit Brief Plan" in markdown
    assert "scripts/jump_cut.py" in markdown
    assert "scripts/render_final.py" in markdown
    assert "scripts/pipeline_manifest.py" in markdown


def test_cli_writes_json_and_markdown(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")
    output = tmp_path / "work" / "edit_brief_plan.json"
    markdown = tmp_path / "work" / "edit_brief_plan.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "edit_brief_plan.py"),
            "--brief",
            f"{source} 剪成三条抖音短视频，加字幕并发布",
            "--project-dir",
            str(tmp_path),
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
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["version"] == "edit_brief_plan.v1"
    assert plan["summary"]["steps"] >= 6
    assert markdown.exists()
