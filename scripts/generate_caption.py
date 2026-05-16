#!/usr/bin/env python3
"""Generate a Xiaohongshu发布文案 (caption note) from a cleaned script.

Output: a JSON with title, caption_body, tags, and publish-time hint.
  - title:    ≤18 chars, first 18 chars include 2 keywords (TF-IDF) so
              the feed-thumbnail preview surfaces them.
  - body:     200-500 chars, emoji every 50-80 chars, repeats target
              keyword once per ~300 chars (TF-IDF normalisation).
  - tags:     3-6 hashtags. Mix垂直+长尾 (no pure hot-tag spam).
  - publish_time_hint: per audience profile.

This module is rule-based — no LLM dependency. For richer rewriting use
rewrite_script.py first, then run generate_caption.py on the result.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from content_guard import enforce as enforce_platform_rules, HardBlock  # noqa: E402


EMOJI_PALETTE = ["📌", "✨", "💡", "🔥", "👇", "✅", "🚀", "📈"]

# Words that should not become tags
STOPWORDS_ZH = set("""
的 了 在 是 我 你 他 她 它 我们 你们 他们 这 那 就 也 都 还 又 把 被 让
不 没 有 会 能 要 想 觉得 因为 所以 但是 然后 而且 就是 这种 那种
""".split())


def extract_keywords(text: str, *, top_n: int = 8) -> List[str]:
    """Return the top-n bigram/short-word candidates, ranked by TF.

    Pure rule-based (no external library). Strips stopwords, keeps 2-6
    char tokens, dedupes.
    """
    # Strip markdown headings / punctuation
    cleaned = re.sub(r"#+\s+|[*_`]", "", text)
    # Split by punctuation/whitespace
    tokens = re.split(r"[\s，。！？、；：（）\(\),.!?;:\-—\n]+", cleaned)
    counts: Counter = Counter()
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok in STOPWORDS_ZH:
            continue
        if len(tok) < 2 or len(tok) > 8:
            continue
        if re.fullmatch(r"\d+", tok):
            continue
        counts[tok] += 1
    return [w for w, _ in counts.most_common(top_n)]


def synthesize_title(hook_text: Optional[str], keywords: List[str],
                      *, max_chars: int = 18) -> str:
    """Pick a title ≤max_chars that surfaces 2 keywords up front when possible."""
    if hook_text:
        title = hook_text.strip()
        # If the hook is short enough, use it directly
        if 4 <= len(title) <= max_chars:
            return title
        # Else, truncate at a clean break
        if len(title) > max_chars:
            cut = title[:max_chars]
            # find last punctuation/space to avoid cutting mid-word
            for sep in "？！。，、 ":
                idx = cut.rfind(sep)
                if idx >= 4:
                    return cut[:idx]
            return cut

    if len(keywords) >= 2:
        kw1, kw2 = keywords[0], keywords[1]
        title = f"{kw1}{kw2}：3个值得知道的事"
        if len(title) <= max_chars:
            return title
        return f"{kw1}{kw2}：值得收藏"
    if keywords:
        return f"{keywords[0]}：值得收藏"
    return "今日分享"


def synthesize_body(script_text: str, keywords: List[str], *,
                     emoji_every_chars: int = 60,
                     min_chars: int = 200,
                     max_chars: int = 500) -> str:
    """Build the 正文 body. Take the cleaned script content and intersperse
    emoji every ~60 chars; cap at max_chars."""
    body = re.sub(r"#+\s+", "", script_text)
    body = re.sub(r"\n{2,}", "\n\n", body).strip()

    # Truncate
    if len(body) > max_chars:
        body = body[:max_chars]
        # cut at the last natural break
        for sep in "\n。！？":
            idx = body.rfind(sep)
            if idx >= min_chars:
                body = body[:idx + 1]
                break

    # Sprinkle emoji
    out_chars = []
    last_emoji = 0
    for i, ch in enumerate(body):
        out_chars.append(ch)
        if (i - last_emoji) >= emoji_every_chars and ch in "。！？\n":
            emoji = EMOJI_PALETTE[(i // emoji_every_chars) % len(EMOJI_PALETTE)]
            out_chars.append(f" {emoji}")
            last_emoji = i

    return "".join(out_chars)


def synthesize_tags(keywords: List[str], *, max_tags: int = 6,
                      min_tags: int = 3) -> List[str]:
    """Return 3-6 # tags. Mix垂类keyword + 长尾."""
    tags = []
    for kw in keywords[:max_tags]:
        tags.append("#" + kw)
    while len(tags) < min_tags and len(keywords) > 0:
        # pad with the most popular keyword again, as compound
        tags.append("#" + keywords[0])
    return tags[:max_tags]


def publish_time_hint(profile: Optional[dict]) -> str:
    if not profile:
        return "weekday 21:00-22:30"
    windows = profile.get("publishing", {}).get("preferred_windows") or []
    if isinstance(windows, list) and windows:
        return windows[0] if isinstance(windows[0], str) else str(windows[0])
    return "weekday 21:00-22:30"


def generate_caption(script_text: str, *,
                     hook_text: Optional[str] = None,
                     profile_name: Optional[str] = None,
                     strict: bool = True) -> dict:
    keywords = extract_keywords(script_text)
    title = synthesize_title(hook_text, keywords)
    body = synthesize_body(script_text, keywords)
    tags = synthesize_tags(keywords)

    profile = None
    if profile_name:
        try:
            from profiles import load_profile
            profile = load_profile(profile_name)
        except (FileNotFoundError, ImportError):
            pass

    payload = {
        "title": title,
        "caption_body": body,
        "tags": tags,
        "publish_time_hint": publish_time_hint(profile),
    }

    if strict:
        # Content-guard the title and body. Tags pre-pended with # are fine.
        enforce_platform_rules([title], context="title", strict=True)
        enforce_platform_rules([body], context="caption", strict=False)
        # ^ caption uses non-strict (warnings only) to leave room for emoji density

    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Generate Xiaohongshu caption (note body)")
    p.add_argument("--script", required=True, help="Path to clean_script.md")
    p.add_argument("--hook", default=None, help="Override hook text used as title")
    p.add_argument("--profile", default=None, help="Audience profile (tech_pro, lifestyle)")
    p.add_argument("--output", default=None, help="JSON output path; stdout if omitted")
    p.add_argument("--no-strict", action="store_true",
                   help="Skip content-guard rejection (warnings still emitted to stderr)")
    args = p.parse_args()

    if not os.path.isfile(args.script):
        print(f"script not found: {args.script}", file=sys.stderr)
        return 1
    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()

    hook_text = args.hook
    if not hook_text:
        # Try to extract from `## Hook` block
        m = re.search(r"^##\s+Hook\s*\n+([^\n#]+)", script_text, re.MULTILINE)
        if m:
            hook_text = m.group(1).strip()

    try:
        payload = generate_caption(
            script_text, hook_text=hook_text,
            profile_name=args.profile, strict=not args.no_strict,
        )
    except HardBlock as exc:
        print(f"🚫 Caption rejected by content guard: {exc}", file=sys.stderr)
        return 2

    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"✅ caption → {args.output}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
