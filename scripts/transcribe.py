#!/usr/bin/env python3
"""
Transcribe audio using OpenAI Whisper and output a JSON file with per-sentence timestamps.
Usage: python3 transcribe.py <audio_path> [--model base] [--language zh]
Output: <audio_dir>/<video_name>_transcript.json
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with Whisper")
    parser.add_argument("audio_path", help="Path to the audio file (.wav)")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (default: base)")
    parser.add_argument("--language", default=None,
                        help="Language code (e.g. zh, en, ja). Omit for auto-detection.")
    args = parser.parse_args()

    audio_path = args.audio_path
    if not os.path.isfile(audio_path):
        print(f"Error: File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    try:
        import whisper
    except ImportError:
        print("Error: openai-whisper is not installed. Run: pip install openai-whisper", file=sys.stderr)
        sys.exit(1)

    print(f"Loading Whisper model: {args.model}")
    model = whisper.load_model(args.model)

    print(f"Transcribing: {audio_path}")
    transcribe_options = {}
    if args.language:
        transcribe_options["language"] = args.language

    result = model.transcribe(audio_path, **transcribe_options)

    segments = []
    for i, seg in enumerate(result.get("segments", []), start=1):
        segments.append({
            "id": i,
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
            "text": seg["text"].strip()
        })

    audio_dir = os.path.dirname(os.path.abspath(audio_path))
    audio_name = os.path.splitext(os.path.basename(audio_path))[0]
    # Remove _audio suffix if present to get video name
    video_name = audio_name.replace("_audio", "")
    output_path = os.path.join(audio_dir, f"{video_name}_transcript.json")

    output_data = {
        "source_audio": audio_path,
        "model": args.model,
        "language": result.get("language", "unknown"),
        "segments": segments
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\nTranscription complete: {output_path}")
    print(f"Total segments: {len(segments)}")
    print("\nSegment preview:")
    for seg in segments[:5]:
        print(f"  #{seg['id']:3d} [{seg['start']:7.2f}s - {seg['end']:7.2f}s] {seg['text']}")
    if len(segments) > 5:
        print(f"  ... and {len(segments) - 5} more segments")


if __name__ == "__main__":
    main()
