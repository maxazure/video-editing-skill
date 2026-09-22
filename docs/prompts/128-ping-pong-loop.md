# 128 — Ping-pong Loop 动作素材正放倒放循环

用于把短手势、产品旋转、粒子运动或生成视频中的一小段动作做成 `正放 → 倒放 → 正放` 的往返循环。它与 `loop_fill.py` 的 hard repeat 用途不同：hard repeat 直接连接源片尾和源片头；ping-pong 在折返点反转运动方向，适合原片首尾本来不闭合的短动作。

## 能力边界

- 输入必须是 progressive CFR SDR。VFR 先运行 `frame_rate_conform.py`；HDR、BT.2020 或高于 8-bit 的源片先确定 `hdr_sdr.py` 流程。
- 只选择短动作区间。FFmpeg `reverse` 需要缓存所选范围的解码帧，planner 会按 `width × height × frames × 4 bytes` 做保守内存估算；超过 `--max-working-set-mib` 会提前停止。
- 输出顺序是 `0…N-1,N-2…1`。倒放分支排除首尾端帧，避免普通 `forward + reverse` 在折返点和循环点产生一帧停顿。
- 源音频固定丢弃。倒放对白、环境声或现场音乐通常不自然；完成视觉复核后再把成片放入已审的 BGM / SFX 时间线。
- `--duration` 可以在最后一个周期中途结束，适合填时间槽；需要文件本身保持完整往返周期时用 `--cycles`。

## 计划与渲染

先确认本机具备 `reverse`、`loop`、`trim`、`concat` 等滤镜：

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile ping_pong_loop \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict
```

选择 1.20–2.05 秒的动作，生成 4 个完整周期：

```bash
python3 scripts/ping_pong_loop.py plan origin/gesture.mp4 \
  --start 1.20 \
  --end 2.05 \
  --cycles 4 \
  --delivery work/gesture-ping-pong.mp4 \
  --turnaround-proof verify/gesture-turnaround.mp4 \
  --loop-seam-proof verify/gesture-loop-seam.mp4 \
  --project-dir . \
  --output work/ping_pong_loop_plan.json \
  --markdown work/ping_pong_loop_plan.md

python3 scripts/ping_pong_loop.py apply \
  work/ping_pong_loop_plan.json \
  --markdown work/ping_pong_loop_plan.md
```

plan 会把 `--start/--end` 吸附到源片帧网格，记录完整 decoded cadence、源 SHA-256、选区帧数、正放/倒放帧数、周期帧数、目标帧数、缓存估算和 proof 帧范围。apply 在一条 filter graph 中完成选区、端帧去重、倒放、循环与最终裁切；交付件和两条 proof 都通过帧数/帧率/尺寸/无音轨合同及 `-xerror` 完整解码后才原子提升。

固定填满 8 秒时把 `--cycles 4` 改成 `--duration 8`。报告出现 `partial_final_cycle` warning 表示结尾落在周期中间；如果交付文件还要自行首尾循环，应改用完整 `--cycles`。

## 双边界与完整播放复核

`turnaround-proof` 显示前进变成后退的折返点；`loop-seam-proof` 把第一周期末尾和第一帧直接拼在一起。两条都以正常速度播放，再完整播放交付件：

```bash
python3 scripts/ping_pong_loop.py confirm work/ping_pong_loop_plan.json \
  --reviewed-by editor \
  --note "完整 1× 播放，折返点与循环点都没有停帧或闪跳" \
  --full-playback completed \
  --turnaround-playback completed \
  --seam-playback completed \
  --turnaround-motion pass \
  --loop-seam-motion pass \
  --duplicate-hold pass \
  --framing-integrity pass \
  --creative-intent pass \
  --markdown work/ping_pong_loop_plan.md

python3 scripts/ping_pong_loop.py verify work/ping_pong_loop_plan.json --strict

python3 scripts/pipeline_manifest.py . \
  --require ping_pong_loop_plan \
  --strict
```

任一播放未完成、检查项为 `fail/unobservable`，或源片、选区、内存合同、输出、proof、人工结论发生漂移，live gate 都会阻断。

## 给 Agent 的提示词

```text
把短动作做成正放倒放的 ping-pong loop。先用 runtime_preflight.py 检查 ping_pong_loop profile，再选择尽量短且动作完整的 source range。运行 ping_pong_loop.py plan 和 apply；不要保留或倒放源音频。正常速度播放 turnaround proof、loop-seam proof 和完整 delivery，确认两个方向边界没有重复端帧、停顿、闪跳或构图变化，效果符合创意意图后再 confirm，并用 verify 与 pipeline_manifest live gate 放行。
```
