#!/usr/bin/env python3
"""Audit rendered short-form pacing without pretending to predict retention.

The report combines hard visual cuts from FFmpeg scene detection with optional
timed-text changes from subtitle_pack/transcript JSON. It flags observable
editing risks: an inactive opening, long visual holds, long combined attention
gaps, metronomic shot cadence, rapid-cut bursts, and stale/missing subtitles.
It never edits the video and does not call an AI provider.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene_boundaries import (  # noqa: E402
    build_scene_plan,
    detect_scene_times,
    probe_duration,
)


VERSION = "retention_rhythm_qa.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round3(value: float) -> float:
    return round(float(value), 3)


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _read_json(path: str) -> Mapping[str, Any]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load_timed_text(path: str) -> List[Dict[str, Any]]:
    """Load timed cues from subtitle_pack.v1 or transcript-style JSON."""
    payload = _read_json(path)
    raw_items = payload.get("cues")
    if not isinstance(raw_items, list):
        raw_items = payload.get("segments")
    if not isinstance(raw_items, list):
        raise ValueError("timed text JSON must contain cues[] or segments[]")

    cues: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, Mapping):
            continue
        start = _finite_float(item.get("start"))
        end = _finite_float(item.get("end"))
        text = " ".join(str(item.get("text") or "").split())
        if start is None or end is None or start < 0 or end <= start or not text:
            continue
        cues.append(
            {
                "id": str(item.get("index") or item.get("id") or index),
                "start": _round3(start),
                "end": _round3(end),
                "duration": _round3(end - start),
                "text": text,
            }
        )
    return sorted(cues, key=lambda cue: (cue["start"], cue["end"], cue["id"]))


def load_scene_boundaries(path: str) -> Tuple[float, List[float]]:
    payload = _read_json(path)
    source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    duration = _finite_float(source.get("duration"))
    if duration is None:
        scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
        duration = max(
            (_finite_float(scene.get("end")) or 0.0)
            for scene in scenes
            if isinstance(scene, Mapping)
        ) if scenes else None
    if duration is None or duration <= 0:
        raise ValueError("scene boundaries JSON has no positive source.duration")

    raw_boundaries = payload.get("boundaries")
    if not isinstance(raw_boundaries, list):
        raise ValueError("scene boundaries JSON must contain boundaries[]")
    boundaries = sorted(
        {
            _round3(value)
            for raw in raw_boundaries
            if (value := _finite_float(raw)) is not None and 0 < value < duration
        }
    )
    return float(duration), boundaries


def _shot_ranges(duration: float, boundaries: Sequence[float]) -> List[Dict[str, float]]:
    points = [0.0] + sorted({float(value) for value in boundaries if 0 < value < duration}) + [duration]
    return [
        {
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
        }
        for start, end in zip(points, points[1:])
        if end > start
    ]


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


def _max_true_run(flags: Sequence[bool]) -> int:
    longest = 0
    current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def _timeline_gaps(duration: float, events: Sequence[float]) -> List[Dict[str, float]]:
    points = [0.0] + sorted({float(value) for value in events if 0 < value < duration}) + [duration]
    return [
        {
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(end - start),
        }
        for start, end in zip(points, points[1:])
        if end > start
    ]


def _uncovered_ranges(duration: float, cues: Sequence[Mapping[str, Any]]) -> List[Dict[str, float]]:
    uncovered: List[Dict[str, float]] = []
    cursor = 0.0
    for cue in cues:
        start = max(0.0, min(duration, float(cue["start"])))
        end = max(start, min(duration, float(cue["end"])))
        if start > cursor:
            uncovered.append(
                {"start": _round3(cursor), "end": _round3(start), "duration": _round3(start - cursor)}
            )
        cursor = max(cursor, end)
    if cursor < duration:
        uncovered.append(
            {"start": _round3(cursor), "end": _round3(duration), "duration": _round3(duration - cursor)}
        )
    return uncovered


def _finding(
    kind: str,
    severity: str,
    message: str,
    action: str,
    *,
    start: Optional[float] = None,
    end: Optional[float] = None,
    value: Optional[float] = None,
    limit: Optional[float] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "kind": kind,
        "severity": severity,
        "message": message,
        "action": action,
    }
    if start is not None:
        result["start"] = _round3(start)
    if end is not None:
        result["end"] = _round3(end)
    if value is not None:
        result["value"] = _round3(value)
    if limit is not None:
        result["limit"] = _round3(limit)
    return result


def analyze_rhythm(
    video_path: str,
    *,
    duration: float,
    boundaries: Sequence[float],
    cues: Sequence[Mapping[str, Any]] = (),
    timed_text_provided: bool = False,
    hook_seconds: float = 3.0,
    soft_max_shot: float = 6.0,
    hard_max_shot: float = 10.0,
    max_attention_gap: float = 6.0,
    hard_attention_gap: float = 10.0,
    metronomic_cv: float = 0.08,
    rapid_shot: float = 0.35,
    max_rapid_ratio: float = 0.35,
    max_rapid_run: int = 4,
    max_subtitle_hold: float = 4.5,
    max_subtitle_gap: float = 1.5,
) -> Dict[str, Any]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if not (0 < soft_max_shot <= hard_max_shot):
        raise ValueError("shot thresholds must satisfy 0 < soft <= hard")
    if not (0 < max_attention_gap <= hard_attention_gap):
        raise ValueError("attention thresholds must satisfy 0 < soft <= hard")
    if hook_seconds <= 0:
        raise ValueError("hook_seconds must be positive")
    if metronomic_cv < 0:
        raise ValueError("metronomic_cv must be non-negative")
    if rapid_shot <= 0 or not (0 <= max_rapid_ratio <= 1) or max_rapid_run < 1:
        raise ValueError("rapid-cut thresholds are invalid")
    if max_subtitle_hold <= 0 or max_subtitle_gap < 0:
        raise ValueError("subtitle thresholds are invalid")

    clean_boundaries = sorted(
        {_round3(value) for raw in boundaries if (value := _finite_float(raw)) is not None and 0 < value < duration}
    )
    clean_cues = sorted(
        [
            dict(cue)
            for cue in cues
            if _finite_float(cue.get("start")) is not None
            and _finite_float(cue.get("end")) is not None
            and float(cue["end"]) > float(cue["start"])
        ],
        key=lambda cue: (float(cue["start"]), float(cue["end"])),
    )
    shots = _shot_ranges(duration, clean_boundaries)
    shot_durations = [float(shot["duration"]) for shot in shots]
    cadence_cv = _coefficient_of_variation(shot_durations)
    rapid_flags = [value < rapid_shot for value in shot_durations]
    rapid_ratio = sum(rapid_flags) / len(rapid_flags) if rapid_flags else 0.0
    rapid_run = _max_true_run(rapid_flags)

    subtitle_events = [float(cue["start"]) for cue in clean_cues]
    attention_events = sorted(set(clean_boundaries + subtitle_events))
    hook_end = min(duration, hook_seconds)
    hook_events = [value for value in attention_events if 0 <= value <= hook_end]
    attention_gaps = _timeline_gaps(duration, attention_events)
    uncovered = _uncovered_ranges(duration, clean_cues) if timed_text_provided else []

    findings: List[Dict[str, Any]] = []
    if timed_text_provided and not clean_cues:
        findings.append(
            _finding(
                "subtitle_timeline_empty",
                "block",
                "Timed-text input contains no valid cues.",
                "Regenerate an output-aligned subtitle_pack JSON before using subtitle rhythm as a gate.",
            )
        )

    if not hook_events:
        severity = "block" if timed_text_provided else "warn"
        findings.append(
            _finding(
                "inactive_hook",
                severity,
                f"No scene cut or subtitle change was observed in the first {hook_end:.1f}s.",
                "Review the opening; add a real pattern interrupt, visual change, or aligned hook caption if needed.",
                start=0.0,
                end=hook_end,
                value=hook_end,
                limit=hook_seconds,
            )
        )

    for shot in shots:
        shot_duration = float(shot["duration"])
        if shot_duration <= soft_max_shot:
            continue
        severity = "block" if shot_duration > hard_max_shot else "warn"
        findings.append(
            _finding(
                "long_visual_hold",
                severity,
                f"Visual shot holds for {shot_duration:.2f}s without a detected hard change.",
                "Review this range for intentional motion; otherwise add B-roll, a reframing beat, or a purposeful cut.",
                start=float(shot["start"]),
                end=float(shot["end"]),
                value=shot_duration,
                limit=hard_max_shot if severity == "block" else soft_max_shot,
            )
        )

    if len(shots) >= 4 and cadence_cv <= metronomic_cv:
        findings.append(
            _finding(
                "metronomic_cadence",
                "warn",
                f"Shot-duration coefficient of variation is {cadence_cv:.3f}; the cut pattern is unusually uniform.",
                "Listen against the script and vary holds deliberately instead of cutting on a fixed timer.",
                value=cadence_cv,
                limit=metronomic_cv,
            )
        )

    if rapid_ratio > max_rapid_ratio or rapid_run > max_rapid_run:
        findings.append(
            _finding(
                "rapid_cut_burst",
                "warn",
                f"{rapid_ratio:.0%} of shots are under {rapid_shot:.2f}s; longest rapid run is {rapid_run}.",
                "Review the burst at normal speed for legibility and motion continuity; keep fast cuts only when earned.",
                value=rapid_ratio,
                limit=max_rapid_ratio,
            )
        )

    if timed_text_provided:
        for cue in clean_cues:
            cue_duration = float(cue["end"]) - float(cue["start"])
            if cue_duration > max_subtitle_hold:
                findings.append(
                    _finding(
                        "stale_subtitle",
                        "warn",
                        f"Subtitle cue remains unchanged for {cue_duration:.2f}s.",
                        "Split the cue at a complete phrase boundary and verify the rendered timing.",
                        start=float(cue["start"]),
                        end=float(cue["end"]),
                        value=cue_duration,
                        limit=max_subtitle_hold,
                    )
                )
        for gap in uncovered:
            if float(gap["duration"]) > max_subtitle_gap:
                findings.append(
                    _finding(
                        "subtitle_gap",
                        "warn",
                        f"No subtitle is scheduled for {float(gap['duration']):.2f}s.",
                        "Confirm this is an intentional silent/visual beat; otherwise repair subtitle timing or coverage.",
                        start=float(gap["start"]),
                        end=float(gap["end"]),
                        value=float(gap["duration"]),
                        limit=max_subtitle_gap,
                    )
                )

        for gap in attention_gaps:
            gap_duration = float(gap["duration"])
            if gap_duration <= max_attention_gap:
                continue
            severity = "block" if gap_duration > hard_attention_gap else "warn"
            findings.append(
                _finding(
                    "attention_gap",
                    severity,
                    f"No scene cut or subtitle refresh occurs for {gap_duration:.2f}s.",
                    "Review the rendered range and add only a content-motivated visual or caption beat.",
                    start=float(gap["start"]),
                    end=float(gap["end"]),
                    value=gap_duration,
                    limit=hard_attention_gap if severity == "block" else max_attention_gap,
                )
            )

    severity_order = {"block": 0, "warn": 1}
    findings.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity")), 2),
            float(item.get("start", -1.0)),
            str(item.get("kind", "")),
        )
    )
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"rhythm-{index:03d}"

    blocking = sum(1 for finding in findings if finding["severity"] == "block")
    warnings = sum(1 for finding in findings if finding["severity"] == "warn")
    longest_visual = max(shot_durations, default=0.0)
    longest_attention = max((float(gap["duration"]) for gap in attention_gaps), default=0.0)
    status = "blocked" if blocking else ("review" if warnings else "ready")

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": {
            "video": os.path.abspath(video_path),
            "duration": _round3(duration),
        },
        "params": {
            "hook_seconds": hook_seconds,
            "soft_max_shot": soft_max_shot,
            "hard_max_shot": hard_max_shot,
            "max_attention_gap": max_attention_gap,
            "hard_attention_gap": hard_attention_gap,
            "metronomic_cv": metronomic_cv,
            "rapid_shot": rapid_shot,
            "max_rapid_ratio": max_rapid_ratio,
            "max_rapid_run": max_rapid_run,
            "max_subtitle_hold": max_subtitle_hold,
            "max_subtitle_gap": max_subtitle_gap,
        },
        "metrics": {
            "scene_boundaries": clean_boundaries,
            "shots": shots,
            "shot_duration": {
                "mean": _round3(statistics.mean(shot_durations)) if shot_durations else 0.0,
                "median": _round3(statistics.median(shot_durations)) if shot_durations else 0.0,
                "p90": _round3(_percentile(shot_durations, 0.90)),
                "max": _round3(longest_visual),
                "coefficient_of_variation": _round3(cadence_cv),
            },
            "hook": {
                "end": _round3(hook_end),
                "scene_cuts": sum(1 for value in clean_boundaries if value <= hook_end),
                "subtitle_changes": sum(1 for value in subtitle_events if value <= hook_end),
                "attention_events": len(hook_events),
            },
            "rapid_cuts": {
                "threshold": rapid_shot,
                "count": sum(rapid_flags),
                "ratio": _round3(rapid_ratio),
                "longest_run": rapid_run,
            },
            "attention": {
                "events": [_round3(value) for value in attention_events],
                "gaps": attention_gaps,
                "longest_gap": _round3(longest_attention),
            },
            "subtitles": {
                "provided": timed_text_provided,
                "cue_count": len(clean_cues),
                "cues": clean_cues,
                "uncovered_ranges": uncovered,
            },
        },
        "findings": findings,
        "summary": {
            "status": status,
            "blocking": blocking,
            "warnings": warnings,
            "scene_cuts": len(clean_boundaries),
            "shots": len(shots),
            "subtitle_cues": len(clean_cues),
            "hook_attention_events": len(hook_events),
            "longest_visual_hold": _round3(longest_visual),
            "longest_attention_gap": _round3(longest_attention),
            "cadence_cv": _round3(cadence_cv),
            "rapid_cut_ratio": _round3(rapid_ratio),
        },
        "notes": [
            "This is a heuristic editing-risk review, not a prediction of platform retention or virality.",
            "FFmpeg scene score detects hard visual changes; camera motion and subtle animation still require human review.",
            "Use output-aligned subtitle_pack JSON when the master has speed or cover offsets.",
        ],
    }


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    shot_metrics = (
        report.get("metrics", {}).get("shot_duration", {})
        if isinstance(report.get("metrics"), Mapping)
        else {}
    )
    lines = [
        "# Retention Rhythm QA",
        "",
        f"- Video: `{report.get('source', {}).get('video', '')}`",
        f"- Duration: `{float(report.get('source', {}).get('duration', 0)):.2f}s`",
        f"- Status: **{str(summary.get('status', 'unknown')).upper()}**",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "## Rhythm Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Scene cuts / shots | {summary.get('scene_cuts', 0)} / {summary.get('shots', 0)} |",
        f"| Hook attention events | {summary.get('hook_attention_events', 0)} |",
        f"| Mean / median shot | {float(shot_metrics.get('mean', 0)):.2f}s / {float(shot_metrics.get('median', 0)):.2f}s |",
        f"| Longest visual hold | {float(summary.get('longest_visual_hold', 0)):.2f}s |",
        f"| Longest attention gap | {float(summary.get('longest_attention_gap', 0)):.2f}s |",
        f"| Cadence CV | {float(summary.get('cadence_cv', 0)):.3f} |",
        f"| Rapid-cut ratio | {float(summary.get('rapid_cut_ratio', 0)):.0%} |",
        f"| Subtitle cues | {summary.get('subtitle_cues', 0)} |",
        "",
        "## Findings",
        "",
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if not findings:
        lines.append("- No configured rhythm risk was detected.")
    else:
        lines.extend(
            [
                "| ID | Severity | Range | Kind | Evidence | Action |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for finding in findings:
            if "start" in finding:
                time_range = f"{format_time(float(finding['start']))}-{format_time(float(finding.get('end', finding['start'])))}"
            else:
                time_range = "-"
            lines.append(
                "| {id} | {severity} | {time_range} | `{kind}` | {message} | {action} |".format(
                    id=finding.get("id", ""),
                    severity=str(finding.get("severity", "")).upper(),
                    time_range=time_range,
                    kind=finding.get("kind", ""),
                    message=str(finding.get("message", "")).replace("|", "/"),
                    action=str(finding.get("action", "")).replace("|", "/"),
                )
            )
    lines.extend(
        [
            "",
            "## Review Contract",
            "",
            "- Treat this as observable pacing evidence, not a retention or virality forecast.",
            "- Inspect every BLOCK and relevant WARN on the rendered master before changing the source timeline.",
            "- Re-render from source and rerun this report; do not patch the already encoded master.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit rendered short-form hook activity, visual cadence, and subtitle rhythm."
    )
    parser.add_argument("video", help="Rendered master/platform video")
    parser.add_argument("--scene-boundaries", help="Reuse scene_boundaries.v1 JSON instead of running FFmpeg")
    parser.add_argument("--timed-text", help="Output-aligned subtitle_pack or transcript JSON")
    parser.add_argument("--output", required=True, help="Write retention_rhythm_qa.v1 JSON")
    parser.add_argument("--markdown", help="Optional human-readable Markdown report")
    parser.add_argument("--scene-threshold", type=float, default=0.28, help="FFmpeg hard scene-change threshold")
    parser.add_argument("--min-scene-gap", type=float, default=0.20, help="Minimum seconds between detected cuts")
    parser.add_argument("--hook-seconds", type=float, default=3.0)
    parser.add_argument("--soft-max-shot", type=float, default=6.0)
    parser.add_argument("--hard-max-shot", type=float, default=10.0)
    parser.add_argument("--max-attention-gap", type=float, default=6.0)
    parser.add_argument("--hard-attention-gap", type=float, default=10.0)
    parser.add_argument("--metronomic-cv", type=float, default=0.08)
    parser.add_argument("--rapid-shot", type=float, default=0.35)
    parser.add_argument("--max-rapid-ratio", type=float, default=0.35)
    parser.add_argument("--max-rapid-run", type=int, default=4)
    parser.add_argument("--max-subtitle-hold", type=float, default=4.5)
    parser.add_argument("--max-subtitle-gap", type=float, default=1.5)
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.scene_boundaries:
            duration, boundaries = load_scene_boundaries(args.scene_boundaries)
        else:
            if not os.path.isfile(args.video):
                raise ValueError(f"video not found: {args.video}")
            duration = probe_duration(args.video)
            detected = detect_scene_times(args.video, args.scene_threshold)
            scene_plan = build_scene_plan(
                args.video,
                detected,
                duration=duration,
                threshold=args.scene_threshold,
                min_scene_duration=args.min_scene_gap,
            )
            boundaries = scene_plan["boundaries"]

        cues = load_timed_text(args.timed_text) if args.timed_text else []
        report = analyze_rhythm(
            args.video,
            duration=duration,
            boundaries=boundaries,
            cues=cues,
            timed_text_provided=bool(args.timed_text),
            hook_seconds=args.hook_seconds,
            soft_max_shot=args.soft_max_shot,
            hard_max_shot=args.hard_max_shot,
            max_attention_gap=args.max_attention_gap,
            hard_attention_gap=args.hard_attention_gap,
            metronomic_cv=args.metronomic_cv,
            rapid_shot=args.rapid_shot,
            max_rapid_ratio=args.max_rapid_ratio,
            max_rapid_run=args.max_rapid_run,
            max_subtitle_hold=args.max_subtitle_hold,
            max_subtitle_gap=args.max_subtitle_gap,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report["source"]["scene_boundaries"] = (
        os.path.abspath(args.scene_boundaries) if args.scene_boundaries else None
    )
    report["source"]["timed_text"] = os.path.abspath(args.timed_text) if args.timed_text else None
    report["params"]["scene_threshold"] = args.scene_threshold
    report["params"]["min_scene_gap"] = args.min_scene_gap
    write_json(args.output, report)
    if args.markdown:
        write_text(args.markdown, emit_markdown(report))

    summary = report["summary"]
    print(
        "Retention rhythm QA: "
        f"{summary['status']} blocking={summary['blocking']} warnings={summary['warnings']} "
        f"cuts={summary['scene_cuts']} longest_hold={summary['longest_visual_hold']:.2f}s",
        file=sys.stderr,
    )
    if args.strict and int(summary["blocking"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
