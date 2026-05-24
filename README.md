# Video Editing Skill — 视频剪辑技能

把 5-15 分钟的原始口播音频 + 一堆无声素材，变成可发布的多平台短视频。

适配 **小红书 / 抖音 / 微信视频号** 三大主流平台，按各平台的算法、比例、时长、审核规则调过参。是一条 **从口播 → 重组故事 → 平台守门 → 自动丰富 → 渲染 → 三平台导出 → 标题文案** 的端到端流水线，不是另一个剪辑工具。

```
口播音频 + 无声素材
   │
   ├─→ transcribe.py            转写 + 词级时间戳 + 口误标记
   │                            (mlx-whisper / faster-whisper / openai-whisper)
   │
   ├─→ rough_cut.py             ASR 粗剪 → 去纯口头禅 / 相邻重复句
   │                            输出可审计 cut list，可选单次 concat 渲染
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
   ├─→ storyboard_plan.py       transcript/clean_script → shot cards
   │                            生成路由 / 连续性锚点 / Dreamina 额度提醒
   │
   ├─→ storyboard_assets.py     shot cards → 素材任务清单 / ready 预检
   │                            imagegen / Dreamina / motion / broll 状态表
   │
   ├─→ screen_focus.py          录屏点击/热点 → focus_events 聚焦计划
   │                            render_final 自动放大、标记、标签
   │
   ├─→ jump_cut.py              自适应静音检测 → cut list → 去停顿成片
   │     └─→ timeline_view.py   切点 filmstrip + waveform 人工复核图
   │
   ├─→ render_final.py          单次编码渲染 + enrich_plan 自动接入
   │     B-roll / 章节卡 / 贴纸 / 生成图 / 点击聚焦 overlay + Heavy 字幕 + 响度规范化
   │     可选 --versioned-output：输出 _V<N>，避免覆盖旧成片
   │
   ├─→ render_qa.py             渲染后黑屏/静帧/静音/尺寸质检
   │     └─→ timeline_view.py   QA 可疑区间可视化复盘
   │
   ├─→ export_edl.py            render_config / cut list → EDL + manifest
   │                            交给 Premiere / Final Cut Pro / Resolve
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
pytest tests/           # 208 个测试，约 3 秒
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

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

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

`render_final.py --enrich-plan work/enrich_plan.json` 会把 plan 里的 B-roll、章节卡、贴纸和已生成图片 cue 自动接回单次渲染；`--enrich-plan` 可重复传入，用来叠加 `screen_focus_plan.json` 这类独立计划。没有实际文件的 imagegen cue 会保留为提示，不会阻塞导出。

### 🎞️ Storyboard Plan — 分镜与生成路由
[`scripts/storyboard_plan.py`](scripts/storyboard_plan.py) · [`scripts/storyboard_assets.py`](scripts/storyboard_assets.py) · [分镜文档](docs/prompts/24-storyboard-plan.md) · [素材清单文档](docs/prompts/25-storyboard-assets.md)

借鉴 GitHub 上视频生成类项目的 storyboard / shot continuity / provider routing 思路，但保持本项目的轻量原则：脚本只做本地规划，不提交任何付费生成任务。

| 输出 | 说明 |
|---|---|
| `storyboard_plan.json` | 每个 shot 的时间码、source segments、section、narration、keywords、visual first/motion/last frame |
| `generation_route` | `codex_imagegen` / `dreamina_video` / `remotion_hyperframes` / `media_library_broll` + fallback + why |
| `continuity.anchors` | 系列色彩、比例、字幕安全区、上一镜头引用、关键词线索 |
| `storyboard_plan.md` | 适合人工 review 的 shot cards，含 prompt 和检查项 |
| `storyboard_assets.json` | 每个 shot 对应素材是否 ready、需要生成/审批/渲染/搜索 |

常用：
```bash
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md \
  --max-shots 8 \
  --target-aspect 9:16

