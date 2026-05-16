"""--primary-speed makes the speed-adjusted version the main output.

Before this change, `--speed 1.25` rendered both a 1.0× base and a 1.25× variant.
Day58 wanted the 1.25× version to BE the deliverable, no 1.0× base. The new
flag --primary-speed N sets the rate of the main output directly.
"""
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_help_advertises_primary_speed():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "--primary-speed" in out.stdout, (
        "render_final.py should expose --primary-speed.\n" + out.stdout
    )


def test_source_composes_all_speeds_with_primary_first():
    """Source must order all_speeds with primary first, then dedup'd extras."""
    src = open(os.path.join(REPO, "scripts/render_final.py")).read()
    # The composition line lives in main()
    assert "[primary] + [s for s in args.speed if s != primary]" in src, (
        "Expected `all_speeds = [primary] + [s for s in args.speed if s != primary]` "
        "so the primary output speed comes first in the render loop."
    )


def test_source_writes_first_speed_to_output_path():
    """idx==0 (the primary) must write to the requested --output, not a suffixed path."""
    src = open(os.path.join(REPO, "scripts/render_final.py")).read()
    # Look for the dispatch that writes idx 0 -> output_path
    assert "if idx == 0:" in src and "out_path = output_path" in src, (
        "Primary speed (first in all_speeds) should write to the requested "
        "--output path regardless of its rate."
    )
