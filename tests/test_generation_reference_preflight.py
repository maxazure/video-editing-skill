import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import generation_reference_preflight as preflight  # noqa: E402
from video_prompt_pack import build_video_prompt_pack  # noqa: E402


def _bundle(*, frame_exclusive=True, audio_only=True, video_limit=3):
    return {
        "version": "video_provider_capabilities.v1",
        "profiles": [
            {
                "provider": "dreamina_seedance",
                "surface": "verified test surface",
                "model": "verified test model",
                "verified_at": datetime.now(timezone.utc).date().isoformat(),
                "sources": [
                    {
                        "source_type": "official_documentation",
                        "url": "https://example.com/provider-docs",
                    }
                ],
                "capabilities": {
                    "modes": [
                        "text_to_video",
                        "image_to_video",
                        "reference_to_video",
                        "video_edit",
                        "video_extension",
                        "clip_stitching",
                    ],
                    "aspect_ratios": ["9:16"],
                    "resolutions": ["720p"],
                    "duration": {"kind": "range", "min_seconds": 2, "max_seconds": 15},
                    "reference_limits": {"images": 4, "videos": video_limit, "audio": 3},
                    "reference_media": {
                        "frame_reference_exclusive": frame_exclusive,
                        "audio_only": audio_only,
                        "total_files": 8,
                        "images": {"extensions": [".png", ".jpg"], "max_bytes": 10_000_000},
                        "videos": {
                            "extensions": [".mp4", ".mov"],
                            "max_bytes": 200_000_000,
                            "min_seconds": 2,
                            "max_seconds": 15,
                            "max_total_seconds": 15,
                        },
                        "audio": {
                            "extensions": [".wav", ".mp3"],
                            "max_bytes": 20_000_000,
                            "min_seconds": 2,
                            "max_seconds": 15,
                            "max_total_seconds": 15,
                        },
                    },
                    "audio": {"generate": True, "reference": True, "preserve_source": "unknown"},
                },
            }
        ],
    }


def _pack(bundle, *, mode="reference_to_video", asset_root=None):
    plan = {
        "version": "storyboard_plan.v1",
        "target": {"aspect": "9:16"},
        "shots": [
            {
                "id": "shot_001",
                "start": 0,
                "end": 4,
                "duration": 4,
                "narration": "Show the product reveal.",
                "generation_route": {"primary": "dreamina_seedance"},
                "visual": {"motion": "slow push in"},
            }
        ],
    }
    return build_video_prompt_pack(
        plan,
        provider="dreamina_seedance",
        mode=mode,
        asset_root=asset_root,
        approved=True,
        capability_bundles=[bundle],
        require_capability_profile=True,
        resolution="720p",
    )


def _write_inputs(tmp_path, manifest, *, bundle=None, mode="reference_to_video", asset_root=None):
    bundle = bundle or _bundle()
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    pack_path = work / "video_prompt_pack.json"
    refs_path = work / "generation_references.json"
    capabilities_path = work / "provider_capabilities.json"
    pack_path.write_text(json.dumps(_pack(bundle, mode=mode, asset_root=asset_root)), encoding="utf-8")
    refs_path.write_text(json.dumps(manifest), encoding="utf-8")
    capabilities_path.write_text(json.dumps(bundle), encoding="utf-8")
    return pack_path, refs_path, capabilities_path


def _manifest(*references):
    return {
        "version": preflight.INPUT_VERSION,
        "shots": [{"shot_id": "shot_001", "references": list(references)}],
    }


def _ref(kind, path, role=None, exclude=None):
    return {
        "kind": kind,
        "path": str(path),
        "role": role or f"the {kind} reference's intended narrow control",
        "exclude": exclude or "identity, unrelated setting, text, and any unrequested property",
    }


def _fake_probe(path, kind):
    duration = None if kind == "image" else (9.0 if "long" in path.stem else 4.0)
    return {
        "kind": kind,
        "format": path.suffix.lstrip("."),
        "codec": "png" if kind == "image" else ("aac" if kind == "audio" else "h264"),
        "duration_seconds": duration,
        "width": 1080 if kind != "audio" else None,
        "height": 1920 if kind != "audio" else None,
        "sample_rate": 48000 if kind == "audio" else None,
        "channels": 2 if kind == "audio" else None,
        "full_decode": "passed",
    }


