# Loop Fill 短素材循环填满固定时长

用于把已能独立循环的环境片、纹理、动画背景或短 B-roll 重复到指定次数，或精确填满一个较长的时间槽。脚本保持原片不变，输出新的 H.264/AAC MP4、首个真实接缝 proof 和 source-bound 计划。

## 核心边界

- 渲染采用 hard repeat，不添加 crossfade，不合成首尾缺失动作。素材首尾不连贯时，proof 会保留真实跳变，复核应标记失败并更换素材或另做转场。
- VFR 或时间戳不单调的源片先用 `frame_rate_conform.py` 生成 CFR 工作副本。
- HDR、BT.2020 或高于 8-bit 的源片先确定 `hdr_sdr.py` 色彩流程。
- 环境背景通常使用 `--audio-mode drop`；需要保留现场环境声时使用 `preserve`，并重点听接缝的 click、pop、gap 和重复语句。
- `--duration` 可以在最后一个周期中途结束，适合填固定 slot；需要完整周期时使用 `--times`。

## 计划与执行

按目标时长填满 30 秒静音背景：

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile core_edit \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict

python3 scripts/loop_fill.py plan origin/ambient.mp4 \
  --duration 00:30 \
  --audio-mode drop \
  --delivery work/ambient-30s.mp4 \
  --seam-proof verify/ambient-loop-seam.mp4 \
  --project-dir . \
  --output work/loop_fill_plan.json \
  --markdown work/loop_fill_plan.md

python3 scripts/loop_fill.py apply \
  work/loop_fill_plan.json \
  --markdown work/loop_fill_plan.md
```

重复完整素材 4 次并保留音频时，把 `--duration 00:30 --audio-mode drop` 改成 `--times 4 --audio-mode preserve`。

plan 会记录源 SHA-256、完整 decoded cadence、精确有理帧率、源/目标时长、读取次数、内部接缝数量、音频策略和 proof 上下文。apply 用 FFmpeg `-stream_loop` 同步重复输入，输出固定 CFR H.264/yuv420p；保留音频时编码为 48 kHz AAC。交付件与 proof 都需通过媒体合同和 `-xerror` 完整解码才会提升。

## 正常速度复核与 gate

先播放 `verify/ambient-loop-seam.mp4`，再看完整 `work/ambient-30s.mp4`。静音背景这样确认：

```bash
python3 scripts/loop_fill.py confirm work/loop_fill_plan.json \
  --reviewed-by editor \
  --note "完整 1× 播放，首个接缝和所有重复周期均正常" \
  --full-playback completed \
  --seam-playback completed \
  --visual-transition pass \
  --motion-continuity pass \
  --duplicate-flash pass \
  --audio-transition not_applicable \
  --slot-coverage pass \
  --markdown work/loop_fill_plan.md

python3 scripts/loop_fill.py verify work/loop_fill_plan.json --strict

python3 scripts/pipeline_manifest.py . \
  --require loop_fill_plan \
  --strict
```

保留音频时，`--audio-transition` 必须为 `pass`。任一复核项为 `fail` / `unobservable`，未完整播放，或源片、交付件、proof、设置、派生状态发生漂移，gate 都会阻断。

## 给 Agent 的提示词

```text
把短背景素材重复到目标次数或固定时长前，先确认它是 progressive CFR SDR。用 loop_fill.py plan 绑定源片、delivery、audio mode 和 seam proof，再 apply。正常速度播放首个真实接缝 proof 与完整 delivery，逐项检查画面跳变、运动连续性、重复闪帧、声音 click/pop/gap 和时长覆盖；全部通过后 confirm，并用 pipeline_manifest live verify。不要用 hard repeat 掩盖本身不闭合的首尾。
```
