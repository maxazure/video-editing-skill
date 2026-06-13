#!/usr/bin/env python3
"""Track async image/video generation tasks without calling providers.

This keeps Dreamina/即梦, PixVerse, Veo, Sora, and similar async jobs in a
local JSON ledger so submit IDs, polling commands, downloaded files, and
blocking states survive across agent sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


LOG_VERSION = "generation_task_log.v1"

STATUS_ALIASES = {
    "": "planned",
    "todo": "planned",
    "approval": "needs_approval",
    "approved": "planned",
    "pending": "queued",
    "wait": "queued",
    "waiting": "queued",
    "queue": "queued",
    "normal": "completed",
    "success": "completed",
    "succeeded": "completed",
    "done": "completed",
    "complete": "completed",
    "downloaded": "downloaded",
    "ready": "ready",
    "linked": "linked",
    "fail": "failed",
    "failure": "failed",
    "notapproved": "not_approved",
    "not_approved": "not_approved",
    "policy_violation": "not_approved",
    "timeout": "timeout",
}

ACTIVE_STATUSES = {"planned", "needs_approval", "submitted", "queued", "processing", "running"}
FAILED_STATUSES = {"failed", "error", "not_approved", "timeout"}
NONBLOCKING_STATUSES = {"downloaded", "ready", "linked", "skipped", "canceled"}
BLOCKING_READINESS = {
    "needs_approval",
    "pending",
    "processing",
    "needs_download",
    "missing_asset",
    "failed",
    "unknown",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_ALIASES.get(raw, raw or "planned")


def _abs_path(path: str, base: Optional[Path] = None) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    return str(p.resolve())


def _path_exists(path: Any) -> bool:
    if not path:
        return False
    return Path(str(path)).expanduser().exists()


def _first_path(task: Mapping[str, Any]) -> str:
    for key in ("asset_path", "expected_path"):
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return ""


def _provider_key(provider: str) -> str:
    return provider.strip().lower().replace(" ", "_")


def _provider_commands(
    *,
    provider: str,
    provider_task_id: str,
    asset_type: str,
    download_dir: str,
) -> Tuple[str, str]:
    if not provider_task_id:
        return "", ""

    normalized = _provider_key(provider)
    if normalized in {"dreamina", "jimeng", "jianying", "dreamina_video", "dreamina_seedance"}:
        poll = f"dreamina query_result --submit_id={provider_task_id}"
        download = f"dreamina query_result --submit_id={provider_task_id} --download_dir={download_dir}"
        return poll, download

    if normalized in {"pixverse", "pixverse_video", "pixverse_image"}:
        kind = "image" if asset_type == "image" else "video"
        poll = f"pixverse task status {provider_task_id} --type {kind} --json"
        download = f"pixverse asset download {provider_task_id} --type {kind} --dest {download_dir} --json"
        return poll, download

    return "", ""


def _task_key(task: Mapping[str, Any], *, fallback_index: int = 0) -> str:
    explicit = str(task.get("task_key") or "").strip()
    if explicit:
        return explicit
    provider = _provider_key(str(task.get("provider") or "provider"))
    task_id = str(task.get("provider_task_id") or "").strip()
    if task_id:
        return f"{provider}:{task_id}"
    shot_id = str(task.get("shot_id") or "").strip()
    if shot_id:
        return f"{provider}:{shot_id}"
    return f"{provider}:task_{fallback_index:03d}"


def _coerce_task(task: MutableMapping[str, Any], *, index: int = 0) -> Dict[str, Any]:
    normalized = dict(task)
    normalized["task_key"] = _task_key(normalized, fallback_index=index)
    normalized["provider"] = str(normalized.get("provider") or "unknown")
    normalized["asset_type"] = str(normalized.get("asset_type") or "video")
    normalized["status"] = normalize_status(normalized.get("status"))
    normalized.setdefault("created_at", utc_now())
    normalized.setdefault("updated_at", normalized["created_at"])
    normalized.setdefault("notes", [])
    if not isinstance(normalized["notes"], list):
        normalized["notes"] = [str(normalized["notes"])]
    return normalized


def new_log() -> Dict[str, Any]:
    now = utc_now()
    return {
        "version": LOG_VERSION,
        "created_at": now,
        "updated_at": now,
        "summary": {
            "tasks": 0,
            "blocking": 0,
            "pending": 0,
            "needs_approval": 0,
            "needs_download": 0,
            "failed": 0,
            "ready": 0,
        },
        "tasks": [],
        "next_actions": [],
    }


def load_log(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return new_log()
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("generation task log must be a JSON object")
    data.setdefault("version", LOG_VERSION)
    data.setdefault("created_at", utc_now())
    data.setdefault("tasks", [])
    return refresh_log(data)


def _find_value(data: Any, keys: Sequence[str]) -> Any:
    if isinstance(data, Mapping):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _find_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_value(value, keys)
            if found not in (None, ""):
                return found
    return None


def task_from_raw_json(raw: Mapping[str, Any]) -> Dict[str, Any]:
    task: Dict[str, Any] = {}
    status_code = _find_value(raw, ("status_code", "code"))
    status = _find_value(raw, ("status", "state", "task_status"))
    if status_code in {1, "1"}:
        status = "completed"
    elif status_code in {8, "8"}:
        status = "failed"
    elif status_code in {7, "7"}:
        status = "not_approved"
    elif status_code in {5, "5", 9, "9"}:
        status = "queued"
    elif status_code in {10, "10"}:
        status = "processing"

    task_id = _find_value(raw, ("submit_id", "task_id", "video_id", "image_id", "id"))
    result_url = _find_value(raw, ("video_url", "image_url", "result_url", "download_url", "url"))
    asset_path = _find_value(raw, ("file", "local_path", "asset_path", "path"))
    error = _find_value(raw, ("error", "message", "failure_reason"))

    if status:
        task["status"] = status
    if task_id:
        task["provider_task_id"] = str(task_id)
    if result_url:
        task["result_url"] = str(result_url)
    if asset_path:
        task["asset_path"] = str(asset_path)
    if error and normalize_status(status) in FAILED_STATUSES:
        task["error"] = str(error)
    return task


def evaluate_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    status = normalize_status(task.get("status"))
    path = _first_path(task)
    local_exists = _path_exists(path)
    readiness = "unknown"
    blocking = True
    next_action = ""

    if status == "needs_approval":
        readiness = "needs_approval"
        next_action = "Confirm paid-credit use before submitting this generation task."
    elif status in {"planned", "submitted", "queued"}:
        readiness = "pending"
        next_action = str(task.get("poll_command") or "Poll the provider until the task completes.")
    elif status in {"processing", "running"}:
        readiness = "processing"
        next_action = str(task.get("poll_command") or "Poll the provider until the task completes.")
    elif status == "completed":
        if local_exists:
            readiness = "ready"
            blocking = False
        else:
            readiness = "needs_download"
            next_action = str(task.get("download_command") or "Download the generated asset and update asset_path.")
    elif status in FAILED_STATUSES:
        readiness = "failed"
        next_action = "Review provider error, retry with a smaller prompt, or choose a fallback asset."
    elif status in NONBLOCKING_STATUSES:
        if status in {"downloaded", "ready", "linked"} and path and not local_exists:
            readiness = "missing_asset"
            next_action = "Update asset_path or restore the generated file before render."
        else:
            readiness = "ready"
            blocking = False
    else:
        next_action = "Normalize this provider status or update the task manually."

    if readiness in BLOCKING_READINESS:
        blocking = True

    return {
        "status": status,
        "readiness": readiness,
        "blocking": blocking,
        "local_asset_exists": local_exists,
        "next_action": next_action,
    }


def refresh_log(log: Mapping[str, Any]) -> Dict[str, Any]:
    tasks = [
        _coerce_task(dict(task), index=idx)
        for idx, task in enumerate(log.get("tasks") or [], start=1)
        if isinstance(task, Mapping)
    ]
    for task in tasks:
        state = evaluate_task(task)
        task["readiness"] = state["readiness"]
        task["blocking"] = state["blocking"]
        task["local_asset_exists"] = state["local_asset_exists"]
        task["next_action"] = state["next_action"]

    summary = {
        "tasks": len(tasks),
        "blocking": sum(1 for task in tasks if task.get("blocking")),
        "pending": sum(1 for task in tasks if task.get("readiness") in {"pending", "processing"}),
        "needs_approval": sum(1 for task in tasks if task.get("readiness") == "needs_approval"),
        "needs_download": sum(1 for task in tasks if task.get("readiness") == "needs_download"),
        "failed": sum(1 for task in tasks if task.get("readiness") == "failed"),
        "missing_asset": sum(1 for task in tasks if task.get("readiness") == "missing_asset"),
        "ready": sum(1 for task in tasks if task.get("readiness") == "ready"),
    }
    next_actions = [
        str(task.get("next_action"))
        for task in tasks
        if task.get("blocking") and task.get("next_action")
    ]

    updated = dict(log)
    updated["version"] = LOG_VERSION
    updated.setdefault("created_at", utc_now())
    updated["updated_at"] = utc_now()
    updated["summary"] = summary
    updated["tasks"] = tasks
    updated["next_actions"] = list(dict.fromkeys(next_actions))
    return updated


def _matches(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    if existing.get("task_key") and existing.get("task_key") == incoming.get("task_key"):
        return True
    if (
        existing.get("provider_task_id")
        and incoming.get("provider_task_id")
        and existing.get("provider_task_id") == incoming.get("provider_task_id")
        and _provider_key(str(existing.get("provider") or "")) == _provider_key(str(incoming.get("provider") or ""))
    ):
        return True
    if (
        existing.get("shot_id")
        and incoming.get("shot_id")
        and existing.get("shot_id") == incoming.get("shot_id")
        and not existing.get("provider_task_id")
    ):
        return True
    return False


def upsert_task(log: Mapping[str, Any], task: Mapping[str, Any]) -> Dict[str, Any]:
    incoming = _coerce_task(dict(task))
    now = utc_now()
    tasks: List[Dict[str, Any]] = []
    replaced = False

    for existing in log.get("tasks") or []:
        if not isinstance(existing, Mapping):
            continue
        current = _coerce_task(dict(existing))
        if _matches(current, incoming):
            merged = dict(current)
            for key, value in incoming.items():
                if value not in (None, "", []):
                    merged[key] = value
            merged["created_at"] = current.get("created_at") or incoming.get("created_at") or now
            merged["updated_at"] = now
            tasks.append(merged)
            replaced = True
        else:
            tasks.append(current)

    if not replaced:
        incoming["created_at"] = incoming.get("created_at") or now
        incoming["updated_at"] = now
        tasks.append(incoming)

    updated = dict(log)
    updated["tasks"] = tasks
    return refresh_log(updated)


def import_provider_decision(log: Mapping[str, Any], decision_log: Mapping[str, Any]) -> Dict[str, Any]:
    updated = dict(log)
    for decision in decision_log.get("decisions") or []:
        if not isinstance(decision, Mapping):
            continue
        selected = str(decision.get("selected") or "")
        paid = bool(decision.get("paid_credit") or decision.get("approval_required"))
        status = str(decision.get("status") or "")
        if not paid and selected not in {"dreamina_video", "dreamina_seedance", "veo", "ltx", "wan", "sora"}:
            continue
        if status in {"ready", "candidate_found"}:
            continue
        task = {
            "provider": selected,
            "task_key": f"{selected}:{decision.get('shot_id') or decision.get('decision_id')}",
            "shot_id": decision.get("shot_id"),
            "asset_type": "video" if selected in {"dreamina_video", "dreamina_seedance", "veo", "ltx", "wan", "sora"} else "image",
            "status": "needs_approval" if decision.get("approval_required") else "planned",
            "expected_path": decision.get("expected_path"),
            "approval_required": bool(decision.get("approval_required")),
            "estimated_usd": decision.get("estimated_usd"),
            "source_decision_id": decision.get("decision_id"),
            "notes": [str(decision.get("next_action") or "").strip()],
        }
        updated = upsert_task(updated, task)
    return refresh_log(updated)


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def emit_markdown(log: Mapping[str, Any]) -> str:
    refreshed = refresh_log(log)
    summary = refreshed.get("summary") or {}
    lines = [
        "# Generation Task Log",
        "",
        "- This ledger tracks async provider tasks only; it does not submit paid jobs.",
        f"- Tasks: {summary.get('tasks', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Ready: {summary.get('ready', 0)}",
        f"- Needs approval: {summary.get('needs_approval', 0)}",
        f"- Needs download: {summary.get('needs_download', 0)}",
        "",
        "| task | provider | shot | provider id | status | readiness | local asset | next action |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for task in refreshed.get("tasks") or []:
        path = _first_path(task)
        lines.append(
            "| {task} | {provider} | {shot} | {task_id} | {status} | {readiness} | {asset} | {action} |".format(
                task=str(task.get("task_key") or "").replace("|", "/"),
                provider=str(task.get("provider") or "").replace("|", "/"),
                shot=str(task.get("shot_id") or "-").replace("|", "/"),
                task_id=str(task.get("provider_task_id") or "-").replace("|", "/"),
                status=str(task.get("status") or "").replace("|", "/"),
                readiness=str(task.get("readiness") or "").replace("|", "/"),
                asset=("yes" if task.get("local_asset_exists") else (os.path.basename(path) if path else "-")),
                action=str(task.get("next_action") or "-").replace("|", "/"),
            )
        )

    command_lines: List[str] = []
    for task in refreshed.get("tasks") or []:
        if task.get("poll_command") or task.get("download_command"):
            command_lines.extend(["", f"## {task.get('task_key')}", ""])
            if task.get("poll_command"):
                command_lines.extend(["Poll:", "", "```bash", str(task["poll_command"]), "```"])
            if task.get("download_command"):
                command_lines.extend(["Download:", "", "```bash", str(task["download_command"]), "```"])
    if command_lines:
        lines.extend(command_lines)

    actions = refreshed.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    return "\n".join(lines).rstrip() + "\n"


def _load_raw_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("--raw-json must point to a JSON object")
    return data


def _task_from_args(args: argparse.Namespace, *, default_status: str) -> Dict[str, Any]:
    download_dir = args.download_dir or (
        str(Path(args.expected_path).expanduser().parent) if args.expected_path else "dreamina-output"
    )
    poll_command, download_command = _provider_commands(
        provider=args.provider,
        provider_task_id=args.task_id or "",
        asset_type=args.asset_type,
        download_dir=download_dir,
    )
    task = {
        "provider": args.provider,
        "task_key": args.task_key,
        "provider_task_id": args.task_id,
        "shot_id": args.shot_id,
        "asset_type": args.asset_type,
        "status": args.status or default_status,
        "expected_path": _abs_path(args.expected_path) if args.expected_path else "",
        "asset_path": _abs_path(args.asset_path) if args.asset_path else "",
        "result_url": args.result_url,
        "prompt": args.prompt,
        "approval_required": bool(args.approval_required),
        "poll_command": args.poll_command or poll_command,
        "download_command": args.download_command or download_command,
        "notes": args.note or [],
    }
    if args.raw_json:
        task.update(task_from_raw_json(_load_raw_json(args.raw_json)))
    return task


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track async media-generation task IDs and downloads.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, provider_required: bool) -> None:
        p.add_argument("--log", default="work/generation_tasks.json", help="Task log JSON path.")
        p.add_argument("--provider", required=provider_required, help="Provider id, e.g. dreamina or pixverse.")
        p.add_argument("--task-id", help="Provider submit_id/task_id/video_id/image_id.")
        p.add_argument("--task-key", help="Stable local key; defaults to provider:task-id or provider:shot-id.")
        p.add_argument("--shot-id", help="Storyboard shot id.")
        p.add_argument("--asset-type", default="video", choices=["video", "image", "audio", "other"])
        p.add_argument("--status", help="Task status, e.g. submitted, processing, completed, downloaded, failed.")
        p.add_argument("--expected-path", help="Where the generated asset should be saved.")
        p.add_argument("--asset-path", help="Actual local generated asset path.")
        p.add_argument("--result-url", help="Provider result/download URL.")
        p.add_argument("--prompt", help="Generation prompt or brief.")
        p.add_argument("--download-dir", help="Directory to use when generating a provider download command.")
        p.add_argument("--poll-command", help="Override generated poll command.")
        p.add_argument("--download-command", help="Override generated download command.")
        p.add_argument("--approval-required", action="store_true", help="Mark task as requiring paid-credit approval.")
        p.add_argument("--raw-json", help="Provider JSON result to import fields from.")
        p.add_argument("--note", action="append", default=[], help="Human note; can repeat.")
        p.add_argument("--markdown", help="Optional Markdown report path.")
        p.add_argument("--strict", action="store_true", help="Exit 2 when log has blocking tasks.")

    add_p = sub.add_parser("add", help="Add or merge a task record.")
    add_common(add_p, provider_required=True)

    update_p = sub.add_parser("update", help="Update an existing task record.")
    add_common(update_p, provider_required=False)

    import_p = sub.add_parser("import-provider-decision", help="Seed planned paid tasks from provider_decision.json.")
    import_p.add_argument("--provider-decision", required=True, help="Input provider_decision.json.")
    import_p.add_argument("--log", default="work/generation_tasks.json", help="Task log JSON path.")
    import_p.add_argument("--markdown", help="Optional Markdown report path.")
    import_p.add_argument("--strict", action="store_true", help="Exit 2 when log has blocking tasks.")

    report_p = sub.add_parser("report", help="Refresh summary and optionally write Markdown.")
    report_p.add_argument("--log", default="work/generation_tasks.json", help="Task log JSON path.")
    report_p.add_argument("--output", help="Optional normalized JSON output path; defaults to updating --log.")
    report_p.add_argument("--markdown", help="Optional Markdown report path.")
    report_p.add_argument("--strict", action="store_true", help="Exit 2 when log has blocking tasks.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.command == "import-provider-decision":
        log = load_log(args.log)
        decision = _load_raw_json(args.provider_decision)
        log = import_provider_decision(log, decision)
        write_json(args.log, log)
    elif args.command == "report":
        log = refresh_log(load_log(args.log))
        write_json(args.output or args.log, log)
    else:
        provider = getattr(args, "provider", None)
        if not provider and not (args.task_key or args.task_id or args.raw_json):
            raise SystemExit("update requires --provider, --task-key, --task-id, or --raw-json")
        if not provider:
            provider = "unknown"
            args.provider = provider
        default_status = "submitted" if getattr(args, "task_id", None) else "planned"
        if getattr(args, "approval_required", False) and not getattr(args, "task_id", None):
            default_status = "needs_approval"
        log = load_log(args.log)
        log = upsert_task(log, _task_from_args(args, default_status=default_status))
        write_json(args.log, log)

    if getattr(args, "markdown", None):
        write_text(args.markdown, emit_markdown(log))

    summary = log["summary"]
    print(
        "Generation task log: "
        f"tasks={summary['tasks']} blocking={summary['blocking']} "
        f"ready={summary['ready']} needs_download={summary['needs_download']}",
        file=sys.stderr,
    )
    if getattr(args, "strict", False) and summary["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