python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md
```

路由规则：抽象概念优先 `codex_imagegen`；数字/指标优先 `remotion_hyperframes`；动作/场景变化推荐 `dreamina_video` 但只标记为需确认，因为 Dreamina/即梦生成可能消耗 credits；其他先走本地素材库 B-roll。`storyboard_assets.py --strict` 会在素材未 ready 时返回退出码 2，适合渲染前拦截。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

### 🔍 Screen Focus — 录屏点击聚焦
[`scripts/screen_focus.py`](scripts/screen_focus.py) · [详细文档](docs/prompts/28-screen-focus.md)

借鉴 Screen Studio/Recordly/JianYing 类工具里的自动点击放大体验，但保持本项目的轻量方式：不录屏、不申请桌面权限，只把手工或工具导出的点击/热点事件转成可审计 `focus_events` enrich plan。

常用：
```bash
python3 scripts/screen_focus.py \
  --events work/clicks.json \
  --screen-width 1920 \
  --screen-height 1080 \
  --output work/screen_focus_plan.json \
  --markdown work/screen_focus_plan.md

python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/screen_focus_plan.json \
  --output output/tutorial_master.mp4
```

`focus_events[]` 支持像素或 0-1 坐标、`duration`、`zoom`、`transition`、`marker_color` 和 `label`；`render_final.py` 会在对应时间段淡入放大裁切画面，并把 label 合并为 timed badge，适合软件教程、产品演示和操作录屏。

### ✂️ ASR Rough Cut — 自动去口头禅/重复句
[`scripts/rough_cut.py`](scripts/rough_cut.py) · [详细文档](docs/prompts/26-rough-cut.md)

借鉴 FireRed-OpenStoryline 的 ASR speech rough cut 思路，但保持本项目的本地可审计方式：不调用 LLM，直接利用 `transcribe.py --detect-fillers` 的结果和相邻 transcript 相似度，先输出计划，再选择是否渲染。

| 能力 | 说明 |
|---|---|
| 纯口头禅移除 | 读取 `filler_words[].is_filler_only`，也能用内置中英文 filler 词表兜底 |
| 相邻重复句检测 | 用归一化文本相似度识别口误重说，默认保守阈值 `0.88` |
| 可审计计划 | 输出 `decisions` / `removed_segments` / `keep_segments` / `speedup_ratio` |
| 单次编码渲染 | 复用 `jump_cut.py` 的 concat 渲染命令，不产生多代中间文件 |

常用：
```bash
python3 scripts/rough_cut.py --transcript work/transcript.json --cut-list work/rough_cut.json
python3 scripts/rough_cut.py --transcript work/transcript.json --input origin/talking.mp4 --output output/talking.roughcut.mp4 --cut-list work/rough_cut.json
python3 scripts/timeline_view.py origin/talking.mp4 --cut-list work/rough_cut.json --output-dir output/verify/rough_cut
```

### ✂️ Jump Cut — 自动去停顿
[`scripts/jump_cut.py`](scripts/jump_cut.py) · [详细文档](docs/prompts/21-jump-cut.md)

借鉴视频生成/剪辑类 skill 里常见的 `remove_silence / jumpcut` 闭环，但默认先产出可审计 cut list，避免直接误切人声：

| 能力 | 说明 |
|---|---|
| 自适应阈值 | 先跑 `loudnorm=print_format=json`，用 `input_thresh` 作为 `silencedetect` 阈值 |
| 可审计 cut list | 输出 `detected_silences` / `removed_segments` / `keep_segments` / `speedup_ratio` |
| 安全 padding | 默认每个切点保留 0.08s，避免咬字被切掉 |
| 单次编码渲染 | 用 `trim/atrim + concat` 一次输出，不产生中间重编码文件 |

常用：
```bash
python3 scripts/jump_cut.py input/talking.mp4 --dry-run --cut-list output/talking.jumpcut.json
python3 scripts/timeline_view.py input/talking.mp4 --cut-list output/talking.jumpcut.json --output-dir output/verify/cuts
python3 scripts/jump_cut.py input/talking.mp4 --output output/talking.jumpcut.mp4 --cut-list output/talking.jumpcut.json
```

### 🔎 Timeline View — 切点/可疑区间复盘图
[`scripts/timeline_view.py`](scripts/timeline_view.py) · [详细文档](docs/prompts/22-timeline-view.md)

借鉴视频剪辑类 skill 的 `timeline_view` 工作台：在跳切前后或 QA 报警区间生成一张 PNG，上半部分是 filmstrip，下半部分是 waveform，方便快速判断“切点是否咬字、画面是否突跳、静音是否自然”。

常用：
```bash
python3 scripts/timeline_view.py output/day58_master.mp4 --at 42.5 --radius 1.5 --output output/verify/42_5s.png
python3 scripts/timeline_view.py origin/talking.mp4 --cut-list work/jumpcut.json --output-dir output/verify/cuts --limit 12
```

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
| 自动丰富接入 | `--enrich-plan work/enrich_plan.json`，可重复传入 |
| 点击聚焦 | `--enrich-plan work/screen_focus_plan.json`，读取 `focus_events[]` |
| 版本化输出 | `--versioned-output` 或 config `"versioned_output": true` |

### 🧾 Versioned Output — 成片不覆盖旧版本
[`scripts/render_final.py`](scripts/render_final.py) · [详细文档](docs/prompts/23-versioned-output.md)

借鉴 GitHub 上视频技能的“每次渲染保留新版本”工作流：`--versioned-output` 会把请求的 `output/day58_master.mp4` 写到下一个 `output/day58_master_V<N>.mp4`，避免 `ffmpeg -y` 覆盖上一版成片。`--formats` 会跟随实际版本文件生成 `day58_master_V3_vertical.mp4` 这类多比例输出。

常用：
```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output output/day58_master.mp4 \
  --versioned-output \
  --formats vertical horizontal
