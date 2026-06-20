import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from project_resume import build_resume_packet, emit_markdown, infer_phase  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _publish_ready_project(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})
    _write(tmp_path / "output" / "day58_master.mp4", "fake video")
    _write(tmp_path / "output" / "day58_qa.json", {"status": "pass", "files": []})
    _write(tmp_path / "output" / "day58_caption.json", {"title": "demo"})


def test_resume_packet_infers_needs_render_phase(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    _write(tmp_path / "work" / "clean_script.md", "# Clean")
    _write(tmp_path / "work" / "render_config.json", {"clips": []})

    packet = build_resume_packet(str(tmp_path), target_stage="publish_ready")

    assert packet["version"] == "project_resume.v1"
    assert packet["status"] == "blocked"
    assert packet["phase"] == "needs_render"
    assert "master_video" in packet["missing_required"]
    assert any(item["category"] == "render_config" for item in packet["latest_artifacts"])
    assert "Start with:" in packet["suggested_prompt"]


def test_ready_project_resume_is_ready_for_publish(tmp_path):
    _publish_ready_project(tmp_path)

    packet = build_resume_packet(str(tmp_path), target_stage="publish_ready")

    assert packet["status"] == "ready"
    assert packet["phase"] == "publish_ready_ready"
    assert packet["recommended_first_action"] == ""
    assert any(item["category"] == "master_video" for item in packet["ready_artifacts"])


def test_markdown_contains_prompt_gates_and_latest_artifacts(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    packet = build_resume_packet(str(tmp_path), target_stage="render_ready")

    markdown = emit_markdown(packet)

    assert "# Project Resume" in markdown
    assert "## Suggested Prompt" in markdown
    assert "## Next Actions" in markdown
    assert "## Gate Snapshot" in markdown
    assert "## Latest Artifacts" in markdown
    assert "`work/transcript.json`" in markdown


def test_infer_phase_prioritizes_unresolved_generation_tasks(tmp_path):
    _publish_ready_project(tmp_path)
    _write(
        tmp_path / "work" / "generation_tasks.json",
        {
            "version": "generation_task_log.v1",
            "summary": {"tasks": 1, "blocking": 1, "pending": 1},
            "tasks": [{"provider": "dreamina", "provider_task_id": "submit_123", "status": "processing"}],
        },
    )
    packet = build_resume_packet(str(tmp_path), target_stage="publish_ready")

    assert infer_phase(packet) == "waiting_on_generation_tasks"
    assert packet["phase"] == "waiting_on_generation_tasks"


def test_cli_writes_json_markdown_agent_note_and_strict_exit_code(tmp_path):
    out_json = tmp_path / "project_resume.json"
    out_md = tmp_path / "project_resume.md"
    agent_note = tmp_path / "CLAUDE.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "project_resume.py"),
            "--project-dir",
            str(tmp_path),
            "--target-stage",
            "publish_ready",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--agent-note",
            str(agent_note),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    assert agent_note.exists()
    packet = json.loads(out_json.read_text(encoding="utf-8"))
    assert packet["version"] == "project_resume.v1"
    assert "# Project Resume" in agent_note.read_text(encoding="utf-8")
