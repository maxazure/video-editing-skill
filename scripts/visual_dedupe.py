#!/usr/bin/env python3
"""Find repeated visual scenes across source videos without deleting media.

The script samples three frames per candidate scene, computes a compact
perceptual signature with FFmpeg + the Python standard library, and emits a
reviewable JSON/Markdown plan.  Scene candidates can come from
``scene_boundaries.v1`` files or whole videos.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_video_info  # noqa: E402


VERSION = "visual_dedupe.v1"
SAMPLE_FRACTIONS = (0.1, 0.5, 0.9)
FRAME_WIDTH = 9
FRAME_HEIGHT = 8
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _float_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _resolve_path(value: str, *, base_dir: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def sample_times(start: float, end: float) -> List[Dict[str, float]]:
    """Return stable 10/50/90 percent sample points inside one scene."""
    if start < 0 or end <= start:
        raise ValueError("scene range must satisfy 0 <= start < end")
    duration = end - start
    return [
        {"fraction": fraction, "time": round(start + duration * fraction, 6)}
        for fraction in SAMPLE_FRACTIONS
    ]


def frame_signature_from_rgb(raw: bytes) -> Dict[str, Any]:
    """Compute a 64-bit dHash plus mean RGB from one 9x8 RGB frame."""
    if len(raw) < FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} RGB bytes, got {len(raw)}")
    pixels = raw[:FRAME_BYTES]
    rows: List[List[int]] = []
    red = green = blue = 0

    for y in range(FRAME_HEIGHT):
        row: List[int] = []
        for x in range(FRAME_WIDTH):
            offset = (y * FRAME_WIDTH + x) * 3
            r, g, b = pixels[offset : offset + 3]
            red += r
            green += g
            blue += b
            row.append((77 * r + 150 * g + 29 * b) >> 8)
        rows.append(row)

    hash_value = 0
    for row in rows:
        for x in range(FRAME_WIDTH - 1):
            hash_value = (hash_value << 1) | int(row[x] > row[x + 1])

    pixel_count = FRAME_WIDTH * FRAME_HEIGHT
    return {
        "dhash": f"{hash_value:016x}",
        "mean_rgb": [
            round(red / pixel_count, 3),
            round(green / pixel_count, 3),
            round(blue / pixel_count, 3),
        ],
    }


def hamming_distance(hash_a: str, hash_b: str) -> int:
    if len(hash_a) != len(hash_b):
        return 64
    try:
        return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()
    except ValueError:
        return 64


def frame_distance(signature_a: Mapping[str, Any], signature_b: Mapping[str, Any]) -> Dict[str, int]:
    """Return perceptual distance with a color guard for flat frames."""
    hash_distance = hamming_distance(
        str(signature_a.get("dhash") or ""),
        str(signature_b.get("dhash") or ""),
    )
    rgb_a = signature_a.get("mean_rgb") or [0, 0, 0]
    rgb_b = signature_b.get("mean_rgb") or [0, 0, 0]
    if not (
        isinstance(rgb_a, Sequence)
        and isinstance(rgb_b, Sequence)
        and len(rgb_a) >= 3
        and len(rgb_b) >= 3
    ):
        color_distance = 32
    else:
        delta = math.sqrt(
            sum((float(rgb_a[index]) - float(rgb_b[index])) ** 2 for index in range(3))
        )
        color_distance = round(delta / math.sqrt(3 * 255**2) * 32)
    return {
        "distance": max(hash_distance, color_distance),
        "hamming_distance": hash_distance,
        "color_distance": color_distance,
    }


def ffmpeg_signature_command(
    video_path: str,
    timestamp: float,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> List[str]:
    return [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:flags=area",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def extract_frame_signature(
    video_path: str,
    timestamp: float,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout: float = 30.0,
) -> Dict[str, Any]:
    command = ffmpeg_signature_command(video_path, timestamp, ffmpeg_bin=ffmpeg_bin)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg not found: {ffmpeg_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out at {timestamp:.3f}s") from exc

    if result.returncode != 0 or len(result.stdout) < FRAME_BYTES:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail[-500:] or f"could not decode frame at {timestamp:.3f}s")
    return frame_signature_from_rgb(result.stdout)


def _source_scenes(source: Mapping[str, Any], *, base_dir: Path) -> tuple[List[Any], Optional[str]]:
    inline = source.get("scenes")
    if isinstance(inline, list):
        return inline, None

    plan_value = source.get("scene_boundaries")
    if not plan_value:
        return [], None
    plan_path = _resolve_path(str(plan_value), base_dir=base_dir)
    payload = _read_json(plan_path)
    scenes = payload.get("scenes") if isinstance(payload, Mapping) else None
    if not isinstance(scenes, list):
        raise ValueError(f"scene plan has no scenes[]: {plan_path}")
    return scenes, plan_path


def load_candidates(
    *,
    manifest_path: Optional[str] = None,
    videos: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Load whole-video or scene-level candidates from CLI inputs."""
    sources: List[Mapping[str, Any]] = []
    base_dir = Path.cwd()
    if manifest_path:
        manifest = Path(manifest_path).expanduser().resolve()
        payload = _read_json(str(manifest))
        if not isinstance(payload, Mapping) or not isinstance(payload.get("sources"), list):
            raise ValueError("manifest must contain a sources[] array")
        sources.extend(item for item in payload["sources"] if isinstance(item, Mapping))
        base_dir = manifest.parent

    for index, video in enumerate(videos, start=1):
        sources.append({"id": f"source_{index:03d}", "video": str(Path(video).expanduser().resolve())})

    if not sources:
        raise ValueError("provide at least two videos or --manifest with sources[]")

    candidates: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source_index, source in enumerate(sources, start=1):
        video_value = source.get("video", source.get("path"))
        if not video_value:
            raise ValueError(f"source #{source_index} is missing video/path")
        video_path = _resolve_path(str(video_value), base_dir=base_dir)
        if not os.path.isfile(video_path):
            raise ValueError(f"video not found: {video_path}")

        duration, width, height, _fps, _rotation = get_video_info(video_path)
        if duration <= 0:
            raise ValueError(f"video duration is not positive: {video_path}")
        source_id = str(source.get("id", source.get("source_id", Path(video_path).stem)))
        quality_score = _float_or_none(source.get("quality_score"))
        scenes, scene_plan_path = _source_scenes(source, base_dir=base_dir)
        if not scenes:
            scenes = [{"scene_id": "full", "start": 0.0, "end": duration}]

        for scene_index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, Mapping):
                continue
            start = _float_or_none(scene.get("start"))
            end = _float_or_none(scene.get("end"))
            if start is None or end is None or start < 0 or end <= start:
                raise ValueError(f"invalid scene range in {source_id} scene #{scene_index}")
            if start >= duration:
                raise ValueError(f"scene starts after media end in {source_id}: {start:.3f}s")
            end = min(end, duration)
            scene_id = str(scene.get("scene_id") or f"scene_{scene_index:03d}")
            candidate_id = str(scene.get("candidate_id") or f"{source_id}:{scene_id}")
            if candidate_id in seen_ids:
                raise ValueError(f"duplicate candidate id: {candidate_id}")
            seen_ids.add(candidate_id)
            scene_quality = _float_or_none(scene.get("quality_score"))
            candidates.append(
                {
                    "id": candidate_id,
                    "source_id": source_id,
                    "scene_id": scene_id,
                    "video": video_path,
                    "scene_plan": scene_plan_path,
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "duration": round(end - start, 6),
                    "width": int(width),
                    "height": int(height),
                    "file_size": os.path.getsize(video_path),
                    "quality_score": scene_quality if scene_quality is not None else quality_score,
                    "samples": [],
                }
            )

    if len(candidates) < 2:
        raise ValueError("visual dedupe needs at least two scene candidates")
    return candidates


