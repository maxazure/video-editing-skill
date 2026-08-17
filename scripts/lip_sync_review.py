#!/usr/bin/env python3
"""Create and verify source-bound lip-sync proof reviews from a final master.

The script deliberately leaves visual judgment to a reviewer. It extracts
short normal-speed and silent quarter-speed proofs from the exact delivery
candidate, then binds those files and the review decisions with SHA-256.
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generated_clip_review import probe_media  # noqa: E402


REQUEST_VERSION = "lip_sync_review_request.v1"
RESPONSE_VERSION = "lip_sync_review_response.v1"
REPORT_VERSION = "lip_sync_review.v1"

VERDICTS = {"pass", "fail"}
PLOSIVE_RESULTS = {"aligned", "misaligned", "not_observable"}
VOWEL_RESULTS = {"aligned", "early", "late", "not_observable"}
FROZEN_MOUTH_RESULTS = {"absent", "present", "not_observable"}
SPEAKER_RESULTS = {"correct", "wrong", "not_observable"}
AUDIO_RESULTS = {"clean", "problem", "not_observable"}
REPAIR_ACTIONS = {
    "none",
    "regenerate_from_locked_audio",
    "trim_or_retime",
    "cut_to_broll",
    "switch_model",
}


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
        {key: value for key, value in report.items() if key not in {"generated_at", "report_id"}}
    )


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", force=force)


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


def _clean_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError(f"invalid segment id: {value!r}")
    return cleaned


def parse_segment_specs(specs: Sequence[str], anchors: Sequence[str]) -> List[Dict[str, Any]]:
    anchor_map: Dict[str, str] = {}
    for raw in anchors:
        if "=" not in raw:
            raise ValueError(f"anchor must use ID=TEXT: {raw}")
        raw_id, text = raw.split("=", 1)
        segment_id = _clean_id(raw_id)
        if segment_id in anchor_map:
            raise ValueError(f"duplicate anchor id: {segment_id}")
        if not text.strip():
            raise ValueError(f"anchor text must not be empty: {segment_id}")
        anchor_map[segment_id] = text.strip()

    segments: List[Dict[str, Any]] = []
    seen = set()
    for raw in specs:
        if "=" not in raw:
            raise ValueError(f"segment must use ID=START:END: {raw}")
        raw_id, raw_range = raw.split("=", 1)
        segment_id = _clean_id(raw_id)
        if segment_id in seen:
            raise ValueError(f"duplicate segment id: {segment_id}")
        seen.add(segment_id)
        parts = raw_range.split(":")
        if len(parts) != 2:
            raise ValueError(f"segment must use ID=START:END: {raw}")
        try:
            start, end = (round(float(value), 6) for value in parts)
        except ValueError as exc:
            raise ValueError(f"segment start/end must be numbers: {raw}") from exc
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError(f"segment start/end must be finite: {raw}")
        if segment_id not in anchor_map:
            raise ValueError(f"missing --anchor for segment: {segment_id}")
        segments.append(
            {
                "segment_id": segment_id,
                "start": start,
                "end": end,
                "anchor_text": anchor_map[segment_id],
            }
        )
    extras = sorted(set(anchor_map) - seen)
    if extras:
        raise ValueError(f"anchors have no matching segment: {', '.join(extras)}")
    if not segments:
        raise ValueError("at least one --segment and matching --anchor are required")
    return segments


def _run_ffmpeg(command: Sequence[str], *, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent, delete=False
    )
    temp_path = Path(handle.name)
    handle.close()
    try:
        actual = [str(temp_path) if part == "__OUTPUT__" else part for part in command]
        result = subprocess.run(actual, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
            raise ValueError(f"proof extraction failed: {detail.splitlines()[-1]}")
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def render_proof(
    source: Path,
    output: Path,
    *,
    start: float,
    duration: float,
    slow: bool,
    force: bool,
) -> Dict[str, Any]:
    if output.exists() and not force:
        raise ValueError(f"refusing to overwrite existing proof without --force: {output}")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
    ]
    if slow:
        command.extend(["-an", "-vf", "setpts=4*PTS"])
    else:
        command.extend(["-map", "0:a:0"])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if not slow:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", "-y", "__OUTPUT__"])
    _run_ffmpeg(command, output=output)
    media = _media_signature(probe_media(str(output)))
    if bool(media["has_audio"]) == slow:
        expected = "silent" if slow else "audio-bearing"
        raise ValueError(f"proof must be {expected}: {output}")
    return media


def _proof_record(
    path: Path,
    *,
    root: Path,
    media: Mapping[str, Any],
    playback_speed: float,
) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "playback_speed": playback_speed,
        "media": _media_signature(media),
    }


def _response_template(request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "request_id": request.get("request_id"),
        "reviewed_by": "",
        "reviews": [
            {
                "segment_id": segment.get("segment_id"),
                "verdict": "",
                "plosive_closures": "",
                "vowel_timing": "",
                "frozen_mouth": "",
                "speaker_assignment": "",
                "audio_quality": "",
                "repair_action": "",
                "notes": "",
            }
            for segment in request.get("segments") or []
        ],
    }


def prepare_request(
    *,
    project_dir: str,
    video_path: str,
    segments: Sequence[Mapping[str, Any]],
    proof_dir: str,
    context: float = 0.35,
    force: bool = False,
) -> Dict[str, Any]:
    if not math.isfinite(context) or not 0 <= context <= 2:
        raise ValueError("context must be between 0 and 2 seconds")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    video = _project_file(video_path, root=root, label="final master")
    source_media = _media_signature(probe_media(str(video)))
    if not source_media["has_audio"]:
        raise ValueError("final master must contain audio for lip-sync review")
    duration = float(source_media["duration"])
    proof_root = _project_output(proof_dir, root=root, label="proof directory")

    prepared: List[Dict[str, Any]] = []
    seen_ids = set()
    output_paths = set()
    for raw in segments:
        segment_id = _clean_id(str(raw.get("segment_id") or ""))
        if segment_id in seen_ids:
            raise ValueError(f"duplicate segment id: {segment_id}")
        seen_ids.add(segment_id)
        try:
            start = round(float(raw.get("start")), 6)
            end = round(float(raw.get("end")), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{segment_id}: start/end must be numbers") from exc
        anchor_text = str(raw.get("anchor_text") or "").strip()
        speaker = str(raw.get("speaker") or "").strip()
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"{segment_id}: range must be finite with 0 <= start < end")
        if end > duration + 0.05:
            raise ValueError(f"{segment_id}: range ends after the final master ({duration:.3f}s)")
        if end - start < 1 or end - start > 10:
            raise ValueError(f"{segment_id}: review phrase must be between 1 and 10 seconds")
        if not anchor_text:
            raise ValueError(f"{segment_id}: anchor_text must not be empty")
        proof_start = max(0.0, start - context)
        proof_end = min(duration, end + context)
        normal = proof_root / f"{segment_id}_1x.mp4"
        slow = proof_root / f"{segment_id}_025x_silent.mp4"
        if normal == slow or video in {normal, slow}:
            raise ValueError(f"{segment_id}: proof outputs must be distinct from the source")
        for output in (normal, slow):
            if output in output_paths:
                raise ValueError(f"duplicate proof output: {output}")
            output_paths.add(output)
            if output.exists() and not force:
                raise ValueError(f"refusing to overwrite existing proof without --force: {output}")
        normal_media = render_proof(
            video,
            normal,
            start=proof_start,
            duration=proof_end - proof_start,
            slow=False,
            force=force,
        )
        slow_media = render_proof(
            video,
            slow,
            start=proof_start,
            duration=proof_end - proof_start,
            slow=True,
            force=force,
        )
        prepared.append(
            {
                "segment_id": segment_id,
                "speaker": speaker,
                "anchor_text": anchor_text,
                "start": start,
                "end": end,
                "proof_start": round(proof_start, 6),
                "proof_end": round(proof_end, 6),
                "proofs": {
                    "normal_speed": _proof_record(
                        normal, root=root, media=normal_media, playback_speed=1.0
                    ),
                    "quarter_speed_silent": _proof_record(
                        slow, root=root, media=slow_media, playback_speed=0.25
                    ),
                },
            }
        )

    if not prepared:
        raise ValueError("at least one lip-sync segment is required")
    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": {
            "path": _relative(video, root),
            "sha256": _sha256(video),
            "size_bytes": video.stat().st_size,
            "media": source_media,
        },
        "context_seconds": round(context, 6),
        "review_protocol": {
            "passes": [
                "Watch each 1x proof with audio and loop the anchor phrase at least twice.",
                "Check visible mouth closure on p/b/m-like plosive anchors against the heard consonant.",
                "Check that vowel mouth shapes are not consistently early or late.",
                "Watch the silent 0.25x proof for frozen mouth motion while speech is present.",
                "Confirm the intended visible speaker is the one producing the heard words.",
            ],
            "limitations": [
                "This is a human review contract, not automated phoneme or face tracking.",
                "Proofs must come from the final delivery candidate because editing, retiming, or audio replacement can invalidate an upstream clip review.",
                "Reviewer labels are not identity authentication or digital signatures.",
            ],
        },
        "segments": prepared,
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = _response_template(request)
    return request


def _close(actual: float, expected: float, *, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


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
        blockers.append("response_template does not match the canonical request")

    source = request.get("source") or {}
    source_media: Dict[str, Any] = {}
    try:
        video = _project_file(str(source.get("path") or ""), root=root, label="final master")
    except ValueError as exc:
        blockers.append(str(exc))
        video = None
    if video is not None:
        if _sha256(video) != str(source.get("sha256") or ""):
            blockers.append("final master bytes changed after proof preparation")
        if video.stat().st_size != int(source.get("size_bytes") or -1):
            blockers.append("final master size changed after proof preparation")
        try:
            source_media = _media_signature(probe_media(str(video)))
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            if source_media != _media_signature(source.get("media") or {}):
                blockers.append("final master media contract changed after proof preparation")
            if not source_media["has_audio"]:
                blockers.append("final master no longer contains audio")

    segments = request.get("segments") or []
    if not isinstance(segments, list) or not segments:
        blockers.append("request must contain at least one segment")
        segments = []
    seen_ids = set()
    seen_proofs = set()
    source_duration = float((source.get("media") or {}).get("duration") or 0)
    for raw in segments:
        if not isinstance(raw, Mapping):
            blockers.append("request segments must be objects")
            continue
        segment_id = str(raw.get("segment_id") or "")
        if not segment_id or segment_id in seen_ids:
            blockers.append(f"duplicate or empty segment id: {segment_id!r}")
            continue
        seen_ids.add(segment_id)
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
            proof_start = float(raw.get("proof_start"))
            proof_end = float(raw.get("proof_end"))
        except (TypeError, ValueError):
            blockers.append(f"{segment_id}: timing values must be numbers")
            continue
        if (
            not all(math.isfinite(value) for value in (start, end, proof_start, proof_end))
            or start < 0
            or end <= start
            or proof_start < 0
            or proof_start > start
            or proof_end < end
            or proof_end > source_duration + 0.05
        ):
            blockers.append(f"{segment_id}: invalid source/proof timing contract")
        if not str(raw.get("anchor_text") or "").strip():
            blockers.append(f"{segment_id}: anchor_text must not be empty")

        proofs = raw.get("proofs") or {}
        if not isinstance(proofs, Mapping) or set(proofs) != {
            "normal_speed",
            "quarter_speed_silent",
        }:
            blockers.append(f"{segment_id}: proofs must contain normal_speed and quarter_speed_silent")
            continue
        base_duration = proof_end - proof_start
        for label, expected_speed, expected_audio in (
            ("normal_speed", 1.0, True),
            ("quarter_speed_silent", 0.25, False),
        ):
            proof = proofs.get(label) or {}
            if not isinstance(proof, Mapping):
                blockers.append(f"{segment_id}: {label} proof must be an object")
                continue
            try:
                path = _project_file(
                    str(proof.get("path") or ""), root=root, label=f"{segment_id} {label} proof"
                )
            except ValueError as exc:
                blockers.append(str(exc))
                continue
            if str(path) in seen_proofs:
                blockers.append(f"duplicate proof path: {path}")
            seen_proofs.add(str(path))
            if video is not None and path == video:
                blockers.append(f"{segment_id}: proof path collides with final master")
            if _sha256(path) != str(proof.get("sha256") or ""):
                blockers.append(f"{segment_id}: {label} proof bytes changed")
            if path.stat().st_size != int(proof.get("size_bytes") or -1):
                blockers.append(f"{segment_id}: {label} proof size changed")
            if float(proof.get("playback_speed") or 0) != expected_speed:
                blockers.append(f"{segment_id}: {label} playback speed contract changed")
            try:
                live_media = _media_signature(probe_media(str(path)))
            except ValueError as exc:
                blockers.append(str(exc))
                continue
            if live_media != _media_signature(proof.get("media") or {}):
                blockers.append(f"{segment_id}: {label} media contract changed")
            if bool(live_media["has_audio"]) != expected_audio:
                blockers.append(f"{segment_id}: {label} audio contract changed")
            expected_duration = base_duration / expected_speed
            tolerance = max(0.12, 2.0 / max(float(live_media["fps"]), 1.0))
            if not _close(float(live_media["duration"]), expected_duration, tolerance=tolerance):
                blockers.append(f"{segment_id}: {label} duration does not match the proof range")

    return {
        "status": "blocked" if blockers else "ready",
        "blockers": sorted(set(blockers)),
        "summary": {"segments": len(segments), "blocking": len(set(blockers)), "warnings": 0},
    }


def _choice(
    value: Any,
    *,
    allowed: set[str],
    label: str,
    errors: List[str],
) -> str:
    result = str(value or "").strip()
    if result not in allowed:
        errors.append(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return result


def audit_response(request: Mapping[str, Any], response: Mapping[str, Any]) -> Dict[str, Any]:
    request_check = verify_request(request)
    blockers = list(request_check["blockers"])
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if str(response.get("request_id") or "") != str(request.get("request_id") or ""):
        blockers.append("response request_id does not match the review request")
    reviewed_by = str(response.get("reviewed_by") or "").strip()
    if not reviewed_by:
        blockers.append("reviewed_by must not be empty")

    raw_reviews = response.get("reviews") or []
    if not isinstance(raw_reviews, list):
        blockers.append("response reviews must be a list")
        raw_reviews = []
    by_id: Dict[str, Mapping[str, Any]] = {}
    for raw in raw_reviews:
        if not isinstance(raw, Mapping):
            blockers.append("response reviews must contain objects")
            continue
        segment_id = str(raw.get("segment_id") or "")
        if not segment_id or segment_id in by_id:
            blockers.append(f"duplicate or empty response segment id: {segment_id!r}")
            continue
        by_id[segment_id] = raw

    normalized: List[Dict[str, Any]] = []
    expected_ids = []
    for segment in request.get("segments") or []:
        segment_id = str(segment.get("segment_id") or "")
        expected_ids.append(segment_id)
        raw = by_id.get(segment_id)
        if raw is None:
            blockers.append(f"missing review for segment: {segment_id}")
            continue
        errors: List[str] = []
        verdict = _choice(raw.get("verdict"), allowed=VERDICTS, label="verdict", errors=errors)
        plosives = _choice(
            raw.get("plosive_closures"),
            allowed=PLOSIVE_RESULTS,
            label="plosive_closures",
            errors=errors,
        )
        vowels = _choice(
            raw.get("vowel_timing"), allowed=VOWEL_RESULTS, label="vowel_timing", errors=errors
        )
        frozen = _choice(
            raw.get("frozen_mouth"),
            allowed=FROZEN_MOUTH_RESULTS,
            label="frozen_mouth",
            errors=errors,
        )
        speaker = _choice(
            raw.get("speaker_assignment"),
            allowed=SPEAKER_RESULTS,
            label="speaker_assignment",
            errors=errors,
        )
        audio = _choice(
            raw.get("audio_quality"), allowed=AUDIO_RESULTS, label="audio_quality", errors=errors
        )
        repair = _choice(
            raw.get("repair_action"),
            allowed=REPAIR_ACTIONS,
            label="repair_action",
            errors=errors,
        )
        notes = str(raw.get("notes") or "").strip()
        checks_pass = (
            plosives == "aligned"
            and vowels == "aligned"
            and frozen == "absent"
            and speaker == "correct"
            and audio == "clean"
        )
        if verdict == "pass" and not checks_pass:
            errors.append("pass requires all five lip-sync checks to pass")
        if verdict == "pass" and repair != "none":
            errors.append("pass requires repair_action=none")
        if verdict == "fail" and repair == "none":
            errors.append("fail requires a concrete repair_action")
        if verdict == "fail" and not notes:
            errors.append("fail requires notes describing the observed evidence")
        if errors:
            blockers.extend(f"{segment_id}: {error}" for error in errors)
        elif verdict == "fail":
            blockers.append(f"{segment_id}: lip-sync review failed; action={repair}")
        normalized.append(
            {
                "segment_id": segment_id,
                "verdict": verdict,
                "plosive_closures": plosives,
                "vowel_timing": vowels,
                "frozen_mouth": frozen,
                "speaker_assignment": speaker,
                "audio_quality": audio,
                "repair_action": repair,
                "notes": notes,
            }
        )
    extras = sorted(set(by_id) - set(expected_ids))
    if extras:
        blockers.append(f"response contains unknown segments: {', '.join(extras)}")

    unique_blockers = sorted(set(blockers))
    status = "blocked" if unique_blockers else "ready"
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": utc_now(),
        "request": dict(request),
        "response": dict(response),
        "reviews": normalized,
        "status": status,
        "summary": {
            "segments": len(request.get("segments") or []),
            "passed": sum(1 for review in normalized if review["verdict"] == "pass"),
            "failed": sum(1 for review in normalized if review["verdict"] == "fail"),
            "blocking": len(unique_blockers),
            "warnings": 0,
        },
        "blockers": unique_blockers,
        "warnings": [],
        "notes": [
            "The report binds review evidence and decisions; it does not perform automated phoneme alignment.",
            "Reviewer labels are not identity authentication or digital signatures.",
            "Any edit, retime, audio replacement, or re-encode of the final master requires new proofs and a new review.",
        ],
    }
    report["report_id"] = _report_id(report)
    return report


def verify_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if report.get("version") != REPORT_VERSION:
        blockers.append(f"report version must be {REPORT_VERSION}")
    if str(report.get("report_id") or "") != _report_id(report):
        blockers.append("report_id does not match canonical report content")
    request = report.get("request") or {}
    response = report.get("response") or {}
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        blockers.append("report request and response must be objects")
    else:
        try:
            canonical = audit_response(request, response)
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(f"report cannot be re-audited: {exc}")
        else:
            for key in ("reviews", "status", "summary", "blockers", "warnings", "notes"):
                if report.get(key) != canonical.get(key):
                    blockers.append(f"report {key} does not match canonical audit state")
    unique_blockers = sorted(set(blockers))
    try:
        stored_blocking = int((report.get("summary") or {}).get("blocking") or 0)
        stored_warnings = int((report.get("summary") or {}).get("warnings") or 0)
        stored_segments = int((report.get("summary") or {}).get("segments") or 0)
    except (AttributeError, TypeError, ValueError):
        unique_blockers.append("report summary counters must be integers")
        unique_blockers = sorted(set(unique_blockers))
        stored_blocking = 0
        stored_warnings = 0
        stored_segments = 0
    return {
        "status": "blocked" if unique_blockers or stored_blocking else "ready",
        "blockers": unique_blockers,
        "summary": {
            "segments": stored_segments,
            "blocking": len(unique_blockers) + stored_blocking,
            "warnings": stored_warnings,
        },
    }


def emit_request_markdown(request: Mapping[str, Any]) -> str:
    lines = [
        "# Lip-sync Review Request",
        "",
        f"- Request ID: `{request.get('request_id', '')}`",
        f"- Final master: `{(request.get('source') or {}).get('path', '')}`",
        f"- Segments: {len(request.get('segments') or [])}",
        "",
        "## Required playback passes",
        "",
    ]
    lines.extend(f"- {item}" for item in (request.get("review_protocol") or {}).get("passes") or [])
    lines.extend(["", "## Segments", ""])
    for segment in request.get("segments") or []:
        lines.extend(
            [
                f"### {segment.get('segment_id')}",
                "",
                f"- Phrase: `{float(segment.get('start') or 0):.3f}s–{float(segment.get('end') or 0):.3f}s`",
                f"- Anchor text: {segment.get('anchor_text', '')}",
                f"- Speaker: {segment.get('speaker') or '(not labeled)'}",
                f"- 1× proof: `{((segment.get('proofs') or {}).get('normal_speed') or {}).get('path', '')}`",
                f"- 0.25× silent proof: `{((segment.get('proofs') or {}).get('quarter_speed_silent') or {}).get('path', '')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Decision rule",
            "",
            "A pass requires aligned plosive closures, aligned vowel timing, no frozen mouth during speech, the correct visible speaker, clean audio, and `repair_action=none`. Anything unobservable fails closed.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_report_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Lip-sync Review Report",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Segments: {summary.get('segments', 0)}",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "## Reviews",
        "",
    ]
    for review in report.get("reviews") or []:
        lines.append(
            f"- `{review.get('segment_id')}` — **{str(review.get('verdict') or '').upper()}**; "
            f"plosives={review.get('plosive_closures')}, vowels={review.get('vowel_timing')}, "
            f"frozen_mouth={review.get('frozen_mouth')}, speaker={review.get('speaker_assignment')}, "
            f"audio={review.get('audio_quality')}, action={review.get('repair_action')}"
        )
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    lines.append("")
    return "\n".join(lines)


def _prepare_output_paths(
    *,
    root: Path,
    raw_paths: Sequence[Tuple[str, str]],
    forbidden: Sequence[Path],
) -> Dict[str, Path]:
    outputs: Dict[str, Path] = {}
    for label, raw in raw_paths:
        if not raw:
            continue
        path = _project_output(raw, root=root, label=label)
        if path in forbidden or path in outputs.values():
            raise ValueError(f"{label} must not collide with an input or another output: {path}")
        outputs[label] = path
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify source-bound lip-sync reviews from a final master."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Extract 1x/0.25x proof clips and create a review request.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--video", required=True, help="Final delivery candidate inside the project.")
    prepare.add_argument("--segment", action="append", default=[], metavar="ID=START:END")
    prepare.add_argument("--anchor", action="append", default=[], metavar="ID=TEXT")
    prepare.add_argument("--speaker", action="append", default=[], metavar="ID=LABEL")
    prepare.add_argument("--proof-dir", default="verify/lip_sync")
    prepare.add_argument("--context", type=float, default=0.35)
    prepare.add_argument("--output", default="work/lip_sync_review_request.json")
    prepare.add_argument("--markdown", default="work/lip_sync_review_request.md")
    prepare.add_argument("--response-template", default="work/lip_sync_review_response.json")
    prepare.add_argument("--force", action="store_true")

    audit = subparsers.add_parser("audit", help="Audit a completed response against the exact proofs.")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", default="work/lip_sync_review.json")
    audit.add_argument("--markdown", default="work/lip_sync_review.md")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Re-read the final master and proof bytes and verify a report.")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def _parse_labels(values: Sequence[str], *, kind: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"{kind} must use ID=VALUE: {raw}")
        raw_id, value = raw.split("=", 1)
        segment_id = _clean_id(raw_id)
        if segment_id in labels:
            raise ValueError(f"duplicate {kind} id: {segment_id}")
        labels[segment_id] = value.strip()
    return labels


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            root = Path(args.project_dir).expanduser().resolve()
            segments = parse_segment_specs(args.segment, args.anchor)
            speakers = _parse_labels(args.speaker, kind="speaker")
            unknown_speakers = sorted(set(speakers) - {item["segment_id"] for item in segments})
            if unknown_speakers:
                raise ValueError(f"speakers have no matching segment: {', '.join(unknown_speakers)}")
            for segment in segments:
                segment["speaker"] = speakers.get(segment["segment_id"], "")
            source = _project_file(args.video, root=root, label="final master")
            proof_root = _project_output(args.proof_dir, root=root, label="proof directory")
            predicted_proofs = tuple(
                proof_root / f"{segment['segment_id']}{suffix}"
                for segment in segments
                for suffix in ("_1x.mp4", "_025x_silent.mp4")
            )
            outputs = _prepare_output_paths(
                root=root,
                raw_paths=(
                    ("request output", args.output),
                    ("request Markdown", args.markdown),
                    ("response template", args.response_template),
                ),
                forbidden=(source, *predicted_proofs),
            )
            for path in outputs.values():
                if path.exists() and not args.force:
                    raise ValueError(f"refusing to overwrite existing file without --force: {path}")
            request = prepare_request(
                project_dir=str(root),
                video_path=args.video,
                segments=segments,
                proof_dir=args.proof_dir,
                context=args.context,
                force=args.force,
            )
            proof_paths = {
                _project_file(proof["path"], root=root, label="generated proof")
                for segment in request["segments"]
                for proof in segment["proofs"].values()
            }
            if any(path in proof_paths for path in outputs.values()):
                raise ValueError("request outputs must not collide with generated proof files")
            _write_json(outputs["request output"], request, force=args.force)
            _write_text(outputs["request Markdown"], emit_request_markdown(request), force=args.force)
            _write_json(
                outputs["response template"], request["response_template"], force=args.force
            )
            print(
                f"Lip-sync review request: pending segments={len(request['segments'])} "
                f"request_id={request['request_id']}"
            )
            return 0

        if args.command == "audit":
            request = _load_json(args.request)
            response = _load_json(args.response)
            root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
            forbidden = [
                _project_file(args.request, root=root, label="request input"),
                _project_file(args.response, root=root, label="response input"),
                _project_file(
                    str((request.get("source") or {}).get("path") or ""),
                    root=root,
                    label="final master",
                ),
            ]
            forbidden.extend(
                _project_file(str(proof.get("path") or ""), root=root, label="proof input")
                for segment in request.get("segments") or []
                for proof in (segment.get("proofs") or {}).values()
            )
            outputs = _prepare_output_paths(
                root=root,
                raw_paths=(("report output", args.output), ("report Markdown", args.markdown)),
                forbidden=tuple(forbidden),
            )
            report = audit_response(request, response)
            _write_json(outputs["report output"], report, force=args.force)
            _write_text(outputs["report Markdown"], emit_report_markdown(report), force=args.force)
            print(
                f"Lip-sync review: {report['status']} blocking={report['summary']['blocking']} "
                f"passed={report['summary']['passed']}/{report['summary']['segments']}"
            )
            return 2 if args.strict and report["status"] == "blocked" else 0

        report = _load_json(args.report)
        verification = verify_report(report)
        print(
            f"Lip-sync review verify: {verification['status']} "
            f"blocking={verification['summary']['blocking']}"
        )
        for blocker in verification["blockers"]:
            print(f"- {blocker}")
        return 2 if args.strict and verification["status"] == "blocked" else 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
