#!/usr/bin/env python3
"""Export readable subtitle sidecars from transcript or render config JSON.

This is a local delivery tool: it does not transcribe, translate, sync, or call
AI providers. It takes timed text that already exists in this pipeline and
creates SRT/VTT/ASS plus a JSON manifest for platform upload and human review.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import sys
from typing import Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from burn_subtitles import detect_language, escape_ass_text  # noqa: E402


VERSION = "subtitle_pack.v1"


@dataclasses.dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


@dataclasses.dataclass(frozen=True)
class SourceClip:
    id: str
    start: float
    end: float
    text: str
    words: Tuple[TimedWord, ...] = ()


@dataclasses.dataclass(frozen=True)
class SubtitleCue:
    index: int
    start: float
    end: float
    text: str
    source_id: str
    warnings: Tuple[str, ...] = ()

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_words(raw_words: Iterable[dict], *, clip_start: float, clip_end: float) -> Tuple[TimedWord, ...]:
    words: List[TimedWord] = []
    for raw in raw_words or []:
        text = _clean_text(raw.get("word") or raw.get("text") or "")
        if not text:
            continue
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"), start)
        if end < clip_start or start > clip_end:
            continue
        words.append(TimedWord(text=text, start=max(start, clip_start), end=min(end, clip_end)))
    return tuple(words)


def load_transcript(path: str) -> List[SourceClip]:
    data = _read_json(path)
    clips = []
    for idx, seg in enumerate(data.get("segments") or [], start=1):
        text = _clean_text(seg.get("text", ""))
        if not text:
            continue
        start = _as_float(seg.get("start"))
        end = _as_float(seg.get("end"), start)
        if end <= start:
            continue
        clips.append(SourceClip(
            id=str(seg.get("id", idx)),
            start=start,
            end=end,
            text=text,
            words=_parse_words(seg.get("words") or [], clip_start=start, clip_end=end),
        ))
    return clips


def load_render_config(path: str) -> List[SourceClip]:
    data = _read_json(path)
    clips = []
    for idx, clip in enumerate(data.get("clips") or [], start=1):
        text = _clean_text(clip.get("text", ""))
        if not text:
            continue
        start = _as_float(clip.get("start"))
        end = _as_float(clip.get("end"), start)
        if end <= start:
            continue
        clips.append(SourceClip(
            id=str(clip.get("id") or clip.get("segment_id") or idx),
            start=start,
            end=end,
            text=text,
            words=_parse_words(clip.get("words") or [], clip_start=start, clip_end=end),
        ))
    return clips


def infer_language(clips: Sequence[SourceClip], override: str = "auto") -> str:
    if override != "auto":
        return override
    sample = " ".join(clip.text for clip in clips[:8])
    return detect_language(sample or "text")


def default_max_chars(language: str) -> int:
    return 18 if language == "zh" else 42


def _visible_len(text: str) -> int:
    return len(text.replace("\n", ""))


def _find_break(text: str, max_chars: int, language: str) -> int:
    if len(text) <= max_chars:
        return len(text)
    window = text[:max_chars + 1]
    break_chars = "，。！？；：、,.!?;: "
    best = -1
    for idx, char in enumerate(window):
        if char in break_chars:
            best = idx + 1
    if best >= max(4, int(max_chars * 0.45)):
        return best
    if language == "en":
        space = window.rfind(" ")
        if space >= max(4, int(max_chars * 0.45)):
            return space + 1
    cut = max_chars
    while (
        language != "zh"
        and cut > 1
        and cut < len(text)
        and text[cut - 1].isalnum()
        and text[cut].isalnum()
    ):
        cut -= 1
    return cut if cut > 1 else max_chars


def split_text(text: str, *, max_chars: int, language: str) -> List[str]:
    remaining = _clean_text(text)
    chunks: List[str] = []
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        cut = _find_break(remaining, max_chars, language)
        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].strip()
    return chunks


def _join_words(words: Sequence[TimedWord], language: str) -> str:
    if language == "zh":
        text = "".join(word.text for word in words)
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", text)
        return text.strip()
    return " ".join(word.text for word in words).strip()


def _group_words(
    words: Sequence[TimedWord],
    *,
    max_chars: int,
    max_duration: float,
    language: str,
) -> List[Tuple[float, float, str, Tuple[str, ...]]]:
    groups: List[Tuple[float, float, str, Tuple[str, ...]]] = []
    current: List[TimedWord] = []

    def flush() -> None:
        if not current:
            return
        text = _join_words(current, language)
        warnings: List[str] = []
        if _visible_len(text) > max_chars:
            warnings.append("over_max_chars")
        groups.append((current[0].start, current[-1].end, text, tuple(warnings)))
        current.clear()

    for word in words:
        candidate = current + [word]
        candidate_text = _join_words(candidate, language)
        candidate_duration = candidate[-1].end - candidate[0].start
        too_long = _visible_len(candidate_text) > max_chars and current
        too_slow = candidate_duration > max_duration and current
        if too_long or too_slow:
            flush()
        current.append(word)
    flush()
    return groups


def _proportional_cues(
    clip: SourceClip,
    *,
    output_start: float,
    speed: float,
    max_chars: int,
    language: str,
) -> List[Tuple[float, float, str, Tuple[str, ...]]]:
    chunks = split_text(clip.text, max_chars=max_chars, language=language)
    if not chunks:
        return []
    total_units = sum(max(1, _visible_len(chunk)) for chunk in chunks)
    scaled_duration = (clip.end - clip.start) / speed
    cursor = output_start
    result = []
    for idx, chunk in enumerate(chunks):
        if idx == len(chunks) - 1:
            end = output_start + scaled_duration
        else:
            share = max(1, _visible_len(chunk)) / total_units
            end = cursor + scaled_duration * share
        warnings: List[str] = []
        if _visible_len(chunk) > max_chars:
            warnings.append("over_max_chars")
        result.append((cursor, end, chunk, tuple(warnings)))
        cursor = end
    return result


def build_cues(
    clips: Sequence[SourceClip],
    *,
    mode: str,
    speed: float = 1.0,
    offset: float = 0.0,
    language: str = "auto",
    max_chars: Optional[int] = None,
    max_duration: float = 4.5,
) -> List[SubtitleCue]:
    if speed <= 0:
        raise ValueError("speed must be greater than 0")
    detected_language = infer_language(clips, language)
    cue_max_chars = max_chars or default_max_chars(detected_language)
    cues: List[SubtitleCue] = []
    cursor = offset

    for clip in clips:
        if mode == "concat":
            clip_output_start = cursor
            cursor += (clip.end - clip.start) / speed
        elif mode == "source":
            clip_output_start = offset + clip.start / speed
        else:
            raise ValueError(f"unknown mode: {mode}")

        if clip.words:
            mapped_words = [
                TimedWord(
                    text=word.text,
                    start=clip_output_start + (word.start - clip.start) / speed,
                    end=clip_output_start + (word.end - clip.start) / speed,
                )
                for word in clip.words
            ]
            pieces = _group_words(
                mapped_words,
                max_chars=cue_max_chars,
                max_duration=max_duration,
                language=detected_language,
            )
        else:
            pieces = _proportional_cues(
                clip,
                output_start=clip_output_start,
                speed=speed,
                max_chars=cue_max_chars,
                language=detected_language,
            )

        for start, end, text, warnings in pieces:
            if not text:
                continue
            cues.append(SubtitleCue(
                index=len(cues) + 1,
                start=round(max(0.0, start), 3),
                end=round(max(start + 0.01, end), 3),
                text=text,
                source_id=clip.id,
                warnings=warnings,
            ))
    return cues


def _format_srt_time(seconds: float) -> str:
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


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    centis = int(round((seconds - math.floor(seconds)) * 100))
    whole = int(math.floor(seconds))
    if centis == 100:
        whole += 1
        centis = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h}:{m:02d}:{s:02d}.{centis:02d}"


def write_srt(cues: Sequence[SubtitleCue], path: str) -> None:
    lines: List[str] = []
    for cue in cues:
        lines.extend([
            str(cue.index),
            f"{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}",
            cue.text,
            "",
        ])
    _write_text(path, "\n".join(lines))


def write_vtt(cues: Sequence[SubtitleCue], path: str) -> None:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.extend([
            f"{_format_vtt_time(cue.start)} --> {_format_vtt_time(cue.end)}",
            cue.text,
            "",
        ])
    _write_text(path, "\n".join(lines))


def write_ass(
    cues: Sequence[SubtitleCue],
    path: str,
    *,
    font_name: str = "Arial",
    font_size: int = 48,
    width: int = 1080,
    height: int = 1920,
) -> None:
    margin_lr = 60
    margin_v = int(height * 0.28)
    header = f"""[Script Info]
