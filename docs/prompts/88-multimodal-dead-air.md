# 88 — Multimodal Dead-Air 静音 + 静帧保守剪辑

用于口播、访谈、开箱、教程或固定机位录屏。它只把“音频静默”与“画面冻结/低变化”同时成立的区间列为候选，避免纯音频 jump cut 把仍有表情、手部动作或屏幕操作的停顿误删。

## 什么时候用

- 停顿很多，但停顿期间可能仍有重要画面。
- 固定机位口播、开箱或教程，需要比纯静音检测更保守。
- 希望先审查带来源哈希的 cut plan，再生成新的工作副本。

普通紧凑口播、可以接受仅按音频去停顿时，继续用 [21-jump-cut.md](21-jump-cut.md)。多模态模式会多跑一次完整视频 `freezedetect`，速度更慢，但误删风险更低。

## 推荐流程

### 1. 生成 source-bound 计划

```bash
python3 scripts/multimodal_dead_air.py plan origin/talking.mp4 \
  --delivery work/talking-dead-air-tight.mp4 \
  --output work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md \
  --strict
```

默认规则：

- 静音：自适应 `loudnorm input_thresh`，至少 1 秒；
- 静帧：FFmpeg `freezedetect=noise=0.02:d=1.0`；
- 静帧必须覆盖一段静音的至少 60%，该静音才进入候选；
- 实际只删除静音与静帧的交集，并在两侧各保留 80ms；
- 总删除量超过源时长 20% 时阻塞。

### 2. 验证并复核所有切点

```bash
python3 scripts/multimodal_dead_air.py verify \
  work/multimodal_dead_air_plan.json --strict

DEAD_AIR_CUT_COUNT="$(python3 -c 'import json; print(len(json.load(open("work/multimodal_dead_air_plan.json"))["removed_segments"]))')"
python3 scripts/timeline_view.py origin/talking.mp4 \
  --cut-list work/multimodal_dead_air_plan.json \
  --output-dir verify/dead-air-cuts \
  --limit "$DEAD_AIR_CUT_COUNT"
```

Markdown 会列出每段 silence、静帧覆盖率和真实 shared interval。`timeline_view.py` 的通用默认上限是 20，上面的命令会按本计划的实际删除段数覆盖该上限，避免漏看后续切点。逐项确认没有重要表情、手势、产品展示、屏幕操作、反应镜头或叙事留白；若 `DEAD_AIR_CUT_COUNT=0`，停止并继续使用原片，不要执行 `apply`。计划的 `plan_id` 只是完整性摘要，不是签名或人工审批。

### 3. 单次编码生成工作副本

```bash
python3 scripts/multimodal_dead_air.py apply \
  work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md

python3 scripts/multimodal_dead_air.py verify \
  work/multimodal_dead_air_plan.json --strict
```

`apply` 复用 jump-cut 的 `trim/atrim + concat` 单次编码器，给保留片段边界加 30ms 音频 fade。它先写交付目录中的临时 MP4；输出必须是 H.264/AAC、`yuv420p`，且尺寸、帧率、采样率、声道数和预计时长符合源片/计划契约，再通过 FFmpeg 全长解码后才原子提升，并把输出 SHA-256 与完整媒体记录写回计划。默认不覆盖已有文件、symlink、源片或计划。

### 4. 成片复核和门禁

```bash
python3 scripts/render_qa.py work/talking-dead-air-tight.mp4 \
  --json verify/dead-air-render-qa.json \
  --review-dir verify/dead-air-render-qa \
  --review-clips

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require multimodal_dead_air_plan \
  --strict
```

如果下游平台已确定，给 `render_qa.py` 追加 `--platform xhs`、`--platform douyin` 或 `--platform wxch`；不要替用户猜平台。`multimodal_dead_air.py verify --strict` 是该计划的独立 live gate；这里的 `pipeline_manifest.py` 是完整项目发布门禁，默认还会要求 transcript、clean script、render config、master、QA 和 caption 等 publish-ready 产物，不能拿它代替单功能验证。

必须 1× 带声音完整看完新工作副本，重点听切点爆音、吞字、呼吸突变和房间底噪跳变；确认后再把工作副本接入后续 transcript/render pipeline。旧字幕、时间码、QA 和 approval receipt 不能直接沿用。

## 调参

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--noise-db auto` | auto | 自适应静音阈值，也可显式传 `-35` |
| `--min-silence` | 1.0s | 最短静音 |
| `--freeze-noise` | 0.02 | FFmpeg freezedetect 像素变化阈值 |
| `--min-freeze` | 1.0s | 最短静帧 |
| `--min-static-overlap` | 0.60 | 静帧覆盖静音的最低比例 |
| `--pad` | 0.08s | 每个交集切点两侧保留量 |
| `--fade-duration` | 0.03s | 保留片段音频边缘 fade |
| `--max-removal-ratio` | 0.20 | 无显式批准时的删除预算 |
| `--allow-over-budget` | 关 | 明确记录超 20% 删除批准；仍须逐段人工复核 |

## 边界

- `freezedetect` 衡量像素变化，不理解内容；固定镜头中的轻微表情可能被视为静止。
- 它不是 blooper、语义、表演质量或留白判断器。
- 摄像机噪点、屏幕动画、自动曝光会让真正死区无法达到静帧门槛；应先调阈值并复核，不要改成“静音或静帧”。
- 多模态 AND gate 降低误删风险，但不能替代人工看源切点和完整成片。