```

配置式开启：
```json
{
  "versioned_output": true,
  "clips": [
    {"video": "origin/talking.mp4", "segment_id": 1, "transcript": "work/transcript.json"}
  ]
}
```

### 🧭 NLE Handoff — EDL 导出
[`scripts/export_edl.py`](scripts/export_edl.py) · [详细文档](docs/prompts/27-export-edl.md)

借鉴自动剪辑/生成类项目常见的“先产 timeline，再交给专业剪辑软件继续精修”工作流：`export_edl.py` 可把本项目的 `render_config.json` 或 `rough_cut.py` / `jump_cut.py` 产生的 `keep_segments` 导出成单轨 CMX 3600 风格 EDL，同时写一个 JSON manifest 保留绝对源路径和精确秒数。

常用：
```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/day58_edit.edl \
  --fps 30 \
  --title DAY58_EDIT

python3 scripts/export_edl.py \
  --cut-list work/rough_cut.json \
  --output work/rough_cut.edl \
  --fps 30
```

适合把自动粗剪交给 Premiere / Final Cut Pro / DaVinci Resolve 做调色、混音、精剪或协作复核。复杂字幕、overlay、章节卡和 B-roll 仍以 `render_final.py` / `export_capcut.py` 为准。

### 2026-05-25 自动化升级记录

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`luoluoluo22/jianying-editor-skill`](https://github.com/luoluoluo22/jianying-editor-skill) | 剪映自动化覆盖录屏、智能变焦、红圈提示这类教程视频高频需求 | 新增本地 `screen_focus.py`，不依赖剪映桌面控制 |
| [`njraladdin/screen-demo`](https://github.com/njraladdin/screen-demo) | Screen Studio 替代品，强调录屏后的 zoom animation、cursor tracking、背景包装 | 新增点击/热点 → zoom cue 的可审计计划 |
| [`webadderall/Recordly`](https://github.com/webadderall/Recordly) | 自动 zoom suggestions、cursor polish、styled frame，面向产品 walkthrough | `focus_events[]` 可叠加到现有 enrich-plan 渲染链路 |
| [`Itz-Hex/hypr-obs-mouse-follow`](https://github.com/Itz-Hex/hypr-obs-mouse-follow) | OBS 录制时跟随鼠标并平滑放大，适合教程录屏 | 本项目改为后期渲染时裁切放大，避免录制端绑定 |

新增/调整能力：`scripts/screen_focus.py` 可读取 JSON/CSV/inline 点击事件，把像素或 0-1 坐标标准化为 `screen_focus_plan.v1`，输出 `focus_events[]` 和 Markdown 复核表；`render_final.py --enrich-plan` 现在可重复传入，并能把 `focus_events` 转成 timed zoom crop、提示框和可选 label badge。

使用方式：先跑 `python3 scripts/screen_focus.py --events work/clicks.json --screen-width 1920 --screen-height 1080 --output work/screen_focus_plan.json --markdown work/screen_focus_plan.md`；渲染时追加 `--enrich-plan work/screen_focus_plan.json`，或和 `work/enrich_plan.json` 一起重复传入。

验证结果：新增 `tests/test_screen_focus.py` 6 项通过；相关回归 `tests/test_render_enrich_plan.py` 5 项通过；完整 `.venv/bin/python -m pytest tests -q` 通过 `208 passed in 3.10s`；`python3 -m compileall scripts tests` 通过；`git diff --check` 通过；inline `--event` smoke 输出了有效 `screen_focus_plan.v1`，2 秒合成视频实测 `focus_events` 可成功渲染为有效 MP4。

### 2026-05-21 自动化升级记录

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`hoodini/ai-agents-skills`](https://github.com/hoodini/ai-agents-skills) 的 Yuv-Viral-Video | 明确要求每次输出 `_V<N>`，旧成片不被覆盖 | 新增 `render_final.py --versioned-output` |
| [`video-db/skills`](https://github.com/video-db/skills) | “See → Understand → Act”、搜索/编辑/导出一体化 | 已有 transcribe/enrich/render/QA 链路，暂不引入外部服务 |
| [`higgsfield-ai/skills`](https://github.com/higgsfield-ai/skills) | 生成后评分与 branded video mode | 本项目已有平台 lint 与 caption 规则，后续可加 hook/retention 评分 |
| [`smixs/visual-skills`](https://github.com/smixs/visual-skills) | 视频生成强调 shot card、连续性与模型路由 | 本项目已有 gpt-image-2 路由；视频生成路由保持 Dreamina/即梦外部 skill |

新增/调整能力：`next_versioned_output_path()` 会扫描同目录已有 `*_V<N>.mp4`，自动选下一个版本；CLI 增加 `--versioned-output`，配置文件支持 `"versioned_output": true`；多平台 `--formats` 改为基于实际版本主文件导出。

使用方式：在最终渲染命令加 `--versioned-output`，或在 render config 写入 `"versioned_output": true`。

验证结果：新增/相关测试 12 项通过；完整 `.venv/bin/python -m pytest tests -q` 通过 180 项；`python3 -m compileall scripts tests` 通过；真实 1 秒 ffmpeg 合成验证了 `master.mp4` 会输出为 `master_V1.mp4`。

### 2026-05-22 自动化升级记录

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | provider scoring、pipeline manifest、decision log、post-render gates | 新增本地 shot-card 路由，不直接接云端 provider |
| [`HKUDS/ViMax`](https://github.com/HKUDS/ViMax) | shot-level storyboard、first/last frame、motion description、continuity | 新增 first/motion/last frame 和 continuity anchors |
| [`trilogy-group/ttv-pipeline`](https://github.com/trilogy-group/ttv-pipeline) | keyframe/chaining mode、长视频分段、backend fallback | 新增 route fallback 与 Dreamina 额度提醒 |
| [`vericontext/vibeframe`](https://github.com/vericontext/vibeframe) | brief → storyboard/design → validate/build 的 agent-native 项目循环 | 新增 `storyboard_plan.md` 供 agent/human review |
| [`dseditor/AI-storyboard-generator`](https://github.com/dseditor/AI-storyboard-generator) | cut count、图片/视频重生成、ComfyUI 工作流配置 | 新增 `--max-shots` 和每镜头 prompt card |
| [`Forget-C/Jellyfish`](https://github.com/Forget-C/Jellyfish) | shot preparation、候选资产确认、统一 readiness state、任务状态 | 新增 `storyboard_assets.py` 素材 readiness manifest |
| [`samagra14/mediagateway`](https://github.com/samagra14/mediagateway) | 多 provider 状态、gallery 管理、成本统计 | 新增 `paid_credit_tasks` 与 `needs_approval` 状态 |
| [`aaurelions/vidosy`](https://github.com/aaurelions/vidosy) | JSON 驱动视频结构与 media assets 约定 | 新增 `work/imagegen` / `work/generated_video` / `work/motion` 默认路径 |

新增/调整能力：`scripts/storyboard_plan.py` 可把 `transcript.json` 和可选 `clean_script.md` 转为 `storyboard_plan.json` / `storyboard_plan.md`，为每个 shot 标注时间码、叙事段落、画面语言、生成路由、fallback、连续性锚点和 review checks；`scripts/storyboard_assets.py` 可把分镜转成素材 readiness manifest，标出 `ready` / `candidate_found` / `needs_generation` / `needs_approval` / `needs_render` / `search_needed`，其中 `dreamina_video` 只做规划并明确提示可能消耗 credits。

使用方式：先跑 `python3 scripts/storyboard_plan.py --transcript work/transcript.json --clean-script work/clean_script.md --output work/storyboard_plan.json --markdown work/storyboard_plan.md --max-shots 8 --target-aspect 9:16`，再跑 `python3 scripts/storyboard_assets.py --storyboard-plan work/storyboard_plan.json --asset-root work --output work/storyboard_assets.json --markdown work/storyboard_assets.md --strict` 做渲染前素材预检。

验证结果：新增 `tests/test_storyboard_plan.py` 5 项和 `tests/test_storyboard_assets.py` 5 项通过；`.venv/bin/python -m pytest tests/test_storyboard_assets.py tests/test_storyboard_plan.py -q` 通过 10 项；完整 `.venv/bin/python -m pytest tests -q` 通过 `190 passed in 1.51s`；`python3 -m compileall scripts tests` 通过。

### 2026-05-23 自动化升级记录

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`FireRedTeam/FireRed-OpenStoryline`](https://github.com/FireRedTeam/FireRed-OpenStoryline) | ASR speech rough cut：按时间戳去口头禅、卡壳和重复表达，并把结果交给后续 timeline | 新增本地 `rough_cut.py`，用 transcript/filler metadata 生成可审计 cut list |
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) | 自动剪辑输出可交换时间线，强调先生成 timeline 再渲染/交给 NLE | `rough_cut.py` 先产 `decisions` / `keep_segments`，再可选渲染 |
| [`AcademySoftwareFoundation/OpenTimelineIO`](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | editorial timeline interchange 与 adapter 生态 | 本次暂不引入 OTIO 依赖，保留 JSON cut list 作为轻量交换层 |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | pipeline artifact / review gate / tool contract | 新增 rough cut 计划里的 `review_hint`，继续走 timeline_view 人工复核 |

新增/调整能力：`scripts/rough_cut.py` 可读取 `transcript.json`，自动移除纯口头禅片段和相邻重复句，输出 `rough_cut.json`，其中包含每个删除决策、合并后的移除区间、保留区间、预计输出时长和节奏压缩比例；传入 `--input/--output` 时可直接复用现有 concat 渲染能力。

使用方式：先跑 `python3 scripts/transcribe.py origin/voice.wav --language zh --word-timestamps --detect-fillers`，再跑 `python3 scripts/rough_cut.py --transcript work/transcript.json --cut-list work/rough_cut.json` 审查计划；确认后用 `python3 scripts/rough_cut.py --transcript work/transcript.json --input origin/talking.mp4 --output output/talking.roughcut.mp4 --cut-list work/rough_cut.json` 渲染。

验证结果：新增 `tests/test_rough_cut.py` 5 项通过；`python3 -m compileall scripts tests` 通过；完整 `.venv/bin/python -m pytest tests -q` 通过 `195 passed in 1.42s`；`docs/prompts/26-rough-cut.md` 记录完整使用方式。

### 2026-05-24 自动化升级记录

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) | 自动剪辑后可导出 Premiere / Resolve / Final Cut Pro / Shotcut / Kdenlive 等时间线 | 新增本地 EDL handoff，不改变现有渲染链路 |
| [`AcademySoftwareFoundation/OpenTimelineIO`](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | editorial timeline interchange，支持 FCP XML / AAF / CMX 3600 EDL 等 adapter 生态 | 不引入重依赖，先实现单轨 CMX 3600 风格 EDL + manifest |
| [`Memories-ai-labs/vea-open-source`](https://github.com/Memories-ai-labs/vea-open-source) | agent 产出 FCPXML，并可交给 DaVinci Resolve 渲染 | 本项目补上 NLE 交接产物，保留专业软件精修入口 |
| [`geerlingguy/final-cut-it-out`](https://github.com/geerlingguy/final-cut-it-out) | 用 ffmpeg 检测 silence 后在 Final Cut Pro 时间线上移除片段 | 本项目保持非破坏式：先导出 EDL/manifest，由人确认后进 NLE |

新增/调整能力：`scripts/export_edl.py` 可读取 `render_config.json`、`rough_cut.py` 或 `jump_cut.py` 的 `keep_segments`，生成单轨 EDL 和 `<output>.json` manifest；支持 `--fps`、`--title`、`--source` 和可选 `--include-transcript-comments`。

使用方式：从成片配置导出用 `python3 scripts/export_edl.py --config work/render_config.json --output work/day58_edit.edl --fps 30`；从粗剪 cut list 导出用 `python3 scripts/export_edl.py --cut-list work/rough_cut.json --output work/rough_cut.edl`。

验证结果：新增 `tests/test_export_edl.py` 7 项通过；完整 `.venv/bin/python -m pytest tests -q` 通过 `202 passed in 3.18s`；`python3 -m compileall scripts tests` 通过；`git diff --check` 通过；research archive validator 通过（4 个 repo、4 份 file tree）。

### ✅ Render QA — 渲染后质检回路
[`scripts/render_qa.py`](scripts/render_qa.py)

借鉴 Remotion/视频生成类技能常见的“render → inspect → fix”闭环，渲染完成后用 `ffprobe`/`ffmpeg` 自动检查：

| 检查 | 目的 |
|---|---|
| video/audio stream | 防止导出空壳、无声视频 |
| duration / dimensions / fps | 防止平台尺寸错、时长异常 |
| `blackdetect` | 发现误裁、素材丢失导致的黑屏 |
| `freezedetect` | 发现长时间卡帧/静帧 |
| `silencedetect` | 发现人声链路丢失或长静音 |

常用：
```bash
python3 scripts/render_qa.py output/day58_master.mp4 --platform douyin --json output/day58_qa.json
python3 scripts/render_qa.py output/day58_xhs.mp4 --platform xhs
python3 scripts/timeline_view.py output/day58_master.mp4 --at 42.5 --radius 1.5 --output output/verify/qa_42_5s.png
```

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

# 1b. 可选：按 ASR 去纯口头禅/重复句，先审查 cut list 再渲染
python3 $SKILL/scripts/rough_cut.py \
  --transcript $WORK/work/transcript.json \
  --cut-list $WORK/work/rough_cut.json

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

# 3b. 先生成分镜 shot cards，审查 B-roll / 生图 / 生成视频 / 动效路由
python3 $SKILL/scripts/storyboard_plan.py \
  --transcript $WORK/work/transcript.json \
  --clean-script $WORK/work/clean_script.md \
  --output $WORK/work/storyboard_plan.json \
  --markdown $WORK/work/storyboard_plan.md \
  --max-shots 8 \
  --target-aspect 9:16

# 3c. 素材任务清单与预检：哪些已 ready，哪些要生图/审批/渲染/搜索
python3 $SKILL/scripts/storyboard_assets.py \
  --storyboard-plan $WORK/work/storyboard_plan.json \
  --asset-root $WORK/work \
  --output $WORK/work/storyboard_assets.json \
  --markdown $WORK/work/storyboard_assets.md

# 3d. 如果 imagegen[] 或 storyboard_assets 里的 needs_generation 非空，在 Codex 里直接调内置 imagegen 工具
#     生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
#     把每条 prompt_en 用 imagegen 生成 1024x1536，存到 $WORK/work/imagegen/
#     不需要 OPENAI_API_KEY；详见 docs/prompts/19-imagegen.md
#     如果 storyboard_assets 里有 needs_approval，提交 Dreamina/即梦前先确认，因为可能消耗 credits。

# 3e. 可选：软件教程/产品演示录屏，导入点击热点并生成自动聚焦计划
python3 $SKILL/scripts/screen_focus.py \
  --events $WORK/work/clicks.json \
  --screen-width 1920 \
  --screen-height 1080 \
  --output $WORK/work/screen_focus_plan.json \
  --markdown $WORK/work/screen_focus_plan.md

# 4. 渲染
#    如果生成了 screen_focus_plan.json，可额外追加：
#    --enrich-plan $WORK/work/screen_focus_plan.json
python3 $SKILL/scripts/render_final.py \
  --config $WORK/work/render_config.json \
  --enrich-plan $WORK/work/enrich_plan.json \
  --profile tech_pro \
  --primary-speed 1.25 \
  --subtitle-style karaoke \
  --output $WORK/output/day${DAY}_master.mp4

# 5. 主片质检
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_master.mp4 --platform douyin \
  --json $WORK/output/day${DAY}_master_qa.json

# 5b. 如果 QA 有 WARN/FAIL，或想抽查关键切点，生成可视化复盘图
python3 $SKILL/scripts/timeline_view.py \
  $WORK/output/day${DAY}_master.mp4 --at 42.5 --radius 1.5 \
  --output $WORK/output/verify/day${DAY}_42_5s.png

# 6. 多平台
python3 $SKILL/scripts/multi_export.py \
  $WORK/output/day${DAY}_master.mp4 --output-dir $WORK/output/

# 6b. 可选：交给专业剪辑软件继续精修/调色/混音
python3 $SKILL/scripts/export_edl.py \
  --config $WORK/work/render_config.json \
  --output $WORK/work/day${DAY}_edit.edl \
  --fps 30

# 7. 平台导出质检
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_xhs.mp4 --platform xhs
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_douyin.mp4 --platform douyin

# 8. 文案
python3 $SKILL/scripts/generate_caption.py \
  --script $WORK/work/clean_script.md --profile tech_pro \
  --output $WORK/output/day${DAY}_caption.json
```

