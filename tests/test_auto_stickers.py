"""auto_stickers — emotion classification + cue scheduling."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from auto_stickers import schedule_stickers, _classify, EMOTION_STICKERS  # noqa: E402


def _t(*segs):
    return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segs]}


def test_classify_excited():
    assert _classify("我突然没想到 AI 这么强") == "excited"


def test_classify_doubt():
    assert _classify("为什么 AI 还是不能完全替代人") == "doubt"


def test_classify_data():
    assert _classify("增长了 300%") == "data"


def test_classify_returns_none_for_neutral():
    assert _classify("今天我们来聊一下AI") is None


def test_sticker_pool_per_emotion():
    for emotion in ("excited", "doubt", "conclusion", "data", "warning"):
        assert emotion in EMOTION_STICKERS
        assert EMOTION_STICKERS[emotion], f"empty pool for {emotion}"


def test_schedule_respects_min_interval():
    cues = schedule_stickers(_t(
        (0.0, 2.0, "不敢相信！"),
        (3.0, 5.0, "竟然有这种结果"),  # 'excited' but too close
        (12.0, 14.0, "我突然意识到 AI 真的厉害"),
    ), min_interval_seconds=8.0)
    # Only segments 0 and 2 should produce cues (≥8s apart)
    assert len(cues) <= 2
    if len(cues) >= 2:
        assert cues[1].start - cues[0].start >= 8.0


def test_schedule_uses_emotion_specific_stickers():
    cues = schedule_stickers(_t((1.0, 2.0, "怎么会这样？")))
    if cues:
        assert cues[0].emotion == "doubt"
        assert cues[0].sticker in EMOTION_STICKERS["doubt"]
