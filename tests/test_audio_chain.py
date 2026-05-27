"""render_final.py defaults the speech audio chain to dynaudnorm + compressor + loudnorm.

This was a day58 production regression: after a 1.25x speed change the mid
section became noticeably quiet. The fix was to add the normalisation chain
into the af_parts pipeline. It now runs by default; opt out with --no-loudnorm.
"""
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from render_final import build_bgm_mix_filter_ops  # noqa: E402


def test_help_advertises_no_loudnorm_flag():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "--no-loudnorm" in out.stdout, (
        "render_final.py should expose --no-loudnorm to disable the default chain.\n"
        f"stdout: {out.stdout}"
    )


def test_help_advertises_bgm_ducking_flags():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True, text=True, check=True,
    )

    assert "--bgm-ducking" in out.stdout
    assert "--no-bgm-ducking" in out.stdout
    assert "--bgm-fade-in" in out.stdout


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


def test_bgm_mix_filter_keeps_static_mix_by_default():
    lines, map_a = build_bgm_mix_filter_ops(
        bgm_input_idx=3,
        voice_label="[voice_a]",
        bgm_total=12.0,
        bgm_volume=0.2,
        bgm_fade_in=1.5,
        bgm_fade_out=2.0,
        ducking=False,
    )
    chain = ";".join(lines)

    assert map_a == "[final_a]"
    assert "volume=0.200" in chain
    assert "afade=t=in:st=0:d=1.5000" in chain
    assert "afade=t=out:st=10.0000:d=2.0000" in chain
    assert "sidechaincompress" not in chain
    assert "[voice_a][bgm_a]amix=inputs=2" in chain


def test_bgm_mix_filter_ducking_sidechains_music_under_voice():
    lines, map_a = build_bgm_mix_filter_ops(
        bgm_input_idx=2,
        voice_label="[voice_a]",
        bgm_total=8.0,
        bgm_volume=0.15,
        ducking=True,
        duck_threshold=0.04,
        duck_ratio=10,
        duck_attack=15,
        duck_release=300,
    )
    chain = ";".join(lines)

    assert map_a == "[final_a]"
    assert "[voice_a]asplit=2[voice_mix][voice_sc]" in chain
    assert "[bgm_base][voice_sc]sidechaincompress" in chain
    assert "threshold=0.0400" in chain
    assert "ratio=10.00" in chain
    assert "[voice_mix][bgm_a]amix=inputs=2" in chain
