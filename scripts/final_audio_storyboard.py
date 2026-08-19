#!/usr/bin/env python3
"""Rebuild an audio storyboard from a locked visual EDL.

The script is provider-neutral and never generates audio.  It binds a reviewed
audio plan to the final EDL timeline and the original storyboard, so narration,
dialogue, sound effects, and music for omitted visuals cannot survive by
accident.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REQUEST_VERSION = "final_audio_storyboard_request.v1"
RESPONSE_VERSION = "final_audio_storyboard_response.v1"
REPORT_VERSION = "final_audio_storyboard.v1"
AUDIO_STRATEGIES = {"single_track", "sectioned_tracks", "stems"}
STEM_TYPES = {
    "dialogue",
    "narration",
    "source_audio",
    "ambience",
    "foley",
    "music_like_bed",
    "special_fx",
}
OMITTED_DISPOSITIONS = {"remove", "rewrite_into_adjacent", "offscreen_bridge"}
EPSILON = 1e-4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


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
    protected_resolved = {path.resolve(strict=False) for path in protected}
    if resolved in protected_resolved:
        raise ValueError(f"output must not overwrite an input: {resolved}")
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


def _float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return round(result, 4)


def _story_visual(shot: Mapping[str, Any]) -> str:
    visual = shot.get("visual") if isinstance(shot.get("visual"), Mapping) else {}
    parts = [
        str(visual.get("first_frame") or "").strip(),
        str(visual.get("motion") or "").strip(),
        str(visual.get("last_frame") or "").strip(),
    ]
    compact = [part for part in parts if part]
    return " / ".join(compact) or str(shot.get("narration") or shot.get("section") or "").strip()


def _source_asset(root: Path, raw: Any, blockers: List[str]) -> Dict[str, Any]:
    source = str(raw or "").strip()
    if not source:
        blockers.append("EDL event has no source path")
        return {"path": "", "status": "missing"}
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        blockers.append(f"EDL source must not be a symlink: {source}")
        return {"path": source, "status": "unsafe"}
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        blockers.append(f"EDL source is missing: {source}")
        return {"path": source, "status": "missing"}
    if not _inside(root, resolved):
        blockers.append(f"EDL source must be inside the project: {resolved}")
        return {"path": str(resolved), "status": "outside_project"}
    if not resolved.is_file():
        blockers.append(f"EDL source is not a file: {resolved}")
        return {"path": _relative(root, resolved), "status": "invalid"}
    record = _file_record(root, resolved)
    record["status"] = "ready"
    return record


def _match_story_id(event: Mapping[str, Any], story_ids: set[str]) -> Tuple[str, List[str]]:
    source_stem = Path(str(event.get("source") or "")).stem
    candidates = [
        event.get("story_id"),
        event.get("source_segment_id"),
        event.get("label"),
        source_stem,
    ]
    matches: List[str] = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value in story_ids and value not in matches:
            matches.append(value)
    if len(matches) == 1:
        return matches[0], matches
    return "", matches


def build_request(
    *,
    root: Path,
    edl_path: Path,
    storyboard_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    edl = _load_json(edl_path)
    storyboard = _load_json(storyboard_path)
    if edl.get("kind") != "nle_handoff_edl" or not isinstance(edl.get("events"), list):
        raise ValueError("EDL manifest must be export_edl.py nle_handoff_edl JSON")
    if storyboard.get("version") != "storyboard_plan.v1" or not isinstance(storyboard.get("shots"), list):
        raise ValueError("storyboard must be storyboard_plan.v1 JSON")

    shots: Dict[str, Mapping[str, Any]] = {}
    blockers: List[str] = []
    warnings: List[str] = []
    for index, raw in enumerate(storyboard["shots"], start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"storyboard shot #{index} is not an object")
            continue
        story_id = str(raw.get("id") or "").strip()
        if not story_id:
            blockers.append(f"storyboard shot #{index} has no id")
        elif story_id in shots:
            blockers.append(f"duplicate storyboard shot id: {story_id}")
        else:
            shots[story_id] = raw

    source_records: Dict[str, Dict[str, Any]] = {}
    sections: List[Dict[str, Any]] = []
    cursor = 0.0
    used_story_ids: List[str] = []
    events = edl["events"]
    for position, raw in enumerate(events, start=1):
        if not isinstance(raw, Mapping):
            blockers.append(f"EDL event #{position} is not an object")
            continue
        final_start = _float(raw.get("record_start"), field=f"event #{position} record_start")
        final_end = _float(raw.get("record_end"), field=f"event #{position} record_end")
        source_start = _float(raw.get("source_start"), field=f"event #{position} source_start")
        source_end = _float(raw.get("source_end"), field=f"event #{position} source_end")
        if final_end <= final_start:
            blockers.append(f"EDL event #{position} has non-positive final duration")
        if source_end <= source_start:
            blockers.append(f"EDL event #{position} has non-positive source duration")
        if abs(final_start - cursor) > EPSILON:
            relation = "gap" if final_start > cursor else "overlap"
            blockers.append(f"EDL final timeline has a {relation} before event #{position}: {cursor:.4f}->{final_start:.4f}")
        cursor = final_end

        source_record = _source_asset(root, raw.get("source"), blockers)
        source_key = str(source_record.get("path") or raw.get("source") or f"event-{position}")
        source_records[source_key] = source_record
        story_id, matches = _match_story_id(raw, set(shots))
        if not story_id:
            if matches:
                blockers.append(f"EDL event #{position} maps ambiguously to storyboard shots: {', '.join(matches)}")
            else:
                blockers.append(
                    f"EDL event #{position} is not mapped to a storyboard shot; set its label to a shot id"
                )
        else:
            used_story_ids.append(story_id)
        shot = shots.get(story_id, {})
        planned_duration = _float(shot.get("duration", 0), field=f"story {story_id or position} duration")
        final_duration = round(max(0.0, final_end - final_start), 4)
        if planned_duration > 0 and final_duration < planned_duration * 0.75:
            warnings.append(
                f"{story_id or f'event #{position}'} keeps {final_duration:.2f}s of a {planned_duration:.2f}s planned beat; rewrite its audio"
            )
        sections.append(
            {
                "section_id": f"audio_{position:03d}",
                "event_number": int(raw.get("number") or position),
                "story_id": story_id,
                "final_start": final_start,
                "final_end": final_end,
                "duration": final_duration,
                "source_path": str(source_record.get("path") or raw.get("source") or ""),
                "source_start": source_start,
                "source_end": source_end,
                "planned_duration": planned_duration,
                "suggested_visual_beat": _story_visual(shot),
                "suggested_dialogue": str(shot.get("dialogue") or "").strip(),
                "suggested_narration": str(shot.get("narration") or "").strip(),
            }
        )

    declared_count = int(edl.get("event_count") or 0)
    if declared_count != len(events):
        blockers.append(f"EDL event_count={declared_count} does not match events={len(events)}")
    declared_duration = _float(edl.get("duration_seconds", 0), field="EDL duration_seconds")
    if abs(declared_duration - cursor) > EPSILON:
        blockers.append(f"EDL duration_seconds={declared_duration:.4f} does not match final timeline={cursor:.4f}")

    duplicates = sorted({story_id for story_id in used_story_ids if used_story_ids.count(story_id) > 1})
    for story_id in duplicates:
        warnings.append(f"storyboard shot {story_id} appears in multiple EDL events; do not duplicate its voiced line")
    omitted = [
        {
            "story_id": story_id,
            "planned_duration": _float(shot.get("duration", 0), field=f"story {story_id} duration"),
            "narration": str(shot.get("narration") or "").strip(),
            "visual_beat": _story_visual(shot),
        }
        for story_id, shot in shots.items()
        if story_id not in used_story_ids
    ]

    response_sections = [
        {
            "section_id": item["section_id"],
            "story_id": item["story_id"],
            "final_start": item["final_start"],
            "final_end": item["final_end"],
            "source_start": item["source_start"],
            "source_end": item["source_end"],
            "visual_beat": item["suggested_visual_beat"],
            "dialogue": item["suggested_dialogue"],
            "narration": item["suggested_narration"],
            "sound_design": "",
            "music": "",
            "stems": [],
            "preserve_source_audio": False,
            "source_audio_note": "",
            "decision_note": "",
        }
        for item in sections
    ]
    response_omitted = [
        {"story_id": item["story_id"], "disposition": "", "target_section_id": "", "note": ""}
        for item in omitted
    ]
    payload: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": {
            "edl": _file_record(root, edl_path),
            "storyboard": _file_record(root, storyboard_path),
            "source_assets": sorted(source_records.values(), key=lambda item: str(item.get("path"))),
        },
        "timeline_duration": cursor,
        "sections": sections,
        "omitted_story": omitted,
        "review_rules": [
            "Use final_start/final_end as the audio timeline; source times are evidence only.",
            "Rewrite shortened narration instead of carrying the pre-EDL script unchanged.",
            "Every voiced line must appear once across all final sections.",
            "Every omitted story beat must be removed, deliberately bridged, or rewritten into one retained section.",
            "Write explicit 'none' when a section intentionally has no music or designed sound.",
            "Do not submit provider work from this JSON directly; turn the approved plan into a timed provider cue sheet.",
        ],
        "response_template": {
            "version": RESPONSE_VERSION,
            "request_id": "",
            "reviewed_by": "",
            "audio_strategy": "sectioned_tracks",
            "shared_tone": "",
            "sections": response_sections,
            "omitted_story": response_omitted,
            "review_notes": "",
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "events": len(sections),
            "mapped_events": sum(1 for item in sections if item["story_id"]),
            "omitted_story_beats": len(omitted),
            "source_assets": len(source_records),
            "blocking": len(set(blockers)),
            "warnings": len(set(warnings)),
        },
    }
    payload["request_id"] = _digest({key: value for key, value in payload.items() if key != "request_id"})
    payload["response_template"]["request_id"] = payload["request_id"]
    return payload


def _verify_request(request: Mapping[str, Any], root: Path) -> List[str]:
    errors: List[str] = []
    if request.get("version") != REQUEST_VERSION:
        return [f"unsupported request version: {request.get('version')}"]
    if str(request.get("project_root") or "") != str(root):
        errors.append("request project_root does not match the live project")
    inputs = request.get("inputs") if isinstance(request.get("inputs"), Mapping) else {}
    try:
        edl = _project_file(root, str((inputs.get("edl") or {}).get("path") or ""), label="EDL")
        storyboard = _project_file(
            root,
            str((inputs.get("storyboard") or {}).get("path") or ""),
            label="storyboard",
        )
        expected = build_request(
            root=root,
            edl_path=edl,
            storyboard_path=storyboard,
            generated_at=str(request.get("generated_at") or ""),
        )
        if expected != request:
            errors.append("request or a bound EDL/storyboard/source asset has drifted")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    return errors


def _text(value: Any) -> str:
    return str(value or "").strip()


def _same_time(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= EPSILON
    except (TypeError, ValueError):
        return False


def _normalize_spoken(value: str) -> str:
    return " ".join(value.casefold().split())


def build_report(
    *,
    root: Path,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    request_path: Path,
    response_path: Path,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    blockers = list(request.get("blockers") or [])
    warnings = list(request.get("warnings") or [])
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"unsupported response version: {response.get('version')}")
    if response.get("request_id") != request.get("request_id"):
        blockers.append("response request_id does not match the live request")
    reviewed_by = _text(response.get("reviewed_by"))
    if not reviewed_by:
        blockers.append("reviewed_by is required")
    strategy = _text(response.get("audio_strategy"))
    if strategy not in AUDIO_STRATEGIES:
        blockers.append(f"audio_strategy must be one of: {', '.join(sorted(AUDIO_STRATEGIES))}")
    shared_tone = _text(response.get("shared_tone"))
    if not shared_tone:
        blockers.append("shared_tone is required")

    expected_sections = request.get("sections") if isinstance(request.get("sections"), list) else []
    raw_sections = response.get("sections") if isinstance(response.get("sections"), list) else []
    if len(raw_sections) != len(expected_sections):
        blockers.append(f"response sections={len(raw_sections)} does not match request={len(expected_sections)}")
    section_by_id: Dict[str, Mapping[str, Any]] = {}
    for item in raw_sections:
        if not isinstance(item, Mapping):
            blockers.append("response contains a non-object section")
            continue
        section_id = _text(item.get("section_id"))
        if not section_id:
            blockers.append("response section has no section_id")
        elif section_id in section_by_id:
            blockers.append(f"duplicate response section: {section_id}")
        else:
            section_by_id[section_id] = item

    sections: List[Dict[str, Any]] = []
    voice_ledger: List[Dict[str, Any]] = []
    spoken_seen: Dict[str, str] = {}
    for expected in expected_sections:
        section_id = str(expected.get("section_id") or "")
        raw = section_by_id.get(section_id)
        if raw is None:
            blockers.append(f"missing response section: {section_id}")
            continue
        for field in ("story_id", "final_start", "final_end", "source_start", "source_end"):
            matches = _same_time(raw.get(field), expected.get(field)) if "start" in field or "end" in field else raw.get(field) == expected.get(field)
            if not matches:
                blockers.append(f"{section_id} changed immutable field {field}")
        visual_beat = _text(raw.get("visual_beat"))
        dialogue = _text(raw.get("dialogue"))
        narration = _text(raw.get("narration"))
        sound_design = _text(raw.get("sound_design"))
        music = _text(raw.get("music"))
        decision_note = _text(raw.get("decision_note"))
        source_audio_note = _text(raw.get("source_audio_note"))
        preserve_source_audio = raw.get("preserve_source_audio") is True
        stems_raw = raw.get("stems") if isinstance(raw.get("stems"), list) else []
        stems = [_text(value) for value in stems_raw if _text(value)]
        if not visual_beat:
            blockers.append(f"{section_id} visual_beat is required")
        if not sound_design:
            blockers.append(f"{section_id} sound_design must be explicit, including 'none'")
        if not music:
            blockers.append(f"{section_id} music must be explicit, including 'none'")
        if not decision_note:
            blockers.append(f"{section_id} decision_note is required")
        if not stems:
            blockers.append(f"{section_id} must list at least one planned stem")
        unknown_stems = sorted(set(stems) - STEM_TYPES)
        if unknown_stems:
            blockers.append(f"{section_id} has unknown stems: {', '.join(unknown_stems)}")
        if len(stems) != len(set(stems)):
            blockers.append(f"{section_id} has duplicate stems")
        if dialogue and "dialogue" not in stems:
            blockers.append(f"{section_id} has dialogue text without a dialogue stem")
        if narration and "narration" not in stems:
            blockers.append(f"{section_id} has narration text without a narration stem")
        if preserve_source_audio and "source_audio" not in stems:
            blockers.append(f"{section_id} preserves source audio without a source_audio stem")
        if preserve_source_audio and not source_audio_note:
            blockers.append(f"{section_id} preserves source audio without source_audio_note")
        if not any((dialogue, narration, sound_design, music, preserve_source_audio)):
            blockers.append(f"{section_id} has no executable audio decision")
        for kind, line in (("dialogue", dialogue), ("narration", narration)):
            if not line:
                continue
            normalized = _normalize_spoken(line)
            if normalized in spoken_seen:
                blockers.append(f"voiced line is duplicated in {spoken_seen[normalized]} and {section_id}")
            else:
                spoken_seen[normalized] = section_id
            voice_ledger.append({"section_id": section_id, "type": kind, "text": line})
        sections.append(
            {
                "section_id": section_id,
                "story_id": expected.get("story_id"),
                "final_start": expected.get("final_start"),
                "final_end": expected.get("final_end"),
                "source_path": expected.get("source_path"),
                "source_start": expected.get("source_start"),
                "source_end": expected.get("source_end"),
                "visual_beat": visual_beat,
                "dialogue": dialogue,
                "narration": narration,
                "sound_design": sound_design,
                "music": music,
                "stems": stems,
                "preserve_source_audio": preserve_source_audio,
                "source_audio_note": source_audio_note,
                "decision_note": decision_note,
            }
        )

    extra_sections = sorted(set(section_by_id) - {str(item.get("section_id")) for item in expected_sections})
    if extra_sections:
        blockers.append(f"response contains unknown sections: {', '.join(extra_sections)}")

    expected_omitted = request.get("omitted_story") if isinstance(request.get("omitted_story"), list) else []
    raw_omitted = response.get("omitted_story") if isinstance(response.get("omitted_story"), list) else []
    omitted_by_id: Dict[str, Mapping[str, Any]] = {}
    for item in raw_omitted:
        if not isinstance(item, Mapping):
            blockers.append("response contains a non-object omitted_story row")
            continue
        story_id = _text(item.get("story_id"))
        if story_id in omitted_by_id:
            blockers.append(f"duplicate omitted story decision: {story_id}")
        else:
            omitted_by_id[story_id] = item
    expected_omitted_ids = {str(item.get("story_id")) for item in expected_omitted}
    if set(omitted_by_id) != expected_omitted_ids:
        blockers.append("omitted_story response coverage does not match the request")
    valid_section_ids = {str(item.get("section_id")) for item in expected_sections}
    omitted_story: List[Dict[str, Any]] = []
    for expected in expected_omitted:
        story_id = str(expected.get("story_id") or "")
        raw = omitted_by_id.get(story_id, {})
        disposition = _text(raw.get("disposition"))
        target = _text(raw.get("target_section_id"))
        note = _text(raw.get("note"))
        if disposition not in OMITTED_DISPOSITIONS:
            blockers.append(f"{story_id} disposition must be one of: {', '.join(sorted(OMITTED_DISPOSITIONS))}")
        if not note:
            blockers.append(f"{story_id} omitted-story note is required")
        if disposition == "remove" and target:
            blockers.append(f"{story_id} remove disposition must not have target_section_id")
        if disposition in {"rewrite_into_adjacent", "offscreen_bridge"} and target not in valid_section_ids:
            blockers.append(f"{story_id} disposition requires a valid target_section_id")
        omitted_story.append(
            {
                "story_id": story_id,
                "disposition": disposition,
                "target_section_id": target,
                "note": note,
            }
        )

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    stem_types = sorted({stem for section in sections for stem in section["stems"]})
    payload: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": generated_at or utc_now(),
        "project_root": str(root),
        "inputs": request.get("inputs"),
        "request": _file_record(root, request_path),
        "response": _file_record(root, response_path),
        "request_id": request.get("request_id"),
        "reviewed_by": reviewed_by,
        "audio_strategy": strategy,
        "shared_tone": shared_tone,
        "timeline_duration": request.get("timeline_duration"),
        "sections": sections,
        "voice_ledger": voice_ledger,
        "omitted_story": omitted_story,
        "review_notes": _text(response.get("review_notes")),
        "generation_handoff": {
            "provider_neutral": True,
            "paid_submission_performed": False,
            "instruction": "Rewrite this approved final-timeline plan as a timed provider cue sheet; do not submit the raw JSON.",
        },
        "blockers": blockers,
        "warnings": warnings,
        "status": "blocked" if blockers else "ready",
        "summary": {
            "sections": len(sections),
            "voice_lines": len(voice_ledger),
            "omitted_story_beats": len(omitted_story),
            "stem_types": stem_types,
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
    }
    payload["report_id"] = _digest({key: value for key, value in payload.items() if key != "report_id"})
    return payload


def verify_report(report_path: str, *, project_dir: Optional[str] = None) -> Dict[str, Any]:
    path = Path(report_path).expanduser().resolve(strict=True)
    report = _load_json(path)
    root = _root(project_dir or str(report.get("project_root") or ""))
    errors: List[str] = []
    if report.get("version") != REPORT_VERSION:
        errors.append(f"unsupported report version: {report.get('version')}")
    if str(report.get("project_root") or "") != str(root):
        errors.append("report project_root does not match the live project")
    request_record = report.get("request") if isinstance(report.get("request"), Mapping) else {}
    response_record = report.get("response") if isinstance(report.get("response"), Mapping) else {}
    try:
        request_path = _project_file(root, str(request_record.get("path") or ""), label="request")
        response_path = _project_file(root, str(response_record.get("path") or ""), label="response")
        for label, bound, live in (
            ("request", request_record, _file_record(root, request_path)),
            ("response", response_record, _file_record(root, response_path)),
        ):
            if bound != live:
                errors.append(f"{label} bytes or metadata have drifted")
        request = _load_json(request_path)
        response = _load_json(response_path)
        errors.extend(_verify_request(request, root))
        if not errors:
            expected = build_report(
                root=root,
                request=request,
                response=response,
                request_path=request_path,
                response_path=response_path,
                generated_at=str(report.get("generated_at") or ""),
            )
            if expected != report:
                errors.append("report derived fields or report_id have drifted")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    if not errors:
        return report
    result = dict(report)
    existing = list(report.get("blockers") or [])
    result["blockers"] = sorted(set(existing + errors))
    result["status"] = "blocked"
    summary = dict(report.get("summary") or {})
    summary["blocking"] = len(result["blockers"])
    result["summary"] = summary
    result["verification_errors"] = sorted(set(errors))
    return result


def emit_markdown(payload: Mapping[str, Any]) -> str:
    version = payload.get("version")
    if version == REQUEST_VERSION:
        summary = payload.get("summary") or {}
        lines = [
            "# Final Audio Storyboard Review Request",
            "",
            f"- Request ID: `{payload.get('request_id', '')}`",
            f"- Final timeline: {payload.get('timeline_duration', 0)}s",
            f"- Events: {summary.get('events', 0)}",
            f"- Mapped: {summary.get('mapped_events', 0)}",
            f"- Omitted story beats: {summary.get('omitted_story_beats', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "## Final Timeline",
            "",
            "| section | final | story | source | suggested narration |",
            "|---|---:|---|---|---|",
        ]
        for item in payload.get("sections") or []:
            narration = str(item.get("suggested_narration") or "").replace("|", "/")
            lines.append(
                f"| {item.get('section_id')} | {item.get('final_start')}-{item.get('final_end')}s | "
                f"{item.get('story_id') or 'UNMAPPED'} | `{item.get('source_path')}` | {narration} |"
            )
        lines.extend(["", "## Review Rules", ""])
        lines.extend(f"- {rule}" for rule in payload.get("review_rules") or [])
    else:
        summary = payload.get("summary") or {}
        lines = [
            "# Final Audio Storyboard",
            "",
            f"- Status: {str(payload.get('status') or '').upper()}",
            f"- Strategy: {payload.get('audio_strategy', '')}",
            f"- Reviewer label: {payload.get('reviewed_by', '')}",
            f"- Timeline: {payload.get('timeline_duration', 0)}s",
            f"- Sections: {summary.get('sections', 0)}",
            f"- Voice lines: {summary.get('voice_lines', 0)}",
            f"- Blocking: {summary.get('blocking', 0)}",
            "",
            "## Sections",
            "",
            "| section | final | story | stems | narration/dialogue | sound | music |",
            "|---|---:|---|---|---|---|---|",
        ]
        for item in payload.get("sections") or []:
            voice = str(item.get("dialogue") or item.get("narration") or "-").replace("|", "/")
            sound = str(item.get("sound_design") or "-").replace("|", "/")
            music = str(item.get("music") or "-").replace("|", "/")
            lines.append(
                f"| {item.get('section_id')} | {item.get('final_start')}-{item.get('final_end')}s | "
                f"{item.get('story_id')} | {', '.join(item.get('stems') or [])} | {voice} | {sound} | {music} |"
            )
    blockers = payload.get("blockers") or []
    warnings = payload.get("warnings") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild and verify a provider-neutral audio storyboard from a locked visual EDL."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Bind EDL/storyboard inputs and write a review template.")
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--edl", required=True, help="export_edl.py JSON manifest, not the .edl text file.")
    prepare.add_argument("--storyboard", required=True, help="storyboard_plan.v1 JSON.")
    prepare.add_argument("--output", required=True, help="Review request JSON.")
    prepare.add_argument("--markdown", help="Optional request Markdown.")
    prepare.add_argument("--response-template", required=True, help="Response JSON template to fill.")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--strict", action="store_true", help="Exit 2 when EDL/story mapping is blocked.")

    audit = sub.add_parser("audit", help="Validate the completed response and write the final audio storyboard.")
    audit.add_argument("--project-dir", default=".")
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = sub.add_parser("verify", help="Live-verify input, response, and derived report bindings.")
    verify.add_argument("--project-dir", default=".")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _root(args.project_dir)
        if args.command == "prepare":
            edl = _project_file(root, args.edl, label="EDL")
            storyboard = _project_file(root, args.storyboard, label="storyboard")
            protected = [edl, storyboard]
            output = _output_file(root, args.output, protected=protected, force=args.force)
            response_path = _output_file(root, args.response_template, protected=protected + [output], force=args.force)
            markdown = (
                _output_file(root, args.markdown, protected=protected + [output, response_path], force=args.force)
                if args.markdown
                else None
            )
            request = build_request(root=root, edl_path=edl, storyboard_path=storyboard)
            _write_json(output, request)
            _write_json(response_path, request["response_template"])
            if markdown:
                _write_text(markdown, emit_markdown(request))
            print(
                f"final audio request: sections={request['summary']['events']} "
                f"mapped={request['summary']['mapped_events']} blocking={request['summary']['blocking']}"
            )
            return 2 if args.strict and request["summary"]["blocking"] else 0

        if args.command == "audit":
            request_path = _project_file(root, args.request, label="request")
            response_path = _project_file(root, args.response, label="response")
            request = _load_json(request_path)
            response = _load_json(response_path)
            request_errors = _verify_request(request, root)
            if request_errors:
                raise ValueError("; ".join(request_errors))
            protected = [request_path, response_path]
            output = _output_file(root, args.output, protected=protected, force=args.force)
            markdown = (
                _output_file(root, args.markdown, protected=protected + [output], force=args.force)
                if args.markdown
                else None
            )
            report = build_report(
                root=root,
                request=request,
                response=response,
                request_path=request_path,
                response_path=response_path,
            )
            _write_json(output, report)
            if markdown:
                _write_text(markdown, emit_markdown(report))
            print(
                f"final audio storyboard: status={report['status']} sections={report['summary']['sections']} "
                f"voice_lines={report['summary']['voice_lines']} blocking={report['summary']['blocking']}"
            )
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report = verify_report(args.report, project_dir=str(root))
        print(
            f"final audio storyboard verify: status={report['status']} "
            f"blocking={report['summary']['blocking']} warnings={report['summary']['warnings']}"
        )
        return 2 if args.strict and report["summary"]["blocking"] else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
