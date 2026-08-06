import copy
import json
import os
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from edit_recipe import (  # noqa: E402
    RecipeError,
    export_recipe,
    portable_sha256,
    replay_recipe,
    verify_recipe,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_bytes(value)


def _source_config(tmp_path):
    video = tmp_path / "source" / "private-talk.mp4"
    transcript = tmp_path / "source" / "private-transcript.json"
    image = tmp_path / "source" / "private-card.png"
    bgm = tmp_path / "source" / "private-bgm.wav"
    _write(video, b"video-a")
    _write(
        transcript,
        {
            "segments": [
                {"id": 1, "start": 0.0, "end": 2.0, "text": "first"},
                {"id": 2, "start": 3.0, "end": 5.0, "text": "second"},
            ]
        },
    )
    _write(image, b"image-a")
    _write(bgm, b"audio-a")
    config = tmp_path / "work" / "render_config.json"
    _write(
        config,
        {
            "title": "Reusable cut",
            "subtitle_style": "bold_pop",
            "clips": [
                {"video": "../source/private-talk.mp4", "transcript": "../source/private-transcript.json", "segment_id": 1},
                {"video": "../source/private-talk.mp4", "transcript": "../source/private-transcript.json", "segment_id": 2},
            ],
            "image_overlays": [{"image": "../source/private-card.png", "start": 0.2, "duration": 0.8}],
            "bgm": "../source/private-bgm.wav",
            "bgm_ducking": True,
        },
    )
    return config, {"video": video, "transcript": transcript, "image": image, "audio": bgm}


def test_export_parameterizes_paths_and_deduplicates_slots(tmp_path):
    config, paths = _source_config(tmp_path)

    recipe = export_recipe(str(config), name="fast-tech", description="Fast reviewed explainer cut")
    encoded = json.dumps(recipe, ensure_ascii=False)

    assert recipe["version"] == "edit_recipe.v1"
    assert recipe["template"]["clips"][0]["video"] == "${video_1}"
    assert recipe["template"]["clips"][1]["video"] == "${video_1}"
    assert recipe["template"]["clips"][0]["transcript"] == "${transcript_1}"
    assert [slot["name"] for slot in recipe["slots"]] == ["video_1", "transcript_1", "image_1", "audio_1"]
    assert recipe["slots"][0]["occurrences"] == ["clips[0].video", "clips[1].video"]
    assert all(str(path.resolve()) not in encoded for path in paths.values())
    assert all(path.name not in encoded for path in paths.values())
    assert verify_recipe(recipe)["status"] == "ready"


def test_export_rejects_missing_linked_file(tmp_path):
    config = tmp_path / "render_config.json"
    _write(config, {"clips": [{"video": "missing.mp4", "transcript": "missing.json", "segment_id": 1}]})

    with pytest.raises(RecipeError, match="missing"):
        export_recipe(str(config), name="broken-cut")


def test_verification_detects_template_tampering(tmp_path):
    config, _paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")
    recipe["template"]["subtitle_style"] = "neon"

    verification = verify_recipe(recipe)

    assert verification["status"] == "blocked"
    assert any(item["code"] == "digest_mismatch" for item in verification["checks"])


def test_verification_rejects_path_leak_even_with_recomputed_digest(tmp_path):
    config, _paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")
    recipe["template"]["cover_image"] = "/private/card.png"
    recipe["portable_sha256"] = portable_sha256(recipe)

    verification = verify_recipe(recipe)

    assert any(item["code"] == "path_leak" for item in verification["checks"])


def test_replay_requires_exact_bindings(tmp_path):
    config, paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")

    with pytest.raises(RecipeError, match="missing=.*audio_1"):
        replay_recipe(
            recipe,
            {
                "video_1": str(paths["video"]),
                "transcript_1": str(paths["transcript"]),
                "image_1": str(paths["image"]),
            },
        )


def test_replay_binds_new_files_and_records_hashes(tmp_path):
    config, _paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")
    target_video = tmp_path / "target" / "episode.mp4"
    target_transcript = tmp_path / "target" / "transcript.json"
    target_image = tmp_path / "target" / "card.jpg"
    target_audio = tmp_path / "target" / "music.mp3"
    _write(target_video, b"video-b")
    _write(
        target_transcript,
        {
            "segments": [
                {"id": 1, "start": 0.0, "end": 2.5, "text": "target first"},
                {"id": 2, "start": 3.0, "end": 5.5, "text": "target second"},
            ]
        },
    )
    _write(target_image, b"image-b")
    _write(target_audio, b"audio-b")

    output, records = replay_recipe(
        recipe,
        {
            "video_1": str(target_video),
            "transcript_1": str(target_transcript),
            "image_1": str(target_image),
            "audio_1": str(target_audio),
        },
    )

    assert output["clips"][0]["video"] == str(target_video.resolve())
    assert output["clips"][1]["transcript"] == str(target_transcript.resolve())
    assert output["image_overlays"][0]["image"] == str(target_image.resolve())
    assert output["bgm"] == str(target_audio.resolve())
    assert len(records) == 4
    assert all(record["sha256"].startswith("sha256:") for record in records)


def test_replay_rejects_binding_type_mismatch(tmp_path):
    config, paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")

    with pytest.raises(RecipeError, match="type mismatch"):
        replay_recipe(
            recipe,
            {
                "video_1": str(paths["image"]),
                "transcript_1": str(paths["transcript"]),
                "image_1": str(paths["image"]),
                "audio_1": str(paths["audio"]),
            },
        )


def test_cli_export_verify_replay_runs_existing_preflight(tmp_path):
    config, _paths = _source_config(tmp_path)
    recipe_path = tmp_path / "recipes" / "fast-tech_edit_recipe.json"
    recipe_md = tmp_path / "recipes" / "fast-tech_edit_recipe.md"
    export_result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "edit_recipe.py"),
            "export",
            "--config",
            str(config),
            "--name",
            "fast-tech",
            "--output",
            str(recipe_path),
            "--markdown",
            str(recipe_md),
        ],
        capture_output=True,
        text=True,
    )

    assert export_result.returncode == 0, export_result.stderr
    assert "private-talk.mp4" not in recipe_path.read_text(encoding="utf-8")
    assert "Portable Edit Recipe" in recipe_md.read_text(encoding="utf-8")
    verify_result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "edit_recipe.py"), "verify", "--recipe", str(recipe_path)],
        capture_output=True,
        text=True,
    )
    assert verify_result.returncode == 0, verify_result.stderr

    target_video = tmp_path / "target" / "episode.mp4"
    target_transcript = tmp_path / "target" / "transcript.json"
    target_image = tmp_path / "target" / "card.png"
    target_audio = tmp_path / "target" / "music.wav"
    _write(target_video, b"video-b")
    _write(
        target_transcript,
        {
            "segments": [
                {"id": 1, "start": 0.0, "end": 2.0, "text": "target first"},
                {"id": 2, "start": 3.0, "end": 5.0, "text": "target second"},
            ]
        },
    )
    _write(target_image, b"image-b")
    _write(target_audio, b"audio-b")
    output = tmp_path / "target" / "render_config.json"
    receipt = tmp_path / "target" / "edit_recipe_replay.json"
    markdown = tmp_path / "target" / "edit_recipe_replay.md"
    replay_result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "edit_recipe.py"),
            "replay",
            "--recipe",
            str(recipe_path),
            "--bind",
            f"video_1={target_video}",
            "--bind",
            f"transcript_1={target_transcript}",
            "--bind",
            f"image_1={target_image}",
            "--bind",
            f"audio_1={target_audio}",
            "--output",
            str(output),
            "--receipt",
            str(receipt),
            "--markdown",
            str(markdown),
            "--strict",
        ],
        capture_output=True,
        text=True,
    )

    assert replay_result.returncode == 0, replay_result.stderr
    replay_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    assert replay_receipt["status"] == "ready"
    assert replay_receipt["preflight"]["version"] == "edit_preflight.v1"
    assert replay_receipt["summary"]["blocking"] == 0
    assert "Edit Recipe Replay" in markdown.read_text(encoding="utf-8")


