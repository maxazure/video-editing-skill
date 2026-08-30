# Audio Channel QA — 左右声道 / 相位 / 单声道兼容门禁

最终 master 的 LUFS、true peak 和时长都合格，仍可能出现另一类交付错误：只有一边先说话、某个声道缺失、左右能量严重不平衡、立体声反相，或折叠到手机单扬声器时中心内容明显消失。

`audio_channel_qa.py` 是只读的 `analyze → verify` 门禁。它使用本地 FFmpeg `aphasemeter + astats` 固定窗口采样，不改媒体、不上传文件、不调用 provider，也不自动修复音频。

## 快速使用

```bash
python3 scripts/audio_channel_qa.py analyze output/final.mp4 \
  --project-dir . \
  --output verify/audio_channel_qa.json \
  --markdown verify/audio_channel_qa.md \
  --strict

python3 scripts/audio_channel_qa.py verify \
  --report verify/audio_channel_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require audio_channel_qa \
  --strict
```

输入可以是最终视频或最终音频文件，但必须位于 `--project-dir` 内。报告会绑定 source SHA-256、大小、首个音轨的 codec / sample rate / channel layout、全部阈值、算法合同、现场 measurements、派生 checks 和 canonical report id；source、设置、算法或计算结果漂移都会让旧报告失效。

## 默认检查

| 检查 | 默认处理 |
|---|---|
| channel layout | mono 直接通过声道专项；stereo 进入测量；`>2` 声道要求先定义并复核明确 downmix |
| channel activity | 任一声道没有超过 `-50 dBFS` 的有效活动即阻断 |
| onset skew | `>50 ms` warning；`>200 ms` blocker |
| L/R integrated balance | 绝对差 `>3 dB` warning；`>6 dB` blocker |
| phase correlation | `<0.20` warning；`<-0.10` blocker |
| negative-phase windows | 超过 `0.50s` 且占双声道共同活动时间 `>10%` 时阻断 |
| mono fold-down loss | `>3.5 dB` warning；`>6 dB` blocker |

相位相关范围约为 `+1`（同相/中心）到 `-1`（反相）；普通宽立体声可接近 `0`。mono fold-down loss 是把左右声道等权折叠后，相对原立体声平均能量的估算损失。报告保留最差相位窗口，便于回到时间线定位试听。

## 修复方向

- 单边提前说话：检查 `adelay` 是否用了 `all=1`，以及每条旁白是否只在目标位置激活一次。
- 缺声道或严重失衡：检查 pan / channel map / muted clip / linked audio 状态，不要只把剩余一边复制过去掩盖根因。
- 反相或 mono 抵消：检查极性翻转、Haas delay、立体声扩宽和重复麦克风轨；修复后重新渲染并重新分析。
- 多声道：先明确 L/R/C/LFE/surround 到 mono/stereo 的 downmix 合同，并在正常播放设备上复核，不由本脚本猜测。

## 边界

- 这是采样式工程筛查，不是校准实验室或广播认证仪表。
- 它不识别语音，也不能判断创意 panning 是否合理；warning 必须结合上下文试听。
- 它不替代 1× 完整试听、`audio_master_report.py` 的 LUFS / dBTP / LRA / 长静音检查，也不替代 `audio_sync.py` 或口型同步复核。
- 最终交付前要分别试听原 stereo master 和 mono fold-down；不要仅凭 JSON 放行。
