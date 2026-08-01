import json
import hashlib
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from approval_receipt import create_receipt, verify_receipt  # noqa: E402


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _create(tmp_path):
    video = tmp_path / "output" / "final.mp4"
    qa = tmp_path / "verify" / "render_qa.json"
    caption = tmp_path / "output" / "caption.json"
    _write(video, "final-v1")
    _write(qa, {"status": "pass"})
    _write(caption, {"title": "Demo"})
    receipt_path = tmp_path / "verify" / "approval_receipt.json"
    receipt = create_receipt(
        str(tmp_path),
        [str(video), str(qa), str(caption)],
        approved_by="Jay",
        note="Reviewed at normal speed.",
        receipt_path=str(receipt_path),
    )
    _write(receipt_path, receipt)
    return receipt, receipt_path, video, qa, caption


def test_create_receipt_hashes_explicit_project_relative_artifacts(tmp_path):
    receipt, _, _, _, _ = _create(tmp_path)

    assert receipt["version"] == "approval_receipt.v1"
    assert receipt["approval_scope"] == "listed_artifacts_only"
    assert receipt["approved_by_label"] == "Jay"
    assert receipt["assurance"] == {
        "identity": "unverified_user_supplied_label",
        "signature": "none",
        "timestamp": "local_system_clock",
    }
    assert receipt["summary"] == {"artifacts": 3}
    assert [item["path"] for item in receipt["artifacts"]] == [
        "output/caption.json",
        "output/final.mp4",
        "verify/render_qa.json",
    ]
    assert all(len(item["sha256"]) == 64 for item in receipt["artifacts"])


def test_verify_is_current_immediately_after_create(tmp_path):
    receipt, receipt_path, _, _, _ = _create(tmp_path)

    verification = verify_receipt(receipt, str(tmp_path), receipt_path=str(receipt_path))

    assert verification["status"] == "current"
    assert verification["summary"]["current"] == 3
    assert verification["summary"]["blocking"] == 0


def test_verify_marks_changed_bytes_stale(tmp_path):
    receipt, receipt_path, video, _, _ = _create(tmp_path)
    video.write_text("final-v2", encoding="utf-8")

    verification = verify_receipt(receipt, str(tmp_path), receipt_path=str(receipt_path))

    assert verification["status"] == "stale"
    changed = next(item for item in verification["artifacts"] if item["path"] == "output/final.mp4")
    assert changed["status"] == "changed"
    assert "sha256 changed" in changed["issue"]


def test_verify_marks_deleted_artifact_missing(tmp_path):
    receipt, receipt_path, _, qa, _ = _create(tmp_path)
    qa.unlink()

    verification = verify_receipt(receipt, str(tmp_path), receipt_path=str(receipt_path))

    assert verification["status"] == "stale"
    missing = next(item for item in verification["artifacts"] if item["path"] == "verify/render_qa.json")
    assert missing["status"] == "missing"


def test_verify_rejects_path_that_becomes_symlink(tmp_path):
    receipt, receipt_path, video, _, _ = _create(tmp_path)
    replacement = tmp_path / "output" / "replacement.mp4"
    replacement.write_text("final-v1", encoding="utf-8")
    video.unlink()
    video.symlink_to(replacement)

    verification = verify_receipt(receipt, str(tmp_path), receipt_path=str(receipt_path))

    result = next(item for item in verification["artifacts"] if item["path"] == "output/final.mp4")
    assert result["status"] == "unsafe"
    assert "canonical" in result["issue"]


def test_create_rejects_outside_duplicate_and_self_paths(tmp_path):
    inside = tmp_path / "output" / "final.mp4"
    outside = tmp_path.parent / "outside.mp4"
    receipt_path = tmp_path / "verify" / "approval_receipt.json"
    _write(inside, "inside")
    _write(outside, "outside")
    _write(receipt_path, {"old": True})

    with pytest.raises(ValueError, match="inside project"):
        create_receipt(str(tmp_path), [str(outside)], approved_by="Jay")
    with pytest.raises(ValueError, match="duplicate"):
        create_receipt(str(tmp_path), [str(inside), str(inside)], approved_by="Jay")
    with pytest.raises(ValueError, match="include itself"):
        create_receipt(
            str(tmp_path),
            [str(receipt_path)],
            approved_by="Jay",
            receipt_path=str(receipt_path),
        )


