import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from render_final import build_bgm_mix_filter_lines, resolve_bgm_ducking  # noqa: E402


def test_bgm_ducking_is_off_by_default():
    assert resolve_bgm_ducking({}) == {"enabled": False}


def test_bgm_ducking_defaults_are_bounded():
    settings = resolve_bgm_ducking({"bgm_ducking": True})

    assert settings == {
        "enabled": True,
        "threshold": 0.03,
        "ratio": 8.0,
        "attack_ms": 20.0,
        "release_ms": 500.0,
    }


def test_cli_override_wins_over_render_config():
    assert resolve_bgm_ducking({"bgm_ducking": True}, False) == {"enabled": False}
    assert resolve_bgm_ducking({}, True)["enabled"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("bgm_ducking", "yes"),
        ("bgm_ducking_threshold", 0),
        ("bgm_ducking_ratio", 21),
        ("bgm_ducking_attack_ms", -1),
        ("bgm_ducking_release_ms", "slow"),
    ],
)
def test_invalid_bgm_ducking_settings_fail_early(key, value):
    config = {"bgm_ducking": True, key: value}

    with pytest.raises(ValueError, match=key):
        resolve_bgm_ducking(config)


def test_sidechain_graph_ducks_bgm_and_preserves_voice_mix():
    lines = build_bgm_mix_filter_lines(
        voice_label="[voice_a]",
        bgm_input_idx=2,
        bgm_total=8.0,
        bgm_volume=0.15,
        bgm_fade_out=2.0,
        ducking=resolve_bgm_ducking({"bgm_ducking": True}),
    )
    graph = ";".join(lines)

    assert "[voice_a]aformat=" in graph
    assert "asplit=2[voice_mix][voice_sc]" in graph
    assert "[bgm_a][voice_sc]sidechaincompress=" in graph
    assert "threshold=0.03:ratio=8:attack=20:release=500" in graph
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in graph
    assert "alimiter=limit=0.95:level=false[final_a]" in graph


def test_disabled_graph_keeps_legacy_constant_volume_mix():
    lines = build_bgm_mix_filter_lines(
        voice_label="[merged_a]",
        bgm_input_idx=1,
        bgm_total=4.0,
        bgm_volume=0.15,
        bgm_fade_out=1.0,
        ducking={"enabled": False},
    )
    graph = ";".join(lines)

    assert "sidechaincompress" not in graph
    assert "[merged_a][bgm_a]amix=inputs=2:duration=first:dropout_transition=0[final_a]" in graph


def test_help_exposes_bgm_ducking_overrides():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--bgm-ducking" in result.stdout
    assert "--no-bgm-ducking" in result.stdout


def _band_mean_db(media, start, duration):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(media),
            "-vn",
            "-af",
            "bandpass=f=220:width_type=h:w=40,volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    assert match, result.stderr
    return float(match.group(1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_real_render_ducks_music_during_voice(tmp_path):
    source = tmp_path / "source.mp4"
    bgm = tmp_path / "bgm.wav"
    transcript = tmp_path / "transcript.json"
    config = tmp_path / "render_config.json"
    output = tmp_path / "ducked.mp4"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=24:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=2",
            "-filter_complex",
            "[1:a]adelay=1000:all=1,apad=pad_dur=1[voice]",
            "-map",
            "0:v",
            "-map",
            "[voice]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000:duration=4",
            "-c:a",
            "pcm_s16le",
            str(bgm),
        ],
        check=True,
    )
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"id": 1, "start": 0.0, "end": 4.0, "text": "voice"}
                ]
            }
        ),
        encoding="utf-8",
    )
    config.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "video": str(source),
                        "transcript": str(transcript),
                        "segment_id": 1,
                    }
                ],
                "bgm": str(bgm),
                "bgm_volume": 0.4,
                "bgm_fade_out": 0,
                "bgm_ducking": True,
                "bgm_ducking_threshold": 0.01,
                "bgm_ducking_ratio": 20,
                "bgm_ducking_attack_ms": 5,
                "bgm_ducking_release_ms": 100,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/render_final.py"),
            "--config",
            str(config),
            "--output",
            str(output),
            "--no-subtitles",
            "--no-cover",
            "--no-loudnorm",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.is_file()
    music_without_voice = _band_mean_db(output, 0.2, 0.6)
    music_under_voice = _band_mean_db(output, 1.5, 1.0)
    assert music_under_voice <= music_without_voice - 10.0
