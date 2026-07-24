# 73 Platform Safe Area QA 平台 UI 安全区门禁

> 在渲染或多平台导出前，检查字幕、强调 badge、PIP 人像、CTA、章节卡和点击 marker 是否会被顶部状态栏、底部文案区或右侧互动按钮遮挡。

`platform_safe_area_qa.py` 只读取本地 JSON，按 `render_final.py` 的默认布局规则估算 bbox，并输出 JSON、Markdown 和可选 SVG 安全区图。它不上传视频、不调用 LLM、不做 OCR，也不会把社区经验值说成永久的平台官方规范。

## 基本用法

检查抖音 9:16 render config 和 enrich plan：

```bash
python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --platform douyin \
  --output verify/platform_safe_area_qa.json \
  --markdown verify/platform_safe_area_qa.md \
  --guide verify/platform_safe_area_guide.svg \
  --strict
```

每个平台要单独跑一次，因为画布和 UI rail 不同：

```bash
python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --platform xhs \
  --output verify/xhs_platform_safe_area_qa.json \
  --guide verify/xhs_platform_safe_area_guide.svg \
  --strict

python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --platform wxch \
  --output verify/wxch_platform_safe_area_qa.json \
  --strict
```

内置 profile：

| profile | 默认画布 | 用途 |
|---|---:|---|
| `universal` | 1080×1920 | TikTok / Reels / Shorts 的保守并集 |
| `xhs` | 1080×1440 | 本项目小红书 3:4 导出，使用保守比例缩放 |
| `douyin` | 1080×1920 | 抖音；当前用 TikTok 社区经验值作为保守 proxy |
| `wxch` | 1080×1920 | 视频号；当前用 universal profile，可按实测覆盖 |
| `tiktok` | 1080×1920 | TikTok |
| `reels` | 1080×1920 | Instagram Reels |
| `shorts` | 1080×1920 | YouTube Shorts |
| `landscape` | 1920×1080 | 传统 10% title-safe |

## 会检查什么

- `render_config` 默认字幕带：按 `render_final.py` 的 bottom-center ASS、`MarginV=28%` 和 `--font-size` 估算；
- `text_badges[]`、`stickers[]`、`emphasis_cues[]`：按居中 Badge 样式估算；
- `pip_overlays[]`：复用 renderer 的 `width_ratio`、`margin_ratio`、`position` 和默认 9:16 PIP 画幅；
- `focus_events[]`：复用 renderer 的 normalized x/y 与 `marker_size`；
- `end_cards[]`：按居中文字盒估算；
- `chapter_cards[]`：无 PNG 时检查居中 badge；已有整图时标记为 `uncheckable`，要求看 SVG/成片。

以下内容不会被脚本“猜出来”：生成图内部的人脸/标题位置、全屏 B-roll 的主体位置、封面 PNG 内部排版、平台 App 新版本的真实 UI 变化。需要时用自定义 bbox 或人工看一帧。

## 自定义关键元素

把 renderer 之外的 CTA、Logo、下三分之一标题或生成图内文字写成 `elements[]`：

```json
{
  "elements": [
    {
      "id": "final-cta",
      "kind": "cta",
      "critical": true,
      "units": "normalized",
      "bbox": {
        "x": 0.12,
        "y": 0.76,
        "width": 0.58,
        "height": 0.10
      }
    }
  ]
}
```

运行：

```bash
python3 scripts/platform_safe_area_qa.py \
  --elements work/platform_elements.json \
  --platform tiktok \
  --output verify/platform_safe_area_qa.json \
  --markdown verify/platform_safe_area_qa.md \
  --guide verify/platform_safe_area_guide.svg \
  --strict
```

`bbox` 也可用像素 `[x, y, width, height]`，或 `{left, top, right, bottom}`。`critical: false` 的元素越界只产生 warning；关键元素越界写入 `summary.blocking`，`--strict` 返回 2。

## 覆盖当前实测边距

平台 UI 会变化。拿到当前 App 的实测截图后，可直接覆盖四边的 unsafe margin：

```bash
python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --platform xhs \
  --safe-left 70 \
  --safe-top 160 \
  --safe-right 130 \
  --safe-bottom 340 \
  --output verify/xhs_platform_safe_area_qa.json \
  --guide verify/xhs_platform_safe_area_guide.svg \
  --strict
```

四个数都是画布像素；`right` / `bottom` 表示从对应边缘往内保留多少像素。

## 处理 blocker

1. 打开 Markdown，定位 `source` 和 `breaches`。
2. 打开 SVG，看元素框与绿色 safe rectangle 的关系。
3. PIP 优先改为 `top_left` / `center`，必要时减小 `width_ratio`。
4. Focus marker 移动 x/y 或减小 `marker_size`。
5. 字幕/CTA 需要按实际字体和最长一行重新声明 bbox；不要只看脚本估算。
6. 修改 layout 后重跑；发布流程需要时用 `pipeline_manifest.py --require platform_safe_area_qa --strict` 强制门禁。

`platform_safe_area_qa.v1` 是 layout artifact，不替代 `subtitle_readability_qa.py` 的 CPS/时间检查，也不替代渲染后一帧一帧的人工审片。
