import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from provider_capability import (  # noqa: E402
    BUNDLE_VERSION,
    profile_support_issues,
    verify_bundle,
)


def capability_bundle(*, verified_at="2026-08-21", source_type="official_documentation"):
    return {
        "version": BUNDLE_VERSION,
        "profiles": [
            {
                "provider": "dreamina_seedance",
                "surface": "Dreamina web",
                "model": "Seedance verified test model",
                "verified_at": verified_at,
                "sources": [
                    {
                        "source_type": source_type,
                        "url": "https://example.com/provider-docs",
                        "note": "Operator checked the named surface.",
                    }
                ],
                "capabilities": {
                    "modes": ["text_to_video", "image_to_video"],
                    "aspect_ratios": ["9:16", "16:9"],
                    "resolutions": ["720p"],
                    "duration": {
                        "kind": "range",
                        "min_seconds": 2,
                        "max_seconds": 8,
                    },
                    "reference_limits": {"images": 2, "videos": 0, "audio": 1},
                    "audio": {
                        "generate": True,
                        "reference": True,
                        "preserve_source": "unknown",
                    },
                },
            }
        ],
    }


def test_verified_surface_profile_is_ready():
    report = verify_bundle(
        capability_bundle(),
        today=date(2026, 8, 21),
        max_age_days=30,
        require_fresh=True,
    )

    assert report["status"] == "ready"
    assert report["summary"] == {
        "profiles": 1,
        "blocking": 0,
        "warnings": 0,
        "official_profiles": 1,
    }
    assert report["profiles"][0]["profile_id"].startswith("vpc_")


def test_stale_profile_blocks_when_freshness_is_required():
    report = verify_bundle(
        capability_bundle(verified_at="2026-07-01"),
        today=date(2026, 8, 21),
        max_age_days=30,
        require_fresh=True,
    )

    assert report["status"] == "blocked"
    assert any("stale_profile" in issue for issue in report["blockers"])


def test_community_only_profile_is_labeled_for_review_not_rejected():
    report = verify_bundle(
        capability_bundle(source_type="community"),
        today=date(2026, 8, 21),
    )

    assert report["status"] == "review"
    assert report["summary"]["blocking"] == 0
    assert report["warnings"] == ["dreamina_seedance:no_official_source"]


def test_support_check_rejects_unverified_settings_and_reference_overflow():
    profile = capability_bundle()["profiles"][0]
    issues = profile_support_issues(
        profile,
        provider="dreamina_seedance",
        mode="video_extension",
        aspect="3:4",
        duration_seconds=10,
        resolution="1080p",
        image_references=3,
    )

    assert issues == [
        "unsupported_mode:video_extension",
        "unsupported_aspect:3:4",
        "unsupported_duration:10",
        "unsupported_resolution:1080p",
        "image_reference_limit_exceeded:3>2",
    ]


def test_fixed_duration_profile_accepts_only_declared_values():
    bundle = capability_bundle()
    profile = bundle["profiles"][0]
    profile["capabilities"]["duration"] = {
        "kind": "fixed",
        "values_seconds": [4, 8],
    }

    report = verify_bundle(bundle, today=date(2026, 8, 21))
    issues = profile_support_issues(
        profile,
        provider="dreamina_seedance",
        mode="text_to_video",
        aspect="9:16",
        duration_seconds=5,
        resolution="720p",
    )

    assert report["status"] == "ready"
    assert issues == ["unsupported_duration:5"]


def test_cli_verifies_bundle_and_writes_reports(tmp_path):
    bundle = capability_bundle(
        verified_at=datetime.now(timezone.utc).date().isoformat(),
    )
    bundle_path = tmp_path / "provider_capabilities.json"
    output_path = tmp_path / "provider_capabilities_verification.json"
    markdown_path = tmp_path / "provider_capabilities_verification.md"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/provider_capability.py"),
            "verify",
            "--bundle",
            str(bundle_path),
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "ready"
    assert "Dreamina web" in markdown_path.read_text(encoding="utf-8")
