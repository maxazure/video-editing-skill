# 50 CapCut Subtitle Import 剪映字幕反向导入

> 把剪映/CapCut 自动字幕或导出的 SRT 变回本项目可用的 `transcript.json`，必要时顺手生成基于字幕间隙的 cut list。

## 什么时候用

- 已经在剪映里用 Auto Captions 生成字幕，并人工改过错词。
- 客户或剪辑师只给你一个剪映草稿，希望后续继续走本项目的清稿、分镜、字幕包、QA 或多平台导出。
- 想用字幕轨作为人声区间代理，先输出一份保守 gap cut list，再人工复核切点。

## 从剪映草稿导入

```bash
python3 scripts/import_capcut_subtitles.py \
  --draft ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/day58 \
  --transcript work/capcut_transcript.json \
  --cut-list work/capcut_gap_cut.json \
  --markdown work/capcut_subtitles.md \
  --gap-threshold 1.0 \
  --source-media origin/talking.mp4
```

`--draft` 可以指向草稿文件夹，也可以直接指向 `draft_content.json`。脚本默认只读取 subtitle 材料，避免把封面标题、贴纸文字、片尾卡误导入为口播字幕。

如果某个草稿把自动字幕保存成普通文字轨：

```bash
python3 scripts/import_capcut_subtitles.py \
  --draft work/jianying_draft/draft_content.json \
  --include-overlays \
  --transcript work/capcut_transcript.json
```

## 从 SRT 导入

```bash
python3 scripts/import_capcut_subtitles.py \
  --srt exports/capcut_auto_captions.srt \
  --transcript work/capcut_transcript.json \
  --srt-output output/subtitles/capcut_clean.srt \
  --markdown work/capcut_subtitles.md
```

导入后的 `work/capcut_transcript.json` 可继续交给：

```bash
python3 scripts/rewrite_script.py --transcript work/capcut_transcript.json --emit-prompt
python3 scripts/rough_cut.py --transcript work/capcut_transcript.json --cut-list work/rough_cut.json
python3 scripts/subtitle_pack.py --transcript work/capcut_transcript.json --output-dir output/subtitles
```

## Gap Cut 复核

`--cut-list` 会把字幕段视为“人声存在”的代理，按字幕间隔生成 `keep_segments`：

- `--gap-threshold 1.0`：间隔小于等于 1 秒的字幕段会合并为同一个保留区间。
- `--pad 0.08`：每个字幕段前后保留少量上下文，避免咬字。
- `--min-keep 0.15`：丢弃太短的保留区间。

生成后先看复核图：

```bash
python3 scripts/timeline_view.py \
  origin/talking.mp4 \
  --cut-list work/capcut_gap_cut.json \
  --output-dir output/verify/capcut_gap_cut
```

确认没误切后，再考虑用 `export_edl.py` / `export_fcpxml.py` 交给 NLE，或把字幕 transcript 交给本项目其它工具继续处理。CapCut 字幕间隙不是逐帧剪辑决策，最终成片前必须复核。
