"""render_final.py defaults the speech audio chain to dynaudnorm + compressor + loudnorm.

This was a day58 production regression: after a 1.25x speed change the mid
section became noticeably quiet. The fix was to add the normalisation chain
into the af_parts pipeline. It now runs by default; opt out with --no-loudnorm.
"""
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_help_advertises_no_loudnorm_flag():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "--no-loudnorm" in out.stdout, (
        "render_final.py should expose --no-loudnorm to disable the default chain.\n"
        f"stdout: {out.stdout}"
    )


def test_source_contains_default_loudness_chain():
    """The default audio chain must include dynaudnorm + acompressor + loudnorm."""
    src = open(os.path.join(REPO, "scripts/render_final.py")).read()
    for f in ("dynaudnorm", "acompressor", "loudnorm"):
        assert f in src, f"Audio chain is missing the {f!r} filter"


def test_loudness_chain_is_gated_by_no_loudnorm():
    """The chain must be inside an `if not args.no_loudnorm:` block."""
    src = open(os.path.join(REPO, "scripts/render_final.py")).read()
    # crude but effective: the no_loudnorm gate appears before the first loudnorm append
    no_lnorm_idx = src.find("args.no_loudnorm")
    loudnorm_append_idx = src.find('"loudnorm=I=-16')
    assert no_lnorm_idx > 0 and loudnorm_append_idx > 0
    assert no_lnorm_idx < loudnorm_append_idx, (
        "loudnorm append should come AFTER the args.no_loudnorm check"
    )
