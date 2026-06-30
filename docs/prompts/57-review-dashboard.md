# Review Dashboard 人工复核面板

> 把项目里的 pipeline gates、QA、preflight、音频报告、发布包等 artifact 汇总成一个可打开的 HTML 复核页。

适用场景：

- 渲染或发布前想让用户一次性看清还剩哪些 blocker。
- 一个视频项目跨 agent/跨会话交接，需要给下一位执行者一个浏览器可读的 review queue。
- 已经跑过多个 gate，不想在一堆 JSON/Markdown 里手动找最新状态。

不适用：

- 需要交互式 NLE 时间线编辑。这个脚本只生成静态 HTML/JSON，不启动服务。
- 需要重新评分创意质量。它只整理本地 artifact 和 gate 状态，不调用视觉/LLM provider。

## 生成复核面板

```bash
python3 scripts/review_dashboard.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/review_dashboard.json \
  --html work/day58/review_dashboard.html \
  --strict
```

输出：

- `review_dashboard.json`：`review_dashboard.v1`，包含 `review_state`、`review_items[]`、`next_actions[]`、`latest_artifacts[]` 和完整 `gate_snapshot[]`。
- `review_dashboard.html`：可直接用浏览器打开的复核面板，列出 blocker/warning、下一步动作和最新 artifact 链接。

`--strict` 会在项目状态 blocked 时返回 2；如果希望 warning 也阻塞 CI/自动化，加 `--fail-on-warn`。

## 要求额外 gate

默认 `publish_ready` 要求 transcript、clean script、render config、master video、render QA 和 caption。临时要求更多 artifact 时重复 `--require`：

```bash
python3 scripts/review_dashboard.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require audio_master_report \
  --require publish_package \
  --output work/day58/review_dashboard.json \
  --html work/day58/review_dashboard.html \
  --strict
```

## 建议复核顺序

1. 先看 `Review Queue`：只处理 blocker、missing required 和 warning。
2. 再看 `Next Actions`：按顺序补跑脚本或修复素材。
3. 最后看 `Gate Snapshot`：确认所有 required gate 都是 ready，再让 agent 继续 render/publish handoff。

这个面板适合跟 `project_resume.py` 搭配使用：`project_resume` 给 agent 接着做的提示词，`review_dashboard` 给人和 agent 看项目是否真的可以继续下一阶段。
