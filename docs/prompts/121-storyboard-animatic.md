# Storyboard Animatic 分镜时长预演

适用于已经有 `storyboard_plan.v1` 和每镜头一张批准静帧，希望在付费视频生成或正式剪辑前，用完整播放提前判断镜头顺序、时长、节奏和相邻画面连续性的项目。

这个工作流只在本地运行 FFmpeg/FFprobe，不生成图片、不上传素材、不调用视频 provider、不消耗 credits。Animatic 是规划代理：它能暴露节奏和静帧衔接问题，不能证明最终运动、人物身份、物理表现、声音或剪辑一定正确。

## 前置条件

- `work/storyboard_plan.json` 必须是 `storyboard_plan.v1`，包含非空、唯一、按 start 严格递增的 shots。
- 每个 shot 必须提供一张不同的项目内 PNG/JPEG/WebP 静帧，推荐使用已批准的首帧、style frame 或生成分镜 panel。
- 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。最终采用的 panel 仍需人工查看。
- 可选传入本地旁白或 guide audio；短于分镜时会补静音，长于分镜时会裁到计划总时长，并留下 warning。

## 1. 建立 source-bound 计划

```bash
python3 scripts/storyboard_animatic.py plan \
  --project-dir . \
  --storyboard work/storyboard_plan.json \
  --panel shot_001=work/storyboard/shot_001.png \
  --panel shot_002=work/storyboard/shot_002.png \
  --panel shot_003=work/storyboard/shot_003.png \
  --audio work/narration.wav \
  --delivery verify/storyboard_animatic.mp4 \
  --output work/storyboard_animatic.json \
  --markdown work/storyboard_animatic.md
```

必须为每个 shot 重复一次 `--panel SHOT_ID=PATH`。缺失、重复、未知 shot、同一路径复用、symlink、项目目录逃逸、不可解码图片都会直接拒绝。默认从 storyboard 的 `target.aspect` 推导最大边 1280 的偶数画布；也可同时指定 `--width` 和 `--height`。`--fit contain` 保留完整 panel 并加黑边，`--fit cover` 填满画布并裁切边缘。

时间线在 shot 的 `start` 处换 panel，最后一镜保持到自身 `end`；首镜从 0 秒显示，因此能和完整 guide audio 一起预演前置停顿。每段左上角烧录 `shot_id + display time`，便于逐镜反馈。计划绑定 storyboard、全部 panels、可选音频的路径、大小、SHA-256 和媒体元数据。

刚创建的计划会保留两个预期 blocker：尚未渲染、尚未完成 1× 人工复核。

## 2. 渲染并完整解码

```bash
python3 scripts/storyboard_animatic.py apply work/storyboard_animatic.json \
  --markdown work/storyboard_animatic.md
```

`apply` 在同目录临时文件中完成所有图片的 scale/pad 或 scale/crop、CFR、时间码标签、concat 和可选 AAC 48 kHz guide audio。只有 H.264/yuv420p、画布、FPS、时长、音轨合同和 `ffmpeg -xerror` 完整解码都通过，且输入在渲染期间没有变化，MP4 才会原子提升到目标路径。已有交付件默认不覆盖；明确重建时加 `--force`。

## 3. 完整播放并确认

先在目标显示设备上完整、正常速度播放 `verify/storyboard_animatic.mp4`，逐项判断：

1. `shot_order`：镜头顺序是否支持故事；
2. `timing_rhythm`：每镜头停留是否足够理解、没有无意拖沓或抢拍；
3. `panel_legibility`：主体、构图和关键内容在实际画布可读；
4. `visual_continuity`：相邻镜头的人物、道具、空间、光线和屏幕方向没有明显冲突；
5. `audio_sync`：有 guide audio 时，旁白/节拍和画面切换一致；无音频时填 `not_applicable`。

```bash
python3 scripts/storyboard_animatic.py confirm work/storyboard_animatic.json \
  --reviewed-by Jay \
  --note "完整 1× 播放，逐镜检查顺序、时长、可读性和边界。" \
  --full-playback completed \
  --shot-order pass \
  --timing-rhythm pass \
  --panel-legibility pass \
  --visual-continuity pass \
  --audio-sync pass \
  --markdown work/storyboard_animatic.md
```

任何 `fail`、缺项或未完整播放都会保持 blocked。确认记录绑定当前 MP4 SHA-256，重渲染或替换文件后必须重看。

## 4. Live verify 与流水线门禁

```bash
python3 scripts/storyboard_animatic.py verify work/storyboard_animatic.json \
  --strict \
  --markdown work/storyboard_animatic.md

python3 scripts/pipeline_manifest.py . \
  --require storyboard_animatic \
  --strict
```

`verify` 会重新读取 storyboard、全部 panel、可选音频和 MP4，重算源摘要、分镜派生时间线、输出媒体合同和完整解码。任一源文件、计划字段、输出字节或人工 review 绑定变化都会 fail closed。画幅 normalization warning 不等于阻断，但必须在完整播放中确认黑边或裁切可接受。

## 何时重做

- 调整任何 shot 的 start/end/duration、顺序或 ID；
- 替换或重新生成 panel；
- 改变 guide audio、画布、FPS 或 fit；
- animatic 暴露镜头过长、过短、顺序错误、文字太小或连续性冲突；
- 计划通过后又重渲染了 MP4。

先修改 storyboard/panels，再重新 `plan → apply → confirm → verify`。通过 animatic 后，生成式镜头仍需执行 `generated_clip_review.py` 与 `generated_sequence_review.py`；最终组装件仍需完整 1× 复核和成片 QA。