Title: Subtitle Pack
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for cue in cues:
        text = escape_ass_text(cue.text).replace("\n", "\\N")
        rows.append(
            f"Dialogue: 0,{_format_ass_time(cue.start)},{_format_ass_time(cue.end)},Default,,0,0,0,,{text}"
        )
    _write_text(path, header + "\n".join(rows) + "\n")


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_json_manifest(
    cues: Sequence[SubtitleCue],
    path: str,
    *,
    source_path: str,
    mode: str,
    speed: float,
    offset: float,
    language: str,
    max_chars: int,
    format_paths: Optional[dict] = None,
) -> None:
    warnings = sorted({warning for cue in cues for warning in cue.warnings})
    payload = {
        "version": VERSION,
        "source": os.path.abspath(source_path),
        "mode": mode,
        "settings": {
            "speed": speed,
            "offset": offset,
            "language": language,
            "max_chars": max_chars,
        },
        "stats": {
            "cue_count": len(cues),
            "duration": round(max((cue.end for cue in cues), default=0.0), 3),
            "warnings": warnings,
        },
        "formats": format_paths or {},
        "cues": [
            {
                "index": cue.index,
                "start": cue.start,
                "end": cue.end,
                "duration": round(cue.duration, 3),
                "text": cue.text,
                "source_id": cue.source_id,
                "warnings": list(cue.warnings),
            }
            for cue in cues
        ],
    }
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _derive_basename(path: str) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    for suffix in ("_transcript", "_render_config", "render_config"):
        if name.endswith(suffix):
            name = name[: -len(suffix)] or "subtitles"
    return name or "subtitles"


