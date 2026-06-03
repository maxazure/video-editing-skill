# Motion Guard 预渲染动感门禁

用途：在最终渲染前检查这条片子是否兑现了“motion-led / 有运动感”的承诺，避免生成图或静态卡片堆太多，成片变成幻灯片。

借鉴 GitHub 上 OpenMontage / HyperFrames 这类 agent video pipeline 的做法：渲染前先跑结构化校验，发现 motion-required 的镜头被静态图替代时先拦截，而不是等渲染后才看出来。

## 常用命令

```bash
python3 scripts/motion_guard.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-manifest work/storyboard_assets.json \
  --motion-required \
  --output work/motion_guard.json \
  --markdown work/motion_guard.md \
  --strict
```

也可以直接检查 render_config：

```bash
python3 scripts/motion_guard.py \
  --render-config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output work/motion_guard.json \
  --markdown work/motion_guard.md
```

## 看什么指标

| 字段 | 说明 |
|---|---|
| `summary.motion_ratio` | motion 秒数 / 总秒数 |
| `summary.max_still_run` | 最长连续静态或未知镜头秒数 |
| `summary.unresolved_motion_assets` | 需要运动素材但还没生成、审批、搜索或渲染的镜头数 |
| `summary.blocking` | `--motion-required` 下会阻断 render 的问题数 |

`motion_guard.py` 不会提交任何生成任务，也不会消耗 Dreamina/即梦额度。它只读本地 JSON artifact，输出 JSON/Markdown review packet。

## 建议流程

1. `storyboard_plan.py` 产出分镜和生成路由。
2. `storyboard_assets.py` 产出素材状态表。
3. `provider_decision.py` 清理 paid-credit / budget / command blockers。
4. `motion_guard.py --motion-required --strict` 检查 motion density。
5. `render_final.py` 渲染。
6. `render_qa.py` 做渲染后媒体 QA。

如果 motion guard fail，优先把连续静态镜头替换成：

- 本地 B-roll 视频
- Dreamina/即梦生成视频（先确认 paid credits）
- Remotion/HyperFrames 本地 motion card
- `screen_focus.py` 的点击聚焦镜头
