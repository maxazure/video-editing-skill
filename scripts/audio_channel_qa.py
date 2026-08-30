#!/usr/bin/env python3
"""Audit final-delivery audio channel integrity with FFmpeg metadata.

Mono sources pass this channel-specific gate without stereo analysis. Stereo
sources are checked for missing-channel activity, left/right level imbalance,
interchannel onset skew, negative phase correlation, and mono fold-down loss.
Multichannel sources fail closed because this short-form delivery workflow does
not define a surround downmix contract.

The command is read-only: it never rewrites media, changes channel layout, or
calls a provider. It complements, but does not replace, normal-speed listening,
``audio_master_report.py``, or sync review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


VERSION = "audio_channel_qa.v1"
VERIFY_VERSION = "audio_channel_qa_verify.v1"
DEFAULT_SETTINGS: Mapping[str, Any] = {
    "analysis_sample_rate": 8000,
    "window_ms": 50,
    "activity_threshold_dbfs": -50.0,
    "warn_balance_db": 3.0,
    "max_balance_db": 6.0,
    "warn_onset_skew_ms": 50.0,
    "max_onset_skew_ms": 200.0,
    "warn_phase_correlation": 0.20,
    "min_phase_correlation": -0.10,
    "negative_phase_threshold": -0.10,
    "max_negative_phase_seconds": 0.50,
    "max_negative_phase_ratio": 0.10,
    "warn_mono_fold_down_loss_db": 3.50,
    "max_mono_fold_down_loss_db": 6.0,
}
MEDIA_KEYS = (
    "duration",
    "format_name",
    "audio_stream_index",
    "audio_codec",
    "sample_rate",
    "channels",
    "channel_layout",
)
ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "ffmpeg_aphasemeter_astats_windows",
    "audio_stream": "first audio stream (0:a:0)",
    "decode": "resample + fixed-size audio frames; media bytes are not rewritten",
    "frame_metrics": [
        "left/right RMS level",
        "left/right peak level",
        "phase correlation",
    ],
    "derived_metrics": [
        "left/right active onset",
        "energy-weighted balance",
        "energy-weighted phase correlation",
        "negative-phase duration/ratio",
        "mono fold-down energy loss",
    ],
    "channel_scope": "mono pass-through; stereo measured; more than two channels blocked",
    "rounding_decimals": 6,
    "automatic_repair": False,
    "speech_classification": False,
    "professional_meter_certification": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


ALGORITHM_ID = _canonical_sha256(ALGORITHM_CONTRACT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_project_path(raw_path: str | Path, *, root: Path, label: str) -> Path:
    lexical = Path(raw_path).expanduser()
    if not lexical.is_absolute():
        lexical = root / lexical
    lexical = Path(os.path.abspath(str(lexical)))
    if lexical.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {lexical}")
    if not _within(lexical, root):
        resolved = lexical.resolve()
        if not _within(resolved, root):
            raise ValueError(f"{label} must stay inside the project directory: {lexical}")
        # macOS exposes /tmp as /private/tmp. Accept that filesystem alias while
        # still rejecting actual project-local symlink traversal below.
        lexical = resolved
    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return lexical


def _project_file(raw_path: str | Path, *, root: Path, label: str) -> Path:
    path = _lexical_project_path(raw_path, root=root, label=label).resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


def _same_path_or_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _safe_output(
    raw_path: str | Path,
    *,
    root: Path,
    label: str,
    forbidden: Sequence[Path],
    force: bool,
) -> Path:
    path = _lexical_project_path(raw_path, root=root, label=label)
    for item in forbidden:
        if _same_path_or_file(path, item):
            raise ValueError(f"{label} must not overwrite bound input: {item}")
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing {label} without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value if value.endswith("\n") else value + "\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round_or_none(value: Any, digits: int = 6) -> Optional[float]:
    parsed = _finite(value)
    return round(parsed, digits) if parsed is not None else None


def probe_audio_media(path: Path | str) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "ffprobe failed")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    audio = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"), None)
    if not isinstance(audio, Mapping):
        raise ValueError("source has no audio stream")
    format_data = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    duration = _finite(format_data.get("duration")) or _finite(audio.get("duration"))
    sample_rate = _finite(audio.get("sample_rate"))
    channels = _finite(audio.get("channels"))
    if duration is None or duration <= 0:
        raise ValueError("source audio duration must be positive")
    if sample_rate is None or sample_rate <= 0 or channels is None or int(channels) <= 0:
        raise ValueError("source audio stream has an invalid sample rate or channel count")
    return {
        "duration": round(duration, 6),
        "format_name": str(format_data.get("format_name") or ""),
        "audio_stream_index": int(audio.get("index") or 0),
        "audio_codec": str(audio.get("codec_name") or ""),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "channel_layout": str(audio.get("channel_layout") or ""),
    }


def _source_contract(path: Path, *, root: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **{key: media.get(key) for key in MEDIA_KEYS},
    }


def normalize_settings(value: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if value:
        unknown = sorted(set(value) - set(settings))
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)}")
        settings.update(value)
    settings["analysis_sample_rate"] = int(settings["analysis_sample_rate"])
    settings["window_ms"] = int(settings["window_ms"])
    for key in set(settings) - {"analysis_sample_rate", "window_ms"}:
        settings[key] = float(settings[key])
    blockers = validate_settings(settings)
    if blockers:
        raise ValueError("; ".join(blockers))
    return settings


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []

    def number(key: str) -> Optional[float]:
        return _finite(settings.get(key))

    rate = number("analysis_sample_rate")
    window = number("window_ms")
    activity = number("activity_threshold_dbfs")
    warn_balance = number("warn_balance_db")
    max_balance = number("max_balance_db")
    warn_onset = number("warn_onset_skew_ms")
    max_onset = number("max_onset_skew_ms")
    warn_phase = number("warn_phase_correlation")
    min_phase = number("min_phase_correlation")
    negative_phase = number("negative_phase_threshold")
    max_negative_seconds = number("max_negative_phase_seconds")
    max_negative_ratio = number("max_negative_phase_ratio")
    warn_mono_loss = number("warn_mono_fold_down_loss_db")
    max_mono_loss = number("max_mono_fold_down_loss_db")

    if rate is None or not 4000 <= rate <= 48000 or int(rate) != rate:
        blockers.append("settings.analysis_sample_rate must be an integer between 4000 and 48000")
    if window is None or not 20 <= window <= 500 or int(window) != window:
        blockers.append("settings.window_ms must be an integer between 20 and 500")
    if activity is None or not -80 <= activity <= -10:
        blockers.append("settings.activity_threshold_dbfs must be between -80 and -10")
    if warn_balance is None or max_balance is None or not 0 <= warn_balance <= max_balance <= 30:
        blockers.append("settings balance thresholds must satisfy 0 <= warn <= max <= 30 dB")
    if warn_onset is None or max_onset is None or not 0 <= warn_onset <= max_onset <= 2000:
        blockers.append("settings onset thresholds must satisfy 0 <= warn <= max <= 2000 ms")
    if min_phase is None or warn_phase is None or not -1 <= min_phase <= warn_phase <= 1:
        blockers.append("settings phase thresholds must satisfy -1 <= min <= warn <= 1")
    if negative_phase is None or not -1 <= negative_phase <= 0:
        blockers.append("settings.negative_phase_threshold must be between -1 and 0")
    if max_negative_seconds is None or not 0 <= max_negative_seconds <= 60:
        blockers.append("settings.max_negative_phase_seconds must be between 0 and 60")
    if max_negative_ratio is None or not 0 <= max_negative_ratio <= 1:
        blockers.append("settings.max_negative_phase_ratio must be between 0 and 1")
    if warn_mono_loss is None or max_mono_loss is None or not 0 <= warn_mono_loss <= max_mono_loss <= 60:
        blockers.append("settings mono-loss thresholds must satisfy 0 <= warn <= max <= 60 dB")
    return blockers


FRAME_RE = re.compile(r"frame:(\d+)\s+pts:\S+\s+pts_time:([-+0-9.eE]+)")
META_RE = re.compile(r"(lavfi\.(?:aphasemeter|astats)\.[^=]+)=([^\s]+)")


def parse_analysis_log(log: str) -> List[Dict[str, Any]]:
    """Parse per-frame ``aphasemeter`` and ``astats`` metadata."""
    frames: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    key_map = {
        "lavfi.aphasemeter.phase": "phase_correlation",
        "lavfi.astats.1.RMS_level": "left_rms_dbfs",
        "lavfi.astats.2.RMS_level": "right_rms_dbfs",
        "lavfi.astats.1.Peak_level": "left_peak_dbfs",
        "lavfi.astats.2.Peak_level": "right_peak_dbfs",
    }
    for line in log.splitlines():
        frame_match = FRAME_RE.search(line)
        if frame_match:
            if current is not None:
                frames.append(current)
            current = {"index": int(frame_match.group(1)), "time": round(float(frame_match.group(2)), 6)}
            continue
        if current is None:
            continue
        meta_match = META_RE.search(line)
        if not meta_match:
            continue
        target = key_map.get(meta_match.group(1))
        if target:
            current[target] = _round_or_none(meta_match.group(2))
    if current is not None:
        frames.append(current)
    return frames


def measure_audio_channels(path: Path | str, *, settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    sample_rate = int(settings["analysis_sample_rate"])
    window_samples = max(1, round(sample_rate * int(settings["window_ms"]) / 1000))
    filters = (
        f"aresample={sample_rate},asetnsamples=n={window_samples}:p=1,"
        "aphasemeter=video=0,"
        "astats=metadata=1:reset=1:measure_perchannel=RMS_level+Peak_level:measure_overall=none,"
        "ametadata=mode=print"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            filters,
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(detail[-1] if detail else "FFmpeg channel analysis failed")
    frames = parse_analysis_log(result.stderr or result.stdout)
    if not frames:
        raise ValueError("FFmpeg channel analysis produced no metadata frames")
    return frames


def _power(dbfs: Any) -> float:
    parsed = _finite(dbfs)
    return 0.0 if parsed is None else 10.0 ** (parsed / 10.0)


def analyze_frames(
    frames: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    window_seconds = float(settings["window_ms"]) / 1000.0
    activity_threshold = float(settings["activity_threshold_dbfs"])
    negative_threshold = float(settings["negative_phase_threshold"])
    left_energy = 0.0
    right_energy = 0.0
    cross_energy = 0.0
    phase_weight = 0.0
    left_active_seconds = 0.0
    right_active_seconds = 0.0
    overlap_active_seconds = 0.0
    negative_phase_seconds = 0.0
    left_onset: Optional[float] = None
    right_onset: Optional[float] = None
    worst_phase: Optional[Dict[str, float]] = None
    peak_left: Optional[float] = None
    peak_right: Optional[float] = None

    for frame in frames:
        start = max(0.0, float(frame.get("time") or 0.0))
        frame_duration = max(0.0, min(window_seconds, float(duration) - start))
        if frame_duration <= 0:
            continue
        left_rms = _finite(frame.get("left_rms_dbfs"))
        right_rms = _finite(frame.get("right_rms_dbfs"))
        left_power = _power(left_rms)
        right_power = _power(right_rms)
        left_energy += left_power * frame_duration
        right_energy += right_power * frame_duration
        left_active = left_rms is not None and left_rms >= activity_threshold
        right_active = right_rms is not None and right_rms >= activity_threshold
        if left_active:
            left_active_seconds += frame_duration
            if left_onset is None:
                left_onset = start
        if right_active:
            right_active_seconds += frame_duration
            if right_onset is None:
                right_onset = start

        phase = _finite(frame.get("phase_correlation"))
        if left_active and right_active and phase is not None:
            overlap_active_seconds += frame_duration
            weight = math.sqrt(left_power * right_power) * frame_duration
            phase_weight += weight
            cross_energy += phase * weight
            if phase < negative_threshold:
                negative_phase_seconds += frame_duration
            if worst_phase is None or phase < worst_phase["correlation"]:
                worst_phase = {
                    "start": round(start, 6),
                    "end": round(min(float(duration), start + frame_duration), 6),
                    "correlation": round(phase, 6),
                }

        for key, target in (("left_peak_dbfs", "left"), ("right_peak_dbfs", "right")):
            value = _finite(frame.get(key))
            if value is None:
                continue
            if target == "left":
                peak_left = value if peak_left is None else max(peak_left, value)
            else:
                peak_right = value if peak_right is None else max(peak_right, value)

    balance_db: Optional[float] = None
    if left_energy > 0 and right_energy > 0:
        balance_db = 10.0 * math.log10(left_energy / right_energy)
    overall_phase = cross_energy / phase_weight if phase_weight > 0 else None
    stereo_energy = (left_energy + right_energy) / 2.0
    mono_energy = (left_energy + right_energy + 2.0 * cross_energy) / 4.0
    if stereo_energy <= 0:
        mono_loss = None
    elif mono_energy <= 0:
        mono_loss = 120.0
    else:
        mono_loss = min(120.0, max(0.0, -10.0 * math.log10(mono_energy / stereo_energy)))
    onset_skew = None
    if left_onset is not None and right_onset is not None:
        onset_skew = abs(left_onset - right_onset) * 1000.0
    negative_ratio = (
        negative_phase_seconds / overlap_active_seconds if overlap_active_seconds > 0 else 0.0
    )
    return {
        "mode": "stereo",
        "frame_count": len(frames),
        "window_seconds": round(window_seconds, 6),
        "left": {
            "onset_seconds": _round_or_none(left_onset),
            "active_seconds": round(left_active_seconds, 6),
            "integrated_rms_dbfs": _round_or_none(
                10.0 * math.log10(left_energy / duration) if left_energy > 0 and duration > 0 else None
            ),
            "peak_dbfs": _round_or_none(peak_left),
        },
        "right": {
            "onset_seconds": _round_or_none(right_onset),
            "active_seconds": round(right_active_seconds, 6),
            "integrated_rms_dbfs": _round_or_none(
                10.0 * math.log10(right_energy / duration) if right_energy > 0 and duration > 0 else None
            ),
            "peak_dbfs": _round_or_none(peak_right),
        },
        "onset_skew_ms": _round_or_none(onset_skew, 3),
        "balance_db": _round_or_none(balance_db, 3),
        "phase_correlation": _round_or_none(overall_phase, 6),
        "negative_phase_seconds": round(negative_phase_seconds, 6),
        "negative_phase_ratio": round(negative_ratio, 6),
        "mono_fold_down_loss_db": _round_or_none(mono_loss, 3),
        "overlap_active_seconds": round(overlap_active_seconds, 6),
        "worst_phase_window": worst_phase,
    }


def _check(name: str, status: str, message: str) -> Dict[str, str]:
    return {"name": name, "status": status, "message": message}


def evaluate_analysis(
    analysis: Mapping[str, Any],
    *,
    channels: int,
    settings: Mapping[str, Any],
) -> List[Dict[str, str]]:
    if channels == 1:
        return [_check("channel_layout", "pass", "Mono source has no stereo channel-integrity risk to measure")]
    if channels != 2:
        return [
            _check(
                "channel_layout",
                "block",
                f"Source has {channels} channels; define and review an explicit mono/stereo downmix before short-form delivery",
            )
        ]

    checks = [_check("channel_layout", "pass", "Stereo source is eligible for channel analysis")]
    left = analysis.get("left") if isinstance(analysis.get("left"), Mapping) else {}
    right = analysis.get("right") if isinstance(analysis.get("right"), Mapping) else {}
    left_active = float(_finite(left.get("active_seconds")) or 0.0)
    right_active = float(_finite(right.get("active_seconds")) or 0.0)
    if left_active <= 0 and right_active <= 0:
        checks.append(_check("channel_activity", "block", "Neither stereo channel crosses the configured activity threshold"))
    elif left_active <= 0 or right_active <= 0:
        missing = "left" if left_active <= 0 else "right"
        checks.append(_check("channel_activity", "block", f"The {missing} channel has no measurable activity"))
    else:
        checks.append(_check("channel_activity", "pass", "Both stereo channels contain measurable activity"))

    onset = _finite(analysis.get("onset_skew_ms"))
    if onset is None:
        checks.append(_check("onset_alignment", "block", "Could not establish an active onset for both channels"))
    elif onset > float(settings["max_onset_skew_ms"]):
        checks.append(_check("onset_alignment", "block", f"Left/right active onset differs by {onset:.1f} ms"))
    elif onset > float(settings["warn_onset_skew_ms"]):
        checks.append(_check("onset_alignment", "warn", f"Left/right active onset differs by {onset:.1f} ms; audition the opening"))
    else:
        checks.append(_check("onset_alignment", "pass", f"Left/right active onset differs by {onset:.1f} ms"))

    balance = _finite(analysis.get("balance_db"))
    if balance is None:
        checks.append(_check("left_right_balance", "block", "Could not measure left/right energy balance"))
    elif abs(balance) > float(settings["max_balance_db"]):
        checks.append(_check("left_right_balance", "block", f"Left/right integrated balance is {balance:+.2f} dB"))
    elif abs(balance) > float(settings["warn_balance_db"]):
        checks.append(_check("left_right_balance", "warn", f"Left/right integrated balance is {balance:+.2f} dB"))
    else:
        checks.append(_check("left_right_balance", "pass", f"Left/right integrated balance is {balance:+.2f} dB"))

    phase = _finite(analysis.get("phase_correlation"))
    if phase is None:
        checks.append(_check("phase_correlation", "block", "Could not measure phase correlation while both channels were active"))
    elif phase < float(settings["min_phase_correlation"]):
        checks.append(_check("phase_correlation", "block", f"Energy-weighted phase correlation is {phase:.3f}"))
    elif phase < float(settings["warn_phase_correlation"]):
        checks.append(_check("phase_correlation", "warn", f"Energy-weighted phase correlation is {phase:.3f}; audition the mono fold-down"))
    else:
        checks.append(_check("phase_correlation", "pass", f"Energy-weighted phase correlation is {phase:.3f}"))

    negative_seconds = float(_finite(analysis.get("negative_phase_seconds")) or 0.0)
    negative_ratio = float(_finite(analysis.get("negative_phase_ratio")) or 0.0)
    if (
        negative_seconds > float(settings["max_negative_phase_seconds"])
        and negative_ratio > float(settings["max_negative_phase_ratio"])
    ):
        checks.append(
            _check(
                "negative_phase_windows",
                "block",
                f"Negative phase persists for {negative_seconds:.2f}s ({negative_ratio:.1%} of active stereo overlap)",
            )
        )
    elif negative_seconds > 0:
        checks.append(
            _check(
                "negative_phase_windows",
                "warn",
                f"Negative phase appears for {negative_seconds:.2f}s ({negative_ratio:.1%} of active stereo overlap)",
            )
        )
    else:
        checks.append(_check("negative_phase_windows", "pass", "No negative-phase analysis window was found"))

    mono_loss = _finite(analysis.get("mono_fold_down_loss_db"))
    if mono_loss is None:
        checks.append(_check("mono_fold_down", "block", "Could not estimate mono fold-down energy"))
    elif mono_loss > float(settings["max_mono_fold_down_loss_db"]):
        checks.append(_check("mono_fold_down", "block", f"Estimated mono fold-down energy loss is {mono_loss:.2f} dB"))
    elif mono_loss > float(settings["warn_mono_fold_down_loss_db"]):
        checks.append(_check("mono_fold_down", "warn", f"Estimated mono fold-down energy loss is {mono_loss:.2f} dB"))
    else:
        checks.append(_check("mono_fold_down", "pass", f"Estimated mono fold-down energy loss is {mono_loss:.2f} dB"))
    return checks


def summarize_checks(checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    blocking = sum(1 for item in checks if item.get("status") == "block")
    warnings = sum(1 for item in checks if item.get("status") == "warn")
    return {
        "status": "blocked" if blocking else "warn" if warnings else "ready",
        "blocking": blocking,
        "warnings": warnings,
        "checks": len(checks),
    }


def _report_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "source": report.get("source"),
        "algorithm": report.get("algorithm"),
        "settings": report.get("settings"),
        "analysis": report.get("analysis"),
        "checks": report.get("checks"),
        "summary": report.get("summary"),
        "limitations": report.get("limitations"),
    }


def build_report(
    source: Path | str,
    *,
    project_dir: Path | str,
    settings: Optional[Mapping[str, Any]] = None,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    source_path = _project_file(source, root=root, label="source")
    probe_fn = probe_fn or probe_audio_media
    measure_fn = measure_fn or measure_audio_channels
    media = dict(probe_fn(source_path))
    normalized_settings = normalize_settings(settings)
    channels = int(media.get("channels") or 0)
    if channels == 2:
        frames = measure_fn(source_path, settings=normalized_settings)
        analysis = analyze_frames(
            frames,
            duration=float(media["duration"]),
            settings=normalized_settings,
        )
    elif channels == 1:
        analysis = {"mode": "mono", "reason": "stereo channel metrics are not applicable"}
    else:
        analysis = {"mode": "unsupported_multichannel", "channels": channels}
    checks = evaluate_analysis(analysis, channels=channels, settings=normalized_settings)
    report: Dict[str, Any] = {
        "schema": VERSION,
        "generated_at": utc_now(),
        "source": _source_contract(source_path, root=root, media=media),
        "algorithm": {"id": ALGORITHM_ID, "contract": dict(ALGORITHM_CONTRACT)},
        "settings": normalized_settings,
        "analysis": analysis,
        "checks": checks,
        "summary": summarize_checks(checks),
        "limitations": [
            "This is a sampled engineering screen, not a calibrated or certified audio meter.",
            "It does not identify speech, judge creative panning, or prove subjective intelligibility.",
            "Listen to the complete master and its mono fold-down at normal speed before delivery.",
            "Run audio_master_report.py separately for loudness, true peak, LRA, and long silence.",
        ],
    }
    report["report_id"] = _canonical_sha256(_report_snapshot(report))
    return report


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[Path | str] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if not isinstance(report, Mapping):
        blockers.append("report must be a JSON object")
    if report.get("schema") != VERSION:
        blockers.append(f"report schema must be {VERSION}")
    root = Path(project_dir or ".").expanduser().resolve()
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    source_path_value = source.get("path")
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    try:
        if not source_path_value:
            raise ValueError("report source.path is missing")
        source_path = _project_file(str(source_path_value), root=root, label="source")
        current = build_report(
            source_path,
            project_dir=root,
            settings=settings,
            probe_fn=probe_fn,
            measure_fn=measure_fn,
        )
    except Exception as exc:
        blockers.append(str(exc))
        current = None

    if current is not None:
        comparisons = (
            ("source", "source bytes or media contract drifted"),
            ("algorithm", "algorithm contract drifted"),
            ("settings", "analysis settings drifted"),
            ("analysis", "live channel measurements drifted"),
            ("checks", "derived channel checks drifted"),
            ("summary", "derived summary drifted"),
            ("limitations", "review limitations drifted"),
            ("report_id", "canonical report id drifted"),
        )
        for key, message in comparisons:
            if report.get(key) != current.get(key):
                blockers.append(message)
        for check in current.get("checks", []):
            if not isinstance(check, Mapping):
                continue
            status = check.get("status")
            message = str(check.get("message") or check.get("name") or "channel check")
            if status == "block":
                blockers.append(f"audio channel blocker: {message}")
            elif status == "warn":
                warnings.append(f"audio channel warning: {message}")
    blocking = len(blockers)
    return {
        "schema": VERIFY_VERSION,
        "verified_at": utc_now(),
        "status": "blocked" if blocking else "warn" if warnings else "ready",
        "report_id": report.get("report_id"),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "status": "blocked" if blocking else "warn" if warnings else "ready",
            "blocking": blocking,
            "warnings": len(warnings),
        },
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Audio Channel QA",
        "",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Channels: `{source.get('channels', '')}` (`{source.get('channel_layout', '')}`)",
        f"- Report ID: `{report.get('report_id', '')}`",
        "",
    ]
    if analysis.get("mode") == "stereo":
        left = analysis.get("left") if isinstance(analysis.get("left"), Mapping) else {}
        right = analysis.get("right") if isinstance(analysis.get("right"), Mapping) else {}
        lines.extend(
            [
                "## Stereo measurements",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Left onset | {left.get('onset_seconds')} s |",
                f"| Right onset | {right.get('onset_seconds')} s |",
                f"| Onset skew | {analysis.get('onset_skew_ms')} ms |",
                f"| L/R balance | {analysis.get('balance_db')} dB |",
                f"| Phase correlation | {analysis.get('phase_correlation')} |",
                f"| Negative phase | {analysis.get('negative_phase_seconds')} s / {float(analysis.get('negative_phase_ratio') or 0):.1%} |",
                f"| Mono fold-down loss | {analysis.get('mono_fold_down_loss_db')} dB |",
                "",
            ]
        )
    lines.extend(["## Checks", ""])
    for check in report.get("checks", []):
        if isinstance(check, Mapping):
            lines.append(f"- **{str(check.get('status', '')).upper()}** `{check.get('name', '')}` — {check.get('message', '')}")
    lines.extend(
        [
            "",
            "## Review boundary",
            "",
            "This gate is local and read-only. It does not repair audio, classify speech, certify a mix, or replace complete 1× listening. Audition the stereo master and a mono fold-down, then run `audio_master_report.py` for loudness and peak checks.",
        ]
    )
    return "\n".join(lines) + "\n"


def _settings_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "analysis_sample_rate": args.analysis_sample_rate,
        "window_ms": args.window_ms,
        "activity_threshold_dbfs": args.activity_threshold_dbfs,
        "warn_balance_db": args.warn_balance_db,
        "max_balance_db": args.max_balance_db,
        "warn_onset_skew_ms": args.warn_onset_skew_ms,
        "max_onset_skew_ms": args.max_onset_skew_ms,
        "warn_phase_correlation": args.warn_phase_correlation,
        "min_phase_correlation": args.min_phase_correlation,
        "negative_phase_threshold": args.negative_phase_threshold,
        "max_negative_phase_seconds": args.max_negative_phase_seconds,
        "max_negative_phase_ratio": args.max_negative_phase_ratio,
        "warn_mono_fold_down_loss_db": args.warn_mono_fold_down_loss_db,
        "max_mono_fold_down_loss_db": args.max_mono_fold_down_loss_db,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit final audio channel alignment, balance, phase, and mono fold-down risk.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyze a project-local final audio/video file")
    analyze.add_argument("source")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--analysis-sample-rate", type=int, default=DEFAULT_SETTINGS["analysis_sample_rate"])
    analyze.add_argument("--window-ms", type=int, default=DEFAULT_SETTINGS["window_ms"])
    analyze.add_argument("--activity-threshold-dbfs", type=float, default=DEFAULT_SETTINGS["activity_threshold_dbfs"])
    analyze.add_argument("--warn-balance-db", type=float, default=DEFAULT_SETTINGS["warn_balance_db"])
    analyze.add_argument("--max-balance-db", type=float, default=DEFAULT_SETTINGS["max_balance_db"])
    analyze.add_argument("--warn-onset-skew-ms", type=float, default=DEFAULT_SETTINGS["warn_onset_skew_ms"])
    analyze.add_argument("--max-onset-skew-ms", type=float, default=DEFAULT_SETTINGS["max_onset_skew_ms"])
    analyze.add_argument("--warn-phase-correlation", type=float, default=DEFAULT_SETTINGS["warn_phase_correlation"])
    analyze.add_argument("--min-phase-correlation", type=float, default=DEFAULT_SETTINGS["min_phase_correlation"])
    analyze.add_argument("--negative-phase-threshold", type=float, default=DEFAULT_SETTINGS["negative_phase_threshold"])
    analyze.add_argument("--max-negative-phase-seconds", type=float, default=DEFAULT_SETTINGS["max_negative_phase_seconds"])
    analyze.add_argument("--max-negative-phase-ratio", type=float, default=DEFAULT_SETTINGS["max_negative_phase_ratio"])
    analyze.add_argument("--warn-mono-fold-down-loss-db", type=float, default=DEFAULT_SETTINGS["warn_mono_fold_down_loss_db"])
    analyze.add_argument("--max-mono-fold-down-loss-db", type=float, default=DEFAULT_SETTINGS["max_mono_fold_down_loss_db"])
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Re-measure and verify a saved report against live media")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.project_dir).expanduser().resolve()
        if args.command == "analyze":
            source = _project_file(args.source, root=root, label="source")
            output = _safe_output(args.output, root=root, label="output", forbidden=[source], force=args.force)
            markdown = None
            if args.markdown:
                markdown = _safe_output(
                    args.markdown,
                    root=root,
                    label="markdown",
                    forbidden=[source, output],
                    force=args.force,
                )
            report = build_report(source, project_dir=root, settings=_settings_from_args(args))
            _atomic_write_json(output, report)
            if markdown is not None:
                _atomic_write_text(markdown, emit_markdown(report))
            print(
                f"Audio channel QA: {report['summary']['status']} "
                f"(blocking={report['summary']['blocking']}, warnings={report['summary']['warnings']})"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path = _project_file(args.report, root=root, label="report")
        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
        verification = verify_report(report, root)
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
