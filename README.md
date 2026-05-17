# Video Editing Skill — 小红书内容引擎

把 5-15 分钟的原始口播音频 + 一堆无声素材，变成可发布的多平台短视频。

不是另一个剪辑工具。是一条 **从口播 → 重组故事 → 平台守门 → 自动丰富 → 渲染 → 三平台导出 → 标题文案** 的端到端流水线，按小红书/抖音/视频号的算法和审核规则调过参。

```
口播音频 + 无声素材
   │
   ├─→ transcribe.py            转写 + 词级时间戳 + 口误标记
   │                            (mlx-whisper / faster-whisper / openai-whisper)
   │
   ├─→ rewrite_script.py        LLM 重组为 5 段式 (hook/pain/turn/value[]/cta)
   │     ↑ 8 hook 模板 + 5 CTA 模板 + 3 故事结构
   │
   ├─→ content_guard.py         80+ 条平台雷区 lint (HARD-BLOCK / SOFT-WARN)
   │     极限词 / 导流 / 医美 / 财富诱导 ...
   │
   ├─→ auto_enrich.py           调度 B-roll / 章节卡 / 贴纸 / BGM 卡点
   │     │ transition / entity match / silence boundary / beat snap
   │     │
   │     └─→ imagegen_hint.py   抽象概念检测 → gpt-image-2 提示词
   │           ↓                 (Codex 内置 imagegen 工具直接执行；无 API key)
   │           Codex imagegen   注意力机制 / 复利 / 信息茧房 等自动配图
   │
   ├─→ render_final.py          单次编码渲染
   │     Heavy 字幕 + 自动响度规范化 + speed + 内部 token 守卫
   │
   ├─→ multi_export.py          小红书 3:4 / 抖音 9:16 / 视频号 ≤60s
   │
   └─→ generate_caption.py      标题 + 200-500 字正文 + 3-6 tags + 发布时段建议
```

> **适用场景**：daily 短视频、口播为主的内容（创业/AI/职场/效率/Vlog）、要发小红书/抖音/视频号
> **不适用**：电影感剪辑、纯音乐 MV、需要精细 keyframe 控制的特效视频

---

## 60 秒上手

```bash
# 1. 装好依赖（macOS Apple Silicon 为例）
brew install ffmpeg
pip install mlx-whisper Pillow

# 2. 克隆
git clone https://github.com/maxazure/video-editing-skill ~/projects/video-editing-skill
cd ~/projects/video-editing-skill

# 3. 环境自检（应该全 ✅ 或 ⚠️ 可选项）
python3 scripts/utils.py

# 4. 跑一遍测试套件确认 OK
pytest tests/           # 151 个测试，应该 <2 秒
```

每天做一条视频的完整模板：**[docs/prompts/15-xhs-daily-tech-video.md](docs/prompts/15-xhs-daily-tech-video.md)**

---

## 安装

### 必装

| 依赖 | 用途 | 装法 |
|---|---|---|
| `ffmpeg` | 一切视频/音频处理 | macOS: `brew install ffmpeg` · Linux: `apt install ffmpeg` |
| `python3` ≥3.10 | 跑脚本 | 系统 / pyenv / brew |
| Whisper | 语音识别 | 见下表 |

### Whisper 引擎（按平台选一种）

| 平台 | 推荐引擎 | 安装命令 |
|---|---|---|
| **Apple Silicon (M1/M2/M3/M4)** | `mlx-whisper` | `pip install mlx-whisper` |
| **NVIDIA GPU (CUDA)** | `faster-whisper` | `pip install faster-whisper` |
| **Intel / AMD / CPU only** | `faster-whisper` (CPU) | `pip install faster-whisper` |
| **后备** | `openai-whisper` | `pip install openai-whisper` |

中国用户走清华镜像：
```bash
pip install mlx-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple
```

