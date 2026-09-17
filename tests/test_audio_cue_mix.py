import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import audio_cue_mix as mix  # noqa: E402
from pipeline_manifest import build_manifest  # noqa: E402


def _media(*, duration=4.0, codec="pcm_s16le", channels=1):
    return {
        "duration": duration,
        "format_name": "wav",
        "audio_stream_index": 0,
        "audio_codec": codec,
        "sample_rate": 48000,
        "channels": channels,
        "channel_layout": "mono" if channels == 1 else "stereo",
    }


def _fixture(tmp_path: Path, *, local=True, synth=False):
    voice = tmp_path / "work" / "voice.wav"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"voice")
    sfx = tmp_path / "assets" / "sfx" / "whoosh.wav"
    sfx.parent.mkdir(parents=True, exist_ok=True)
    sfx.write_bytes(b"local-sfx")
    cues = [
        {
            "id": "sfx_001",
            "category": "transition_whoosh",
            "start": 0.5,
            "end": 0.95,
            "duration": 0.45,
            "status": "ready" if local else "needs_generation",
            "asset": str(sfx) if local else None,
            "trigger_segment": 1,
            "trigger_text": "但是接下来很关键",
            "mix": {"target_level": "-18 to -12 LUFS momentary"},
        }
    ]
    if synth:
        cues.append(
            {
                "id": "sfx_002",
                "category": "warning_tick",
                "start": 2.0,
                "duration": 0.4,
                "status": "needs_generation",
                "asset": None,
                "trigger_segment": 2,
                "trigger_text": "注意这个风险",
            }
        )
    sheet = tmp_path / "work" / "audio_cue_sheet.json"
    sheet.write_text(
        json.dumps(
            {
                "version": "audio_cue_sheet.v1",
                "summary": {"sfx_cues": len(cues), "blocking": 0},
                "voice_track": {"duration": 4.0},
                "music": [],
                "sfx": cues,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return voice, sfx, sheet


def _patch_probe(monkeypatch, *, voice_duration=4.0):
    def fake_probe(path):
        path = Path(path)
        payload = path.read_bytes()
        if payload == b"mixed-audio":
            return _media(duration=voice_duration, codec="pcm_s24le", channels=2)
        if payload == b"local-sfx":
            return _media(duration=0.45, codec="pcm_s16le", channels=2)
        return _media(duration=voice_duration, codec="pcm_s16le", channels=1)

    monkeypatch.setattr(mix, "probe_audio_media", fake_probe)
    monkeypatch.setattr(mix, "_full_decode", lambda _path: None)


def _write_plan(path: Path, plan):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def _pass_checks():
    return {field: "pass" for field in mix.REVIEW_FIELDS}


def test_plan_binds_local_and_synthesized_cues(tmp_path, monkeypatch):
    voice, sfx, sheet = _fixture(tmp_path, local=True, synth=True)
    _patch_probe(monkeypatch)

    plan = mix.build_plan(
        str(sheet),
        str(voice),
        "work/audio_cue_mix.wav",
        project_dir=str(tmp_path),
        synthesize_missing=True,
        sfx_gain_db=-17.0,
    )

    assert plan["version"] == mix.VERSION
    assert plan["voice"]["sha256"] == mix._sha256(voice)
    assert plan["cues"][0]["route"] == "local_asset"
    assert plan["cues"][0]["asset"]["sha256"] == mix._sha256(sfx)
    assert plan["cues"][1]["route"] == "ffmpeg_synthesis"
    assert "sine=frequency=440" in plan["cues"][1]["synthesis"]["source"]
    assert plan["summary"]["local_assets"] == 1
    assert plan["summary"]["synthesized"] == 1
    assert plan["blockers"] == [mix.PENDING_APPLY]
    assert "amix=inputs=3:duration=first:dropout_transition=0:normalize=0" in plan["render_spec"]["filter_complex"]
    assert "alimiter=limit=0.95:level=false" in plan["render_spec"]["filter_complex"]
    assert plan["plan_id"] == mix._plan_id(plan)


def test_missing_unknown_and_out_of_range_cues_remain_blocked(tmp_path, monkeypatch):
    voice, _sfx, sheet = _fixture(tmp_path, local=False)
    _patch_probe(monkeypatch)
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    payload["sfx"].append(
        {
            "id": "sfx_002",
            "category": "custom_boom",
            "start": 3.0,
            "duration": 0.5,
            "asset": None,
        }
    )
    payload["sfx"].append(
        {
            "id": "sfx_003",
            "category": "warning_tick",
            "start": 3.9,
            "duration": 0.5,
            "asset": None,
        }
    )
    sheet.write_text(json.dumps(payload), encoding="utf-8")

    plan = mix.build_plan(
        str(sheet),
        str(voice),
        "work/audio_cue_mix.wav",
        project_dir=str(tmp_path),
        synthesize_missing=True,
    )
    verification = mix.verify_plan(plan)

    assert any("no supported synthesis recipe" in item for item in plan["planning_issues"]["blockers"])
    assert any("beyond narration duration" in item for item in plan["planning_issues"]["blockers"])
    assert verification["summary"]["blocking"] >= 3


def test_plan_rejects_unsafe_paths_output_collision_and_gain(tmp_path, monkeypatch):
    voice, _sfx, sheet = _fixture(tmp_path)
    _patch_probe(monkeypatch)
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"outside")
    payload = json.loads(sheet.read_text(encoding="utf-8"))
    payload["sfx"][0]["asset"] = str(outside)
    sheet.write_text(json.dumps(payload), encoding="utf-8")
    unsafe = mix.build_plan(
        str(sheet), str(voice), "work/out.wav", project_dir=str(tmp_path)
    )
    assert any("inside the project" in item for item in unsafe["planning_issues"]["blockers"])
    outside.unlink()

    _voice, _sfx, sheet = _fixture(tmp_path)
    with pytest.raises(ValueError, match="between -40 and -3"):
        mix.build_plan(
            str(sheet),
            str(voice),
            "work/out.wav",
            project_dir=str(tmp_path),
            sfx_gain_db=-1,
        )
    with pytest.raises(ValueError, match="must not overwrite"):
        mix.build_plan(
            str(sheet),
            str(voice),
            str(voice),
            project_dir=str(tmp_path),
        )


def test_apply_confirm_verify_and_manifest_live_gate(tmp_path, monkeypatch):
    voice, _sfx, sheet = _fixture(tmp_path, local=True)
    _patch_probe(monkeypatch)
    plan = mix.build_plan(
        str(sheet),
        str(voice),
        "work/audio_cue_mix.wav",
        project_dir=str(tmp_path),
    )
    plan_path = tmp_path / "work" / "audio_cue_mix.json"
    _write_plan(plan_path, plan)
    commands = []

    def fake_run(command, **_kwargs):
        command = list(command)
        commands.append(command)
        Path(command[-1]).write_bytes(b"mixed-audio")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mix.subprocess, "run", fake_run)
    applied = mix.apply_plan(str(plan_path))

    assert applied["blockers"] == [mix.PENDING_CONFIRM]
    assert applied["application"]["full_decode"] == "pass"
    assert (tmp_path / "work" / "audio_cue_mix.wav").read_bytes() == b"mixed-audio"
    render_command = commands[0]
    assert render_command[0] == "ffmpeg"
    assert "-filter_complex" in render_command
    assert "normalize=0" in render_command[render_command.index("-filter_complex") + 1]

    confirmed = mix.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="Listened end to end at normal speed; cues land cleanly below every spoken phrase.",
        full_playback="completed",
        checks=_pass_checks(),
    )
    assert confirmed["status"] == "ready"
    assert confirmed["summary"]["blocking"] == 0

    verified = mix.verify_plan(confirmed)
    assert verified["status"] == "ready"
    manifest = build_manifest(
        str(tmp_path), target_stage="analysis", required=["audio_cue_mix"]
    )
    gate = next(item for item in manifest["gates"] if item["category"] == "audio_cue_mix")
    assert gate["status"] == "ready"


