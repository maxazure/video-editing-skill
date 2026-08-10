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
    assert "audio_boundary_plan" in ids
    assert "shorts_batch" in ids
    assert ids.index("highlight_candidates") < ids.index("audio_boundary_plan") < ids.index("shorts_batch")
    assert "jump_cut" in ids
    assert "audio_cue_sheet" in ids
    assert "publish_package" in ids
    assert plan["source"]["source_media"] == str(source)
    highlight = next(step for step in plan["steps"] if step["id"] == "highlight_candidates")
    assert "--platform douyin" in highlight["command"]
    batch = next(step for step in plan["steps"] if step["id"] == "shorts_batch")
    assert "work/audio_boundary_plan.json" in batch["command"]


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


def test_review_proxy_brief_routes_timecoded_review_video(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"{source} 渲染后生成低码率时间码审片视频",
        project_dir=str(tmp_path),
    )

    step = next(step for step in plan["steps"] if step["id"] == "review_proxy")
    assert step["script"] == "review_proxy.py"
    assert "verify/final_review_proxy.mp4" in step["command"]
    assert step["gate_category"] == "review_proxy"


def test_review_proxy_only_uses_supplied_render_without_rerendering(tmp_path):
    source = tmp_path / "master.mp4"
    source.write_text("fake master", encoding="utf-8")

    plan = build_plan(f"把 {source} 做成审片代理", project_dir=str(tmp_path))

    ids = [step["id"] for step in plan["steps"]]
    assert "master_video" not in ids
    proxy = next(step for step in plan["steps"] if step["id"] == "review_proxy")
    assert str(source) in proxy["command"]


def test_target_script_brief_routes_alignment_before_render(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"把 {source} 按目标脚本剪成成片并生成发布包",
        project_dir=str(tmp_path),
    )

    ids = [step["id"] for step in plan["steps"]]
    assert "script_alignment" in ids
    assert "edit_preflight" in ids
    assert "master_video" in ids
    assert "publish_package" in ids
    assert "clean_script" not in ids
    assert ids.index("script_alignment") < ids.index("edit_preflight") < ids.index("master_video")
    step = next(step for step in plan["steps"] if step["id"] == "script_alignment")
    assert "scripts/script_alignment.py" in step["command"]
    assert "work/render_config.json" in step["outputs"]
    assert "work/clean_script.md" in step["outputs"]


def test_semantic_review_brief_routes_context_packet_after_transcription(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"给 {source} 的字幕做全篇上下文语义校稿，检查专业术语错词",
        project_dir=str(tmp_path),
    )

    ids = [step["id"] for step in plan["steps"]]
    assert ids.index("transcript") < ids.index("semantic_transcript_review")
    step = next(step for step in plan["steps"] if step["id"] == "semantic_transcript_review")
    assert "semantic_transcript_review.py prepare" in step["command"]
    assert step["gate_category"] == "semantic_transcript_review"


def test_reversible_edit_brief_routes_revision_history(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"给 {source} 的剪辑配置建立可逆修改和修订历史，之后可以撤销剪辑或重做剪辑",
        project_dir=str(tmp_path),
    )

    step = next(step for step in plan["steps"] if step["id"] == "edit_revision_history")
    assert "edit_revision.py prepare" in step["command"]
    assert "work/render_config.json" in step["command"]
    assert step["gate_category"] == "edit_revision_history"
    assert any("approval JSON" in note for note in plan["notes"])


def test_portable_recipe_briefs_route_export_and_replay(tmp_path):
    source = tmp_path / "talk.mp4"
    source.write_text("fake video", encoding="utf-8")

    export_plan = build_plan(
        f"把 {source} 当前 render config 保存为剪辑配方，归档剪辑风格",
        project_dir=str(tmp_path),
    )
    export_step = next(step for step in export_plan["steps"] if step["id"] == "edit_recipe_export")
    assert "edit_recipe.py export" in export_step["command"]
    assert export_step["gate_category"] == "edit_recipe"

    replay_plan = build_plan(
        "套用剪辑模板，绑定新素材后回放剪辑配方并渲染",
        project_dir=str(tmp_path),
    )
    ids = [step["id"] for step in replay_plan["steps"]]
    assert ids.index("edit_recipe_replay") < ids.index("edit_preflight") < ids.index("master_video")
    replay_step = next(step for step in replay_plan["steps"] if step["id"] == "edit_recipe_replay")
    assert "--bind '<slot=local_path>'" in replay_step["command"]
    assert any("human preview" in note for note in replay_plan["notes"])


def test_speed_ramp_brief_routes_source_bound_plan(tmp_path):
    source = tmp_path / "action.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"给 {source} 的落地瞬间做 speed ramp 和慢动作",
        project_dir=str(tmp_path),
    )

    step = next(step for step in plan["steps"] if step["id"] == "speed_ramp_plan")
    assert step["script"] == "speed_ramp.py"
    assert "speed_ramp.py plan" in step["command"]
    assert "work/speed_ramp_plan.json" in step["outputs"]
    assert step["gate_category"] == "speed_ramp_plan"


def test_j_cut_brief_routes_audio_transition_plan_into_render(tmp_path):
    source = tmp_path / "interview.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"给 {source} 的两个片段做 J-cut，让声音先行再渲染成片",
        project_dir=str(tmp_path),
    )

    ids = [step["id"] for step in plan["steps"]]
    assert ids.index("audio_transition_plan") < ids.index("edit_preflight") < ids.index("master_video")
    step = next(step for step in plan["steps"] if step["id"] == "audio_transition_plan")
    assert step["script"] == "audio_transition.py"
    assert "<after_clip>,<j_cut|l_cut>,<duration_seconds>" in step["command"]
    assert step["gate_category"] == "audio_transition_plan"
    render = next(step for step in plan["steps"] if step["id"] == "master_video")
    assert "--audio-transition-plan work/audio_transition_plan.json" in render["command"]
    assert "--primary-speed 1.0" in render["command"]
    assert any("headphones" in note for note in plan["notes"])


def test_shaky_footage_brief_routes_stabilization_review(tmp_path):
    source = tmp_path / "handheld.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"给 {source} 做视频防抖，修复手持抖动后再剪辑",
        project_dir=str(tmp_path),
    )

    step = next(step for step in plan["steps"] if step["id"] == "video_stabilization_plan")
    assert step["script"] == "video_stabilization.py"
    assert "video_stabilization.py plan" in step["command"]
    assert "--decision review" in step["command"]
    assert "work/video_stabilization_plan.json" in step["outputs"]
    assert step["gate_category"] == "video_stabilization_plan"
    assert any("full-length --comparison" in note for note in plan["notes"])


def test_file_size_brief_routes_target_size_delivery(tmp_path):
    source = tmp_path / "master.mp4"
    source.write_text("fake video", encoding="utf-8")

    plan = build_plan(
        f"把 {source} 视频压缩到 18MB 以内，满足上传限制",
        project_dir=str(tmp_path),
    )

    step = next(step for step in plan["steps"] if step["id"] == "delivery_encode_plan")
    assert step["script"] == "delivery_encode.py"
    assert "delivery_encode.py plan" in step["command"]
    assert "--max-size-mib 18.0" in step["command"]
    assert str(source) in step["command"]
    assert step["gate_category"] == "delivery_encode_plan"
    assert any("normal speed" in note for note in plan["notes"])


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
