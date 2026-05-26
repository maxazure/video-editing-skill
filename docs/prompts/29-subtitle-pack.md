# Subtitle Pack 字幕交付包

把已有 `transcript.json` 或 `render_config.json` 导出为平台可上传的字幕 sidecar：

- `.srt`：通用平台字幕
- `.vtt`：网页播放器 / YouTube 常用
- `.ass`：保留本项目短视频字幕位置和描边风格，可本地复核
- `.json`：保留 cue、来源片段、时序参数和警告，方便 agent 复查

## 适用场景

- 成片已经用 `render_final.py` 导出，但平台还需要上传 SRT/VTT。
- 自动剪辑后要把字幕交给人工校对，而不是只烧录在画面里。
- 有封面秒数或 `--primary-speed` 加速，普通 transcript 字幕会和最终视频错位。

## 从转写直接导出

适合原始音频或原始视频未做拼接的情况：

```bash
python3 scripts/subtitle_pack.py \
  --transcript work/day58_transcript.json \
  --output-dir output/subtitles \
  --basename day58 \
  --formats srt vtt ass json
```

默认会按语言选择单行长度：中文 18 字、英文 42 字。中文会优先在标点处切开，避免平台字幕一行太长。

## 对齐最终 render_config 成片

如果最终视频来自 `render_final.py --config` 的片段串接，用 `--config`。它会按 clips 顺序重建最终时间线：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename day58_master \
  --speed 1.25 \
  --offset 2.0 \
  --formats srt vtt ass json
```

- `--speed` 对应 `render_final.py --primary-speed`。
- `--offset` 对应片头封面或前贴片秒数。
- `--mode concat` 是 `--config` 的默认模式；`--transcript` 默认 `--mode source`。

## 校对建议

先看 `*.json` 的 `stats.warnings`。如果出现 `over_max_chars`，通常是英文长词、产品名或 URL 无法自然切开；可调大 `--max-chars`，或先在清稿中把长 token 改成更适合口播的写法。

常用短视频设置：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename xhs_upload \
  --language zh \
  --max-chars 16 \
  --speed 1.25 \
  --offset 2.0
```

