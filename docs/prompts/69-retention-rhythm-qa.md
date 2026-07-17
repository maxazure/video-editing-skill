# Retention Rhythm QA — 成片留存节奏风险审计

适合已经有 master / platform export，想在发布前检查以下问题：

- 前三秒完全没有 hard scene change 或字幕变化。
- 单个视觉镜头拖得过久。
- 画面和字幕同时长时间没有新 beat。
- 镜头长度过度等距，像固定计时器切片。
- 0.35 秒以下快切连续太多，字幕或画面来不及读。
- 字幕长时间不刷新，或存在较长无字幕空窗。

它只报告可观测的剪辑节奏风险，不预测真实播放留存率、CTR 或“爆款概率”，也不会自动改 timeline。

## 推荐流程

先生成与最终 master 对齐的 timed-text JSON。主片如果用了 `--primary-speed 1.25` 和 2 秒封面，字幕包必须使用相同参数：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename final_master \
  --speed 1.25 \
  --offset 2.0
```

然后直接分析成片：

```bash
python3 scripts/retention_rhythm_qa.py output/final_master.mp4 \
  --timed-text output/subtitles/final_master.json \
  --output verify/retention_rhythm_qa.json \
  --markdown verify/retention_rhythm_qa.md \
  --strict
```

脚本默认自己运行 FFmpeg scene detection。已有 `scene_boundaries.v1` 时可以复用，适合重复调整阈值或离线测试：

```bash
python3 scripts/retention_rhythm_qa.py output/final_master.mp4 \
  --scene-boundaries verify/final_scene_boundaries.json \
  --timed-text output/subtitles/final_master.json \
  --output verify/retention_rhythm_qa.json \
  --markdown verify/retention_rhythm_qa.md \
  --strict
```

## 默认门禁

| 检查 | 默认 | 处理 |
|---|---:|---|
| Hook window | 前 3 秒 | 无 timed text 时只 WARN；提供 timed text 后仍无 scene/subtitle event 才 BLOCK |
| Visual hold | > 6 秒 | WARN |
| Severe visual hold | > 10 秒 | BLOCK |
| Combined attention gap | > 6 秒 | WARN |
| Severe attention gap | > 10 秒 | BLOCK |
| Metronomic cadence | shot-duration CV ≤ 0.08 | WARN |
| Rapid cut | shot < 0.35 秒 | 比例 > 35% 或连续 > 4 个时 WARN |
| Subtitle hold | > 4.5 秒 | WARN |
| Subtitle uncovered gap | > 1.5 秒 | WARN |

这些默认值有意比一些“viral shorts”模板保守。持续运镜、产品 demo、情绪留白、脱口秀 reaction hold 都可能是合理的；WARN 必须结合 master 人工判断，不能为了清零报告机械加切点。

## 输出怎么看

`retention_rhythm_qa.v1` 主要字段：

- `metrics.scene_boundaries[]` / `metrics.shots[]`：实际 hard-cut 节奏。
- `metrics.hook`：前三秒 scene cut、subtitle change 和合并 attention event。
- `metrics.shot_duration`：mean / median / p90 / max / coefficient of variation。
- `metrics.rapid_cuts`：短镜头比例和最长连续快切。
- `metrics.attention.gaps[]`：scene cut 与字幕 refresh 合并后的空窗。
- `findings[]`：精确时间范围、严重度、证据和建议动作。
- `summary.blocking`：`--strict` 是否返回 2，也是 `pipeline_manifest.py` 的阻塞依据。

如果报告命中：

1. 先打开 Markdown，按时间范围查看**已渲染 master**。
2. 需要更多证据时运行 `timeline_view.py --at <seconds>`。
3. 判断是 intentional hold / continuous motion，还是确实缺少视觉或字幕 beat。
4. 回到源 `render_config` / enrich plan / cut list 调整。
5. 从源素材重新渲染，再复跑本报告；不要对已编码 master 做二次补丁。

发布流程要强制具备这份报告：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require retention_rhythm_qa \
  --strict
```

## 边界

- FFmpeg scene score 擅长 hard cut / 大幅视觉变化，不理解缓慢推拉、人物动作或细微 kinetic text。
- timed text 应优先使用 `subtitle_pack.v1`，因为源 transcript 在变速、重排或片头 offset 后可能不再对齐 master。
- 报告不能替代完整审片、受众测试或平台真实 analytics。
