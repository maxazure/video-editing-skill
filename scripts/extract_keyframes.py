#!/usr/bin/env python3
"""
Extract keyframes from a video and compose them into a timeline/storyboard image.

This is especially useful for oral-broadcast (口播) videos shot while walking,
where the visual content (locations, scenery, gestures) provides context that
audio transcription alone cannot capture.

Usage:
  python3 scripts/extract_keyframes.py <video_path> [options]

  # Extract keyframes with scene-change detection, output storyboard
  python3 scripts/extract_keyframes.py video.mp4

  # Specify max keyframes and output directory
  python3 scripts/extract_keyframes.py video.mp4 --max-frames 20 --output-dir ./keyframes

  # Adjust scene-change sensitivity (lower = more keyframes)
  python3 scripts/extract_keyframes.py video.mp4 --threshold 0.3

Output:
  <video_name>_keyframes/         Directory with individual keyframe PNGs
  <video_name>_storyboard.png     Composite timeline image with timestamps
  <video_name>_keyframes.json     Keyframe metadata (timestamps, frame numbers)
"""

import argparse
import json
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info, _run_quiet


def extract_keyframes(video_path, output_dir, max_frames=16, threshold=0.4):
    """Extract keyframes using ffmpeg scene-change detection.

    Args:
        video_path: Path to source video.
        output_dir: Directory to save keyframe images.
        max_frames: Maximum number of keyframes to extract.
        threshold: Scene-change sensitivity (0.0-1.0, lower = more frames).

    Returns:
        List of dicts: [{"frame": int, "timestamp": float, "path": str}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Detect scene changes and get timestamps
    detect_cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "frame=pts_time,pict_type",
        "-select_streams", "v:0",
        "-of", "csv=p=0",
        "-f", "lavfi",
        f"movie='{_escape_lavfi_path(video_path)}',select='gt(scene,{threshold})'",
    ]

    try:
        result = subprocess.run(
            detect_cmd, capture_output=True, text=True, timeout=300
        )
        scene_output = result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        scene_output = ""

    # Parse scene-change timestamps
    scene_times = []
    if scene_output:
        for line in scene_output.split("\n"):
            parts = line.strip().split(",")
            if parts and parts[0]:
                try:
                    t = float(parts[0])
                    scene_times.append(t)
                except ValueError:
                    continue

    # Get video duration for fallback uniform sampling
    duration, width, height, fps, _ = get_video_info(video_path)

    # If scene detection found too few keyframes, supplement with uniform sampling
    if len(scene_times) < 4:
        # Fallback: uniform interval sampling
        interval = max(1.0, duration / max_frames)
        scene_times = [i * interval for i in range(int(duration / interval) + 1)]

    # Always include first and last frames
    if not scene_times or scene_times[0] > 1.0:
        scene_times.insert(0, 0.5)
    if scene_times[-1] < duration - 2.0:
        scene_times.append(max(0, duration - 1.0))

    # Remove duplicates and sort
    scene_times = sorted(set(scene_times))

    # Limit to max_frames by uniform subsampling if too many
    if len(scene_times) > max_frames:
        step = len(scene_times) / max_frames
        scene_times = [scene_times[int(i * step)] for i in range(max_frames)]

    # Step 2: Extract each keyframe as PNG
    keyframes = []
    for i, t in enumerate(scene_times):
        out_path = os.path.join(output_dir, f"keyframe_{i:03d}_{t:.1f}s.png")
        extract_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_path,
        ]
        try:
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
        except subprocess.SubprocessError:
            continue

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            keyframes.append({
                "index": i,
                "timestamp": round(t, 2),
                "path": out_path,
            })

    return keyframes


def compose_storyboard(keyframes, output_path, video_width, video_height,
                       cols=4, thumb_width=320):
    """Compose keyframes into a single storyboard/timeline image.

    Args:
        keyframes: List of keyframe dicts from extract_keyframes().
        output_path: Path for the output storyboard PNG.
        video_width: Original video width.
        video_height: Original video height.
        cols: Number of columns in the grid.
        thumb_width: Width of each thumbnail in pixels.

    Returns:
        Path to the storyboard image, or None on failure.
    """
    if not keyframes:
        return None

    n = len(keyframes)
    rows = math.ceil(n / cols)
    thumb_height = int(thumb_width * video_height / video_width)

    # Build ffmpeg filter for grid composition with timestamp labels
    inputs = []
    filter_parts = []

    for i, kf in enumerate(keyframes):
        inputs.extend(["-i", kf["path"]])
        ts = kf["timestamp"]
        m, s = divmod(ts, 60)
        label = f"{int(m):02d}:{s:05.2f}"
        # Scale each frame and add timestamp overlay
        filter_parts.append(
            f"[{i}:v]scale={thumb_width}:{thumb_height},"
            f"drawtext=text='{label}':fontsize=16:fontcolor=white:"
            f"borderw=2:bordercolor=black:x=5:y=h-25[t{i}]"
        )

    # Pad to fill the grid if needed
    for i in range(n, rows * cols):
        filter_parts.append(
            f"color=c=black:s={thumb_width}x{thumb_height}:d=1[t{i}]"
        )

    # Build horizontal stacks for each row, then vertical stack
    row_labels = []
    total = rows * cols
    for r in range(rows):
        row_inputs = "".join(f"[t{r * cols + c}]" for c in range(cols))
        row_label = f"[row{r}]"
        filter_parts.append(f"{row_inputs}hstack=inputs={cols}{row_label}")
        row_labels.append(row_label)

    # Vertical stack all rows
    vstack_input = "".join(row_labels)
    filter_parts.append(f"{vstack_input}vstack=inputs={rows}[out]")

    full_filter = ";\n".join(filter_parts)

    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)
    cmd.extend([
        "-filter_complex", full_filter,
        "-map", "[out]",
        "-frames:v", "1",
        output_path,
    ])

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        if os.path.isfile(output_path):
            return output_path
    except subprocess.CalledProcessError as e:
        print(f"Storyboard composition failed: {e.stderr[-500:]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Storyboard composition timed out", file=sys.stderr)

    return None


def _escape_lavfi_path(path):
    """Escape path for use in ffmpeg lavfi filter source."""
    path = path.replace("\\", "/")
    path = path.replace("'", "'\\''")
    path = path.replace(":", "\\:")
    return path


def _format_timestamp(seconds):
    """Format seconds to MM:SS.s string."""
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:04.1f}"


def main():
    parser = argparse.ArgumentParser(
        description="Extract keyframes from video and compose a timeline storyboard"
    )
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory for keyframes (default: <video>_keyframes/)")
    parser.add_argument("--max-frames", type=int, default=16,
                        help="Maximum number of keyframes to extract (default: 16)")
    parser.add_argument("--threshold", type=float, default=0.4,
                        help="Scene-change sensitivity 0.0-1.0, lower = more frames (default: 0.4)")
    parser.add_argument("--cols", type=int, default=4,
                        help="Number of columns in storyboard grid (default: 4)")
    parser.add_argument("--thumb-width", type=int, default=320,
                        help="Thumbnail width in pixels (default: 320)")
    parser.add_argument("--no-storyboard", action="store_true",
                        help="Skip storyboard composition, only extract individual frames")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    if not os.path.isfile(video_path):
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output directory
    video_dir = os.path.dirname(video_path)
    video_name = os.path.splitext(os.path.basename(video_path))[0]

    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    else:
        output_dir = os.path.join(video_dir, f"{video_name}_keyframes")

    # Get video info
    duration, width, height, fps, rotation = get_video_info(video_path)
    print(f"Video: {video_name}")
    print(f"  Resolution: {width}x{height}, Duration: {_format_timestamp(duration)}, FPS: {fps:.1f}")

    # Extract keyframes
    print(f"Extracting keyframes (threshold={args.threshold}, max={args.max_frames})...")
    keyframes = extract_keyframes(
        video_path, output_dir,
        max_frames=args.max_frames,
        threshold=args.threshold,
    )

    if not keyframes:
        print("Error: No keyframes extracted", file=sys.stderr)
        sys.exit(1)

    print(f"Extracted {len(keyframes)} keyframes:")
    for kf in keyframes:
        print(f"  [{kf['index']:3d}] {_format_timestamp(kf['timestamp'])} → {os.path.basename(kf['path'])}")

    # Save keyframe metadata
    meta_path = os.path.join(video_dir, f"{video_name}_keyframes.json")
    meta = {
        "video": video_path,
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "keyframes": keyframes,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"Metadata: {meta_path}")

    # Compose storyboard
    if not args.no_storyboard:
        storyboard_path = os.path.join(video_dir, f"{video_name}_storyboard.png")
        print(f"Composing storyboard ({args.cols} columns, {args.thumb_width}px thumbnails)...")
        result = compose_storyboard(
            keyframes, storyboard_path, width, height,
            cols=args.cols, thumb_width=args.thumb_width,
        )
        if result:
            size_kb = os.path.getsize(result) / 1024
            print(f"Storyboard: {result} ({size_kb:.0f}KB)")
        else:
            print("Warning: Storyboard composition failed, individual frames are still available")

    print("\nDone.")


if __name__ == "__main__":
    main()
