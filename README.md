# Video Editing Skill — 视频剪辑技能

把 5-15 分钟的原始口播音频 + 一堆无声素材，变成可发布的多平台短视频。

适配 **小红书 / 抖音 / 微信视频号** 三大主流平台，按各平台的算法、比例、时长、审核规则调过参。是一条 **从口播 → 重组故事 → 平台守门 → 自动丰富 → 渲染 → 三平台导出 → 标题文案** 的端到端流水线，不是另一个剪辑工具。

```
口播音频 + 无声素材
   │
   ├─→ transcribe.py            转写 + 词级时间戳 + 口误标记
   │                            (mlx-whisper / faster-whisper / openai-whisper)
   │
   ├─→ scene_boundaries.py      视频画面 → 视觉场景边界
   │                            ffmpeg scene score + JSON/Markdown review
   │
   ├─→ rough_cut.py             ASR 粗剪 → 去纯口头禅 / 相邻重复句
   │                            输出可审计 cut list，可选单次 concat 渲染
   │
   ├─→ highlight_picker.py      长视频 → 精华候选
   │                            hook/value/turn/data 透明打分 + scene snap + render_config
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
   │                            可选接入 media_library.py recommend 排名候选素材
   │
   ├─→ provider_decision.py     provider 打分 / 预算 cap / paid 审批 / 命令依赖预检
   │
   ├─→ transition_bridge.py     相邻分镜 → AI 转场桥接计划
   │                            尾帧/首帧引用 + Dreamina 审批 + local fallback
   │
   ├─→ screen_focus.py          录屏点击/热点 → focus_events 聚焦计划
   │                            render_final 自动放大、标记、标签
   │
   ├─→ jump_cut.py              自适应静音检测 → cut list → 去停顿成片
   │     └─→ timeline_view.py   切点 filmstrip + waveform 人工复核图
   │
   ├─→ render_final.py          单次编码渲染 + enrich_plan 自动接入
   │     B-roll / 章节卡 / 贴纸 / 生成图 / 点击聚焦 overlay + BGM ducking + Heavy 字幕 + 响度规范化
   │     可选 --versioned-output：输出 _V<N>，避免覆盖旧成片
   │
   ├─→ render_qa.py             渲染后黑屏/静帧/静音/尺寸质检 + review packet
   │     └─→ timeline_view.py   QA 可疑区间可视化复盘
   │
   ├─→ subtitle_pack.py         SRT / VTT / ASS / JSON 字幕交付包
   │                            支持 render_config 串接、加速倍率、片头 offset 对齐
   │
   ├─→ chapter_markers.py       JSON / Markdown / FFmetadata / YouTube 章节时间戳
   │                            transcript / clean_script / 章节 JSON → 发布侧章节交付
   │
   ├─→ export_edl.py            render_config / cut list → EDL + manifest
   │                            交给 Premiere / Final Cut Pro / Resolve
   │
   ├─→ multi_export.py          小红书 3:4 / 抖音 9:16 / 视频号 ≤60s
   │
   ├─→ generate_caption.py      标题 + 200-500 字正文 + 3-6 tags + 发布时段建议
   │
   └─→ pipeline_manifest.py     汇总 artifact、缺口和发布前门禁
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
pytest tests/           # 251 个测试，约 4 秒
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
| `storyboard_assets.json` | 每个 shot 对应素材是否 ready、需要生成/审批/渲染/搜索；B-roll 可带 `candidate_scores` 排名理由 |

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

路由规则：抽象概念优先 `codex_imagegen`；数字/指标优先 `remotion_hyperframes`；动作/场景变化推荐 `dreamina_video` 但只标记为需确认，因为 Dreamina/即梦生成可能消耗 credits；其他先走本地素材库 B-roll。传 `--media-library <project_dir>` 时，`storyboard_assets.py` 会从 `media_index.json` / `media_index.db` 里按标签、文件名、时长和画幅推荐候选，并在 Markdown 表里显示分数。`storyboard_assets.py --strict` 会在素材未 ready 时返回退出码 2，适合渲染前拦截。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

### 🧾 Provider Decision Log — 生成供应商选择预检
[`scripts/provider_decision.py`](scripts/provider_decision.py) · [详细文档](docs/prompts/30-provider-decision.md)

借鉴 OpenMontage 这类 agentic video production 项目的 provider scoring / decision log / budget governance 思路，但保持本项目轻量：只读 `storyboard_assets.json`，不调用外部 provider，不提交任何 paid generation。

| 能力 | 说明 |
|---|---|
| 7 维 provider 打分 | task fit / output quality / control / reliability / cost efficiency / latency / continuity |
| paid-credit 审批门 | Dreamina/即梦视频生成默认 `needs_approval`，`--strict` 返回 2 |
| 预算 cap | `--budget-cap` 累计估算超限时标记 `budget_blocked` |
| 命令可用性 | 检查 `dreamina` / `node` 等本机依赖，缺失时降分并提示 |
| fallback 降级提醒 | primary route 不可用而改选 fallback 时标记 `fallback_selected` |
| 人工 review artifact | 输出 `provider_decision.json` + `provider_decision.md`，保留所有候选 provider 和理由 |

常用：
```bash
python3 scripts/provider_decision.py \
  --asset-manifest work/storyboard_assets.json \
  --output work/provider_decision.json \
  --markdown work/provider_decision.md \
  --budget-cap 3.00 \
  --single-action-approval 0.50 \
  --strict

# 按实际账号成本覆盖默认估算
python3 scripts/provider_decision.py \
  --asset-manifest work/storyboard_assets.json \
  --output work/provider_decision.json \
  --route-cost dreamina_video=1.20 \
  --budget-cap 5.00
