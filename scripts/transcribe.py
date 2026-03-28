#!/usr/bin/env python3
"""
Transcribe audio using Whisper and output a JSON file with per-sentence timestamps.

Supports two engines (auto-detected):
  - faster-whisper (recommended, 4x faster, less memory)
  - openai-whisper (fallback)

Usage: python3 transcribe.py <audio_path> [--model auto] [--language zh] [--engine auto]
Output: <audio_dir>/<video_name>_transcript.json
"""

import argparse
import json
import os
import sys

# Allow importing utils from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    detect_gpu, detect_whisper_engine, recommend_whisper_model,
    get_whisper_device, setup_china_env, detect_platform,
)

# Common filler words/phrases by language
FILLER_PATTERNS = {
    "zh": [
        "嗯", "呃", "额", "啊", "哦", "唔",
        "那个", "就是", "就是说", "然后呢", "怎么说呢",
        "对吧", "你知道吗", "我觉得吧", "基本上",
    ],
    "en": [
        "um", "uh", "erm", "ah", "oh",
        "like", "you know", "i mean", "basically",
        "actually", "literally", "right", "so yeah",
        "kind of", "sort of",
    ],
}


def transcribe_faster_whisper(audio_path, model_name, language, device, compute_type,
                              word_timestamps=False):
    """Transcribe using faster-whisper engine."""
    from faster_whisper import WhisperModel

    print(f"[faster-whisper] Loading model: {model_name} (device={device}, compute={compute_type})")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    kwargs = {"word_timestamps": word_timestamps}
    if language:
        kwargs["language"] = language

    segments_iter, info = model.transcribe(audio_path, **kwargs)
    detected_lang = info.language

    segments = []
    for i, seg in enumerate(segments_iter, start=1):
        entry = {
            "id": i,
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        }
        if word_timestamps and seg.words:
            entry["words"] = [
                {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                }
                for w in seg.words if w.word.strip()
            ]
        segments.append(entry)

    return segments, detected_lang


