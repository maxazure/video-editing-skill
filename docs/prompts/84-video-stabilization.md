# Video Stabilization：source-bound 手持防抖

适用于手机、运动相机、无人机或手持相机中“不想要的高频抖动”。流程会保留原片，先把源文件、FFmpeg 后端和人工决定写进计划，再渲染新的工作副本与全长 A/B 对照；只有看完对照并确认后，manifest gate 才会放行。

它不适合修复运动模糊、滚动快门果冻、严重失焦，也不应把有意的摇摄、跟拍或手持呼吸感自动抹平。

## 1. 检查本机后端

```bash
python3 scripts/video_stabilization.py doctor
```

后端优先级：

- `vidstabdetect + vidstabtransform`：两遍运动路径分析 / 平滑，质量更高。
- `deshake`：FFmpeg 内置单遍 fallback；计划会永久保留降级 warning，不会假装等价于 `vidstab`。

`plan --backend auto` 只在创建计划时选择一次，并把确切后端写入 artifact。后续 `verify/apply` 不会因为环境变化静默切换算法。

## 2. 先看原片，再记录决定

还没判断时先生成阻塞中的 review 计划：

```bash
python3 scripts/video_stabilization.py plan origin/handheld.mp4 \
  --decision review \
  --output work/video_stabilization_plan.json \
  --markdown work/video_stabilization_plan.md
```

确认是不想要的抖动后，重新生成带明确决定的计划：

```bash
python3 scripts/video_stabilization.py plan origin/handheld.mp4 \
  --profile balanced \
  --decision stabilize \
  --reviewed-by "editor" \
  --note "固定机位访谈中的高频手抖，不是有意摇摄" \
  --output work/video_stabilization_plan.json \
  --markdown work/video_stabilization_plan.md \
  --force
```

如果原来的手持感应该保留，用 `--decision keep --reviewed-by "editor" --note "..."`。这种计划不渲染新文件，但仍把源 SHA-256、后端检测结果和决定留作证据。

三个 profile：

- `conservative`：较小搜索 / 平滑范围，优先保留构图与真实运动。
- `balanced`：默认值，适合普通手机手持。
- `strong`：只用于明显抖动；更容易出现漂浮感、边缘扭曲或裁切压力。

计划绑定源文件大小、SHA-256、duration、fps、尺寸和音频状态。`reviewed_by_label` 只是工作流标签，不是身份认证或数字签名。

## 3. 渲染工作副本与全长对照

```bash
python3 scripts/video_stabilization.py apply work/video_stabilization_plan.json \
  --output work/handheld-stabilized.mp4 \
  --comparison verify/handheld-stabilization-compare.mp4 \
  --markdown work/video_stabilization_plan.md
```

原片永不覆盖。输出默认拒绝已有文件、symlink、source/plan 自覆盖；确认目标正确后才使用 `--force`。稳定版重新编码为 H.264/AAC，保留时长、尺寸与有无音频的契约；A/B 对照把原片放左边、稳定版放右边并保持全长。

如果计划选中两遍 `vidstab`，motion transforms 只存在于受控临时目录，完成后删除；如果选中 `deshake`，apply 使用计划内固定的 `rx/ry/edge/blocksize/contrast/search`，不会临场改参数。

## 4. 看完整对照后确认

用 1× 播放速度看完整 `verify/handheld-stabilization-compare.mp4`，重点检查：

1. 人脸、建筑直线、屏幕边框和画面四角是否扭曲。
2. 有意的 pan / follow shot 是否变得漂浮或滞后。
3. 镜像边缘是否呼吸、拉伸或暴露异常纹理。
4. 构图是否仍能安全进入后续竖屏重构图。
5. 抖动是否确实降低，而不是只换成另一种 wobble。

确认可用：

```bash
python3 scripts/video_stabilization.py confirm work/video_stabilization_plan.json \
  --reviewed-by "editor" \
  --note "完整 1x A/B 已看；边缘、人物和有意摇摄均可接受" \
  --markdown work/video_stabilization_plan.md

python3 scripts/video_stabilization.py verify work/video_stabilization_plan.json --strict
```

确认前，`decision=stabilize` 会依次因“尚未 apply”和“尚未复核 comparison”阻塞。确认后，live verify 仍会重算 plan id，并检查源片、稳定版和 comparison 的大小 / SHA-256；任何替换、删除、篡改或后端缺失都会让 gate 重新阻塞。

```bash
python3 scripts/pipeline_manifest.py . \
  --target-stage publish_ready \
  --require video_stabilization_plan \
  --strict
```

后续一律把 `work/handheld-stabilized.mp4` 当工作素材，不要删除或替换 `origin/handheld.mp4`。稳定化会改变像素但不应改变时间线；仍需重新检查主体重构图、隐私遮挡、调色、字幕安全区和最终 render QA。
