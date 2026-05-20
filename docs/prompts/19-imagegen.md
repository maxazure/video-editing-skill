# 19 — AI 生图（gpt-image-2 / Codex imagegen）

V3 加入了对 OpenAI **gpt-image-2** 的支持。当抽象概念（注意力机制、信息茧房、复利…）出现在口播里时，自动产出适配 gpt-image-2 提示词格式的生图请求；Codex agent 可以直接调内置的 `imagegen` 工具完成生图。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时让 AI 生图

| 场景 | 建议 | 备选 |
|---|---|---|
| 抽象概念可视化（"注意力机制" / "复利"） | ✅ 适合 AI 生图 | 真实素材替代不了 |
| 数据图（不含真实数字） | ✅ 适合 AI 生图 | 后期叠真数 |
| 章节卡背景 | ✅ 适合 AI 生图 | 也可用 Pillow 纯色块 |
| 章节标题卡（带文字） | ⚠️ 短英文标题 OK；中文/多行容易翻车 | Pillow 渲染更稳 |
| B-roll 替代（真实素材不够） | ⚠️ 中远景 OK，特写慎用 | 优先用素材库 |
| 人物 / 创始人形象 | ❌ 不要 AI 生 | 用真实拍摄 |
| Logo / 品牌素材 | ❌ 不要 AI 生 | 用 SVG / Figma 设计稿 |

## V3 自动检测

`auto_enrich.py` 会自动扫描 transcript + clean_script，检测：

1. **抽象概念关键词命中**（来自 `scripts/imagegen_hint.py::ABSTRACT_CONCEPTS`）：
   - 注意力机制 → `attention_mechanism` 模板
   - 信息茧房 / 信息孤岛 / 回音壁 → `information_bubble`
   - 复利 / 复利效应 / 雪球效应 → `compound_interest`
   - 长尾 / 长尾效应 → `long_tail_effect`

2. **隐喻提示词**（"比如" / "比方说" / "想象一下" / "类比" / "好比是"）→ 触发 free-form 提示词骨架，给 agent 填具体内容

3. **clean_script 二级标题**中含上述概念时，额外加一条 `chapter_background` 生图

结果以 `imagegen` 字段并入 `enrich_plan.json`。

## 用法

### 1. 单独跑 imagegen_hint

```bash
python3 scripts/imagegen_hint.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/imagegen_cues.json \
  --codex-md work/imagegen_briefing.md
```

`imagegen_briefing.md` 是给 Codex agent 直接抄的文档，每个 cue 包含：
- trigger（为什么标出来）
- timing（视频里第几秒）
- template 名
- **完整 EN prompt**（粘贴即用）
- 中文 prompt（参考；不要替换 EN，gpt-image-2 处理英文更稳）

### 2. 集成在 auto_enrich

```bash
python3 scripts/auto_enrich.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --bgm origin/bgm.mp3 \
  --output work/enrich_plan.json

# enrich_plan.json 会有四个并列字段：
#   broll / stickers / chapter_cards / imagegen
```

### 3. 在 Codex 里执行

Codex agent 看到 `imagegen` cues 后，直接调内置 `imagegen` 工具：

```
# Codex 会话里这样描述就够：
> 把 work/imagegen_cues.json 里第 1 条 prompt 用 imagegen 生成，
> 1024x1536 high quality，存到 work/imagegen/attention_mechanism.png
```

Codex 不需要 `OPENAI_API_KEY`——它自己路由到 gpt-image-2。文件落到 `$CODEX_HOME/generated_images/`，再由 Codex 移到你指定的位置。

### 4. 在 Codex 外（用 OpenAI Python SDK）

如果你不用 Codex，需要自己调 OpenAI API。本 skill 只产出 prompt，不内置客户端。用法举例：

