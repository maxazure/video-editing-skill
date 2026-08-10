#!/usr/bin/env python3
"""Plan, render, and verify explicit J-cut/L-cut audio transitions.

The visual timeline remains a hard-cut sequence. Only the primary clip audio is
shifted: a J-cut reveals the incoming clip's audio handle before picture-in; an
L-cut keeps the outgoing audio handle after picture-out and resumes the incoming
clip after the overlap. Nothing is inferred automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "audio_transition_plan.v1"
VERIFY_VERSION = "audio_transition_verification.v1"
APPLY_VERSION = "audio_transition_apply.v1"
KINDS = {"j_cut", "l_cut"}
MIN_DURATION = 0.05
MAX_DURATION = 2.0
DEFAULT_EDGE_FADE = 0.03
ROUND_DIGITS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite existing file: {output}") from exc


def _write_text(path: str, value: str) -> None:
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite existing file: {output}") from exc


def _validate_new_targets(paths: Sequence[str]) -> None:
    resolved: Dict[Path, str] = {}
    for raw in paths:
        target = Path(raw).expanduser().resolve()
        if target in resolved:
            raise ValueError(f"output paths must be distinct: {raw} and {resolved[target]}")
        if target.exists() or target.is_symlink():
            raise ValueError(f"refusing to overwrite existing file: {target}")
        resolved[target] = raw


def load_plan(path: str) -> Dict[str, Any]:
    plan_path = Path(path).expanduser()
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read audio-transition plan: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("audio-transition plan root must be an object")
    return payload


def _fraction(value: Any) -> float:
    text = str(value or "0")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / float(denominator)
    return float(text)


def probe_media(path: str) -> Dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"media file does not exist: {source}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed for {source}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {source}: {exc}") from exc

    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video, Mapping):
        raise ValueError(f"media has no video stream: {source}")
    raw_duration = (payload.get("format") or {}).get("duration") or video.get("duration")
    try:
        duration = float(raw_duration)
        fps = _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid media duration/fps for {source}: {exc}") from exc
    if duration <= 0 or fps <= 0:
        raise ValueError(f"media duration/fps must be positive: {source}")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    return {
        "duration": _round(duration),
        "fps": _round(fps),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": str(video.get("codec_name") or ""),
        "has_audio": audio is not None,
        "audio_codec": str(audio.get("codec_name") or "") if audio else "",
    }


def parse_transition(value: str) -> Dict[str, Any]:
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 3:
        raise ValueError("--transition expects AFTER_CLIP,TYPE,DURATION")
    try:
        after_clip = int(parts[0])
        duration = float(parts[2])
    except ValueError as exc:
        raise ValueError("transition boundary must be an integer and duration must be numeric") from exc
    aliases = {"j": "j_cut", "j-cut": "j_cut", "l": "l_cut", "l-cut": "l_cut"}
    kind = aliases.get(parts[1].lower(), parts[1].lower().replace("-", "_"))
    return {"after_clip": after_clip, "kind": kind, "duration": duration}


def normalize_transitions(raw_items: Sequence[Mapping[str, Any]], clip_count: int) -> List[Dict[str, Any]]:
    if clip_count < 2:
        raise ValueError("J-cut/L-cut planning requires at least two clips")
    if not raw_items:
        raise ValueError("at least one --transition is required")
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_items:
        try:
            after_clip = int(raw.get("after_clip"))
            duration = float(raw.get("duration"))
        except (TypeError, ValueError) as exc:
            raise ValueError("transition boundary/duration is invalid") from exc
        kind = str(raw.get("kind") or "").lower().replace("-", "_")
        if kind not in KINDS:
            raise ValueError("transition type must be j_cut or l_cut")
        if after_clip < 1 or after_clip >= clip_count:
            raise ValueError(f"after_clip must be between 1 and {clip_count - 1}")
        if not math.isfinite(duration) or not MIN_DURATION <= duration <= MAX_DURATION:
            raise ValueError(f"transition duration must be between {MIN_DURATION:g}s and {MAX_DURATION:g}s")
        if after_clip in seen:
            raise ValueError(f"duplicate transition for boundary after clip {after_clip}")
        seen.add(after_clip)
        normalized.append(
            {
                "id": "",
                "after_clip": after_clip,
                "kind": kind,
                "duration": _round(duration),
            }
        )
    normalized.sort(key=lambda item: item["after_clip"])
    for index, item in enumerate(normalized, start=1):
        item["id"] = f"audio-transition-{index:03d}"
    return normalized


def _resolve_path(raw: Any, working_directory: Path) -> Path:
    path = Path(str(raw or "")).expanduser()
    if not path.is_absolute():
        path = working_directory / path
    return path.resolve()


def _file_record(path: Path, *, media: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if media is not None:
        record["media"] = dict(media)
    return record


def resolve_config(config_path: str, *, working_directory: Optional[str] = None) -> Dict[str, Any]:
    config_file = Path(config_path).expanduser().resolve()
    if not config_file.is_file():
        raise ValueError(f"render config does not exist: {config_file}")
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"render config is invalid JSON: {exc}") from exc
    if not isinstance(config, Mapping) or not isinstance(config.get("clips"), list):
        raise ValueError("render config must contain clips[]")
    if len(config["clips"]) < 2:
        raise ValueError("render config must contain at least two clips")

    workdir = Path(working_directory or os.getcwd()).expanduser().resolve()
    transcript_cache: Dict[str, Mapping[str, Any]] = {}
    transcript_records: Dict[str, Dict[str, Any]] = {}
    media_cache: Dict[str, Dict[str, Any]] = {}
    source_records: Dict[str, Dict[str, Any]] = {}
    clips: List[Dict[str, Any]] = []
    cursor = 0.0

    for index, entry in enumerate(config["clips"], start=1):
        if not isinstance(entry, Mapping):
            raise ValueError(f"clip {index} must be an object")
        for field in ("video", "transcript", "segment_id"):
            if field not in entry:
                raise ValueError(f"clip {index} is missing {field}")
        video = _resolve_path(entry["video"], workdir)
        transcript = _resolve_path(entry["transcript"], workdir)
        if not video.is_file():
            raise ValueError(f"clip {index} video does not exist: {video}")
        if not transcript.is_file():
            raise ValueError(f"clip {index} transcript does not exist: {transcript}")

        transcript_key = str(transcript)
        if transcript_key not in transcript_cache:
            try:
                transcript_payload = json.loads(transcript.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid transcript JSON {transcript}: {exc}") from exc
            segments = transcript_payload.get("segments") if isinstance(transcript_payload, Mapping) else None
            if not isinstance(segments, list):
                raise ValueError(f"transcript must contain segments[]: {transcript}")
            transcript_cache[transcript_key] = {
                str(item.get("id")): item
                for item in segments
                if isinstance(item, Mapping) and item.get("id") is not None
            }
            transcript_records[transcript_key] = _file_record(transcript)

        segment = transcript_cache[transcript_key].get(str(entry["segment_id"]))
        if not isinstance(segment, Mapping):
            raise ValueError(f"clip {index} segment_id {entry['segment_id']} was not found")
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"clip {index} segment timing is invalid") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"clip {index} needs 0 <= start < end")

        video_key = str(video)
        if video_key not in media_cache:
            media_cache[video_key] = probe_media(video_key)
            source_records[video_key] = _file_record(video, media=media_cache[video_key])
        media = media_cache[video_key]
        if not media["has_audio"]:
            raise ValueError(f"clip {index} source has no audio stream: {video}")
        if end > float(media["duration"]) + 0.05:
            raise ValueError(f"clip {index} ends after source duration")

        broll_path = None
        broll_start = 0.0
        if entry.get("broll"):
            broll = _resolve_path(entry["broll"], workdir)
            if not broll.is_file():
                raise ValueError(f"clip {index} B-roll does not exist: {broll}")
            broll_key = str(broll)
            if broll_key not in media_cache:
                media_cache[broll_key] = probe_media(broll_key)
                source_records[broll_key] = _file_record(broll, media=media_cache[broll_key])
            broll_start = float(entry.get("broll_start", 0.0))
            if broll_start < 0 or broll_start + (end - start) > float(media_cache[broll_key]["duration"]) + 0.05:
                raise ValueError(f"clip {index} B-roll does not cover the clip duration")
            broll_path = broll_key

        duration = end - start
        clip = {
            "index": index,
            "video": video_key,
            "transcript": transcript_key,
            "segment_id": entry["segment_id"],
            "text_sha256": hashlib.sha256(str(segment.get("text") or "").encode("utf-8")).hexdigest(),
            "source_start": _round(start),
            "source_end": _round(end),
            "duration": _round(duration),
            "output_start": _round(cursor),
            "output_end": _round(cursor + duration),
        }
        if broll_path:
            clip["broll"] = broll_path
            clip["broll_start"] = _round(broll_start)
        clips.append(clip)
        cursor += duration

    return {
        "config": {
            **_file_record(config_file),
            "working_directory": str(workdir),
        },
        "sources": [source_records[key] for key in sorted(source_records)],
        "transcripts": [transcript_records[key] for key in sorted(transcript_records)],
        "clips": clips,
        "timeline_duration": _round(cursor),
        "media": media_cache,
    }


def compile_audio_layers(
    clips: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
    *,
    media_by_path: Mapping[str, Mapping[str, Any]],
    edge_fade: float,
) -> List[Dict[str, Any]]:
    transition_by_boundary = {int(item["after_clip"]): item for item in transitions}
    layers: List[Dict[str, Any]] = []
    for position, clip in enumerate(clips, start=1):
        incoming = transition_by_boundary.get(position - 1)
        outgoing = transition_by_boundary.get(position)
        source_start = float(clip["source_start"])
        source_end = float(clip["source_end"])
        output_start = float(clip["output_start"])
        output_end = float(clip["output_end"])
        fade_in = edge_fade
        fade_out = edge_fade

        if incoming and incoming["kind"] == "j_cut":
            duration = float(incoming["duration"])
            source_start -= duration
            output_start -= duration
            fade_in = duration
        elif incoming and incoming["kind"] == "l_cut":
            duration = float(incoming["duration"])
            source_start += duration
            output_start += duration

        if outgoing and outgoing["kind"] == "l_cut":
            duration = float(outgoing["duration"])
            source_end += duration
            output_end += duration
            fade_out = duration
        elif outgoing and outgoing["kind"] == "j_cut":
            fade_out = float(outgoing["duration"])

        source_media = media_by_path[str(clip["video"])]
        if source_start < -1e-6:
            raise ValueError(
                f"J-cut after clip {position - 1} needs more incoming audio handle before clip {position}"
            )
        if source_end > float(source_media["duration"]) + 0.01:
            raise ValueError(f"L-cut after clip {position} needs more outgoing audio handle")
        if output_start < -1e-6:
            raise ValueError(f"transition before clip {position} starts before output time zero")
        layer_duration = source_end - source_start
        if layer_duration < max(MIN_DURATION, edge_fade * 2):
            raise ValueError(f"clip {position} has no usable audio after applying transition handles")
        if fade_in + fade_out > layer_duration + 1e-6:
            raise ValueError(f"clip {position} is too short for the requested transition fades")
        if abs((output_end - output_start) - layer_duration) > 1e-4:
            raise ValueError(f"clip {position} audio source/output durations diverge")

        layers.append(
            {
                "clip_index": position,
                "video": clip["video"],
                "source_start": _round(source_start),
                "source_end": _round(source_end),
                "output_start": _round(output_start),
                "output_end": _round(output_end),
                "duration": _round(layer_duration),
                "fade_in": _round(min(fade_in, layer_duration / 2)),
                "fade_out": _round(min(fade_out, layer_duration / 2)),
            }
        )
    return layers


def _canonical_plan_content(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": plan.get("version"),
        "config": plan.get("config"),
        "sources": plan.get("sources"),
        "transcripts": plan.get("transcripts"),
        "clips": plan.get("clips"),
        "transitions": plan.get("transitions"),
        "audio_layers": plan.get("audio_layers"),
        "timeline": plan.get("timeline"),
        "options": plan.get("options"),
    }


def _plan_id(plan: Mapping[str, Any]) -> str:
    return _canonical_digest(_canonical_plan_content(plan))


def build_audio_transition_plan(
    config_path: str,
    transitions: Sequence[Mapping[str, Any]],
    *,
    edge_fade: float = DEFAULT_EDGE_FADE,
    working_directory: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        edge_fade = float(edge_fade)
    except (TypeError, ValueError) as exc:
        raise ValueError("edge_fade must be numeric") from exc
    if not math.isfinite(edge_fade) or not 0.005 <= edge_fade <= 0.2:
        raise ValueError("edge_fade must be between 0.005s and 0.2s")

    resolved = resolve_config(config_path, working_directory=working_directory)
    normalized = normalize_transitions(transitions, len(resolved["clips"]))
    layers = compile_audio_layers(
        resolved["clips"],
        normalized,
        media_by_path=resolved["media"],
        edge_fade=edge_fade,
    )
    warnings = [
        "Listen to every changed boundary at 1x on headphones and phone speakers before publishing.",
        "J-cut/L-cut timing is explicit; this plan does not infer dialogue intent, room tone, or safe handles.",
    ]
    if any(item["kind"] == "l_cut" for item in normalized):
        warnings.append(
            "Each L-cut skips the incoming clip's first overlap duration of audio to restore sync; use it only where that handle is ambience, room tone, or intentionally omitted speech."
        )
    plan: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": utc_now(),
        "config": resolved["config"],
        "sources": resolved["sources"],
        "transcripts": resolved["transcripts"],
        "clips": resolved["clips"],
        "transitions": normalized,
        "audio_layers": layers,
        "timeline": {
            "duration": resolved["timeline_duration"],
            "visual_cut_count": len(resolved["clips"]) - 1,
        },
        "options": {"edge_fade": _round(edge_fade)},
        "review_contract": {
            "required": True,
            "playback_speed": "1x",
            "checks": [
                "No word is clipped or unintentionally repeated at the transition.",
                "The incoming/outgoing ambience is continuous and there is no click, pump, or doubled voice.",
                "Dialogue remains understandable on headphones and a phone speaker.",
            ],
        },
        "warnings": warnings,
        "status": "review",
        "summary": {
            "blocking": 0,
            "warnings": len(warnings),
            "clips": len(resolved["clips"]),
            "transitions": len(normalized),
            "j_cuts": sum(item["kind"] == "j_cut" for item in normalized),
            "l_cuts": sum(item["kind"] == "l_cut" for item in normalized),
            "timeline_duration": resolved["timeline_duration"],
        },
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def verify_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if not isinstance(plan, Mapping):
        blockers.append("plan root must be an object")
    elif plan.get("version") != VERSION:
        blockers.append(f"plan version must be {VERSION}")

    if not blockers:
        expected_id = _plan_id(plan)
        if plan.get("plan_id") != expected_id:
            blockers.append("plan_id does not match canonical plan content")
        config = plan.get("config") if isinstance(plan.get("config"), Mapping) else {}
        try:
            rebuilt = build_audio_transition_plan(
                str(config.get("path") or ""),
                plan.get("transitions") if isinstance(plan.get("transitions"), list) else [],
                edge_fade=(plan.get("options") or {}).get("edge_fade", DEFAULT_EDGE_FADE),
                working_directory=str(config.get("working_directory") or ""),
            )
        except (OSError, ValueError) as exc:
            blockers.append(f"cannot rebuild plan from live inputs: {exc}")
        else:
            if _canonical_plan_content(plan) != _canonical_plan_content(rebuilt):
                blockers.append("compiled plan no longer matches live config, transcripts, sources, or transitions")
            warnings.extend(rebuilt["warnings"])

    status = "blocked" if blockers else ("review" if warnings else "ready")
    return {
        "version": VERIFY_VERSION,
        "verified_at": utc_now(),
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {"blocking": len(blockers), "warnings": len(warnings)},
    }


def plan_matches_clips(plan: Mapping[str, Any], clips: Sequence[Mapping[str, Any]]) -> List[str]:
    blockers: List[str] = []
    planned = plan.get("clips") if isinstance(plan.get("clips"), list) else []
    if len(planned) != len(clips):
        return ["audio-transition plan clip count does not match render config"]
    for index, (stored, live) in enumerate(zip(planned, clips), start=1):
        expected = {
            "video": str(Path(str(live.get("video") or "")).resolve()),
            "source_start": _round(float(live.get("start"))),
            "source_end": _round(float(live.get("end"))),
        }
        if stored.get("video") != expected["video"]:
            blockers.append(f"clip {index} video does not match audio-transition plan")
        if stored.get("source_start") != expected["source_start"] or stored.get("source_end") != expected["source_end"]:
            blockers.append(f"clip {index} timing does not match audio-transition plan")
        planned_broll = stored.get("broll")
        live_broll = live.get("broll")
        if planned_broll != (str(Path(live_broll).resolve()) if live_broll else None):
            blockers.append(f"clip {index} B-roll does not match audio-transition plan")
    return blockers


def build_filter_graph(
    plan: Mapping[str, Any],
    *,
    target_w: int,
    target_h: int,
    target_fps: float,
) -> Tuple[str, List[str]]:
    clips = plan.get("clips") if isinstance(plan.get("clips"), list) else []
    layers = plan.get("audio_layers") if isinstance(plan.get("audio_layers"), list) else []
    if not clips or len(clips) != len(layers):
        raise ValueError("audio-transition plan clips/audio_layers are incomplete")

    input_files: List[str] = []
    input_index: Dict[str, int] = {}
    for clip in clips:
        for raw_path in (clip.get("video"), clip.get("broll")):
            if raw_path and raw_path not in input_index:
                input_index[str(raw_path)] = len(input_files)
                input_files.append(str(raw_path))

    filters: List[str] = []
    video_inputs = ""
    for index, clip in enumerate(clips):
        video_path = str(clip.get("broll") or clip["video"])
        input_idx = input_index[video_path]
        if clip.get("broll"):
            start = float(clip.get("broll_start", 0.0))
            end = start + float(clip["duration"])
        else:
            start = float(clip["source_start"])
            end = float(clip["source_end"])
        filters.append(
            f"[{input_idx}:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"fps={target_fps:.6f},scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},setsar=1,format=yuv420p[v{index}]"
        )
        video_inputs += f"[v{index}]"
    filters.append(f"{video_inputs}concat=n={len(clips)}:v=1:a=0[merged_v]")

    audio_inputs = ""
    for index, layer in enumerate(layers):
        input_idx = input_index[str(layer["video"])]
        duration = float(layer["duration"])
        chain = [
            f"atrim=start={float(layer['source_start']):.6f}:end={float(layer['source_end']):.6f}",
            "asetpts=PTS-STARTPTS",
            "aresample=48000",
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
        ]
        fade_in = min(float(layer.get("fade_in", 0.0)), duration / 2)
        fade_out = min(float(layer.get("fade_out", 0.0)), duration / 2)
        if fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={fade_in:.6f}")
        if fade_out > 0:
            chain.append(f"afade=t=out:st={max(0.0, duration - fade_out):.6f}:d={fade_out:.6f}")
        delay_ms = int(round(float(layer["output_start"]) * 1000))
        if delay_ms > 0:
            chain.append(f"adelay={delay_ms}:all=1")
        filters.append(f"[{input_idx}:a]{','.join(chain)}[audio{index}]")
        audio_inputs += f"[audio{index}]"
    timeline_duration = float((plan.get("timeline") or {}).get("duration"))
    filters.append(
        f"{audio_inputs}amix=inputs={len(layers)}:duration=longest:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.95:level=false,atrim=duration={timeline_duration:.6f},"
        "asetpts=PTS-STARTPTS[merged_a]"
    )
    return ";\n".join(filters), input_files


def render_markdown(plan: Mapping[str, Any], *, plan_path: str = "work/audio_transition_plan.json") -> str:
    lines = [
        "# J-cut / L-cut Audio Transition Plan",
        "",
        f"- Status: `{plan.get('status')}`",
        f"- Plan ID: `{plan.get('plan_id')}`",
        f"- Clips: {len(plan.get('clips') or [])}",
        f"- Visual timeline: {(plan.get('timeline') or {}).get('duration')}s",
        "",
        "## Transitions",
        "",
        "| After clip | Type | Duration | Audio behavior |",
        "|---:|---|---:|---|",
    ]
    for item in plan.get("transitions") or []:
        if item["kind"] == "j_cut":
            behavior = "Incoming audio handle starts before picture-in; outgoing audio fades under it."
        else:
            behavior = "Outgoing audio handle continues after picture-out; incoming audio resumes after the overlap."
        lines.append(f"| {item['after_clip']} | `{item['kind']}` | {item['duration']:.3f}s | {behavior} |")

    lines.extend(
        [
            "",
            "## Apply",
            "",
            "```bash",
            f"python3 scripts/audio_transition.py apply {plan_path} \\",
            "  --output output/audio-transition-master.mp4 \\",
            "  --receipt work/audio_transition_apply.json",
            "```",
            "",
            "The apply command calls `render_final.py --audio-transition-plan` and keeps video, subtitles, overlays, BGM, and the shifted primary audio in one FFmpeg encode.",
            "",
            "## Required review",
            "",
            "Play every changed boundary at 1× on headphones and a phone speaker. Confirm there is no clipped/repeated word, doubled voice, click, pump, or ambience discontinuity. The script validates handles and hashes; it cannot decide whether overlapping dialogue is editorially correct.",
            "",
        ]
    )
    for warning in plan.get("warnings") or []:
        lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def _validate_output_target(output: Path, plan: Mapping[str, Any], plan_path: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists: {output}")
    protected = {Path(str((plan.get("config") or {}).get("path") or "")).resolve(), plan_path.resolve()}
    protected.update(Path(str(item.get("path") or "")).resolve() for item in plan.get("sources") or [])
    protected.update(Path(str(item.get("path") or "")).resolve() for item in plan.get("transcripts") or [])
    if output.resolve() in protected:
        raise ValueError("output cannot overwrite a plan input")


def apply_plan(
    plan_path: str,
    output_path: str,
    *,
    receipt_path: Optional[str] = None,
    no_subtitles: bool = False,
    no_cover: bool = False,
    no_loudnorm: bool = False,
    no_content_guard: bool = False,
) -> Dict[str, Any]:
    plan_file = Path(plan_path).expanduser().resolve()
    plan = load_plan(str(plan_file))
    verification = verify_plan(plan)
    if verification["summary"]["blocking"]:
        raise ValueError("audio-transition plan is blocked: " + "; ".join(verification["blockers"]))

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _validate_output_target(output, plan, plan_file)
    destination = Path(receipt_path or plan_file.with_name("audio_transition_apply.json")).expanduser().resolve()
    _validate_new_targets([str(output), str(destination)])
    suffix = output.suffix or ".mp4"
    temp_output = output.with_name(f".{output.stem}-{uuid.uuid4().hex[:10]}{suffix}")
    render_script = Path(__file__).with_name("render_final.py")
    command = [
        sys.executable,
        str(render_script),
        "--config",
        str((plan.get("config") or {}).get("path")),
        "--audio-transition-plan",
        str(plan_file),
        "--output",
        str(temp_output),
    ]
    for enabled, flag in (
        (no_subtitles, "--no-subtitles"),
        (no_cover, "--no-cover"),
        (no_loudnorm, "--no-loudnorm"),
        (no_content_guard, "--no-content-guard"),
    ):
        if enabled:
            command.append(flag)
    result = subprocess.run(
        command,
        cwd=str((plan.get("config") or {}).get("working_directory") or os.getcwd()),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not temp_output.is_file():
        if temp_output.exists():
            temp_output.unlink()
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise ValueError(f"render_final failed: {detail}")

    media = probe_media(str(temp_output))
    if not media["has_audio"]:
        temp_output.unlink()
        raise ValueError("rendered output has no audio stream")
    os.replace(temp_output, output)
    receipt = {
        "version": APPLY_VERSION,
        "applied_at": utc_now(),
        "plan_id": plan["plan_id"],
        "plan_path": str(plan_file),
        "output": {
            **_file_record(output, media=media),
        },
        "render": {
            "single_pass": True,
            "engine": "render_final.py",
            "no_subtitles": no_subtitles,
            "no_cover": no_cover,
            "no_loudnorm": no_loudnorm,
            "no_content_guard": no_content_guard,
        },
    }
    _write_json(str(destination), receipt)
    return receipt


def verify_receipt(plan: Mapping[str, Any], receipt: Mapping[str, Any]) -> List[str]:
    blockers: List[str] = []
    if receipt.get("version") != APPLY_VERSION:
        blockers.append(f"receipt version must be {APPLY_VERSION}")
    if receipt.get("plan_id") != plan.get("plan_id"):
        blockers.append("receipt plan_id does not match plan")
    output = receipt.get("output") if isinstance(receipt.get("output"), Mapping) else {}
    path = Path(str(output.get("path") or "")).expanduser()
    if not path.is_file():
        blockers.append("receipt output is missing")
        return blockers
    if path.stat().st_size != output.get("size"):
        blockers.append("receipt output size changed")
    if _sha256(path) != output.get("sha256"):
        blockers.append("receipt output sha256 changed")
    try:
        media = probe_media(str(path))
    except ValueError as exc:
        blockers.append(str(exc))
    else:
        if not media["has_audio"]:
            blockers.append("receipt output has no audio stream")
    return blockers


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and apply explicit source-bound J-cut/L-cut audio transitions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Build a source-bound audio-transition plan")
    plan_parser.add_argument("config", help="render_config.json with at least two clips")
    plan_parser.add_argument(
        "--transition",
        action="append",
        required=True,
        help="Repeatable AFTER_CLIP,TYPE,DURATION (example: 1,j_cut,0.5)",
    )
    plan_parser.add_argument("--edge-fade", type=float, default=DEFAULT_EDGE_FADE)
    plan_parser.add_argument("--output", default="work/audio_transition_plan.json")
    plan_parser.add_argument("--markdown", default="work/audio_transition_plan.md")

    verify_parser = subparsers.add_parser("verify", help="Rebuild and verify a plan against live inputs")
    verify_parser.add_argument("plan")
    verify_parser.add_argument("--receipt", help="Optional apply receipt to verify")
    verify_parser.add_argument("--strict", action="store_true", help="Exit 2 on blocking verification errors")

    apply_parser = subparsers.add_parser("apply", help="Render through render_final.py in one FFmpeg encode")
    apply_parser.add_argument("plan")
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--receipt")
    apply_parser.add_argument("--no-subtitles", action="store_true")
    apply_parser.add_argument("--no-cover", action="store_true")
    apply_parser.add_argument("--no-loudnorm", action="store_true")
    apply_parser.add_argument("--no-content-guard", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            targets = [args.output]
            if args.markdown:
                targets.append(args.markdown)
            _validate_new_targets(targets)
            transitions = [parse_transition(item) for item in args.transition]
            plan = build_audio_transition_plan(args.config, transitions, edge_fade=args.edge_fade)
            _write_json(args.output, plan)
            if args.markdown:
                _write_text(args.markdown, render_markdown(plan, plan_path=args.output))
            print(
                f"Audio transition plan review: {plan['summary']['transitions']} transition(s), "
                f"blocking={plan['summary']['blocking']}, warnings={plan['summary']['warnings']}"
            )
            return 0

        if args.command == "verify":
            plan = load_plan(args.plan)
            verification = verify_plan(plan)
            if args.receipt:
                receipt = load_plan(args.receipt)
                receipt_blockers = verify_receipt(plan, receipt)
                verification["blockers"].extend(receipt_blockers)
                verification["summary"]["blocking"] = len(verification["blockers"])
                if receipt_blockers:
                    verification["status"] = "blocked"
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 2 if args.strict and verification["summary"]["blocking"] else 0

        receipt = apply_plan(
            args.plan,
            args.output,
            receipt_path=args.receipt,
            no_subtitles=args.no_subtitles,
            no_cover=args.no_cover,
            no_loudnorm=args.no_loudnorm,
            no_content_guard=args.no_content_guard,
        )
        print(f"Audio transition render ready: {receipt['output']['path']}")
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
