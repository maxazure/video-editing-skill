import json
import os
import subprocess
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from script_alignment import (  # noqa: E402
    build_alignment,
    build_render_config,
    load_sources,
    parse_target_script,
    score_match,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _word_transcript(path, media, words):
    cursor = 0.0
    timed = []
    for index, text in enumerate(words, start=1):
        timed.append({"word": text, "start": cursor, "end": cursor + 0.5, "segment_id": index})
        cursor += 0.65
    _write_json(
        path,
        {
            "duration": cursor,
            "source": {"path": str(media)},
            "words": timed,
        },
    )


def test_target_script_ignores_headings_and_supports_sentence_mode(tmp_path):
    script = tmp_path / "target.md"
    script.write_text(
        "# 开场\n\n- 为什么总是失败？先检查 GPT-5.6 配置。\n\n## 收尾\n1. 最后再上线！\n",
        encoding="utf-8",
    )

    line_units = parse_target_script(str(script), unit_mode="line")
    sentence_units = parse_target_script(str(script), unit_mode="sentence")

    assert [item.text for item in line_units] == ["为什么总是失败？先检查 GPT-5.6 配置。", "最后再上线！"]
    assert [item.section for item in line_units] == ["开场", "收尾"]
    assert [item.text for item in sentence_units] == ["为什么总是失败？", "先检查 GPT-5.6 配置。", "最后再上线！"]


def test_score_match_exposes_transparent_components():
    exact = score_match("先检查配置", "先检查配置")
    partial = score_match("先检查配置", "先检查一下部署配置")

    assert exact["score"] == 100.0
    assert exact["exact"] is True
    assert 0 < partial["score"] < 100
    assert set(partial) == {
        "score",
        "sequence",
        "target_coverage",
        "source_coverage",
        "ngram_overlap",
        "length_fit",
        "exact",
    }


def test_alignment_reorders_source_ranges_in_target_script_order(tmp_path):
    media = tmp_path / "take.mp4"
    media.write_bytes(b"video")
    transcript = tmp_path / "take_transcript.json"
    _word_transcript(transcript, media, ["最后上线", "先检查配置"])
    target = tmp_path / "target.md"
    target.write_text("先检查配置\n最后上线\n", encoding="utf-8")

    sources = load_sources([f"main={transcript}"], {})
    plan = build_alignment(
        parse_target_script(str(target)),
        sources,
        target_script=str(target),
    )
    config = build_render_config(plan)

    assert plan["status"] == "ready"
    assert plan["summary"]["matched"] == 2
    assert [clip["start"] for clip in config["clips"]] == [0.65, 0.0]
    assert [clip["text"] for clip in config["clips"]] == ["先检查配置", "最后上线"]


def test_equal_multi_take_matches_require_human_choice(tmp_path):
    media_a = tmp_path / "a.mp4"
    media_b = tmp_path / "b.mp4"
    media_a.write_bytes(b"a")
    media_b.write_bytes(b"b")
    transcript_a = tmp_path / "a_transcript.json"
    transcript_b = tmp_path / "b_transcript.json"
    _word_transcript(transcript_a, media_a, ["三步完成部署"])
    _word_transcript(transcript_b, media_b, ["三步完成部署"])
    target = tmp_path / "target.md"
    target.write_text("三步完成部署\n", encoding="utf-8")

    sources = load_sources([f"take-a={transcript_a}", f"take-b={transcript_b}"], {})
    initial = build_alignment(parse_target_script(str(target)), sources, target_script=str(target))
    decision = initial["decisions"][0]
    selected = next(item for item in decision["candidates"] if item["source_label"] == "take-b")

    assert initial["status"] == "blocked"
    assert decision["status"] == "review"
    assert decision["blocking_reasons"] == ["ambiguous_match"]

    reviewed = build_alignment(
        parse_target_script(str(target)),
        sources,
        target_script=str(target),
        choices={"target-001": selected["id"]},
    )

    assert reviewed["status"] == "ready"
    assert reviewed["summary"]["human_choices"] == 1
    assert reviewed["decisions"][0]["chosen"]["source_label"] == "take-b"


def test_source_range_is_not_reused_by_default(tmp_path):
    media = tmp_path / "take.mp4"
    media.write_bytes(b"video")
    transcript = tmp_path / "take_transcript.json"
    _word_transcript(transcript, media, ["唯一一句"])
    target = tmp_path / "target.md"
    target.write_text("唯一一句\n唯一一句\n", encoding="utf-8")

    sources = load_sources([f"main={transcript}"], {})
    plan = build_alignment(parse_target_script(str(target)), sources, target_script=str(target))

    assert plan["decisions"][0]["status"] == "matched"
    assert plan["decisions"][1]["status"] == "unmatched"
    assert plan["decisions"][1]["blocking_reasons"] == ["no_candidate"]


def test_missing_source_media_is_blocking(tmp_path):
    transcript = tmp_path / "take_transcript.json"
    _write_json(
        transcript,
        {"segments": [{"id": 1, "start": 0.0, "end": 1.0, "text": "完整句子"}]},
    )
    target = tmp_path / "target.md"
    target.write_text("完整句子\n", encoding="utf-8")

    plan = build_alignment(
        parse_target_script(str(target)),
        load_sources([f"main={transcript}"], {}),
        target_script=str(target),
    )

    assert plan["status"] == "blocked"
    assert plan["summary"]["blocking_reasons"] == {"source_media_unset": 1}
    assert build_render_config(plan)["clips"] == []


def test_cli_strict_blocks_then_choices_make_render_config_ready(tmp_path):
    media_a = tmp_path / "a.mp4"
    media_b = tmp_path / "b.mp4"
    media_a.write_bytes(b"a")
    media_b.write_bytes(b"b")
    transcript_a = tmp_path / "a_transcript.json"
    transcript_b = tmp_path / "b_transcript.json"
    _word_transcript(transcript_a, media_a, ["保留这一句"])
    _word_transcript(transcript_b, media_b, ["保留这一句"])
    target = tmp_path / "target.md"
    target.write_text("保留这一句\n", encoding="utf-8")
    output = tmp_path / "alignment.json"
    markdown = tmp_path / "alignment.md"
    render_config = tmp_path / "render_config.json"
    clean_script = tmp_path / "clean_script.md"
    command = [
        sys.executable,
        os.path.join(REPO, "scripts", "script_alignment.py"),
        "--target-script",
        str(target),
        "--transcript",
        f"a={transcript_a}",
        "--transcript",
        f"b={transcript_b}",
        "--output",
        str(output),
        "--markdown",
        str(markdown),
        "--render-config",
        str(render_config),
        "--clean-script",
        str(clean_script),
        "--strict",
    ]

    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 2
    initial = json.loads(output.read_text(encoding="utf-8"))
    candidate_id = initial["decisions"][0]["candidates"][1]["id"]
    choices = tmp_path / "choices.json"
    _write_json(choices, {"choices": {"target-001": candidate_id}})

    second = subprocess.run(command[:-1] + ["--choices", str(choices), "--strict"], capture_output=True, text=True)

    assert second.returncode == 0, second.stderr
    reviewed = json.loads(output.read_text(encoding="utf-8"))
    config = json.loads(render_config.read_text(encoding="utf-8"))
    assert reviewed["status"] == "ready"
    assert len(config["clips"]) == 1
    assert "Target Script Alignment" in markdown.read_text(encoding="utf-8")
    assert clean_script.read_text(encoding="utf-8") == "保留这一句\n"