NVIDIA GPU 配置详见本文末尾的 [Linux GPU 配置](#linux-gpu-配置) 段。

### 可选

| 依赖 | 启用 | 缺时回落 |
|---|---|---|
| `Pillow` | 章节卡 PNG 渲染（auto_chapter_cards） | 不能跑章节卡 |
| `librosa` | BGM 真实节拍检测 | 用 120 bpm 固定网格 |
| `pyyaml` | profile YAML 读取加速 | 用内置 fallback parser |
| `spacy + zh_core_web_sm` | 高级 B-roll 命名实体识别（V3.2+ 路线图） | 用关键词列表匹配 |

### AI 图像生成（gpt-image-2）

| 运行环境 | 路径 | 凭证 |
|---|---|---|
| **Codex CLI**（推荐） | 用 Codex 内置 `imagegen` 工具，自动路由 gpt-image-2 | **无需** OpenAI API key |
| **Claude Code / 其他** | 用 OpenAI Python SDK（`openai.images.generate`），或任何能调 gpt-image-1.5/2 的工具 | 需要 `OPENAI_API_KEY` |

本 skill 只负责**产出提示词**（`imagegen_hint.py`）+ **提供模板库**（`prompts/imagegen_templates.yaml`）—— 不内置 OpenAI 客户端。在 Codex 里 agent 直接调内置 `imagegen`；其他环境用户自行接入。

完整规则详见 [docs/prompts/19-imagegen.md](docs/prompts/19-imagegen.md)。

---

## V3 核心能力

### 🛡️ Content Guard — 平台雷区 lint
[`scripts/content_guard.py`](scripts/content_guard.py) · [详细文档](docs/prompts/16-content-guard.md)

80+ 条 regex 检查 4 类硬性违规 + 3 类软性警告：

| 级别 | 类别 | 例子 |
|---|---|---|
| 🚫 HARD | 广告法极限词 | 最 / 第一 / 唯一 / 万能 / 全网最低 / 遥遥领先 |
| 🚫 HARD | 导流外站 | 微信 / VX / wx / +V / 加微 / QQ / 手机号 / 抖音 / 二维码 |
| 🚫 HARD | 医美/医疗 | 治愈 / 根治 / 祛斑 / 抗衰 / 水光针 / 热玛吉 / 医生同款 |
| 🚫 HARD | 财富诱导 | 年入 X 万 / 躺赚 / 财富自由 / 稳赚不赔 / 零成本 / 暴利 |
| ⚠️ SOFT | 标题/正文 | 标题 >20 字、`!!!` 连用、emoji 占比 >30%、正文 >800 字 |

被 `render_final.py` / `rewrite_script.py` / `generate_caption.py` 自动调用——HARD 违规导出退出码 2。

### 📖 Story Engine — 让 AI 按小红书爆款公式重组
[`scripts/rewrite_script.py`](scripts/rewrite_script.py) · [hook 模板](scripts/prompts/hook_templates.yaml) · [CTA 模板](scripts/prompts/cta_templates.yaml)

- **8 个钩子模板**：反常识、痛点共鸣、数字成绩、悬念问句、身份标签、反差对比、利益承诺、场景代入
- **5 个 CTA 模板**：按小红书 CES 权重（关注 8 > 评论/分享 4 > 收藏/点赞 1）排序
- **3 种故事结构**：`pain_solve`（干货）/ `story_reversal`（故事）/ `listicle`（盘点）

不绑定任何 LLM 提供商——脚本输出 prompt，你喂给 Claude / ChatGPT，把返回 JSON 喂回脚本验证 + 物化为 `clean_script.md`。

### 🎬 Auto-Enrich — 自动加 B-roll / 章节卡 / 贴纸 / 卡点
[详细文档](docs/prompts/18-auto-enrich.md)

| 模块 | 触发逻辑 |
|---|---|
| [`auto_broll.py`](scripts/auto_broll.py) | 转折词（但是/然而/关键是/重点来了）/ 实体匹配素材库 / 长镜头守卫 |
| [`auto_chapter_cards.py`](scripts/auto_chapter_cards.py) | `## ` 章节标题 / 静音 ≥1.5s 边界 / Pillow PNG 渲染 |
| [`beat_sync.py`](scripts/beat_sync.py) | librosa beat_track + ±200ms snap（缺时回落固定网格） |
| [`auto_stickers.py`](scripts/auto_stickers.py) | 情绪关键词→emoji 池（excited 🚀✨🔥 / doubt 🤔 / data 📈 等） |
| [`auto_enrich.py`](scripts/auto_enrich.py) | 编排上面四个，输出综合 plan JSON（含 imagegen cues） |

### 🎨 AI 图像生成（gpt-image-2 / Codex imagegen）
[`scripts/imagegen_hint.py`](scripts/imagegen_hint.py) · [`scripts/prompts/imagegen_templates.yaml`](scripts/prompts/imagegen_templates.yaml) · [详细文档](docs/prompts/19-imagegen.md)

抽象概念（注意力机制 / 复利 / 信息茧房 / 长尾效应 …）自动检测 + 适配 **gpt-image-2** 七槽位提示词结构。

- **Codex 环境**：检测到的 prompt 直接喂给 Codex 内置 `imagegen` 工具——**无需** OpenAI API key，Codex 自动路由到 gpt-image-2
- **其他环境**：用 OpenAI Python SDK 自己接（`openai.OpenAI().images.generate(...)`，需 `OPENAI_API_KEY`）。本 skill 只产 prompt，不内置客户端
- **内置 7 个 sample**：注意力机制 / 信息茧房 / 复利 / 长尾效应 / 数据柱状图 / 章节标题卡 / 早晨笔记本 B-roll（每个都带双语 prompt + why-it-works）
- **5 个 structure 槽位**：chapter_background / chapter_title_card / broll_fallback / data_visualization / abstract_concept
- **gpt-image-2 规则全部编码**：引号 = 精确文字渲染、约束写进 prose（无 negative-prompt 字段）、具体相机+光圈+光照（避免 "AI 味"）、默认拒绝人脸/人手特写、中文标题不走 gpt-image-2

### 🎚️ 渲染层（V3 强化）
[`scripts/render_final.py`](scripts/render_final.py)

| 默认行为 | 触发命令 / 配置 |
|---|---|
| Heavy 字幕字体（Source Han Sans Heavy / STHeiti Medium） | `find_chinese_font()` 自动选 |
| 响度规范化 `dynaudnorm + acompressor + loudnorm` | 默认开启，`--no-loudnorm` 关 |
| 速度直接生效（不留 1.0× 副本） | `--primary-speed 1.25` |
| 受众档位预设（节奏/字幕密度/BGM 增益） | `--profile tech_pro` |
| 内部 token 拦截 | 自动；任何 `1.25x`/`mlx-whisper`/`loudnorm` 出现在画面文本字段都退出 |
| 平台 lint | 自动；`--no-content-guard` 关 |
| 字幕风格 | `--subtitle-style normal/karaoke/bold_pop/neon/minimal/yellow_pop` |

### 📦 多平台导出
[`scripts/multi_export.py`](scripts/multi_export.py) · [详细文档](docs/prompts/17-multi-platform.md)

| 平台 | 尺寸 | 时长 | 说明 |
|---|---|---|---|
| 小红书 / RED | 1080×1440 (3:4) | — | 占满 feed 缩略图 (+40% 显示面积) |
| 抖音 / TikTok | 1080×1920 (9:16) | — | 全屏沉浸 |
| 微信视频号 | 1080×1920 (9:16) | ≤60s | 自动截断；社交链分发 |

### ✍️ Caption Generator — 标题 + 正文 + 标签
[`scripts/generate_caption.py`](scripts/generate_caption.py)

无 LLM 依赖，纯规则：
- 标题 ≤18 字，前 18 字含 2 个 TF-IDF 关键词
- 正文 200-500 字，每 ~60 字一个 emoji（`📌✨💡🔥👇✅🚀📈`）
- 3-6 个 # tag，混合垂类 + 长尾（避免纯热词堆叠被判搬运）
- 发布时段建议来自所选 audience profile

### 👤 受众 Profile
[`scripts/profiles/`](scripts/profiles/)

预设镜头节奏、字幕密度、BGM 增益、目标比例：

- `tech_pro` — AI/创业/效率向（90s 默认，每 2.5s 切镜，BGM -16dB，3:4 小红书首选）
- `lifestyle` — vlog/穿搭/家居向（60s 默认，每 2.0s 切镜，BGM -10dB）

字体预设（5 套）在 `profiles/_fonts.yaml`：得意黑 / 阿里妈妈数黑体 / 阿里妈妈方圆体 / 思源黑体 Heavy / 奶酪体。

---

## 日常工作流

完整命令链见 [**docs/prompts/15-xhs-daily-tech-video.md**](docs/prompts/15-xhs-daily-tech-video.md)。

简化版（每天替换 `<NN>` 和 `<主题>`）：

```bash
DAY=NN
WORK=~/Movies/xiaohongshu/day$DAY
SKILL=~/projects/video-editing-skill

# 1. 转写
python3 $SKILL/scripts/transcribe.py $WORK/origin/voice.mp3 \
  --word-timestamps --detect-fillers

# 2. 重组（手动喂 prompt 给 LLM，落地 JSON 后回放）
python3 $SKILL/scripts/rewrite_script.py \
  --transcript $WORK/work/transcript.json --emit-prompt > $WORK/work/prompt.md
# ...LLM 输出 work/llm.json 后...
python3 $SKILL/scripts/rewrite_script.py \
  --transcript $WORK/work/transcript.json \
  --llm-output $WORK/work/llm.json \
  --output $WORK/work/clean_script.md

# 3. 自动丰富（plan 里会有 broll / stickers / chapter_cards / imagegen 四列）
python3 $SKILL/scripts/auto_enrich.py \
  --transcript $WORK/work/transcript.json \
  --clean-script $WORK/work/clean_script.md \
  --bgm $WORK/origin/bgm.mp3 \
  --output $WORK/work/enrich_plan.json

# 3b. 如果 imagegen[] 非空，在 Codex 里直接调内置 imagegen 工具
#     把每条 prompt_en 用 imagegen 生成 1024x1536，存到 $WORK/work/imagegen/
#     不需要 OPENAI_API_KEY；详见 docs/prompts/19-imagegen.md

# 4. 渲染
python3 $SKILL/scripts/render_final.py \
  --config $WORK/work/render_config.json \
  --profile tech_pro \
  --primary-speed 1.25 \
  --subtitle-style karaoke \
  --output $WORK/output/day${DAY}_master.mp4

# 5. 多平台
python3 $SKILL/scripts/multi_export.py \
  $WORK/output/day${DAY}_master.mp4 --output-dir $WORK/output/

# 6. 文案
python3 $SKILL/scripts/generate_caption.py \
  --script $WORK/work/clean_script.md --profile tech_pro \
  --output $WORK/output/day${DAY}_caption.json
```

---

## 测试

```bash
pytest tests/           # 151 测试，<2 秒
```

按模块跑：
```bash
pytest tests/test_content_guard.py -v       # 80+ 规则的 38 个测试
pytest tests/test_rewrite_script.py -v      # Story Engine
pytest tests/test_auto_broll.py -v          # B-roll 调度
pytest tests/test_multi_export.py -v        # 多平台比例转换
pytest tests/test_generate_caption.py -v    # 文案合成
pytest tests/test_imagegen_hint.py -v       # gpt-image-2 提示词检测
```

---

## 提示词教程

| # | 主题 | 何时用 |
|---|---|---|
| 01 | [口播素材处理](docs/prompts/01-oral-broadcast.md) | 第一次入门，完整 V2 流程 |
| 06 | [多平台导出（V2 版）](docs/prompts/06-multi-platform.md) | 简易多比例（V3 推荐看 17） |
| 14 | [导出剪映/CapCut](docs/prompts/14-export-capcut.md) | 想在剪映里继续手工调 |
| **15** | **[V3 完整流水线](docs/prompts/15-xhs-daily-tech-video.md)** | **每天做一条小红书视频 — 推荐入口** |
| **16** | **[Content Guard](docs/prompts/16-content-guard.md)** | **担心标题/正文限流** |
| **17** | **[三平台导出](docs/prompts/17-multi-platform.md)** | **一次发小红书/抖音/视频号** |
| **18** | **[Auto-Enrich](docs/prompts/18-auto-enrich.md)** | **想让视频更"有质感"** |
| **19** | **[AI 生图（gpt-image-2 / Codex imagegen）](docs/prompts/19-imagegen.md)** | **抽象概念自动配图** |

完整列表见 [docs/prompts/README.md](docs/prompts/README.md)。

---

## 平台支持

| 平台 | Whisper | 编码器 | 备注 |
|---|---|---|---|
| **macOS Apple Silicon** | mlx-whisper (Metal) | VideoToolbox | 主开发平台。large-v3-turbo 推荐 |
| macOS Intel | faster-whisper (CPU) | VideoToolbox | medium 模型推荐 |
| Linux + NVIDIA | faster-whisper (CUDA) | NVENC | RTX 40 系直通；50 系需 float16 |
| Linux + Intel Arc | faster-whisper (CPU) | QSV | iGPU/Arc 都走 QSV |
| WSL | faster-whisper | NVENC（如有） | Windows 字体自动从 /mnt/c |
| Windows | faster-whisper | QSV/AMF | 推荐 WSL2 |

中国用户：自动检测中国 locale，pip 走清华镜像、HuggingFace 走 hf-mirror.com，也可 `--mirror` 强制启用。

---

## 架构

每个脚本一个明确职责。没有"god script"。

```
scripts/
├── utils.py                    平台/字体/编码器自检
├── _internal_text_guard.py     内部 token 拦截器
├── transcribe.py               Whisper 转写
├── extract_audio.py            音频提取
├── split_video.py              按句切片（V2 兼容）
├── media_library.py            素材库索引（CLIP-ready）
├── merge_clips.py              合并片段（V2 兼容）
├── content_guard.py            平台雷区 lint                   [V3]
├── rewrite_script.py           Story Engine                    [V3]
├── auto_broll.py               B-roll 调度                      [V3]
├── auto_chapter_cards.py       章节卡渲染                       [V3]
├── beat_sync.py                BGM 卡点                         [V3]
├── auto_stickers.py            情绪→贴纸                        [V3]
├── imagegen_hint.py            抽象概念→gpt-image-2 提示词       [V3]
├── auto_enrich.py              丰富度编排                       [V3]
├── render_final.py             单次编码渲染（V3 强化）
├── burn_subtitles.py           字幕 ASS 生成
├── generate_cover.py           封面生成
├── generate_cover_image.py     Chrome-rendered 封面
├── add_chapter_bar.py          章节进度条
├── export_capcut.py            剪映工程导出
├── generate_standup_timeline.py Remotion timeline
├── multi_export.py             三平台导出                       [V3]
├── generate_caption.py         标题/正文/标签                   [V3]
├── prompts/
│   ├── hook_templates.yaml     8 钩子模板                       [V3]
│   ├── cta_templates.yaml      5 CTA 模板                       [V3]
│   └── imagegen_templates.yaml gpt-image-2 提示词模板 + 7 sample [V3]
└── profiles/
    ├── __init__.py             加载器                           [V3]
    ├── tech_pro.yaml           AI/创业 profile                   [V3]
    ├── lifestyle.yaml          vlog profile                      [V3]
    └── _fonts.yaml             5 套字体预设                     [V3]
```

实施记录：[docs/plans/2026-05-17-v3-xhs-improvements.md](docs/plans/2026-05-17-v3-xhs-improvements.md)

---

## Linux GPU 配置

### NVIDIA 40 系（RTX 4060/4070/4080/4090）

开箱即用，CUDA 12.4+ + 驱动 535+：

```bash
sudo apt install nvidia-driver-535 nvidia-cuda-toolkit
pip install faster-whisper
nvidia-smi   # 验证
```

### NVIDIA 50 系（RTX 5070/5080/5090）

需要最新 CUDA + 强制 float16 防止 INT8 cuBLAS 报错。`scripts/utils.py` 自动检测 50 系列并使用 float16 精度。

### Intel Arc / iGPU

走 QSV 编码：
```bash
pip install faster-whisper   # CPU 模式跑 Whisper
ffmpeg -hwaccels  # 应该列出 qsv
```

详细分卡指南：`python3 scripts/utils.py` 会按你的硬件给具体提示。

---

## 贡献

V3.2+ 路线图后续可能加：

- 自动接 `enrich_plan.json`（含 broll / chapter / sticker / imagegen cues）到 `render_final.py`——目前还要手工塞 cues
- spaCy 中文 NER → 更精准的 B-roll 实体匹配（升级当前的关键词列表）
- CLIP embedding 跨段比对 → 自动匹配最贴合段落内容的素材
- librosa real beat detection 作为默认（当前回落到 120 bpm 固定网格）
- zxing-cpp QR 码扫描 + 外站 logo OCR → 画面级 Content Guard
- gpt-image-2 character anchor 一致性（多张图同一人物形象保持一致）

V3 已完成：Phase 1-5 + imagegen 集成（[#9](https://github.com/maxazure/video-editing-skill/pull/9) [#11](https://github.com/maxazure/video-editing-skill/pull/11) [#12](https://github.com/maxazure/video-editing-skill/pull/12) [#13](https://github.com/maxazure/video-editing-skill/pull/13) [#14](https://github.com/maxazure/video-editing-skill/pull/14) [#15](https://github.com/maxazure/video-editing-skill/pull/15) [#16](https://github.com/maxazure/video-editing-skill/pull/16)）。

PR 欢迎。新功能必须带测试，每个新脚本至少 5 个测试，全套 <2 秒跑完。

---

## License

MIT.

---

_BestAI Labs · 2026_
