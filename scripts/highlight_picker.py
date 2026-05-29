#!/usr/bin/env python3
"""Pick scored short-form highlight candidates from a timestamped transcript.

This script is intentionally local and auditable. It does not call an LLM or
render video; it produces review artifacts that can feed render_final.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "highlight_candidates.v1"

PLATFORM_DEFAULTS: Dict[str, Tuple[float, float, float]] = {
    "xhs": (20.0, 90.0, 45.0),
    "douyin": (15.0, 60.0, 35.0),
    "wxch": (15.0, 60.0, 35.0),
    "shorts": (15.0, 60.0, 35.0),
    "reels": (15.0, 90.0, 45.0),
    "tiktok": (15.0, 60.0, 35.0),
}

ZH_FILLERS = (
    "嗯", "呃", "啊", "那个", "就是", "然后", "其实就是", "怎么说",
)
EN_FILLERS = (
    "um", "uh", "like", "you know", "i mean", "sort of", "kind of",
)

DANGLING_ENDINGS = (
    "但是", "因为", "如果", "所以", "然后", "而且", "比如", "比如说",
    "but", "because", "if", "so", "and", "for example",
)

SIGNAL_PATTERNS: Dict[str, Tuple[str, Tuple[str, ...], float]] = {
    "hook_question": (
        "strong question hook",
        (r"为什么", r"怎么", r"你有没有", r"你知道", r"what if", r"why\b", r"how\b", r"did you know", r"\?"),
        0.32,
    ),
    "hook_contrarian": (
        "contrarian hook",
        (r"其实", r"反而", r"不是", r"别再", r"千万", r"不要", r"nobody", r"wrong", r"stop ", r"mistake"),
        0.30,
    ),
    "problem_pain": (
        "clear pain or risk",
        (r"痛点", r"焦虑", r"问题", r"坑", r"失败", r"亏", r"风险", r"problem", r"pain", r"fail", r"risk", r"cost"),
        0.24,
    ),
    "turn_reveal": (
        "turn or reveal",
        (r"但是", r"关键", r"结果", r"后来", r"突然", r"发现", r"真相", r"secret", r"truth", r"then ", r"but ", r"however", r"turns out"),
        0.24,
    ),
    "value_practical": (
        "practical value",
        (r"方法", r"步骤", r"清单", r"技巧", r"建议", r"流程", r"模板", r"how to", r"step", r"checklist", r"framework", r"template"),
        0.22,
    ),
    "data_specific": (
        "specific data point",
        (r"\d+(\.\d+)?\s*[%倍万kK]?", r"\$[0-9]", r"增长", r"下降", r"提升", r"降低", r"save[sd]?", r"grew", r"reduced"),
        0.20,
    ),
    "emotion": (
        "emotional peak",
        (r"震惊", r"离谱", r"崩溃", r"兴奋", r"害怕", r"笑", r"amazing", r"crazy", r"shocked", r"excited", r"afraid", r"laugh"),
        0.16,
    ),
    "cta": (
        "publishable CTA",
        (r"评论", r"收藏", r"关注", r"转发", r"告诉我", r"comment", r"save this", r"follow", r"share"),
        0.10,
    ),
}


@dataclass(frozen=True)
class TranscriptSegment:
    idx: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class CandidateWindow:
    start_index: int
    end_index: int
    segments: Tuple[TranscriptSegment, ...]

    @property
    def start(self) -> float:
        return self.segments[0].start

    @property
    def end(self) -> float:
        return self.segments[-1].end

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def text(self) -> str:
        return " ".join(seg.text.strip() for seg in self.segments if seg.text.strip())


def _round3(value: float) -> float:
    return round(float(value), 3)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_scene_boundaries(path: str) -> Dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("--scene-boundaries must point to a JSON object")
    return payload


def infer_language(transcript: Mapping[str, Any], override: str = "auto") -> str:
    if override and override != "auto":
        return "zh" if override.lower().startswith("zh") else "en"
    raw = transcript.get("language") or transcript.get("detected_language")
    if raw:
        return "zh" if str(raw).lower().startswith("zh") else "en"
    sample = "".join(str(seg.get("text", "")) for seg in transcript.get("segments", [])[:20])
    return "zh" if re.search(r"[\u4e00-\u9fff]", sample) else "en"


def normalize_segments(transcript: Mapping[str, Any]) -> List[TranscriptSegment]:
    segments: List[TranscriptSegment] = []
    for pos, raw in enumerate(transcript.get("segments") or [], start=1):
        if not isinstance(raw, Mapping):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad transcript segment: {raw!r}") from exc
        if end <= start:
            continue
        try:
            idx = int(raw.get("id", pos))
        except (TypeError, ValueError):
            idx = pos
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        segments.append(TranscriptSegment(idx=idx, start=_round3(start), end=_round3(end), text=text))
    return sorted(segments, key=lambda s: (s.start, s.end, s.idx))


def _clip_text(text: str, limit: int = 42) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)].rstrip() + "..."


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _count_matches(text: str, patterns: Iterable[str]) -> int:
    return sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)


def _token_count(text: str, language: str) -> int:
    if language == "zh":
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        ascii_words = len(re.findall(r"[A-Za-z0-9]+", text))
        return max(1, zh_chars + ascii_words)
    return max(1, len(re.findall(r"[A-Za-z0-9']+", text)))


def _signal_score(text: str, signal_ids: Sequence[str], cap: float = 1.0) -> Tuple[float, List[str]]:
    score = 0.0
    labels: List[str] = []
    for signal_id in signal_ids:
        label, patterns, weight = SIGNAL_PATTERNS[signal_id]
        matches = _count_matches(text, patterns)
        if matches:
            score += min(weight * matches, weight * 1.8)
            labels.append(label)
    return min(cap, score), labels


def _duration_score(duration: float, target_duration: float, min_duration: float, max_duration: float) -> float:
    if duration < min_duration or duration > max_duration:
        return 0.0
    spread = max(target_duration - min_duration, max_duration - target_duration, 1.0)
    return max(0.25, 1.0 - abs(duration - target_duration) / spread)


def _density_score(text: str, duration: float, language: str) -> float:
    if duration <= 0:
        return 0.0
    tokens_per_second = _token_count(text, language) / duration
    ideal = 4.8 if language == "zh" else 2.35
    spread = 3.2 if language == "zh" else 1.45
    return max(0.1, 1.0 - abs(tokens_per_second - ideal) / spread)


def _filler_penalty(text: str, language: str) -> Tuple[float, List[str]]:
    fillers = ZH_FILLERS if language == "zh" else EN_FILLERS
    found = [f for f in fillers if re.search(re.escape(f), text, flags=re.IGNORECASE)]
    if not found:
        return 0.0, []
    penalty = min(0.18, 0.035 * len(found))
    return penalty, [f"filler-heavy: {', '.join(found[:4])}"]


def _completeness_score(window: CandidateWindow) -> Tuple[float, List[str]]:
    warnings: List[str] = []
    score = 1.0
    first = window.segments[0].text.strip().lower()
    last = window.segments[-1].text.strip().lower()
    if _contains_any(first, (r"^(但是|然后|所以|and\b|but\b|so\b)",)):
        score -= 0.22
        warnings.append("starts with a connector")
    if _contains_any(last, tuple(rf"{re.escape(word)}[，,。.!！]?$" for word in DANGLING_ENDINGS)):
        score -= 0.30
        warnings.append("may end mid-thought")
    if len(window.segments) < 2:
        score -= 0.20
        warnings.append("single transcript segment")
    if not re.search(r"[。！？.!?]$", last):
        score -= 0.05
    return max(0.0, min(1.0, score)), warnings


def score_window(
    window: CandidateWindow,
    *,
    language: str,
    min_duration: float,
    max_duration: float,
    target_duration: float,
) -> Dict[str, Any]:
    text = window.text
    hook_text = " ".join(seg.text for seg in window.segments if seg.end - window.start <= 5.0) or window.segments[0].text
    hook_score, hook_labels = _signal_score(
        hook_text,
        ("hook_question", "hook_contrarian", "problem_pain", "data_specific"),
        cap=1.0,
    )
    value_score, value_labels = _signal_score(
        text,
        ("value_practical", "data_specific", "cta"),
        cap=1.0,
    )
    turn_score, turn_labels = _signal_score(
        text,
        ("turn_reveal", "problem_pain", "hook_contrarian"),
        cap=1.0,
    )
    emotion_score, emotion_labels = _signal_score(text, ("emotion", "problem_pain"), cap=1.0)
    duration_score = _duration_score(window.duration, target_duration, min_duration, max_duration)
    density_score = _density_score(text, window.duration, language)
    completeness_score, warnings = _completeness_score(window)
    filler_penalty, filler_warnings = _filler_penalty(text, language)
    warnings.extend(filler_warnings)

    raw = (
        0.24 * hook_score
        + 0.18 * value_score
        + 0.14 * turn_score
        + 0.10 * emotion_score
        + 0.14 * duration_score
        + 0.10 * density_score
        + 0.10 * completeness_score
    )
    score = max(0.0, min(100.0, raw * 100.0 - filler_penalty * 100.0))
    signal_labels = list(dict.fromkeys(hook_labels + value_labels + turn_labels + emotion_labels))
    if hook_score < 0.20:
        warnings.append("weak opening hook")
    if duration_score < 0.45:
        warnings.append("duration far from platform sweet spot")

    title = suggest_title(window, signal_labels)
    reason = build_reason(signal_labels, duration_score, completeness_score)
    return {
        "score": round(score, 1),
        "score_breakdown": {
            "hook": round(hook_score, 3),
            "value": round(value_score, 3),
            "turn": round(turn_score, 3),
            "emotion": round(emotion_score, 3),
            "duration": round(duration_score, 3),
            "density": round(density_score, 3),
            "completeness": round(completeness_score, 3),
            "filler_penalty": round(filler_penalty, 3),
        },
        "signals": signal_labels,
        "warnings": list(dict.fromkeys(warnings)),
        "hook_text": _clip_text(hook_text, 80),
        "title_suggestion": title,
        "reason": reason,
    }


def suggest_title(window: CandidateWindow, signals: Sequence[str]) -> str:
    first = window.segments[0].text.strip()
    first = re.sub(r"^[，,。.!！?？\s]+", "", first)
    if not first:
        return "Highlight clip"
    if "specific data point" in signals or re.search(r"\d", first):
        return _clip_text(first, 24)
    if "clear pain or risk" in signals:
        return _clip_text(first, 22)
    return _clip_text(first, 20)


def build_reason(signals: Sequence[str], duration_score: float, completeness_score: float) -> str:
    parts = list(signals[:3])
    if duration_score >= 0.75:
        parts.append("good platform length")
    if completeness_score >= 0.85:
        parts.append("self-contained ending")
    if not parts:
        return "Chosen for transcript density and acceptable duration."
    return "Chosen for " + ", ".join(parts) + "."


def generate_windows(
    segments: Sequence[TranscriptSegment],
    *,
    min_duration: float,
    max_duration: float,
) -> List[CandidateWindow]:
    windows: List[CandidateWindow] = []
    for start_index, start_segment in enumerate(segments):
        for end_index in range(start_index, len(segments)):
            end_segment = segments[end_index]
            duration = end_segment.end - start_segment.start
            if duration > max_duration + 0.001:
                break
            if duration >= min_duration:
                windows.append(CandidateWindow(
                    start_index=start_index,
                    end_index=end_index,
                    segments=tuple(segments[start_index:end_index + 1]),
                ))
    return windows


def _overlap_ratio(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    latest_start = max(float(a["start"]), float(b["start"]))
    earliest_end = min(float(a["end"]), float(b["end"]))
    overlap = max(0.0, earliest_end - latest_start)
    shortest = max(0.001, min(float(a["duration"]), float(b["duration"])))
    return overlap / shortest


def dedupe_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    overlap_threshold: float = 0.50,
) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda c: (-float(c["score"]), float(c["start"]))):
        if any(_overlap_ratio(candidate, kept_item) > overlap_threshold for kept_item in kept):
            continue
        kept.append(dict(candidate))
    return kept


def _boundary_points(scene_boundaries: Optional[Mapping[str, Any]]) -> List[float]:
    if not scene_boundaries:
        return []
    points: List[float] = []
    for raw in scene_boundaries.get("boundaries") or []:
        try:
            points.append(float(raw))
        except (TypeError, ValueError):
            continue
    for scene in scene_boundaries.get("scenes") or []:
        if not isinstance(scene, Mapping):
            continue
        for key in ("start", "end"):
            try:
                points.append(float(scene[key]))
            except (KeyError, TypeError, ValueError):
                continue
    duration = scene_boundaries.get("source", {}).get("duration") if isinstance(scene_boundaries.get("source"), Mapping) else None
    try:
        if duration is not None:
            points.append(float(duration))
    except (TypeError, ValueError):
        pass
    return sorted(set(_round3(p) for p in points if p >= 0))


def _nearest_prior_boundary(value: float, points: Sequence[float], tolerance: float) -> Optional[float]:
    choices = [p for p in points if p <= value and value - p <= tolerance]
    return max(choices) if choices else None


def _nearest_next_boundary(value: float, points: Sequence[float], tolerance: float) -> Optional[float]:
    choices = [p for p in points if p >= value and p - value <= tolerance]
    return min(choices) if choices else None


def apply_scene_snap(
    candidate: Mapping[str, Any],
    *,
    boundary_points: Sequence[float],
    tolerance: float,
    max_duration: float,
) -> Dict[str, Any]:
    """Expand candidate timing to nearby visual scene cuts without dropping transcript."""
    adjusted = dict(candidate)
    if not boundary_points or tolerance <= 0:
        return adjusted

    original_start = float(candidate["start"])
    original_end = float(candidate["end"])
    snapped_start = _nearest_prior_boundary(original_start, boundary_points, tolerance)
    snapped_end = _nearest_next_boundary(original_end, boundary_points, tolerance)
    start = original_start if snapped_start is None else snapped_start
    end = original_end if snapped_end is None else snapped_end

    if end - start > max_duration:
        end = original_end
    if end - start > max_duration:
        start = original_start
    if end <= start:
        return adjusted

    applied = abs(start - original_start) > 0.0005 or abs(end - original_end) > 0.0005
    if applied:
        adjusted["start"] = _round3(start)
        adjusted["end"] = _round3(end)
        adjusted["duration"] = _round3(end - start)
    adjusted["scene_snap"] = {
        "applied": applied,
        "tolerance": float(tolerance),
        "original_start": _round3(original_start),
        "original_end": _round3(original_end),
        "snapped_start": _round3(start),
        "snapped_end": _round3(end),
        "start_shift": _round3(start - original_start),
        "end_shift": _round3(end - original_end),
    }
    return adjusted


def build_highlight_candidates(
    transcript: Mapping[str, Any],
    *,
    platform: str = "xhs",
    language: str = "auto",
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
    target_duration: Optional[float] = None,
    num_clips: int = 5,
    max_candidates: int = 30,
    overlap_threshold: float = 0.50,
    scene_boundaries: Optional[Mapping[str, Any]] = None,
    scene_snap_tolerance: float = 1.5,
) -> Dict[str, Any]:
    if platform not in PLATFORM_DEFAULTS:
        raise ValueError(f"unsupported platform: {platform}")
    default_min, default_max, default_target = PLATFORM_DEFAULTS[platform]
    min_duration = default_min if min_duration is None else float(min_duration)
    max_duration = default_max if max_duration is None else float(max_duration)
    target_duration = default_target if target_duration is None else float(target_duration)
    if min_duration <= 0 or max_duration < min_duration:
        raise ValueError("duration range must be positive and max >= min")

    lang = infer_language(transcript, language)
    segments = normalize_segments(transcript)
    windows = generate_windows(segments, min_duration=min_duration, max_duration=max_duration)
    scene_points = _boundary_points(scene_boundaries)
    scored: List[Dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        score_data = score_window(
            window,
            language=lang,
            min_duration=min_duration,
            max_duration=max_duration,
            target_duration=target_duration,
        )
        candidate = {
            "id": f"highlight_{index:04d}",
            "start": _round3(window.start),
            "end": _round3(window.end),
            "duration": _round3(window.duration),
            "segment_ids": [seg.idx for seg in window.segments],
            "text": window.text,
            **score_data,
        }
        if scene_points:
            candidate = apply_scene_snap(
                candidate,
                boundary_points=scene_points,
                tolerance=scene_snap_tolerance,
                max_duration=max_duration,
            )
        scored.append(candidate)

    deduped = dedupe_candidates(scored, overlap_threshold=overlap_threshold)
    candidates = deduped[:max_candidates]
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate["id"] = f"highlight_{rank:03d}"
    selected = candidates[:num_clips]

    duration = transcript.get("duration")
    if duration is None and segments:
        duration = max(seg.end for seg in segments)
    return {
        "version": VERSION,
        "source": {
            "language": lang,
            "segments": len(segments),
            "duration": _round3(float(duration or 0.0)),
        },
        "params": {
            "platform": platform,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "target_duration": target_duration,
            "num_clips": num_clips,
            "max_candidates": max_candidates,
            "overlap_threshold": overlap_threshold,
            "scene_snap_tolerance": scene_snap_tolerance if scene_points else None,
        },
        "summary": {
            "windows_scored": len(scored),
            "candidates_after_dedupe": len(deduped),
            "selected": len(selected),
            "top_score": selected[0]["score"] if selected else 0,
            "scene_snapped": sum(1 for item in selected if item.get("scene_snap", {}).get("applied")),
        },
        "selected": selected,
        "candidates": candidates,
    }


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def emit_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Highlight Candidates",
        "",
        f"- Platform: `{plan['params']['platform']}`",
        f"- Duration range: `{plan['params']['min_duration']:.0f}-{plan['params']['max_duration']:.0f}s`",
        f"- Windows scored: `{plan['summary']['windows_scored']}`",
        f"- Selected clips: `{plan['summary']['selected']}`",
        f"- Scene-snapped clips: `{plan['summary'].get('scene_snapped', 0)}`",
        "",
        "| Rank | Time | Score | Scene Snap | Hook | Why | Warnings |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for candidate in plan.get("selected", []):
        warnings = "; ".join(candidate.get("warnings") or []) or "-"
        snap = candidate.get("scene_snap") or {}
        snap_text = "-"
        if snap.get("applied"):
            snap_text = "{start:+.2f}s/{end:+.2f}s".format(
                start=float(snap.get("start_shift", 0.0)),
                end=float(snap.get("end_shift", 0.0)),
            )
        lines.append(
            "| {rank} | {start}-{end} | {score:.1f} | {snap} | {hook} | {reason} | {warnings} |".format(
                rank=candidate["rank"],
                start=format_time(candidate["start"]),
                end=format_time(candidate["end"]),
                score=float(candidate["score"]),
                snap=snap_text,
                hook=_clip_text(candidate.get("hook_text", ""), 52).replace("|", "\\|"),
                reason=str(candidate.get("reason", "")).replace("|", "\\|"),
                warnings=warnings.replace("|", "\\|"),
            )
        )
    lines.extend([
        "",
        "## Review Notes",
        "",
        "- Pick candidates with a real opening hook and a self-contained ending before rendering.",
        "- If `warnings` mention weak hook or mid-thought ending, rewrite or extend the clip manually.",
        "- If scene snapping is enabled, start times only move backward and end times only move forward to avoid cutting transcript words.",
        "- `--render-config` can create a direct `render_final.py` input when a source video path is supplied.",
    ])
    return "\n".join(lines) + "\n"


def build_render_config(plan: Mapping[str, Any], video_path: str) -> Dict[str, Any]:
    clips = []
    for candidate in plan.get("selected", []):
        clips.append({
            "name": candidate["id"],
            "video": video_path,
            "start": candidate["start"],
            "end": candidate["end"],
            "text": candidate["text"],
            "highlight_score": candidate["score"],
            "segment_ids": candidate["segment_ids"],
        })
        if candidate.get("scene_snap"):
            clips[-1]["scene_snap"] = candidate["scene_snap"]
    return {
        "version": "render_config.v1",
        "source": "highlight_picker.py",
        "clips": clips,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pick scored short-form highlight candidates from transcript JSON")
    parser.add_argument("--transcript", required=True, help="Transcript JSON with segments[start,end,text]")
    parser.add_argument("--output", required=True, help="Output highlight_candidates JSON")
    parser.add_argument("--markdown", help="Optional Markdown review table")
    parser.add_argument("--video", help="Source video path used when writing --render-config")
    parser.add_argument("--render-config", help="Optional render_final.py config containing selected clips")
    parser.add_argument("--scene-boundaries", help="Optional scene_boundaries JSON from scripts/scene_boundaries.py")
    parser.add_argument("--scene-snap-tolerance", type=float, default=1.5, help="Seconds to expand a candidate to nearby visual cut points")
    parser.add_argument("--platform", choices=sorted(PLATFORM_DEFAULTS), default="xhs")
    parser.add_argument("--language", default="auto", help="auto, zh, or en")
    parser.add_argument("--num-clips", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--min-duration", type=float)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--target-duration", type=float)
    parser.add_argument("--overlap-threshold", type=float, default=0.50)
    parser.add_argument("--min-score", type=float, default=55.0, help="Strict mode fails if no selected candidate reaches this score")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when no selected candidate reaches --min-score")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    transcript = load_json(args.transcript)
    scene_boundaries = None
    if args.scene_boundaries:
        try:
            scene_boundaries = load_scene_boundaries(args.scene_boundaries)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    try:
        plan = build_highlight_candidates(
            transcript,
            platform=args.platform,
            language=args.language,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            target_duration=args.target_duration,
            num_clips=max(1, args.num_clips),
            max_candidates=max(1, args.max_candidates),
            overlap_threshold=max(0.0, min(1.0, args.overlap_threshold)),
            scene_boundaries=scene_boundaries,
            scene_snap_tolerance=max(0.0, args.scene_snap_tolerance),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, plan)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(emit_markdown(plan))
    if args.render_config:
        if not args.video:
            print("Error: --render-config requires --video", file=sys.stderr)
            return 1
        write_json(args.render_config, build_render_config(plan, args.video))

    selected = plan.get("selected") or []
    best = float(selected[0]["score"]) if selected else 0.0
    print(
        "Highlight candidates: {selected} selected, best score {score:.1f}, wrote {path}".format(
            selected=len(selected),
            score=best,
            path=args.output,
        )
    )
    if args.strict and best < args.min_score:
        print(f"Strict mode: best score {best:.1f} < min score {args.min_score:.1f}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
