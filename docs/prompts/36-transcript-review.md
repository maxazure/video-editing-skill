# Transcript Review 转录校验回路

Whisper 生成的 `transcript.json` 进入字幕、分镜、粗剪、render_config 和文案之前，先跑一轮可编辑 review。这个步骤用来修正产品名、英文专有名词、中文同音字和尾部幻觉，避免错误字幕被烤进最终 MP4。

## 什么时候用

- 口播里有 Claude / Codex / OpenClaw / 剪映 / 小红书这类容易被 ASR 听错的词。
- 要做 karaoke/逐词字幕，且 `transcript.json` 带 `words[]`。
- 想让人工只改文本，不直接改 JSON。
- 生成前需要把“用户已确认 transcript”作为一个可审计 artifact。

## 1. 导出 review 文件

```bash
python3 scripts/transcript_review.py export \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --corrections work/corrections.json
```

`--corrections` 可传 JSON：

```json
{
  "cloud": "Claude",
  "Excalibro": "Excalidraw",
  "小红树": "小红书"
}
```

也可传文本：

```text
cloud => Claude
Excalibro => Excalidraw
小红树 => 小红书
```

review 文件格式：

```text
# Transcript Review
# Edit only the text after the prefix. Keep [seg:<id> start:<time> end:<time>] unchanged.

[seg:1 start:00:00.000 end:00:02.000] 今天聊 Claude
[seg:2 start:00:02.200 end:00:04.000] 然后打开 Excalidraw
```

只改前缀后面的文字。前缀保留 segment id 和时间码，供 apply 阶段安全匹配。

## 2. 应用人工修正

```bash
python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json
```

默认不覆盖原始 transcript，而是写到 `--output`。确认后，后续脚本用 `work/transcript_reviewed.json`：

```bash
python3 scripts/rewrite_script.py \
  --transcript work/transcript_reviewed.json \
  --emit-prompt > work/prompt.md
```

如果你明确要覆盖原文件，用 `--in-place`。

## 3. 词级时间戳

默认会按原 segment 的时间范围重新分配 `words[]`，让 karaoke 字幕继续有可用词级时间戳。它不是声学重新对齐，只适合人工修正文字、产品名和轻微措辞；如果整段重写太多，应重新转写或重新切段。

要保留原 `words[]` 不动：

```bash
python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json \
  --keep-words
```

## 4. 推荐日常顺序

```bash
python3 scripts/transcribe.py origin/voice.wav \
  --language zh \
  --word-timestamps \
  --detect-fillers

python3 scripts/transcript_review.py export \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --corrections work/corrections.json

# 人工打开 work/transcript_review.txt，只改文字，保存

python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json
```

之后所有需要 transcript 的脚本优先使用 `work/transcript_reviewed.json`。