def hash_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout: float = 30.0,
    min_samples: int = 2,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    hashed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        samples: List[Dict[str, Any]] = []
        for sample in sample_times(float(candidate["start"]), float(candidate["end"])):
            try:
                signature = extract_frame_signature(
                    str(candidate["video"]),
                    sample["time"],
                    ffmpeg_bin=ffmpeg_bin,
                    timeout=timeout,
                )
                samples.append({**sample, **signature})
            except RuntimeError as exc:
                errors.append(
                    {
                        "candidate_id": candidate["id"],
                        "fraction": sample["fraction"],
                        "time": sample["time"],
                        "error": str(exc),
                    }
                )
        candidate["samples"] = samples
        candidate["hash_status"] = "ready" if len(samples) >= min_samples else "failed"
        hashed.append(candidate)
    return hashed, errors


def compare_candidates(
    candidate_a: Mapping[str, Any],
    candidate_b: Mapping[str, Any],
    *,
    threshold: int,
    min_matching_samples: int,
    min_duration_ratio: float,
) -> Optional[Dict[str, Any]]:
    duration_a = float(candidate_a.get("duration") or 0)
    duration_b = float(candidate_b.get("duration") or 0)
    if duration_a <= 0 or duration_b <= 0:
        return None
    duration_ratio = min(duration_a, duration_b) / max(duration_a, duration_b)
    if duration_ratio < min_duration_ratio:
        return None

    samples_a = {float(item["fraction"]): item for item in candidate_a.get("samples") or []}
    samples_b = {float(item["fraction"]): item for item in candidate_b.get("samples") or []}
    common_fractions = sorted(set(samples_a) & set(samples_b))
    if len(common_fractions) < min_matching_samples:
        return None

    evidence: List[Dict[str, Any]] = []
    for fraction in common_fractions:
        distance = frame_distance(samples_a[fraction], samples_b[fraction])
        evidence.append(
            {
                "fraction": fraction,
                "time_a": samples_a[fraction]["time"],
                "time_b": samples_b[fraction]["time"],
                **distance,
            }
        )

    distances = [item["distance"] for item in evidence]
    median_distance = float(statistics.median(distances))
    matched_samples = sum(distance <= threshold for distance in distances)
    if matched_samples < min_matching_samples or median_distance > threshold:
        return None
    return {
        "candidate_a": candidate_a["id"],
        "candidate_b": candidate_b["id"],
        "median_distance": round(median_distance, 3),
        "mean_distance": round(statistics.fmean(distances), 3),
        "matched_samples": matched_samples,
        "compared_samples": len(evidence),
        "duration_ratio": round(duration_ratio, 4),
        "similarity": round(1.0 - median_distance / 64.0, 4),
        "sample_evidence": evidence,
    }