def test_failed_review_and_live_drift_fail_closed(tmp_path, monkeypatch):
    voice, sfx, sheet = _fixture(tmp_path, local=True)
    _patch_probe(monkeypatch)
    plan = mix.build_plan(
        str(sheet), str(voice), "work/audio_cue_mix.wav", project_dir=str(tmp_path)
    )
    plan_path = tmp_path / "work" / "audio_cue_mix.json"
    _write_plan(plan_path, plan)

    def fake_run(command, **_kwargs):
        Path(list(command)[-1]).write_bytes(b"mixed-audio")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mix.subprocess, "run", fake_run)
    mix.apply_plan(str(plan_path))
    checks = _pass_checks()
    checks["sfx_level"] = "fail"
    failed = mix.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="The transition effect masks the following consonant.",
        full_playback="completed",
        checks=checks,
    )
    assert failed["status"] == "blocked"
    assert any("sfx_level" in item for item in failed["blockers"])

    sfx.write_bytes(b"changed-sfx")
    stale = mix.verify_plan(failed)
    assert any("cue sfx_001 asset bytes" in item for item in stale["blockers"])

    tampered = json.loads(json.dumps(failed))
    tampered["cues"][0]["start"] = 1.25
    assert "audio cue mix immutable plan content changed" in mix.verify_plan(tampered)["blockers"]


