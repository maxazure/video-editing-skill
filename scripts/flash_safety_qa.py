#!/usr/bin/env python3
"""Run a local, source-bound heuristic screen for flashing-light risk.

The analyzer downsamples the final video with FFmpeg, measures frame-to-frame
luminance and saturated-red changes, pairs opposite transitions into flashes,
and evaluates rolling one-second and five-second windows.  It is intentionally
conservative and dependency-light: this is a triage gate, not medical advice,
legal compliance evidence, or a replacement for an accredited PSE analyzer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from generated_motion_window import probe_media


VERSION = "flash_safety_qa.v1"
VERIFY_VERSION = "flash_safety_qa_verify.v1"
DEFAULT_ANALYSIS_FPS = 30.0
DEFAULT_ANALYSIS_WIDTH = 64
DEFAULT_LUMA_CHANGE = 0.10
DEFAULT_RED_CHANGE = 0.10
DEFAULT_AREA_FRACTION = 0.25
DEFAULT_PAIR_GAP = 0.50
HIGH_WINDOW_SECONDS = 1.0
HIGH_FLASH_LIMIT = 3
EXTENDED_WINDOW_SECONDS = 5.0
EXTENDED_FLASH_MINIMUM = 10

ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "downsampled_opposite_transition_flash_screen",
    "luminance": "bt709_8bit_approximation",
    "red_signal": "max(0, red - max(green, blue)) / 255",
    "transition": "dominant signed changed-area fraction meets threshold",
    "flash": "two opposite transitions of the same signal within pair_gap_seconds",
    "high_frequency": {
        "window_seconds": HIGH_WINDOW_SECONDS,
        "condition": "more_than_3_flashes",
    },
    "extended_frequency": {
        "window_seconds": EXTENDED_WINDOW_SECONDS,
        "condition": "at_least_10_flashes",
    },
    "spatial_pattern_detection": False,
    "certification": False,
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
        # macOS exposes /tmp as /private/tmp. Accept that filesystem alias,
        # while still rejecting project-local symlink traversal below.
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
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
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


def _media_contract(media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "duration": round(float(media.get("duration") or 0), 6),
        "fps": round(float(media.get("fps") or 0), 6),
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "rotation": int(media.get("rotation") or 0),
        "has_audio": bool(media.get("has_audio")),
        "video_codec": media.get("video_codec"),
        "audio_codec": media.get("audio_codec"),
        "pixel_format": media.get("pixel_format"),
    }


def _source_contract(path: Path, *, root: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **_media_contract(media),
    }


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    fps = _finite(settings.get("analysis_fps"))
    width = _finite(settings.get("analysis_width"))
    luma = _finite(settings.get("luma_change"))
    red = _finite(settings.get("red_change"))
    area = _finite(settings.get("area_fraction"))
    gap = _finite(settings.get("pair_gap_seconds"))
    if fps is None or not 12 <= fps <= 60:
        blockers.append("settings.analysis_fps must be between 12 and 60")
    if width is None or int(width) != width or not 16 <= int(width) <= 320:
        blockers.append("settings.analysis_width must be an integer between 16 and 320")
    if luma is None or not 0.02 <= luma <= 1:
        blockers.append("settings.luma_change must be between 0.02 and 1")
    if red is None or not 0.02 <= red <= 1:
        blockers.append("settings.red_change must be between 0.02 and 1")
    if area is None or not 0.05 <= area <= 1:
        blockers.append("settings.area_fraction must be between 0.05 and 1")
    if gap is None or not 0.05 <= gap <= 1:
        blockers.append("settings.pair_gap_seconds must be between 0.05 and 1")
    return blockers


def _analysis_dimensions(media: Mapping[str, Any], width: int) -> Tuple[int, int]:
    source_width = int(media.get("width") or 0)
    source_height = int(media.get("height") or 0)
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source display dimensions must be positive")
    height = max(2, int(round((source_height * width / source_width) / 2)) * 2)
    return width, height


def extract_rgb_frames(
    path: Path,
    *,
    media: Mapping[str, Any],
    analysis_fps: float,
    analysis_width: int,
) -> Tuple[List[bytes], float, int, int]:
    effective_fps = min(float(analysis_fps), float(media.get("fps") or analysis_fps))
    width, height = _analysis_dimensions(media, int(analysis_width))
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        f"fps={effective_fps:.6f},scale={width}:{height}:flags=area,format=rgb24",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or b"FFmpeg frame extraction failed").decode("utf-8", errors="replace").strip()
        raise ValueError(detail.splitlines()[-1] if detail else "FFmpeg frame extraction failed")
    frame_size = width * height * 3
    if not result.stdout or len(result.stdout) % frame_size:
        raise ValueError("FFmpeg returned an incomplete RGB frame stream")
    frames = [result.stdout[offset:offset + frame_size] for offset in range(0, len(result.stdout), frame_size)]
    if len(frames) < 2:
        raise ValueError("at least two sampled video frames are required")
    return frames, round(effective_fps, 6), width, height


def _frame_channels(frame: bytes) -> Tuple[List[int], List[int]]:
    luma: List[int] = []
    red_signal: List[int] = []
    for offset in range(0, len(frame), 3):
        red = frame[offset]
        green = frame[offset + 1]
        blue = frame[offset + 2]
        luma.append((54 * red + 183 * green + 19 * blue) >> 8)
        red_signal.append(max(0, red - max(green, blue)))
    return luma, red_signal


def _transition(
    previous: Sequence[int],
    current: Sequence[int],
    *,
    threshold: float,
    area_fraction: float,
    time_seconds: float,
    frame_index: int,
) -> Optional[Dict[str, Any]]:
    threshold_value = threshold * 255.0
    positive = 0
    negative = 0
    absolute_total = 0.0
    signed_total = 0.0
    for before, after in zip(previous, current):
        difference = float(after - before)
        absolute_total += abs(difference)
        signed_total += difference
        if difference >= threshold_value:
            positive += 1
        elif difference <= -threshold_value:
            negative += 1
    pixels = max(1, len(previous))
    positive_fraction = positive / pixels
    negative_fraction = negative / pixels
    affected = max(positive_fraction, negative_fraction)
    if affected + 1e-12 < area_fraction:
        return None
    direction = "rise" if positive_fraction >= negative_fraction else "fall"
    return {
        "frame": frame_index,
        "time": round(time_seconds, 6),
        "direction": direction,
        "affected_fraction": round(affected, 6),
        "mean_signed_change": round(signed_total / pixels / 255.0, 6),
        "mean_absolute_change": round(absolute_total / pixels / 255.0, 6),
    }


def pair_transitions(
    transitions: Sequence[Mapping[str, Any]],
    *,
    max_gap: float,
) -> List[Dict[str, Any]]:
    flashes: List[Dict[str, Any]] = []
    pending: Optional[Mapping[str, Any]] = None
    for current in transitions:
        if pending is not None:
            gap = float(current["time"]) - float(pending["time"])
            if 0 < gap <= max_gap and current.get("direction") != pending.get("direction"):
                flashes.append({
                    "start": round(float(pending["time"]), 6),
                    "end": round(float(current["time"]), 6),
                    "duration": round(gap, 6),
                    "first_direction": pending.get("direction"),
                    "affected_fraction": round(
                        min(float(pending.get("affected_fraction") or 0), float(current.get("affected_fraction") or 0)),
                        6,
                    ),
                })
                pending = None
                continue
        pending = current
    return flashes


def _rolling_peak(events: Sequence[Mapping[str, Any]], seconds: float) -> int:
    queue: deque[float] = deque()
    peak = 0
    for event in events:
        timestamp = float(event["end"])
        queue.append(timestamp)
        while queue and timestamp - queue[0] > seconds + 1e-9:
            queue.popleft()
        peak = max(peak, len(queue))
    return peak


def _risk_windows(
    events: Sequence[Mapping[str, Any]],
    *,
    signal: str,
    window_seconds: float,
    minimum_count: int,
    rule: str,
) -> List[Dict[str, Any]]:
    queue: deque[float] = deque()
    windows: List[Dict[str, Any]] = []
    for event in events:
        timestamp = float(event["end"])
        queue.append(timestamp)
        while queue and timestamp - queue[0] > window_seconds + 1e-9:
            queue.popleft()
        if len(queue) >= minimum_count:
            windows.append({
                "signal": signal,
                "rule": rule,
                "start": round(max(0.0, timestamp - window_seconds), 6),
                "end": round(timestamp, 6),
                "flashes": len(queue),
            })
    return _merge_windows(windows)


def _merge_windows(windows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for item in sorted(windows, key=lambda row: (str(row["signal"]), str(row["rule"]), float(row["start"]))):
        current = dict(item)
        if (
            merged
            and merged[-1]["signal"] == current["signal"]
            and merged[-1]["rule"] == current["rule"]
            and float(current["start"]) <= float(merged[-1]["end"]) + 1e-6
        ):
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(current["end"]))
            merged[-1]["flashes"] = max(int(merged[-1]["flashes"]), int(current["flashes"]))
        else:
            merged.append(current)
    return merged


def analyze_frame_sequence(
    frames: Sequence[bytes],
    *,
    width: int,
    height: int,
    fps: float,
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    if validate_settings(settings):
        raise ValueError("invalid flash-safety settings: " + "; ".join(validate_settings(settings)))
    frame_size = int(width) * int(height) * 3
    if len(frames) < 2 or any(len(frame) != frame_size for frame in frames):
        raise ValueError("RGB frames do not match the declared dimensions")
    luma_transitions: List[Dict[str, Any]] = []
    red_transitions: List[Dict[str, Any]] = []
    previous_luma, previous_red = _frame_channels(frames[0])
    for index, frame in enumerate(frames[1:], start=1):
        current_luma, current_red = _frame_channels(frame)
        time_seconds = index / float(fps)
        luma = _transition(
            previous_luma,
            current_luma,
            threshold=float(settings["luma_change"]),
            area_fraction=float(settings["area_fraction"]),
            time_seconds=time_seconds,
            frame_index=index,
        )
        red = _transition(
            previous_red,
            current_red,
            threshold=float(settings["red_change"]),
            area_fraction=float(settings["area_fraction"]),
            time_seconds=time_seconds,
            frame_index=index,
        )
        if luma is not None:
            luma_transitions.append(luma)
        if red is not None:
            red_transitions.append(red)
        previous_luma, previous_red = current_luma, current_red

    luma_flashes = pair_transitions(luma_transitions, max_gap=float(settings["pair_gap_seconds"]))
    red_flashes = pair_transitions(red_transitions, max_gap=float(settings["pair_gap_seconds"]))
    risks: List[Dict[str, Any]] = []
    for signal, events in (("luminance", luma_flashes), ("saturated_red", red_flashes)):
        risks.extend(_risk_windows(
            events,
            signal=signal,
            window_seconds=HIGH_WINDOW_SECONDS,
            minimum_count=HIGH_FLASH_LIMIT + 1,
            rule="more_than_3_flashes_in_1_second",
        ))
        risks.extend(_risk_windows(
            events,
            signal=signal,
            window_seconds=EXTENDED_WINDOW_SECONDS,
            minimum_count=EXTENDED_FLASH_MINIMUM,
            rule="at_least_10_flashes_in_5_seconds",
        ))
    return {
        "sample": {
            "frames": len(frames),
            "fps": round(float(fps), 6),
            "width": int(width),
            "height": int(height),
            "covered_seconds": round((len(frames) - 1) / float(fps), 6),
        },
        "transitions": {
            "luminance": luma_transitions,
            "saturated_red": red_transitions,
        },
        "flashes": {
            "luminance": luma_flashes,
            "saturated_red": red_flashes,
        },
        "peaks": {
            "luminance_1s": _rolling_peak(luma_flashes, HIGH_WINDOW_SECONDS),
            "luminance_5s": _rolling_peak(luma_flashes, EXTENDED_WINDOW_SECONDS),
            "saturated_red_1s": _rolling_peak(red_flashes, HIGH_WINDOW_SECONDS),
            "saturated_red_5s": _rolling_peak(red_flashes, EXTENDED_WINDOW_SECONDS),
        },
        "risk_windows": sorted(risks, key=lambda item: (float(item["start"]), item["signal"], item["rule"])),
    }


def analyze_video(
    path: Path,
    *,
    media: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    frames, effective_fps, width, height = extract_rgb_frames(
        path,
        media=media,
        analysis_fps=float(settings["analysis_fps"]),
        analysis_width=int(settings["analysis_width"]),
    )
    return analyze_frame_sequence(
        frames,
        width=width,
        height=height,
        fps=effective_fps,
        settings=settings,
    )


def _derived_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    risk_windows = analysis.get("risk_windows") if isinstance(analysis.get("risk_windows"), list) else []
    blockers = [
        (
            f"{item.get('signal', 'unknown')} {item.get('rule', 'flash risk')} at "
            f"{float(item.get('start') or 0):.3f}-{float(item.get('end') or 0):.3f}s "
            f"({int(item.get('flashes') or 0)} flashes in the rolling window)"
        )
        for item in risk_windows
        if isinstance(item, Mapping)
    ]
    peaks = analysis.get("peaks") if isinstance(analysis.get("peaks"), Mapping) else {}
    warnings: List[str] = []
    if not blockers and max(int(peaks.get("luminance_1s") or 0), int(peaks.get("saturated_red_1s") or 0)) >= 2:
        warnings.append("Repeated flashes were detected below the blocking threshold; review those moments at normal speed.")
    summary = {
        "frames_analyzed": int((analysis.get("sample") or {}).get("frames") or 0),
        "luminance_transitions": len((analysis.get("transitions") or {}).get("luminance") or []),
        "red_transitions": len((analysis.get("transitions") or {}).get("saturated_red") or []),
        "luminance_flashes": len((analysis.get("flashes") or {}).get("luminance") or []),
        "red_flashes": len((analysis.get("flashes") or {}).get("saturated_red") or []),
        "risk_windows": len(risk_windows),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }


def canonical_report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256({
        key: value
        for key, value in report.items()
        if key not in {"generated_at", "report_id"}
    })


def _set_derived(report: Dict[str, Any]) -> None:
    report.update(_derived_snapshot(report))
    report["report_id"] = canonical_report_id(report)


def build_report(
    video: str | Path,
    *,
    project_dir: str | Path,
    analysis_fps: float = DEFAULT_ANALYSIS_FPS,
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH,
    luma_change: float = DEFAULT_LUMA_CHANGE,
    red_change: float = DEFAULT_RED_CHANGE,
    area_fraction: float = DEFAULT_AREA_FRACTION,
    pair_gap: float = DEFAULT_PAIR_GAP,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    analyze_fn = analyze_fn or analyze_video
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _project_file(video, root=root, label="video")
    settings = {
        "analysis_fps": float(analysis_fps),
        "analysis_width": int(analysis_width),
        "luma_change": float(luma_change),
        "red_change": float(red_change),
        "area_fraction": float(area_fraction),
        "pair_gap_seconds": float(pair_gap),
    }
    setting_errors = validate_settings(settings)
    if setting_errors:
        raise ValueError("invalid settings: " + "; ".join(setting_errors))
    media = dict(probe_fn(source))
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": _source_contract(source, root=root, media=media),
        "settings": settings,
        "algorithm": {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT},
        "analysis": dict(analyze_fn(source, media=media, settings=settings)),
        "limitations": [
            "This downsampled heuristic is a triage screen, not medical advice, legal compliance evidence, or certification.",
            "It does not detect harmful spatial patterns and can miss localized, high-resolution, color-managed, or display-dependent effects.",
            "A clean result does not prove safety. Escalate high-risk or regulated delivery to an accredited photosensitivity analyzer.",
        ],
    }
    _set_derived(report)
    return report


def _structural_blockers(report: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported version: {report.get('version')!r}")
    project = str(report.get("project_dir") or "")
    if not project or not Path(project).expanduser().is_absolute():
        blockers.append("project_dir must be an absolute path")
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    blockers.extend(validate_settings(settings))
    if report.get("algorithm") != {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT}:
        blockers.append("algorithm contract differs from the current flash-safety analyzer")
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    if not str(source.get("path") or "") or len(str(source.get("sha256") or "")) != 64:
        blockers.append("source fingerprint is incomplete")
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    if not isinstance(analysis.get("risk_windows"), list):
        blockers.append("analysis.risk_windows must be a list")
    expected = _derived_snapshot(report)
    for key in ("status", "blockers", "warnings", "summary"):
        if report.get(key) != expected[key]:
            blockers.append(f"stored {key} differs from canonical derived state")
    if report.get("report_id") != canonical_report_id(report):
        blockers.append("report_id does not match canonical report content")
    return blockers


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[str | Path] = None,
    *,
    probe_fn: Optional[Callable[[Path | str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    analyze_fn = analyze_fn or analyze_video
    structural = _structural_blockers(report)
    live_blockers: List[str] = []
    stored_project = Path(str(report.get("project_dir") or "")).expanduser()
    requested_project = Path(project_dir).expanduser().resolve() if project_dir is not None else stored_project.resolve()
    if not stored_project.is_absolute() or stored_project.resolve() != requested_project:
        live_blockers.append("report project_dir differs from the current project")
    source_record = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    source: Optional[Path] = None
    if not live_blockers:
        try:
            source = _project_file(str(source_record.get("path") or ""), root=requested_project, label="source video")
        except ValueError as exc:
            live_blockers.append(str(exc))
    if source is not None:
        if _sha256(source) != str(source_record.get("sha256") or ""):
            live_blockers.append("source video bytes changed after flash-safety analysis")
        if source.stat().st_size != int(source_record.get("size_bytes") or -1):
            live_blockers.append("source video size changed after flash-safety analysis")
        try:
            live_media = dict(probe_fn(source))
        except Exception as exc:
            live_blockers.append(f"source video probe failed: {exc}")
        else:
            if _media_contract(live_media) != {key: source_record.get(key) for key in _media_contract(live_media)}:
                live_blockers.append("source video media contract changed after flash-safety analysis")
            if not structural and not live_blockers:
                try:
                    live_analysis = dict(
                        analyze_fn(
                            source,
                            media=live_media,
                            settings=report.get("settings") if isinstance(report.get("settings"), Mapping) else {},
                        )
                    )
                except Exception as exc:
                    live_blockers.append(f"live flash-safety analysis failed: {exc}")
                else:
                    if live_analysis != report.get("analysis"):
                        live_blockers.append("live flash-safety evidence differs from the stored analysis")
    risk_state = _derived_snapshot(report)
    all_blockers = list(risk_state["blockers"]) + structural + live_blockers
    warnings = list(risk_state["warnings"])
    return {
        "version": VERIFY_VERSION,
        "status": "blocked" if all_blockers else ("warn" if warnings else "ready"),
        "report_id": report.get("report_id"),
        "blockers": all_blockers,
        "warnings": warnings,
        "summary": {
            **risk_state["summary"],
            "risk_blocking": len(risk_state["blockers"]),
            "integrity_blocking": len(structural) + len(live_blockers),
            "blocking": len(all_blockers),
            "warnings": len(warnings),
        },
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    sample = analysis.get("sample") if isinstance(analysis.get("sample"), Mapping) else {}
    lines = [
        "# Flash Safety QA",
        "",
        f"- Status: **{report.get('status', 'blocked')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Sample: {sample.get('frames', 0)} frames at {sample.get('fps', 0)} fps / {sample.get('width', 0)}×{sample.get('height', 0)}",
        f"- Luminance flashes: {report.get('summary', {}).get('luminance_flashes', 0)}",
        f"- Saturated-red flashes: {report.get('summary', {}).get('red_flashes', 0)}",
        "",
        "## Risk windows",
        "",
    ]
    windows = analysis.get("risk_windows") if isinstance(analysis.get("risk_windows"), list) else []
    if windows:
        lines.extend(["| signal | rule | time | peak flashes |", "|---|---|---:|---:|"])
        for item in windows:
            lines.append(
                f"| {item.get('signal')} | {item.get('rule')} | "
                f"{float(item.get('start') or 0):.3f}–{float(item.get('end') or 0):.3f}s | {item.get('flashes')} |"
            )
    else:
        lines.append("No blocking window was found by this heuristic.")
    lines.extend([
        "",
        "## Review and remediation",
        "",
        "- Play every flagged interval at normal speed on the intended delivery display; do not judge flashing from a contact sheet.",
        "- Prefer removing repeated flashes, lowering contrast/red saturation, reducing the flashing area, or slowing the alternation before re-running this report.",
        "- For regulated, broadcast, advertising, medical, educational, or otherwise high-risk delivery, use an accredited photosensitivity analyzer.",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    return "\n".join(lines) + "\n"


def _load_report(path: str | Path) -> Dict[str, Any]:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("report must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Screen final video for high-frequency luminance and saturated-red flashes"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a video and write source-bound JSON/Markdown")
    analyze_parser.add_argument("video")
    analyze_parser.add_argument("--project-dir", default=".")
    analyze_parser.add_argument("--output", default="work/flash_safety_qa.json")
    analyze_parser.add_argument("--markdown", default="work/flash_safety_qa.md")
    analyze_parser.add_argument("--analysis-fps", type=float, default=DEFAULT_ANALYSIS_FPS)
    analyze_parser.add_argument("--analysis-width", type=int, default=DEFAULT_ANALYSIS_WIDTH)
    analyze_parser.add_argument("--luma-change", type=float, default=DEFAULT_LUMA_CHANGE)
    analyze_parser.add_argument("--red-change", type=float, default=DEFAULT_RED_CHANGE)
    analyze_parser.add_argument("--area-fraction", type=float, default=DEFAULT_AREA_FRACTION)
    analyze_parser.add_argument("--pair-gap", type=float, default=DEFAULT_PAIR_GAP)
    analyze_parser.add_argument("--force", action="store_true")
    analyze_parser.add_argument("--strict", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Re-analyze live source bytes and verify the stored report")
    verify_parser.add_argument("--report", default="work/flash_safety_qa.json")
    verify_parser.add_argument("--project-dir")
    verify_parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            root = Path(args.project_dir).expanduser().resolve()
            source = _project_file(args.video, root=root, label="video")
            output = _safe_output(
                args.output,
                root=root,
                label="JSON output",
                forbidden=[source],
                force=args.force,
            )
            markdown = _safe_output(
                args.markdown,
                root=root,
                label="Markdown output",
                forbidden=[source, output],
                force=args.force,
            )
            report = build_report(
                source,
                project_dir=root,
                analysis_fps=args.analysis_fps,
                analysis_width=args.analysis_width,
                luma_change=args.luma_change,
                red_change=args.red_change,
                area_fraction=args.area_fraction,
                pair_gap=args.pair_gap,
            )
            _atomic_write_json(output, report)
            _atomic_write_text(markdown, emit_markdown(report))
            print(
                f"flash_safety_qa status={report['status']} "
                f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']} "
                f"report={output}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report = _load_report(args.report)
        verification = verify_report(report, args.project_dir)
        print(
            f"flash_safety_qa verify status={verification['status']} "
            f"blocking={verification['summary']['blocking']} warnings={verification['summary']['warnings']}"
        )
        for blocker in verification["blockers"]:
            print(f"BLOCK: {blocker}")
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
