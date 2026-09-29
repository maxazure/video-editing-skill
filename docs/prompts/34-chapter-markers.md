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

人工复核 `chapters.json` 后，用可现场验证的封装脚本复制音视频流并写入章节：

```bash
python3 scripts/chapter_mux.py mux output/master.mp4 output/chapters/chapters.json \
  --output output/master_with_chapters.mp4 --receipt verify/chapter_mux.json
python3 scripts/chapter_mux.py verify verify/chapter_mux.json
```

`chapter_mux.py` 要求首章从 0 秒开始、章节连续且末章结束时间与源 MP4 相差不超过 0.1 秒；若 transcript 推算的时长不等于最终成片，重新运行 `chapter_markers.py --duration <成片秒数>` 并复核。它不重编码音视频，会核对输出章节、音视频流哈希、完整解码及输入/输出文件字节。输入限未带章节的单视频、最多单音频 MP4；已有软字幕等额外轨的文件要另行处理。不同平台是否读取 MP4 chapter metadata 取决于平台，仍建议把 `chapters-youtube.txt` 贴进长视频简介，并在目标播放器检查跳转。

## 严格模式

```bash
python3 scripts/chapter_markers.py \
  --chapters work/chapters_draft.json \
  --duration 720 \
  --output-dir output/chapters \
  --strict
```

`--strict` 在出现 warning 时返回 2，例如首章不是 `0:00` 被自动对齐、章节间隔过短被跳过。自动化里可以用它提醒人工先审章节。