def _parse_formats(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if item not in {"srt", "vtt", "ass", "json"}:
                raise ValueError(f"unknown subtitle format: {item}")
            if item not in result:
                result.append(item)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export SRT/VTT/ASS subtitle sidecars")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--transcript", help="Transcript JSON from transcribe.py")
    source.add_argument("--config", help="render_final.py render_config.json")
    parser.add_argument("--mode", choices=["auto", "source", "concat"], default="auto",
                        help="Timing mode: source keeps original timestamps; concat follows render_config order")
    parser.add_argument("--output-dir", required=True, help="Directory for subtitle files")
    parser.add_argument("--basename", default=None, help="Output file basename")
    parser.add_argument("--formats", nargs="+", default=["srt", "vtt", "ass", "json"],
                        help="Formats to write: srt vtt ass json, or comma-separated")
    parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto",
                        help="Language for line splitting defaults")
    parser.add_argument("--max-chars", type=int, default=None,
                        help="Max visible characters per cue; defaults to 18 for zh, 42 for en")
    parser.add_argument("--max-duration", type=float, default=4.5,
                        help="Max cue duration when word timestamps are available")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Final playback speed, e.g. 1.25 for render_final --primary-speed 1.25")
    parser.add_argument("--offset", type=float, default=0.0,
                        help="Seconds to add before first subtitle, e.g. cover duration")
    parser.add_argument("--ass-font-name", default="Arial")
    parser.add_argument("--font-size", type=int, default=48)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    source_path = args.transcript or args.config
    if not source_path or not os.path.isfile(source_path):
        print(f"source file not found: {source_path}", file=sys.stderr)
        return 1
    try:
        formats = _parse_formats(args.formats)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    clips = load_render_config(source_path) if args.config else load_transcript(source_path)
    if not clips:
        print("no subtitle text found in source", file=sys.stderr)
        return 1

    mode = args.mode
    if mode == "auto":
        mode = "concat" if args.config else "source"
    language = infer_language(clips, args.language)
    max_chars = args.max_chars or default_max_chars(language)

    try:
        cues = build_cues(
            clips,
            mode=mode,
            speed=args.speed,
            offset=args.offset,
            language=language,
            max_chars=max_chars,
            max_duration=args.max_duration,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not cues:
        print("no subtitle cues generated", file=sys.stderr)
        return 1

    basename = args.basename or _derive_basename(source_path)
    os.makedirs(args.output_dir, exist_ok=True)
    paths = {
        fmt: os.path.abspath(os.path.join(args.output_dir, f"{basename}.{fmt}"))
        for fmt in formats
    }

    if "srt" in paths:
        write_srt(cues, paths["srt"])
    if "vtt" in paths:
        write_vtt(cues, paths["vtt"])
    if "ass" in paths:
        write_ass(
            cues,
            paths["ass"],
            font_name=args.ass_font_name,
            font_size=args.font_size,
            width=args.width,
            height=args.height,
        )
    if "json" in paths:
        write_json_manifest(
            cues,
            paths["json"],
            source_path=source_path,
            mode=mode,
            speed=args.speed,
            offset=args.offset,
            language=language,
            max_chars=max_chars,
            format_paths=paths,
        )

    print(f"subtitle cues: {len(cues)}")
    for fmt in formats:
        print(f"{fmt}: {paths[fmt]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
