#!/usr/bin/env python3
"""Prepare and verify source-bound reviews for generated video clips.

The script does not claim to understand visual semantics. It creates bounded
contact sheets and a review contract, then validates a human/model response
against the exact clip bytes that were reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REQUEST_VERSION = "generated_clip_review_request.v1"
RESPONSE_VERSION = "generated_clip_review_response.v1"
REPORT_VERSION = "generated_clip_review.v1"

SCORE_WEIGHTS: Mapping[str, int] = {
    "identity_wardrobe": 25,
    "action_end_state": 20,
    "motion_anatomy_physics": 20,
    "camera_behavior": 10,
    "frame_integrity": 15,
    "look_consistency": 10,
}

HARD_FAIL_CODES = {
    "identity_break",
    "missing_or_wrong_action",
    "anatomy_or_physics_failure",
    "extra_subject_or_object",
    "prop_disappearance_or_drift",
    "rendered_text_or_watermark",
    "continuity_contradiction",
    "audio_picture_mismatch",
    "explicit_must_avoid_violation",
}

GENERATED_VIDEO_ROUTES = {
    "dreamina_video",
    "dreamina_seedance",
    "seedance",
    "veo",
    "sora",
    "ltx",
    "wan",
}

VERDICTS = {"pass", "pass_with_edits", "fail"}
STORY_READABILITY = {"clear", "partial", "unclear"}
RANGE_TOLERANCE = 0.05


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
    lexical = _lexical_project_path(raw_path, root=root, label=label)
    path = lexical.resolve()
    return path


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _float_ratio(value: Any) -> float:
    raw = str(value or "0").strip()
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 0.0
    return float(raw or 0)


def probe_media(path: str) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,sample_rate,channels:format=duration",
        "-of",
        "json",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(f"ffprobe failed for {path}: {detail.splitlines()[-1]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {path}") from exc

    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not isinstance(video, Mapping):
        raise ValueError(f"clip has no decodable video stream: {path}")
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"clip has invalid duration metadata: {path}") from exc
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"clip has invalid duration or dimensions: {path}")
    return {
        "duration": round(duration, 6),
        "fps": round(_float_ratio(video.get("r_frame_rate")), 6),
        "width": width,
        "height": height,
        "video_codec": str(video.get("codec_name") or ""),
        "pixel_format": str(video.get("pix_fmt") or ""),
        "has_audio": bool(audio),
        "audio_codec": str(audio.get("codec_name") or "") if audio else "",
        "sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "channels": int(audio.get("channels") or 0) if audio else 0,
    }


def _media_signature(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "duration": round(float(value.get("duration") or 0), 6),
        "fps": round(float(value.get("fps") or 0), 6),
        "width": int(value.get("width") or 0),
        "height": int(value.get("height") or 0),
        "video_codec": str(value.get("video_codec") or ""),
        "pixel_format": str(value.get("pixel_format") or ""),
        "has_audio": bool(value.get("has_audio")),
        "audio_codec": str(value.get("audio_codec") or ""),
        "sample_rate": int(value.get("sample_rate") or 0),
        "channels": int(value.get("channels") or 0),
    }


def generate_contact_sheet(
    clip: Path,
    output: Path,
    *,
    duration: float,
    sample_fps: float,
    max_frames: int,
    thumb_width: int,
    force: bool,
) -> Dict[str, Any]:
    if output.exists() and not force:
        raise ValueError(f"refusing to overwrite contact sheet without --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    effective_fps = min(sample_fps, max_frames / duration)
    effective_fps = max(effective_fps, min(1.0, 1.0 / duration))
    frame_count = max(1, min(max_frames, int(math.ceil(duration * effective_fps))))
    columns = min(8, frame_count)
    rows = int(math.ceil(frame_count / columns))
    filtergraph = (
        f"fps={effective_fps:.9f},scale={thumb_width}:-2,"
        f"tile={columns}x{rows}:padding=4:margin=4:color=black"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(clip),
        "-vf",
        filtergraph,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise ValueError(f"contact sheet generation failed for {clip}: {detail.splitlines()[-1]}")
    return {
        "sample_fps": round(effective_fps, 6),
        "estimated_frames": frame_count,
        "columns": columns,
        "rows": rows,
        "thumb_width": thumb_width,
    }


def _clean_clip_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError(f"invalid clip id: {value!r}")
    return cleaned


def _parse_clip_spec(spec: str) -> Tuple[str, str]:
    if "=" in spec:
        candidate, path = spec.split("=", 1)
        if candidate and "/" not in candidate and "\\" not in candidate and path:
            return _clean_clip_id(candidate), path
    path = Path(spec).expanduser()
    return _clean_clip_id(path.stem), spec


def clips_from_asset_manifest(path: str) -> List[Dict[str, Any]]:
    manifest = _load_json(path)
    items = manifest.get("items") or []
    if not isinstance(items, list):
        raise ValueError("storyboard asset manifest items must be a list")
    clips: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        route = str(item.get("route") or "").strip().lower()
        if route not in GENERATED_VIDEO_ROUTES and str(item.get("kind") or "") != "generated_video":
            continue
        raw_path = str(item.get("resolved_path") or item.get("expected_path") or "").strip()
        if not raw_path:
            continue
        clips.append(
            {
                "clip_id": _clean_clip_id(str(item.get("shot_id") or Path(raw_path).stem)),
                "path": raw_path,
                "shot_id": str(item.get("shot_id") or ""),
                "expected_beat": str(item.get("prompt") or item.get("section") or "").strip(),
                "provider_route": route,
            }
        )
    if not clips:
        raise ValueError("asset manifest contains no generated-video items")
    return clips


def _response_template(request: Mapping[str, Any]) -> Dict[str, Any]:
    reviews = []
    for clip in request.get("clips") or []:
        reviews.append(
            {
                "clip_id": clip.get("clip_id"),
                "verdict": "",
                "story_readability": "",
                "scores": {key: None for key in SCORE_WEIGHTS},
                "hard_fail_codes": [],
                "keep_ranges": [],
                "remove_ranges": [],
                "regenerate": None,
                "prompt_fix": "",
                "notes": "",
            }
        )
    return {
        "version": RESPONSE_VERSION,
        "request_id": request.get("request_id"),
        "reviewed_by": "",
        "reviews": reviews,
    }


def prepare_request(
    clip_specs: Sequence[Mapping[str, Any]],
    *,
    project_dir: str,
    contact_sheet_dir: str,
    sample_fps: float = 2.0,
    max_frames: int = 48,
    thumb_width: int = 320,
    force: bool = False,
) -> Dict[str, Any]:
    if sample_fps <= 0 or sample_fps > 10:
        raise ValueError("sample_fps must be greater than 0 and at most 10")
    if max_frames < 4 or max_frames > 120:
        raise ValueError("max_frames must be between 4 and 120")
    if thumb_width < 160 or thumb_width > 1280:
        raise ValueError("thumb_width must be between 160 and 1280")

    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    sheet_dir = _project_output(contact_sheet_dir, root=root, label="contact sheet directory")
    seen_ids = set()
    seen_paths = set()
    clips: List[Dict[str, Any]] = []

    for raw in clip_specs:
        clip_id = _clean_clip_id(str(raw.get("clip_id") or ""))
        clip = _project_file(str(raw.get("path") or ""), root=root, label=f"clip {clip_id}")
        if clip_id in seen_ids:
            raise ValueError(f"duplicate clip id: {clip_id}")
        if str(clip) in seen_paths:
            raise ValueError(f"duplicate clip path: {clip}")
        seen_ids.add(clip_id)
        seen_paths.add(str(clip))
        media = probe_media(str(clip))
        sheet = sheet_dir / f"{clip_id}_contact_sheet.jpg"
        sampling = generate_contact_sheet(
            clip,
            sheet,
            duration=float(media["duration"]),
            sample_fps=sample_fps,
            max_frames=max_frames,
            thumb_width=thumb_width,
            force=force,
        )
        clips.append(
            {
                "clip_id": clip_id,
                "shot_id": str(raw.get("shot_id") or clip_id),
                "provider_route": str(raw.get("provider_route") or ""),
                "expected_beat": str(raw.get("expected_beat") or "").strip(),
                "path": _relative(clip, root),
                "sha256": _sha256(clip),
                "size_bytes": clip.stat().st_size,
                "media": _media_signature(media),
                "contact_sheet": {
                    "path": _relative(sheet, root),
                    "sha256": _sha256(sheet),
                    **sampling,
                },
            }
        )

    if not clips:
        raise ValueError("at least one generated clip is required")
    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "review_protocol": {
            "passes": [
                "Watch the complete clip once at normal speed with audio.",
                "Watch at 0.25x for face, hands, anatomy, contact, gravity, and object permanence.",
                "Watch once muted so plausible audio cannot hide picture defects.",
                "Listen once without relying on the picture so audio defects remain visible.",
            ],
            "hard_fail_codes": sorted(HARD_FAIL_CODES),
            "score_weights": dict(SCORE_WEIGHTS),
            "thresholds": {
                "pass_minimum": 80,
                "pass_with_edits_minimum": 65,
                "hard_fail_overrides_score": True,
            },
            "limitations": [
                "Contact sheets are sampling aids and do not replace full-speed playback.",
                "Reviewer labels are not identity authentication or digital signatures.",
                "The script validates evidence and decisions; it does not infer visual quality.",
            ],
        },
        "clips": clips,
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = _response_template(request)
    return request


def verify_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    if request.get("version") != REQUEST_VERSION:
        blockers.append(f"request version must be {REQUEST_VERSION}")
    if not root.is_dir():
        blockers.append(f"project directory is missing: {root}")
    expected_id = _request_id(request)
    if str(request.get("request_id") or "") != expected_id:
        blockers.append("request_id does not match canonical request content")

    clips = request.get("clips") or []
    if not isinstance(clips, list) or not clips:
        blockers.append("request must contain at least one clip")
        clips = []
    seen_ids = set()
    for item in clips:
        if not isinstance(item, Mapping):
            blockers.append("request clips must be objects")
            continue
        clip_id = str(item.get("clip_id") or "")
        if not clip_id or clip_id in seen_ids:
            blockers.append(f"duplicate or empty clip id: {clip_id!r}")
            continue
        seen_ids.add(clip_id)
        try:
            clip = _project_file(str(item.get("path") or ""), root=root, label=f"clip {clip_id}")
        except ValueError as exc:
            blockers.append(str(exc))
            continue
        if _sha256(clip) != str(item.get("sha256") or ""):
            blockers.append(f"{clip_id}: clip bytes changed after review preparation")
        if clip.stat().st_size != int(item.get("size_bytes") or -1):
            blockers.append(f"{clip_id}: clip size changed after review preparation")
        try:
            live_media = _media_signature(probe_media(str(clip)))
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            if live_media != _media_signature(item.get("media") or {}):
                blockers.append(f"{clip_id}: clip media contract changed")

        contact = item.get("contact_sheet") or {}
        if not isinstance(contact, Mapping):
            blockers.append(f"{clip_id}: contact_sheet must be an object")
            continue
        try:
            sheet = _project_file(
                str(contact.get("path") or ""),
                root=root,
                label=f"contact sheet for {clip_id}",
            )
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            if _sha256(sheet) != str(contact.get("sha256") or ""):
                blockers.append(f"{clip_id}: contact sheet bytes changed")

    return {
        "status": "blocked" if blockers else "ready",
        "blockers": sorted(set(blockers)),
        "summary": {"clips": len(clips), "blocking": len(set(blockers)), "warnings": 0},
    }


def _score_value(value: Any, *, label: str, errors: List[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        errors.append(f"{label} must be an integer from 1 to 5")
        return 0
    return value


def _normalize_ranges(
    value: Any,
    *,
    label: str,
    duration: float,
    errors: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    ranges: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            errors.append(f"{label}[{index}] must be an object")
            continue
        try:
            start = round(float(item.get("start")), 6)
            end = round(float(item.get("end")), 6)
        except (TypeError, ValueError):
            errors.append(f"{label}[{index}] start/end must be numbers")
            continue
        reason = str(item.get("reason") or "").strip()
        if start < 0 or end <= start or end > duration + RANGE_TOLERANCE:
            errors.append(f"{label}[{index}] must stay inside 0..{duration:.3f}s with end > start")
        if not reason:
            errors.append(f"{label}[{index}] requires a reason")
        ranges.append({"start": start, "end": min(end, duration), "reason": reason})
    ranges.sort(key=lambda item: (item["start"], item["end"]))
    for previous, current in zip(ranges, ranges[1:]):
        if current["start"] < previous["end"] - RANGE_TOLERANCE:
            errors.append(f"{label} ranges must not overlap")
            break
    return ranges


def _ranges_cover_duration(
    keep: Sequence[Mapping[str, Any]],
    remove: Sequence[Mapping[str, Any]],
    duration: float,
) -> bool:
    combined = sorted([*keep, *remove], key=lambda item: (float(item["start"]), float(item["end"])))
    if not combined or abs(float(combined[0]["start"])) > RANGE_TOLERANCE:
        return False
    cursor = 0.0
    for item in combined:
        start = float(item["start"])
        end = float(item["end"])
        if start > cursor + RANGE_TOLERANCE or start < cursor - RANGE_TOLERANCE:
            return False
        cursor = max(cursor, end)
    return abs(cursor - duration) <= RANGE_TOLERANCE


def _audit_review(review: Mapping[str, Any], clip: Mapping[str, Any]) -> Dict[str, Any]:
    clip_id = str(clip.get("clip_id") or "")
    errors: List[str] = []
    verdict = str(review.get("verdict") or "").strip().lower()
    readability = str(review.get("story_readability") or "").strip().lower()
    if verdict not in VERDICTS:
        errors.append(f"verdict must be one of {sorted(VERDICTS)}")
    if readability not in STORY_READABILITY:
        errors.append(f"story_readability must be one of {sorted(STORY_READABILITY)}")

    raw_scores = review.get("scores") or {}
    if not isinstance(raw_scores, Mapping):
        errors.append("scores must be an object")
        raw_scores = {}
    scores = {
        key: _score_value(raw_scores.get(key), label=f"scores.{key}", errors=errors)
        for key in SCORE_WEIGHTS
    }
    unexpected_scores = sorted(set(raw_scores).difference(SCORE_WEIGHTS))
    if unexpected_scores:
        errors.append(f"unknown score keys: {', '.join(unexpected_scores)}")
    weighted_score = round(
        sum(scores[key] / 5.0 * weight for key, weight in SCORE_WEIGHTS.items()),
        1,
    )

    raw_fail_codes = review.get("hard_fail_codes") or []
    if not isinstance(raw_fail_codes, list):
        errors.append("hard_fail_codes must be a list")
        raw_fail_codes = []
    hard_fail_codes = sorted(set(str(code).strip() for code in raw_fail_codes if str(code).strip()))
    unknown_fail_codes = sorted(set(hard_fail_codes).difference(HARD_FAIL_CODES))
    if unknown_fail_codes:
        errors.append(f"unknown hard_fail_codes: {', '.join(unknown_fail_codes)}")

    duration = float((clip.get("media") or {}).get("duration") or 0)
    keep = _normalize_ranges(
        review.get("keep_ranges") or [],
        label="keep_ranges",
        duration=duration,
        errors=errors,
    )
    remove = _normalize_ranges(
        review.get("remove_ranges") or [],
        label="remove_ranges",
        duration=duration,
        errors=errors,
    )
    for kept in keep:
        for removed in remove:
            if max(kept["start"], removed["start"]) < min(kept["end"], removed["end"]) - RANGE_TOLERANCE:
                errors.append("keep_ranges and remove_ranges must not overlap")
                break

    regenerate = review.get("regenerate")
    if not isinstance(regenerate, bool):
        errors.append("regenerate must be true or false")
    prompt_fix = str(review.get("prompt_fix") or "").strip()
    notes = str(review.get("notes") or "").strip()
    if not notes:
        errors.append("notes must summarize the visual review evidence")

    if verdict == "pass":
        if weighted_score < 80:
            errors.append("pass requires weighted_score >= 80")
        if readability != "clear":
            errors.append("pass requires clear story_readability")
        if hard_fail_codes:
            errors.append("pass cannot contain hard_fail_codes")
        if regenerate is not False:
            errors.append("pass requires regenerate=false")
        if remove:
            errors.append("pass cannot contain remove_ranges; use pass_with_edits")
        if keep and not _ranges_cover_duration(keep, [], duration):
            errors.append("pass keep_ranges must cover the complete clip or be empty")
    elif verdict == "pass_with_edits":
        if weighted_score < 65:
            errors.append("pass_with_edits requires weighted_score >= 65")
        if readability == "unclear":
            errors.append("pass_with_edits cannot have unclear story_readability")
        if hard_fail_codes:
            errors.append("hard failures require verdict=fail")
        if regenerate is not False:
            errors.append("pass_with_edits requires regenerate=false")
        if not keep or not remove:
            errors.append("pass_with_edits requires both keep_ranges and remove_ranges")
        elif not _ranges_cover_duration(keep, remove, duration):
            errors.append("pass_with_edits ranges must cover the complete clip without gaps or overlaps")
    elif verdict == "fail":
        if regenerate is not True:
            errors.append("fail requires regenerate=true")
        if not prompt_fix:
            errors.append("fail requires a concrete prompt_fix")

    if hard_fail_codes and verdict != "fail":
        errors.append("hard_fail_codes override score and require verdict=fail")
    if readability == "unclear" and verdict != "fail":
        errors.append("unclear story_readability requires verdict=fail")
    if weighted_score < 65 and verdict != "fail":
        errors.append("weighted_score below 65 requires verdict=fail")

    return {
        "clip_id": clip_id,
        "verdict": verdict,
        "story_readability": readability,
        "scores": scores,
        "weighted_score": weighted_score,
        "hard_fail_codes": hard_fail_codes,
        "keep_ranges": keep,
        "remove_ranges": remove,
        "regenerate": regenerate,
        "prompt_fix": prompt_fix,
        "notes": notes,
        "validation_errors": sorted(set(errors)),
    }


def build_report(request: Mapping[str, Any], response: Mapping[str, Any]) -> Dict[str, Any]:
    request_check = verify_request(request)
    blockers = list(request_check["blockers"])
    warnings: List[str] = []
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if str(response.get("request_id") or "") != str(request.get("request_id") or ""):
        blockers.append("response request_id does not match the prepared request")
    reviewed_by = str(response.get("reviewed_by") or "").strip()
    if not reviewed_by:
        blockers.append("reviewed_by is required (label only; not identity authentication)")

    clips = {
        str(item.get("clip_id") or ""): item
        for item in request.get("clips") or []
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
        clip_id = str(item.get("clip_id") or "")
        if clip_id in provided:
            blockers.append(f"duplicate response review for {clip_id}")
        provided[clip_id] = item
    for missing in sorted(set(clips).difference(provided)):
        blockers.append(f"missing response review for {missing}")
    for extra in sorted(set(provided).difference(clips)):
        blockers.append(f"response contains unknown clip id {extra}")

    reviews: List[Dict[str, Any]] = []
    for clip_id, clip in clips.items():
        review = _audit_review(provided.get(clip_id, {}), clip)
        reviews.append(review)
        if review["validation_errors"]:
            blockers.extend(f"{clip_id}: {error}" for error in review["validation_errors"])
        elif review["verdict"] == "fail":
            blockers.append(f"{clip_id}: generated clip requires regeneration")
        elif review["verdict"] == "pass_with_edits":
            warnings.append(f"{clip_id}: use only the approved keep_ranges before assembly")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    summary = {
        "clips": len(clips),
        "pass": sum(1 for item in reviews if item["verdict"] == "pass" and not item["validation_errors"]),
        "pass_with_edits": sum(
            1 for item in reviews if item["verdict"] == "pass_with_edits" and not item["validation_errors"]
        ),
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
            "This artifact binds review decisions to clip and contact-sheet bytes.",
            "It does not authenticate the reviewer or replace full-speed audiovisual review.",
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
        "# Generated Clip Review Request",
        "",
        f"- Request ID: `{request.get('request_id', '')}`",
        f"- Clips: {len(request.get('clips') or [])}",
        "- Scope: generated clips before final assembly",
        "",
        "## Required review passes",
        "",
    ]
    for item in (request.get("review_protocol") or {}).get("passes") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Clips",
            "",
            "| clip | duration | expected beat | source | contact sheet |",
            "|---|---:|---|---|---|",
        ]
    )
    for clip in request.get("clips") or []:
        expected = str(clip.get("expected_beat") or "").replace("|", "/")
        lines.append(
            f"| {clip.get('clip_id', '')} | {float((clip.get('media') or {}).get('duration') or 0):.3f}s "
            f"| {expected} | `{clip.get('path', '')}` | `{(clip.get('contact_sheet') or {}).get('path', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Decision rules",
            "",
            "- `pass`: weighted score >= 80, story is clear, no hard fail, no removal needed.",
            "- `pass_with_edits`: weighted score >= 65; keep/remove ranges must cover the whole clip.",
            "- `fail`: set `regenerate=true` and provide a concrete `prompt_fix`.",
            "- Any hard-fail code or unclear story overrides the numeric score and requires `fail`.",
            "- Contact sheets are orientation aids only; watch and listen to the complete clip before deciding.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_report_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Generated Clip Review",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Reviewed by: `{(report.get('response') or {}).get('reviewed_by', '')}`",
        f"- Clips: {summary.get('clips', 0)}",
        f"- Pass / edit / fail: {summary.get('pass', 0)} / {summary.get('pass_with_edits', 0)} / {summary.get('fail', 0)}",
        f"- Blocking / warnings: {summary.get('blocking', 0)} / {summary.get('warnings', 0)}",
        "",
        "| clip | verdict | score | readability | hard fails | keep/remove |",
        "|---|---|---:|---|---|---:|",
    ]
    for review in report.get("reviews") or []:
        lines.append(
            "| {clip} | {verdict} | {score:.1f} | {readability} | {fails} | {keep}/{remove} |".format(
                clip=review.get("clip_id", ""),
                verdict=review.get("verdict", ""),
                score=float(review.get("weighted_score") or 0),
                readability=review.get("story_readability", ""),
                fails=", ".join(review.get("hard_fail_codes") or []) or "—",
                keep=len(review.get("keep_ranges") or []),
                remove=len(review.get("remove_ranges") or []),
            )
        )
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.get("warnings") or [])
    lines.extend(
        [
            "",
            "A ready report is not a signature. Re-run `generated_clip_review.py verify` after any clip, contact-sheet, or report change.",
            "",
        ]
    )
    return "\n".join(lines)


def _collect_clip_specs(args: argparse.Namespace) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for raw in args.clip or []:
        clip_id, path = _parse_clip_spec(raw)
        specs.append({"clip_id": clip_id, "path": path, "shot_id": clip_id})
    if args.asset_manifest:
        specs.extend(clips_from_asset_manifest(args.asset_manifest))
    return specs


def _prepare_command(args: argparse.Namespace) -> int:
    specs = _collect_clip_specs(args)
    root = Path(args.project_dir).expanduser().resolve()
    request = prepare_request(
        specs,
        project_dir=args.project_dir,
        contact_sheet_dir=args.contact_sheet_dir,
        sample_fps=args.sample_fps,
        max_frames=args.max_frames,
        thumb_width=args.thumb_width,
        force=args.force,
    )
    output = _project_output(args.output, root=root, label="review request output")
    _write_json(output, request, force=args.force)
    if args.markdown:
        markdown = _project_output(args.markdown, root=root, label="request Markdown output")
        _write_text(markdown, emit_request_markdown(request), force=args.force)
    if args.response_template:
        response_template = _project_output(
            args.response_template,
            root=root,
            label="response template output",
        )
        _write_json(
            response_template,
            request["response_template"],
            force=args.force,
        )
    print(
        f"Generated clip review request: {args.output}; clips={len(request['clips'])} "
        f"request_id={request['request_id']}"
    )
    return 0


def _audit_command(args: argparse.Namespace) -> int:
    request = _load_json(args.request)
    response = _load_json(args.response)
    report = build_report(request, response)
    root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    output = _project_output(args.output, root=root, label="review report output")
    _write_json(output, report, force=args.force)
    if args.markdown:
        markdown = _project_output(args.markdown, root=root, label="report Markdown output")
        _write_text(markdown, emit_report_markdown(report), force=args.force)
    summary = report["summary"]
    print(
        f"Generated clip review audit: status={report['status']} clips={summary['clips']} "
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
        description="Prepare, audit, and live-verify reviews for generated video clips."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create contact sheets and a source-bound review request.")
    prepare.add_argument("--project-dir", default=".", help="Project root; clips and outputs must stay inside it.")
    prepare.add_argument(
        "--clip",
        action="append",
        default=[],
        help="Generated clip as [clip_id=]path; repeat for multiple clips.",
    )
    prepare.add_argument("--asset-manifest", help="Optional storyboard_assets.json with ready generated videos.")
    prepare.add_argument("--contact-sheet-dir", required=True, help="Directory for generated contact-sheet JPEGs.")
    prepare.add_argument("--sample-fps", type=float, default=2.0, help="Requested sampling rate, capped by max frames.")
    prepare.add_argument("--max-frames", type=int, default=48, help="Maximum sampled frames per clip (4-120).")
    prepare.add_argument("--thumb-width", type=int, default=320, help="Contact-sheet cell width (160-1280).")
    prepare.add_argument("--output", required=True, help="Output generated_clip_review_request.json.")
    prepare.add_argument("--markdown", help="Optional Markdown review request.")
    prepare.add_argument("--response-template", help="Optional blank response JSON to fill during review.")
    prepare.add_argument("--force", action="store_true", help="Replace request/contact-sheet outputs.")
    prepare.set_defaults(func=_prepare_command)

    audit = subparsers.add_parser("audit", help="Validate a completed review response and write the live gate artifact.")
    audit.add_argument("--request", required=True, help="Prepared generated_clip_review_request.json.")
    audit.add_argument("--response", required=True, help="Completed generated_clip_review_response.json.")
    audit.add_argument("--output", required=True, help="Output generated_clip_review.json.")
    audit.add_argument("--markdown", help="Optional Markdown audit report.")
    audit.add_argument("--strict", action="store_true", help="Exit 2 when any clip is invalid or requires regeneration.")
    audit.add_argument("--force", action="store_true", help="Replace audit outputs.")
    audit.set_defaults(func=_audit_command)

    verify = subparsers.add_parser("verify", help="Recompute the audit and detect stale clips or tampering.")
    verify.add_argument("--report", required=True, help="generated_clip_review.json to verify.")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when live verification is blocked.")
    verify.set_defaults(func=_verify_command)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"generated_clip_review: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
