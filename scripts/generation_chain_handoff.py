#!/usr/bin/env python3
"""Extract and verify an approved clip tail for sequential video generation.

The workflow is local-only. It binds an exact decoded frame from an already
reviewed generated clip to one approved sequence-handoff boundary. It never
uploads media, submits a provider job, spends credits, or claims that a tail
frame alone preserves identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from generated_clip_review import REPORT_VERSION as CLIP_REVIEW_VERSION
from generated_clip_review import verify_report as verify_clip_review
from sequence_handoff import REPORT_VERSION as SEQUENCE_HANDOFF_VERSION
from sequence_handoff import verify_report as verify_sequence_handoff


VERSION = "generation_chain_handoff.v1"
DECISIONS = {"use_exact_start_frame", "reject"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root(project_dir: str) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project directory is not a directory: {root}")
    return root


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical(root: Path, raw: str, *, label: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(str(candidate)))
    if not _inside(root, candidate):
        raise ValueError(f"{label} must stay inside the project: {candidate}")
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return candidate


def _project_file(root: Path, raw: str, *, label: str) -> Path:
    candidate = _lexical(root, raw, label=label).resolve(strict=True)
    if not candidate.is_file():
        raise ValueError(f"{label} is not a file: {candidate}")
    return candidate


def _output_file(
    root: Path,
    raw: str,
    *,
    label: str,
    protected: Iterable[Path],
    force: bool,
) -> Path:
    candidate = _lexical(root, raw, label=label).resolve(strict=False)
    protected_paths = [path.resolve(strict=False) for path in protected]
    if candidate in protected_paths:
        raise ValueError(f"{label} must not overwrite an input: {candidate}")
    if candidate.exists() and any(
        candidate.samefile(path) for path in protected_paths if path.exists()
    ):
        raise ValueError(f"{label} must not overwrite a hard-linked input: {candidate}")
    if candidate.exists() and not force:
        raise ValueError(f"{label} already exists; pass --force to replace: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _relative(root: Path, path: Path) -> str:
    return path.resolve(strict=True).relative_to(root).as_posix()


def _file_record(root: Path, path: Path) -> Dict[str, Any]:
    return {
        "path": _relative(root, path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_matches(root: Path, record: Mapping[str, Any], *, label: str) -> Tuple[Path, Optional[str]]:
    try:
        path = _project_file(root, _text(record.get("path")), label=label)
    except (OSError, ValueError) as exc:
        return root, str(exc)
    expected = {
        "path": _text(record.get("path")),
        "size_bytes": int(record.get("size_bytes") or 0),
        "sha256": _text(record.get("sha256")),
    }
    if _file_record(root, path) != expected:
        return path, f"{label} bytes or path have drifted"
    return path, None


def _ratio(value: Any) -> float:
    raw = _text(value)
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        divisor = float(denominator)
        return float(numerator) / divisor if divisor else 0.0
    return float(raw or 0)


def probe_frames(path: Path) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_streams",
        "-show_frames",
        "-show_entries",
        "stream=width,height,r_frame_rate:frame=best_effort_timestamp_time,pkt_duration_time",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(f"ffprobe failed for {path}: {detail.splitlines()[-1]}")
    payload = json.loads(result.stdout or "{}")
    streams = [row for row in payload.get("streams") or [] if isinstance(row, Mapping)]
    if not streams:
        raise ValueError(f"clip has no video stream: {path}")
    stream = streams[0]
    fps = _ratio(stream.get("r_frame_rate"))
    frames: List[Dict[str, Any]] = []
    for index, row in enumerate(payload.get("frames") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            pts = float(row.get("best_effort_timestamp_time"))
        except (TypeError, ValueError):
            if fps <= 0:
                continue
            pts = index / fps
        try:
            frame_duration = float(row.get("pkt_duration_time"))
        except (TypeError, ValueError):
            frame_duration = (1.0 / fps) if fps > 0 else 0.0
        if math.isfinite(pts) and pts >= 0:
            frames.append(
                {
                    "index": index,
                    "pts_seconds": round(pts, 6),
                    "duration_seconds": round(max(0.0, frame_duration), 6),
                }
            )
    if not frames:
        raise ValueError(f"clip has no timestamped decoded frames: {path}")
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(fps, 6),
        "decoded_frames": len(frames),
        "frames": frames,
    }


def _select_terminal_frame(
    timeline: Mapping[str, Any],
    *,
    start: float,
    end: float,
) -> Dict[str, Any]:
    tolerance = max(0.0005, 0.25 / float(timeline.get("fps") or 25.0))
    candidates = [
        row
        for row in timeline.get("frames") or []
        if float(row.get("pts_seconds") or 0) + tolerance >= start
        and float(row.get("pts_seconds") or 0) < end - tolerance
    ]
    if not candidates:
        candidates = [
            row
            for row in timeline.get("frames") or []
            if start - tolerance <= float(row.get("pts_seconds") or 0) <= end + tolerance
        ]
    if not candidates:
        raise ValueError(f"approved range {start:.6f}..{end:.6f}s contains no decodable frame")
    return dict(candidates[-1])


def _extract_frame(clip: Path, *, frame_index: int, output: Path) -> None:
    if output.suffix.lower() != ".png":
        raise ValueError("handoff frame output must use a .png extension")
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}.", suffix=".png", dir=output.parent, delete=False
    )
    handle.close()
    temporary = Path(handle.name)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(clip),
        "-map",
        "0:v:0",
        "-vf",
        f"select=eq(n\\,{frame_index})",
        "-frames:v",
        "1",
        "-compression_level",
        "6",
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
        if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
            raise ValueError(f"handoff frame extraction failed: {detail.splitlines()[-1]}")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _raw_pixels(path: Path, *, frame_index: Optional[int] = None) -> Dict[str, Any]:
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0"]
    if frame_index is not None:
        command.extend(["-vf", f"select=eq(n\\,{frame_index})"])
    command.extend(["-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    result = subprocess.run(command, capture_output=True, check=False, timeout=120)
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"ffmpeg pixel decode failed").decode("utf-8", errors="replace").strip()
        raise ValueError(detail.splitlines()[-1])
    return {"bytes": len(result.stdout), "sha256": hashlib.sha256(result.stdout).hexdigest()}


def _find_boundary(report: Mapping[str, Any], boundary_id: str) -> Dict[str, Any]:
    matches = [
        dict(row)
        for row in report.get("boundaries") or []
        if isinstance(row, Mapping) and _text(row.get("boundary_id")) == boundary_id
    ]
    if len(matches) != 1:
        raise ValueError(f"sequence handoff must contain exactly one boundary {boundary_id}")
    boundary = matches[0]
    if _text(boundary.get("decision")) != "approve":
        raise ValueError(f"sequence handoff boundary {boundary_id} is not approved")
    return boundary


def _find_clip_and_review(
    report: Mapping[str, Any], *, from_shot: str
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, float]]:
    request = report.get("request") if isinstance(report.get("request"), Mapping) else {}
    clips = [
        dict(row)
        for row in request.get("clips") or []
        if isinstance(row, Mapping)
        and (_text(row.get("shot_id")) == from_shot or _text(row.get("clip_id")) == from_shot)
    ]
    if len(clips) != 1:
        raise ValueError(f"generated clip review must contain exactly one clip for {from_shot}")
    clip = clips[0]
    clip_id = _text(clip.get("clip_id"))
    reviews = [
        dict(row)
        for row in report.get("reviews") or []
        if isinstance(row, Mapping) and _text(row.get("clip_id")) == clip_id
    ]
    if len(reviews) != 1:
        raise ValueError(f"generated clip review must contain exactly one decision for {clip_id}")
    review = reviews[0]
    verdict = _text(review.get("verdict"))
    if verdict not in {"pass", "pass_with_edits"} or review.get("validation_errors"):
        raise ValueError(f"clip {clip_id} is not approved for downstream use")
    duration = float((clip.get("media") or {}).get("duration") or 0)
    if verdict == "pass_with_edits":
        ranges = sorted(
            [dict(row) for row in review.get("keep_ranges") or [] if isinstance(row, Mapping)],
            key=lambda row: (float(row.get("start") or 0), float(row.get("end") or 0)),
        )
        if not ranges:
            raise ValueError(f"clip {clip_id} pass_with_edits has no approved keep range")
        selected = ranges[-1]
        approved = {"start": float(selected.get("start") or 0), "end": float(selected.get("end") or 0)}
    else:
        approved = {"start": 0.0, "end": duration}
    if approved["end"] <= approved["start"] or approved["end"] > duration + 0.05:
        raise ValueError(f"clip {clip_id} has an invalid terminal approved range")
    return clip, review, {key: round(value, 6) for key, value in approved.items()}


def _artifact_id(plan: Mapping[str, Any]) -> str:
    excluded = {"artifact_id", "status", "blockers", "warnings"}
    return _digest("gch", {key: value for key, value in plan.items() if key not in excluded})


def _review_blockers(review: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    decision = _text(review.get("decision"))
    if decision not in DECISIONS:
        return ["confirm the handoff with decision=use_exact_start_frame or reject"]
    if not _text(review.get("reviewed_by")):
        blockers.append("reviewed_by is required")
    if not _text(review.get("notes")):
        blockers.append("review notes are required")
    if decision == "reject":
        blockers.append("tail-frame handoff was rejected; regenerate or use the planned cut without chaining")
    else:
        if review.get("same_scene_continuation_confirmed") is not True:
            blockers.append("same_scene_continuation_confirmed must be true")
        if review.get("tail_frame_accepted") is not True:
            blockers.append("tail_frame_accepted must be true")
        if review.get("original_anchors_preserved") is not True:
            blockers.append("original_anchors_preserved must be true")
    return blockers


def build_plan(
    *,
    root: Path,
    clip_review_path: Path,
    sequence_handoff_path: Path,
    boundary_id: str,
    frame_output: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    clip_report = _load_json(clip_review_path)
    if clip_report.get("version") != CLIP_REVIEW_VERSION:
        raise ValueError(f"clip review version must be {CLIP_REVIEW_VERSION}")
    clip_check = verify_clip_review(clip_report)
    if int((clip_check.get("summary") or {}).get("blocking") or 0):
        raise ValueError("generated clip review is blocked: " + "; ".join(clip_check.get("blockers") or []))

    sequence_report = verify_sequence_handoff(str(sequence_handoff_path), project_dir=str(root))
    if sequence_report.get("version") != SEQUENCE_HANDOFF_VERSION:
        raise ValueError(f"sequence handoff version must be {SEQUENCE_HANDOFF_VERSION}")
    if int((sequence_report.get("summary") or {}).get("blocking") or 0):
        raise ValueError("sequence handoff is blocked: " + "; ".join(sequence_report.get("blockers") or []))

    boundary = _find_boundary(sequence_report, boundary_id)
    from_shot = _text(boundary.get("from_shot"))
    to_shot = _text(boundary.get("to_shot"))
    clip, review, approved_range = _find_clip_and_review(clip_report, from_shot=from_shot)
    clip_path = _project_file(root, _text(clip.get("path")), label=f"clip {from_shot}")
    if _file_record(root, clip_path) != {
        "path": _text(clip.get("path")),
        "size_bytes": int(clip.get("size_bytes") or 0),
        "sha256": _text(clip.get("sha256")),
    }:
        raise ValueError(f"clip {from_shot} no longer matches its reviewed bytes")

    timeline = probe_frames(clip_path)
    selected = _select_terminal_frame(
        timeline,
        start=approved_range["start"],
        end=approved_range["end"],
    )
    _extract_frame(clip_path, frame_index=int(selected["index"]), output=frame_output)
    source_pixels = _raw_pixels(clip_path, frame_index=int(selected["index"]))
    output_pixels = _raw_pixels(frame_output)
    if source_pixels != output_pixels:
        raise ValueError("extracted handoff frame pixels do not match the selected source frame")

    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": {
            "clip_review": {
                **_file_record(root, clip_review_path),
                "report_id": _text(clip_report.get("report_id")),
            },
            "sequence_handoff": {
                **_file_record(root, sequence_handoff_path),
                "report_id": _text(sequence_report.get("report_id")),
            },
        },
        "boundary": {
            key: boundary.get(key)
            for key in (
                "boundary_id",
                "from_shot",
                "to_shot",
                "carrier_type",
                "offer_from",
                "receive_in",
                "edit_type",
                "match_requirement",
                "axis_decision",
                "screen_direction_decision",
            )
        },
        "source_clip": {
            **_file_record(root, clip_path),
            "clip_id": _text(clip.get("clip_id")),
            "shot_id": _text(clip.get("shot_id")) or from_shot,
            "review_verdict": _text(review.get("verdict")),
            "approved_terminal_range": approved_range,
            "media": dict(clip.get("media") or {}),
        },
        "selected_frame": {
            **selected,
            "decoded_frames": int(timeline.get("decoded_frames") or 0),
            "fps": float(timeline.get("fps") or 0),
            "width": int(timeline.get("width") or 0),
            "height": int(timeline.get("height") or 0),
            "pixel_sha256": source_pixels["sha256"],
            "pixel_bytes": source_pixels["bytes"],
        },
        "handoff_frame": {
            **_file_record(root, frame_output),
            "role": "exact first frame for the next generated shot",
            "pixel_sha256": output_pixels["sha256"],
            "pixel_bytes": output_pixels["bytes"],
            "width": int(timeline.get("width") or 0),
            "height": int(timeline.get("height") or 0),
        },
        "prompt_contract": {
            "target_shot": to_shot,
            "mode_override": "image_to_video",
            "reference_role": "Continue the exact pose, object state, environment, lighting, and composition from this first frame.",
            "identity_policy": "Preserve the original character, product, and style anchors; the tail frame controls continuity state and does not replace identity references.",
            "motion_budget": "Use one primary subject action and one camera behavior with physically plausible timing.",
            "submission_order": f"Generate and review {from_shot} before submitting {to_shot}; this boundary cannot run in parallel.",
        },
        "review": {
            "decision": "pending",
            "reviewed_by": "",
            "same_scene_continuation_confirmed": False,
            "tail_frame_accepted": False,
            "original_anchors_preserved": False,
            "notes": "",
        },
        "limitations": [
            "The extracted frame proves pixels and source time, not identity, rights, consent, or provider acceptance.",
            "Use exact chaining only for the same scene or an intentionally continuous beat; use the planned cut for scene changes.",
            "Keep original character/product references when the provider surface permits them, and re-review every generated result.",
            "This artifact never uploads media or submits a paid generation job.",
        ],
    }
    plan["blockers"] = _review_blockers(plan["review"])
    plan["warnings"] = []
    plan["status"] = "blocked" if plan["blockers"] else "ready"
    plan["artifact_id"] = _artifact_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"plan version must be {VERSION}")
    try:
        root = _root(_text(plan.get("project_root")))
    except (OSError, ValueError) as exc:
        return {
            "status": "blocked",
            "blockers": [str(exc)],
            "warnings": [],
            "summary": {"blocking": 1, "warnings": 0},
        }

    inputs = plan.get("inputs") if isinstance(plan.get("inputs"), Mapping) else {}
    clip_record = inputs.get("clip_review") if isinstance(inputs.get("clip_review"), Mapping) else {}
    sequence_record = inputs.get("sequence_handoff") if isinstance(inputs.get("sequence_handoff"), Mapping) else {}
    clip_review_path, error = _record_matches(root, clip_record, label="generated clip review")
    if error:
        blockers.append(error)
    sequence_path, error = _record_matches(root, sequence_record, label="sequence handoff")
    if error:
        blockers.append(error)

    clip_report: Dict[str, Any] = {}
    sequence_report: Dict[str, Any] = {}
    boundary = plan.get("boundary") if isinstance(plan.get("boundary"), Mapping) else {}
    source = plan.get("source_clip") if isinstance(plan.get("source_clip"), Mapping) else {}
    selected = plan.get("selected_frame") if isinstance(plan.get("selected_frame"), Mapping) else {}
    frame = plan.get("handoff_frame") if isinstance(plan.get("handoff_frame"), Mapping) else {}

    if not blockers:
        try:
            clip_report = _load_json(clip_review_path)
            clip_check = verify_clip_review(clip_report)
            blockers.extend(clip_check.get("blockers") or [])
            warnings.extend(clip_check.get("warnings") or [])
            if _text(clip_report.get("report_id")) != _text(clip_record.get("report_id")):
                blockers.append("generated clip review report_id has drifted")
            sequence_report = verify_sequence_handoff(str(sequence_path), project_dir=str(root))
            blockers.extend(sequence_report.get("blockers") or [])
            warnings.extend(sequence_report.get("warnings") or [])
            if _text(sequence_report.get("report_id")) != _text(sequence_record.get("report_id")):
                blockers.append("sequence handoff report_id has drifted")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            blockers.append(str(exc))

    if clip_report and sequence_report:
        try:
            live_boundary = _find_boundary(sequence_report, _text(boundary.get("boundary_id")))
            stored_boundary = {
                key: boundary.get(key)
                for key in (
                    "boundary_id",
                    "from_shot",
                    "to_shot",
                    "carrier_type",
                    "offer_from",
                    "receive_in",
                    "edit_type",
                    "match_requirement",
                    "axis_decision",
                    "screen_direction_decision",
                )
            }
            expected_boundary = {key: live_boundary.get(key) for key in stored_boundary}
            if stored_boundary != expected_boundary:
                blockers.append("stored sequence boundary no longer matches the reviewed handoff")
            clip, review, approved = _find_clip_and_review(
                clip_report, from_shot=_text(live_boundary.get("from_shot"))
            )
            clip_path = _project_file(root, _text(source.get("path")), label="source clip")
            expected_source = {
                **_file_record(root, clip_path),
                "clip_id": _text(clip.get("clip_id")),
                "shot_id": _text(clip.get("shot_id")) or _text(live_boundary.get("from_shot")),
                "review_verdict": _text(review.get("verdict")),
                "approved_terminal_range": approved,
                "media": dict(clip.get("media") or {}),
            }
            if dict(source) != expected_source:
                blockers.append("stored source clip or approved terminal range has drifted")

            timeline = probe_frames(clip_path)
            terminal = _select_terminal_frame(timeline, start=approved["start"], end=approved["end"])
            expected_selection = {
                **terminal,
                "decoded_frames": int(timeline.get("decoded_frames") or 0),
                "fps": float(timeline.get("fps") or 0),
                "width": int(timeline.get("width") or 0),
                "height": int(timeline.get("height") or 0),
                "pixel_sha256": _text(selected.get("pixel_sha256")),
                "pixel_bytes": int(selected.get("pixel_bytes") or 0),
            }
            if any(selected.get(key) != value for key, value in expected_selection.items() if key not in {"pixel_sha256", "pixel_bytes"}):
                blockers.append("selected frame is no longer the approved terminal decoded frame")
            source_pixels = _raw_pixels(clip_path, frame_index=int(terminal["index"]))
            if source_pixels["sha256"] != _text(selected.get("pixel_sha256")) or source_pixels["bytes"] != int(selected.get("pixel_bytes") or 0):
                blockers.append("selected source frame pixels have drifted")

            frame_path, frame_error = _record_matches(root, frame, label="handoff frame")
            if frame_error:
                blockers.append(frame_error)
            else:
                output_pixels = _raw_pixels(frame_path)
                if output_pixels != source_pixels:
                    blockers.append("handoff frame pixels no longer match the selected source frame")
                if output_pixels["sha256"] != _text(frame.get("pixel_sha256")) or output_pixels["bytes"] != int(frame.get("pixel_bytes") or 0):
                    blockers.append("stored handoff frame pixel digest has drifted")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            blockers.append(str(exc))

    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    expected_review_blockers = _review_blockers(review)
    stored_blockers = sorted(set(_text(item) for item in plan.get("blockers") or [] if _text(item)))
    if stored_blockers != sorted(set(expected_review_blockers)):
        blockers.append("stored blockers do not match the canonical review decision")
    expected_stored_status = "blocked" if expected_review_blockers else "ready"
    if _text(plan.get("status")) != expected_stored_status:
        blockers.append("stored status does not match the canonical review decision")
    if plan.get("warnings") not in ([], None):
        blockers.append("stored warnings must be empty for generation_chain_handoff.v1")
    blockers.extend(expected_review_blockers)
    expected_id = _artifact_id(plan)
    if _text(plan.get("artifact_id")) != expected_id:
        blockers.append("artifact_id does not match canonical handoff content")
    unique_blockers = sorted(set(_text(item) for item in blockers if _text(item)))
    unique_warnings = sorted(set(_text(item) for item in warnings if _text(item)))
    return {
        "status": "blocked" if unique_blockers else ("warn" if unique_warnings else "ready"),
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "summary": {"blocking": len(unique_blockers), "warnings": len(unique_warnings)},
    }


def verify_report(report_path: str, *, project_dir: str = ".") -> Dict[str, Any]:
    root = _root(project_dir)
    path = _project_file(root, report_path, label="generation chain handoff")
    plan = _load_json(path)
    if _text(plan.get("project_root")) != str(root):
        result = verify_plan(plan)
        result["blockers"] = sorted(set(result.get("blockers") or []) | {"project_root does not match the live project"})
        result["status"] = "blocked"
        result["summary"] = {"blocking": len(result["blockers"]), "warnings": len(result.get("warnings") or [])}
        return result
    return verify_plan(plan)


def confirm_plan(
    plan: Mapping[str, Any],
    *,
    decision: str,
    reviewed_by: str,
    same_scene_confirmed: bool,
    tail_frame_accepted: bool,
    original_anchors_preserved: bool,
    notes: str,
) -> Dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    updated = json.loads(json.dumps(plan))
    updated["review"] = {
        "decision": decision,
        "reviewed_by": _text(reviewed_by),
        "same_scene_continuation_confirmed": bool(same_scene_confirmed),
        "tail_frame_accepted": bool(tail_frame_accepted),
        "original_anchors_preserved": bool(original_anchors_preserved),
        "notes": _text(notes),
        "confirmed_at": utc_now(),
    }
    updated["blockers"] = _review_blockers(updated["review"])
    updated["warnings"] = []
    updated["status"] = "blocked" if updated["blockers"] else "ready"
    updated["artifact_id"] = _artifact_id(updated)
    return updated


def emit_markdown(plan: Mapping[str, Any], verification: Optional[Mapping[str, Any]] = None) -> str:
    result = verification or verify_plan(plan)
    boundary = plan.get("boundary") or {}
    selected = plan.get("selected_frame") or {}
    frame = plan.get("handoff_frame") or {}
    review = plan.get("review") or {}
    lines = [
        "# Generation Chain Handoff",
        "",
        f"- Status: **{str(result.get('status') or '').upper()}**",
        f"- Artifact ID: `{plan.get('artifact_id', '')}`",
        f"- Boundary: `{boundary.get('boundary_id', '')}` · `{boundary.get('from_shot', '')}` → `{boundary.get('to_shot', '')}`",
        f"- Approved source frame: #{selected.get('index', '')} at {float(selected.get('pts_seconds') or 0):.6f}s",
        f"- Handoff PNG: `{frame.get('path', '')}`",
        f"- Review decision: `{review.get('decision', '')}`",
        "",
        "## Review before confirmation",
        "",
        "- View the exact PNG and the already-reviewed predecessor clip at normal speed.",
        "- Confirm this is the same scene or an intentionally continuous beat.",
        "- Confirm the visible pose, props, environment, light, and composition are suitable as the next clip's exact start.",
        "- Keep original character/product/style anchors; the tail frame carries state and space, not identity authority.",
        "- Give the next short clip one primary action and one camera behavior.",
        "",
        "## Prompt contract",
        "",
    ]
    for value in (plan.get("prompt_contract") or {}).values():
        lines.append(f"- {_text(value)}")
    if result.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in result.get("blockers") or [])
    if result.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in result.get("warnings") or [])
    lines.extend(["", "Re-run `generation_chain_handoff.py verify --strict` immediately before rebuilding the provider prompt pack.", ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Extract an approved terminal frame and create a blocked review plan.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--clip-review", required=True)
    prepare.add_argument("--sequence-handoff", required=True)
    prepare.add_argument("--boundary", required=True, help="Approved boundary id, for example boundary_001.")
    prepare.add_argument("--frame-output", required=True, help="Project-local .png output path.")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--markdown")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true")

    confirm = subparsers.add_parser("confirm", help="Record whether the extracted tail is safe to use as the next exact first frame.")
    confirm.add_argument("--project-dir", default=".")
    confirm.add_argument("--plan", required=True)
    confirm.add_argument("--output", required=True)
    confirm.add_argument("--markdown")
    confirm.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--same-scene-confirmed", action="store_true")
    confirm.add_argument("--tail-frame-accepted", action="store_true")
    confirm.add_argument("--original-anchors-preserved", action="store_true")
    confirm.add_argument("--notes", required=True)
    confirm.add_argument("--force", action="store_true")
    confirm.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Live-verify upstream reports, source bytes, frame selection, pixels, and review.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        root = _root(args.project_dir)
        if args.command == "prepare":
            clip_review = _project_file(root, args.clip_review, label="generated clip review")
            sequence_handoff = _project_file(root, args.sequence_handoff, label="sequence handoff")
            output = _output_file(
                root,
                args.output,
                label="plan output",
                protected=[clip_review, sequence_handoff],
                force=args.force,
            )
            frame_output = _output_file(
                root,
                args.frame_output,
                label="handoff frame output",
                protected=[clip_review, sequence_handoff, output],
                force=args.force,
            )
            plan = build_plan(
                root=root,
                clip_review_path=clip_review,
                sequence_handoff_path=sequence_handoff,
                boundary_id=args.boundary,
                frame_output=frame_output,
            )
            _write_json(output, plan)
            verification = verify_plan(plan)
        elif args.command == "confirm":
            source = _project_file(root, args.plan, label="generation chain handoff plan")
            output = _output_file(
                root,
                args.output,
                label="confirmed plan output",
                protected=[] if Path(args.output).expanduser().resolve() == source else [source],
                force=args.force,
            )
            plan = confirm_plan(
                _load_json(source),
                decision=args.decision,
                reviewed_by=args.reviewed_by,
                same_scene_confirmed=args.same_scene_confirmed,
                tail_frame_accepted=args.tail_frame_accepted,
                original_anchors_preserved=args.original_anchors_preserved,
                notes=args.notes,
            )
            _write_json(output, plan)
            verification = verify_plan(plan)
        else:
            source = _project_file(root, args.plan, label="generation chain handoff plan")
            plan = _load_json(source)
            verification = verify_plan(plan)

        if args.markdown:
            markdown = _output_file(root, args.markdown, label="markdown output", protected=[], force=True)
            markdown.write_text(emit_markdown(plan, verification), encoding="utf-8")
        print(
            f"Generation chain handoff {verification['status']}: "
            f"blocking={verification['summary']['blocking']} warnings={verification['summary']['warnings']}"
        )
        if args.strict and verification["summary"]["blocking"]:
            return 2
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"generation chain handoff error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
