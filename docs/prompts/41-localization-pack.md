# 41 — Localization Pack 多语字幕 / 配音交付包

`scripts/localization_pack.py` 把现有 `transcript.json` 或 `render_config.json` 变成可审校的多语字幕和配音任务包。适合中文视频要发英文、日文、西语等版本，或要把翻译后的台词交给 TTS / 配音工具前做时长、可读性、speaker voice 检查。

本脚本不翻译、不合成语音、不调用 TTS，也不上传视频；它只生成 `localization_pack.v1` JSON、Markdown review、可选 SRT 草稿和 `dubbing_tasks[]`。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 常用命令

先导出待翻译包：

```bash
python3 scripts/localization_pack.py \
  --transcript work/transcript_reviewed.json \
  --target-language en \
  --output work/localization_pack.json \
  --markdown work/localization_pack.md \
  --srt work/localization_en.todo.srt
```

人工/LLM 填好翻译后复检：

```bash
python3 scripts/localization_pack.py \
  --transcript work/transcript_reviewed.json \
  --target-language en \
  --translations work/localization_en_reviewed.json \
  --output work/localization_pack.json \
  --markdown work/localization_pack.md \
  --srt output/subtitles/day58.en.srt \
  --require-translations \
  --fail-on-readability \
  --strict
```

需要配音任务时：

```bash
python3 scripts/localization_pack.py \
  --transcript work/transcript_reviewed.json \
  --target-language en \
  --translations work/localization_en_reviewed.json \
  --voice-map work/voices.json \
  --dubbing \
  --require-translations \
  --require-voices \
  --output work/localization_pack.json \
  --markdown work/localization_pack.md \
  --strict
```

## 翻译 JSON

最简单是用 `loc_001` 这种段落 ID 做 key：

```json
{
  "translations": {
    "loc_001": "Do not rush the edit.",
    "loc_002": "Check subtitle readability before dubbing."
  }
}
```

也可以用数组：

```json
{
  "segments": [
    {
      "id": "loc_001",
      "target_text": "Do not rush the edit."
    }
  ]
}
```

## Voice Map

如果 transcript 或 render config 里有 `speaker` / `speaker_id`，可以给不同说话人分配配音音色：

```json
{
  "host": "en-US-JennyNeural",
  "guest": "en-US-GuyNeural",
  "default": "en-US-JennyNeural"
}
```

`--require-voices` 会要求每个有 speaker 的 segment 都显式命中 voice map；否则只使用 `default` 或 `--default-voice` 作为提示。

## 输出

| 字段 | 说明 |
|---|---|
| `segments[]` | 每条本地化 cue，包含 source / target、speaker、时长、CPS、估算 TTS speed、warnings |
| `dubbing_tasks[]` | 传给 TTS 或外部配音工具的本地任务清单，不含 provider 调用 |
| `summary.blocking` | 缺翻译、可读性超限、TTS 速度超限或缺 voice 时的阻塞数 |
| `next_actions[]` | 下一步要修的具体问题 |

## 发布门禁

海外分发或配音版建议把本地化包加进发布门禁：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require localization_pack \
  --strict
```

如果 `localization_pack.json` 里 `summary.blocking > 0`，即使它不是默认必需项，`pipeline_manifest.py` 也会阻止发布。

## Review 要点

- 先确认原始 transcript 已经走过 `transcript_review.py`，不要把 ASR 错词翻译到多语版本。
- `target_cps_high` 通常说明翻译太长，短视频字幕应先改短，不要靠 TTS 强行加速。
- `tts_speed_over_limit` 出现时，优先重写台词或加长该 segment，再送配音。
- 多人访谈先跑 `speaker_turns.py`，再用 voice map 给不同 speaker 指定音色。
