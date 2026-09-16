#!/usr/bin/env python3
"""Bind and verify local image/video/audio references before paid generation.

The workflow is local-only. It validates exact provider capability evidence,
reference counts, media types, sizes, durations, mode compatibility, stable
@Image/@Video/@Audio labels, and narrow prompt roles. It never uploads media,
submits a provider job, or spends credits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from provider_capability import profile_index, verify_bundle
from video_prompt_pack import GENERATED_VIDEO_PROVIDERS, verify_prompt_pack


INPUT_VERSION = "generation_reference_inputs.v1"
REPORT_VERSION = "generation_reference_preflight.v1"
KINDS = ("image", "video", "audio")
REFERENCE_MODES = {
    "reference_to_video",
    "video_edit",
    "video_extension",
    "clip_stitching",
}
FRAME_MODES = {"image_to_video", "first_last_frame"}
LABEL_PREFIX = {"image": "Image", "video": "Video", "audio": "Audio"}


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


def _project_file(root: Path, raw: str, *, label: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not _inside(root, resolved):
        raise ValueError(f"{label} must be inside the project: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"{label} is not a file: {resolved}")
    return resolved


def _output_file(root: Path, raw: str, *, protected: Iterable[Path], force: bool) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError(f"output must not be a symlink: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=False)
    if not _inside(root, resolved):
        raise ValueError(f"output must be inside the project: {resolved}")
    protected_paths = [item.resolve(strict=False) for item in protected]
    if resolved in protected_paths:
        raise ValueError(f"output must not overwrite an input: {resolved}")
    if resolved.exists() and any(
        resolved.samefile(item) for item in protected_paths if item.exists()
    ):
        raise ValueError(f"output must not overwrite a hard-linked input: {resolved}")
    if resolved.exists() and not force:
        raise ValueError(f"output already exists; pass --force to replace: {resolved}")
    return resolved


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


def _duration(payload: Mapping[str, Any], streams: Sequence[Mapping[str, Any]]) -> float:
    values: List[float] = []
    raw_values = [(payload.get("format") or {}).get("duration")]
    raw_values.extend(stream.get("duration") for stream in streams)
    for raw in raw_values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            values.append(value)
    return round(max(values), 4) if values else 0.0


def _decode(path: Path, kind: str) -> None:
    selector = "0:a:0" if kind == "audio" else "0:v:0"
    command = ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", selector]
    if kind == "image":
        command.extend(["-frames:v", "1"])
    command.extend(["-f", "null", "-"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("full decode timed out after 120 seconds") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg decode failed").strip()
        raise ValueError(detail.splitlines()[-1])


def _probe_reference(path: Path, expected_kind: str) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(detail.splitlines()[-1])
    payload = json.loads(result.stdout or "{}")
    streams = [item for item in payload.get("streams") or [] if isinstance(item, Mapping)]
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if expected_kind in {"image", "video"} and not videos:
        raise ValueError(f"expected {expected_kind} but no video/image stream exists")
    if expected_kind == "audio" and not audios:
        raise ValueError("expected audio but no audio stream exists")
    selected = audios[0] if expected_kind == "audio" else videos[0]
    duration = _duration(payload, streams)
    if expected_kind in {"video", "audio"} and duration <= 0:
        raise ValueError(f"{expected_kind} duration is unavailable or non-positive")
    _decode(path, expected_kind)
    return {
        "kind": expected_kind,
        "format": _text((payload.get("format") or {}).get("format_name")),
        "codec": _text(selected.get("codec_name")),
        "duration_seconds": duration if expected_kind != "image" else None,
        "width": int(selected.get("width") or 0) if expected_kind != "audio" else None,
        "height": int(selected.get("height") or 0) if expected_kind != "audio" else None,
        "sample_rate": int(selected.get("sample_rate") or 0) if expected_kind == "audio" else None,
        "channels": int(selected.get("channels") or 0) if expected_kind == "audio" else None,
        "full_decode": "passed",
    }


def build_template(pack: Mapping[str, Any]) -> Dict[str, Any]:
    shots = []
    for item in pack.get("items") or []:
        if not isinstance(item, Mapping) or item.get("provider") not in GENERATED_VIDEO_PROVIDERS:
            continue
        shots.append({"shot_id": _text(item.get("shot_id")), "references": []})
    return {
        "version": INPUT_VERSION,
        "shots": shots,
        "authoring_rules": [
            "Add only local project files that will actually be submitted for this shot.",
            "Each reference needs kind=image|video|audio, path, one narrow role, and explicit properties to exclude.",
            "List references in the exact order that should become @ImageN, @VideoN, or @AudioN.",
            "Keep exact first/last frames in frame mode; use reference_to_video for semantic image/video/audio guidance.",
        ],
    }


def _manifest_index(manifest: Mapping[str, Any], blockers: List[str]) -> Dict[str, Mapping[str, Any]]:
    if manifest.get("version") != INPUT_VERSION:
        blockers.append(f"reference manifest version must be {INPUT_VERSION}")
    rows = manifest.get("shots")
    if not isinstance(rows, list):
        blockers.append("reference manifest shots must be a list")
        return {}
    indexed: Dict[str, Mapping[str, Any]] = {}
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            blockers.append(f"reference manifest shot #{position} is not an object")
            continue
        shot_id = _text(row.get("shot_id"))
        if not shot_id:
            blockers.append(f"reference manifest shot #{position} has no shot_id")
        elif shot_id in indexed:
            blockers.append(f"duplicate reference manifest shot: {shot_id}")
        else:
            indexed[shot_id] = row
    return indexed


def _pack_index(pack: Mapping[str, Any], blockers: List[str]) -> Dict[str, Mapping[str, Any]]:
    if pack.get("version") != "video_prompt_pack.v1":
        blockers.append("prompt pack version must be video_prompt_pack.v1")
    indexed: Dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(pack.get("items") or [], start=1):
        if not isinstance(item, Mapping):
            blockers.append(f"prompt pack item #{position} is not an object")
            continue
        shot_id = _text(item.get("shot_id"))
        if not shot_id:
            blockers.append(f"prompt pack item #{position} has no shot_id")
        elif shot_id in indexed:
            blockers.append(f"duplicate prompt pack shot: {shot_id}")
        else:
            indexed[shot_id] = item
    return indexed


def _resolved_pack_reference(
    root: Path,
    value: Any,
    *,
    label: str,
    required: bool,
    blockers: List[str],
) -> Optional[Path]:
    record = value if isinstance(value, Mapping) else {}
    raw = _text(record.get("resolved_path") or record.get("expected_path"))
    if not raw:
        if required:
            blockers.append(f"{label} is required but no path is configured")
        return None
    try:
        return _project_file(root, raw, label=label)
    except (OSError, ValueError) as exc:
        if required or record.get("expected_path"):
            blockers.append(str(exc))
        return None


def _reference_rules(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    capabilities = profile.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return {}
    value = capabilities.get("reference_media")
    return value if isinstance(value, Mapping) else {}


def _media_rule(rules: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    key = "images" if kind == "image" else ("videos" if kind == "video" else "audio")
    value = rules.get(key)
    return value if isinstance(value, Mapping) else {}


def _validate_constraints(
    *,
    shot_id: str,
    references: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    mode: str,
    has_exact_frame: bool,
    blockers: List[str],
) -> None:
    if not references:
        return
    capabilities = profile.get("capabilities") if isinstance(profile.get("capabilities"), Mapping) else {}
    limits = capabilities.get("reference_limits") if isinstance(capabilities.get("reference_limits"), Mapping) else {}
    counts = {kind: sum(1 for item in references if item.get("kind") == kind) for kind in KINDS}
    for kind, key in (("image", "images"), ("video", "videos"), ("audio", "audio")):
        limit = limits.get(key)
        if not isinstance(limit, int) or isinstance(limit, bool):
            blockers.append(f"{shot_id}: unverified {kind} reference count limit")
        elif counts[kind] > limit:
            blockers.append(f"{shot_id}: {kind} reference limit exceeded: {counts[kind]}>{limit}")

    rules = _reference_rules(profile)
    if not rules:
        blockers.append(f"{shot_id}: provider profile has no reference_media contract")
        return
    total_files = rules.get("total_files")
    if not isinstance(total_files, int) or isinstance(total_files, bool):
        blockers.append(f"{shot_id}: total reference count limit is unverified")
    elif len(references) > total_files:
        blockers.append(f"{shot_id}: total reference limit exceeded: {len(references)}>{total_files}")

    if mode not in REFERENCE_MODES:
        blockers.append(f"{shot_id}: references require a reference/edit/extension/clip-stitching mode, got {mode}")
    exclusive = rules.get("frame_reference_exclusive")
    if has_exact_frame and exclusive is not False:
        blockers.append(f"{shot_id}: exact frame and semantic references cannot be combined on this verified surface")

    visual_count = counts["image"] + counts["video"]
    if counts["audio"]:
        audio = capabilities.get("audio") if isinstance(capabilities.get("audio"), Mapping) else {}
        if audio.get("reference") is not True:
            blockers.append(f"{shot_id}: provider audio-reference support is not verified")
        if visual_count == 0 and rules.get("audio_only") is not True:
            blockers.append(f"{shot_id}: audio-only reference mode is not verified")

    for kind in KINDS:
        selected = [item for item in references if item.get("kind") == kind]
        if not selected:
            continue
        media_rules = _media_rule(rules, kind)
        if not media_rules:
            blockers.append(f"{shot_id}: provider profile has no {kind} reference media rules")
            continue
        extensions = {str(item).lower() for item in media_rules.get("extensions") or []}
        maximum_bytes = media_rules.get("max_bytes")
        if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool):
            blockers.append(f"{shot_id}: {kind} byte limit is unverified")
        total_duration = 0.0
        for item in selected:
            record = item.get("file") if isinstance(item.get("file"), Mapping) else {}
            suffix = Path(str(record.get("path") or "")).suffix.lower()
            if not extensions:
                blockers.append(f"{shot_id}: accepted {kind} extensions are unverified")
            elif suffix not in extensions:
                blockers.append(f"{shot_id}: {item.get('label')} extension {suffix or '<none>'} is unsupported")
            if isinstance(maximum_bytes, int) and int(record.get("size_bytes") or 0) > maximum_bytes:
                blockers.append(
                    f"{shot_id}: {item.get('label')} exceeds max bytes: "
                    f"{record.get('size_bytes')}>{maximum_bytes}"
                )
            if kind in {"video", "audio"}:
                duration = float((item.get("media") or {}).get("duration_seconds") or 0)
                total_duration += duration
                minimum = media_rules.get("min_seconds")
                maximum = media_rules.get("max_seconds")
                if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
                    blockers.append(f"{shot_id}: {kind} duration bounds are unverified")
                elif duration < float(minimum) or duration > float(maximum):
                    blockers.append(
                        f"{shot_id}: {item.get('label')} duration {duration:g}s is outside "
                        f"{float(minimum):g}-{float(maximum):g}s"
                    )
        if kind in {"video", "audio"}:
            maximum_total = media_rules.get("max_total_seconds")
            if not isinstance(maximum_total, (int, float)):
                blockers.append(f"{shot_id}: {kind} total-duration limit is unverified")
            elif total_duration > float(maximum_total) + 1e-6:
                blockers.append(
                    f"{shot_id}: {kind} reference total {total_duration:g}s exceeds {float(maximum_total):g}s"
                )


def _reference_prompt(references: Sequence[Mapping[str, Any]], base_prompt: str) -> str:
    if not references:
        return base_prompt
    lines = ["REFERENCE ROLES:"]
    for item in references:
        lines.append(
            f"{item.get('label')} controls {item.get('role')}. "
            f"Do not inherit {item.get('exclude')}."
        )
    if base_prompt:
        lines.extend(["MAIN REQUEST:", base_prompt])
    return "\n".join(lines)


def _build_shot(
    *,
    root: Path,
    row: Mapping[str, Any],
    item: Mapping[str, Any],
    profile: Mapping[str, Any],
    profile_verification: Mapping[str, Any],
    blockers: List[str],
    warnings: List[str],
) -> Dict[str, Any]:
    shot_id = _text(item.get("shot_id"))
    provider = _text(item.get("provider"))
    mode = _text(item.get("mode"))
    shot_blockers: List[str] = []
    shot_warnings: List[str] = []
    references: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    counters = {kind: 0 for kind in KINDS}

    has_exact_frame = False
    exact_frame: Optional[Dict[str, Any]] = None
    if mode in FRAME_MODES:
        path = _resolved_pack_reference(
            root,
            item.get("reference"),
            label=f"{shot_id} exact first frame",
            required=True,
            blockers=shot_blockers,
        )
        if path is not None:
            has_exact_frame = True
            exact_frame = _file_record(root, path)

    style_path = _resolved_pack_reference(
        root,
        item.get("style_reference"),
        label=f"{shot_id} shared style reference",
        required=False,
        blockers=shot_blockers,
    )
    if style_path is not None:
        counters["image"] += 1
        label = f"@Image{counters['image']}"
        try:
            media = _probe_reference(style_path, "image")
            references.append(
                {
                    "label": label,
                    "kind": "image",
                    "origin": "prompt_pack_style_reference",
                    "role": "the shared palette, lighting, and visual style only",
                    "exclude": "exact action, identity, dialogue, text, framing, and audio",
                    "file": _file_record(root, style_path),
                    "media": media,
                }
            )
            seen_paths.add(str(style_path))
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            shot_blockers.append(f"{shot_id}: {label} is not decodable: {exc}")

    raw_references = row.get("references")
    if not isinstance(raw_references, list):
        shot_blockers.append(f"{shot_id}: references must be a list")
        raw_references = []
    for position, raw in enumerate(raw_references, start=1):
        if not isinstance(raw, Mapping):
            shot_blockers.append(f"{shot_id}: reference #{position} is not an object")
            continue
        kind = _text(raw.get("kind")).lower()
        if kind not in KINDS:
            shot_blockers.append(f"{shot_id}: reference #{position} has unsupported kind {kind or '<empty>'}")
            continue
        role = _text(raw.get("role"))
        exclude = _text(raw.get("exclude"))
        if not role:
            shot_blockers.append(f"{shot_id}: reference #{position} requires one narrow role")
        if not exclude:
            shot_blockers.append(f"{shot_id}: reference #{position} requires explicit excluded properties")
        counters[kind] += 1
        label = f"@{LABEL_PREFIX[kind]}{counters[kind]}"
        try:
            path = _project_file(root, _text(raw.get("path")), label=f"{shot_id} {label}")
        except (OSError, ValueError) as exc:
            shot_blockers.append(str(exc))
            continue
        if str(path) in seen_paths:
            shot_blockers.append(f"{shot_id}: duplicate reference file: {_relative(root, path)}")
            continue
        seen_paths.add(str(path))
        try:
            media = _probe_reference(path, kind)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            shot_blockers.append(f"{shot_id}: {label} is not decodable as {kind}: {exc}")
            continue
        references.append(
            {
                "label": label,
                "kind": kind,
                "origin": "reference_manifest",
                "role": role,
                "exclude": exclude,
                "file": _file_record(root, path),
                "media": media,
            }
        )

    _validate_constraints(
        shot_id=shot_id,
        references=references,
        profile=profile,
        mode=mode,
        has_exact_frame=has_exact_frame,
        blockers=shot_blockers,
    )
    if references and not _text(item.get("prompt")):
        shot_blockers.append(f"{shot_id}: prompt pack item has no provider prompt")

    profile_id = _text(profile_verification.get("profile_id"))
    stored_profile_id = _text((item.get("capability_profile") or {}).get("profile_id"))
    if not profile_id:
        shot_blockers.append(f"{shot_id}: provider capability profile has no verified profile id")
    elif stored_profile_id != profile_id:
        shot_blockers.append(f"{shot_id}: prompt pack capability profile does not match the live provider profile")

    blockers.extend(shot_blockers)
    warnings.extend(shot_warnings)
    counts = {kind: sum(1 for ref in references if ref.get("kind") == kind) for kind in KINDS}
    return {
        "shot_id": shot_id,
        "provider": provider,
        "surface": _text(profile.get("surface")),
        "model": _text(profile.get("model")),
        "profile_id": profile_id,
        "mode": mode,
        "exact_frame": exact_frame,
        "references": references,
        "reference_counts": counts,
        "prompt_addendum": _reference_prompt(references, "").strip(),
        "provider_prompt": _reference_prompt(references, _text(item.get("prompt"))),
        "status": "blocked" if shot_blockers else ("review" if shot_warnings else "ready"),
        "blockers": sorted(set(shot_blockers)),
        "warnings": sorted(set(shot_warnings)),
    }


def build_report(
    *,
    root: Path,
    prompt_pack_path: Path,
    reference_manifest_path: Path,
    capability_paths: Sequence[Path],
    max_age_days: int = 30,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    pack = _load_json(prompt_pack_path)
    manifest = _load_json(reference_manifest_path)
    bundles = [_load_json(path) for path in capability_paths]
    blockers: List[str] = []
    warnings: List[str] = []
    pack_items = _pack_index(pack, blockers)
    manifest_items = _manifest_index(manifest, blockers)

    bundle_reports = [
        verify_bundle(bundle, max_age_days=max_age_days, require_fresh=True)
        for bundle in bundles
    ]
    for report in bundle_reports:
        blockers.extend(f"provider_capabilities:{item}" for item in report.get("blockers") or [])
        warnings.extend(f"provider_capabilities:{item}" for item in report.get("warnings") or [])
    profiles = profile_index(bundles, max_age_days=max_age_days, require_fresh=True)

    prompt_verification = verify_prompt_pack(pack, capability_bundles=bundles)
    blockers.extend(f"prompt_pack:{item}" for item in prompt_verification.get("blockers") or [])
    warnings.extend(f"prompt_pack:{item}" for item in prompt_verification.get("warnings") or [])

    unknown_shots = sorted(set(manifest_items) - set(pack_items))
    if unknown_shots:
        blockers.append("reference manifest contains unknown shots: " + ", ".join(unknown_shots))
    generated_shots = {
        shot_id
        for shot_id, item in pack_items.items()
        if item.get("provider") in GENERATED_VIDEO_PROVIDERS
    }
    missing_shots = sorted(generated_shots - set(manifest_items))
    if missing_shots:
        blockers.append(
            "reference manifest is missing generated-video shots: " + ", ".join(missing_shots)
        )

    shots: List[Dict[str, Any]] = []
    for shot_id, row in manifest_items.items():
        item = pack_items.get(shot_id)
        if item is None:
            continue
        provider = _text(item.get("provider"))
        if provider not in GENERATED_VIDEO_PROVIDERS:
            blockers.append(f"{shot_id}: provider {provider or '<empty>'} is not a generated-video provider")
            continue
        entry = profiles.get(provider)
        if entry is None:
            blockers.append(f"{shot_id}: no live capability profile for {provider}")
            continue
        verification = entry.get("verification") or {}
        if int((verification.get("summary") or {}).get("blocking") or 0):
            blockers.append(f"{shot_id}: capability profile for {provider} is invalid or stale")
        shots.append(
            _build_shot(
                root=root,
                row=row,
                item=item,
                profile=entry.get("profile") or {},
                profile_verification=verification,
                blockers=blockers,
                warnings=warnings,
            )
        )

    unique_blockers = sorted(set(blockers))
    unique_warnings = sorted(set(warnings))
    counts = {
        kind: sum(int((shot.get("reference_counts") or {}).get(kind) or 0) for shot in shots)
        for kind in KINDS
    }
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "policy": {"max_age_days": max_age_days, "full_decode": True},
        "inputs": {
            "prompt_pack": _file_record(root, prompt_pack_path),
            "reference_manifest": _file_record(root, reference_manifest_path),
            "capability_bundles": [_file_record(root, path) for path in capability_paths],
        },
        "shots": shots,
        "limitations": [
            "This report validates local submission inputs; it does not upload media or submit provider jobs.",
            "A decodable reference does not prove rights, consent, factual accuracy, or provider acceptance.",
            "Reference roles constrain the prompt handoff but do not guarantee model fidelity.",
            "Use production_authorization.py for external uploads, real people, brands, protected IP, and paid generation scope.",
            "Review every downloaded generated clip and the assembled sequence before delivery.",
        ],
        "status": "blocked" if unique_blockers else ("review" if unique_warnings else "ready"),
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "summary": {
            "shots": len(shots),
            "references": sum(counts.values()),
            "images": counts["image"],
            "videos": counts["video"],
            "audio": counts["audio"],
            "full_decode_passed": sum(len(shot.get("references") or []) for shot in shots),
            "blocking": len(unique_blockers),
            "warnings": len(unique_warnings),
        },
    }
    report["report_id"] = _digest(
        "grp",
        {key: value for key, value in report.items() if key != "report_id"},
    )
    return report


def verify_report(report_path: str, *, project_dir: str = ".") -> Dict[str, Any]:
    root = _root(project_dir)
    report_file = _project_file(root, report_path, label="generation reference report")
    report = _load_json(report_file)
    errors: List[str] = []
    if report.get("version") != REPORT_VERSION:
        errors.append(f"unsupported report version: {report.get('version')}")
    if _text(report.get("project_root")) != str(root):
        errors.append("report project_root does not match the live project")
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    try:
        pack_record = inputs.get("prompt_pack") if isinstance(inputs.get("prompt_pack"), Mapping) else {}
        manifest_record = inputs.get("reference_manifest") if isinstance(inputs.get("reference_manifest"), Mapping) else {}
        pack_path = _project_file(root, _text(pack_record.get("path")), label="prompt pack")
        manifest_path = _project_file(root, _text(manifest_record.get("path")), label="reference manifest")
        if _file_record(root, pack_path) != pack_record:
            errors.append("prompt pack has drifted")
        if _file_record(root, manifest_path) != manifest_record:
            errors.append("reference manifest has drifted")
        capability_paths: List[Path] = []
        raw_capabilities = inputs.get("capability_bundles")
        if not isinstance(raw_capabilities, list):
            errors.append("capability bundle records are missing")
            raw_capabilities = []
        for position, record in enumerate(raw_capabilities, start=1):
            if not isinstance(record, Mapping):
                errors.append(f"capability bundle record #{position} is invalid")
                continue
            path = _project_file(root, _text(record.get("path")), label=f"capability bundle #{position}")
            capability_paths.append(path)
            if _file_record(root, path) != record:
                errors.append(f"capability bundle #{position} has drifted")
        expected = build_report(
            root=root,
            prompt_pack_path=pack_path,
            reference_manifest_path=manifest_path,
            capability_paths=capability_paths,
            max_age_days=int((report.get("policy") or {}).get("max_age_days") or 30),
            generated_at=_text(report.get("generated_at")),
        )
        if expected != report:
            errors.append("stored report, media probes, prompts, or derived constraints have drifted")
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))

    if not errors:
        return report
    result = dict(report)
    result["verification_errors"] = sorted(set(errors))
    result["blockers"] = sorted(
        set(list(report.get("blockers") or []) + [f"verification: {item}" for item in errors])
    )
    result["status"] = "blocked"
    summary = dict(report.get("summary") or {})
    summary["blocking"] = len(result["blockers"])
    result["summary"] = summary
    return result


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Generation Reference Preflight",
        "",
        f"- Status: `{report.get('status', '')}`",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Shots: {summary.get('shots', 0)}",
        f"- References: {summary.get('references', 0)} "
        f"(images {summary.get('images', 0)}, videos {summary.get('videos', 0)}, audio {summary.get('audio', 0)})",
        f"- Full decodes passed: {summary.get('full_decode_passed', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "| shot | provider / model | mode | references | status |",
        "|---|---|---|---:|---|",
    ]
    for shot in report.get("shots") or []:
        lines.append(
            f"| {shot.get('shot_id')} | {shot.get('provider')} / {shot.get('model')} | "
            f"{shot.get('mode')} | {len(shot.get('references') or [])} | {shot.get('status')} |"
        )
    for shot in report.get("shots") or []:
        lines.extend(["", f"## {shot.get('shot_id')}", ""])
        for reference in shot.get("references") or []:
            media = reference.get("media") or {}
            duration = media.get("duration_seconds")
            details = f"{duration:g}s" if isinstance(duration, (int, float)) else "still"
            lines.append(
                f"- `{reference.get('label')}` {reference.get('kind')} · `{(reference.get('file') or {}).get('path')}` "
                f"· {details}: controls {reference.get('role')}; excludes {reference.get('exclude')}"
            )
        lines.extend(["", "```text", str(shot.get("provider_prompt") or ""), "```"])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", "", *[f"- {item}" for item in report.get("blockers") or []]])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in report.get("warnings") or []]])
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report.get("limitations") or []]])
    return "\n".join(lines).rstrip() + "\n"


def _bound_media_paths(root: Path, report: Mapping[str, Any]) -> List[Path]:
    paths: List[Path] = []
    for shot in report.get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        records = [shot.get("exact_frame")]
        records.extend(
            reference.get("file")
            for reference in shot.get("references") or []
            if isinstance(reference, Mapping)
        )
        for record in records:
            if isinstance(record, Mapping) and _text(record.get("path")):
                paths.append(_project_file(root, _text(record.get("path")), label="bound reference"))
    return paths


def _declared_media_paths(
    root: Path,
    pack: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> List[Path]:
    raw_paths: List[str] = []
    for item in pack.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        for key in ("reference", "style_reference"):
            record = item.get(key)
            if isinstance(record, Mapping):
                raw = _text(record.get("resolved_path") or record.get("expected_path"))
                if raw:
                    raw_paths.append(raw)
    for shot in manifest.get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        for reference in shot.get("references") or []:
            if isinstance(reference, Mapping) and _text(reference.get("path")):
                raw_paths.append(_text(reference.get("path")))

    paths: List[Path] = []
    for raw in raw_paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if _inside(root, resolved) and resolved.is_file():
            paths.append(resolved)
    return paths


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Template, analyze, and live-verify multimodal generation references."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="Create a shot-indexed reference input manifest.")
    template.add_argument("--project-dir", default=".")
    template.add_argument("--prompt-pack", required=True)
    template.add_argument("--output", required=True)
    template.add_argument("--force", action="store_true")

    analyze = sub.add_parser("analyze", help="Probe references and build a source-bound preflight report.")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--prompt-pack", required=True)
    analyze.add_argument("--references", required=True, help=f"{INPUT_VERSION} JSON manifest.")
    analyze.add_argument("--capability-profile", action="append", default=[], required=True)
    analyze.add_argument("--max-age-days", type=int, default=30)
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Re-probe every bound input and rebuild derived state.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = _root(args.project_dir)
        if args.command == "template":
            pack_path = _project_file(root, args.prompt_pack, label="prompt pack")
            output = _output_file(root, args.output, protected=[pack_path], force=args.force)
            _write_json(output, build_template(_load_json(pack_path)))
            print(f"Wrote generation reference template: {output}")
            return 0

        if args.command == "analyze":
            pack_path = _project_file(root, args.prompt_pack, label="prompt pack")
            manifest_path = _project_file(root, args.references, label="reference manifest")
            pack_payload = _load_json(pack_path)
            manifest_payload = _load_json(manifest_path)
            capability_paths = [
                _project_file(root, raw, label=f"capability profile #{position}")
                for position, raw in enumerate(args.capability_profile, start=1)
            ]
            report = build_report(
                root=root,
                prompt_pack_path=pack_path,
                reference_manifest_path=manifest_path,
                capability_paths=capability_paths,
                max_age_days=args.max_age_days,
            )
            protected = [
                pack_path,
                manifest_path,
                *capability_paths,
                *_declared_media_paths(root, pack_payload, manifest_payload),
                *_bound_media_paths(root, report),
            ]
            output = _output_file(root, args.output, protected=protected, force=args.force)
            markdown = (
                _output_file(root, args.markdown, protected=[*protected, output], force=args.force)
                if args.markdown
                else None
            )
            _write_json(output, report)
            if markdown is not None:
                markdown.write_text(emit_markdown(report), encoding="utf-8")
            print(
                f"Generation reference preflight {report['status']}: references={report['summary']['references']} "
                f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        verified = verify_report(args.report, project_dir=str(root))
        print(
            f"Generation reference verification {verified['status']}: "
            f"blocking={verified.get('summary', {}).get('blocking', 0)}"
        )
        return 2 if args.strict and verified.get("summary", {}).get("blocking") else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
