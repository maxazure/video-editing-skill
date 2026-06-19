# PIP Overlay 摄像头小窗

`scripts/pip_overlay.py` 把录屏教程、产品 demo 或课程里的 facecam/camera 录制转成可复核的 `pip_overlays[]` enrich plan。它不录屏、不转码、不混入 camera audio，只生成 JSON 和 Markdown，最终由 `render_final.py --enrich-plan` 在单次编码链路里合成。

## 常用流程

```bash
python3 scripts/pip_overlay.py \
  --camera origin/facecam.mp4 \
  --segment "0,18,bottom_right" \
  --segment "18,42,top_right" \
  --sync-offset 0.18 \
  --width-ratio 0.24 \
  --output work/pip_overlay_plan.json \
  --markdown work/pip_overlay_plan.md

python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/screen_focus_plan.json \
  --enrich-plan work/pip_overlay_plan.json \
  --primary-speed 1.25 \
  --output output/tutorial_master.mp4
```

## 什么时候用

- 软件教程、产品 walkthrough、课程录屏，需要讲解人小窗增强信任感。
- 主画面是屏幕录制，已经用 `screen_focus.py` 标出点击位置。
- camera 和 screen 分开录制，需要用 `--sync-offset` 做轻微同步。
- 不想进剪映/Screen Studio 手工摆小窗，希望保留可审计计划。

## 参数说明

| 参数 | 说明 |
|---|---|
| `--camera` | facecam/camera 视频路径 |
| `--segment "start,end[,position]"` | 一段 PIP 显示区间；可重复，用来改变小窗位置 |
| `--sync-offset` | 读取 camera 源时额外加的秒数；camera 画面晚到时可设正数 |
| `--source-start` | 第一个 segment 对应的 camera 源起点；多段会按时间差推导 |
| `--position` | 默认位置：`bottom_right` / `bottom_left` / `top_right` / `top_left` / `center` |
| `--width-ratio` | 小窗宽度占成片宽度比例，默认 `0.24` |
| `--margin-ratio` | 小窗边距占短边比例，默认 `0.035` |
| `--opacity` | 小窗透明度，默认 `1.0` |
| `--transition` | 淡入淡出时长，默认 `0.12` 秒 |

## 输出字段

`pip_overlay_plan.json` 是标准 enrich plan：

```json
{
  "version": "pip_overlay_plan.v1",
  "pip_overlays": [
    {
      "video": "/abs/path/facecam.mp4",
      "start": 0.0,
      "end": 18.0,
      "source_start": 0.18,
      "sync_offset": 0.18,
      "position": "bottom_right",
      "width_ratio": 0.24,
      "audio": false
    }
  ]
}
```

`render_final.py` 会：

- 把 `pip_overlays[]` 作为 timed video overlay 接入。
- 默认忽略 camera audio，只保留主 render config 的人声/BGM 链路。
- 在 `--primary-speed` 或 `--speed` 输出中同步压缩 PIP 时间线。
- 把 PIP 放在字幕前合成，字幕仍然在最上层，避免小窗遮挡字幕不可读。

## 复核建议

先看 `pip_overlay_plan.md`，确认每段时间、位置和 `source_start`。渲染后建议抽查小窗开始、位置切换和结束附近：

```bash
python3 scripts/timeline_view.py output/tutorial_master.mp4 --at 18 --output output/verify/pip_18s.png
python3 scripts/render_qa.py output/tutorial_master.mp4 --platform douyin
```

如果成片变速后小窗不同步，优先检查 `--sync-offset` 的方向：正数表示读取 camera 源时更晚的位置，负数表示更早的位置。