def test_synthesized_mix_keeps_review_warning_after_pass(tmp_path, monkeypatch):
    voice, _sfx, sheet = _fixture(tmp_path, local=False)
    _patch_probe(monkeypatch)
    plan = mix.build_plan(
        str(sheet),
        str(voice),
        "work/audio_cue_mix.wav",
        project_dir=str(tmp_path),
        synthesize_missing=True,
    )
    plan_path = tmp_path / "work" / "audio_cue_mix.json"
    _write_plan(plan_path, plan)

    def fake_run(command, **_kwargs):
        Path(list(command)[-1]).write_bytes(b"mixed-audio")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mix.subprocess, "run", fake_run)
    mix.apply_plan(str(plan_path))
    confirmed = mix.confirm_plan(
        str(plan_path),
        reviewed_by="editor",
        note="The procedural whoosh is subtle and lands before the turn.",
        full_playback="completed",
        checks=_pass_checks(),
    )

    assert confirmed["status"] == "warn"
    assert confirmed["summary"]["blocking"] == 0
    assert confirmed["summary"]["synthesized"] == 1


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_ffmpeg_synth_mix_smoke(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    voice = work / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=180:duration=3:sample_rate=48000",
            "-c:a",
            "pcm_s16le",
            str(voice),
        ],
        check=True,
    )
    sheet = work / "audio_cue_sheet.json"
    sheet.write_text(
        json.dumps(
            {
                "version": "audio_cue_sheet.v1",
                "summary": {"sfx_cues": 2, "blocking": 0},
                "music": [],
                "sfx": [
                    {
                        "id": "sfx_001",
                        "category": "transition_whoosh",
                        "start": 0.6,
                        "duration": 0.45,
                        "asset": None,
                    },
                    {
                        "id": "sfx_002",
                        "category": "emphasis_ping",
                        "start": 1.8,
                        "duration": 0.35,
                        "asset": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = mix.build_plan(
        str(sheet),
        str(voice),
        "work/audio_cue_mix.wav",
        project_dir=str(tmp_path),
        synthesize_missing=True,
    )
    plan_path = work / "audio_cue_mix.json"
    _write_plan(plan_path, plan)
    applied = mix.apply_plan(str(plan_path))
    output = tmp_path / applied["application"]["output"]["path"]
    media = mix.probe_audio_media(output)

    assert media["audio_codec"] == "pcm_s24le"
    assert media["sample_rate"] == 48000
    assert media["channels"] == 2
    assert abs(media["duration"] - 3.0) <= 0.05
