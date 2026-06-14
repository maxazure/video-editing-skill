# 48 — Color Grade 调色计划

`scripts/color_grade.py` 给短视频生成可审计的调色计划：先选一个 bounded preset，再输出 `color_grade.v1`、Markdown review 和 FFmpeg filter。最终渲染时用 `render_final.py --color-grade` 接入，filter 会放在 B-roll / 图片 / 点击聚焦之后、字幕和水印之前，统一画面质感但不改变字幕颜色。

## 适用场景

| 场景 | 推荐 preset |
|---|---|
| 普通口播 / 产品演示 | `natural` |
| 生活方式 / 咖啡厅 / 桌面 | `warm` |
| AI 工具 / 软件教程 / 录屏 | `screen` 或 `cool` |
| 节奏强的短视频 | `punchy` |
| 访谈 / 人像 / 平缓叙事 | `soft` |
| B-roll 多、想要轻电影感 | `cinematic` |

## 用法

生成调色计划：

```bash
python3 scripts/color_grade.py \
  --preset screen \
  --output work/color_grade.json \
  --markdown work/color_grade.md
```

渲染时接入计划：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --color-grade work/color_grade.json \
  --output output/tutorial_master.mp4
```

也可以直接用 preset：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --color-grade warm \
  --output output/day58_master.mp4
```

如果主片已经渲染完，只想对现有 master 做一次调色复版：

```bash
python3 scripts/color_grade.py \
  --preset cinematic \
  --input output/day58_master.mp4 \
  --render-output output/day58_master_grade.mp4 \
  --output work/color_grade.json \
  --markdown work/color_grade.md
```

## 自定义参数

```bash
python3 scripts/color_grade.py \
  --preset natural \
  --contrast 1.12 \
  --saturation 1.08 \
  --temperature 0.05 \
  --sharpness 0.2 \
  --output work/color_grade.json \
  --markdown work/color_grade.md \
  --strict
```

参数都会被限制在保守范围内：`brightness -0.12..0.12`、`contrast 0.75..1.35`、`saturation 0.65..1.45`、`gamma 0.80..1.25`、`temperature -0.20..0.20`、`tint -0.12..0.12`、`sharpness 0..0.80`。`--strict` 会在参数被 clamp 时返回退出码 2，适合自动化流程发现过激调色。

## render_config 写法

```json
{
  "color_grade": {
    "preset": "screen",
    "adjustments": {
      "contrast": 1.08,
      "saturation": 1.0,
      "sharpness": 0.35
    }
  }
}
```

也可以直接引用 `color_grade.py` 输出的 plan：

```json
{
  "color_grade": "work/color_grade.json"
}
```

## Prompt

```text
请先根据素材类型选择 color_grade.py preset：口播用 natural，软件录屏用 screen，生活方式用 warm，节奏强的短视频用 punchy，访谈用 soft，B-roll 多的片子用 cinematic。先输出 work/color_grade.json 和 work/color_grade.md 供我确认；最终渲染时用 render_final.py --color-grade work/color_grade.json 接入，不要用二次压缩替代单次编码渲染。若只是给已完成 master 做复版，再用 color_grade.py --input --render-output。
```
