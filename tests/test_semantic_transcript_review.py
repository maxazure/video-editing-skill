import json
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from semantic_transcript_review import (  # noqa: E402
    SemanticReviewError,
    apply_choices,
    audit_response,
    build_review_request,
    emit_audit_markdown,
    transcript_sha256,
)


def sample_transcript():
    return {
        "language": "zh",
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 2.0,
                "text": "今天用检映做视频",
                "words": [
                    {"word": "今天", "start": 0.0, "end": 0.5},
                    {"word": "用检映", "start": 0.5, "end": 1.2},
                    {"word": "做视频", "start": 1.2, "end": 2.0},
                ],
            },
            {"id": 2, "start": 2.0, "end": 4.0, "text": "OpenClow 很好用"},
            {"id": 3, "start": 4.0, "end": 6.0, "text": "价格是 100 元。"},
        ],
    }


def response_for(transcript, proposals=None, reviewed=None):
    return {
        "version": "semantic_transcript_review.v1",
        "source_sha256": transcript_sha256(transcript),
        "reviewed_segment_ids": reviewed or ["1", "2", "3"],
        "proposals": proposals or [],
    }


def cjk_proposal(**overrides):
    proposal = {
        "segment_id": "1",
        "span_start": 3,
        "span_end": 4,
        "source": "检",
        "replacement": "剪",
        "category": "homophone",
        "confidence": 0.98,
        "recommendation": "accept",
        "reason": "上下文是视频剪辑软件。",
    }
    proposal.update(overrides)
    return proposal


def test_prepare_builds_bounded_context_and_source_bound_response_template():
    transcript = sample_transcript()
    request = build_review_request(transcript, context_radius=1)

    assert request["artifact_type"] == "request"
    assert request["source"]["sha256"] == transcript_sha256(transcript)
    assert request["units"][0]["next"] == [{"segment_id": "2", "text": "OpenClow 很好用"}]
    assert request["units"][1]["previous"][0]["segment_id"] == "1"
    assert request["response_template"]["reviewed_segment_ids"] == ["1", "2", "3"]


def test_audit_derives_complete_coverage_and_requires_human_choice():
    transcript = sample_transcript()
    audit = audit_response(transcript, response_for(transcript, [cjk_proposal()]))

    assert audit["coverage"]["complete"] is True
    assert audit["summary"]["valid"] == 1
    assert audit["summary"]["pending_choices"] == 1
    assert audit["summary"]["blocking"] == 1
    assert audit["proposals"][0]["proposal_id"].startswith("patch-")
    assert "choices" in emit_audit_markdown(audit).lower()


def test_audit_blocks_partial_or_stale_coverage():
    transcript = sample_transcript()
    response = response_for(transcript, reviewed=["1", "2"])
    response["source_sha256"] = "0" * 64

    audit = audit_response(transcript, response)

    assert audit["coverage"]["complete"] is False
    assert audit["summary"]["validation_blocking"] == 2
    assert any("source_sha256" in item for item in audit["validation_blockers"])
    assert any("partial" in item for item in audit["validation_blockers"])


@pytest.mark.parametrize(
    "proposal,issue",
    [
        (cjk_proposal(span_start=2, span_end=5, source="用检映", replacement="用剪映"), "not minimal"),
        (cjk_proposal(replacement="剪。"), "punctuation"),
        (
            {
                "segment_id": "3",
                "span_start": 4,
                "span_end": 7,
                "source": "100",
                "replacement": "200",
                "category": "typo",
                "confidence": 1,
                "recommendation": "accept",
                "reason": "guess",
            },
            "numbers",
        ),
        (cjk_proposal(span_start=0, span_end=1), "exact target"),
    ],
)
def test_audit_rejects_unsafe_or_nonminimal_patches(proposal, issue):
    transcript = sample_transcript()
    audit = audit_response(transcript, response_for(transcript, [proposal]))

    assert audit["summary"]["invalid"] == 1
    assert any(issue in item for item in audit["proposals"][0]["issues"])


def test_audit_rejects_overlapping_patches():
    transcript = sample_transcript()
    second = cjk_proposal(
        span_start=3,
        span_end=5,
        source="检映",
        replacement="剪影",
        category="word_choice",
    )

    audit = audit_response(transcript, response_for(transcript, [cjk_proposal(), second]))

    assert audit["summary"]["invalid"] == 2
    assert all("overlaps" in " ".join(item["issues"]) for item in audit["proposals"])


