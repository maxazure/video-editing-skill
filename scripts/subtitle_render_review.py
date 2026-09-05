#!/usr/bin/env python3
"""Create and verify source-bound subtitle proofs from the final video.

The tool samples high-risk cues from ``subtitle_pack.v1``, extracts a short
context clip and a midpoint frame for each sample from the exact delivery
candidate, and requires a full-speed human review.  It deliberately does not
pretend that OCR or a font cmap proves what the viewer actually sees.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lip_sync_review import (  # noqa: E402
    _canonical_sha256,
    _load_json,
    _media_signature,
    _prepare_output_paths,
    _project_file,
    _project_output,
    _relative,
    _report_id,
    _run_ffmpeg,
    _sha256,
    _write_json,
    _write_text,
    probe_media,
    utc_now,
)


REQUEST_VERSION = "subtitle_render_review_request.v1"
RESPONSE_VERSION = "subtitle_render_review_response.v1"
REPORT_VERSION = "subtitle_render_review.v1"

FULL_PLAYBACK_RESULTS = {"completed", "not_completed"}
FULL_VIDEO_RESULTS = {"pass", "fail", "not_observable"}
VERDICTS = {"pass", "fail"}
PRESENCE_RESULTS = {"visible", "missing", "not_observable"}
TEXT_RESULTS = {"matches", "mismatch", "not_observable"}
READABILITY_RESULTS = {"readable", "unreadable", "not_observable"}
LAYOUT_RESULTS = {"clear", "clipped", "obscured", "not_observable"}
REPAIR_ACTIONS = {
    "none",
    "rerender_captions",
    "fix_text",
    "retime_captions",
    "restyle_captions",
    "reposition_captions",
    "replace_final_master",
}


def _request_id(request: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            key: value
            for key, value in request.items()
            if key not in {"generated_at", "request_id", "response_template"}
        }
    )


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _ensure_distinct(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (left_label, left) in enumerate(items):
        for right_label, right in items[index + 1 :]:
            if _same_file(left, right):
                raise ValueError(
                    f"{right_label} must not overwrite or hardlink {left_label}: {right}"
                )


def _clean_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    return cleaned or "cue"


def load_subtitle_pack(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read subtitle pack: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("version") != "subtitle_pack.v1":
        raise ValueError("subtitle pack must use version subtitle_pack.v1")
    cues = payload.get("cues")
    if not isinstance(cues, list) or not cues:
        raise ValueError("subtitle pack must contain at least one cue")
    return payload


def normalize_cues(pack: Mapping[str, Any], *, media_duration: float) -> List[Dict[str, Any]]:
    if not math.isfinite(media_duration) or media_duration <= 0:
        raise ValueError("final video duration must be positive and finite")
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for position, raw in enumerate(pack.get("cues") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"subtitle cue {position} must be an object")
        cue_id = str(raw.get("index", position)).strip()
        if not cue_id or cue_id in seen:
            raise ValueError(f"subtitle cue index is duplicated or empty: {cue_id!r}")
        seen.add(cue_id)
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"subtitle cue {cue_id} must contain non-empty text")
        try:
            start = round(float(raw.get("start")), 6)
            end = round(float(raw.get("end")), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"subtitle cue {cue_id} start/end must be numbers") from exc
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            raise ValueError(f"subtitle cue {cue_id} must satisfy 0 <= start < end")
        if end > media_duration + 0.05:
            raise ValueError(
                f"subtitle cue {cue_id} ends after the final video "
                f"({end:.3f}s > {media_duration:.3f}s)"
            )
        duration = round(end - start, 6)
        stripped = text.strip()
        visible_chars = sum(1 for character in stripped if not character.isspace())
        normalized.append(
            {
                "cue_id": cue_id,
                "source_position": position,
                "start": start,
                "end": end,
                "duration": duration,
                "text": stripped,
                "visible_characters": visible_chars,
                "characters_per_second": round(visible_chars / duration, 3),
            }
        )
    return sorted(
        normalized,
        key=lambda cue: (cue["start"], cue["end"], cue["source_position"], cue["cue_id"]),
    )


def select_review_cues(
    cues: Sequence[Mapping[str, Any]],
    *,
    max_samples: int = 8,
    explicit_cue_ids: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    if isinstance(max_samples, bool) or not isinstance(max_samples, int) or not 1 <= max_samples <= 24:
        raise ValueError("max_samples must be an integer between 1 and 24")
    if not cues:
        raise ValueError("at least one normalized subtitle cue is required")
    by_id = {str(cue.get("cue_id") or ""): cue for cue in cues}
    explicit = [str(value).strip() for value in explicit_cue_ids]
    if any(not value for value in explicit) or len(set(explicit)) != len(explicit):
        raise ValueError("explicit cue ids must be non-empty and unique")
    unknown = [value for value in explicit if value not in by_id]
    if unknown:
        raise ValueError(f"unknown explicit cue ids: {', '.join(unknown)}")
    if len(explicit) > max_samples:
        raise ValueError("explicit cue ids exceed max_samples")

    selected: Dict[str, Dict[str, Any]] = {}

    def add(cue: Mapping[str, Any], reason: str) -> None:
        cue_id = str(cue.get("cue_id") or "")
        if cue_id in selected:
            if reason not in selected[cue_id]["selection_reasons"]:
                selected[cue_id]["selection_reasons"].append(reason)
            return
        if len(selected) >= max_samples:
            return
        selected[cue_id] = {
            **dict(cue),
            "selection_reasons": [reason],
        }

    for cue_id in explicit:
        add(by_id[cue_id], "explicit")

    timeline_middle = (float(cues[0]["start"]) + float(cues[-1]["end"])) / 2.0
    ranked = (
        (cues[0], "first_cue"),
        (cues[-1], "last_cue"),
        (
            max(cues, key=lambda cue: (int(cue["visible_characters"]), -int(cue["source_position"]))),
            "longest_text",
        ),
        (
            max(cues, key=lambda cue: (float(cue["characters_per_second"]), -int(cue["source_position"]))),
            "highest_cps",
        ),
        (
            min(cues, key=lambda cue: (float(cue["duration"]), int(cue["source_position"]))),
            "shortest_duration",
        ),
        (
            min(
                cues,
                key=lambda cue: (
                    abs(((float(cue["start"]) + float(cue["end"])) / 2.0) - timeline_middle),
                    int(cue["source_position"]),
                ),
            ),
            "timeline_middle",
        ),
    )
    for cue, reason in ranked:
        add(cue, reason)

    if len(selected) < max_samples:
        target_count = min(max_samples, len(cues))
        if target_count == 1:
            even_indices = [0]
        else:
            even_indices = [
                round(index * (len(cues) - 1) / (target_count - 1))
                for index in range(target_count)
            ]
        for index in even_indices:
            add(cues[index], "even_timeline_coverage")
        for cue in cues:
            if len(selected) >= target_count:
                break
            add(cue, "timeline_fill")

    ordered = sorted(
        selected.values(),
        key=lambda cue: (float(cue["start"]), float(cue["end"]), int(cue["source_position"])),
    )
    for ordinal, cue in enumerate(ordered, start=1):
        cue["sample_id"] = f"sample-{ordinal:03d}"
    return ordered


def _still_signature(path: Path) -> Dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,pix_fmt",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise ValueError(f"ffprobe failed for {path}: {detail.splitlines()[-1]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {path}") from exc
    video = next(
        (
            stream
            for stream in payload.get("streams") or []
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video, Mapping):
        raise ValueError(f"evidence frame has no image stream: {path}")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"evidence frame has invalid dimensions: {path}")
    return {
        "width": width,
        "height": height,
        "codec": str(video.get("codec_name") or ""),
        "pixel_format": str(video.get("pix_fmt") or ""),
    }


def _evidence_paths(
    proof_root: Path, sample: Mapping[str, Any]
) -> Tuple[Path, Path]:
    stem = f"{sample.get('sample_id')}_{_clean_id(str(sample.get('cue_id') or 'cue'))}"
    return proof_root / f"{stem}_context.mp4", proof_root / f"{stem}_mid.jpg"


def render_evidence(
    source: Path,
    clip_output: Path,
    frame_output: Path,
    *,
    proof_start: float,
    proof_duration: float,
    midpoint: float,
    force: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for label, output in (("context clip", clip_output), ("midpoint frame", frame_output)):
        if output.exists() and not force:
            raise ValueError(f"refusing to overwrite existing {label} without --force: {output}")
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{proof_start:.6f}",
            "-t",
            f"{proof_duration:.6f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-y",
            "__OUTPUT__",
        ],
        output=clip_output,
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{midpoint:.6f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            "__OUTPUT__",
        ],
        output=frame_output,
    )
    return _media_signature(probe_media(str(clip_output))), _still_signature(frame_output)


def _file_record(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _response_template(request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "request_id": request.get("request_id"),
        "reviewed_by": "",
        "full_playback": "",
        "full_video_verdict": "",
        "full_video_notes": "",
        "reviews": [
            {
                "sample_id": sample.get("sample_id"),
                "cue_id": sample.get("cue_id"),
                "verdict": "",
                "caption_presence": "",
                "text_match": "",
                "readability": "",
                "layout": "",
                "repair_action": "",
                "notes": "",
            }
            for sample in request.get("samples") or []
        ],
    }


def prepare_request(
    *,
    project_dir: str,
    video_path: str,
    subtitle_pack_path: str,
    proof_dir: str,
    max_samples: int = 8,
    explicit_cue_ids: Sequence[str] = (),
    context: float = 0.35,
    force: bool = False,
) -> Dict[str, Any]:
    if not math.isfinite(context) or not 0 <= context <= 2:
        raise ValueError("context must be between 0 and 2 seconds")
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    video = _project_file(video_path, root=root, label="final video")
    subtitle_pack = _project_file(
        subtitle_pack_path, root=root, label="subtitle pack"
    )
    _ensure_distinct({"final video": video, "subtitle pack": subtitle_pack})
    source_media = _media_signature(probe_media(str(video)))
    duration = float(source_media["duration"])
    pack = load_subtitle_pack(subtitle_pack)
    cues = normalize_cues(pack, media_duration=duration)
    selected = select_review_cues(
        cues, max_samples=max_samples, explicit_cue_ids=explicit_cue_ids
    )
    proof_root = _project_output(proof_dir, root=root, label="proof directory")

    prepared: List[Dict[str, Any]] = []
    all_paths: Dict[str, Path] = {
        "final video": video,
        "subtitle pack": subtitle_pack,
    }
    for sample in selected:
        clip_path, frame_path = _evidence_paths(proof_root, sample)
        all_paths[f"{sample['sample_id']} context clip"] = clip_path
        all_paths[f"{sample['sample_id']} midpoint frame"] = frame_path
    _ensure_distinct(all_paths)

    for sample in selected:
        proof_start = max(0.0, float(sample["start"]) - context)
        proof_end = min(duration, float(sample["end"]) + context)
        midpoint = (float(sample["start"]) + float(sample["end"])) / 2.0
        clip_path, frame_path = _evidence_paths(proof_root, sample)
        clip_media, still_media = render_evidence(
            video,
            clip_path,
            frame_path,
            proof_start=proof_start,
            proof_duration=proof_end - proof_start,
            midpoint=midpoint,
            force=force,
        )
        if clip_media["width"] != source_media["width"] or clip_media["height"] != source_media["height"]:
            raise ValueError(f"{sample['sample_id']}: context clip dimensions changed")
        if bool(clip_media["has_audio"]) != bool(source_media["has_audio"]):
            raise ValueError(f"{sample['sample_id']}: context clip audio contract changed")
        if still_media["width"] != source_media["width"] or still_media["height"] != source_media["height"]:
            raise ValueError(f"{sample['sample_id']}: midpoint frame dimensions changed")
        prepared.append(
            {
                **sample,
                "proof_start": round(proof_start, 6),
                "proof_end": round(proof_end, 6),
                "midpoint": round(midpoint, 6),
                "evidence": {
                    "context_clip": {
                        **_file_record(clip_path, root=root),
                        "media": clip_media,
                    },
                    "midpoint_frame": {
                        **_file_record(frame_path, root=root),
                        "image": still_media,
                    },
                },
            }
        )

    request: Dict[str, Any] = {
        "version": REQUEST_VERSION,
        "generated_at": utc_now(),
        "project_dir": str(root),
        "source": {
            **_file_record(video, root=root),
            "media": source_media,
        },
        "subtitle_pack": _file_record(subtitle_pack, root=root),
        "sampling": {
            "algorithm": "explicit+first+last+longest_text+highest_cps+shortest_duration+timeline_middle+even_fill.v1",
            "max_samples": max_samples,
            "explicit_cue_ids": list(explicit_cue_ids),
            "context_seconds": round(context, 6),
        },
        "cue_count": len(cues),
        "samples": prepared,
        "review_protocol": {
            "passes": [
                "Play the exact final video from start to finish at 1x and inspect every rendered caption.",
                "Compare each sampled cue's expected text with its context clip and midpoint frame.",
                "Confirm the caption is present, readable at delivery size, and not clipped or obscured.",
                "Check first, last, longest, fastest, shortest, middle, and evenly distributed cues selected by the report.",
            ],
            "limitations": [
                "Sampled proofs do not replace the required full-video playback pass.",
                "This is a human pixel review contract, not OCR, forced alignment, or automatic legibility scoring.",
                "The report proves which video, subtitle pack, and evidence bytes were reviewed; reviewer labels are not authentication or signatures.",
            ],
        },
    }
    request["request_id"] = _request_id(request)
    request["response_template"] = _response_template(request)
    return request


def _sample_core(sample: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: sample.get(key)
        for key in (
            "cue_id",
            "source_position",
            "start",
            "end",
            "duration",
            "text",
            "visible_characters",
            "characters_per_second",
            "selection_reasons",
            "sample_id",
            "proof_start",
            "proof_end",
            "midpoint",
        )
    }


def verify_request(
    request: Mapping[str, Any], project_dir: Optional[str] = None
) -> Dict[str, Any]:
    blockers: List[str] = []
    stored_root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
    root = Path(project_dir).expanduser().resolve() if project_dir else stored_root
    if request.get("version") != REQUEST_VERSION:
        blockers.append(f"request version must be {REQUEST_VERSION}")
    if not root.is_dir():
        blockers.append(f"project directory is missing: {root}")
    if stored_root != root:
        blockers.append("request project_dir does not match the verification project")
    if str(request.get("request_id") or "") != _request_id(request):
        blockers.append("request_id does not match canonical request content")
    if request.get("response_template") != _response_template(request):
        blockers.append("response_template does not match the canonical request")

    source = request.get("source") or {}
    subtitle = request.get("subtitle_pack") or {}
    video: Optional[Path] = None
    subtitle_path: Optional[Path] = None
    live_media: Dict[str, Any] = {}
    try:
        video = _project_file(str(source.get("path") or ""), root=root, label="final video")
    except ValueError as exc:
        blockers.append(str(exc))
    if video is not None:
        if _sha256(video) != str(source.get("sha256") or ""):
            blockers.append("final video bytes changed after proof preparation")
        if video.stat().st_size != int(source.get("size_bytes") or -1):
            blockers.append("final video size changed after proof preparation")
        try:
            live_media = _media_signature(probe_media(str(video)))
        except (TypeError, ValueError) as exc:
            blockers.append(str(exc))
        else:
            if live_media != _media_signature(source.get("media") or {}):
                blockers.append("final video media contract changed after proof preparation")

    try:
        subtitle_path = _project_file(
            str(subtitle.get("path") or ""), root=root, label="subtitle pack"
        )
    except ValueError as exc:
        blockers.append(str(exc))
    if subtitle_path is not None:
        if _sha256(subtitle_path) != str(subtitle.get("sha256") or ""):
            blockers.append("subtitle pack bytes changed after proof preparation")
        if subtitle_path.stat().st_size != int(subtitle.get("size_bytes") or -1):
            blockers.append("subtitle pack size changed after proof preparation")

    samples = request.get("samples") or []
    if not isinstance(samples, list) or not samples:
        blockers.append("request must contain at least one subtitle sample")
        samples = []
    settings = request.get("sampling") or {}
    try:
        raw_max_samples = settings.get("max_samples")
        if isinstance(raw_max_samples, bool) or not isinstance(raw_max_samples, int):
            raise ValueError("max_samples must be an integer between 1 and 24")
        max_samples = raw_max_samples
        raw_context = settings.get("context_seconds")
        if isinstance(raw_context, bool) or not isinstance(raw_context, (int, float)):
            raise ValueError("context_seconds must be a number between 0 and 2")
        context = float(raw_context)
        explicit = settings.get("explicit_cue_ids") or []
        if not isinstance(explicit, list) or any(
            not isinstance(value, str) for value in explicit
        ):
            raise ValueError("explicit_cue_ids must be a list of strings")
        if settings.get("algorithm") != "explicit+first+last+longest_text+highest_cps+shortest_duration+timeline_middle+even_fill.v1":
            raise ValueError("sampling algorithm contract changed")
        if not math.isfinite(context) or not 0 <= context <= 2:
            raise ValueError("context_seconds must be between 0 and 2")
        if subtitle_path is None:
            raise ValueError("subtitle pack is unavailable")
        duration = float((source.get("media") or {}).get("duration") or 0)
        live_cues = normalize_cues(load_subtitle_pack(subtitle_path), media_duration=duration)
        expected = select_review_cues(
            live_cues, max_samples=max_samples, explicit_cue_ids=explicit
        )
        for sample in expected:
            sample["proof_start"] = round(max(0.0, float(sample["start"]) - context), 6)
            sample["proof_end"] = round(min(duration, float(sample["end"]) + context), 6)
            sample["midpoint"] = round(
                (float(sample["start"]) + float(sample["end"])) / 2.0, 6
            )
        if int(request.get("cue_count") or -1) != len(live_cues):
            blockers.append("cue_count does not match the live subtitle pack")
        if [_sample_core(sample) for sample in samples] != [
            _sample_core(sample) for sample in expected
        ]:
            blockers.append("sample selection does not match the live subtitle pack and settings")
    except (AttributeError, TypeError, ValueError) as exc:
        blockers.append(f"sample selection cannot be rebuilt: {exc}")

    seen_paths: Dict[str, Path] = {}
    if video is not None:
        seen_paths["final video"] = video
    if subtitle_path is not None:
        seen_paths["subtitle pack"] = subtitle_path
    source_duration = float((source.get("media") or {}).get("duration") or 0)
    source_width = int((source.get("media") or {}).get("width") or 0)
    source_height = int((source.get("media") or {}).get("height") or 0)
    source_has_audio = bool((source.get("media") or {}).get("has_audio"))
    for sample in samples:
        if not isinstance(sample, Mapping):
            blockers.append("subtitle samples must be objects")
            continue
        sample_id = str(sample.get("sample_id") or "")
        evidence = sample.get("evidence") or {}
        if not sample_id or not isinstance(evidence, Mapping) or set(evidence) != {
            "context_clip",
            "midpoint_frame",
        }:
            blockers.append(f"{sample_id or 'sample'}: evidence contract is incomplete")
            continue
        for label in ("context_clip", "midpoint_frame"):
            record = evidence.get(label) or {}
            try:
                path = _project_file(
                    str(record.get("path") or ""),
                    root=root,
                    label=f"{sample_id} {label}",
                )
            except ValueError as exc:
                blockers.append(str(exc))
                continue
            for prior_label, prior_path in seen_paths.items():
                if _same_file(path, prior_path):
                    blockers.append(
                        f"{sample_id} {label} must not overwrite or hardlink {prior_label}"
                    )
            seen_paths[f"{sample_id} {label}"] = path
            if _sha256(path) != str(record.get("sha256") or ""):
                blockers.append(f"{sample_id}: {label} bytes changed")
            if path.stat().st_size != int(record.get("size_bytes") or -1):
                blockers.append(f"{sample_id}: {label} size changed")
            try:
                if label == "context_clip":
                    live = _media_signature(probe_media(str(path)))
                    if live != _media_signature(record.get("media") or {}):
                        blockers.append(f"{sample_id}: context clip media contract changed")
                    if live["width"] != source_width or live["height"] != source_height:
                        blockers.append(f"{sample_id}: context clip dimensions differ from final video")
                    if bool(live["has_audio"]) != source_has_audio:
                        blockers.append(f"{sample_id}: context clip audio differs from final video")
                    proof_start = float(sample.get("proof_start"))
                    proof_end = float(sample.get("proof_end"))
                    expected_duration = proof_end - proof_start
                    tolerance = max(0.12, 2.0 / max(float(live["fps"]), 1.0))
                    if abs(float(live["duration"]) - expected_duration) > tolerance:
                        blockers.append(f"{sample_id}: context clip duration does not match proof range")
                else:
                    live_still = _still_signature(path)
                    if live_still != dict(record.get("image") or {}):
                        blockers.append(f"{sample_id}: midpoint frame media contract changed")
                    if live_still["width"] != source_width or live_still["height"] != source_height:
                        blockers.append(f"{sample_id}: midpoint frame dimensions differ from final video")
            except (TypeError, ValueError) as exc:
                blockers.append(f"{sample_id}: {label} cannot be verified: {exc}")
        try:
            if not (
                0 <= float(sample.get("proof_start")) <= float(sample.get("start"))
                < float(sample.get("end")) <= float(sample.get("proof_end"))
                <= source_duration + 0.05
            ):
                blockers.append(f"{sample_id}: invalid cue/proof timing contract")
        except (TypeError, ValueError):
            blockers.append(f"{sample_id}: cue/proof timing values must be numbers")

    unique = sorted(set(blockers))
    return {
        "status": "blocked" if unique else "ready",
        "blockers": unique,
        "summary": {
            "samples": len(samples),
            "blocking": len(unique),
            "warnings": 0,
        },
    }


def _choice(value: Any, *, allowed: set[str], label: str, errors: List[str]) -> str:
    result = str(value or "").strip()
    if result not in allowed:
        errors.append(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return result


def audit_response(request: Mapping[str, Any], response: Mapping[str, Any]) -> Dict[str, Any]:
    request_check = verify_request(request)
    blockers = list(request_check["blockers"])
    if response.get("version") != RESPONSE_VERSION:
        blockers.append(f"response version must be {RESPONSE_VERSION}")
    if str(response.get("request_id") or "") != str(request.get("request_id") or ""):
        blockers.append("response request_id does not match the review request")
    reviewed_by = str(response.get("reviewed_by") or "").strip()
    if not reviewed_by:
        blockers.append("reviewed_by must not be empty")

    global_errors: List[str] = []
    full_playback = _choice(
        response.get("full_playback"),
        allowed=FULL_PLAYBACK_RESULTS,
        label="full_playback",
        errors=global_errors,
    )
    full_verdict = _choice(
        response.get("full_video_verdict"),
        allowed=FULL_VIDEO_RESULTS,
        label="full_video_verdict",
        errors=global_errors,
    )
    full_notes = str(response.get("full_video_notes") or "").strip()
    if full_playback != "completed":
        global_errors.append("full_playback must be completed")
    if full_verdict != "pass":
        global_errors.append("full_video_verdict must be pass")
    if full_verdict in {"fail", "not_observable"} and not full_notes:
        global_errors.append("failed or unobservable full-video review requires notes")
    blockers.extend(global_errors)

    raw_reviews = response.get("reviews") or []
    if not isinstance(raw_reviews, list):
        blockers.append("response reviews must be a list")
        raw_reviews = []
    by_id: Dict[str, Mapping[str, Any]] = {}
    for raw in raw_reviews:
        if not isinstance(raw, Mapping):
            blockers.append("response reviews must contain objects")
            continue
        sample_id = str(raw.get("sample_id") or "")
        if not sample_id or sample_id in by_id:
            blockers.append(f"duplicate or empty response sample id: {sample_id!r}")
            continue
        by_id[sample_id] = raw

    normalized: List[Dict[str, Any]] = []
    expected_ids: List[str] = []
    for sample in request.get("samples") or []:
        sample_id = str(sample.get("sample_id") or "")
        cue_id = str(sample.get("cue_id") or "")
        expected_ids.append(sample_id)
        raw = by_id.get(sample_id)
        if raw is None:
            blockers.append(f"missing review for sample: {sample_id}")
            continue
        errors: List[str] = []
        if str(raw.get("cue_id") or "") != cue_id:
            errors.append("cue_id does not match the request sample")
        verdict = _choice(raw.get("verdict"), allowed=VERDICTS, label="verdict", errors=errors)
        presence = _choice(
            raw.get("caption_presence"),
            allowed=PRESENCE_RESULTS,
            label="caption_presence",
            errors=errors,
        )
        text_match = _choice(
            raw.get("text_match"), allowed=TEXT_RESULTS, label="text_match", errors=errors
        )
        readability = _choice(
            raw.get("readability"),
            allowed=READABILITY_RESULTS,
            label="readability",
            errors=errors,
        )
        layout = _choice(
            raw.get("layout"), allowed=LAYOUT_RESULTS, label="layout", errors=errors
        )
        repair = _choice(
            raw.get("repair_action"),
            allowed=REPAIR_ACTIONS,
            label="repair_action",
            errors=errors,
        )
        notes = str(raw.get("notes") or "").strip()
        checks_pass = (
            presence == "visible"
            and text_match == "matches"
            and readability == "readable"
            and layout == "clear"
        )
        if verdict == "pass" and not checks_pass:
            errors.append("pass requires visible, matching, readable, and clear caption pixels")
        if verdict == "pass" and repair != "none":
            errors.append("pass requires repair_action=none")
        if verdict == "fail" and repair == "none":
            errors.append("fail requires a concrete repair_action")
        if verdict == "fail" and not notes:
            errors.append("fail requires notes describing the observed evidence")
        if errors:
            blockers.extend(f"{sample_id}: {error}" for error in errors)
        elif verdict == "fail":
            blockers.append(f"{sample_id}: subtitle render review failed; action={repair}")
        normalized.append(
            {
                "sample_id": sample_id,
                "cue_id": cue_id,
                "verdict": verdict,
                "caption_presence": presence,
                "text_match": text_match,
                "readability": readability,
                "layout": layout,
                "repair_action": repair,
                "notes": notes,
            }
        )
    extras = sorted(set(by_id) - set(expected_ids))
    if extras:
        blockers.append(f"response contains unknown samples: {', '.join(extras)}")

    unique = sorted(set(blockers))
    report: Dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": utc_now(),
        "request": dict(request),
        "response": dict(response),
        "reviews": normalized,
        "status": "blocked" if unique else "ready",
        "summary": {
            "samples": len(request.get("samples") or []),
            "passed": sum(1 for review in normalized if review["verdict"] == "pass"),
            "failed": sum(1 for review in normalized if review["verdict"] == "fail"),
            "full_playback_completed": full_playback == "completed",
            "blocking": len(unique),
            "warnings": 0,
        },
        "blockers": unique,
        "warnings": [],
        "notes": [
            "Readiness requires both sampled pixel proofs and a complete 1x pass of the exact final video.",
            "Reviewer labels are self-reported and are not identity authentication or digital signatures.",
        ],
    }
    report["report_id"] = _report_id(report)
    return report


def verify_report(
    report: Mapping[str, Any], project_dir: Optional[str] = None
) -> Dict[str, Any]:
    blockers: List[str] = []
    if report.get("version") != REPORT_VERSION:
        blockers.append(f"report version must be {REPORT_VERSION}")
    if str(report.get("report_id") or "") != _report_id(report):
        blockers.append("report_id does not match canonical report content")
    request = report.get("request") or {}
    response = report.get("response") or {}
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        blockers.append("report request and response must be objects")
    else:
        try:
            canonical = audit_response(request, response)
            if project_dir:
                live = verify_request(request, project_dir)
                if live["blockers"]:
                    canonical["blockers"] = sorted(
                        set(canonical.get("blockers") or []) | set(live["blockers"])
                    )
                    canonical["summary"]["blocking"] = len(canonical["blockers"])
                    canonical["status"] = "blocked"
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(f"report cannot be re-audited: {exc}")
        else:
            for key in ("reviews", "status", "summary", "blockers", "warnings", "notes"):
                if report.get(key) != canonical.get(key):
                    blockers.append(f"report {key} does not match canonical audit state")
    unique = sorted(set(blockers))
    try:
        stored = report.get("summary") or {}
        stored_blocking = int(stored.get("blocking") or 0)
        stored_warnings = int(stored.get("warnings") or 0)
        stored_samples = int(stored.get("samples") or 0)
    except (AttributeError, TypeError, ValueError):
        unique.append("report summary counters must be integers")
        unique = sorted(set(unique))
        stored_blocking = 0
        stored_warnings = 0
        stored_samples = 0
    return {
        "status": "blocked" if unique or stored_blocking else "ready",
        "blockers": unique,
        "summary": {
            "samples": stored_samples,
            "blocking": len(unique) + stored_blocking,
            "warnings": stored_warnings,
        },
    }


def emit_request_markdown(request: Mapping[str, Any]) -> str:
    lines = [
        "# Subtitle Render Review Request",
        "",
        f"- Request ID: `{request.get('request_id', '')}`",
        f"- Final video: `{(request.get('source') or {}).get('path', '')}`",
        f"- Subtitle pack: `{(request.get('subtitle_pack') or {}).get('path', '')}`",
        f"- Cues: {request.get('cue_count', 0)}",
        f"- Samples: {len(request.get('samples') or [])}",
        "",
        "## Required review",
        "",
    ]
    lines.extend(f"- {item}" for item in (request.get("review_protocol") or {}).get("passes") or [])
    lines.extend(["", "## Sampled cues", ""])
    for sample in request.get("samples") or []:
        evidence = sample.get("evidence") or {}
        lines.extend(
            [
                f"### {sample.get('sample_id')} / cue {sample.get('cue_id')}",
                "",
                f"- Time: `{float(sample.get('start') or 0):.3f}s–{float(sample.get('end') or 0):.3f}s`",
                f"- Selected for: `{', '.join(sample.get('selection_reasons') or [])}`",
                f"- Expected text: {sample.get('text', '')}",
                f"- Context clip: `{(evidence.get('context_clip') or {}).get('path', '')}`",
                f"- Midpoint frame: `{(evidence.get('midpoint_frame') or {}).get('path', '')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Decision rule",
            "",
            "A pass requires the complete final video to be watched at 1x, plus visible, matching, readable, unclipped and unobscured caption pixels for every sample. Missing or unobservable evidence fails closed.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_report_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Subtitle Render Review Report",
        "",
        f"- Status: **{str(report.get('status') or '').upper()}**",
        f"- Report ID: `{report.get('report_id', '')}`",
        f"- Full playback completed: {summary.get('full_playback_completed', False)}",
        f"- Samples: {summary.get('samples', 0)}",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "## Reviews",
        "",
    ]
    for review in report.get("reviews") or []:
        lines.append(
            f"- `{review.get('sample_id')}` / cue `{review.get('cue_id')}` — "
            f"**{str(review.get('verdict') or '').upper()}**; "
            f"presence={review.get('caption_presence')}, text={review.get('text_match')}, "
            f"readability={review.get('readability')}, layout={review.get('layout')}, "
            f"action={review.get('repair_action')}"
        )
    if report.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.get("blockers") or [])
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify source-bound subtitle pixel reviews from a final video."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="Sample subtitle cues and extract final-video proof clips and frames."
    )
    prepare.add_argument("--project-dir", default=".")
    prepare.add_argument("--video", required=True, help="Final delivery candidate inside the project.")
    prepare.add_argument("--subtitle-pack", required=True, help="subtitle_pack.v1 JSON inside the project.")
    prepare.add_argument("--proof-dir", default="verify/subtitle_render")
    prepare.add_argument("--max-samples", type=int, default=8)
    prepare.add_argument("--cue-id", action="append", default=[], help="Always sample this cue index.")
    prepare.add_argument("--context", type=float, default=0.35)
    prepare.add_argument("--output", default="work/subtitle_render_review_request.json")
    prepare.add_argument("--markdown", default="work/subtitle_render_review_request.md")
    prepare.add_argument("--response-template", default="work/subtitle_render_review_response.json")
    prepare.add_argument("--force", action="store_true")

    audit = subparsers.add_parser(
        "audit", help="Audit a completed human response against the exact proofs."
    )
    audit.add_argument("--request", required=True)
    audit.add_argument("--response", required=True)
    audit.add_argument("--output", default="work/subtitle_render_review.json")
    audit.add_argument("--markdown", default="work/subtitle_render_review.md")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--strict", action="store_true")

    verify = subparsers.add_parser(
        "verify", help="Re-read the final video, subtitle pack, and evidence and verify a report."
    )
    verify.add_argument("--report", required=True)
    verify.add_argument("--project-dir", default=None)
    verify.add_argument("--strict", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            root = Path(args.project_dir).expanduser().resolve()
            source = _project_file(args.video, root=root, label="final video")
            subtitle = _project_file(args.subtitle_pack, root=root, label="subtitle pack")
            source_media = _media_signature(probe_media(str(source)))
            cues = normalize_cues(
                load_subtitle_pack(subtitle), media_duration=float(source_media["duration"])
            )
            selected = select_review_cues(
                cues, max_samples=args.max_samples, explicit_cue_ids=args.cue_id
            )
            proof_root = _project_output(args.proof_dir, root=root, label="proof directory")
            predicted = [path for sample in selected for path in _evidence_paths(proof_root, sample)]
            outputs = _prepare_output_paths(
                root=root,
                raw_paths=(
                    ("request output", args.output),
                    ("request Markdown", args.markdown),
                    ("response template", args.response_template),
                ),
                forbidden=(source, subtitle, *predicted),
            )
            _ensure_distinct(
                {
                    "final video": source,
                    "subtitle pack": subtitle,
                    **{label: path for label, path in outputs.items()},
                    **{f"evidence {index}": path for index, path in enumerate(predicted)},
                }
            )
            for path in outputs.values():
                if path.exists() and not args.force:
                    raise ValueError(f"refusing to overwrite existing file without --force: {path}")
            request = prepare_request(
                project_dir=str(root),
                video_path=args.video,
                subtitle_pack_path=args.subtitle_pack,
                proof_dir=args.proof_dir,
                max_samples=args.max_samples,
                explicit_cue_ids=args.cue_id,
                context=args.context,
                force=args.force,
            )
            _write_json(outputs["request output"], request, force=args.force)
            _write_text(outputs["request Markdown"], emit_request_markdown(request), force=args.force)
            _write_json(
                outputs["response template"], request["response_template"], force=args.force
            )
            print(
                f"Subtitle render review request: pending cues={request['cue_count']} "
                f"samples={len(request['samples'])} request_id={request['request_id']}"
            )
            return 0

        if args.command == "audit":
            request = _load_json(args.request)
            response = _load_json(args.response)
            root = Path(str(request.get("project_dir") or "")).expanduser().resolve()
            forbidden = [
                _project_file(args.request, root=root, label="request input"),
                _project_file(args.response, root=root, label="response input"),
                _project_file(
                    str((request.get("source") or {}).get("path") or ""),
                    root=root,
                    label="final video",
                ),
                _project_file(
                    str((request.get("subtitle_pack") or {}).get("path") or ""),
                    root=root,
                    label="subtitle pack",
                ),
            ]
            forbidden.extend(
                _project_file(
                    str(record.get("path") or ""), root=root, label="evidence input"
                )
                for sample in request.get("samples") or []
                for record in (sample.get("evidence") or {}).values()
            )
            outputs = _prepare_output_paths(
                root=root,
                raw_paths=(("report output", args.output), ("report Markdown", args.markdown)),
                forbidden=tuple(forbidden),
            )
            _ensure_distinct(
                {
                    **{f"input {index}": path for index, path in enumerate(forbidden)},
                    **{label: path for label, path in outputs.items()},
                }
            )
            report = audit_response(request, response)
            _write_json(outputs["report output"], report, force=args.force)
            _write_text(outputs["report Markdown"], emit_report_markdown(report), force=args.force)
            print(
                f"Subtitle render review: {report['status']} "
                f"blocking={report['summary']['blocking']} "
                f"passed={report['summary']['passed']}/{report['summary']['samples']}"
            )
            return 2 if args.strict and report["status"] == "blocked" else 0

        report = _load_json(args.report)
        verification = verify_report(report, args.project_dir)
        print(
            f"Subtitle render review verify: {verification['status']} "
            f"blocking={verification['summary']['blocking']}"
        )
        for blocker in verification["blockers"]:
            print(f"- {blocker}")
        return 2 if args.strict and verification["status"] == "blocked" else 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
