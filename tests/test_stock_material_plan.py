import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from stock_material_plan import (  # noqa: E402
    build_provider_query,
    build_stock_material_plan,
    derive_search_terms,
)


def _write_index(project_dir, items):
    (project_dir / "media_index.json").write_text(
        json.dumps({"meta": {"backend": "json", "count": len(items)}, "items": items}),
        encoding="utf-8",
    )


def test_derive_search_terms_keeps_explicit_terms_first():
    terms = derive_search_terms(
        subject="AI workflow automation",
        script_text="AI workflow dashboard helps teams automate repetitive delivery work.",
        explicit_terms="dashboard, team collaboration",
        amount=4,
    )

    assert [term["term"] for term in terms[:2]] == ["dashboard", "team collaboration"]
    assert any(term["source"] == "subject" for term in terms)
    assert len(terms) == 4


def test_provider_query_builds_moneyprinter_style_stock_urls():
    query = build_provider_query(
        provider="pexels",
        term="AI workflow",
        target={"platform": "douyin", "width": 1080, "height": 1920, "aspect": "9:16"},
        minimum_duration=4,
        per_page=12,
    )

    assert query["provider"] == "pexels"
    assert "api.pexels.com/videos/search" in query["endpoint"]
    assert "orientation=portrait" in query["endpoint"]
    assert query["api_key_env"] == "PEXELS_API_KEY"
    assert query["mpt_config_key"] == "pexels_api_keys"


def test_stock_material_plan_includes_provider_queries_and_batch_coverage():
    plan = build_stock_material_plan(
        subject="AI workflow automation",
        explicit_terms=["dashboard", "office"],
        providers=["pexels", "coverr"],
        platform="douyin",
        clip_duration=4,
        video_count=2,
        required_duration=12,
        term_count=2,
        per_page=8,
    )

    assert plan["schema"] == "stock_material_plan.v1"
    assert plan["summary"]["required_coverage_seconds"] == 24
    assert plan["summary"]["estimated_clips_needed"] == 6
    assert plan["summary"]["provider_query_count"] == 4
    assert any(query["provider"] == "coverr" for query in plan["provider_queries"])
    assert any("Coverr" in warning for warning in plan["warnings"])


def test_stock_material_plan_ranks_local_candidates(tmp_path):
    broll_dir = tmp_path / "media" / "broll"
    broll_dir.mkdir(parents=True)
    (broll_dir / "dashboard.mp4").write_bytes(b"fake")
    _write_index(
        tmp_path,
        [
            {
                "path": "media/broll/dashboard.mp4",
                "type": "video",
                "category": "broll",
                "tags": ["dashboard", "workflow"],
                "duration": 5.0,
                "width": 1080,
                "height": 1920,
            }
        ],
    )

    plan = build_stock_material_plan(
        subject="AI workflow",
        explicit_terms=["dashboard"],
        providers=["pexels"],
        platform="douyin",
        clip_duration=4,
        media_library_project=str(tmp_path),
    )

    assert plan["summary"]["local_candidate_count"] == 1
    assert plan["local_candidates"]["dashboard"][0]["path"] == "media/broll/dashboard.mp4"


def test_stock_material_plan_cli_writes_json_and_markdown(tmp_path):
    script = tmp_path / "transcript.json"
    script.write_text(
        json.dumps({"segments": [{"start": 0, "end": 8, "text": "AI workflow dashboard"}]}),
        encoding="utf-8",
    )
    out_json = tmp_path / "stock_plan.json"
    out_md = tmp_path / "stock_plan.md"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/stock_material_plan.py"),
            "--subject",
            "AI workflow",
            "--script",
            str(script),
            "--terms",
            "dashboard",
            "--provider",
            "pixabay",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["provider_queries"][0]["provider"] == "pixabay"
    assert "# Stock Material Plan" in out_md.read_text(encoding="utf-8")
