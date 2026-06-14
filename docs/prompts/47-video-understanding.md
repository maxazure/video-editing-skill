# 47 — Video Understanding 抽样帧 + 可选 YOLO

`scripts/video_understanding.py` 为口播、访谈、产品演示和户外素材生成结构化视觉理解 artifact。它默认只抽样帧，不强制安装机器视觉依赖；需要识别人、手机、电脑、屏幕、车辆、杯子等对象时，再用 `--detector yolo` 启用 Ultralytics YOLO。

## 适用场景

| 场景 | 处理 |
|---|---|
| 横屏口播转竖屏 | 先检测人物/主体，再交给 `smart_reframe.py --detections` |
| 软件录屏或产品演示 | 给 screen/device/product 类对象打标签，辅助 B-roll 和隐私检查 |
| 户外 vlog/采访 | 标出人、车、街景对象，辅助选片和风险复核 |
| 隐私发布前检查 | 把检测框交给 `privacy_redact.py --detections`，再人工确认遮挡 |

## 用法

不安装 detector，只生成抽样帧和 review shell：

```bash
python3 scripts/video_understanding.py origin/talk.mp4 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md
```

启用 YOLO：

```bash
pip install ultralytics

python3 scripts/scene_boundaries.py origin/talk.mp4 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md

python3 scripts/video_understanding.py origin/talk.mp4 \
  --scene-boundaries work/scene_boundaries.json \
  --frames-dir work/video_frames \
  --detector yolo \
  --model yolo11n.pt \
  --sample-interval 2 \
  --max-frames 32 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md \
  --strict
```

复用已有检测结果：

```bash
python3 scripts/video_understanding.py origin/talk.mp4 \
  --external-detections work/yolo_export.json \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md
```

## 输出

`video_understanding.json` 输出 `video_understanding.v1`：

| 字段 | 说明 |
|---|---|
| `frames[]` | 抽样帧 id、时间戳、场景 id、图片路径和该帧检测结果 |
| `detections[]` | label、class_id、bbox、confidence、source、track_id |
| `tracks[]` | 轻量轨迹：出现时间、帧数、平均置信度、最大画面占比、运动距离 |
| `scene_tags[]` | `person`、`device`、`vehicle`、`dominant_subject`、`moving_subject` 等 |
| `warnings[]` | 未启用 detector、未检测到对象、低置信度检测等 |

## 下游串接

主体感知重构图：

```bash
python3 scripts/smart_reframe.py origin/talk.mp4 \
  --detections work/video_understanding.json \
  --scene-boundaries work/scene_boundaries.json \
  --platform douyin \
  --output work/reframe_douyin.json \
  --markdown work/reframe_douyin.md \
  --strict
```

隐私遮挡计划：

```bash
python3 scripts/privacy_redact.py \
  --video origin/talk.mp4 \
  --detections work/video_understanding.json \
  --output work/privacy_redaction.json \
  --markdown work/privacy_redaction.md
```

## 设计取舍

- YOLO 是可选依赖，避免让普通口播流程被模型下载和 GPU/Metal/CUDA 兼容问题阻塞。
- 默认是抽样帧检测，不逐帧跑模型，速度更适合日常短视频生产。
- 内置 `tracks[]` 是轻量 bbox 关联，用于编辑决策，不等同于体育/车流等场景需要的严格多目标跟踪。
- 如果素材需要逐帧稳定 ID，可以先用 Ultralytics `model.track(..., tracker="bytetrack.yaml")`、BoT-SORT 或 Norfair 生成结果，再转换成 `detections[]` / `tracks[]` 交给本项目。

## Prompt

```text
请先运行 video_understanding.py 生成视频素材理解 artifact。若我没有要求安装 YOLO，则先用默认模式抽样帧；若我明确需要物体检测，则安装/使用 ultralytics 并传 --detector yolo。输出 video_understanding.json 和 video_understanding.md 后，结合 transcript、scene_boundaries 和 review Markdown 判断主体、屏幕、产品、车辆、隐私风险和 B-roll 标签；需要竖屏导出时，把 video_understanding.json 传给 smart_reframe.py --detections。
```
