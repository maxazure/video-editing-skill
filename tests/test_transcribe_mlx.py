"""Smoke tests: mlx-whisper backend wired into transcribe.py + utils.py."""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_engine_choices_include_mlx():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/transcribe.py"), "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "mlx-whisper" in out.stdout, (
        f"--engine should advertise mlx-whisper.\nstdout: {out.stdout}"
    )


def test_utils_diagnostics_runs():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/utils.py")],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, (
        f"utils.py crashed.\nstderr: {out.stderr}\nstdout: {out.stdout}"
    )
    assert "Whisper engine" in out.stdout


def test_utils_lists_mlx_in_engine_field_when_available():
    """On Apple Silicon with mlx-whisper installed, auto-detect should pick it."""
    try:
        import platform
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return  # only verify on Apple Silicon
        import importlib.util
        if importlib.util.find_spec("mlx_whisper") is None:
            return
    except Exception:
        return
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/utils.py")],
        capture_output=True, text=True, check=True,
    )
    assert "mlx-whisper" in out.stdout, (
        "Apple Silicon + mlx_whisper installed → auto-detect should report mlx-whisper.\n"
        f"stdout: {out.stdout}"
    )
