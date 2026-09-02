# Caption / Speech QA — 字幕与独立人声活动对齐

用于已经生成 `subtitle_pack.v1` JSON，且能提供最终独立对白/旁白轨的项目。它补的是“字幕时间结构合法，但显示时其实没有对应人声”的缺口：整条孤立字幕、严重 offset、字幕过早出现、说完后停留太久、字幕跨过长静音，都会被定位到 cue。

## 输入边界

- `--subtitle-pack` 必须是 `subtitle_pack.py` 输出的 JSON，不直接读取 SRT/VTT/ASS。
- `speech_source` 必须是独立 dialogue/narration bus，或明确没有 BGM/SFX 的 speech-dominant 音轨；可以是音频文件，也可以是带目标人声音轨的视频。
- 不要把已混入 BGM、环境声或音效的最终 master 当成人声轨。脚本只识别振幅活动，不识别人声；非人声声音会制造假通过。
- 这是本地、只读、provider-free 的启发式门禁，不做 forced alignment、ASR、字/音素级同步或自动修复。

## 使用

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename final \
  --formats srt vtt ass json

python3 scripts/caption_speech_qa.py analyze work/final_narration.wav \
  --subtitle-pack output/subtitles/final.json \
  --project-dir . \
  --output verify/caption_speech_qa.json \
  --markdown verify/caption_speech_qa.md \
  --strict

python3 scripts/caption_speech_qa.py verify \
  --report verify/caption_speech_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require caption_speech_qa \
  --strict
```

默认使用 FFmpeg `silencedetect=noise=-36dB:d=0.16`。每条 cue 会记录：

- `active_seconds` / `active_ratio`：显示区间内测到的音频活动量；零活动或低于 25% 默认阻断，25%–50% 警告。
- `leading_silence_seconds` / `trailing_silence_seconds`：字幕出现在人声前、或在人声结束后继续停留的时间；默认超过 0.60 秒阻断。
- `longest_internal_silence_seconds`：字幕中间最长静音；默认超过 0.80 秒警告，提示拆 cue 或重新对齐。
- cue 是否超出独立人声轨时间线；允许 0.05 秒容差，超过即阻断。

如果底噪或录音门限不同，可以调整 `--noise-db` / `--min-silence-seconds`，但阈值变化会进入报告 hash，旧报告会失效。不要通过放宽阈值掩盖真实错位；先听人声轨并看 Markdown 定位到的 cue。

## 修复顺序

1. 在最终成片和独立人声轨上正常速度查看/听取被标记 cue。
2. 如果整体统一提前或延迟，修正 `subtitle_pack.py --offset` 后重建字幕包。
3. 如果是变速或 concat 后逐渐漂移，回到 `render_config`、word timestamps 或已确认的对齐来源修复，不要逐条手搓随机偏移。
4. 如果只有一条 cue 跨过自然长停顿，按完整语义边界拆 cue。
5. 重新生成 JSON/SRT/VTT/ASS，重跑 `analyze` 和 `verify`，再在最终混音上完整 1× 审看字幕。

报告把 speech source / subtitle pack 的 SHA-256、大小、音频媒体契约、算法、settings、silence/active intervals、逐 cue 指标、checks、summary 和 `report_id` 绑定在一起。任一输入、参数、测量或派生状态变化都会使 live verify fail closed；报告不是数字签名，也不证明字级、音素级或语义正确。
