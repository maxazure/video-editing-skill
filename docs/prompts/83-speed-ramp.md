# Speed Ramp：局部慢动作与 velocity edit

适用于运动、产品 reveal、游戏、婚礼或 montage 中少量需要强调的 impact moment。它读取显式 source-time 锚点，先生成可审计计划，再用本地 FFmpeg 事务式渲染；不会自动猜动作峰值，也不会调用生成服务。

## 1. 先定 impact frame

用 `timeline_view.py` 或逐帧播放器找到真正需要变慢的接触帧 / 揭晓帧。不要先凭“第 5 秒左右”直接渲染：慢动作提前或落后几帧都会削弱效果。

下面例子在 `4.6–5.0s` 从 1× 平滑降到 0.25×，保持到 `5.8s`，再在 `5.8–6.2s` 回到 1×：

```bash
python3 scripts/speed_ramp.py plan origin/action.mp4 \
  --ramp 4.6,5.0,1,0.25,s_curve \
  --hold 5.0,5.8,0.25 \
  --ramp 5.8,6.2,0.25,1,ease \
  --interpolate-fps 120 \
  --output work/speed_ramp_plan.json \
  --markdown work/speed_ramp_plan.md
```

支持的 curve：

- `linear`：等速变化，适合短而直接的 ramp。
- `ease`：正弦缓入缓出，适合产品、人物或婚礼画面。
- `s_curve`：更平滑的五次曲线，适合大幅减速 / 恢复。
- `snap`：在 ramp 区间中点瞬时切换，适合明确 beat / impact；必须带音频正常速度复核。

`--ramp-steps` 默认 8，把曲线编译成若干 constant-speed 小段；它是确定性的近似，不是 NLE 光学流曲线。事件不能重叠，支持速度范围为 `0.1x–4x`。

## 2. 看计划，不要直接相信参数

Markdown 会列出：

- 源文件 SHA-256、fps、duration 和 plan id；
- 每个 ramp / hold 的 source range、速度和 impact anchor；
- 编译后的 output duration、最慢 / 最快速度；
- 突变边界、低 native unique fps、插帧不足和极慢音频 warning。

30 fps 源片降到 0.25× 时，原生只有约 7.5 个不同帧 / 输出秒。`--interpolate-fps 120` 可在变慢前用 FFmpeg `minterpolate` 补样，但插帧可能制造肢体、边缘或纹理扭曲；它不是生成模型，也不保证“无瑕慢动作”。高帧率原片仍然优先。

独立验证：

```bash
python3 scripts/speed_ramp.py verify work/speed_ramp_plan.json --strict
```

验证会重算 canonical `plan_id`、源文件大小 / SHA-256、完整 source/output coverage、逐段 `duration / speed` 和 review contract。源片换字节、删文件或手改 pieces 都会失败。`pipeline_manifest.py --require speed_ramp_plan --strict` 可把它设为显式 gate。

## 3. 事务式渲染

```bash
python3 scripts/speed_ramp.py apply work/speed_ramp_plan.json \
  --output work/action-speed-ramped.mp4 \
  --receipt work/speed_ramp_apply.json
```

脚本先渲染同目录临时 MP4，FFmpeg 成功后才替换最终路径；默认拒绝覆盖，确认目标正确后才加 `--force`。它会对画面用 `setpts`、对音频用可移植的分段 `atempo`，并在启用时先做 motion interpolation。原片不会修改，也不能把 output 指回 source。

如果极慢音频不适合，可在 plan 阶段加 `--mute-audio`，再单独做音乐 / SFX。已有旁白、字幕、章节和 cue 依赖旧时间线时，speed-ramped 文件应作为新的 source 重新进入 render config；不要沿用旧 sidecar 时间码。

## 4. 必做复核

1. 用 1× 播放速度、打开声音观看完整输出。
2. 确认减速精确落在 impact frame，而不是起跳 / 接触后的普通帧。
3. 检查 snap 是否突兀、ramp 是否拖沓、慢动作是否过多。
4. 逐帧看插值窗口有无重影、重复肢体、边缘破碎或纹理呼吸。
5. 重新运行 `render_qa.py`；如果继续进入完整剪辑，还要重新生成字幕和所有 output-time artifact。

一个 60 秒成片通常只需要少量明确的 speed ramp。这个工具提供可重复的时间映射和本地渲染，不负责自动 action detection、审美判断、beat 检测或 AI 补帧真实性。
