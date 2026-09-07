# Audio Dropout QA — 短促音频掉点复核

用于最终旁白、对白 stem 或 speech-dominant 成片中几十到几百毫秒的突然断音。`audio_master_report.py` 负责长静音、LUFS、true peak 和 LRA；本流程专门找被长静音门槛漏掉的短促近静音区间。

## 适用输入

优先输入最终独立对白/旁白轨。只有混音成片时也可运行，但必须在交付说明中写明 BGM/SFX 可能掩盖人声掉点。脚本读取第一条音频流、下混为单声道做检测，不改源文件。

默认检测条件：

- 16 kHz 分析采样率、20 ms 固定窗口；
- 候选窗口 RMS 不高于 `-60 dBFS`；
- 候选持续 `40–400 ms`；
- 前后各 `120 ms` 至少 75% 窗口达到 `-38 dBFS`；
- 候选相对前后文至少下降 `24 dB`。

这些门槛负责定位，不能自动判断“坏音频”。降噪门、刻意停顿、呼吸剪辑、音乐和音效都会改变结果。

## 1. 分析并导出听觉证据

```bash
python3 scripts/audio_dropout_qa.py analyze work/final_narration.wav \
  --project-dir . \
  --evidence-dir verify/audio_dropout_clips \
  --output verify/audio_dropout_qa_scan.json \
  --markdown verify/audio_dropout_qa_scan.md \
  --response-template verify/audio_dropout_qa_response.json \
  --strict
```

每个候选会生成一段带前后上下文的 48 kHz mono PCM WAV，保持正常速度。没有候选时报告可直接为 `ready`；有候选时会保持 `blocked`，直到完成复核。

## 2. 正常速度试听并填写 response

完整播放输入音轨，再逐段播放证据 WAV。填写：

- `reviewed_by`：复核标签；它不是身份认证或数字签名；
- `full_track_played_at_1x: true`；
- 每个候选的 `audible_observations.before / during / after`；
- `decision`：`dropout | intentional_pause | uncertain`；
- 原因；`dropout` / `uncertain` 还必须写 `repair_action`。

`intentional_pause` 会保留 warning；`dropout` 与 `uncertain` 继续阻塞。确认掉点后回到原始录音、拼接点或渲染链修复，再重新生成整个报告，不能只改 JSON。

## 3. 审计并现场复验

```bash
python3 scripts/audio_dropout_qa.py audit \
  --report verify/audio_dropout_qa_scan.json \
  --response verify/audio_dropout_qa_response.json \
  --output verify/audio_dropout_qa.json \
  --markdown verify/audio_dropout_qa.md \
  --project-dir . \
  --strict

python3 scripts/audio_dropout_qa.py verify \
  --report verify/audio_dropout_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require audio_dropout_qa \
  --strict
```

报告绑定源文件 SHA-256、音频媒体合同、算法、设置、现场 measurements、候选、证据 WAV 字节、人工 response、派生状态与 canonical id。源文件、检测结果、证据、response 或报告字段变化都会让 live verification 失效。

## 边界

- 它不做 ASR、forced alignment、可懂度判断或自动修音。
- 它不替代全轨 1× 试听。
- 它不替代 `audio_master_report.py` 的响度、爆峰、动态与长静音检查。
- 混音中的持续 BGM/SFX 可能填满人声掉点；有独立人声 stem 时应对 stem 运行。
- 大量候选超过 `--max-candidates` 时会 fail closed，先检查降噪门、素材损坏或阈值是否适合，再重跑。
