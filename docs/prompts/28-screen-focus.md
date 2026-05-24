# Screen Focus 点击聚焦

把录屏里的点击、菜单、按钮或输入框位置转成可审计的聚焦计划，让 `render_final.py` 在对应时间段自动放大该区域，并可加一个简短标签。适合软件教程、产品演示、操作录屏，不负责录屏本身。

## 输入事件

JSON 格式：

```json
{
  "screen": {"width": 1920, "height": 1080},
  "events": [
    {"time": 4.2, "x": 1510, "y": 820, "label": "导出按钮"},
    {"time": "00:09.50", "x": 0.28, "y": 0.36, "label": "设置面板"}
  ]
}
```

也可以用 CSV，字段至少包含 `time/start`、`x`、`y`，可选 `label`、`duration`、`zoom`。坐标可用像素，也可用 0-1 归一化坐标；像素坐标需要提供屏幕宽高。

## 生成聚焦计划

```bash
python3 scripts/screen_focus.py \
  --events work/clicks.json \
  --screen-width 1920 \
  --screen-height 1080 \
  --output work/screen_focus_plan.json \
  --markdown work/screen_focus_plan.md \
  --default-duration 1.2 \
  --default-zoom 1.75
```

快速手写几个热点：

```bash
python3 scripts/screen_focus.py \
  --event "4.2,1510,820,导出按钮" \
  --event "9.5,540,390,设置面板" \
  --screen-width 1920 \
  --screen-height 1080 \
  --output work/screen_focus_plan.json
```

输出里的 `focus_events[]` 可以直接作为 enrich plan 传给渲染器：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/screen_focus_plan.json \
  --output output/tutorial_master.mp4
```

## 输出字段

| 字段 | 说明 |
|---|---|
| `start` / `end` | 聚焦在成片时间线中的起止秒数 |
| `x` / `y` | 0-1 归一化热点坐标 |
| `zoom` | 放大倍率，默认 1.75，最大 4 |
| `transition` | 淡入淡出时长，默认 0.16 秒 |
| `marker` | 是否在聚焦点画提示框 |
| `label` | 可选标签，会合并为 `text_badges` |

## 复核建议

先看 `screen_focus_plan.md`，确认时间点和坐标没有偏移；渲染后再跑：

```bash
python3 scripts/render_qa.py output/tutorial_master.mp4 --platform douyin
python3 scripts/timeline_view.py output/tutorial_master.mp4 --at 4.2 --output output/verify/focus_4_2.png
```

如果录屏经过裁剪或缩放，优先使用 0-1 归一化坐标，避免像素坐标和最终画幅不一致。
