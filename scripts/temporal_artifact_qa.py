#!/usr/bin/env python3
"""Screen final or generated video for brief return-to-state frame artifacts.

The local detector looks for one-to-three-frame excursions whose entry and exit
are both much larger than nearby motion while the frames immediately outside
the excursion remain similar.  Candidates are only triage signals: each one is
exported as a before/suspect/after JPEG and requires an explicit visual review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from generated_motion_window import probe_media


VERSION = "temporal_artifact_qa.v1"
RESPONSE_VERSION = "temporal_artifact_qa_response.v1"
DEFAULT_ANALYSIS_FPS = 30.0
DEFAULT_ANALYSIS_WIDTH = 160
DEFAULT_LOCAL_RADIUS = 12
DEFAULT_MAX_ARTIFACT_FRAMES = 3
DEFAULT_SPIKE_RATIO = 2.5
DEFAULT_MIN_TRANSITION_MSE = 150.0
DEFAULT_RECOVERY_RATIO = 0.35
DEFAULT_MAX_CANDIDATES = 24
DECISIONS = {"artifact", "intentional_edit", "uncertain"}

ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "local_return_to_state_mse_screen",
    "sample": "ffmpeg fps + area scale + 8-bit grayscale",
    "transition_metric": "mean_squared_error_between_adjacent_sampled_frames",
    "candidate": (
        "one_to_max_artifact_frames excursion with entry and exit MSE above both "
        "an absolute floor and local-baseline ratio"
    ),
    "recovery": (
        "frame before excursion and frame after excursion must remain similar "
        "relative to entry and exit"
    ),
    "automatic_semantic_verdict": False,
    "human_review_required_when_candidates_exist": True,
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


def _load_json(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


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


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def default_settings() -> Dict[str, Any]:
    return {
        "analysis_fps": DEFAULT_ANALYSIS_FPS,
        "analysis_width": DEFAULT_ANALYSIS_WIDTH,
        "local_radius": DEFAULT_LOCAL_RADIUS,
        "max_artifact_frames": DEFAULT_MAX_ARTIFACT_FRAMES,
        "spike_ratio": DEFAULT_SPIKE_RATIO,
        "min_transition_mse": DEFAULT_MIN_TRANSITION_MSE,
        "recovery_ratio": DEFAULT_RECOVERY_RATIO,
        "max_candidates": DEFAULT_MAX_CANDIDATES,
    }


def validate_settings(settings: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    fps = _finite(settings.get("analysis_fps"))
    width = _finite(settings.get("analysis_width"))
    radius = _finite(settings.get("local_radius"))
    max_frames = _finite(settings.get("max_artifact_frames"))
    ratio = _finite(settings.get("spike_ratio"))
    minimum = _finite(settings.get("min_transition_mse"))
    recovery = _finite(settings.get("recovery_ratio"))
    maximum = _finite(settings.get("max_candidates"))
    if fps is None or not 12 <= fps <= 60:
        blockers.append("settings.analysis_fps must be between 12 and 60")
    if width is None or int(width) != width or not 32 <= int(width) <= 640:
        blockers.append("settings.analysis_width must be an integer between 32 and 640")
    if radius is None or int(radius) != radius or not 4 <= int(radius) <= 120:
        blockers.append("settings.local_radius must be an integer between 4 and 120")
    if max_frames is None or int(max_frames) != max_frames or not 1 <= int(max_frames) <= 6:
        blockers.append("settings.max_artifact_frames must be an integer between 1 and 6")
    if ratio is None or not 1.25 <= ratio <= 20:
        blockers.append("settings.spike_ratio must be between 1.25 and 20")
    if minimum is None or not 1 <= minimum <= 65025:
        blockers.append("settings.min_transition_mse must be between 1 and 65025")
    if recovery is None or not 0.01 <= recovery <= 0.95:
        blockers.append("settings.recovery_ratio must be between 0.01 and 0.95")
    if maximum is None or int(maximum) != maximum or not 1 <= int(maximum) <= 100:
        blockers.append("settings.max_candidates must be an integer between 1 and 100")
    return blockers


def _analysis_dimensions(media: Mapping[str, Any], width: int) -> Tuple[int, int]:
    source_width = int(media.get("width") or 0)
    source_height = int(media.get("height") or 0)
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source display dimensions must be positive")
    height = max(2, int(round((source_height * width / source_width) / 2)) * 2)
    return width, height


def extract_gray_frames(
    path: Path,
    *,
    media: Mapping[str, Any],
    analysis_fps: float,
    analysis_width: int,
) -> Tuple[List[bytes], float, int, int]:
    effective_fps = min(float(analysis_fps), float(media.get("fps") or analysis_fps))
    width, height = _analysis_dimensions(media, int(analysis_width))
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-vf", f"fps={effective_fps:.6f},scale={width}:{height}:flags=area,format=gray",
        "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or b"FFmpeg frame extraction failed").decode("utf-8", errors="replace").strip()
        raise ValueError(detail.splitlines()[-1] if detail else "FFmpeg frame extraction failed")
    frame_size = width * height
    if not result.stdout or len(result.stdout) % frame_size:
        raise ValueError("FFmpeg returned an incomplete grayscale frame stream")
    frames = [result.stdout[offset:offset + frame_size] for offset in range(0, len(result.stdout), frame_size)]
    if len(frames) < 3:
        raise ValueError("at least three sampled video frames are required")
    return frames, effective_fps, width, height


def frame_mse(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("frames must have equal non-zero byte lengths")
    return sum((a - b) * (a - b) for a, b in zip(left, right)) / len(left)


def _candidate_score(candidate: Mapping[str, Any]) -> float:
    threshold = max(float(candidate.get("threshold_mse") or 0), 1.0)
    spike = min(float(candidate.get("entry_mse") or 0), float(candidate.get("exit_mse") or 0)) / threshold
    recovery = float(candidate.get("recovery_fraction") or 1)
    return round(spike / max(recovery, 0.01), 6)


def analyze_frame_sequence(
    frames: Sequence[bytes],
    *,
    width: int,
    height: int,
    fps: float,
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    errors = validate_settings(settings)
    if errors:
        raise ValueError("; ".join(errors))
    if len(frames) < 3:
        raise ValueError("at least three sampled frames are required")
    expected_size = int(width) * int(height)
    if any(len(frame) != expected_size for frame in frames):
        raise ValueError("sampled grayscale frame size does not match width and height")

    transitions = [frame_mse(left, right) for left, right in zip(frames, frames[1:])]
    radius = int(settings["local_radius"])
    max_artifact_frames = int(settings["max_artifact_frames"])
    spike_ratio = float(settings["spike_ratio"])
    minimum = float(settings["min_transition_mse"])
    recovery_ratio = float(settings["recovery_ratio"])
    raw_candidates: List[Dict[str, Any]] = []

    for start in range(1, len(frames) - 1):
        for length in range(1, min(max_artifact_frames, len(frames) - start - 1) + 1):
            end = start + length - 1
            entry_index = start - 1
            exit_index = end
            local_start = max(0, entry_index - radius)
            local_end = min(len(transitions), exit_index + radius + 1)
            baseline_values = [
                transitions[index]
                for index in range(local_start, local_end)
                if index < entry_index or index > exit_index
            ]
            baseline = float(statistics.median(baseline_values)) if baseline_values else 0.0
            threshold = max(minimum, baseline * spike_ratio)
            entry = transitions[entry_index]
            exit_value = transitions[exit_index]
            if entry < threshold or exit_value < threshold:
                continue
            recovery = frame_mse(frames[start - 1], frames[end + 1])
            recovery_fraction = recovery / max(min(entry, exit_value), 1.0)
            recovery_ceiling = max(minimum, baseline * 1.5)
            if recovery_fraction > recovery_ratio or recovery > recovery_ceiling:
                continue
            candidate = {
                "start_frame": start,
                "end_frame": end,
                "frame_count": length,
                "start_time": round(start / fps, 6),
                "end_time": round((end + 1) / fps, 6),
                "entry_mse": round(entry, 6),
                "exit_mse": round(exit_value, 6),
                "recovery_mse": round(recovery, 6),
                "recovery_fraction": round(recovery_fraction, 6),
                "local_baseline_mse": round(baseline, 6),
                "threshold_mse": round(threshold, 6),
            }
            candidate["risk_score"] = _candidate_score(candidate)
            raw_candidates.append(candidate)

    selected: List[Dict[str, Any]] = []
    for candidate in sorted(raw_candidates, key=lambda item: (-float(item["risk_score"]), int(item["frame_count"]))):
        start = int(candidate["start_frame"])
        end = int(candidate["end_frame"])
        if any(start <= int(existing["end_frame"]) and end >= int(existing["start_frame"]) for existing in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: (int(item["start_frame"]), int(item["end_frame"])))
    detected = len(selected)
    selected = selected[: int(settings["max_candidates"])]
    for index, candidate in enumerate(selected, start=1):
        candidate["candidate_id"] = f"temporal_artifact_{index:04d}"

    return {
        "sample": {
            "fps": round(float(fps), 6),
            "width": int(width),
            "height": int(height),
            "frames": len(frames),
            "seconds": round(len(frames) / fps, 6),
        },
        "transitions": {
            "count": len(transitions),
            "median_mse": round(float(statistics.median(transitions)), 6),
            "maximum_mse": round(max(transitions), 6),
        },
        "candidates": selected,
        "detected_candidates": detected,
        "truncated_candidates": max(0, detected - len(selected)),
    }


def analyze_video(path: Path, *, media: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    frames, fps, width, height = extract_gray_frames(
        path,
        media=media,
        analysis_fps=float(settings["analysis_fps"]),
        analysis_width=int(settings["analysis_width"]),
    )
    return analyze_frame_sequence(frames, width=width, height=height, fps=fps, settings=settings)


def generate_candidate_evidence(
    source: Path,
    output: Path,
    *,
    candidate: Mapping[str, Any],
    fps: float,
    thumb_width: int = 320,
    force: bool = False,
) -> None:
    if output.exists() and not force:
        raise ValueError(f"refusing to overwrite candidate evidence without --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    before = max(0, int(candidate["start_frame"]) - 1)
    suspect = (int(candidate["start_frame"]) + int(candidate["end_frame"])) // 2
    after = int(candidate["end_frame"]) + 1
    select = "+".join(f"eq(n\\,{index})" for index in (before, suspect, after))
    filtergraph = (
        f"fps={fps:.6f},select='{select}',scale={thumb_width}:-2:flags=lanczos,"
        "tile=3x1:nb_frames=3:padding=4:margin=4:color=black"
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", filtergraph, "-frames:v", "1", "-q:v", "2", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "FFmpeg evidence extraction failed").strip()
        raise ValueError(detail.splitlines()[-1] if detail else "FFmpeg evidence extraction failed")


def _scan_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256({
        "version": report.get("version"),
        "project_dir": report.get("project_dir"),
        "source": report.get("source"),
        "algorithm": report.get("algorithm"),
        "settings": report.get("settings"),
        "analysis": report.get("analysis"),
        "evidence": report.get("evidence"),
    })


def _report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256({
        "scan_id": report.get("scan_id"),
        "response": report.get("response"),
        "reviews": report.get("reviews"),
        "status": report.get("status"),
        "summary": report.get("summary"),
        "blockers": report.get("blockers"),
        "warnings": report.get("warnings"),
    })


def _response_template(scan_id: str, candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "scan_id": scan_id,
        "reviewed_by": "",
        "full_video_played_at_1x": None,
        "reviews": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "decision": "",
                "frame_observations": {"before": "", "suspect": "", "after": ""},
                "reason": "",
                "repair_action": "",
            }
            for candidate in candidates
        ],
    }


def _review_snapshot(
    report: Mapping[str, Any],
    response: Optional[Mapping[str, Any]],
    *,
    scan_blockers: Sequence[str] = (),
) -> Dict[str, Any]:
    blockers = list(scan_blockers)
    warnings: List[str] = []
    candidates = {
        str(item.get("candidate_id") or ""): item
        for item in (report.get("analysis") or {}).get("candidates") or []
        if isinstance(item, Mapping)
    }
    reviews: List[Dict[str, Any]] = []
    if not candidates:
        if response:
            blockers.append("response must be omitted when the automatic scan found no candidates")
    elif not isinstance(response, Mapping):
        blockers.append(f"{len(candidates)} temporal artifact candidate(s) require explicit visual review")
    else:
        if response.get("version") != RESPONSE_VERSION:
            blockers.append(f"response version must be {RESPONSE_VERSION}")
        if str(response.get("scan_id") or "") != str(report.get("scan_id") or ""):
            blockers.append("response scan_id does not match the analyzed video")
        if not str(response.get("reviewed_by") or "").strip():
            blockers.append("reviewed_by is required (label only; not identity authentication)")
        if response.get("full_video_played_at_1x") is not True:
            blockers.append("full_video_played_at_1x must be true")
        raw_reviews = response.get("reviews") or []
        if not isinstance(raw_reviews, list):
            blockers.append("response reviews must be a list")
            raw_reviews = []
        provided: Dict[str, Mapping[str, Any]] = {}
        for raw in raw_reviews:
            if not isinstance(raw, Mapping):
                blockers.append("response reviews must contain objects")
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            if candidate_id in provided:
                blockers.append(f"duplicate response review for {candidate_id}")
            provided[candidate_id] = raw
        for missing in sorted(set(candidates).difference(provided)):
            blockers.append(f"missing response review for {missing}")
        for extra in sorted(set(provided).difference(candidates)):
            blockers.append(f"response contains unknown candidate id {extra}")

        for candidate_id in candidates:
            raw = provided.get(candidate_id, {})
            decision = str(raw.get("decision") or "").strip().lower()
            observations = raw.get("frame_observations") or {}
            errors: List[str] = []
            if decision not in DECISIONS:
                errors.append(f"decision must be one of {sorted(DECISIONS)}")
            if not isinstance(observations, Mapping):
                observations = {}
                errors.append("frame_observations must be an object")
            normalized_observations = {
                key: str(observations.get(key) or "").strip()
                for key in ("before", "suspect", "after")
            }
            for key, value in normalized_observations.items():
                if not value:
                    errors.append(f"frame_observations.{key} is required")
            reason = str(raw.get("reason") or "").strip()
            repair_action = str(raw.get("repair_action") or "").strip()
            if not reason:
                errors.append("reason is required")
            if decision in {"artifact", "uncertain"} and not repair_action:
                errors.append(f"{decision or 'non-pass'} decision requires repair_action")
            review = {
                "candidate_id": candidate_id,
                "decision": decision,
                "frame_observations": normalized_observations,
                "reason": reason,
                "repair_action": repair_action,
                "validation_errors": sorted(set(errors)),
            }
            reviews.append(review)
            blockers.extend(f"{candidate_id}: {error}" for error in review["validation_errors"])
            if not review["validation_errors"]:
                if decision == "artifact":
                    blockers.append(f"{candidate_id}: confirmed temporal artifact requires repair")
                elif decision == "uncertain":
                    blockers.append(f"{candidate_id}: uncertain visual result requires repair or escalation")
                elif decision == "intentional_edit":
                    warnings.append(f"{candidate_id}: heuristic candidate accepted as an intentional edit")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    summary = {
        "candidates": len(candidates),
        "intentional_edits": sum(1 for item in reviews if item["decision"] == "intentional_edit" and not item["validation_errors"]),
        "confirmed_artifacts": sum(1 for item in reviews if item["decision"] == "artifact" and not item["validation_errors"]),
        "uncertain": sum(1 for item in reviews if item["decision"] == "uncertain" and not item["validation_errors"]),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "reviews": reviews,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_report(
    source: str | Path,
    *,
    project_dir: str | Path,
    evidence_dir: str | Path,
    settings: Optional[Mapping[str, Any]] = None,
    probe_fn: Optional[Callable[[str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
    evidence_fn: Optional[Callable[..., None]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    probe_fn = probe_fn or probe_media
    analyze_fn = analyze_fn or analyze_video
    evidence_fn = evidence_fn or generate_candidate_evidence
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source_path = _project_file(source, root=root, label="source video")
    evidence_root = _lexical_project_path(evidence_dir, root=root, label="evidence directory")
    if evidence_root.exists() and not evidence_root.is_dir():
        raise ValueError(f"evidence directory is not a directory: {evidence_root}")
    evidence_root.mkdir(parents=True, exist_ok=True)
    normalized_settings = default_settings()
    if settings:
        normalized_settings.update(dict(settings))
    errors = validate_settings(normalized_settings)
    if errors:
        raise ValueError("; ".join(errors))
    media = _media_contract(probe_fn(str(source_path)))
    analysis = dict(analyze_fn(source_path, media=media, settings=normalized_settings))
    evidence: List[Dict[str, Any]] = []
    for candidate in analysis.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        output = evidence_root / f"{candidate_id}_before_suspect_after.jpg"
        if output.is_symlink():
            raise ValueError(f"candidate evidence must not be a symlink: {output}")
        if _same_path_or_file(output, source_path):
            raise ValueError("candidate evidence must not overwrite the source video")
        evidence_fn(
            source_path,
            output,
            candidate=candidate,
            fps=float((analysis.get("sample") or {}).get("fps") or normalized_settings["analysis_fps"]),
            force=force,
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError(f"candidate evidence was not created: {output}")
        evidence.append({
            "candidate_id": candidate_id,
            "path": _relative(output.resolve(), root),
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
            "frame_order": ["before", "suspect", "after"],
        })
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": {
            "path": _relative(source_path, root),
            "sha256": _sha256(source_path),
            "size_bytes": source_path.stat().st_size,
            "media": media,
        },
        "algorithm": {"id": ALGORITHM_ID, **dict(ALGORITHM_CONTRACT)},
        "settings": normalized_settings,
        "analysis": analysis,
        "evidence": evidence,
        "response": None,
        "limitations": [
            "This is a local heuristic screen, not an automatic visual-quality verdict.",
            "It targets brief return-to-state excursions and can miss persistent drift, semantic deformation, or artifacts smaller than the sampled pixels.",
            "Fast action, intentional flash frames, whip pans, or stylized glitches can trigger candidates and require contextual review.",
            "Reviewer labels are not identity authentication or digital signatures.",
        ],
    }
    report["scan_id"] = _scan_id(report)
    report["response_template"] = _response_template(
        report["scan_id"],
        (report.get("analysis") or {}).get("candidates") or [],
    )
    report.update(_review_snapshot(report, None))
    report["report_id"] = _report_id(report)
    return report


def _verify_scan(
    report: Mapping[str, Any],
    *,
    project_dir: Optional[str | Path] = None,
    probe_fn: Optional[Callable[[str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> List[str]:
    probe_fn = probe_fn or probe_media
    analyze_fn = analyze_fn or analyze_video
    blockers: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"report version must be {VERSION}")
    if (report.get("algorithm") or {}).get("id") != ALGORITHM_ID:
        blockers.append("algorithm id does not match the current temporal artifact screen")
    if dict(report.get("algorithm") or {}) != {"id": ALGORITHM_ID, **dict(ALGORITHM_CONTRACT)}:
        blockers.append("algorithm contract differs from the current implementation")
    settings = report.get("settings") or {}
    if not isinstance(settings, Mapping):
        settings = {}
        blockers.append("settings must be an object")
    blockers.extend(validate_settings(settings))
    root = Path(project_dir or str(report.get("project_dir") or "")).expanduser().resolve()
    if not root.is_dir():
        blockers.append(f"project directory is missing: {root}")
        return sorted(set(blockers))
    source = report.get("source") or {}
    try:
        source_path = _project_file(str(source.get("path") or ""), root=root, label="source video")
    except ValueError as exc:
        blockers.append(str(exc))
        return sorted(set(blockers))
    if _sha256(source_path) != str(source.get("sha256") or ""):
        blockers.append("source video bytes changed after temporal artifact analysis")
    if source_path.stat().st_size != int(source.get("size_bytes") or -1):
        blockers.append("source video size changed after temporal artifact analysis")
    try:
        live_media = _media_contract(probe_fn(str(source_path)))
    except Exception as exc:
        blockers.append(f"source video probe failed: {exc}")
        live_media = {}
    if live_media and live_media != dict(source.get("media") or {}):
        blockers.append("source video media contract changed after temporal artifact analysis")
    if live_media and not validate_settings(settings):
        try:
            live_analysis = dict(analyze_fn(source_path, media=live_media, settings=settings))
        except Exception as exc:
            blockers.append(f"live temporal artifact analysis failed: {exc}")
        else:
            if live_analysis != dict(report.get("analysis") or {}):
                blockers.append("live temporal artifact evidence differs from the stored analysis")

    candidates = {
        str(item.get("candidate_id") or "")
        for item in (report.get("analysis") or {}).get("candidates") or []
        if isinstance(item, Mapping)
    }
    evidence = report.get("evidence") or []
    if not isinstance(evidence, list):
        blockers.append("evidence must be a list")
        evidence = []
    seen = set()
    for item in evidence:
        if not isinstance(item, Mapping):
            blockers.append("evidence entries must be objects")
            continue
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id in seen:
            blockers.append(f"duplicate evidence for {candidate_id}")
        seen.add(candidate_id)
        try:
            path = _project_file(str(item.get("path") or ""), root=root, label=f"evidence for {candidate_id}")
        except ValueError as exc:
            blockers.append(str(exc))
            continue
        if _same_path_or_file(path, source_path):
            blockers.append(f"evidence for {candidate_id} must not alias the source video")
        if _sha256(path) != str(item.get("sha256") or ""):
            blockers.append(f"evidence bytes changed for {candidate_id}")
        if path.stat().st_size != int(item.get("size_bytes") or -1):
            blockers.append(f"evidence size changed for {candidate_id}")
        if item.get("frame_order") != ["before", "suspect", "after"]:
            blockers.append(f"evidence frame order changed for {candidate_id}")
    if seen != candidates:
        blockers.append("evidence candidate coverage does not match the stored analysis")
    if str(report.get("scan_id") or "") != _scan_id(report):
        blockers.append("scan_id does not match stored source, settings, analysis, and evidence")
    expected_template = _response_template(
        str(report.get("scan_id") or ""),
        (report.get("analysis") or {}).get("candidates") or [],
    )
    if report.get("response_template") != expected_template:
        blockers.append("response_template does not match the analyzed candidates")
    return sorted(set(blockers))


def audit_report(
    report: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    project_dir: Optional[str | Path] = None,
    probe_fn: Optional[Callable[[str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    updated = dict(report)
    updated["response"] = dict(response)
    updated.update(_review_snapshot(
        updated,
        updated["response"],
        scan_blockers=_verify_scan(updated, project_dir=project_dir, probe_fn=probe_fn, analyze_fn=analyze_fn),
    ))
    updated["report_id"] = _report_id(updated)
    return updated


def verify_report(
    report: Mapping[str, Any],
    project_dir: Optional[str | Path] = None,
    *,
    probe_fn: Optional[Callable[[str], Mapping[str, Any]]] = None,
    analyze_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    scan_blockers = _verify_scan(report, project_dir=project_dir, probe_fn=probe_fn, analyze_fn=analyze_fn)
    canonical = _review_snapshot(report, report.get("response"), scan_blockers=scan_blockers)
    blockers = list(canonical["blockers"])
    for key in ("status", "reviews", "summary", "blockers", "warnings"):
        if report.get(key) != canonical.get(key):
            blockers.append(f"stored {key} does not match live temporal artifact audit")
    if str(report.get("report_id") or "") != _report_id(report):
        blockers.append("report_id does not match stored report content")
    blockers = sorted(set(blockers))
    warnings = sorted(set(canonical["warnings"]))
    summary = dict(canonical["summary"])
    summary["blocking"] = len(blockers)
    summary["warnings"] = len(warnings)
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    analysis = report.get("analysis") or {}
    sample = analysis.get("sample") or {}
    lines = [
        "# Temporal Artifact QA",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Source: `{(report.get('source') or {}).get('path', '')}`",
        f"- Scan ID: `{report.get('scan_id', '')}`",
        f"- Sample: {sample.get('frames', 0)} frames at {float(sample.get('fps') or 0):.3f} fps, {sample.get('width', 0)}x{sample.get('height', 0)} grayscale",
        f"- Candidates: {(report.get('summary') or {}).get('candidates', 0)}",
        "",
        "## Candidate evidence",
        "",
        "Each JPEG is ordered left-to-right: before / suspect / after.",
        "",
        "| candidate | time | frames | entry / exit MSE | recovery MSE | evidence | decision |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    evidence = {str(item.get("candidate_id") or ""): item for item in report.get("evidence") or []}
    reviews = {str(item.get("candidate_id") or ""): item for item in report.get("reviews") or []}
    for candidate in analysis.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        path = (evidence.get(candidate_id) or {}).get("path", "")
        decision = (reviews.get(candidate_id) or {}).get("decision", "pending") or "pending"
        lines.append(
            f"| {candidate_id} | {float(candidate.get('start_time') or 0):.3f}-{float(candidate.get('end_time') or 0):.3f}s "
            f"| {candidate.get('frame_count', 0)} | {float(candidate.get('entry_mse') or 0):.1f} / {float(candidate.get('exit_mse') or 0):.1f} "
            f"| {float(candidate.get('recovery_mse') or 0):.1f} | `{path}` | {decision} |"
        )
    if not analysis.get("candidates"):
        lines.append("| none | - | - | - | - | - | automatic screen found no candidate |")
    lines.extend([
        "",
        "## Review contract",
        "",
        "- Play the full video at 1x before deciding; a triptych is only a locator.",
        "- Describe the before, suspect, and after frame separately.",
        "- Use `intentional_edit` only when the transient is expected in the timeline and visually clean.",
        "- Use `artifact` for a confirmed tear, flash frame, deformation, or unintended insert; provide a concrete repair action.",
        "- Use `uncertain` when the evidence is inconclusive; it remains blocking until repaired or escalated.",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    return "\n".join(lines) + "\n"


def _settings_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "analysis_fps": args.analysis_fps,
        "analysis_width": args.analysis_width,
        "local_radius": args.local_radius,
        "max_artifact_frames": args.max_artifact_frames,
        "spike_ratio": args.spike_ratio,
        "min_transition_mse": args.min_transition_mse,
        "recovery_ratio": args.recovery_ratio,
        "max_candidates": args.max_candidates,
    }


def _analyze_command(args: argparse.Namespace) -> int:
    root = Path(args.project_dir).expanduser().resolve()
    source = _project_file(args.source, root=root, label="source video")
    output = _safe_output(args.output, root=root, label="report output", forbidden=[source], force=args.force)
    markdown = _safe_output(args.markdown, root=root, label="Markdown output", forbidden=[source], force=args.force) if args.markdown else None
    response_template = _safe_output(args.response_template, root=root, label="response template output", forbidden=[source], force=args.force) if args.response_template else None
    report = build_report(
        source,
        project_dir=root,
        evidence_dir=args.evidence_dir,
        settings=_settings_from_args(args),
        force=args.force,
    )
    _atomic_write_json(output, report)
    if markdown:
        _atomic_write_text(markdown, emit_markdown(report))
    if response_template:
        _atomic_write_json(response_template, report["response_template"])
    summary = report["summary"]
    print(f"Temporal artifact QA: status={report['status']} candidates={summary['candidates']} blocking={summary['blocking']} warnings={summary['warnings']}")
    return 2 if args.strict and summary["blocking"] else 0


def _audit_command(args: argparse.Namespace) -> int:
    raw_report = _load_json(args.report)
    root = Path(args.project_dir or str(raw_report.get("project_dir") or ".")).expanduser().resolve()
    _project_file(args.report, root=root, label="analysis report")
    response_path = _project_file(args.response, root=root, label="review response")
    source = _project_file(str((raw_report.get("source") or {}).get("path") or ""), root=root, label="source video")
    output = _safe_output(args.output, root=root, label="audit output", forbidden=[source], force=args.force)
    markdown = _safe_output(args.markdown, root=root, label="Markdown output", forbidden=[source], force=args.force) if args.markdown else None
    response = _load_json(response_path)
    audited = audit_report(raw_report, response, project_dir=root)
    _atomic_write_json(output, audited)
    if markdown:
        _atomic_write_text(markdown, emit_markdown(audited))
    summary = audited["summary"]
    print(f"Temporal artifact audit: status={audited['status']} candidates={summary['candidates']} blocking={summary['blocking']} warnings={summary['warnings']}")
    return 2 if args.strict and summary["blocking"] else 0


def _verify_command(args: argparse.Namespace) -> int:
    report = _load_json(args.report)
    verification = verify_report(report, args.project_dir)
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 2 if args.strict and verification["summary"]["blocking"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Screen brief return-to-state frame artifacts and bind human review evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Run the local screen and export before/suspect/after evidence.")
    analyze.add_argument("source", help="Final or generated video inside the project directory.")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--evidence-dir", default="verify/temporal_artifact_frames")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--response-template")
    analyze.add_argument("--analysis-fps", type=float, default=DEFAULT_ANALYSIS_FPS)
    analyze.add_argument("--analysis-width", type=int, default=DEFAULT_ANALYSIS_WIDTH)
    analyze.add_argument("--local-radius", type=int, default=DEFAULT_LOCAL_RADIUS)
    analyze.add_argument("--max-artifact-frames", type=int, default=DEFAULT_MAX_ARTIFACT_FRAMES)
    analyze.add_argument("--spike-ratio", type=float, default=DEFAULT_SPIKE_RATIO)
    analyze.add_argument("--min-transition-mse", type=float, default=DEFAULT_MIN_TRANSITION_MSE)
    analyze.add_argument("--recovery-ratio", type=float, default=DEFAULT_RECOVERY_RATIO)
    analyze.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    analyze.add_argument("--strict", action="store_true")
    analyze.add_argument("--force", action="store_true")
    analyze.set_defaults(func=_analyze_command)

    audit = subparsers.add_parser("audit", help="Bind a completed visual response to the analyzed video and evidence.")
    audit.add_argument("--report", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--project-dir")
    audit.add_argument("--markdown")
    audit.add_argument("--strict", action="store_true")
    audit.add_argument("--force", action="store_true")
    audit.set_defaults(func=_audit_command)

    verify = subparsers.add_parser("verify", help="Re-run the screen and reject source, evidence, response, or report drift.")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir")
    verify.add_argument("--strict", action="store_true")
    verify.set_defaults(func=_verify_command)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
