#!/usr/bin/env python3
"""Xiaohongshu/RED platform-rule lint for script + title + caption.

Two violation levels:
  HARD-BLOCK — refuse to export. Examples: extreme words (广告法),
               off-platform diversion (微信/QQ/手机号), medical claims,
               wealth bait.
  SOFT-WARN  — caller may proceed but should review. Examples: overly long
               title, runs of !!!, suspect emoji density.

Sources are documented in docs/plans/2026-05-17-v3-xhs-improvements.md
(Phase 2 research synthesis, 2025-2026 platform rules).

CLI:
    python3 scripts/content_guard.py --script <path> [--title T] [--caption C] [--strict]
"""
import argparse
import dataclasses
import enum
import json
import os
import re
import sys
from typing import Iterable, List, Optional


# ── Violation domain ──────────────────────────────────────────────────────


class ViolationLevel(enum.Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclasses.dataclass(frozen=True)
class Violation:
    level: ViolationLevel
    category: str
    pattern: str
    match: str
    context: str


class HardBlock(Exception):
    """Raised by enforce() when any HARD violation is found and strict=True."""


class SoftWarn(Exception):
    """Raised by enforce() when SOFT violations are found and strict=True."""


# ── Regex banks ───────────────────────────────────────────────────────────
# Each entry: (compiled regex, category, level)

_HARD_PATTERNS = [
    # 广告法极限词 (Advertising Law forbidden absolute terms)
    (r"最[佳具优好高低先新]", "extreme-word", ViolationLevel.HARD),
    (r"第[一1](?![0-9百千万亿])", "extreme-word", ViolationLevel.HARD),
    (r"全[网球]第[一1]", "extreme-word", ViolationLevel.HARD),
    (r"唯一", "extreme-word", ViolationLevel.HARD),
    (r"顶[尖级]", "extreme-word", ViolationLevel.HARD),
    (r"国家级", "extreme-word", ViolationLevel.HARD),
    (r"世界级", "extreme-word", ViolationLevel.HARD),
    (r"极致", "extreme-word", ViolationLevel.HARD),
    (r"万能", "extreme-word", ViolationLevel.HARD),
    (r"独家", "extreme-word", ViolationLevel.HARD),
    (r"首[发选家创]", "extreme-word", ViolationLevel.HARD),
    (r"销量冠军", "extreme-word", ViolationLevel.HARD),
    (r"全网最[低高便宜贵]价?", "extreme-word", ViolationLevel.HARD),
    (r"史无前例", "extreme-word", ViolationLevel.HARD),
    (r"遥遥领先", "extreme-word", ViolationLevel.HARD),
    (r"王牌", "extreme-word", ViolationLevel.HARD),
    (r"领袖品牌", "extreme-word", ViolationLevel.HARD),

    # Off-platform diversion: 微信族 (WeChat aliases, obfuscations)
    (r"微信", "diversion-wechat", ViolationLevel.HARD),
    (r"威信", "diversion-wechat", ViolationLevel.HARD),
    (r"薇信", "diversion-wechat", ViolationLevel.HARD),
    (r"嶶信", "diversion-wechat", ViolationLevel.HARD),
    (r"徾信", "diversion-wechat", ViolationLevel.HARD),
    (r"\b[Vv][Xx]\b", "diversion-wechat", ViolationLevel.HARD),
    (r"\bwx\b", "diversion-wechat", ViolationLevel.HARD),
    (r"\bv信", "diversion-wechat", ViolationLevel.HARD),
    (r"加\s*[Vv＋+]", "diversion-wechat", ViolationLevel.HARD),
    (r"＋[Vv]", "diversion-wechat", ViolationLevel.HARD),
    (r"\+\s*[Vv]\b", "diversion-wechat", ViolationLevel.HARD),
    (r"\b加微\b", "diversion-wechat", ViolationLevel.HARD),
    (r"^薇$|[^a-z]薇[^a-z]|^威$|[^a-z]威[^a-z]", "diversion-wechat", ViolationLevel.HARD),

    # Off-platform diversion: phone, QQ
    (r"\b1[3-9]\d{9}\b", "diversion-phone", ViolationLevel.HARD),
    (r"\b[Qq][Qq]号?\b", "diversion-qq", ViolationLevel.HARD),
    (r"扣扣\d+|企鹅\d+", "diversion-qq", ViolationLevel.HARD),

    # Off-platform diversion: external apps
    (r"\b(?:抖音|快手|淘宝|某宝|拼多多|某多|京东|视频号|公众号|私域|加群)\b", "diversion-external", ViolationLevel.HARD),
    (r"(?:https?://|www\.|\.com|\.cn|\.net)", "diversion-url", ViolationLevel.HARD),
    (r"二维码", "diversion-qrcode", ViolationLevel.HARD),

    # 医美/医疗功效词 (medical/medical-aesthetic functional claims)
    (r"治[愈疗]", "medical-claim", ViolationLevel.HARD),
    (r"根治", "medical-claim", ViolationLevel.HARD),
    (r"特效", "medical-claim", ViolationLevel.HARD),
    (r"祛[斑痘印疤]", "medical-claim", ViolationLevel.HARD),
    (r"抗[衰老氧化]", "medical-claim", ViolationLevel.HARD),
    (r"水光针", "medical-claim", ViolationLevel.HARD),
    (r"热玛吉", "medical-claim", ViolationLevel.HARD),
    (r"线雕", "medical-claim", ViolationLevel.HARD),
    (r"溶脂", "medical-claim", ViolationLevel.HARD),
    (r"医生同款", "medical-claim", ViolationLevel.HARD),
    (r"三甲(?:推荐|医院)", "medical-claim", ViolationLevel.HARD),

    # 财富诱导 (wealth bait)
    (r"年入[\d一二两三四五六七八九十百千万]+", "wealth-bait", ViolationLevel.HARD),
    (r"月入[\d一二两三四五六七八九十百千万]+", "wealth-bait", ViolationLevel.HARD),
    (r"日入[\d一二两三四五六七八九十百千万]+", "wealth-bait", ViolationLevel.HARD),
    (r"躺赚", "wealth-bait", ViolationLevel.HARD),
    (r"财富自由", "wealth-bait", ViolationLevel.HARD),
    (r"稳赚不赔", "wealth-bait", ViolationLevel.HARD),
    (r"零成本", "wealth-bait", ViolationLevel.HARD),
    (r"包[过赚]", "wealth-bait", ViolationLevel.HARD),
    (r"暴利", "wealth-bait", ViolationLevel.HARD),
]


# Soft-warn rules tend to be context-aware so they're applied at scan time.
_PUNCT_RUN = re.compile(r"([!！?？。.])\1{2,}")  # 3+ same in a row


def _compile(patterns):
    return [(re.compile(p, re.IGNORECASE), cat, lvl) for p, cat, lvl in patterns]


_HARD_COMPILED = _compile(_HARD_PATTERNS)


# ── Public scan API ───────────────────────────────────────────────────────


def scan_text(text: str, *, context: str = "body") -> List[Violation]:
    """Return all Violation objects found in `text`.

    `context` controls which soft-warn rules apply:
      - "title": length ≤ 20, no punctuation runs, no emoji-spam
      - "caption": length ≤ 800, emoji density check
      - "body" / "script": just the hard rules
    """
    if not isinstance(text, str) or not text:
        return []

    out: List[Violation] = []
    for rx, cat, lvl in _HARD_COMPILED:
        for m in rx.finditer(text):
            out.append(Violation(
                level=lvl, category=cat, pattern=rx.pattern,
                match=m.group(0), context=context,
            ))

    # Soft rules — context-dependent
    if context == "title":
        if _visible_length(text) > 20:
            out.append(Violation(
                level=ViolationLevel.SOFT, category="title-too-long",
                pattern="len>20", match=text[:25] + "…", context=context,
            ))
        if _PUNCT_RUN.search(text):
            m = _PUNCT_RUN.search(text)
            out.append(Violation(
                level=ViolationLevel.SOFT, category="punct-run",
                pattern=_PUNCT_RUN.pattern, match=m.group(0), context=context,
            ))

    if context in ("title", "caption"):
        emoji_count = sum(1 for ch in text if _is_emoji(ch))
        char_count = max(_visible_length(text), 1)
        if char_count > 0 and emoji_count / char_count > 0.30:
            out.append(Violation(
                level=ViolationLevel.SOFT, category="emoji-spam",
                pattern="ratio>0.30", match=f"{emoji_count}/{char_count}",
                context=context,
            ))

    if context == "caption" and _visible_length(text) > 800:
        out.append(Violation(
            level=ViolationLevel.SOFT, category="caption-too-long",
            pattern="len>800", match=f"len={_visible_length(text)}",
            context=context,
        ))

    return out


def _visible_length(text: str) -> int:
    """Count characters, treating CJK and ASCII alike."""
    return len(text)


def _is_emoji(ch: str) -> bool:
    """Crude emoji range check; good enough for soft-warn density."""
    if not ch:
        return False
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF    # misc symbols & pictographs
        or 0x2600 <= cp <= 0x27BF   # misc symbols, dingbats
        or 0x1F1E6 <= cp <= 0x1F1FF  # regional indicators
    )


