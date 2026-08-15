#!/usr/bin/env python3
"""Prepare and verify source-bound continuity reviews across generated clips.

This script does not infer visual continuity. It extracts the accepted outgoing
and incoming frames for every adjacent generated clip, creates bounded visual
evidence, and validates a reviewer response against the exact reviewed bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import generated_clip_review


REQUEST_VERSION = "generated_sequence_review_request.v1"
RESPONSE_VERSION = "generated_sequence_review_response.v1"
REPORT_VERSION = "generated_sequence_review.v1"

CHECK_KEYS = (
    "identity_wardrobe",
    "prop_state",
    "spatial_orientation",
    "action_end_state",
    "camera_framing",
    "lighting_palette",
)
CHECK_STATUSES = {"match", "intentional_change", "mismatch", "not_applicable"}
VERDICTS = {"pass", "fail"}
FAILURE_CODES = {
    "identity_drift",
    "wardrobe_drift",
    "prop_state_drift",
    "spatial_discontinuity",
    "screen_direction_flip",
    "action_state_mismatch",
    "camera_geometry_jump",
    "lighting_palette_drift",
    "boundary_artifact",
}
FLOAT_TOLERANCE = 0.001


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


def _request_id(request: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            key: value
            for key, value in request.items()
            if key not in {"generated_at", "request_id", "response_template"}
        }
    )


def _report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "version": report.get("version"),
            "request": report.get("request"),
            "response": report.get("response"),
            "reviews": report.get("reviews"),
            "status": report.get("status"),
            "summary": report.get("summary"),
            "blockers": report.get("blockers"),
            "warnings": report.get("warnings"),
        }
    )


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Mapping[str, Any], *, force: bool) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", force=force)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_project_path(raw_path: str, *, root: Path, label: str) -> Path:
    lexical = Path(raw_path).expanduser()
    if not lexical.is_absolute():
        lexical = root / lexical
    lexical = Path(os.path.abspath(str(lexical)))
    if not _within(lexical, root):
        raise ValueError(f"{label} must stay inside the project directory: {lexical}")
    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {current}")
    return lexical


def _project_file(raw_path: str, *, root: Path, label: str) -> Path:
    lexical = _lexical_project_path(raw_path, root=root, label=label)
    path = lexical.resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


def _project_output(raw_path: str, *, root: Path, label: str) -> Path:
    return _lexical_project_path(raw_path, root=root, label=label).resolve()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _clean_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError(f"invalid clip id: {value!r}")
    return cleaned


def _even(value: float) -> int:
    number = max(2, int(round(value)))
    return number if number % 2 == 0 else number + 1


def _canvas(media: Mapping[str, Any]) -> Tuple[int, int]:
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("clip media requires positive width and height")
    if width >= height:
        canvas_width = min(width, 640)
        canvas_height = canvas_width * height / width
    else:
        canvas_height = min(height, 720)
        canvas_width = canvas_height * width / height
    return _even(canvas_width), _even(canvas_height)


def _run_ffmpeg(command: Sequence[str], *, outputs: Sequence[Path], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or any(not path.exists() or path.stat().st_size == 0 for path in outputs):
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise ValueError(f"{label} failed: {detail.splitlines()[-1]}")


def generate_boundary_evidence(
    from_clip: Path,
    to_clip: Path,
    output_dir: Path,
    *,
    boundary_id: str,
    outgoing_time: float,
    incoming_time: float,
    tail_start: float,
    tail_end: float,
    head_start: float,
    head_end: float,
    from_media: Mapping[str, Any],
    to_media: Mapping[str, Any],
    force: bool,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _clean_id(boundary_id)
    outgoing = output_dir / f"{stem}_outgoing.jpg"
    incoming = output_dir / f"{stem}_incoming.jpg"
    comparison = output_dir / f"{stem}_comparison.jpg"
    preview = output_dir / f"{stem}_preview.mp4"
    outputs = [outgoing, incoming, comparison, preview]
    if not force:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            raise ValueError(f"refusing to overwrite boundary evidence without --force: {', '.join(existing)}")

    width, height = _canvas(from_media)
    fps = min(
        30.0,
        max(1.0, min(float(from_media.get("fps") or 24), float(to_media.get("fps") or 24))),
    )
    normalize = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )
    overwrite = "-y"
    for source, timestamp, destination, label in (
        (from_clip, outgoing_time, outgoing, "outgoing frame extraction"),
        (to_clip, incoming_time, incoming, "incoming frame extraction"),
    ):
        _run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                overwrite,
                "-i",
                str(source),
                "-ss",
                f"{timestamp:.6f}",
                "-frames:v",
                "1",
                "-vf",
                normalize,
                "-q:v",
                "2",
                str(destination),
            ],
            outputs=[destination],
            label=label,
        )

    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            overwrite,
            "-i",
            str(outgoing),
            "-i",
            str(incoming),
            "-filter_complex",
            "[0:v][1:v]hstack=inputs=2[v]",
            "-map",
            "[v]",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(comparison),
        ],
        outputs=[comparison],
        label="boundary comparison generation",
    )

    filtergraph = (
        f"[0:v]trim=start={tail_start:.6f}:end={tail_end:.6f},setpts=PTS-STARTPTS,"
        f"fps={fps:.6f},{normalize}[left];"
        f"[1:v]trim=start={head_start:.6f}:end={head_end:.6f},setpts=PTS-STARTPTS,"
        f"fps={fps:.6f},{normalize}[right];"
        "[left][right]concat=n=2:v=1:a=0[outv]"
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            overwrite,
            "-i",
            str(from_clip),
            "-i",
            str(to_clip),
            "-filter_complex",
            filtergraph,
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(preview),
        ],
        outputs=[preview],
        label="boundary preview generation",
    )
    return {
        "canvas": {"width": width, "height": height, "fps": round(fps, 6)},
        "outgoing_frame": outgoing,
        "incoming_frame": incoming,
        "comparison": comparison,
        "preview": preview,
    }


def _approved_ranges(review: Mapping[str, Any], duration: float) -> List[Dict[str, float]]:
    raw_ranges = review.get("keep_ranges") or []
    if not raw_ranges:
        return [{"start": 0.0, "end": round(duration, 6)}]
    ranges = [
        {"start": round(float(item.get("start") or 0), 6), "end": round(float(item.get("end") or 0), 6)}
        for item in raw_ranges
        if isinstance(item, Mapping)
    ]
    ranges.sort(key=lambda item: (item["start"], item["end"]))
    return ranges


def _boundary_times(
    from_clip: Mapping[str, Any],
    to_clip: Mapping[str, Any],
    preview_seconds: float,
) -> Dict[str, float]:
    from_ranges = from_clip.get("approved_ranges") or []
    to_ranges = to_clip.get("approved_ranges") or []
    if not from_ranges or not to_ranges:
        raise ValueError("approved clip ranges are required for boundary evidence")
    last = from_ranges[-1]
    first = to_ranges[0]
    from_fps = max(1.0, float((from_clip.get("media") or {}).get("fps") or 24))
    # Container duration can extend slightly past the final decodable video
    # packet because of audio padding. Step back two frames so extraction is
    # still representative of the accepted endpoint without seeking past it.
    outgoing_time = max(float(last["start"]), float(last["end"]) - 2.0 / from_fps)
    incoming_time = float(first["start"])
    tail_start = max(float(last["start"]), float(last["end"]) - preview_seconds)
    head_end = min(float(first["end"]), float(first["start"]) + preview_seconds)
    if float(last["end"]) <= tail_start or head_end <= float(first["start"]):
        raise ValueError("approved ranges are too short for boundary preview")
    return {
        "outgoing_frame": round(outgoing_time, 6),
        "incoming_frame": round(incoming_time, 6),
        "tail_start": round(tail_start, 6),
        "tail_end": round(float(last["end"]), 6),
        "head_start": round(float(first["start"]), 6),
        "head_end": round(head_end, 6),
    }


def _storyboard_context(
    from_shot: str,
    to_shot: str,
    shots: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    source = shots.get(from_shot) or {}
    target = shots.get(to_shot) or {}
    target_continuity = target.get("continuity") if isinstance(target.get("continuity"), Mapping) else {}
    source_visual = source.get("visual") if isinstance(source.get("visual"), Mapping) else {}
    target_visual = target.get("visual") if isinstance(target.get("visual"), Mapping) else {}
    reuse = str(target_continuity.get("reuse_reference_from") or "")
    return {
        "mode": "linked" if reuse == from_shot else "cut_or_independent",
        "reuse_reference_from": reuse,
        "continuity_anchors": [str(item) for item in (target_continuity.get("anchors") or [])],
        "expected_outgoing_state": str(source_visual.get("last_frame") or ""),
        "expected_incoming_state": str(target_visual.get("first_frame") or ""),
    }


def _response_template(request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "request_id": request.get("request_id"),
        "reviewed_by": "",
        "reviews": [
            {
                "boundary_id": boundary.get("boundary_id"),
                "verdict": "",
                "checks": {key: "" for key in CHECK_KEYS},
                "failure_codes": [],
                "observed_transition": "",
                "repair_action": "",
                "notes": "",
            }
            for boundary in request.get("boundaries") or []
        ],
    }


def prepare_request(
    clip_review_path: str,
    *,
    project_dir: str,
    evidence_dir: str,
    storyboard_plan_path: Optional[str] = None,
    preview_seconds: float = 1.0,
    force: bool = False,
) -> Dict[str, Any]:
    if preview_seconds < 0.25 or preview_seconds > 3.0:
        raise ValueError("preview_seconds must be between 0.25 and 3.0")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    clip_review_file = _project_file(clip_review_path, root=root, label="generated clip review")
    clip_review = _load_json(str(clip_review_file))
    live_clip_review = generated_clip_review.verify_report(clip_review)
    if int((live_clip_review.get("summary") or {}).get("blocking") or 0):
        raise ValueError("generated clip review must pass live verification before sequence review")
    review_root = Path(str(((clip_review.get("request") or {}).get("project_dir") or ""))).expanduser().resolve()
    if review_root != root:
        raise ValueError("generated clip review project_dir does not match --project-dir")

    request_clips = {
        str(item.get("clip_id") or ""): item
        for item in ((clip_review.get("request") or {}).get("clips") or [])
        if isinstance(item, Mapping)
    }
    reviewed = {
        str(item.get("clip_id") or ""): item
        for item in (clip_review.get("reviews") or [])
        if isinstance(item, Mapping)
    }
    clips: Dict[str, Dict[str, Any]] = {}
    report_order: List[str] = []
    shot_to_clip: Dict[str, str] = {}
    for clip_id, source in request_clips.items():
        decision = reviewed.get(clip_id)
        if not isinstance(decision, Mapping):
            raise ValueError(f"generated clip review is missing decision for {clip_id}")
        clean_id = _clean_id(clip_id)
        shot_id = str(source.get("shot_id") or clean_id)
        if shot_id in shot_to_clip:
            raise ValueError(f"duplicate generated clip shot_id: {shot_id}")
        clip_file = _project_file(str(source.get("path") or ""), root=root, label=f"clip {clean_id}")
        media = dict(source.get("media") or {})
        duration = float(media.get("duration") or 0)
        if duration <= 0:
            raise ValueError(f"clip {clean_id} has invalid duration")
        clips[clean_id] = {
            "clip_id": clean_id,
            "shot_id": shot_id,
            "path": _relative(clip_file, root),
            "sha256": _sha256(clip_file),
            "size_bytes": clip_file.stat().st_size,
            "media": media,
            "clip_review_verdict": str(decision.get("verdict") or ""),
            "approved_ranges": _approved_ranges(decision, duration),
        }
        report_order.append(clean_id)
        shot_to_clip[shot_id] = clean_id
    if len(clips) < 2:
        raise ValueError("sequence continuity review requires at least two generated clips")

    storyboard_source: Optional[Dict[str, Any]] = None
    storyboard_shots: Dict[str, Mapping[str, Any]] = {}
    clip_order = list(report_order)
    if storyboard_plan_path:
        storyboard_file = _project_file(storyboard_plan_path, root=root, label="storyboard plan")
        storyboard = _load_json(str(storyboard_file))
        ordered_shots = []
        for item in storyboard.get("shots") or []:
            if not isinstance(item, Mapping):
                continue
            shot_id = str(item.get("id") or "")
            if shot_id:
                if shot_id in storyboard_shots:
                    raise ValueError(f"storyboard plan contains duplicate shot id: {shot_id}")
                storyboard_shots[shot_id] = item
                if shot_id in shot_to_clip:
                    ordered_shots.append(shot_id)
        if set(ordered_shots) != set(shot_to_clip):
            missing = sorted(set(shot_to_clip).difference(ordered_shots))
            raise ValueError(f"storyboard plan is missing reviewed generated shots: {', '.join(missing)}")
        clip_order = [shot_to_clip[shot_id] for shot_id in ordered_shots]
        storyboard_source = {
            "path": _relative(storyboard_file, root),
            "sha256": _sha256(storyboard_file),
            "size_bytes": storyboard_file.stat().st_size,
        }

    evidence_root = _project_output(evidence_dir, root=root, label="sequence evidence directory")
    boundaries: List[Dict[str, Any]] = []
    for from_id, to_id in zip(clip_order, clip_order[1:]):
        from_item = clips[from_id]
        to_item = clips[to_id]
        boundary_id = f"{from_id}__{to_id}"
        times = _boundary_times(from_item, to_item, preview_seconds)
        evidence = generate_boundary_evidence(
            _project_file(from_item["path"], root=root, label=f"clip {from_id}"),
            _project_file(to_item["path"], root=root, label=f"clip {to_id}"),
            evidence_root,
            boundary_id=boundary_id,
            outgoing_time=times["outgoing_frame"],
            incoming_time=times["incoming_frame"],
            tail_start=times["tail_start"],
            tail_end=times["tail_end"],
            head_start=times["head_start"],
            head_end=times["head_end"],
            from_media=from_item["media"],
            to_media=to_item["media"],
            force=force,
        )
        evidence_files = {}
        for key in ("outgoing_frame", "incoming_frame", "comparison", "preview"):
            path = Path(evidence[key]).resolve()
            evidence_files[key] = {
                "path": _relative(path, root),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        boundaries.append(
            {
                "boundary_id": boundary_id,
                "from_clip_id": from_id,
                "to_clip_id": to_id,
                "source_times": times,
                "preview_seconds_per_side": round(preview_seconds, 6),
                "canvas": evidence["canvas"],
                "storyboard_context": _storyboard_context(
                    str(from_item.get("shot_id") or from_id),
                    str(to_item.get("shot_id") or to_id),
                    storyboard_shots,
                ),
                "evidence": evidence_files,
            }
        )

    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "sources": {
            "generated_clip_review": {
                "path": _relative(clip_review_file, root),
                "sha256": _sha256(clip_review_file),
                "size_bytes": clip_review_file.stat().st_size,
                "report_id": str(clip_review.get("report_id") or ""),
            },
            "storyboard_plan": storyboard_source,
        },
        "clip_order": clip_order,
        "clips": [clips[clip_id] for clip_id in clip_order],
        "boundaries": boundaries,
        "review_protocol": {
            "passes": [
                "Watch each silent boundary preview at 1x before inspecting still frames.",
                "Inspect the outgoing and incoming frames side by side at full size.",
                "Compare identity/wardrobe, prop state, spatial direction, action state, camera geometry, and lighting/palette.",
                "Mark planned changes as intentional_change; never hide an unexplained mismatch inside notes.",
            ],
            "check_keys": list(CHECK_KEYS),
            "check_statuses": sorted(CHECK_STATUSES),
            "failure_codes": sorted(FAILURE_CODES),
            "limitations": [
                "Boundary previews are muted and do not replace full audiovisual clip review.",
                "The script validates evidence and decisions; it does not infer visual continuity.",
                "Reviewer labels are not identity authentication or digital signatures.",
            ],
        },
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = _response_template(request)
    return request


def _same_float(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= FLOAT_TOLERANCE
    except (TypeError, ValueError):
        return False


def verify_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    if request.get("version") != REQUEST_VERSION:
        blockers.append(f"request version must be {REQUEST_VERSION}")
    if not root.is_dir():
        blockers.append(f"project directory is missing: {root}")
    if str(request.get("request_id") or "") != _request_id(request):
        blockers.append("request_id does not match canonical request content")
    if request.get("response_template") != _response_template(request):
        blockers.append("response_template does not match the prepared boundaries")

    sources = request.get("sources") if isinstance(request.get("sources"), Mapping) else {}
    clip_source = sources.get("generated_clip_review") if isinstance(sources, Mapping) else {}
    live_clip_review: Optional[Mapping[str, Any]] = None
    if not isinstance(clip_source, Mapping):
        blockers.append("sources.generated_clip_review must be an object")
    else:
        try:
            clip_review_file = _project_file(
                str(clip_source.get("path") or ""), root=root, label="generated clip review"
            )
            if _sha256(clip_review_file) != str(clip_source.get("sha256") or ""):
                blockers.append("generated clip review bytes changed after sequence preparation")
            if clip_review_file.stat().st_size != int(clip_source.get("size_bytes") or -1):
                blockers.append("generated clip review size changed after sequence preparation")
            live_clip_review = _load_json(str(clip_review_file))
            if str(live_clip_review.get("report_id") or "") != str(clip_source.get("report_id") or ""):
                blockers.append("generated clip review report_id changed")
            verification = generated_clip_review.verify_report(live_clip_review)
            if int((verification.get("summary") or {}).get("blocking") or 0):
                blockers.append("generated clip review no longer passes live verification")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(str(exc))

    storyboard_source = sources.get("storyboard_plan") if isinstance(sources, Mapping) else None
    live_storyboard_shots: Dict[str, Mapping[str, Any]] = {}
    if storyboard_source is not None:
        if not isinstance(storyboard_source, Mapping):
            blockers.append("sources.storyboard_plan must be an object or null")
        else:
            try:
                storyboard = _project_file(
                    str(storyboard_source.get("path") or ""), root=root, label="storyboard plan"
                )
                if _sha256(storyboard) != str(storyboard_source.get("sha256") or ""):
                    blockers.append("storyboard plan bytes changed after sequence preparation")
                if storyboard.stat().st_size != int(storyboard_source.get("size_bytes") or -1):
                    blockers.append("storyboard plan size changed after sequence preparation")
                storyboard_data = _load_json(str(storyboard))
                for item in storyboard_data.get("shots") or []:
                    if not isinstance(item, Mapping):
                        continue
                    shot_id = str(item.get("id") or "")
                    if not shot_id:
                        continue
                    if shot_id in live_storyboard_shots:
                        blockers.append(f"storyboard plan contains duplicate shot id: {shot_id}")
                    live_storyboard_shots[shot_id] = item
            except ValueError as exc:
                blockers.append(str(exc))

    clip_order = request.get("clip_order") or []
    raw_clips = request.get("clips") or []
    if not isinstance(clip_order, list) or len(clip_order) < 2:
        blockers.append("clip_order must contain at least two clip ids")
        clip_order = []
    if not isinstance(raw_clips, list):
        blockers.append("clips must be a list")
        raw_clips = []
    clips: Dict[str, Mapping[str, Any]] = {}
    for item in raw_clips:
        if not isinstance(item, Mapping):
            blockers.append("clips must contain objects")
            continue
        clip_id = str(item.get("clip_id") or "")
        if not clip_id or clip_id in clips:
            blockers.append(f"duplicate or empty clip id: {clip_id!r}")
            continue
        clips[clip_id] = item
        try:
            clip = _project_file(str(item.get("path") or ""), root=root, label=f"clip {clip_id}")
            if _sha256(clip) != str(item.get("sha256") or ""):
                blockers.append(f"{clip_id}: clip bytes changed after sequence preparation")
            if clip.stat().st_size != int(item.get("size_bytes") or -1):
                blockers.append(f"{clip_id}: clip size changed after sequence preparation")
        except ValueError as exc:
            blockers.append(str(exc))
    if list(clips) != list(clip_order):
        blockers.append("clips must appear exactly once in clip_order")
    if isinstance(live_clip_review, Mapping):
        live_sources = {
            str(item.get("clip_id") or ""): item
            for item in ((live_clip_review.get("request") or {}).get("clips") or [])
            if isinstance(item, Mapping)
        }
        live_reviews = {
            str(item.get("clip_id") or ""): item
            for item in (live_clip_review.get("reviews") or [])
            if isinstance(item, Mapping)
        }
        if set(clips) != set(live_sources):
            blockers.append("request clips do not match the live generated clip review")
        for clip_id, item in clips.items():
            source = live_sources.get(clip_id)
            decision = live_reviews.get(clip_id)
            if not isinstance(source, Mapping) or not isinstance(decision, Mapping):
                continue
            duration = float((source.get("media") or {}).get("duration") or 0)
            expected = {
                "clip_id": clip_id,
                "shot_id": str(source.get("shot_id") or clip_id),
                "path": str(source.get("path") or ""),
                "sha256": str(source.get("sha256") or ""),
                "size_bytes": int(source.get("size_bytes") or 0),
                "media": dict(source.get("media") or {}),
                "clip_review_verdict": str(decision.get("verdict") or ""),
                "approved_ranges": _approved_ranges(decision, duration),
            }
            if dict(item) != expected:
                blockers.append(f"{clip_id}: stored clip contract does not match live generated clip review")

    raw_boundaries = request.get("boundaries") or []
    if not isinstance(raw_boundaries, list):
        blockers.append("boundaries must be a list")
        raw_boundaries = []
    expected_pairs = list(zip(clip_order, clip_order[1:]))
    if len(raw_boundaries) != len(expected_pairs):
        blockers.append("boundaries must cover every adjacent clip pair exactly once")
    for index, pair in enumerate(expected_pairs):
        if index >= len(raw_boundaries) or not isinstance(raw_boundaries[index], Mapping):
            continue
        boundary = raw_boundaries[index]
        from_id, to_id = pair
        expected_id = f"{from_id}__{to_id}"
        if (
            str(boundary.get("boundary_id") or "") != expected_id
            or str(boundary.get("from_clip_id") or "") != from_id
            or str(boundary.get("to_clip_id") or "") != to_id
        ):
            blockers.append(f"boundary {index} does not match adjacent pair {expected_id}")
        if from_id in clips and to_id in clips:
            try:
                expected_times = _boundary_times(
                    clips[from_id], clips[to_id], float(boundary.get("preview_seconds_per_side") or 0)
                )
            except ValueError as exc:
                blockers.append(f"{expected_id}: {exc}")
            else:
                stored_times = boundary.get("source_times") or {}
                for key, expected in expected_times.items():
                    if not _same_float(stored_times.get(key), expected):
                        blockers.append(f"{expected_id}: source_times.{key} is stale or invalid")
            expected_context = _storyboard_context(
                str(clips[from_id].get("shot_id") or from_id),
                str(clips[to_id].get("shot_id") or to_id),
                live_storyboard_shots,
            )
            if boundary.get("storyboard_context") != expected_context:
                blockers.append(f"{expected_id}: storyboard_context does not match the live storyboard")
        evidence = boundary.get("evidence") if isinstance(boundary.get("evidence"), Mapping) else {}
        for key in ("outgoing_frame", "incoming_frame", "comparison", "preview"):
            item = evidence.get(key) if isinstance(evidence, Mapping) else None
            if not isinstance(item, Mapping):
                blockers.append(f"{expected_id}: evidence.{key} must be an object")
                continue
            try:
                path = _project_file(str(item.get("path") or ""), root=root, label=f"{expected_id} {key}")
                if _sha256(path) != str(item.get("sha256") or ""):
                    blockers.append(f"{expected_id}: {key} bytes changed")
                if path.stat().st_size != int(item.get("size_bytes") or -1):
                    blockers.append(f"{expected_id}: {key} size changed")
            except ValueError as exc:
                blockers.append(str(exc))

    blockers = sorted(set(blockers))
    return {
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "summary": {
            "clips": len(clips),
            "boundaries": len(raw_boundaries),
            "blocking": len(blockers),
            "warnings": 0,
        },
    }


def _audit_boundary(review: Mapping[str, Any], boundary_id: str) -> Dict[str, Any]:
    errors: List[str] = []
    verdict = str(review.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        errors.append(f"verdict must be one of {sorted(VERDICTS)}")
    raw_checks = review.get("checks")
    if not isinstance(raw_checks, Mapping):
        errors.append("checks must be an object")
        raw_checks = {}
    checks = {key: str(raw_checks.get(key) or "").strip().lower() for key in CHECK_KEYS}
    unexpected = sorted(set(raw_checks).difference(CHECK_KEYS))
    if unexpected:
        errors.append(f"unknown check keys: {', '.join(unexpected)}")
    for key, status in checks.items():
        if status not in CHECK_STATUSES:
            errors.append(f"checks.{key} must be one of {sorted(CHECK_STATUSES)}")
    evaluated = [status for status in checks.values() if status != "not_applicable" and status in CHECK_STATUSES]
    if len(evaluated) < 2:
        errors.append("at least two continuity checks must be evaluated")

    raw_codes = review.get("failure_codes") or []
    if not isinstance(raw_codes, list):
        errors.append("failure_codes must be a list")
        raw_codes = []
    failure_codes = sorted(set(str(item).strip() for item in raw_codes if str(item).strip()))
    unknown_codes = sorted(set(failure_codes).difference(FAILURE_CODES))
    if unknown_codes:
        errors.append(f"unknown failure_codes: {', '.join(unknown_codes)}")
    mismatch = sorted(key for key, status in checks.items() if status == "mismatch")
    intentional = sorted(key for key, status in checks.items() if status == "intentional_change")
    observed = str(review.get("observed_transition") or "").strip()
    repair = str(review.get("repair_action") or "").strip()
    notes = str(review.get("notes") or "").strip()
    if not observed:
        errors.append("observed_transition is required")
    if not notes:
        errors.append("notes must explain the boundary decision")
    if verdict == "pass":
        if mismatch:
            errors.append("pass cannot contain mismatch checks")
        if failure_codes:
            errors.append("pass cannot contain failure_codes")
        if repair:
            errors.append("pass cannot contain repair_action")
    elif verdict == "fail":
        if not mismatch and not failure_codes:
            errors.append("fail requires a mismatch check or failure_code")
        if not repair:
            errors.append("fail requires a concrete repair_action")
    if mismatch and verdict != "fail":
        errors.append("mismatch checks require verdict=fail")
    if mismatch and not failure_codes:
        errors.append("mismatch checks require at least one failure_code")
    if failure_codes and verdict != "fail":
        errors.append("failure_codes require verdict=fail")
    return {
        "boundary_id": boundary_id,
        "verdict": verdict,
        "checks": checks,
        "failure_codes": failure_codes,
        "mismatch_checks": mismatch,
        "intentional_changes": intentional,
        "observed_transition": observed,
        "repair_action": repair,
        "notes": notes,
        "validation_errors": sorted(set(errors)),
    }


def build_report(request: Mapping[str, Any], response: Mapping[str, Any]) -> Dict[str, Any]:
    request_check = verify_request(request)
    blockers = list(request_check.get("blockers") or [])
    warnings: List[str] = []
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if str(response.get("request_id") or "") != str(request.get("request_id") or ""):
        blockers.append("response request_id does not match the prepared request")
    reviewed_by = str(response.get("reviewed_by") or "").strip()
    if not reviewed_by:
        blockers.append("reviewed_by is required (label only; not identity authentication)")

    boundaries = {
        str(item.get("boundary_id") or ""): item
        for item in request.get("boundaries") or []
        if isinstance(item, Mapping)
    }
    raw_reviews = response.get("reviews") or []
    if not isinstance(raw_reviews, list):
        blockers.append("response reviews must be a list")
        raw_reviews = []
    provided: Dict[str, Mapping[str, Any]] = {}
    for item in raw_reviews:
        if not isinstance(item, Mapping):
            blockers.append("response reviews must contain objects")
            continue
        boundary_id = str(item.get("boundary_id") or "")
        if boundary_id in provided:
            blockers.append(f"duplicate response review for {boundary_id}")
        provided[boundary_id] = item
    for missing in sorted(set(boundaries).difference(provided)):
        blockers.append(f"missing response review for {missing}")
    for extra in sorted(set(provided).difference(boundaries)):
        blockers.append(f"response contains unknown boundary id {extra}")

    reviews: List[Dict[str, Any]] = []
    for boundary_id in boundaries:
        review = _audit_boundary(provided.get(boundary_id, {}), boundary_id)
        reviews.append(review)
        if review["validation_errors"]:
            blockers.extend(f"{boundary_id}: {error}" for error in review["validation_errors"])
        elif review["verdict"] == "fail":
            blockers.append(f"{boundary_id}: sequence boundary requires repair or regeneration")
        elif review["intentional_changes"]:
            warnings.append(
                f"{boundary_id}: intentional changes accepted for {', '.join(review['intentional_changes'])}"
            )

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    summary = {
        "clips": len(request.get("clips") or []),
        "boundaries": len(boundaries),
        "pass": sum(1 for item in reviews if item["verdict"] == "pass" and not item["validation_errors"]),
        "fail": sum(1 for item in reviews if item["verdict"] == "fail" and not item["validation_errors"]),
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": utc_now(),
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "request": dict(request),
        "response": dict(response),
        "reviews": reviews,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "This artifact binds continuity decisions to reviewed clip and boundary-evidence bytes.",
            "It does not authenticate the reviewer or replace full audiovisual sequence playback.",
        ],
    }
    report["report_id"] = _report_id(report)
    return report


def verify_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if report.get("version") != REPORT_VERSION:
        blockers.append(f"report version must be {REPORT_VERSION}")
    request = report.get("request") or {}
    response = report.get("response") or {}
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        blockers.append("report request and response must be objects")
        return {
            "status": "blocked",
            "blockers": blockers,
            "warnings": [],
            "summary": {"blocking": len(blockers), "warnings": 0},
        }
    canonical = build_report(request, response)
    for key in ("status", "reviews", "summary", "blockers", "warnings"):
        if report.get(key) != canonical.get(key):
            blockers.append(f"stored {key} does not match live canonical audit")
    if str(report.get("report_id") or "") != _report_id(report):
        blockers.append("report_id does not match stored report content")
    if str(report.get("report_id") or "") != str(canonical.get("report_id") or ""):
        blockers.append("report_id does not match live canonical audit")
    blockers.extend(canonical.get("blockers") or [])
    blockers = sorted(set(blockers))
    warnings = sorted(set(canonical.get("warnings") or []))
    return {
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            **dict(canonical.get("summary") or {}),
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
    }


def emit_request_markdown(request: Mapping[str, Any]) -> str:
    lines = [
        "# Generated Sequence Continuity Review Request",
        "",
        f"- Request ID: `{request.get('request_id', '')}`",
        f"- Clips / boundaries: {len(request.get('clips') or [])} / {len(request.get('boundaries') or [])}",
        "- Scope: adjacent generated clips before final assembly",
        "",
        "## Required review passes",
        "",
    ]
    for item in (request.get("review_protocol") or {}).get("passes") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "| boundary | mode | preview | comparison | expected handoff |",
            "|---|---|---|---|---|",
        ]
    )
    for boundary in request.get("boundaries") or []:
        context = boundary.get("storyboard_context") or {}
        evidence = boundary.get("evidence") or {}
        handoff = (
            f"{context.get('expected_outgoing_state', '')} → {context.get('expected_incoming_state', '')}"
        ).replace("|", "/")
        lines.append(
            f"| {boundary.get('boundary_id', '')} | {context.get('mode', '')} "
            f"| `{(evidence.get('preview') or {}).get('path', '')}` "
            f"| `{(evidence.get('comparison') or {}).get('path', '')}` | {handoff} |"
        )
    lines.extend(
        [
            "",
            "## Decision rules",
            "",
            "- Check values: `match`, `intentional_change`, `mismatch`, or `not_applicable`.",
            "- `pass` cannot contain an unexplained mismatch or failure code.",
            "- `fail` requires a mismatch/failure code and a concrete repair action.",
            "- An intentional storyboard change may pass, but remains a warning in the sequence gate.",
            "- The preview is muted; review audio joins later in the assembled master.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_report_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Generated Sequence Continuity Review",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Reviewed by: `{(report.get('response') or {}).get('reviewed_by', '')}`",
        f"- Clips / boundaries: {summary.get('clips', 0)} / {summary.get('boundaries', 0)}",
        f"- Pass / fail: {summary.get('pass', 0)} / {summary.get('fail', 0)}",
        f"- Blocking / warnings: {summary.get('blocking', 0)} / {summary.get('warnings', 0)}",
        "",
        "| boundary | verdict | mismatches | intentional changes | failure codes |",
        "|---|---|---|---|---|",
    ]
    for review in report.get("reviews") or []:
        lines.append(
            f"| {review.get('boundary_id', '')} | {review.get('verdict', '')} "
            f"| {', '.join(review.get('mismatch_checks') or []) or '-'} "
            f"| {', '.join(review.get('intentional_changes') or []) or '-'} "
            f"| {', '.join(review.get('failure_codes') or []) or '-'} |"
        )
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    return "\n".join(lines).rstrip() + "\n"


def _prepare_command(args: argparse.Namespace) -> int:
    request = prepare_request(
        args.clip_review,
        project_dir=args.project_dir,
        evidence_dir=args.evidence_dir,
        storyboard_plan_path=args.storyboard_plan,
        preview_seconds=args.preview_seconds,
        force=args.force,
    )
    root = Path(str(request.get("project_dir") or "")).resolve()
    output = _project_output(args.output, root=root, label="sequence review request output")
    _write_json(output, request, force=args.force)
    if args.markdown:
        markdown = _project_output(args.markdown, root=root, label="sequence request Markdown")
        _write_text(markdown, emit_request_markdown(request), force=args.force)
    if args.response_template:
        response = _project_output(args.response_template, root=root, label="sequence response template")
        _write_json(response, request["response_template"], force=args.force)
    print(
        f"Generated sequence review request: {args.output}; clips={len(request['clips'])} "
        f"boundaries={len(request['boundaries'])} request_id={request['request_id']}"
    )
    return 0


def _audit_command(args: argparse.Namespace) -> int:
    request = _load_json(args.request)
    response = _load_json(args.response)
    report = build_report(request, response)
    root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    output = _project_output(args.output, root=root, label="sequence review report output")
    _write_json(output, report, force=args.force)
    if args.markdown:
        markdown = _project_output(args.markdown, root=root, label="sequence report Markdown")
        _write_text(markdown, emit_report_markdown(report), force=args.force)
    summary = report["summary"]
    print(
        f"Generated sequence review audit: status={report['status']} boundaries={summary['boundaries']} "
        f"blocking={summary['blocking']} warnings={summary['warnings']}"
    )
    return 2 if args.strict and summary["blocking"] else 0


def _verify_command(args: argparse.Namespace) -> int:
    report = _load_json(args.report)
    verification = verify_report(report)
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 2 if args.strict and verification["summary"]["blocking"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, audit, and live-verify continuity reviews across generated video clips."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create adjacent boundary evidence and a review request.")
    prepare.add_argument("--project-dir", default=".", help="Project root; all inputs and outputs must stay inside it.")
    prepare.add_argument("--clip-review", required=True, help="Live-verifiable generated_clip_review.json.")
    prepare.add_argument("--storyboard-plan", help="Optional storyboard_plan.json used for shot order and anchors.")
    prepare.add_argument("--evidence-dir", required=True, help="Directory for frames, comparisons, and previews.")
    prepare.add_argument("--preview-seconds", type=float, default=1.0, help="Tail/head seconds per boundary (0.25-3.0).")
    prepare.add_argument("--output", required=True, help="Output generated_sequence_review_request.json.")
    prepare.add_argument("--markdown", help="Optional Markdown review request.")
    prepare.add_argument("--response-template", help="Optional blank response JSON to fill during review.")
    prepare.add_argument("--force", action="store_true", help="Replace request and boundary evidence outputs.")
    prepare.set_defaults(func=_prepare_command)

    audit = subparsers.add_parser("audit", help="Validate a completed sequence review response.")
    audit.add_argument("--request", required=True, help="Prepared generated_sequence_review_request.json.")
    audit.add_argument("--response", required=True, help="Completed generated_sequence_review_response.json.")
    audit.add_argument("--output", required=True, help="Output generated_sequence_review.json.")
    audit.add_argument("--markdown", help="Optional Markdown audit report.")
    audit.add_argument("--strict", action="store_true", help="Exit 2 when any sequence boundary fails.")
    audit.add_argument("--force", action="store_true", help="Replace audit outputs.")
    audit.set_defaults(func=_audit_command)

    verify = subparsers.add_parser("verify", help="Recompute the audit and detect stale evidence or clips.")
    verify.add_argument("--report", required=True, help="generated_sequence_review.json to verify.")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when live verification is blocked.")
    verify.set_defaults(func=_verify_command)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"generated_sequence_review: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
