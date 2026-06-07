#!/usr/bin/env python3
"""Build a localization and dubbing review package from timed transcript text.

This tool is local and artifact-first: it does not translate, synthesize audio,
clone voices, or call providers. It prepares a reviewable package that a human,
LLM, or TTS workflow can fill and validate before localized publishing.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from subtitle_pack import (  # noqa: E402
    SourceClip,
    TimedWord,
    build_cues,
    default_max_chars,
    infer_language,
)


VERSION = "localization_pack.v1"


@dataclasses.dataclass(frozen=True)
class SourceMeta:
    id: str
    speaker: str = ""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _parse_words(raw_words: Iterable[Mapping[str, Any]], *, clip_start: float, clip_end: float) -> Tuple[TimedWord, ...]:
    words: List[TimedWord] = []
    for raw in raw_words or []:
        text = _clean_text(raw.get("word") or raw.get("text"))
        if not text:
            continue
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"), start)
        if end < clip_start or start > clip_end:
            continue
        words.append(TimedWord(text=text, start=max(start, clip_start), end=min(end, clip_end)))
    return tuple(words)


def _speaker_from(raw: Mapping[str, Any]) -> str:
    for key in ("speaker", "speaker_id", "speaker_label", "role", "character"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def load_source(path: str, *, source_type: str) -> Tuple[List[SourceClip], Dict[str, SourceMeta]]:
    data = _read_json(path)
    if not isinstance(data, Mapping):
        raise ValueError("source JSON must be an object")
    key = "clips" if source_type == "config" else "segments"
    clips: List[SourceClip] = []
    meta: Dict[str, SourceMeta] = {}
    for idx, raw in enumerate(data.get(key) or [], start=1):
        if not isinstance(raw, Mapping):
            continue
        text = _clean_text(raw.get("text") or raw.get("caption") or raw.get("subtitle"))
        if not text:
            continue
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"), start)
        if end <= start:
            continue
        source_id = str(raw.get("id") or raw.get("segment_id") or idx)
        clips.append(SourceClip(
            id=source_id,
            start=start,
            end=end,
            text=text,
            words=_parse_words(raw.get("words") or [], clip_start=start, clip_end=end),
        ))
        meta[source_id] = SourceMeta(id=source_id, speaker=_speaker_from(raw))
    return clips, meta


def _coerce_translation_items(data: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        if isinstance(data.get("segments"), list):
            return [item for item in data["segments"] if isinstance(item, Mapping)]
        if isinstance(data.get("cues"), list):
            return [item for item in data["cues"] if isinstance(item, Mapping)]
        if isinstance(data.get("translations"), list):
            return [item for item in data["translations"] if isinstance(item, Mapping)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    return []


def load_translations(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    data = _read_json(path)
    mapping: Dict[str, str] = {}
    if isinstance(data, Mapping) and isinstance(data.get("translations"), Mapping):
        data = data["translations"]
    if isinstance(data, Mapping):
        for key, value in data.items():
            if isinstance(value, Mapping):
                text = value.get("target_text") or value.get("translation") or value.get("text")
            else:
                text = value
            cleaned = _clean_text(text)
            if cleaned:
                mapping[str(key)] = cleaned
    for item in _coerce_translation_items(data):
        text = _clean_text(item.get("target_text") or item.get("translation") or item.get("translated_text") or item.get("text"))
        if not text:
            continue
        keys = [
            item.get("id"),
            item.get("cue_id"),
            item.get("source_id"),
            item.get("index"),
        ]
        for key in keys:
            if key not in (None, ""):
                mapping[str(key)] = text
    return mapping


def load_voice_map(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    data = _read_json(path)
    if isinstance(data, Mapping) and isinstance(data.get("voices"), Mapping):
        data = data["voices"]
    if not isinstance(data, Mapping):
        raise ValueError("voice map must be a JSON object or contain voices{}")
    return {
        str(key): str(value.get("voice") if isinstance(value, Mapping) else value)
        for key, value in data.items()
        if value not in (None, "")
    }


def _translation_for(
    translations: Mapping[str, str],
    *,
    loc_id: str,
    cue_index: int,
    source_id: str,
) -> str:
    for key in (loc_id, str(cue_index), source_id):
        text = translations.get(key)
        if text:
            return text
    return ""


def _visible_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text)) if _looks_cjk(text) else len(text.replace("\n", ""))


def _looks_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def _word_count(text: str) -> int:
    words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", text)
    if words:
        return len(words)
    return max(1, _visible_len(text))


def estimate_tts_speed(text: str, *, duration: float, language: str) -> float:
    if not text or duration <= 0:
        return 0.0
    if language == "zh" or _looks_cjk(text):
        comfortable_cps = 5.2
        return (_visible_len(text) / duration) / comfortable_cps
    comfortable_wps = 2.7
    return (_word_count(text) / duration) / comfortable_wps


def _format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    whole = int(math.floor(seconds))
    if millis == 1000:
        whole += 1
        millis = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def build_pack(
    clips: Sequence[SourceClip],
    meta: Mapping[str, SourceMeta],
    *,
    source_path: str,
    source_type: str,
    source_language: str,
    target_language: str,
    translations: Mapping[str, str],
    voice_map: Mapping[str, str],
    default_voice: str,
    mode: str,
    speed: float,
    offset: float,
    max_chars: int,
    max_duration: float,
    max_cps: float,
    dubbing: bool,
    require_translations: bool,
    require_voices: bool,
    fail_on_readability: bool,
    max_tts_speed: float,
) -> Dict[str, Any]:
    cues = build_cues(
        clips,
        mode=mode,
        speed=speed,
        offset=offset,
        language=source_language,
        max_chars=max_chars,
        max_duration=max_duration,
    )
    segments: List[Dict[str, Any]] = []
    dubbing_tasks: List[Dict[str, Any]] = []
    counts = {
        "missing_translations": 0,
        "readability_warnings": 0,
        "dubbing_warnings": 0,
        "voice_missing": 0,
        "blocking": 0,
    }

    for cue in cues:
        loc_id = f"loc_{cue.index:03d}"
        source_meta = meta.get(cue.source_id, SourceMeta(id=cue.source_id))
        target_text = _translation_for(
            translations,
            loc_id=loc_id,
            cue_index=cue.index,
            source_id=cue.source_id,
        )
        duration = max(0.001, cue.duration)
        target_chars = _visible_len(target_text)
        target_cps = target_chars / duration if target_text else 0.0
        tts_speed = estimate_tts_speed(target_text, duration=duration, language=target_language)
        speaker = source_meta.speaker
        voice = voice_map.get(speaker) or voice_map.get("default") or default_voice
        warnings = list(cue.warnings)
        blocking_reasons: List[str] = []

        if not target_text:
            warnings.append("missing_translation")
            counts["missing_translations"] += 1
            if require_translations:
                blocking_reasons.append("missing_translation")
        if target_text and target_chars > max_chars:
            warnings.append("target_over_max_chars")
            counts["readability_warnings"] += 1
            if fail_on_readability:
                blocking_reasons.append("target_over_max_chars")
        if target_text and target_cps > max_cps:
            warnings.append("target_cps_high")
            counts["readability_warnings"] += 1
            if fail_on_readability:
                blocking_reasons.append("target_cps_high")
        if dubbing and target_text and tts_speed > max_tts_speed:
            warnings.append("tts_speed_over_limit")
            counts["dubbing_warnings"] += 1
            blocking_reasons.append("tts_speed_over_limit")
        if dubbing and require_voices and speaker and speaker not in voice_map:
            warnings.append("voice_missing")
            counts["voice_missing"] += 1
            blocking_reasons.append("voice_missing")

        counts["blocking"] += len(blocking_reasons)
        segment = {
            "id": loc_id,
            "index": cue.index,
            "source_id": cue.source_id,
            "speaker": speaker,
            "start": round(cue.start, 3),
            "end": round(cue.end, 3),
            "duration": round(cue.duration, 3),
            "source_text": cue.text,
            "target_text": target_text,
            "target_chars": target_chars,
            "target_cps": round(target_cps, 2),
            "estimated_tts_speed": round(tts_speed, 2),
            "voice": voice if dubbing else "",
            "warnings": sorted(set(warnings)),
            "blocking_reasons": blocking_reasons,
            "review_status": "blocked" if blocking_reasons else ("todo" if not target_text else "ready"),
        }
        segments.append(segment)
        if dubbing:
            dubbing_tasks.append({
                "id": f"dub_{cue.index:03d}",
                "segment_id": loc_id,
                "speaker": speaker,
                "voice": voice,
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "duration": round(cue.duration, 3),
                "text": target_text,
                "source_text": cue.text,
                "speed_hint": round(tts_speed, 2),
                "max_duration": round(cue.duration, 3),
                "warnings": segment["warnings"],
            })

    next_actions = []
    if counts["missing_translations"]:
        next_actions.append("Fill target_text for each missing localization segment, then rerun with --translations.")
    if counts["readability_warnings"]:
        next_actions.append("Shorten translated lines or split source cues to satisfy subtitle readability limits.")
    if counts["dubbing_warnings"]:
        next_actions.append("Rewrite long translated lines or allow a longer segment before sending to TTS.")
    if counts["voice_missing"]:
        next_actions.append("Add missing speaker voices to the voice map before multi-role dubbing.")

    return {
        "version": VERSION,
        "source": {
            "path": os.path.abspath(source_path),
            "type": source_type,
            "language": source_language,
        },
        "target": {
            "language": target_language,
            "dubbing": dubbing,
        },
        "settings": {
            "mode": mode,
            "speed": speed,
            "offset": offset,
            "max_chars": max_chars,
            "max_duration": max_duration,
            "max_cps": max_cps,
            "max_tts_speed": max_tts_speed,
            "require_translations": require_translations,
            "require_voices": require_voices,
            "fail_on_readability": fail_on_readability,
        },
        "summary": {
            "cue_count": len(segments),
            "duration": round(max((item["end"] for item in segments), default=0.0), 3),
            **counts,
        },
        "segments": segments,
        "dubbing_tasks": dubbing_tasks,
        "next_actions": next_actions,
    }


def emit_markdown(pack: Mapping[str, Any]) -> str:
    summary = pack.get("summary") or {}
    lines = [
        "# Localization Pack Review",
        "",
        f"- Source: `{pack.get('source', {}).get('path', '')}`",
        f"- Source language: `{pack.get('source', {}).get('language', '')}`",
        f"- Target language: `{pack.get('target', {}).get('language', '')}`",
        f"- Dubbing tasks: {'yes' if pack.get('target', {}).get('dubbing') else 'no'}",
        f"- Status: **{'BLOCKED' if summary.get('blocking') else 'READY'}**",
        f"- Segments: {summary.get('cue_count', 0)}",
        f"- Missing translations: {summary.get('missing_translations', 0)}",
        f"- Readability warnings: {summary.get('readability_warnings', 0)}",
        f"- Dubbing warnings: {summary.get('dubbing_warnings', 0)}",
        f"- Blocking items: {summary.get('blocking', 0)}",
        "",
        "## Segments",
        "",
        "| id | time | speaker | source | target | cps | tts | voice | warnings |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]
    for item in pack.get("segments") or []:
        time = f"{item.get('start', 0):.2f}-{item.get('end', 0):.2f}"
        source = str(item.get("source_text") or "").replace("|", "\\|")
        target = str(item.get("target_text") or "").replace("|", "\\|") or "TODO"
        warnings = ", ".join(item.get("warnings") or []) or "-"
        lines.append(
            "| {id} | {time} | {speaker} | {source} | {target} | {cps} | {tts} | {voice} | {warnings} |".format(
                id=item.get("id", ""),
                time=time,
                speaker=item.get("speaker") or "-",
                source=source,
                target=target,
                cps=item.get("target_cps", 0),
                tts=item.get("estimated_tts_speed", 0),
                voice=item.get("voice") or "-",
                warnings=warnings,
            )
        )
    actions = pack.get("next_actions") or []
    if actions:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines).rstrip() + "\n"


def write_srt(pack: Mapping[str, Any], path: str, *, fallback_to_source: bool = True) -> None:
    lines: List[str] = []
    for item in pack.get("segments") or []:
        text = _clean_text(item.get("target_text"))
        if not text and fallback_to_source:
            text = "[TODO] " + _clean_text(item.get("source_text"))
        lines.extend([
            str(item.get("index", len(lines) + 1)),
            f"{_format_time(_as_float(item.get('start')))} --> {_format_time(_as_float(item.get('end')))}",
            text,
            "",
        ])
    _write_text(path, "\n".join(lines))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a localization/dubbing review package")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--transcript", help="Transcript JSON from transcribe.py")
    source.add_argument("--config", help="render_final.py render_config.json")
    parser.add_argument("--target-language", required=True, help="Target language code, e.g. en, zh, ja, es")
    parser.add_argument("--source-language", default="auto", choices=["auto", "zh", "en"],
                        help="Source language for cue splitting defaults")
    parser.add_argument("--translations", help="Reviewed translations JSON to merge into target_text")
    parser.add_argument("--voice-map", help="JSON mapping speaker -> voice for dubbing tasks")
    parser.add_argument("--default-voice", default="default", help="Fallback voice label for dubbing tasks")
    parser.add_argument("--output", required=True, help="Output localization_pack JSON path")
    parser.add_argument("--markdown", help="Optional Markdown review path")
    parser.add_argument("--srt", help="Optional target-language SRT draft path")
    parser.add_argument("--mode", choices=["auto", "source", "concat"], default="auto",
                        help="Timing mode; auto uses concat for render configs and source for transcripts")
    parser.add_argument("--speed", type=float, default=1.0, help="Final playback speed")
    parser.add_argument("--offset", type=float, default=0.0, help="Seconds before first localized subtitle")
    parser.add_argument("--max-chars", type=int, default=None, help="Max target chars per cue")
    parser.add_argument("--max-duration", type=float, default=4.5, help="Max source cue duration")
    parser.add_argument("--max-cps", type=float, default=18.0, help="Max target characters per second")
    parser.add_argument("--dubbing", action="store_true", help="Emit dubbing_tasks[] with timing and voice hints")
    parser.add_argument("--require-translations", action="store_true", help="Block when target_text is missing")
    parser.add_argument("--require-voices", action="store_true", help="Block when a speaker lacks voice-map entry")
    parser.add_argument("--fail-on-readability", action="store_true",
                        help="Block when target text exceeds max chars or max cps")
    parser.add_argument("--max-tts-speed", type=float, default=1.25,
                        help="Max estimated TTS speed multiplier before dubbing blocks")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when summary.blocking > 0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    source_path = args.transcript or args.config
    source_type = "config" if args.config else "transcript"
    if not source_path or not os.path.isfile(source_path):
        print(f"source file not found: {source_path}", file=sys.stderr)
        return 1

    try:
        clips, meta = load_source(source_path, source_type=source_type)
        if not clips:
            print("no timed text found in source", file=sys.stderr)
            return 1
        source_language = infer_language(clips, args.source_language)
        mode = args.mode
        if mode == "auto":
            mode = "concat" if args.config else "source"
        target_language = args.target_language.lower()
        max_chars = args.max_chars or default_max_chars("zh" if target_language.startswith("zh") else "en")
        translations = load_translations(args.translations)
        voice_map = load_voice_map(args.voice_map)
        pack = build_pack(
            clips,
            meta,
            source_path=source_path,
            source_type=source_type,
            source_language=source_language,
            target_language=target_language,
            translations=translations,
            voice_map=voice_map,
            default_voice=args.default_voice,
            mode=mode,
            speed=args.speed,
            offset=args.offset,
            max_chars=max_chars,
            max_duration=args.max_duration,
            max_cps=args.max_cps,
            dubbing=args.dubbing,
            require_translations=args.require_translations,
            require_voices=args.require_voices,
            fail_on_readability=args.fail_on_readability,
            max_tts_speed=args.max_tts_speed,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _write_json(args.output, pack)
    if args.markdown:
        _write_text(args.markdown, emit_markdown(pack))
    if args.srt:
        write_srt(pack, args.srt)

    summary = pack["summary"]
    print(
        "Localization pack: "
        f"segments={summary['cue_count']} "
        f"missing={summary['missing_translations']} "
        f"readability={summary['readability_warnings']} "
        f"dubbing={summary['dubbing_warnings']} "
        f"blocking={summary['blocking']}",
        file=sys.stderr,
    )
    if args.strict and summary["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
