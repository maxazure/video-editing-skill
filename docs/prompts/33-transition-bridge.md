# 33 - Transition Bridge 转场桥接计划

当分镜之间跳得太硬、但又不想直接提交付费生成任务时，先用 `transition_bridge.py` 做一份本地转场计划。

它读取 `storyboard_plan.json`，可选读取 `storyboard_assets.json`，为相邻 shot 生成：

- 是否需要 AI 转场的透明评分
- 前一镜尾帧 / 后一镜首帧的引用路径
- Dreamina/即梦转场 prompt
- paid-credit 审批提示
- 本地 fallback（crossfade / straight cut）

脚本只写 JSON/Markdown，不调用 Dreamina，不下载素材，不消耗 credits。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 常用命令

```bash
python3 scripts/transition_bridge.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-manifest work/storyboard_assets.json \
  --asset-root work \
  --output work/transition_bridge_plan.json \
  --markdown work/transition_bridge_plan.md \
  --mode auto \
  --max-ai-bridges 3
```

## 模式选择

| 模式 | 行为 |
|---|---|
| `auto` | 只给 section / route / keyword 变化较大的相邻镜头标记 `dreamina_video` |
| `ai` | 每个相邻镜头都标记为需审批的 AI 转场 |
| `default` | 全部使用本地 deterministic crossfade 计划 |
| `skip` | 不生成转场桥接项 |

`--strict` 会在存在 `needs_approval` 时返回退出码 2，适合自动化里提醒人工先确认 credits。

## 推荐工作流

```bash
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md

python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md

python3 scripts/transition_bridge.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-manifest work/storyboard_assets.json \
  --asset-root work \
  --output work/transition_bridge_plan.json \
  --markdown work/transition_bridge_plan.md \
  --mode auto \
  --strict
```

如果 `--strict` 返回 2，先看 `work/transition_bridge_plan.md`：只批准值得生成的 `dreamina_video` bridge，其余用 fallback。

## 输出字段

| 字段 | 说明 |
|---|---|
| `summary.paid_credit_tasks` | 需要 Dreamina/即梦审批的转场数 |
| `bridges[].need_score` | 分镜变化强度，来自 section/route/keyword/CTA 等信号 |
| `bridges[].reference_frames` | 前一镜尾帧、后一镜首帧的素材路径或候选路径 |
| `bridges[].prompt` | 可复制到视频生成工具的自然语言 prompt |
| `bridges[].fallback_route` | 不生成时的本地 fallback |
| `bridges[].expected_path` | 批准生成后建议保存的位置 |

## 注意

Dreamina/即梦生成和 upscale 命令可能消耗 credits。转场生成通常比普通剪辑更贵，建议只对情绪/场景/叙事跳变明显的镜头使用，并保持小批量。