```python
# pip install openai
import os, json, openai
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
cues = json.load(open("work/imagegen_cues.json"))

for i, cue in enumerate(cues):
    rsp = client.images.generate(
        model="gpt-image-1",   # 或当前可用的最新模型
        prompt=cue["prompt_en"],
        size="1024x1536",
        quality="high",
        n=1,
    )
    img_b64 = rsp.data[0].b64_json
    # 解码写文件，filename 用 cue["concept"] slug
```

> 模型名按 OpenAI 当前可用为准。在 Codex 里走 gpt-image-2，在 API 直连里通常是 gpt-image-1.5/2（视账号权限）。两者 prompt 格式完全一样，所以本 skill 的模板库都通用。

## gpt-image-2 提示词规则（必读）

来源：[OpenAI Cookbook prompting guide](https://cookbook.openai.com/examples/multimodal/image-gen-models-prompting-guide) · [fal.ai gpt-image-2 guide](https://fal.ai/learn/tools/prompting-gpt-image-2) · [gpt-image-2 model docs](https://developers.openai.com/api/docs/models/gpt-image-2)

### 1. 七槽位结构

```
Subject → Action / Context → Environment → Mood → Style → Lighting & Color → Camera & Composition
                                                                    ↓
                                                            + Constraints
```

每个 sample prompt 都按这个序写。例如 attention_mechanism：

```
[Subject]      isometric illustration of an "Attention Mechanism"
[Action]       beams of soft cyan light selectively converging
[Environment]  glowing translucent neural network, floating word tokens
[Mood]         (implicit via "soft cyan" + "flat vector")
[Style]        flat vector style
[Lighting]     glowing, navy background, subtle dot grid
[Composition]  3/4 isometric view, crisp edges, generous negative space
[Constraints]  no labels, no watermark, no human face... (system suffix)
```

### 2. 引号 = 精确渲染目标

gpt-image-2 把双引号字符串当作 **exact-render** 文本。这是唯一靠谱的图内文字方式：

```
✅ The phrase "COMPOUND INTEREST" in small serif caps along the bottom edge.
✅ A title "CHAPTER 02" in large elegant serif caps centered above the line.
❌ A title that says compound interest somewhere   (会变成 c0mp0und 1nter3st 这种)
```

中文/非拉丁字体准确率低；标题必须中文时，建议 Pillow 渲染而不是 gpt-image-2。

### 3. 长度 60-250 词最佳

短了模型靠默认，长了指令稀释。每个 prompt 应该完整自然语言段落，不是逗号串。

### 4. 无独立 negative prompt 字段

把"不要什么"写进同一段：

```
✅ No watermark. No text on the notebook. No face visible.
❌ negative_prompt="watermark, text, face"   (gpt-image-2 不读这种字段)
```

### 5. 避坑

| 坑 | 怎么躲 |
|---|---|
| **人脸/人手特写**翻车 | 用 reference 图 + `images.edit`；或拍背影/中景 |
| **AI 味写实图** | 写具体相机+光圈+光照（"Fuji X100V at f/2.0, available light"），不要堆 "hyperrealistic 8k ultra-detailed" |
| **图里中文字**易错字 | 中文标题用 Pillow，不要让 gpt-image-2 渲染 |
| **角色 ID 漂移**（多张图同一人不一样） | 用 `images.edit()` 传 anchor 图，prompt 写 `"Continue with the same character"` |
| **透明背景** | gpt-image-2 不支持。Codex 变通：生成在 `#00ff00` chroma-key 上，跑 remove_chroma_key |

### 6. 与 Midjourney / SD 的迁移

- ✅ subject / style / lighting / camera 术语通用
- ✅ composition 术语通用（rule of thirds / low angle）
- ❌ Midjourney `--ar 16:9 --stylize 250` 标志 → 用 `size="1024x1536"` 参数
- ❌ SD `(detailed:1.3)` 权重括号 → gpt-image-2 忽略
- ❌ `[ugly, blurry]` 负向方括号 → gpt-image-2 忽略，要写进句子
- ❌ SD 风的关键词逗号串 → gpt-image-2 偏好整句叙述

## 内置模板速查

`scripts/prompts/imagegen_templates.yaml` 7 个 sample：

| id | 概念 | 用途 | 比例 |
|---|---|---|---|
| `attention_mechanism` | 注意力机制 | abstract_concept | 9:16 |
| `information_bubble` | 信息茧房 | abstract_concept | 9:16 |
| `compound_interest` | 复利 | chapter_background | 9:16 |
| `long_tail_effect` | 长尾效应 | abstract_concept | 9:16 |
| `data_bars_clean` | 数据图（无数字） | data_visualization | 9:16 |
| `chapter_title_card_minimal` | 章节卡 #02 | chapter_title_card | 9:16 |
| `broll_notebook_morning` | 早晨笔记本 | broll_fallback | 9:16 |

## 5 个 structure 槽位

| structure | 用途 | 关键 slot |
|---|---|---|
| `chapter_background` | 章节卡背景，标题烧在上面 | 上半留白给字幕 |
| `chapter_title_card` | 全屏章节卡（图本身带文字） | 引号锁定精确文字 |
| `broll_fallback` | B-roll 替代画面 | 中远景、隐藏脸 |
| `data_visualization` | 数据图 | 不含真数字 |
| `abstract_concept` | 抽象概念 | 用具象隐喻 |

## Codex `imagegen` 接入细节

- **内置工具**：`imagegen` 是 Codex CLI 的标准 skill。在 Codex 会话里直接说"用 imagegen 生成 ..." 即可，**不需要 OpenAI API key**——Codex 路由到 gpt-image-2 后端。
- **输出位置**：默认落到 `$CODEX_HOME/generated_images/`，可以让 Codex 移到 `work/imagegen/`。
- **不要一次塞 n=N 个 prompt**：每张图一次独立调用，prompt 单独控制。
- **Codex 外的路径**：用 OpenAI Python SDK 自己调（`openai.OpenAI().images.generate(...)`），需要 `OPENAI_API_KEY`。本 skill 不内置客户端，只输出 prompt。

## 全部流水线里的位置

```
口播 → transcribe → rewrite_script → content_guard → auto_enrich
                                                          │
                                                          ├─→ broll cues
                                                          ├─→ chapter cards
                                                          ├─→ stickers
                                                          └─→ imagegen cues ← 本文档
                                                                  │
                                                                  ↓
                                                          Codex `imagegen`
                                                                  │
                                                                  ↓
                                                          work/imagegen/*.png
                                                                  │
                                                                  ↓
                                                          enrich_plan image_path/generated_path
                                                                  │
                                                                  ↓
                                                          render_final --enrich-plan
```

如果已经把生成图路径写回 `imagegen[].image_path` 或 `imagegen[].generated_path`，`render_final.py --enrich-plan work/enrich_plan.json` 会自动把图片作为定时 overlay 接入；还没有生成文件的 cue 会保留为提示，不阻塞导出。

## 信源

- [OpenAI Cookbook — Image gen prompting guide](https://cookbook.openai.com/examples/multimodal/image-gen-models-prompting-guide)
- [OpenAI Cookbook — gpt-image-1.5 prompting guide](https://cookbook.openai.com/examples/multimodal/image-gen-1.5-prompting_guide)
- [gpt-image-2 model docs](https://developers.openai.com/api/docs/models/gpt-image-2)
- [Codex imagegen SKILL.md](https://github.com/openai/codex/blob/main/codex-rs/skills/src/assets/samples/imagegen/SKILL.md)
- [fal.ai gpt-image-2 prompting](https://fal.ai/learn/tools/prompting-gpt-image-2)
- [OpenAI Community — gpt-image-2 issues collection](https://community.openai.com/t/collection-of-gpt-image-generator-2-0-issues-bugs-and-work-around-tips-check-first-post/1379535)
- [Lushbinary 2026 vs Midjourney/Flux/Imagen comparison](https://lushbinary.com/blog/ai-image-generation-comparison-midjourney-gpt-flux/)
