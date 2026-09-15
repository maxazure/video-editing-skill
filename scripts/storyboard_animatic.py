#!/usr/bin/env python3
"""Render, review, and live-verify a source-bound storyboard animatic.

The workflow turns one approved still panel per storyboard shot into a timed
MP4 preview before expensive video generation or final editing.  It never
generates panels, submits provider jobs, or treats a timed slideshow as proof
that the final motion, identity, continuity, or edit will work.
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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from frame_rate_conform import probe_media


VERSION = "storyboard_animatic.v1"
PENDING_APPLY = "storyboard animatic has not been rendered and validated"
PENDING_CONFIRM = "storyboard animatic full-playback review has not been confirmed"
REVIEW_FIELDS = (
    "shot_order",
    "timing_rhythm",
    "panel_legibility",
    "visual_continuity",
    "audio_sync",
)
REVIEW_CHOICES = {"pass", "fail", "not_applicable"}
SHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_SHOTS = 200
MAX_DURATION_SECONDS = 6 * 60 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run(command)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 3000:
        detail = detail[-3000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value)).hexdigest()}"


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except ValueError:
        return False


def _project_root(value: str) -> Path:
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project directory is not a directory: {root}")
    return root


def _project_path(
    root: Path,
    value: str,
    *,
    label: str,
    must_exist: bool,
    suffixes: Optional[Iterable[str]] = None,
) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=must_exist)
    if not _inside(resolved, root):
        raise ValueError(f"{label} must stay inside the project directory: {resolved}")
    if must_exist and not resolved.is_file():
        raise ValueError(f"{label} is not a file: {resolved}")
    if suffixes and resolved.suffix.lower() not in {item.lower() for item in suffixes}:
        allowed = ", ".join(sorted(suffixes))
        raise ValueError(f"{label} must use one of: {allowed}")
    return resolved


def _write_target(
    root: Path,
    value: str,
    *,
    label: str,
    suffix: str,
    protected: Iterable[Path],
    force: bool,
) -> Path:
    target = _project_path(root, value, label=label, must_exist=False, suffixes=(suffix,))
    protected_paths = [path.resolve(strict=False) for path in protected]
    if target in protected_paths:
        raise ValueError(f"{label} must not overwrite an input: {target}")
    if target.exists() and any(target.samefile(path) for path in protected_paths if path.exists()):
        raise ValueError(f"{label} must not overwrite a hard-linked input: {target}")
    if target.exists() and not force:
        raise FileExistsError(f"{label} exists; pass --force to replace it: {target}")
    return target


def _finite_number(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return round(number, 6)


def _parse_aspect(value: str) -> Tuple[float, float]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        raise ValueError(f"storyboard target aspect must use W:H, got: {value or '<empty>'}")
    width, height = float(match.group(1)), float(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError("storyboard target aspect values must be positive")
    return width, height


def _even(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded + 1


def infer_dimensions(aspect: str, *, max_dimension: int = 1280) -> Tuple[int, int]:
    ratio_width, ratio_height = _parse_aspect(aspect)
    if ratio_width >= ratio_height:
        return _even(max_dimension), _even(max_dimension * ratio_height / ratio_width)
    return _even(max_dimension * ratio_width / ratio_height), _even(max_dimension)


def _shot_snapshot(raw: Mapping[str, Any], index: int) -> Dict[str, Any]:
    shot_id = str(raw.get("id") or "").strip()
    if not SHOT_ID_RE.fullmatch(shot_id):
        raise ValueError(f"storyboard shot #{index} has an invalid id: {shot_id or '<empty>'}")
    start = _finite_number(raw.get("start"), field=f"shot {shot_id} start")
    end = _finite_number(raw.get("end"), field=f"shot {shot_id} end")
    duration = _finite_number(raw.get("duration", end - start), field=f"shot {shot_id} duration")
    if end <= start:
        raise ValueError(f"shot {shot_id} must end after it starts")
    if duration <= 0 or abs(duration - (end - start)) > 0.05:
        raise ValueError(f"shot {shot_id} duration must match end-start within 0.05 seconds")
    return {
        "id": shot_id,
        "section": str(raw.get("section") or "").strip(),
        "start": start,
        "end": end,
        "duration": duration,
        "narration": str(raw.get("narration") or "").strip(),
    }


def storyboard_snapshot(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if payload.get("version") != "storyboard_plan.v1":
        raise ValueError("storyboard version must be storyboard_plan.v1")
    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise ValueError("storyboard shots must be a non-empty list")
    if len(raw_shots) > MAX_SHOTS:
        raise ValueError(f"storyboard supports at most {MAX_SHOTS} shots")

    shots: List[Dict[str, Any]] = []
    seen: set[str] = set()
    previous_start = -1.0
    for index, raw in enumerate(raw_shots, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"storyboard shot #{index} must be an object")
        shot = _shot_snapshot(raw, index)
        if shot["id"] in seen:
            raise ValueError(f"duplicate storyboard shot id: {shot['id']}")
        if shot["start"] <= previous_start:
            raise ValueError(f"storyboard shot starts must strictly increase at {shot['id']}")
        seen.add(shot["id"])
        previous_start = shot["start"]
        shots.append(shot)

    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    aspect = str(target.get("aspect") or "").strip()
    _parse_aspect(aspect)
    total_duration = shots[-1]["end"]
    if total_duration <= 0 or total_duration > MAX_DURATION_SECONDS:
        raise ValueError("animatic duration must be greater than zero and at most 6 hours")

    timeline: List[Dict[str, Any]] = []
    for index, shot in enumerate(shots):
        display_start = 0.0 if index == 0 else shot["start"]
        display_end = shots[index + 1]["start"] if index + 1 < len(shots) else shot["end"]
        if display_end <= display_start:
            raise ValueError(f"shot {shot['id']} has no positive animatic display interval")
        timeline.append(
            {
                "shot_id": shot["id"],
                "display_start": round(display_start, 6),
                "display_end": round(display_end, 6),
                "display_duration": round(display_end - display_start, 6),
                "narration_start": shot["start"],
                "narration_end": shot["end"],
            }
        )
    return {
        "target": {
            "platform": str(target.get("platform") or "").strip(),
            "aspect": aspect,
        },
        "shots": shots,
        "timeline": timeline,
        "duration_seconds": round(total_duration, 6),
    }


def parse_panel_specs(values: Sequence[str]) -> Dict[str, str]:
    panels: Dict[str, str] = {}
    for value in values:
        shot_id, separator, path = value.partition("=")
        shot_id = shot_id.strip()
        path = path.strip()
        if not separator or not shot_id or not path:
            raise ValueError(f"panel must use SHOT_ID=PATH: {value}")
        if shot_id in panels:
            raise ValueError(f"duplicate panel mapping: {shot_id}")
        panels[shot_id] = path
    return panels


def probe_image(path: Path) -> Dict[str, Any]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for panel: {path}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid panel metadata: {path}") from exc
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise ValueError(f"panel must contain exactly one decodable image stream: {path}")
    stream = streams[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"panel dimensions are unavailable: {path}")
    return {
        "codec": str(stream.get("codec_name") or "").lower(),
        "width": width,
        "height": height,
        "pixel_format": str(stream.get("pix_fmt") or "").lower() or None,
    }


def probe_audio(path: Path) -> Dict[str, Any]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for audio: {path}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid audio metadata: {path}") from exc
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError(f"audio stream not found: {path}")
    stream = streams[0]
    duration_raw = stream.get("duration") or (payload.get("format") or {}).get("duration")
    duration = _finite_number(duration_raw, field="audio duration")
    if duration <= 0:
        raise ValueError(f"audio has no positive duration: {path}")
    return {
        "codec": str(stream.get("codec_name") or "").lower(),
        "duration": duration,
        "sample_rate": int(stream.get("sample_rate") or 0) or None,
        "channels": int(stream.get("channels") or 0) or None,
    }


def _record_panel(path: Path, shot_id: str) -> Dict[str, Any]:
    return {"shot_id": shot_id, **_fingerprint(path), "image": probe_image(path)}


def _identity_payload(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: plan.get(key)
        for key in (
            "version",
            "project_root",
            "storyboard",
            "target",
            "shots",
            "timeline",
            "panels",
            "audio",
            "settings",
            "delivery",
            "review_contract",
        )
    }


def _inherent_warnings(plan: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        target_width, target_height = _parse_aspect(str(target.get("aspect") or ""))
        target_ratio = target_width / target_height
        output_width = float(settings.get("width") or 0)
        output_height = float(settings.get("height") or 0)
        if (
            output_width > 0
            and output_height > 0
            and abs((output_width / output_height) / target_ratio - 1.0) > 0.03
        ):
            warnings.append("animatic canvas aspect differs from the storyboard target")
        for panel in plan.get("panels") or []:
            image = panel.get("image") if isinstance(panel, Mapping) and isinstance(panel.get("image"), Mapping) else {}
            width, height = float(image.get("width") or 0), float(image.get("height") or 0)
            if width > 0 and height > 0 and abs((width / height) / target_ratio - 1.0) > 0.03:
                warnings.append(
                    f"panel {panel.get('shot_id')} aspect differs from storyboard target; {settings.get('fit')} normalization will be visible"
                )
    except ValueError:
        pass
    audio = plan.get("audio") if isinstance(plan.get("audio"), Mapping) else None
    if audio:
        audio_duration = float((audio.get("media") or {}).get("duration") or 0)
        target_duration = float(settings.get("duration_seconds") or 0)
        tolerance = max(0.05, 2.0 / max(1.0, float(settings.get("fps") or 1)))
        if audio_duration + tolerance < target_duration:
            warnings.append("audio is shorter than the storyboard; the animatic pads the tail with silence")
        elif audio_duration - tolerance > target_duration:
            warnings.append("audio is longer than the storyboard; the animatic trims it to the planned duration")
    return sorted(set(warnings))


def _output_contract_blockers(media: Mapping[str, Any], plan: Mapping[str, Any]) -> List[str]:
    settings = plan.get("settings") or {}
    blockers: List[str] = []
    fps = float(settings.get("fps") or 0)
    duration = float(settings.get("duration_seconds") or 0)
    tolerance = max(0.10, 2.0 / max(1.0, fps))
    if int(media.get("width") or 0) != int(settings.get("width") or 0):
        blockers.append("animatic width does not match the plan")
    if int(media.get("height") or 0) != int(settings.get("height") or 0):
        blockers.append("animatic height does not match the plan")
    if str(media.get("video_codec") or "") != "h264":
        blockers.append("animatic video codec must be H.264")
    if str(media.get("pixel_format") or "") != "yuv420p":
        blockers.append("animatic pixel format must be yuv420p")
    measured_fps = float(media.get("avg_fps") or 0)
    if abs(measured_fps - fps) > 0.01:
        blockers.append("animatic frame rate does not match the plan")
    if abs(float(media.get("duration") or 0) - duration) > tolerance:
        blockers.append("animatic duration does not match the storyboard timeline")
    expected_audio = bool(plan.get("audio"))
    if bool(media.get("has_audio")) != expected_audio:
        blockers.append("animatic audio-stream presence does not match the plan")
    if expected_audio:
        if str(media.get("audio_codec") or "") != "aac":
            blockers.append("animatic audio codec must be AAC")
        if int(media.get("sample_rate") or 0) != 48000:
            blockers.append("animatic audio sample rate must be 48000 Hz")
    return blockers


def _decode_command(path: Path) -> List[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-f",
        "null",
        "-",
    ]


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings = _inherent_warnings(plan)
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")
        return _verification(plan, blockers, warnings)
    try:
        root = Path(str(plan.get("project_root") or "")).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project_root is not a directory")
    except (OSError, ValueError) as exc:
        blockers.append(f"invalid project_root: {exc}")
        return _verification(plan, blockers, warnings)

    if plan.get("plan_id") != _digest("animatic_plan", _identity_payload(plan)):
        blockers.append("plan identity has drifted")

    try:
        storyboard_record = plan.get("storyboard") if isinstance(plan.get("storyboard"), Mapping) else {}
        storyboard = _project_path(
            root,
            str(storyboard_record.get("path") or ""),
            label="storyboard",
            must_exist=True,
            suffixes=(".json",),
        )
        if _fingerprint(storyboard) != dict(storyboard_record):
            blockers.append("storyboard bytes changed")
        snapshot = storyboard_snapshot(_load_json(storyboard))
        for key in ("target", "shots", "timeline"):
            if snapshot[key] != plan.get(key):
                blockers.append(f"storyboard-derived {key} changed")
        if snapshot["duration_seconds"] != (plan.get("settings") or {}).get("duration_seconds"):
            blockers.append("storyboard-derived duration changed")
    except (OSError, ValueError, RuntimeError) as exc:
        blockers.append(str(exc))

    panel_rows = plan.get("panels")
    if not isinstance(panel_rows, list) or len(panel_rows) != len(plan.get("shots") or []):
        blockers.append("panels must cover every storyboard shot exactly once")
    else:
        expected_ids = [item.get("id") for item in plan.get("shots") or []]
        actual_ids = [item.get("shot_id") if isinstance(item, Mapping) else None for item in panel_rows]
        if actual_ids != expected_ids:
            blockers.append("panel order or shot coverage changed")
        for panel in panel_rows:
            if not isinstance(panel, Mapping):
                blockers.append("panel list contains a non-object row")
                continue
            try:
                path = _project_path(
                    root,
                    str(panel.get("path") or ""),
                    label=f"panel {panel.get('shot_id')}",
                    must_exist=True,
                    suffixes=(".png", ".jpg", ".jpeg", ".webp"),
                )
                expected = _record_panel(path, str(panel.get("shot_id") or ""))
                if expected != dict(panel):
                    blockers.append(f"panel bytes or metadata changed: {panel.get('shot_id')}")
            except (OSError, ValueError, RuntimeError) as exc:
                blockers.append(str(exc))

    audio = plan.get("audio") if isinstance(plan.get("audio"), Mapping) else None
    if audio:
        try:
            path = _project_path(root, str(audio.get("path") or ""), label="audio", must_exist=True)
            expected = {**_fingerprint(path), "media": probe_audio(path)}
            if expected != dict(audio):
                blockers.append("audio bytes or metadata changed")
        except (OSError, ValueError, RuntimeError) as exc:
            blockers.append(str(exc))

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    if settings.get("fit") not in {"contain", "cover"}:
        blockers.append("fit must be contain or cover")
    if int(settings.get("fps") or 0) < 1 or int(settings.get("fps") or 0) > 60:
        blockers.append("fps must be between 1 and 60")
    for field in ("width", "height"):
        value = int(settings.get(field) or 0)
        if value < 2 or value > 8192 or value % 2:
            blockers.append(f"{field} must be an even integer between 2 and 8192")

    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else None
    if application is None:
        blockers.append(PENDING_APPLY)
    else:
        try:
            output_record = application.get("output") if isinstance(application.get("output"), Mapping) else {}
            delivery = _project_path(
                root,
                str((plan.get("delivery") or {}).get("path") or ""),
                label="animatic delivery",
                must_exist=True,
                suffixes=(".mp4",),
            )
            current = {**_fingerprint(delivery), "media": probe_media(delivery)}
            if current != dict(output_record):
                blockers.append("animatic output bytes or metadata changed")
            blockers.extend(_output_contract_blockers(current["media"], plan))
            decode = _run(_decode_command(delivery))
            if decode.returncode != 0:
                blockers.append("animatic full decode failed")
            if not bool((application.get("validation") or {}).get("full_decode_checked")):
                blockers.append("animatic application lacks full-decode evidence")
        except (OSError, ValueError, RuntimeError) as exc:
            blockers.append(str(exc))

    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else None
    if review is None:
        blockers.append(PENDING_CONFIRM)
    elif application is None:
        blockers.append("animatic review exists before a validated render")
    else:
        if not str(review.get("reviewed_by") or "").strip():
            blockers.append("animatic review requires reviewed_by")
        if not str(review.get("note") or "").strip():
            blockers.append("animatic review requires a note")
        if review.get("full_playback") != "completed":
            blockers.append("animatic review requires completed full playback")
        output = application.get("output") or {}
        if review.get("output_sha256") != output.get("sha256"):
            blockers.append("animatic review is not bound to the current output")
        checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
        for field in REVIEW_FIELDS:
            value = checks.get(field)
            if value not in REVIEW_CHOICES:
                blockers.append(f"animatic review {field} must use a supported decision")
            elif field == "audio_sync" and not plan.get("audio"):
                if value not in {"pass", "not_applicable"}:
                    blockers.append("audio_sync must be pass or not_applicable when no guide audio is present")
            elif value != "pass":
                blockers.append(f"animatic review failed: {field}")

    return _verification(plan, blockers, warnings)


def _verification(plan: Mapping[str, Any], blockers: Sequence[str], warnings: Sequence[str]) -> Dict[str, Any]:
    unique_blockers = sorted(set(str(item) for item in blockers if str(item)))
    unique_warnings = sorted(set(str(item) for item in warnings if str(item)))
    return {
        "version": plan.get("version"),
        "plan_id": plan.get("plan_id"),
        "status": "blocked" if unique_blockers else ("warn" if unique_warnings else "ready"),
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "summary": {
            "shots": len(plan.get("shots") or []),
            "duration_seconds": (plan.get("settings") or {}).get("duration_seconds"),
            "blocking": len(unique_blockers),
            "warnings": len(unique_warnings),
        },
    }


def _set_derived(plan: Dict[str, Any]) -> Dict[str, Any]:
    verification = verify_plan(plan)
    for field in ("status", "blockers", "warnings", "summary"):
        plan[field] = verification[field]
    return plan


def build_plan(
    storyboard_path: str,
    panel_specs: Sequence[str],
    delivery_path: str,
    *,
    project_dir: str = ".",
    audio_path: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: int = 24,
    fit: str = "contain",
) -> Dict[str, Any]:
    root = _project_root(project_dir)
    storyboard = _project_path(
        root, storyboard_path, label="storyboard", must_exist=True, suffixes=(".json",)
    )
    snapshot = storyboard_snapshot(_load_json(storyboard))
    mappings = parse_panel_specs(panel_specs)
    expected_ids = [shot["id"] for shot in snapshot["shots"]]
    missing = [shot_id for shot_id in expected_ids if shot_id not in mappings]
    extras = sorted(set(mappings) - set(expected_ids))
    if missing:
        raise ValueError(f"missing panel mappings: {', '.join(missing)}")
    if extras:
        raise ValueError(f"unknown panel mappings: {', '.join(extras)}")
    panels = []
    for shot_id in expected_ids:
        panel = _project_path(
            root,
            mappings[shot_id],
            label=f"panel {shot_id}",
            must_exist=True,
            suffixes=(".png", ".jpg", ".jpeg", ".webp"),
        )
        panels.append(_record_panel(panel, shot_id))
    if len({row["path"] for row in panels}) != len(panels):
        raise ValueError("each storyboard shot must use a distinct panel file")

    if (width is None) != (height is None):
        raise ValueError("provide both width and height, or omit both to infer from target aspect")
    if width is None:
        width, height = infer_dimensions(snapshot["target"]["aspect"])
    assert height is not None
    if width < 2 or height < 2 or width > 8192 or height > 8192 or width % 2 or height % 2:
        raise ValueError("width and height must be even integers between 2 and 8192")
    if fps < 1 or fps > 60:
        raise ValueError("fps must be between 1 and 60")
    if fit not in {"contain", "cover"}:
        raise ValueError("fit must be contain or cover")

    audio = None
    protected = [storyboard, *(Path(row["path"]) for row in panels)]
    if audio_path:
        audio_file = _project_path(root, audio_path, label="audio", must_exist=True)
        audio = {**_fingerprint(audio_file), "media": probe_audio(audio_file)}
        protected.append(audio_file)
    delivery = _project_path(
        root, delivery_path, label="animatic delivery", must_exist=False, suffixes=(".mp4",)
    )
    if delivery in {path.resolve(strict=False) for path in protected}:
        raise ValueError("animatic delivery must not overwrite an input")

    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "storyboard": _fingerprint(storyboard),
        "target": snapshot["target"],
        "shots": snapshot["shots"],
        "timeline": snapshot["timeline"],
        "panels": panels,
        "audio": audio,
        "settings": {
            "width": width,
            "height": height,
            "fps": fps,
            "fit": fit,
            "duration_seconds": snapshot["duration_seconds"],
            "video_encoder": "libx264",
            "video_crf": 20,
            "pixel_format": "yuv420p",
            "labels": "shot_id_and_display_time",
        },
        "delivery": {
            "path": str(delivery),
            "format": "mp4",
            "purpose": "pre_generation_storyboard_timing_review",
        },
        "application": None,
        "review": None,
        "review_contract": {
            "playback_speed": "1x",
            "required_checks": list(REVIEW_FIELDS),
            "instructions": [
                "Play the complete animatic at normal speed; do not approve from isolated stills.",
                "Confirm shot order, time allocation, panel legibility, and visible continuity across every boundary.",
                "When guide audio is present, listen through the full file and confirm narration/beat alignment.",
                "Treat the animatic as a planning proxy; generated motion and final edits still need their own review.",
            ],
        },
    }
    plan["plan_id"] = _digest("animatic_plan", _identity_payload(plan))
    return _set_derived(plan)


def build_filter_graph(plan: Mapping[str, Any]) -> str:
    settings = plan.get("settings") or {}
    width, height = int(settings["width"]), int(settings["height"])
    fps = int(settings["fps"])
    fit = str(settings["fit"])
    filters: List[str] = []
    for index, row in enumerate(plan.get("timeline") or []):
        if fit == "contain":
            geometry = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
        else:
            geometry = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        label = (
            f"{row['shot_id']}  t={float(row['display_start']):.2f}-{float(row['display_end']):.2f}s"
        )
        font_size = max(18, round(height * 0.028))
        filters.append(
            f"[{index}:v]{geometry},setsar=1,fps={fps},format=yuv420p,"
            f"drawtext=text='{label}':fontcolor=white:fontsize={font_size}:"
            "box=1:boxcolor=black@0.68:boxborderw=10:x=20:y=20"
            f"[v{index}]"
        )
    inputs = "".join(f"[v{index}]" for index in range(len(plan.get("timeline") or [])))
    filters.append(f"{inputs}concat=n={len(plan.get('timeline') or [])}:v=1:a=0[vout]")
    return ";\n".join(filters) + "\n"


def build_command(plan: Mapping[str, Any], output_path: Path, filter_script: Path) -> List[str]:
    settings = plan.get("settings") or {}
    fps = int(settings["fps"])
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    panel_by_id = {row["shot_id"]: row for row in plan.get("panels") or []}
    for row in plan.get("timeline") or []:
        panel = panel_by_id[row["shot_id"]]
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-t",
                f"{float(row['display_duration']):.6f}",
                "-i",
                str(panel["path"]),
            ]
        )
    audio = plan.get("audio")
    if audio:
        command.extend(["-i", str(audio["path"])])
    command.extend(
        [
            "-filter_complex_script",
            str(filter_script),
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(settings.get("video_crf") or 20),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
        ]
    )
    if audio:
        audio_index = len(plan.get("panels") or [])
        duration = float(settings["duration_seconds"])
        command.extend(
            [
                "-map",
                f"{audio_index}:a:0",
                "-af",
                f"apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        )
    else:
        command.append("-an")
    command.extend(
        [
            "-t",
            f"{float(settings['duration_seconds']):.6f}",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def _resolve_plan_file(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError("animatic plan must not be a symlink")
    path = candidate.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValueError(f"animatic plan must be an existing JSON file: {path}")
    return path


def _temporary_mp4(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp.mp4", dir=str(target.parent))
    os.close(descriptor)
    return Path(name)


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_json(path)
    verification = verify_plan(plan)
    expected_pending = {PENDING_APPLY, PENDING_CONFIRM}
    substantive = [item for item in verification["blockers"] if item not in expected_pending]
    if substantive:
        raise ValueError("plan is not safe to apply: " + "; ".join(substantive))
    root = Path(str(plan["project_root"])).resolve(strict=True)
    if not _inside(path, root):
        raise ValueError("animatic plan must stay inside the project directory")
    delivery = _project_path(
        root,
        str((plan.get("delivery") or {}).get("path") or ""),
        label="animatic delivery",
        must_exist=False,
        suffixes=(".mp4",),
    )
    if delivery.is_symlink():
        raise ValueError("animatic delivery must not be a symlink")
    if delivery.exists() and not force:
        raise FileExistsError(f"animatic delivery exists; pass --force to replace it: {delivery}")
    input_before = {
        "storyboard": _fingerprint(Path(plan["storyboard"]["path"])),
        "panels": [_fingerprint(Path(row["path"])) for row in plan["panels"]],
        "audio": _fingerprint(Path(plan["audio"]["path"])) if plan.get("audio") else None,
    }
    temporary_output = _temporary_mp4(delivery)
    descriptor, filter_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".filters.txt", dir=str(delivery.parent)
    )
    os.close(descriptor)
    filter_script = Path(filter_name)
    try:
        filter_script.write_text(build_filter_graph(plan), encoding="utf-8")
        _run_checked(build_command(plan, temporary_output, filter_script), "storyboard animatic render")
        media = probe_media(temporary_output)
        contract_blockers = _output_contract_blockers(media, plan)
        if contract_blockers:
            raise RuntimeError("animatic output validation failed: " + "; ".join(contract_blockers))
        _run_checked(_decode_command(temporary_output), "storyboard animatic full decode")
        input_after = {
            "storyboard": _fingerprint(Path(plan["storyboard"]["path"])),
            "panels": [_fingerprint(Path(row["path"])) for row in plan["panels"]],
            "audio": _fingerprint(Path(plan["audio"]["path"])) if plan.get("audio") else None,
        }
        if input_after != input_before:
            raise RuntimeError("storyboard, panel, or audio input changed during render")
        os.replace(temporary_output, delivery)
    finally:
        for temporary in (temporary_output, filter_script):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    output = {**_fingerprint(delivery), "media": probe_media(delivery)}
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "validation": {
            "validated_at": utc_now(),
            "full_decode_checked": True,
            "full_decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
        },
    }
    plan["review"] = None
    _set_derived(plan)
    final_substantive = [item for item in plan["blockers"] if item != PENDING_CONFIRM]
    if final_substantive:
        raise RuntimeError("rendered animatic failed final verification: " + "; ".join(final_substantive))
    _atomic_write_json(path, plan)
    return plan


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = _resolve_plan_file(plan_path)
    plan = _load_json(path)
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the storyboard animatic plan before confirming it")
    if not reviewed_by.strip() or not note.strip():
        raise ValueError("reviewed_by and a non-empty review note are required")
    if full_playback not in {"completed", "not_completed"}:
        raise ValueError("full_playback must be completed or not_completed")
    current = dict(plan)
    current["review"] = None
    verification = verify_plan(current)
    substantive = [item for item in verification["blockers"] if item != PENDING_CONFIRM]
    if substantive:
        raise ValueError("plan is not safe to confirm: " + "; ".join(substantive))
    normalized = {field: str(checks.get(field) or "") for field in REVIEW_FIELDS}
    if any(value not in REVIEW_CHOICES for value in normalized.values()):
        raise ValueError(f"every review check must be one of {sorted(REVIEW_CHOICES)}")
    plan["review"] = {
        "confirmed_at": utc_now(),
        "reviewed_by": reviewed_by.strip(),
        "note": note.strip(),
        "full_playback": full_playback,
        "checks": normalized,
        "output_sha256": plan["application"]["output"]["sha256"],
    }
    _set_derived(plan)
    _atomic_write_json(path, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    settings = plan.get("settings") or {}
    lines = [
        "# Storyboard Animatic",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Storyboard: `{(plan.get('storyboard') or {}).get('path', '')}`",
        f"- Plan ID: `{plan.get('plan_id', '')}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path', '')}`",
        f"- Canvas / FPS: `{settings.get('width')}x{settings.get('height')} @ {settings.get('fps')}`",
        f"- Duration / shots: `{settings.get('duration_seconds')}s / {len(plan.get('shots') or [])}`",
        f"- Guide audio: `{'yes' if plan.get('audio') else 'no'}`",
        "",
        "## Timeline",
        "",
        "| shot | display | narration | panel |",
        "|---|---:|---:|---|",
    ]
    panels = {row["shot_id"]: row for row in plan.get("panels") or [] if isinstance(row, Mapping)}
    for row in plan.get("timeline") or []:
        panel = panels.get(row.get("shot_id"), {})
        lines.append(
            f"| `{row.get('shot_id')}` | {row.get('display_start')}-{row.get('display_end')}s "
            f"| {row.get('narration_start')}-{row.get('narration_end')}s | `{panel.get('path', '')}` |"
        )
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(["", "## Review contract", ""])
    lines.extend(f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions") or [])
    lines.append("")
    return "\n".join(lines)


def _write_markdown(value: Optional[str], plan: Mapping[str, Any], *, root: Path, forbidden: Iterable[Path]) -> None:
    if not value:
        return
    target = _project_path(root, value, label="markdown output", must_exist=False, suffixes=(".md",))
    forbidden_paths = {item.resolve(strict=False) for item in forbidden}
    if target in forbidden_paths:
        raise ValueError("markdown output must not overlap an input, plan, or delivery")
    if target.is_symlink():
        raise ValueError("markdown output must not be a symlink")
    _atomic_write_text(target, render_markdown(plan))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and source-bind timed storyboard panels for full-playback review before generation."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Bind storyboard bytes, one still panel per shot, timing, and delivery.")
    plan.add_argument("--project-dir", default=".")
    plan.add_argument("--storyboard", required=True)
    plan.add_argument(
        "--panel",
        action="append",
        required=True,
        help="Repeat SHOT_ID=PATH once for every storyboard shot.",
    )
    plan.add_argument("--audio", help="Optional project-local guide/narration audio.")
    plan.add_argument("--width", type=int)
    plan.add_argument("--height", type=int)
    plan.add_argument("--fps", type=int, default=24)
    plan.add_argument("--fit", choices=("contain", "cover"), default="contain")
    plan.add_argument("--delivery", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--markdown")
    plan.add_argument("--force", action="store_true", help="Replace plan/Markdown artifacts only.")

    apply = commands.add_parser("apply", help="Render, validate, fully decode, and atomically promote the animatic.")
    apply.add_argument("plan")
    apply.add_argument("--markdown")
    apply.add_argument("--force", action="store_true", help="Replace the animatic delivery.")

    confirm = commands.add_parser("confirm", help="Record complete 1x playback review against the output bytes.")
    confirm.add_argument("plan")
    confirm.add_argument("--reviewed-by", required=True)
    confirm.add_argument("--note", required=True)
    confirm.add_argument("--full-playback", choices=("completed", "not_completed"), required=True)
    for field in REVIEW_FIELDS:
        confirm.add_argument(
            f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True
        )
    confirm.add_argument("--markdown")

    verify = commands.add_parser("verify", help="Re-read storyboard, panels, audio, delivery, and review.")
    verify.add_argument("plan")
    verify.add_argument("--markdown")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            root = _project_root(args.project_dir)
            storyboard = _project_path(
                root, args.storyboard, label="storyboard", must_exist=True, suffixes=(".json",)
            )
            mappings = parse_panel_specs(args.panel)
            protected = [storyboard]
            protected.extend(
                _project_path(
                    root,
                    value,
                    label=f"panel {shot_id}",
                    must_exist=True,
                    suffixes=(".png", ".jpg", ".jpeg", ".webp"),
                )
                for shot_id, value in mappings.items()
            )
            if args.audio:
                protected.append(_project_path(root, args.audio, label="audio", must_exist=True))
            output = _write_target(
                root,
                args.output,
                label="plan output",
                suffix=".json",
                protected=protected,
                force=args.force,
            )
            delivery = _project_path(
                root, args.delivery, label="animatic delivery", must_exist=False, suffixes=(".mp4",)
            )
            if output == delivery:
                raise ValueError("plan output and animatic delivery must be different")
            report = build_plan(
                args.storyboard,
                args.panel,
                args.delivery,
                project_dir=args.project_dir,
                audio_path=args.audio,
                width=args.width,
                height=args.height,
                fps=args.fps,
                fit=args.fit,
            )
            if args.markdown:
                markdown_target = _project_path(
                    root,
                    args.markdown,
                    label="markdown output",
                    must_exist=False,
                    suffixes=(".md",),
                )
                if markdown_target.exists() and not args.force:
                    raise FileExistsError(
                        f"markdown output exists; pass --force to replace it: {markdown_target}"
                    )
            _atomic_write_json(output, report)
            _write_markdown(
                args.markdown,
                report,
                root=root,
                forbidden=[*protected, output, delivery],
            )
        elif args.command == "apply":
            report = apply_plan(args.plan, force=args.force)
            root = Path(report["project_root"])
            _write_markdown(
                args.markdown,
                report,
                root=root,
                forbidden=[
                    Path(args.plan),
                    Path(report["storyboard"]["path"]),
                    *(Path(row["path"]) for row in report["panels"]),
                    Path(report["delivery"]["path"]),
                ],
            )
        elif args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            report = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                checks=checks,
            )
            root = Path(report["project_root"])
            _write_markdown(
                args.markdown,
                report,
                root=root,
                forbidden=[Path(args.plan), Path(report["delivery"]["path"])],
            )
        else:
            path = _resolve_plan_file(args.plan)
            stored = _load_json(path)
            report = verify_plan(stored)
            root = Path(str(stored.get("project_root") or ".")).resolve()
            refreshed = dict(stored)
            for field in ("status", "blockers", "warnings", "summary"):
                refreshed[field] = report[field]
            _write_markdown(args.markdown, refreshed, root=root, forbidden=[path])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if getattr(args, "strict", False) and report["summary"]["blocking"] else 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
