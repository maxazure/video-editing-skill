#!/usr/bin/env python3
"""Extract and verify a transferable story formula from a reference video.

The workflow binds a local reference video, its timecoded transcript, and the
target storyboard.  A reviewer labels the reference's narrative/emotional beats
and maps every target shot to that structure.  The resulting report is designed
for prompt construction; it never copies reference pixels/audio or submits a
generation job.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from generated_clip_review import probe_media


REQUEST_VERSION = "reference_story_formula_request.v1"
RESPONSE_VERSION = "reference_story_formula_response.v1"
REPORT_VERSION = "reference_story_formula.v1"
MECHANISMS = {
    "hook",
    "setup",
    "tension",
    "escalation",
    "reveal",
    "proof",
    "relief",
    "payoff",
    "cta",
    "custom",
}
COPY_POLICY_FIELDS = (
    "reference_pixels_excluded",
    "reference_audio_excluded",
    "reference_words_excluded",
    "reference_branding_excluded",
    "reference_specific_plot_excluded",
)


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
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _media_signature(media: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "duration": round(float(media.get("duration") or 0), 6),
        "fps": round(float(media.get("fps") or 0), 6),
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "video_codec": _text(media.get("video_codec")),
        "pixel_format": _text(media.get("pixel_format")),
        "has_audio": bool(media.get("has_audio")),
        "audio_codec": _text(media.get("audio_codec")),
        "sample_rate": int(media.get("sample_rate") or 0),
        "channels": int(media.get("channels") or 0),
    }


def _number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return round(result, 6)


def _segments(transcript: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for position, raw in enumerate(transcript.get("segments") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"transcript segment #{position} must be an object")
        segment_id = _text(raw.get("id") if raw.get("id") is not None else raw.get("segment_id"))
        if not segment_id:
            segment_id = str(position)
        start = _number(raw.get("start"), field=f"segment {segment_id} start")
        end = _number(raw.get("end"), field=f"segment {segment_id} end")
        text = _text(raw.get("text"))
        if start < 0 or end <= start:
            raise ValueError(f"segment {segment_id} must have a positive ordered range")
        if not text:
            raise ValueError(f"segment {segment_id} has no text")
        rows.append({"id": segment_id, "start": start, "end": end, "text": text})
    if not rows:
        raise ValueError("reference transcript has no usable segments")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("reference transcript segment ids must be unique")
    previous_end = -1.0
    for row in rows:
        if row["start"] < previous_end - 0.05:
            raise ValueError("reference transcript segments must be time ordered without material overlap")
        previous_end = max(previous_end, float(row["end"]))
    return rows


def _shots(storyboard: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if storyboard.get("version") != "storyboard_plan.v1":
        raise ValueError("target storyboard version must be storyboard_plan.v1")
    rows: List[Dict[str, Any]] = []
    for position, raw in enumerate(storyboard.get("shots") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"target storyboard shot #{position} must be an object")
        shot_id = _text(raw.get("id"))
        if not shot_id:
            raise ValueError(f"target storyboard shot #{position} has no id")
        start = _number(raw.get("start"), field=f"shot {shot_id} start")
        end = _number(raw.get("end"), field=f"shot {shot_id} end")
        if end <= start:
            raise ValueError(f"target storyboard shot {shot_id} must have a positive ordered range")
        visual = raw.get("visual") if isinstance(raw.get("visual"), Mapping) else {}
        rows.append(
            {
                "shot_id": shot_id,
                "section": _text(raw.get("section")),
                "start": start,
                "end": end,
                "narration": _text(raw.get("narration")),
                "visual": {
                    "first_frame": _text(visual.get("first_frame")),
                    "motion": _text(visual.get("motion")),
                    "last_frame": _text(visual.get("last_frame")),
                },
            }
        )
    if not rows:
        raise ValueError("target storyboard has no usable shots")
    ids = [row["shot_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("target storyboard shot ids must be unique")
    return rows


def _partition(segments: Sequence[Mapping[str, Any]], beat_count: int) -> List[List[Mapping[str, Any]]]:
    if beat_count < 1 or beat_count > 12:
        raise ValueError("beat_count must be between 1 and 12")
    count = min(beat_count, len(segments))
    groups: List[List[Mapping[str, Any]]] = []
    for index in range(count):
        start = index * len(segments) // count
        end = (index + 1) * len(segments) // count
        group = list(segments[start:end])
        if group:
            groups.append(group)
    return groups


def _request_id(request: Mapping[str, Any]) -> str:
    return _digest(
        "rsfr",
        {
            key: value
            for key, value in request.items()
            if key not in {"generated_at", "request_id", "response_template"}
        },
    )


def _report_id(report: Mapping[str, Any]) -> str:
    return _digest(
        "rsf",
        {key: value for key, value in report.items() if key not in {"generated_at", "report_id"}},
    )


def build_request(
    *,
    root: Path,
    reference_video: Path,
    reference_transcript: Path,
    target_storyboard: Path,
    beat_count: int = 5,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    transcript = _load_json(reference_transcript)
    storyboard = _load_json(target_storyboard)
    segments = _segments(transcript)
    shots = _shots(storyboard)
    media = _media_signature(probe_media(str(reference_video)))
    if media["duration"] <= 0 or media["width"] <= 0 or media["height"] <= 0:
        raise ValueError("reference video must have a decodable video stream and positive duration")
    if float(segments[-1]["end"]) > float(media["duration"]) + 0.25:
        raise ValueError("reference transcript extends beyond the reference video")

    groups = _partition(segments, beat_count)
    beat_templates = []
    for position, group in enumerate(groups, start=1):
        beat_templates.append(
            {
                "beat_id": f"beat_{position:03d}",
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "evidence_segment_ids": [row["id"] for row in group],
                "mechanism": "",
                "mechanism_custom": "",
                "viewer_state_before": "",
                "viewer_state_after": "",
                "trigger": "",
                "camera_function": "",
                "transferable_rule": "",
                "do_not_copy": "",
                "decision": "",
                "review_note": "",
            }
        )
    mapping_templates = [
        {
            "shot_id": shot["shot_id"],
            "beat_id": "",
            "content_anchor": "",
            "viewer_shift": "",
            "surface_change": "",
            "visual_action": "",
            "decision": "",
            "review_note": "",
        }
        for shot in shots
    ]
    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": {
            "reference_video": {**_file_record(root, reference_video), "media": media},
            "reference_transcript": _file_record(root, reference_transcript),
            "target_storyboard": {
                **_file_record(root, target_storyboard),
                "version": storyboard.get("version"),
            },
        },
        "reference": {
            "duration": media["duration"],
            "transcript_start": segments[0]["start"],
            "transcript_end": segments[-1]["end"],
            "segments": segments,
        },
        "target": {"shots": shots},
        "params": {"beat_count": len(groups), "copy_risk_chars": 18},
        "review_rules": [
            "Describe the reference's viewer-state changes and narrative mechanism from the full video and transcript, not from isolated keywords.",
            "Every reference transcript segment belongs to exactly one ordered beat and every target shot maps to exactly one beat.",
            "Transfer only abstract structure. Exclude the reference's pixels, audio, wording, branding, and specific plot events.",
            "Each content anchor states the new subject or product surface, the mapped beat, and the intended viewer shift.",
            "The report does not prove originality, rights clearance, retention, or generation quality; review the finished candidate separately.",
        ],
        "summary": {
            "reference_segments": len(segments),
            "formula_beats": len(groups),
            "target_shots": len(shots),
            "blocking": 0,
            "warnings": 0,
        },
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = {
        "version": RESPONSE_VERSION,
        "request_id": request["request_id"],
        "reviewed_by": "",
        "review_notes": "",
        "formula_name": "",
        "formula_summary": "",
        "copy_policy": {field: False for field in COPY_POLICY_FIELDS},
        "formula_beats": beat_templates,
        "shot_mappings": mapping_templates,
    }
    return request


def _copy_risks(request: Mapping[str, Any]) -> List[Dict[str, Any]]:
    reference_text = " ".join(_text(row.get("text")) for row in request.get("reference", {}).get("segments") or [])
    reference_normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", reference_text).casefold()
    threshold = int((request.get("params") or {}).get("copy_risk_chars") or 18)
    findings: List[Dict[str, Any]] = []
    for shot in request.get("target", {}).get("shots") or []:
        narration = _text(shot.get("narration"))
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", narration).casefold()
        if not normalized or not reference_normalized:
            continue
        match = difflib.SequenceMatcher(None, reference_normalized, normalized, autojunk=False).find_longest_match()
        if match.size >= threshold:
            findings.append(
                {
                    "shot_id": shot.get("shot_id"),
                    "shared_characters": match.size,
                    "target_excerpt": normalized[match.b : match.b + min(match.size, 48)],
                    "message": "Target narration shares a long normalized span with the reference; confirm it is a necessary fact/name or rewrite it.",
                }
            )
    return findings


def _audit(request: Mapping[str, Any], response: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    if request.get("version") != REQUEST_VERSION:
        blockers.append(f"request version must be {REQUEST_VERSION}")
    if request.get("request_id") != _request_id(request):
        blockers.append("request_id does not match canonical request contents")
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if response.get("request_id") != request.get("request_id"):
        blockers.append("response request_id does not match the request")
    for field in ("reviewed_by", "review_notes", "formula_name", "formula_summary"):
        if not _text(response.get(field)):
            blockers.append(f"response {field} is required")
    copy_policy = response.get("copy_policy") if isinstance(response.get("copy_policy"), Mapping) else {}
    for field in COPY_POLICY_FIELDS:
        if copy_policy.get(field) is not True:
            blockers.append(f"copy_policy {field} must be true")

    segments = request.get("reference", {}).get("segments") or []
    segment_by_id = {str(row.get("id")): row for row in segments if isinstance(row, Mapping)}
    expected_beat_ids = [row.get("beat_id") for row in request.get("response_template", {}).get("formula_beats") or []]
    beats = [row for row in response.get("formula_beats") or [] if isinstance(row, Mapping)]
    beat_ids = [_text(row.get("beat_id")) for row in beats]
    if beat_ids != expected_beat_ids:
        blockers.append("formula beat ids/order must match the response template")
    evidence_seen: List[str] = []
    previous_end = -1.0
    for beat in beats:
        beat_id = _text(beat.get("beat_id")) or "unknown"
        mechanism = _text(beat.get("mechanism"))
        if mechanism not in MECHANISMS:
            blockers.append(f"{beat_id} mechanism must be one of {sorted(MECHANISMS)}")
        if mechanism == "custom" and not _text(beat.get("mechanism_custom")):
            blockers.append(f"{beat_id} mechanism_custom is required for custom")
        for field in (
            "viewer_state_before",
            "viewer_state_after",
            "trigger",
            "camera_function",
            "transferable_rule",
            "do_not_copy",
            "review_note",
        ):
            if not _text(beat.get(field)):
                blockers.append(f"{beat_id} {field} is required")
        if _text(beat.get("decision")) != "approve":
            blockers.append(f"{beat_id} decision must be approve")
        evidence = [_text(value) for value in beat.get("evidence_segment_ids") or [] if _text(value)]
        if not evidence:
            blockers.append(f"{beat_id} needs evidence_segment_ids")
            continue
        if any(segment_id not in segment_by_id for segment_id in evidence):
            blockers.append(f"{beat_id} references an unknown transcript segment")
            continue
        evidence_seen.extend(evidence)
        derived_start = float(segment_by_id[evidence[0]]["start"])
        derived_end = float(segment_by_id[evidence[-1]]["end"])
        try:
            start = _number(beat.get("start"), field=f"{beat_id} start")
            end = _number(beat.get("end"), field=f"{beat_id} end")
        except ValueError as exc:
            blockers.append(str(exc))
            continue
        if abs(start - derived_start) > 0.05 or abs(end - derived_end) > 0.05:
            blockers.append(f"{beat_id} range must match its first/last evidence segments")
        if start < previous_end - 0.05:
            blockers.append(f"{beat_id} is out of chronological order")
        previous_end = end
    expected_segment_ids = [str(row.get("id")) for row in segments]
    if evidence_seen != expected_segment_ids:
        blockers.append("formula beat evidence must cover every transcript segment exactly once in order")

    target_shots = request.get("target", {}).get("shots") or []
    expected_shot_ids = [str(row.get("shot_id")) for row in target_shots]
    mappings = [row for row in response.get("shot_mappings") or [] if isinstance(row, Mapping)]
    mapping_ids = [_text(row.get("shot_id")) for row in mappings]
    if mapping_ids != expected_shot_ids:
        blockers.append("shot mapping ids/order must match the target storyboard")
    beat_order = {beat_id: index for index, beat_id in enumerate(beat_ids)}
    previous_beat = -1
    for mapping in mappings:
        shot_id = _text(mapping.get("shot_id")) or "unknown"
        beat_id = _text(mapping.get("beat_id"))
        if beat_id not in beat_order:
            blockers.append(f"shot {shot_id} maps to an unknown beat")
        else:
            if beat_order[beat_id] < previous_beat:
                blockers.append(f"shot {shot_id} moves backward in the reference formula")
            previous_beat = beat_order[beat_id]
        for field in ("content_anchor", "viewer_shift", "surface_change", "visual_action", "review_note"):
            if not _text(mapping.get(field)):
                blockers.append(f"shot {shot_id} {field} is required")
        if _text(mapping.get("decision")) != "approve":
            blockers.append(f"shot {shot_id} decision must be approve")

    for risk in _copy_risks(request):
        warnings.append(
            f"possible reference wording reuse in {risk['shot_id']}: {risk['shared_characters']} normalized characters"
        )
    return sorted(set(blockers)), sorted(set(warnings))


def build_report(
    *,
    root: Path,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    request_path: Path,
    response_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    blockers, warnings = _audit(request, response)
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "request_id": request.get("request_id"),
        "inputs": dict(request.get("inputs") or {}),
        "files": {
            "request": _file_record(root, request_path),
            "response": _file_record(root, response_path),
        },
        "formula": {
            "name": _text(response.get("formula_name")),
            "summary": _text(response.get("formula_summary")),
            "beats": [dict(row) for row in response.get("formula_beats") or [] if isinstance(row, Mapping)],
        },
        "target": {
            "storyboard_shots": [dict(row) for row in request.get("target", {}).get("shots") or []],
            "shot_mappings": [dict(row) for row in response.get("shot_mappings") or [] if isinstance(row, Mapping)],
        },
        "copy_review": {
            "policy": dict(response.get("copy_policy") or {}),
            "possible_wording_reuse": _copy_risks(request),
        },
        "review": {
            "reviewed_by": _text(response.get("reviewed_by")),
            "notes": _text(response.get("review_notes")),
        },
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "formula_beats": len(response.get("formula_beats") or []),
            "target_shots": len(response.get("shot_mappings") or []),
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
        "notes": [
            "This artifact transfers reviewed narrative/emotional structure only; it does not authorize copying reference media, wording, branding, or plot details.",
            "A wording-overlap warning is a triage signal, not a plagiarism or rights determination.",
            "Review final prompts, generated clips, the assembled sequence, and applicable rights separately.",
        ],
    }
    report["report_id"] = _report_id(report)
    return report


def _record_matches(root: Path, record: Mapping[str, Any], *, label: str) -> Optional[str]:
    try:
        path = _project_file(root, _text(record.get("path")), label=label)
    except (OSError, ValueError) as exc:
        return str(exc)
    expected = {
        "path": _text(record.get("path")),
        "size_bytes": int(record.get("size_bytes") or 0),
        "sha256": _text(record.get("sha256")),
    }
    if _file_record(root, path) != expected:
        return f"{label} bytes or path have drifted"
    return None


def verify_report(report_path: str, *, project_dir: str = ".") -> Dict[str, Any]:
    root = _root(project_dir)
    path = _project_file(root, report_path, label="reference story formula report")
    report = _load_json(path)
    blockers: List[str] = []
    warnings: List[str] = []
    if report.get("version") != REPORT_VERSION:
        blockers.append(f"report version must be {REPORT_VERSION}")
    if report.get("project_root") != str(root):
        blockers.append("report project_root does not match the live project")
    for label, record in (report.get("inputs") or {}).items():
        if not isinstance(record, Mapping):
            blockers.append(f"missing input record: {label}")
            continue
        error = _record_matches(root, record, label=str(label).replace("_", " "))
        if error:
            blockers.append(error)
    files = report.get("files") if isinstance(report.get("files"), Mapping) else {}
    live_paths: Dict[str, Path] = {}
    for label in ("request", "response"):
        record = files.get(label) if isinstance(files.get(label), Mapping) else None
        if record is None:
            blockers.append(f"missing {label} file record")
            continue
        error = _record_matches(root, record, label=f"formula {label}")
        if error:
            blockers.append(error)
            continue
        live_paths[label] = _project_file(root, _text(record.get("path")), label=f"formula {label}")
    try:
        video_record = (report.get("inputs") or {}).get("reference_video") or {}
        video_path = _project_file(root, _text(video_record.get("path")), label="reference video")
        if _media_signature(probe_media(str(video_path))) != video_record.get("media"):
            blockers.append("reference video media contract has drifted")
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(str(exc))
    if not blockers and set(live_paths) == {"request", "response"}:
        try:
            request = _load_json(live_paths["request"])
            response = _load_json(live_paths["response"])
            rebuilt = build_report(
                root=root,
                request=request,
                response=response,
                request_path=live_paths["request"],
                response_path=live_paths["response"],
                generated_at=_text(report.get("generated_at")) or None,
            )
            if rebuilt.get("report_id") != report.get("report_id"):
                blockers.append("report does not match the live request/response derivation")
            warnings.extend(rebuilt.get("warnings") or [])
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(f"live report derivation failed: {exc}")
    if report.get("report_id") != _report_id(report):
        blockers.append("report_id does not match canonical report contents")
    if int((report.get("summary") or {}).get("blocking") or 0):
        blockers.extend(str(value) for value in report.get("blockers") or [])
    return {
        "version": REPORT_VERSION,
        "report_id": report.get("report_id"),
        "status": "blocked" if blockers else ("review" if warnings else "ready"),
        "summary": {"blocking": len(set(blockers)), "warnings": len(set(warnings))},
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def emit_markdown(payload: Mapping[str, Any]) -> str:
    if payload.get("version") == REQUEST_VERSION:
        lines = [
            "# Reference Story Formula Request",
            "",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Reference segments: {payload.get('summary', {}).get('reference_segments', 0)}",
            f"- Formula beats: {payload.get('summary', {}).get('formula_beats', 0)}",
            f"- Target shots: {payload.get('summary', {}).get('target_shots', 0)}",
            "",
            "## Review rules",
            "",
            *[f"- {rule}" for rule in payload.get("review_rules") or []],
            "",
            "## Reference transcript",
            "",
            "| segment | time | text |",
            "|---|---:|---|",
        ]
        for segment in payload.get("reference", {}).get("segments") or []:
            segment_text = _text(segment.get("text")).replace("|", "\\|")
            lines.append(
                f"| `{segment.get('id', '')}` | {segment.get('start', 0):.2f}–{segment.get('end', 0):.2f}s | {segment_text} |"
            )
        return "\n".join(lines) + "\n"

    lines = [
        "# Reference Story Formula Report",
        "",
        f"- Report ID: `{payload.get('report_id', '')}`",
        f"- Status: `{payload.get('status', '')}`",
        f"- Formula: {payload.get('formula', {}).get('name', '')}",
        f"- Summary: {payload.get('formula', {}).get('summary', '')}",
        f"- Blocking: {payload.get('summary', {}).get('blocking', 0)}",
        f"- Warnings: {payload.get('summary', {}).get('warnings', 0)}",
        "",
        "## Formula beats",
        "",
        "| beat | time | mechanism | viewer shift | transferable rule |",
        "|---|---:|---|---|---|",
    ]
    for beat in payload.get("formula", {}).get("beats") or []:
        shift = f"{_text(beat.get('viewer_state_before'))} → {_text(beat.get('viewer_state_after'))}"
        lines.append(
            "| `{}` | {:.2f}–{:.2f}s | {} | {} | {} |".format(
                beat.get("beat_id", ""),
                float(beat.get("start") or 0),
                float(beat.get("end") or 0),
                _text(beat.get("mechanism")).replace("|", "\\|"),
                shift.replace("|", "\\|"),
                _text(beat.get("transferable_rule")).replace("|", "\\|"),
            )
        )
    lines.extend(["", "## Target mapping", ""])
    for mapping in payload.get("target", {}).get("shot_mappings") or []:
        lines.extend(
            [
                f"### {mapping.get('shot_id', '')} → {mapping.get('beat_id', '')}",
                "",
                f"- Content anchor: {_text(mapping.get('content_anchor'))}",
                f"- Viewer shift: {_text(mapping.get('viewer_shift'))}",
                f"- Surface change: {_text(mapping.get('surface_change'))}",
                f"- Visual action: {_text(mapping.get('visual_action'))}",
                "",
            ]
        )
    if payload.get("blockers"):
        lines.extend(["## Blockers", "", *[f"- {item}" for item in payload.get("blockers") or []], ""])
    if payload.get("warnings"):
        lines.extend(["## Warnings", "", *[f"- {item}" for item in payload.get("warnings") or []], ""])
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract and verify a source-bound reference story formula for a target storyboard."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Bind reference media/transcript and target shots; write a review template.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--reference-video", required=True)
    prepare.add_argument("--reference-transcript", required=True)
    prepare.add_argument("--target-storyboard", required=True)
    prepare.add_argument("--beat-count", type=int, default=5)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--markdown")
    prepare.add_argument("--response-template", required=True)
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true")

    audit = sub.add_parser("audit", help="Validate reviewed beats/mappings and write the formula report.")
    audit.add_argument("--project-dir", default=".")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Live-verify source/request/response bytes and derived report state.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--report", required=True)
    verify.add_argument("--output")
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    root = _root(args.project_dir)
    if args.command == "prepare":
        reference_video = _project_file(root, args.reference_video, label="reference video")
        reference_transcript = _project_file(root, args.reference_transcript, label="reference transcript")
        target_storyboard = _project_file(root, args.target_storyboard, label="target storyboard")
        protected = [reference_video, reference_transcript, target_storyboard]
        output = _output_file(root, args.output, label="request output", protected=protected, force=args.force)
        response_output = _output_file(
            root,
            args.response_template,
            label="response template output",
            protected=[*protected, output],
            force=args.force,
        )
        markdown = (
            _output_file(root, args.markdown, label="markdown output", protected=[*protected, output, response_output], force=args.force)
            if args.markdown
            else None
        )
        request = build_request(
            root=root,
            reference_video=reference_video,
            reference_transcript=reference_transcript,
            target_storyboard=target_storyboard,
            beat_count=args.beat_count,
        )
        _write_json(output, request)
        _write_json(response_output, request["response_template"])
        if markdown:
            markdown.write_text(emit_markdown(request), encoding="utf-8")
        print(f"Wrote reference story formula request: {output}")
        print(f"Wrote response template: {response_output}")
        return 2 if args.strict and request["summary"]["blocking"] else 0

    if args.command == "audit":
        request_path = _project_file(root, args.request, label="formula request")
        response_path = _project_file(root, args.response, label="formula response")
        output = _output_file(root, args.output, label="report output", protected=[request_path, response_path], force=args.force)
        markdown = (
            _output_file(root, args.markdown, label="markdown output", protected=[request_path, response_path, output], force=args.force)
            if args.markdown
            else None
        )
        report = build_report(
            root=root,
            request=_load_json(request_path),
            response=_load_json(response_path),
            request_path=request_path,
            response_path=response_path,
        )
        _write_json(output, report)
        if markdown:
            markdown.write_text(emit_markdown(report), encoding="utf-8")
        print(
            f"Wrote reference story formula report: {output}; status={report['status']} "
            f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
        )
        return 2 if args.strict and report["summary"]["blocking"] else 0

    verification = verify_report(args.report, project_dir=str(root))
    if args.output:
        output = _output_file(root, args.output, label="verification output", protected=[], force=True)
        _write_json(output, verification)
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 2 if args.strict and verification["summary"]["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
