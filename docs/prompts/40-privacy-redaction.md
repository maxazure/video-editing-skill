# 40 — Privacy Redaction 视觉隐私遮挡

`scripts/privacy_redact.py` 用手工框或外部检测 JSON 生成隐私遮挡计划，适合发布前处理人脸、车牌、微信号、手机号、邮箱、客户资料、屏幕录制里的个人信息等敏感区域。

本脚本不内置 YOLO、EgoBlur、deface 或任何云端检测模型；它只负责读取已经产生或人工确认的框，输出 `privacy_redaction_plan.v1` JSON、Markdown review，以及可选 FFmpeg blur/pixelate/mask 命令。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 常用命令

只生成 review，不渲染：

```bash
python3 scripts/privacy_redact.py \
  --video output/day58_master.mp4 \
  --detections work/privacy_detections.json \
  --output work/privacy_redaction.json \
  --markdown work/privacy_redaction.md \
  --method pixelate \
  --scale 1.20 \
  --require-reviewed \
  --strict
```

只有手工框时：

```bash
python3 scripts/privacy_redact.py \
  --video output/day58_master.mp4 \
  --box "3.2:7.8:120,220,360,90:wechat_id:true" \
  --output work/privacy_redaction.json \
  --markdown work/privacy_redaction.md
```

确认 Markdown 后再渲染：

```bash
python3 scripts/privacy_redact.py \
  --video output/day58_master.mp4 \
  --detections work/privacy_detections_reviewed.json \
  --output work/privacy_redaction.json \
  --markdown work/privacy_redaction.md \
  --method blur \
  --render-output output/day58_master_redacted.mp4
```

## 输入 JSON

支持 `detections[]`、`events[]`、`redactions[]`、`privacy_redactions[]`，以及 frame-level `frames[].detections[]` / `frames[].boxes[]`：

```json
{
  "detections": [
    {
      "start": 3.2,
      "end": 7.8,
      "bbox": [0.12, 0.18, 0.36, 0.28],
      "unit": "normalized",
      "label": "face",
      "score": 0.94,
      "reviewed": true
    },
    {
      "start": 12.0,
      "duration": 2.5,
      "x": 420,
      "y": 810,
      "w": 260,
      "h": 72,
      "label": "license_plate",
      "reviewed": true
    }
  ]
}
```

坐标规则：

| 格式 | 字段 |
|---|---|
| XYXY | `bbox` / `box` / `xyxy` 为 `[x1, y1, x2, y2]` |
| XYWH | `x/y/w/h`，或 `bbox_format: "xywh"` |
| 归一化 | `unit: "normalized"` 或四个值都在 0-1 范围 |
| frame-level | `time` + `boxes[]`，没有 end 时用 `--frame-hold` |

## 参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--method` | `blur` | `blur` / `pixelate` / `solid` |
| `--scale` | `1.15` | 扩大框，避免边缘露出 |
| `--min-score` | `0.0` | 丢弃低置信度检测 |
| `--label` | 空 | 只保留指定 label，可重复 |
| `--exclude-label` | 空 | 排除指定 label，可重复 |
| `--require-reviewed` | 关 | 未 `reviewed: true` 的框会 blocking |
| `--require-redactions` | 关 | 没有任何遮挡事件时 blocking |
| `--render-output` | 空 | 传入后才会渲染 redacted MP4 |
| `--dry-run` | 关 | 写出计划和命令但不执行 FFmpeg |

## 发布门禁

敏感项目建议把视觉隐私 review 加进 publish gate：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require privacy_redaction \
  --strict
```

如果 `privacy_redaction.json` 里 `summary.blocking > 0`，即使它不是默认必需项，`pipeline_manifest.py` 也会阻止发布。

## Review 要点

- 检查 Markdown 里每个框的时间范围、label、坐标是否覆盖完整敏感区域。
- 人脸、车牌、证件、微信号、手机号建议用 `pixelate` 或强 `blur`，不要只用轻度模糊。
- 对自动检测框先人工确认并写入 `reviewed: true`，再用 `--require-reviewed --strict` 进入自动化。
- 屏幕录制里固定位置的敏感信息，通常手工 `--box` 比视觉模型更可靠。
