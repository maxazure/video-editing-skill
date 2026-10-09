#!/usr/bin/env python3
"""Bind a local .cube LUT to an auditable FFmpeg color-grade plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

VERSION = "lut_grade.v1"
INTERPOLATIONS = ("nearest", "trilinear", "tetrahedral", "pyramid", "prism")
UNSAFE_PATH = re.compile(r"[:;,\[\]'\"\\\n\r\x00]")


def fingerprint(raw: str) -> dict:
    path = Path(raw).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".cube":
        raise ValueError("LUT must be a regular .cube file, not a symlink")
    if UNSAFE_PATH.search(str(path)):
        raise ValueError("LUT path contains FFmpeg filtergraph delimiter characters")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def filter_for(lut: dict, interpolation: str) -> str:
    if interpolation not in INTERPOLATIONS:
        raise ValueError("unsupported LUT interpolation")
    return f"lut3d=file='{lut['path']}':interp={interpolation}"


def check_ffmpeg(vf: str) -> None:
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi",
               "-i", "color=c=gray:s=16x16:d=0.04", "-vf", vf, "-frames:v", "1", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("FFmpeg could not load LUT: " + result.stderr.strip()[-800:])


def build_plan(path: str, interpolation: str = "tetrahedral") -> dict:
    before = fingerprint(path)
    vf = filter_for(before, interpolation)
    check_ffmpeg(vf)
    if fingerprint(path) != before:
        raise ValueError("LUT changed during validation")
    return {"version": VERSION, "lut": before, "interpolation": interpolation,
            "ffmpeg": {"vf": vf}}


def filter_from_plan(plan: dict) -> str:
    if not isinstance(plan, dict) or plan.get("version") != VERSION:
        raise ValueError("invalid LUT grade plan")
    lut = plan.get("lut")
    if not isinstance(lut, dict) or not isinstance(lut.get("path"), str):
        raise ValueError("LUT plan lacks a valid path")
    fresh = build_plan(lut["path"], plan.get("interpolation"))
    if fresh != plan:
        raise ValueError("LUT file or plan changed since validation")
    return fresh["ffmpeg"]["vf"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("plan", help="Validate a .cube file and write a bound plan")
    create.add_argument("lut")
    create.add_argument("--interpolation", choices=INTERPOLATIONS, default="tetrahedral")
    create.add_argument("--output", required=True)
    verify = sub.add_parser("verify", help="Revalidate a plan and its LUT")
    verify.add_argument("plan")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            print(json.dumps({"status": "verified", "filter": filter_from_plan(plan)}))
        else:
            lut = fingerprint(args.lut)
            output = Path(args.output).expanduser().absolute()
            if output.is_symlink() or output.exists() or output == Path(lut["path"]):
                raise ValueError("output must be a new path distinct from the LUT")
            plan = build_plan(args.lut, args.interpolation)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(plan, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps({"status": "planned", "output": str(output)}))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"lut_grade: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
