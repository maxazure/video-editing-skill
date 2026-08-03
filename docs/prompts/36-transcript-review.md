# Transcript Review 转录校验回路

Whisper 生成的 `transcript.json` 进入字幕、分镜、粗剪、render_config 和文案之前，先跑一轮可编辑 review。这个步骤用来修正产品名、英文专有名词、中文同音字和尾部幻觉，避免错误字幕被烤进最终 MP4。

专业术语或同音错词必须借助全篇前后文判断时，先运行 [79-Semantic Transcript Review](79-semantic-transcript-review.md)，再把 `transcript_semantic_reviewed.json` 作为本页的 `--transcript` 输入。语义 proposal/choices 不替代这里的同步媒体听审。

## 什么时候用

- 口播里有 Claude / Codex / OpenClaw / 剪映 / 小红书这类容易被 ASR 听错的词。
- 要做 karaoke/逐词字幕，且 `transcript.json` 带 `words[]`。
- 想让人工只改文本，不直接改 JSON。
- 想边听/边看源媒体边校稿，而不是在文本编辑器里猜时间点。
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

## 2. 可选：生成本地同步视频校稿页

如果需要对着口型、画面或音频逐段确认，直接从同一份 transcript 生成自包含 HTML：

```bash
python3 scripts/transcript_review.py html \
  --transcript work/transcript.json \
  --video origin/talking.mp4 \
  --corrections work/corrections.json \
  --output work/transcript_review.html \
  --max-cps 20

open work/transcript_review.html       # macOS
# xdg-open work/transcript_review.html # Linux
# start work/transcript_review.html    # Windows
```

页面能力：

- 点击时间码跳到 segment 起点，播放时自动高亮当前段。
- 行内编辑；刷新前的草稿保存在当前浏览器 `localStorage`。
- 全文查找/替换、复制 review、保存/下载 `transcript_review.txt`。
- 按 segment 字符数和时长即时显示 CPS；超过 `--max-cps` 标黄，提示先缩短文案。
- 媒体和 transcript 都只在本机打开，不上传、不调用 LLM；页面没有 CDN 或 npm 依赖。

如果原媒体路径失效，可在页面里点“选择本地视频”重新绑定。浏览器不支持 File System Access API 时，“保存”会下载文件；将下载的 `transcript_review.txt` 放到 `work/` 后继续下一步。页面不会直接覆盖 JSON，避免一次误操作破坏原始 ASR。

## 3. 应用人工修正

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

## 4. 词级时间戳

默认会按原 segment 的时间范围重新分配 `words[]`，让 karaoke 字幕继续有可用词级时间戳。它不是声学重新对齐，只适合人工修正文字、产品名和轻微措辞；如果整段重写太多，应重新转写或重新切段。

要保留原 `words[]` 不动：

```bash
python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json \
  --keep-words
```

## 5. 推荐日常顺序

```bash
python3 scripts/transcribe.py origin/voice.wav \
  --language zh \
  --word-timestamps \
  --detect-fillers

python3 scripts/transcript_review.py html \
  --transcript work/transcript.json \
  --video origin/talking.mp4 \
  --output work/transcript_review.html \
  --corrections work/corrections.json

# 浏览器打开 work/transcript_review.html，对着媒体校稿并保存 work/transcript_review.txt

python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json
```

之后所有需要 transcript 的脚本优先使用 `work/transcript_reviewed.json`。

纯终端/SSH 环境继续使用第 1 节的 `export` 即可；`html` 是增强入口，不替代文本 round-trip。