def test_cli_refuses_to_overwrite_recipe(tmp_path):
    config, _paths = _source_config(tmp_path)
    output = tmp_path / "edit_recipe.json"
    output.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "edit_recipe.py"),
            "export",
            "--config",
            str(config),
            "--name",
            "fast-tech",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert output.read_text(encoding="utf-8") == "keep"
    assert "refusing to overwrite" in result.stderr


def test_cli_refuses_output_collision_with_bound_media_even_with_force(tmp_path):
    config, paths = _source_config(tmp_path)
    recipe_path = tmp_path / "edit_recipe.json"
    recipe_path.write_text(json.dumps(export_recipe(str(config), name="fast-tech")), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts", "edit_recipe.py"),
            "replay",
            "--recipe",
            str(recipe_path),
            "--bind",
            f"video_1={paths['video']}",
            "--bind",
            f"transcript_1={paths['transcript']}",
            "--bind",
            f"image_1={paths['image']}",
            "--bind",
            f"audio_1={paths['audio']}",
            "--output",
            str(paths["video"]),
            "--receipt",
            str(tmp_path / "receipt.json"),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert paths["video"].read_bytes() == b"video-a"
    assert "refusing to overwrite an input file" in result.stderr


def test_cli_verify_rejects_tampered_recipe(tmp_path):
    config, _paths = _source_config(tmp_path)
    recipe = export_recipe(str(config), name="fast-tech")
    tampered = copy.deepcopy(recipe)
    tampered["template"]["bgm_ducking"] = False
    recipe_path = tmp_path / "tampered_edit_recipe.json"
    _write(recipe_path, tampered)

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "edit_recipe.py"), "verify", "--recipe", str(recipe_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "blocked" in result.stdout
