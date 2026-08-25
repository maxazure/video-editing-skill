# Freeze-Punch 关键帧定格强调

适用于动作落点、产品 reveal、惊讶表情、比分/数字揭晓等已经人工确定的 peak moment。它把关键帧后的短画面窗口替换为该关键帧，并做轻微定点 punch-in；音频与总时长保持不变。

不适合：可见人物正在说话、关键动作必须连续可读、画面冻结会掩盖手部/产品状态、或原片分辨率不足以承受裁切放大。此时优先用 `speed_ramp.py`、普通硬切或 B-roll cover。

## 1. 逐帧确定 impact time

先用 `timeline_view.py` 或播放器逐帧查看源片，不要让脚本猜峰值：

```bash
python3 scripts/timeline_view.py origin/reaction.mp4 \
  --at 4.80 \
  --radius 0.5 \
  --output verify/reaction-impact.png
```

## 2. 建立 source-bound 计划

```bash
python3 scripts/freeze_punch.py plan origin/reaction.mp4 \
  --freeze 4.80,0.80,1.08,0.50,0.42 \
  --delivery work/reaction-freeze-punched.mp4 \
  --output work/freeze_punch_plan.json \
  --markdown work/freeze_punch_plan.md
```

`--freeze` 格式：

```text
TIME,DURATION[,SCALE[,ANCHOR_X[,ANCHOR_Y]]]
```

- `TIME`：冻结帧与替换窗口起点，使用源片秒数。
- `DURATION`：替换窗口长度，范围 `0.1–3.0s`；默认建议 `0.4–1.0s`。
- `SCALE`：punch 放大，范围 `1.0–1.5x`，默认 `1.08x`。
- `ANCHOR_X/Y`：裁切锚点，`0–1`；主体偏上时可把 `Y` 调到 `0.35–0.45`。
- `--freeze` 可重复传入，但窗口不能重叠。

刚生成的 plan 会明确包含 `freeze-punch render has not been applied` blocker；这是预期状态。此时先阅读 Markdown，确认窗口、crop 和 warnings，不要把 pending plan 当成交付件。

## 3. Apply、验证与复核

```bash
python3 scripts/freeze_punch.py apply work/freeze_punch_plan.json
python3 scripts/freeze_punch.py verify work/freeze_punch_plan.json --strict

python3 scripts/render_qa.py work/reaction-freeze-punched.mp4 \
  --json work/reaction-freeze-punched_qa.json \
  --review-dir verify/reaction-freeze-punched

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require freeze_punch_plan \
  --strict
```

`apply` 只写同目录临时 MP4；H.264/AAC、`yuv420p`、尺寸、fps、音轨、未改变的总时长与完整解码全部通过后，才原子提升为 `--delivery`。随后它把输出 path、SHA-256、大小和媒体契约写回原 plan。源片、计划或成片字节变化后，`verify` / manifest 都会阻断。

最终必须用 1×、带声音检查：

1. freeze 是否精确落在峰值帧，而不是峰值前后。
2. 入口/出口是否产生难懂的动作跳跃。
3. 是否冻结了正在说话的嘴、眨眼或关键手势。
4. punch crop 是否切掉脸、手、产品、字幕或界面重点。
5. 放大后的画面是否明显变软。

## 与 Speed Ramp 的区别

- `freeze_punch.py`：替换原有画面窗口，音频和总时长不变；下游字幕/章节时间码不因本操作漂移。
- `speed_ramp.py`：保留完整动作但改变播放速度和总时长；下游 timed artifacts 必须重新生成。

freeze-punch 仍会改变视频像素，因此任何绑定旧成片 bytes 的 QA、approval receipt 或 publish package 都必须重做。SHA-256 只是字节绑定，不是数字签名，也不能证明审美选择正确。