def test_create_and_verify_reject_volatile_generated_artifacts(tmp_path):
    volatile = tmp_path / "work" / "pipeline_manifest.json"
    normal = tmp_path / "output" / "final.mp4"
    _write(volatile, {"status": "ready"})
    _write(normal, "final")

    with pytest.raises(ValueError, match="volatile generated artifact"):
        create_receipt(str(tmp_path), [str(volatile)], approved_by="Jay")

    receipt = create_receipt(str(tmp_path), [str(normal)], approved_by="Jay")
    raw = volatile.read_bytes()
    receipt["artifacts"] = [{
        "path": "work/pipeline_manifest.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }]
    verification = verify_receipt(receipt, str(tmp_path))

    assert verification["status"] == "stale"
    assert verification["artifacts"][0]["status"] == "invalid"
    assert "volatile generated artifact" in verification["artifacts"][0]["issue"]


def test_verify_rejects_traversal_and_duplicate_receipt_entries(tmp_path):
    _write(tmp_path / "output" / "final.mp4", "final")
    receipt = {
        "version": "approval_receipt.v1",
        "approval_scope": "listed_artifacts_only",
        "approved_by_label": "Jay",
        "hash_algorithm": "sha256",
        "artifacts": [
            {"path": "../outside.mp4", "sha256": "0" * 64, "size_bytes": 1},
            {"path": "../outside.mp4", "sha256": "0" * 64, "size_bytes": 1},
        ],
    }

    verification = verify_receipt(receipt, str(tmp_path))

    assert verification["status"] == "stale"
    assert verification["summary"]["blocking"] == 2
    assert {item["status"] for item in verification["artifacts"]} == {"unsafe", "invalid"}


def test_cli_create_then_strict_verify_detects_stale_file(tmp_path):
    video = tmp_path / "output" / "final.mp4"
    _write(video, "final-v1")
    receipt_path = tmp_path / "verify" / "approval_receipt.json"

    create = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "approval_receipt.py"),
            "create",
            "--project-dir",
            str(tmp_path),
            "--artifact",
            str(video),
            "--approved-by",
            "Jay",
            "--output",
            str(receipt_path),
        ],
        capture_output=True,
        text=True,
    )
    assert create.returncode == 0, create.stderr

    fresh = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "approval_receipt.py"),
            "verify",
            "--project-dir",
            str(tmp_path),
            "--receipt",
            str(receipt_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert fresh.returncode == 0, fresh.stderr

    video.write_text("final-v2", encoding="utf-8")
    stale = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "approval_receipt.py"),
            "verify",
            "--project-dir",
            str(tmp_path),
            "--receipt",
            str(receipt_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert stale.returncode == 2
    assert "stale" in stale.stderr


def test_cli_create_requires_replace_for_existing_receipt(tmp_path):
    video = tmp_path / "output" / "final.mp4"
    receipt_path = tmp_path / "verify" / "approval_receipt.json"
    _write(video, "final-v1")
    command = [
        sys.executable,
        os.path.join(REPO, "scripts", "approval_receipt.py"),
        "create",
        "--project-dir",
        str(tmp_path),
        "--artifact",
        str(video),
        "--approved-by",
        "Jay",
        "--output",
        str(receipt_path),
    ]

    first = subprocess.run(command, capture_output=True, text=True)
    refused = subprocess.run(command, capture_output=True, text=True)
    replaced = subprocess.run([*command, "--replace"], capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert refused.returncode == 1
    assert "pass --replace" in refused.stderr
    assert replaced.returncode == 0, replaced.stderr