def enforce(texts: Iterable[str], *, strict: bool = True, context: str = "body") -> List[Violation]:
    """Scan a batch of texts. Raise HardBlock (strict) on any hard violation,
    SoftWarn on soft-only violations, or return the violation list."""
    all_violations: List[Violation] = []
    for t in texts:
        all_violations.extend(scan_text(t, context=context))

    if strict:
        hard = [v for v in all_violations if v.level == ViolationLevel.HARD]
        if hard:
            preview = "; ".join(f"{v.category}:{v.match!r}" for v in hard[:5])
            raise HardBlock(f"{len(hard)} hard violation(s): {preview}")
        soft = [v for v in all_violations if v.level == ViolationLevel.SOFT]
        if soft:
            preview = "; ".join(f"{v.category}:{v.match!r}" for v in soft[:5])
            raise SoftWarn(f"{len(soft)} soft warning(s): {preview}")

    return all_violations


# ── CLI ───────────────────────────────────────────────────────────────────


def _format_violation(v: Violation) -> str:
    icon = "🚫" if v.level == ViolationLevel.HARD else "⚠️"
    return f"  {icon} [{v.category}] {v.match!r}  (context={v.context})"


def main() -> int:
    p = argparse.ArgumentParser(description="Xiaohongshu/RED content lint")
    p.add_argument("--script", help="Path to cleaned script (markdown or txt)")
    p.add_argument("--title", help="Inline title to check")
    p.add_argument("--caption", help="Path to caption/note body file")
    p.add_argument("--config", help="Render config JSON (checks title/subtitle/chapters)")
    p.add_argument("--strict", action="store_true",
                   help="Exit with non-zero status when violations found")
    args = p.parse_args()

    violations: List[Violation] = []

    if args.title:
        violations.extend(scan_text(args.title, context="title"))

    if args.script and os.path.isfile(args.script):
        with open(args.script, encoding="utf-8") as f:
            violations.extend(scan_text(f.read(), context="script"))

    if args.caption and os.path.isfile(args.caption):
        with open(args.caption, encoding="utf-8") as f:
            violations.extend(scan_text(f.read(), context="caption"))

    if args.config and os.path.isfile(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        violations.extend(scan_text(cfg.get("title", ""), context="title"))
        violations.extend(scan_text(cfg.get("subtitle", ""), context="title"))
        for ch in cfg.get("chapters", []) or []:
            t = ch.get("title", "") if isinstance(ch, dict) else ch
            violations.extend(scan_text(t, context="title"))

    hard = [v for v in violations if v.level == ViolationLevel.HARD]
    soft = [v for v in violations if v.level == ViolationLevel.SOFT]

    if hard:
        print(f"🚫 HARD: {len(hard)} violation(s)")
        for v in hard:
            print(_format_violation(v))
    if soft:
        print(f"⚠️  SOFT: {len(soft)} warning(s)")
        for v in soft:
            print(_format_violation(v))
    if not violations:
        print("✅ No violations found.")

    if args.strict and (hard or soft):
        return 1
    if hard:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
