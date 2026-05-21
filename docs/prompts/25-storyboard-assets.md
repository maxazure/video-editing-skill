# 25 — Storyboard Assets 素材清单与预检

把 `storyboard_plan.json` 转成可执行素材清单，先看每个 shot 需要生成、渲染、审批还是链接本地 B-roll，再进入最终渲染。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时用

- 已经生成 `storyboard_plan.json`，准备安排 Codex 生图、Dreamina/即梦视频、Remotion 动效或本地 B-roll。
- 想在消耗 Dreamina/即梦 credits 前，列出所有需要审批的 paid generation shot。
- 渲染前想确认生成图、生成视频、motion card、本地素材是否已经落盘。
- 多个 agent/人工协作时，需要一个稳定的素材任务清单。

## 命令

```bash
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md \
  --strict
```

`--strict` 会在任意素材还没 ready 时返回退出码 `2`，适合放在渲染前检查；不加 `--strict` 时只写清单，不阻塞流程。

## 输出状态

| status | 含义 | 下一步 |
|---|---|---|
| `ready` | 预期路径或显式资产路径已存在 | 把路径补回 `render_config` / `enrich_plan` |
| `candidate_found` | 本地 B-roll 搜到候选，但还没链接 | 人工确认并写入渲染配置 |
| `needs_generation` | Codex 生图还未落盘 | 用 Codex 内置 `image_gen` 生成并保存 |
| `needs_approval` | Dreamina/即梦视频任务需要先确认 credits | 确认后再提交生成，保存输出视频 |
| `needs_render` | Remotion/HyperFrames 本地动效还未渲染 | 本地渲染 motion card |
| `search_needed` | 本地 B-roll 未找到候选 | 用 `media_library.py search` 或补拍/补素材 |

默认路径约定：

```text
work/
├── imagegen/shot_001.png
├── generated_video/shot_004.mp4
├── motion/shot_003.mp4
└── broll/
```

## 推荐流程

```bash
# 1. 分镜规划
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md

# 2. 素材任务清单
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md

# 3. 按 storyboard_assets.md 补齐素材
#    - codex_imagegen: Codex 内置 image_gen
#    - dreamina_video: 先确认 credits，再提交 Dreamina/即梦
#    - remotion_hyperframes: 本地渲染 motion card
#    - media_library_broll: 选择并链接本地素材

# 4. 渲染前严格检查
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md \
  --strict
```

## 设计原则

- 只做本地预检和任务编排，不自动消耗任何云端生成额度。
- 付费视频生成统一标记为 `needs_approval`，避免 agent 误提交批量任务。
- `ready` 只代表文件存在，不代表画面质量通过；渲染后仍要跑 `render_qa.py` 和必要的 `timeline_view.py`。
