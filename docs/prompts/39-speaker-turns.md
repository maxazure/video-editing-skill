# Speaker Turns 说话人回合

用途：播客、访谈、圆桌、双人口播进入剪辑前，先把 diarization 结果对齐到 transcript，确认“谁在什么时候说了什么”。适合把外部 pyannote / WhisperX / diarize / Gemini / Scribe 产出的 `start/end/speaker` 或 RTTM 文件接进本项目。

`speaker_turns.py` 不运行 diarization 模型、不上传音频、不消耗任何 provider credits；它只读本地 JSON/RTTM 和 transcript，输出 JSON + Markdown review packet，可选再输出给 `render_final.py --enrich-plan` 使用的 speaker badge cue。

## 常用命令

```bash
python3 scripts/speaker_turns.py \
  --transcript work/transcript.json \
  --diarization work/diarization.json \
  --speaker-map work/speakers.json \
  --output work/speaker_turns.json \
  --markdown work/speaker_turns.md \
  --enrich-plan work/speaker_badges.json \
  --min-speakers 2 \
  --strict
```

RTTM 输入：

```bash
python3 scripts/speaker_turns.py \
  --transcript work/transcript.json \
  --rttm work/audio.rttm \
  --output work/speaker_turns.json \
  --markdown work/speaker_turns.md
```

如果 transcript 本身已经有 `speaker` / `speaker_id`，可以不传 diarization：

```bash
python3 scripts/speaker_turns.py \
  --transcript work/scribe_transcript.json \
  --output work/speaker_turns.json \
  --markdown work/speaker_turns.md
```

## Speaker Map

`--speaker-map` 用来把模型标签换成人能看懂的名字：

```json
{
  "SPEAKER_00": { "name": "主持人", "role": "host", "color": "#2563eb" },
  "SPEAKER_01": { "name": "嘉宾", "role": "guest", "color": "#16a34a" }
}
```

## 输出怎么用

| 文件 | 用途 |
|---|---|
| `speaker_turns.json` | 机器可读，含 `summary`、`turns`、`crosstalk`、`diarization_segments` |
| `speaker_turns.md` | 人工 review：speaker 占比、每个 turn、混说/未标记 warning |
| `speaker_badges.json` | 可选 enrich plan，传给 `render_final.py --enrich-plan` 显示说话人 badge |

把 badge 接回渲染：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/speaker_badges.json \
  --output output/interview_master.mp4
```

需要把说话人回合变成发布门禁时：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/interview \
  --target-stage publish_ready \
  --require speaker_turns \
  --strict
```

## Review 要点

- `summary.detected_speakers` 是否符合预期人数。
- `summary.unlabeled_ratio` 是否过高；`--strict` 默认超过 20% 会 blocking。
- `crosstalk` 是否集中在可剪掉或需要保留的争论高光处。
- `mixed_speakers` warning 多时，优先检查 transcript segment 是否跨越 speaker change；有 word timestamps 时结果通常更细。
- `speaker_badges.json` 只是标签 cue，不改变字幕文本；字幕仍由 `render_final.py` / `subtitle_pack.py` 负责。