def test_ready_multimodal_references_get_stable_labels_and_bound_prompt(tmp_path, monkeypatch):
    image = tmp_path / "work" / "product.png"
    video = tmp_path / "work" / "motion.mp4"
    audio = tmp_path / "work" / "rhythm.wav"
    for path in (image, video, audio):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(path.name.encode())
    manifest = _manifest(
        _ref("image", image, "the hero product geometry", "background and generated text"),
        _ref("video", video, "camera motion and action timing only", "identity, wardrobe, and setting"),
        _ref("audio", audio, "music rhythm only", "voice identity and dialogue"),
    )
    pack_path, refs_path, capabilities_path = _write_inputs(tmp_path, manifest)
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)

    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
        generated_at="2026-09-17T00:00:00Z",
    )

    assert report["status"] == "ready"
    assert report["summary"]["references"] == 3
    refs = report["shots"][0]["references"]
    assert [item["label"] for item in refs] == ["@Image1", "@Video1", "@Audio1"]
    prompt = report["shots"][0]["provider_prompt"]
    assert "@Video1 controls camera motion and action timing only" in prompt
    assert "Do not inherit identity, wardrobe, and setting" in prompt
    assert "MAIN REQUEST:" in prompt


def test_total_duration_and_reference_count_limits_fail_closed(tmp_path, monkeypatch):
    first = tmp_path / "work" / "long-one.mp4"
    second = tmp_path / "work" / "long-two.mp4"
    for path in (first, second):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"video")
    bundle = _bundle(video_limit=1)
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        _manifest(_ref("video", first), _ref("video", second)),
        bundle=bundle,
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)

    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
    )

    assert report["status"] == "blocked"
    assert any("video reference limit exceeded: 2>1" in item for item in report["blockers"])
    assert any("video reference total 18s exceeds 15s" in item for item in report["blockers"])


def test_audio_only_mode_and_missing_role_are_blocked(tmp_path, monkeypatch):
    audio = tmp_path / "work" / "voice.wav"
    audio.parent.mkdir(exist_ok=True)
    audio.write_bytes(b"audio")
    reference = _ref("audio", audio)
    reference["role"] = ""
    bundle = _bundle(audio_only=False)
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        _manifest(reference),
        bundle=bundle,
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)

    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
    )

    assert any("requires one narrow role" in item for item in report["blockers"])
    assert any("audio-only reference mode is not verified" in item for item in report["blockers"])


def test_exact_frame_and_semantic_references_respect_mode_exclusivity(tmp_path, monkeypatch):
    asset_root = tmp_path / "work"
    frame = asset_root / "imagegen" / "shot_001.png"
    motion = asset_root / "motion.mp4"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    motion.write_bytes(b"motion")
    manifest = _manifest(_ref("video", motion))
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        manifest,
        mode="image_to_video",
        asset_root=str(asset_root),
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)

    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
    )

    assert report["status"] == "blocked"
    assert report["shots"][0]["exact_frame"]["path"] == "work/imagegen/shot_001.png"
    assert any("exact frame and semantic references cannot be combined" in item for item in report["blockers"])
    assert any("references require a reference/edit/extension/clip-stitching mode" in item for item in report["blockers"])


def test_live_verify_detects_reference_byte_drift(tmp_path, monkeypatch):
    video = tmp_path / "work" / "motion.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"motion-v1")
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        _manifest(_ref("video", video)),
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)
    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
        generated_at="2026-09-17T00:00:00Z",
    )
    report_path = tmp_path / "work" / "generation_reference_preflight.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    current = preflight.verify_report(str(report_path), project_dir=str(tmp_path))
    video.write_bytes(b"motion-v2")
    stale = preflight.verify_report(str(report_path), project_dir=str(tmp_path))

    assert current["status"] == "ready"
    assert stale["status"] == "blocked"
    assert any("drifted" in item for item in stale["verification_errors"])


