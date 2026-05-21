# 24 — Storyboard Plan 分镜与生成路由

把 transcript / clean script 先转成可审计 shot cards，再决定哪些画面用已有素材、哪些用 Codex 生图、哪些需要 Dreamina/即梦视频生成或 Remotion/HyperFrames 动效。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时用

- 口播素材画面不够，需要先规划 B-roll / 生成图 / 生成视频。
- 想把抽象概念做成一致的视觉系列，而不是临时一张张出图。
- 要用 Dreamina/即梦、Seedance、Veo、Runway 等视频模型前，先确认分镜和连续性，避免浪费额度。
- 只有音频或清稿，准备做 Remotion/HyperFrames 解说视频。

## 命令

```bash
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md \
  --max-shots 8 \
  --target-aspect 9:16 \
  --platform xhs
```

输出：

- `storyboard_plan.json`：机器可读的分镜、时间码、路由、连续性锚点、检查项。
- `storyboard_plan.md`：给人快速 review 的 shot cards。

## 路由含义

| route | 用途 | 是否会直接消耗额度 |
|---|---|---|
| `codex_imagegen` | 抽象概念、情绪隐喻、章节视觉 | 否；脚本只产 prompt，Codex agent 调 `image_gen` 时才生成 |
| `dreamina_video` | 有动作/场景变化的生成视频 | 否；脚本只规划。提交 Dreamina/即梦前必须确认，因为可能消耗 credits |
| `remotion_hyperframes` | 数字、图表、CTA、确定性动效卡 | 否；本地实现后再渲染 |
| `media_library_broll` | 优先搜索现有素材库 B-roll | 否 |

## 推荐流程

```bash
# 1. 自动丰富
python3 scripts/auto_enrich.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/enrich_plan.json

# 2. 分镜规划
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md

# 3. 生成素材任务清单
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md

# 4. 人工 review work/storyboard_plan.md 和 work/storyboard_assets.md
#    - codex_imagegen: 用 Codex 内置 image_gen 出图，文件放 work/imagegen/
#    - dreamina_video: 先确认额度，再按 prompt 提交 Dreamina/即梦
#    - remotion_hyperframes: 做成本地动效 overlay
#    - media_library_broll: 用 media_library.py search 找已有素材

# 5. 把生成/找到的素材路径补回 enrich_plan 或 render_config
python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output output/master.mp4
```

## Review 清单

- 每个 shot 的 `narration` 是否和画面目标一致。
- `continuity.anchors` 是否能保持系列感：色彩、主体尺度、字幕安全区。
- 生成媒体 prompt 里不要硬编码中文字幕，字幕交给 `render_final.py` 烧录。
- `dreamina_video` 只代表“适合生成视频”，不是自动提交任务；提交前确认 credits。
- 分镜确认后运行 `storyboard_assets.py --strict`，确保生成图/生成视频/motion card/B-roll 路径都 ready。
- 渲染后仍然跑 `render_qa.py` 和必要的 `timeline_view.py`。
