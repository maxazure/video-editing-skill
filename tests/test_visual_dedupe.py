import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import visual_dedupe  # noqa: E402
from visual_dedupe import (  # noqa: E402
    build_report,
    emit_markdown,
    ffmpeg_signature_command,
    find_duplicate_pairs,
    frame_distance,
    frame_signature_from_rgb,
    group_duplicates,
    hamming_distance,
    load_candidates,
    sample_times,
)


def _signature(hash_value="0" * 16, rgb=(100, 100, 100)):
    return {"dhash": hash_value, "mean_rgb": list(rgb)}


def _candidate(
    candidate_id,
    source_id,
    signatures,
    *,
    duration=10.0,
    quality_score=None,
    width=1920,
    height=1080,
    file_size=1000,
):
    return {
        "id": candidate_id,
        "source_id": source_id,
        "scene_id": "scene_001",
        "video": f"/tmp/{source_id}.mp4",
        "start": 0.0,
        "end": duration,
        "duration": duration,
        "width": width,
        "height": height,
        "file_size": file_size,
        "quality_score": quality_score,
        "hash_status": "ready",
        "samples": [
            {"fraction": fraction, "time": duration * fraction, **signature}
            for fraction, signature in zip(visual_dedupe.SAMPLE_FRACTIONS, signatures)
        ],
    }


def test_sample_times_uses_ten_fifty_ninety_percent():
    assert sample_times(10, 20) == [
        {"fraction": 0.1, "time": 11.0},
        {"fraction": 0.5, "time": 15.0},
        {"fraction": 0.9, "time": 19.0},
    ]


def test_dhash_and_color_guard_distinguish_flat_colors():
    red = frame_signature_from_rgb(bytes([255, 0, 0]) * (9 * 8))
    blue = frame_signature_from_rgb(bytes([0, 0, 255]) * (9 * 8))

    assert red["dhash"] == blue["dhash"] == "0" * 16
    assert frame_distance(red, red)["distance"] == 0
    assert frame_distance(red, blue)["distance"] > 8


def test_hamming_distance_counts_changed_bits_and_rejects_invalid_hash():
    assert hamming_distance("0000000000000000", "0000000000000003") == 2
    assert hamming_distance("bad", "0000000000000000") == 64


def test_duplicate_pair_needs_two_matching_samples_and_cross_source():
    base = [_signature(), _signature(), _signature()]
    one_changed = [_signature(), _signature("f" * 16), _signature()]
    different = [_signature("f" * 16), _signature("f" * 16), _signature("f" * 16)]
    candidates = [
        _candidate("a:1", "a", base),
        _candidate("b:1", "b", one_changed),
        _candidate("c:1", "c", different),
    ]

    pairs = find_duplicate_pairs(candidates, threshold=8, min_matching_samples=2)

    assert [(pair["candidate_a"], pair["candidate_b"]) for pair in pairs] == [("a:1", "b:1")]
    assert pairs[0]["matched_samples"] == 2
    assert pairs[0]["median_distance"] == 0


def test_same_source_is_opt_in_and_duration_ratio_is_enforced():
    signatures = [_signature(), _signature(), _signature()]
    candidates = [
        _candidate("a:1", "a", signatures, duration=10),
        _candidate("a:2", "a", signatures, duration=10),
        _candidate("b:1", "b", signatures, duration=2),
    ]

    assert find_duplicate_pairs(candidates, min_duration_ratio=0.5) == []
    pairs = find_duplicate_pairs(
        candidates,
        min_duration_ratio=0.5,
        include_same_source=True,
    )
    assert [(pair["candidate_a"], pair["candidate_b"]) for pair in pairs] == [("a:1", "a:2")]


def test_group_recommends_explicit_quality_and_lists_exclusions():
    signatures = [_signature(), _signature(), _signature()]
    candidates = [
        _candidate("a:1", "a", signatures, quality_score=0.6, width=3840, height=2160),
        _candidate("b:1", "b", signatures, quality_score=0.9, width=1280, height=720),
    ]
    pairs = find_duplicate_pairs(candidates)

    groups = group_duplicates(candidates, pairs)

    assert groups[0]["recommended_keep"] == "b:1"
    assert groups[0]["suggested_exclusions"] == ["a:1"]
    assert "quality_score" in groups[0]["keep_reason"]


def test_manifest_expands_scene_boundary_candidates_relative_to_manifest(tmp_path, monkeypatch):
    video = tmp_path / "origin" / "a.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")
    scenes = tmp_path / "work" / "a_scenes.json"
    scenes.parent.mkdir()
    scenes.write_text(
        json.dumps(
            {
                "version": "scene_boundaries.v1",
                "scenes": [
                    {"scene_id": "scene_001", "start": 0, "end": 3},
                    {"scene_id": "scene_002", "start": 3, "end": 8, "quality_score": 0.95},
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "work" / "dedupe_sources.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "cam-a",
                        "video": "../origin/a.mp4",
                        "scene_boundaries": "a_scenes.json",
                        "quality_score": 0.8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(visual_dedupe, "get_video_info", lambda _path: (8.0, 1920, 1080, 30.0, 0))

    candidates = load_candidates(manifest_path=str(manifest))

    assert [candidate["id"] for candidate in candidates] == [
        "cam-a:scene_001",
        "cam-a:scene_002",
    ]
    assert candidates[0]["quality_score"] == 0.8
    assert candidates[1]["quality_score"] == 0.95
    assert candidates[1]["start"] == 3


def test_report_blocks_for_duplicate_review_and_markdown_is_actionable():
    signatures = [_signature(), _signature(), _signature()]
    candidates = [
        _candidate("a:1", "a", signatures),
        _candidate("b:1", "b", signatures),
    ]

    report = build_report(candidates, [])
    markdown = emit_markdown(report)

    assert report["version"] == "visual_dedupe.v1"
    assert report["summary"]["blocking"] == 1
    assert report["summary"]["suggested_exclusions"] == 1
    assert "Recommended keep" in markdown
    assert "does not delete or modify source media" in markdown
    assert f"[a.mp4]({Path('/tmp/a.mp4').resolve().as_uri()})" in markdown
    assert "10% 00:00:01.000" in markdown


def test_cli_writes_review_artifacts_and_strict_returns_two(tmp_path, monkeypatch):
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    video_a.write_bytes(b"a")
    video_b.write_bytes(b"b")
    output = tmp_path / "visual_dedupe.json"
    markdown = tmp_path / "visual_dedupe.md"
    monkeypatch.setattr(visual_dedupe, "get_video_info", lambda _path: (5.0, 1920, 1080, 30.0, 0))
    monkeypatch.setattr(
        visual_dedupe,
        "extract_frame_signature",
        lambda *_args, **_kwargs: _signature(rgb=(90, 100, 110)),
    )

    code = visual_dedupe.main(
        [
            str(video_a),
            str(video_b),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ]
    )

    assert code == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["duplicate_groups"] == 1
    assert markdown.exists()


def test_ffmpeg_command_emits_small_rgb_frame():
    command = ffmpeg_signature_command("input.mp4", 1.25)
    joined = " ".join(command)

    assert "scale=9:8:flags=area" in joined
    assert "-pix_fmt rgb24" in joined
    assert "-ss 1.250000" in joined
