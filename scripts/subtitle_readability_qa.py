#!/usr/bin/env python3
"""Audit output-aligned subtitle cues before publishing.

The tool reads ``subtitle_pack.v1`` JSON and reports timing integrity plus
observable readability risks. It is local, deterministic, and read-only: it
does not rewrite captions or call an AI provider.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene_boundaries import probe_duration  # noqa: E402


VERSION = "subtitle_readability_qa.v1"
CJK_RE = re.compile(r"[\u3400-\u9fff]")


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


def _clean_text(value: Any) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(value or "").replace("\ufeff", "").splitlines()]
    return "\n".join(line for line in lines if line)


def _visible_len(text: str, language: str) -> int:
    if language == "zh":
        return len(re.sub(r"\s+", "", text))
    return len(text.replace("\n", ""))


def _line_lengths(text: str, language: str) -> List[int]:
    return [_visible_len(line, language) for line in text.splitlines() if line.strip()]


def _infer_language(payload: Mapping[str, Any], cues: Sequence[Mapping[str, Any]], override: str) -> str:
    if override != "auto":
        return override
    settings = payload.get("settings")
    if isinstance(settings, Mapping):
        configured = str(settings.get("language") or "").lower()
        if configured in {"zh", "en"}:
            return configured
    sample = " ".join(_clean_text(cue.get("text")) for cue in cues[:12])
    return "zh" if CJK_RE.search(sample) else "en"


def load_subtitle_pack(path: str) -> tuple[Mapping[str, Any], List[Dict[str, Any]]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("subtitle input must be a JSON object")
    raw_cues = payload.get("cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise ValueError("subtitle input must contain a non-empty cues[] list")

    cues: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_cues, start=1):
        if not isinstance(raw, Mapping):
            cues.append({"id": str(index), "index": index, "start": None, "end": None, "text": ""})
            continue
        cues.append(
            {
                "id": str(raw.get("index") or raw.get("id") or index),
                "index": index,
                "start": _finite_float(raw.get("start")),
                "end": _finite_float(raw.get("end")),
                "text": _clean_text(raw.get("text")),
            }
        )
    return payload, cues


def _finding(
    kind: str,
    severity: str,
    cue: Mapping[str, Any],
    message: str,
    action: str,
    *,
    value: Optional[float] = None,
    limit: Optional[float] = None,
    related_cue_id: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "kind": kind,
        "severity": severity,
        "cue_id": str(cue.get("id", "")),
        "cue_index": int(cue.get("index", 0)),
        "text": str(cue.get("text") or ""),
        "message": message,
        "action": action,
    }
    start = _finite_float(cue.get("start"))
    end = _finite_float(cue.get("end"))
    if start is not None:
        result["start"] = _round3(start)
    if end is not None:
        result["end"] = _round3(end)
    if value is not None:
        result["value"] = _round3(value)
    if limit is not None:
        result["limit"] = _round3(limit)
    if related_cue_id is not None:
        result["related_cue_id"] = related_cue_id
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def analyze_cues(
    subtitle_path: str,
    payload: Mapping[str, Any],
    cues: Sequence[Mapping[str, Any]],
    *,
    language: str = "auto",
    optimal_cps: float = 18.0,
    max_cps: float = 25.0,
    min_duration: float = 0.5,
    hard_min_duration: float = 0.15,
    max_duration: float = 7.0,
    max_lines: int = 2,
    max_chars: Optional[int] = None,
    media_path: str = "",
    media_duration: Optional[float] = None,
) -> Dict[str, Any]:
    if optimal_cps <= 0 or max_cps < optimal_cps:
        raise ValueError("CPS thresholds must satisfy 0 < optimal_cps <= max_cps")
    if hard_min_duration <= 0 or min_duration < hard_min_duration or max_duration <= min_duration:
        raise ValueError("duration thresholds must satisfy 0 < hard_min <= min < max")
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1")
    if media_duration is not None and media_duration <= 0:
        raise ValueError("media_duration must be positive")

    detected_language = _infer_language(payload, cues, language)
    line_limit = max_chars if max_chars is not None else (18 if detected_language == "zh" else 42)
    if line_limit < 1:
        raise ValueError("max_chars must be at least 1")

    findings: List[Dict[str, Any]] = []
    metrics: List[Dict[str, Any]] = []
    previous_input_start: Optional[float] = None
    valid_cues: List[Dict[str, Any]] = []

    for cue in cues:
        start = _finite_float(cue.get("start"))
        end = _finite_float(cue.get("end"))
        text = _clean_text(cue.get("text"))
        if start is None or end is None:
            findings.append(
                _finding(
                    "invalid_timing",
                    "block",
                    cue,
                    "Cue start/end is missing, non-numeric, or non-finite.",
                    "Regenerate the subtitle pack from valid output-timeline timestamps.",
                )
            )
            continue
        if start < 0:
            findings.append(
                _finding(
                    "negative_start",
                    "block",
                    cue,
                    f"Cue starts before the output timeline at {start:.3f}s.",
                    "Correct the speed/offset mapping and regenerate the subtitle pack.",
                    value=start,
                    limit=0.0,
                )
            )
        if end <= start:
            findings.append(
                _finding(
                    "non_positive_duration",
                    "block",
                    cue,
                    f"Cue duration is {end - start:.3f}s.",
                    "Give the cue a positive output-timeline duration.",
                    value=end - start,
                    limit=hard_min_duration,
                )
            )
            continue
        if previous_input_start is not None and start < previous_input_start:
            findings.append(
                _finding(
                    "non_monotonic_order",
                    "block",
                    cue,
                    "Cue starts before the preceding cue in file order.",
                    "Sort or regenerate cues in output-timeline order.",
                    value=start,
                    limit=previous_input_start,
                )
            )
        previous_input_start = start
        if not text:
            findings.append(
                _finding(
                    "empty_text",
                    "block",
                    cue,
                    "Cue has timing but no visible text.",
                    "Remove the empty cue or restore its reviewed caption text.",
                )
            )

        duration = end - start
        chars = _visible_len(text, detected_language)
        cps = chars / duration if chars else 0.0
        line_lengths = _line_lengths(text, detected_language)
        line_count = len(line_lengths)
        longest_line = max(line_lengths, default=0)
        metric = {
            "id": str(cue.get("id", "")),
            "index": int(cue.get("index", 0)),
            "start": _round3(start),
            "end": _round3(end),
            "duration": _round3(duration),
            "text": text,
            "visible_chars": chars,
            "characters_per_second": _round3(cps),
            "line_count": line_count,
            "max_line_chars": longest_line,
        }
        metrics.append(metric)
        valid_cues.append(metric)

        if duration < hard_min_duration:
            findings.append(
                _finding(
                    "flash_cue",
                    "block",
                    cue,
                    f"Cue is visible for only {duration:.3f}s.",
                    "Merge or retime the cue; verify the revised caption on the rendered master.",
                    value=duration,
                    limit=hard_min_duration,
                )
            )
        elif duration < min_duration:
            findings.append(
                _finding(
                    "short_duration",
                    "warn",
                    cue,
                    f"Cue is visible for {duration:.3f}s.",
                    "Review at normal speed; merge or extend it if the caption cannot be recognized.",
                    value=duration,
                    limit=min_duration,
                )
            )
        if duration > max_duration:
            findings.append(
                _finding(
                    "long_duration",
                    "warn",
                    cue,
                    f"Cue remains visible for {duration:.2f}s.",
                    "Split it at a complete phrase boundary if the rendered caption feels stale.",
                    value=duration,
                    limit=max_duration,
                )
            )
        if cps > max_cps:
            findings.append(
                _finding(
                    "cps_over_max",
                    "block",
                    cue,
                    f"Reading speed is {cps:.1f} characters per second.",
                    "Shorten the text or extend/split its output-timeline window.",
                    value=cps,
                    limit=max_cps,
                )
            )
        elif cps > optimal_cps:
            findings.append(
                _finding(
                    "cps_high",
                    "warn",
                    cue,
                    f"Reading speed is {cps:.1f} characters per second.",
                    "Review at normal speed and shorten or retime only if it is hard to read.",
                    value=cps,
                    limit=optimal_cps,
                )
            )
        if line_count > max_lines:
            findings.append(
                _finding(
                    "too_many_lines",
                    "warn",
                    cue,
                    f"Cue uses {line_count} lines.",
                    "Reduce to the configured line count without breaking phrase meaning.",
                    value=float(line_count),
                    limit=float(max_lines),
                )
            )
        if longest_line > line_limit:
            findings.append(
                _finding(
                    "line_too_long",
                    "warn",
                    cue,
                    f"Longest line has {longest_line} visible characters.",
                    "Rebreak the cue at punctuation or a natural phrase boundary.",
                    value=float(longest_line),
                    limit=float(line_limit),
                )
            )
        if media_duration is not None and end > media_duration + 0.05:
            findings.append(
                _finding(
                    "media_out_of_bounds",
                    "block",
                    cue,
                    f"Cue ends at {end:.3f}s, beyond media duration {media_duration:.3f}s.",
                    "Regenerate output-aligned subtitles with the same speed and cover offset as the master.",
                    value=end,
                    limit=media_duration,
                )
            )

    active: Optional[Mapping[str, Any]] = None
    for cue in sorted(valid_cues, key=lambda item: (float(item["start"]), float(item["end"]), str(item["id"]))):
        if active is not None and float(cue["start"]) < float(active["end"]) - 0.001:
            overlap = float(active["end"]) - float(cue["start"])
            findings.append(
                _finding(
                    "cue_overlap",
                    "block",
                    cue,
                    f"Cue overlaps `{active['id']}` by {overlap:.3f}s.",
                    "Repair the output-timeline timestamps so only intentional styled layers overlap.",
                    value=overlap,
                    limit=0.0,
                    related_cue_id=str(active["id"]),
                )
            )
        if active is None or float(cue["end"]) > float(active["end"]):
            active = cue

    severity_order = {"block": 0, "warn": 1}
    findings.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity")), 2),
            float(item.get("start", -1.0)),
            str(item.get("kind", "")),
        )
    )
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"subtitle-{index:03d}"

    blocking = sum(1 for finding in findings if finding["severity"] == "block")
    warnings = sum(1 for finding in findings if finding["severity"] == "warn")
    cps_values = [float(item["characters_per_second"]) for item in metrics]
    status = "blocked" if blocking else ("review" if warnings else "ready")

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": {
            "subtitle_pack": os.path.abspath(subtitle_path),
            "subtitle_version": str(payload.get("version") or ""),
            "media": os.path.abspath(media_path) if media_path else "",
            "media_duration": _round3(media_duration) if media_duration is not None else None,
        },
        "config": {
            "language": detected_language,
            "optimal_cps": optimal_cps,
            "max_cps": max_cps,
            "min_duration": min_duration,
            "hard_min_duration": hard_min_duration,
            "max_duration": max_duration,
            "max_lines": max_lines,
            "max_chars": line_limit,
        },
        "metrics": {
            "cues": metrics,
            "max_cps": _round3(max(cps_values, default=0.0)),
            "p95_cps": _round3(_percentile(cps_values, 0.95)),
            "mean_cps": _round3(sum(cps_values) / len(cps_values)) if cps_values else 0.0,
        },
        "findings": findings,
        "summary": {
            "status": status,
            "blocking": blocking,
            "warnings": warnings,
            "findings": len(findings),
            "cues": len(metrics),
            "max_cps": _round3(max(cps_values, default=0.0)),
            "overlaps": sum(1 for finding in findings if finding["kind"] == "cue_overlap"),
            "out_of_bounds": sum(1 for finding in findings if finding["kind"] == "media_out_of_bounds"),
        },
        "notes": [
            "CPS and layout thresholds are review heuristics; only change captions after watching the rendered master.",
            "Use subtitle_pack JSON aligned with the final speed, clip order, and cover offset.",
            "Structural timing errors and extreme reading-speed failures block; ordinary readability risks remain warnings.",
        ],
    }


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    lines = [
        "# Subtitle Readability QA",
        "",
        f"- Subtitle pack: `{source.get('subtitle_pack', '')}`",
        f"- Media: `{source.get('media') or 'not checked'}`",
        f"- Status: **{str(summary.get('status', 'unknown')).upper()}**",
        f"- Cues: {summary.get('cues', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Max CPS: {float(summary.get('max_cps', 0)):.1f}",
        "",
        "## Findings",
        "",
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if not findings:
        lines.append("- No configured subtitle timing or readability risk was detected.")
    else:
        lines += [
            "| ID | Severity | Cue | Time | Kind | Evidence | Action |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for finding in findings:
            if "start" in finding:
                time_range = (
                    f"{_format_time(float(finding['start']))}-"
                    f"{_format_time(float(finding.get('end', finding['start'])))}"
                )
            else:
                time_range = "-"
            lines.append(
                "| {id} | {severity} | {cue} | {time_range} | `{kind}` | {message} | {action} |".format(
                    id=finding.get("id", ""),
                    severity=str(finding.get("severity", "")).upper(),
                    cue=finding.get("cue_id", ""),
                    time_range=time_range,
                    kind=finding.get("kind", ""),
                    message=str(finding.get("message", "")).replace("|", "/"),
                    action=str(finding.get("action", "")).replace("|", "/"),
                )
            )
    lines += [
        "",
        "Review every BLOCK and relevant WARN on the rendered master, repair the source timing/text, regenerate the subtitle pack, and rerun this QA.",
        "",
    ]
    return "\n".join(lines)


def _error_report(subtitle_path: str, media_path: str, message: str) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "source": {
            "subtitle_pack": os.path.abspath(subtitle_path),
            "subtitle_version": "",
            "media": os.path.abspath(media_path) if media_path else "",
            "media_duration": None,
        },
        "config": {},
        "metrics": {"cues": [], "max_cps": 0.0, "p95_cps": 0.0, "mean_cps": 0.0},
        "findings": [
            {
                "id": "subtitle-001",
                "kind": "input_error",
                "severity": "block",
                "cue_id": "",
                "cue_index": 0,
                "text": "",
                "message": message,
                "action": "Fix the input artifact and rerun subtitle readability QA.",
            }
        ],
        "summary": {
            "status": "blocked",
            "blocking": 1,
            "warnings": 0,
            "findings": 1,
            "cues": 0,
            "max_cps": 0.0,
            "overlaps": 0,
            "out_of_bounds": 0,
        },
        "notes": [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit output-aligned subtitle timing, reading speed, line length, and media bounds."
    )
    parser.add_argument("subtitle_pack", help="subtitle_pack.v1 JSON aligned to the rendered output")
    parser.add_argument("--media", help="Optional rendered master/platform video for duration bounds")
    parser.add_argument("--output", required=True, help="Write subtitle_readability_qa.v1 JSON")
    parser.add_argument("--markdown", help="Optional human-readable Markdown report")
    parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    parser.add_argument("--optimal-cps", type=float, default=18.0, help="Warn above this reading speed")
    parser.add_argument("--max-cps", type=float, default=25.0, help="Block above this reading speed")
    parser.add_argument("--min-duration", type=float, default=0.5, help="Warn below this cue duration")
    parser.add_argument("--hard-min-duration", type=float, default=0.15, help="Block below this cue duration")
    parser.add_argument("--max-duration", type=float, default=7.0, help="Warn above this cue duration")
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--max-chars", type=int, help="Max visible characters per line; zh=18, en=42")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking is non-zero")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload, cues = load_subtitle_pack(args.subtitle_pack)
        media_duration = None
        if args.media:
            if not os.path.isfile(args.media):
                raise ValueError(f"media not found: {args.media}")
            media_duration = probe_duration(args.media)
        report = analyze_cues(
            args.subtitle_pack,
            payload,
            cues,
            language=args.language,
            optimal_cps=args.optimal_cps,
            max_cps=args.max_cps,
            min_duration=args.min_duration,
            hard_min_duration=args.hard_min_duration,
            max_duration=args.max_duration,
            max_lines=args.max_lines,
            max_chars=args.max_chars,
            media_path=args.media or "",
            media_duration=media_duration,
        )
    except (OSError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        report = _error_report(args.subtitle_pack, args.media or "", str(exc))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(emit_markdown(report))

    summary = report["summary"]
    print(
        "Subtitle readability QA: "
        f"status={summary['status']} cues={summary['cues']} "
        f"blocking={summary['blocking']} warnings={summary['warnings']}"
    )
    if args.strict and int(summary.get("blocking", 0)):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
