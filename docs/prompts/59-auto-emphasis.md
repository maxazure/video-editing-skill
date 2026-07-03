# 59 — Auto Emphasis：口播重点自动落点

`auto_emphasis.py` 从 transcript 里找适合加“轻强调”的口播点：问句、数字 claim、转折、结论、风险提醒，以及明显停顿后的恢复。输出是 `emphasis_cues[]`，可以单独 review，也可以作为 `render_final.py --enrich-plan` 的输入。

## 单独生成强调计划

```bash
python3 scripts/auto_emphasis.py \
  --transcript work/transcript.json \
  --output work/emphasis_plan.json \
  --markdown work/emphasis_plan.md \
  --min-interval 3 \
  --max-cues 12
```

输出示例：

```json
{
  "version": "auto_emphasis_plan.v1",
  "summary": {"cues": 3},
  "emphasis_cues": [
    {
      "start": 5.1,
      "end": 6.15,
      "label": "数据点",
      "trigger": "numeric_claim",
      "matched_text": "42%",
      "effect": "badge_push_in",
      "zoom": 1.1,
      "x": 0.5,
      "y": 0.5,
      "marker": false
    }
  ]
}
```

如果 transcript 里有 `words[]`，脚本会优先锚到具体词的时间戳；没有词级时间戳时，会按该词在段落里的字符位置估算。

## 接入渲染

```bash
python3 scripts/edit_preflight.py \
  --config work/render_config.json \
  --enrich-plan work/emphasis_plan.json \
  --output work/edit_preflight.json \
  --markdown work/edit_preflight.md \
  --strict

python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/emphasis_plan.json \
  --output output/master.mp4
```

`render_final.py` 会把 `emphasis_cues[]` 转成两类效果：

- `label` / `matched_text` → timed ASS badge。
- `effect=badge_push_in` 或 `zoom > 1` → 画面中心轻微 push-in，默认不画红框。

## 什么时候用

- 口播里有很多数字、反转、结论，想让观众更容易抓重点。
- 不想额外找 B-roll，但希望成片节奏不完全平。
- 完整 `auto_enrich.py` 太重，只想先生成可复核的强调点。

如果已经在跑完整 enrich pipeline，直接用 `auto_enrich.py` 即可，它会自动包含 `emphasis_cues[]`。
