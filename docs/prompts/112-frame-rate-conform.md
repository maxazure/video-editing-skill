# Frame-rate Conform 可变帧率源素材归一

手机、屏幕录制和会议软件常输出 VFR（variable frame rate）素材。它能正常播放，但逐帧时间间隔不均；经过多段裁切、拼接、字幕或外部音轨处理后，时间码可能变得难以复现，也更容易暴露音画漂移。

`frame_rate_conform.py` 在剪辑前创建一份 source-bound CFR 工作副本。它不改 `origin/` 原片，也不把简单的 `r_frame_rate != avg_frame_rate` 当作完整证据：脚本读取所有解码视频帧的 `best_effort_timestamp_time`，统计相邻 PTS 间隔，再验证新文件的实际帧间隔、帧数和音画流起止。

## 何时使用

- `ffprobe` 报告 nominal / average rate 不一致，或手机、录屏素材已知为 VFR。
- 多次切段或拼接后出现逐渐累积的音画漂移。
- 下游需要可复现的逐帧时间线，例如字幕、镜头边界、screen focus、multicam 或 NLE handoff。
- 多份素材帧率不同，且准备在剪辑前先统一工作帧率。

如果问题来自独立录音设备时钟漂移，用 `audio_sync.py` / `multicam_sync.py`；数字人口型问题用 `lip_sync_review.py`。CFR 归一不会自动修复这些问题。

## 计划、应用与验证

```bash
python3 scripts/frame_rate_conform.py plan origin/phone.mp4 \
  --fps 30 \
  --delivery work/phone-cfr.mp4 \
  --project-dir . \
  --output work/frame_rate_conform_plan.json \
  --markdown work/frame_rate_conform_plan.md

# 先看 decoded frame count、variable intervals 和目标帧率，再执行：
python3 scripts/frame_rate_conform.py apply work/frame_rate_conform_plan.json
python3 scripts/frame_rate_conform.py verify work/frame_rate_conform_plan.json --strict
python3 scripts/pipeline_manifest.py . \
  --require frame_rate_conform_plan --strict
```

目标帧率必须显式填写；支持整数、十进制和有理数：

```bash
--fps 30
--fps 29.97          # 规范化为 30000/1001
--fps 30000/1001     # 精确 NTSC rate
--fps 60
```

口播、普通手机视频通常用 30；运动、游戏或原生 60fps 录屏在目标平台允许时保留 60。把 60 降到 30 会丢掉一半运动采样；把低帧率升到 60 只会复制帧，不会生成新动作。

## Artifact 与门禁

计划记录：

- 原片绝对路径、SHA-256、大小、codec、pixel format、显示尺寸、rotation、色彩 metadata、音轨属性和音画流起止。
- `avg_frame_rate`、`r_frame_rate`，以及全量解码 PTS 的 frame/interval count、min/P05/median/mean/P95/max、variable ratio 和 non-monotonic count。
- 精确目标有理帧率、确定性 `fps` / `setsar=1` / audio timestamp filter、编码契约和容差。
- 输出 SHA-256、媒体与 cadence 合同、完整 FFmpeg decode receipt、canonical plan id。

apply 先在目标目录写临时 MP4。只有以下检查全部通过才原子提升：

- H.264、`yuv420p`、AAC 48 kHz 和 MP4 family 合同匹配；编码命令包含 `+faststart`。
- 显示尺寸保持，rotation 烧入画面并清零，平均帧率等于确切 target。
- 解码 PTS 单调且间隔恒定；`frame_count ≈ video_duration × target_fps`。
- 音画都从零点附近开始，尾端差和总时长变化不超过计划容差。
- `ffmpeg -xerror` 能完整解码全部流。

原片、计划、设置、工作副本或派生统计变化后，`verify` 与 `pipeline_manifest.py` 都会 fail closed。后续转写、切段、字幕、同步和渲染必须改用 `work/phone-cfr.mp4`。

## 边界

- 输出是编辑工作副本，会重编码画面与声音；原片继续保留在 `origin/`。
- HDR、BT.2020 或高于 8-bit 的输入会停止，先用明确的 HDR/SDR 色彩工作流处理，防止静默压成 SDR `yuv420p`。
- 已存在超过一个目标帧的音画起点偏移，或明显尾端差，会在 plan 阶段阻断；先判断它是错误、独立设备漂移还是有意留尾。
- CFR 通过只证明时基与媒体合同；仍需完整 1× 播放，检查 pan、屏幕滚动、快速动作、口型和结尾。