```

### 🌉 Transition Bridge — 相邻分镜转场计划
[`scripts/transition_bridge.py`](scripts/transition_bridge.py) · [详细文档](docs/prompts/33-transition-bridge.md)

借鉴 FireRed-OpenStoryline 的 AI transition generation 思路，但保持本项目的 artifact-first 方式：只读取 `storyboard_plan.json` / `storyboard_assets.json`，为相邻分镜输出尾帧/首帧引用、转场 prompt、Dreamina/即梦 paid-credit 审批和本地 fallback，不提交任何生成任务。

| 模式 | 说明 |
|---|---|
| `auto` | 只在 section / route / keyword 跳变明显时建议 `dreamina_video` |
| `ai` | 每个相邻 shot 都生成需审批的 AI 转场 prompt |
| `default` | 全部使用本地 deterministic crossfade 计划 |
| `skip` | 不生成转场桥接项 |

常用：
```bash
python3 scripts/transition_bridge.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-manifest work/storyboard_assets.json \
  --asset-root work \
  --output work/transition_bridge_plan.json \
  --markdown work/transition_bridge_plan.md \
  --mode auto \
  --max-ai-bridges 3 \
  --strict
```

`--strict` 会在存在 `needs_approval` 时返回 2，提醒先人工确认 Dreamina/即梦 credits。批准后把生成的桥接视频保存到 `bridges[].expected_path`；不批准时按 `fallback_route` 走本地转场或直切。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

### 🗂️ Media Library Recommend — 本地 B-roll 候选推荐
[`scripts/media_library.py`](scripts/media_library.py)

借鉴终端视频编辑工具里的 transcript-aware B-roll 选择思路，但只做本地索引和透明打分，不下载 stock、不调用外部视觉模型。推荐结果会保留 `score`、`reasons`、`absolute_path`，方便 agent 或人工先确认再接入 `render_config` / `enrich_plan`。

常用：
```bash
# 先建立或刷新素材库索引
python3 scripts/media_library.py init .
python3 scripts/media_library.py scan .

# 给某个分镜或口播段找 B-roll 候选
python3 scripts/media_library.py recommend "AI workflow dashboard" \
  --project-dir . \
  --category broll \
  --target-duration 3 \
  --target-aspect 9:16 \
  --json

# 让 storyboard_assets 的素材预检表直接带候选排名
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --media-library . \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md
```

打分规则：tag 命中权重大于文件名命中，其次是路径、metadata、关联 transcript；`category=broll`、视频类型、时长覆盖 cue、画幅接近目标比例会加分；默认过滤索引里已经不存在的文件，`--include-missing` 可用于清理 stale index。

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

### 📝 Subtitle Pack — SRT/VTT/ASS 字幕交付
[`scripts/subtitle_pack.py`](scripts/subtitle_pack.py) · [详细文档](docs/prompts/29-subtitle-pack.md)

借鉴 VideoLingo / Twick / ffsubsync 这类字幕工具对“可上传字幕文件、单行可读切分、时间线对齐”的重视，但保持本项目轻量：不重新转写、不调翻译/配音服务，只把现有 `transcript.json` 或 `render_config.json` 变成可校对、可上传的 sidecar 字幕包。

常用：
```bash
python3 scripts/subtitle_pack.py \
  --transcript work/day58_transcript.json \
  --output-dir output/subtitles \
  --basename day58 \
  --formats srt vtt ass json

python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename day58_master \
  --speed 1.25 \
  --offset 2.0
```

`--transcript` 默认保留原始时间码；`--config` 默认按 `render_final.py` 的 clips 顺序串接时间线。`--speed` 对齐 `--primary-speed`，`--offset` 对齐封面/片头秒数；中文默认 18 字单行、英文默认 42 字单行，也可用 `--max-chars` 覆盖。

### 📑 Chapter Markers — 章节时间戳交付
[`scripts/chapter_markers.py`](scripts/chapter_markers.py) · [详细文档](docs/prompts/34-chapter-markers.md)

借鉴 VidPipe 这类 agentic video pipeline 对章节 sidecar 的重视：长视频/课程/YouTube/B 站交付时，不只给字幕，还给可上传、可 review、可写入 metadata 的章节文件。本项目保持轻量：只读取本地 transcript / clean_script / 显式章节 JSON，不调用 LLM，不改视频文件。

常用：
```bash
python3 scripts/chapter_markers.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output-dir output/chapters

python3 scripts/chapter_markers.py \
  --chapters work/chapters_draft.json \
  --duration 720 \
  --output-dir output/chapters \
  --strict
```

输出 `chapters.json`、`chapters.md`、`chapters.ffmetadata`、`chapters-youtube.txt`。`chapters.ffmetadata` 可用 `ffmpeg -map_metadata 1 -codec copy` 写进 MP4/MKV；平台简介仍建议直接贴 `chapters-youtube.txt`。

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

### 🎯 Highlight Picker — 长视频精华候选
[`scripts/highlight_picker.py`](scripts/highlight_picker.py) · [详细文档](docs/prompts/31-highlight-picker.md)

借鉴 GitHub 上 OpusClip-style 开源项目的 highlight scoring / hook reason / JSON handoff 思路，但保持本项目轻量：不调用 LLM、不下载素材、不渲染，只把 transcript 变成可审计候选。可选接入 `scene_boundaries.py`，把候选开头/结尾扩展到附近视觉切点，避免卡在镜头中间。

| 能力 | 说明 |
|---|---|
| 平台时长默认值 | 小红书 20-90s；抖音/视频号/TikTok/Shorts 15-60s；Reels 15-90s |
| 透明打分 | hook question / contrarian / pain / turn / practical value / data / emotion / CTA |
| 去重 | 重叠候选按分数保留最强一条，避免同一段反复输出 |
| Review artifact | 输出 JSON + Markdown，包含 score breakdown、signals、warnings、reason |
| 场景对齐 | 可选 `--scene-boundaries`，start 只向前扩展、end 只向后扩展，不吞字 |
| 渲染串接 | 可选 `--render-config` 直接产出 `render_final.py` 可读 clips |

常用：
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
  --num-clips 3 \
  --strict

python3 scripts/render_final.py \
  --config work/highlight_render_config.json \
  --output output/highlight_master.mp4 \
  --versioned-output
```

`--strict` 会在最佳候选低于 `--min-score` 时返回 2，适合自动化里先拦住弱 hook 或半句话结尾的片段，让人先改选/补写再渲染。

### 🎬 Scene Boundaries — 视觉场景边界
[`scripts/scene_boundaries.py`](scripts/scene_boundaries.py) · [详细文档](docs/prompts/32-scene-boundaries.md)

