import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import hdr_sdr  # noqa: E402


HDR_MEDIA = {
    "duration": 4.0,
    "fps": 30.0,
    "width": 640,
    "height": 360,
    "rotation": 0,
    "has_audio": True,
    "video_codec": "hevc",
    "audio_codec": "aac",
    "pixel_format": "yuv420p10le",
    "bit_depth": 10,
    "color_primaries": "bt2020",
    "color_transfer": "smpte2084",
    "color_space": "bt2020nc",
    "color_range": "tv",
    "side_data_types": [],
    "format_names": ["mov", "mp4"],
}

SDR_MEDIA = {
    **HDR_MEDIA,
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "bit_depth": 8,
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
}


def _source(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mov"
    source.write_bytes(b"hdr-source")
    return source


def _patch_ready(monkeypatch, media=None):
    monkeypatch.setattr(hdr_sdr, "probe_media", lambda _path: dict(media or HDR_MEDIA))
    monkeypatch.setattr(hdr_sdr, "_available_filters", lambda: set(hdr_sdr.REQUIRED_FILTERS))


def _plan(tmp_path: Path, monkeypatch, media=None):
    _patch_ready(monkeypatch, media)
    return hdr_sdr.build_plan(
        str(_source(tmp_path)),
        str(tmp_path / "output" / "share_sdr.mp4"),
    )


def test_plan_binds_pq_source_and_canonical_sdr_contract(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)

    assert plan["version"] == hdr_sdr.VERSION
    assert plan["source"]["sha256"] == hdr_sdr._sha256(tmp_path / "source.mov")
    assert plan["settings"]["source_profile"] == "pq"
    assert "tin=smpte2084" in plan["settings"]["filter_chain"]
    assert "format=gbrpf32le" in plan["settings"]["filter_chain"]
    assert plan["settings"]["color_transfer"] == "bt709"
    assert plan["blockers"] == [hdr_sdr.PENDING_APPLY]
    assert plan["status"] == "blocked"


def test_hlg_is_explicit_and_ambiguous_color_metadata_is_rejected(tmp_path, monkeypatch):
    hlg = {**HDR_MEDIA, "color_transfer": "arib-std-b67"}
    plan = _plan(tmp_path / "hlg", monkeypatch, hlg)

    assert plan["settings"]["source_profile"] == "hlg"
    assert "tin=arib-std-b67" in plan["settings"]["filter_chain"]

    for index, media in enumerate(
        [
            {**HDR_MEDIA, "color_transfer": "bt709", "color_primaries": "bt709", "color_space": "bt709"},
            {**HDR_MEDIA, "color_transfer": "unknown"},
            {**HDR_MEDIA, "color_primaries": "unknown"},
            {**HDR_MEDIA, "color_space": "unknown"},
        ]
    ):
        monkeypatch.setattr(hdr_sdr, "probe_media", lambda _path, value=media: dict(value))
        with pytest.raises(ValueError, match="refusing|not PQ/HLG"):
            hdr_sdr.build_plan(
                str(_source(tmp_path / f"bad-{index}")),
                str(tmp_path / f"bad-{index}" / "sdr.mp4"),
            )


def test_missing_zscale_blocks_before_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(hdr_sdr, "probe_media", lambda _path: dict(HDR_MEDIA))
    monkeypatch.setattr(hdr_sdr, "_available_filters", lambda: {"tonemap"})
    plan = hdr_sdr.build_plan(
        str(_source(tmp_path)),
        str(tmp_path / "sdr.mp4"),
    )
    plan_path = tmp_path / "hdr_sdr_plan.json"
    hdr_sdr._atomic_write_json(plan_path, plan)

    assert any("zscale" in item for item in plan["blockers"])
    with pytest.raises(ValueError, match="not ready to apply"):
        hdr_sdr.apply_plan(str(plan_path))
    assert not (tmp_path / "sdr.mp4").exists()


def test_encode_command_sets_linear_tonemap_and_explicit_bt709_tags(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    command = hdr_sdr.build_command(plan, tmp_path / "temporary.mp4")

    filter_chain = command[command.index("-vf") + 1]
    assert filter_chain.startswith("zscale=t=linear:tin=smpte2084:npl=100")
    assert "tonemap=tonemap=hable:desat=0" in filter_chain
    assert command[command.index("-color_primaries") + 1] == "bt709"
    assert command[command.index("-color_trc") + 1] == "bt709"
    assert command[command.index("-colorspace") + 1] == "bt709"
    assert command[command.index("-color_range") + 1] == "tv"
    assert "+faststart" in command


def test_verify_detects_source_drift_and_rewritten_settings(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    (tmp_path / "source.mov").write_bytes(b"changed-hdr-source")

    drifted = hdr_sdr.verify_plan(plan)

    assert any("source" in item and "changed" in item for item in drifted["blockers"])

    (tmp_path / "source.mov").write_bytes(b"hdr-source")
    plan["settings"]["video_crf"] = 30
    plan["plan_id"] = hdr_sdr._plan_id(plan)
    rewritten = hdr_sdr.verify_plan(plan)

    assert any("settings do not match" in item for item in rewritten["blockers"])


def test_apply_validates_full_decode_and_atomically_promotes(tmp_path, monkeypatch):
    source = _source(tmp_path)

    def fake_probe(path):
        return dict(HDR_MEDIA if Path(path).resolve() == source.resolve() else SDR_MEDIA)

    monkeypatch.setattr(hdr_sdr, "probe_media", fake_probe)
    monkeypatch.setattr(hdr_sdr, "_available_filters", lambda: set(hdr_sdr.REQUIRED_FILTERS))
    plan = hdr_sdr.build_plan(str(source), str(tmp_path / "output" / "share_sdr.mp4"))
    plan_path = tmp_path / "work" / "hdr_sdr_plan.json"
    hdr_sdr._atomic_write_json(plan_path, plan)

    commands = []

    def fake_run(command):
        commands.append(list(command))
        if "-vf" in command:
            Path(command[-1]).write_bytes(b"validated-sdr-output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(hdr_sdr, "_run_command", fake_run)
    applied = hdr_sdr.apply_plan(str(plan_path))
    delivery = tmp_path / "output" / "share_sdr.mp4"

    assert delivery.read_bytes() == b"validated-sdr-output"
    assert source.read_bytes() == b"hdr-source"
    assert any("-xerror" in command for command in commands)
    assert applied["summary"]["blocking"] == 0
    assert applied["application"]["validation"]["decode_checked"] is True
    assert applied["application"]["validation"]["output_sha256"] == hdr_sdr._sha256(delivery)
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert hdr_sdr.verify_plan(persisted)["summary"]["blocking"] == 0


def test_output_contract_rejects_wrong_color_tags(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    wrong = {**SDR_MEDIA, "color_transfer": "smpte2084", "color_primaries": "bt2020"}

    blockers = hdr_sdr._output_contract_blockers(wrong, plan["source"], plan["settings"])

    assert any("color_primaries must be bt709" in item for item in blockers)
    assert any("color_transfer must be bt709" in item for item in blockers)


def test_plan_cli_refuses_silent_artifact_overwrite(tmp_path, monkeypatch, capsys):
    _patch_ready(monkeypatch)
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "hdr_sdr_plan.json"
    args = [
        "plan",
        str(source),
        "--delivery",
        str(tmp_path / "output" / "share_sdr.mp4"),
        "--output",
        str(plan_path),
    ]

    assert hdr_sdr.main(args) == 0
    original = plan_path.read_bytes()
    assert hdr_sdr.main(args) == 1
    assert plan_path.read_bytes() == original
    assert "pass --force to replace" in capsys.readouterr().err


def test_plan_cli_preflights_all_artifacts_before_writing(tmp_path, monkeypatch):
    _patch_ready(monkeypatch)
    source = _source(tmp_path)
    plan_path = tmp_path / "work" / "hdr_sdr_plan.json"
    markdown_path = tmp_path / "work" / "hdr_sdr_plan.md"
    markdown_path.parent.mkdir(parents=True)
    markdown_path.write_text("user review", encoding="utf-8")

    result = hdr_sdr.main(
        [
            "plan",
            str(source),
            "--delivery",
            str(tmp_path / "output" / "share_sdr.mp4"),
            "--output",
            str(plan_path),
            "--markdown",
            str(markdown_path),
        ]
    )

    assert result == 1
    assert not plan_path.exists()
    assert markdown_path.read_text(encoding="utf-8") == "user review"


def test_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "hdr_sdr.py"), "apply", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "atomically promote" in result.stdout
