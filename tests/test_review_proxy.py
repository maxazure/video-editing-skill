import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from review_proxy import (  # noqa: E402
    build_ffmpeg_command,
    build_plan,
    build_video_filter,
    default_paths,
    emit_markdown,
    escape_drawtext_label,
    media_summary,
)


def _meta(audio=True):
    streams = [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1080,
            "height": 1920,
            "duration": "12.5",
        }
    ]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {"format": {"duration": "12.5"}, "streams": streams}


def test_default_paths_keep_proxy_artifacts_together(tmp_path):
    output, manifest, markdown = default_paths(str(tmp_path / "master.mp4"))

    assert output.endswith("master_review_proxy.mp4")
    assert manifest.endswith("master_review_proxy.json")
    assert markdown.endswith("master_review_proxy.md")


def test_media_summary_reports_video_audio_and_duration():
    summary = media_summary(_meta(audio=True))

    assert summary == {
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
        "duration_seconds": 12.5,
    }


def test_escape_drawtext_label_handles_ffmpeg_special_characters():
    escaped = escape_drawtext_label("Client's 50%: V2\\cut")

    assert escaped == "Client\\'s 50\\%\\: V2\\\\cut"


def test_video_filter_downscales_without_upscaling_and_burns_timecode():
    value = build_video_filter(max_height=720, fps=24, label="REVIEW", timecode=True)

    assert "scale=-2:'min(ih,720)'" in value
    assert "fps=24" in value
    assert "text='REVIEW'" in value
    assert "text='%{pts\\:hms}'" in value
    assert "y=20+h/30+18" in value
    assert "boxcolor=black@0.68" in value


def test_video_filter_can_disable_all_burn_in_text():
    value = build_video_filter(max_height=540, fps=15, label="", timecode=False)

    assert "scale=-2:'min(ih,540)'" in value
    assert "drawtext" not in value


def test_ffmpeg_command_is_web_ready_and_maps_optional_audio(tmp_path):
    command = build_ffmpeg_command(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"))

    assert command[:3] == ["ffmpeg", "-hide_banner", "-y"]
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "0:a?" in command
    assert command[command.index("-preset") + 1] == "veryfast"
    assert command[command.index("-crf") + 1] == "28"
    assert command[command.index("-movflags") + 1] == "+faststart"


def test_plan_warns_for_silent_source_and_markdown_marks_non_publish(tmp_path):
    command = ["ffmpeg", "-i", "in.mp4", "out.mp4"]
    plan = build_plan(
        str(tmp_path / "in.mp4"),
        str(tmp_path / "out_review_proxy.mp4"),
        _meta(audio=False),
        command,
        max_height=720,
        fps=24,
        crf=28,
        preset="veryfast",
        audio_bitrate="96k",
        label="REVIEW PROXY",
        timecode=True,
    )

    assert plan["version"] == "review_proxy.v1"
    assert plan["summary"]["blocking"] == 0
    assert plan["summary"]["warnings"] == 1
    assert plan["proxy"]["audio_codec"] is None
    markdown = emit_markdown(plan)
    assert "not a publish master" in markdown
    assert "Use the visible elapsed timecode" in markdown