def find_duplicate_pairs(
    candidates: Sequence[Mapping[str, Any]],
    *,
    threshold: int = 8,
    min_matching_samples: int = 2,
    min_duration_ratio: float = 0.5,
    include_same_source: bool = False,
) -> List[Dict[str, Any]]:
    pairs: List[Dict[str, Any]] = []
    for index, candidate_a in enumerate(candidates):
        if candidate_a.get("hash_status") != "ready":
            continue
        for candidate_b in candidates[index + 1 :]:
            if candidate_b.get("hash_status") != "ready":
                continue
            if not include_same_source and candidate_a.get("source_id") == candidate_b.get("source_id"):
                continue
            pair = compare_candidates(
                candidate_a,
                candidate_b,
                threshold=threshold,
                min_matching_samples=min_matching_samples,
                min_duration_ratio=min_duration_ratio,
            )
            if pair:
                pairs.append(pair)
    return pairs


def _quality_rank(candidate: Mapping[str, Any]) -> tuple[float, int, int, float]:
    quality = _float_or_none(candidate.get("quality_score"))
    return (
        quality if quality is not None else -1.0,
        int(candidate.get("width") or 0) * int(candidate.get("height") or 0),
        int(candidate.get("file_size") or 0),
        float(candidate.get("duration") or 0),
    )


