import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from media_library import recommend_assets, score_media_candidate  # noqa: E402


def _write_index(project_dir, items):
    (project_dir / "media_index.json").write_text(
        json.dumps(
            {
                "meta": {"backend": "json", "count": len(items)},
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_score_media_candidate_explains_query_matches():
    item = {
        "path": "media/broll/workflow-dashboard.mp4",
        "type": "video",
        "category": "broll",
        "tags": ["workflow", "dashboard"],
        "duration": 4.0,
        "width": 1080,
        "height": 1920,
    }

    score, reasons = score_media_candidate(
        item,
        "workflow dashboard",
        target_duration=3.0,
        target_aspect="9:16",
    )

    assert score > 10
    assert "tag:workflow" in reasons
    assert "filename:dashboard" in reasons
    assert "duration-covers-cue" in reasons
    assert "aspect-match" in reasons


def test_recommend_assets_ranks_tagged_broll(tmp_path):
    broll_dir = tmp_path / "media" / "broll"
    broll_dir.mkdir(parents=True)
    (broll_dir / "workflow-dashboard.mp4").write_bytes(b"fake video")
    (broll_dir / "workflow-random.mp4").write_bytes(b"fake video")
    _write_index(
        tmp_path,
        [
            {
                "path": "media/broll/workflow-random.mp4",
                "type": "video",
                "category": "broll",
                "tags": ["desk"],
                "duration": 12.0,
                "width": 1920,
                "height": 1080,
            },
            {
                "path": "media/broll/workflow-dashboard.mp4",
                "type": "video",
                "category": "broll",
                "tags": ["workflow", "dashboard"],
                "duration": 4.0,
                "width": 1080,
                "height": 1920,
            },
        ],
    )

    results = recommend_assets(
        str(tmp_path),
        "workflow dashboard",
        category="broll",
        target_duration=3.0,
        target_aspect="9:16",
    )

    assert results[0]["absolute_path"].endswith("workflow-dashboard.mp4")
    assert results[0]["score"] > results[1]["score"]
    assert "tag:workflow" in results[0]["reasons"]


def test_recommend_cli_emits_json(tmp_path):
    broll_dir = tmp_path / "media" / "broll"
    broll_dir.mkdir(parents=True)
    (broll_dir / "ai-workflow.mp4").write_bytes(b"fake video")
    _write_index(
        tmp_path,
        [
            {
                "path": "media/broll/ai-workflow.mp4",
                "type": "video",
                "category": "broll",
                "tags": ["AI", "workflow"],
                "duration": 3.5,
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/media_library.py"),
            "recommend",
            "AI workflow",
            "--project-dir",
            str(tmp_path),
            "--category",
            "broll",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["results"][0]["absolute_path"].endswith("ai-workflow.mp4")
    assert payload["results"][0]["category"] == "broll"
