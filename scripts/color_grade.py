#!/usr/bin/env python3
"""Build auditable color-grade plans and FFmpeg filter chains.

The script is intentionally conservative: presets are bounded, custom
adjustments are clamped, and the output can be reviewed before render_final.py
applies the filter in the single-pass render graph.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_ffmpeg_encode_args, get_video_info  # noqa: E402


VERSION = "color_grade.v1"

ADJUSTMENT_BOUNDS: Mapping[str, Tuple[float, float]] = {
    "brightness": (-0.12, 0.12),
    "contrast": (0.75, 1.35),
    "saturation": (0.65, 1.45),
    "gamma": (0.80, 1.25),
    "temperature": (-0.20, 0.20),
    "tint": (-0.12, 0.12),
    "sharpness": (0.0, 0.80),
}

BASE_ADJUSTMENTS: Mapping[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "sharpness": 0.0,
}

PRESETS: Mapping[str, Mapping[str, Any]] = {
    "natural": {
        "description": "Neutral cleanup for talking heads and product demos.",
        "adjustments": {"contrast": 1.04, "saturation": 1.05, "sharpness": 0.18},
    },
    "warm": {
        "description": "Slight warm lift for lifestyle, desks, cafes, and human scenes.",
        "adjustments": {
            "brightness": 0.015,
            "contrast": 1.05,
            "saturation": 1.08,
            "temperature": 0.10,
            "sharpness": 0.12,
        },
    },
    "cool": {
        "description": "Cooler tech/product look for screens and tools.",
        "adjustments": {
            "brightness": -0.005,
            "contrast": 1.06,
            "saturation": 1.03,
            "temperature": -0.09,
            "tint": -0.02,
            "sharpness": 0.20,
        },
    },
    "punchy": {
        "description": "Higher contrast social-feed look for energetic short clips.",
        "adjustments": {
            "brightness": 0.01,
            "contrast": 1.16,
            "saturation": 1.20,
            "gamma": 0.96,
            "sharpness": 0.30,
        },
    },
    "soft": {
        "description": "Lower-contrast soft grade for skin, interviews, and calm narration.",
        "adjustments": {
            "brightness": 0.02,
            "contrast": 0.92,
            "saturation": 0.96,
            "gamma": 1.04,
            "temperature": 0.04,
        },
    },
    "cinematic": {
        "description": "Restrained contrast and cool shadows for B-roll-heavy pieces.",
        "adjustments": {
            "brightness": -0.012,
            "contrast": 1.14,
            "saturation": 0.93,
            "gamma": 0.98,
            "temperature": -0.04,
            "tint": 0.02,
            "sharpness": 0.15,
        },
    },
    "screen": {
        "description": "Screen-recording cleanup with mild contrast and sharpness.",
        "adjustments": {
            "brightness": 0.0,
            "contrast": 1.08,
            "saturation": 1.00,
            "gamma": 0.98,
            "sharpness": 0.35,
        },
    },
}


def _round4(value: float) -> float:
    return round(float(value), 4)


def _finite_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(name: str, value: Any, warnings: List[str]) -> float:
    low, high = ADJUSTMENT_BOUNDS[name]
    parsed = _finite_float(value, default=BASE_ADJUSTMENTS[name])
    if parsed is None:
        parsed = BASE_ADJUSTMENTS[name]
    clamped = min(high, max(low, parsed))
    if clamped != parsed:
        warnings.append(f"{name} clamped from {parsed:g} to {clamped:g}")
    return _round4(clamped)


def normalize_adjustments(
    *,
    preset: str = "natural",
    overrides: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, float], List[str]]:
    """Return bounded adjustments and warnings for a preset + overrides."""
    if preset not in PRESETS:
        raise ValueError(f"unknown color grade preset: {preset}")

    merged: Dict[str, Any] = dict(BASE_ADJUSTMENTS)
    merged.update(PRESETS[preset].get("adjustments", {}))
    if overrides:
        for key, value in overrides.items():
            if key in ADJUSTMENT_BOUNDS and value is not None:
                merged[key] = value

    warnings: List[str] = []
    normalized = {
        key: _clamp(key, merged.get(key), warnings)
        for key in ADJUSTMENT_BOUNDS
    }
    return normalized, warnings


def build_ffmpeg_filter(adjustments: Mapping[str, Any]) -> str:
    """Build a single-label-safe FFmpeg video filter chain."""
    brightness = _finite_float(adjustments.get("brightness"), default=0.0) or 0.0
    contrast = _finite_float(adjustments.get("contrast"), default=1.0) or 1.0
    saturation = _finite_float(adjustments.get("saturation"), default=1.0) or 1.0
    gamma = _finite_float(adjustments.get("gamma"), default=1.0) or 1.0
    temperature = _finite_float(adjustments.get("temperature"), default=0.0) or 0.0
    tint = _finite_float(adjustments.get("tint"), default=0.0) or 0.0
    sharpness = _finite_float(adjustments.get("sharpness"), default=0.0) or 0.0

    parts = [
        "eq=brightness={brightness:.4f}:contrast={contrast:.4f}:"
        "saturation={saturation:.4f}:gamma={gamma:.4f}".format(
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            gamma=gamma,
        )
    ]

    if abs(temperature) >= 0.001 or abs(tint) >= 0.001:
        red = temperature * 0.18
        blue = -temperature * 0.18
        green = -tint * 0.18
        parts.append(
            "colorbalance="
            f"rs={red:.4f}:rm={red * 0.8:.4f}:rh={red * 0.55:.4f}:"
            f"gs={green:.4f}:gm={green * 0.8:.4f}:gh={green * 0.55:.4f}:"
            f"bs={blue:.4f}:bm={blue * 0.8:.4f}:bh={blue * 0.55:.4f}"
        )

    if sharpness > 0:
        parts.append(f"unsharp=5:5:{sharpness:.4f}:5:5:0.0000")

    return ",".join(parts)


def validate_filter_chain(value: str) -> str:
    """Reject multi-label or multi-statement filters before render_final uses them."""
    chain = str(value or "").strip()
    if not chain:
        raise ValueError("color grade filter is empty")
    forbidden = {";", "\n", "\r", "[", "]"}
    if any(token in chain for token in forbidden):
        raise ValueError("color grade filter must be a single video-chain fragment")
    if "movie=" in chain or "amovie=" in chain:
        raise ValueError("color grade filter cannot add external media sources")
    return chain


def build_plan(
    *,
    preset: str = "natural",
    overrides: Optional[Mapping[str, Any]] = None,
    source: Optional[str] = None,
    render_output: Optional[str] = None,
    notes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    adjustments, warnings = normalize_adjustments(preset=preset, overrides=overrides)
    vf = build_ffmpeg_filter(adjustments)
    plan: Dict[str, Any] = {
        "version": VERSION,
        "preset": preset,
        "description": PRESETS[preset]["description"],
        "adjustments": adjustments,
        "ffmpeg": {"vf": vf},
        "render_config": {
            "color_grade": {
                "preset": preset,
                "adjustments": adjustments,
                "filter": vf,
            }
        },
        "warnings": warnings,
        "notes": list(notes or []),
    }
    if source:
        plan["source"] = os.path.abspath(source)
        if os.path.isfile(source):
            duration, width, height, fps, rotation = get_video_info(source)
            plan["source_info"] = {
                "duration": round(duration, 3),
                "width": width,
                "height": height,
                "fps": round(fps, 3),
                "rotation": rotation,
            }
    if render_output:
        plan["render_output"] = os.path.abspath(render_output)
        if source:
            command = build_render_command(source, render_output, vf)
            plan["ffmpeg"]["command"] = command
    return plan


def build_render_command(input_path: str, output_path: str, vf: str) -> List[str]:
    validate_filter_chain(vf)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_path, "-vf", vf]
    cmd.extend(get_ffmpeg_encode_args())
    cmd.extend(["-c:a", "copy", "-movflags", "+faststart", output_path])
    return cmd


def color_grade_filter_from_value(value: Any, *, base_dir: Optional[str] = None) -> Optional[str]:
    """Resolve render_config/CLI color-grade value to a safe FFmpeg chain.

    Supported values:
      - "warm" / "screen" preset name
      - path to a color_grade.v1 JSON plan
      - dict with {"preset": ..., "adjustments": ...}
      - dict with {"filter": ...} from color_grade.py output
    """
    if value in (None, "", False):
        return None

    if isinstance(value, str):
        raw = value.strip()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute() and base_dir:
            candidate = Path(base_dir).expanduser().resolve() / candidate
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                return color_grade_filter_from_value(json.load(f), base_dir=str(candidate.parent))
        if raw in PRESETS:
            adjustments, _warnings = normalize_adjustments(preset=raw)
            return build_ffmpeg_filter(adjustments)
        if raw.endswith(".json") or "/" in raw or "\\" in raw:
            raise ValueError(f"color grade plan not found: {raw}")
        return validate_filter_chain(raw)

    if not isinstance(value, Mapping):
        raise ValueError("color_grade must be a preset, plan path, filter, or object")

    if value.get("version") == VERSION and isinstance(value.get("ffmpeg"), Mapping):
        vf = str(value["ffmpeg"].get("vf") or "")
        return validate_filter_chain(vf)

    if value.get("filter"):
        return validate_filter_chain(str(value.get("filter")))

    preset = str(value.get("preset") or "natural")
    overrides = copy.deepcopy(value.get("adjustments") or {})
    if not isinstance(overrides, Mapping):
        overrides = {}
    adjustments, _warnings = normalize_adjustments(preset=preset, overrides=overrides)
    return build_ffmpeg_filter(adjustments)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def emit_markdown(plan: Mapping[str, Any]) -> str:
    adjustments = plan.get("adjustments") or {}
    lines = [
        "# Color Grade Plan",
        "",
        f"- Version: `{plan.get('version', '')}`",
        f"- Preset: `{plan.get('preset', '')}`",
        f"- Description: {plan.get('description', '')}",
    ]
    if plan.get("source"):
        lines.append(f"- Source: `{plan.get('source')}`")
    if plan.get("render_output"):
        lines.append(f"- Render output: `{plan.get('render_output')}`")
    lines.extend(["", "## Adjustments", "", "| key | value |", "|---|---:|"])
    for key in ADJUSTMENT_BOUNDS:
        lines.append(f"| `{key}` | {adjustments.get(key, '')} |")

    warnings = plan.get("warnings") or []
    lines.extend(["", "## Warnings"])
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- none")

    vf = ((plan.get("ffmpeg") or {}).get("vf") or "").strip()
    lines.extend(
        [
            "",
            "## FFmpeg Filter",
            "",
            "```text",
            vf,
            "```",
            "",
            "## render_final.py Config Snippet",
            "",
            "```json",
            json.dumps(plan.get("render_config", {}), ensure_ascii=False, indent=2),
            "```",
        ]
    )

    command = (plan.get("ffmpeg") or {}).get("command")
    if command:
        lines.extend(
            [
                "",
                "## Optional Existing-Master Render",
                "",
                "```bash",
                " ".join(command),
                "```",
            ]
        )

    return "\n".join(lines) + "\n"


def _arg_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "brightness": args.brightness,
        "contrast": args.contrast,
        "saturation": args.saturation,
        "gamma": args.gamma,
        "temperature": args.temperature,
        "tint": args.tint,
        "sharpness": args.sharpness,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a bounded color-grade plan")
    parser.add_argument("--preset", default="natural", choices=sorted(PRESETS))
    parser.add_argument("--input", help="Optional source video for metadata or existing-master render")
    parser.add_argument("--output", required=True, help="Output color_grade.json path")
    parser.add_argument("--markdown", help="Optional Markdown review path")
    parser.add_argument("--render-output", help="Optional rendered video path for an existing master")
    parser.add_argument("--brightness", type=float)
    parser.add_argument("--contrast", type=float)
    parser.add_argument("--saturation", type=float)
    parser.add_argument("--gamma", type=float)
    parser.add_argument("--temperature", type=float, help="Warm/cool shift, bounded -0.20..0.20")
    parser.add_argument("--tint", type=float, help="Green/magenta shift, bounded -0.12..0.12")
    parser.add_argument("--sharpness", type=float, help="Unsharp amount, bounded 0..0.80")
    parser.add_argument("--dry-run", action="store_true", help="Do not render even with --render-output")
    parser.add_argument("--strict", action="store_true", help="Return 2 when custom values were clamped")
    args = parser.parse_args()

    if args.render_output and not args.input:
        print("--render-output requires --input", file=sys.stderr)
        return 1

    plan = build_plan(
        preset=args.preset,
        overrides=_arg_overrides(args),
        source=args.input,
        render_output=args.render_output,
    )
    write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))

    if args.render_output and args.input and not args.dry_run:
        cmd = (plan.get("ffmpeg") or {}).get("command")
        if not cmd:
            print("No render command generated", file=sys.stderr)
            return 1
        subprocess.run(cmd, check=True)

    print(f"color grade plan -> {args.output}")
    if args.strict and plan.get("warnings"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
