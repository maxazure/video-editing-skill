# 23 — Versioned Output：成片不覆盖旧版本

视频剪辑任务经常要多次微调字幕、BGM、B-roll 和节奏。`render_final.py --versioned-output`
会把请求的输出路径当作一个版本族，自动写到下一个 `<name>_V<N>.mp4`，避免 `ffmpeg -y`
覆盖上一版成片。

## 常用命令

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output output/day58_master.mp4 \
  --versioned-output
```

如果 `output/day58_master_V1.mp4` 已存在，本次会写入 `output/day58_master_V2.mp4`。

也可以写进配置：

```json
{
  "versioned_output": true,
  "clips": [
    {"video": "origin/talking.mp4", "segment_id": 1, "transcript": "work/transcript.json"}
  ]
}
```

## 与多平台导出一起用

`--formats` 会跟随实际版本文件。例如主输出变成 `day58_master_V3.mp4` 后：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --output output/day58_master.mp4 \
  --versioned-output \
  --formats vertical horizontal
```

会产出：

- `output/day58_master_V3.mp4`
- `output/day58_master_V3_vertical.mp4`
- `output/day58_master_V3_horizontal.mp4`

## 适用场景

- 客户/团队评审要保留每一版；
- 自动化每日跑视频，不希望覆盖昨天的成片；
- 同一条视频反复调字幕、章节卡、B-roll，需要回看对比；
- `--primary-speed`、`--enrich-plan`、`--formats` 同时使用时，需要稳定命名。
