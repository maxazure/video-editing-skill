# Provider Decision Log — 生成供应商选择与预算/审批预检

用于在生成图片、生成视频、渲染动态图卡或搜索 B-roll 之前，先把每个分镜素材任务的 provider 选择、候选路径、成本估算、命令可用性和审批状态写成可审计 JSON/Markdown。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 什么时候用

- `storyboard_assets.py --strict` 发现有 `needs_generation` / `needs_approval` / `needs_render` / `search_needed`
- 想知道哪些任务会消耗 Dreamina/即梦 credits
- 想在批量生成前设置预算上限或单次审批阈值
- 想向客户或人工 reviewer 解释为什么选 Codex imagegen、Dreamina、Remotion/HyperFrames 或本地素材库

## 基本流程

```bash
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --media-library . \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md

python3 scripts/provider_decision.py \
  --asset-manifest work/storyboard_assets.json \
  --output work/provider_decision.json \
  --markdown work/provider_decision.md \
  --budget-cap 3.00 \
  --single-action-approval 0.50 \
  --strict
```

`--strict` 会在以下情况返回退出码 2：

- 选中的 provider 需要人工审批
- 累计估算超过 `--budget-cap`
- 选中的 provider 缺少命令依赖，例如 `dreamina` 或 `node`
- primary route 不可用而降级到了 fallback provider

## 输出内容

`provider_decision.json`：

- `summary.approval_required`：需要审批的任务数
- `summary.paid_credit_tasks`：会消耗 paid credits 的任务数
- `summary.budget_blocked`：超过预算上限的任务数
- `summary.fallback_selected`：因为 primary 不可用而降级的任务数
- `decisions[].options_considered`：每个候选 provider 的 7 维打分
- `decisions[].selected`：当前建议 provider
- `decisions[].next_action`：下一步应该生成、审批、降级还是安装依赖

`provider_decision.md` 是给人看的 review 表，适合贴进任务说明或客户交付清单。

## 7 维评分

评分借鉴 agentic video production 项目常用的 provider governance，但保持本项目轻量：

| 维度 | 权重 | 说明 |
|---|---:|---|
| `task_fit` | 30% | 是否适合这个素材任务 |
| `output_quality` | 20% | 预期成片质量 |
| `control` | 15% | 可控性、可复现性、是否方便改 |
| `reliability` | 15% | 本机/工具链成功概率 |
| `cost_efficiency` | 10% | 成本效率 |
| `latency` | 5% | 等待时间 |
| `continuity` | 5% | 是否延续已定分镜风格和 fallback |

## 成本覆盖

默认成本只是本地治理用的保守估算，不代表真实账单。需要按账号实际价格覆盖时使用：

```bash
python3 scripts/provider_decision.py \
  --asset-manifest work/storyboard_assets.json \
  --output work/provider_decision.json \
  --route-cost dreamina_video=1.20 \
  --route-cost codex_imagegen=0.05 \
  --budget-cap 5.00
```

## 建议闭环

1. `storyboard_plan.py` 产出分镜和路由。
2. `storyboard_assets.py` 找 ready/candidate/missing 状态。
3. `provider_decision.py --strict` 做 provider、预算、审批预检。
4. 人工确认 `needs_approval` 或 `budget_blocked`。
5. 生成或链接素材。
6. 再跑 `storyboard_assets.py --strict`，通过后进入 `render_final.py`。