def transcribe_openai_whisper(audio_path, model_name, language, word_timestamps=False):
    """Transcribe using openai-whisper engine."""
    import whisper

    print(f"[openai-whisper] Loading model: {model_name}")
    model = whisper.load_model(model_name)

    kwargs = {"word_timestamps": word_timestamps}
    if language:
        kwargs["language"] = language

    result = model.transcribe(audio_path, **kwargs)
    detected_lang = result.get("language", "unknown")

    segments = []
    for i, seg in enumerate(result.get("segments", []), start=1):
        entry = {
            "id": i,
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
            "text": seg["text"].strip(),
        }
        if word_timestamps and seg.get("words"):
            entry["words"] = [
                {
                    "word": w["word"].strip(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                }
                for w in seg["words"] if w.get("word", "").strip()
            ]
        segments.append(entry)

    return segments, detected_lang


def detect_silences(segments, min_gap=1.0):
    """Detect silent gaps between speech segments.

    Args:
        segments: List of {"id", "start", "end", "text"} dicts.
        min_gap: Minimum gap duration (seconds) to flag as silence.

    Returns:
        List of {"start", "end", "duration", "before_segment", "after_segment"} dicts.
    """
    silences = []
    for i in range(1, len(segments)):
        gap_start = segments[i - 1]["end"]
        gap_end = segments[i]["start"]
        gap_dur = gap_end - gap_start
        if gap_dur >= min_gap:
            silences.append({
                "start": round(gap_start, 2),
                "end": round(gap_end, 2),
                "duration": round(gap_dur, 2),
                "before_segment": segments[i - 1]["id"],
                "after_segment": segments[i]["id"],
            })
    return silences


def detect_filler_words(segments, language="zh"):
    """Detect filler words/phrases in transcript segments.

    Args:
        segments: List of {"id", "start", "end", "text"} dicts.
        language: Language code ("zh", "en", etc.)

    Returns:
        List of {"segment_id", "text", "fillers_found", "is_filler_only"} dicts.
    """
    import re
    lang_key = "zh" if language and language.startswith("zh") else "en"
    patterns = FILLER_PATTERNS.get(lang_key, FILLER_PATTERNS["en"])

    results = []
    for seg in segments:
        text = seg["text"].strip()
        text_lower = text.lower()
        found = []
        for filler in patterns:
            if lang_key == "zh":
                if filler in text:
                    found.append(filler)
            else:
                if re.search(r'\b' + re.escape(filler) + r'\b', text_lower):
                    found.append(filler)

        if found:
            clean = text_lower
            for f in patterns:
                if lang_key == "zh":
                    clean = clean.replace(f, "")
                else:
                    clean = re.sub(r'\b' + re.escape(f) + r'\b', '', clean)
            clean = re.sub(r'[^\w]', '', clean).strip()
            is_filler_only = len(clean) == 0 or (len(text) <= 6 and len(found) > 0)

            results.append({
                "segment_id": seg["id"],
                "text": text,
                "fillers_found": found,
                "is_filler_only": is_filler_only,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with Whisper")
    parser.add_argument("audio_path", help="Path to the audio file (.wav)")
    parser.add_argument("--model", default="auto",
                        help="Whisper model size: tiny/base/small/medium/large-v3/large-v3-turbo/auto (default: auto)")
    parser.add_argument("--language", default=None,
                        help="Language code (e.g. zh, en, ja). Omit for auto-detection.")
    parser.add_argument("--engine", default="auto", choices=["auto", "faster-whisper", "openai-whisper"],
                        help="Whisper engine (default: auto-detect)")
    parser.add_argument("--mirror", action="store_true",
                        help="Use China mirrors for model download")
    parser.add_argument("--silence-threshold", type=float, default=1.0,
                        help="Min gap (seconds) to flag as silence (default: 1.0). Set 0 to disable.")
    parser.add_argument("--word-timestamps", action="store_true",
                        help="Include per-word timestamps (required for karaoke subtitles)")
    parser.add_argument("--detect-fillers", action="store_true",
                        help="Detect filler words (um, uh, 嗯, 呃, etc.) and mark segments")
    args = parser.parse_args()

    audio_path = os.path.abspath(args.audio_path)
    if not os.path.isfile(audio_path):
        print(f"Error: File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    # China mirror setup
    if args.mirror:
        os.environ["USE_CN_MIRROR"] = "1"
    setup_china_env()

    # Detect GPU & hardware
    gpu_info = detect_gpu()

    # Choose engine
    if args.engine == "auto":
        engine = detect_whisper_engine()
        if engine == "none":
            print("Error: No Whisper engine found.", file=sys.stderr)
            print("Install one of:", file=sys.stderr)
            print("  pip install faster-whisper  (recommended, 4x faster)", file=sys.stderr)
            print("  pip install openai-whisper", file=sys.stderr)
            sys.exit(1)
    else:
        engine = args.engine

    # Choose model
    if args.model == "auto":
        model_name, reason = recommend_whisper_model(gpu_info)
        print(f"[auto] Selected model: {model_name} ({reason})")
    else:
        model_name = args.model

    print(f"Engine: {engine}")
    print(f"Model: {model_name}")
    print(f"GPU: {gpu_info['type']}")
    print(f"Transcribing: {audio_path}")

    # Run transcription
    if args.word_timestamps:
        print("Word-level timestamps: enabled (for karaoke subtitles)")

    if engine == "faster-whisper":
        device, compute_type = get_whisper_device(gpu_info)
        segments, detected_lang = transcribe_faster_whisper(
            audio_path, model_name, args.language, device, compute_type,
            word_timestamps=args.word_timestamps,
        )
    else:
        segments, detected_lang = transcribe_openai_whisper(
            audio_path, model_name, args.language,
            word_timestamps=args.word_timestamps,
        )

    # Build output path
    audio_dir = os.path.dirname(audio_path)
    audio_name = os.path.splitext(os.path.basename(audio_path))[0]
    video_name = audio_name.replace("_audio", "")
    output_path = os.path.join(audio_dir, f"{video_name}_transcript.json")

    # Detect silences
    silences = []
    if args.silence_threshold > 0 and len(segments) > 1:
        silences = detect_silences(segments, min_gap=args.silence_threshold)

    output_data = {
        "source_audio": audio_path,
        "engine": engine,
        "model": model_name,
        "language": detected_lang,
        "segments": segments,
    }
    if silences:
        output_data["silences"] = silences

    # Detect filler words
    fillers = []
    if args.detect_fillers:
        fillers = detect_filler_words(segments, detected_lang)
        if fillers:
            output_data["filler_words"] = fillers

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\nTranscription complete: {output_path}")
    print(f"Total segments: {len(segments)}")

    # Report silences
    if silences:
        total_silence = sum(s["duration"] for s in silences)
        print(f"\nDetected {len(silences)} silent gaps (>= {args.silence_threshold}s), total {total_silence:.1f}s:")
        for s in silences:
            print(f"  {s['start']:7.2f}s - {s['end']:7.2f}s ({s['duration']:.1f}s gap, between #{s['before_segment']} and #{s['after_segment']})")
        print(f"\nTip: These gaps are likely stammers, pauses, or hesitations.")
        print(f"     Exclude these segment ranges when building render_config.json.")
    else:
        print(f"\nNo significant silences detected (threshold: {args.silence_threshold}s).")

    # Report fillers
    if args.detect_fillers and fillers:
        filler_only = [f for f in fillers if f["is_filler_only"]]
        print(f"\nDetected filler words in {len(fillers)} segments ({len(filler_only)} filler-only):")
        for f in fillers[:10]:
            marker = " ← SKIP" if f["is_filler_only"] else ""
            print(f"  #{f['segment_id']:3d} [{', '.join(f['fillers_found'])}] \"{f['text']}\"{marker}")
        if len(fillers) > 10:
            print(f"  ... and {len(fillers) - 10} more")

    print("\nSegment preview:")
    for seg in segments[:5]:
        print(f"  #{seg['id']:3d} [{seg['start']:7.2f}s - {seg['end']:7.2f}s] {seg['text']}")
    if len(segments) > 5:
        print(f"  ... and {len(segments) - 5} more segments")


if __name__ == "__main__":
    main()