---

## 测试

```bash
pytest tests/           # 208 测试，约 3 秒
```

按模块跑：
```bash
pytest tests/test_content_guard.py -v       # 80+ 规则的 38 个测试
pytest tests/test_rewrite_script.py -v      # Story Engine
pytest tests/test_auto_broll.py -v          # B-roll 调度
pytest tests/test_multi_export.py -v        # 多平台比例转换
pytest tests/test_render_qa.py -v           # 渲染后质检
pytest tests/test_render_enrich_plan.py -v  # enrich_plan 自动接入渲染
pytest tests/test_rough_cut.py -v           # ASR 粗剪：口头禅/重复句 cut list
pytest tests/test_timeline_view.py -v       # 切点/QA 可视化复盘图
pytest tests/test_generate_caption.py -v    # 文案合成
pytest tests/test_imagegen_hint.py -v       # gpt-image-2 提示词检测
pytest tests/test_storyboard_plan.py -v     # 分镜 shot cards + 生成路由
pytest tests/test_storyboard_assets.py -v   # 分镜素材 readiness manifest
pytest tests/test_export_edl.py -v          # NLE handoff EDL + manifest
pytest tests/test_screen_focus.py -v        # 录屏点击聚焦计划 + render 接入
```

### 本次自动化更新记录（2026-05-20 UTC）

