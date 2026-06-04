# 38 — Smart Reframe 主体感知裁切

`scripts/smart_reframe.py` 用外部检测 JSON 生成可审计的裁切计划，让横屏素材导出 9:16 / 3:4 时不要只做中心裁切。

本脚本不内置 YOLO、MediaPipe 或云端视觉模型。检测可以来自任何工具，只要提供带时间戳和 `bbox` 的 JSON；本项目负责把检测结果按场景合并成 `track` / `center` / `letterbox` 计划，并交给 `multi_export.py --reframe-plan` 应用。

## 适用场景

| 场景 | 处理 |
|---|---|
| 横屏口播转竖屏 | 跟随人脸/人物中心裁切 |
| 双人访谈/多人 panel | 人群过宽时自动 letterbox，避免裁掉人 |
| 没有检测结果的片段 | 回退中心裁切，并在 review 里标 warning |
| 已有 scene_boundaries | 按视觉场景分别决定 crop，减少镜头内跳动 |

## 输入格式

检测 JSON 支持多种宽松结构：

```json
{
  "frames": [
    {
      "time": 1.2,
      "objects": [
        {"label": "face", "bbox": [1450, 220, 1620, 430], "confidence": 0.9},
        {"label": "person", "bbox": [1320, 120, 1780, 1000], "confidence": 0.8}
      ]
    }
  ]
}
```

也支持 `detections[]`、`objects[]`、`segments[]`，以及 `bbox` / `box` / `rect` / `roi` / `face_box` / `person_box`。坐标可以是像素，也可以是 0-1 归一化坐标。

## 用法

```bash
python3 scripts/scene_boundaries.py origin/talk.mp4 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md

python3 scripts/smart_reframe.py origin/talk.mp4 \
  --detections work/detections.json \
  --scene-boundaries work/scene_boundaries.json \
  --platform douyin \
  --output work/reframe_douyin.json \
  --markdown work/reframe_douyin.md \
  --strict

python3 scripts/multi_export.py origin/talk.mp4 \
  --platforms douyin \
  --reframe-plan work/reframe_douyin.json \
  --output-dir output/
```

`--strict` 会在任一片段没有检测结果、只能中心裁切时返回退出码 2，适合导出前提醒人工检查。多人过宽触发 `letterbox` 不算失败，因为这是保留完整画面的有意策略。

## 输出

`smart_reframe.py` 输出：

| 文件 | 说明 |
|---|---|
| `reframe_douyin.json` | 机器可读计划：source、target、params、summary、detections、segments |
| `reframe_douyin.md` | 人工 review 表：每段时间、策略、crop 坐标、检测数、原因 |

典型 segment：

```json
{
  "id": "reframe_001",
  "start": 0.0,
  "end": 6.0,
  "strategy": "track",
  "crop": {"width": 608, "height": 1080, "x": 1110, "y": 0, "focus_x": 0.78, "focus_y": 0.31},
  "reason": "weighted subject focus from detector boxes"
}
```

`strategy` 含义：

| strategy | 含义 |
|---|---|
| `track` | 用检测框加权中心决定裁切位置 |
| `center` | 无检测结果，中心裁切 fallback |
| `letterbox` | 人群/主体跨度太宽，缩放全画面并补边 |

## 参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--platform` | `douyin` | `xhs` / `douyin` / `wxch` |
| `--target-size` | 平台预设 | 自定义目标尺寸，如 `1080x1920` |
| `--wide-subject-threshold` | `0.92` | 主体跨度超过 crop 宽/高多少时 letterbox |
| `--merge-tolerance-px` | `8` | 相邻 crop 坐标差小于该值时合并 |
| `--no-letterbox-wide-groups` | 关 | 强制对多人镜头也裁切跟随 |
| `--strict` | 关 | center fallback 时返回 2 |

## 注意

- 一份 `reframe_plan` 只匹配一个目标尺寸。要同时导出小红书 3:4 和抖音 9:16，分别生成两份 plan。
- 对录屏素材，优先用 `screen_focus.py` 处理点击/热点；`smart_reframe.py` 更适合真人、访谈、panel 和横屏素材转竖屏。
- 如果检测框明显漂移，先修检测 JSON；不要靠增大 crop 容忍度掩盖问题。