借鉴 PySceneDetect / OpenShorts 这类项目把场景边界用于视频拆分、viral moment 识别和 review 的思路，但保持本项目依赖轻：只用已必装的 FFmpeg `select=gt(scene,threshold),showinfo` 检测视觉切点，输出可审计 JSON/Markdown，不引入 OpenCV/PySceneDetect 硬依赖。

常用：
```bash
python3 scripts/scene_boundaries.py origin/long-talk.mp4 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md \
  --threshold 0.35 \
  --min-scene-duration 1.0

python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --scene-boundaries work/scene_boundaries.json \
  --scene-snap-tolerance 1.5 \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --platform xhs
```

`scene_boundaries.json` 采用 `scene_boundaries.v1`，包含 `boundaries[]` 和 `scenes[]`；`highlight_picker.py` 会把每个 candidate 的 `scene_snap` 写入 JSON、Markdown 和可选 `render_config`。如果切点太密，调高 `--threshold` 或 `--min-scene-duration`。

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
| BGM 自动 ducking | `--bgm-ducking` 或 config `"bgm_ducking": true` |
| 版本化输出 | `--versioned-output` 或 config `"versioned_output": true` |

背景音乐常用配置：
```json
{
  "bgm": "work/origin/bgm.mp3",
  "bgm_volume": 0.15,
  "bgm_fade_in": 1.0,
  "bgm_fade_out": 3.0,
  "bgm_ducking": true,
  "bgm_duck_threshold": 0.03,
  "bgm_duck_ratio": 8.0,
  "bgm_duck_attack": 20.0,
  "bgm_duck_release": 250.0
}
```

CLI 覆盖：
```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --bgm work/origin/bgm.mp3 \
  --bgm-ducking \
  --bgm-fade-in 1 \
  --bgm-fade-out 3 \
  --output output/day58_master.mp4
```

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

### 📋 Pipeline Manifest — 生产线状态清单
[`scripts/pipeline_manifest.py`](scripts/pipeline_manifest.py) · [详细文档](docs/prompts/35-pipeline-manifest.md)

借鉴 GitHub 上 agentic video pipeline 的 job history / run state / build report 思路，但不引入数据库、Web 服务或队列：`pipeline_manifest.py` 只扫描本地项目目录，把 transcript、clean script、render_config、成片、QA、caption，以及 storyboard/provider/transition 的阻塞状态汇总成一个可审计清单。

常用：
```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/pipeline_manifest.json \
  --markdown work/day58/pipeline_manifest.md \
  --strict
```

`publish_ready` 默认要求 `transcript` / `clean_script` / `render_config` / `master_video` / `render_qa` / `caption` 都存在；如果发现 `storyboard_assets.json`、`provider_decision.json` 或 `transition_bridge_plan.json` 里仍有 blocking、approval_required、budget_blocked、QA fail 等问题，`--strict` 返回 2。需要把字幕或章节作为强制交付时可加 `--require subtitles --require chapter_markers`。

