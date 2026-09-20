# 126 — Source-bound Video Enhancement 本地放大与补帧

当用户要把现有视频放大到更高交付分辨率，或把固定帧率素材补到更高帧率时，使用 `video_enhancement.py`。它执行本地、确定性的 FFmpeg Lanczos resize 与可选 `minterpolate`，绑定源文件、目标规格、输出和全长 A/B 复核。

这条路径不会重绘画面，也不会声称恢复源片中不存在的细节。需要 ML VSR、去压缩伪影、面部修复或生成式重建时，应另选经过授权的专用工具，并把结果作为新衍生件重新复核。

## 1. 运行环境预检

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile video_enhancement \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict
```

profile 检查 Python、FFmpeg/FFprobe、H.264/AAC encoder，以及 `scale / setsar / fps / hstack / minterpolate`。即使本次只放大、不补帧，完整 profile 仍证明同一工作流的全部能力可用。

## 2. 建立源绑定计划

按目标短边放大并补帧：

```bash
python3 scripts/video_enhancement.py plan output/master.mp4 \
  --target-short-edge 1080 \
  --fps 60 \
  --enhanced output/master-enhanced.mp4 \
  --comparison verify/video-enhancement-ab.mp4 \
  --output work/video_enhancement_plan.json \
  --markdown work/video_enhancement_plan.md
```

也可用固定倍率：

```bash
python3 scripts/video_enhancement.py plan output/master.mp4 \
  --scale 2 \
  --enhanced output/master-2x.mp4 \
  --comparison verify/video-enhancement-ab.mp4 \
  --output work/video_enhancement_plan.json
```

`--target-short-edge` 对横屏和竖屏含义一致：`1280×720 → 1920×1080`，`720×1280 → 1080×1920`。它与 `--scale` 互斥。只补帧时可以省略两者，仅传 `--fps`；目标帧率必须不低于源帧率，最高 120 fps。

计划会记录源 SHA-256、显示方向后的尺寸、时长、帧率、codec/像素格式、音轨存在性、目标尺寸/帧率和确切 filter 合同。无实际变化、缩小请求、超过 4× 的倍率、短边超过 4320 或所需 FFmpeg filter 缺失都会提前拒绝。

## 3. 渲染与完整解码

```bash
python3 scripts/video_enhancement.py apply \
  work/video_enhancement_plan.json \
  --markdown work/video_enhancement_plan.md
```

apply 使用同目录临时文件。增强件必须符合目标尺寸/帧率、保留源音轨存在性、时长漂移不越界，并通过 `ffmpeg -xerror` 完整解码后才原子提升。随后生成无声全长 A/B：左侧为源片，右侧为增强件；两边统一显示高度和目标时间基线，便于观察补帧差异。

## 4. 人工复核与确认

先正常速度完整播放 A/B，再单独带声播放完整增强件。确认四项：

- `detail`：真实细节、文字和线条没有受损。
- `edges`：没有明显 halo、ringing、蜡感或边缘破坏。
- `motion_cadence`：切点、遮挡、手、脸和高速运动没有 ghost/warp，未补帧时确认原 cadence 正常保留。
- `audio_sync`：声音连续且全片同步。

```bash
python3 scripts/video_enhancement.py confirm \
  work/video_enhancement_plan.json \
  --detail pass \
  --edges pass \
  --motion-cadence pass \
  --audio-sync pass \
  --reviewed-by editor \
  --note "完整播放 A/B 与带声增强件，细节、边缘、运动和同步均通过" \
  --markdown work/video_enhancement_plan.md
```

任一项填 `fail` 会把 review 记录为 rejected，并继续阻断。高运动、遮挡、whip pan、快速剪切或滚动字幕若出现补帧伪影，取消 `--fps` 重新计划；Lanczos 放大暴露压缩块或源片本就模糊时，不要把尺寸增加误当成画质恢复。

## 5. Live gate

```bash
python3 scripts/video_enhancement.py verify \
  work/video_enhancement_plan.json --strict

python3 scripts/pipeline_manifest.py . \
  --require video_enhancement_plan --strict
```

源文件、目标设置、增强件、A/B、完整解码绑定、人工结论或 canonical plan ID 发生漂移都会使旧计划失效。`warn` 会保留本地 resize 与补帧的能力边界，但完成复核后不作为 blocker。

## 可直接交给 Agent 的任务描述

```text
请用 video_enhancement.py 为现有固定帧率视频建立 source-bound 放大/补帧计划。先运行 video_enhancement runtime profile，再按目标短边或倍率和目标 fps 创建计划，执行 apply，并完整解码输出。正常速度完整观看 source-left/enhanced-right A/B，再带声完整观看增强件；逐项确认 detail、edges、motion_cadence、audio_sync 后 confirm，最后运行 verify 与 pipeline manifest。不要把 Lanczos resize 描述成 ML 超分或细节恢复。
```
