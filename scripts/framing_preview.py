#!/usr/bin/env python3
"""Render and verify source-bound framing choices for platform exports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from generated_clip_review import probe_media
from multi_export import PRESETS
from utils import get_video_info


VERSION = "framing_preview.v1"
STRATEGIES = ("cover", "contain", "blur")
STRATEGY_LABELS = {
    "cover": "Fill canvas / center crop",
    "contain": "Preserve full frame / neutral bars",
    "blur": "Preserve full frame / blurred background",
    "native": "Source and target aspect already match",
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


def canonical_report_id(report: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {key: value for key, value in report.items() if key not in {"generated_at", "report_id"}}
    )


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
    path = _lexical_project_path(raw_path, root=root, label=label).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    return path


def _project_output(raw_path: str, *, root: Path, label: str) -> Path:
    return _lexical_project_path(raw_path, root=root, label=label).resolve()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _ensure_distinct_paths(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (label, path) in enumerate(items):
        for previous_label, previous in items[:index]:
            if path.resolve() == previous.resolve():
                raise ValueError(f"{label} must not overwrite {previous_label}: {path}")
            if path.exists() and previous.exists() and os.path.samefile(path, previous):
                raise ValueError(f"{label} must not hard-link to {previous_label}: {path}")


def _fingerprint(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


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


def select_sample_times(duration: float, requested: Sequence[float] = ()) -> List[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source duration must be positive")
    raw = list(requested) if requested else [duration * 0.15, duration * 0.50, duration * 0.85]
    if not raw or len(raw) > 6:
        raise ValueError("supply between one and six sample times")
    limit = max(0.0, duration - min(0.04, duration / 2))
    times = [round(min(float(value), limit), 3) for value in raw]
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in raw):
        raise ValueError("sample times must be finite and non-negative")
    if len(times) != len(set(times)):
        raise ValueError("sample times collapse to duplicate source frames")
    return times


def aspect_matches(src_w: int, src_h: int, dst_w: int, dst_h: int) -> bool:
    if min(src_w, src_h, dst_w, dst_h) <= 0:
        raise ValueError("source and target dimensions must be positive")
    return abs((src_w / src_h) - (dst_w / dst_h)) < 1e-3


def build_filter_complex(strategy: str, width: int, height: int) -> str:
    """Return a one-input FFmpeg graph with a single ``[vout]`` output."""
    if strategy == "native":
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vout]"
        )
    if strategy == "cover":
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1[vout]"
        )
    if strategy == "contain":
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111111,setsar=1[vout]"
        )
    if strategy == "blur":
        return (
            f"[0:v]split=2[bg0][fg0];"
            f"[bg0]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=30[bg];"
            f"[fg0]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]"
        )
    raise ValueError(f"unknown framing strategy: {strategy}")


def _run(command: Sequence[str]) -> None:
    result = subprocess.run(list(command), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "FFmpeg failed").strip()
        raise ValueError(detail.splitlines()[-1])


def _render_variant(
    source: Path,
    destination: Path,
    *,
    strategy: str,
    width: int,
    height: int,
    times: Sequence[float],
) -> Dict[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    thumb_width = min(360, width)
    thumb_height = max(2, round(height * thumb_width / width / 2) * 2)
    with tempfile.TemporaryDirectory(prefix="framing-preview-", dir=str(destination.parent)) as temp_name:
        temp = Path(temp_name)
        frames: List[Path] = []
        graph = build_filter_complex(strategy, width, height)
        for index, time_s in enumerate(times):
            frame = temp / f"frame-{index:02d}.png"
            _run([
                "ffmpeg", "-v", "error", "-ss", f"{time_s:.3f}", "-i", str(source),
                "-filter_complex", graph, "-map", "[vout]", "-frames:v", "1", "-y", str(frame),
            ])
            frames.append(frame)

        command: List[str] = ["ffmpeg", "-v", "error"]
        for frame in frames:
            command.extend(["-i", str(frame)])
        scaled = "".join(
            f"[{index}:v]scale={thumb_width}:{thumb_height}[v{index}];"
            for index in range(len(frames))
        )
        if len(frames) == 1:
            stack = "[v0]null[out]"
        else:
            inputs = "".join(f"[v{index}]" for index in range(len(frames)))
            stack = f"{inputs}hstack=inputs={len(frames)}[out]"
        command.extend([
            "-filter_complex", scaled + stack,
            "-map", "[out]", "-frames:v", "1", "-q:v", "2", "-y", str(destination),
        ])
        _run(command)
    return {"width": thumb_width * len(times), "height": thumb_height, "sample_frames": len(times)}


def _derive_state(report: Mapping[str, Any]) -> Dict[str, Any]:
    platforms = report.get("platforms") if isinstance(report.get("platforms"), list) else []
    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    require_selection = bool(settings.get("require_selection"))
    blockers: List[str] = []
    warnings: List[str] = []
    selected = 0
    for raw in platforms:
        if not isinstance(raw, Mapping):
            blockers.append("platform entries must be objects")
            continue
        name = str(raw.get("platform") or "unknown")
        variants = raw.get("variants") if isinstance(raw.get("variants"), list) else []
        strategies = [str(item.get("strategy") or "") for item in variants if isinstance(item, Mapping)]
        choice = str(raw.get("selected_strategy") or "")
        if not variants:
            blockers.append(f"{name} has no framing variants")
        if len(strategies) != len(set(strategies)):
            blockers.append(f"{name} has duplicate framing variants")
        if choice and choice not in strategies:
            blockers.append(f"{name} selected strategy was not rendered: {choice}")
        if choice:
            selected += 1
        elif require_selection:
            blockers.append(f"{name} framing selection is required before export")
        else:
            warnings.append(f"{name} has no framing selection")
    if not platforms:
        blockers.append("no platform framing previews were rendered")
    summary = {
        "platforms": len(platforms),
        "selected": selected,
        "blocking": len(blockers),
        "warnings": len(warnings),
    }
    return {
        "status": "blocked" if blockers else ("ready" if selected == len(platforms) else "needs_review"),
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary,
    }


def create_report(
    source_path: str,
    *,
    project_dir: str,
    preview_dir: str,
    platforms: Sequence[str],
    times: Sequence[float] = (),
    require_selection: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    source = _project_file(source_path, root=root, label="source video")
    destination_dir = _project_output(preview_dir, root=root, label="preview directory")
    if source == destination_dir or destination_dir in source.parents:
        raise ValueError("preview directory must not collide with the source video")
    names = [str(name).strip().lower() for name in platforms]
    if not names or len(names) != len(set(names)):
        raise ValueError("platforms must be a non-empty unique list")
    unknown = [name for name in names if name not in PRESETS]
    if unknown:
        raise ValueError(f"unknown platform(s): {', '.join(unknown)}")

    media = probe_media(str(source))
    sample_times = select_sample_times(float(media["duration"]), times)
    _, src_w, src_h, _, rotation = get_video_info(str(source))
    destination_dir.mkdir(parents=True, exist_ok=True)
    platform_records: List[Dict[str, Any]] = []
    final_paths: List[Path] = []
    for name in names:
        preset = PRESETS[name]
        strategies = ("native",) if aspect_matches(src_w, src_h, preset.width, preset.height) else STRATEGIES
        final_paths.extend(destination_dir / f"{name}-{strategy}.jpg" for strategy in strategies)
    if not force:
        existing = next((path for path in final_paths if path.exists()), None)
        if existing is not None:
            raise ValueError(f"refusing to overwrite existing preview without --force: {existing}")

    with tempfile.TemporaryDirectory(prefix="framing-preview-set-", dir=str(destination_dir.parent)) as temp_name:
        temp = Path(temp_name)
        staged: List[tuple[Path, Path]] = []
        for name in names:
            preset = PRESETS[name]
            matches = aspect_matches(src_w, src_h, preset.width, preset.height)
            strategies = ("native",) if matches else STRATEGIES
            variants: List[Dict[str, Any]] = []
            for strategy in strategies:
                final_path = destination_dir / f"{name}-{strategy}.jpg"
                staged_path = temp / final_path.name
                geometry = _render_variant(
                    source,
                    staged_path,
                    strategy=strategy,
                    width=preset.width,
                    height=preset.height,
                    times=sample_times,
                )
                graph = build_filter_complex(strategy, preset.width, preset.height)
                variants.append({
                    "strategy": strategy,
                    "label": STRATEGY_LABELS[strategy],
                    "filter_sha256": hashlib.sha256(graph.encode("utf-8")).hexdigest(),
                    "preview": {**_fingerprint(staged_path, root=temp), "path": _relative(final_path, root), **geometry},
                })
                staged.append((staged_path, final_path))
            platform_records.append({
                "platform": name,
                "width": preset.width,
                "height": preset.height,
                "aspect_mismatch": not matches,
                "variants": variants,
                "selected_strategy": "native" if matches else "",
            })
        for staged_path, final_path in staged:
            os.replace(staged_path, final_path)

    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": {
            **_fingerprint(source, root=root),
            "media": _media_signature(media),
            "display_width": int(src_w),
            "display_height": int(src_h),
            "rotation": int(rotation),
        },
        "settings": {"sample_times": sample_times, "require_selection": bool(require_selection)},
        "platforms": platform_records,
    }
    report.update(_derive_state(report))
    report["report_id"] = canonical_report_id(report)
    return report


def verify_report(report: Mapping[str, Any], project_dir: Optional[str] = None) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if report.get("version") != VERSION:
        blockers.append(f"unsupported framing preview version: {report.get('version')!r}")
    raw_root = report.get("project_dir")
    if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
        blockers.append("project_dir must be a non-empty absolute path")
    root = Path(str(raw_root or ".")).expanduser().resolve()
    if project_dir is not None:
        expected = Path(project_dir).expanduser().resolve()
        if root != expected:
            blockers.append("report project_dir does not match the verification project")
        root = expected

    source_record = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    source: Optional[Path] = None
    try:
        source = _project_file(str(source_record.get("path") or ""), root=root, label="source video")
        current = _fingerprint(source, root=root)
        if current != {key: source_record.get(key) for key in ("path", "sha256", "size_bytes")}:
            blockers.append("source video bytes changed after framing previews were rendered")
        if _media_signature(probe_media(str(source))) != source_record.get("media"):
            blockers.append("source video media contract changed after framing previews were rendered")
        _, display_width, display_height, _, rotation = get_video_info(str(source))
        if (
            source_record.get("display_width") != int(display_width)
            or source_record.get("display_height") != int(display_height)
            or source_record.get("rotation") != int(rotation)
        ):
            blockers.append("source video display orientation changed after framing previews were rendered")
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(f"source video verification failed: {exc}")

    settings = report.get("settings") if isinstance(report.get("settings"), Mapping) else {}
    sample_times = settings.get("sample_times") if isinstance(settings.get("sample_times"), list) else []
    if source is not None:
        try:
            if select_sample_times(float(source_record.get("media", {}).get("duration") or 0), sample_times) != sample_times:
                blockers.append("sample times are not canonical for the bound source duration")
        except (AttributeError, TypeError, ValueError) as exc:
            blockers.append(f"sample time verification failed: {exc}")

    platforms = report.get("platforms") if isinstance(report.get("platforms"), list) else []
    seen: set[str] = set()
    for index, raw in enumerate(platforms):
        if not isinstance(raw, Mapping):
            blockers.append(f"platform #{index + 1} must be an object")
            continue
        name = str(raw.get("platform") or "")
        if name in seen:
            blockers.append(f"duplicate platform framing entry: {name}")
        seen.add(name)
        if name not in PRESETS:
            blockers.append(f"unknown platform framing entry: {name}")
            continue
        preset = PRESETS[name]
        if raw.get("width") != preset.width or raw.get("height") != preset.height:
            blockers.append(f"{name} target canvas changed after preview rendering")
        try:
            matches = aspect_matches(
                int(source_record.get("display_width") or 0), int(source_record.get("display_height") or 0),
                preset.width, preset.height,
            )
        except (TypeError, ValueError) as exc:
            blockers.append(f"{name} source display dimensions are invalid: {exc}")
            continue
        if raw.get("aspect_mismatch") is not (not matches):
            blockers.append(f"{name} aspect_mismatch does not match source and target canvases")
        expected_strategies = ["native"] if matches else list(STRATEGIES)
        variants = raw.get("variants") if isinstance(raw.get("variants"), list) else []
        actual_strategies: List[str] = []
        for variant_index, variant in enumerate(variants):
            if not isinstance(variant, Mapping):
                blockers.append(f"{name} variant #{variant_index + 1} must be an object")
                continue
            strategy = str(variant.get("strategy") or "")
            actual_strategies.append(strategy)
            try:
                graph = build_filter_complex(strategy, preset.width, preset.height)
            except ValueError as exc:
                blockers.append(f"{name} variant is invalid: {exc}")
                continue
            if variant.get("filter_sha256") != hashlib.sha256(graph.encode("utf-8")).hexdigest():
                blockers.append(f"{name}/{strategy} framing filter contract changed")
            preview = variant.get("preview") if isinstance(variant.get("preview"), Mapping) else {}
            try:
                preview_path = _project_file(str(preview.get("path") or ""), root=root, label=f"{name}/{strategy} preview")
                current = _fingerprint(preview_path, root=root)
                if current != {key: preview.get(key) for key in ("path", "sha256", "size_bytes")}:
                    blockers.append(f"{name}/{strategy} preview bytes changed")
            except (OSError, TypeError, ValueError) as exc:
                blockers.append(f"{name}/{strategy or variant_index + 1} preview verification failed: {exc}")
        if actual_strategies != expected_strategies:
            blockers.append(f"{name} variants do not match the canonical strategy set")

    derived = _derive_state(report)
    for key in ("blockers", "warnings", "summary", "status"):
        if report.get(key) != derived[key]:
            blockers.append(f"stored {key} does not match derived framing preview state")
    if report.get("report_id") != canonical_report_id(report):
        blockers.append("framing preview report_id does not match canonical report content")
    combined_blockers = list(dict.fromkeys(blockers + list(derived["blockers"])))
    combined_warnings = list(dict.fromkeys(warnings + list(derived["warnings"])))
    return {
        "status": "blocked" if combined_blockers else derived["status"],
        "blockers": combined_blockers,
        "warnings": combined_warnings,
        "summary": {"blocking": len(combined_blockers), "warnings": len(combined_warnings)},
    }


def select_strategy(report: Mapping[str, Any], platform: str, strategy: str) -> Dict[str, Any]:
    verification = verify_report(report)
    integrity_blockers = [
        item for item in verification["blockers"]
        if not item.endswith("framing selection is required before export")
    ]
    if integrity_blockers:
        raise ValueError("cannot select from an invalid framing preview report: " + integrity_blockers[0])
    records = report.get("platforms") if isinstance(report.get("platforms"), list) else []
    match = next((item for item in records if isinstance(item, Mapping) and item.get("platform") == platform), None)
    if match is None:
        raise ValueError(f"platform was not previewed: {platform}")
    available = [str(item.get("strategy") or "") for item in match.get("variants") or [] if isinstance(item, Mapping)]
    if strategy not in available:
        raise ValueError(f"{strategy} was not rendered for {platform}")
    updated = dict(report)
    updated_records = [dict(item) if isinstance(item, Mapping) else item for item in records]
    target = next(item for item in updated_records if isinstance(item, dict) and item.get("platform") == platform)
    target["selected_strategy"] = strategy
    updated["platforms"] = updated_records
    updated["generated_at"] = utc_now()
    updated.update(_derive_state(updated))
    updated["report_id"] = canonical_report_id(updated)
    return updated


def emit_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("source") or {}
    lines = [
        "# Platform Framing Preview",
        "",
        f"- Status: **{report.get('status', 'unknown')}**",
        f"- Source: `{source.get('path', '')}`",
        f"- Source canvas: {source.get('display_width')}×{source.get('display_height')}",
        f"- Sample times: {', '.join(f'{float(value):.3f}s' for value in (report.get('settings') or {}).get('sample_times') or [])}",
        "",
        "| Platform | Target | Strategy | Review JPEG | Selected |",
        "|---|---:|---|---|:---:|",
    ]
    for item in report.get("platforms") or []:
        selected = item.get("selected_strategy")
        for variant in item.get("variants") or []:
            strategy = variant.get("strategy")
            preview = variant.get("preview") or {}
            lines.append(
                f"| `{item.get('platform')}` | {item.get('width')}×{item.get('height')} | "
                f"`{strategy}` — {variant.get('label')} | `{preview.get('path')}` | "
                f"{'yes' if strategy == selected else ''} |"
            )
    lines.extend([
        "",
        "Review every JPEG at phone size and full size. Cover must not remove faces, hands, readable UI, "
        "logos, product edges, document boundaries, or multiple subjects. Prefer contain or blur when those "
        "protected details would be cropped.",
        "",
        "Record each non-native choice with:",
        "",
        "```bash",
        "python3 scripts/framing_preview.py select --report work/framing_preview.json --platform <platform> --strategy <cover|contain|blur>",
        "```",
        "",
        "Then export with `multi_export.py --framing-preview work/framing_preview.json`.",
    ])
    if report.get("blockers"):
        lines.extend(["", "## Blockers", "", *[f"- {item}" for item in report["blockers"]]])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in report["warnings"]]])
    return "\n".join(lines).rstrip() + "\n"


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("framing preview report must be a JSON object")
    return payload


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _write_json(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", force=force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render and verify source-bound platform framing previews")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Render cover/contain/blur framing variants")
    create.add_argument("--project-dir", default=".")
    create.add_argument("--video", required=True)
    create.add_argument("--preview-dir", default="verify/framing")
    create.add_argument("--platforms", nargs="+", choices=sorted(PRESETS), default=list(PRESETS))
    create.add_argument("--time", action="append", type=float, default=[])
    create.add_argument("--require-selection", action="store_true")
    create.add_argument("--output", required=True)
    create.add_argument("--markdown")
    create.add_argument("--force", action="store_true")
    create.add_argument("--strict", action="store_true")
    select = subparsers.add_parser("select", help="Record one reviewed platform strategy")
    select.add_argument("--report", required=True)
    select.add_argument("--platform", required=True, choices=sorted(PRESETS))
    select.add_argument("--strategy", required=True, choices=STRATEGIES)
    select.add_argument("--markdown")
    verify = subparsers.add_parser("verify", help="Verify source, preview, filter, and selection bindings")
    verify.add_argument("--report", required=True)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            root = Path(args.project_dir).expanduser().resolve()
            output = _project_output(args.output, root=root, label="report output")
            markdown = _project_output(args.markdown, root=root, label="Markdown output") if args.markdown else None
            source = _project_file(args.video, root=root, label="source video")
            preview_dir = _project_output(args.preview_dir, root=root, label="preview directory")
            _, source_width, source_height, _, _ = get_video_info(str(source))
            protected = {"source video": source, "report output": output}
            if markdown is not None:
                protected["Markdown output"] = markdown
            for name in args.platforms:
                preset = PRESETS[name]
                strategies = (
                    ("native",)
                    if aspect_matches(
                        int(source_width),
                        int(source_height),
                        preset.width,
                        preset.height,
                    )
                    else STRATEGIES
                )
                for strategy in strategies:
                    protected[f"{name}/{strategy} preview"] = preview_dir / f"{name}-{strategy}.jpg"
            _ensure_distinct_paths(protected)
            report = create_report(
                args.video,
                project_dir=str(root),
                preview_dir=args.preview_dir,
                platforms=args.platforms,
                times=args.time,
                require_selection=args.require_selection,
                force=args.force,
            )
            _write_json(output, report, force=args.force)
            if markdown is not None:
                _write_text(markdown, emit_markdown(report), force=args.force)
            print(json.dumps({"status": report["status"], "report": str(output), "summary": report["summary"]}, ensure_ascii=False))
            return 2 if args.strict and report["summary"]["blocking"] else 0

        report_path = Path(args.report).expanduser().resolve()
        report = _load_json(report_path)
        if args.command == "select":
            raw_root = report.get("project_dir")
            if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
                raise ValueError("report project_dir must be a non-empty absolute path")
            root = Path(raw_root).expanduser().resolve()
            report_path = _project_file(str(report_path), root=root, label="framing preview report")
            markdown_path = _project_output(
                args.markdown or str(report_path.with_suffix(".md")), root=root, label="Markdown output"
            )
            updated = select_strategy(report, args.platform, args.strategy)
            _write_json(report_path, updated, force=True)
            _write_text(markdown_path, emit_markdown(updated), force=True)
            print(json.dumps({"status": updated["status"], "report": str(report_path), "summary": updated["summary"]}, ensure_ascii=False))
            return 0

        verification = verify_report(report)
        print(json.dumps(verification, ensure_ascii=False))
        return 2 if args.strict and verification["summary"]["blocking"] else 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"framing preview error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