### 2026-06-02 自动化升级记录（Pipeline Manifest）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目落地方式 |
|---|---|---|
| [`znyupup/ai-video-editing-skill`](https://github.com/znyupup/ai-video-editing-skill) | `edit_plan.json`、`edit_plan_fixed.json`、Dashboard 和 QC frames 让 agent/human 能看懂项目当前状态 | 新增本地 `pipeline_manifest.py`，把本项目分散的 artifact 汇总成状态清单 |
| [`czmomocha/agents-video-pipeline`](https://github.com/czmomocha/agents-video-pipeline) | `PipelineState` 显式保存 plan/script/storyboard/shots/output/errors/metrics，并规划 checkpointer 断点续跑 | 不引入 LangGraph/checkpointer，只做可重复生成的 run-state JSON |
| [`el-frontend/video-wizard`](https://github.com/el-frontend/video-wizard) | Job History / queue 记录长任务 status、progress、error 和输出路径 | 不加数据库或 Web UI，用 `--strict` 作为 CLI 发布门禁 |
| [`Aadi7171/Agentic-video-pipeline`](https://github.com/Aadi7171/Agentic-video-pipeline) | 多 agent 串行产出 script / voice / assets / final manifest URL，阶段边界清晰 | 保留本项目本地 artifact-first 方式，把阶段缺口写入 `next_actions` |

新增/调整能力：新增 `scripts/pipeline_manifest.py` 和 [docs/prompts/35-pipeline-manifest.md](docs/prompts/35-pipeline-manifest.md)。脚本扫描项目目录，输出 `pipeline_manifest.v1` JSON 和 Markdown review 表；支持 `analysis` / `plan_review` / `render_ready` / `publish_ready` 四个 target stage；`--require` 可把 `subtitles`、`chapter_markers`、`platform_exports` 等可选交付变成硬门禁；`--strict` 会在缺少必需 artifact、render QA fail、storyboard/provider/transition 仍有 blocking 或 paid approval 时返回 2。

使用方式：`python3 scripts/pipeline_manifest.py --project-dir work/day58 --target-stage publish_ready --output work/day58/pipeline_manifest.json --markdown work/day58/pipeline_manifest.md --strict`。如果需要强制字幕和章节 sidecar，追加 `--require subtitles --require chapter_markers`；如果返回 2，先看 Markdown 的 `Next Actions`，补齐缺件或解除审批/预算/QA 阻塞后再发布。

验证结果：新增 `tests/test_pipeline_manifest.py` 6 项；`.venv/bin/python -m pytest tests/test_pipeline_manifest.py -q` 通过 `6 passed in 0.14s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `257 passed in 3.39s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/pipeline_manifest.py --help` 通过；`git diff --check` 通过；research archive validator 通过（`repo_dirs=5 file_tree_files=5 code_manifest_files=1`）。

### 2026-06-01 自动化升级记录（Chapter Markers）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目落地方式 |
|---|---|---|
| [`htekdev/vidpipe`](https://github.com/htekdev/vidpipe) | pipeline 输出 `chapters.json`、Markdown、FFmetadata、YouTube timestamps，方便长视频发布和 summary 引用 | 新增本地 `chapter_markers.py`，不引入 LLM agent，只做可复核章节 sidecar |
| [`el-frontend/video-wizard`](https://github.com/el-frontend/video-wizard) | Job History / task queue 让长渲染有状态、进度和错误记录 | 本次不加服务架构；继续保持轻量 CLI artifact |
| [`MastroMimmo/ffmpeg-skill`](https://github.com/MastroMimmo/ffmpeg-skill) | 高层 FFmpeg wrapper 输出 JSON，并用 palettegen/paletteuse 做高质量 GIF | JSON-first CLI 风格保留；GIF 导出列为后续候选，不抢本次章节交付优先级 |
| [`remotion-dev/skills`](https://github.com/remotion-dev/skills/blob/main/skills/remotion/SKILL.md) | Remotion Studio + one-frame render check 强调预览/验证闭环 | 本项目已有 render QA/timeline view，本次只补发布侧章节 metadata |

新增/调整能力：新增 `scripts/chapter_markers.py` 和 [docs/prompts/34-chapter-markers.md](docs/prompts/34-chapter-markers.md)。脚本可从 `transcript.json`、`clean_script.md` 的 `## ` 标题，或人工/LLM 给出的章节 JSON 生成 `chapter_markers.v1` manifest，并同时写出 `chapters.md`、`chapters.ffmetadata`、`chapters-youtube.txt`。首章会对齐到 `0:00` 以适配 YouTube/B 站简介章节；`--strict` 在自动修正或章节间隔过短时返回 2。

使用方式：`python3 scripts/chapter_markers.py --transcript work/transcript.json --clean-script work/clean_script.md --output-dir output/chapters`。如果已有人工确认的章节，用 `python3 scripts/chapter_markers.py --chapters work/chapters_draft.json --duration 720 --output-dir output/chapters --strict`。`chapters.ffmetadata` 可配合 `ffmpeg -map_metadata 1 -codec copy` 写入容器 metadata；上传平台仍建议把 `chapters-youtube.txt` 贴进简介。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

验证结果：新增 `tests/test_chapter_markers.py` 7 项；`.venv/bin/python -m pytest tests/test_chapter_markers.py -q` 通过 `7 passed in 0.05s`；相关回归 `.venv/bin/python -m pytest tests/test_chapter_markers.py tests/test_subtitle_pack.py tests/test_auto_chapter_cards.py -q` 通过 `17 passed in 0.13s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `251 passed in 3.50s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/chapter_markers.py --help` 通过；`git diff --check` 通过；research archive validator 通过（`repo_dirs=4 file_tree_files=4 code_manifest_files=1`）。

### 2026-05-31 自动化升级记录（Transition Bridge）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目落地方式 |
|---|---|---|
| [`FireRedTeam/FireRed-OpenStoryline`](https://github.com/FireRedTeam/FireRed-OpenStoryline) | AI transition generation 用前一片段尾帧、后一片段首帧和自然语言描述生成过渡镜头，并明确提示成本较高 | 新增本地 `transition_bridge.py`，只产 prompt / frame refs / paid 审批，不直接扣费 |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | pipeline/style playbook 里把 transition family、pacing 和 provider/cost 审批作为生产约束 | `auto` 模式按 section/route/keyword 跳变控制 AI 转场数量，并保留 fallback |
| [`heygen-com/hyperframes`](https://github.com/heygen-com/hyperframes) | agent-friendly 本地预览/渲染强调确定性和可复核 | 非 AI 场景默认 `deterministic_crossfade` / `straight_cut`，避免把生成转场当成必需品 |
| [`aaurelions/vidosy`](https://github.com/aaurelions/vidosy) | JSON 驱动的 scenes/audio/render 配置清晰 | 新增 `transition_bridge_plan.v1` JSON + Markdown review artifact |

新增/调整能力：新增 `scripts/transition_bridge.py` 和 [docs/prompts/33-transition-bridge.md](docs/prompts/33-transition-bridge.md)。脚本读取 `storyboard_plan.json`，可选读取 `storyboard_assets.json`，为相邻分镜输出 `transition_bridge_plan.v1`：包含 `need_score`、前一镜尾帧 / 后一镜首帧引用、Dreamina/即梦转场 prompt、`needs_approval` paid-credit 提示、`expected_path` 和本地 `fallback_route`。支持 `--mode auto|ai|default|skip`，其中 `auto` 只对跳变明显的镜头建议 AI 转场，`--strict` 在需要审批时返回 2。

使用方式：先跑 `storyboard_plan.py` 和 `storyboard_assets.py`，再执行 `python3 scripts/transition_bridge.py --storyboard-plan work/storyboard_plan.json --asset-manifest work/storyboard_assets.json --asset-root work --output work/transition_bridge_plan.json --markdown work/transition_bridge_plan.md --mode auto --max-ai-bridges 3 --strict`。如果返回 2，先人工确认值得生成的 Dreamina/即梦转场；不批准时按 `fallback_route` 走本地 crossfade 或直切。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

验证结果：新增 `tests/test_transition_bridge.py` 4 项；`.venv/bin/python -m pytest tests/test_transition_bridge.py -q` 通过 `4 passed in 0.07s`；相关回归 `.venv/bin/python -m pytest tests/test_transition_bridge.py tests/test_storyboard_assets.py tests/test_storyboard_plan.py tests/test_provider_decision.py -q` 通过 `22 passed in 0.21s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `244 passed in 3.51s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/transition_bridge.py --help` 通过；`git diff --check` 通过；research archive validator 通过（`repo_dirs=4 file_tree_files=4 code_manifest_files=1`）。

### 2026-05-30 自动化升级记录（Scene Boundaries）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`Breakthrough/PySceneDetect`](https://github.com/Breakthrough/PySceneDetect) | content/adaptive scene detection、`list-scenes`、`split-video`、首尾帧 review 是成熟剪辑 primitive | 不引入 PySceneDetect/OpenCV 硬依赖，先用 FFmpeg scene score 产出轻量边界 artifact |
| [`mutonby/openshorts`](https://github.com/mutonby/openshorts) | Clip Generator 把 transcript + PySceneDetect scene boundaries 一起交给 viral moment detection | 新增 `scene_boundaries.py`，并让 `highlight_picker.py` 可选接入视觉切点 |
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator) | score / hook / reason / JSON output / overlap dedupe / crop handoff | 保留本地透明 transcript scoring，同时把 scene snap 写入 JSON、Markdown 和 render_config |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | agent-native 视频工作流里有明确 scene review 阶段 | 新增 `scene_boundaries.md` 复核表，先看视觉切点再渲染 |

新增/调整能力：新增 `scripts/scene_boundaries.py` 和 [docs/prompts/32-scene-boundaries.md](docs/prompts/32-scene-boundaries.md)。脚本用本地 FFmpeg `select=gt(scene,threshold),showinfo` 检测视觉切点，输出 `scene_boundaries.v1` JSON + Markdown review。`scripts/highlight_picker.py` 新增 `--scene-boundaries` / `--scene-snap-tolerance`，会把候选 start 只向前扩展到附近视觉切点、end 只向后扩展到附近视觉切点，避免吞掉 transcript 字词；`scene_snap` 会进入候选 JSON、Markdown 和可选 `render_config`。

使用方式：先跑 `python3 scripts/scene_boundaries.py origin/long-talk.mp4 --output work/scene_boundaries.json --markdown work/scene_boundaries.md --threshold 0.35 --min-scene-duration 1.0`；再跑 `python3 scripts/highlight_picker.py --transcript work/long_transcript.json --scene-boundaries work/scene_boundaries.json --scene-snap-tolerance 1.5 --video origin/long-talk.mp4 --output work/highlight_candidates.json --markdown work/highlight_candidates.md --render-config work/highlight_render_config.json --platform xhs --num-clips 3 --strict`。

验证结果：新增 `tests/test_scene_boundaries.py` 5 项，并给 `tests/test_highlight_picker.py` 增加 scene snap 覆盖；`.venv/bin/python -m pytest tests/test_scene_boundaries.py tests/test_highlight_picker.py -q` 通过 `12 passed in 0.08s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `240 passed in 2.39s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/scene_boundaries.py --help` 和 `.venv/bin/python scripts/highlight_picker.py --help` 通过；FFmpeg 合成 3 色视频 smoke 检测出 `2 cuts, 3 scenes`；`git diff --check` 通过；research archive validator 通过（`repo_dirs=4 file_tree_files=4 code_manifest_files=1`）。

### 2026-05-29 自动化升级记录（Highlight Picker）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator) | long-video chunking、virality signals、score/hook/reason JSON、overlap dedupe | 新增本地 `highlight_picker.py`，不调用云端 clipping API |
| [`Shaarav4795/ClippedAI`](https://github.com/Shaarav4795/ClippedAI) | transcription cache、clip finder、word density/engagement/duration scoring | 采用透明规则打分，保留 `score_breakdown` 方便复核 |
| [`mutonby/openshorts`](https://github.com/mutonby/openshorts) | transcript + scene boundary 选 15-60s viral moments，再 FFmpeg extract/reframe | 先补 transcript candidate artifact；后续可再接 scene boundary |
| [`majiayu000/claude-skill-registry` autocut-shorts](https://github.com/majiayu000/claude-skill-registry/tree/main/skills/data/autocut-shorts) | transcript/laughter/sentiment/scene 多信号 virality score + JSON report | 本次先实现无依赖 transcript scoring 和 Markdown review |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | pipeline manifest、review focus、quality gate、artifact-first 生产方式 | 保持 JSON/Markdown 审计产物，不把选片逻辑藏进 agent prompt |

新增/调整能力：新增 `scripts/highlight_picker.py`、[docs/prompts/31-highlight-picker.md](docs/prompts/31-highlight-picker.md)，并把 [docs/prompts/08-long-to-short.md](docs/prompts/08-long-to-short.md) 接上本地精华候选流程。脚本读取 `transcript.json`，按平台时长窗口生成候选，对 hook question / contrarian / pain / turn / practical value / data / emotion / CTA、时长、密度、完整性和 filler 风险做透明打分，输出 `highlight_candidates.json` + `highlight_candidates.md`；可选 `--render-config` 直接生成 `render_final.py` 可读 clips。

使用方式：`python3 scripts/highlight_picker.py --transcript work/long_transcript.json --video origin/long-talk.mp4 --output work/highlight_candidates.json --markdown work/highlight_candidates.md --render-config work/highlight_render_config.json --platform xhs --num-clips 3 --strict`。先人工看 Markdown 里的 hook、reason、warnings；确认后用 `python3 scripts/render_final.py --config work/highlight_render_config.json --output output/highlight_master.mp4 --versioned-output` 渲染。

验证结果：新增 `tests/test_highlight_picker.py` 5 项；`.venv/bin/python -m pytest tests/test_highlight_picker.py -q` 通过 `5 passed in 0.05s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `233 passed in 3.58s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/highlight_picker.py --help` smoke 通过；`git diff --check` 通过；research archive validator 通过（`repo_dirs=1 file_tree_files=1 code_manifest_files=1`）。

### 2026-05-29 自动化升级记录（Provider Decision Log）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | provider scoring、decision audit trail、budget controls，把成本/审批作为生成前门禁 | 新增轻量 `provider_decision.py`，只做本地预检与日志，不引入云 provider |
| [`resemble-ai/remotion-resemble-skill`](https://github.com/resemble-ai/remotion-resemble-skill) | 生成前检查凭证，TTS/字幕/Remotion 串联清晰 | 本项目把命令可用性和审批状态写入 provider decision log |
| [`Agents365-ai/video-podcast-maker`](https://github.com/Agents365-ai/video-podcast-maker) | 多 TTS provider、Remotion Studio 预览、平台化输出，强调人先确认脚本/样式 | 本项目继续保留人工 review artifact，生成前先看 provider/预算/审批 |
| [`luoluoluo22/jianying-editor-skill`](https://github.com/luoluoluo22/jianying-editor-skill) | 剪映自动化规则细，云视频/云音乐/TTS 路由和导出 SOP 明确 | 本项目不绑定剪映桌面，但补齐生成任务的 provider 路由审计 |
| [`remotion-dev/skills`](https://github.com/remotion-dev/skills/blob/main/skills/remotion/SKILL.md) | Remotion 预览、单帧检查、字幕/FFmpeg 子规则清楚 | 本项目把 `node`/本地 motion card 依赖纳入 provider 可用性检查 |

新增/调整能力：新增 `scripts/provider_decision.py` 和 [docs/prompts/30-provider-decision.md](docs/prompts/30-provider-decision.md)。它读取 `storyboard_assets.json`，对 `codex_imagegen`、`dreamina_video`、`remotion_hyperframes`、`media_library_broll` 等候选 provider 做 7 维评分（task fit / quality / control / reliability / cost efficiency / latency / continuity），输出 `provider_decision.json` 与 `provider_decision.md`；`--strict` 会在需要 paid-credit 审批、超过 `--budget-cap`、缺少 `dreamina`/`node` 等依赖或 primary route 降级到 fallback 时返回 2。选择逻辑会优先尊重 storyboard 的 primary route，只有 primary 不可用时才降级到 fallback。

使用方式：先跑 `storyboard_assets.py` 生成素材状态，再跑 `python3 scripts/provider_decision.py --asset-manifest work/storyboard_assets.json --output work/provider_decision.json --markdown work/provider_decision.md --budget-cap 3.00 --single-action-approval 0.50 --strict`。如果账号真实成本不同，可用 `--route-cost dreamina_video=1.20` 覆盖默认估算。

验证结果：新增 `tests/test_provider_decision.py` 7 项；`.venv/bin/python -m pytest tests/test_provider_decision.py tests/test_storyboard_assets.py tests/test_storyboard_plan.py -q` 通过 `18 passed in 0.14s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `228 passed in 2.21s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/provider_decision.py --help` smoke 通过；`git diff --check` 通过。

### 2026-05-28 自动化升级记录（BGM Ducking Mix）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | production pipeline 明确把 audio mixer、ducking、fades 和 narration/music balance 作为后期质量门禁 | 新增 `render_final.py` 的 BGM sidechain ducking，不引入云端音乐/混音依赖 |
| [`aaurelions/vidosy`](https://github.com/aaurelions/vidosy) | JSON 驱动的 background music、scene narration、fade in/out、volume control | 保留本项目 render_config 风格，补齐 `bgm_fade_in` 和 `bgm_ducking` 配置 |
| [`remotion-dev/template-prompt-to-video`](https://github.com/remotion-dev/template-prompt-to-video) | timeline 同步 `Elements / Text / Audio`，把音频作为一等时间线产物 | 继续在单次 FFmpeg render 中处理，不新增 Remotion 依赖 |
| [`wilwaldon/Claude-Code-Video-Toolkit`](https://github.com/wilwaldon/Claude-Code-Video-Toolkit) | 视频 agent 技能强调 FFmpeg post-processing、归一化、压缩和批处理 | 复用 FFmpeg `sidechaincompress`，保持本地可审计 filter_complex |

新增/调整能力：`scripts/render_final.py` 新增可选 BGM 自动 ducking。配置 `"bgm_ducking": true` 或 CLI 传 `--bgm-ducking` 后，渲染层会把处理后人声分成 `voice_mix` / `voice_sc` 两路，用 `voice_sc` 触发 `sidechaincompress` 压低 BGM，再与 `voice_mix` 混合；默认不开启，旧的静态 `amix` 行为不变。同时新增 `bgm_fade_in` / `--bgm-fade-in`，并可通过 `bgm_duck_threshold`、`bgm_duck_ratio`、`bgm_duck_attack`、`bgm_duck_release` 细调。

使用方式：配置式写入 `"bgm": "work/origin/bgm.mp3", "bgm_volume": 0.15, "bgm_fade_in": 1.0, "bgm_fade_out": 3.0, "bgm_ducking": true`；命令式用 `python3 scripts/render_final.py --config work/render_config.json --bgm work/origin/bgm.mp3 --bgm-ducking --bgm-fade-in 1 --bgm-fade-out 3 --output output/master.mp4`。信息密度高的口播建议保留默认 `threshold=0.03 / ratio=8 / attack=20ms / release=250ms`。

验证结果：新增/更新 `tests/test_audio_chain.py`，覆盖 help flags、默认静态 BGM 混音、ducking sidechain filter；`.venv/bin/python -m pytest tests/test_audio_chain.py -q` 通过 `6 passed in 0.12s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `221 passed in 3.40s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；research archive validator 通过（4 个 repo、4 份 file tree、1 份 code manifest）。

### 2026-05-27 自动化升级记录（Subtitle Pack）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`Huanshere/VideoLingo`](https://github.com/Huanshere/VideoLingo) | 关注字幕切分、对齐、单行字幕、翻译/配音交付质量 | 新增本地字幕包导出；本次不引入翻译/配音依赖 |
| [`smacke/ffsubsync`](https://github.com/smacke/ffsubsync) | 把字幕文件和视频对齐当作独立交付能力 | 支持 `--speed` / `--offset`，让 sidecar 字幕对齐最终成片 |
| [`ncounterspecialist/twick`](https://github.com/ncounterspecialist/twick) | AI captions + timed tracks 可接入编辑器/SDK | 输出 SRT/VTT/ASS/JSON，方便平台上传、网页播放和人工校对 |
| [`vericontext/vibeframe`](https://github.com/vericontext/vibeframe) | agent-native 项目产物保留 build/review report | JSON manifest 保留 cue 来源、时序参数和 warning |
| [`harry0703/MoneyPrinterTurbo`](https://github.com/harry0703/MoneyPrinterTurbo/blob/main/README-en.md) | topic-to-video 流水线包含字幕、素材和 BGM 交付 | 本项目已有完整短视频流水线，本次补齐平台字幕 sidecar |

新增/调整能力：新增 `scripts/subtitle_pack.py`，可从 `transcript.json` 或 `render_config.json` 导出 SRT、VTT、ASS 和 JSON manifest；默认中文 18 字单行、英文 42 字单行，优先按标点/词边界切分；如果 transcript 带 `words[]`，会用词级时间戳生成更准的 cue；`--config` 会按最终 clips 串接时间线，`--speed` 和 `--offset` 用来对齐 `render_final.py --primary-speed` 和片头封面秒数。

使用方式：原始转写字幕用 `python3 scripts/subtitle_pack.py --transcript work/day58_transcript.json --output-dir output/subtitles --basename day58 --formats srt vtt ass json`；最终成片字幕用 `python3 scripts/subtitle_pack.py --config work/render_config.json --output-dir output/subtitles --basename day58_master --speed 1.25 --offset 2.0`。详细示例见 [docs/prompts/29-subtitle-pack.md](docs/prompts/29-subtitle-pack.md)。

验证结果：新增 `tests/test_subtitle_pack.py` 4 项；`.venv/bin/python -m pytest tests/test_subtitle_pack.py -q` 通过 `4 passed in 0.06s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `218 passed in 3.28s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；`.venv/bin/python scripts/subtitle_pack.py --help` smoke 验证 CLI 参数正常。

### 2026-05-26 自动化升级记录（Media Library Recommend）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | agentic production pipeline、质量门禁、artifact 交付清晰 | 保持本地 JSON/Markdown artifact，不引入云端生成依赖 |
| [`vericontext/vibeframe`](https://github.com/vericontext/vibeframe) | `media/`、storyboard、build/review report 串成 agent-native 项目循环 | `storyboard_assets.py --media-library` 把素材索引结果写进 readiness manifest |
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | transcript-aware B-roll / generated visual scoring，强调先规划再合成 | 新增透明 `score` / `reasons` 的本地候选排名 |
| [`DojoCodingLabs/remotion-superpowers`](https://github.com/DojoCodingLabs/remotion-superpowers) | stock footage、视频 review loop、短视频 preset 集成 | 本项目只推荐本地素材；下载/生成仍走已有 storyboard / Dreamina / imagegen 路由 |

新增/调整能力：`scripts/media_library.py` 新增 `recommend` 子命令，可从 `media_index.json` / `media_index.db` 中按查询词、tag、文件名、metadata、关联 transcript、时长覆盖和目标画幅给本地素材打分；`scripts/storyboard_assets.py` 新增 `--media-library`，会把 `media_library_broll` shot 的 ranked B-roll 候选写入 `candidate_paths` 和 `candidate_scores`，Markdown 复核表会显示候选分数。

使用方式：先用 `python3 scripts/media_library.py scan .` 建索引，再跑 `python3 scripts/media_library.py recommend "AI workflow dashboard" --project-dir . --category broll --target-duration 3 --target-aspect 9:16 --json`；分镜预检时加 `--media-library .`，例如 `python3 scripts/storyboard_assets.py --storyboard-plan work/storyboard_plan.json --asset-root work --media-library . --output work/storyboard_assets.json --markdown work/storyboard_assets.md`。

验证结果：新增 `tests/test_media_library_recommend.py` 3 项，并更新 `tests/test_storyboard_assets.py`；`.venv/bin/python -m pytest tests/test_media_library_recommend.py tests/test_storyboard_assets.py -q` 通过 `9 passed in 0.11s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `214 passed in 2.21s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；`python3 scripts/media_library.py recommend --help` smoke 验证 CLI 参数正常。

### 2026-05-25 自动化升级记录（Render QA Review Packet）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`video-db/skills`](https://github.com/video-db/skills) | 视频理解后返回可搜索 moment、可播放 evidence clip 和可分享输出 | 新增本地 QA review packet，不引入外部服务 |
| [`remotion-dev/skills`](https://github.com/remotion-dev/skills/blob/main/skills/remotion/SKILL.md) | 把渲染后检查做成明确的 inspect/fix 闭环 | `render_qa.py` 现在可直接生成 Markdown/JSON 复核包 |
| [`heygen-com/skills`](https://github.com/heygen-com/skills) | 通过可复用状态文件把 avatar/video/translate 串成生产链 | 本项目继续沿用 JSON/Markdown artifact 串联，不新增供应商状态 |
| [`libtv-labs/libtv-skills`](https://github.com/libtv-labs/libtv-skills/blob/main/skills/libtv-skill/SKILL.md) / [`Wan-Video/Wan-skills`](https://github.com/Wan-Video/Wan-skills) | 异步生成、轮询和下载结果的任务化交付 | 本次先补渲染后 evidence handoff；生成任务仍交给 storyboard/Dreamina 路由 |

新增/调整能力：`scripts/render_qa.py` 增加 `--review-dir`，可把黑屏、静帧、静音检测出的可疑区间汇总成 `render_qa_review.json` 和 `render_qa_review.md`；加 `--review-clips` 时会为每个可疑区间抽取带上下文的短 MP4 到 `clips/`。新增 `build_review_segments()` / `write_review_packet()`，便于自动化流水线复用。

使用方式：`python3 scripts/render_qa.py output/day58_master.mp4 --platform douyin --json output/day58_qa.json --review-dir output/verify/day58_qa --review-clips`；只想生成复核表、不抽视频片段时去掉 `--review-clips`。可用 `--review-padding 1.0` 调整前后文秒数，用 `--max-review-segments 12` 控制证据数量。

验证结果：新增/更新 `tests/test_render_qa.py`，`.venv/bin/python -m pytest tests/test_render_qa.py -q` 通过 `9 passed in 0.02s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `210 passed in 2.20s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；2 秒黑屏/静音合成视频 smoke 验证 `--review-dir --review-clips` 会写出 Markdown、JSON 和 2 个证据 MP4。

### 2026-05-25 自动化升级记录（Screen Focus）

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

借鉴 Remotion/视频生成类技能常见的“render → inspect → fix”闭环，以及 VideoDB 类项目的 playable evidence handoff，渲染完成后用 `ffprobe`/`ffmpeg` 自动检查并可生成复核包：

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
python3 scripts/render_qa.py output/day58_master.mp4 \
  --platform douyin \
  --json output/day58_qa.json \
  --review-dir output/verify/day58_qa \
  --review-clips
python3 scripts/timeline_view.py output/day58_master.mp4 --at 42.5 --radius 1.5 --output output/verify/qa_42_5s.png
```

`--review-dir` 会写 `render_qa_review.json` 和 `render_qa_review.md`，把黑屏、静帧、静音的可疑区间按 FAIL/WARN 排序；`--review-clips` 会额外抽取短 MP4 证据片段。只需要审阅清单时不加 `--review-clips`。

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

# 1c. 可选：长视频/多镜头素材先检测视觉场景边界，供 highlight picker 对齐自然切点
python3 $SKILL/scripts/scene_boundaries.py $WORK/origin/long-talk.mp4 \
  --output $WORK/work/scene_boundaries.json \
  --markdown $WORK/work/scene_boundaries.md

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
  --json $WORK/output/day${DAY}_master_qa.json \
  --review-dir $WORK/output/verify/day${DAY}_qa \
  --review-clips

# 5b. 如果 QA 有 WARN/FAIL，先看 review packet；想抽查关键切点再生成可视化复盘图
python3 $SKILL/scripts/timeline_view.py \
  $WORK/output/day${DAY}_master.mp4 --at 42.5 --radius 1.5 \
  --output $WORK/output/verify/day${DAY}_42_5s.png

# 5c. 可选：导出平台可上传字幕 sidecar
python3 $SKILL/scripts/subtitle_pack.py \
  --config $WORK/work/render_config.json \
  --output-dir $WORK/output/subtitles \
  --basename day${DAY}_master \
  --speed 1.25 \
  --offset 2.0

# 5d. 可选：导出 YouTube/B站/课程章节时间戳与 FFmetadata
python3 $SKILL/scripts/chapter_markers.py \
  --transcript $WORK/work/transcript.json \
  --clean-script $WORK/work/clean_script.md \
  --output-dir $WORK/output/chapters

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

# 9. 发布前 artifact 门禁
python3 $SKILL/scripts/pipeline_manifest.py \
  --project-dir $WORK \
  --target-stage publish_ready \
  --output $WORK/output/pipeline_manifest.json \
  --markdown $WORK/output/pipeline_manifest.md \
  --strict
```

---

## 测试

```bash
pytest tests/           # 257 测试，约 4 秒
```

按模块跑：
```bash
pytest tests/test_content_guard.py -v       # 80+ 规则的 38 个测试
pytest tests/test_rewrite_script.py -v      # Story Engine
pytest tests/test_auto_broll.py -v          # B-roll 调度
pytest tests/test_multi_export.py -v        # 多平台比例转换
pytest tests/test_render_qa.py -v           # 渲染后质检
pytest tests/test_render_enrich_plan.py -v  # enrich_plan 自动接入渲染
pytest tests/test_audio_chain.py -v         # 响度链 + BGM ducking 混音
pytest tests/test_rough_cut.py -v           # ASR 粗剪：口头禅/重复句 cut list
pytest tests/test_timeline_view.py -v       # 切点/QA 可视化复盘图
pytest tests/test_generate_caption.py -v    # 文案合成
pytest tests/test_imagegen_hint.py -v       # gpt-image-2 提示词检测
pytest tests/test_storyboard_plan.py -v     # 分镜 shot cards + 生成路由
pytest tests/test_storyboard_assets.py -v   # 分镜素材 readiness manifest
pytest tests/test_export_edl.py -v          # NLE handoff EDL + manifest
pytest tests/test_screen_focus.py -v        # 录屏点击聚焦计划 + render 接入
pytest tests/test_subtitle_pack.py -v       # SRT/VTT/ASS/JSON 字幕交付包
pytest tests/test_chapter_markers.py -v     # JSON/Markdown/FFmetadata/YouTube 章节时间戳
pytest tests/test_scene_boundaries.py -v    # 视觉场景边界 + highlight scene snap
pytest tests/test_pipeline_manifest.py -v   # 生产线 artifact 状态清单/发布门禁
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
| **29** | **[Subtitle Pack](docs/prompts/29-subtitle-pack.md)** | **导出 SRT/VTT/ASS/JSON 字幕包** |
| **30** | **[Provider Decision Log](docs/prompts/30-provider-decision.md)** | **生成 provider、预算和审批预检** |
| **31** | **[Highlight Picker](docs/prompts/31-highlight-picker.md)** | **长视频精华候选 + render_config** |
| **32** | **[Scene Boundaries](docs/prompts/32-scene-boundaries.md)** | **视觉场景边界 + highlight 自然切点对齐** |
| **33** | **[Transition Bridge](docs/prompts/33-transition-bridge.md)** | **相邻分镜转场 prompt + paid 审批** |
| **34** | **[Chapter Markers](docs/prompts/34-chapter-markers.md)** | **YouTube/B站/课程章节时间戳 + FFmetadata** |

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
├── highlight_picker.py         长视频精华候选 + render_config        [V3]
├── scene_boundaries.py         视觉场景边界 + highlight scene snap    [V3]
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
├── provider_decision.py        provider 打分 + 预算/审批预检       [V3]
├── transition_bridge.py        相邻分镜转场桥接计划              [V3]
├── screen_focus.py             录屏点击/热点聚焦计划              [V3]
├── render_final.py             单次编码渲染 + enrich_plan 接入（V3 强化）
├── render_qa.py                渲染后黑屏/静帧/静音/尺寸质检       [V3]
├── timeline_view.py            filmstrip+waveform 可视化复盘图     [V3]
├── subtitle_pack.py            SRT/VTT/ASS/JSON 字幕交付包        [V3]
├── chapter_markers.py          JSON/Markdown/FFmetadata/YouTube 章节时间戳 [V3]
├── burn_subtitles.py           字幕 ASS 生成
├── generate_cover.py           封面生成
├── generate_cover_image.py     Chrome-rendered 封面
├── add_chapter_bar.py          章节进度条
├── export_capcut.py            剪映工程导出
├── export_edl.py               NLE handoff EDL + manifest          [V3]
├── generate_standup_timeline.py Remotion timeline
├── multi_export.py             三平台导出                       [V3]
├── generate_caption.py         标题/正文/标签                   [V3]
├── pipeline_manifest.py        生产线 artifact 状态清单/发布门禁 [V3]
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
