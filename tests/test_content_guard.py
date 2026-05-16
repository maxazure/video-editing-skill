"""content_guard.py — Xiaohongshu/RED platform lint.

HARD-BLOCK violations halt export; SOFT-WARN raise warnings only.
Coverage: extreme words, off-platform diversion, medical/medical-aesthetic,
wealth bait, political/sexual (block-list stub), title/punctuation rules.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from content_guard import (  # noqa: E402
    scan_text, ViolationLevel,
    HardBlock, SoftWarn,
)


# ── HARD BLOCK — extreme words (广告法极限词) ──────────────────────────────

@pytest.mark.parametrize("text", [
    "全网最低价",
    "国家级认证",
    "第一品牌",
    "唯一选择",
    "极致体验",
    "万能解决方案",
    "遥遥领先",
])
def test_extreme_words_blocked(text):
    violations = scan_text(text)
    assert any(v.level == ViolationLevel.HARD for v in violations), \
        f"{text!r} should hard-block (extreme words)"


# ── HARD BLOCK — off-platform diversion ───────────────────────────────────

@pytest.mark.parametrize("text", [
    "加微信 wx123abc",
    "加我威信",
    "加薇 18888888888",
    "VX: abc123",
    "我的手机 13800138000",
    "QQ 12345678",
    "+V 详聊",
    "vx 详聊",
    "加微 详聊",
])
def test_diversion_blocked(text):
    violations = scan_text(text)
    assert any(v.level == ViolationLevel.HARD for v in violations), \
        f"{text!r} should hard-block (diversion)"


# ── HARD BLOCK — medical/medical-aesthetic functional claims ──────────────

@pytest.mark.parametrize("text", [
    "根治痘印",
    "祛斑神器",
    "水光针效果",
    "热玛吉同款",
    "医生同款产品",
    "三甲推荐",
    "抗衰老必备",
])
def test_medical_claims_blocked(text):
    violations = scan_text(text)
    assert any(v.level == ViolationLevel.HARD for v in violations), \
        f"{text!r} should hard-block (medical/aesthetic claim)"


# ── HARD BLOCK — wealth bait ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "月入5万",
    "年入百万",
    "稳赚不赔",
    "躺赚被动收入",
    "财富自由秘籍",
    "零成本创业",
    "包过包赚",
])
def test_wealth_bait_blocked(text):
    violations = scan_text(text)
    assert any(v.level == ViolationLevel.HARD for v in violations), \
        f"{text!r} should hard-block (wealth bait)"


# ── SOFT WARN — title/punctuation hygiene ─────────────────────────────────

def test_title_over_20_chars_warns():
    long_title = "这是一个非常非常非常非常长的小红书标题用来测试警告" + "X" * 10
    violations = scan_text(long_title, context="title")
    assert any(v.level == ViolationLevel.SOFT for v in violations), \
        "Title > 20 chars should soft-warn"


def test_punctuation_run_warns():
    violations = scan_text("震惊！！！！！", context="title")
    assert any(v.level == ViolationLevel.SOFT for v in violations), \
        "3+ consecutive ! should soft-warn"


# ── PASS — clean content ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "AI失业焦虑？我看到更多机会",  # day58 actual title
    "DAY 58 — 一个人怎么靠 AI 接客户",
    "BestAI Labs",
    "看完这条视频，分享你的想法",
])
def test_clean_text_passes(text):
    violations = scan_text(text)
    hard = [v for v in violations if v.level == ViolationLevel.HARD]
    assert not hard, f"{text!r} should not hard-block; got: {hard}"


# ── Exception API ─────────────────────────────────────────────────────────

def test_hardblock_raises_when_strict():
    from content_guard import enforce
    with pytest.raises(HardBlock):
        enforce(["加微信 详聊"], strict=True)


def test_softwarn_only_when_strict():
    from content_guard import enforce
    # Soft-warn should still raise SoftWarn in strict mode
    with pytest.raises((SoftWarn, HardBlock)):
        enforce(["震惊！！！！！" + "X" * 30], strict=True, context="title")
