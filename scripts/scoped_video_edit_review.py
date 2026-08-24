#!/usr/bin/env python3
"""Review whether an AI video edit changed only its declared scope.

The script does not claim to detect semantic preservation automatically. It
binds the source and edited video bytes, creates same-time visual evidence, and
validates an explicit reviewer response against the requested change and named
invariants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import generated_clip_review


REQUEST_VERSION = "scoped_video_edit_review_request.v1"
RESPONSE_VERSION = "scoped_video_edit_review_response.v1"
REPORT_VERSION = "scoped_video_edit_review.v1"

CHANGE_CATEGORIES = {
    "subject",
    "wardrobe",
    "object",
    "background",
    "look",
    "lighting",
    "vfx",
    "text",
    "audio",
    "other",
}
PROTECTION_KEYS = (
    "subject_identity",
    "wardrobe",
    "performance_motion",
    "camera_motion",
    "framing_composition",
    "scene_outside_scope",
    "lighting_color",
    "props_text",
    "edit_rhythm",
    "source_audio",
)
PROTECTION_LABELS = {
    "subject_identity": "subject identity, face, hair, and body proportions",
    "wardrobe": "wardrobe and accessories outside the requested change",
    "performance_motion": "performance, pose, gesture, lip movement, path, and timing",
    "camera_motion": "camera position, lens feel, movement, and shake",
    "framing_composition": "crop, aspect, framing, composition, and perspective",
    "scene_outside_scope": "people, objects, and scene regions outside the edit target",
    "lighting_color": "lighting direction, exposure, palette, and color relationships",
    "props_text": "non-target props, product geometry, logos, and readable text",
    "edit_rhythm": "cuts, event order, pacing, and total timing",
    "source_audio": "original dialogue, voice, ambience, effects, music, and sync",
}
REVIEW_STATUSES = {"pass", "fail", "not_observable"}
VERDICTS = {"pass", "fail"}
SAMPLE_RATIOS = (0.15, 0.50, 0.85)
FLOAT_TOLERANCE = 0.001


probe_media = generated_clip_review.probe_media


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
            "review": report.get("review"),
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


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _ensure_distinct_paths(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (label, path) in enumerate(items):
        for other_label, other in items[:index]:
            if _same_file(path, other):
                raise ValueError(f"{label} must not overwrite {other_label}: {path}")


def _media_signature(value: Mapping[str, Any]) -> Dict[str, Any]:
    return generated_clip_review._media_signature(value)


def _source_record(path: Path, *, root: Path) -> Dict[str, Any]:
    media = _media_signature(probe_media(str(path)))
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "media": media,
    }


def _even(value: float) -> int:
    number = max(2, int(round(value)))
    return number if number % 2 == 0 else number + 1


def _canvas(media: Mapping[str, Any]) -> Tuple[int, int]:
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("source media requires positive dimensions")
    if width >= height:
        canvas_width = min(width, 640)
        canvas_height = canvas_width * height / width
    else:
        canvas_height = min(height, 720)
        canvas_width = canvas_height * width / height
    return _even(canvas_width), _even(canvas_height)


def _run_ffmpeg(command: Sequence[str], *, output: Path, label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise ValueError(f"{label} failed: {detail.splitlines()[-1]}")


def _sample_times(start: float, end: float) -> List[float]:
    span = end - start
    return [round(start + span * ratio, 6) for ratio in SAMPLE_RATIOS]


def generate_comparison_evidence(
    source: Path,
    edited: Path,
    output_dir: Path,
    *,
    start: float,
    end: float,
    source_media: Mapping[str, Any],
    edited_media: Mapping[str, Any],
    force: bool,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = [output_dir / f"comparison_{index:02d}.jpg" for index in range(1, 4)]
    preview = output_dir / "comparison_preview.mp4"
    outputs = [*frames, preview]
    if not force:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            raise ValueError(f"refusing to overwrite comparison evidence without --force: {', '.join(existing)}")

    width, height = _canvas(source_media)
    fps = min(
        30.0,
        max(1.0, min(float(source_media.get("fps") or 24), float(edited_media.get("fps") or 24))),
    )
    normalize = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )
    for timestamp, destination in zip(_sample_times(start, end), frames):
        filtergraph = f"[0:v]{normalize}[left];[1:v]{normalize}[right];[left][right]hstack=inputs=2[outv]"
        _run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp:.6f}",
                "-i",
                str(source),
                "-ss",
                f"{timestamp:.6f}",
                "-i",
                str(edited),
                "-filter_complex",
                filtergraph,
                "-map",
                "[outv]",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(destination),
            ],
            output=destination,
            label="same-time comparison frame generation",
        )

    span = end - start
    filtergraph = (
        f"[0:v]fps={fps:.6f},{normalize}[left];"
        f"[1:v]fps={fps:.6f},{normalize}[right];"
        "[left][right]hstack=inputs=2[outv]"
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(source),
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(edited),
            "-filter_complex",
            filtergraph,
            "-map",
            "[outv]",
            "-map",
            "0:a:0?",
            "-map",
            "1:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            f"{span:.6f}",
            "-movflags",
            "+faststart",
            str(preview),
        ],
        output=preview,
        label="same-time comparison preview generation",
    )
    return {
        "canvas": {"width": width * 2, "height": height, "fps": round(fps, 6)},
        "frames": frames,
        "preview": preview,
    }


def _automatic_findings(
    source_media: Mapping[str, Any],
    edited_media: Mapping[str, Any],
    *,
    protections: Sequence[str],
    review_span: float,
) -> Tuple[List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    source_duration = float(source_media.get("duration") or 0)
    edited_duration = float(edited_media.get("duration") or 0)
    source_fps = max(1.0, float(source_media.get("fps") or 0))
    duration_tolerance = max(0.1, 2.0 / source_fps)
    if abs(source_duration - edited_duration) > duration_tolerance:
        blockers.append(
            f"edited duration drift exceeds {duration_tolerance:.3f}s: "
            f"source={source_duration:.6f}s edited={edited_duration:.6f}s"
        )
    if (
        int(source_media.get("width") or 0),
        int(source_media.get("height") or 0),
    ) != (
        int(edited_media.get("width") or 0),
        int(edited_media.get("height") or 0),
    ):
        blockers.append("edited dimensions differ from the source")
    if abs(float(source_media.get("fps") or 0) - float(edited_media.get("fps") or 0)) > 0.05:
        blockers.append("edited frame rate differs from the source by more than 0.05 fps")

    source_has_audio = bool(source_media.get("has_audio"))
    edited_has_audio = bool(edited_media.get("has_audio"))
    if "source_audio" in protections and (not source_has_audio or not edited_has_audio):
        blockers.append("source_audio is protected but both files do not contain an audio stream")
    elif source_has_audio != edited_has_audio:
        warnings.append("audio stream presence changed but source_audio was not declared as protected")
    if review_span > 20.0:
        warnings.append("review scope exceeds 20 seconds; split provider edits when practical and still watch both files in full")
    return blockers, warnings


def _suggested_prompt(
    *,
    change: str,
    change_category: str,
    start: float,
    end: float,
    protections: Sequence[str],
) -> str:
    keep = "; ".join(PROTECTION_LABELS[key] for key in protections)
    return (
        f"Strictly edit @Video1 from {start:.3f}s to {end:.3f}s. "
        f"Change only this {change_category} target: {change}. "
        "@Video1 is the sole editing master for action, timing, camera, composition, and event order. "
        f"Preserve unchanged: {keep}. "
        "Keep the source duration, dimensions, and frame rate. Except for the exact target above, do not alter any other layer."
    )


def _response_template(request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "request_id": request.get("request_id"),
        "reviewed_by": "",
        "playback": {
            "source_full_1x": False,
            "edited_full_1x": False,
            "comparison_scope_1x": False,
        },
        "target_change": {"status": "", "evidence": ""},
        "protections": [
            {"key": key, "status": "", "evidence": ""}
            for key in request.get("protections") or []
        ],
        "verdict": "",
        "repair_action": "",
        "notes": "",
    }


def prepare_request(
    source_path: str,
    edited_path: str,
    *,
    project_dir: str,
    evidence_dir: str,
    change_category: str,
    change: str,
    protections: Sequence[str],
    start: float = 0.0,
    end: Optional[float] = None,
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    source = _project_file(source_path, root=root, label="source video")
    edited = _project_file(edited_path, root=root, label="edited video")
    if _same_file(source, edited):
        raise ValueError("source and edited video must be different files")

    category = change_category.strip().lower()
    if category not in CHANGE_CATEGORIES:
        raise ValueError(f"unsupported change category: {change_category}")
    change_text = " ".join(change.split())
    if not change_text:
        raise ValueError("change description is required")
    ordered_protections: List[str] = []
    for raw in protections:
        key = str(raw).strip().lower()
        if key not in PROTECTION_KEYS:
            raise ValueError(f"unsupported protection key: {raw}")
        if key in ordered_protections:
            raise ValueError(f"duplicate protection key: {key}")
        ordered_protections.append(key)
    if len(ordered_protections) < 2:
        raise ValueError("declare at least two explicit --preserve invariants")
    if category == "audio" and "source_audio" in ordered_protections:
        raise ValueError("an audio edit cannot also declare source_audio as unchanged")

    source_record = _source_record(source, root=root)
    edited_record = _source_record(edited, root=root)
    if source_record["sha256"] == edited_record["sha256"]:
        raise ValueError("source and edited video have identical bytes; no edit can be reviewed")
    source_duration = float(source_record["media"]["duration"])
    edited_duration = float(edited_record["media"]["duration"])
    review_end = min(source_duration, edited_duration) if end is None else float(end)
    review_start = float(start)
    if review_start < 0 or review_end <= review_start or review_end > min(source_duration, edited_duration) + FLOAT_TOLERANCE:
        raise ValueError("review range must be positive and stay within both source and edited video durations")
    review_start = round(review_start, 6)
    review_end = round(review_end, 6)

    evidence_root = _project_output(evidence_dir, root=root, label="comparison evidence directory")
    _ensure_distinct_paths(
        {
            "source video": source,
            "edited video": edited,
            "comparison frame 1": evidence_root / "comparison_01.jpg",
            "comparison frame 2": evidence_root / "comparison_02.jpg",
            "comparison frame 3": evidence_root / "comparison_03.jpg",
            "comparison preview": evidence_root / "comparison_preview.mp4",
        }
    )
    evidence = generate_comparison_evidence(
        source,
        edited,
        evidence_root,
        start=review_start,
        end=review_end,
        source_media=source_record["media"],
        edited_media=edited_record["media"],
        force=force,
    )
    evidence_record = {
        "canvas": evidence["canvas"],
        "sample_times": _sample_times(review_start, review_end),
        "frames": [],
    }
    for timestamp, path in zip(evidence_record["sample_times"], evidence["frames"]):
        resolved = Path(path).resolve()
        evidence_record["frames"].append(
            {
                "time": timestamp,
                "path": _relative(resolved, root),
                "sha256": _sha256(resolved),
                "size_bytes": resolved.stat().st_size,
            }
        )
    preview = Path(evidence["preview"]).resolve()
    evidence_record["preview"] = {
        "path": _relative(preview, root),
        "sha256": _sha256(preview),
        "size_bytes": preview.stat().st_size,
    }

    automatic_blockers, automatic_warnings = _automatic_findings(
        source_record["media"],
        edited_record["media"],
        protections=ordered_protections,
        review_span=review_end - review_start,
    )
    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": source_record,
        "edited": edited_record,
        "edit_scope": {
            "category": category,
            "change": change_text,
            "start": review_start,
            "end": review_end,
        },
        "protections": ordered_protections,
        "suggested_prompt": _suggested_prompt(
            change=change_text,
            change_category=category,
            start=review_start,
            end=review_end,
            protections=ordered_protections,
        ),
        "evidence": evidence_record,
        "automatic_findings": {
            "blockers": automatic_blockers,
            "warnings": automatic_warnings,
        },
        "review_instructions": [
            "Watch the complete source and edited files separately at 1x with audio.",
            "Watch the full side-by-side scope preview at 1x; source is left and edited is right.",
            "Inspect all same-time JPEG pairs for the requested target and every named invariant.",
            "The comparison preview carries source audio first and edited audio second when both streams exist; select each track explicitly.",
            "Mark not_observable instead of guessing. Any failed or unobservable required check blocks approval.",
        ],
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = _response_template(request)
    return request


def build_report(request: Mapping[str, Any], response: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    automatic_findings = (
        request.get("automatic_findings")
        if isinstance(request.get("automatic_findings"), Mapping)
        else {}
    )
    warnings = [str(item) for item in (automatic_findings.get("warnings") or [])]
    blockers.extend(str(item) for item in (automatic_findings.get("blockers") or []))
    if request.get("version") != REQUEST_VERSION:
        blockers.append("request version is invalid")
    if request.get("request_id") != _request_id(request):
        blockers.append("request_id does not match canonical request content")
    if response.get("version") != RESPONSE_VERSION:
        blockers.append("response version is invalid")
    if response.get("request_id") != request.get("request_id"):
        blockers.append("response request_id does not match the request")
    reviewed_by = " ".join(str(response.get("reviewed_by") or "").split())
    if not reviewed_by:
        blockers.append("reviewed_by is required")

    playback = response.get("playback") if isinstance(response.get("playback"), Mapping) else {}
    playback_review = {
        key: playback.get(key) is True
        for key in ("source_full_1x", "edited_full_1x", "comparison_scope_1x")
    }
    for key, complete in playback_review.items():
        if not complete:
            blockers.append(f"playback confirmation is required: {key}")

    target = response.get("target_change") if isinstance(response.get("target_change"), Mapping) else {}
    target_status = str(target.get("status") or "").strip().lower()
    target_evidence = " ".join(str(target.get("evidence") or "").split())
    if target_status not in REVIEW_STATUSES:
        blockers.append("target_change status must be pass, fail, or not_observable")
    if not target_evidence:
        blockers.append("target_change evidence is required")
    if target_status != "pass":
        blockers.append(f"requested target change did not pass review: {target_status or 'missing'}")

    expected_keys = [str(key) for key in (request.get("protections") or [])]
    raw_protections = response.get("protections") if isinstance(response.get("protections"), list) else []
    protection_reviews: List[Dict[str, str]] = []
    seen: List[str] = []
    for item in raw_protections:
        if not isinstance(item, Mapping):
            blockers.append("protection review entries must be objects")
            continue
        key = str(item.get("key") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        evidence = " ".join(str(item.get("evidence") or "").split())
        if key in seen:
            blockers.append(f"duplicate protection review: {key}")
            continue
        seen.append(key)
        if key not in expected_keys:
            blockers.append(f"unexpected protection review: {key}")
        if status not in REVIEW_STATUSES:
            blockers.append(f"{key or 'unknown protection'} status must be pass, fail, or not_observable")
        if not evidence:
            blockers.append(f"{key or 'unknown protection'} evidence is required")
        if status != "pass":
            blockers.append(f"protected invariant did not pass: {key or 'unknown'}={status or 'missing'}")
        protection_reviews.append({"key": key, "status": status, "evidence": evidence})
    missing = [key for key in expected_keys if key not in seen]
    if missing:
        blockers.append(f"missing protection reviews: {', '.join(missing)}")

    verdict = str(response.get("verdict") or "").strip().lower()
    repair_action = " ".join(str(response.get("repair_action") or "").split())
    notes = " ".join(str(response.get("notes") or "").split())
    if verdict not in VERDICTS:
        blockers.append("verdict must be pass or fail")
    semantic_failure = target_status != "pass" or any(
        item["status"] != "pass" for item in protection_reviews
    ) or bool(automatic_findings.get("blockers"))
    expected_verdict = "fail" if semantic_failure else "pass"
    if verdict in VERDICTS and verdict != expected_verdict:
        blockers.append(f"verdict must be {expected_verdict} for the recorded findings")
    if expected_verdict == "fail" and not repair_action:
        blockers.append("repair_action is required when review fails")
    if not notes:
        blockers.append("review notes are required")

    blocker_list = sorted(set(blockers))
    warning_list = sorted(set(warnings))
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": utc_now(),
        "request": dict(request),
        "response": dict(response),
        "review": {
            "reviewed_by": reviewed_by,
            "playback": playback_review,
            "target_change": {"status": target_status, "evidence": target_evidence},
            "protections": protection_reviews,
            "verdict": verdict,
            "repair_action": repair_action,
            "notes": notes,
        },
        "status": "ready" if not blocker_list else "blocked",
        "summary": {
            "blocking": len(blocker_list),
            "warnings": len(warning_list),
            "protections": len(expected_keys),
            "protections_passed": sum(item["status"] == "pass" for item in protection_reviews),
            "target_passed": target_status == "pass",
        },
        "blockers": blocker_list,
        "warnings": warning_list,
    }
    report["report_id"] = _report_id(report)
    return report


def _float_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= FLOAT_TOLERANCE
    except (TypeError, ValueError):
        return False


def verify_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    live_blockers: List[str] = []
    request = report.get("request") if isinstance(report.get("request"), Mapping) else {}
    response = report.get("response") if isinstance(report.get("response"), Mapping) else {}
    if report.get("version") != REPORT_VERSION:
        live_blockers.append("report version is invalid")
    if request.get("version") != REQUEST_VERSION:
        live_blockers.append("embedded request version is invalid")
    if request.get("request_id") != _request_id(request):
        live_blockers.append("embedded request_id does not match canonical content")

    root_raw = str(request.get("project_dir") or "")
    root = Path(root_raw).expanduser().resolve() if root_raw else Path("/")
    if not root_raw or not root.is_dir():
        live_blockers.append("embedded project_dir is missing or unavailable")
    else:
        for label in ("source", "edited"):
            stored = request.get(label) if isinstance(request.get(label), Mapping) else {}
            try:
                path = _project_file(str(stored.get("path") or ""), root=root, label=f"{label} video")
                if _sha256(path) != stored.get("sha256") or path.stat().st_size != int(stored.get("size_bytes") or -1):
                    live_blockers.append(f"{label} video bytes changed after review preparation")
                live_media = _media_signature(probe_media(str(path)))
                if live_media != stored.get("media"):
                    live_blockers.append(f"{label} video media contract changed")
            except (OSError, TypeError, ValueError) as exc:
                live_blockers.append(str(exc))

        source_record = request.get("source") if isinstance(request.get("source"), Mapping) else {}
        edited_record = request.get("edited") if isinstance(request.get("edited"), Mapping) else {}
        try:
            source_file = _project_file(str(source_record.get("path") or ""), root=root, label="source video")
            edited_file = _project_file(str(edited_record.get("path") or ""), root=root, label="edited video")
            if _same_file(source_file, edited_file) or _sha256(source_file) == _sha256(edited_file):
                live_blockers.append("source and edited video no longer represent distinct content")
        except (OSError, ValueError) as exc:
            live_blockers.append(str(exc))

        evidence = request.get("evidence") if isinstance(request.get("evidence"), Mapping) else {}
        scope = request.get("edit_scope") if isinstance(request.get("edit_scope"), Mapping) else {}
        try:
            expected_times = _sample_times(
                float(scope.get("start") or 0),
                float(scope.get("end") or 0),
            )
        except (TypeError, ValueError) as exc:
            expected_times = []
            live_blockers.append(f"invalid edit scope timing: {exc}")
        stored_times = evidence.get("sample_times") if isinstance(evidence.get("sample_times"), list) else []
        if len(stored_times) != len(expected_times) or any(
            not _float_equal(left, right) for left, right in zip(stored_times, expected_times)
        ):
            live_blockers.append("comparison sample times do not match the edit scope")
        evidence_items = list(evidence.get("frames") or [])
        preview = evidence.get("preview")
        if isinstance(preview, Mapping):
            evidence_items.append(preview)
        else:
            live_blockers.append("comparison preview record is missing")
        if len(evidence.get("frames") or []) != 3:
            live_blockers.append("exactly three same-time comparison frames are required")
        for item in evidence_items:
            if not isinstance(item, Mapping):
                live_blockers.append("comparison evidence record is invalid")
                continue
            try:
                path = _project_file(str(item.get("path") or ""), root=root, label="comparison evidence")
                if _sha256(path) != item.get("sha256") or path.stat().st_size != int(item.get("size_bytes") or -1):
                    live_blockers.append(f"comparison evidence bytes changed: {item.get('path')}")
            except (OSError, TypeError, ValueError) as exc:
                live_blockers.append(str(exc))

        try:
            expected_prompt = _suggested_prompt(
                change=str(scope.get("change") or ""),
                change_category=str(scope.get("category") or ""),
                start=float(scope.get("start") or 0),
                end=float(scope.get("end") or 0),
                protections=[str(item) for item in (request.get("protections") or [])],
            )
            if request.get("suggested_prompt") != expected_prompt:
                live_blockers.append("suggested prompt no longer matches the scoped edit contract")
            source_media = source_record.get("media") if isinstance(source_record.get("media"), Mapping) else {}
            edited_media = edited_record.get("media") if isinstance(edited_record.get("media"), Mapping) else {}
            findings = _automatic_findings(
                source_media,
                edited_media,
                protections=[str(item) for item in (request.get("protections") or [])],
                review_span=float(scope.get("end") or 0) - float(scope.get("start") or 0),
            )
            stored_findings = (
                request.get("automatic_findings")
                if isinstance(request.get("automatic_findings"), Mapping)
                else {}
            )
            if list(stored_findings.get("blockers") or []) != findings[0] or list(stored_findings.get("warnings") or []) != findings[1]:
                live_blockers.append("automatic media findings do not match the current request contract")
        except (KeyError, TypeError, ValueError) as exc:
            live_blockers.append(f"invalid scoped edit contract: {exc}")

    try:
        canonical = build_report(request, response)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        canonical = {
            "status": "blocked",
            "summary": {"blocking": 1, "warnings": 0},
            "blockers": [str(exc)],
            "warnings": [],
        }
    for key in ("review", "status", "summary", "blockers", "warnings", "report_id"):
        if report.get(key) != canonical.get(key):
            live_blockers.append(f"stored {key} does not match canonical review state")

    combined = sorted(set([*(canonical.get("blockers") or []), *live_blockers]))
    warnings = sorted(set(str(item) for item in (canonical.get("warnings") or [])))
    verification = dict(report)
    verification["status"] = "ready" if not combined else "blocked"
    summary = dict(canonical.get("summary") or {})
    summary["blocking"] = len(combined)
    summary["warnings"] = len(warnings)
    verification["summary"] = summary
    verification["blockers"] = combined
    verification["warnings"] = warnings
    verification["live_verified_at"] = utc_now()
    return verification


def emit_request_markdown(request: Mapping[str, Any]) -> str:
    scope = request.get("edit_scope") or {}
    evidence = request.get("evidence") or {}
    lines = [
        "# Scoped Video Edit Review Request",
        "",
        f"- Request ID: `{request.get('request_id')}`",
        f"- Source: `{(request.get('source') or {}).get('path')}`",
        f"- Edited: `{(request.get('edited') or {}).get('path')}`",
        f"- Scope: `{scope.get('start'):.3f}s` → `{scope.get('end'):.3f}s`",
        f"- Change category: `{scope.get('category')}`",
        f"- Change only: {scope.get('change')}",
        "",
        "## Preserve",
        "",
    ]
    for key in request.get("protections") or []:
        lines.append(f"- `{key}` — {PROTECTION_LABELS.get(str(key), '')}")
    lines.extend(["", "## Suggested Provider-neutral Prompt", "", "```text", str(request.get("suggested_prompt") or ""), "```", "", "## Evidence", ""])
    lines.append(f"- Full comparison preview: `{(evidence.get('preview') or {}).get('path')}` (source left, edited right)")
    for item in evidence.get("frames") or []:
        lines.append(f"- `{float(item.get('time') or 0):.3f}s`: `{item.get('path')}`")
    lines.extend(["", "## Required Review", ""])
    for item in request.get("review_instructions") or []:
        lines.append(f"- {item}")
    blockers = (request.get("automatic_findings") or {}).get("blockers") or []
    warnings = (request.get("automatic_findings") or {}).get("warnings") or []
    if blockers or warnings:
        lines.extend(["", "## Automatic Findings", ""])
        lines.extend(f"- BLOCK: {item}" for item in blockers)
        lines.extend(f"- WARN: {item}" for item in warnings)
    lines.extend(
        [
            "",
            "The prompt is a provider-neutral scope contract, not proof that the active UI/API supports every control. ",
            "The reviewer label and SHA-256 digests are local evidence labels, not identity authentication or digital signatures.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_report_markdown(report: Mapping[str, Any]) -> str:
    review = report.get("review") or {}
    lines = [
        "# Scoped Video Edit Review",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Report ID: `{report.get('report_id')}`",
        f"- Reviewer label: `{review.get('reviewed_by')}`",
        f"- Verdict: `{review.get('verdict')}`",
        f"- Target change: `{(review.get('target_change') or {}).get('status')}` — {(review.get('target_change') or {}).get('evidence')}",
        "",
        "## Protected Invariants",
        "",
        "| invariant | status | evidence |",
        "|---|---|---|",
    ]
    for item in review.get("protections") or []:
        lines.append(f"| `{item.get('key')}` | `{item.get('status')}` | {item.get('evidence')} |")
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    if review.get("repair_action"):
        lines.extend(["", "## Repair Action", "", str(review.get("repair_action"))])
    lines.extend(["", "## Notes", "", str(review.get("notes") or ""), ""])
    return "\n".join(lines)


def _prepare_outputs(args: argparse.Namespace, request: Mapping[str, Any]) -> None:
    root = Path(args.project_dir).expanduser().resolve()
    source = _project_file(args.source, root=root, label="source video")
    edited = _project_file(args.edited, root=root, label="edited video")
    targets = [
        _project_output(args.output, root=root, label="request output"),
        _project_output(args.markdown, root=root, label="request markdown"),
        _project_output(args.response_template, root=root, label="response template"),
    ]
    if len({str(path) for path in targets}) != len(targets):
        raise ValueError("request, markdown, and response template outputs must be different")
    protected_paths = [source, edited]
    evidence = request.get("evidence") or {}
    for item in [*(evidence.get("frames") or []), evidence.get("preview")]:
        if isinstance(item, Mapping):
            protected_paths.append(
                _project_file(str(item.get("path") or ""), root=root, label="comparison evidence")
            )
    for target in targets:
        if any(_same_file(target, protected) for protected in protected_paths):
            raise ValueError("review outputs must not overwrite source, edited media, or comparison evidence")
    _write_json(targets[0], request, force=args.force)
    _write_text(targets[1], emit_request_markdown(request), force=args.force)
    _write_json(targets[2], request["response_template"], force=args.force)


def _audit_outputs(
    args: argparse.Namespace,
    request: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("request project_dir is missing or unavailable")
    output = _project_output(args.output, root=root, label="report output")
    markdown = _project_output(args.markdown, root=root, label="report markdown")
    if _same_file(output, markdown):
        raise ValueError("report JSON and Markdown outputs must be different")
    protected_paths = [
        _project_file(str((request.get(key) or {}).get("path") or ""), root=root, label=f"{key} video")
        for key in ("source", "edited")
    ]
    evidence = request.get("evidence") or {}
    for item in [*(evidence.get("frames") or []), evidence.get("preview")]:
        if isinstance(item, Mapping):
            protected_paths.append(
                _project_file(str(item.get("path") or ""), root=root, label="comparison evidence")
            )
    for raw, label in ((args.request, "request input"), (args.response, "response input")):
        path = Path(raw).expanduser().resolve()
        if path.exists():
            protected_paths.append(path)
    for target in (output, markdown):
        if any(_same_file(target, protected) for protected in protected_paths):
            raise ValueError("report outputs must not overwrite inputs or bound evidence")
    _write_json(output, report, force=args.force)
    _write_text(markdown, emit_report_markdown(report), force=args.force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and verify source-bound reviews for scoped AI video edits.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Bind source/edit bytes and create same-time comparison evidence")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--edited", required=True)
    prepare.add_argument("--change-category", required=True, choices=sorted(CHANGE_CATEGORIES))
    prepare.add_argument("--change", required=True, help="One exact target change; compound edits should be split")
    prepare.add_argument("--preserve", action="append", default=[], choices=PROTECTION_KEYS)
    prepare.add_argument("--start", type=float, default=0.0)
    prepare.add_argument("--end", type=float)
    prepare.add_argument("--evidence-dir", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--markdown", required=True)
    prepare.add_argument("--response-template", required=True)
    prepare.add_argument("--force", action="store_true")

    audit = subparsers.add_parser("audit", help="Validate a completed reviewer response")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown", required=True)
    audit.add_argument("--strict", action="store_true")
    audit.add_argument("--force", action="store_true")

    verify = subparsers.add_parser("verify", help="Live-verify a stored review report")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            request = prepare_request(
                args.source,
                args.edited,
                project_dir=args.project_dir,
                evidence_dir=args.evidence_dir,
                change_category=args.change_category,
                change=args.change,
                protections=args.preserve,
                start=args.start,
                end=args.end,
                force=args.force,
            )
            _prepare_outputs(args, request)
            findings = request.get("automatic_findings") or {}
            print(
                f"request={request['request_id']} blockers={len(findings.get('blockers') or [])} "
                f"warnings={len(findings.get('warnings') or [])}"
            )
            return 0
        if args.command == "audit":
            request = _load_json(args.request)
            response = _load_json(args.response)
            report = build_report(request, response)
            _audit_outputs(args, request, report)
            print(
                f"status={report['status']} blocking={report['summary']['blocking']} "
                f"warnings={report['summary']['warnings']}"
            )
            return 2 if args.strict and report["status"] != "ready" else 0
        report = _load_json(args.report)
        verification = verify_report(report)
        print(
            f"status={verification['status']} blocking={verification['summary']['blocking']} "
            f"warnings={verification['summary']['warnings']}"
        )
        return 2 if args.strict and verification["status"] != "ready" else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
