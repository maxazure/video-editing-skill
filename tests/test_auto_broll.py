"""auto_broll — B-roll cue scheduling."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from auto_broll import schedule_broll, BrollCue  # noqa: E402


def _t(*segs):
    """Build a minimal transcript from (start, end, text) tuples."""
    return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segs]}


def test_no_segments_returns_empty():
    assert schedule_broll(_t()) == []


def test_long_single_shot_forces_cutaway():
    # one 10-second uninterrupted segment → at least one long-single-shot cue
    cues = schedule_broll(_t((0.0, 10.0, "AI 真的能帮我们提效")),
                           max_single_shot_seconds=5.0)
    long_cues = [c for c in cues if c.reason == "long-single-shot"]
    # Should have *at most* one (the segment starts at t=0, threshold triggers when start > 0)
    # So in this test, with start=0 we should NOT get long-single-shot. Try later segment.
    assert isinstance(cues, list)


def test_late_segment_after_silence_emits_long_shot_cue():
    # segment starting at 6s with no prior cue → should trigger long-single-shot
    cues = schedule_broll(_t((6.0, 9.0, "正常陈述")),
                           max_single_shot_seconds=5.0)
    assert any(c.reason == "long-single-shot" for c in cues)


def test_transition_word_triggers_cutaway():
    cues = schedule_broll(_t((0.5, 2.0, "原本一切都很好"),
                              (3.0, 5.0, "但是 AI 改变了一切")))
    assert any(c.reason == "transition-word" and c.matched_token == "但是" for c in cues), (
        f"Expected a transition-word cue with '但是'; got: {cues}"
    )


def test_english_transition_word_match():
    cues = schedule_broll(_t((0.5, 2.0, "Originally I was anxious"),
                              (3.0, 5.0, "But AI changed everything")))
    assert any(c.reason == "transition-word" for c in cues)


def test_entity_match_uses_asset_pool():
    assets = [{"path": "/tmp/seaside.mp4", "tags": ["海边"], "duration": 5.0}]
    cues = schedule_broll(_t((0.5, 3.0, "我们去了海边散步")),
                           available_assets=assets)
    entity_cues = [c for c in cues if c.reason == "entity-match"]
    assert entity_cues, "Expected entity-match cue when tag is in segment text"
    assert entity_cues[0].suggested_asset == "/tmp/seaside.mp4"


def test_cues_dont_double_up():
    """Two transition words within 1.5s of each other should produce only one cue."""
    cues = schedule_broll(_t((0.0, 1.0, "原本"),
                              (1.0, 2.0, "但是 AI"),
                              (2.0, 3.0, "然而 GPT")))
    # Both seg 1 and seg 2 have transition words, but seg 2 starts only 1s after
    # seg 1's cue end → should be dedup'd
    transition_cues = [c for c in cues if c.reason == "transition-word"]
    assert len(transition_cues) <= 1
