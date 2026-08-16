#!/usr/bin/env python3
"""Measure a reference video's edit rhythm and compare a rendered candidate.

The report copies structure, never source pixels: FFmpeg hard-cut timestamps,
shot-duration statistics, normalized cut positions, and review contact sheets.
Every source and evidence file is hash-bound so a later verify can fail closed
when the reviewed bytes or derived report state drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generated_clip_review import generate_contact_sheet, probe_media  # noqa: E402
from scene_boundaries import build_scene_plan, detect_scene_times  # noqa: E402


VERSION = "reference_edit_rhythm.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round3(value: float) -> float:
    return round(float(value), 3)


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


def canonical_report_id(report: Mapping[str, Any]) -> str:
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


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean > 0 else 0.0


def analyze_timeline(duration: float, boundaries: Sequence[float]) -> Dict[str, Any]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be positive")
    clean = sorted(
        {
            _round3(float(value))
            for value in boundaries
            if math.isfinite(float(value)) and 0 < float(value) < duration
        }
    )
    points = [0.0, *clean, duration]
    shots = [
        {
            "index": index,
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
            "duration_fraction": round((end - start) / duration, 6),
        }
        for index, (start, end) in enumerate(zip(points, points[1:]), start=1)
        if end > start
    ]
    shot_durations = [float(shot["duration"]) for shot in shots]
    normalized = [round(value / duration, 6) for value in clean]
    phase_counts = [0, 0, 0]
    for position in normalized:
        phase_counts[min(2, int(position * 3))] += 1
    phase_shares = [round(count / len(clean), 6) if clean else 0.0 for count in phase_counts]
    final_hold = shot_durations[-1] if shot_durations else duration
    return {
        "duration": round(duration, 6),
        "boundaries": clean,
        "normalized_boundaries": normalized,
        "shots": shots,
        "metrics": {
            "cuts": len(clean),
            "shots": len(shots),
            "cuts_per_minute": round(len(clean) * 60.0 / duration, 6),
            "shot_duration": {
                "mean": _round3(statistics.mean(shot_durations)) if shot_durations else 0.0,
                "median": _round3(statistics.median(shot_durations)) if shot_durations else 0.0,
                "p90": _round3(_percentile(shot_durations, 0.9)),
                "min": _round3(min(shot_durations, default=0.0)),
                "max": _round3(max(shot_durations, default=0.0)),
                "coefficient_of_variation": _round3(_coefficient_of_variation(shot_durations)),
            },
            "final_hold": {
                "seconds": _round3(final_hold),
                "duration_fraction": round(final_hold / duration, 6),
            },
            "phase_cut_counts": {
                "opening": phase_counts[0],
                "middle": phase_counts[1],
                "closing": phase_counts[2],
            },
            "phase_cut_shares": {
                "opening": phase_shares[0],
                "middle": phase_shares[1],
                "closing": phase_shares[2],
            },
        },
    }


def _relative_delta(reference: float, candidate: float) -> float:
    if reference == 0:
        return 0.0 if candidate == 0 else 1.0
    return abs(candidate - reference) / abs(reference)


def _boundary_distance(reference: Sequence[float], candidate: Sequence[float]) -> float:
    if not reference and not candidate:
        return 0.0
    if not reference or not candidate:
        return 1.0

    def nearest_mean(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(min(abs(value - other) for other in right) for value in left) / len(left)

    return (nearest_mean(reference, candidate) + nearest_mean(candidate, reference)) / 2.0


def _finding(
    kind: str,
    *,
    reference: float,
    candidate: float,
    delta: float,
    limit: float,
    require_match: bool,
    message: str,
    action: str,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "severity": "block" if require_match else "warn",
        "reference": round(reference, 6),
        "candidate": round(candidate, 6),
        "delta": round(delta, 6),
        "limit": round(limit, 6),
        "message": message,
        "action": action,
    }


def compare_timelines(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    require_match: bool,
    max_cut_density_delta: float,
    max_median_shot_delta: float,
    max_final_hold_delta: float,
    max_boundary_distance: float,
    max_phase_share_delta: float,
) -> Dict[str, Any]:
    limits = {
        "max_cut_density_delta": max_cut_density_delta,
        "max_median_shot_delta": max_median_shot_delta,
        "max_final_hold_delta": max_final_hold_delta,
        "max_boundary_distance": max_boundary_distance,
        "max_phase_share_delta": max_phase_share_delta,
    }
    if any(not math.isfinite(value) or value < 0 for value in limits.values()):
        raise ValueError("comparison tolerances must be finite and non-negative")

    ref_metrics = reference["metrics"]
    cand_metrics = candidate["metrics"]
    ref_density = float(ref_metrics["cuts_per_minute"])
    cand_density = float(cand_metrics["cuts_per_minute"])
    density_delta = _relative_delta(ref_density, cand_density)
    ref_median = float(ref_metrics["shot_duration"]["median"])
    cand_median = float(cand_metrics["shot_duration"]["median"])
    median_delta = _relative_delta(ref_median, cand_median)
    ref_final = float(ref_metrics["final_hold"]["duration_fraction"])
    cand_final = float(cand_metrics["final_hold"]["duration_fraction"])
    final_delta = abs(cand_final - ref_final)
    boundary_distance = _boundary_distance(
        reference["normalized_boundaries"], candidate["normalized_boundaries"]
    )
    ref_phases = ref_metrics["phase_cut_shares"]
    cand_phases = cand_metrics["phase_cut_shares"]
    phase_delta = sum(abs(float(ref_phases[key]) - float(cand_phases[key])) for key in ref_phases) / 2.0

    measurements = {
        "cut_density": {
            "reference": round(ref_density, 6),
            "candidate": round(cand_density, 6),
            "relative_delta": round(density_delta, 6),
        },
        "median_shot": {
            "reference": round(ref_median, 6),
            "candidate": round(cand_median, 6),
            "relative_delta": round(median_delta, 6),
        },
        "final_hold_fraction": {
            "reference": round(ref_final, 6),
            "candidate": round(cand_final, 6),
            "absolute_delta": round(final_delta, 6),
        },
        "normalized_boundary_distance": round(boundary_distance, 6),
        "phase_cut_share_distance": round(phase_delta, 6),
    }
    findings: List[Dict[str, Any]] = []
    if int(ref_metrics["cuts"]) == 0:
        findings.append(
            {
                "kind": "reference_has_no_detected_cuts",
                "severity": "warn",
                "message": "The configured detector found no hard cuts in the reference.",
                "action": "Inspect the reference contact sheet and lower the scene threshold if it uses soft transitions.",
            }
        )
    checks = [
        (
            "cut_density_delta",
            ref_density,
            cand_density,
            density_delta,
            max_cut_density_delta,
            "Candidate hard-cut density differs materially from the reference.",
            "Review shot lengths and add or remove only content-motivated cuts.",
        ),
        (
            "median_shot_delta",
            ref_median,
            cand_median,
            median_delta,
            max_median_shot_delta,
            "Candidate median shot duration differs materially from the reference.",
            "Compare the shot table and rebalance holds without copying the reference footage.",
        ),
        (
            "final_hold_delta",
            ref_final,
            cand_final,
            final_delta,
            max_final_hold_delta,
            "Candidate final-hold proportion differs materially from the reference.",
            "Review whether the ending needs a shorter resolve or a longer intentional hero hold.",
        ),
        (
            "boundary_position_delta",
            0.0,
            boundary_distance,
            boundary_distance,
            max_boundary_distance,
            "Normalized cut positions do not follow the reference's broad temporal shape.",
            "Compare opening, middle, and closing cut placement; keep only changes justified by the candidate story.",
        ),
        (
            "phase_cut_share_delta",
            0.0,
            phase_delta,
            phase_delta,
            max_phase_share_delta,
            "Opening/middle/closing cut shares differ materially from the reference.",
            "Review which phase carries the pacing change instead of matching total cut count alone.",
        ),
    ]
    for kind, ref_value, cand_value, delta, limit, message, action in checks:
        if delta > limit:
            findings.append(
                _finding(
                    kind,
                    reference=ref_value,
                    candidate=cand_value,
                    delta=delta,
                    limit=limit,
                    require_match=require_match,
                    message=message,
                    action=action,
                )
            )
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"reference-rhythm-{index:03d}"
    blocking = sum(1 for finding in findings if finding["severity"] == "block")
    warnings = sum(1 for finding in findings if finding["severity"] == "warn")
    return {
        "require_match": require_match,
        "limits": limits,
        "measurements": measurements,
        "findings": findings,
        "summary": {
            "status": "blocked" if blocking else ("review" if warnings else "ready"),
            "blocking": blocking,
            "warnings": warnings,
        },
    }


def _derive_report_state(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    comparison = compare_timelines(
        reference,
        candidate,
        require_match=bool(params.get("require_match")),
        max_cut_density_delta=float(params["max_cut_density_delta"]),
        max_median_shot_delta=float(params["max_median_shot_delta"]),
        max_final_hold_delta=float(params["max_final_hold_delta"]),
        max_boundary_distance=float(params["max_boundary_distance"]),
        max_phase_share_delta=float(params["max_phase_share_delta"]),
    )
    summary = {
        **comparison["summary"],
        "reference_cuts": int(reference["metrics"]["cuts"]),
        "candidate_cuts": int(candidate["metrics"]["cuts"]),
        "reference_shots": int(reference["metrics"]["shots"]),
        "candidate_shots": int(candidate["metrics"]["shots"]),
    }
    return {"comparison": comparison, "summary": summary}


def build_report(
    *,
    project_dir: str,
    sources: Mapping[str, Any],
    evidence: Mapping[str, Any],
    reference_boundaries: Sequence[float],
    candidate_boundaries: Sequence[float],
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    reference = analyze_timeline(float(sources["reference"]["media"]["duration"]), reference_boundaries)
    candidate = analyze_timeline(float(sources["candidate"]["media"]["duration"]), candidate_boundaries)
    derived = _derive_report_state(reference, candidate, params)
    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "params": dict(params),
        "sources": dict(sources),
        "evidence": dict(evidence),
        "reference": reference,
        "candidate": candidate,
        **derived,
        "notes": [
            "This report compares editing structure only; it does not authorize copying source footage, audio, branding, or story content.",
            "FFmpeg scene score primarily detects hard visual changes; soft transitions and motion within a shot require contact-sheet and full-playback review.",
            "Structural similarity is editorial evidence, not a quality, retention, or legal-clearance score.",
        ],
    }
    report["report_id"] = canonical_report_id(report)
    return report


def _source_record(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "media": _media_signature(probe_media(str(path))),
    }


def _evidence_record(path: Path, *, root: Path, sampling: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "sampling": dict(sampling),
    }


def analyze_project(
    *,
    project_dir: str,
    reference_path: str,
    candidate_path: str,
    evidence_dir: str,
    scene_threshold: float,
    min_scene_gap: float,
    sample_fps: float,
    max_frames: int,
    thumb_width: int,
    require_match: bool,
    max_cut_density_delta: float,
    max_median_shot_delta: float,
    max_final_hold_delta: float,
    max_boundary_distance: float,
    max_phase_share_delta: float,
    force: bool,
) -> Dict[str, Any]:
    if not 0 <= scene_threshold <= 1:
        raise ValueError("scene_threshold must be between 0 and 1")
    if min_scene_gap < 0:
        raise ValueError("min_scene_gap must be non-negative")
    if not 0 < sample_fps <= 10:
        raise ValueError("sample_fps must be greater than 0 and at most 10")
    if not 4 <= max_frames <= 120:
        raise ValueError("max_frames must be between 4 and 120")
    if not 160 <= thumb_width <= 1280:
        raise ValueError("thumb_width must be between 160 and 1280")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    reference_file = _project_file(reference_path, root=root, label="reference video")
    candidate_file = _project_file(candidate_path, root=root, label="candidate video")
    if reference_file == candidate_file:
        raise ValueError("reference and candidate videos must be different files")
    sheet_dir = _project_output(evidence_dir, root=root, label="evidence directory")
    sheet_paths = {
        label: sheet_dir / f"{label}_contact_sheet.jpg"
        for label in ("reference", "candidate")
    }
    if len(set(sheet_paths.values())) != 2 or any(
        sheet in {reference_file, candidate_file} for sheet in sheet_paths.values()
    ):
        raise ValueError("contact-sheet outputs must be distinct from source videos")
    for sheet in sheet_paths.values():
        if sheet.exists() and not force:
            raise ValueError(
                f"refusing to overwrite existing contact sheet without --force: {sheet}"
            )

    sources: Dict[str, Any] = {}
    evidence: Dict[str, Any] = {}
    boundaries: Dict[str, Sequence[float]] = {}
    for label, path in (("reference", reference_file), ("candidate", candidate_file)):
        source = _source_record(path, root=root)
        detected = detect_scene_times(str(path), scene_threshold)
        plan = build_scene_plan(
            str(path),
            detected,
            duration=float(source["media"]["duration"]),
            threshold=scene_threshold,
            min_scene_duration=min_scene_gap,
        )
        sheet = sheet_paths[label]
        sampling = generate_contact_sheet(
            path,
            sheet,
            duration=float(source["media"]["duration"]),
            sample_fps=sample_fps,
            max_frames=max_frames,
            thumb_width=thumb_width,
            force=force,
        )
        sources[label] = source
        evidence[f"{label}_contact_sheet"] = _evidence_record(sheet, root=root, sampling=sampling)
        boundaries[label] = plan["boundaries"]

    params = {
        "scene_threshold": scene_threshold,
        "min_scene_gap": min_scene_gap,
        "sample_fps": sample_fps,
        "max_frames": max_frames,
        "thumb_width": thumb_width,
        "require_match": require_match,
        "max_cut_density_delta": max_cut_density_delta,
        "max_median_shot_delta": max_median_shot_delta,
        "max_final_hold_delta": max_final_hold_delta,
        "max_boundary_distance": max_boundary_distance,
        "max_phase_share_delta": max_phase_share_delta,
    }
    return build_report(
        project_dir=str(root),
        sources=sources,
        evidence=evidence,
        reference_boundaries=boundaries["reference"],
        candidate_boundaries=boundaries["candidate"],
        params=params,
    )


def verify_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    verification_warnings: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported report version: {report.get('version')!r}")
    raw_project_dir = report.get("project_dir")
    if not isinstance(raw_project_dir, str) or not raw_project_dir or not Path(raw_project_dir).is_absolute():
        blockers.append("project_dir must be a non-empty absolute path")
    root = Path(str(raw_project_dir or ".")).expanduser().resolve()
    if not root.is_dir():
        blockers.append(f"project directory does not exist: {root}")

    sources = report.get("sources") if isinstance(report.get("sources"), Mapping) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    live_source_paths: List[Path] = []
    for label in ("reference", "candidate"):
        record = sources.get(label) if isinstance(sources.get(label), Mapping) else None
        if record is None:
            blockers.append(f"missing source record: {label}")
            continue
        try:
            path = _project_file(str(record.get("path") or ""), root=root, label=f"{label} video")
            live_source_paths.append(path)
            if path.stat().st_size != int(record.get("size_bytes") or -1):
                blockers.append(f"{label} video size changed")
            if _sha256(path) != record.get("sha256"):
                blockers.append(f"{label} video bytes changed")
            live_media = _media_signature(probe_media(str(path)))
            if live_media != record.get("media"):
                blockers.append(f"{label} video media contract changed")
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(str(exc))

    live_evidence_paths: List[Path] = []
    for label in ("reference_contact_sheet", "candidate_contact_sheet"):
        record = evidence.get(label) if isinstance(evidence.get(label), Mapping) else None
        if record is None:
            blockers.append(f"missing evidence record: {label}")
            continue
        try:
            path = _project_file(str(record.get("path") or ""), root=root, label=label)
            live_evidence_paths.append(path)
            if path.stat().st_size != int(record.get("size_bytes") or -1):
                blockers.append(f"{label} size changed")
            if _sha256(path) != record.get("sha256"):
                blockers.append(f"{label} bytes changed")
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(str(exc))

    if len(set(live_source_paths)) != len(live_source_paths):
        blockers.append("reference and candidate resolve to the same source file")
    if len(set(live_evidence_paths)) != len(live_evidence_paths):
        blockers.append("reference and candidate contact sheets resolve to the same file")
    if set(live_source_paths).intersection(live_evidence_paths):
        blockers.append("contact-sheet evidence must not overwrite a source video")

    reference = report.get("reference") if isinstance(report.get("reference"), Mapping) else None
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else None
    params = report.get("params") if isinstance(report.get("params"), Mapping) else None
    if reference is None or candidate is None or params is None:
        blockers.append("report is missing reference, candidate, or params state")
    else:
        try:
            expected_reference = analyze_timeline(
                float(sources["reference"]["media"]["duration"]),
                reference.get("boundaries") or [],
            )
            expected_candidate = analyze_timeline(
                float(sources["candidate"]["media"]["duration"]),
                candidate.get("boundaries") or [],
            )
            expected_derived = _derive_report_state(expected_reference, expected_candidate, params)
            if expected_reference != reference:
                blockers.append("stored reference rhythm state is not canonical")
            if expected_candidate != candidate:
                blockers.append("stored candidate rhythm state is not canonical")
            if expected_derived["comparison"] != report.get("comparison"):
                blockers.append("stored rhythm comparison is not canonical")
            if expected_derived["summary"] != report.get("summary"):
                blockers.append("stored rhythm summary is not canonical")
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(f"could not recompute rhythm report: {exc}")

    if report.get("report_id") != canonical_report_id(report):
        blockers.append("report_id does not match canonical report contents")

    stored_summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    try:
        stored_blocking = max(0, int(stored_summary.get("blocking") or 0))
        stored_warnings = max(0, int(stored_summary.get("warnings") or 0))
    except (TypeError, ValueError):
        stored_blocking = 0
        stored_warnings = 0
        blockers.append("stored summary counts are invalid")
    unique_blockers = sorted(set(blockers))
    unique_warnings = sorted(set(verification_warnings))
    blocking = stored_blocking + len(unique_blockers)
    warnings = stored_warnings + len(unique_warnings)
    return {
        "version": VERSION,
        "status": "blocked" if blocking else ("review" if warnings else "ready"),
        "report_id": report.get("report_id"),
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "summary": {
            "blocking": blocking,
            "warnings": warnings,
            "stored_blocking": stored_blocking,
            "stored_warnings": stored_warnings,
            "verification_blocking": len(unique_blockers),
            "verification_warnings": len(unique_warnings),
        },
    }


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    reference = report.get("reference") if isinstance(report.get("reference"), Mapping) else {}
    candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
    ref_metrics = reference.get("metrics") if isinstance(reference.get("metrics"), Mapping) else {}
    cand_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), Mapping) else {}
    comparison = report.get("comparison") if isinstance(report.get("comparison"), Mapping) else {}
    measurements = comparison.get("measurements") if isinstance(comparison.get("measurements"), Mapping) else {}
    sources = report.get("sources") if isinstance(report.get("sources"), Mapping) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    lines = [
        "# Reference Edit Rhythm",
        "",
        f"- Status: **{str(summary.get('status', 'unknown')).upper()}**",
        f"- Require match: `{bool(comparison.get('require_match'))}`",
        f"- Reference: `{sources.get('reference', {}).get('path', '')}`",
        f"- Candidate: `{sources.get('candidate', {}).get('path', '')}`",
        f"- Report ID: `{report.get('report_id', '')}`",
        "",
        "## Measured Rhythm",
        "",
        "| Metric | Reference | Candidate |",
        "| --- | ---: | ---: |",
        f"| Duration | {float(reference.get('duration', 0)):.2f}s | {float(candidate.get('duration', 0)):.2f}s |",
        f"| Cuts / shots | {ref_metrics.get('cuts', 0)} / {ref_metrics.get('shots', 0)} | {cand_metrics.get('cuts', 0)} / {cand_metrics.get('shots', 0)} |",
        f"| Cuts per minute | {float(ref_metrics.get('cuts_per_minute', 0)):.2f} | {float(cand_metrics.get('cuts_per_minute', 0)):.2f} |",
        f"| Median shot | {float(ref_metrics.get('shot_duration', {}).get('median', 0)):.2f}s | {float(cand_metrics.get('shot_duration', {}).get('median', 0)):.2f}s |",
        f"| Final hold | {float(ref_metrics.get('final_hold', {}).get('seconds', 0)):.2f}s | {float(cand_metrics.get('final_hold', {}).get('seconds', 0)):.2f}s |",
        "",
        "## Structural Differences",
        "",
        "| Measurement | Delta | Limit |",
        "| --- | ---: | ---: |",
        f"| Cut density | {float(measurements.get('cut_density', {}).get('relative_delta', 0)):.1%} | {float(comparison.get('limits', {}).get('max_cut_density_delta', 0)):.1%} |",
        f"| Median shot | {float(measurements.get('median_shot', {}).get('relative_delta', 0)):.1%} | {float(comparison.get('limits', {}).get('max_median_shot_delta', 0)):.1%} |",
        f"| Final-hold fraction | {float(measurements.get('final_hold_fraction', {}).get('absolute_delta', 0)):.3f} | {float(comparison.get('limits', {}).get('max_final_hold_delta', 0)):.3f} |",
        f"| Normalized cut positions | {float(measurements.get('normalized_boundary_distance', 0)):.3f} | {float(comparison.get('limits', {}).get('max_boundary_distance', 0)):.3f} |",
        f"| Phase cut shares | {float(measurements.get('phase_cut_share_distance', 0)):.3f} | {float(comparison.get('limits', {}).get('max_phase_share_delta', 0)):.3f} |",
        "",
        "## Findings",
        "",
    ]
    findings = comparison.get("findings") if isinstance(comparison.get("findings"), list) else []
    if not findings:
        lines.append("- No configured structural difference exceeded its tolerance.")
    else:
        lines.extend(["| ID | Severity | Finding | Action |", "| --- | --- | --- | --- |"])
        for finding in findings:
            lines.append(
                "| {id} | {severity} | {message} | {action} |".format(
                    id=finding.get("id", ""),
                    severity=str(finding.get("severity", "")).upper(),
                    message=str(finding.get("message", "")).replace("|", "/"),
                    action=str(finding.get("action", "")).replace("|", "/"),
                )
            )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Reference contact sheet: `{evidence.get('reference_contact_sheet', {}).get('path', '')}`",
            f"- Candidate contact sheet: `{evidence.get('candidate_contact_sheet', {}).get('path', '')}`",
            "",
            "## Review Contract",
            "",
            "- Copy structure only. Do not copy source pixels, audio, branding, story content, or protected assets.",
            "- Inspect both contact sheets and watch both videos at 1x; hard-cut detection misses some dissolves and in-shot motion.",
            "- Use `--require-match` only when the reference rhythm is an explicit acceptance criterion.",
            "- Re-run `verify` before assembly or publish; hashes detect drift but are not signatures or rights clearance.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure reference-video hard-cut rhythm and compare a rendered candidate."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Create a source-bound rhythm comparison report.")
    analyze.add_argument("--project-dir", default=".")
    analyze.add_argument("--reference", required=True)
    analyze.add_argument("--candidate", required=True)
    analyze.add_argument("--evidence-dir", default="verify/reference_edit_rhythm")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--markdown")
    analyze.add_argument("--scene-threshold", type=float, default=0.30)
    analyze.add_argument("--min-scene-gap", type=float, default=0.20)
    analyze.add_argument("--sample-fps", type=float, default=1.0)
    analyze.add_argument("--max-frames", type=int, default=24)
    analyze.add_argument("--thumb-width", type=int, default=320)
    analyze.add_argument("--max-cut-density-delta", type=float, default=0.40)
    analyze.add_argument("--max-median-shot-delta", type=float, default=0.50)
    analyze.add_argument("--max-final-hold-delta", type=float, default=0.15)
    analyze.add_argument("--max-boundary-distance", type=float, default=0.12)
    analyze.add_argument("--max-phase-share-delta", type=float, default=0.30)
    analyze.add_argument("--require-match", action="store_true")
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify live source/evidence bytes and derived report state.")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            root = Path(args.project_dir).expanduser().resolve()
            output = _project_output(args.output, root=root, label="report output")
            markdown = (
                _project_output(args.markdown, root=root, label="Markdown output")
                if args.markdown
                else None
            )
            for path in (output, markdown):
                if path is not None and path.exists() and not args.force:
                    raise ValueError(f"refusing to overwrite existing file without --force: {path}")
            source_paths = {
                _project_file(args.reference, root=root, label="reference video"),
                _project_file(args.candidate, root=root, label="candidate video"),
            }
            result_paths = {path for path in (output, markdown) if path is not None}
            if len(result_paths) != len([path for path in (output, markdown) if path is not None]):
                raise ValueError("report and Markdown outputs must be different files")
            if result_paths.intersection(source_paths):
                raise ValueError("report outputs must not overwrite source videos")
            evidence_root = _project_output(
                args.evidence_dir, root=root, label="evidence directory"
            )
            evidence_paths = {
                evidence_root / "reference_contact_sheet.jpg",
                evidence_root / "candidate_contact_sheet.jpg",
            }
            if result_paths.intersection(evidence_paths):
                raise ValueError("report outputs must not overwrite contact-sheet evidence")
            report = analyze_project(
                project_dir=str(root),
                reference_path=args.reference,
                candidate_path=args.candidate,
                evidence_dir=args.evidence_dir,
                scene_threshold=args.scene_threshold,
                min_scene_gap=args.min_scene_gap,
                sample_fps=args.sample_fps,
                max_frames=args.max_frames,
                thumb_width=args.thumb_width,
                require_match=args.require_match,
                max_cut_density_delta=args.max_cut_density_delta,
                max_median_shot_delta=args.max_median_shot_delta,
                max_final_hold_delta=args.max_final_hold_delta,
                max_boundary_distance=args.max_boundary_distance,
                max_phase_share_delta=args.max_phase_share_delta,
                force=args.force,
            )
            _write_json(output, report, force=args.force)
            if markdown is not None:
                _write_text(markdown, emit_markdown(report), force=args.force)
            summary = report["summary"]
            print(
                "Reference edit rhythm: "
                f"{summary['status']} blocking={summary['blocking']} warnings={summary['warnings']} "
                f"reference_cuts={summary['reference_cuts']} candidate_cuts={summary['candidate_cuts']}",
                file=sys.stderr,
            )
            return 2 if args.strict and int(summary["blocking"]) else 0

        report = _load_json(args.report)
        verification = verify_report(report)
        summary = verification["summary"]
        print(
            "Reference edit rhythm verify: "
            f"{verification['status']} blocking={summary['blocking']} warnings={summary['warnings']}",
            file=sys.stderr,
        )
        for blocker in verification["blockers"]:
            print(f"BLOCK: {blocker}", file=sys.stderr)
        return 2 if args.strict and int(summary["blocking"]) else 0
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
