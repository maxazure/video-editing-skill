#!/usr/bin/env python3
"""Export one source video into three platform-specific deliverables.

Presets:
  xhs    — 3:4 (1080×1440) for Xiaohongshu feed. Centre-crop from 9:16 master.
  douyin — 9:16 (1080×1920) for Douyin/TikTok. Re-encode if needed.
  wxch   — 9:16 (1080×1920), capped at 60s for 微信视频号 social sharing.

Usage:
    python3 scripts/multi_export.py <input.mp4> --output-dir ./output \\
        --platforms xhs douyin wxch
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import get_video_info, get_ffmpeg_encode_args  # noqa: E402


@dataclasses.dataclass(frozen=True)
class PlatformPreset:
    name: str
    width: int
    height: int
    max_duration_seconds: Optional[float]
    crf: int                 # for x264 fallback
    audio_bitrate: str
    notes: str


PRESETS = {
    "xhs": PlatformPreset(
        name="xhs", width=1080, height=1440,        # 3:4
        max_duration_seconds=None, crf=22, audio_bitrate="192k",
        notes="Xiaohongshu/RED: 3:4 fills the feed thumbnail (+~40% display area).",
    ),
    "douyin": PlatformPreset(
        name="douyin", width=1080, height=1920,     # 9:16
        max_duration_seconds=None, crf=22, audio_bitrate="192k",
        notes="Douyin/TikTok: full-screen 9:16.",
    ),
    "wxch": PlatformPreset(
        name="wxch", width=1080, height=1920,       # 9:16 ≤60s
        max_duration_seconds=60.0, crf=23, audio_bitrate="160k",
        notes="WeChat视频号: 9:16, ideally ≤60s for social-graph distribution.",
    ),
}


def _source_aspect_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """Build an ffmpeg -vf filter that turns src into dst dimensions.

    Rules:
      - same aspect: simple scale
      - source is taller (9:16 → 3:4): centre-crop top+bottom
      - source is wider (16:9 → 9:16): centre-crop sides
    """
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if abs(src_ratio - dst_ratio) < 1e-3:
        return f"scale={dst_w}:{dst_h}"

    if src_ratio < dst_ratio:
        # source is taller than dst — crop top/bottom or letterbox sides
        # we centre-crop the source to dst aspect, then scale
        target_h = src_w / dst_ratio
        return f"crop=in_w:{int(target_h)}:(in_w-out_w)/2:(in_h-out_h)/2,scale={dst_w}:{dst_h}"
    else:
        # source is wider than dst — crop sides
        target_w = src_h * dst_ratio
        return f"crop={int(target_w)}:in_h:(in_w-out_w)/2:(in_h-out_h)/2,scale={dst_w}:{dst_h}"


def build_ffmpeg_command(input_path: str, output_path: str, preset: PlatformPreset,
                          src_w: int, src_h: int, src_duration: float,
                          framing_strategy: Optional[str] = None) -> List[str]:
    """Build the ffmpeg command for one platform preset."""
    cmd: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                       "-i", input_path]

    if preset.max_duration_seconds and src_duration > preset.max_duration_seconds:
        cmd.extend(["-t", f"{preset.max_duration_seconds:.2f}"])

    encode = get_ffmpeg_encode_args()
    if framing_strategy:
        from framing_preview import build_filter_complex

        graph = build_filter_complex(framing_strategy, preset.width, preset.height)
        cmd.extend(["-filter_complex", graph, "-map", "[vout]", "-map", "0:a:0?"])
    else:
        vf = _source_aspect_filter(src_w, src_h, preset.width, preset.height)
        cmd.extend(["-vf", vf])
    cmd.extend(encode)
    cmd.extend([
        "-crf", str(preset.crf),
        "-c:a", "aac", "-b:a", preset.audio_bitrate,
        "-movflags", "+faststart",
        output_path,
    ])
    return cmd


def export_one(input_path: str, output_dir: str, preset: PlatformPreset,
               framing_strategy: Optional[str] = None) -> Optional[str]:
    src_duration, src_w, src_h, _fps, _rot = get_video_info(input_path)

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(output_dir, f"{base}_{preset.name}.mp4")

    cmd = build_ffmpeg_command(
        input_path, out_path, preset, src_w, src_h, src_duration,
        framing_strategy=framing_strategy,
    )
    print(f"\n[{preset.name}] {preset.width}×{preset.height}  →  {out_path}")
    print(f"   {preset.notes}")
    if framing_strategy:
        print(f"   reviewed framing: {framing_strategy}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"   ❌ ffmpeg failed (returncode {exc.returncode})", file=sys.stderr)
        return None
    return out_path


def load_framing_strategies(report_path: str, input_path: str,
                            platforms: List[str]) -> dict[str, str]:
    """Load a live-verified framing report for the exact export source."""
    from framing_preview import verify_report

    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    if not isinstance(report, dict):
        raise ValueError("framing preview report must be a JSON object")
    verification = verify_report(report)
    if verification["summary"]["blocking"]:
        raise ValueError("framing preview is invalid: " + verification["blockers"][0])
    root = os.path.realpath(str(report.get("project_dir") or "."))
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    bound_input = os.path.realpath(os.path.join(root, str(source.get("path") or "")))
    if bound_input != os.path.realpath(input_path):
        raise ValueError("framing preview is bound to a different source video")
    choices = {
        str(item.get("platform")): str(item.get("selected_strategy"))
        for item in report.get("platforms") or []
        if isinstance(item, dict) and item.get("selected_strategy")
    }
    missing = [platform for platform in platforms if platform not in choices]
    if missing:
        raise ValueError(f"framing selection is missing for: {', '.join(missing)}")
    return {platform: choices[platform] for platform in platforms}


def main() -> int:
    p = argparse.ArgumentParser(description="Multi-platform export")
    p.add_argument("input", help="Master video path (typically 9:16 1080×1920)")
    p.add_argument("--output-dir", default="./output")
    p.add_argument("--platforms", nargs="+", default=list(PRESETS),
                   choices=list(PRESETS),
                   help="Platforms to export (default: all)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the ffmpeg commands without running")
    p.add_argument(
        "--framing-preview",
        help="Live-verified framing_preview.json with a selection for every requested platform",
    )
    args = p.parse_args()

    if not os.path.isfile(args.input):
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    src_duration, src_w, src_h, _fps, _rot = get_video_info(args.input)
    print(f"Source: {src_w}×{src_h}, {src_duration:.2f}s")

    try:
        framing = (
            load_framing_strategies(args.framing_preview, args.input, args.platforms)
            if args.framing_preview else {}
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Framing preview error: {exc}", file=sys.stderr)
        return 2

    summary: List[dict] = []
    for plat in args.platforms:
        preset = PRESETS[plat]
        out_path = os.path.join(args.output_dir, f"{os.path.splitext(os.path.basename(args.input))[0]}_{plat}.mp4")
        strategy = framing.get(plat)
        cmd = build_ffmpeg_command(
            args.input, out_path, preset, src_w, src_h, src_duration,
            framing_strategy=strategy,
        )
        if args.dry_run:
            print(f"\n[{plat}] {' '.join(cmd)}")
            summary.append({"platform": plat, "output": out_path, "framing_strategy": strategy, "cmd": cmd})
        else:
            result = export_one(args.input, args.output_dir, preset, framing_strategy=strategy)
            summary.append({"platform": plat, "output": result, "framing_strategy": strategy})

    manifest = os.path.join(args.output_dir, "multi_export_manifest.json")
    os.makedirs(args.output_dir, exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n✅ manifest → {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