def group_duplicates(
    candidates: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    candidate_by_id = {str(item["id"]): item for item in candidates}
    parent = {candidate_id: candidate_id for candidate_id in candidate_by_id}

    def find(candidate_id: str) -> str:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    def union(candidate_a: str, candidate_b: str) -> None:
        root_a = find(candidate_a)
        root_b = find(candidate_b)
        if root_a != root_b:
            parent[root_b] = root_a

    for pair in pairs:
        union(str(pair["candidate_a"]), str(pair["candidate_b"]))

    grouped_ids: Dict[str, set[str]] = {}
    for pair in pairs:
        root = find(str(pair["candidate_a"]))
        grouped_ids.setdefault(root, set()).update(
            [str(pair["candidate_a"]), str(pair["candidate_b"])]
        )

    groups: List[Dict[str, Any]] = []
    for group_index, member_ids in enumerate(
        sorted(grouped_ids.values(), key=lambda values: sorted(values)),
        start=1,
    ):
        members = [candidate_by_id[candidate_id] for candidate_id in sorted(member_ids)]
        keep = max(members, key=_quality_rank)
        pair_evidence = [
            dict(pair)
            for pair in pairs
            if str(pair["candidate_a"]) in member_ids and str(pair["candidate_b"]) in member_ids
        ]
        explicit_quality = any(_float_or_none(item.get("quality_score")) is not None for item in members)
        groups.append(
            {
                "group_id": f"duplicate_group_{group_index:03d}",
                "recommended_keep": keep["id"],
                "keep_reason": (
                    "highest quality_score, then resolution/file size"
                    if explicit_quality
                    else "highest resolution, then source file size"
                ),
                "member_ids": [item["id"] for item in members],
                "suggested_exclusions": [item["id"] for item in members if item["id"] != keep["id"]],
                "pair_evidence": pair_evidence,
            }
        )
    return groups


def build_report(
    candidates: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    *,
    threshold: int = 8,
    min_matching_samples: int = 2,
    min_duration_ratio: float = 0.5,
    include_same_source: bool = False,
) -> Dict[str, Any]:
    pairs = find_duplicate_pairs(
        candidates,
        threshold=threshold,
        min_matching_samples=min_matching_samples,
        min_duration_ratio=min_duration_ratio,
        include_same_source=include_same_source,
    )
    groups = group_duplicates(candidates, pairs)
    failed_candidates = sum(item.get("hash_status") != "ready" for item in candidates)
    partial_failures = len(
        {
            str(error.get("candidate_id"))
            for error in errors
            if str(error.get("candidate_id"))
            and str(error.get("candidate_id"))
            not in {
                str(item["id"])
                for item in candidates
                if item.get("hash_status") != "ready"
            }
        }
    )
    blocking = len(groups) + failed_candidates
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": "blocked" if blocking else ("warn" if partial_failures else "ready"),
        "params": {
            "sample_fractions": list(SAMPLE_FRACTIONS),
            "distance_threshold": threshold,
            "min_matching_samples": min_matching_samples,
            "min_duration_ratio": min_duration_ratio,
            "include_same_source": include_same_source,
            "distance_metric": "64-bit dHash guarded by mean-RGB distance",
        },
        "summary": {
            "candidates": len(candidates),
            "hashed": len(candidates) - failed_candidates,
            "failed": failed_candidates,
            "partial_warnings": partial_failures,
            "duplicate_pairs": len(pairs),
            "duplicate_groups": len(groups),
            "suggested_exclusions": sum(len(group["suggested_exclusions"]) for group in groups),
            "blocking": blocking,
        },
        "duplicate_groups": groups,
        "pairs": pairs,
        "candidates": [dict(item) for item in candidates],
        "errors": [dict(item) for item in errors],
        "notes": [
            "This report never deletes, moves, or overwrites source media.",
            "Review pair evidence before excluding a scene; perceptual similarity is not editorial equivalence.",
            "Recommended keep uses explicit quality_score first, then resolution and source file size.",
        ],
    }


def _timecode(value: Any) -> str:
    seconds = max(0.0, float(value or 0))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _media_link(value: Any) -> str:
    path = Path(str(value or ""))
    if not str(value or ""):
        return "-"
    try:
        return f"[{_cell(path.name)}]({path.resolve().as_uri()})"
    except ValueError:
        return f"`{_cell(value)}`"


