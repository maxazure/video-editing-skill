# 31 Highlight Picker 长视频精华候选

> 从 `transcript.json` 里先挑出可发布的短视频候选，再决定渲染哪几条。

适合 10-60 分钟访谈、课程、直播回放、长口播。脚本只做本地打分和交付 artifact，不调用 LLM、不剪视频、不提交任何付费生成任务。

## 基本用法

```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --platform douyin \
  --num-clips 5 \
  --strict
```

输出：
- `highlight_candidates.json`：所有候选和 top selected，含 `score`、`signals`、`warnings`、`hook_text`、`reason`、`segment_ids`
- `highlight_candidates.md`：人工复核表，先看 hook、理由和 warning

## 直接生成 render_config

如果已经知道原始视频路径，可以让脚本同时输出 `render_final.py` 可用的配置：

```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --video origin/long-talk.mp4 \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --render-config work/highlight_render_config.json \
  --platform xhs \
  --num-clips 3

python3 scripts/render_final.py \
  --config work/highlight_render_config.json \
  --output output/highlight_master.mp4 \
  --versioned-output
```

## 打分逻辑

`highlight_picker.py` 会用滑动 transcript 窗口生成候选，并按平台默认时长过滤：

| 平台 | 默认时长 |
|---|---:|
| 小红书 `xhs` | 20-90s |
| 抖音 `douyin` | 15-60s |
| 视频号 `wxch` | 15-60s |
| TikTok / Shorts | 15-60s |
| Reels | 15-90s |

分数来自透明规则：
- 前 5 秒是否有问题、反常识、痛点、数字结果
- 片段中是否有转折、揭秘、实用清单、步骤、模板
- 是否有情绪峰值、明确数据点、发布 CTA
- 时长是否接近平台 sweet spot
- 语速密度是否适合短视频
- 是否开头/结尾像半句话
- 是否有明显 filler-heavy 风险

相互重叠的候选会按分数去重，避免同一个精彩段落重复输出多条。

## 常用参数

```bash
# 自定义时长
python3 scripts/highlight_picker.py \
  --transcript work/transcript.json \
  --output work/highlights.json \
  --min-duration 20 \
  --max-duration 75 \
  --target-duration 45

# 更严格的自动化门禁
python3 scripts/highlight_picker.py \
  --transcript work/transcript.json \
  --output work/highlights.json \
  --min-score 65 \
  --strict
```

`--strict` 会在最优候选低于 `--min-score` 时返回退出码 2，适合自动化里提示人工重写 hook 或重新拆段。
