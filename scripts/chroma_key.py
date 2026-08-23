#!/usr/bin/env python3
"""Prepare, review, apply, and verify source-bound chroma-key composites.

The workflow renders representative composite and matte frames before a full
encode.  A reviewer must explicitly pass edge quality, subject integrity,
spill control, and background fit.  Source, background, preview, and final
output bytes are bound to one report so stale approval fails closed.
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from generated_clip_review import probe_media


VERSION = "chroma_key.v1"
REVIEW_CHECKS = (
    "edge_quality",
    "subject_integrity",
    "spill_control",
    "background_fit",
)
REVIEW_VALUES = {"pass", "fail"}
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
REQUIRED_FILTERS = {"alphaextract", "chromakey", "overlay"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def canonical_report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {key: value for key, value in report.items() if key not in {"generated_at", "report_id"}}
    )


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
    path = _lexical_project_path(raw_path, root=root, label=label).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


def _project_output(raw_path: str, *, root: Path, label: str) -> Path:
    return _lexical_project_path(raw_path, root=root, label=label).resolve()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _fingerprint(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _ensure_distinct_paths(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (label, path) in enumerate(items):
        for other_label, other in items[:index]:
            if path == other or _same_existing_file(path, other):
                raise ValueError(f"{label} must not overwrite {other_label}: {path}")


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def _run_checked(command: Sequence[str]) -> None:
    result = _run(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ValueError(detail.splitlines()[-1])


def _available_filters() -> Set[str]:
    result = _run(["ffmpeg", "-hide_banner", "-filters"])
    if result.returncode != 0:
        raise ValueError((result.stderr or "could not list FFmpeg filters").strip())
    filters: Set[str] = set()
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        fields = line.split()
        if len(fields) >= 2 and re.fullmatch(r"[TSC.]{2,3}", fields[0]):
            filters.add(fields[1])
    return filters


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


def probe_background(path: str) -> Dict[str, Any]:
    candidate = Path(path)
    if candidate.suffix.lower() not in IMAGE_SUFFIXES:
        return {"kind": "video", **_media_signature(probe_media(path))}
    result = _run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,pix_fmt",
        "-of",
        "json",
        path,
    ])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(f"ffprobe failed for {path}: {detail.splitlines()[-1]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next(
        (stream for stream in payload.get("streams") or [] if stream.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, Mapping):
        raise ValueError(f"background image has no decodable visual stream: {path}")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"background image has invalid dimensions: {path}")
    return {
        "kind": "image",
        "duration": 0.0,
        "fps": 0.0,
        "width": width,
        "height": height,
        "video_codec": str(video.get("codec_name") or ""),
        "pixel_format": str(video.get("pix_fmt") or ""),
        "has_audio": False,
        "audio_codec": "",
        "sample_rate": 0,
        "channels": 0,
    }


def _background_signature(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {"kind": str(value.get("kind") or ""), **_media_signature(value)}


def _normalize_key_color(value: str) -> Tuple[str, str, bool]:
    raw = str(value).strip().lower()
    if raw in {"green", "#00ff00", "0x00ff00", "00ff00"}:
        return "0x00FF00", "green", False
    if raw in {"blue", "#0000ff", "0x0000ff", "0000ff"}:
        return "0x0000FF", "blue", False
    match = re.fullmatch(r"(?:#|0x)?([0-9a-f]{6})", raw)
    if not match:
        raise ValueError("key color must be green, blue, or a six-digit RGB hex value")
    digits = match.group(1).upper()
    red, green, blue = (int(digits[index:index + 2], 16) for index in (0, 2, 4))
    screen_type = "green" if green >= blue and green >= red else "blue"
    return f"0x{digits}", screen_type, True


def _bounded_float(value: Any, *, label: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return round(number, 6)


def select_sample_times(duration: float, requested: Sequence[float] = ()) -> List[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("foreground duration must be positive")
    raw = list(requested) if requested else [duration * 0.15, duration * 0.50, duration * 0.85]
    if not raw or len(raw) > 6:
        raise ValueError("supply between one and six preview times")
    limit = max(0.0, duration - min(0.04, duration / 2))
    times: List[float] = []
    for value in raw:
        number = _bounded_float(value, label="preview time", minimum=0.0, maximum=duration)
        times.append(round(min(number, limit), 3))
    if len(times) != len(set(times)):
        raise ValueError("preview times collapse to duplicate source frames")
    return times


def _settings(
    *,
    key_color: str,
    similarity: float,
    blend: float,
    despill: float,
    sample_times: Sequence[float],
) -> Dict[str, Any]:
    color, screen_type, custom = _normalize_key_color(key_color)
    return {
        "key_color": color,
        "screen_type": screen_type,
        "custom_key_color": custom,
        "similarity": _bounded_float(similarity, label="similarity", minimum=0.00001, maximum=1.0),
        "blend": _bounded_float(blend, label="blend", minimum=0.0, maximum=1.0),
        "despill": _bounded_float(despill, label="despill", minimum=0.0, maximum=1.0),
        "sample_times": [round(float(value), 3) for value in sample_times],
    }


def _key_chain(settings: Mapping[str, Any], *, label: str = "fg") -> str:
    chain = (
        "format=yuva444p,"
        f"chromakey={settings['key_color']}:{float(settings['similarity']):.6f}:"
        f"{float(settings['blend']):.6f}"
    )
    if float(settings.get("despill") or 0) > 0:
        chain += (
            f",despill=type={settings['screen_type']}:"
            f"mix={float(settings['despill']):.6f}"
        )
    return f"[0:v]{chain},setsar=1[{label}]"


def build_composite_filter(settings: Mapping[str, Any], media: Mapping[str, Any]) -> str:
    width = int(media["width"])
    height = int(media["height"])
    fps = float(media["fps"])
    background = (
        f"[1:v]fps={fps:.6f},scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[bg]"
    )
    foreground = _key_chain(settings)
    composite = "[bg][fg]overlay=shortest=1:format=auto,format=yuv420p[v]"
    return ";".join((background, foreground, composite))


def build_matte_filter(settings: Mapping[str, Any]) -> str:
    return (
        "[0:v]format=yuva444p,"
        f"chromakey={settings['key_color']}:{float(settings['similarity']):.6f}:"
        f"{float(settings['blend']):.6f},alphaextract,format=gray[matte]"
    )


def _background_input(path: Path, background: Mapping[str, Any]) -> List[str]:
    if background.get("kind") == "image":
        return ["-loop", "1", "-i", str(path)]
    return ["-stream_loop", "-1", "-i", str(path)]


def _render_preview_pair(
    foreground: Path,
    background_path: Path,
    *,
    background: Mapping[str, Any],
    settings: Mapping[str, Any],
    media: Mapping[str, Any],
    time_s: float,
    composite_path: Path,
    matte_path: Path,
) -> None:
    composite_command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{time_s:.3f}",
        "-i", str(foreground),
        *_background_input(background_path, background),
        "-filter_complex", build_composite_filter(settings, media),
        "-map", "[v]", "-frames:v", "1", "-y", str(composite_path),
    ]
    matte_command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{time_s:.3f}",
        "-i", str(foreground),
        "-filter_complex", build_matte_filter(settings),
        "-map", "[matte]", "-frames:v", "1", "-y", str(matte_path),
    ]
    _run_checked(composite_command)
    _run_checked(matte_command)


def _preview_paths(preview_dir: Path, times: Sequence[float]) -> List[Tuple[str, float, str, Path]]:
    records: List[Tuple[str, float, str, Path]] = []
    for index, time_s in enumerate(times, start=1):
        stem = f"chroma-key-{index:02d}-{time_s:.3f}s".replace(".", "_")
        records.append((f"sample-{index:02d}-composite", time_s, "composite", preview_dir / f"{stem}-composite.png"))
        records.append((f"sample-{index:02d}-matte", time_s, "matte", preview_dir / f"{stem}-matte.png"))
    return records


def _render_previews(
    foreground: Path,
    background_path: Path,
    *,
    root: Path,
    preview_dir: Path,
    background: Mapping[str, Any],
    settings: Mapping[str, Any],
    media: Mapping[str, Any],
    force: bool,
) -> List[Dict[str, Any]]:
    planned = _preview_paths(preview_dir, settings["sample_times"])
    existing = [path for _, _, _, path in planned if path.exists()]
    if existing and not force:
        raise ValueError(f"refusing to overwrite existing preview without --force: {existing[0]}")
    preview_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chroma-key-previews-", dir=str(preview_dir.parent)) as temp_name:
        temp = Path(temp_name)
        staged: Dict[str, Path] = {}
        for index, time_s in enumerate(settings["sample_times"], start=1):
            composite = temp / f"sample-{index:02d}-composite.png"
            matte = temp / f"sample-{index:02d}-matte.png"
            _render_preview_pair(
                foreground,
                background_path,
                background=background,
                settings=settings,
                media=media,
                time_s=float(time_s),
                composite_path=composite,
                matte_path=matte,
            )
            staged[f"sample-{index:02d}-composite"] = composite
            staged[f"sample-{index:02d}-matte"] = matte
        preview_dir.mkdir(parents=True, exist_ok=True)
        result: List[Dict[str, Any]] = []
        for preview_id, time_s, kind, final_path in planned:
            temporary = staged[preview_id]
            os.replace(temporary, final_path)
            result.append({
                "id": preview_id,
                "time": round(float(time_s), 3),
                "kind": kind,
                **_fingerprint(final_path, root=root),
            })
    return result


def _computed_warnings(report: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    foreground = report.get("foreground") if isinstance(report.get("foreground"), Mapping) else {}
    background = report.get("background") if isinstance(report.get("background"), Mapping) else {}
    if settings.get("custom_key_color"):
        warnings.append("custom key color uses inferred green/blue despill family; inspect the matte and colored edges carefully")
    def number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    if number(settings.get("similarity")) > 0.35:
        warnings.append("high chromakey similarity can remove clothing, props, or skin-adjacent colors")
    if number(settings.get("blend")) > 0.30:
        warnings.append("high chromakey blend can create soft halos around the subject")
    if number(settings.get("despill")) > 0.85:
        warnings.append("strong despill can distort legitimate green or blue subject colors")
    if not bool((foreground.get("media") or {}).get("has_audio")):
        warnings.append("foreground has no audio; the composite will be silent because background audio is ignored")
    if (background.get("media") or {}).get("kind") == "video":
        warnings.append("background video loops from its first frame for the foreground duration; review the loop seam")
    return warnings


def _review_blockers(report: Mapping[str, Any]) -> List[str]:
    review = report.get("review")
    if not isinstance(review, Mapping):
        return ["preview review has not been recorded"]
    blockers: List[str] = []
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    if set(checks) != set(REVIEW_CHECKS):
        blockers.append("preview review must cover every required check exactly once")
    for check in REVIEW_CHECKS:
        value = checks.get(check)
        if value not in REVIEW_VALUES:
            blockers.append(f"preview review has invalid {check}: {value!r}")
        elif value != "pass":
            blockers.append(f"preview review failed {check}")
    if not str(review.get("reviewer") or "").strip():
        blockers.append("preview review requires a reviewer label")
    if not str(review.get("note") or "").strip():
        blockers.append("preview review requires a non-empty evidence note")
    expected_decision = "approve" if not any(value != "pass" for value in checks.values()) and set(checks) == set(REVIEW_CHECKS) else "reject"
    if review.get("decision") != expected_decision:
        blockers.append("preview review decision does not match its check results")
    return blockers


def _derive_state(
    report: Mapping[str, Any],
    *,
    integrity_blockers: Sequence[str] = (),
    integrity_warnings: Sequence[str] = (),
) -> Dict[str, Any]:
    blockers = list(integrity_blockers) + _review_blockers(report)
    review = report.get("review") if isinstance(report.get("review"), Mapping) else {}
    if review.get("decision") == "approve" and not report.get("application"):
        blockers.append("approved chroma-key composite has not been rendered")
    warnings = list(integrity_warnings) + _computed_warnings(report)
    previews = report.get("previews") if isinstance(report.get("previews"), list) else []
    summary = {
        "preview_frames": len(previews),
        "reviewed": 1 if report.get("review") else 0,
        "applied": 1 if report.get("application") else 0,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
    }


def _refresh(report: Mapping[str, Any]) -> Dict[str, Any]:
    updated = dict(report)
    for key in ("blockers", "warnings", "summary", "status", "report_id"):
        updated.pop(key, None)
    updated.update(_derive_state(updated))
    updated["report_id"] = canonical_report_id(updated)
    return updated


def prepare_report(
    foreground_path: str,
    background_path: str,
    *,
    project_dir: str,
    output_video: str,
    preview_dir: str = "verify/chroma_key",
    key_color: str = "green",
    similarity: float = 0.10,
    blend: float = 0.08,
    despill: float = 0.50,
    times: Sequence[float] = (),
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    foreground = _project_file(foreground_path, root=root, label="foreground video")
    background_file = _project_file(background_path, root=root, label="background media")
    destination = _project_output(output_video, root=root, label="output video")
    previews = _project_output(preview_dir, root=root, label="preview directory")
    if previews in {foreground, background_file} or previews in foreground.parents or previews in background_file.parents:
        raise ValueError("preview directory must not contain a source file")

    media = _media_signature(probe_media(str(foreground)))
    background_media = _background_signature(probe_background(str(background_file)))
    sample_times = select_sample_times(float(media["duration"]), times)
    normalized = _settings(
        key_color=key_color,
        similarity=similarity,
        blend=blend,
        despill=despill,
        sample_times=sample_times,
    )
    required_filters = set(REQUIRED_FILTERS)
    if normalized["despill"] > 0:
        required_filters.add("despill")
    missing = sorted(required_filters - _available_filters())
    if missing:
        raise ValueError(f"FFmpeg is missing required chroma-key filter(s): {', '.join(missing)}")

    planned_previews = _preview_paths(previews, sample_times)
    protected = {
        "foreground video": foreground,
        "background media": background_file,
        "output video": destination,
        **{f"preview {preview_id}": path for preview_id, _, _, path in planned_previews},
    }
    _ensure_distinct_paths(protected)
    preview_records = _render_previews(
        foreground,
        background_file,
        root=root,
        preview_dir=previews,
        background=background_media,
        settings=normalized,
        media=media,
        force=force,
    )
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "foreground": {**_fingerprint(foreground, root=root), "media": media},
        "background": {**_fingerprint(background_file, root=root), "media": background_media},
        "settings": normalized,
        "filter_contract": {
            "required_filters": sorted(required_filters),
            "composite_sha256": hashlib.sha256(build_composite_filter(normalized, media).encode("utf-8")).hexdigest(),
            "matte_sha256": hashlib.sha256(build_matte_filter(normalized).encode("utf-8")).hexdigest(),
            "background_policy": "loop_video_or_hold_image",
            "audio_policy": "foreground_only",
        },
        "previews": preview_records,
        "output_target": {"path": _relative(destination, root)},
        "review": None,
        "application": None,
        "limits": [
            "This workflow only keys a known green, blue, or custom screen color; it is not AI subject matting.",
            "Representative previews cannot prove every frame; watch the full composite at 1x after apply.",
            "Reviewer labels and SHA-256 digests are not identity authentication or digital signatures.",
        ],
    }
    return _refresh(report)


def _static_checks(report: Mapping[str, Any], project_dir: Optional[str] = None) -> Tuple[List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported chroma-key report version: {report.get('version')!r}")
    raw_root = report.get("project_dir")
    if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
        blockers.append("project_dir must be a non-empty absolute path")
    root = Path(str(raw_root or ".")).expanduser().resolve()
    if project_dir is not None:
        expected = Path(project_dir).expanduser().resolve()
        if root != expected:
            blockers.append("report project_dir does not match the verification project")
        root = expected
    if not root.is_dir():
        blockers.append(f"project directory does not exist: {root}")

    foreground_record = report.get("foreground") if isinstance(report.get("foreground"), Mapping) else {}
    background_record = report.get("background") if isinstance(report.get("background"), Mapping) else {}
    settings_record = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    filter_record = report.get("filter_contract") if isinstance(report.get("filter_contract"), Mapping) else {}
    foreground: Optional[Path] = None
    background_file: Optional[Path] = None
    media: Dict[str, Any] = {}
    background_media: Dict[str, Any] = {}

    try:
        foreground = _project_file(str(foreground_record.get("path") or ""), root=root, label="foreground video")
        current = _fingerprint(foreground, root=root)
        expected = {key: foreground_record.get(key) for key in ("path", "sha256", "size_bytes")}
        if current != expected:
            blockers.append("foreground video bytes changed after chroma-key previews were rendered")
        media = _media_signature(probe_media(str(foreground)))
        if media != foreground_record.get("media"):
            blockers.append("foreground video media contract changed after chroma-key previews were rendered")
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(f"foreground verification failed: {exc}")

    try:
        background_file = _project_file(str(background_record.get("path") or ""), root=root, label="background media")
        current = _fingerprint(background_file, root=root)
        expected = {key: background_record.get(key) for key in ("path", "sha256", "size_bytes")}
        if current != expected:
            blockers.append("background media bytes changed after chroma-key previews were rendered")
        background_media = _background_signature(probe_background(str(background_file)))
        if background_media != background_record.get("media"):
            blockers.append("background media contract changed after chroma-key previews were rendered")
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(f"background verification failed: {exc}")

    try:
        sample_times = select_sample_times(float(media.get("duration") or 0), settings_record.get("sample_times") or [])
        normalized = _settings(
            key_color=str(settings_record.get("key_color") or ""),
            similarity=settings_record.get("similarity"),
            blend=settings_record.get("blend"),
            despill=settings_record.get("despill"),
            sample_times=sample_times,
        )
        if normalized != settings_record:
            blockers.append("stored chroma-key settings are non-canonical or internally inconsistent")
    except (TypeError, ValueError) as exc:
        normalized = {}
        blockers.append(f"invalid chroma-key settings: {exc}")

    if normalized and media:
        expected_composite = hashlib.sha256(build_composite_filter(normalized, media).encode("utf-8")).hexdigest()
        expected_matte = hashlib.sha256(build_matte_filter(normalized).encode("utf-8")).hexdigest()
        if filter_record.get("composite_sha256") != expected_composite:
            blockers.append("composite filter contract changed after preview review")
        if filter_record.get("matte_sha256") != expected_matte:
            blockers.append("matte filter contract changed after preview review")
        required = set(REQUIRED_FILTERS)
        if float(normalized.get("despill") or 0) > 0:
            required.add("despill")
        if filter_record.get("required_filters") != sorted(required):
            blockers.append("required FFmpeg filter contract is non-canonical")
        try:
            missing = sorted(required - _available_filters())
            if missing:
                blockers.append(f"FFmpeg is missing required chroma-key filter(s): {', '.join(missing)}")
        except ValueError as exc:
            blockers.append(str(exc))

    previews = report.get("previews") if isinstance(report.get("previews"), list) else []
    expected_preview_keys = {
        (preview_id, round(float(time_s), 3), kind)
        for preview_id, time_s, kind, _ in _preview_paths(
            Path("unused"), normalized.get("sample_times") if normalized else []
        )
    }
    actual_preview_keys: Set[Tuple[str, float, str]] = set()
    for item in previews:
        if not isinstance(item, Mapping):
            blockers.append("preview entry must be an object")
            continue
        try:
            key = (str(item.get("id") or ""), round(float(item.get("time")), 3), str(item.get("kind") or ""))
        except (TypeError, ValueError):
            blockers.append("preview entry has invalid id, time, or kind")
            continue
        if key in actual_preview_keys:
            blockers.append(f"duplicate preview entry: {key[0]}")
            continue
        actual_preview_keys.add(key)
        try:
            preview_path = _project_file(str(item.get("path") or ""), root=root, label=f"preview {key[0]}")
            current = _fingerprint(preview_path, root=root)
            expected = {field: item.get(field) for field in ("path", "sha256", "size_bytes")}
            if current != expected:
                blockers.append(f"preview bytes changed after review: {key[0]}")
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(f"preview verification failed for {key[0]}: {exc}")
    if actual_preview_keys != expected_preview_keys:
        blockers.append("preview coverage does not match the stored sample times")

    output_record = report.get("output_target") if isinstance(report.get("output_target"), Mapping) else {}
    try:
        output_path = _project_output(str(output_record.get("path") or ""), root=root, label="output video")
        protected_outputs: Dict[str, Optional[Path]] = {
            "foreground video": foreground,
            "background media": background_file,
            "output video": output_path,
        }
        for item in previews:
            if isinstance(item, Mapping) and item.get("path"):
                protected_outputs[f"preview {item.get('id') or item.get('path')}"] = _project_output(
                    str(item["path"]), root=root, label="preview path"
                )
        _ensure_distinct_paths(
            {key: value for key, value in protected_outputs.items() if value is not None}
        )
    except (OSError, TypeError, ValueError) as exc:
        output_path = None
        blockers.append(f"output target verification failed: {exc}")

    application = report.get("application")
    if application is not None:
        if not isinstance(application, Mapping):
            blockers.append("application must be an object or null")
        elif output_path is None:
            blockers.append("applied output cannot be verified without a valid output target")
        else:
            try:
                current = _fingerprint(_project_file(str(application.get("path") or ""), root=root, label="applied output"), root=root)
                expected = {field: application.get(field) for field in ("path", "sha256", "size_bytes")}
                if current != expected or application.get("path") != output_record.get("path"):
                    blockers.append("applied output bytes or target path changed")
                current_media = _media_signature(probe_media(str(output_path)))
                if current_media != application.get("media"):
                    blockers.append("applied output media contract changed")
                if media:
                    tolerance = max(0.15, 2.0 / max(float(media.get("fps") or 1), 1.0))
                    if abs(current_media["duration"] - media["duration"]) > tolerance:
                        blockers.append("applied output duration does not match the foreground")
                    if (current_media["width"], current_media["height"]) != (media["width"], media["height"]):
                        blockers.append("applied output dimensions do not match the foreground")
                    if abs(current_media["fps"] - media["fps"]) > 0.02:
                        blockers.append("applied output frame rate does not match the foreground")
                    if current_media["has_audio"] != media["has_audio"]:
                        blockers.append("applied output audio presence does not match the foreground-only policy")
                if current_media["video_codec"] != "h264" or current_media["pixel_format"] != "yuv420p":
                    blockers.append("applied output must be H.264 yuv420p")
            except (OSError, TypeError, ValueError) as exc:
                blockers.append(f"applied output verification failed: {exc}")

    if report.get("report_id") != canonical_report_id(report):
        blockers.append("canonical chroma-key report id changed")
    return blockers, warnings


def verify_report(report: Mapping[str, Any], project_dir: Optional[str] = None) -> Dict[str, Any]:
    blockers, warnings = _static_checks(report, project_dir)
    verification = dict(report)
    derived = _derive_state(report, integrity_blockers=blockers, integrity_warnings=warnings)
    stored = {key: report.get(key) for key in ("blockers", "warnings", "summary", "status")}
    expected = {key: derived[key] for key in stored}
    if stored != expected:
        derived["blockers"].append("stored chroma-key status fields are stale or tampered")
        derived["summary"] = {
            **derived["summary"],
            "blocking": len(derived["blockers"]),
        }
        derived["status"] = "blocked"
    verification.update(derived)
    return verification


def record_review(
    report: Mapping[str, Any],
    *,
    reviewer: str,
    note: str,
    edge_quality: str,
    subject_integrity: str,
    spill_control: str,
    background_fit: str,
) -> Dict[str, Any]:
    blockers, _ = _static_checks(report)
    if blockers:
        raise ValueError(f"cannot review an invalid or stale report: {blockers[0]}")
    if report.get("application"):
        raise ValueError("cannot replace review after apply; prepare a new report and output target")
    checks = {
        "edge_quality": edge_quality,
        "subject_integrity": subject_integrity,
        "spill_control": spill_control,
        "background_fit": background_fit,
    }
    invalid = [f"{key}={value!r}" for key, value in checks.items() if value not in REVIEW_VALUES]
    if invalid:
        raise ValueError(f"review checks must be pass or fail: {', '.join(invalid)}")
    reviewer = str(reviewer).strip()
    note = " ".join(str(note).split())
    if not reviewer or not note:
        raise ValueError("reviewer and evidence note must be non-empty")
    updated = dict(report)
    updated["review"] = {
        "reviewed_at": utc_now(),
        "reviewer": reviewer,
        "note": note,
        "checks": checks,
        "decision": "approve" if all(value == "pass" for value in checks.values()) else "reject",
    }
    return _refresh(updated)


def _render_composite(
    foreground: Path,
    background_path: Path,
    destination: Path,
    *,
    background: Mapping[str, Any],
    settings: Mapping[str, Any],
    media: Mapping[str, Any],
) -> None:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(foreground),
        *_background_input(background_path, background),
        "-filter_complex", build_composite_filter(settings, media),
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-r", f"{float(media['fps']):.6f}",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{float(media['duration']):.6f}", "-y", str(destination),
    ]
    _run_checked(command)


def apply_report(report: Mapping[str, Any], *, force: bool = False) -> Dict[str, Any]:
    blockers, _ = _static_checks(report)
    if blockers:
        raise ValueError(f"cannot apply an invalid or stale report: {blockers[0]}")
    review_blockers = _review_blockers(report)
    if review_blockers:
        raise ValueError(f"cannot apply before a passing preview review: {review_blockers[0]}")
    if report.get("application"):
        raise ValueError("report already contains an applied output; prepare a new report to rerender")
    root = Path(str(report["project_dir"])).resolve()
    foreground = _project_file(str((report.get("foreground") or {}).get("path") or ""), root=root, label="foreground video")
    background_path = _project_file(str((report.get("background") or {}).get("path") or ""), root=root, label="background media")
    destination = _project_output(str((report.get("output_target") or {}).get("path") or ""), root=root, label="output video")
    if destination.exists() and not force:
        raise ValueError(f"refusing to overwrite existing output without --force: {destination}")
    _ensure_distinct_paths({
        "foreground video": foreground,
        "background media": background_path,
        "output video": destination,
    })
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix or ".mp4", dir=str(destination.parent)
    )
    os.close(fd)
    os.unlink(temporary_name)
    temporary = Path(temporary_name)
    try:
        media = (report.get("foreground") or {}).get("media") or {}
        background = (report.get("background") or {}).get("media") or {}
        settings = report.get("settings") or {}
        _render_composite(
            foreground,
            background_path,
            temporary,
            background=background,
            settings=settings,
            media=media,
        )
        output_media = _media_signature(probe_media(str(temporary)))
        tolerance = max(0.15, 2.0 / max(float(media.get("fps") or 1), 1.0))
        if abs(output_media["duration"] - float(media["duration"])) > tolerance:
            raise ValueError("rendered output duration does not match the foreground")
        if (output_media["width"], output_media["height"]) != (media["width"], media["height"]):
            raise ValueError("rendered output dimensions do not match the foreground")
        if output_media["has_audio"] != bool(media.get("has_audio")):
            raise ValueError("rendered output audio presence violates the foreground-only policy")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    updated = dict(report)
    updated["application"] = {
        "applied_at": utc_now(),
        **_fingerprint(destination, root=root),
        "media": _media_signature(probe_media(str(destination))),
    }
    return _refresh(updated)


def emit_markdown(report: Mapping[str, Any]) -> str:
    settings = report.get("settings") or {}
    review = report.get("review") or {}
    lines = [
        "# Chroma-key Review",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Foreground: `{(report.get('foreground') or {}).get('path', '')}`",
        f"- Background: `{(report.get('background') or {}).get('path', '')}`",
        f"- Output: `{(report.get('output_target') or {}).get('path', '')}`",
        f"- Key: `{settings.get('key_color')}` / similarity `{settings.get('similarity')}` / blend `{settings.get('blend')}` / despill `{settings.get('despill')}`",
        f"- Review decision: `{review.get('decision') or 'pending'}`",
        "",
        "## Preview Evidence",
        "",
        "| Source time | Composite | Matte |",
        "|---:|---|---|",
    ]
    previews = report.get("previews") or []
    by_time: Dict[float, Dict[str, str]] = {}
    for item in previews:
        bucket = by_time.setdefault(round(float(item.get("time") or 0), 3), {})
        bucket[str(item.get("kind") or "")] = str(item.get("path") or "")
    for time_s, bucket in sorted(by_time.items()):
        lines.append(f"| {time_s:.3f}s | `{bucket.get('composite', '')}` | `{bucket.get('matte', '')}` |")
    lines.extend([
        "",
        "Review hair/hands/edges, holes in the subject, green/blue spill, and whether the replacement background fits. "
        "After apply, watch the full output at 1x; representative frames cannot prove every frame.",
    ])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", "", *[f"- {item}" for item in report["blockers"]]])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in report["warnings"]]])
    return "\n".join(lines).rstrip() + "\n"


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("chroma-key report must be a JSON object")
    return payload


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", force=force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview, review, render, and verify chroma-key composites")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Render representative composite and matte preview frames")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--foreground", required=True)
    prepare.add_argument("--background", required=True)
    prepare.add_argument("--output-video", required=True)
    prepare.add_argument("--preview-dir", default="verify/chroma_key")
    prepare.add_argument("--key-color", default="green")
    prepare.add_argument("--similarity", type=float, default=0.10)
    prepare.add_argument("--blend", type=float, default=0.08)
    prepare.add_argument("--despill", type=float, default=0.50)
    prepare.add_argument("--time", type=float, action="append", default=[])
    prepare.add_argument("--report", required=True)
    prepare.add_argument("--markdown")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true")

    review = subparsers.add_parser("review", help="Record the required human preview checks")
    review.add_argument("--report", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", required=True)
    for check in REVIEW_CHECKS:
        review.add_argument(f"--{check.replace('_', '-')}", required=True, choices=sorted(REVIEW_VALUES))
    review.add_argument("--markdown")
    review.add_argument("--strict", action="store_true")

    apply = subparsers.add_parser("apply", help="Render the approved full composite and bind the output")
    apply.add_argument("--report", required=True)
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true")
    apply.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Live-verify sources, previews, review, filters, and output")
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir")
    verify.add_argument("--strict", action="store_true")
    return parser


def _report_paths(report_path: str, markdown_path: Optional[str]) -> Tuple[Path, Optional[Path], Dict[str, Any]]:
    lexical_report = Path(report_path).expanduser().resolve()
    report = _load_json(lexical_report)
    raw_root = report.get("project_dir")
    if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
        raise ValueError("report project_dir must be a non-empty absolute path")
    root = Path(raw_root).resolve()
    safe_report = _project_file(str(lexical_report), root=root, label="chroma-key report")
    markdown = _project_output(
        markdown_path or str(safe_report.with_suffix(".md")), root=root, label="Markdown output"
    )
    foreground = _project_file(str((report.get("foreground") or {}).get("path") or ""), root=root, label="foreground video")
    background = _project_file(str((report.get("background") or {}).get("path") or ""), root=root, label="background media")
    output_target = _project_output(
        str((report.get("output_target") or {}).get("path") or ""), root=root, label="output video"
    )
    protected_paths = {
        "chroma-key report": safe_report,
        "Markdown output": markdown,
        "foreground video": foreground,
        "background media": background,
        "output video": output_target,
    }
    for item in report.get("previews") or []:
        if isinstance(item, Mapping) and item.get("path"):
            protected_paths[f"preview {item.get('id') or item.get('path')}"] = _project_output(
                str(item["path"]), root=root, label="preview path"
            )
    _ensure_distinct_paths(protected_paths)
    return safe_report, markdown, report


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            root = Path(args.project_dir).expanduser().resolve()
            foreground = _project_file(args.foreground, root=root, label="foreground video")
            background = _project_file(args.background, root=root, label="background media")
            report_path = _project_output(args.report, root=root, label="report output")
            markdown = _project_output(args.markdown, root=root, label="Markdown output") if args.markdown else None
            output_video = _project_output(args.output_video, root=root, label="output video")
            paths = {
                "foreground video": foreground,
                "background media": background,
                "report output": report_path,
                "output video": output_video,
            }
            if markdown is not None:
                paths["Markdown output"] = markdown
            preview_dir = _project_output(args.preview_dir, root=root, label="preview directory")
            paths["preview directory"] = preview_dir
            preview_times = select_sample_times(
                float(_media_signature(probe_media(str(foreground)))["duration"]), args.time
            )
            for preview_id, _, _, preview_path in _preview_paths(preview_dir, preview_times):
                paths[f"preview {preview_id}"] = preview_path
            _ensure_distinct_paths(paths)
            if report_path.exists() and not args.force:
                raise ValueError(f"refusing to overwrite existing report without --force: {report_path}")
            if markdown is not None and markdown.exists() and not args.force:
                raise ValueError(f"refusing to overwrite existing Markdown without --force: {markdown}")
            report = prepare_report(
                args.foreground,
                args.background,
                project_dir=str(root),
                output_video=args.output_video,
                preview_dir=args.preview_dir,
                key_color=args.key_color,
                similarity=args.similarity,
                blend=args.blend,
                despill=args.despill,
                times=args.time,
                force=args.force,
            )
            _write_json(report_path, report, force=args.force)
            if markdown is not None:
                _write_text(markdown, emit_markdown(report), force=args.force)
            print(json.dumps({"status": report["status"], "report": str(report_path), "summary": report["summary"]}, ensure_ascii=False))
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path, markdown, report = _report_paths(args.report, getattr(args, "markdown", None))
        if args.command == "review":
            updated = record_review(
                report,
                reviewer=args.reviewer,
                note=args.note,
                edge_quality=args.edge_quality,
                subject_integrity=args.subject_integrity,
                spill_control=args.spill_control,
                background_fit=args.background_fit,
            )
            _write_json(report_path, updated, force=True)
            if markdown is not None:
                _write_text(markdown, emit_markdown(updated), force=True)
            print(json.dumps({"status": updated["status"], "report": str(report_path), "summary": updated["summary"]}, ensure_ascii=False))
            return 2 if args.strict and updated["summary"]["blocking"] else 0
        if args.command == "apply":
            updated = apply_report(report, force=args.force)
            _write_json(report_path, updated, force=True)
            if markdown is not None:
                _write_text(markdown, emit_markdown(updated), force=True)
            print(json.dumps({"status": updated["status"], "report": str(report_path), "summary": updated["summary"]}, ensure_ascii=False))
            return 2 if args.strict and updated["summary"]["blocking"] else 0

        verification = verify_report(report, args.project_dir)
        print(json.dumps({"status": verification["status"], "summary": verification["summary"], "blockers": verification["blockers"], "warnings": verification["warnings"]}, ensure_ascii=False, indent=2))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