def emit_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Visual Dedupe Review",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Candidates: {summary.get('candidates', 0)}",
        f"- Duplicate groups: {summary.get('duplicate_groups', 0)}",
        f"- Suggested exclusions: {summary.get('suggested_exclusions', 0)}",
        f"- Failed candidates: {summary.get('failed', 0)}",
        "",
        "> This is a review plan. It does not delete or modify source media.",
        "",
    ]
    candidate_by_id = {
        str(candidate["id"]): candidate for candidate in report.get("candidates") or []
    }

    for group in report.get("duplicate_groups") or []:
        lines.extend(
            [
                f"## {group['group_id']}",
                "",
                f"- Recommended keep: `{_cell(group['recommended_keep'])}`",
                f"- Reason: {_cell(group.get('keep_reason') or '')}",
                f"- Review/exclude candidates: {', '.join(f'`{_cell(item)}`' for item in group.get('suggested_exclusions') or []) or '-'}",
                "",
                "| candidate | source | media | range | duration | quality | resolution |",
                "|---|---|---|---|---:|---:|---:|",
            ]
        )
        for candidate_id in group.get("member_ids") or []:
            candidate = candidate_by_id.get(str(candidate_id), {})
            quality = candidate.get("quality_score")
            lines.append(
                "| `{candidate}` | `{source}` | {media} | {start}–{end} | {duration:.2f}s | {quality} | {width}×{height} |".format(
                    candidate=_cell(candidate_id),
                    source=_cell(candidate.get("source_id") or ""),
                    media=_media_link(candidate.get("video")),
                    start=_timecode(candidate.get("start")),
                    end=_timecode(candidate.get("end")),
                    duration=float(candidate.get("duration") or 0),
                    quality="-" if quality is None else quality,
                    width=candidate.get("width") or 0,
                    height=candidate.get("height") or 0,
                )
            )

        lines.extend(
            [
                "",
                "| pair | median distance | matched samples | duration ratio |",
                "|---|---:|---:|---:|",
            ]
        )
        for pair in group.get("pair_evidence") or []:
            lines.append(
                "| `{a}` ↔ `{b}` | {distance} | {matched}/{compared} | {ratio:.3f} |".format(
                    a=_cell(pair.get("candidate_a") or ""),
                    b=_cell(pair.get("candidate_b") or ""),
                    distance=pair.get("median_distance"),
                    matched=pair.get("matched_samples"),
                    compared=pair.get("compared_samples"),
                    ratio=float(pair.get("duration_ratio") or 0),
                )
            )
        lines.append("")
        for pair in group.get("pair_evidence") or []:
            samples = "; ".join(
                "{fraction:.0f}% {time_a} ↔ {time_b}, d={distance} (hash={hash_distance}, color={color_distance})".format(
                    fraction=float(item.get("fraction") or 0) * 100,
                    time_a=_timecode(item.get("time_a")),
                    time_b=_timecode(item.get("time_b")),
                    distance=item.get("distance"),
                    hash_distance=item.get("hamming_distance"),
                    color_distance=item.get("color_distance"),
                )
                for item in pair.get("sample_evidence") or []
            )
            lines.append(
                f"- `{_cell(pair.get('candidate_a') or '')}` ↔ `{_cell(pair.get('candidate_b') or '')}` samples: {samples or '-'}"
            )
        lines.append("")

    if report.get("errors"):
        lines.extend(
            [
                "## Decode Errors",
                "",
                "| candidate | time | error |",
                "|---|---:|---|",
            ]
        )
        for error in report["errors"]:
            lines.append(
                "| `{}` | {} | {} |".format(
                    _cell(error.get("candidate_id") or ""),
                    _timecode(error.get("time")),
                    _cell(error.get("error") or ""),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Next Step",
            "",
            "Open both linked source ranges for every group, confirm they are editorially interchangeable, then remove the suggested duplicate from the downstream edit plan—not from the source folder. Use `timeline_view.py` at the listed sample times when a closer frame/waveform check is needed.",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find visually repeated scenes across source videos with FFmpeg perceptual signatures."
    )
    parser.add_argument("videos", nargs="*", help="Whole videos to compare when --manifest is not used")
    parser.add_argument("--manifest", help="JSON sources[] manifest; relative paths resolve beside the manifest")
    parser.add_argument("--output", required=True, help="Output visual_dedupe.v1 JSON")
    parser.add_argument("--markdown", help="Optional Markdown review report")
    parser.add_argument("--hamming-threshold", type=int, default=8, help="Maximum combined dHash/color distance (0-64)")
    parser.add_argument("--min-matching-samples", type=int, default=2, help="Required matching 10/50/90%% samples")
    parser.add_argument("--min-duration-ratio", type=float, default=0.5, help="Minimum shorter/longer scene duration ratio")
    parser.add_argument("--include-same-source", action="store_true", help="Also compare scenes from the same source video")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg binary")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-frame FFmpeg timeout in seconds")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when duplicates or unhashable candidates need review")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.hamming_threshold <= 64:
        parser.error("--hamming-threshold must be between 0 and 64")
    if not 1 <= args.min_matching_samples <= len(SAMPLE_FRACTIONS):
        parser.error(f"--min-matching-samples must be between 1 and {len(SAMPLE_FRACTIONS)}")
    if not 0 < args.min_duration_ratio <= 1:
        parser.error("--min-duration-ratio must be in (0, 1]")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        candidates = load_candidates(manifest_path=args.manifest, videos=args.videos)
        hashed, errors = hash_candidates(
            candidates,
            ffmpeg_bin=args.ffmpeg,
            timeout=args.timeout,
            min_samples=args.min_matching_samples,
        )
        report = build_report(
            hashed,
            errors,
            threshold=args.hamming_threshold,
            min_matching_samples=args.min_matching_samples,
            min_duration_ratio=args.min_duration_ratio,
            include_same_source=args.include_same_source,
        )
        write_json(args.output, report)
        if args.markdown:
            write_text(args.markdown, emit_markdown(report))
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    summary = report["summary"]
    print(
        "Visual dedupe: "
        f"{summary['candidates']} candidates, "
        f"{summary['duplicate_groups']} duplicate group(s), "
        f"{summary['failed']} failed"
    )
    if args.strict and summary["blocking"]:
        print(
            "Strict review gate: duplicate groups or decode failures need attention; "
            "exit 2 does not mean source media was modified.",
            file=sys.stderr,
        )
    return 2 if args.strict and summary["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
