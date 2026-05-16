"""Refuse to burn pipeline-internal tokens (speed, model, engine) onto frames.

Rule: only user-facing content (title, brand, cleaned script) reaches drawtext/ASS.
Anything that looks like a debug or build label is rejected by check_visible_text.
"""
import os
import sys

import pytest

# Make scripts/ importable as a package-less directory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from _internal_text_guard import check_visible_text, InternalTextLeak  # noqa: E402


# ---- Forbidden patterns ----

def test_speed_label_x_suffix_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("加速 1.25x 播放")


def test_speed_label_capital_x_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("Speed 2X")


def test_model_name_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("whisper-large-v3-turbo")


def test_engine_name_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("使用 mlx-whisper 转写")


def test_audio_filter_name_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("loudnorm applied")


def test_debug_marker_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("DEBUG: rendering")


def test_tempfile_marker_rejected():
    with pytest.raises(InternalTextLeak):
        check_visible_text("opening day58_temp.mp4")


# ---- Allowed text ----

def test_normal_title_ok():
    check_visible_text("DAY 58 — AI 失业焦虑")


def test_clean_script_segment_ok():
    check_visible_text("AI 让我更不焦虑了")


def test_brand_mark_ok():
    check_visible_text("BestAI Labs")


def test_empty_string_ok():
    check_visible_text("")


def test_non_string_input_ignored():
    # numbers, None etc. should not crash the guard
    check_visible_text(None)
    check_visible_text(42)
