"""generate_caption — title/body/tag synthesis + content guard integration."""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from generate_caption import (  # noqa: E402
    extract_keywords, synthesize_title, synthesize_body,
    synthesize_tags, generate_caption,
)


def test_extract_keywords_finds_top_terms():
    text = """
    AI 失业焦虑 真的让我反思了很多。
    AI 让我接到更多客户，也让我交付更快。
    AI 是工具，不是替代我们的对手。
    """
    kws = extract_keywords(text, top_n=5)
    assert len(kws) >= 1
    assert "AI" in kws or "失业焦虑" in kws


def test_synthesize_title_uses_hook_when_short_enough():
    title = synthesize_title("AI失业焦虑？我看到更多机会", ["AI", "焦虑"])
    assert title == "AI失业焦虑？我看到更多机会"


def test_synthesize_title_truncates_long_hook():
    long_hook = "我用 AI 做了一件让我自己都不敢相信的事情而且坚持了 6 个月"
    title = synthesize_title(long_hook, [])
    assert len(title) <= 18


def test_synthesize_title_falls_back_to_keywords():
    title = synthesize_title(None, ["AI", "失业焦虑"])
    assert "AI" in title or "失业焦虑" in title


def test_synthesize_body_within_limits():
    long_script = "AI 真的能帮我们。" * 200
    body = synthesize_body(long_script, ["AI"])
    assert 100 <= len(body) <= 700  # account for emoji insertion


def test_synthesize_body_contains_emoji():
    body = synthesize_body("AI 帮我提效。" * 30, ["AI"])
    # at least one emoji from the palette should appear
    palette = "📌✨💡🔥👇✅🚀📈"
    assert any(c in body for c in palette)


def test_synthesize_tags_within_count():
    tags = synthesize_tags(["AI", "失业", "创业", "工作"], min_tags=3, max_tags=5)
    assert 3 <= len(tags) <= 5
    assert all(t.startswith("#") for t in tags)


def test_generate_caption_full_round_trip(tmp_path):
    script = (tmp_path / "clean.md")
    script.write_text(
        "# Clean Script\n\n## Hook\n"
        "AI失业焦虑？我看到更多机会\n\n"
        "## Pain\n之前我也焦虑，怕 AI 替代人。\n\n"
        "## Turn\n但客户找我的次数没减少。\n\n"
        "## Value\nAI 让我交付更快。\n\n"
        "## CTA\n你是焦虑还是抓住机会？\n"
    )
    payload = generate_caption(open(script).read())
    assert "title" in payload and 4 <= len(payload["title"]) <= 25
    assert "caption_body" in payload
    assert isinstance(payload["tags"], list)
    assert 3 <= len(payload["tags"]) <= 6
    assert payload["publish_time_hint"]


def test_generate_caption_rejects_hardblock_title():
    # script with a diversion phrase that becomes the hook → title trips guard
    script = "## Hook\n加微信 wx123\n## Value\nx"
    with pytest.raises(Exception):  # HardBlock or similar
        generate_caption(script)


def test_cli(tmp_path):
    script = tmp_path / "clean.md"
    script.write_text(
        "## Hook\nAI失业焦虑\n## Pain\n我担心被替代\n## CTA\n你怎么看？\n"
    )
    out_path = tmp_path / "caption.json"
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/generate_caption.py"),
         "--script", str(script), "--output", str(out_path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    payload = json.loads(out_path.read_text())
    assert "title" in payload
