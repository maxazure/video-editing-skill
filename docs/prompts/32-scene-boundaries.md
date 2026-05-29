# 32 Scene Boundaries 视觉场景边界

> 先检测长视频里的视觉切点，再让 highlight picker 把候选片段对齐到自然场景边界。

适合访谈、课程、直播回放、演示录屏、混剪素材这类“文字内容精彩，但画面切点也重要”的长视频。脚本只调用本地 FFmpeg，不调用 LLM、不上传视频、不提交任何付费生成任务。

## 基本用法

```bash
python3 scripts/scene_boundaries.py origin/long-talk.mp4 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md \
  --threshold 0.35 \
  --min-scene-duration 1.0
```

输出：
- `scene_boundaries.json`：`scene_boundaries.v1`，包含 `boundaries[]` 和 `scenes[]`
- `scene_boundaries.md`：人工复核表，按场景列出时间段和持续时间

如果切点太密，调高 `--threshold` 或 `--min-scene-duration`；如果漏掉明显切点，调低 `--threshold`。

## 接入 Highlight Picker

```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --scene-boundaries work/scene_boundaries.json \
  --scene-snap-tolerance 1.5 \
  --video origin/long-talk.mp4 \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --render-config work/highlight_render_config.json \
  --platform xhs \
  --num-clips 3 \
  --strict
```

`highlight_picker.py` 会保持 transcript 打分逻辑不变，只在候选片段开始/结束点附近找视觉边界：
- start 只会向前扩展到最近的场景切点
- end 只会向后扩展到最近的场景切点
- 默认容忍距离是 `1.5s`
- 如果扩展后超过平台最大时长，就放弃对应扩展

这样做的目的不是让视觉边界替代内容判断，而是避免短视频开头/结尾卡在镜头中间。

## Review 建议

先看 `highlight_candidates.md`：
- `Scene Snap` 显示 `start/end` 各自扩展了多少秒
- `warnings` 仍然优先处理，比如 weak hook 或 mid-thought ending
- 对访谈/课程，宁愿让 scene snap 多留 0.5-1 秒，也不要吞掉开头语气词后的第一句重点

确认后再渲染：

```bash
python3 scripts/render_final.py \
  --config work/highlight_render_config.json \
  --output output/highlight_master.mp4 \
  --versioned-output
```

## 何时不用

- 纯口播固定机位：scene boundaries 通常很少，直接用 transcript highlight 就够。
- 快切素材或游戏录屏：先把 `--threshold` 调高，否则可能产生太多视觉切点。
- 已经手工定好剪点：直接编辑 `render_config.json` 更快。