- **调研来源**：GitHub 搜索并对比 `znyupup/ai-video-editing-skill` 的 `edit_plan.json + Dashboard`、`FireRedTeam/FireRed-OpenStoryline` 的节点化 workflow schema、`taylorzhou16/video-gen` 的 storyboard JSON / 一致性 review，以及 `6missedcalls/video-editing-skill` 的轻量 ffmpeg 编排。
- **新增能力**：`render_final.py` 新增 `--enrich-plan`，可直接读取 `auto_enrich.py` 输出，把 B-roll cue 转成定时视频 overlay，把章节卡/贴纸转成 ASS badge，把带实际文件路径的 imagegen cue 转成定时图片 overlay；同时修复 `text_badges` 已检查但未写入字幕 ASS 的问题，普通字幕和 karaoke 字幕都支持 badge。
- **使用方式**：`python3 scripts/render_final.py --config work/render_config.json --enrich-plan work/enrich_plan.json --output output/master.mp4`。`broll[].suggested_asset`、`chapter_cards[].png`、`imagegen[].image_path/generated_path` 支持相对 `enrich_plan.json` 的路径；没有生成文件的 imagegen cue 只提示，不阻塞。
- **验证结果**：新增 `tests/test_render_enrich_plan.py` 5 项通过；相关回归 `tests/test_auto_enrich.py tests/test_render_guard_integration.py tests/test_render_content_guard_integration.py tests/test_audio_chain.py tests/test_primary_speed.py` 共 15 项通过；全量 `.venv/bin/python -m pytest tests` 通过 `176 passed in 1.94s`；`python3 -m compileall scripts tests` 通过；合成 4 秒视频实测 `--enrich-plan` 成功应用 1 个 B-roll、1 个章节卡图片 overlay、1 个 badge 并输出有效 MP4。

