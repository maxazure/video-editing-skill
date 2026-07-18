# Subtitle Readability QA — 最终字幕可读性门禁

用于发布前检查已经和 master / platform export 对齐的字幕时间线。它读取 `subtitle_pack.v1` JSON，输出 `subtitle_readability_qa.v1` JSON / Markdown，不改字幕、不渲染、不调用外部服务。

## 推荐流程

先按最终剪辑顺序、播放速度和片头 offset 生成字幕包：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename final_master \
  --speed 1.25 \
  --offset 2.0
```

再检查字幕和实际成片：

```bash
python3 scripts/subtitle_readability_qa.py \
  output/subtitles/final_master.json \
  --media output/final_master.mp4 \
  --output verify/subtitle_readability_qa.json \
  --markdown verify/subtitle_readability_qa.md \
  --strict
```

如果暂时只有字幕包，可以不传 `--media`；这样仍会检查 cue 本身，但不会检查字幕是否超过成片结尾。

## 默认检查

| 检查 | 默认 | 结果 |
|---|---:|---|
| Cue 时间缺失、非数字、倒序或非正时长 | 不允许 | BLOCK |
| Cue 重叠 | > 1ms | BLOCK |
| Cue 超过媒体结尾 | > 50ms tolerance | BLOCK |
| 闪现字幕 | < 0.15s | BLOCK |
| 极端阅读速度 | > 25 CPS | BLOCK |
| 较短字幕 | < 0.5s | WARN |
| 较高阅读速度 | > 18 CPS | WARN |
| 长时间不更新 | > 7s | WARN |
| 行数 | > 2 行 | WARN |
| 单行长度 | 中文 > 18 字；英文 > 42 字符 | WARN |

CPS 和排版阈值是复核提示，不是跨语言、跨平台的绝对定律。普通可读性风险只进入 WARN；确定性时间错误、极端闪现或极端 CPS 才进入 `summary.blocking`。

## 输出怎么看

- `metrics.cues[]`：每条 cue 的 duration、visible chars、CPS、行数和最长行。
- `findings[]`：精确 cue、成片时间范围、severity、证据和建议动作。
- `summary.status`：`ready`、`review` 或 `blocked`。
- `summary.blocking`：`--strict` 是否返回 2，也是 pipeline manifest 的门禁依据。
- `summary.overlaps` / `out_of_bounds`：最常见的时间线交付事故计数。

命中后先按 Markdown 时间码在最终成片上看一遍，再回到清稿、`render_config` 或字幕切分参数修复。重新生成字幕包并复跑，不要直接手改 JSON 来消除报告。

自定义阈值示例：

```bash
python3 scripts/subtitle_readability_qa.py output/subtitles/final_master.json \
  --media output/final_master.mp4 \
  --language zh \
  --optimal-cps 16 \
  --max-cps 24 \
  --max-chars 16 \
  --output verify/subtitle_readability_qa.json \
  --strict
```

发布流程要强制具备这份报告：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require subtitle_readability_qa \
  --strict
```

## 边界

- 它检查 timed text artifact，不做 OCR，因此不能证明烧录字幕的字体、颜色、描边或画面遮挡正确。
- 传入 `--media` 只使用 FFprobe 检查 duration 边界，不分析视觉安全区。
- 连续、无间隙的 cue 是合法的；脚本只阻塞真实时间重叠。
- WARN 必须结合正常播放速度人工判断，不要为了清零报告机械拆句。
