"""render_final.py enrich-plan ingestion."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from render_final import build_karaoke_ass, build_merged_ass, merge_enrich_plan  # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_merge_enrich_plan_translates_cues(tmp_path):
    broll = tmp_path / "city.mp4"
    image = tmp_path / "concept.png"
    broll.write_bytes(b"fake")
    image.write_bytes(b"fake")

    plan = {
        "broll": [
            {
                "start": 3.0,
                "end": 5.0,
                "suggested_asset": "city.mp4",
                "broll_start": 1.2,
                "reason": "transition-word",
            }
        ],
        "chapter_cards": [
            {"title": "关键转折", "start": 6.0, "duration": 1.0}
        ],
        "stickers": [
            {"sticker": "OK", "start": 7.0, "end": 8.0, "emotion": "conclusion"}
        ],
        "emphasis_cues": [
            {
                "start": 10.0,
                "end": 11.0,
                "label": "重点",
                "trigger": "payoff_line",
                "matched_text": "所以",
                "effect": "badge_push_in",
                "zoom": 1.12,
            }
        ],
        "imagegen": [
            {"image_path": "concept.png", "timing_seconds": 9.0, "duration": 2.0}
        ],
    }

    merged = merge_enrich_plan({"clips": [], "text_badges": []}, plan, plan_base_dir=str(tmp_path))
    assert merged["broll_overlays"][0]["video"] == str(broll)
    assert merged["broll_overlays"][0]["source_start"] == 1.2
    assert merged["chapters"][0]["title"] == "关键转折"
    assert [b["text"] for b in merged["text_badges"]] == ["关键转折", "OK", "重点"]
    assert merged["focus_events"][0]["marker"] is False
    assert merged["image_overlays"][0]["image"] == str(image)
    assert merged["_enrich_plan_stats"]["broll_overlays"] == 1
    assert merged["_enrich_plan_stats"]["text_badges"] == 3
    assert merged["_enrich_plan_stats"]["emphasis_cues"] == 1


def test_merge_enrich_plan_keeps_imagegen_advisory_when_no_file(tmp_path):
    plan = {"imagegen": [{"timing_seconds": 1.0, "prompt_en": "make a visual metaphor"}]}
    merged = merge_enrich_plan({"clips": []}, plan, plan_base_dir=str(tmp_path))
    assert "image_overlays" not in merged
    assert merged["_enrich_plan_stats"]["advisory_imagegen"] == 1


def test_text_badges_render_in_normal_and_karaoke_ass():
    clips = [{"start": 0.0, "end": 2.0, "text": "hello world"}]
    badges = [{"text": "KEY", "start": 0.5, "end": 1.5}]

    normal_ass, _, _ = build_merged_ass(
        clips, "Arial", 48, 1080, 1920, text_badges=badges,
    )
    karaoke_ass, _, _ = build_karaoke_ass(
        clips, "Arial", 48, 1080, 1920, text_badges=badges,
    )

    assert "Style: Badge" in normal_ass
    assert "Dialogue: 1" in normal_ass and "KEY" in normal_ass
    assert "Style: Badge" in karaoke_ass
    assert "Dialogue: 1" in karaoke_ass and "KEY" in karaoke_ass


def test_pop_in_subtitles_scale_per_cue_and_preserve_short_text():
    clips = [
        {"start": 0.0, "end": 1.0, "text": "First"},
        {"start": 1.0, "end": 1.2, "text": "Short"},
        {"start": 1.2, "end": 2.2, "text": "Third"},
    ]
    ass, duration, _ = build_merged_ass(
        clips, "Arial", 48, 1080, 1920,
        speed=2.0, cover_duration=0.5, subtitle_style="pop_in",
    )
    cues = [line for line in ass.splitlines() if line.startswith("Dialogue: 0,")]

    assert duration == 1.6
    assert len(cues) == 3
    assert cues[0].startswith("Dialogue: 0,0:00:00.50,0:00:01.00,")
    assert "\\fscx80\\fscy80\\t(0,120,\\fscx108\\fscy108)" in cues[0]
    assert "\\t(120,220,\\fscx100\\fscy100)" in cues[0]
    assert "\\t(" not in cues[1] and cues[1].endswith("Short")
    assert "\\t(0,120," in cues[2]


def test_existing_bold_pop_style_remains_static():
    ass, _, _ = build_merged_ass(
        [{"start": 0.0, "end": 1.0, "text": "Static"}],
        "Arial", 48, 1080, 1920, subtitle_style="bold_pop",
    )
    assert "\\t(" not in ass


def test_enrich_plan_is_guarded_before_render_work(tmp_path):
    cfg = tmp_path / "render_config.json"
    plan = tmp_path / "enrich_plan.json"
    cfg.write_text(json.dumps({
        "title": "DAY 58",
        "clips": [{"video": str(tmp_path / "missing.mp4"), "start": 0.0, "end": 1.0}],
    }))
    plan.write_text(json.dumps({
        "chapter_cards": [{"title": "全网最低价", "start": 1.0, "duration": 1.0}]
    }, ensure_ascii=False))

    out = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/render_final.py"),
            "--config", str(cfg),
            "--enrich-plan", str(plan),
            "--output", str(tmp_path / "out.mp4"),
        ],
        capture_output=True,
        text=True,
    )
    combined = out.stdout + out.stderr
    assert out.returncode == 2
    assert "Content guard refused" in combined


def test_render_help_exposes_enrich_plan():
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/render_final.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0
    assert "--enrich-plan" in out.stdout
