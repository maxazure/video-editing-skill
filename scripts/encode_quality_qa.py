#!/usr/bin/env python3
"""Measure source-bound visual loss in a same-timeline video re-encode.

This gate compares a reference master with a compressed/transcoded derivative
using FFmpeg's built-in full-reference SSIM and PSNR filters.  It is deliberately
narrow: both videos must preserve timing and display aspect ratio.  Creative
edits, reframing, grading, HDR tone mapping, interpolation, and retiming need a
different review contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from delivery_encode import probe_media


VERSION = "encode_quality_qa.v1"
VERIFY_VERSION = "encode_quality_qa_verify.v1"
DEFAULT_MIN_MEAN_SSIM = 0.95
DEFAULT_MIN_P05_SSIM = 0.88
DEFAULT_MIN_MEAN_PSNR_DB = 35.0
DEFAULT_DURATION_TOLERANCE_FRAMES = 1.0
DEFAULT_FPS_TOLERANCE = 0.01
DEFAULT_WORST_FRAMES = 12

ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "ffmpeg_full_reference_encode_quality",
    "metrics": ["ssim", "psnr"],
    "candidate_is_main_input": True,
    "timestamp_normalization": "settb=AVTB,setpts=PTS-STARTPTS",
    "pixel_normalization": "lanczos scale to reference display size, setsar=1, yuv420p",
    "frame_sync": "shortest=1,eof_action=endall",
    "audio_compared": False,
    "vmaf_compared": False,
}
ALGORITHM_ID = hashlib.sha256(
    json.dumps(ALGORITHM_CONTRACT, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
        # still rejecting project-local symlink traversal below.
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
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
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


def _file_contract(path: Path, *, root: Path, media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        **_media_contract(media),
    }


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    mean_ssim = _finite(settings.get("min_mean_ssim"))
    p05_ssim = _finite(settings.get("min_p05_ssim"))
    mean_psnr = _finite(settings.get("min_mean_psnr_db"))
    duration_frames = _finite(settings.get("duration_tolerance_frames"))
    fps_tolerance = _finite(settings.get("fps_tolerance"))
    worst_frames = _finite(settings.get("worst_frames"))
    if mean_ssim is None or not 0 <= mean_ssim <= 1:
        blockers.append("settings.min_mean_ssim must be between 0 and 1")
    if p05_ssim is None or not 0 <= p05_ssim <= 1:
        blockers.append("settings.min_p05_ssim must be between 0 and 1")
    if mean_psnr is None or not 10 <= mean_psnr <= 100:
        blockers.append("settings.min_mean_psnr_db must be between 10 and 100")
    if duration_frames is None or not 0 <= duration_frames <= 5:
        blockers.append("settings.duration_tolerance_frames must be between 0 and 5")
    if fps_tolerance is None or not 0 <= fps_tolerance <= 1:
        blockers.append("settings.fps_tolerance must be between 0 and 1")
    if worst_frames is None or int(worst_frames) != worst_frames or not 1 <= int(worst_frames) <= 50:
        blockers.append("settings.worst_frames must be an integer between 1 and 50")
    return blockers


def validate_pair(
    reference: Path,
    candidate: Path,
    *,
    reference_media: Mapping[str, Any],
    candidate_media: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> None:
    if _same_path_or_file(reference, candidate):
        raise ValueError("reference and candidate must be distinct files")
    errors = validate_settings(settings)
    if errors:
        raise ValueError("invalid settings: " + "; ".join(errors))
    reference_fps = float(reference_media.get("fps") or 0)
    candidate_fps = float(candidate_media.get("fps") or 0)
    reference_duration = float(reference_media.get("duration") or 0)
    candidate_duration = float(candidate_media.get("duration") or 0)
    reference_width = int(reference_media.get("width") or 0)
    reference_height = int(reference_media.get("height") or 0)
    candidate_width = int(candidate_media.get("width") or 0)
    candidate_height = int(candidate_media.get("height") or 0)
    if min(
        reference_fps,
        candidate_fps,
        reference_duration,
        candidate_duration,
        reference_width,
        reference_height,
        candidate_width,
        candidate_height,
    ) <= 0:
        raise ValueError("reference and candidate require complete positive video metadata")
    fps_delta = abs(reference_fps - candidate_fps)
    if fps_delta > float(settings["fps_tolerance"]) + 1e-9:
        raise ValueError(
            f"frame rates differ by {fps_delta:.6f} fps; this gate only supports same-timeline re-encodes"
        )
    duration_tolerance = float(settings["duration_tolerance_frames"]) / min(reference_fps, candidate_fps)
    duration_delta = abs(reference_duration - candidate_duration)
    if duration_delta > duration_tolerance + 1e-6:
        raise ValueError(
            f"durations differ by {duration_delta:.6f}s, over the {duration_tolerance:.6f}s tolerance"
        )
    reference_aspect = reference_width / reference_height
    candidate_aspect = candidate_width / candidate_height
    if abs(reference_aspect - candidate_aspect) > 0.001:
        raise ValueError("display aspect ratios differ; reframed or cropped videos require visual review, not pixel metrics")


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _parse_stats(path: Path, metric: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = dict(re.findall(r"([A-Za-z_]+):([^\s]+)", line))
        frame_raw = values.get("n")
        value_raw = values.get("All") if metric == "ssim" else values.get("psnr_avg")
        if frame_raw is None or value_raw is None:
            continue
        try:
            frame = int(frame_raw)
        except ValueError as exc:
            raise ValueError(f"invalid {metric} frame index: {frame_raw!r}") from exc
        value = _finite(value_raw)
        infinite = str(value_raw).lower() in {"inf", "+inf", "infinity", "+infinity"}
        if value is None and not infinite:
            raise ValueError(f"invalid {metric} metric value at frame {frame}: {value_raw!r}")
        rows.append({"frame": frame, "value": value, "infinite": infinite})
    if not rows:
        raise ValueError(f"FFmpeg produced no parseable {metric} frame metrics")
    return rows


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without values")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize_metrics(
    ssim_rows: Sequence[Mapping[str, Any]],
    psnr_rows: Sequence[Mapping[str, Any]],
    *,
    fps: float,
    width: int,
    height: int,
    candidate_scaled: bool,
    worst_frames: int,
) -> Dict[str, Any]:
    if len(ssim_rows) != len(psnr_rows):
        raise ValueError("SSIM and PSNR compared different frame counts")
    psnr_by_frame = {int(row["frame"]): row for row in psnr_rows}
    if set(psnr_by_frame) != {int(row["frame"]) for row in ssim_rows}:
        raise ValueError("SSIM and PSNR frame indexes do not match")
    ssim_values = [float(row["value"]) for row in ssim_rows if _finite(row.get("value")) is not None]
    if len(ssim_values) != len(ssim_rows):
        raise ValueError("SSIM metrics must be finite for every compared frame")
    finite_psnr = [float(row["value"]) for row in psnr_rows if _finite(row.get("value")) is not None]
    infinite_psnr = sum(bool(row.get("infinite")) for row in psnr_rows)
    if len(finite_psnr) + infinite_psnr != len(psnr_rows):
        raise ValueError("PSNR metrics contain non-finite values that are not positive infinity")
    worst = []
    for row in sorted(ssim_rows, key=lambda item: (float(item["value"]), int(item["frame"])))[:worst_frames]:
        frame = int(row["frame"])
        psnr_row = psnr_by_frame[frame]
        worst.append({
            "frame": frame,
            "time": round(max(0, frame - 1) / fps, 6),
            "ssim": round(float(row["value"]), 6),
            "psnr_db": round(float(psnr_row["value"]), 6) if psnr_row.get("value") is not None else None,
            "psnr_infinite": bool(psnr_row.get("infinite")),
        })
    return {
        "normalization": {
            "width": int(width),
            "height": int(height),
            "fps": round(float(fps), 6),
            "candidate_scaled": bool(candidate_scaled),
            "pixel_format": "yuv420p",
            "scale_flags": "lanczos",
        },
        "frames_compared": len(ssim_rows),
        "seconds_compared": round(len(ssim_rows) / fps, 6),
        "ssim": {
            "mean": round(statistics.fmean(ssim_values), 6),
            "p05": round(_percentile(ssim_values, 0.05), 6),
            "minimum": round(min(ssim_values), 6),
        },
        "psnr": {
            "mean_finite_db": round(statistics.fmean(finite_psnr), 6) if finite_psnr else None,
            "p05_finite_db": round(_percentile(finite_psnr, 0.05), 6) if finite_psnr else None,
            "minimum_finite_db": round(min(finite_psnr), 6) if finite_psnr else None,
            "finite_frames": len(finite_psnr),
            "infinite_frames": infinite_psnr,
            "all_infinite": not finite_psnr and infinite_psnr == len(psnr_rows),
        },
        "worst_frames": worst,
    }


def measure_quality(
    reference: Path,
    candidate: Path,
    *,
    reference_media: Mapping[str, Any],
    candidate_media: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    width = int(reference_media["width"])
    height = int(reference_media["height"])
    fps = float(reference_media["fps"])
    with tempfile.TemporaryDirectory(prefix="encode-quality-qa-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        ssim_path = temporary_root / "ssim.log"
        psnr_path = temporary_root / "psnr.log"
        graph = (
            f"[0:v:0]settb=AVTB,setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p,split=2[c_ssim][c_psnr];"
            f"[1:v:0]settb=AVTB,setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p,split=2[r_ssim][r_psnr];"
            f"[c_ssim][r_ssim]ssim=stats_file={_filter_path(ssim_path)}:shortest=1:eof_action=endall[ssim_out];"
            f"[c_psnr][r_psnr]psnr=stats_file={_filter_path(psnr_path)}:shortest=1:eof_action=endall[psnr_out]"
        )
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(candidate),
                "-i",
                str(reference),
                "-filter_complex_threads",
                "1",
                "-filter_complex",
                graph,
                "-map",
                "[ssim_out]",
                "-map",
                "[psnr_out]",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = " ".join((result.stderr or result.stdout or "FFmpeg quality comparison failed").split())
            raise ValueError(detail[-3000:])
        ssim_rows = _parse_stats(ssim_path, "ssim")
        psnr_rows = _parse_stats(psnr_path, "psnr")
    return summarize_metrics(
        ssim_rows,
        psnr_rows,
        fps=fps,
        width=width,
        height=height,
        candidate_scaled=(int(candidate_media["width"]), int(candidate_media["height"])) != (width, height),
        worst_frames=int(settings["worst_frames"]),
    )


def _derived_snapshot(report: Mapping[str, Any]) -> Dict[str, Any]:
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    ssim = analysis.get("ssim") if isinstance(analysis.get("ssim"), Mapping) else {}
    psnr = analysis.get("psnr") if isinstance(analysis.get("psnr"), Mapping) else {}
    blockers: List[str] = []
    warnings: List[str] = []
    mean_ssim = _finite(ssim.get("mean"))
    p05_ssim = _finite(ssim.get("p05"))
    mean_psnr = _finite(psnr.get("mean_finite_db"))
    min_mean_ssim = _finite(settings.get("min_mean_ssim"))
    min_p05_ssim = _finite(settings.get("min_p05_ssim"))
    min_mean_psnr = _finite(settings.get("min_mean_psnr_db"))
    if mean_ssim is not None and min_mean_ssim is not None and mean_ssim < min_mean_ssim:
        blockers.append(f"mean SSIM {mean_ssim:.6f} is below {min_mean_ssim:.6f}")
    if p05_ssim is not None and min_p05_ssim is not None and p05_ssim < min_p05_ssim:
        blockers.append(f"P05 SSIM {p05_ssim:.6f} is below {min_p05_ssim:.6f}")
    if not bool(psnr.get("all_infinite")) and mean_psnr is not None and min_mean_psnr is not None:
        if mean_psnr < min_mean_psnr:
            blockers.append(f"finite-frame mean PSNR {mean_psnr:.3f} dB is below {min_mean_psnr:.3f} dB")
    reference = report.get("reference") if isinstance(report.get("reference"), Mapping) else {}
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
    if (reference.get("width"), reference.get("height")) != (candidate.get("width"), candidate.get("height")):
        warnings.append(
            "Candidate resolution differs from the reference; metrics include deterministic Lanczos normalization."
        )
    reference_duration = _finite(reference.get("duration"))
    candidate_duration = _finite(candidate.get("duration"))
    if (
        reference_duration is not None
        and candidate_duration is not None
        and abs(reference_duration - candidate_duration) > 1e-6
    ):
        warnings.append(
            f"Reference and candidate durations differ by {abs(reference_duration - candidate_duration):.6f}s "
            "within the configured frame tolerance."
        )
    if not blockers:
        near = []
        if mean_ssim is not None and min_mean_ssim is not None and mean_ssim < min_mean_ssim + 0.01:
            near.append("mean SSIM")
        if p05_ssim is not None and min_p05_ssim is not None and p05_ssim < min_p05_ssim + 0.02:
            near.append("P05 SSIM")
        if mean_psnr is not None and min_mean_psnr is not None and mean_psnr < min_mean_psnr + 2:
            near.append("mean PSNR")
        if near:
            warnings.append("Quality is close to the configured floor: " + ", ".join(near) + ".")
    summary = {
        "frames_compared": int(analysis.get("frames_compared") or 0),
        "mean_ssim": mean_ssim,
        "p05_ssim": p05_ssim,
        "mean_psnr_db": mean_psnr,
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
        key: value for key, value in report.items() if key not in {"generated_at", "report_id"}
    })


def _set_derived(report: Dict[str, Any]) -> None:
    report.update(_derived_snapshot(report))
    report["report_id"] = canonical_report_id(report)


def build_report(
    reference: str | Path,
    candidate: str | Path,
    *,
    project_dir: str | Path,
    min_mean_ssim: float = DEFAULT_MIN_MEAN_SSIM,
    min_p05_ssim: float = DEFAULT_MIN_P05_SSIM,
    min_mean_psnr_db: float = DEFAULT_MIN_MEAN_PSNR_DB,
    duration_tolerance_frames: float = DEFAULT_DURATION_TOLERANCE_FRAMES,
    fps_tolerance: float = DEFAULT_FPS_TOLERANCE,
    worst_frames: int = DEFAULT_WORST_FRAMES,
    probe_fn: Optional[Callable[[Path], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    measure_fn = measure_fn or measure_quality
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    reference_path = _project_file(reference, root=root, label="reference video")
    candidate_path = _project_file(candidate, root=root, label="candidate video")
    settings = {
        "min_mean_ssim": float(min_mean_ssim),
        "min_p05_ssim": float(min_p05_ssim),
        "min_mean_psnr_db": float(min_mean_psnr_db),
        "duration_tolerance_frames": float(duration_tolerance_frames),
        "fps_tolerance": float(fps_tolerance),
        "worst_frames": int(worst_frames),
    }
    reference_media = dict(probe_fn(reference_path))
    candidate_media = dict(probe_fn(candidate_path))
    validate_pair(
        reference_path,
        candidate_path,
        reference_media=reference_media,
        candidate_media=candidate_media,
        settings=settings,
    )
    analysis = dict(
        measure_fn(
            reference_path,
            candidate_path,
            reference_media=reference_media,
            candidate_media=candidate_media,
            settings=settings,
        )
    )
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "reference": _file_contract(reference_path, root=root, media=reference_media),
        "candidate": _file_contract(candidate_path, root=root, media=candidate_media),
        "settings": settings,
        "algorithm": {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT},
        "analysis": analysis,
        "limitations": [
            "Use only for same-timeline, same-composition re-encodes; creative edits, reframing, grading, HDR tone mapping, retiming, or interpolation make pixel metrics misleading.",
            "SSIM and PSNR are engineering signals, not a substitute for full-speed visual review on the target display.",
            "This implementation does not invoke libvmaf, so the report does not claim a VMAF score.",
            "Audio quality is not measured; keep render/audio/channel QA as separate gates.",
        ],
    }
    _set_derived(report)
    return report


def _structural_blockers(report: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported version: {report.get('version')!r}")
    project_dir = str(report.get("project_dir") or "")
    if not project_dir or not Path(project_dir).expanduser().is_absolute():
        blockers.append("project_dir must be an absolute path")
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    blockers.extend(validate_settings(settings))
    if report.get("algorithm") != {"id": ALGORITHM_ID, **ALGORITHM_CONTRACT}:
        blockers.append("algorithm contract differs from the current encode-quality analyzer")
    for label in ("reference", "candidate"):
        record = report.get(label) if isinstance(report.get(label), Mapping) else {}
        if not str(record.get("path") or "") or len(str(record.get("sha256") or "")) != 64:
            blockers.append(f"{label} fingerprint is incomplete")
    reference = report.get("reference") if isinstance(report.get("reference"), Mapping) else {}
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    frames_compared = int(analysis.get("frames_compared") or 0)
    if frames_compared <= 0:
        blockers.append("analysis.frames_compared must be positive")
    ssim = analysis.get("ssim") if isinstance(analysis.get("ssim"), Mapping) else {}
    psnr = analysis.get("psnr") if isinstance(analysis.get("psnr"), Mapping) else {}
    for key in ("mean", "p05", "minimum"):
        value = _finite(ssim.get(key))
        if value is None or not 0 <= value <= 1:
            blockers.append(f"analysis.ssim.{key} must be between 0 and 1")
    if not isinstance(psnr.get("all_infinite"), bool):
        blockers.append("analysis.psnr.all_infinite must be boolean")
    finite_frames = int(psnr.get("finite_frames") or 0)
    infinite_frames = int(psnr.get("infinite_frames") or 0)
    if finite_frames < 0 or infinite_frames < 0 or finite_frames + infinite_frames != frames_compared:
        blockers.append("analysis.psnr frame counts must cover every compared frame")
    expected_all_infinite = finite_frames == 0 and infinite_frames == frames_compared and frames_compared > 0
    if psnr.get("all_infinite") != expected_all_infinite:
        blockers.append("analysis.psnr.all_infinite differs from the stored frame counts")
    psnr_values = [psnr.get("mean_finite_db"), psnr.get("p05_finite_db"), psnr.get("minimum_finite_db")]
    if expected_all_infinite:
        if any(value is not None for value in psnr_values):
            blockers.append("all-infinite PSNR must not store finite summary values")
    elif finite_frames > 0 and any(_finite(value) is None or float(value) <= 0 for value in psnr_values):
        blockers.append("finite PSNR summary values must be positive numbers")
    normalization = analysis.get("normalization") if isinstance(analysis.get("normalization"), Mapping) else {}
    expected_normalization = {
        "width": int(reference.get("width") or 0),
        "height": int(reference.get("height") or 0),
        "fps": round(float(reference.get("fps") or 0), 6),
        "candidate_scaled": (reference.get("width"), reference.get("height"))
        != (candidate.get("width"), candidate.get("height")),
        "pixel_format": "yuv420p",
        "scale_flags": "lanczos",
    }
    if normalization != expected_normalization:
        blockers.append("analysis.normalization differs from the bound media contract")
    worst = analysis.get("worst_frames")
    if not isinstance(worst, list):
        blockers.append("analysis.worst_frames must be a list")
    elif len(worst) > min(int(settings.get("worst_frames") or 0), max(0, frames_compared)):
        blockers.append("analysis.worst_frames exceeds the configured limit")
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
    probe_fn: Optional[Callable[[Path], Mapping[str, Any]]] = None,
    measure_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    measure_fn = measure_fn or measure_quality
    structural = _structural_blockers(report)
    live_blockers: List[str] = []
    stored_project = Path(str(report.get("project_dir") or "")).expanduser()
    requested_project = Path(project_dir).expanduser().resolve() if project_dir is not None else stored_project.resolve()
    if not stored_project.is_absolute() or stored_project.resolve() != requested_project:
        live_blockers.append("report project_dir differs from the current project")
    paths: Dict[str, Path] = {}
    media: Dict[str, Dict[str, Any]] = {}
    if not live_blockers:
        for label in ("reference", "candidate"):
            record = report.get(label) if isinstance(report.get(label), Mapping) else {}
            try:
                path = _project_file(str(record.get("path") or ""), root=requested_project, label=f"{label} video")
            except ValueError as exc:
                live_blockers.append(str(exc))
                continue
            paths[label] = path
            if _sha256(path) != str(record.get("sha256") or ""):
                live_blockers.append(f"{label} video bytes changed after encode-quality analysis")
            if path.stat().st_size != int(record.get("size_bytes") or -1):
                live_blockers.append(f"{label} video size changed after encode-quality analysis")
            try:
                current_media = dict(probe_fn(path))
            except Exception as exc:
                live_blockers.append(f"{label} video probe failed: {exc}")
                continue
            media[label] = current_media
            if _media_contract(current_media) != {key: record.get(key) for key in _media_contract(current_media)}:
                live_blockers.append(f"{label} video media contract changed after encode-quality analysis")
    if len(paths) == 2 and _same_path_or_file(paths["reference"], paths["candidate"]):
        live_blockers.append("reference and candidate now resolve to the same file")
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    if len(paths) == 2 and len(media) == 2 and not structural and not live_blockers:
        try:
            validate_pair(
                paths["reference"],
                paths["candidate"],
                reference_media=media["reference"],
                candidate_media=media["candidate"],
                settings=settings,
            )
            live_analysis = dict(
                measure_fn(
                    paths["reference"],
                    paths["candidate"],
                    reference_media=media["reference"],
                    candidate_media=media["candidate"],
                    settings=settings,
                )
            )
        except Exception as exc:
            live_blockers.append(f"live encode-quality comparison failed: {exc}")
        else:
            if live_analysis != report.get("analysis"):
                live_blockers.append("live encode-quality metrics differ from the stored analysis")
    quality_state = _derived_snapshot(report)
    all_blockers = list(quality_state["blockers"]) + structural + live_blockers
    warnings = list(quality_state["warnings"])
    return {
        "version": VERIFY_VERSION,
        "status": "blocked" if all_blockers else ("warn" if warnings else "ready"),
        "report_id": report.get("report_id"),
        "blockers": all_blockers,
        "warnings": warnings,
        "summary": {
            **quality_state["summary"],
            "quality_blocking": len(quality_state["blockers"]),
            "integrity_blocking": len(structural) + len(live_blockers),
            "blocking": len(all_blockers),
            "warnings": len(warnings),
        },
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    reference = report.get("reference") if isinstance(report.get("reference"), Mapping) else {}
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
    analysis = report.get("analysis") if isinstance(report.get("analysis"), Mapping) else {}
    ssim = analysis.get("ssim") if isinstance(analysis.get("ssim"), Mapping) else {}
    psnr = analysis.get("psnr") if isinstance(analysis.get("psnr"), Mapping) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    mean_psnr = psnr.get("mean_finite_db")
    lines = [
        "# Encode Quality QA",
        "",
        f"- Status: **{report.get('status', 'blocked')}**",
        f"- Reference: `{reference.get('path', '')}` (`{reference.get('sha256', '')}`)",
        f"- Candidate: `{candidate.get('path', '')}` (`{candidate.get('sha256', '')}`)",
        f"- Compared: {analysis.get('frames_compared', 0)} frames / {analysis.get('seconds_compared', 0)} seconds",
        f"- Mean SSIM: **{ssim.get('mean', 'n/a')}** (floor {settings.get('min_mean_ssim', 'n/a')})",
        f"- P05 SSIM: **{ssim.get('p05', 'n/a')}** (floor {settings.get('min_p05_ssim', 'n/a')})",
        f"- Finite-frame mean PSNR: **{mean_psnr if mean_psnr is not None else '∞'} dB** (floor {settings.get('min_mean_psnr_db', 'n/a')} dB)",
        "",
        "## Worst frames",
        "",
        "| frame | time | SSIM | PSNR dB |",
        "|---:|---:|---:|---:|",
    ]
    for row in analysis.get("worst_frames") or []:
        psnr_value = "∞" if row.get("psnr_infinite") else row.get("psnr_db")
        lines.append(
            f"| {row.get('frame')} | {float(row.get('time') or 0):.3f}s | {row.get('ssim')} | {psnr_value} |"
        )
    lines.extend([
        "",
        "## Review",
        "",
        "- Open the reference and candidate at every worst-frame timecode, then play through the surrounding motion at 1× on the target display.",
        "- Inspect text, faces, gradients, fast motion, edges, and fine texture; raise bitrate or reduce downscaling if artifacts are visible.",
        "- Do not use this report for cropped, reframed, graded, tone-mapped, interpolated, retimed, or editorially changed video.",
        "- Keep audio, render, flash, color, channel, and human review gates separate.",
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
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="compare a same-timeline candidate against its reference")
    analyze.add_argument("reference", help="reference/master video inside the project")
    analyze.add_argument("candidate", help="compressed/transcoded candidate inside the project")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--output", default="verify/encode_quality_qa.json")
    analyze.add_argument("--markdown")
    analyze.add_argument("--min-mean-ssim", type=float, default=DEFAULT_MIN_MEAN_SSIM)
    analyze.add_argument("--min-p05-ssim", type=float, default=DEFAULT_MIN_P05_SSIM)
    analyze.add_argument("--min-mean-psnr-db", type=float, default=DEFAULT_MIN_MEAN_PSNR_DB)
    analyze.add_argument("--duration-tolerance-frames", type=float, default=DEFAULT_DURATION_TOLERANCE_FRAMES)
    analyze.add_argument("--fps-tolerance", type=float, default=DEFAULT_FPS_TOLERANCE)
    analyze.add_argument("--worst-frames", type=int, default=DEFAULT_WORST_FRAMES)
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="live-verify a saved report and both bound videos")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            verification = verify_report(_load_report(args.report), args.project_dir)
            print(json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        root = Path(args.project_dir).expanduser().resolve()
        reference = _project_file(args.reference, root=root, label="reference video")
        candidate = _project_file(args.candidate, root=root, label="candidate video")
        output = _safe_output(
            args.output,
            root=root,
            label="report",
            forbidden=[reference, candidate],
            force=args.force,
        )
        markdown: Optional[Path] = None
        if args.markdown:
            markdown = _safe_output(
                args.markdown,
                root=root,
                label="Markdown report",
                forbidden=[reference, candidate, output],
                force=args.force,
            )
        report = build_report(
            reference,
            candidate,
            project_dir=root,
            min_mean_ssim=args.min_mean_ssim,
            min_p05_ssim=args.min_p05_ssim,
            min_mean_psnr_db=args.min_mean_psnr_db,
            duration_tolerance_frames=args.duration_tolerance_frames,
            fps_tolerance=args.fps_tolerance,
            worst_frames=args.worst_frames,
        )
        _atomic_write_json(output, report)
        if markdown is not None:
            _atomic_write_text(markdown, emit_markdown(report))
        print(json.dumps({
            "status": report["status"],
            "report": str(output),
            "report_id": report["report_id"],
            "summary": report["summary"],
        }, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if args.strict and report["summary"]["blocking"] else 0
    except Exception as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
