#!/usr/bin/env python3
"""Plan, apply, and verify source-bound HDR-to-SDR social delivery.

The workflow is deliberately narrow: convert a PQ/HLG HDR source into a
Rec.709 limited-range H.264/AAC MP4 without modifying the source.  The plan
binds the source bytes and color metadata.  Apply requires FFmpeg zscale and
tonemap, renders to a sibling temporary file, validates the SDR contract,
fully decodes the result, and only then atomically promotes it.
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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


VERSION = "hdr_sdr.v1"
PENDING_APPLY = "HDR-to-SDR delivery has not been applied and validated"
HDR_TRANSFERS = {
    "smpte2084": "pq",
    "arib-std-b67": "hlg",
}
REQUIRED_FILTERS = {"tonemap", "zscale"}
MP4_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
OUTPUT_COLOR = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True)


def _run_checked(command: Sequence[str], label: str) -> None:
    result = _run_command(command)
    if result.returncode == 0:
        return
    detail = " ".join((result.stderr or result.stdout or "").split())
    if len(detail) > 3000:
        detail = detail[-3000:]
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def _fraction(value: Any) -> Optional[float]:
    if value in {None, "", "0/0"}:
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            parsed = float(numerator) / denominator_value
        else:
            parsed = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rotation(video: Mapping[str, Any]) -> int:
    raw = (video.get("tags") or {}).get("rotate")
    if raw is None:
        for item in video.get("side_data_list") or []:
            if isinstance(item, Mapping) and item.get("rotation") is not None:
                raw = item.get("rotation")
                break
    try:
        value = int(round(float(raw or 0))) % 360
    except (TypeError, ValueError):
        return 0
    return value if value in {0, 90, 180, 270} else 0


def _bit_depth(video: Mapping[str, Any]) -> Optional[int]:
    try:
        explicit = int(video.get("bits_per_raw_sample") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    pixel_format = str(video.get("pix_fmt") or "").lower()
    match = re.search(r"p(\d{2})(?:le|be)?$", pixel_format)
    if match:
        return int(match.group(1))
    return 8 if pixel_format else None


def probe_media(path: Path) -> Dict[str, Any]:
    result = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next(
        (item for item in data.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if not video:
        raise ValueError(f"video stream not found: {path}")
    audio = next(
        (item for item in data.get("streams", []) if item.get("codec_type") == "audio"),
        None,
    )
    duration = _fraction((data.get("format") or {}).get("duration"))
    if duration is None:
        duration = _fraction(video.get("duration"))
    fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    if duration is None or duration <= 0 or fps is None or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video metadata is incomplete: {path}")
    side_data_types = sorted(
        {
            str(item.get("side_data_type") or "").strip()
            for item in video.get("side_data_list") or []
            if isinstance(item, Mapping) and str(item.get("side_data_type") or "").strip()
        }
    )
    return {
        "duration": round(duration, 6),
        "fps": round(fps, 6),
        "width": width,
        "height": height,
        "rotation": rotation,
        "has_audio": audio is not None,
        "video_codec": str(video.get("codec_name") or "").lower(),
        "audio_codec": str((audio or {}).get("codec_name") or "").lower() or None,
        "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
        "bit_depth": _bit_depth(video),
        "color_primaries": str(video.get("color_primaries") or "unknown").lower(),
        "color_transfer": str(video.get("color_transfer") or "unknown").lower(),
        "color_space": str(video.get("color_space") or "unknown").lower(),
        "color_range": str(video.get("color_range") or "unknown").lower(),
        "side_data_types": side_data_types,
        "format_names": sorted(
            token.strip().lower()
            for token in str((data.get("format") or {}).get("format_name") or "").split(",")
            if token.strip()
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> Dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _source_info(path: Path) -> Dict[str, Any]:
    return {**_fingerprint(path), **probe_media(path)}


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _hdr_profile(media: Mapping[str, Any]) -> str:
    transfer = str(media.get("color_transfer") or "unknown").lower()
    profile = HDR_TRANSFERS.get(transfer)
    if profile is None:
        if transfer in {"", "unknown", "unspecified", "reserved"}:
            raise ValueError("source color_transfer is missing or unknown; refusing to guess HDR")
        raise ValueError(f"source is not PQ/HLG HDR (color_transfer={transfer})")
    primaries = str(media.get("color_primaries") or "unknown").lower()
    if primaries != "bt2020":
        raise ValueError(
            f"HDR source color_primaries must be bt2020, got {primaries}; refusing an ambiguous conversion"
        )
    matrix = str(media.get("color_space") or "unknown").lower()
    if matrix not in {"bt2020nc", "bt2020c"}:
        raise ValueError(
            f"HDR source color_space must be bt2020nc/bt2020c, got {matrix}; refusing an ambiguous conversion"
        )
    return profile


def _tone_map_chain(transfer: str) -> str:
    return (
        f"zscale=t=linear:tin={transfer}:npl=100,"
        "format=gbrpf32le,"
        "zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,"
        "format=yuv420p"
    )


def _settings_for(source: Mapping[str, Any]) -> Dict[str, Any]:
    profile = _hdr_profile(source)
    transfer = str(source.get("color_transfer"))
    return {
        "source_profile": profile,
        "source_transfer": transfer,
        "tone_map": "hable",
        "nominal_peak_luminance_nits": 100,
        "highlight_desaturation": 0,
        "filter_chain": _tone_map_chain(transfer),
        "container": "mp4",
        "video_codec": "h264",
        "video_encoder": "libx264",
        "video_crf": 18,
        "video_preset": "medium",
        "audio_codec": "aac" if source.get("has_audio") else None,
        "audio_bitrate_kbps": 192 if source.get("has_audio") else None,
        "pixel_format": "yuv420p",
        **OUTPUT_COLOR,
        "duration_tolerance_seconds": round(max(0.25, 3.0 / float(source.get("fps") or 1)), 6),
    }


def _available_filters() -> set[str]:
    result = _run_command(["ffmpeg", "-hide_banner", "-filters"])
    if result.returncode != 0:
        return set()
    return {
        name
        for name in REQUIRED_FILTERS
        if re.search(rf"(?m)^\s*[.A-Z|]+\s+{re.escape(name)}\s+", result.stdout or "")
    }


def _canonical_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "source": plan.get("source"),
        "settings": plan.get("settings"),
        "delivery": plan.get("delivery"),
        "application": plan.get("application"),
        "review_contract": plan.get("review_contract"),
        "blockers": plan.get("blockers"),
        "warnings": plan.get("warnings"),
        "summary": plan.get("summary"),
        "status": plan.get("status"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_core(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_live_file(record: Mapping[str, Any], label: str, blockers: List[str]) -> Optional[Path]:
    candidate = Path(str(record.get("path") or "")).expanduser()
    if not candidate.is_absolute():
        blockers.append(f"{label}.path must be absolute")
        return None
    if candidate.is_symlink():
        blockers.append(f"{label}.path must not be a symlink")
        return None
    if not candidate.is_file():
        blockers.append(f"{label} file is missing: {candidate}")
        return None
    if record.get("size_bytes") != candidate.stat().st_size:
        blockers.append(f"{label} size changed")
    elif record.get("sha256") != _sha256(candidate):
        blockers.append(f"{label} sha256 changed")
    return candidate


def _format_matches_mp4(format_names: Any) -> bool:
    return bool({str(item).lower() for item in format_names or []}.intersection(MP4_FORMATS))


def _output_contract_blockers(
    media: Mapping[str, Any], source: Mapping[str, Any], settings: Mapping[str, Any]
) -> List[str]:
    blockers: List[str] = []
    if not _format_matches_mp4(media.get("format_names")):
        blockers.append("SDR delivery is not an MP4-family container")
    if media.get("video_codec") != "h264":
        blockers.append("SDR delivery video codec must be H.264")
    if media.get("pixel_format") != "yuv420p":
        blockers.append("SDR delivery pixel format must be yuv420p")
    for field, expected in OUTPUT_COLOR.items():
        if media.get(field) != expected:
            blockers.append(f"SDR delivery {field} must be {expected}")
    if media.get("width") != source.get("width") or media.get("height") != source.get("height"):
        blockers.append("SDR delivery displayed dimensions do not match the source")
    expected_fps = float(source.get("fps") or 0)
    observed_fps = float(media.get("fps") or 0)
    if abs(observed_fps - expected_fps) > max(0.05, expected_fps * 0.01):
        blockers.append("SDR delivery fps does not match the source")
    tolerance = float(settings.get("duration_tolerance_seconds") or 0)
    if abs(float(media.get("duration") or 0) - float(source.get("duration") or 0)) > tolerance:
        blockers.append("SDR delivery duration drift exceeds the planned tolerance")
    if media.get("has_audio") != source.get("has_audio"):
        blockers.append("SDR delivery audio presence does not match the source")
    if source.get("has_audio") and media.get("audio_codec") != "aac":
        blockers.append("SDR delivery audio codec must be AAC")
    return blockers


def _computed_warnings(source: Mapping[str, Any]) -> List[str]:
    warnings: List[str] = []
    bit_depth = source.get("bit_depth")
    if isinstance(bit_depth, int) and bit_depth < 10:
        warnings.append("HDR source is below 10-bit; inspect gradients and banding after tone mapping.")
    side_data = " ".join(str(item).lower() for item in source.get("side_data_types") or [])
    if "dovi" in side_data or "dolby vision" in side_data:
        warnings.append("Dolby Vision dynamic metadata is not preserved in the SDR derivative; inspect the full output.")
    if "smpte 2094" in side_data or "hdr dynamic metadata" in side_data:
        warnings.append("HDR10+ dynamic metadata is discarded in the SDR derivative; inspect highlight roll-off.")
    return warnings


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
        "0",
        "-f",
        "null",
        "-",
    ]


def _compute_derived(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if plan.get("version") != VERSION:
        blockers.append(f"version must be {VERSION}")

    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    source_path = _validate_live_file(source, "source", blockers)
    if source_path is not None:
        try:
            live_source = _source_info(source_path)
        except (RuntimeError, ValueError) as exc:
            blockers.append(f"source probe failed: {exc}")
        else:
            if source != live_source:
                blockers.append("source fingerprint or color/media contract changed after planning")

    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    try:
        expected_settings = _settings_for(source)
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
    else:
        if settings != expected_settings:
            blockers.append("settings do not match the canonical HDR source contract")

    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), Mapping) else {}
    delivery_path = Path(str(delivery.get("path") or "")).expanduser()
    if not delivery_path.is_absolute():
        blockers.append("delivery.path must be absolute")
    elif delivery_path.suffix.lower() != ".mp4":
        blockers.append("delivery.path must use .mp4")
    elif delivery_path.is_symlink():
        blockers.append("delivery.path must not be a symlink")
    if delivery != {"path": str(delivery_path), "format": "mp4"}:
        blockers.append("delivery record is not canonical")
    if source.get("path") and delivery_path.is_absolute():
        if delivery_path.resolve() == Path(str(source.get("path"))).resolve():
            blockers.append("SDR delivery must not overwrite the HDR source")

    application = plan.get("application")
    applied = isinstance(application, Mapping)
    if not applied:
        missing_filters = sorted(REQUIRED_FILTERS - _available_filters())
        if missing_filters:
            blockers.append(
                "FFmpeg is missing required HDR filters: " + ", ".join(missing_filters)
            )
        blockers.append(PENDING_APPLY)
    else:
        assert isinstance(application, Mapping)
        output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
        output_path = _validate_live_file(output, "application.output", blockers)
        if output.get("path") != delivery.get("path"):
            blockers.append("application.output.path does not match delivery.path")
        if output_path is not None:
            try:
                live_media = {**_fingerprint(output_path), **probe_media(output_path)}
            except (RuntimeError, ValueError) as exc:
                blockers.append(f"SDR delivery probe failed: {exc}")
            else:
                if output != live_media:
                    blockers.append("stored SDR delivery contract is stale or was modified")
                blockers.extend(_output_contract_blockers(live_media, source, settings))
        validation = application.get("validation") if isinstance(application.get("validation"), Mapping) else {}
        expected_decode = _decode_command(delivery_path) if delivery_path.is_absolute() else []
        if validation.get("decode_checked") is not True:
            blockers.append("full FFmpeg decode validation is missing")
        if validation.get("decode_command") != expected_decode:
            blockers.append("decode validation command is stale or non-canonical")
        if validation.get("output_sha256") != output.get("sha256"):
            blockers.append("decode validation is not bound to the current SDR delivery sha256")

    warnings = _computed_warnings(source)
    summary = {
        "source_profile": settings.get("source_profile"),
        "source_transfer": settings.get("source_transfer"),
        "target_color": "bt709",
        "applied": applied,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    status = "blocked" if blockers else "warn" if warnings else "ready"
    return {"blockers": blockers, "warnings": warnings, "summary": summary, "status": status}


def _set_derived(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan.update(_compute_derived(plan))
    plan["plan_id"] = _plan_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(plan)
    integrity_blockers: List[str] = []
    if plan.get("plan_id") != _plan_id(plan):
        integrity_blockers.append("plan_id does not match canonical plan content")
    derived = _compute_derived(plan)
    for field in ("blockers", "warnings", "summary", "status"):
        if plan.get(field) != derived[field]:
            integrity_blockers.append(f"stored {field} is stale or was modified")
    result.update(derived)
    result["blockers"] = integrity_blockers + list(derived["blockers"])
    result["summary"] = {**derived["summary"], "blocking": len(result["blockers"])}
    result["status"] = "blocked" if result["blockers"] else derived["status"]
    return result


def build_plan(source_path: str, delivery_path: str) -> Dict[str, Any]:
    source_candidate = Path(source_path).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("source must not be a symlink")
    source = source_candidate.resolve()
    if not source.is_file():
        raise ValueError(f"HDR source video does not exist: {source}")
    delivery_candidate = Path(delivery_path).expanduser()
    if delivery_candidate.is_symlink():
        raise ValueError("SDR delivery must not be a symlink")
    delivery = delivery_candidate.resolve()
    if delivery.suffix.lower() != ".mp4":
        raise ValueError("SDR delivery must use .mp4")
    if delivery == source:
        raise ValueError("SDR delivery must not overwrite the HDR source")

    source_record = _source_info(source)
    settings = _settings_for(source_record)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": source_record,
        "settings": settings,
        "delivery": {"path": str(delivery), "format": "mp4"},
        "application": None,
        "review_contract": {
            "instructions": [
                "Compare the HDR source and SDR derivative on a calibrated or trusted SDR display.",
                "Watch the complete SDR file at normal speed and inspect skin tones, highlights, shadows, gradients, and saturated colors.",
                "Run render_qa.py, shot_color_qa.py, and approval_receipt.py on the exact SDR bytes before publishing.",
            ],
            "limitations": [
                "Hable tone mapping is a deterministic technical conversion, not a creative color grade or HDR mastering decision.",
                "Dolby Vision/HDR10+ dynamic metadata is intentionally not preserved in an SDR derivative.",
                "Full decode and BT.709 tags prove technical conformance, not perceptual approval.",
            ],
        },
    }
    return _set_derived(plan)


def build_command(plan: Mapping[str, Any], temporary_output: Path) -> List[str]:
    source = Path(str((plan.get("source") or {}).get("path") or ""))
    settings = plan.get("settings") if isinstance(plan.get("settings"), Mapping) else {}
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-vf",
        str(settings.get("filter_chain") or ""),
        "-c:v",
        "libx264",
        "-crf",
        str(settings.get("video_crf")),
        "-preset",
        str(settings.get("video_preset")),
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-metadata:s:v:0",
        "rotate=0",
    ]
    if (plan.get("source") or {}).get("has_audio"):
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-movflags", "+faststart", "-y", str(temporary_output)])
    return command


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read HDR-to-SDR plan: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("HDR-to-SDR plan must be a JSON object")
    return data


def _resolve_plan_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("HDR-to-SDR plan must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise ValueError(f"HDR-to-SDR plan must be an existing JSON file: {resolved}")
    return resolved


def _safe_delivery(path: Path, *, source: Path, plan_file: Path, force: bool) -> Path:
    if path.suffix.lower() != ".mp4":
        raise ValueError("SDR delivery must use .mp4")
    if path.is_symlink():
        raise ValueError("SDR delivery must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if resolved in {source.resolve(), plan_file.resolve()}:
        raise ValueError("SDR delivery must not overwrite the source or plan")
    if resolved.exists() and not force:
        raise ValueError(f"SDR delivery already exists (pass --force to replace): {resolved}")
    return resolved


def apply_plan(plan_path: str, *, force: bool = False) -> Dict[str, Any]:
    plan_file = _resolve_plan_file(plan_path)
    plan = _load_plan(plan_file)
    verification = verify_plan(plan)
    blockers = list(verification.get("blockers") or [])
    if blockers != [PENDING_APPLY]:
        raise ValueError("plan is not ready to apply: " + "; ".join(blockers or ["already applied"]))

    source = Path(str(plan["source"]["path"]))
    delivery = _safe_delivery(
        Path(str(plan["delivery"]["path"])),
        source=source,
        plan_file=plan_file,
        force=force,
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=".tmp.mp4", dir=str(delivery.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _run_checked(build_command(plan, temporary), "HDR-to-SDR tone-map encode")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("HDR-to-SDR encode did not create a non-empty output")
        temporary_media = {**_fingerprint(temporary), **probe_media(temporary)}
        contract_blockers = _output_contract_blockers(
            temporary_media, plan["source"], plan["settings"]
        )
        if contract_blockers:
            raise RuntimeError("; ".join(contract_blockers))
        _run_checked(_decode_command(temporary), "full SDR delivery decode validation")
        os.replace(temporary, delivery)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    output = {**_fingerprint(delivery), **probe_media(delivery)}
    plan["application"] = {
        "applied_at": utc_now(),
        "output": output,
        "validation": {
            "verified_at": utc_now(),
            "decode_checked": True,
            "decode_command": _decode_command(delivery),
            "output_sha256": output["sha256"],
        },
    }
    _set_derived(plan)
    final_verification = verify_plan(plan)
    if final_verification.get("blockers"):
        raise RuntimeError("applied HDR-to-SDR plan failed final verification")
    _atomic_write_json(plan_file, plan)
    return plan


def render_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") or {}
    settings = plan.get("settings") or {}
    delivery = plan.get("delivery") or {}
    lines = [
        "# HDR → SDR Delivery Plan",
        "",
        f"- Status: **{plan.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source SHA-256: `{source.get('sha256', '')}`",
        f"- Source color: `{source.get('color_primaries')}/{source.get('color_transfer')}/{source.get('color_space')}/{source.get('color_range')}`",
        f"- Detected profile: `{settings.get('source_profile', '')}`",
        f"- Delivery: `{delivery.get('path', '')}`",
        "- Target: `H.264 yuv420p / BT.709 / limited range / AAC`",
        "- Tone map: `Hable`, nominal SDR peak `100 nits`, highlight desaturation `0`",
        "",
        "## Filter chain",
        "",
        f"`{settings.get('filter_chain', '')}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.get("blockers") or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in plan.get("warnings") or ["None"])
    lines.extend(["", "## Required review", ""])
    lines.extend(f"- {item}" for item in (plan.get("review_contract") or {}).get("instructions", []))
    lines.append("")
    return "\n".join(lines)


def _write_new(path: str, content: Union[Mapping[str, Any], str], *, force: bool) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("output artifact must not be a symlink")
    resolved = candidate.resolve()
    if resolved.exists() and not force:
        raise ValueError(f"output artifact already exists (pass --force to replace): {resolved}")
    if isinstance(content, str):
        _atomic_write_text(resolved, content)
    else:
        _atomic_write_json(resolved, content)
    return resolved


def _artifact_destination(path: str, suffix: str, forbidden: Sequence[Path]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("output artifact must not be a symlink")
    resolved = candidate.resolve()
    if resolved.suffix.lower() != suffix:
        raise ValueError(f"output artifact must use {suffix}")
    if any(resolved == item.resolve() for item in forbidden):
        raise ValueError("output artifact must not overwrite the source or SDR delivery")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify source-bound PQ/HLG HDR to Rec.709 SDR delivery."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Probe and bind an HDR source and SDR delivery.")
    plan_parser.add_argument("source", help="PQ/HLG HDR source video.")
    plan_parser.add_argument("--delivery", required=True, help="Planned Rec.709 MP4 output path.")
    plan_parser.add_argument("--output", required=True, help="HDR-to-SDR plan JSON path.")
    plan_parser.add_argument("--markdown", help="Optional human-readable review path.")
    plan_parser.add_argument("--force", action="store_true", help="Replace plan/review artifacts.")

    apply_parser = subparsers.add_parser(
        "apply",
        help="Tone-map, validate, fully decode, and atomically promote the SDR delivery.",
        description="Tone-map, validate, fully decode, and atomically promote the SDR delivery.",
    )
    apply_parser.add_argument("plan", help="Existing HDR-to-SDR plan JSON.")
    apply_parser.add_argument("--force", action="store_true", help="Replace an existing SDR delivery.")

    verify_parser = subparsers.add_parser("verify", help="Live-verify plan, source, output, and hashes.")
    verify_parser.add_argument("plan", help="Existing HDR-to-SDR plan JSON.")
    verify_parser.add_argument("--strict", action="store_true", help="Return 2 for blockers or warnings.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(args.source, args.delivery)
            forbidden = [Path(plan["source"]["path"]), Path(plan["delivery"]["path"])]
            output = _artifact_destination(args.output, ".json", forbidden)
            markdown = (
                _artifact_destination(args.markdown, ".md", forbidden)
                if args.markdown
                else None
            )
            if markdown is not None and markdown == output:
                raise ValueError("plan JSON and Markdown outputs must be different files")
            for candidate in (output, markdown):
                if candidate is not None and candidate.exists() and not args.force:
                    raise ValueError(
                        f"output artifact already exists (pass --force to replace): {candidate}"
                    )
            _write_new(str(output), plan, force=args.force)
            if markdown is not None:
                _write_new(str(markdown), render_markdown(plan), force=args.force)
            print(
                f"HDR-to-SDR plan: {plan['status']} "
                f"(blocking={plan['summary']['blocking']}, warnings={plan['summary']['warnings']})"
            )
            return 0
        if args.command == "apply":
            plan = apply_plan(args.plan, force=args.force)
            print(
                f"HDR-to-SDR delivery: {plan['status']} "
                f"(blocking={plan['summary']['blocking']}, warnings={plan['summary']['warnings']})"
            )
            return 0
        plan_file = _resolve_plan_file(args.plan)
        verification = verify_plan(_load_plan(plan_file))
        print(
            f"HDR-to-SDR verification: {verification['status']} "
            f"(blocking={verification['summary']['blocking']}, "
            f"warnings={verification['summary']['warnings']})"
        )
        if verification["blockers"] or (args.strict and verification["warnings"]):
            return 2
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
