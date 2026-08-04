import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import edit_revision  # noqa: E402
from edit_revision import (  # noqa: E402
    APPROVAL_VERSION,
    RevisionError,
    apply_revision,
    audit_proposal,
    prepare_proposal,
    redo_revision,
    undo_revision,
    verify_history,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")


def _approved(review_id, label="Jay"):
    return {
        "version": APPROVAL_VERSION,
        "review_id": review_id,
        "decision": "approve",
        "approved_by_label": label,
    }


def _ready_proposal(tmp_path, *, two=False):
    config = tmp_path / "work" / "render_config.json"
    transcript = tmp_path / "work" / "transcript.json"
    _write(config, {"clips": [{"start": 0, "end": 8}]})
    _write(transcript, {"segments": [{"id": 1, "text": "hello"}]})
    artifacts = [str(config)]
    if two:
        caption = tmp_path / "work" / "caption.md"
        _write(caption, "old caption\n")
        artifacts.append(str(caption))
    proposal = prepare_proposal(
        str(tmp_path),
        artifacts,
        dependencies=[str(transcript)],
        title="Tighten the opening",
        reason="Remove the slow first beat after review.",
    )
    proposal["artifacts"][0]["proposed_content"] = json.dumps(
        {"clips": [{"start": 1, "end": 8}]}, ensure_ascii=False, indent=2
    ) + "\n"
    if two:
        proposal["artifacts"][1]["proposed_content"] = "new caption\n"
    return proposal, config, transcript


def test_prepare_binds_artifact_and_dependency_hashes(tmp_path):
    proposal, config, transcript = _ready_proposal(tmp_path)

    assert proposal["artifacts"][0]["path"] == "work/render_config.json"
    assert len(proposal["artifacts"][0]["base"]["sha256"]) == 64
    assert proposal["dependencies"][0]["path"] == "work/transcript.json"
    assert proposal["artifacts"][0]["proposed_content"] != config.read_text(encoding="utf-8")
    assert proposal["dependencies"][0]["size_bytes"] == transcript.stat().st_size


def test_audit_requires_a_real_change_title_and_reason(tmp_path):
    config = tmp_path / "work" / "render_config.json"
    _write(config, {"clips": []})
    proposal = prepare_proposal(str(tmp_path), [str(config)])

    audit = audit_proposal(str(tmp_path), proposal)

    assert audit["status"] == "blocked"
    assert audit["summary"]["blocking"] >= 3
    assert "title must not be empty" in audit["issues"]
    assert "reason must not be empty" in audit["issues"]
    assert any("does not change" in issue for issue in audit["issues"])


def test_audit_emits_stable_source_bound_approval_template(tmp_path):
    proposal, _, _ = _ready_proposal(tmp_path)

    first = audit_proposal(str(tmp_path), proposal)
    second = audit_proposal(str(tmp_path), proposal)

    assert first["status"] == "pending_approval"
    assert first["summary"]["blocking"] == 1
    assert first["review_id"] == second["review_id"]
    assert first["approval_template"]["review_id"] == first["review_id"]


def test_audit_rejects_stale_base_and_dependency(tmp_path):
    proposal, config, transcript = _ready_proposal(tmp_path)
    _write(config, {"clips": [{"start": 2, "end": 8}]})
    _write(transcript, {"segments": [{"id": 1, "text": "changed"}]})

    audit = audit_proposal(str(tmp_path), proposal)

    assert audit["status"] == "blocked"
    assert any("base artifact changed" in issue for issue in audit["issues"])
    assert any("dependency changed" in issue for issue in audit["issues"])


def test_audit_rejects_invalid_json_and_duplicate_paths(tmp_path):
    proposal, _, _ = _ready_proposal(tmp_path)
    proposal["artifacts"][0]["proposed_content"] = "{not-json}"
    proposal["artifacts"].append(dict(proposal["artifacts"][0]))

    audit = audit_proposal(str(tmp_path), proposal)

    assert audit["status"] == "blocked"
    assert any("proposed JSON is invalid" in issue for issue in audit["issues"])
    assert any("duplicate artifact path" in issue for issue in audit["issues"])


def test_prepare_rejects_source_media_code_and_symlinks(tmp_path):
    source = tmp_path / "origin" / "source.txt"
    script = tmp_path / "scripts" / "helper.json"
    target = tmp_path / "work" / "target.json"
    link = tmp_path / "work" / "link.json"
    readme = tmp_path / "README.md"
    _write(source, "source")
    _write(script, {})
    _write(target, {})
    _write(readme, "project docs")
    link.symlink_to(target)

    with pytest.raises(RevisionError, match="read-only"):
        prepare_proposal(str(tmp_path), [str(source)])
    with pytest.raises(RevisionError, match="read-only"):
        prepare_proposal(str(tmp_path), [str(script)])
    with pytest.raises(RevisionError, match="symlink"):
        prepare_proposal(str(tmp_path), [str(link)])
    with pytest.raises(RevisionError, match="document files are read-only"):
        prepare_proposal(str(tmp_path), [str(readme)])


def test_apply_commits_multiple_artifacts_as_one_revision(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path, two=True)
    audit = audit_proposal(str(tmp_path), proposal)

    history, operation = apply_revision(
        str(tmp_path), proposal, audit, _approved(audit["review_id"])
    )

    assert json.loads(config.read_text(encoding="utf-8"))["clips"][0]["start"] == 1
    assert (tmp_path / "work" / "caption.md").read_text(encoding="utf-8") == "new caption\n"
    assert history["cursor"] == 1
    assert history["summary"]["applied"] == 1
    assert len(operation["artifacts"]) == 2
    assert verify_history(history, str(tmp_path))["status"] == "current"


def test_group_write_rolls_back_files_on_runtime_replace_error(tmp_path, monkeypatch):
    first = tmp_path / "work" / "first.txt"
    second = tmp_path / "work" / "second.txt"
    _write(first, "first-before")
    _write(second, "second-before")
    real_replace = edit_revision.os.replace
    calls = {"count": 0}

    def flaky_replace(source, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated second-file replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(edit_revision.os, "replace", flaky_replace)
    with pytest.raises(OSError, match="simulated"):
        edit_revision._atomic_write_batch(
            [
                (first, b"first-after", b"first-before"),
                (second, b"second-after", b"second-before"),
            ]
        )

    assert first.read_bytes() == b"first-before"
    assert second.read_bytes() == b"second-before"


def test_group_write_rechecks_all_targets_before_replacing(tmp_path):
    first = tmp_path / "work" / "first.txt"
    second = tmp_path / "work" / "second.txt"
    _write(first, "first-current")
    _write(second, "second-current")

    with pytest.raises(RevisionError, match="target changed before grouped write"):
        edit_revision._atomic_write_batch(
            [
                (first, b"first-after", b"stale-first"),
                (second, b"second-after", b"second-current"),
            ]
        )

    assert first.read_bytes() == b"first-current"
    assert second.read_bytes() == b"second-current"


@pytest.mark.parametrize(
    "approval",
    [
        {"version": APPROVAL_VERSION, "review_id": "wrong", "decision": "approve", "approved_by_label": "Jay"},
        {"version": APPROVAL_VERSION, "review_id": "placeholder", "decision": "reject", "approved_by_label": "Jay"},
        {"version": APPROVAL_VERSION, "review_id": "placeholder", "decision": "approve", "approved_by_label": ""},
    ],
)
def test_apply_requires_bound_explicit_approval_without_partial_write(tmp_path, approval):
    proposal, config, _ = _ready_proposal(tmp_path, two=True)
    audit = audit_proposal(str(tmp_path), proposal)
    if approval["review_id"] == "placeholder":
        approval["review_id"] = audit["review_id"]
    before_config = config.read_bytes()
    before_caption = (tmp_path / "work" / "caption.md").read_bytes()

    with pytest.raises(RevisionError):
        apply_revision(str(tmp_path), proposal, audit, approval)

    assert config.read_bytes() == before_config
    assert (tmp_path / "work" / "caption.md").read_bytes() == before_caption
    assert not (tmp_path / "work" / "edit_revision_history.json").exists()


def test_apply_reaudits_live_files_and_rejects_post_audit_drift(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    _write(config, {"clips": [{"start": 3, "end": 8}]})

    with pytest.raises(RevisionError, match="stale"):
        apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))

    assert json.loads(config.read_text(encoding="utf-8"))["clips"][0]["start"] == 3


def test_undo_and_redo_restore_exact_bytes(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path)
    before = config.read_bytes()
    audit = audit_proposal(str(tmp_path), proposal)
    history, _ = apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    after = config.read_bytes()

    undone, operation = undo_revision(str(tmp_path))
    assert config.read_bytes() == before
    assert undone["cursor"] == 0
    assert operation["operation_id"] == history["operations"][0]["operation_id"]
    assert verify_history(undone, str(tmp_path))["status"] == "current"

    redone, _ = redo_revision(str(tmp_path))
    assert config.read_bytes() == after
    assert redone["cursor"] == 1
    assert verify_history(redone, str(tmp_path))["status"] == "current"


def test_undo_refuses_external_artifact_change(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    _write(config, {"clips": [{"start": 99, "end": 100}]})

    with pytest.raises(RevisionError, match="history is stale"):
        undo_revision(str(tmp_path))


def test_undo_refuses_drift_in_an_older_tracked_artifact(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path, two=True)
    first_audit = audit_proposal(str(tmp_path), proposal)
    apply_revision(str(tmp_path), proposal, first_audit, _approved(first_audit["review_id"]))

    caption = tmp_path / "work" / "caption.md"
    second = prepare_proposal(
        str(tmp_path),
        [str(caption)],
        title="Shorten the caption",
        reason="Keep the selected title but remove one line.",
    )
    second["artifacts"][0]["proposed_content"] = "short caption\n"
    second_audit = audit_proposal(str(tmp_path), second)
    apply_revision(str(tmp_path), second, second_audit, _approved(second_audit["review_id"]))
    _write(config, {"clips": [{"start": 50, "end": 60}]})

    with pytest.raises(RevisionError, match="history is stale"):
        undo_revision(str(tmp_path))


def test_redo_refuses_changed_based_on_dependency(tmp_path):
    proposal, _, transcript = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    undo_revision(str(tmp_path))
    _write(transcript, {"segments": [{"id": 1, "text": "new truth"}]})

    with pytest.raises(RevisionError, match="dependency changed"):
        redo_revision(str(tmp_path))


def test_new_revision_after_undo_requires_explicit_history_fork(tmp_path):
    proposal, config, _ = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    undo_revision(str(tmp_path))

    next_proposal = prepare_proposal(
        str(tmp_path),
        [str(config)],
        title="Choose a different opening",
        reason="The first approved revision was undone after review.",
    )
    next_proposal["artifacts"][0]["proposed_content"] = json.dumps(
        {"clips": [{"start": 2, "end": 8}]}, indent=2
    ) + "\n"
    next_audit = audit_proposal(str(tmp_path), next_proposal)

    with pytest.raises(RevisionError, match="redo revisions are pending"):
        apply_revision(
            str(tmp_path),
            next_proposal,
            next_audit,
            _approved(next_audit["review_id"]),
        )

    history, operation = apply_revision(
        str(tmp_path),
        next_proposal,
        next_audit,
        _approved(next_audit["review_id"]),
        fork_history=True,
    )
    assert history["cursor"] == 1
    assert len(history["operations"]) == 1
    assert len(history["archived_branches"]) == 1
    assert operation["title"] == "Choose a different opening"
    assert json.loads(config.read_text(encoding="utf-8"))["clips"][0]["start"] == 2


def test_history_verification_detects_artifact_dependency_and_blob_drift(tmp_path):
    proposal, config, transcript = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    history, operation = apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    _write(config, {"clips": [{"start": 7, "end": 8}]})
    _write(transcript, {"segments": []})
    blob = tmp_path / operation["artifacts"][0]["after_blob"]
    blob.write_bytes(b"tampered")

    verification = verify_history(history, str(tmp_path))

    assert verification["status"] == "stale"
    assert verification["summary"]["blocking"] >= 3
    assert any(item["status"] == "changed" for item in verification["artifacts"])
    assert any(item["status"] == "changed" for item in verification["dependencies"])
    assert any(item["status"] == "changed" for item in verification["blobs"])


def test_history_verification_rejects_blob_store_not_bound_to_journal(tmp_path):
    proposal, _, _ = _ready_proposal(tmp_path)
    audit = audit_proposal(str(tmp_path), proposal)
    history, _ = apply_revision(str(tmp_path), proposal, audit, _approved(audit["review_id"]))
    history["blob_store"] = "work"

    verification = verify_history(history, str(tmp_path))

    assert verification["status"] == "stale"
    assert "blob_store does not match journal_path" in verification["issues"]


def test_cli_prepare_audit_apply_status_undo_redo_round_trip(tmp_path):
    config = tmp_path / "work" / "render_config.json"
    _write(config, {"clips": []})
    proposal_path = tmp_path / "work" / "edit-proposal.json"
    audit_path = tmp_path / "work" / "edit-audit.json"
    approval_path = tmp_path / "work" / "edit-approval.json"
    script = os.path.join(REPO, "scripts", "edit_revision.py")

    prepare = subprocess.run(
        [
            sys.executable,
            script,
            "prepare",
            "--project-dir",
            str(tmp_path),
            "--artifact",
            str(config),
            "--title",
            "Add selected clip",
            "--reason",
            "The reviewed take is ready.",
            "--output",
            str(proposal_path),
        ],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["artifacts"][0]["proposed_content"] = json.dumps({"clips": [{"start": 1, "end": 2}]})
    _write(proposal_path, proposal)

    audit = subprocess.run(
        [sys.executable, script, "audit", "--project-dir", str(tmp_path), "--proposal", str(proposal_path), "--output", str(audit_path)],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 0, audit.stderr
    audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    _write(approval_path, _approved(audit_data["review_id"]))

    for command in ("apply", "status", "undo", "redo"):
        args = [sys.executable, script, command, "--project-dir", str(tmp_path), "--strict"]
        if command == "apply":
            args.extend(["--proposal", str(proposal_path), "--audit", str(audit_path), "--approval", str(approval_path)])
        result = subprocess.run(args, capture_output=True, text=True)
        assert result.returncode == 0, f"{command}: {result.stderr}"

    assert json.loads(config.read_text(encoding="utf-8"))["clips"][0]["start"] == 1
