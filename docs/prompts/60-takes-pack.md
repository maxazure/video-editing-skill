# Takes Pack 多 take 阅读视图

用于多个口播 take、采访机位、课程片段或补录素材已经转写完成，但还没有决定怎么选段时。先把多个 `transcript.json` 压成一份 phrase-level Markdown，让 agent/剪辑师按短语级时间码比较表达质量，再进入 `highlight_picker.py`、`srt_edit_plan.py`、`render_config.json` 或 NLE handoff。

## 什么时候用

- 同一段内容录了 `take1/take2/take3`，需要快速挑最自然的一版。
- 访谈、播客或课程有多份 transcript，需要先扫可引用的短语级片段。
- 原始 transcript 带 `words[]`，JSON 太大，不适合直接塞进 LLM 上下文。
- 要把“候选片段 + reason”交给人工确认，但还不想渲染任何视频。

## 常用方式

```bash
python3 scripts/takes_pack.py \
  --transcript take1=work/take1_transcript.json \
  --transcript take2=work/take2_transcript.json \
  --output work/takes_packed.md \
  --json work/takes_pack.json \
  --break-gap 0.5
```

也可以扫描目录：

```bash
python3 scripts/takes_pack.py \
  --transcripts-dir work/transcripts \
  --output work/takes_packed.md \
  --json work/takes_pack.json
```

## 输出

`takes_packed.md` 按 source 分组，每行包含：

- phrase id，例如 `take1-003`
- 源时间码，例如 `00:12.40-00:16.80`
- speaker（如果 transcript 有）
- 来源 segment ids
- 压缩后的短语文本

`takes_pack.json` 使用 `takes_pack.v1`，包含 `sources[]`、`phrases[]` 和 `summary`。`pipeline_manifest.py` 会识别它，但它默认不阻塞发布；需要强制多 take review 时再加：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage render_ready \
  --require takes_pack \
  --strict
```

## 推荐工作流

1. 对每个素材先跑 `transcribe.py --word-timestamps`，或导入剪映/CapCut 已校对字幕。
2. 跑 `takes_pack.py` 生成 `takes_packed.md`。
3. 让 agent 用 phrase id/time range 挑选最佳表达，输出候选清单。
4. 对长视频可继续跑 `highlight_picker.py --brief`；对已确定保留/删除的字幕，可跑 `srt_edit_plan.py`。
5. 最后把确认后的时间段写入 `render_config.json`，或导出 EDL/FCPXML/OTIO 给剪辑软件。

## 注意

- `takes_pack.py` 不转写、不渲染、不调用 LLM，也不提交任何生成任务。
- 默认 `--break-gap 0.5` 会在 0.5 秒以上静音处拆 phrase；speaker 变化也会拆 phrase。
- 如果只传一份 transcript，脚本仍会输出阅读视图，但 Markdown 会提示 multi-take 比较能力有限。
