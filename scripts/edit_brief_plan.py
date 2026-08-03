#!/usr/bin/env python3
"""Route a natural-language edit brief to this skill's local workflow.

The script is deliberately deterministic: it scans a short user brief for
production signals, emits an ordered runbook with existing scripts, and writes
JSON/Markdown artifacts. It does not call an LLM, render media, upload files,
or submit paid generation jobs.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "edit_brief_plan.v1"
IMAGEGEN_ROUTING = "生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。"

PLATFORM_ALIASES: Sequence[Tuple[str, Sequence[str]]] = (
    ("xhs", ("小红书", "rednote", "red note", "red", "xhs")),
    ("douyin", ("抖音", "douyin", "tiktok china")),
    ("wxch", ("视频号", "微信视频号", "wechat channels", "wxch")),
    ("tiktok", ("tiktok", "tik tok")),
    ("youtube_shorts", ("youtube shorts", "yt shorts", "shorts")),
    ("instagram_reels", ("instagram reels", "reels")),
)

SIGNAL_KEYWORDS: Mapping[str, Sequence[str]] = {
    "source_ingest": (
        "raw footage",
        "footage",
        "素材目录",
        "原始素材",
        "素材堆",
        "建项目",
        "项目目录",
        "folder",
        "directory",
    ),
    "transcript": ("transcript", "whisper", "转写", "转录", "字幕", "caption", "subtitles", "口播", "voiceover"),
    "semantic_review": (
        "semantic transcript review",
        "context-aware transcript",
        "semantic subtitle review",
        "语义校稿",
        "语义审校",
        "上下文校稿",
        "全篇校稿",
        "专业术语校稿",
        "字幕错词",
    ),
    "multi_take": ("multi take", "takes", "多 take", "多条素材", "多个 take", "选 take", "挑 take"),
    "target_script": (
        "target script",
        "script alignment",
        "script-based edit",
        "按脚本剪",
        "按稿剪",
        "目标脚本",
        "确认稿",
        "成片稿",
        "对稿剪辑",
        "文案对齐",
        "照这个稿子",
    ),
    "long_to_short": (
        "long to short",
        "long video",
        "长视频",
        "访谈",
        "播客",
        "直播回放",
        "拆条",
        "拆短视频",
        "精华",
        "highlight",
        "clip",
        "clips",
    ),
    "batch_shorts": ("batch", "批量", "多条短视频", "3 条", "三条", "5 条", "五条", "several shorts", "multiple shorts"),
    "hook": ("hook", "开头", "前三秒", "3 秒", "3秒", "opening", "first three seconds"),
    "cleanup_silence": ("remove silence", "silence", "停顿", "空白", "冷场", "jump cut", "剪紧", "紧凑"),
    "cleanup_words": ("口头禅", "卡壳", "重复句", "filler", "stutter", "rough cut"),
    "story_rewrite": ("rewrite", "清稿", "重写", "重组", "故事线", "叙事", "结构", "story"),
    "content_guard": ("合规", "审核", "违禁", "限流", "risk", "guard", "发布前检查"),
    "source_receipts": ("事实", "数据", "新闻", "来源", "source", "citation", "proof", "claim"),
    "broll": ("b-roll", "broll", "素材画面", "换画面", "补画面", "stock", "pexels", "pixabay", "coverr"),
    "generated_assets": (
        "生图",
        "imagegen",
        "gpt-image",
        "gpt image",
        "dreamina",
        "即梦",
        "jimeng",
        "veo",
        "sora",
        "ltx",
        "wan",
        "生成视频",
        "generated video",
    ),
    "audio_design": ("bgm", "music", "配乐", "音效", "sfx", "sound design", "声音设计"),
    "audio_sync": ("外录", "领夹麦", "lav", "recorder", "scratch audio", "audio sync", "对齐音频", "同步音频"),
    "screen_focus": ("录屏", "screen recording", "demo", "cursor", "点击", "热点", "focus"),
    "pip": ("facecam", "webcam", "小窗", "pip", "camera overlay", "摄像头"),
    "color_grade": ("调色", "lut", "color grade", "cinematic", "色彩", "film look"),
    "render": ("render", "渲染", "导出", "输出", "成片", "final mp4"),
    "qa": ("qa", "质检", "黑屏", "静音", "静帧", "检查成片"),
    "review_proxy": ("review proxy", "审片代理", "审片视频", "低码率审片", "时间码审片", "timecode review"),
    "multi_platform": ("三平台", "多平台", "multi platform", "多个平台", "xhs douyin", "小红书 抖音"),
    "publish": ("发布", "上传", "publish", "upload", "发布包", "标题", "文案", "hashtag", "tags"),
    "subtitle_sidecar": ("srt", "vtt", "ass", "字幕文件", "sidecar"),
    "nle_handoff": ("premiere", "final cut", "fcpxml", "resolve", "达芬奇", "edl", "otio", "剪辑软件"),
    "review_dashboard": ("dashboard", "review dashboard", "复核面板", "人工复核", "看板"),
}

SIGNAL_LABELS: Mapping[str, str] = {
    "source_ingest": "素材导入 / 项目启动",
    "transcript": "转写 / 字幕时间码",
    "semantic_review": "全篇上下文语义校稿",
    "multi_take": "多 take 阅读视图",
    "target_script": "目标脚本对齐剪辑",
    "long_to_short": "长视频拆短视频",
    "batch_shorts": "多条短视频批量规划",
    "hook": "前三秒 hook",
    "cleanup_silence": "去停顿 / jump cut",
    "cleanup_words": "去口头禅 / 重复句",
    "story_rewrite": "清稿 / 叙事重组",
    "content_guard": "平台内容风控",
    "source_receipts": "事实来源复核",
    "broll": "B-roll / stock 素材规划",
    "generated_assets": "生成式图片 / 视频素材",
    "audio_design": "BGM / SFX 声音设计",
    "audio_sync": "外录音频对齐",
    "screen_focus": "录屏聚焦",
    "pip": "摄像头小窗",
    "color_grade": "调色计划",
    "render": "渲染成片",
    "qa": "成片质检",
    "review_proxy": "低码率时间码审片视频",
    "multi_platform": "多平台导出",
    "publish": "发布包 / 文案",
    "subtitle_sidecar": "字幕 sidecar",
    "nle_handoff": "NLE 交接",
    "review_dashboard": "人工复核面板",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_text(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def shell(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _contains(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def detect_platforms(brief: str, override: Optional[str] = None) -> List[str]:
    if override:
        return [override]
    found: List[str] = []
    for platform, aliases in PLATFORM_ALIASES:
        if any(_contains(brief, alias) for alias in aliases):
            found.append(platform)
    return found or ["douyin"]


def infer_source_media(brief: str) -> str:
    match = re.search(r"(?P<path>(?:~|\.{1,2}|/|[A-Za-z0-9_\-./])+?\.(?:mp4|mov|m4v|mkv|webm|avi|mp3|wav|m4a|aac|flac))", brief, re.IGNORECASE)
    if not match:
        return ""
    return match.group("path").rstrip(".,;:，。；：")


def detect_signals(brief: str, platforms: Sequence[str]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for signal, terms in SIGNAL_KEYWORDS.items():
        matched = [term for term in terms if _contains(brief, term)]
        if matched:
            signals.append(
                {
                    "id": signal,
                    "label": SIGNAL_LABELS.get(signal, signal),
                    "matched_terms": matched,
                }
            )

    platform_terms = [platform for platform in platforms if platform]
    if platform_terms:
        signals.append(
            {
                "id": "platform",
                "label": "目标平台",
                "matched_terms": platform_terms,
            }
        )
    return signals


def _signal_ids(signals: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(signal.get("id")) for signal in signals}


def _transcript_path(source_media: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if source_media and not source_media.startswith("<"):
        path = Path(source_media)
        return str(path.with_name(f"{path.stem}_transcript.json"))
    return "work/transcript.json"


def _clean_script_path() -> str:
    return "work/clean_script.md"


def _add_step(steps: List[Dict[str, Any]], seen: set[str], step: Dict[str, Any]) -> None:
    if step["id"] in seen:
        return
    seen.add(step["id"])
    step["order"] = len(steps) + 1
    steps.append(step)


def _step(
    step_id: str,
    *,
    phase: str,
    script: str,
    label: str,
    reason: str,
    command: str,
    outputs: Sequence[str],
    gate_category: Optional[str] = None,
    required: bool = True,
) -> Dict[str, Any]:
    return {
        "id": step_id,
        "phase": phase,
        "script": script,
        "label": label,
        "reason": reason,
        "command": command,
        "outputs": list(outputs),
        "gate_category": gate_category or step_id,
        "required": required,
    }


def build_plan(
    brief: str,
    *,
    project_dir: str = ".",
    source_media: str = "",
    transcript: Optional[str] = None,
    platform: Optional[str] = None,
    python_bin: str = "python3",
) -> Dict[str, Any]:
    normalized = " ".join(brief.split())
    source_media = source_media or infer_source_media(normalized)
    platforms = detect_platforms(normalized, platform)
    signals = detect_signals(normalized, platforms)
    ids = _signal_ids(signals)
    primary_platform = platforms[0]
    source = source_media or "<source_media>"
    transcript_path = _transcript_path(source_media, transcript)
    clean_script = _clean_script_path()
    blockers: List[str] = []
    warnings: List[str] = []
    notes: List[str] = []

    if not normalized:
        blockers.append("brief is empty")

    if source_media and not Path(source_media).expanduser().exists():
        blockers.append(f"source media not found: {source_media}")
    elif not source_media and ids.intersection({"source_ingest", "transcript", "target_script", "long_to_short", "render", "publish", "review_proxy"}):
        warnings.append("source media was not provided; commands use <source_media> placeholders")

    if transcript and not Path(transcript).expanduser().exists():
        warnings.append(f"transcript path was provided but does not exist yet: {transcript}")

    steps: List[Dict[str, Any]] = []
    seen: set[str] = set()

    needs_transcript = bool(
        ids.intersection(
            {
                "transcript",
                "semantic_review",
                "multi_take",
                "target_script",
                "long_to_short",
                "hook",
                "cleanup_words",
                "story_rewrite",
                "content_guard",
                "source_receipts",
                "broll",
                "generated_assets",
                "audio_design",
                "publish",
                "subtitle_sidecar",
            }
        )
    )
    wants_render = bool(ids.intersection({"render", "publish", "target_script", "long_to_short", "batch_shorts", "cleanup_silence", "cleanup_words", "broll", "screen_focus", "pip", "color_grade"}))
    wants_clean_script = bool(
        ids.intersection({"story_rewrite", "hook", "content_guard", "source_receipts", "broll", "generated_assets", "publish"})
        and "target_script" not in ids
    )

    if ids.intersection({"source_ingest"}) or not source_media:
        _add_step(
            steps,
            seen,
            _step(
                "source_inventory",
                phase="ingest",
                script="project_bootstrap.py",
                label="Create project folders and source inventory",
                reason="The brief refers to raw footage or lacks a concrete source path.",
                command=shell(
                    [
                        python_bin,
                        "scripts/project_bootstrap.py",
                        "--source",
                        source,
                        "--project-dir",
                        project_dir,
                        "--strict",
                    ]
                ),
                outputs=[f"{project_dir.rstrip('/')}/work/source_inventory.json", f"{project_dir.rstrip('/')}/project.md"],
                gate_category="source_inventory",
                required=False,
            ),
        )

    if needs_transcript:
        _add_step(
            steps,
            seen,
            _step(
                "transcript",
                phase="analysis",
                script="transcribe.py",
                label="Transcribe source media with word timestamps",
                reason="Downstream script, subtitle, highlight, hook, or publish steps need timed speech.",
                command=shell([python_bin, "scripts/transcribe.py", source, "--word-timestamps", "--detect-fillers"]),
                outputs=[transcript_path],
                gate_category="transcript",
            ),
        )

    if "semantic_review" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "semantic_transcript_review",
                phase="review",
                script="semantic_transcript_review.py",
                label="Prepare a source-bound context review packet",
                reason="The brief asks to check ASR wording or terminology with whole-transcript context.",
                command=shell(
                    [
                        python_bin,
                        "scripts/semantic_transcript_review.py",
                        "prepare",
                        "--transcript",
                        transcript_path,
                        "--output",
                        "work/semantic_review_request.json",
                        "--markdown",
                        "work/semantic_review_request.md",
                    ]
                ),
                outputs=["work/semantic_review_request.json", "work/semantic_review_request.md"],
                gate_category="semantic_transcript_review",
            ),
        )

    if "multi_take" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "takes_pack",
                phase="analysis",
                script="takes_pack.py",
                label="Pack multiple takes into a phrase-level reading view",
                reason="The brief asks to compare or select across multiple takes.",
                command=shell([python_bin, "scripts/takes_pack.py", "--transcripts-dir", "work/takes", "--output", "work/takes_packed.md", "--json", "work/takes_pack.json"]),
                outputs=["work/takes_pack.json", "work/takes_packed.md"],
                gate_category="takes_pack",
                required=False,
            ),
        )

    if "target_script" in ids:
        alignment_inputs = (
            ["--transcripts-dir", "work/takes"]
            if "multi_take" in ids
            else ["--transcript", f"main={transcript_path}", "--media", f"main={source}"]
        )
        _add_step(
            steps,
            seen,
            _step(
                "script_alignment",
                phase="edit",
                script="script_alignment.py",
                label="Align the reviewed target script to source speech",
                reason="The brief asks to assemble original spoken ranges in target-script order.",
                command=shell(
                    [
                        python_bin,
                        "scripts/script_alignment.py",
                        "--target-script",
                        "<target_script.md>",
                        *alignment_inputs,
                        "--output",
                        "work/script_alignment.json",
                        "--markdown",
                        "work/script_alignment.md",
                        "--render-config",
                        "work/render_config.json",
                        "--clean-script",
                        "work/clean_script.md",
                        "--strict",
                    ]
                ),
                outputs=[
                    "work/script_alignment.json",
                    "work/script_alignment.md",
                    "work/render_config.json",
                    "work/clean_script.md",
                ],
                gate_category="script_alignment",
            ),
        )

    if "long_to_short" in ids or "batch_shorts" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "highlight_candidates",
                phase="analysis",
                script="highlight_picker.py",
                label="Pick short-form highlight candidates",
                reason="The brief asks to cut a long recording into one or more short clips.",
                command=shell(
                    [
                        python_bin,
                        "scripts/highlight_picker.py",
                        "--transcript",
                        transcript_path,
                        "--video",
                        source,
                        "--output",
                        "work/highlight_candidates.json",
                        "--markdown",
                        "work/highlight_candidates.md",
                        "--platform",
                        primary_platform,
                        "--num-clips",
                        "5",
                        "--strict",
                    ]
                ),
                outputs=["work/highlight_candidates.json", "work/highlight_candidates.md"],
                gate_category="highlight_candidates",
            ),
        )

        _add_step(
            steps,
            seen,
            _step(
                "audio_boundary_plan",
                phase="analysis",
                script="audio_boundary_snap.py",
                label="Snap selected highlights to safe audio boundaries",
                reason="Word, sentence, and silence alignment prevents mid-word or mid-thought short-video cuts.",
                command=shell(
                    [
                        python_bin,
                        "scripts/audio_boundary_snap.py",
                        "--candidates",
                        "work/highlight_candidates.json",
                        "--transcript",
                        transcript_path,
                        "--media",
                        source,
                        "--output",
                        "work/audio_boundary_plan.json",
                        "--markdown",
                        "work/audio_boundary_plan.md",
                        "--strict",
                    ]
                ),
                outputs=["work/audio_boundary_plan.json", "work/audio_boundary_plan.md"],
                gate_category="audio_boundary_plan",
            ),
        )

    if "batch_shorts" in ids or ("long_to_short" in ids and "publish" in ids):
        _add_step(
            steps,
            seen,
            _step(
                "shorts_batch",
                phase="plan",
                script="shorts_batch.py",
                label="Turn selected highlights into render jobs",
                reason="The brief asks for multiple shorts or a repeatable short-clip queue.",
                command=shell(
                    [
                        python_bin,
                        "scripts/shorts_batch.py",
                        "--highlights",
                        "work/audio_boundary_plan.json",
                        "--video",
                        source,
                        "--output",
                        "work/shorts_batch.json",
                        "--markdown",
                        "work/shorts_batch.md",
                        "--render-config-dir",
                        "work/shorts_render_configs",
                        "--output-dir",
                        "output/shorts",
                        "--qa-dir",
                        "verify/shorts",
                        "--basename",
                        "short",
                        "--platform",
                        primary_platform,
                        "--strict",
                    ]
                ),
                outputs=["work/shorts_batch.json", "work/shorts_batch.md"],
                gate_category="shorts_batch",
            ),
        )

    if "hook" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "hook_variants",
                phase="plan",
                script="hook_variants.py",
                label="Generate opening hook variants",
                reason="The brief calls out the first seconds or opening angle.",
                command=shell([python_bin, "scripts/hook_variants.py", "--transcript", transcript_path, "--platform", primary_platform, "--output", "work/hook_variants.json", "--markdown", "work/hook_variants.md", "--strict"]),
                outputs=["work/hook_variants.json", "work/hook_variants.md"],
                gate_category="hook_variants",
                required=False,
            ),
        )

    if "cleanup_words" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "rough_cut",
                phase="edit",
                script="rough_cut.py",
                label="Create filler/repetition rough-cut list",
                reason="The brief asks to remove fillers, stutters, or repeated speech.",
                command=shell([python_bin, "scripts/rough_cut.py", "--transcript", transcript_path, "--cut-list", "work/rough_cut.json"]),
                outputs=["work/rough_cut.json"],
                gate_category="rough_cut",
            ),
        )

    if "cleanup_silence" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "jump_cut",
                phase="edit",
                script="jump_cut.py",
                label="Detect silence gaps and plan jump cuts",
                reason="The brief asks to remove silence or tighten pacing.",
                command=shell([python_bin, "scripts/jump_cut.py", source, "--dry-run", "--cut-list", "work/jump_cut.json"]),
                outputs=["work/jump_cut.json"],
                gate_category="rough_cut",
            ),
        )

    if wants_clean_script:
        _add_step(
            steps,
            seen,
            _step(
                "clean_script",
                phase="plan",
                script="rewrite_script.py",
                label="Emit a clean-script prompt, then validate the reviewed LLM JSON",
                reason="The brief needs story structure, safer wording, source-aware copy, or publish captions.",
                command=shell([python_bin, "scripts/rewrite_script.py", "--transcript", transcript_path, "--structure", "pain_solve", "--emit-prompt"]),
                outputs=[clean_script],
                gate_category="clean_script",
            ),
        )

    if "source_receipts" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "source_receipts",
                phase="review",
                script="source_receipts.py",
                label="Build a proof deck for factual claims",
                reason="The brief mentions facts, data, news, sources, or proof.",
                command=shell([python_bin, "scripts/source_receipts.py", "--claims", "work/source_claims.json", "--output", "work/source_receipts.json", "--markdown", "work/source_receipts.md", "--html", "work/source_receipts.html", "--strict"]),
                outputs=["work/source_receipts.json", "work/source_receipts.md", "work/source_receipts.html"],
                gate_category="source_receipts",
            ),
        )

    if "content_guard" in ids or "publish" in ids or any(p in {"xhs", "douyin", "wxch"} for p in platforms):
        _add_step(
            steps,
            seen,
            _step(
                "content_guard",
                phase="review",
                script="content_guard.py",
                label="Lint platform-sensitive wording before render/publish",
                reason="The brief targets short-form platforms or explicitly asks for risk checks.",
                command=shell([python_bin, "scripts/content_guard.py", "--script", clean_script, "--strict"]),
                outputs=["terminal report"],
                gate_category="content_guard",
                required=False,
            ),
        )

    if "audio_sync" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "audio_sync",
                phase="analysis",
                script="audio_sync.py",
                label="Align external recorder audio to camera scratch audio",
                reason="The brief mentions external audio, lav mic, recorder, or sync.",
                command=shell([python_bin, "scripts/audio_sync.py", "--reference-media", source, "--external-audio", "<external_audio>", "--output", "work/audio_sync_plan.json", "--markdown", "work/audio_sync_plan.md", "--strict"]),
                outputs=["work/audio_sync_plan.json", "work/audio_sync_plan.md"],
                gate_category="audio_sync",
            ),
        )

    if "broll" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "stock_material_plan",
                phase="assets",
                script="stock_material_plan.py",
                label="Plan stock/B-roll search terms and local registration",
                reason="The brief asks for B-roll, replacement visuals, or stock footage.",
                command=shell([python_bin, "scripts/stock_material_plan.py", "--subject", "<topic>", "--script", clean_script, "--output", "work/stock_material_plan.json", "--markdown", "work/stock_material_plan.md"]),
                outputs=["work/stock_material_plan.json", "work/stock_material_plan.md"],
                gate_category="asset_provenance",
                required=False,
            ),
        )

    if "generated_assets" in ids:
        notes.append(IMAGEGEN_ROUTING)
        _add_step(
            steps,
            seen,
            _step(
                "storyboard_plan",
                phase="assets",
                script="storyboard_plan.py",
                label="Plan generated image/video shots before submitting providers",
                reason="The brief mentions image generation, Dreamina/即梦, or generated video.",
                command=shell([python_bin, "scripts/storyboard_plan.py", "--transcript", transcript_path, "--clean-script", clean_script, "--output", "work/storyboard_plan.json", "--markdown", "work/storyboard_plan.md"]),
                outputs=["work/storyboard_plan.json", "work/storyboard_plan.md"],
                gate_category="storyboard_plan",
            ),
        )
        _add_step(
            steps,
            seen,
            _step(
                "video_prompt_pack",
                phase="assets",
                script="video_prompt_pack.py",
                label="Create provider prompt pack and paid-generation approval gate",
                reason="Generated video providers need prompts, continuity anchors, and approval before credits are spent.",
                command=shell([python_bin, "scripts/video_prompt_pack.py", "--storyboard-plan", "work/storyboard_plan.json", "--provider", "dreamina", "--output", "work/video_prompt_pack.json", "--markdown", "work/video_prompt_pack.md", "--strict"]),
                outputs=["work/video_prompt_pack.json", "work/video_prompt_pack.md"],
                gate_category="video_prompt_pack",
            ),
        )

    if "audio_design" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "audio_cue_sheet",
                phase="assets",
                script="audio_cue_sheet.py",
                label="Plan BGM/SFX cues and local audio blockers",
                reason="The brief asks for music, SFX, or sound design.",
                command=shell([python_bin, "scripts/audio_cue_sheet.py", "--transcript", transcript_path, "--asset-root", "assets/audio", "--output", "work/audio_cue_sheet.json", "--markdown", "work/audio_cue_sheet.md", "--strict"]),
                outputs=["work/audio_cue_sheet.json", "work/audio_cue_sheet.md"],
                gate_category="audio_cue_sheet",
            ),
        )

    if "screen_focus" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "screen_focus",
                phase="assets",
                script="screen_focus.py",
                label="Plan zoom/focus events for screen recordings",
                reason="The brief mentions screen recording, demos, clicks, or cursor focus.",
                command=shell([python_bin, "scripts/screen_focus.py", "--events", "work/screen_events.json", "--output", "work/screen_focus_plan.json"]),
                outputs=["work/screen_focus_plan.json"],
                gate_category="enrich_plan",
                required=False,
            ),
        )

    if "pip" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "pip_overlay",
                phase="assets",
                script="pip_overlay.py",
                label="Plan facecam picture-in-picture overlay",
                reason="The brief asks for facecam, webcam, or PIP camera overlay.",
                command=shell([python_bin, "scripts/pip_overlay.py", "--camera", "<facecam_video>", "--segment", "0:30", "--output", "work/pip_overlays.json"]),
                outputs=["work/pip_overlays.json"],
                gate_category="enrich_plan",
                required=False,
            ),
        )

    if "broll" in ids or "generated_assets" in ids or "audio_design" in ids or "screen_focus" in ids or "pip" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "enrich_plan",
                phase="assets",
                script="auto_enrich.py",
                label="Merge B-roll, generated assets, audio, focus, and PIP cues",
                reason="The request needs visual or audio enrichment before final render.",
                command=shell([python_bin, "scripts/auto_enrich.py", "--transcript", transcript_path, "--clean-script", clean_script, "--output", "work/enrich_plan.json"]),
                outputs=["work/enrich_plan.json"],
                gate_category="enrich_plan",
            ),
        )

    if "color_grade" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "color_grade",
                phase="render",
                script="color_grade.py",
                label="Create a bounded color-grade plan",
                reason="The brief asks for color, LUT, or a film/cinematic look.",
                command=shell([python_bin, "scripts/color_grade.py", "--preset", "natural", "--output", "work/color_grade.json", "--markdown", "work/color_grade.md", "--strict"]),
                outputs=["work/color_grade.json", "work/color_grade.md"],
                gate_category="color_grade",
                required=False,
            ),
        )

    if wants_render:
        _add_step(
            steps,
            seen,
            _step(
                "edit_preflight",
                phase="render",
                script="edit_preflight.py",
                label="Check render inputs before encoding",
                reason="Render-time failures should be caught before running FFmpeg.",
                command=shell([python_bin, "scripts/edit_preflight.py", "--config", "work/render_config.json", "--enrich-plan", "work/enrich_plan.json", "--output", "work/edit_preflight.json", "--strict"]),
                outputs=["work/edit_preflight.json"],
                gate_category="edit_preflight",
            ),
        )
        render_command = [python_bin, "scripts/render_final.py", "--config", "work/render_config.json", "--output", "output/final.mp4", "--primary-speed", "1.25", "--versioned-output"]
        if "broll" in ids or "generated_assets" in ids or "audio_design" in ids or "screen_focus" in ids or "pip" in ids:
            render_command.extend(["--enrich-plan", "work/enrich_plan.json"])
        if "color_grade" in ids:
            render_command.extend(["--color-grade", "work/color_grade.json"])
        _add_step(
            steps,
            seen,
            _step(
                "master_video",
                phase="render",
                script="render_final.py",
                label="Render final master MP4",
                reason="The brief asks for an output video or a publishable edit.",
                command=shell(render_command),
                outputs=["output/final_V<N>.mp4"],
                gate_category="master_video",
            ),
        )

    if wants_render or "qa" in ids or "publish" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "render_qa",
                phase="qa",
                script="render_qa.py",
                label="Run black-frame, still-frame, audio, and dimension QA",
                reason="Rendered videos should be checked before export or publish.",
                command=shell([python_bin, "scripts/render_qa.py", "output/final.mp4", "--platform", primary_platform, "--json", "verify/render_qa.json", "--review-dir", "verify/render_qa"]),
                outputs=["verify/render_qa.json", "verify/render_qa/"],
                gate_category="render_qa",
            ),
        )
        _add_step(
            steps,
            seen,
            _step(
                "audio_master_report",
                phase="qa",
                script="audio_master_report.py",
                label="Check loudness, true peak, LRA, and long silence",
                reason="Short-form publishing needs audio levels checked after final render.",
                command=shell([python_bin, "scripts/audio_master_report.py", "output/final.mp4", "--output", "verify/audio_master_report.json", "--markdown", "verify/audio_master_report.md", "--strict"]),
                outputs=["verify/audio_master_report.json", "verify/audio_master_report.md"],
                gate_category="audio_master_report",
                required=False,
            ),
        )

    if "subtitle_sidecar" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "subtitles",
                phase="publish",
                script="subtitle_pack.py",
                label="Export SRT/VTT/ASS/JSON subtitle sidecars",
                reason="The brief explicitly asks for subtitle files.",
                command=shell([python_bin, "scripts/subtitle_pack.py", "--transcript", transcript_path, "--output-dir", "output/subtitles"]),
                outputs=["output/subtitles/"],
                gate_category="subtitles",
                required=False,
            ),
        )

    if "review_proxy" in ids:
        proxy_input = "output/final.mp4" if wants_render else source
        _add_step(
            steps,
            seen,
            _step(
                "review_proxy",
                phase="review",
                script="review_proxy.py",
                label="Create a lightweight timecoded review proxy",
                reason="The brief asks for a shareable review video with precise timecode feedback.",
                command=shell([python_bin, "scripts/review_proxy.py", proxy_input, "--output", "verify/final_review_proxy.mp4", "--manifest", "verify/final_review_proxy.json", "--markdown", "verify/final_review_proxy.md"]),
                outputs=["verify/final_review_proxy.mp4", "verify/final_review_proxy.json", "verify/final_review_proxy.md"],
                gate_category="review_proxy",
                required=False,
            ),
        )

    if "multi_platform" in ids or len(set(platforms)) > 1:
        _add_step(
            steps,
            seen,
            _step(
                "platform_exports",
                phase="publish",
                script="multi_export.py",
                label="Export platform-specific aspect ratios",
                reason="The brief asks for multiple platforms or platform variants.",
                command=shell([python_bin, "scripts/multi_export.py", "output/final.mp4", "--platforms", "xhs", "douyin", "wxch"]),
                outputs=["output/*_xhs.mp4", "output/*_douyin.mp4", "output/*_wxch.mp4"],
                gate_category="platform_exports",
            ),
        )

    if "publish" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "caption",
                phase="publish",
                script="generate_caption.py",
                label="Generate title, body copy, tags, and post timing",
                reason="The brief asks for publish copy, title, tags, or upload package.",
                command=shell([python_bin, "scripts/generate_caption.py", "--script", clean_script, "--profile", "tech_pro", "--output", "work/caption.json"]),
                outputs=["work/caption.json"],
                gate_category="caption",
            ),
        )
        _add_step(
            steps,
            seen,
            _step(
                "publish_package",
                phase="publish",
                script="publish_package.py",
                label="Assemble final local publish package",
                reason="The request ends at upload-ready deliverables.",
                command=shell([python_bin, "scripts/publish_package.py", "--project-dir", project_dir, "--platforms", *platforms, "--caption", "work/caption.json", "--output", "work/publish_package.json", "--markdown", "work/publish_package.md", "--strict"]),
                outputs=["work/publish_package.json", "work/publish_package.md"],
                gate_category="publish_package",
            ),
        )

    if "nle_handoff" in ids:
        _add_step(
            steps,
            seen,
            _step(
                "nle_handoff",
                phase="handoff",
                script="export_otio.py",
                label="Export timeline handoff for professional NLEs",
                reason="The brief mentions Premiere, Final Cut, Resolve, EDL, FCPXML, or OTIO.",
                command=shell([python_bin, "scripts/export_otio.py", "--config", "work/render_config.json", "--output", "edit/timeline.otio"]),
                outputs=["edit/timeline.otio", "edit/timeline.otio.json"],
                gate_category="nle_handoff",
                required=False,
            ),
        )

    if "review_dashboard" in ids or len(steps) >= 8:
        _add_step(
            steps,
            seen,
            _step(
                "review_dashboard",
                phase="review",
                script="review_dashboard.py",
                label="Build a browser-readable review queue",
                reason="The plan has several gates or the brief asks for a review dashboard.",
                command=shell([python_bin, "scripts/review_dashboard.py", "--project-dir", project_dir, "--output", "work/review_dashboard.json", "--html", "work/review_dashboard.html", "--strict"]),
                outputs=["work/review_dashboard.json", "work/review_dashboard.html"],
                gate_category="review_dashboard",
                required=False,
            ),
        )

    if not steps and normalized:
        warnings.append("no specific edit signals matched; emitted a minimal transcript-to-render path")
        _add_step(
            steps,
            seen,
            _step(
                "transcript",
                phase="analysis",
                script="transcribe.py",
                label="Transcribe source media",
                reason="Fallback path for a generic video edit brief.",
                command=shell([python_bin, "scripts/transcribe.py", source, "--word-timestamps", "--detect-fillers"]),
                outputs=[transcript_path],
                gate_category="transcript",
            ),
        )
        _add_step(
            steps,
            seen,
            _step(
                "master_video",
                phase="render",
                script="render_final.py",
                label="Render final master MP4",
                reason="Fallback path for a generic video edit brief.",
                command=shell([python_bin, "scripts/render_final.py", "--config", "work/render_config.json", "--output", "output/final.mp4"]),
                outputs=["output/final.mp4"],
                gate_category="master_video",
            ),
        )

    gates = []
    for step in steps:
        category = str(step.get("gate_category") or "")
        if not category or any(gate["category"] == category for gate in gates):
            continue
        gates.append(
            {
                "category": category,
                "required": bool(step.get("required")),
                "reason": step.get("reason", ""),
            }
        )

    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "status": "blocked" if blockers else "ready",
        "source": {
            "brief": normalized,
            "project_dir": project_dir,
            "source_media": source_media,
            "transcript": transcript,
            "platforms": platforms,
        },
        "signals": signals,
        "steps": steps,
        "gates": gates,
        "notes": notes,
        "warnings": warnings,
        "blockers": blockers,
        "summary": {
            "signals": len(signals),
            "steps": len(steps),
            "required_steps": sum(1 for step in steps if step.get("required")),
            "blocking": len(blockers),
            "warnings": len(warnings),
        },
    }


def emit_markdown(plan: Mapping[str, Any]) -> str:
    source = plan.get("source") if isinstance(plan.get("source"), Mapping) else {}
    lines = [
        "# Edit Brief Plan",
        "",
        f"- Status: `{plan.get('status')}`",
        f"- Platforms: {', '.join(source.get('platforms') or [])}",
        f"- Project: `{source.get('project_dir') or '.'}`",
        "",
        "## Brief",
        "",
        str(source.get("brief") or ""),
        "",
    ]

    blockers = [str(item) for item in plan.get("blockers") or []]
    warnings = [str(item) for item in plan.get("warnings") or []]
    notes = [str(item) for item in plan.get("notes") or []]
    if blockers:
        lines.extend(["## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    if notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {item}" for item in notes)
        lines.append("")

    lines.extend(["## Matched Signals", ""])
    for signal in plan.get("signals") or []:
        if not isinstance(signal, Mapping):
            continue
        terms = ", ".join(str(item) for item in signal.get("matched_terms") or [])
        lines.append(f"- **{signal.get('label') or signal.get('id')}**: {terms}")
    lines.append("")

    lines.extend(["## Runbook", ""])
    for step in plan.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        lines.extend(
            [
                f"### {step.get('order')}. {step.get('label')}",
                "",
                f"- Phase: `{step.get('phase')}`",
                f"- Script: `{step.get('script')}`",
                f"- Gate: `{step.get('gate_category')}`",
                f"- Why: {step.get('reason')}",
                "- Command:",
                "",
                "```bash",
                str(step.get("command") or ""),
                "```",
                "",
            ]
        )
        outputs = [str(item) for item in step.get("outputs") or []]
        if outputs:
            lines.append("- Outputs: " + ", ".join(f"`{item}`" for item in outputs))
            lines.append("")

    lines.extend(["## Manifest Check", ""])
    required = [gate["category"] for gate in plan.get("gates") or [] if isinstance(gate, Mapping) and gate.get("required")]
    require_args = " ".join(f"--require {shlex.quote(category)}" for category in required)
    lines.extend(
        [
            "After producing the planned artifacts, check the requested gates:",
            "",
            "```bash",
            f"python3 scripts/pipeline_manifest.py --project-dir {shlex.quote(str(source.get('project_dir') or '.'))} --target-stage publish_ready {require_args} --strict",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route a natural-language edit brief to local video workflow scripts.")
    brief_group = parser.add_mutually_exclusive_group(required=True)
    brief_group.add_argument("--brief", help="Natural-language video edit request")
    brief_group.add_argument("--brief-file", help="Path to a text/Markdown file containing the edit request")
    parser.add_argument("--project-dir", default=".", help="Video project directory used in generated commands")
    parser.add_argument("--source-media", default="", help="Optional source video/audio path")
    parser.add_argument("--transcript", help="Optional existing transcript JSON path")
    parser.add_argument("--platform", choices=["douyin", "instagram_reels", "tiktok", "wxch", "xhs", "youtube_shorts"], help="Override platform detection")
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--output", default="work/edit_brief_plan.json")
    parser.add_argument("--markdown", help="Optional Markdown runbook path")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when the plan has blockers")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    brief = args.brief if args.brief is not None else load_text(args.brief_file)
    plan = build_plan(
        brief,
        project_dir=args.project_dir,
        source_media=args.source_media,
        transcript=args.transcript,
        platform=args.platform,
        python_bin=args.python_bin,
    )
    write_json(args.output, plan)
    if args.markdown:
        write_text(args.markdown, emit_markdown(plan))
    if plan["status"] == "blocked":
        print(f"Edit brief plan blocked: {plan['summary']['blocking']} blocker(s)", file=sys.stderr)
        return 2 if args.strict else 0
    print(f"Edit brief plan ready: {plan['summary']['steps']} step(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
