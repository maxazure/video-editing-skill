import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from source_receipts import (  # noqa: E402
    build_receipts_manifest,
    emit_html,
    emit_markdown,
    load_claim_inputs,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def test_ready_claim_with_official_url_and_screenshot(tmp_path):
    screenshot = tmp_path / "receipts" / "openai.png"
    _write(screenshot, "fake image")
    claims = [
        {
            "id": "c001",
            "text": "OpenAI published the product note.",
            "source_url": "https://openai.com/news/example",
            "source_title": "OpenAI product note",
            "source_type": "official",
            "screenshot": "receipts/openai.png",
            "risk": "news",
            "timecode": "00:01-00:04",
            "_base_dir": str(tmp_path),
        }
    ]

    manifest = build_receipts_manifest(
        claims,
        project_dir=str(tmp_path),
        require_screenshot=True,
        require_primary_source=True,
    )

    assert manifest["version"] == "source_receipts.v1"
    assert manifest["status"] == "ready"
    assert manifest["summary"]["blocking"] == 0
    assert manifest["claims"][0]["source_file_exists"] is True
    assert manifest["claims"][0]["timecode"] == {"start": 1.0, "end": 4.0}


def test_claim_without_any_receipt_blocks(tmp_path):
    manifest = build_receipts_manifest(
        [{"id": "c001", "text": "This needs proof."}],
        project_dir=str(tmp_path),
    )

    assert manifest["status"] == "blocked"
    assert manifest["summary"]["blocking"] == 1
    assert "missing_source_receipt" in manifest["claims"][0]["issues"]
    assert any("source_url" in action for action in manifest["next_steps"])


def test_high_risk_claim_requires_url_even_with_local_file(tmp_path):
    screenshot = tmp_path / "receipt.png"
    _write(screenshot, "fake image")

    manifest = build_receipts_manifest(
        [{"text": "Revenue doubled.", "risk": "finance", "screenshot": str(screenshot)}],
        project_dir=str(tmp_path),
    )

    assert manifest["status"] == "blocked"
    assert "high_risk_missing_source_url" in manifest["claims"][0]["issues"]


def test_require_primary_source_blocks_unmarked_web_source(tmp_path):
    manifest = build_receipts_manifest(
        [{"text": "A claim.", "source_url": "https://example.com/report"}],
        project_dir=str(tmp_path),
        require_primary_source=True,
    )

    assert manifest["status"] == "blocked"
    assert "non_primary_source" in manifest["claims"][0]["issues"]


def test_load_claim_inputs_uses_claim_file_directory_for_relative_files(tmp_path):
    screenshot = tmp_path / "work" / "receipts" / "shot.png"
    _write(screenshot, "fake image")
    claims_file = tmp_path / "work" / "claims.json"
    _write(
        claims_file,
        {
            "claims": [
                {
                    "text": "Claim with local receipt.",
                    "source_url": "https://example.com/source",
                    "source_type": "official",
                    "screenshot": "receipts/shot.png",
                }
            ]
        },
    )

    claims = load_claim_inputs([str(claims_file)], [], default_base=tmp_path)
    manifest = build_receipts_manifest(claims, project_dir=str(tmp_path))

    assert manifest["status"] == "warn"
    assert manifest["claims"][0]["source_file"] == str(screenshot.resolve())
    assert manifest["claims"][0]["source_file_exists"] is True


def test_markdown_and_html_include_claim_cards(tmp_path):
    screenshot = tmp_path / "receipt.png"
    _write(screenshot, "fake image")
    manifest = build_receipts_manifest(
        [
            {
                "id": "c001",
                "text": "Proof-backed claim",
                "source_url": "https://example.com/source",
                "source_title": "Example Source",
                "source_type": "official",
                "screenshot": str(screenshot),
            }
        ],
        project_dir=str(tmp_path),
    )

    markdown = emit_markdown(manifest)
    html = emit_html(manifest)

    assert "# Source Receipts Review" in markdown
    assert "Proof-backed claim" in markdown
    assert "Source Receipts" in html
    assert "file://" in html
    assert "Example Source" in html


def test_cli_writes_json_markdown_html_and_strict_exit(tmp_path):
    out_json = tmp_path / "source_receipts.json"
    out_md = tmp_path / "source_receipts.md"
    out_html = tmp_path / "source_receipts.html"

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "source_receipts.py"),
            "--project-dir",
            str(tmp_path),
            "--claim",
            "Unverified claim",
            "--output",
            str(out_json),
            "--markdown",
            str(out_md),
            "--html",
            str(out_html),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert out_json.exists()
    assert out_md.exists()
    assert out_html.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["version"] == "source_receipts.v1"
    assert data["summary"]["blocking"] == 1


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "source_receipts.py"), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "source receipts" in result.stdout.lower()
