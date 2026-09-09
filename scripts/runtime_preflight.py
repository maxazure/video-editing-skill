#!/usr/bin/env python3
"""Inspect the local media toolchain before an edit or render starts.

The report distinguishes a component that is proven missing from a component
whose FFmpeg listing could not be read. Selected workflow profiles are checked
against the live machine and can be verified again by pipeline_manifest.py.
No media is decoded, rendered, uploaded, or modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "runtime_preflight.v1"
ALGORITHM = {
    "id": "runtime-capability-profile-v1",
    "command_detection": "PATH lookup plus version command",
    "ffmpeg_component_detection": "parse ffmpeg -filters and -encoders listings",
    "states": ["available", "missing", "unknown"],
    "policy": "missing and unknown required capabilities fail closed",
}

PROFILE_SPECS: Mapping[str, Mapping[str, Any]] = {
    "media_io": {
        "label": "Media inspection/extraction",
        "description": "Probe media or extract streams without requiring the full render filter chain.",
        "required": ["runtime:python", "command:ffmpeg", "command:ffprobe"],
        "alternatives": [],
    },
    "core_edit": {
        "label": "Core local edit/render",
        "description": "Run a deterministic H.264/AAC edit with the filters used by the main renderer.",
        "required": [
            "runtime:python",
            "command:ffmpeg",
            "command:ffprobe",
            "encoder:libx264",
            "encoder:aac",
            "filter:scale",
            "filter:crop",
            "filter:overlay",
            "filter:concat",
            "filter:aresample",
            "filter:loudnorm",
        ],
        "alternatives": [],
    },
    "captions": {
        "label": "Burned captions",
        "description": "Render SRT/ASS and the skill's subtitle presets through libass.",
        "required": ["filter:subtitles"],
        "alternatives": [],
    },
    "qa": {
        "label": "Local render QA",
        "description": "Run the final black/freeze/silence, loudness, signal, metric, and waveform checks.",
        "required": [
            "filter:blackdetect",
            "filter:freezedetect",
            "filter:silencedetect",
            "filter:ebur128",
            "filter:signalstats",
            "filter:ssim",
            "filter:psnr",
            "filter:showwavespic",
        ],
        "alternatives": [],
    },
    "hdr_sdr": {
        "label": "HDR to Rec.709 SDR",
        "description": "Run the explicit zscale plus tonemap delivery path.",
        "required": ["filter:zscale", "filter:tonemap"],
        "alternatives": [],
    },
    "stabilization": {
        "label": "Video stabilization",
        "description": "Use two-pass vidstab when available, with deshake as the declared fallback.",
        "required": [],
        "alternatives": [
            {
                "label": "two_pass_vidstab",
                "required": ["filter:vidstabdetect", "filter:vidstabtransform"],
            },
            {
                "label": "single_pass_deshake",
                "required": ["filter:deshake"],
            },
        ],
    },
    "remotion": {
        "label": "Remotion composition",
        "description": "Run the repository's TypeScript/Remotion composition workflow.",
        "required": ["command:node", "command:npx"],
        "alternatives": [],
    },
}

REMEDIES: Mapping[str, str] = {
    "runtime:python": "Run this skill with Python 3.10 or newer.",
    "command:ffmpeg": "Install a full FFmpeg build and make ffmpeg available on PATH.",
    "command:ffprobe": "Install ffprobe from the same FFmpeg distribution and make it available on PATH.",
    "command:node": "Install a supported Node.js runtime and make node available on PATH.",
    "command:npx": "Install npm/npx with Node.js and make npx available on PATH.",
    "encoder:libx264": "Install or rebuild FFmpeg with the libx264 encoder enabled.",
    "encoder:aac": "Install or rebuild FFmpeg with an AAC encoder enabled.",
    "filter:subtitles": "Install an FFmpeg build with libass/subtitles support (for example ffmpeg-full on macOS).",
    "filter:zscale": "Install an FFmpeg build with libzimg/zscale support.",
    "filter:tonemap": "Install an FFmpeg build with the tonemap filter enabled.",
    "filter:vidstabdetect": "Install an FFmpeg build with libvidstab, or use a build that provides deshake.",
    "filter:vidstabtransform": "Install an FFmpeg build with libvidstab, or use a build that provides deshake.",
    "filter:deshake": "Install an FFmpeg build with deshake, or enable both vidstabdetect and vidstabtransform.",
}

Runner = Callable[[Sequence[str], float], Mapping[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _run_command(args: Sequence[str], timeout_seconds: float) -> Mapping[str, Any]:
    try:
        result = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "returncode": None, "output": ""}
    except (FileNotFoundError, OSError) as exc:
        return {"status": "failed", "returncode": None, "output": "", "detail": str(exc)}
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return {
        "status": "completed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "output": output,
    }


def parse_component_listing(text: str) -> List[str]:
    """Return component names from FFmpeg 6/7/8-style flag tables."""
    names = set()
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 2:
            continue
        flags, name = parts[0], parts[1]
        if not re.fullmatch(r"[.A-Z|]{2,8}", flags):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            continue
        names.add(name)
    return sorted(names)


def _version_command(name: str) -> Sequence[str]:
    if name in {"ffmpeg", "ffprobe"}:
        return [name, "-version"]
    return [name, "--version"]


def _detect_command(name: str, *, timeout_seconds: float, runner: Runner) -> Dict[str, Any]:
    if shutil.which(name) is None:
        return {"status": "missing", "version": "", "detail": "not found on PATH"}
    result = runner(_version_command(name), timeout_seconds)
    if result.get("status") != "completed":
        detail = str(result.get("status") or "failed")
        if result.get("returncode") is not None:
            detail += f" (exit {result.get('returncode')})"
        return {"status": "unknown", "version": "", "detail": detail}
    lines = [line.strip() for line in str(result.get("output") or "").splitlines() if line.strip()]
    if not lines:
        return {"status": "unknown", "version": "", "detail": "version command returned no text"}
    return {"status": "available", "version": lines[0], "detail": "version command succeeded"}


def _detect_listing(kind: str, *, timeout_seconds: float, runner: Runner) -> Tuple[Dict[str, Any], List[str]]:
    option = "-filters" if kind == "filter" else "-encoders"
    result = runner(["ffmpeg", "-hide_banner", option], timeout_seconds)
    if result.get("status") != "completed":
        detail = str(result.get("status") or "failed")
        if result.get("returncode") is not None:
            detail += f" (exit {result.get('returncode')})"
        return {"status": "failed", "row_count": 0, "detail": detail}, []
    names = parse_component_listing(str(result.get("output") or ""))
    if not names:
        return {"status": "unparsed", "row_count": 0, "detail": "no component rows recognized"}, []
    return {"status": "parsed", "row_count": len(names), "detail": "FFmpeg listing parsed"}, names


def collect_snapshot(
    *,
    timeout_seconds: float = 10.0,
    runner: Runner = _run_command,
) -> Dict[str, Any]:
    commands = {
        name: _detect_command(name, timeout_seconds=timeout_seconds, runner=runner)
        for name in ("ffmpeg", "ffprobe", "node", "npx")
    }
    detections: Dict[str, Any] = {}
    components: Dict[str, List[str]] = {"filter": [], "encoder": []}
    if commands["ffmpeg"]["status"] == "available":
        for kind in ("filter", "encoder"):
            detection, names = _detect_listing(kind, timeout_seconds=timeout_seconds, runner=runner)
            detections[kind] = detection
            components[kind] = names
    else:
        state = "missing" if commands["ffmpeg"]["status"] == "missing" else "failed"
        for kind in ("filter", "encoder"):
            detections[kind] = {
                "status": state,
                "row_count": 0,
                "detail": "ffmpeg command is unavailable",
            }
    return {
        "python": {
            "status": "available" if sys.version_info >= (3, 10) else "missing",
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "commands": commands,
        "detections": detections,
        "components": components,
    }


def _public_runtime(snapshot: Mapping[str, Any], needed: Sequence[str]) -> Dict[str, Any]:
    command_names = {
        capability.partition(":")[2]
        for capability in needed
        if capability.startswith("command:")
    }
    component_kinds = {
        capability.partition(":")[0]
        for capability in needed
        if capability.startswith(("filter:", "encoder:"))
    }
    if component_kinds:
        command_names.add("ffmpeg")
    commands = snapshot.get("commands") if isinstance(snapshot.get("commands"), Mapping) else {}
    detections = snapshot.get("detections") if isinstance(snapshot.get("detections"), Mapping) else {}
    return {
        "python": dict(snapshot.get("python") or {}),
        "commands": {
            name: dict(commands.get(name) or {})
            for name in sorted(command_names)
        },
        "detections": {
            name: dict(detections.get(name) or {})
            for name in sorted(component_kinds)
        },
    }


def _capability_state(capability: str, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    kind, _, name = capability.partition(":")
    if not kind or not name:
        raise ValueError(f"invalid capability: {capability}")
    if kind == "runtime" and name == "python":
        runtime = snapshot.get("python") if isinstance(snapshot.get("python"), Mapping) else {}
        return {"status": str(runtime.get("status") or "unknown"), "evidence": str(runtime.get("version") or "")}
    if kind == "command":
        commands = snapshot.get("commands") if isinstance(snapshot.get("commands"), Mapping) else {}
        command = commands.get(name) if isinstance(commands.get(name), Mapping) else {}
        return {"status": str(command.get("status") or "unknown"), "evidence": str(command.get("version") or command.get("detail") or "")}
    if kind in {"filter", "encoder"}:
        commands = snapshot.get("commands") if isinstance(snapshot.get("commands"), Mapping) else {}
        ffmpeg = commands.get("ffmpeg") if isinstance(commands.get("ffmpeg"), Mapping) else {}
        if ffmpeg.get("status") == "missing":
            return {"status": "missing", "evidence": "ffmpeg command is missing"}
        detections = snapshot.get("detections") if isinstance(snapshot.get("detections"), Mapping) else {}
        detection = detections.get(kind) if isinstance(detections.get(kind), Mapping) else {}
        if detection.get("status") != "parsed":
            return {"status": "unknown", "evidence": str(detection.get("detail") or "listing unavailable")}
        components = snapshot.get("components") if isinstance(snapshot.get("components"), Mapping) else {}
        names = set(str(item) for item in (components.get(kind) or []))
        return {
            "status": "available" if name in names else "missing",
            "evidence": f"{kind} listing parsed ({detection.get('row_count', len(names))} rows)",
        }
    raise ValueError(f"unsupported capability kind: {kind}")


def _remedy(capability: str) -> str:
    return REMEDIES.get(capability, f"Install an FFmpeg build that provides {capability}.")


def _branch_status(required: Sequence[str], capabilities: Mapping[str, Mapping[str, Any]]) -> str:
    states = [str(capabilities[item].get("status") or "unknown") for item in required]
    if all(state == "available" for state in states):
        return "available"
    if "missing" in states:
        return "missing"
    return "unknown"


def _check(
    *,
    profile: str,
    code: str,
    status: str,
    message: str,
    capability: str = "",
    remedy: str = "",
    alternatives: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "profile": profile,
        "code": code,
        "status": status,
        "severity": "info" if status == "available" else "block",
        "message": message,
    }
    if capability:
        item["capability"] = capability
    if remedy:
        item["remedy"] = remedy
    if alternatives is not None:
        item["alternatives"] = list(alternatives)
    return item


def _stable_payload(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "selected_profiles": report.get("selected_profiles"),
        "settings": report.get("settings"),
        "algorithm": report.get("algorithm"),
        "runtime": report.get("runtime"),
        "capabilities": report.get("capabilities"),
        "profiles": report.get("profiles"),
        "checks": report.get("checks"),
        "limitations": report.get("limitations"),
    }


def _report_id(report: Mapping[str, Any]) -> str:
    return "rpf_" + _digest(_stable_payload(report))[:24]


def _refresh_summary(report: Dict[str, Any]) -> None:
    checks = [item for item in report.get("checks") or [] if isinstance(item, Mapping)]
    profiles = [item for item in report.get("profiles") or [] if isinstance(item, Mapping)]
    blocking = sum(1 for item in checks if item.get("severity") == "block")
    warnings = sum(1 for item in checks if item.get("severity") == "warn")
    report["status"] = "ready" if blocking == 0 else "blocked"
    report["summary"] = {
        "profiles": len(profiles),
        "ready_profiles": sum(1 for item in profiles if item.get("status") == "ready"),
        "capabilities": len(report.get("capabilities") or {}),
        "blocking": blocking,
        "warnings": warnings,
    }
    report["report_id"] = _report_id(report)


def build_report(
    profile_names: Optional[Sequence[str]] = None,
    *,
    timeout_seconds: float = 10.0,
    snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be in (0, 60]")
    selected = sorted(set(profile_names or ["core_edit"]))
    if not selected:
        selected = ["core_edit"]
    unknown_profiles = [name for name in selected if name not in PROFILE_SPECS]
    if unknown_profiles:
        raise ValueError(f"unknown runtime profile: {unknown_profiles[0]}")

    live_snapshot = dict(snapshot) if snapshot is not None else collect_snapshot(timeout_seconds=timeout_seconds)
    needed = set()
    for profile_name in selected:
        spec = PROFILE_SPECS[profile_name]
        needed.update(str(item) for item in spec.get("required") or [])
        for alternative in spec.get("alternatives") or []:
            needed.update(str(item) for item in alternative.get("required") or [])
    capabilities = {
        capability: _capability_state(capability, live_snapshot)
        for capability in sorted(needed)
    }

    checks: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []
    for profile_name in selected:
        spec = PROFILE_SPECS[profile_name]
        profile_checks: List[str] = []
        for capability in spec.get("required") or []:
            state = str(capabilities[capability]["status"])
            code = f"{profile_name}:{capability.replace(':', '_')}"
            profile_checks.append(code)
            checks.append(
                _check(
                    profile=profile_name,
                    code=code,
                    status=state,
                    capability=capability,
                    message=f"{capability} is {state}.",
                    remedy="" if state == "available" else _remedy(capability),
                )
            )

        alternative_results = []
        for alternative in spec.get("alternatives") or []:
            required = [str(item) for item in alternative.get("required") or []]
            alternative_results.append(
                {
                    "label": str(alternative.get("label") or "alternative"),
                    "required": required,
                    "status": _branch_status(required, capabilities),
                }
            )
        if alternative_results:
            if any(item["status"] == "available" for item in alternative_results):
                state = "available"
            elif any(item["status"] == "unknown" for item in alternative_results):
                state = "unknown"
            else:
                state = "missing"
            code = f"{profile_name}:backend"
            profile_checks.append(code)
            missing_caps = sorted(
                {
                    capability
                    for item in alternative_results
                    if item["status"] != "available"
                    for capability in item["required"]
                }
            )
            checks.append(
                _check(
                    profile=profile_name,
                    code=code,
                    status=state,
                    message=(
                        "At least one declared backend is available."
                        if state == "available"
                        else f"No declared backend is proven available ({state})."
                    ),
                    remedy="" if state == "available" else " ".join(_remedy(item) for item in missing_caps),
                    alternatives=alternative_results,
                )
            )

        selected_checks = [item for item in checks if item["profile"] == profile_name]
        profiles.append(
            {
                "id": profile_name,
                "label": spec["label"],
                "description": spec["description"],
                "status": "ready" if all(item["severity"] != "block" for item in selected_checks) else "blocked",
                "check_codes": profile_checks,
            }
        )

    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "selected_profiles": selected,
        "settings": {"timeout_seconds": float(timeout_seconds)},
        "algorithm": dict(ALGORITHM),
        "runtime": _public_runtime(live_snapshot, sorted(needed)),
        "capabilities": capabilities,
        "profiles": profiles,
        "checks": checks,
        "limitations": [
            "Capability discovery proves that a binary or FFmpeg component is listed; it does not run a real encode, decode, GPU, font, or provider job.",
            "A listed hardware encoder may still fail because of driver, device, session, or pixel-format constraints; no hardware encoder is required by these profiles.",
            "A ready runtime report does not replace project input preflight, media probing, full decode, visual review, audio review, or final delivery QA.",
        ],
    }
    _refresh_summary(report)
    return report


def verify_report(
    stored_report: Mapping[str, Any],
    project_dir: Optional[str] = None,
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    del project_dir  # Signature matches source-bound gates; this gate is machine-bound.
    if not isinstance(stored_report, Mapping) or stored_report.get("schema") != SCHEMA:
        raise ValueError(f"report must use schema {SCHEMA}")
    selected = stored_report.get("selected_profiles")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ValueError("report selected_profiles must be a list of profile ids")
    settings = stored_report.get("settings") if isinstance(stored_report.get("settings"), Mapping) else {}
    try:
        timeout_seconds = float(settings.get("timeout_seconds", 10.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("report timeout_seconds is invalid") from exc

    live = build_report(selected, timeout_seconds=timeout_seconds, snapshot=snapshot)
    measured_report_id = str(live.get("report_id") or "")
    verification_checks: List[Dict[str, Any]] = []
    stored_expected_id = _report_id(stored_report)
    if str(stored_report.get("report_id") or "") != stored_expected_id:
        verification_checks.append(
            _check(
                profile="verification",
                code="stored_report_integrity",
                status="missing",
                message="Stored runtime report content does not match its report_id.",
                remedy="Run runtime_preflight.py analyze again and do not hand-edit the report.",
            )
        )
    if stored_expected_id != measured_report_id:
        verification_checks.append(
            _check(
                profile="verification",
                code="runtime_environment_drift",
                status="missing",
                message="Live runtime capabilities or versions differ from the stored report.",
                remedy="Review the live result and replace the stored report with a fresh analyze run.",
            )
        )
    live["checks"].extend(verification_checks)
    live["verification"] = {
        "stored_report_id": str(stored_report.get("report_id") or ""),
        "stored_expected_id": stored_expected_id,
        "measured_report_id": measured_report_id,
        "drift": stored_expected_id != measured_report_id,
        "integrity": str(stored_report.get("report_id") or "") == stored_expected_id,
    }
    _refresh_summary(live)
    return live


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Runtime Preflight",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Report ID: `{report.get('report_id')}`",
        f"- Profiles: {', '.join(report.get('selected_profiles') or [])}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "## Profiles",
        "",
        "| profile | status | purpose |",
        "|---|---|---|",
    ]
    for item in report.get("profiles") or []:
        lines.append(f"| `{item.get('id')}` | {item.get('status')} | {item.get('description')} |")
    lines.extend(
        [
            "",
            "## Capability Checks",
            "",
            "| profile | capability/backend | state | action |",
            "|---|---|---|---|",
        ]
    )
    for item in report.get("checks") or []:
        capability = item.get("capability") or item.get("code")
        remedy = str(item.get("remedy") or "")
        lines.append(
            f"| `{item.get('profile')}` | `{capability}` | {item.get('status')} | {remedy.replace('|', '/')} |"
        )
    runtime = report.get("runtime") if isinstance(report.get("runtime"), Mapping) else {}
    lines.extend(["", "## Runtime", "", "| component | state | version/detail |", "|---|---|---|"])
    python_info = runtime.get("python") if isinstance(runtime.get("python"), Mapping) else {}
    lines.append(
        f"| Python | {python_info.get('status')} | {python_info.get('implementation', '')} {python_info.get('version', '')} |"
    )
    for name, info in sorted((runtime.get("commands") or {}).items()):
        lines.append(f"| `{name}` | {info.get('status')} | {str(info.get('version') or info.get('detail') or '').replace('|', '/')} |")
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    return "\n".join(lines) + "\n"


def write_json(path: str, report: Mapping[str, Any]) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: str, text: str) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and verify local video-production runtime capabilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Inspect the current runtime and write a capability report.")
    analyze.add_argument("--profile", action="append", choices=sorted(PROFILE_SPECS), help="Workflow profile to require; repeatable. Defaults to core_edit.")
    analyze.add_argument("--timeout-seconds", type=float, default=10.0, help="Timeout for each introspection command, from 0 to 60 seconds.")
    analyze.add_argument("--output", required=True, help="runtime_preflight.v1 JSON output path.")
    analyze.add_argument("--markdown", help="Optional human-readable Markdown output path.")
    analyze.add_argument("--strict", action="store_true", help="Exit 2 when a required capability is missing or unknown.")

    verify = subparsers.add_parser("verify", help="Re-run the stored profiles and reject runtime/report drift.")
    verify.add_argument("--report", required=True, help="Stored runtime_preflight.v1 JSON report.")
    verify.add_argument("--output", help="Optional JSON path for the live verification result.")
    verify.add_argument("--markdown", help="Optional Markdown path for the live verification result.")
    verify.add_argument("--strict", action="store_true", help="Exit 2 when live verification has blockers.")

    subparsers.add_parser("list-profiles", help="List built-in workflow profiles and their purpose.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "list-profiles":
        for name, spec in PROFILE_SPECS.items():
            print(f"{name}\t{spec['label']}\t{spec['description']}")
        return 0
    try:
        if args.command == "analyze":
            report = build_report(args.profile, timeout_seconds=args.timeout_seconds)
            write_json(args.output, report)
            if args.markdown:
                write_text(args.markdown, emit_markdown(report))
        else:
            stored = json.loads(Path(args.report).expanduser().read_text(encoding="utf-8"))
            report = verify_report(stored)
            if args.output:
                write_json(args.output, report)
            if args.markdown:
                write_text(args.markdown, emit_markdown(report))
        print(json.dumps({"status": report["status"], "report_id": report["report_id"], "summary": report["summary"]}, ensure_ascii=False))
        return 2 if args.strict and report["summary"]["blocking"] else 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"runtime_preflight error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
