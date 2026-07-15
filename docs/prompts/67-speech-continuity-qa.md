# 67. Speech Continuity QA — 成片复读 / 口吃门禁

`render_qa.py` 能发现黑屏、静帧、静音和尺寸问题，但它听不懂语义。自动粗剪也可能在实际组装后留下半句复读、相邻 take 重复或句内口吃，所以最终 master 应按需做一次“成片二次 ASR → 语音连续性检查”。

## 什么时候使用

- `rough_cut.py` / `jump_cut.py` 删除了很多片段。
- 多个 take、多个源视频或 NLE timeline 被重新组装。
- 人耳听到疑似重复，但 cut list 和波形看不出问题。
- 发布前要把“没有技术性复读”变成可机读 gate。

## 完整流程

先从**已渲染 master**重新提取音频和转录，不要复用源素材 transcript：

```bash
python3 scripts/extract_audio.py output/final.mp4
python3 scripts/transcribe.py output/final_audio.wav \
  --model auto \
  --language zh \
  --word-timestamps
```

然后生成 JSON / Markdown，并在检测到重复时返回退出码 2：

```bash
python3 scripts/speech_continuity_qa.py output/final_transcript.json \
  --output verify/speech_continuity_qa.json \
  --markdown verify/speech_continuity_qa.md \
  --strict
```

脚本只读本地 transcript，不调用 LLM、不上传媒体、不消耗 provider credits。

## 检查内容

| finding kind | 含义 | 默认判定 |
|---|---|---|
| `boundary_exact_repeat` | 前一段结尾与后一段开头精确复读 | 至少 3 个中文字符 / 英文词，最多比较 12 个单位 |
| `adjacent_near_duplicate` | 相邻两个 take 内容高度相似 | 每段至少 5 个单位，相似度 ≥ 0.90 |
| `internal_immediate_repeat` | 同一 ASR segment 内连续说了两遍相同短语 | 至少 3 个单位 |

如果相邻 segment 都有 speaker label 且说话人不同，默认不判重复，避免把访谈中的确认/追问当成剪辑事故。确实要跨说话人检查时加 `--include-speaker-changes`。

## 常用调参

```bash
# 更保守：至少重复 5 个单位才阻塞
python3 scripts/speech_continuity_qa.py output/final_transcript.json \
  --min-repeat-units 5 \
  --strict

# ASR 分段间隔较大时，扩大相邻比较窗口
python3 scripts/speech_continuity_qa.py output/final_transcript.json \
  --max-boundary-gap 3.5 \
  --strict

# 只接受几乎相同的相邻 take
python3 scripts/speech_continuity_qa.py output/final_transcript.json \
  --similarity-threshold 0.96 \
  --strict
```

中文按单字计数，英文按单词计数；标点、大小写和多余空格会被归一化。

## 修复闭环

1. 打开 Markdown，按 finding 的时间范围试听 master。
2. 用 `timeline_view.py --at <seconds>` 查看对应画面和波形。
3. 调整源 `render_config.json`、rough/jump cut list 或 NLE timeline。
4. 从源素材重新渲染 master，避免在成片上二次拼补。
5. 重新转录新 master，再跑一次本脚本，直到 `summary.status=ready`。

`pipeline_manifest.py` 会发现 `speech_continuity_qa.json`；只要 `summary.blocking > 0` 就阻塞。需要把该报告设为必需项时：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require speech_continuity_qa \
  --strict
```

## 限制

- 这是技术性复读检测，不判断修辞性重复是否“好听”；命中项仍应人工试听。
- ASR 自身可能产生重复幻觉。先核对 master，再改剪辑。
- 它不替代 `render_qa.py`、`audio_master_report.py` 或 `timeline_view.py`；三者分别覆盖画面/信号、响度和切点证据。
