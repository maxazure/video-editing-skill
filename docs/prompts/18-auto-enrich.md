# 18 — Auto-Enrich：让视频自动"丰富有质感"

V3 加入了一套调度模块，决定**在哪儿**给视频加 B-roll / 章节卡 / 贴纸 / 卡点。

## 五个组件

| 模块 | 输出 | 触发逻辑 |
|---|---|---|
| `auto_broll.py` | B-roll 切片 cue | 转折词 / 实体匹配素材库 / 长镜头守卫 |
| `auto_chapter_cards.py` | 1080×1920 PNG | `## ` 标题 / 静音 ≥1.5s 边界 |
| `beat_sync.py` | 节拍时间表 + 对齐函数 | librosa beat_track，缺时回落到固定 BPM 网格 |
| `auto_stickers.py` | emoji 贴纸 cue | 情绪关键词分类（excited/doubt/conclusion/data/warning/joke） |
| `auto_enrich.py` | 综合 plan JSON | 调用以上 4 个，并对 broll/sticker 跑 beat snap |

## 一键执行

```bash
python3 scripts/auto_enrich.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --bgm origin/bgm.mp3 \
  --output work/enrich_plan.json
```

输出 `enrich_plan.json`：

```json
{
  "broll": [
    {"start": 8.50, "end": 10.50, "reason": "transition-word",
     "matched_token": "但是", "suggested_asset": "/origin/seaside.mp4"}
  ],
  "stickers": [
    {"start": 12.20, "end": 13.60, "emotion": "data",
     "sticker": "📈", "text_anchor": "增长50%"}
  ],
  "chapter_cards": [
    {"title": "Hook", "start": 5.0, "duration": 1.0,
     "style": "color_block", "color": "#1A1A1A", "text_color": "#FFFFFF"}
  ]
}
```

## 各模块单独跑

需要细调时单独跑就行：

```bash
# 只算 B-roll
python3 scripts/auto_broll.py --transcript work/transcript.json \
  --assets work/media_library.json \
  --output work/broll_cues.json

# 只生成章节卡 PNG
python3 scripts/auto_chapter_cards.py \
  --script work/clean_script.md \
  --audio work/voice_clean.wav \
  --total-duration 90 \
  --output-dir work/cards/

# 对已有 cuts 做卡点对齐
python3 scripts/beat_sync.py \
  --bgm origin/bgm.mp3 \
  --cuts work/cut_times.json \
  --window 0.20 \
  --output work/cut_times_snapped.json
```

## 触发规则细节

### auto_broll

按优先级排序，**每段最多一条 cue**（避免堆叠）：

1. **transition-word**（最优先）：检测到 `但是 / 然而 / 关键是 / 重点来了 / 我突然意识到 / actually / however` 等转折词 → 在该段开头切 2 秒空镜
2. **entity-match**：素材库的 tag 命中段落文本（例如 tag="海边" + 段落"我们去海边走了走"）→ 用对应素材
3. **long-single-shot**（兜底）：当前段落开始时距上次 B-roll 已超过 5 秒，且开头钩子已结束（> 3 秒）→ 强制切一次

### auto_chapter_cards

- 优先从 clean_script.md 的 `## ` 二级标题取标题
- 数量按 `total_duration` 限：1 分钟视频 2-3 张，2 分钟 4-5 张，最多 5 张
- 位置：能拿到音频时按 silencedetect 边界，否则均匀分布（跳过开头 3 秒钩子）

### auto_stickers

| 情绪 | 关键词 | 贴纸池 |
|---|---|---|
| excited | 突然 / 没想到 / 疯狂 / 超 / amazing | 🚀 ✨ 🔥 |
| doubt | 为什么 / 怎么会 / 可能 / why / maybe | 🤔 ❓ |
| conclusion | 所以 / 总结 / 因此 / 划重点 | 💡 ✅ |
| data | 数字+% / 万 / 增长 / growth | 📈 📊 📉 |
| warning | 小心 / 千万别 / careful | ⚠️ ❗ |
| joke | 哈哈 / 笑死 / funny / lol | 😅 🤣 |

- 默认每 8-15 秒 1 个，单段不超过 2 个

### beat_sync

- 用 librosa 提取节拍；没装时回落到 120 bpm 固定网格
- snap 窗口默认 ±200ms
- 输出仍是 [{start, ...}] 结构，便于直接喂回 render_final

## 把 enrich plan 接回 render

> 截至当前 PR，`render_final.py` 还**不会自动读 enrich_plan.json**。你需要手工把 plan 里的 chapter cards 和 sticker 字段塞到 render config 的 `text_badges` / `end_cards` / `overlays` 里。后续 PR 会做自动接管。

## 可选重型依赖

| 依赖 | 启用什么 | 缺时回落 |
|---|---|---|
| `librosa` | 真实 BGM 节拍检测 | 120 bpm 固定网格 |
| `Pillow` | 章节卡 PNG 渲染 | 报错（必须装） |
| `spacy + zh_core_web_sm` | 命名实体识别（B-roll v2） | 用关键词列表匹配 |
| `zxing-cpp` | 画面二维码扫描（v2） | 不扫，跳过该检查 |

装法：
```bash
pip install librosa Pillow
```
