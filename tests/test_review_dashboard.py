import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from review_dashboard import build_dashboard_packet, emit_html  # noqa: E402


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


def test_dashboard_packet_prioritizes_blocked_gates(tmp_path):
    _publish_ready_project(tmp_path)
    _write(
        tmp_path / "work" / "edit_preflight.json",
        {
            "version": "edit_preflight.v1",
            "summary": {"blocking": 2, "warnings": 1},
        },
    )

    packet = build_dashboard_packet(str(tmp_path), target_stage="publish_ready")

    assert packet["version"] == "review_dashboard.v1"
    assert packet["status"] == "blocked"
    assert packet["review_state"] == "needs_fix"
    assert packet["blocked_gates"] == ["edit_preflight"]
    assert packet["review_items"][0]["category"] == "edit_preflight"
    assert packet["review_items"][0]["priority_label"] == "blocker"
    assert "2 blocking item(s)" in packet["review_items"][0]["notes"][0]


def test_ready_dashboard_has_no_review_queue_items(tmp_path):
    _publish_ready_project(tmp_path)

    packet = build_dashboard_packet(str(tmp_path), target_stage="publish_ready")

    assert packet["status"] == "ready"
    assert packet["review_state"] == "ready_for_final_review"
    assert packet["review_items"] == []
    assert any(item["category"] == "master_video" for item in packet["latest_artifacts"])


def test_dashboard_html_contains_review_queue_and_file_links(tmp_path):
    _publish_ready_project(tmp_path)
    _write(
        tmp_path / "output" / "audio_master_report.json",
        {
            "version": "audio_master_report.v1",
            "summary": {"blocking": 1},
        },
    )
    packet = build_dashboard_packet(str(tmp_path), target_stage="publish_ready")

    html = emit_html(packet)

    assert "Review Dashboard" in html
    assert "audio_master_report" in html
    assert "1 blocking item(s)" in html
    assert "file://" in html


def test_cli_writes_json_html_and_strict_exit_code(tmp_path):
    _write(tmp_path / "work" / "transcript.json", {"segments": []})
    out_json = tmp_path / "review_dashboard.json"
    out_html = tmp_path / "review_dashboard.html"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "review_dashboard.py"),
            "--project-dir",
            str(tmp_path),
            "--target-stage",
            "publish_ready",
            "--output",
            str(out_json),
            "--html",
            str(out_html),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_html.exists()
    packet = json.loads(out_json.read_text(encoding="utf-8"))
    assert packet["version"] == "review_dashboard.v1"
    assert "master_video" in packet["missing_required"]
    assert "Review Dashboard" in out_html.read_text(encoding="utf-8")
