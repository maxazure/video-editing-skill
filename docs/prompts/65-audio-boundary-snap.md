# Audio Boundary Snap 音频感知剪辑边界

把已经选中的精华片段按词级时间戳、完整句结尾和相邻静音区重新校正，避免 start/end 落在词中间、吞掉尾音或把半句话直接截断。

## 适用场景

- 已经运行 `highlight_picker.py`，准备批量渲染 1-10 条短视频。
- transcript 有 Whisper `segments[].words[]` 或 ElevenLabs Scribe 顶层 `words[]`。
- 视觉场景边界已经合理，但声音切点仍可能吞字、爆音或断在半句。
- 想把每次时间调整和 blocker 落成 JSON/Markdown，而不是只存在 agent 对话里。

## 基本用法

```bash
python3 scripts/audio_boundary_snap.py \
  --candidates work/highlight_candidates.json \
  --transcript work/long_transcript.json \
  --media origin/long-talk.mp4 \
  --output work/audio_boundary_plan.json \
  --markdown work/audio_boundary_plan.md \
  --strict
```

脚本按以下顺序处理：

1. 从 transcript 读取词级 start/end；Scribe 的 `spacing` 和 `audio_event` 不当作词。
2. start 如果落在词中间，回到该词开头；如果落在词间静音，吸附到下一词或相邻静音区。
3. end 如果还不是完整句，会在 `--sentence-window` 内寻找下一个句号、问号、感叹号或分号。
4. 默认在最后一个完整词后留 `--padding-ms 300`；有 transcript `silences[]` 或 `--media` 时，优先吸附到静音区中点。
5. 检查输入计划的最短/最长时长；安全边界超限时写 blocker，不会为了硬压时长重新切进词中间。

## 输出

- `audio_boundary_plan.json`：`audio_boundary_plan.v1`，顶层 `selected[]` 保留原 highlight 字段，并增加 `audio_boundary_snap`。
- `audio_boundary_plan.md`：逐条列出 original/snapped time、start/end delta、reason 和 blocker。
- `summary.blocking`：缺词级时间戳、候选时间非法、显式媒体缺失或安全边界超时长时非零。

每条 `audio_boundary_snap` 包含：

- `original_start` / `original_end`
- `snapped_start` / `snapped_end`
- `start_delta` / `end_delta`
- `start_reason` / `end_reason`
- `first_word` / `last_word`
- `sentence_extended`
- `warnings[]` / `blockers[]`

## 接到 Shorts Batch

输出保持顶层 `selected[]` 和平台参数，可直接作为现有批处理输入：

```bash
python3 scripts/shorts_batch.py \
  --highlights work/audio_boundary_plan.json \
  --video origin/long-talk.mp4 \
  --output work/shorts_batch.json \
  --markdown work/shorts_batch.md \
  --render-config-dir work/shorts_render_configs \
  --output-dir output/shorts \
  --qa-dir verify/shorts \
  --strict
```

每条 render config 会保留 `audio_boundary_snap`，便于渲染后追溯 cut point 的来源和时间调整。

## 只用 transcript 静音

如果 transcript 已由 `transcribe.py` 写入 `silences[]`，可以不传媒体：

```bash
python3 scripts/audio_boundary_snap.py \
  --candidates work/highlight_candidates.json \
  --transcript work/long_transcript.json \
  --output work/audio_boundary_plan.json \
  --markdown work/audio_boundary_plan.md
```

加 `--no-silence` 可关闭 transcript/FFmpeg 静音吸附，只按词和句末校正。`--silence-noise-db`、`--silence-min-duration`、`--start-window`、`--sentence-window`、`--silence-window` 可调整检测范围。

## Manifest gate

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage analysis \
  --require audio_boundary_plan \
  --strict
```

`audio_boundary_plan.json` 即使不是 required，只要 `summary.blocking > 0` 也会成为可见 blocker。先打开 Markdown 复核首尾词和 delta，再进入 batch/render；脚本本身不渲染、不上传、不调用 LLM 或付费 provider。
