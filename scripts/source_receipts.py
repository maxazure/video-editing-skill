#!/usr/bin/env python3
"""Build source receipts for proof-backed video claims.

This is a local review gate for factual/source-backed videos. It validates a
small claim manifest, checks local screenshots or receipt files, and emits JSON,
Markdown, and optional HTML source-deck artifacts. It does not browse, capture
screenshots, upload files, or call providers.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "source_receipts.v1"
URL_RE = re.compile(r"^https?://", re.I)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PRIMARY_SOURCE_TYPES = {
    "official",
    "primary",
    "first_party",
    "owned",
    "government",
    "academic",
    "paper",
    "company",
    "source",
}
HIGH_RISK_TYPES = {
    "high",
    "news",
    "data",
    "stat",
    "finance",
    "financial",
    "health",
    "medical",
    "legal",
    "regulatory",
    "claim",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_key(value: Any) -> str:
    return _norm(value).lower().replace("-", "_").replace(" ", "_")


def _first(raw: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _coerce_claims(data: Any) -> List[Mapping[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if not isinstance(data, Mapping):
        return []
    for key in ("claims", "items", "source_receipts", "receipts"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    if _first(data, ("text", "claim", "statement")):
        return [data]
    return []


def _parse_inline_claim(value: str) -> Mapping[str, Any]:
    text = value.strip()
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, Mapping):
            raise ValueError("--claim JSON must be an object")
        return parsed

    parts = [part.strip() for part in value.split("|")]
    if not parts or not parts[0]:
        raise ValueError("--claim needs at least claim text")
    claim: Dict[str, Any] = {"text": parts[0]}
    if len(parts) > 1 and parts[1]:
        claim["source_url"] = parts[1]
    if len(parts) > 2 and parts[2]:
        claim["source_file"] = parts[2]
    if len(parts) > 3 and parts[3]:
        claim["source_type"] = parts[3]
    if len(parts) > 4 and parts[4]:
        claim["risk"] = parts[4]
    if len(parts) > 5 and parts[5]:
        claim["source_title"] = parts[5]
    return claim


def load_claim_inputs(
    claim_paths: Sequence[str],
    inline_claims: Sequence[str],
    *,
    default_base: Path,
) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    for path_text in claim_paths:
        path = Path(path_text).expanduser().resolve()
        for claim in _coerce_claims(load_json(str(path))):
            item = dict(claim)
            item["_base_dir"] = str(path.parent)
            claims.append(item)

    for inline in inline_claims:
        item = dict(_parse_inline_claim(inline))
        item["_base_dir"] = str(default_base)
        claims.append(item)

    return claims


def _resolve_file(value: Any, base_dir: Path) -> Tuple[str, bool]:
    text = _norm(value)
    if not text:
        return "", False
    if URL_RE.match(text):
        return text, False
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve()), True


def _parse_seconds(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass

    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        nums = [float(part) for part in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds
    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


def _parse_timecode(raw: Mapping[str, Any]) -> Tuple[Optional[Dict[str, float]], List[str]]:
    source = raw.get("timecode")
    start: Optional[float] = None
    end: Optional[float] = None
    warnings: List[str] = []

    if isinstance(source, Mapping):
        start = _parse_seconds(source.get("start"))
        end = _parse_seconds(source.get("end"))
    elif isinstance(source, str) and "-" in source:
        left, right = source.split("-", 1)
        start = _parse_seconds(left)
        end = _parse_seconds(right)
    else:
        start = _parse_seconds(_first(raw, ("start", "start_seconds")))
        end = _parse_seconds(_first(raw, ("end", "end_seconds")))

    if start is None and end is None:
        return None, warnings
    if start is None or end is None or start < 0 or end <= start:
        warnings.append("invalid_timecode")
        return None, warnings
    return {"start": round(start, 3), "end": round(end, 3)}, warnings


def _source_label(item: Mapping[str, Any]) -> str:
    if item.get("source_title"):
        return str(item["source_title"])
    if item.get("source_url"):
        return str(item["source_url"])
    if item.get("source_file"):
        return os.path.basename(str(item["source_file"]))
    return "-"


def evaluate_claim(
    raw: Mapping[str, Any],
    index: int,
    *,
    default_base: Path,
    require_screenshot: bool = False,
    require_primary_source: bool = False,
) -> Dict[str, Any]:
    base_dir = Path(str(raw.get("_base_dir") or default_base)).expanduser().resolve()
    claim_id = _norm(_first(raw, ("id", "claim_id"))) or f"claim_{index:03d}"
    text = _norm(_first(raw, ("text", "claim", "statement", "narration")))
    source_url = _norm(_first(raw, ("source_url", "url", "receipt_url", "citation_url", "landing_url")))
    source_title = _norm(_first(raw, ("source_title", "title", "source_name", "publication")))
    source_type = _norm_key(_first(raw, ("source_type", "provider", "type", "source_kind")))
    risk = _norm_key(_first(raw, ("risk", "risk_level", "claim_type"))) or "normal"
    section = _norm(_first(raw, ("section", "shot_id", "segment_id", "cue_id")))
    note = _norm(_first(raw, ("note", "evidence_note", "usage_note", "reason")))
    source_file_raw = _first(
        raw,
        (
            "source_file",
            "screenshot",
            "screenshot_path",
            "receipt_file",
            "evidence_path",
            "image",
            "image_path",
        ),
    )
    source_file, source_file_is_local = _resolve_file(source_file_raw, base_dir)
    timecode, time_warnings = _parse_timecode(raw)

    issues: List[str] = []
    warnings: List[str] = list(time_warnings)

    if not text:
        issues.append("empty_claim_text")
    if not source_url and not source_file:
        issues.append("missing_source_receipt")
    if source_url and not URL_RE.match(source_url):
        issues.append("invalid_source_url")
    if source_file and source_file_is_local:
        path = Path(source_file)
        if not path.exists():
            issues.append("missing_source_file")
        elif not path.is_file():
            issues.append("source_file_not_file")
    elif require_screenshot:
        issues.append("missing_source_file")

    if risk in HIGH_RISK_TYPES and not source_url:
        issues.append("high_risk_missing_source_url")
    if require_primary_source and source_type not in PRIMARY_SOURCE_TYPES:
        issues.append("non_primary_source")

    if source_url and not source_title:
        warnings.append("source_title_missing")
    if source_url and not source_type:
        warnings.append("source_type_missing")

    status = "blocked" if issues else ("warn" if warnings else "ready")
    item: Dict[str, Any] = {
        "id": claim_id,
        "text": text,
        "section": section,
        "risk": risk,
        "source_type": source_type,
        "source_title": source_title,
        "source_url": source_url,
        "source_file": source_file,
        "source_file_exists": bool(source_file and source_file_is_local and Path(source_file).is_file()),
        "timecode": timecode,
        "note": note,
        "status": status,
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
    }
    return item


def _next_steps(claims: Sequence[Mapping[str, Any]]) -> List[str]:
    actions: List[str] = []
    for claim in claims:
        claim_id = claim.get("id")
        for issue in claim.get("issues") or []:
            if issue == "missing_source_receipt":
                actions.append(f"Add source_url or source_file for {claim_id}.")
            elif issue == "missing_source_file":
                actions.append(f"Capture or relink a local screenshot/receipt file for {claim_id}.")
            elif issue == "high_risk_missing_source_url":
                actions.append(f"Add a verifiable URL for high-risk claim {claim_id}.")
            elif issue == "invalid_source_url":
                actions.append(f"Replace invalid URL on {claim_id} with http(s) source_url.")
            elif issue == "non_primary_source":
                actions.append(f"Mark {claim_id} with a primary source_type or replace the source.")
            elif issue == "empty_claim_text":
                actions.append(f"Write claim text for {claim_id}.")
            else:
                actions.append(f"Resolve {issue} on {claim_id}.")
    if not claims:
        actions.append("Add at least one claim or skip source_receipts for opinion-only videos.")
    return list(dict.fromkeys(actions))


def build_receipts_manifest(
    claims: Sequence[Mapping[str, Any]],
    *,
    project_dir: str = ".",
    default_base: Optional[Path] = None,
    require_screenshot: bool = False,
    require_primary_source: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    base = default_base or root
    evaluated = [
        evaluate_claim(
            claim,
            index,
            default_base=base,
            require_screenshot=require_screenshot,
            require_primary_source=require_primary_source,
        )
        for index, claim in enumerate(claims, start=1)
    ]

    blocking = sum(1 for item in evaluated if item["status"] == "blocked")
    warnings = sum(len(item.get("warnings") or []) for item in evaluated)
    if not evaluated:
        blocking = 1

    status = "blocked" if blocking else ("warn" if warnings else "ready")
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "status": status,
        "summary": {
            "claims": len(evaluated),
            "ready": sum(1 for item in evaluated if item["status"] == "ready"),
            "warnings": warnings,
            "blocking": blocking,
            "high_risk_claims": sum(1 for item in evaluated if item["risk"] in HIGH_RISK_TYPES),
            "source_urls": sum(1 for item in evaluated if item.get("source_url")),
            "source_files": sum(1 for item in evaluated if item.get("source_file")),
        },
        "claims": evaluated,
        "next_steps": _next_steps(evaluated),
        "notes": [
            "This artifact validates provided receipts; it does not fetch pages or capture screenshots.",
            "Use local screenshot/source_file fields when the video needs visual proof cards.",
        ],
        "settings": {
            "require_screenshot": require_screenshot,
            "require_primary_source": require_primary_source,
        },
    }


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("\n", " ").replace("|", "\\|")


def emit_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), Mapping) else {}
    lines = [
        "# Source Receipts Review",
        "",
        f"- Status: **{str(manifest.get('status', '')).upper()}**",
        f"- Claims: {summary.get('claims', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Source URLs: {summary.get('source_urls', 0)}",
        f"- Source files: {summary.get('source_files', 0)}",
        "",
        "## Claims",
        "",
        "| id | status | risk | section | claim | source | file | issues |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for claim in manifest.get("claims") or []:
        source = claim.get("source_url") or _source_label(claim)
        file_label = os.path.basename(str(claim.get("source_file") or "")) or "-"
        issues = ", ".join(claim.get("issues") or claim.get("warnings") or []) or "-"
        lines.append(
            "| {id} | {status} | {risk} | {section} | {text} | {source} | `{file}` | {issues} |".format(
                id=_md(claim.get("id")),
                status=_md(claim.get("status")),
                risk=_md(claim.get("risk")),
                section=_md(claim.get("section") or "-"),
                text=_md(claim.get("text")),
                source=_md(source or "-"),
                file=_md(file_label),
                issues=_md(issues),
            )
        )

    actions = manifest.get("next_steps") or []
    if actions:
        lines.extend(["", "## Next Steps", ""])
        lines.extend(f"- {action}" for action in actions)

    return "\n".join(lines).rstrip() + "\n"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _file_href(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().as_uri()
    except (OSError, ValueError):
        return ""


def emit_html(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), Mapping) else {}
    cards: List[str] = []
    for claim in manifest.get("claims") or []:
        source_url = str(claim.get("source_url") or "")
        source_file = str(claim.get("source_file") or "")
        file_href = _file_href(source_file)
        file_block = ""
        if file_href and Path(source_file).suffix.lower() in IMAGE_EXTS:
            file_block = f'<a href="{_esc(file_href)}"><img src="{_esc(file_href)}" alt=""></a>'
        elif file_href:
            file_block = f'<a class="file" href="{_esc(file_href)}">{_esc(os.path.basename(source_file))}</a>'
        else:
            file_block = '<span class="muted">No local receipt file</span>'

        source_link = (
            f'<a href="{_esc(source_url)}">{_esc(claim.get("source_title") or source_url)}</a>'
            if source_url
            else '<span class="muted">No source URL</span>'
        )
        issues = ", ".join(claim.get("issues") or claim.get("warnings") or []) or "clear"
        cards.append(
            "<article class=\"card\">"
            f"<div class=\"status { _esc(claim.get('status')) }\">{_esc(claim.get('status'))}</div>"
            f"<h2>{_esc(claim.get('id'))}</h2>"
            f"<p class=\"claim\">{_esc(claim.get('text'))}</p>"
            f"<p><strong>Risk:</strong> {_esc(claim.get('risk'))} "
            f"<strong>Section:</strong> {_esc(claim.get('section') or '-')}</p>"
            f"<p><strong>Source:</strong> {source_link}</p>"
            f"<div class=\"receipt\">{file_block}</div>"
            f"<p class=\"issues\"><strong>Review:</strong> {_esc(issues)}</p>"
            "</article>"
        )

    if not cards:
        cards.append("<article class=\"card\"><p>No claims found.</p></article>")

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Source Receipts</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f8; color: #1f2933; }
    header { padding: 28px 32px 18px; background: #111827; color: white; }
    header h1 { margin: 0 0 8px; font-size: 28px; }
    .metrics { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    .metric { background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.16); padding: 8px 10px; border-radius: 8px; }
    main { padding: 24px 32px 40px; display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
    .card { background: white; border: 1px solid #d7dde4; border-radius: 8px; padding: 16px; box-shadow: 0 1px 2px rgba(15,23,42,0.06); }
    h2 { margin: 0 0 8px; font-size: 18px; }
    .claim { font-size: 17px; line-height: 1.45; }
    .status { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .ready { background: #dcfce7; color: #166534; }
    .warn { background: #fef3c7; color: #92400e; }
    .blocked { background: #fee2e2; color: #991b1b; }
    .receipt { margin-top: 12px; min-height: 42px; }
    img { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; }
    .file { display: inline-block; padding: 8px 10px; border: 1px solid #d7dde4; border-radius: 6px; }
    .muted { color: #6b7280; }
    .issues { color: #374151; }
    a { color: #0f5cc0; }
  </style>
</head>
<body>
  <header>
    <h1>Source Receipts</h1>
    <div>Status: <strong>""" + _esc(str(manifest.get("status", "")).upper()) + """</strong></div>
    <div class="metrics">
      <div class="metric">Claims: """ + _esc(summary.get("claims", 0)) + """</div>
      <div class="metric">Blocking: """ + _esc(summary.get("blocking", 0)) + """</div>
      <div class="metric">Warnings: """ + _esc(summary.get("warnings", 0)) + """</div>
      <div class="metric">URLs: """ + _esc(summary.get("source_urls", 0)) + """</div>
      <div class="metric">Files: """ + _esc(summary.get("source_files", 0)) + """</div>
    </div>
  </header>
  <main>
""" + "\n".join(cards) + """
  </main>
</body>
</html>
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a source receipts proof deck for video claims.")
    parser.add_argument("--claims", action="append", default=[], help="Claim JSON file; can repeat.")
    parser.add_argument(
        "--claim",
        action="append",
        default=[],
        help="Inline claim as JSON object or 'text|source_url|source_file|source_type|risk|source_title'.",
    )
    parser.add_argument("--project-dir", default=".", help="Project/work folder for manifest metadata.")
    parser.add_argument("--base-dir", help="Base path for inline claim files (default: project dir).")
    parser.add_argument("--output", default="source_receipts.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--html", help="Optional static HTML source deck path.")
    parser.add_argument("--require-screenshot", action="store_true", help="Require every claim to have a local receipt file.")
    parser.add_argument(
        "--require-primary-source",
        action="store_true",
        help="Require source_type to be official/primary/owned/government/academic/etc.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking > 0.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    project_dir = Path(args.project_dir).expanduser().resolve()
    base_dir = Path(args.base_dir).expanduser().resolve() if args.base_dir else project_dir
    try:
        claims = load_claim_inputs(args.claims, args.claim, default_base=base_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"source_receipts: {exc}", file=sys.stderr)
        return 1

    manifest = build_receipts_manifest(
        claims,
        project_dir=str(project_dir),
        default_base=base_dir,
        require_screenshot=args.require_screenshot,
        require_primary_source=args.require_primary_source,
    )
    write_json(args.output, manifest)
    if args.markdown:
        write_text(args.markdown, emit_markdown(manifest))
    if args.html:
        write_text(args.html, emit_html(manifest))

    print(f"source_receipts: {manifest['status']} ({manifest['summary']['blocking']} blocking)")
    if args.strict and int(manifest["summary"]["blocking"] or 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