def test_apply_requires_choices_bound_to_review_id_and_source():
    transcript = sample_transcript()
    audit = audit_response(transcript, response_for(transcript, [cjk_proposal()]))
    proposal_id = audit["proposals"][0]["proposal_id"]
    choices = {
        "version": "semantic_transcript_review.v1",
        "source_sha256": transcript_sha256(transcript),
        "review_id": "stale-review",
        "reviewer": "Jay",
        "choices": {proposal_id: "approve"},
    }

    with pytest.raises(SemanticReviewError, match="review_id"):
        apply_choices(transcript, audit, choices)


def test_apply_approved_patch_and_redistribute_changed_words():
    transcript = sample_transcript()
    audit = audit_response(transcript, response_for(transcript, [cjk_proposal()]))
    proposal_id = audit["proposals"][0]["proposal_id"]
    choices = {
        "version": "semantic_transcript_review.v1",
        "source_sha256": transcript_sha256(transcript),
        "review_id": audit["review_id"],
        "reviewer": "Jay",
        "choices": {proposal_id: "approve"},
    }

    updated, result = apply_choices(transcript, audit, choices)

    assert updated["segments"][0]["text"] == "今天用剪映做视频"
    assert updated["segments"][0]["words"][-1]["end"] == 2.0
    assert updated["semantic_review"]["approved"] == 1
    assert result["artifact_type"] == "result"
    assert result["summary"]["blocking"] == 0
    assert result["proposals"][0]["choice"] == "approve"


def test_apply_rejected_patch_preserves_text():
    transcript = sample_transcript()
    audit = audit_response(transcript, response_for(transcript, [cjk_proposal()]))
    proposal_id = audit["proposals"][0]["proposal_id"]
    choices = {
        "version": "semantic_transcript_review.v1",
        "source_sha256": transcript_sha256(transcript),
        "review_id": audit["review_id"],
        "reviewer": "Jay",
        "choices": {proposal_id: "reject"},
    }

    updated, result = apply_choices(transcript, audit, choices)

    assert updated["segments"][0]["text"] == transcript["segments"][0]["text"]
    assert result["summary"]["rejected"] == 1
    assert result["summary"]["applied"] == 0


def test_cli_prepare_audit_apply_round_trip(tmp_path):
    transcript = sample_transcript()
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    request_path = tmp_path / "semantic_request.json"
    response_path = tmp_path / "semantic_response.json"
    audit_path = tmp_path / "transcript_semantic_review.json"
    choices_path = tmp_path / "choices.json"
    reviewed_path = tmp_path / "transcript_reviewed.json"
    script = os.path.join(REPO, "scripts", "semantic_transcript_review.py")

    prepare = subprocess.run(
        [sys.executable, script, "prepare", "--transcript", str(transcript_path), "--output", str(request_path)],
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    response_path.write_text(
        json.dumps(response_for(transcript, [cjk_proposal()]), ensure_ascii=False),
        encoding="utf-8",
    )
    audit = subprocess.run(
        [
            sys.executable,
            script,
            "audit",
            "--transcript",
            str(transcript_path),
            "--response",
            str(response_path),
            "--output",
            str(audit_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 2
    audited = json.loads(audit_path.read_text(encoding="utf-8"))
    proposal_id = audited["proposals"][0]["proposal_id"]
    choices_path.write_text(
        json.dumps(
            {
                "version": "semantic_transcript_review.v1",
                "source_sha256": transcript_sha256(transcript),
                "review_id": audited["review_id"],
                "reviewer": "Jay",
                "choices": {proposal_id: "approve"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    apply = subprocess.run(
        [
            sys.executable,
            script,
            "apply",
            "--transcript",
            str(transcript_path),
            "--audit",
            str(audit_path),
            "--choices",
            str(choices_path),
            "--output",
            str(reviewed_path),
        ],
        capture_output=True,
        text=True,
    )
    assert apply.returncode == 0, apply.stderr
    assert json.loads(reviewed_path.read_text(encoding="utf-8"))["segments"][0]["text"] == "今天用剪映做视频"
    assert json.loads(audit_path.read_text(encoding="utf-8"))["status"] == "ready"
