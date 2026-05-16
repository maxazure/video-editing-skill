"""auto_chapter_cards — markdown parsing, placement, manifest."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from auto_chapter_cards import parse_chapters_from_md, schedule_cards, ChapterCard  # noqa: E402


def test_parse_chapters_from_md(tmp_path):
    md = tmp_path / "script.md"
    md.write_text(
        "# Clean Script\n\n## Hook\nopening\n\n## Pain\npain stuff\n\n### sub\n\n## Value\n"
    )
    titles = parse_chapters_from_md(str(md))
    assert titles == ["Hook", "Pain", "Value"]


def test_parse_returns_empty_for_missing_file():
    assert parse_chapters_from_md("/nonexistent.md") == []


def test_schedule_returns_card_per_title():
    cards = schedule_cards(["Hook", "Pain", "Value"], total_duration=90)
    assert len(cards) == 3
    for c in cards:
        assert isinstance(c, ChapterCard)
        assert c.start >= 3.0
        assert c.start < 90
        assert c.duration > 0


def test_schedule_respects_max_cards():
    cards = schedule_cards(["a", "b", "c", "d", "e", "f", "g"], max_cards=3)
    assert len(cards) == 3


def test_schedule_snaps_to_provided_boundaries():
    cards = schedule_cards(["A", "B"], boundaries=[7.5, 30.0], total_duration=90)
    assert cards[0].start == 7.5
    assert cards[1].start == 30.0


def test_schedule_palette_rotation():
    cards = schedule_cards(["A", "B", "C"], total_duration=90)
    colors = {c.color for c in cards}
    # at least 2 unique colors (palette rotates)
    assert len(colors) >= 2