def test_template_and_analyze_cli_write_artifacts(tmp_path, monkeypatch):
    video = tmp_path / "work" / "motion.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"motion")
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        _manifest(_ref("video", video)),
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)
    template_path = tmp_path / "work" / "generation_reference_template.json"

    template_exit = preflight.main(
        [
            "template",
            "--project-dir",
            str(tmp_path),
            "--prompt-pack",
            str(pack_path),
            "--output",
            str(template_path),
        ]
    )
    output = tmp_path / "work" / "generation_reference_preflight.json"
    markdown = tmp_path / "work" / "generation_reference_preflight.md"
    analyze_exit = preflight.main(
        [
            "analyze",
            "--project-dir",
            str(tmp_path),
            "--prompt-pack",
            str(pack_path),
            "--references",
            str(refs_path),
            "--capability-profile",
            str(capabilities_path),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ]
    )

    assert template_exit == 0
    assert json.loads(template_path.read_text(encoding="utf-8"))["shots"][0]["shot_id"] == "shot_001"
    assert analyze_exit == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ready"
    assert "@Video1" in markdown.read_text(encoding="utf-8")


def test_manifest_must_cover_every_generated_video_shot(tmp_path):
    pack_path, refs_path, capabilities_path = _write_inputs(tmp_path, _manifest())
    refs_path.write_text(
        json.dumps({"version": preflight.INPUT_VERSION, "shots": []}),
        encoding="utf-8",
    )

    report = preflight.build_report(
        root=tmp_path,
        prompt_pack_path=pack_path,
        reference_manifest_path=refs_path,
        capability_paths=[capabilities_path],
    )

    assert report["status"] == "blocked"
    assert "reference manifest is missing generated-video shots: shot_001" in report["blockers"]


def test_analyze_refuses_to_overwrite_bound_media_or_hardlink(tmp_path, monkeypatch):
    video = tmp_path / "work" / "motion.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"protected-motion")
    pack_path, refs_path, capabilities_path = _write_inputs(
        tmp_path,
        _manifest(_ref("video", video)),
    )
    monkeypatch.setattr(preflight, "_probe_reference", _fake_probe)
    common = [
        "analyze",
        "--project-dir", str(tmp_path),
        "--prompt-pack", str(pack_path),
        "--references", str(refs_path),
        "--capability-profile", str(capabilities_path),
        "--force",
    ]

    with pytest.raises(SystemExit):
        preflight.main([*common, "--output", str(video)])

    alias = tmp_path / "work" / "report.json"
    os.link(video, alias)
    with pytest.raises(SystemExit):
        preflight.main([*common, "--output", str(alias)])

    assert video.read_bytes() == b"protected-motion"


def test_real_ffmpeg_multimodal_cli_smoke(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return
    work = tmp_path / "work"
    work.mkdir()
    image = work / "product.png"
    video = work / "motion.mp4"
    audio = work / "rhythm.wav"
    media_commands = [
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x128:d=0.1",
            "-frames:v", "1", "-update", "1", "-y", str(image),
        ],
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x128:r=12:d=2.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
        ],
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=2.4",
            "-ar", "48000", "-y", str(audio),
        ],
    ]
    for command in media_commands:
        subprocess.run(command, check=True, capture_output=True, text=True)

    manifest = _manifest(
        _ref("image", image, "product geometry only", "background, text, and camera"),
        _ref("video", video, "camera motion only", "identity, product shape, and setting"),
        _ref("audio", audio, "rhythm only", "voice, dialogue, and ambience"),
    )
    pack_path, refs_path, capabilities_path = _write_inputs(tmp_path, manifest)
    report_path = work / "generation_reference_preflight.json"
    markdown_path = work / "generation_reference_preflight.md"
    analyze = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/generation_reference_preflight.py"),
            "analyze",
            "--project-dir", str(tmp_path),
            "--prompt-pack", str(pack_path),
            "--references", str(refs_path),
            "--capability-profile", str(capabilities_path),
            "--output", str(report_path),
            "--markdown", str(markdown_path),
            "--strict",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    verify = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO, "scripts/generation_reference_preflight.py"),
            "verify",
            "--project-dir", str(tmp_path),
            "--report", str(report_path),
            "--strict",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert analyze.returncode == 0, analyze.stderr
    assert verify.returncode == 0, verify.stderr
    assert report["status"] == "ready"
    assert report["summary"]["full_decode_passed"] == 3
    assert [item["label"] for item in report["shots"][0]["references"]] == [
        "@Image1", "@Video1", "@Audio1",
    ]
