import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from render_final import (  # noqa: E402
    SPEECH_DENOISE_PRESETS,
    build_speech_audio_filters,
    build_speech_denoise_filters,
    resolve_speech_denoise,
)


def test_speech_denoise_is_off_by_default():
    assert resolve_speech_denoise({}) == {"enabled": False}
    assert build_speech_denoise_filters({"enabled": False}) == []


def test_medium_config_selects_medium_preset():
    settings = resolve_speech_denoise({"speech_denoise": "medium"})

    assert settings["preset"] == "medium"
    assert settings["highpass_hz"] == 80
    assert settings["noise_reduction_db"] == 9.0


@pytest.mark.parametrize("preset", ["light", "medium", "strong"])
def test_presets_are_conservative_and_bounded(preset):
    settings = resolve_speech_denoise({"speech_denoise": preset})
    filters = build_speech_denoise_filters(settings)

    assert settings["noise_reduction_db"] <= 12
    assert filters[0] == "highpass=f=80:p=2"
    assert filters[1].startswith(
        f"afftdn=nr={settings['noise_reduction_db']:g}:"
    )
    assert ":tn=1:" in filters[1]


def test_cli_override_wins_over_config():
    assert resolve_speech_denoise({"speech_denoise": "strong"}, "off") == {
        "enabled": False
    }
    assert resolve_speech_denoise({}, "light")["preset"] == "light"


@pytest.mark.parametrize("value", ["maximum", True, False, 3, [], {}])
def test_invalid_speech_denoise_config_fails_early(value):
    with pytest.raises(ValueError, match="speech_denoise"):
        resolve_speech_denoise({"speech_denoise": value})


def test_filter_order_is_denoise_then_speed_then_dynamics_then_cover():
    filters = build_speech_audio_filters(
        denoise=resolve_speech_denoise({"speech_denoise": "medium"}),
        speed=1.25,
        loudness_enabled=True,
        cover_duration=2.0,
    )

    assert filters == [
        "highpass=f=80:p=2",
        "afftdn=nr=9:nf=-45:tn=1:gs=8",
        "atempo=1.2500",
        "dynaudnorm=f=250:g=15",
        "acompressor=threshold=-18dB:ratio=3:attack=20:release=200",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "adelay=2000:all=1",
    ]


def test_help_exposes_denoise_overrides():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--speech-denoise" in result.stdout
    assert "--no-speech-denoise" in result.stdout
    assert all(name in result.stdout for name in SPEECH_DENOISE_PRESETS)


def _band_mean_db(media, frequency):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            "1.2",
            "-t",
            "0.6",
            "-i",
            str(media),
            "-vn",
            "-af",
            f"bandpass=f={frequency}:width_type=h:w=80,volumedetect",
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
def test_real_render_improves_during_speech_snr_in_single_pass(tmp_path):
    source = tmp_path / "source.mp4"
    transcript = tmp_path / "transcript.json"
    config = tmp_path / "render_config.json"
    output = tmp_path / "denoised.mp4"

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
            "color=c=black:s=160x90:r=24:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=50:sample_rate=48000:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=3",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=white:amplitude=0.08:sample_rate=48000:duration=3",
            "-filter_complex",
            (
                "[1:a]volume=0.2[rumble];"
                "[2:a]volume=0.45:"
                "enable='between(t,1,2)'[voice];"
                "[3:a]volume=0.5[noise];"
                "[rumble][voice][noise]"
                "amix=inputs=3:duration=longest:normalize=0[a]"
            ),
            "-map",
            "0:v",
            "-map",
            "[a]",
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
    transcript.write_text(
        json.dumps(
            {"segments": [{"id": 1, "start": 0.0, "end": 3.0, "text": "voice"}]}
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
                "speech_denoise": "strong",
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
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Speech denoise: strong" in result.stdout
    input_snr = _band_mean_db(source, 1000) - _band_mean_db(source, 6000)
    output_snr = _band_mean_db(output, 1000) - _band_mean_db(output, 6000)
    assert output_snr >= input_snr + 3.0
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,sample_rate",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(probe.stdout)
    assert float(payload["format"]["duration"]) == pytest.approx(3.0, abs=0.08)
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    assert int(audio["sample_rate"]) >= 48000