### 本次自动化更新记录（2026-05-19 UTC）

- **新增能力**：参考 GitHub 上 `browser-use/video-use` 的 `timeline_view` 复盘工作台、`remotion-dev/skills` 的单帧/预览验证习惯，以及 `Agents365-ai/video-podcast-maker` 的 Remotion Studio 预览迭代思路，新增 `scripts/timeline_view.py`。
- **使用方式**：单点复盘用 `python3 scripts/timeline_view.py output/master.mp4 --at 42.5 --radius 1.5 --output output/verify/42_5s.png`；跳切批量复盘用 `--cut-list work/jumpcut.json --output-dir output/verify/cuts`。
- **验证结果**：`pytest tests/test_timeline_view.py -v` 通过 7 项；`python3 -m compileall scripts tests` 通过；合成 4 秒视频实测 `--at` 输出 1600×1120 PNG，`--cut-list` 批量输出 2 张 PNG；全量 `.venv/bin/python -m pytest tests` 通过 `171 passed in 1.65s`。

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
| **20** | **[Render QA](docs/prompts/20-render-qa.md)** | **渲染后机器质检** |
| **21** | **[Jump Cut](docs/prompts/21-jump-cut.md)** | **自动去停顿** |
| **22** | **[Timeline View](docs/prompts/22-timeline-view.md)** | **切点/可疑区间人工复盘图** |
| **23** | **[Versioned Output](docs/prompts/23-versioned-output.md)** | **避免覆盖旧成片** |
| **24** | **[Storyboard Plan](docs/prompts/24-storyboard-plan.md)** | **分镜 shot cards + 生成路由** |
| **25** | **[Storyboard Assets](docs/prompts/25-storyboard-assets.md)** | **分镜素材任务清单 + ready 预检** |
| **26** | **[ASR Rough Cut](docs/prompts/26-rough-cut.md)** | **去口头禅/重复句粗剪** |
| **27** | **[NLE Handoff](docs/prompts/27-export-edl.md)** | **导出 EDL 给 Premiere/FCP/Resolve** |
| **28** | **[Screen Focus](docs/prompts/28-screen-focus.md)** | **录屏点击/热点自动聚焦** |

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
├── rough_cut.py                transcript 粗剪：去口头禅/重复句      [V3]
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
├── storyboard_plan.py          分镜 shot cards + 生成路由         [V3]
├── storyboard_assets.py        分镜素材任务清单 + ready 预检       [V3]
├── screen_focus.py             录屏点击/热点聚焦计划              [V3]
├── render_final.py             单次编码渲染 + enrich_plan 接入（V3 强化）
├── render_qa.py                渲染后黑屏/静帧/静音/尺寸质检       [V3]
├── timeline_view.py            filmstrip+waveform 可视化复盘图     [V3]
├── burn_subtitles.py           字幕 ASS 生成
├── generate_cover.py           封面生成
├── generate_cover_image.py     Chrome-rendered 封面
├── add_chapter_bar.py          章节进度条
├── export_capcut.py            剪映工程导出
├── export_edl.py               NLE handoff EDL + manifest          [V3]
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
