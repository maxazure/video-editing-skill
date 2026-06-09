#!/usr/bin/env python3
"""Build an auditable audio cue sheet for short-form video projects.

The script does not generate music, voiceover, or sound effects. It turns a
transcript into local-first audio planning artifacts so an agent can review
music/SFX needs before spending credits or rendering.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


AUDIO_EXTS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
DEFAULT_EXCLUDES = {".git", ".venv", "node_modules", "research-archive", "__pycache__"}

MUSIC_MOOD_RULES: Sequence[Tuple[str, Tuple[str, ...], Tuple[int, int], str]] = (
    (
        "tech_pulse",
        ("AI", "模型", "自动化", "系统", "数据", "产品", "效率", "代码", "agent", "workflow"),
        (112, 128),
        "minimal tech pulse, instrumental, confident but not distracting",
    ),
    (
        "warm_explainer",
        ("故事", "生活", "客户", "体验", "朋友", "家庭", "成长", "分享"),
        (86, 108),
        "warm light groove, instrumental, friendly explainer background",
    ),
    (
        "tension_turn",
        ("但是", "问题", "风险", "焦虑", "卡住", "失败", "反转", "不过", "however"),
        (78, 96),
        "subtle tension bed, instrumental, restrained low percussion",
    ),
    (
        "upbeat_cta",
        ("关注", "评论", "收藏", "点赞", "转发", "subscribe", "follow", "comment"),
        (118, 132),
        "upbeat clean ending bed, instrumental, optimistic CTA energy",
    ),
)

SFX_RULES: Sequence[Dict[str, Any]] = (
    {
        "category": "transition_whoosh",
        "tokens": ("但是", "然而", "不过", "反而", "转折", "接下来", "切换", "but", "however", "instead"),
        "asset_keywords": ("whoosh", "transition", "swipe", "swoosh"),
        "offset": -0.02,
        "duration": 0.45,
        "prompt": "short soft whoosh that leads the visual transition",
    },
    {
        "category": "emphasis_ping",
        "tokens": ("重点", "关键", "记住", "核心", "真正", "一定", "attention", "key", "important"),
        "asset_keywords": ("ping", "pop", "ding", "accent", "emphasis"),
        "offset": 0.0,
        "duration": 0.35,
        "prompt": "small bright accent under an important phrase",
    },
    {
        "category": "success_chime",
        "tokens": ("完成", "搞定", "成功", "解决", "提升", "增长", "ready", "success", "done"),
        "asset_keywords": ("success", "chime", "complete", "positive"),
        "offset": 0.0,
        "duration": 0.6,
        "prompt": "subtle success chime, quiet enough to keep speech clear",
    },
    {
        "category": "warning_tick",
        "tokens": ("风险", "问题", "坑", "错误", "失败", "注意", "warning", "risk", "problem"),
        "asset_keywords": ("warn", "warning", "tick", "alert", "low"),
        "offset": 0.0,
        "duration": 0.45,
        "prompt": "low quiet warning tick without horror tone",
    },
)


@dataclasses.dataclass(frozen=True)
class Segment:
    idx: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclasses.dataclass(frozen=True)
class AudioAsset:
    path: str
    kind: str
    tokens: Tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def normalize_segments(transcript: Mapping[str, Any]) -> List[Segment]:
    segments: List[Segment] = []
    for pos, raw in enumerate(transcript.get("segments") or []):
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start = _float(raw.get("start"), 0.0)
        end = _float(raw.get("end"), start)
        if end < start:
            end = start
        idx = int(raw.get("id") or raw.get("segment_id") or pos + 1)
        segments.append(Segment(idx=idx, start=start, end=end, text=text))
    return segments


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round2(value: float) -> float:
    return round(float(value), 2)


def _tokenize_path(path: Path) -> Tuple[str, ...]:
    text = " ".join([path.stem, *path.parts]).lower()
    return tuple(t for t in re.split(r"[^a-z0-9\u4e00-\u9fff]+", text) if t)


def scan_audio_assets(paths: Iterable[str]) -> List[AudioAsset]:
    assets: Dict[str, AudioAsset] = {}
    for raw in paths:
        if not raw:
            continue
        root = Path(raw).expanduser()
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            if any(part in DEFAULT_EXCLUDES for part in path.parts):
                continue
            tokens = _tokenize_path(path)
            kind = _classify_asset(tokens, path)
            resolved = str(path.resolve())
            assets[resolved] = AudioAsset(path=resolved, kind=kind, tokens=tokens)
    return sorted(assets.values(), key=lambda item: item.path)


def _classify_asset(tokens: Sequence[str], path: Path) -> str:
    token_set = set(tokens)
    parts = {p.lower() for p in path.parts}
    if token_set & {"sfx", "fx", "whoosh", "transition", "ding", "chime", "alert", "click"}:
        return "sfx"
    if token_set & {"bgm", "music", "bed", "soundtrack", "score"} or parts & {"bgm", "music"}:
        return "music"
    return "audio"


def choose_music_mood(text: str, style: str = "auto") -> Dict[str, Any]:
    if style != "auto":
        for mood, _tokens, bpm, prompt in MUSIC_MOOD_RULES:
            if mood == style:
                return {"mood": mood, "bpm_range": list(bpm), "prompt": prompt}
    scores: List[Tuple[int, str, Tuple[int, int], str]] = []
    lowered = text.lower()
    for mood, tokens, bpm, prompt in MUSIC_MOOD_RULES:
        score = sum(1 for token in tokens if token.lower() in lowered or token in text)
        scores.append((score, mood, bpm, prompt))
    score, mood, bpm, prompt = sorted(scores, key=lambda item: (-item[0], item[1]))[0]
    if score <= 0:
        mood, bpm, prompt = ("warm_explainer", (92, 112), "light instrumental explainer bed, neutral and speech-safe")
    return {"mood": mood, "bpm_range": list(bpm), "prompt": prompt}


def find_audio_candidate(assets: Sequence[AudioAsset], *, kind: str, keywords: Sequence[str]) -> Optional[AudioAsset]:
    scored: List[Tuple[int, str, AudioAsset]] = []
    key_set = {k.lower() for k in keywords}
    for asset in assets:
        score = 0
        if asset.kind == kind:
            score += 6
        elif asset.kind == "audio":
            score += 1
        token_set = set(asset.tokens)
        score += 4 * len(token_set & key_set)
        if kind == "music" and token_set & {"bgm", "music", "bed"}:
            score += 2
        if kind == "sfx" and token_set & {"sfx", "fx"}:
            score += 2
        if score > 0:
            scored.append((score, asset.path, asset))
    if not scored:
        return None
    return sorted(scored, key=lambda item: (-item[0], item[1]))[0][2]


def build_audio_cue_sheet(
    *,
    transcript: Mapping[str, Any],
    asset_roots: Sequence[str] = (),
    style: str = "auto",
    max_sfx: int = 8,
    min_sfx_gap: float = 3.0,
    require_local_music: bool = False,
    require_local_sfx: bool = False,
    source_path: Optional[str] = None,
) -> Dict[str, Any]:
    segments = normalize_segments(transcript)
    if not segments:
        raise ValueError("transcript has no usable segments")

    assets = scan_audio_assets(asset_roots)
    full_text = " ".join(seg.text for seg in segments)
    total_duration = max(seg.end for seg in segments)
    mood = choose_music_mood(full_text, style=style)

    music_candidate = find_audio_candidate(
        assets,
        kind="music",
        keywords=(mood["mood"], "bgm", "music", "bed", "instrumental"),
    )
    music_status = "ready" if music_candidate else ("blocked" if require_local_music else "needs_generation")
    music = {
        "id": "music_bed_001",
        "type": "music_bed",
        "start": 0.0,
        "end": _round2(total_duration),
        "duration": _round2(total_duration),
        "mood": mood["mood"],
        "bpm_range": mood["bpm_range"],
        "prompt": mood["prompt"],
        "route": "local_bgm" if music_candidate else "music_provider_or_local_search",
        "status": music_status,
        "asset": music_candidate.path if music_candidate else None,
        "paid_credit": music_candidate is None,
        "approval_note": "Generated music or stock music may require provider credits or license review."
        if music_candidate is None
        else "",
        "mix": {
            "role": "secondary_under_voice",
            "target_level": "-28 to -24 LUFS under speech",
            "ducking": "duck 18-20 dB below primary speech when narration is active",
            "lyrics": "avoid lyrics under narration",
            "fade_in": 0.8,
            "fade_out": 2.5,
        },
    }

    sfx = build_sfx_cues(
        segments,
        assets=assets,
        max_sfx=max_sfx,
        min_gap=min_sfx_gap,
        require_local=require_local_sfx,
    )

    voice_track = {
        "type": "primary_voice",
        "source": source_path or "transcript",
        "segment_count": len(segments),
        "start": _round2(min(seg.start for seg in segments)),
        "end": _round2(total_duration),
        "duration": _round2(sum(seg.duration for seg in segments)),
        "status": "ready",
        "mix": {
            "role": "primary",
            "target_integrated_lufs": "-16 LUFS for short-form platform delivery",
            "true_peak": "-1.5 dBTP",
        },
    }

    cues = [music, *sfx]
    summary = summarize(cues, voice_track=voice_track)
    next_actions = build_next_actions(summary, music, sfx)

    return {
        "version": "audio_cue_sheet.v1",
        "generated_at": utc_now(),
        "inputs": {
            "transcript": source_path,
            "asset_roots": list(asset_roots),
            "style": style,
            "max_sfx": max_sfx,
            "min_sfx_gap": min_sfx_gap,
            "require_local_music": require_local_music,
            "require_local_sfx": require_local_sfx,
        },
        "summary": summary,
        "voice_track": voice_track,
        "music": [music],
        "sfx": sfx,
        "mix_notes": [
            "Keep speech as the primary track; do not let music or SFX mask consonants.",
            "Use instrumental music under narration; lyrics compete with speech.",
            "Place transition SFX 10-20 ms before the visual cut when possible.",
            "Review every generated or stock audio asset for license/provenance before publishing.",
        ],
        "next_actions": next_actions,
    }


def build_sfx_cues(
    segments: Sequence[Segment],
    *,
    assets: Sequence[AudioAsset],
    max_sfx: int,
    min_gap: float,
    require_local: bool,
) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    last_time = -999.0
    for seg in segments:
        if len(cues) >= max_sfx:
            break
        rule = _match_sfx_rule(seg.text)
        if rule is None:
            continue
        start = max(0.0, seg.start + float(rule["offset"]))
        if start - last_time < min_gap:
            continue
        candidate = find_audio_candidate(assets, kind="sfx", keywords=rule["asset_keywords"])
        status = "ready" if candidate else ("blocked" if require_local else "needs_generation")
        cues.append(
            {
                "id": f"sfx_{len(cues) + 1:03d}",
                "type": "sound_effect",
                "category": rule["category"],
                "start": _round2(start),
                "end": _round2(start + float(rule["duration"])),
                "duration": _round2(float(rule["duration"])),
                "trigger_segment": seg.idx,
                "trigger_text": seg.text,
                "matched_token": _matched_token(seg.text, rule["tokens"]),
                "prompt": rule["prompt"],
                "route": "local_sfx" if candidate else "sfx_provider_or_local_search",
                "status": status,
                "asset": candidate.path if candidate else None,
                "paid_credit": candidate is None,
                "approval_note": "Generated SFX may require provider credits or license review."
                if candidate is None
                else "",
                "mix": {
                    "target_level": "-18 to -12 LUFS momentary",
                    "voice_safe": "short transient, avoid masking the spoken word",
                },
            }
        )
        last_time = start
    return cues


def _match_sfx_rule(text: str) -> Optional[Mapping[str, Any]]:
    lowered = text.lower()
    for rule in SFX_RULES:
        if any(token.lower() in lowered or token in text for token in rule["tokens"]):
            return rule
    return None


def _matched_token(text: str, tokens: Sequence[str]) -> str:
    lowered = text.lower()
    for token in tokens:
        if token.lower() in lowered or token in text:
            return token
    return ""


def summarize(cues: Sequence[Mapping[str, Any]], *, voice_track: Mapping[str, Any]) -> Dict[str, Any]:
    ready = sum(1 for cue in cues if cue.get("status") == "ready")
    blocked = sum(1 for cue in cues if cue.get("status") == "blocked")
    needs_generation = sum(1 for cue in cues if cue.get("status") == "needs_generation")
    approval_required = sum(1 for cue in cues if cue.get("paid_credit"))
    return {
        "voice_segments": int(voice_track.get("segment_count") or 0),
        "duration": voice_track.get("end", 0.0),
        "music_cues": sum(1 for cue in cues if cue.get("type") == "music_bed"),
        "sfx_cues": sum(1 for cue in cues if cue.get("type") == "sound_effect"),
        "ready": ready,
        "needs_generation": needs_generation,
        "approval_required": approval_required,
        "blocking": blocked,
    }


def build_next_actions(
    summary: Mapping[str, Any],
    music: Mapping[str, Any],
    sfx: Sequence[Mapping[str, Any]],
) -> List[str]:
    actions: List[str] = []
    if music.get("status") != "ready":
        actions.append(
            "Pick a local instrumental BGM file or approve music generation/search before final render."
        )
    missing_sfx = [cue for cue in sfx if cue.get("status") != "ready"]
    if missing_sfx:
        actions.append(
            f"Resolve {len(missing_sfx)} SFX cue(s): choose local files, remove cues, or approve generation."
        )
    if int(summary.get("approval_required") or 0):
        actions.append("Review provider credits and asset provenance before submitting generated audio work.")
    actions.append("After audio assets are chosen, update render_config.json bgm fields and keep this sheet with QA artifacts.")
    return actions


def emit_markdown(sheet: Mapping[str, Any]) -> str:
    summary = sheet.get("summary") or {}
    lines = [
        "# Audio Cue Sheet",
        "",
        f"- Status: {'BLOCKED' if summary.get('blocking') else 'REVIEW'}",
        f"- Duration: {summary.get('duration', 0)}s",
        f"- Music cues: {summary.get('music_cues', 0)}",
        f"- SFX cues: {summary.get('sfx_cues', 0)}",
        f"- Needs generation: {summary.get('needs_generation', 0)}",
        f"- Approval required: {summary.get('approval_required', 0)}",
        f"- Blocking: {summary.get('blocking', 0)}",
        "",
        "## Music",
        "",
        "| id | mood | status | route | asset | prompt |",
        "|---|---|---|---|---|---|",
    ]
    for cue in sheet.get("music") or []:
        lines.append(
            "| {id} | {mood} | {status} | {route} | `{asset}` | {prompt} |".format(
                id=cue.get("id", ""),
                mood=cue.get("mood", ""),
                status=cue.get("status", ""),
                route=cue.get("route", ""),
                asset=cue.get("asset") or "-",
                prompt=str(cue.get("prompt") or "").replace("|", "/"),
            )
        )

    lines.extend(
        [
            "",
            "## SFX",
            "",
            "| id | time | category | status | matched | asset |",
            "|---|---:|---|---|---|---|",
        ]
    )
    for cue in sheet.get("sfx") or []:
        lines.append(
            "| {id} | {start}-{end}s | {category} | {status} | {matched} | `{asset}` |".format(
                id=cue.get("id", ""),
                start=cue.get("start", 0),
                end=cue.get("end", 0),
                category=cue.get("category", ""),
                status=cue.get("status", ""),
                matched=cue.get("matched_token", ""),
                asset=cue.get("asset") or "-",
            )
        )

    actions = sheet.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    notes = sheet.get("mix_notes") or []
    if notes:
        lines.extend(["", "## Mix Notes", ""])
        lines.extend(f"- {note}" for note in notes)

    return "\n".join(lines).rstrip() + "\n"


def write_json(path: str, data: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an audio cue sheet for BGM/SFX review without generating audio."
    )
    parser.add_argument("--transcript", required=True, help="Transcript JSON with segments[].start/end/text.")
    parser.add_argument(
        "--asset-root",
        action="append",
        default=[],
        help="Directory or audio file to scan for local BGM/SFX. Can repeat.",
    )
    parser.add_argument(
        "--style",
        default="auto",
        choices=["auto", *[rule[0] for rule in MUSIC_MOOD_RULES]],
        help="Music mood style. Default auto detects from transcript text.",
    )
    parser.add_argument("--max-sfx", type=int, default=8, help="Maximum SFX cues to emit.")
    parser.add_argument("--min-sfx-gap", type=float, default=3.0, help="Minimum seconds between SFX cues.")
    parser.add_argument("--require-local-music", action="store_true", help="Block when no local music asset is found.")
    parser.add_argument("--require-local-sfx", action="store_true", help="Block when SFX cues have no local asset.")
    parser.add_argument("--output", default="audio_cue_sheet.json", help="Output JSON path.")
    parser.add_argument("--markdown", help="Optional Markdown review path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when the sheet has blocking items.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        transcript = load_json(args.transcript)
        sheet = build_audio_cue_sheet(
            transcript=transcript,
            asset_roots=args.asset_root,
            style=args.style,
            max_sfx=max(0, args.max_sfx),
            min_sfx_gap=max(0.0, args.min_sfx_gap),
            require_local_music=args.require_local_music,
            require_local_sfx=args.require_local_sfx,
            source_path=str(Path(args.transcript).expanduser().resolve()),
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_json(args.output, sheet)
    if args.markdown:
        write_text(args.markdown, emit_markdown(sheet))

    summary = sheet["summary"]
    print(
        "audio cue sheet: "
        f"music={summary['music_cues']} "
        f"sfx={summary['sfx_cues']} "
        f"needs_generation={summary['needs_generation']} "
        f"blocking={summary['blocking']}"
    )
    if args.strict and int(summary.get("blocking") or 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
