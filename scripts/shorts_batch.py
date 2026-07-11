#!/usr/bin/env python3
"""Plan a render batch from highlight_picker.py candidates.

The script is local-first and side-effect-light: it writes one render_config
per selected highlight plus a JSON/Markdown job sheet. It does not render,
upload, or call any model/provider.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


VERSION = "shorts_batch.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _shell(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _job_id(basename: str, rank: int) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in basename.strip().lower())
    cleaned = cleaned.strip("_-") or "short"
    return f"{cleaned}_{rank:03d}"


def _is_file(path: str) -> bool:
    return Path(path).expanduser().is_file()


def _candidate_title(candidate: Mapping[str, Any], rank: int) -> str:
    title = str(candidate.get("title_suggestion") or candidate.get("hook_text") or "").strip()
    return title or f"Short {rank:03d}"


def _render_config_for_job(
    candidate: Mapping[str, Any],
    *,
    video_path: str,
    job_id: str,
    rank: int,
    platform: str,
    cover_style: str,
    profile: Optional[str],
    primary_speed: Optional[float],
    source_highlights: str,
) -> Dict[str, Any]:
    clip: Dict[str, Any] = {
        "name": job_id,
        "video": video_path,
        "start": _round3(candidate.get("start")),
        "end": _round3(candidate.get("end")),
        "text": str(candidate.get("text") or ""),
        "highlight_score": candidate.get("score", 0),
        "segment_ids": list(candidate.get("segment_ids") or []),
    }
    for key in ("brief_match", "scene_snap", "audio_boundary_snap"):
        if candidate.get(key):
            clip[key] = candidate[key]

    config: Dict[str, Any] = {
        "version": "render_config.v1",
        "source": "shorts_batch.py",
        "title": _candidate_title(candidate, rank),
        "cover_style": cover_style,
        "platform": platform,
        "metadata": {
            "batch_job_id": job_id,
            "highlight_id": candidate.get("id"),
            "highlight_rank": candidate.get("rank", rank),
            "source_highlights": source_highlights,
        },
        "clips": [clip],
    }
    if profile:
        config["profile"] = profile
    if primary_speed:
        config["primary_speed"] = float(primary_speed)
    return config


def build_shorts_batch(
    highlights: Mapping[str, Any],
    *,
    video_path: str,
    highlights_path: str = "",
    output_dir: str = "output/shorts",
    render_config_dir: str = "work/shorts_render_configs",
    qa_dir: str = "verify/shorts",
    basename: str = "short",
    platform: Optional[str] = None,
    cover_style: str = "bold",
    profile: Optional[str] = None,
    primary_speed: Optional[float] = 1.25,
    python_bin: str = "python3",
    include_qa: bool = True,
) -> Dict[str, Any]:
    selected = [item for item in highlights.get("selected") or [] if isinstance(item, Mapping)]
    source_exists = _is_file(video_path)
    source_platform = str((highlights.get("params") or {}).get("platform") or "douyin")
    platform = platform or source_platform

    jobs: List[Dict[str, Any]] = []
    render_config_root = Path(render_config_dir)
    output_root = Path(output_dir)
    qa_root = Path(qa_dir)

    for index, candidate in enumerate(selected, start=1):
        rank = int(candidate.get("rank") or index)
        job_id = _job_id(basename, rank)
        render_config_path = str(render_config_root / f"{job_id}_render_config.json")
        output_video = str(output_root / f"{job_id}.mp4")
        qa_json = str(qa_root / f"{job_id}_qa.json")
        qa_review_dir = str(qa_root / job_id)
        blockers: List[str] = []
        warnings = [str(item) for item in candidate.get("warnings") or []]
        if not source_exists:
            blockers.append(f"source video not found: {video_path}")

        render_command = [
            python_bin,
            "scripts/render_final.py",
            "--config",
            render_config_path,
            "--output",
            output_video,
        ]
        if primary_speed:
            render_command.extend(["--primary-speed", str(primary_speed)])
        if profile:
            render_command.extend(["--profile", profile])

        qa_command = [
            python_bin,
            "scripts/render_qa.py",
            output_video,
            "--platform",
            platform,
            "--json",
            qa_json,
            "--review-dir",
            qa_review_dir,
        ]

        job = {
            "id": job_id,
            "status": "blocked" if blockers else "planned",
            "rank": rank,
            "highlight_id": candidate.get("id"),
            "title": _candidate_title(candidate, rank),
            "score": candidate.get("score", 0),
            "start": _round3(candidate.get("start")),
            "end": _round3(candidate.get("end")),
            "duration": _round3(candidate.get("duration")),
            "segment_ids": list(candidate.get("segment_ids") or []),
            "warnings": warnings,
            "blockers": blockers,
            "render_config": render_config_path,
            "output_video": output_video,
            "render_command": render_command,
            "render_shell": _shell(render_command),
        }
        if include_qa:
            job["qa_json"] = qa_json
            job["qa_review_dir"] = qa_review_dir
            job["qa_command"] = qa_command
            job["qa_shell"] = _shell(qa_command)
        jobs.append(job)

    blocking = sum(1 for job in jobs if job["blockers"])
    status = "blocked" if blocking else ("ready" if jobs else "blocked")
    if not jobs:
        blocking = 1

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": status,
        "source": {
            "highlights": highlights_path,
            "video": video_path,
            "video_exists": source_exists,
            "highlight_version": highlights.get("version"),
            "platform": platform,
        },
        "params": {
            "output_dir": output_dir,
            "render_config_dir": render_config_dir,
            "qa_dir": qa_dir if include_qa else None,
            "basename": basename,
            "cover_style": cover_style,
            "profile": profile,
            "primary_speed": primary_speed,
            "include_qa": include_qa,
        },
        "summary": {
            "selected": len(selected),
            "jobs": len(jobs),
            "planned": sum(1 for job in jobs if job["status"] == "planned"),
            "blocking": blocking,
            "warnings": sum(len(job["warnings"]) for job in jobs),
        },
        "jobs": jobs,
        "notes": [
            "Review the Markdown job sheet before rendering a large batch.",
            "Run each render_shell command, then run qa_shell for the produced MP4.",
            "This script does not render or upload media.",
        ],
    }


def emit_markdown(batch: Mapping[str, Any]) -> str:
    summary = batch.get("summary") or {}
    lines = [
        "# Shorts Batch",
        "",
        f"- Status: **{str(batch.get('status', '')).upper()}**",
        f"- Source video: `{(batch.get('source') or {}).get('video', '')}`",
        f"- Jobs: {summary.get('jobs', 0)}",
        f"- Planned: {summary.get('planned', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "| job | time | score | status | title | warnings | output |",
        "|---|---|---:|---|---|---|---|",
    ]
    for job in batch.get("jobs") or []:
        warnings = "; ".join(job.get("warnings") or []) or "-"
        lines.append(
            "| {id} | {start:.2f}-{end:.2f}s | {score} | {status} | {title} | {warnings} | `{output}` |".format(
                id=job.get("id", ""),
                start=float(job.get("start") or 0.0),
                end=float(job.get("end") or 0.0),
                score=job.get("score", 0),
                status=job.get("status", ""),
                title=str(job.get("title", "")).replace("|", "\\|"),
                warnings=warnings.replace("|", "\\|"),
                output=job.get("output_video", ""),
            )
        )

    if batch.get("jobs"):
        lines.extend(["", "## Commands", ""])
        for job in batch.get("jobs") or []:
            lines.extend([
                f"### {job.get('id', '')}",
                "",
                "```bash",
                str(job.get("render_shell", "")),
                "```",
            ])
            if job.get("qa_shell"):
                lines.extend([
                    "",
                    "```bash",
                    str(job.get("qa_shell", "")),
                    "```",
                ])
            if job.get("blockers"):
                lines.extend(["", "Blockers:"])
                lines.extend(f"- {item}" for item in job.get("blockers") or [])
            lines.append("")

    lines.extend([
        "## Review Notes",
        "",
        "- Check each hook, ending, and warning before rendering the full batch.",
        "- If a candidate starts weak or ends mid-thought, adjust the highlight plan first.",
        "- After rendering, run the QA command for each job and keep the QA JSON with the output video.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_render_configs(batch: Mapping[str, Any], highlights: Mapping[str, Any], *, video_path: str) -> None:
    candidates = {
        (item.get("id"), item.get("rank")): item
        for item in highlights.get("selected") or []
        if isinstance(item, Mapping)
    }
    params = batch.get("params") or {}
    source = batch.get("source") or {}
    for job in batch.get("jobs") or []:
        key = (job.get("highlight_id"), job.get("rank"))
        candidate = candidates.get(key)
        if not candidate:
            continue
        config = _render_config_for_job(
            candidate,
            video_path=video_path,
            job_id=str(job["id"]),
            rank=int(job["rank"]),
            platform=str(source.get("platform") or "douyin"),
            cover_style=str(params.get("cover_style") or "bold"),
            profile=params.get("profile"),
            primary_speed=params.get("primary_speed"),
            source_highlights=str(source.get("highlights") or ""),
        )
        write_json(str(job["render_config"]), config)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create per-short render jobs from highlight_picker.py selected candidates."
    )
    parser.add_argument("--highlights", required=True, help="highlight_candidates JSON from highlight_picker.py")
    parser.add_argument("--video", required=True, help="Source video path used by each render config")
    parser.add_argument("--output", required=True, help="Output shorts_batch JSON")
    parser.add_argument("--markdown", help="Optional Markdown job sheet")
    parser.add_argument("--render-config-dir", default="work/shorts_render_configs")
    parser.add_argument("--output-dir", default="output/shorts")
    parser.add_argument("--qa-dir", default="verify/shorts")
    parser.add_argument("--basename", default="short")
    parser.add_argument("--platform", help="Override platform for QA commands; defaults to highlight params")
    parser.add_argument("--cover-style", default="bold")
    parser.add_argument("--profile")
    parser.add_argument("--primary-speed", type=float, default=1.25)
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--no-qa", action="store_true", help="Do not emit render_qa.py commands")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when the batch has blockers")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        highlights = load_json(args.highlights)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    batch = build_shorts_batch(
        highlights,
        video_path=args.video,
        highlights_path=args.highlights,
        output_dir=args.output_dir,
        render_config_dir=args.render_config_dir,
        qa_dir=args.qa_dir,
        basename=args.basename,
        platform=args.platform,
        cover_style=args.cover_style,
        profile=args.profile,
        primary_speed=args.primary_speed,
        python_bin=args.python_bin,
        include_qa=not args.no_qa,
    )

    write_json(args.output, batch)
    write_render_configs(batch, highlights, video_path=args.video)
    if args.markdown:
        write_text(args.markdown, emit_markdown(batch))

    summary = batch["summary"]
    print(
        "Shorts batch: "
        f"{batch['status']} jobs={summary['jobs']} planned={summary['planned']} "
        f"blocking={summary['blocking']} wrote {args.output}",
        file=sys.stderr,
    )
    if args.strict and int(summary.get("blocking") or 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
