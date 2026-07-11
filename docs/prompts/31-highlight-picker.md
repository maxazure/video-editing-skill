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
  --scene-boundaries work/scene_boundaries.json \
  --scene-snap-tolerance 1.5 \
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

如果先运行了 [32 Scene Boundaries](32-scene-boundaries.md)，`--scene-boundaries` 会让候选片段开头只向前扩展到附近视觉切点、结尾只向后扩展到附近视觉切点，避免吞掉 transcript 字词。扩展信息会写入每个 candidate 的 `scene_snap`，并进入 `render_config`。

## 按自然语言 brief 找片段

当用户不是要“自动找最爆的片段”，而是已经知道要找什么时，传 `--brief` 或 `--query`：

```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --brief "产品发布 用户反应 价格对比" \
  --output work/brief_highlights.json \
  --markdown work/brief_highlights.md \
  --video origin/long-talk.mp4 \
  --render-config work/brief_render_config.json \
  --platform douyin \
  --num-clips 3 \
  --strict
```

`--brief` 会把自然语言意图拆成英文关键词和中文短语片段，和原有 hook/value/duration/completeness 分数一起排序。输出里每条 candidate 会多出：
- `brief_match.score`：0-1 相关性分数
- `brief_match.matched_terms`：命中的 brief 词
- `score_breakdown.brief`：参与总分的相关性分

这个模式适合 “找产品 reveal”、“找用户强反应”、“找教程关键步骤”、“找失败教训” 这类定向剪片。仍然要看 Markdown 复核表，避免只因为关键词命中就截到半句话。

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
- 如果传了 `--brief`，是否命中 brief/query 的主题词

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

## 选中后校正音频边界

`scene_boundaries.py` 负责把候选扩到附近视觉切点；如果还要确保不在词中间或半句结尾硬切，接着运行 [65 Audio Boundary Snap](65-audio-boundary-snap.md)：

```bash
python3 scripts/audio_boundary_snap.py \
  --candidates work/highlight_candidates.json \
  --transcript work/long_transcript.json \
  --media origin/long-talk.mp4 \
  --output work/audio_boundary_plan.json \
  --markdown work/audio_boundary_plan.md \
  --strict
```

复核 `audio_boundary_plan.md` 的 start/end delta 后，可把 JSON 直接传给 `shorts_batch.py --highlights`。
