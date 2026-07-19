#!/usr/bin/env python3
"""Preflight image/video references before paid video generation.

The script is local-only. It checks first-frame and shared style references for
existence, decodability, dimensions, orientation, target-aspect compatibility,
resolution, and transparent backgrounds. It never submits a generation job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
GENERATED_VIDEO_PROVIDERS = {"dreamina_seedance", "veo", "ltx", "wan", "sora"}


def load_prompt_pack(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("video prompt pack must be a JSON object")
    return data


def parse_aspect(value: str) -> Tuple[float, str]:
    raw = str(value or "").strip().lower()
    if ":" in raw:
        left, right = raw.split(":", 1)
    elif "x" in raw:
        left, right = raw.split("x", 1)
    else:
        raise ValueError(f"invalid aspect ratio: {value!r}; expected W:H")
    width = float(left)
    height = float(right)
    if width <= 0 or height <= 0:
        raise ValueError("aspect dimensions must be positive")
    ratio = width / height
    return ratio, orientation_for_ratio(ratio)


def orientation_for_ratio(ratio: float, *, square_tolerance: float = 0.04) -> str:
    if abs(ratio - 1.0) <= square_tolerance:
        return "square"
    return "landscape" if ratio > 1.0 else "portrait"


def _path_from_reference(value: Any, *, base_dir: Path) -> str:
    raw = ""
    if isinstance(value, Mapping):
        raw = str(
            value.get("resolved_path")
            or value.get("path")
            or value.get("expected_path")
            or ""
        ).strip()
    elif value:
        raw = str(value).strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _inspect_with_pillow(path: Path) -> Optional[Dict[str, Any]]:
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(path) as image:
            width, height = image.size
            bands = image.getbands()
            has_alpha = "A" in bands or "transparency" in image.info
            transparent = False
            if has_alpha:
                rgba = image.convert("RGBA")
                alpha_min, alpha_max = rgba.getchannel("A").getextrema()
                transparent = alpha_min < 255
            return {
                "kind": "image",
                "format": str(image.format or path.suffix.lstrip(".")).lower(),
                "width": int(width),
                "height": int(height),
                "pixel_format": str(image.mode),
                "has_alpha": bool(has_alpha),
                "transparent_background": bool(transparent),
                "duration_seconds": None,
            }
    except Exception:
        return None


def _inspect_with_ffprobe(path: Path) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,codec_type,width,height,pix_fmt:format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(detail.splitlines()[-1])
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError("no decodable video/image stream")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("reference has invalid dimensions")
    pixel_format = str(stream.get("pix_fmt") or "")
    duration_raw = (payload.get("format") or {}).get("duration")
    try:
        duration = round(float(duration_raw), 3) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    has_alpha = "rgba" in pixel_format or "bgra" in pixel_format or "yuva" in pixel_format
    transparent = _has_transparent_pixels(path) if has_alpha else False
    return {
        "kind": "image" if path.suffix.lower() in IMAGE_EXTS else "video",
        "format": str(stream.get("codec_name") or path.suffix.lstrip(".")).lower(),
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "has_alpha": has_alpha,
        "transparent_background": transparent,
        "duration_seconds": duration,
    }


def _has_transparent_pixels(path: Path) -> bool:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        "alphaextract",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout:
        return False
    return min(result.stdout) < 255


def inspect_reference(path: str) -> Dict[str, Any]:
    reference = Path(path)
    if reference.suffix.lower() in IMAGE_EXTS:
        metadata = _inspect_with_pillow(reference)
        if metadata is not None:
            return metadata
    return _inspect_with_ffprobe(reference)


def _correction_for_orientation(actual: str, target: str, aspect: str) -> str:
    if actual == "portrait" and target == "landscape":
        return (
            f"Reframe the subject for {aspect}; extend the scene horizontally and do not add "
            "pillarboxing or black bars."
        )
    if actual == "landscape" and target == "portrait":
        return (
            f"Reframe the subject for {aspect}; crop around the primary subject or extend the scene "
            "vertically and do not add letterboxing."
        )
    return f"Create or crop a new reference in {aspect} before submitting the generation job."


def evaluate_reference(
    *,
    reference_id: str,
    role: str,
    path: str,
    required: bool,
    target_aspect: str,
    target_ratio: float,
    target_orientation: str,
    aspect_tolerance: float,
    min_short_edge: int,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    corrections: List[str] = []
    media: Dict[str, Any] = {}

    if not path:
        message = f"{reference_id}: no {role} path was supplied"
        (blockers if required else warnings).append(message)
    elif not Path(path).exists():
        message = f"{reference_id}: reference file does not exist: {path}"
        (blockers if required else warnings).append(message)
    elif not Path(path).is_file():
        blockers.append(f"{reference_id}: reference path is not a file: {path}")
    else:
        try:
            media = inspect_reference(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(f"{reference_id}: reference is not decodable: {exc}")

    if media:
        width = int(media["width"])
        height = int(media["height"])
        ratio = width / height
        actual_orientation = orientation_for_ratio(ratio)
        aspect_error = abs(ratio - target_ratio) / target_ratio
        media.update({
            "aspect_ratio": round(ratio, 4),
            "aspect_error": round(aspect_error, 4),
            "orientation": actual_orientation,
            "short_edge": min(width, height),
        })

        if actual_orientation != target_orientation:
            blockers.append(
                f"{reference_id}: {actual_orientation} reference conflicts with "
                f"{target_aspect} {target_orientation} output"
            )
            corrections.append(
                _correction_for_orientation(actual_orientation, target_orientation, target_aspect)
            )
        elif aspect_error > aspect_tolerance:
            blockers.append(
                f"{reference_id}: aspect {width}:{height} differs from {target_aspect} "
                f"by {aspect_error:.1%} (tolerance {aspect_tolerance:.1%})"
            )
            corrections.append(
                f"Crop, outpaint, or regenerate this reference at {target_aspect}; keep the focal "
                "subject inside the final crop."
            )

        if min(width, height) < min_short_edge:
            warnings.append(
                f"{reference_id}: short edge {min(width, height)}px is below "
                f"{min_short_edge}px"
            )
            corrections.append(
                f"Use a higher-resolution reference with a short edge of at least {min_short_edge}px."
            )

        if media.get("transparent_background"):
            warnings.append(f"{reference_id}: reference contains transparent pixels")
            corrections.append(
                "Composite the subject onto an intentional background or add an explicit provider "
                "background instruction; do not allow checkerboard/black fill."
            )

    status = "blocked" if blockers else ("warn" if warnings else "ready")
    return {
        "id": reference_id,
        "role": role,
        "required": required,
        "path": path,
        "status": status,
        "media": media,
        "blockers": blockers,
        "warnings": warnings,
        "corrections": list(dict.fromkeys(corrections)),
    }


def _parse_overrides(values: Sequence[str], *, base_dir: Path) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --reference {value!r}; expected shot_id=/path/to/file")
        reference_id, raw_path = value.split("=", 1)
        reference_id = reference_id.strip()
        if not reference_id or not raw_path.strip():
            raise ValueError(f"invalid --reference {value!r}; both id and path are required")
        overrides[reference_id] = _path_from_reference(raw_path.strip(), base_dir=base_dir)
    return overrides


def build_preflight(
    pack: Mapping[str, Any],
    *,
    prompt_pack_path: str,
    style_reference: Optional[str] = None,
    reference_overrides: Optional[Mapping[str, str]] = None,
    target_aspect: Optional[str] = None,
    require_style_reference: bool = False,
    aspect_tolerance: float = 0.20,
    min_short_edge: int = 512,
) -> Dict[str, Any]:
    pack_path = Path(prompt_pack_path).expanduser().resolve()
    base_dir = pack_path.parent
    global_data = pack.get("global") if isinstance(pack.get("global"), Mapping) else {}
    aspect = str(target_aspect or global_data.get("aspect") or "9:16")
    target_ratio, target_orientation = parse_aspect(aspect)
    overrides = dict(reference_overrides or {})

    references: List[Dict[str, Any]] = []
    generated_item_ids = [
        str(item.get("shot_id") or "")
        for item in (pack.get("items") or [])
        if isinstance(item, Mapping) and str(item.get("provider") or "") in GENERATED_VIDEO_PROVIDERS
    ]

    style_value: Any = style_reference if style_reference is not None else global_data.get("style_reference")
    style_path = _path_from_reference(style_value, base_dir=base_dir)
    style_lock_status = "not_requested"
    if style_path or require_style_reference:
        style_result = evaluate_reference(
            reference_id="global_style_reference",
            role="style_reference",
            path=style_path,
            required=bool(style_path) or require_style_reference,
            target_aspect=aspect,
            target_ratio=target_ratio,
            target_orientation=target_orientation,
            aspect_tolerance=aspect_tolerance,
            min_short_edge=min_short_edge,
        )
        references.append(style_result)
        style_lock_status = style_result["status"]

    for pos, item in enumerate(pack.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        shot_id = str(item.get("shot_id") or f"shot_{pos + 1:03d}")
        mode = str(item.get("mode") or "")
        path = overrides.get(shot_id) or _path_from_reference(item.get("reference"), base_dir=base_dir)
        required = mode == "image_to_video"
        if not required and not (path and Path(path).exists()):
            continue
        references.append(evaluate_reference(
            reference_id=shot_id,
            role="first_frame" if required else "optional_reference",
            path=path,
            required=required,
            target_aspect=aspect,
            target_ratio=target_ratio,
            target_orientation=target_orientation,
            aspect_tolerance=aspect_tolerance,
            min_short_edge=min_short_edge,
        ))

    blockers = [message for item in references for message in item["blockers"]]
    warnings = [message for item in references for message in item["warnings"]]
    summary = {
        "references": len(references),
        "ready": sum(item["status"] == "ready" for item in references),
        "warnings": len(warnings),
        "blocking": len(blockers),
        "aspect_mismatches": sum(
            any("conflicts with" in message or "differs from" in message for message in item["blockers"])
            for item in references
        ),
        "transparent_backgrounds": sum(
            bool(item.get("media", {}).get("transparent_background")) for item in references
        ),
        "style_lock_targets": len(generated_item_ids),
    }
    return {
        "version": "reference_frame_preflight.v1",
        "status": "blocked" if blockers else ("warn" if warnings else "ready"),
        "source": {
            "prompt_pack": str(pack_path),
            "prompt_pack_version": pack.get("version"),
        },
        "target": {
            "aspect": aspect,
            "ratio": round(target_ratio, 4),
            "orientation": target_orientation,
        },
        "settings": {
            "require_style_reference": require_style_reference,
            "aspect_tolerance": aspect_tolerance,
            "min_short_edge": min_short_edge,
        },
        "style_lock": {
            "status": style_lock_status,
            "reference_path": style_path,
            "applies_to": generated_item_ids,
        },
        "summary": summary,
        "references": references,
        "blockers": blockers,
        "warnings": warnings,
        "next_steps": list(dict.fromkeys(
            correction
            for item in references
            for correction in item["corrections"]
        )) or ["Reference frames are ready for prompt/provider review."],
    }


def emit_markdown(preflight: Mapping[str, Any]) -> str:
    summary = preflight.get("summary") or {}
    target = preflight.get("target") or {}
    lines = [
        "# Reference Frame Preflight",
        "",
        f"- Status: **{preflight.get('status', '')}**",
        f"- Target: `{target.get('aspect', '')}` ({target.get('orientation', '')})",
        f"- References: {summary.get('references', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        "",
        "| reference | role | dimensions | orientation | alpha | status |",
        "|---|---|---:|---|---|---|",
    ]
    for item in preflight.get("references") or []:
        media = item.get("media") or {}
        dimensions = (
            f"{media.get('width')}×{media.get('height')}"
            if media.get("width") and media.get("height")
            else "-"
        )
        lines.append(
            "| {id} | {role} | {dims} | {orientation} | {alpha} | {status} |".format(
                id=item.get("id", ""),
                role=item.get("role", ""),
                dims=dimensions,
                orientation=media.get("orientation", "-"),
                alpha="yes" if media.get("transparent_background") else "no",
                status=item.get("status", ""),
            )
        )

    for heading, key in (("Blockers", "blockers"), ("Warnings", "warnings"), ("Next Steps", "next_steps")):
        values = list(preflight.get(key) or [])
        if not values:
            continue
        lines.extend(["", f"## {heading}", ""])
        lines.extend(f"- {value}" for value in values)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight first-frame and shared style references before video generation."
    )
    parser.add_argument("--prompt-pack", required=True, help="Input video_prompt_pack.json.")
    parser.add_argument("--output", required=True, help="Output preflight JSON.")
    parser.add_argument("--markdown", help="Optional Markdown review output.")
    parser.add_argument("--style-reference", help="Override shared style-reference image/video.")
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help="Override a shot reference as shot_id=/path/to/file; can repeat.",
    )
    parser.add_argument("--target-aspect", help="Override target aspect, for example 9:16.")
    parser.add_argument(
        "--require-style-reference",
        action="store_true",
        help="Block when a shared style reference is not present and readable.",
    )
    parser.add_argument(
        "--aspect-tolerance",
        type=float,
        default=0.20,
        help="Maximum relative aspect-ratio error before blocking (default: 0.20).",
    )
    parser.add_argument(
        "--min-short-edge",
        type=int,
        default=512,
        help="Warn when a reference short edge is below this pixel count (default: 512).",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when blockers remain.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not 0 <= args.aspect_tolerance <= 1:
        parser.error("--aspect-tolerance must be between 0 and 1")
    if args.min_short_edge <= 0:
        parser.error("--min-short-edge must be positive")

    pack_path = Path(args.prompt_pack).expanduser().resolve()
    pack = load_prompt_pack(str(pack_path))
    try:
        overrides = _parse_overrides(args.reference, base_dir=pack_path.parent)
        preflight = build_preflight(
            pack,
            prompt_pack_path=str(pack_path),
            style_reference=args.style_reference,
            reference_overrides=overrides,
            target_aspect=args.target_aspect,
            require_style_reference=args.require_style_reference,
            aspect_tolerance=args.aspect_tolerance,
            min_short_edge=args.min_short_edge,
        )
    except ValueError as exc:
        parser.error(str(exc))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(preflight, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(preflight))

    print(
        "Reference frame preflight: "
        f"{preflight['status']}; references={preflight['summary']['references']} "
        f"blocking={preflight['summary']['blocking']} warnings={preflight['summary']['warnings']}"
    )
    if args.strict and preflight["summary"]["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
