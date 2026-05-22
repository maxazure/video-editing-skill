# 26. ASR Rough Cut — 口头禅/重复句粗剪

当素材不是“停顿太多”，而是“嗯、呃、那个”或口误重说太多时，用 `rough_cut.py` 先按 transcript 做一版可审计粗剪。它和 `jump_cut.py` 分工不同：

- `jump_cut.py` 看音频能量，适合去静音停顿；
- `rough_cut.py` 看 ASR 文本和时间戳，适合去纯口头禅片段和相邻重复句。

## 常用命令

先只生成计划，不渲染：

```bash
python3 scripts/rough_cut.py \
  --transcript work/transcript.json \
  --cut-list work/rough_cut.json
```

生成计划并渲染：

```bash
python3 scripts/rough_cut.py \
  --transcript work/transcript.json \
  --input origin/talking.mp4 \
  --output output/talking.roughcut.mp4 \
  --cut-list work/rough_cut.json
```

人工复核切点：

```bash
python3 scripts/timeline_view.py \
  output/talking.roughcut.mp4 \
  --cut-list work/rough_cut.json \
  --output-dir output/verify/rough_cut
```

## 输入要求

建议先转写时打开 filler 检测：

```bash
python3 scripts/transcribe.py origin/talking.wav \
  --language zh \
  --word-timestamps \
  --detect-fillers
```

`rough_cut.py` 会读取：

- `segments[].start/end/text`
- `filler_words[].is_filler_only`
- transcript 顶层 `language` / `duration`（可选）

即使没有 `filler_words`，脚本也会用内置中英文 filler 词表做保守检测。

## 输出字段

`work/rough_cut.json` 里最重要的是：

| 字段 | 说明 |
|---|---|
| `decisions` | 每个被移除 transcript 片段的原因：`filler_only` / `repeated_before_retry` / `repeated_duplicate` |
| `removed_segments` | 合并后的实际移除时间段 |
| `keep_segments` | 可直接用于 ffmpeg concat 的保留时间段 |
| `removed_seconds` | 预计剪掉的秒数 |
| `output_duration_estimate` | 预计输出时长 |
| `speedup_ratio` | 粗剪后的节奏压缩比例 |

## 调参

```bash
# 更保守：只去纯口头禅，不碰重复句
python3 scripts/rough_cut.py --transcript work/transcript.json --no-repeat-detect --cut-list work/rough_cut.json

# 更严格：只有高度相似才判定重复
python3 scripts/rough_cut.py --transcript work/transcript.json --repeat-threshold 0.94 --cut-list work/rough_cut.json

# 只生成 ffmpeg 命令和计划，不实际渲染
python3 scripts/rough_cut.py --transcript work/transcript.json --input origin/talking.mp4 --output output/rough.mp4 --cut-list work/rough_cut.json --dry-run
```

## 建议工作流

1. `transcribe.py --detect-fillers --word-timestamps`
2. `rough_cut.py --cut-list work/rough_cut.json`
3. 打开 `work/rough_cut.json` 看 `decisions`
4. 对激进切点跑 `timeline_view.py` 复核
5. 满意后再 `rough_cut.py --input ... --output ...`
6. 后续继续 `auto_enrich.py` / `render_final.py` / `render_qa.py`
