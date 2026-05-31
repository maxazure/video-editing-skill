# 34 - Chapter Markers 章节元数据交付

当视频需要发 YouTube / B 站 / 课程平台，或要把章节写进 MP4 metadata 时，用 `chapter_markers.py` 生成一组可交付的章节 sidecar。

它可以读取：

- `transcript.json`：按时间戳推断章节
- `clean_script.md`：用 `## ` 标题做章节名，并对齐 transcript 时间
- 显式章节 JSON：人工或 LLM 先决定 `{timestamp,title,description}` 后再格式化

输出固定为 4 个文件：

- `chapters.json`
- `chapters.md`
- `chapters.ffmetadata`
- `chapters-youtube.txt`

脚本只做本地格式化和保守推断，不调用 LLM，不改视频文件。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 常用命令

```bash
python3 scripts/chapter_markers.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output-dir output/chapters
```

如果已经有人工确认的章节 JSON：

```bash
python3 scripts/chapter_markers.py \
  --chapters work/chapters_draft.json \
  --duration 720 \
  --output-dir output/chapters \
  --basename day58
```

`work/chapters_draft.json` 可以是数组，也可以是 `{ "chapters": [...] }`：

```json
{
  "chapters": [
    {"timestamp": 0, "title": "Opening Hook", "description": "Why this matters."},
    {"timestamp": 96, "title": "Workflow Setup", "description": "Prepare the editing flow."}
  ]
}
```

## 输出用途

| 文件 | 用途 |
|---|---|
| `chapters.json` | 结构化 manifest，供 agent / 自动化继续读取 |
| `chapters.md` | 人工 review 表，适合贴到交付说明 |
| `chapters.ffmetadata` | FFmpeg 可写入 MP4/MKV chapter metadata |
| `chapters-youtube.txt` | 可直接贴进 YouTube/B 站简介的时间戳列表 |

## 写入视频 metadata

生成 `chapters.ffmetadata` 后，可用 FFmpeg 复制音视频流并写入章节：

```bash
ffmpeg -i output/master.mp4 \
  -i output/chapters/chapters.ffmetadata \
  -map_metadata 1 \
  -codec copy \
  output/master_with_chapters.mp4
```

这一步不会重编码音视频，但不同平台是否读取 MP4 chapter metadata 取决于平台；上传平台通常仍建议同时把 `chapters-youtube.txt` 贴进简介。

## 严格模式

```bash
python3 scripts/chapter_markers.py \
  --chapters work/chapters_draft.json \
  --duration 720 \
  --output-dir output/chapters \
  --strict
```

`--strict` 在出现 warning 时返回 2，例如首章不是 `0:00` 被自动对齐、章节间隔过短被跳过。自动化里可以用它提醒人工先审章节。
