#!/usr/bin/env python3
"""Inspect exact decoded video frames in a bounded grid or export one frame."""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = "frame_grid.v1"
SHOWINFO = re.compile(r"\[Parsed_showinfo_\d+[^\n]*?\bn:\s*(\d+)\s+pts:\s*\S+\s+pts_time:\s*([+-]?[\d.]+)")
LOOKUP_SOURCE = re.compile(r"\[Parsed_showinfo_0[^\n]*?\bn:\s*(\d+)\s+pts:\s*\S+\s+pts_time:\s*([+-]?[\d.]+)")
LOOKUP_SELECTED = re.compile(r"\[Parsed_showinfo_2[^\n]*?\bn:\s*\d+\s+pts:\s*\S+\s+pts_time:\s*([+-]?[\d.]+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256(path)}


def checked_input(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source must be a regular file, without a symlink: {path}")
    return path.resolve()


def checked_outputs(source: Path, image: str, receipt: str, force: bool) -> tuple[Path, Path]:
    paths = [Path(image).expanduser(), Path(receipt).expanduser()]
    if any(path.is_symlink() for path in paths):
        raise ValueError("output cannot be a symlink")
    paths = [path.resolve() for path in paths]
    if paths[0] == paths[1] or source in paths:
        raise ValueError("source, image and receipt paths must differ")
    if all(path.exists() for path in paths) and paths[0].samefile(paths[1]):
        raise ValueError("image and receipt cannot alias each other")
    for path in paths:
        if path.exists() and path.samefile(source):
            raise ValueError("output cannot alias source")
        if path.exists() and not force:
            raise FileExistsError(f"output exists; pass --force: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    return paths[0], paths[1]


def build_filter(start: int, step: int, count: int, columns: int, width: int, grid: bool) -> str:
    last = start + step * (count - 1)
    select = f"select='between(n,{start},{last})*not(mod(n-{start},{step}))'"
    chain = f"{select},showinfo,scale={width}:-2:flags=lanczos,setsar=1"
    if grid:
        rows = math.ceil(count / columns)
        chain += (",drawtext=text='CELL %{n}':start_number=1:fontsize=18:fontcolor=white:"
                  "borderw=2:bordercolor=black:x=8:y=8"
                  f",pad={width}:ih+4:0:0:black,tile=layout={columns}x{rows}:"
                  f"nb_frames={count}:padding=4:margin=4:color=black")
    return chain


def parse_showinfo(stderr: str) -> list[float]:
    hits = [(int(index), float(value)) for index, value in SHOWINFO.findall(stderr)]
    if any(index != expected or not math.isfinite(pts) for expected, (index, pts) in enumerate(hits)):
        raise ValueError("could not verify decoded frame order or PTS")
    return [pts for _, pts in hits]


def locate(source: str, at_seconds: float) -> dict:
    """Decode from the start and find the source frame nearest a presentation timestamp."""
    if not math.isfinite(at_seconds) or at_seconds < 0:
        raise ValueError("at-seconds must be a finite, nonnegative number")
    source_path = checked_input(source)
    before = file_record(source_path)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
               "-filter_threads", "1", "-i", str(source_path), "-map", "0:v:0",
               "-vf", f"showinfo,select='gte(t,{at_seconds:.9f})',showinfo",
               "-an", "-vsync", "0", "-frames:v", "1", "-f", "null", "-"]
    neighbors = deque(maxlen=2)
    selected = None
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True, errors="replace") as process:
        assert process.stderr is not None
        for line in process.stderr:
            match = LOOKUP_SOURCE.search(line)
            if match and selected is None:
                frame, pts = int(match.group(1)), float(match.group(2))
                if not math.isfinite(pts):
                    raise ValueError("source has a nonfinite frame PTS")
                neighbors.append({"decoded_frame": frame, "pts_seconds": pts})
            match = LOOKUP_SELECTED.search(line)
            if match and selected is None:
                selected = float(match.group(1))
        if process.wait() != 0:
            raise RuntimeError("FFmpeg could not decode the requested timestamp")
    if selected is None or not neighbors or neighbors[-1]["pts_seconds"] != selected:
        raise ValueError("timestamp is after the last decoded frame")
    nearest = min(neighbors, key=lambda row: (abs(row["pts_seconds"] - at_seconds), row["decoded_frame"]))
    if file_record(source_path) != before:
        raise ValueError("source changed during frame lookup")
    return {"version": SCHEMA, "source": before, "at_seconds": at_seconds,
            "nearest": nearest, "delta_seconds": round(nearest["pts_seconds"] - at_seconds, 9),
            "neighbors": list(neighbors)}


def create(*, source: str, output: str, receipt: str, start_frame: int,
           step: int = 1, count: int = 1, columns: int = 4,
           width: int = 320, grid: bool = True, force: bool = False) -> dict:
    if start_frame < 0 or not 1 <= step <= 10000 or not 1 <= count <= 36:
        raise ValueError("start-frame must be >=0; step 1..10000; count 1..36")
    if not 1 <= columns <= 6 or not 64 <= width <= 960 or width % 2:
        raise ValueError("columns must be 1..6; width must be even and 64..960")
    if not grid and count != 1:
        raise ValueError("single-frame export requires count=1")
    source_path = checked_input(source)
    output_path, receipt_path = checked_outputs(source_path, output, receipt, force)
    before = file_record(source_path)
    with tempfile.TemporaryDirectory(prefix=".frame-grid-", dir=str(output_path.parent)) as work:
        temporary = Path(work) / "image.png"
        command = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
                   "-i", str(source_path), "-map", "0:v:0", "-vf",
                   build_filter(start_frame, step, count, columns, width, grid),
                   "-an", "-vsync", "0", "-frames:v", "1", "-y", str(temporary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError((result.stderr or "FFmpeg did not produce an image")[-1200:])
        times = parse_showinfo(result.stderr)
        if len(times) != count:
            raise ValueError(f"requested {count} decoded frames, found {len(times)}; check frame range")
        if not temporary.is_file() or not temporary.stat().st_size:
            raise RuntimeError("FFmpeg did not produce an image")
        if file_record(source_path) != before:
            raise ValueError("source changed during extraction")
        temporary.replace(output_path)
    entries = [{"cell": i + 1, "row": i // columns + 1, "column": i % columns + 1,
                "decoded_frame": start_frame + i * step, "pts_seconds": time}
               for i, time in enumerate(times)]
    payload = {"version": SCHEMA, "mode": "grid" if grid else "frame",
               "source": before, "image": file_record(output_path),
               "selection": {"start_frame": start_frame, "step": step, "count": count,
                             "columns": columns if grid else 1, "cell_width": width},
               "frames": entries}
    payload["receipt_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def verify(receipt: str) -> dict:
    path = checked_input(receipt)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != SCHEMA:
        raise ValueError("unknown frame-grid receipt version")
    claimed = data.pop("receipt_sha256", None)
    actual = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if claimed != actual:
        raise ValueError("receipt content changed")
    for key in ("source", "image"):
        record = data[key]
        candidate = checked_input(record["path"])
        if file_record(candidate) != record:
            raise ValueError(f"{key} changed after extraction")
    selection = data["selection"]
    if len(data["frames"]) != selection["count"]:
        raise ValueError("frame mapping is incomplete")
    return {"status": "ready", "frames": len(data["frames"]), "image": data["image"]["path"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grid", "frame"):
        item = sub.add_parser(name, help="Create a grid or exact decoded-frame PNG")
        item.add_argument("source")
        item.add_argument("--start-frame", type=int, required=True)
        if name == "grid":
            item.add_argument("--step", type=int, default=1)
            item.add_argument("--count", type=int, default=16)
            item.add_argument("--columns", type=int, default=4)
        item.add_argument("--width", type=int, default=320)
        item.add_argument("--output", required=True)
        item.add_argument("--receipt", required=True)
        item.add_argument("--force", action="store_true")
    check = sub.add_parser("verify", help="Check the source, image and mapping receipt")
    check.add_argument("receipt")
    lookup = sub.add_parser("locate", help="Find the nearest decoded frame to a source PTS in seconds")
    lookup.add_argument("source")
    lookup.add_argument("--at-seconds", type=float, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify(args.receipt), ensure_ascii=False))
        elif args.command == "locate":
            print(json.dumps(locate(args.source, args.at_seconds), ensure_ascii=False))
        else:
            grid = args.command == "grid"
            data = create(source=args.source, output=args.output, receipt=args.receipt,
                          start_frame=args.start_frame, step=args.step if grid else 1,
                          count=args.count if grid else 1, columns=args.columns if grid else 1,
                          width=args.width, grid=grid, force=args.force)
            print(f"{data['mode']}: {data['image']['path']} ({len(data['frames'])} frames)")
    except (ValueError, FileExistsError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"frame_grid: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
