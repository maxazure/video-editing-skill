"""Refuse to burn pipeline-internal tokens onto rendered video frames.

Output frames must only contain user-facing content (titles, cleaned script,
brand marks, chapter labels). Anything that looks like a debug/build label
— speed multipliers, model names, engine names, ffmpeg filter names, tempfile
markers — should never reach drawtext or ASS rendering.

Usage:
    from _internal_text_guard import check_visible_text, InternalTextLeak
    check_visible_text(title)   # raises InternalTextLeak on violation
"""
import re


class InternalTextLeak(Exception):
    """Raised when text destined for an output frame contains internal tokens."""


_FORBIDDEN_PATTERNS = [
    # Speed multipliers: 1.25x, 2X, 0.5x
    (r"\b\d+(?:\.\d+)?\s*[xX]\b", "speed multiplier"),
    # Whisper engine names
    (r"\b(?:mlx|faster|openai)[-_]?whisper\b", "whisper engine name"),
    # Whisper model names
    (r"\bwhisper[-_]?(?:large|medium|small|base|tiny|turbo)", "whisper model name"),
    # FFmpeg filter / audio chain internal names
    (r"\b(?:atempo|dynaudnorm|loudnorm|acompressor|drawtext|ass=)\b", "ffmpeg filter name"),
    # Debug markers
    (r"\b(?:DEBUG|TODO|FIXME|XXX|HACK)\b", "debug marker"),
    # Tempfile markers
    (r"(?:_temp|_tmp|\.tmp)\b", "tempfile marker"),
]


def check_visible_text(text) -> None:
    """Raise InternalTextLeak if `text` contains a pipeline-internal token.

    Non-strings (None, numbers, etc.) are silently ignored so callers can
    pass through optional fields without conditional guards.
    """
    if not isinstance(text, str) or not text:
        return
    for pat, label in _FORBIDDEN_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raise InternalTextLeak(
                f"Refusing to burn pipeline-internal token into frame "
                f"({label}): text={text!r} matched={m.group(0)!r}"
            )


def safe_visible_text(text, fallback=""):
    """Variant that swallows the exception and returns fallback instead.

    Useful inside hot loops where you want to skip rather than crash.
    The leak is still printed to stderr for visibility.
    """
    try:
        check_visible_text(text)
        return text
    except InternalTextLeak as exc:
        import sys
        print(f"[guard] {exc}", file=sys.stderr)
        return fallback
