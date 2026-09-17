#!/usr/bin/env python3
"""Render reviewed SFX cues into one source-bound narration master.

The workflow consumes ``audio_cue_sheet.v1`` and a finalized narration track.
Each SFX cue must bind either a project-local audio file or one of the four
deterministic FFmpeg synthesis recipes defined here.  ``apply`` renders a
single 48 kHz stereo audio file without attenuating the narration through
``amix`` normalization.  A complete normal-speed listening review is required
before the plan becomes ready.

Music remains outside this tool.  Use ``render_final.py`` and its narration-
driven BGM ducking after this SFX mixdown, or use the reviewed audio master in
an NLE/Remotion timeline that has an equivalent music-mix contract.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from narration_loudness_qa import MEDIA_KEYS, probe_audio_media


VERSION = "audio_cue_mix_plan.v1"
PENDING_APPLY = "audio cue mix has not been rendered and decoded"
PENDING_CONFIRM = "complete normal-speed audio cue mix review has not been confirmed"
SUPPORTED_OUTPUTS = {".wav": "pcm_s24le", ".m4a": "aac"}
REVIEW_FIELDS = (
    "speech_intelligibility",
    "cue_timing",
    "sfx_level",
    "creative_fit",
    "clicks_or_clipping",
)
REVIEW_CHOICES = {"pass", "fail"}
MAX_CUES = 64
MAX_CUE_SECONDS = 10.0
MAX_MASTER_SECONDS = 24 * 60 * 60
SYNTH_RECIPES: Mapping[str, Mapping[str, Any]] = {
    "transition_whoosh": {
        "source": "anoisesrc=duration={duration}:color=pink:sample_rate=48000:amplitude=0.35",
        "filters": "highpass=f=300,lowpass=f=6000",
        "description": "filtered pink-noise whoosh with a short fade envelope",
    },
    "emphasis_ping": {
        "source": "sine=frequency=2200:duration={duration}:sample_rate=48000",
        "filters": "volume=0.55",
        "description": "short 2.2 kHz emphasis tone",
    },
    "success_chime": {
        "source": "sine=frequency=1046.5:duration={duration}:sample_rate=48000",
        "filters": "volume=0.50",
        "description": "short high chime with a decaying envelope",
    },
    "warning_tick": {
        "source": "sine=frequency=440:duration={duration}:sample_rate=48000",
        "filters": "volume=0.45",
        "description": "restrained low warning tick",
    },
}
ALGORITHM_CONTRACT: Mapping[str, Any] = {
    "name": "ffmpeg_source_bound_audio_cue_mix",
    "sample_rate": 48000,
    "channel_layout": "stereo",
    "voice_policy": "preserve finalized narration gain; amix normalize=0",
    "cue_policy": "trim/pad every cue, fade edges, apply bounded gain, delay to output time",
    "peak_policy": "alimiter limit=0.95 with auto-level disabled",
    "decode_policy": "full FFmpeg decode with -xerror before atomic promotion",
    "music_policy": "excluded; keep using the existing narration-driven BGM ducking workflow",
    "synthesis": {
        category: {
            "source": recipe["source"],
            "filters": recipe["filters"],
            "description": recipe["description"],
        }
        for category, recipe in sorted(SYNTH_RECIPES.items())
    },
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


def _finite(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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


def _safe_output_path(
    raw_path: str | Path,
    *,
    root: Path,
    label: str,
    forbidden: Sequence[Path],
    allow_existing: bool = False,
) -> Path:
    path = _lexical_project_path(raw_path, root=root, label=label)
    for item in forbidden:
        if _same_path_or_file(path, item):
            raise ValueError(f"{label} must not overwrite bound input: {item}")
    if path.exists() and not allow_existing:
        raise ValueError(f"refusing to overwrite existing {label}: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _fingerprint(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _audio_contract(path: Path, *, root: Path) -> Dict[str, Any]:
    media = probe_audio_media(path)
    return {
        **_fingerprint(path, root=root),
        **{key: media.get(key) for key in MEDIA_KEYS},
    }


def _load_json(path: Path, *, label: str) -> Dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _resolve_asset(raw_path: str, *, root: Path, cue_sheet_dir: Path, label: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return _project_file(candidate, root=root, label=label)
    sheet_relative = (cue_sheet_dir / candidate).resolve()
    if sheet_relative.is_file() and _within(sheet_relative, root):
        return _project_file(sheet_relative, root=root, label=label)
    return _project_file(candidate, root=root, label=label)


def _normalize_cues(
    sheet: Mapping[str, Any],
    *,
    root: Path,
    cue_sheet_dir: Path,
    duration: float,
    synthesize_missing: bool,
    sfx_gain_db: float,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    raw_cues = sheet.get("sfx")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise ValueError("audio cue sheet must contain at least one sfx[] cue")
    if len(raw_cues) > MAX_CUES:
        raise ValueError(f"audio cue sheet has more than {MAX_CUES} SFX cues")
    cues: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cues, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"sfx cue {index} must be an object")
            continue
        cue_id = " ".join(str(raw.get("id") or f"sfx_{index:03d}").split())
        if not cue_id or cue_id in seen:
            blockers.append(f"sfx cue id must be non-empty and unique: {cue_id!r}")
            continue
        seen.add(cue_id)
        category = " ".join(str(raw.get("category") or "").split()).lower()
        start = _finite(raw.get("start"))
        cue_duration = _finite(raw.get("duration"))
        raw_end = _finite(raw.get("end"))
        if cue_duration is None and start is not None and raw_end is not None:
            cue_duration = raw_end - start
        if start is None or start < 0:
            blockers.append(f"cue {cue_id} start must be a finite non-negative number")
            continue
        if cue_duration is None or cue_duration <= 0 or cue_duration > MAX_CUE_SECONDS:
            blockers.append(f"cue {cue_id} duration must be greater than 0 and at most {MAX_CUE_SECONDS:g}s")
            continue
        end = start + cue_duration
        if end > duration + 0.05:
            blockers.append(f"cue {cue_id} ends at {end:.3f}s beyond narration duration {duration:.3f}s")
            continue
        cue_gain = _finite((raw.get("mix") or {}).get("gain_db") if isinstance(raw.get("mix"), Mapping) else None)
        gain_db = sfx_gain_db if cue_gain is None else cue_gain
        if gain_db < -40 or gain_db > -3:
            blockers.append(f"cue {cue_id} gain_db must be between -40 and -3 dB")
            continue
        asset_value = str(raw.get("asset") or "").strip()
        base = {
            "id": cue_id,
            "category": category,
            "start": round(start, 6),
            "end": round(end, 6),
            "duration": round(cue_duration, 6),
            "gain_db": round(gain_db, 2),
            "trigger_segment": raw.get("trigger_segment"),
            "trigger_text": " ".join(str(raw.get("trigger_text") or "").split()),
        }
        if asset_value:
            try:
                asset_path = _resolve_asset(
                    asset_value,
                    root=root,
                    cue_sheet_dir=cue_sheet_dir,
                    label=f"cue {cue_id} asset",
                )
                asset = _audio_contract(asset_path, root=root)
            except (OSError, ValueError) as exc:
                blockers.append(str(exc))
                continue
            cues.append({**base, "route": "local_asset", "asset": asset, "synthesis": None})
            continue
        recipe = SYNTH_RECIPES.get(category)
        if synthesize_missing and recipe is not None:
            cues.append(
                {
                    **base,
                    "route": "ffmpeg_synthesis",
                    "asset": None,
                    "synthesis": {
                        "category": category,
                        "source": str(recipe["source"]).format(duration=f"{cue_duration:.6f}"),
                        "filters": str(recipe["filters"]),
                        "description": str(recipe["description"]),
                    },
                }
            )
            warnings.append(
                f"cue {cue_id} uses deterministic FFmpeg synthesis; confirm the sound fits the edit"
            )
        elif synthesize_missing:
            blockers.append(
                f"cue {cue_id} category {category or '<missing>'} has no supported synthesis recipe"
            )
        else:
            blockers.append(f"cue {cue_id} has no local asset; resolve it or use --synthesize-missing")
    cues.sort(key=lambda item: (float(item["start"]), item["id"]))
    return cues, sorted(set(blockers)), sorted(set(warnings))


def _render_spec(cues: Sequence[Mapping[str, Any]], duration: float, suffix: str) -> Dict[str, Any]:
    inputs: List[Dict[str, Any]] = [{"index": 0, "kind": "voice"}]
    filter_lines = [
        f"[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=0:{duration:.6f},apad=whole_dur={duration:.6f},atrim=0:{duration:.6f},"
        "asetpts=N/SR/TB[voice]"
    ]
    mix_labels = ["[voice]"]
    for index, cue in enumerate(cues, start=1):
        route = str(cue.get("route") or "")
        if route == "local_asset":
            inputs.append({"index": index, "kind": "file", "path": (cue.get("asset") or {}).get("path")})
        else:
            inputs.append(
                {
                    "index": index,
                    "kind": "lavfi",
                    "source": (cue.get("synthesis") or {}).get("source"),
                }
            )
        cue_duration = float(cue["duration"])
        fade = min(0.05, max(0.005, cue_duration / 4.0))
        fade_out_start = max(0.0, cue_duration - fade)
        synthesis_filter = ""
        if route == "ffmpeg_synthesis":
            raw_filter = str((cue.get("synthesis") or {}).get("filters") or "").strip()
            synthesis_filter = f",{raw_filter}" if raw_filter else ""
        delay_ms = int(round(float(cue["start"]) * 1000))
        label = f"sfx_{index:03d}"
        filter_lines.append(
            f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            f"{synthesis_filter},atrim=0:{cue_duration:.6f},apad=whole_dur={cue_duration:.6f},"
            f"atrim=0:{cue_duration:.6f},afade=t=in:st=0:d={fade:.6f},"
            f"afade=t=out:st={fade_out_start:.6f}:d={fade:.6f},"
            f"volume={float(cue['gain_db']):.2f}dB,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        mix_labels.append(f"[{label}]")
    filter_lines.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:"
        f"dropout_transition=0:normalize=0,alimiter=limit=0.95:level=false,"
        f"atrim=0:{duration:.6f},asetpts=N/SR/TB[out]"
    )
    codec = SUPPORTED_OUTPUTS[suffix]
    codec_args = ["-c:a", codec]
    if codec == "aac":
        codec_args.extend(["-b:a", "192k", "-movflags", "+faststart"])
    return {
        "inputs": inputs,
        "filter_complex": ";\n".join(filter_lines),
        "map": "[out]",
        "codec": codec,
        "codec_args": codec_args,
        "sample_rate": 48000,
        "channels": 2,
        "duration": round(duration, 6),
        "duration_tolerance": 0.05,
        "full_decode_required": True,
        "atomic_promotion": True,
    }


def _immutable_core(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "project_root": plan.get("project_root"),
        "algorithm": plan.get("algorithm"),
        "cue_sheet": plan.get("cue_sheet"),
        "voice": plan.get("voice"),
        "cues": plan.get("cues"),
        "delivery": plan.get("delivery"),
        "settings": plan.get("settings"),
        "planning_issues": plan.get("planning_issues"),
        "render_spec": plan.get("render_spec"),
        "review_contract": plan.get("review_contract"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    return _canonical_sha256(_immutable_core(plan))


def _derived_state(
    plan: Mapping[str, Any],
    *,
    blockers: Sequence[str],
    warnings: Sequence[str],
) -> Dict[str, Any]:
    cues = plan.get("cues") if isinstance(plan.get("cues"), list) else []
    unique_blockers = sorted(set(str(item) for item in blockers if str(item).strip()))
    unique_warnings = sorted(set(str(item) for item in warnings if str(item).strip()))
    return {
        "status": "blocked" if unique_blockers else ("warn" if unique_warnings else "ready"),
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "summary": {
            "cues": len(cues),
            "local_assets": sum(1 for cue in cues if cue.get("route") == "local_asset"),
            "synthesized": sum(1 for cue in cues if cue.get("route") == "ffmpeg_synthesis"),
            "applied": int(bool(plan.get("application"))),
            "reviewed": int(bool(plan.get("review"))),
            "blocking": len(unique_blockers),
            "warnings": len(unique_warnings),
        },
    }


def build_plan(
    cue_sheet_path: str,
    voice_path: str,
    delivery_path: str,
    *,
    project_dir: str = ".",
    synthesize_missing: bool = False,
    sfx_gain_db: float = -18.0,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    if not math.isfinite(sfx_gain_db) or sfx_gain_db < -40 or sfx_gain_db > -3:
        raise ValueError("sfx gain must be between -40 and -3 dB")
    cue_sheet = _project_file(cue_sheet_path, root=root, label="audio cue sheet")
    voice = _project_file(voice_path, root=root, label="voice track")
    delivery = _safe_output_path(
        delivery_path,
        root=root,
        label="audio cue mix delivery",
        forbidden=[cue_sheet, voice],
    )
    if delivery.suffix.lower() not in SUPPORTED_OUTPUTS:
        raise ValueError("audio cue mix delivery must use .wav or .m4a")
    sheet = _load_json(cue_sheet, label="audio cue sheet")
    if sheet.get("version") != "audio_cue_sheet.v1":
        raise ValueError("audio cue sheet must use version audio_cue_sheet.v1")
    voice_contract = _audio_contract(voice, root=root)
    duration = float(voice_contract["duration"])
    if duration <= 0 or duration > MAX_MASTER_SECONDS:
        raise ValueError("voice duration must be greater than zero and at most 24 hours")
    cues, blockers, warnings = _normalize_cues(
        sheet,
        root=root,
        cue_sheet_dir=cue_sheet.parent,
        duration=duration,
        synthesize_missing=synthesize_missing,
        sfx_gain_db=float(sfx_gain_db),
    )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "created_at": utc_now(),
        "project_root": str(root),
        "algorithm": {"id": ALGORITHM_ID, "contract": ALGORITHM_CONTRACT},
        "cue_sheet": _fingerprint(cue_sheet, root=root),
        "voice": voice_contract,
        "cues": cues,
        "delivery": {"path": _relative(delivery, root), "suffix": delivery.suffix.lower()},
        "settings": {
            "synthesize_missing": bool(synthesize_missing),
            "default_sfx_gain_db": round(float(sfx_gain_db), 2),
            "music_included": False,
        },
        "planning_issues": {"blockers": blockers, "warnings": warnings},
        "render_spec": _render_spec(cues, duration, delivery.suffix.lower()),
        "review_contract": {
            "playback": "Listen to the complete rendered narration + SFX mix at normal speed.",
            "checks": list(REVIEW_FIELDS),
            "pass_rule": "full_playback=completed and every review check=pass",
            "limitations": [
                "Procedural synthesis removes the need for a downloaded SFX asset but does not prove creative fit.",
                "Local audio assets still need provenance and license review before publication.",
                "This mix excludes BGM; review the final video mix again after music is added.",
                "A limiter is a peak guard, not a substitute for audio_master_report.py or calibrated listening.",
            ],
        },
        "application": None,
        "review": None,
    }
    plan["plan_id"] = _plan_id(plan)
    state = _derived_state(plan, blockers=[*blockers, PENDING_APPLY], warnings=warnings)
    plan.update(state)
    return plan


def _record_path(record: Mapping[str, Any], *, root: Path, label: str) -> Path:
    return _project_file(str(record.get("path") or ""), root=root, label=label)


def _live_record_blockers(
    record: Mapping[str, Any],
    *,
    root: Path,
    label: str,
    audio: bool,
) -> Tuple[List[str], Optional[Path]]:
    blockers: List[str] = []
    try:
        path = _record_path(record, root=root, label=label)
        live = _audio_contract(path, root=root) if audio else _fingerprint(path, root=root)
    except (OSError, ValueError) as exc:
        return [str(exc)], None
    keys = ["path", "sha256", "size_bytes", *(MEDIA_KEYS if audio else ())]
    if any(record.get(key) != live.get(key) for key in keys):
        blockers.append(f"{label} bytes or audio media contract changed")
    return blockers, path


def _full_decode(path: Path) -> Optional[str]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    detail = " ".join((result.stderr or result.stdout or "").split())
    return f"audio cue mix full decode failed{': ' + detail[-1000:] if detail else ''}"


def _output_contract_blockers(record: Mapping[str, Any], spec: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if int(record.get("sample_rate") or 0) != int(spec.get("sample_rate") or 0):
        blockers.append("audio cue mix must use 48 kHz sample rate")
    if int(record.get("channels") or 0) != int(spec.get("channels") or 0):
        blockers.append("audio cue mix must use stereo output")
    if str(record.get("audio_codec") or "") != str(spec.get("codec") or ""):
        blockers.append("audio cue mix codec does not match the planned delivery")
    duration = _finite(record.get("duration")) or 0.0
    expected = float(spec.get("duration") or 0)
    tolerance = float(spec.get("duration_tolerance") or 0)
    if abs(duration - expected) > tolerance:
        blockers.append(
            f"audio cue mix duration {duration:.3f}s differs from planned {expected:.3f}s by more than {tolerance:.3f}s"
        )
    return blockers


def _review_blockers(plan: Mapping[str, Any]) -> List[str]:
    review = plan.get("review") if isinstance(plan.get("review"), Mapping) else {}
    application = plan.get("application") if isinstance(plan.get("application"), Mapping) else {}
    output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
    if not review:
        return [PENDING_CONFIRM]
    blockers: List[str] = []
    if not str(review.get("reviewed_by") or "").strip():
        blockers.append("reviewed_by is required")
    if not str(review.get("note") or "").strip():
        blockers.append("a non-empty review note is required")
    if review.get("full_playback") != "completed":
        blockers.append("complete normal-speed playback is required")
    checks = review.get("checks") if isinstance(review.get("checks"), Mapping) else {}
    for field in REVIEW_FIELDS:
        if checks.get(field) != "pass":
            blockers.append(f"review check {field} must be pass, got {checks.get(field) or 'missing'}")
    if review.get("output_sha256") != output.get("sha256"):
        blockers.append("review is not bound to the current audio cue mix sha256")
    if review.get("plan_id") != plan.get("plan_id"):
        blockers.append("review is not bound to the current audio cue mix plan id")
    return blockers


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    candidate = copy.deepcopy(dict(plan))
    planning_issues = (
        candidate.get("planning_issues")
        if isinstance(candidate.get("planning_issues"), Mapping)
        else {}
    )
    blockers: List[str] = list(planning_issues.get("blockers") or [])
    warnings = list(planning_issues.get("warnings") or [])
    if candidate.get("version") != VERSION:
        blockers.append(f"plan version must be {VERSION}")
    root_value = str(candidate.get("project_root") or "")
    root = Path(root_value).expanduser().resolve() if root_value else Path("/")
    if not root.is_dir():
        blockers.append("project root is missing")
    algorithm = candidate.get("algorithm") if isinstance(candidate.get("algorithm"), Mapping) else {}
    if algorithm.get("id") != ALGORITHM_ID or algorithm.get("contract") != ALGORITHM_CONTRACT:
        blockers.append("audio cue mix algorithm contract changed")
    if candidate.get("plan_id") != _plan_id(candidate):
        blockers.append("audio cue mix immutable plan content changed")
    cue_sheet = candidate.get("cue_sheet") if isinstance(candidate.get("cue_sheet"), Mapping) else {}
    voice = candidate.get("voice") if isinstance(candidate.get("voice"), Mapping) else {}
    cue_blockers, _ = _live_record_blockers(cue_sheet, root=root, label="audio cue sheet", audio=False)
    voice_blockers, _ = _live_record_blockers(voice, root=root, label="voice track", audio=True)
    blockers.extend(cue_blockers)
    blockers.extend(voice_blockers)
    cues = candidate.get("cues") if isinstance(candidate.get("cues"), list) else []
    if not cues:
        blockers.append("audio cue mix has no normalized cues")
    for cue in cues:
        if not isinstance(cue, Mapping):
            blockers.append("audio cue mix contains a non-object cue")
            continue
        if cue.get("route") == "local_asset":
            asset = cue.get("asset") if isinstance(cue.get("asset"), Mapping) else {}
            live_blockers, _ = _live_record_blockers(
                asset,
                root=root,
                label=f"cue {cue.get('id') or '<missing>'} asset",
                audio=True,
            )
            blockers.extend(live_blockers)
        elif cue.get("route") == "ffmpeg_synthesis":
            synthesis = cue.get("synthesis") if isinstance(cue.get("synthesis"), Mapping) else {}
            category = str(synthesis.get("category") or "")
            recipe = SYNTH_RECIPES.get(category)
            expected_source = (
                str(recipe["source"]).format(duration=f"{float(cue.get('duration') or 0):.6f}")
                if recipe
                else None
            )
            if not recipe or synthesis.get("source") != expected_source or synthesis.get("filters") != recipe["filters"]:
                blockers.append(f"cue {cue.get('id') or '<missing>'} synthesis recipe changed")
        else:
            blockers.append(f"cue {cue.get('id') or '<missing>'} has an invalid route")
    delivery = candidate.get("delivery") if isinstance(candidate.get("delivery"), Mapping) else {}
    try:
        output_path = _lexical_project_path(
            str(delivery.get("path") or ""), root=root, label="audio cue mix delivery"
        )
    except ValueError as exc:
        output_path = None
        blockers.append(str(exc))
    application = candidate.get("application") if isinstance(candidate.get("application"), Mapping) else {}
    if not application:
        blockers.append(PENDING_APPLY)
    elif output_path is None or not output_path.is_file() or output_path.is_symlink():
        blockers.append("audio cue mix delivery is missing or is a symlink")
    else:
        if application.get("render_spec_sha256") != _canonical_sha256(candidate.get("render_spec") or {}):
            blockers.append("application is not bound to the current render specification")
        try:
            live_output = _audio_contract(output_path.resolve(), root=root)
        except (OSError, ValueError) as exc:
            blockers.append(f"audio cue mix delivery could not be probed: {exc}")
        else:
            recorded_output = application.get("output") if isinstance(application.get("output"), Mapping) else {}
            if any(recorded_output.get(key) != live_output.get(key) for key in ["path", "sha256", "size_bytes", *MEDIA_KEYS]):
                blockers.append("audio cue mix delivery bytes or audio media contract changed")
            blockers.extend(_output_contract_blockers(live_output, candidate.get("render_spec") or {}))
            decode_error = _full_decode(output_path)
            if decode_error:
                blockers.append(decode_error)
    if application:
        blockers.extend(_review_blockers(candidate))
    state = _derived_state(candidate, blockers=blockers, warnings=warnings)
    candidate.update(state)
    return candidate


def _build_ffmpeg_command(plan: Mapping[str, Any], *, root: Path, temporary_output: Path) -> List[str]:
    voice = _record_path(plan.get("voice") or {}, root=root, label="voice track")
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(voice)]
    for cue in plan.get("cues") or []:
        if cue.get("route") == "local_asset":
            asset = _record_path(cue.get("asset") or {}, root=root, label=f"cue {cue.get('id')} asset")
            command.extend(["-i", str(asset)])
        else:
            command.extend(["-f", "lavfi", "-i", str((cue.get("synthesis") or {}).get("source") or "")])
    spec = plan.get("render_spec") or {}
    command.extend(
        [
            "-filter_complex",
            str(spec.get("filter_complex") or ""),
            "-map",
            str(spec.get("map") or "[out]"),
            *[str(item) for item in spec.get("codec_args") or []],
            "-ar",
            "48000",
            "-ac",
            "2",
            str(temporary_output),
        ]
    )
    return command


def apply_plan(plan_path: str) -> Dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    plan = _load_json(path, label="audio cue mix plan")
    verification = verify_plan(plan)
    allowed = {PENDING_APPLY}
    static_blockers = [item for item in verification.get("blockers") or [] if item not in allowed]
    if static_blockers:
        raise ValueError("audio cue mix plan is blocked: " + "; ".join(static_blockers))
    root = Path(str(plan.get("project_root") or "")).resolve()
    cue_sheet = _record_path(plan.get("cue_sheet") or {}, root=root, label="audio cue sheet")
    voice = _record_path(plan.get("voice") or {}, root=root, label="voice track")
    local_assets = [
        _record_path(cue.get("asset") or {}, root=root, label=f"cue {cue.get('id')} asset")
        for cue in plan.get("cues") or []
        if cue.get("route") == "local_asset"
    ]
    delivery = _safe_output_path(
        str((plan.get("delivery") or {}).get("path") or ""),
        root=root,
        label="audio cue mix delivery",
        forbidden=[cue_sheet, voice, *local_assets, path],
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{delivery.stem}.", suffix=delivery.suffix, dir=str(delivery.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        command = _build_ffmpeg_command(plan, root=root, temporary_output=temporary)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = " ".join((result.stderr or result.stdout or "").split())
            raise RuntimeError(f"audio cue mix render failed{': ' + detail[-2000:] if detail else ''}")
        output_record = _audio_contract(temporary, root=delivery.parent)
        output_record["path"] = _relative(delivery, root)
        contract_blockers = _output_contract_blockers(output_record, plan.get("render_spec") or {})
        decode_error = _full_decode(temporary)
        if decode_error:
            contract_blockers.append(decode_error)
        if contract_blockers:
            raise RuntimeError("rendered audio cue mix failed validation: " + "; ".join(contract_blockers))
        os.replace(temporary, delivery)
        final_record = _audio_contract(delivery, root=root)
    finally:
        if temporary.exists():
            temporary.unlink()
    plan["application"] = {
        "applied_at": utc_now(),
        "output": final_record,
        "full_decode": "pass",
        "render_spec_sha256": _canonical_sha256(plan.get("render_spec") or {}),
    }
    plan["review"] = None
    checked = verify_plan(plan)
    _atomic_write_json(path, checked)
    return checked


def confirm_plan(
    plan_path: str,
    *,
    reviewed_by: str,
    note: str,
    full_playback: str,
    checks: Mapping[str, str],
) -> Dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    plan = _load_json(path, label="audio cue mix plan")
    if not isinstance(plan.get("application"), Mapping):
        raise ValueError("apply the audio cue mix plan before confirming it")
    unknown = sorted(set(checks) - set(REVIEW_FIELDS))
    if unknown:
        raise ValueError(f"unknown review checks: {', '.join(unknown)}")
    normalized_checks = {field: str(checks.get(field) or "").strip().lower() for field in REVIEW_FIELDS}
    invalid = {field: value for field, value in normalized_checks.items() if value not in REVIEW_CHOICES}
    if invalid:
        raise ValueError("every review check must be pass or fail")
    output = plan["application"].get("output") or {}
    plan["review"] = {
        "reviewed_at": utc_now(),
        "reviewed_by": " ".join(reviewed_by.split()),
        "note": " ".join(note.split()),
        "full_playback": full_playback,
        "checks": normalized_checks,
        "output_sha256": output.get("sha256"),
        "plan_id": plan.get("plan_id"),
    }
    checked = verify_plan(plan)
    _atomic_write_json(path, checked)
    return checked


def emit_markdown(plan: Mapping[str, Any]) -> str:
    summary = plan.get("summary") if isinstance(plan.get("summary"), Mapping) else {}
    lines = [
        "# Audio Cue Mix",
        "",
        f"- Status: {str(plan.get('status') or 'blocked').upper()}",
        f"- Plan ID: `{plan.get('plan_id') or ''}`",
        f"- Voice: `{(plan.get('voice') or {}).get('path') or ''}`",
        f"- Delivery: `{(plan.get('delivery') or {}).get('path') or ''}`",
        f"- Duration: {(plan.get('render_spec') or {}).get('duration', 0)}s",
        f"- Cues: {summary.get('cues', 0)} ({summary.get('local_assets', 0)} local, {summary.get('synthesized', 0)} synthesized)",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Cues",
        "",
        "| id | time | category | route | gain | source |",
        "|---|---:|---|---|---:|---|",
    ]
    for cue in plan.get("cues") or []:
        source = (cue.get("asset") or {}).get("path") or (cue.get("synthesis") or {}).get("description") or "-"
        lines.append(
            f"| {cue.get('id', '')} | {cue.get('start', 0):.3f}s | {cue.get('category', '')} | "
            f"{cue.get('route', '')} | {cue.get('gain_db', 0):.1f} dB | `{str(source).replace('|', '/')}` |"
        )
    blockers = plan.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
    warnings = plan.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
    lines.extend(
        [
            "",
            "## Review Contract",
            "",
            "Listen to the complete narration + SFX mix at normal speed. Confirm speech intelligibility, cue timing, SFX level, creative fit, and absence of clicks/clipping.",
            "BGM is deliberately excluded; review the final video mix again after music is added.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_optional_markdown(path: Optional[str], plan: Mapping[str, Any]) -> None:
    if not path:
        return
    root = Path(str(plan.get("project_root") or ".")).resolve()
    markdown_path = _lexical_project_path(path, root=root, label="audio cue mix markdown")
    _atomic_write_text(markdown_path, emit_markdown(plan))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and review a source-bound narration + SFX cue mix."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Bind cue sheet, narration, SFX sources, and output contract.")
    plan_parser.add_argument("--cue-sheet", required=True, help="Project-local audio_cue_sheet.v1 JSON.")
    plan_parser.add_argument("--voice", required=True, help="Finalized project-local narration/dialogue audio.")
    plan_parser.add_argument("--delivery", required=True, help="Planned .wav or .m4a narration + SFX mix.")
    plan_parser.add_argument("--project-dir", default=".")
    plan_parser.add_argument("--synthesize-missing", action="store_true", help="Use built-in FFmpeg recipes for supported missing SFX categories.")
    plan_parser.add_argument("--sfx-gain-db", type=float, default=-18.0, help="Default SFX gain in dB, from -40 to -3.")
    plan_parser.add_argument("--output", default="work/audio_cue_mix.json", help="Plan JSON path.")
    plan_parser.add_argument("--markdown", help="Optional Markdown review path.")
    plan_parser.add_argument("--strict", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Render, fully decode, and atomically promote the mix.")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--markdown")
    apply_parser.add_argument("--strict", action="store_true")

    confirm_parser = subparsers.add_parser("confirm", help="Bind a complete 1x listening review to the rendered mix.")
    confirm_parser.add_argument("--plan", required=True)
    confirm_parser.add_argument("--reviewed-by", required=True)
    confirm_parser.add_argument("--note", required=True)
    confirm_parser.add_argument("--full-playback", choices=["completed", "incomplete"], required=True)
    for field in REVIEW_FIELDS:
        confirm_parser.add_argument(f"--{field.replace('_', '-')}", choices=sorted(REVIEW_CHOICES), required=True)
    confirm_parser.add_argument("--markdown")
    confirm_parser.add_argument("--strict", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Live-verify inputs, output, decode, and listening review.")
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--markdown")
    verify_parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                args.cue_sheet,
                args.voice,
                args.delivery,
                project_dir=args.project_dir,
                synthesize_missing=args.synthesize_missing,
                sfx_gain_db=args.sfx_gain_db,
            )
            root = Path(args.project_dir).expanduser().resolve()
            output = _safe_output_path(
                args.output,
                root=root,
                label="audio cue mix plan",
                forbidden=[
                    _project_file(args.cue_sheet, root=root, label="audio cue sheet"),
                    _project_file(args.voice, root=root, label="voice track"),
                ],
            )
            _atomic_write_json(output, plan)
        elif args.command == "apply":
            plan = apply_plan(args.plan)
        elif args.command == "confirm":
            checks = {field: getattr(args, field) for field in REVIEW_FIELDS}
            plan = confirm_plan(
                args.plan,
                reviewed_by=args.reviewed_by,
                note=args.note,
                full_playback=args.full_playback,
                checks=checks,
            )
        else:
            plan_path = Path(args.plan).expanduser().resolve()
            raw = _load_json(plan_path, label="audio cue mix plan")
            plan = verify_plan(raw)
            _atomic_write_json(plan_path, plan)
        _write_optional_markdown(args.markdown, plan)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps({"status": plan.get("status"), "summary": plan.get("summary")}, ensure_ascii=False))
    return 2 if args.strict and int((plan.get("summary") or {}).get("blocking") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
