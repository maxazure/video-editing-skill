---
name: video-editing
description: "Xiaohongshu/RED-tuned short-form video workflow for voice-over, talking-head, tutorials, interviews, podcasts, screen recordings, B-roll, captions, and generated assets. Covers edit routing, transcription, semantic review, multi-take/audio sync/stabilization/dead-air cleanup, highlights/shorts, story and source gates, enrichment and video-generation planning, reference-frame preflight, generated clip/sequence review, color/speed/J-L cuts, reversible revisions and recipes, preflight/render/QA/audio master, edit comparison, retention/subtitle/safe-area/speech/lip-sync QA, subtitles, CapCut, platform/size exports, covers, captions, publish packages, dashboards, handoff formats, and Remotion."
metadata: { "openclaw": { "emoji": "🎬", "os": ["darwin", "linux", "win32"], "requires": { "bins": ["ffmpeg", "python3"] }, "install": [{ "id": "ffmpeg-brew", "kind": "brew", "formula": "ffmpeg", "bins": ["ffmpeg"], "label": "Install FFmpeg (brew)" }] } }
---

# Video Editing Skill — 视频剪辑技能（V3）

适配 **小红书 / 抖音 / 微信视频号** 三大主流平台。一条 **从素材导入 → 口播 → 重组故事 → 平台守门 → 自动丰富 → 渲染 → 三平台导出 → 标题文案** 的端到端流水线，按各平台的算法、比例、时长、审核规则调过参——不只是剪辑工具。

## V3 完整流水线（一图看懂）

```
口播音频 + 无声素材
   │
   ├─→ project_bootstrap.py     原始素材目录 → source inventory / project.md
   ├─→ edit_brief_plan.py       用户一句话需求 → 本地脚本 runbook / gates
   ├─→ transcribe.py            转写 + 词级时间戳 + 口误标记
   ├─→ semantic_transcript_review.py
   │                            全篇前后文审校包 / 最小补丁验证 / 人工 choices gate
   ├─→ transcript_review.py     本地同步媒体 HTML 校稿 / CPS 提示 / review.txt 回写
   ├─→ takes_pack.py            多 take / Scribe transcript → phrase-level 阅读视图
   │                            speaker / audio_event 编辑节拍
   ├─→ script_alignment.py      已审目标稿 → 多 take 原话候选 / choices / render_config
   │                            词/段边界 / 透明分数 / 歧义与缺素材 gate
   ├─→ audio_sync.py            外录音轨自动对齐 / 替换音轨计划
   ├─→ multicam_sync.py         多机位 → 参考时间线 / 时钟漂移证据 / 对齐预览 gate
   ├─→ scene_boundaries.py      fixed/adaptive 视觉切点 + 逐切点 evidence
   ├─→ visual_dedupe.py         多来源场景 → 感知哈希重复组 / 保留建议 / review gate
   ├─→ video_understanding.py   抽样帧 + 可选 YOLO 检测 / tracks / scene_tags
   ├─→ video_stabilization.py   手持素材 → source-bound 后端计划 / 工作副本 / 全长 A/B gate
   ├─→ highlight_picker.py      长视频精华候选 / brief-query 定向找片段
   ├─→ audio_boundary_snap.py   已选片段 → 词/句末/静音边界校正
   ├─→ shorts_batch.py          精华候选 → per-short render_config / render + QA job sheet
   ├─→ rough_cut.py             ASR 粗剪：去纯口头禅 / 相邻重复句
   ├─→ hook_variants.py         前三秒 hook 批量角度 / 推荐排序 / 风险检查
   ├─→ rewrite_script.py        LLM 重组 5 段式（hook/pain/turn/value/cta）
   ├─→ content_guard.py         80+ 条平台雷区 lint
   ├─→ source_receipts.py       事实 claim → URL/截图 source deck + publish gate
   ├─→ beat_sync.py             BGM beat-grid → 可审计剪辑骨架，或吸附已有切点
   ├─→ speed_ramp.py            impact ranges → source-bound 局部变速计划 / 验证 / apply
   ├─→ auto_enrich.py           B-roll / 章节卡 / 贴纸 / 强调点 / BGM 卡点 / imagegen 提示词
   │       └─→ Codex imagegen   gpt-image-2 自动生图（抽象概念配图）
   ├─→ audio_cue_sheet.py       BGM / SFX 音频设计清单 / 生成审批 gate
   ├─→ storyboard_plan.py       分镜 shot cards / 生成路由 / 连续性锚点
   ├─→ video_prompt_pack.py     Dreamina/Veo/LTX/Wan/Sora 提示词包 / 审批 gate
   ├─→ reference_frame_preflight.py
   │                            首帧/style key 尺寸/方向/透明背景/画幅 gate
   ├─→ generation_task_log.py   异步生成任务台账 / submit_id / 下载 gate
   ├─→ generated_clip_review.py 生成片段 contact sheet / 常识物理 / 连续性 / 重生 gate
   ├─→ generated_sequence_review.py
   │                            已审片段相邻尾帧/首帧/预览 / 跨镜头连续性 gate
   ├─→ generation_lessons.py    已审片段 → provider/model scoped 提示词经验库
   ├─→ storyboard_assets.py     素材任务清单 / ready 预检 / paid 额度提醒
   │                            可选 media_library.py recommend 排名 B-roll 候选
   ├─→ stock_material_plan.py   远程 stock 搜索规划
   │                            Pexels / Pixabay / Coverr 查询计划 + 素材登记提示
   ├─→ screen_focus.py          录屏点击/热点 → 自动聚焦计划
   ├─→ pip_overlay.py           录屏 + facecam → PIP 小窗计划
   ├─→ color_grade.py           bounded 调色 plan / render_final 单次编码接入
   ├─→ jump_cut.py              自适应去停顿 + 20% 删除预算 + 可审计 cut list + 30ms 防爆音 fade
   ├─→ multimodal_dead_air.py   静音 AND 静帧 → source-bound 保守删段 / 单次编码 / live gate
   ├─→ audio_transition.py      显式 J-cut/L-cut → source handle / hash / 1× 试听 gate
   ├─→ edit_revision.py         文本剪辑 artifact → source-bound 审批 / 成组 apply / undo / redo
   ├─→ edit_recipe.py           已审 render_config → typed-slot 可移植配方 / 新素材绑定回放
   ├─→ edit_preflight.py        render_config/enrich_plan/cut list 渲染前预检 gate
   ├─→ platform_safe_area_qa.py 字幕/PIP/CTA/marker → 平台 UI 安全区 gate + SVG guide
   ├─→ subtitle_style_preview.py 真实源帧 → 最终 ASS 预设对比 JPEG / 选择 / live gate
   ├─→ render_final.py          单次编码渲染（可选口播降噪 + enrich_plan/focus_events/pip_overlays + Heavy 字幕 + 响度规范化 + BGM ducking）
   │                            可选 --versioned-output 防覆盖旧成片
   ├─→ render_qa.py             渲染后黑屏/静帧/静音/尺寸质检 + review packet
   ├─→ shot_color_qa.py         成片镜头亮度/对比/色度/饱和度/broadcast-range + 切点跳变 gate
   ├─→ retention_rhythm_qa.py   成片 hook 活动 / 长镜头 / 注意力空窗 / 节奏 gate
   ├─→ reference_edit_rhythm.py 参考片 vs 成片 hard-cut 结构 / contact sheets / live gate
   ├─→ speech_continuity_qa.py  成片二次 ASR → 切点复读 / 近重复 take / 句内口吃 gate
   ├─→ lip_sync_review.py       最终 master → 1×/0.25× 口型证据 / 人工 audit / live gate
   ├─→ review_proxy.py          低码率完整审片 MP4 / 可见时间码 / faststart
   ├─→ timeline_view.py         源素材删除段 / 成片输出切点 filmstrip + waveform 复盘图
   ├─→ edit_compare.py          原片连续时钟 vs 最终像素 / 删段置黑 / 映射验证
   ├─→ subtitle_pack.py         SRT/VTT/ASS/JSON 字幕交付包（speed/offset 对齐）
   ├─→ subtitle_readability_qa.py
   │                            最终字幕 CPS / 时长 / 行长 / 重叠 / 媒体越界 gate
   ├─→ import_capcut_subtitles.py
   │                            剪映/CapCut 自动字幕 → transcript / gap cut list
   ├─→ srt_edit_plan.py         SRT + keep/drop 编辑指令 → render_config / cut list
   ├─→ project_resume.py        续跑上下文包 / agent handoff / 可选 CLAUDE.md
   ├─→ review_dashboard.py      静态 HTML/JSON 人工复核面板 / gate review queue
   ├─→ export_edl.py            render_config / cut list → EDL + manifest
   ├─→ export_fcpxml.py         render_config / cut list → FCPXML + manifest
   ├─→ export_otio.py           render_config / cut list → OTIO + manifest
   ├─→ multi_export.py          小红书 3:4 / 抖音 9:16 / 视频号 ≤60s
   ├─→ hdr_sdr.py               PQ/HLG HDR → source-bound Rec.709 SDR / 完整解码 gate
   ├─→ delivery_encode.py       source-bound 两遍 H.264/AAC / 硬大小上限 / 完整解码 gate
   ├─→ generate_caption.py      标题 + 200-500 字正文 + 3-6 tags + 发布时段
   ├─→ cover_variants.py        2-4 套封面 / feed-size 预览 / 最终选择 gate
   ├─→ approval_receipt.py      已复核交付件 → SHA-256 收据 / stale approval gate
   └─→ publish_package.py       平台视频/封面/字幕/章节/文案上传包 + gate 状态
```

**每天做一条短视频的完整提示词模板**：[docs/prompts/15-xhs-daily-tech-video.md](docs/prompts/15-xhs-daily-tech-video.md)（推荐入口）。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## V3 新增脚本一览（按调用顺序）

| 脚本 | 职责 | 关键 CLI |
|---|---|---|
| `project_bootstrap.py` | 原始素材目录 → 项目结构 / source inventory / project.md | `--source raw_dir` `--project-dir work/day61` `--mode copy|hardlink` `--strict` |
| `edit_brief_plan.py` | 自然语言剪辑需求 → 本地脚本 runbook / 命令 / manifest gate | `--brief` `--brief-file` `--source-media` `--platform` `--markdown` `--strict` |
| `transcript_review.py` | transcript → 文本或本地同步媒体 HTML 校稿 → reviewed transcript | `export` / `html --video --max-cps` / `apply --review --output` |
| `semantic_transcript_review.py` | transcript → 前后文审校包 / 最小补丁审计 / 人工 choices / reviewed transcript | `prepare` / `audit --strict` / `apply --choices` |
| `_internal_text_guard.py` | 拦截内部 token 进画面 | 内部模块，render_final 自动调 |
| `content_guard.py` | 平台雷区 lint | `--script` `--title` `--caption` `--strict` |
| `source_receipts.py` | 事实 claim → URL/截图 proof deck、Markdown/HTML 和发布 gate | `--claims source_claims.json` `--html` `--require-primary-source` `--strict` |
| `takes_pack.py` | 多 take / 顶层 Scribe words → phrase-level Markdown/JSON，保留 speaker/audio events | `--transcript take1=...` `--transcripts-dir` `--json` `--break-gap` |
| `script_alignment.py` | 已审目标稿 → 多 take 词/段边界候选、人工 choices、render_config 和 gate | `--target-script` `--transcript label=...` `--media label=...` `--choices` `--render-config` `--strict` |
| `audio_sync.py` | scratch audio + 外录音轨 → offset / 替换音轨命令 / gate | `--reference-media` `--external-audio` `--replace-output` `--apply` `--strict` |
| `multicam_sync.py` | 多机位 → offset/coverage/音轨选择/pairwise/可选时钟漂移/对齐预览 gate | `--reference-media` `--angle` `--manual-offset` `--measure-clock-drift` `--preview-output` `--apply-preview` `--strict` |
| `rough_cut.py` | transcript 粗剪：去口头禅/重复句 | `--transcript` `--cut-list` / `--input` `--output` |
| `scene_boundaries.py` | FFmpeg fixed/adaptive scene score → 场景边界 + cut evidence | `--method adaptive` `--adaptive-threshold` `--min-scene-score` `--min-scene-duration` |
| `visual_dedupe.py` | 多来源场景三点感知哈希 → 重复组 / 保留建议 / review gate | `--manifest` / `<videos...>` `--hamming-threshold` `--include-same-source` `--strict` |
| `video_understanding.py` | 抽样帧 + 可选 YOLO 物体检测 + 轻量 tracklets | `--detector yolo` `--scene-boundaries` `--external-detections` `--strict` |
| `video_stabilization.py` | 源视频 → exact FFmpeg backend / source hash / 稳定工作副本 / 全长 A/B 复核 gate | `doctor` / `plan --decision` / `apply --comparison` / `confirm` / `verify --strict` |
| `highlight_picker.py` | 长视频精华候选 + brief/query 定向找片段 | `--transcript` `--brief`/`--query` `--scene-boundaries` `--render-config` |
| `audio_boundary_snap.py` | selected highlights → 词级、句末和静音边界校正 + blocker | `--candidates` `--transcript` `--media` `--markdown` `--strict` |
| `shorts_batch.py` | highlight_candidates → 多条短视频 render_config / render + QA job sheet | `--highlights` `--video` `--render-config-dir` `--output-dir` `--strict` |
| `hook_variants.py` | transcript/clean_script → 8 个前三秒 hook 角度、风险检查和推荐排序 | `--transcript` `--topic` `--platform` `--output` `--markdown` `--strict` |
| `rewrite_script.py` | LLM 5 段式重组 + 验证 | `--transcript` `--structure` `--hook-template` `--emit-prompt` / `--llm-output` |
| `auto_broll.py` | B-roll 调度 | `--transcript` `--assets` `--max-single-shot` |
| `media_library.py` | 本地素材库索引 + B-roll 候选推荐 + 素材来源登记 | `init` `scan` `search` `recommend --category broll --json` `import` `annotate` |
| `stock_material_plan.py` | 主题/脚本 → Pexels/Pixabay/Coverr stock 查询计划 | `--subject` `--script` `--provider` `--media-library` `--output` `--markdown` |
| `auto_chapter_cards.py` | 章节卡 PNG | `--script` `--audio` `--style` `--output-dir` |
| `auto_stickers.py` | 情绪→贴纸 | `--transcript` `--min-interval` |
| `auto_emphasis.py` | 问句/数字/转折/结论 → badge + subtle push-in cues | `--transcript` `--output` `--markdown` |
| `beat_sync.py` | BGM → beat edit slots / Markdown review，或吸附已有切点 | `--bgm --generate-plan --beats-per-cut 4 --output ... --markdown ...` / `--cuts --window` |
| `speed_ramp.py` | impact ranges → snap/ease/s-curve/hold 计划、digest 验证和本地事务式 apply | `plan <video> --ramp --hold --interpolate-fps` / `verify --strict` / `apply --output --receipt` |
| `auto_enrich.py` | 编排 B-roll / 贴纸 / 强调点 / 章节卡 / imagegen cues | `--transcript` `--clean-script` `--bgm` `--output` |
| `imagegen_hint.py` | 检测抽象概念 → 产 gpt-image-2 提示词 | `--transcript` `--clean-script` `--codex-md` |
| `audio_cue_sheet.py` | transcript → BGM/SFX cue、生成审批和音频门禁 | `--transcript` `--asset-root` `--require-local-music` `--require-local-sfx` `--strict` |
| `storyboard_plan.py` | transcript/clean_script → 分镜 shot cards + 生成路由 | `--transcript` `--clean-script` `--output` `--markdown` |
| `video_prompt_pack.py` | storyboard_plan → 多 provider 视频生成提示词包 + 角色/品牌/style lock + paid approval gate | `--storyboard-plan` `--style-reference` `--provider` `--animate-stills` `--approved` `--strict` |
| `reference_frame_preflight.py` | video_prompt_pack → 首帧/style key 存在性、解码、尺寸、方向、画幅、透明背景 gate | `--prompt-pack` `--require-style-reference` `--reference shot_id=...` `--strict` |
| `generation_task_log.py` | 异步生成任务台账：submit_id/task id、轮询、下载、本地落盘 gate | `add` `update` `import-provider-decision` `report --strict` |
| `generated_clip_review.py` | 生成视频片段 source-bound 视觉复核：contact sheet、评分、裁切范围、重生建议 | `prepare --clip/--asset-manifest` `audit --request --response` `verify --report --strict` |
| `generated_sequence_review.py` | 已审生成片段跨镜头连续性复核：真实尾帧/首帧、并排图、无声边界预览和 live gate | `prepare --clip-review [--storyboard-plan]` `audit --request --response` `verify --report --strict` |
| `generation_lessons.py` | 从 canonical generated-clip review 提取经明确批准的 provider/model/category 经验，并供下一次 prompt pack 选择 | `add --review --clip-id --lesson --approved-by [--supersedes]` `verify --strict` `select --provider [--model]` |
| `storyboard_assets.py` | storyboard_plan → 素材清单 + ready/paid 预检 | `--storyboard-plan` `--asset-root` `--output` `--strict` |
| `screen_focus.py` | 录屏点击/热点 → 聚焦 zoom enrich plan | `--events` `--event` `--screen-width` `--output` |
| `pip_overlay.py` | 录屏 + facecam → PIP 摄像头小窗 enrich plan | `--camera` `--segment` `--sync-offset` `--output` |
| `color_grade.py` | bounded 调色 plan + FFmpeg filter + 可选现有 master 复版 | `--preset` `--output` `--markdown` `--render-output` `--strict` |
| `jump_cut.py` | 自适应静音检测 → 去停顿计划 / 删除预算 gate / 成片 + 切点音频 fade | `<input.mp4>` `--dry-run` `--cut-list cuts.json` `--strict` / `--output jumpcut.mp4` `--max-removal-ratio 0.20` `--allow-over-budget` |
| `multimodal_dead_air.py` | 静音 + 静帧交集 → source-bound 死区计划 / live verify / 单次编码工作副本 | `plan --delivery --output --markdown --strict` / `verify --strict` / `apply --markdown` |
| `audio_transition.py` | render_config → 显式 J-cut/L-cut source handle / hash / 单次编码 apply / receipt | `plan --transition AFTER_CLIP,TYPE,DURATION` / `apply --output` / `verify --receipt --strict` |
| `edit_revision.py` | render_config/enrich_plan 等文本 artifact → source-bound proposal / 独立审批 / 成组 apply / undo / redo | `prepare --artifact --depends-on` / `audit --strict` / `apply --approval` / `status|undo|redo` |
| `edit_recipe.py` | 已审 render_config → typed-slot 无路径配方 / digest 验证 / 绑定新素材回放 | `export --config --name` / `verify --recipe` / `replay --bind SLOT=PATH --receipt --strict` |
| `edit_preflight.py` | render_config/enrich_plan/cut list 渲染前预检 gate | `--config render_config.json` `--enrich-plan enrich_plan.json` `--output edit_preflight.json` `--strict` |
| `platform_safe_area_qa.py` | 字幕、badge、PIP、CTA、章节卡、marker → 平台 UI 遮挡 gate + SVG guide | `--config` `--enrich-plan` `--elements` `--platform xhs|douyin|wxch` `--guide` `--strict` |
| `subtitle_style_preview.py` | 真实源帧 → `render_final.py` 最终 ASS 样式对比 JPEG、人工选择与 source/font/style live gate | `create --video --platform --preview-dir --require-selection` / `select --report --style` / `verify --strict` |
| `render_final.py` | 单次编码渲染 + 可选口播降噪 / J-cut/L-cut / enrich_plan / 旁白驱动 BGM ducking | `--config render_config.json` `--audio-transition-plan audio_transition_plan.json` `--speech-denoise light` `--enrich-plan enrich_plan.json` `--bgm-ducking` `--output final.mp4` |
| `render_qa.py` | 渲染后 QA：尺寸/音频/黑屏/静帧/静音 + review packet | `<video.mp4>` `--platform douyin` `--json qa.json` `--review-dir verify/qa` |
| `shot_color_qa.py` | rendered master → 镜头亮度/对比/色度/饱和度/broadcast-range 与切点跳变 gate | `<video.mp4>` `--scene-boundaries` `--output shot_color_qa.json` `--markdown` `--strict` |
| `retention_rhythm_qa.py` | 成片 hook 活动、长镜头、注意力空窗、等距/快切和字幕节奏风险 | `<video.mp4>` `--timed-text subtitles.json` `--output retention_rhythm_qa.json` `--strict` |
| `reference_edit_rhythm.py` | 参考片 vs 成片 hard-cut 密度、镜头时长、结尾 hold、归一化切点与 contact-sheet source-bound 对照 | `analyze --reference --candidate --evidence-dir [--require-match]` / `verify --report --strict` |
| `speech_continuity_qa.py` | 成片二次 transcript → 复读 / 近重复 take / 句内口吃 gate | `<final_transcript.json>` `--output speech_continuity_qa.json` `--markdown` `--strict` |
| `lip_sync_review.py` | 最终 master → 完整短语 1× 带声 / 0.25× 静音 proof、口型人工 audit 与 source-bound live gate | `prepare --video --segment --anchor --proof-dir` / `audit --request --response --strict` / `verify --report --strict` |
| `review_proxy.py` | master/platform MP4 → 低码率 timecoded 审片视频 + JSON/Markdown | `<video.mp4>` `--output verify/review_proxy.mp4` `--dry-run` `--no-timecode` |
| `audio_master_report.py` | 成片响度报告：LUFS / true peak / LRA / 长静音 gate | `<video.mp4>` `--output audio_master_report.json` `--markdown audio_master_report.md` `--strict` |
| `timeline_view.py` | 源素材删除段 / 成片输出切点可视化复盘图 | `<video.mp4>` `--at 42.5` `--output view.png` / `--rendered-cut-list cuts.json` `--output-dir verify/` |
| `edit_compare.py` | 原片连续时钟 vs 最终像素双栏视频；删段置黑并验证映射 | `<source.mp4> <final.mp4>` `--cut-list` `--output-speed` `--output-offset` `--output` |
| `subtitle_pack.py` | transcript/render_config → SRT/VTT/ASS/JSON 字幕包 | `--transcript work/transcript.json --output-dir output/subtitles` / `--config render_config.json --speed 1.25 --offset 2.0` |
| `subtitle_readability_qa.py` | output-aligned 字幕 → CPS、时长、行长、重叠和媒体越界 gate | `<subtitle_pack.json>` `--media final.mp4` `--output subtitle_readability_qa.json` `--strict` |
| `import_capcut_subtitles.py` | 剪映/CapCut 自动字幕或 SRT → transcript + gap cut list | `--draft <draft_dir>` / `--srt captions.srt` `--transcript work/capcut_transcript.json` `--cut-list work/capcut_gap_cut.json` |
| `srt_edit_plan.py` | SRT + 人工/agent keep/drop 指令 → edit plan / render_config / cut list | `--srt captions.srt --guide edit_guide.md --source-media origin/talking.mp4 --render-config work/render_config.json --strict` |
| `project_resume.py` | 本地 artifacts → 续跑上下文包 / agent handoff | `--project-dir work/day58 --markdown work/day58/project_resume.md --agent-note work/day58/CLAUDE.md` |
| `review_dashboard.py` | 本地 artifacts → 静态 HTML/JSON review queue + gate snapshot | `--project-dir work/day58 --html work/day58/review_dashboard.html --strict` |
| `export_edl.py` | NLE handoff：导出 EDL + manifest | `--config render_config.json --output edit.edl` / `--cut-list rough_cut.json --output rough.edl` |
| `export_fcpxml.py` | NLE handoff：导出 FCPXML + manifest | `--config render_config.json --output edit.fcpxml` / `--cut-list rough_cut.json --output rough.fcpxml` |
| `export_otio.py` | NLE handoff：导出 OpenTimelineIO `.otio` + manifest | `--config render_config.json --output edit.otio` / `--cut-list rough_cut.json --output rough.otio` |
| `multi_export.py` | 三平台导出 | `<input.mp4>` `--platforms xhs douyin wxch` |
| `hdr_sdr.py` | PQ/HLG master → source-bound Hable tone-map / BT.709 limited tags / 完整解码 gate | `plan --delivery` / `apply` / `verify --strict` |
| `delivery_encode.py` | master → 目标大小 H.264/AAC MP4 / source hash / 两遍码率 / 完整解码 gate | `plan --delivery --max-size-mib` / `apply` / `verify --strict` |
| `generate_caption.py` | 标题/正文/tag | `--script` `--profile` `--output` |
| `cover_variants.py` | 多套封面 A/B 方案、feed-size 预览、标题协同和最终选择 | `<video>` `--title` `--caption` `--platform` `--render` `--select cover-c` `--strict` |
| `approval_receipt.py` | 已复核视频/封面/文案/字幕/QA → SHA-256 收据和过期审批门禁 | `create --artifact ... --approved-by` / `verify --strict` |
| `publish_package.py` | 发布上传包：平台视频、封面、字幕、章节、文案和 gate 状态 | `--project-dir` `--platforms` `--video xhs=...` `--strict` |
| `profiles/__init__.py` | 受众档位加载 | `load_profile("tech_pro")` |

## V3 新增 render_final.py 标志位

| 标志 | 默认 | 说明 |
|---|---|---|
| `--profile tech_pro` | 关 | 加载 [scripts/profiles/tech_pro.yaml](scripts/profiles/tech_pro.yaml) 的节奏/字幕/BGM 默认值 |
| `--primary-speed 1.25` | 1.0 | 主输出速度。`--speed` 仍可加额外变种 |
| `--no-loudnorm` | 不传 = 开启响度规范化 | 关闭 `dynaudnorm + acompressor + loudnorm` |
| `--speech-denoise light|medium|strong` | off | 80 Hz 高通 + 6/9/12 dB `afftdn`，在变速/响度/BGM ducking 前清理稳态底噪；`--no-speech-denoise` 覆盖 config |
| `--no-content-guard` | 不传 = 开启 lint | 关闭平台规则检查（不推荐） |
| `--subtitle-style karaoke` | normal | 逐词卡拉 OK 字幕 |
| `--enrich-plan work/enrich_plan.json` | 关 | 可重复传入；自动接入 B-roll / 章节卡 / 贴纸 / 生成图 / focus_events / pip_overlays |
| `--color-grade work/color_grade.json` | 关 | 接入 `color_grade.py` 输出或 preset，放在字幕/HUD 前 |
| `--bgm-ducking` | 关 | 用最终旁白轨触发 FFmpeg sidechain，动态压低 BGM；`--no-bgm-ducking` 可覆盖 config |
| `--versioned-output` | 关 | 输出到下一个 `<name>_V<N>.mp4`，避免覆盖上一版成片 |

## V3 Day58 production 教训（已编码进默认行为）

| 教训 | V3 怎么解决 |
|---|---|
| 顶部漏 `1.25x` 这种内部 token | `_internal_text_guard` 自动拒绝，规则在 [scripts/_internal_text_guard.py](scripts/_internal_text_guard.py) |
| 字幕 Hiragino W3 太细 | `find_chinese_font()` 默认排序：Source Han Sans Heavy > Smiley Sans > STHeiti Medium > PingFang Semibold |
| 加速后中段听不清 | render_final 默认 `dynaudnorm=f=250:g=15 + acompressor=threshold=-18dB:ratio=3 + loudnorm=I=-16:TP=-1.5:LRA=11` |
| 1.25× 想做主输出但 `--speed` 还留 1.0× | 新增 `--primary-speed` 一等公民 |
| 字幕里 Whisper 错词（ChatGPTT 等） | `rewrite_script.py` 走清稿优先，原 Whisper 词只供时间戳 |
| 平台违规词被发现才知道（限流） | `content_guard.py` 渲染前自动 lint |

---

# 旧版（V2）参考资料

下面是 V2 时代的工作流文档。仍然有效，但日常使用推荐先看 docs/prompts/15。

## Prerequisites（前置要求）

在执行任何操作之前，先运行环境检测：

```bash
python3 scripts/utils.py
```

这会自动检测平台（macOS/Linux/WSL/Windows）、GPU 类型、可用编码器、Whisper 引擎，并给出诊断报告。

## Prerequisites（前置要求）

在执行任何操作之前，先运行环境检测：

```bash
python3 scripts/utils.py
```

这会自动检测平台（macOS/Linux/WSL/Windows）、GPU 类型、可用编码器、Whisper 引擎，并给出诊断报告。

如果缺少依赖，提示用户安装：
- **ffmpeg**: `brew install ffmpeg`（macOS）或 `apt install ffmpeg`（Linux/WSL）或下载 Windows 版本
- **whisper**:
  - **Apple Silicon (M1/M2/M3/M4)**: `pip install mlx-whisper`（推荐，Metal 加速最快）
  - **NVIDIA / CPU**: `pip install faster-whisper`（推荐，速度快 4 倍）或 `pip install openai-whisper`
- **中国用户**加速安装（Apple Silicon）：`pip install mlx-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple`
  其他平台：`pip install faster-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple`

如果项目根目录有 `.venv` 虚拟环境，运行 Python 脚本前先激活：
```bash
source .venv/bin/activate  # macOS/Linux/WSL
# Windows: .venv\Scripts\activate
```

### 平台说明

- **macOS (Apple Silicon)**: 自动使用 VideoToolbox 硬件编码加速；Whisper 引擎自动选 `mlx-whisper`（已安装），推荐 large-v3-turbo 模型（走 `mlx-community/whisper-large-v3-turbo`）
- **字幕字体（短视频）**: 默认优先选 Heavy / Medium 字重的中文字体：用户库的 `Source Han Sans SC Heavy` / `Smiley Sans` > 系统 `STHeiti Medium` > `PingFang SC Semibold`。如果都没有，会自动从 [adobe-fonts/source-han-sans](https://github.com/adobe-fonts/source-han-sans) 下载 Heavy 字重缓存到 `~/.cache/video-editing/fonts/`。绝不再默认使用 Hiragino W3 这类细字
- **macOS (Intel)**: 使用 VideoToolbox 编码，Whisper 使用 CPU 模式
- **Linux**: 自动检测 NVIDIA GPU (NVENC)、Intel QSV、AMD AMF
- **WSL**: 支持，自动检测 Windows 字体路径 (`/mnt/c/Windows/Fonts/`)
- **Windows**: 建议使用 WSL2 环境运行；支持 QSV/AMF 硬件编码
- **无独显 (集成显卡)**: Intel iGPU 使用 QSV 编码，AMD iGPU 使用 AMF 编码；Whisper 建议 medium 模型（而非 large）
- **中国用户**: 自动检测中国区域，使用清华 pip 镜像和 HuggingFace 镜像下载模型，也可通过 `--mirror` 参数强制启用

### Linux GPU 配置指南（NVIDIA / Intel Arc）

在 Linux 上使用 GPU 加速 Whisper 语音识别时，不同显卡需要不同的配置方案。运行 `python3 scripts/utils.py` 会自动检测显卡型号并给出建议，但如果遇到问题，请参考以下方案。

#### 方案 A：NVIDIA 40 系列显卡（RTX 4060 / 4070 / 4080 / 4090）

40 系列（Ada Lovelace 架构，Compute Capability 8.9）对 faster-whisper 支持最成熟，开箱即用。

**安装步骤：**
```bash
# 1. 安装 NVIDIA 驱动（535+）和 CUDA Toolkit 12.4+
sudo apt install nvidia-driver-535 nvidia-cuda-toolkit
# 或从 NVIDIA 官网安装最新驱动：https://www.nvidia.com/drivers

# 2. 验证 CUDA
nvidia-smi  # 应显示驱动版本和 CUDA 版本

# 3. 安装 faster-whisper（自动安装匹配的 CTranslate2）
pip install faster-whisper>=1.1.0
```

**配置说明：**
- CUDA Toolkit: 12.4+（推荐 12.6）
- CTranslate2: >= 4.5.0（自动随 faster-whisper 安装）
- 计算精度: `float16`（默认）, `int8_float16`, `int8` 均可使用
- Whisper 模型: 推荐 `large-v3`（VRAM >= 6GB）
- 无需特殊配置，`python3 scripts/transcribe.py` 会自动检测并使用 CUDA

#### 方案 B：NVIDIA 50 系列显卡（RTX 5060 / 5060 Ti / 5070 / 5080 / 5090）

50 系列（Blackwell 架构，Compute Capability 12.0，sm_120）需要额外注意 CUDA 版本和计算精度设置。

**已知问题：**
CTranslate2 在 Blackwell 架构上使用 INT8 精度时会报错 `cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED`，
这是因为 Blackwell 的 INT8 Tensor Core 需要矩阵维度为 16 的倍数对齐。CTranslate2 >= 4.7.1 已修复此问题，
但为保险起见，本工具在检测到 50 系列显卡时会自动使用 `float16` 精度。

**安装步骤：**
```bash
# 1. 安装 NVIDIA 驱动（565+，必须支持 Blackwell）
#    从 NVIDIA 官网下载最新驱动：https://www.nvidia.com/drivers
#    或使用包管理器安装 565 以上版本
sudo apt install nvidia-driver-565

# 2. 安装 CUDA Toolkit 12.8+（Blackwell 最低要求）
#    推荐从 NVIDIA 官网安装：https://developer.nvidia.com/cuda-downloads
#    选择 Linux > x86_64 > Ubuntu > deb (network)

# 3. 验证 CUDA
nvidia-smi  # 应显示 CUDA 12.8+

# 4. 安装 faster-whisper 和最新 CTranslate2
pip install faster-whisper>=1.1.0
pip install --upgrade ctranslate2>=4.7.1  # 确保包含 Blackwell 修复

# 5. 如果仍然报错，强制使用 float16 精度（本工具已自动处理）
#    手动测试：
python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('tiny', device='cuda', compute_type='float16')
print('CUDA float16 OK')
"
```

**配置说明：**
- CUDA Toolkit: >= 12.8（推荐 13.0+，最新为 13.2）
- NVIDIA 驱动: >= 565
- CTranslate2: >= 4.7.1（包含 INT8 padding 修复）
- 计算精度: 推荐 `float16`（最稳定）；`int8_float16` 在 CTranslate2 >= 4.7.1 上可能可用
- 如果 `int8` 仍然报错，工具会自动降级到 `float16`
- Whisper 模型: 推荐 `large-v3`（VRAM >= 6GB）
- `utils.py` 会自动检测 50 系列显卡（通过 `nvidia-smi` 查询 GPU 名称中的 "RTX 50"），并选择安全的 `float16` 精度

**排错：**
如果出现 `CUBLAS_STATUS_NOT_SUPPORTED` 错误：
1. 确认 CTranslate2 版本 >= 4.7.1：`python3 -c "import ctranslate2; print(ctranslate2.__version__)"`
2. 确认 CUDA 版本 >= 12.8：`nvidia-smi` 或 `nvcc --version`
3. 尝试手动指定 `--compute-type float16`（如果直接使用 transcribe.py 的话）
4. 确认驱动版本 >= 565：`nvidia-smi` 查看 Driver Version

#### 方案 C：Intel Arc 独立显卡（A770 / A750 / B580）

Intel Arc 显卡**不支持 CUDA**，因此 faster-whisper（依赖 CTranslate2/CUDA）无法直接在 Intel Arc 上 GPU 加速。
需要使用替代方案。

**推荐方案：OpenVINO + Whisper（最易用）**
```bash
# 1. 安装 OpenVINO
pip install openvino openvino-genai

# 2. 使用 OpenVINO GenAI 的 WhisperPipeline
python3 -c "
import openvino_genai as ov_genai
pipe = ov_genai.WhisperPipeline('OpenVINO/whisper-large-v3-fp16-ov', device='GPU')
result = pipe.generate('audio.wav', language='<|zh|>')
print(result.texts[0])
"

# 3. 或使用 Hugging Face 预转换模型
pip install optimum[openvino]
# 从 HuggingFace 下载 OpenVINO 格式 Whisper 模型
# https://huggingface.co/OpenVINO/whisper-medium-int8-ov
```

**备选方案：whisper.cpp + SYCL（性能更好，配置更复杂）**
```bash
# 1. 安装 Intel oneAPI Base Toolkit
#    https://www.intel.com/content/www/us/en/developer/tools/oneapi/base-toolkit-download.html
wget -O- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
  | gpg --dearmor | sudo tee /usr/share/keyrings/oneapi-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] \
  https://apt.repos.intel.com/oneapi all main" \
  | sudo tee /etc/apt/sources.list.d/oneAPI.list
sudo apt update && sudo apt install intel-oneapi-base-toolkit

# 2. 编译 whisper.cpp（启用 SYCL 后端）
source /opt/intel/oneapi/setvars.sh
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build -DWHISPER_SYCL=ON
cmake --build build --config Release

# 3. 下载 Whisper 模型并运行
./build/bin/whisper-cli -m models/ggml-large-v3.bin -f audio.wav -l zh
```

**配置说明：**
- Intel Arc 不支持 CUDA，faster-whisper 在 Intel Arc 上只能用 CPU 模式
- OpenVINO 方案最简单，支持 Arc A770/A750/B580 和集成显卡
- whisper.cpp + SYCL 性能更好（A770 上接近 NVIDIA 中端显卡水平），但需要 oneAPI 环境
- B580（Battlemage 架构）的 SYCL 支持尚在优化中，A770 目前更稳定
- 推荐 Whisper 模型: `medium`（12GB VRAM 的 A770）或 `small`（8GB VRAM 的 A750）
- 如果用户有 Intel Arc 显卡，脚本会自动检测并使用 CPU 模式运行 faster-whisper（作为 fallback）

#### GPU 配置速查表

| 显卡系列 | 架构 | CUDA Toolkit | 驱动版本 | CTranslate2 | 计算精度 | Whisper 引擎 |
|---------|------|-------------|---------|-------------|---------|-------------|
| RTX 40xx | Ada Lovelace (sm_89) | >= 12.4 | >= 535 | >= 4.5.0 | float16 / int8 均可 | faster-whisper |
| RTX 50xx | Blackwell (sm_120) | >= 12.8 | >= 565 | >= 4.7.1 | **float16**（推荐） | faster-whisper |
| Intel Arc | Xe HPG / Battlemage | N/A | i915 | N/A | N/A | OpenVINO 或 whisper.cpp+SYCL |
| Intel iGPU | 集成显卡 | N/A | i915 | N/A | int8 (CPU) | faster-whisper (CPU 模式) |
| 无独显 | CPU | N/A | N/A | 任意版本 | int8 (CPU) | faster-whisper (CPU 模式) |

## Workflow（工作流程）

### Phase 0a: Project Bootstrap（项目启动 / 原始素材导入）

当用户给的是一个原始素材文件夹、下载目录或一组还没整理的素材时，先用 [project_bootstrap.py](./scripts/project_bootstrap.py) 建立稳定项目结构，而不是直接在外部原始路径上剪辑：

```bash
python3 scripts/project_bootstrap.py \
  --source ~/Downloads/raw-shoot \
  --project-dir work/day61 \
  --title "Day61 launch edit" \
  --output work/day61/work/source_inventory.json \
  --markdown work/day61/work/source_inventory.md \
  --project-note work/day61/project.md \
  --strict
```

输出：
- `origin/raw|broll|audio|bgm|images|assets|sidecars/`：项目内 working copy。
- `work/source_inventory.json`：`project_bootstrap.v1`，记录外部 `source_path`、项目内 `project_path`、分类、动作和 next actions。
- `work/source_inventory.md`：给人审的素材清单。
- `project.md`：跨会话项目记忆。
- `next_steps.md`：下一步命令清单。

默认 `--mode copy`，同名文件自动加后缀，不覆盖。大素材同盘可用 `--mode hardlink`，失败会回退 copy 并写入 warning。此脚本不转码、不渲染、不上传、不调用 LLM，也不提交任何生成任务。需要把素材导入作为 analysis gate 时，跑：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day61 \
  --target-stage analysis \
  --require source_inventory \
  --strict
```

### Phase 0: Media Library Setup（素材库初始化）

首次使用时，帮助用户建立素材目录结构：

```bash
python3 scripts/media_library.py init [project_dir]
```

这会创建以下目录结构：
```
media/
├── raw/      — 原始素材（摄像机/手机直出的视频）
├── broll/    — B-roll 素材（城市街景、产品特写等）
├── bgm/      — 背景音乐（MP3/WAV/M4A）
├── assets/   — 叠加素材（水印 PNG、Logo 等）
└── output/   — 输出目录
```

**询问素材来源**：
1. 询问用户的视频文件位置（本地路径、外部设备或云端）
2. 建议将原始素材复制/移动到 `media/raw/` 目录
3. 询问是否有 B-roll、BGM 等辅助素材
4. 如果用户视频散落在多个目录，建议先集中到 `media/raw/`

**扫描并建立索引**：
```bash
python3 scripts/media_library.py scan [project_dir]
```

索引系统会自动：
- 扫描所有视频/音频/图片文件
- 提取时长、分辨率、帧率等元数据
- 关联已有的 transcript 文件
- 小型项目（< 200 文件）使用 JSON 索引（`media_index.json`）
- 大型项目自动升级为 SQLite 索引（`media_index.db`）
- 手动升级：`python3 scripts/media_library.py upgrade`

**查看素材库状态**：
```bash
python3 scripts/media_library.py status
```

**搜索素材**：
```bash
python3 scripts/media_library.py search "关键词"
```

**推荐 B-roll 候选**：
```bash
python3 scripts/media_library.py recommend "AI workflow dashboard" \
  --project-dir . \
  --category broll \
  --target-duration 3 \
  --target-aspect 9:16 \
  --json
```

推荐结果包含 `score`、`reasons`、`absolute_path`，用于人工/agent 先确认再写入 `render_config` 或 `enrich_plan`。默认过滤已经不存在的索引文件；需要清理 stale index 时可加 `--include-missing`。

**本地素材不足时规划 stock 查询**：
```bash
python3 scripts/stock_material_plan.py \
  --subject "AI workflow automation" \
  --script work/transcript.json \
  --provider pexels \
  --provider pixabay \
  --provider coverr \
  --media-library . \
  --output work/stock_material_plan.json \
  --markdown work/stock_material_plan.md
```

`stock_material_plan.py` 只生成 `stock_material_plan.v1` 和 Markdown review，不联网、不下载、不消耗额度。它借鉴 MoneyPrinterTurbo 的 `video_terms` / Pexels / Pixabay / Coverr / `video_count` 素材规划方式，但保持本 skill 的 artifact-first 风格。

**下载或客户给的素材确认授权后登记**：
```bash
python3 scripts/media_library.py import /path/to/downloaded.mp4 \
  --project-dir . \
  --category broll \
  --copy \
  --provider pexels \
  --source-url "https://www.pexels.com/video/demo-123/" \
  --creator "Demo Creator" \
  --license "Pexels License" \
  --tag "workflow,dashboard"

python3 scripts/media_library.py annotate media/broll/downloaded.mp4 \
  --project-dir . \
  --source-url "https://example.com/source" \
  --license "owned" \
  --tag "client-approved"
```

登记后的 `provider`、`source_url`、`creator`、`license` 会进入 `media_index.json/db`，后续由 `asset_provenance.py` 做发布门禁。

### Phase 0.5: Source Receipts（事实来源 proof deck，可选但推荐）

如果视频包含新闻、数据、产品事实、健康/金融/法律判断、来源页截图或“官方说法”，在进入分镜/发布前先把 claim 和证据落成 source receipts：

```bash
python3 scripts/source_receipts.py \
  --claims work/source_claims.json \
  --project-dir . \
  --output work/source_receipts.json \
  --markdown work/source_receipts.md \
  --html work/source_receipts.html \
  --require-primary-source \
  --strict
```

`source_receipts.py` 只验证已提供的 URL 和本地截图/证据文件；不联网抓取、不截图、不上传。`source_claims.json` 里的 `screenshot` / `source_file` 相对 claims JSON 所在目录解析。新闻、数据、金融、健康、法律等高风险 claim 必须有 `source_url`；需要视觉 proof card 时加 `--require-screenshot`。发布门禁可用 `pipeline_manifest.py --require source_receipts --strict` 强制检查，`summary.blocking > 0` 会阻塞。

### Phase 1: Audio Extraction（音频提取）

对每个输入视频文件，使用 [extract_audio.py](./scripts/extract_audio.py) 提取音频：

```bash
python3 scripts/extract_audio.py "<video_path>"
```

输出：与视频同目录下的 `<video_name>_audio.wav` 文件。

### Phase 2: Speech Recognition（语音识别）

使用 [transcribe.py](./scripts/transcribe.py) 对音频进行语音识别，生成带时间戳的逐句文本：

```bash
python3 scripts/transcribe.py "<audio_path>" --model auto --language zh --detect-fillers
```

- `--model auto`：根据硬件自动选择最佳模型（NVIDIA GPU → large-v3，Apple Silicon → large-v3-turbo，集成显卡 → medium，纯 CPU → small）
- 也可手动指定：`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`
- `--engine auto`：自动检测 faster-whisper（推荐）或 openai-whisper
- `--mirror`：中国用户使用镜像源下载模型
- `--language`：`zh`（中文），`en`（英文），`ja`（日文）等，也可省略让 whisper 自动检测
- `--silence-threshold 1.0`：静音检测阈值（秒），默认 1.0。设为 0 关闭
- `--word-timestamps`：启用逐词时间戳（卡拉OK字幕必需）
- `--detect-fillers`：检测填充词（中文：嗯/呃/那个/就是说；英文：um/uh/like/you know），标记纯填充词片段为建议跳过

输出：与音频同目录下的 `<video_name>_transcript.json` 文件，格式如下：

```json
{
  "segments": [
    {"id": 1, "start": 0.0, "end": 2.5, "text": "大家好"},
    {"id": 2, "start": 2.5, "end": 5.1, "text": "今天我们来聊一个话题"}
  ],
  "silences": [
    {"start": 15.2, "end": 18.5, "duration": 3.3, "before_segment": 5, "after_segment": 6}
  ],
  "filler_words": [
    {"segment_id": 3, "text": "嗯那个", "fillers_found": ["嗯", "那个"], "is_filler_only": true},
    {"segment_id": 7, "text": "就是说我觉得这个方案", "fillers_found": ["就是说"], "is_filler_only": false}
  ]
}
```

**静音检测**：transcribe.py 会自动分析相邻语音片段之间的间隙。超过阈值（默认 1 秒）的间隙会被标记为静音并输出到 `silences` 字段中。这些静音通常是说话人的停顿、卡壳或口误，在构建 render_config.json 选片时应注意避开这些区域。

### Phase 2a: Video Keyframe Extraction（视频关键帧提取）

对于口播类视频（尤其是在户外行走中拍摄的、带有环境音的素材），仅靠音频转录无法了解视频的视觉内容。使用 [extract_keyframes.py](./scripts/extract_keyframes.py) 提取视频关键帧并生成时序图，以便全面理解视频内容：

```bash
python3 scripts/extract_keyframes.py "<video_path>"
```

参数说明：
- `--max-frames 16`：最大关键帧数量（默认 16）
- `--threshold 0.4`：场景变化检测灵敏度（0.0-1.0，越低提取越多关键帧，默认 0.4）
- `--cols 4`：时序图网格列数（默认 4）
- `--thumb-width 320`：缩略图宽度（默认 320px）
- `--output-dir`：关键帧输出目录（默认 `<video_name>_keyframes/`）
- `--no-storyboard`：仅提取关键帧，不合成时序图

输出：
- `<video_name>_keyframes/` — 各关键帧 PNG 图片（带时间戳命名）
- `<video_name>_storyboard.png` — 合成的时序图（网格布局 + 时间戳标注）
- `<video_name>_keyframes.json` — 关键帧元数据（时间戳、帧号、文件路径）

**为什么需要关键帧提取**：
- 口播视频经常在走路中拍摄，画面中的场景变化（街道→公园→咖啡店）是重要的叙事线索
- 时序图让 AI 可以同时看到音频内容（transcript）和视觉内容（keyframes），做出更好的选片判断
- 可以发现纯音频分析无法捕捉的信息：肢体语言、表情变化、环境切换、产品展示等

**典型工作流**：
```bash
# 1. 提取音频并转录
python3 scripts/extract_audio.py video.mp4
python3 scripts/transcribe.py video_audio.wav --model auto --language zh --detect-fillers

# 2. 提取关键帧生成时序图
python3 scripts/extract_keyframes.py video.mp4

# 3. AI 结合 transcript + storyboard 进行综合分析
# → 查看 video_storyboard.png 了解视频画面内容
# → 对照 video_transcript.json 了解语音内容
# → 综合判断哪些片段最适合保留
```

**AI Agent 综合分析要点**：
- 将关键帧时序图与转录文本对照，标注每个时间段的「说了什么 + 画面是什么」
- 找出画面与语音最匹配的高质量片段（如：讲到"这家店"时画面正好对着店铺）
- 识别画面模糊、遮挡、光线不佳的片段，建议跳过
- 户外拍摄时，注意画面抖动严重的片段，在选片时降低优先级

### Phase 2a: Adaptive Scene Boundaries（自适应视觉场景边界）

长视频拆条、抽样理解或成片节奏分析前，优先为运动镜头生成自适应场景边界：

```bash
python3 scripts/scene_boundaries.py video.mp4 \
  --method adaptive \
  --adaptive-threshold 3.0 \
  --min-scene-score 0.15 \
  --min-scene-duration 1.0 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md
```

`adaptive` 把每帧 FFmpeg scene score 与前后邻域均值比较，可减少持续摇镜、运动或闪烁造成的密集误切；`boundary_evidence[]` 保留 score、adaptive ratio 和邻域均值，必须先看 Markdown 再交给 `highlight_picker.py --scene-boundaries`。需要复现旧流程或固定机位素材时，用 `--method fixed --threshold 0.35`。

### Phase 2b: Visual Dedupe（跨素材重复镜头复核）

多机位、多 take、重复转码或 B-roll 候选进入时间线前，用 `visual_dedupe.py` 对多个 source 的场景做三点感知哈希复核：

```bash
python3 scripts/visual_dedupe.py \
  --manifest work/visual_dedupe_sources.json \
  --output work/visual_dedupe.json \
  --markdown work/visual_dedupe.md \
  --strict
```

manifest 的 `sources[]` 为每个来源提供 `id`、`video`、可选 `scene_boundaries` 和 `quality_score`；相对路径以 manifest 目录为基准。脚本默认只比较不同来源，要求 10%/50%/90% 至少两个采样点匹配，并把 `quality_score`、分辨率和文件大小用于保留建议。它只输出 review artifact，绝不删除或移动源素材。发现重复组时，先人工查看 source range，再从下游 edit plan 排除确认重复的候选。

### Phase 2c: Video Understanding（抽样帧 + 可选 YOLO）

当素材里的人、手机、电脑屏幕、产品、车辆或其他动态对象会影响裁切、隐私遮挡或 B-roll 选择时，使用 [video_understanding.py](./scripts/video_understanding.py) 生成结构化视觉理解 artifact。默认不需要安装 detector；如果要运行 YOLO，先安装可选依赖 `ultralytics`。

```bash
# 无 detector：只抽样帧并生成 review shell
python3 scripts/video_understanding.py video.mp4 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md

# 可选 YOLO：检测对象并生成 detections/tracks/scene_tags
pip install ultralytics
python3 scripts/video_understanding.py video.mp4 \
  --scene-boundaries work/scene_boundaries.json \
  --detector yolo \
  --model yolo11n.pt \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md \
  --strict
```

输出：
- `video_understanding.json` — `video_understanding.v1`，包含 `frames[]`、`detections[]`、`tracks[]`、`scene_tags[]`、`warnings[]`
- `video_understanding.md` — 人工 review 表，列出抽样帧、检测数量、轨迹、标签和 warning

典型下游：
```bash
python3 scripts/smart_reframe.py video.mp4 \
  --detections work/video_understanding.json \
  --platform douyin \
  --output work/reframe_douyin.json \
  --markdown work/reframe_douyin.md

python3 scripts/privacy_redact.py \
  --video video.mp4 \
  --detections work/video_understanding.json \
  --output work/privacy_redaction.json \
  --markdown work/privacy_redaction.md
```

注意：内置 tracklets 是为口播剪辑做的轻量关联，不等同于逐帧多目标跟踪。对体育、车流、多人遮挡等高动态素材，可以用 Ultralytics `model.track(..., tracker="bytetrack.yaml")`、BoT-SORT 或 Norfair 生成更密集的检测/track 结果，再转换成同一份 `detections[]` / `tracks[]` JSON。

### Phase 2.5: Transcript Review（转录文字校验）

转录完成后，**必须**对所有 transcript.json 中的文字进行逐条审查，修正以下两类问题：

**1. 语音识别错误（ASR errors）**：
Whisper 常见的识别错误类型：
- **专有名词/产品名**：如 "opencloud" → "OpenClaw"、"cloudcode" → "Claude Code"、"cloud ops" → "Claude Opus"
- **同音字错误**：如 "小红树" → "小红书"、"检映" → "剪映"、"断耕" → "断更"、"懒得讲" → "懒得剪"
- **英文拼写**：如 "scale" → "skill"、"箱子" → "视频"
- **尾部幻觉**：Whisper 有时在安静片段末尾生成无意义的重复文字，应直接删除

**2. 口误标记（Speaker errors）**：
- **重复/卡壳**：说话人重复说同一句话或卡住后重新说，标记为可跳过
- **乱码片段**：语音模糊导致识别为无意义文字的片段（如连续的单字碎片），标记为可跳过

专业术语、人名、同音字或中英混说较多时，先运行 `semantic_transcript_review.py prepare`，让当前 Agent/模型填写 provider-neutral response；再运行 `audit`。不要把模型 confidence 当批准：从 audit Markdown 复制绑定 `source_sha256 + review_id` 的 choices 模板，逐项 `approve` / `reject` 后才运行 `apply`。`audit` 会从源 transcript 推导完整覆盖率，并拒绝整句润色、非最小字符补丁、数字/标点变化、越界/重叠补丁和旧 transcript hash。成功 apply 后，把 `transcript_semantic_reviewed.json` 交给下面的同步媒体 HTML 继续听审；详见 [docs/prompts/79-semantic-transcript-review.md](docs/prompts/79-semantic-transcript-review.md)。

```bash
python3 scripts/semantic_transcript_review.py prepare \
  --transcript work/transcript.json \
  --output work/semantic_review_request.json \
  --markdown work/semantic_review_request.md

python3 scripts/semantic_transcript_review.py audit \
  --transcript work/transcript.json \
  --response work/semantic_review_response.json \
  --output work/transcript_semantic_review.json \
  --markdown work/transcript_semantic_review.md \
  --strict

python3 scripts/semantic_transcript_review.py apply \
  --transcript work/transcript.json \
  --audit work/transcript_semantic_review.json \
  --choices work/semantic_review_choices.json \
  --output work/transcript_semantic_reviewed.json
```

**校验流程**：
1. 用 `transcript_review.py html` 生成一个无外部依赖的本地页面；纯终端环境用 `export` 生成文本。
2. 页面里点击时间码对着媒体校稿；播放时当前段自动高亮。行内编辑支持本地自动保存、查找替换和 CPS 标黄。
3. 显式保存 `transcript_review.txt`，不要让页面或 agent 直接覆盖原始 transcript。
4. 用 `apply` 生成 `transcript_reviewed.json`；后续清稿、粗剪、分镜和字幕统一使用 reviewed 文件。
5. 对于口误/乱码片段，在展示片段列表时（Phase 3）标注为建议跳过。

```bash
python3 scripts/transcript_review.py html \
  --transcript work/transcript.json \
  --video origin/talking.mp4 \
  --corrections work/corrections.json \
  --output work/transcript_review.html \
  --max-cps 20

python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json
```

HTML 和媒体只在本机打开，不上传、不调用 LLM。浏览器不能直接写文件时会下载 `transcript_review.txt`；把它放回 `work/` 后再 apply。CPS 是预渲染提示，最终还要跑 `subtitle_readability_qa.py --strict`。

**注意**：此步骤必须在 Phase 5（渲染）之前完成，因为字幕文字来源于 transcript.json。修正后再渲染，才能保证最终视频中的字幕文字正确。

### Phase 2.5a: Target Script Alignment（按确认稿装配原话，可选）

如果客户、编导或用户已经确认成片稿，而同一句话录了多个 take 或分散在不同素材里，先把目标稿按“一行一个完整 spoken unit”整理，再匹配 reviewed transcript：

```bash
python3 scripts/script_alignment.py \
  --target-script work/target_script.md \
  --transcript take-a=work/take-a_transcript_reviewed.json \
  --transcript take-b=work/take-b_transcript_reviewed.json \
  --media take-a=origin/take-a.mp4 \
  --media take-b=origin/take-b.mp4 \
  --output work/script_alignment.json \
  --markdown work/script_alignment.md \
  --render-config work/render_config.json \
  --clean-script work/clean_script.md \
  --strict
```

脚本只做本地词面匹配，不调用 LLM、不改源文件、不判断表情/镜头/表演质量。它输出每句的稳定 candidate id、source time、原话和 sequence/coverage/ngram/length evidence；word timestamps 存在时优先收紧到词边界，只有 segment 时间戳时保守保留整段。低分、前两名过近、无候选、素材缺失或源时间重复占用会写 `summary.blocking` 并让 `--strict` 返回 2。

同文案多个 take 出现 `ambiguous_match` 时，必须看/听 Markdown 里的候选，把确认结果写成 `{"choices":{"target-001":"<candidate-id>"}}`，再用 `--choices work/script_alignment_choices.json` 重跑。人工 choice 只能解决词面低分/多解，不能绕过缺文件或时间重叠。`summary.blocking=0` 后再进入 `edit_preflight.py` / `render_final.py`；详细用法见 [docs/prompts/78-script-alignment.md](docs/prompts/78-script-alignment.md)。

### Phase 2.6: ASR Rough Cut（口头禅/重复句粗剪，可选）

如果 transcript 中纯口头禅、卡壳重说或相邻重复句较多，先用 [rough_cut.py](./scripts/rough_cut.py) 生成可审计粗剪计划：

```bash
python3 scripts/rough_cut.py --transcript work/transcript.json --cut-list work/rough_cut.json
```

确认计划后可直接渲染粗剪版：

```bash
python3 scripts/rough_cut.py \
  --transcript work/transcript.json \
  --input origin/talking.mp4 \
  --output output/talking.roughcut.mp4 \
  --cut-list work/rough_cut.json
```

`rough_cut.py` 会输出 `decisions` / `removed_segments` / `keep_segments` / `speedup_ratio`。它不调用 LLM，不提交任何付费任务；只用 `transcribe.py --detect-fillers` 的 filler metadata 和相邻文本相似度做保守粗剪。渲染前用 `timeline_view.py --cut-list work/rough_cut.json` 看源素材删除段；渲染后用 `--rendered-cut-list work/rough_cut.json` 看成片实际拼接点。

### Phase 2.6b: Multimodal Dead-Air（静音 + 静帧保守去死区，可选）

如果纯音频 `jump_cut.py` 可能误删仍有表情、手势、产品展示或屏幕操作的停顿，改用音频静音与画面静帧的 AND gate：

```bash
python3 scripts/multimodal_dead_air.py plan origin/talking.mp4 \
  --delivery work/talking-dead-air-tight.mp4 \
  --output work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md \
  --strict
python3 scripts/multimodal_dead_air.py verify work/multimodal_dead_air_plan.json --strict
DEAD_AIR_CUT_COUNT="$(python3 -c 'import json; print(len(json.load(open("work/multimodal_dead_air_plan.json"))["removed_segments"]))')"
python3 scripts/timeline_view.py origin/talking.mp4 \
  --cut-list work/multimodal_dead_air_plan.json \
  --output-dir verify/dead-air-cuts \
  --limit "$DEAD_AIR_CUT_COUNT"
python3 scripts/multimodal_dead_air.py apply work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md
```

默认只有静帧覆盖静音至少 60% 才入选，并且只删除二者交集；80ms padding、30ms 音频 fade 和 20% 删除预算继续生效。计划绑定源 SHA-256 和媒体契约，apply 使用临时 MP4，并在 H.264/AAC、`yuv420p`、尺寸、帧率、采样率、声道、时长和完整解码全部通过后原子提升。`timeline_view.py` 默认只看 20 个切点，因此必须像上面一样把本计划的实际删除段数传给 `--limit`；若计数为 0，停止并保留原片。它不理解表演、语义或有意留白；所有源切点仍须先看，输出仍须 1× 带声音完整复核。详见 [docs/prompts/88-multimodal-dead-air.md](docs/prompts/88-multimodal-dead-air.md)。

### Phase 3: User Interaction（用户交互）

**展示片段列表给用户**，格式如下：

```
视频片段列表：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  #   | 时间区间          | 内容
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1   | 00:00.0 - 00:02.5 | 大家好
  2   | 00:02.5 - 00:05.1 | 今天我们来聊一个话题
  3   | 00:05.1 - 00:08.3 | 这个话题非常有意思
  ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择要合成的片段（示例）：
  - 连续范围：1-10
  - 多个片段：1,3,5,7
  - 混合选择：1-4,6,8-10
```

如果有多个视频文件，分别展示每个视频的片段列表，让用户跨视频选择。

#### AI 智能选片建议

在展示片段列表时，AI agent 应基于以下维度为每个片段提供推荐评分（1-5 星）：

**吸引力评分维度**：
1. **Hook 强度**（前 3 秒）：是否有吸引人的开头（提问、反直觉观点、情感触发）
2. **信息密度**：每秒传递的有效信息量（避免重复、废话）
3. **情感变化**：是否有情感起伏（幽默→严肃→惊喜）
4. **完整性**：片段是否构成完整叙事单元（有开头、展开、收尾）

**自动跳过建议**：
- transcript 中 `is_filler_only: true` 的片段（纯填充词）
- 静音间隙 > 2 秒的相邻片段（卡壳后重说）
- 转录文字与前一片段高度重复的片段（口误重说）

**长视频自动拆短片**（视频 > 3 分钟时）：
AI agent 应分析 transcript 识别话题转换点（语义断裂、过渡词如"接下来"、"另外"），将片段按话题分组为独立短视频（每个 30-90 秒），并为每组计算整体吸引力评分：

```
推荐短视频拆分方案：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  方案 | 片段范围    | 时长  | 主题         | 推荐指数
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  A    | #1-#8       | 45s   | 痛点引入      | ★★★★★
  B    | #9-#18      | 62s   | 核心方法      | ★★★★☆
  C    | #19-#25     | 38s   | 实操演示      | ★★★☆☆
  D    | #1-#25      | 2m25s | 完整版       | ★★★★☆
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

等待用户回复选择后，进入 Phase 4。

选择完成后、批量渲染前，建议用词级时间戳校正切点，避免候选范围落在词中间或半句结尾：

```bash
python3 scripts/audio_boundary_snap.py \
  --candidates work/highlight_candidates.json \
  --transcript work/transcript.json \
  --media origin/long-talk.mp4 \
  --output work/audio_boundary_plan.json \
  --markdown work/audio_boundary_plan.md \
  --strict
```

输出 `audio_boundary_plan.v1` 的 `selected[]` 可直接传给 `shorts_batch.py --highlights`。脚本只读本地候选、transcript 和可选媒体；它不渲染、不上传、不调用 provider。`--strict` 会在缺词级时间戳、非法时间段、源媒体缺失或安全边界超平台时长时返回 2。

### Phase 4: Render Config（渲染配置）

根据用户的选择，生成 `render_config.json` 配置文件。

**重要说明：渲染配置是项目级文件**

SKILL 中展示的 render_config.json 格式只是**模板/参考**，不要直接修改 Skill 仓库中的任何文件。每个视频项目应在**用户工作目录**下创建独立的渲染配置：

```bash
# 在用户的视频项目工作目录下创建 script/ 文件夹
mkdir -p script/

# 在 script/ 下为每个视频项目创建独立的渲染配置文件
# 例如：script/render_config.json
```

**目录结构示例**：
```
~/Videos/my-project/          ← 用户工作目录
├── script/                   ← 渲染配置目录（每个项目独立）
│   └── render_config.json    ← 本项目的渲染配置
├── media/
│   ├── raw/                  ← 原始素材
│   ├── broll/
│   └── output/               ← 最终输出
├── *_audio.wav               ← 提取的音频
└── *_transcript.json         ← 转录文件
```

**渲染配置文件格式**（`script/render_config.json`）：

```json
{
  "clips": [
    {"video": "path/to/video1.MOV", "segment_id": 4, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video1.MOV", "segment_id": 5, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video2.MOV", "segment_id": 1, "transcript": "path/to/transcript2.json",
     "broll": "path/to/cityscape.mp4", "broll_start": 5.0}
  ],
  "title": "封面标题文字",
  "subtitle": "副标题/情感钩子（可选）",
  "cover_style": "news",
  "cover_duration": 2.0,
  "cover_image": "path/to/custom_cover.png",
  "cover_use_frame": false,
  "video_overlay": "path/to/overlay.png",
  "rec_blink": {
    "dot_image": "path/to/dot.png",
    "x": 55, "y": 66,
    "period": 1.0
  },
  "end_cards": [
    {"text": "感谢观看\n更多内容敬请期待", "duration": 3.5}
  ],
  "bgm": "path/to/background_music.mp3",
  "bgm_volume": 0.15,
  "bgm_fade_out": 3.0,
  "bgm_ducking": true,
  "bgm_ducking_threshold": 0.03,
  "bgm_ducking_ratio": 8,
  "bgm_ducking_attack_ms": 20,
  "bgm_ducking_release_ms": 500,
  "subtitle_style": "karaoke",
  "subtitle_highlight_color": "#FFFF00",
  "chapters": [
    {"title": "章节名", "start": 0.0, "end": 30.0}
  ],
  "text_badges": [
    {"text": "关键结论", "start": 12.0, "end": 13.5}
  ],
  "broll_overlays": [
    {"video": "path/to/cityscape.mp4", "start": 8.0, "end": 10.0, "source_start": 2.0}
  ],
  "image_overlays": [
    {"image": "path/to/generated.png", "start": 18.0, "end": 21.0, "fit": "cover"}
  ],
  "pip_overlays": [
    {"video": "path/to/facecam.mp4", "start": 0.0, "end": 42.0, "position": "bottom_right", "sync_offset": 0.18}
  ]
}
```

**使用渲染配置**：
```bash
# 渲染时指定 script/ 下的配置文件
python3 scripts/render_final.py --config script/render_config.json --output media/output/final.mp4
```

**B-roll 替换**（`broll` 字段）：
- 在 clip 中添加 `"broll": "path/to/video.mp4"` 可替换该片段的画面，同时保留原始音频
- `broll_start` 指定从 B-roll 视频的哪个时间点开始截取（默认 0.0）
- B-roll 视频会自动缩放/裁切以匹配主视频分辨率
- 适用场景：屏幕录制口播配城市街景、音频配音配画面等

**Auto-Enrich 自动接入**（推荐）：
- 先运行 `auto_enrich.py --output work/enrich_plan.json`
- 渲染时加 `--enrich-plan work/enrich_plan.json`
- `broll[].suggested_asset` 会转成定时 B-roll video overlay；`chapter_cards[]`、`stickers[]` 和 `emphasis_cues[]` 会转成 ASS badge；`emphasis_cues[]` 还可生成无红框的轻微 center push-in；`chapter_cards[].png` / `imagegen[].image_path` / `imagegen[].generated_path` 会转成定时图片 overlay
- `pip_overlays[]` 会转成定时 facecam / camera 小窗；camera audio 默认忽略，主音频仍来自 render config
- 没有实际生成文件的 imagegen cue 只作为提示输出，不会阻塞渲染
- 合并后的可见文字仍会走 `_internal_text_guard` 和 `content_guard.py`

**Beat Edit Plan 节拍剪辑骨架**（音乐视频 / 卡点 montage 可选）：
- 运行 `beat_sync.py --bgm origin/bgm.mp3 --generate-plan --duration 30 --beats-per-cut 4 --min-segment 0.75 --max-segment 3 --output work/beat_edit_plan.json --markdown work/beat_edit_plan.md`
- 输出 `beat_edit_plan.v1`：`cut_times[]`、program-time `segments[]`、逐切点 `boundary_evidence[]`、检测方法和 `summary`
- 默认每 4 拍提出一个切点；最短/最长镜头守卫会优先改选附近 beat，实在没有合适 beat 才插入 `duration_guard`
- `librosa` 不可用或读取失败时只生成固定 BPM 网格，并把状态标为 `review`；必须听音乐复核，不能把 fallback 当作真实节拍
- 计划只定义时间槽，不选择素材、不渲染、不改源文件；确认后再把镜头映射进 `render_config` / EDL / OTIO
- 已有 cut times 仍使用原命令 `beat_sync.py --bgm origin/bgm.mp3 --cuts work/cut_times.json --window 0.2`

**Speed Ramp 局部慢动作 / velocity edit**（动作、产品 reveal、游戏、montage 可选）：
- 先逐帧找到 impact frame，再运行 `speed_ramp.py plan origin/action.mp4 --ramp 4.6,5.0,1,0.25,s_curve --hold 5.0,5.8,0.25 --ramp 5.8,6.2,0.25,1,ease --interpolate-fps 120 --output work/speed_ramp_plan.json --markdown work/speed_ramp_plan.md`
- `verify` 会重算 plan id、源文件 SHA-256、source/output coverage 和逐段 `duration / speed`；源片或 pieces 漂移立即阻塞
- `apply` 用同目录临时文件渲染，成功后才替换目标；默认不覆盖、不跟随 output symlink，也不能覆盖 source
- `--interpolate-fps` 是 FFmpeg motion interpolation，可能产生肢体 / 边缘伪影；极慢音频必须试听，必要时 plan 阶段加 `--mute-audio`
- 最终必须用 1×、带声音完整播放；变速后重新生成字幕 / timecoded artifacts、跑 render QA，并重新做最终审批。详见 `docs/prompts/83-speed-ramp.md`

**J-cut / L-cut 声音先行 / 延续转场**（访谈、叙事、场景切换可选）：
- 只在人工明确选定的边界运行：`audio_transition.py plan work/render_config.json --transition 1,j_cut,0.4 --transition 3,l_cut,0.5 --output work/audio_transition_plan.json --markdown work/audio_transition_plan.md`
- J-cut 使用下一 clip 入点之前的真实源音频 handle；L-cut 使用上一 clip 出点之后的 handle，并跳过下一 clip 等长的开头音频以恢复同步。handle 不足直接阻塞，不用静音伪造
- 计划绑定 render config、transcript、源片/B-roll SHA-256 和 canonical plan id；`verify` 会从 live inputs 重新编译，手改 digest 不能隐藏漂移
- 用 `audio_transition.py apply ... --output output/master.mp4 --receipt work/audio_transition_apply.json`，或给 `render_final.py` 加 `--audio-transition-plan`；字幕、overlay、BGM 和错位主音频仍在一次 FFmpeg 编码中完成
- 必须在 1× 用耳机和手机逐个试听改变的边界，确认无吞字、复读、双人声、click、泵动和环境底噪跳变。机器只能验证时序/hash，不能判断交叠对白是否合适。详见 [docs/prompts/86-audio-transition.md](docs/prompts/86-audio-transition.md)

**Video Stabilization source-bound 手持防抖**（仅用于不想要的抖动）：
- 先运行 `video_stabilization.py doctor`。`plan --backend auto` 优先两遍 `vidstab`；缺少该 filter 时会把单遍 `deshake` 明确写进计划并保留降级 warning，不会在 apply 时静默换后端
- 原片看过后，用 `plan origin/handheld.mp4 --decision stabilize --reviewed-by editor --output work/video_stabilization_plan.json --markdown work/video_stabilization_plan.md` 记录源 SHA-256、profile 和人工决定；有意手持 / pan 应改用 `--decision keep`
- `apply ... --output work/handheld-stabilized.mp4 --comparison verify/handheld-stabilization-compare.mp4` 只写新工作副本与全长左/右 A/B；随后必须 1× 看完整 comparison，再运行 `confirm --reviewed-by ... --note ...`
- `pipeline_manifest.py` 会实时检查源片、稳定版、comparison 的 hash 与复核状态。稳定化不能修复滚动快门、运动模糊或失焦；后续用工作副本，不覆盖 `origin/`。详见 `docs/prompts/84-video-stabilization.md`

**PIP Overlay 录屏摄像头小窗**（录屏教程可选）：
- 运行 `pip_overlay.py --camera origin/facecam.mp4 --segment "0,18,bottom_right" --segment "18,42,top_right" --sync-offset 0.18 --output work/pip_overlay_plan.json --markdown work/pip_overlay_plan.md`
- 渲染时追加 `--enrich-plan work/pip_overlay_plan.json`；可和 `screen_focus_plan.json`、`enrich_plan.json` 重复传入
- 输出 `pip_overlay_plan.v1`，包含 `pip_overlays[]`、每段位置、source_start、sync offset、尺寸比例、透明度和淡入淡出
- `render_final.py` 会随 `--primary-speed` / `--speed` 同步压缩 camera 小窗时间线，避免变速输出时 PIP 和主画面错位

**Audio Sync 外录音频自动对齐**（相机内录 + 外录麦克风时推荐）：
- 运行 `audio_sync.py --reference-media origin/camera.mp4 --external-audio origin/lav.wav --output work/audio_sync_plan.json --markdown work/audio_sync_plan.md --replace-output output/camera_lav_synced.mp4 --strict`
- 输出 `audio_sync_plan.v1`，包含 `alignment.offset_seconds`、`confidence`、`replace_audio.command`、Markdown 复核表和 `summary.blocking`
- 正数 offset 表示延迟外录音轨；负数 offset 表示裁掉外录音轨开头
- 确认计划后再加 `--apply` 执行 FFmpeg 替换音轨；脚本会 copy 原视频流、用同步后的外录音轨编码 AAC
- 如果自动估计低置信度，可用 `--offset 0.18` 这类手动偏移跳过估计；`pipeline_manifest.py` 会拦截低置信度或缺文件的 audio sync artifact

**Multicam Sync 多机位可逆同步计划**（两台以上设备录制同一事件时推荐）：
- 运行 `multicam_sync.py --reference-media origin/cam-a.mp4 --angle origin/cam-b.mp4 --angle origin/cam-c.mp4 --measure-clock-drift --output work/multicam_sync_plan.json --markdown work/multicam_sync_plan.md --preview-output output/verify/multicam_sync_preview.mp4 --apply-preview --strict`
- 输出 `multicam_sync_plan.v1`：每路 offset/confidence、自动最响音轨、reference/source coverage、公共 overlap、pairwise 一致性、可选 drift probes/线性 fit 和 preview command
- 原片不修改、不重编码；只有 `--apply-preview` 会生成短网格预览。正 offset 表示该机位 `t=0` 位于参考时间线更晚的位置
- 多音轨相机可用 `--audio-stream "origin/cam-b.mp4=2"` 指定有效轨；无音轨/人工拍板用 `--manual-offset "origin/cam-c.mp4=1.24"`
- 漂移测量默认关闭；启用后默认取 5 个 20 秒窗口，至少 4 个共识 inlier 才拟合 `offset(R)=intercept+slope*R`，报告明确符号的 ppm、测量分辨率、累计漂移、残差和未应用的 `atempo/setpts` advisory factors
- 漂移证据只覆盖选择的参考/源音轨；不能据此自动断言视频 PTS 或其他音轨共享同一时钟
- 漂移超阈值或拟合不可靠会进入 review gate；脚本不自动校正，音画必须使用同一仿射映射。未启用时，30 分钟以上仍必须复核头/中/尾
- 详细使用与边界见 [docs/prompts/76-multicam-sync.md](docs/prompts/76-multicam-sync.md)

**Storyboard Plan 分镜与生成路由**（生成素材前推荐）：
- 运行 `storyboard_plan.py --transcript work/transcript.json --clean-script work/clean_script.md --output work/storyboard_plan.json --markdown work/storyboard_plan.md`
- 输出每个 shot 的时间码、narration、first/motion/last-frame 描述、`codex_imagegen` / `dreamina_video` / `remotion_hyperframes` / `media_library_broll` 路由、fallback 和 continuity anchors
- 可选运行 `video_prompt_pack.py --storyboard-plan work/storyboard_plan.json --asset-root work --style-reference work/imagegen/style-key.png --output work/video_prompt_pack.json --markdown work/video_prompt_pack.md --strict`，把分镜转成 Dreamina/即梦 Seedance、Veo、LTX、Wan、Sora 提示词包、共享 style lock 和 approval gate
- image-to-video 提交前运行 `reference_frame_preflight.py --prompt-pack work/video_prompt_pack.json --output work/reference_frame_preflight.json --markdown work/reference_frame_preflight.md --require-style-reference --strict`，拦截缺失/损坏/方向冲突/严重画幅冲突参考帧
- 再运行 `storyboard_assets.py --storyboard-plan work/storyboard_plan.json --asset-root work --media-library . --output work/storyboard_assets.json --markdown work/storyboard_assets.md --strict`，渲染前确认素材 `ready`
- `dreamina_video` 只表示适合视频生成，不会自动提交任务；提交 Dreamina/即梦前必须确认，因为可能消耗 credits
- 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

**Video Prompt Pack + Reference Frame Preflight 视频生成提示词与参考帧门禁**（生成视频前推荐）：
- 运行 `video_prompt_pack.py --storyboard-plan work/storyboard_plan.json --asset-root work --character "same host" --brand-anchor "palette=charcoal,white,signal yellow" --style-reference work/imagegen/style-key.png --output work/video_prompt_pack.json --markdown work/video_prompt_pack.md --strict`
- 输出 `global.character_sheet_prompt`、`global.style_reference`、`items[].prompt`、`items[].negative_prompt`、`items[].reference.expected_path/resolved_path`、`items[].approval_status` 和 `summary.blocking`
- `--style-reference` 会把同一 style key 绑定到所有 shot，并在每条 provider prompt 加同一条 `STYLE LOCK`
- 参考图落盘后运行 `reference_frame_preflight.py --prompt-pack work/video_prompt_pack.json --output work/reference_frame_preflight.json --markdown work/reference_frame_preflight.md --require-style-reference --strict`
- `reference_frame_preflight.v1` 检查首帧/style key 是否存在、可解码、方向/画幅是否匹配、分辨率是否过低和透明背景；阻塞项接入 `pipeline_manifest.py`
- `--provider veo|ltx|wan|sora|dreamina_seedance` 可把同一分镜改写成指定模型提示词；`--animate-stills` 会把 `codex_imagegen` 参考图 route 转为 image-to-video 提示词
- `--strict` 会在 generated-video provider 还没 `--approved` 时返回 2；脚本不提交 provider 任务、不消耗 credits
- 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

**Generation Task Log 异步生成任务台账**（提交生成任务后推荐）：
- 从 provider 决策生成待审批台账：`generation_task_log.py import-provider-decision --provider-decision work/provider_decision.json --log work/generation_tasks.json --markdown work/generation_tasks.md --strict`
- Dreamina/即梦提交后必须保存 `submit_id`：`generation_task_log.py add --log work/generation_tasks.json --provider dreamina --task-id <submit_id> --shot-id shot_002 --expected-path work/generated_video/shot_002.mp4 --status submitted`
- 下载完成后更新本地文件：`generation_task_log.py update --log work/generation_tasks.json --provider dreamina --task-id <submit_id> --status downloaded --asset-path work/generated_video/shot_002.mp4 --markdown work/generation_tasks.md`
- 输出 `generation_task_log.v1`，包含 `poll_command`、`download_command`、`readiness` 和 `summary.blocking`
- `completed` 但没有本地 `asset_path` 仍会阻塞；`pipeline_manifest.py` 会自动识别 `generation_tasks.json` 并把未清零任务列为 gate

**Generated Clip Review 生成视频片段复核**（生成结果下载后、组装前必跑）：
- 先刷新 `storyboard_assets.json`，再运行 `generated_clip_review.py prepare --project-dir . --asset-manifest work/storyboard_assets.json --contact-sheet-dir verify/generated_clips --output work/generated_clip_review_request.json --markdown work/generated_clip_review_request.md --response-template work/generated_clip_review_response.json`
- reviewer 必须完整执行 1× 带声、0.25×、静音看画面、只听声音四遍检查；contact sheet 只是抽样导航，不能代替播放完整片段
- response 对每条 clip 记录 identity/wardrobe、action/end state、motion/anatomy/physics、camera、frame integrity、look consistency 六项 1–5 分，以及 hard-fail code、`keep_ranges` / `remove_ranges`、是否重生和具体 `prompt_fix`
- `pass` 要求加权分 ≥80 且无删段；`pass_with_edits` 要求 ≥65，并由 keep/remove 精确覆盖全片；常识/物理、身份、道具、文字水印、连续性或音画 hard fail 无论总分多高都必须 `fail`
- 填完 response 后运行 `generated_clip_review.py audit --request work/generated_clip_review_request.json --response work/generated_clip_review_response.json --output work/generated_clip_review.json --markdown work/generated_clip_review.md --strict`；任何 clip/contact sheet 漂移、缺审、非法区间或需重生都会阻塞
- 组装/发布前再运行 `generated_clip_review.py verify --report work/generated_clip_review.json --strict`，也可用 `pipeline_manifest.py --require generated_clip_review --strict`。reviewer label 不是身份认证或数字签名

**Generated Sequence Review 跨镜头连续性复核**（两条以上生成片段逐片通过后、组装前必跑）：
- 运行 `generated_sequence_review.py prepare --project-dir . --clip-review work/generated_clip_review.json --storyboard-plan work/storyboard_plan.json --evidence-dir verify/generated_sequence --output work/generated_sequence_review_request.json --markdown work/generated_sequence_review_request.md --response-template work/generated_sequence_review_response.json`
- 脚本按 storyboard 顺序，为每个相邻边界提取批准范围内的上一镜尾帧、下一镜首帧、并排图和“尾部 + 头部”无声 1× preview；如果上游是 `pass_with_edits`，只使用批准的首个/最后一个 keep range
- 完整查看 preview 和 comparison 后，为 identity/wardrobe、prop state、spatial orientation、action end state、camera framing、lighting/palette 填 `match|intentional_change|mismatch|not_applicable`。至少两项必须真实评估；`mismatch` 必须 `fail`，给 failure code 和具体 `repair_action`
- 运行 `generated_sequence_review.py audit --request work/generated_sequence_review_request.json --response work/generated_sequence_review_response.json --output work/generated_sequence_review.json --markdown work/generated_sequence_review.md --strict`，组装/发布前再 `verify --report work/generated_sequence_review.json --strict`；clip、上游 review、storyboard 或任一 evidence bytes 漂移都会阻塞
- `pipeline_manifest.py --require generated_clip_review --require generated_sequence_review --strict` 可把逐片和跨镜头两层都设为门禁。preview 无声，不能替代最终 master 的完整声画复核；reviewer label 和 SHA-256 不是身份认证或签名

**Generation Lessons 生成视频经验闭环**（可选；只沉淀可泛化且人工明确批准的经验）：
- 从已完成的 `generated_clip_review.json` 选定一条 clip，运行 `generation_lessons.py add --library work/generation_lessons.json --review work/generated_clip_review.json --clip-id shot_002 --category hand_contact --lesson "For hand-to-prop contact, isolate one interaction and keep the hand visible through release." --approved-by "<reviewer-label>" --model seedance-2.0`
- `add` 会实时重算 canonical review，允许“片段确实需要重生”这一预期 blocker，但拒绝 source/contact-sheet 漂移、漏审、非法 response、stored summary/report-id 篡改；entry 绑定 report/request/clip/contact-sheet SHA-256。`approved_by` 只是审计标签，不是身份认证或签名
- 复用前运行 `generation_lessons.py verify --library work/generation_lessons.json --strict`；按 provider/model/category 预览可用 `generation_lessons.py select --library work/generation_lessons.json --provider dreamina_seedance --model seedance-2.0 --limit 3 --output work/selected_generation_lessons.json --markdown work/selected_generation_lessons.md`
- 新证据与旧规则冲突时，不删除旧 entry；新建 lesson 并加 `--supersedes <old-lesson-id>`。旧 evidence 继续留在 library，但只要新 entry 也匹配当前 provider/model/category，`select` 就排除被替代规则
- 下一次生成提示词包显式加 `video_prompt_pack.py ... --lesson-library work/generation_lessons.json --lesson-model seedance-2.0 --lesson-limit 3`。provider 专属规则优先，未给 `--lesson-model` 时不会误用 model-specific 经验；脚本只追加人工批准的 `lesson`，不会自动把 clip-specific `prompt_fix` 当成通用规则，也不会自动重生或消费 credits
- 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
- 详细说明见 [docs/prompts/89-generated-clip-review.md](docs/prompts/89-generated-clip-review.md)

**Storyboard Assets 素材清单与预检**（分镜后、渲染前推荐）：
- 运行 `storyboard_assets.py --storyboard-plan work/storyboard_plan.json --asset-root work --media-library . --output work/storyboard_assets.json --markdown work/storyboard_assets.md`
- 输出每个 shot 的素材状态：`ready` / `candidate_found` / `needs_generation` / `needs_approval` / `needs_render` / `search_needed`
- 如果传入 `--media-library`，`media_library_broll` shot 会从素材索引里生成 `candidate_paths` + `candidate_scores`，按 tag、文件名、metadata、时长和画幅透明排名
- 加 `--strict` 时，任何素材未 ready 都会返回退出码 2，适合放在最终渲染前
- `needs_approval` 代表 Dreamina/即梦等可能消耗 credits 的任务，必须先确认再提交；生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

**自定义封面**（`cover_image`）：
- 提供自定义封面 PNG 路径，优先于自动生成的封面
- 尺寸应与视频分辨率匹配（如 1080x1920）

**持续叠加层**（`video_overlay`）：
- 提供透明 PNG 路径，在封面之后的整个视频上持续叠加显示
- 适用场景：品牌水印、系列标识（如 DAY 标签、数据指标）等
- PNG 必须包含 Alpha 通道（RGBA），透明区域不会遮挡视频

**闪烁圆点**（`rec_blink`）：
- 在视频上叠加一个周期性闪烁的小圆点 PNG（如录像机 REC 标志）
- `dot_image`：圆点 PNG 路径（建议 12-16px，RGBA 格式）
- `x`, `y`：圆点在视频画面中的像素坐标
- `period`：闪烁周期（秒），默认 1.0（0.5 秒亮 + 0.5 秒灭）

**结尾卡片**（`end_cards`）：
- 在视频末尾追加黑屏文字卡片，每张卡片有 300ms 淡入淡出
- `text`：卡片文字内容，用 `\n` 换行
- `duration`：每张卡片显示时长（秒），建议 3.0-4.0
- 文字居中显示，字号为正文字幕的 1.4 倍

**口播稳态底噪清理**（`speech_denoise`）：
- 默认 `off`；只有空调、风扇、电流声、轻微房间底噪相对稳定时才启用 `"light"` / `"medium"` / `"strong"`
- CLI 可用 `--speech-denoise light`；配置已开启时可用 `--no-speech-denoise` 临时关闭
- 三档固定 80 Hz、2-pole 高通，FFT reduction 为 6/9/12 dB；`strong` 不会超过 12 dB
- 顺序固定为 `highpass → afftdn → atempo → dynaudnorm → acompressor → loudnorm → cover delay → BGM ducking/mix`
- 已做 VAD/noise gate、Adobe Podcast、Descript、RX 或机内强降噪的音轨通常保持 off；数字静音、噪声突变、多麦、瞬态敲击/咳嗽和混响必须另行处理
- 先渲染 10–20 秒 off/light/medium A/B，戴耳机正常速度试听辅音、尾音和停顿；渲染后继续运行 `audio_master_report.py --strict`

**背景音乐**（`bgm`）：
- 提供背景音乐文件路径（MP3/M4A/WAV 等 FFmpeg 支持的格式）
- `bgm_volume`：BGM 音量（0.0-1.0），默认 0.15（人声为主，BGM 为辅）
- `bgm_fade_out`：结尾淡出时长（秒），默认 3.0
- `bgm_ducking: true` 或 CLI `--bgm-ducking`：用最终旁白轨作为 sidechain，旁白出现时自动压低 BGM；封面、停顿和片尾无旁白时音乐会自然恢复
- 默认 ducking 参数是 threshold `0.03`、ratio `8`、attack `20ms`、release `500ms`；只有试听或 `audio_master_report.py` 证据表明需要时才调整
- ducking 分支用 `amix normalize=0` 保留人声响度，并在混音末尾加 `alimiter=0.95` 防止峰值溢出；旧配置默认仍关闭，保持已有输出兼容
- BGM 自动循环播放直到视频结束，不需要预先剪辑长度
- 推荐免费可商用音乐源：Pixabay Music、Mixkit、YouTube Audio Library
- 选曲建议：口播/教程用轻柔纯音乐（无人声），节奏不要太强，避免抢人声

**字幕风格预设**（`subtitle_style`）：
| 风格 | 效果 | 适用场景 |
|------|------|---------|
| `normal` | 白字黑描边（默认） | 适合所有场景 |
| `karaoke` | 逐词高亮 | 音乐/节奏感内容 |
| `bold_pop` | 粗描边高对比 | MrBeast/Hormozi 风格 |
| `neon` | 霓虹灯青紫色 | 科技/潮流内容 |
| `minimal` | 极简无描边 | 文艺/安静内容 |
| `yellow_pop` | 黄字黑描边 | 高可见度，户外/嘈杂画面 |

**卡拉OK字幕 / 逐词高亮**（`subtitle_style: "karaoke"`）：
- 在 config 中设置 `"subtitle_style": "karaoke"` 启用逐词高亮字幕
- 需要先用 `--word-timestamps` 参数进行语音识别，获取逐词时间戳
- `subtitle_highlight_color`：当前词高亮颜色，默认 `"#FFFF00"`（黄色）
- `subtitle_base_color`：未说到的词底色，默认 `"#FFFFFF"`（白色）
- `subtitle_base_alpha`：底色透明度 hex，默认 `"80"`（半透明）
- 如果 transcript 中没有 word 级时间戳，会自动回退到按字符均匀分布（效果稍差）
- 也可通过 CLI 参数 `--subtitle-style karaoke` 覆盖 config
- 典型工作流：
  ```bash
  # 1. 转录时开启逐词时间戳
  python3 scripts/transcribe.py audio.wav --model auto --language zh --word-timestamps
  # 2. 渲染时选择 karaoke 字幕风格
  python3 scripts/render_final.py --config render_config.json --output final.mp4 --subtitle-style karaoke
  ```

**音频源替代（M4A/独立音频）**：
- 如果有独立录制的音频文件（M4A 等），可先用 ffmpeg 转为带黑屏视频轨的 MP4：
  ```bash
  ffmpeg -f lavfi -i "color=c=black:s=1080x1920:r=30:d=60" -i audio.m4a -c:v libx264 -c:a aac -shortest audio.mp4
  ```
- 然后在 clip 中用 `"video": "audio.mp4"` 提供音频源，用 `"broll"` 提供画面
- 这样可以将补录的配音与任意画面组合

**封面标题**：
1. 如果用户提供了标题，直接使用。
2. 如果用户没有特别要求，**站在观众角度**总结一个吸引人的标题（6-15 个字）。
3. `subtitle` 可选，用于补充情感钩子或关键信息。
4. **移动端优先**：封面在手机列表页里只是一个缩略图，标题必须先保证可读性，再考虑画面细节。

**封面文字排版规则**：
- 标题按**一行最多约 8 个汉字**来设计；超过时自动换行，不要把单行塞得过满。
- 英文/数字按**约半个汉字宽度**估算；例如 `AI`、`GPT-5` 之类不应把整行宽度挤爆。
- `subtitle` 字号默认按标题的**约 50%** 处理，只承担补充信息，不和主标题争抢视觉中心。
- 如果标题超过两行，优先**缩短文案**，不要继续缩小字号来硬塞。
- 做教程/工具类封面时，主标题尽量控制在 **4-8 个字**，副标题控制在 **6-12 个字**。

**封面风格**（`cover_style`）— 根据视频内容选择最合适的风格：
| 风格 | 适用场景 | 视觉效果 |
|------|---------|---------|
| `bold` | 教程、科普、技术 | 黑底 + 大号白色粗体字，简洁有力 |
| `news` | 热点、观点、争议 | 深色渐变底 + 白色标题 + 黄色副标题，冲击力强 |
| `frame` | Vlog、实拍、场景 | 视频首帧做背景 + 暗色遮罩 + 描边白字 |
| `gradient` | 生活、情感、艺术 | 紫粉渐变底 + 发光白字，温柔优雅 |
| `minimal` | 思考、文化、深度 | 纯黑底 + 细体白字，极简克制 |
| `white` | 教程、产品、品牌化内容 | 纯白底 + 深色字，现代感更强 |
| `techcard` | 屏幕录制、软件教程、AI 工具演示 | 左侧大标题 + 右侧画面卡片，兼顾信息量和可读性 |

AI agent 应根据视频主题和内容语气自动选择：
- 科技/工具类 → `bold` 或 `news`
- 争议/观点类 → `news`（白标题+黄副标题效果最抢眼）
- Vlog/实拍类 → `frame`
- 情感/生活类 → `gradient`
- 深度/文化类 → `minimal`
- 纯桌面录屏 / 软件教程 → `techcard` 优先；如果画面太杂，就退回 `bold` / `white`

**背景取帧规则**：
- 不要机械地使用第一帧。对于录屏教程，优先选择**信息密度更高**、界面更完整的一帧做背景或卡片图。
- 如果背景画面会影响标题识别，优先使用 `bold` / `white` / `minimal` 这类纯底风格。
- 单独预览封面时，可用 `scripts/generate_cover_image.py --frame-timestamp 00:10:00` 指定取帧时间。

**多封面 A/B 方案与最终选择**：
```bash
python3 scripts/cover_variants.py output/final_xhs.mp4 \
  --title "20分钟出片" \
  --subtitle "AI剪辑完整流程" \
  --caption output/caption.json \
  --platform xhs \
  --frame-timestamp 12.5 \
  --output-dir output/covers \
  --render \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md
```

默认产出 3 套方案和 `*_preview.png` 小图。先按 feed-size 预览比较，再重跑并加 `--select cover-c --require-selection --strict` 记录最终封面。标题负责主题/关键词，封面负责结果/情绪/反差/证据；需要强制不重复时加 `--require-distinct-cover-text`。`pipeline_manifest.py --require cover_variants` 可把选择设为发布 gate。若需要 AI 生成或编辑封面底图，生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

**封面时长**（`cover_duration`）：
- 默认 2.0 秒，将第一帧冻结并叠加封面
- 也可通过 `--cover-duration` 命令行参数覆盖

**章节划分**：
- 根据视频内容逻辑划分章节，建议 **不超过 4 个章节**
- 章节名要**简短**（2-4 个字），如：痛点、原因、方案、工具
- 章节时间需要根据选定片段的累计时长精确计算

### Phase 4.8: Edit Revision（剪辑 artifact 可逆修订，可选）

当用户要求修改 `render_config.json`、`enrich_plan.json`、caption 或字幕 sidecar，同时需要完整审稿、依赖防漂移和 undo/redo 时，先准备 proposal：

```bash
python3 scripts/edit_revision.py prepare \
  --project-dir . \
  --artifact work/render_config.json \
  --artifact work/enrich_plan.json \
  --depends-on work/transcript_reviewed.json \
  --title "收紧开头并调整 B-roll" \
  --reason "已完成时间码审片，采用第二版开头。" \
  --output work/edit_revision_proposal.json
```

只改 proposal 的 `artifacts[].proposed_content`，再运行 `audit`。合法 audit 的 `pending_approval` 在 `--strict` 下返回 2 是预期人工 gate；必须另存绑定相同 `review_id`、`decision: approve` 和非空 `approved_by_label` 的 approval JSON，`apply` 才会把多个文件作为一个 revision 成组写入并创建 `work/edit_revision_history.json`。运行期写入错误会尝试恢复旧 bytes，但跨文件不承诺操作系统级原子提交；进程异常后先跑 `status --strict`。status 会实时检查 artifact、已应用 dependency 和 content-addressed before/after blobs；外部手改、依赖漂移、symlink 或 blob 损坏时拒绝 undo/redo。原始媒体、代码、输出成片和 `verify/` 不在管理范围。详细流程见 [docs/prompts/80-edit-revision.md](docs/prompts/80-edit-revision.md)。

### Phase 4.85: Portable Edit Recipe（可移植剪辑配方，可选）

当用户要把已审 `render_config.json` 保存为模板，或换一批素材复刻同一时间线/字幕/BGM/overlay 结构时，先导出无路径 recipe：

```bash
python3 scripts/edit_recipe.py export \
  --config work/render_config.json \
  --name fast-tech-explainer \
  --description "快节奏科技口播" \
  --output work/recipes/fast-tech-explainer_edit_recipe.json \
  --markdown work/recipes/fast-tech-explainer_edit_recipe.md
```

导出前必须通过 source preflight；本地文件引用会变成 typed slots，同一路径只占一个槽位，recipe 不保存原路径。新项目打开 Markdown slot 表，为每槽各传一次 `--bind SLOT=PATH` 运行 `replay`，同时写 `--receipt` 和 `--markdown`；脚本会验证 portable digest、槽位集合、文件类型与存在性，记录新 binding hash，并对输出 config 再跑 preflight。默认不覆盖，只有明确目标正确时加 `--force`。recipe hash 不是签名或人工批准；回放后仍必须渲染并看完整成片。详细流程见 [docs/prompts/82-edit-recipe.md](docs/prompts/82-edit-recipe.md)。

### Phase 4.9: Platform Safe Area QA（平台 UI 安全区门禁）

在渲染或多平台导出前，按目标平台分别检查字幕、强调 badge、PIP 人像、CTA、章节卡和点击 marker 是否会进入顶部状态栏、底部文案区或右侧互动按钮栏：

```bash
python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --platform xhs \
  --output verify/xhs_platform_safe_area_qa.json \
  --markdown verify/xhs_platform_safe_area_qa.md \
  --guide verify/xhs_platform_safe_area_guide.svg \
  --strict
```

抖音和视频号派生版分别改用 `--platform douyin` / `--platform wxch`。脚本按 `render_final.py` 的默认 ASS、PIP 和 focus-marker 几何规则估算 bbox；renderer 之外的 CTA/Logo 可以通过 `--elements custom_elements.json` 提供像素或 normalized bbox。平台 App UI 变化时用 `--safe-left` / `--safe-top` / `--safe-right` / `--safe-bottom` 覆盖实测边距。

这是本地、可审计的 layout gate：不上传、不调用 LLM、不做 OCR，不推断生成图或封面内部的主体/文字位置。已有整图章节卡会标记 `uncheckable`，需要打开 SVG guide 或渲染帧人工复核。`summary.blocking > 0` 会让 `--strict` 返回 2；需要设为生产线必需项时用 `pipeline_manifest.py --require platform_safe_area_qa --strict`。

### Phase 4.95: Subtitle Style Preview（真实画面字幕样式预览）

最终编码前先把真实 ASS 预设渲染到源片早/中/晚代表帧：

```bash
python3 scripts/subtitle_style_preview.py create \
  --project-dir . \
  --video origin/talking.mp4 \
  --platform xhs \
  --text "这句字幕要覆盖中英文和数字 2026" \
  --preview-dir verify/subtitle_styles \
  --output work/subtitle_style_preview.json \
  --markdown work/subtitle_style_preview.md \
  --require-selection

python3 scripts/subtitle_style_preview.py select \
  --report work/subtitle_style_preview.json \
  --style normal

python3 scripts/subtitle_style_preview.py verify \
  --report work/subtitle_style_preview.json \
  --strict
```

默认比较 `normal` / `minimal` / `bold_pop`，并使用 `render_final.py` 的同一 ASS builder、字体、字号和目标画幅中心裁切。手机尺寸与全尺寸都要看；确认后把所选值传给 `render_final.py --subtitle-style`。源片、字体、样式定义或 JPEG bytes 变化会让旧选择失效。`karaoke` 预览只证明视觉样式，真实逐词节奏仍取决于词级时间戳；最终还要跑 safe-area、readability QA 和 1× 成片审片。详见 [docs/prompts/94-subtitle-style-preview.md](docs/prompts/94-subtitle-style-preview.md)。

### Phase 5: Single-Pass Render（单次渲染）

使用 [render_final.py](./scripts/render_final.py) 从原始视频**一次编码**生成最终视频：

```bash
python3 scripts/render_final.py --config render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output final.mp4 --speed 1.25 1.5
```

**核心原理**：
- **单视频**（最常见场景）：使用 `select/aselect` + `between()` 表达式一次性筛选所有保留片段，FFmpeg 解码完整源视频但只编码选中的帧，配合 `-crf 18 -preset medium` 只编码一次
- **多视频混剪**：使用 `trim/atrim` 裁切 + `concat` 拼接（自动降级）
- 封面使用 `tpad` 冻结第一帧 + `adelay` 添加静音，字幕时间自动偏移，全部在**一次编码**中完成
- 章节时间轴不烧入视频，渲染完成后以文本形式输出，供用户手动粘贴到小红书等平台

参数说明：
- `--config`：渲染配置 JSON 路径
- `--enrich-plan`：可选，读取 `auto_enrich.py` 输出的 JSON，把 B-roll / 章节卡 / 贴纸 / 强调点 / 已生成图片 cue 自动接回渲染
- `--output`：输出文件路径
- `--speed 1.25 1.5`：同时输出变速版本（每个变速版本也是从原始视频直接编码，不是从已编码视频二次压缩）
- `--cover-duration 2.0`：封面冻结时长（秒），覆盖配置中的 `cover_duration`
- `--font-path`：自定义字体文件
- `--font-size`：字幕字号（默认 120，即竖屏宽度 1080 ÷ 9 = 120，每个汉字占屏幕宽度的 1/9，基于实际分辨率自动缩放）
- `--no-subtitles`、`--no-cover`：跳过对应功能

**输出**：
- `final.mp4`（原速）
- `final_1_25x.mp4`（1.25 倍速）
- `final_1_5x.mp4`（1.5 倍速）
- 渲染完成后终端输出章节时间轴文本，可直接复制到小红书

**多平台格式导出**：
```bash
python3 scripts/render_final.py --config render_config.json --output final.mp4 \
  --formats vertical square horizontal
```

同时输出：
- `final_vertical.mp4`（9:16 抖音/小红书/TikTok）
- `final_square.mp4`（1:1 Instagram）
- `final_horizontal.mp4`（16:9 YouTube/B站）

裁切策略为中心裁切（center-crop），保持画面主体不变。

**自动功能**：
- 字幕自动检测语言、自动折行、竖屏优化定位
- 封面自动叠加标题文字（带描边和阴影），冻结首帧 1-2 秒
- 变速版本的字幕时间自动缩放
- B-roll 自动缩放裁切匹配主视频分辨率
- `--enrich-plan` 的定时 B-roll / image overlay 自动缩放裁切匹配主视频分辨率
- 结尾卡片自动拼接黑帧 + 静音 + 淡入淡出字幕
- 持续叠加层和闪烁圆点在封面之后自动启用
- 视频/音频时长自动对齐（`-shortest`），避免平台上传时的时长不匹配问题

### Phase 5b: Export to JianYing / CapCut（导出剪映工程）

如果用户希望在剪映中继续编辑（添加特效、调色、精修转场等），可以将剪辑方案**直接导出为剪映工程文件**，无需 ffmpeg 渲染：

```bash
python3 scripts/export_capcut.py --config render_config.json --output ./my_draft
```

**导出内容**：
- **视频轨道**：所有选定的片段按顺序排列在主视频轨道上，包含片头封面（冻结首帧或自定义封面图）
- **字幕轨道**：每个片段的转录文字作为独立字幕段落，位于画面下方
- **文字轨道**：封面标题、副标题、结尾卡片文字（与字幕分离，便于单独编辑）
- **音频轨道**：背景音乐（BGM），自动设置音量
- **转场**：片段之间自动添加淡入淡出转场（300ms）
- **B-roll**：支持 B-roll 替换画面（使用 broll 路径作为视频源）

参数说明：
- `--config`：渲染配置 JSON 路径（与 render_final.py 共用同一个 render_config.json）
- `--output`：输出文件夹路径（会创建 draft_content.json 和 draft_meta_info.json）
- `--name`：项目名称（默认使用配置中的标题）

**使用方式**：
1. 将导出的文件夹复制到剪映草稿目录：
   - macOS: `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`
   - Windows: `%APPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft/`
2. 打开剪映，在"草稿"列表中即可看到该项目
3. 在剪映中可继续添加特效、调整转场、调色、导出等

**注意**：
- 导出的工程文件使用**绝对路径**引用视频/音频素材，确保素材文件不要移动位置
- 支持剪映专业版（JianyingPro）；剪映 6+ 版本均可打开生成的草稿
- 此功能与 Phase 5（ffmpeg 渲染）**二选一**：如果用户只需要最终视频，用 render_final.py；如果需要进一步在剪映中编辑，用 export_capcut.py

如果字幕已经在剪映/CapCut 里用 Auto Captions 生成并人工校对过，可以反向导入为本项目 transcript：

```bash
python3 scripts/import_capcut_subtitles.py \
  --draft ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/day58 \
  --transcript work/capcut_transcript.json \
  --cut-list work/capcut_gap_cut.json \
  --markdown work/capcut_subtitles.md \
  --source-media origin/talking.mp4
```

也可以导入剪映导出的 SRT：

```bash
python3 scripts/import_capcut_subtitles.py \
  --srt exports/capcut_auto_captions.srt \
  --transcript work/capcut_transcript.json \
  --srt-output output/subtitles/capcut_clean.srt
```

`--cut-list` 用字幕间隙生成 `keep_segments`，适合先用 `timeline_view.py --cut-list` 复核，再交给 `export_edl.py` 或后续粗剪流程。默认只读取 subtitle 材料；如果草稿把自动字幕存为普通文字轨，再加 `--include-overlays`。

如果已经有 SRT 和人工/agent 写好的字幕编号编辑意见，用 `srt_edit_plan.py` 直接生成可复核剪辑方案：

`work/edit_guide.md` 示例：

```md
title: 发布会高光
platform: xhs
keep 3-5: 先用产品发布和用户反应
drop 1-2: 铺垫太慢
keep 8: 补一句核心结论
```

```bash
python3 scripts/srt_edit_plan.py \
  --srt exports/capcut_auto_captions.srt \
  --guide work/edit_guide.md \
  --source-media origin/talking.mp4 \
  --output work/srt_edit_plan.json \
  --render-config work/render_config.json \
  --cut-list work/srt_edit_cut.json \
  --markdown work/srt_edit_plan.md \
  --strict
```

`keep/include/use/select` 行按出现顺序生成最终输出顺序；`drop/skip/exclude/remove` 行写入 review。`--cut-list` 输出按原素材时间排序，适合 `timeline_view.py --cut-list` 看切点；真正的重排序输出看 `--render-config`。需要强制每个字幕编号都被审过时，加 `--require-all-reviewed --strict`。

### Phase 5c: NLE Handoff EDL / FCPXML / OTIO（交给 Premiere / FCP / Resolve）

如果用户不需要剪映草稿，而是要把自动剪辑方案交给专业剪辑软件做调色、混音、精剪或协作复核，可导出单轨 EDL、单 spine FCPXML 或 OpenTimelineIO `.otio`：

```bash
python3 scripts/export_edl.py --config render_config.json --output work/edit.edl --fps 30
python3 scripts/export_edl.py --cut-list work/rough_cut.json --output work/rough_cut.edl --fps 30
python3 scripts/export_fcpxml.py --config render_config.json --output work/edit.fcpxml --fps 30 --width 1080 --height 1920
python3 scripts/export_fcpxml.py --cut-list work/rough_cut.json --output work/rough_cut.fcpxml --fps 30
python3 scripts/export_otio.py --config render_config.json --output work/edit.otio --fps 30
python3 scripts/export_otio.py --cut-list work/rough_cut.json --output work/rough_cut.otio --fps 30
```

`export_edl.py` / `export_fcpxml.py` / `export_otio.py` 都会同时写 `<output>.json` manifest，保留绝对源路径、精确秒数、record/source timecode 和事件清单。`export_otio.py` 默认写 V1 + A1 两条 track，可用 `--no-audio-track` 只交接视频；复杂字幕、overlay、章节卡、B-roll 仍以 `render_final.py` / `export_capcut.py` 为准；NLE handoff 只负责轻量选段时间线。

### Phase 6: Post-render Validation（渲染后验证）

渲染完成后，对最终视频执行验证流程：

**6a. 机器质检（先跑）**：
```bash
python3 scripts/render_qa.py final.mp4 \
  --platform douyin \
  --json final_qa.json \
  --review-dir verify/final_qa \
  --review-clips
```

`render_qa.py` 会检查容器元数据、平台尺寸、视频/音频流、黑屏、长静帧和长静音。对小红书派生文件使用 `--platform xhs`，对抖音/视频号使用 `--platform douyin` 或 `--platform wxch`。`--review-dir` 会写 `render_qa_review.json` / `.md`，`--review-clips` 会为可疑区间抽取短 MP4；如果只想快速查元数据，可加 `--no-filters`。

**6b. 镜头色彩 / 曝光 QA（多机位、B-roll、生成素材或调色后推荐）**：
```bash
python3 scripts/shot_color_qa.py final.mp4 \
  --output verify/shot_color_qa.json \
  --markdown verify/shot_color_qa.md \
  --strict
```

`shot_color_qa.py` 在最终编码文件上用 FFmpeg `signalstats` 按镜头聚合亮度、对比、U/V、饱和度和 broadcast-range 指标，并比较相邻镜头的亮度/色度差。视觉跳变默认只 WARN，必须结合 Markdown 时间码和 `timeline_view.py` 看 master；非 full-range 输出的持续越界或镜头无 sample 默认阻塞。可用 `--scene-boundaries work/scene_boundaries.json` 复用已审场景，或由脚本自动检测。它不是校准 scopes、HDR proof、白平衡/肤色判断或审美评分。`pipeline_manifest.py --require shot_color_qa --strict` 可把报告设为发布必需项。

**6c. 留存节奏风险审计（短视频 master / platform export 推荐）**：
```bash
python3 scripts/subtitle_pack.py \
  --config render_config.json \
  --output-dir output/subtitles \
  --basename final_master \
  --speed 1.25 \
  --offset 2.0

python3 scripts/retention_rhythm_qa.py final.mp4 \
  --timed-text output/subtitles/final_master.json \
  --output verify/retention_rhythm_qa.json \
  --markdown verify/retention_rhythm_qa.md \
  --strict
```

`retention_rhythm_qa.py` 在**已渲染成片**上复用 FFmpeg scene score，并可合并 output-aligned subtitle JSON，检查前三秒 scene/subtitle activity、长视觉 hold、scene + subtitle attention gap、机械等距节奏、过密快切和字幕长时间不刷新。没有 timed text 时，inactive hook 只警告，避免把持续运镜或轻微动效误判成硬失败；高置信严重长 hold / attention gap 会写入 `summary.blocking`。这是可观测节奏风险，不是平台留存率或爆款预测。命中后先看 Markdown 时间范围和 master，再回到源 timeline 重渲染。`pipeline_manifest.py --require retention_rhythm_qa --strict` 可把报告设为发布必需项。

**6c.1. 参考视频剪辑节奏量化（用户明确给参考片时跑）**：
```bash
python3 scripts/reference_edit_rhythm.py analyze \
  --project-dir . \
  --reference origin/reference-ad.mp4 \
  --candidate final.mp4 \
  --evidence-dir verify/reference_edit_rhythm \
  --output work/reference_edit_rhythm.json \
  --markdown work/reference_edit_rhythm.md \
  --strict

python3 scripts/reference_edit_rhythm.py verify \
  --report work/reference_edit_rhythm.json \
  --strict
```

`reference_edit_rhythm.py` 对参考片和候选片运行同一套 hard scene detection，绑定两条视频和两张 contact sheet 的 SHA-256/媒体契约，并比较 cuts/minute、median shot、final-hold 比例、归一化切点位置及 opening/middle/closing cut share。默认偏差只 WARN；只有结构匹配是明确验收条件时才加 `--require-match`。完整看两条视频，只借鉴结构，不复制参考片的画面、音频、品牌或故事。scene score 会漏掉部分 dissolve 与镜头内动作，contact sheet 也不能替代 1× 播放。详见 [docs/prompts/92-reference-edit-rhythm.md](docs/prompts/92-reference-edit-rhythm.md)。

**6c.2. 最终成片 Lip-sync 复核（数字人 / 生成口播 / 对口型 close-up）**：
```bash
python3 scripts/lip_sync_review.py prepare \
  --project-dir . \
  --video final.mp4 \
  --segment hook=2.40:6.10 \
  --anchor hook="把品牌卖点说清楚" \
  --proof-dir verify/lip_sync \
  --output work/lip_sync_review_request.json \
  --markdown work/lip_sync_review_request.md \
  --response-template work/lip_sync_review_response.json

# 看完 1× 带声和 0.25× 静音 proofs、填写 response 后：
python3 scripts/lip_sync_review.py audit \
  --request work/lip_sync_review_request.json \
  --response work/lip_sync_review_response.json \
  --output work/lip_sync_review.json \
  --markdown work/lip_sync_review.md \
  --strict
python3 scripts/lip_sync_review.py verify --report work/lip_sync_review.json --strict
```

短语必须来自最终 master 且完整可见，长度 1–10 秒，优先包含 p/b/m 类闭唇锚点。`pass` 要求爆破音闭唇、元音 timing、讲话时嘴部运动、可见说话人和音频质量全部通过；`not_observable` fail closed。脚本只绑定人工审过的 master/proof bytes，不做人脸或自动音素识别。任何剪切、变速、换音、重编码或 evidence 漂移都要重新 `prepare → audit`。详见 [docs/prompts/93-lip-sync-review.md](docs/prompts/93-lip-sync-review.md)。

**6d. 完整审片代理（需要分享整条视频或精确时间码反馈时跑）**：
```bash
python3 scripts/review_proxy.py final.mp4 \
  --output verify/final_review_proxy.mp4 \
  --manifest verify/final_review_proxy.json \
  --markdown verify/final_review_proxy.md
```

`review_proxy.py` 不修改 master；它输出最大 720p、24fps、H.264/AAC、`+faststart` 的轻量 MP4，并在左上角烧入 `REVIEW PROXY` 和 elapsed timecode。审片意见应引用可见时间码，最终 QA 和发布仍使用 master / platform export。`--dry-run` 只写可复现命令和 artifact，`--no-timecode` 可关闭时间码。

**6e. 可视化复盘（QA 有 WARN/FAIL 或抽查切点时跑）**：
```bash
python3 scripts/timeline_view.py final.mp4 --at 42.5 --radius 1.5 --output verify/42_5s.png
python3 scripts/timeline_view.py origin/talking.mp4 --cut-list work/jumpcut.json --output-dir verify/cuts --limit 12
python3 scripts/timeline_view.py output/rough_cut.mp4 --rendered-cut-list work/rough_cut.json --output-speed 1.25 --output-offset 1.0 --output-dir verify/rendered_cuts --json verify/rendered_cuts.json
```

`timeline_view.py` 会生成 filmstrip + waveform PNG；上半部分看画面连续性，下半部分看人声/静音边界。`--cut-list` 查看源素材时间轴，`--rendered-cut-list` 则按 `keep_segments` 累计时长映射到成片的实际拼接点；有全局变速或片头封面时同步传 `--output-speed` / `--output-offset`。JSON 会保留输出切点和前后 source range；无音频视频会自动只输出 filmstrip。

**6f. 原片/成片 source-time 对照（结构剪辑复核时跑）**：
```bash
python3 scripts/edit_compare.py \
  origin/talking.mp4 final.mp4 \
  --cut-list work/rough_cut.json \
  --output-speed 1.25 \
  --output-offset 2.0 \
  --output verify/source_vs_final.mp4 \
  --report verify/edit_compare.json \
  --markdown verify/edit_compare.md
```

左栏连续播放原片；右栏把最终交付像素投回同一 source clock，被删除范围显示黑屏。脚本会检查时长、双栏尺寸、source-clock 音轨、删段黑屏和代表性保留段像素。V1 只支持单来源、时间升序、无重叠的 `keep_segments` 加全局 speed/offset；重排、多来源或非线性变速要回到 NLE/OTIO 时间线复核。详细说明见 [docs/prompts/74-edit-compare.md](docs/prompts/74-edit-compare.md)。

**6g. 字幕 sidecar 交付（平台需要 SRT/VTT 时跑）**：
```bash
python3 scripts/subtitle_pack.py \
  --config render_config.json \
  --output-dir output/subtitles \
  --basename final_master \
  --speed 1.25 \
  --offset 2.0 \
  --formats srt vtt ass json
```

`subtitle_pack.py` 可从 `transcript.json` 或 `render_config.json` 生成 SRT/VTT/ASS/JSON。`--config` 会按最终 clips 顺序串接字幕时间线；`--speed` 对齐 `render_final.py --primary-speed`；`--offset` 对齐片头封面秒数。JSON manifest 保留每条 cue 的来源片段和 `over_max_chars` 之类校对警告。

生成 JSON 后，对最终字幕执行只读发布门禁：

```bash
python3 scripts/subtitle_readability_qa.py \
  output/subtitles/final_master.json \
  --media final.mp4 \
  --output verify/subtitle_readability_qa.json \
  --markdown verify/subtitle_readability_qa.md \
  --strict
```

`subtitle_readability_qa.py` 检查 output-timeline 的无效时间、乱序、重叠、极短闪现、CPS、持续时间、行数/行长，并可用 FFprobe 验证 cue 没有超过成片结尾。普通 CPS/排版风险只 WARN，必须看正常速度 master；确定性时间事故和极端阅读速度写入 `summary.blocking`。它不做 OCR，不替代字体、描边、位置或遮挡人工审片。`pipeline_manifest.py --require subtitle_readability_qa --strict` 可把报告设为发布必需项。

**6h. 发布上传包（最终上传前跑）**：
```bash
python3 scripts/publish_package.py \
  --project-dir work/day58 \
  --platforms xhs douyin wxch \
  --output work/day58/publish_package.json \
  --markdown work/day58/publish_package.md \
  --strict
```

`publish_package.py` 不上传、不调用平台 API；它只把 `multi_export.py` 的平台 MP4、`generate_caption.py` 的标题/正文/tags、封面、SRT/VTT、章节文本和 `pipeline_manifest` gate 状态合成 `publish_package.v1`。如果存在 `cover_variants.json` 且 `selected_cover` 文件有效，会优先使用已复核封面；显式 `--cover` 可覆盖。如果缺少某个平台视频、caption 为空、或 pipeline manifest 已 blocked，`--strict` 返回 2。需要交给外部发布 connector 时，用这份 JSON 作为 handoff；手工上传时看 Markdown checklist。

**6i. 最终审批收据（客户/人工确认后跑）**：
```bash
python3 scripts/approval_receipt.py create \
  --project-dir work/day58 \
  --artifact output/day58_xhs.mp4 \
  --artifact output/day58_douyin.mp4 \
  --artifact output/day58_wxch.mp4 \
  --artifact output/cover.png \
  --artifact output/day58_caption.json \
  --artifact verify/render_qa.json \
  --approved-by "reviewer label" \
  --output verify/approval_receipt.json \
  --markdown verify/approval_receipt.md

python3 scripts/publish_package.py \
  --project-dir work/day58 \
  --require-approval-receipt \
  --strict
```

`approval_receipt.py` 只绑定显式列出的稳定交付件；不要把每次都会重写的 pipeline manifest、publish package 或 dashboard 放进收据。`verify --strict` 和 `pipeline_manifest.py --require approval_receipt --strict` 会重新读取当前文件并比较 SHA-256；任何重渲染、换封面、改文案、删字幕或路径变成 symlink 都让旧审批过期。`approved_by` 只是本地标签，不是身份认证或数字签名。详细说明见 [docs/prompts/77-approval-receipt.md](docs/prompts/77-approval-receipt.md)。

**6j. 续跑上下文包（跨会话/自动化收尾时跑）**：
```bash
python3 scripts/project_resume.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/project_resume.json \
  --markdown work/day58/project_resume.md \
  --agent-note work/day58/CLAUDE.md \
  --strict
```

`project_resume.py` 复用 `pipeline_manifest.py` 的本地 gate，不渲染、不上传、不提交生成任务。它输出 `project_resume.v1`，包含 `phase`、`recommended_first_action`、`next_actions[]`、最近 artifacts、关键 gate snapshot 和一句可直接交给下一位 agent 的 `suggested_prompt`。长流程被压缩上下文、自动化结束、或要交给另一位 agent 时，优先把 Markdown/agent note 作为接手入口。

**6k. 人工复核面板（最终确认/交接前跑）**：
```bash
python3 scripts/review_dashboard.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/review_dashboard.json \
  --html work/day58/review_dashboard.html \
  --strict
```

`review_dashboard.py` 复用 `pipeline_manifest.py` 的本地 gate，输出 `review_dashboard.v1` 和一个可直接在浏览器打开的 HTML。它把 blocking/missing/warning gate 放进 `review_items[]`，同时列出 `next_actions[]`、最新 artifacts 和完整 gate snapshot。适合用户最终确认，也适合自动化结束时留给下一位 agent。

**6l. 成片复读 / 口吃门禁**：
```bash
python3 scripts/extract_audio.py output/final.mp4
python3 scripts/transcribe.py output/final_audio.wav --model auto --language zh --word-timestamps
python3 scripts/speech_continuity_qa.py output/final_transcript.json \
  --output verify/speech_continuity_qa.json \
  --markdown verify/speech_continuity_qa.md \
  --strict
```

必须对**已渲染 master**重新转录，不能复用源素材 transcript。`speech_continuity_qa.py` 会检查相邻 segment 的结尾/开头精确复读、相邻近重复 take 和句内即时口吃；不同 speaker 默认不互判。命中后先按时间码试听 master，再调整源 `render_config` / cut list，重新渲染并复跑。`pipeline_manifest.py --require speech_continuity_qa --strict` 可把这份报告设为发布必需项。

**6m. PQ/HLG HDR → Rec.709 SDR 交付（HDR master 面向普通社媒/网页时跑）**：
```bash
python3 scripts/hdr_sdr.py plan output/final_hdr.mp4 \
  --delivery output/final_sdr.mp4 \
  --output work/hdr_sdr_plan.json \
  --markdown work/hdr_sdr_plan.md
python3 scripts/hdr_sdr.py apply work/hdr_sdr_plan.json
python3 scripts/hdr_sdr.py verify work/hdr_sdr_plan.json
```

`hdr_sdr.py` 只接受 metadata 明确的 `smpte2084` PQ 或 `arib-std-b67` HLG，并要求 BT.2020 primaries/matrix；未知/矛盾 color tags 会 fail closed。正确转换必须同时有 FFmpeg `zscale` + `tonemap`，不允许用裸 tonemap 或只降到 `yuv420p` 的退化回退。apply 用 linear-light float + Hable 生成 H.264/AAC、`yuv420p`、BT.709 limited-range MP4，显式验证四项 color tag、时长/尺寸/fps/音轨、hash 和全长解码后才原子提升。Dolby Vision/HDR10+ 动态 metadata 不保留；必须在可信 SDR 屏完整复核肤色、高光、阴影、渐变和饱和色，再对 SDR 文件重跑 QA 和审批。详见 [docs/prompts/87-hdr-sdr.md](docs/prompts/87-hdr-sdr.md)。

**6n. 目标大小交付编码（有明确 MB 上限时跑）**：
```bash
python3 scripts/delivery_encode.py plan output/final.mp4 \
  --delivery output/final_delivery.mp4 \
  --max-size-mib 18 \
  --output work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md
python3 scripts/delivery_encode.py apply work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md
python3 scripts/delivery_encode.py verify work/delivery_encode_plan.json
```

`delivery_encode.py` 绑定 master SHA-256，用两遍 `libx264` 在 6% 容器余量内计算 H.264/AAC 码率；输出超过硬上限、容器/流/尺寸/fps/时长/音频不符或完整 FFmpeg decode 失败都会阻塞。apply 只先写同目录临时 MP4，技术验证通过后才原子晋升，不覆盖源文件。完整解码不是画质批准；必须正常速度看完交付版，再跑 `render_qa.py` 并把最终 SHA-256 放入 `approval_receipt.py`。详见 [docs/prompts/85-delivery-encode.md](docs/prompts/85-delivery-encode.md)。

**6o. 字幕文字最终校验**：
1. 读取最终视频使用的所有 transcript 片段的文字
2. 按最终视频的片段顺序，逐条检查以下问题：
   - **语音识别残留错误**：Phase 2.5 可能遗漏的同音字、专有名词错误
   - **口误未清理**：说话人的口误（如说反了、重复了）是否仍然保留在最终视频中
   - **上下文连贯性**：跨视频拼接后，相邻片段之间是否存在语义断裂或逻辑跳跃
   - **字幕一致性**：同一个词/名称在不同片段中是否拼写一致
3. 如发现问题，列出问题清单并展示给用户：
   ```
   字幕校验结果：
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     #  | 问题类型     | 原文 → 建议修正
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     3  | 识别错误     | "检映" → "剪映"
     7  | 口误        | "先说了结果" → 建议删除此片段
    12  | 名称不一致   | "opencloud" → 统一为 "OpenClaw"
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ```
4. 用户确认修正后，修改对应的 transcript.json，然后重新执行 Phase 5 渲染

## Important Notes（注意事项）

### 视频质量与编码准则（最重要）

1. **单次编码原则**：从原始视频到最终输出，**只允许一次编码**。严禁多次重编码（如先切分编码、再烧字幕编码、再加封面编码），每次重编码都会累积质量损失。使用 `render_final.py` 的 `select/aselect` + `between()` 方案（单视频）或 `trim/atrim` + `concat` 方案（多视频），在一条 ffmpeg 命令中完成裁切、拼接、字幕、封面的全部操作。
2. **变速版本也从原始视频直接编码**：`--speed 1.25 1.5` 的变速版本在 `filter_complex` 中集成 `setpts` + `atempo`，直接从原始视频一步到位，**不要**从已编码的 1x 视频再次压缩。
3. **编码参数**：使用固定比特率（如 `-b:v 12M`）而非质量参数（如 `-q:v`）。固定比特率可以精确控制文件大小和质量，避免 `-q:v` 在不同编码器上表现不一致。参考原始视频比特率（通常 8-15 Mbps）设定。
4. **旧流程脚本仅用于预览**：`split_video.py`、`burn_subtitles.py`、`merge_clips.py`、`generate_cover.py`、`add_chapter_bar.py` 仍可单独使用，但**最终输出必须使用 `render_final.py`**。旧脚本适合快速预览单个片段效果。

### Rotation 检测

5. **Rotation 检测**：iPhone 等设备录制的竖屏视频，编码尺寸可能是 1920x1080 + rotation=-90 元数据。所有脚本通过 `utils.get_video_info()` 统一检测 rotation 并自动交换宽高，确保获取正确的显示尺寸。检测视频信息时必须首先检查 rotation。

### 章节时间轴

6. **章节数量**：建议不超过 4 个章节，章节名 2-4 个字。
7. **时间轴不烧入视频**：渲染完成后以文本形式输出章节时间轴（含封面偏移），用户手动复制到小红书等平台的视频描述中。

### 其他

8. **多视频处理**：如果用户提供多个视频，对每个视频独立执行 Phase 1-2.5，然后在 Phase 3 统一展示所有视频的片段列表，支持跨视频混合选择片段。
9. **识别模型选择**：中文视频建议使用 `large` 模型，`base`/`small` 模型中文识别率较低。`large` 模型约需 2.9GB 下载空间。
10. **工作目录**：所有中间文件（音频、转录）都保存在视频文件所在目录下，便于管理。渲染完成后应清理临时文件（ASS 字幕文件、filter_complex 脚本）。
11. **错误处理**：如果某一步失败，向用户报告具体错误信息，并建议可能的解决方案。
12. **字幕字体**：默认使用 LXGW WenKai Medium（霞鹜文楷中粗体），自动下载缓存。ffmpeg 需要编译包含 `libass` 和 `libfreetype`。macOS 可通过 `brew install ffmpeg` 获取。
13. **竖屏适配**：字幕位置和字体大小已针对 9:16 竖屏视频（如小红书、抖音）优化。默认字号 120（= 屏幕宽度 1080 ÷ 9），每个汉字占竖屏宽度的约 1/9。横屏视频同样支持，字号会按实际分辨率自动缩放。

## Remotion Voiceover Workflow（语音生成视频）

当用户只有语音（或音频文件）但没有画面时，使用 Remotion 生成配合语音的视频画面。

详细的 Remotion API 参考、模板样式、组件结构见 [REMOTION_VOICEOVER.md](./REMOTION_VOICEOVER.md)。

### 适用场景

- **纯音频口播** — 有录音/TTS 语音，需要生成匹配的画面
- **音频 + 静态图片** — 有语音和图片素材，需要组合成动态视频
- **播客可视化** — 将播客/对话音频转为带字幕和视觉效果的视频
- **解说视频** — 配音 + 文字动画 + 背景图的组合

### Remotion Workflow（工作流）

1. **音频准备** — 使用 `extract_audio.py` 提取音频，或直接使用用户提供的音频文件
2. **语音识别** — 使用 `transcribe.py` 生成 transcript.json（逐句时间戳）
3. **时间轴生成** — AI Agent 根据 transcript 内容分析语义，生成 `timeline.json`：
   - 将多个 segments 按语义分组为 scenes
   - 为每个 scene 选择类型（title/content/kinetic/quote 等）
   - 选择背景视觉、文字动画、转场效果
   - 配置字幕样式（TikTok 逐词高亮 / 底部字幕 / 全屏文字）
4. **素材准备** — 收集/生成场景所需的图片素材
5. **Remotion 渲染** — 使用 `npx remotion render` 根据 timeline.json 渲染最终视频
6. **后处理（可选）** — 使用 `render_final.py` 与其他视频片段合并

### 视频模板风格选择

| 风格 | 适用场景 | 视觉效果 |
|------|---------|---------|
| `tiktok` | 短视频口播、知识分享 | 渐变背景 + 关键词卡片 + TikTok 风格逐词字幕 + 进度条 |
| `tutorial` | 教程、科普、产品介绍 | 图文分栏 + Ken Burns 图片 + 要点逐行淡入 + 底部字幕 |
| `kinetic` | 情感类、激励类、文案 | 全屏大号文字逐行弹入 + 弹性动效 |
| `podcast` | 播客、访谈、对话 | 头像 + 音频波形可视化 + 引用文字 |
| `news` | 新闻播报、行业资讯 | 顶部横幅 + Lower Third 信息条 + 滚动字幕 |
| `slideshow` | 产品介绍、旅行、相册 | 多图 Ken Burns + 转场效果 + 说明文字 |

### Remotion 环境要求

```bash
# 需要 Node.js 18+ 和 npm
node --version  # >= 18.0.0
npm --version

# 安装 Remotion standup 项目依赖
cd remotion-standup
npm install
```

### 脱口秀/纯音频视频生成（Standup Comedy Workflow）

当用户有一段脱口秀音频（或任何纯语音内容）但没有画面时，可以生成带文字动效的视频：

**Step 1: 语音识别**
```bash
python3 scripts/transcribe.py audio.wav --model auto --language zh
```

**Step 2: 生成时间轴**
```bash
# 默认风格
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --output remotion-standup/public/timeline.json

# 活力风格（更多夸张动效）
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --style energetic \
    --output remotion-standup/public/timeline.json

# 选择特定片段 + 自定义字体
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --segments 1-10,12,15-20 \
    --font "LXGW WenKai, sans-serif" \
    --output remotion-standup/public/timeline.json
```

**Step 3: 预览和渲染**
```bash
cd remotion-standup

# 把音频文件复制到 public 目录
cp /path/to/audio.wav public/audio/standup.wav

# 开发预览
npx remotion studio

# 渲染最终视频
npx remotion render StandupVideo out/standup.mp4 --codec=h264 --crf=18
```

**脚本会自动：**
- 检测笑点/短句（感叹号、关键词、短句跟长句的对比）→ 用 slam/shake/bounce 等夸张动效
- 短句放大字号（emphasis 1.3-1.6x），长句缩小避免溢出
- 笑点使用径向渐变 + 醒目配色（红/橙/金/绿）
- 常规句子使用深色渐变背景 + 不同动画循环
- 自动在中文长句中间插入换行

**12 种文字动画效果：**

| 动画 | 效果 | 适合场景 |
|------|------|---------|
| `fadeIn` | 渐显 | 平稳叙述 |
| `springIn` | 弹性入场 | 正常对话 |
| `scaleUp` | 由小放大 | 强调 |
| `scaleDown` | 由大缩小 | 砸入感 |
| `typewriter` | 打字机 | 引述/对话 |
| `bounce` | 弹跳 | 活泼/搞笑 |
| `shake` | 抖动 | 笑点/吐槽 |
| `slam` | 急速砸入 | 重磅笑点 |
| `wave` | 逐字波浪 | 开场/结尾 |
| `glitch` | 故障闪烁 | 意外/反转 |
| `rotateIn` | 旋转入场 | 切换话题 |
| `splitReveal` | 中间展开 | 揭示/揭晓 |

**3 种风格预设：**

| 风格 | 说明 |
|------|------|
| `default` | 均衡混合所有动画，适合大多数内容 |
| `calm` | 只用平缓动画（fadeIn/springIn/typewriter），适合讲故事/深度内容 |
| `energetic` | 偏重夸张动画（slam/shake/bounce/glitch），适合脱口秀/搞笑内容 |

### timeline.json 配置格式

```json
{
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "audioSrc": "public/audio/voiceover.wav",
  "style": "tiktok",
  "font": {
    "family": "Noto Sans SC",
    "titleWeight": "700",
    "bodyWeight": "400",
    "titleSize": 72,
    "bodySize": 48
  },
  "colors": {
    "background": "#1a1a2e",
    "text": "#ffffff",
    "highlight": "#FFD700",
    "accent": "#e94560"
  },
  "scenes": [
    {
      "id": 1,
      "startMs": 0,
      "endMs": 5000,
      "type": "title",
      "title": "今天聊一个话题",
      "background": { "type": "gradient", "colors": ["#667eea", "#764ba2"] },
      "transition": { "type": "fade", "durationMs": 500 }
    }
  ],
  "captions": {
    "enabled": true,
    "style": "tiktok",
    "fontSize": 56,
    "highlightColor": "#FFD700"
  }
}
```

## Font Catalog（字体目录）

项目内置了一套可下载的免费字体目录，覆盖中文和英文视频制作常用字体。

### 字体管理

```bash
# 查看环境报告（包含已缓存字体列表）
python3 scripts/utils.py

# 在 Python 中使用字体 API
python3 -c "
from scripts.utils import list_available_fonts, download_font

# 列出所有可用字体
for f in list_available_fonts():
    status = '✓' if f['cached'] else '○'
    print(f\"{status} {f['id']:25s} {f['name']:25s} {f['use_case']:8s} {f['description']}\")

# 下载指定字体
download_font('lxgw-wenkai')
download_font('inter')
"
```

### 中文字体

| 字体 ID | 名称 | 风格 | 用途 | 许可证 |
|---------|------|------|------|--------|
| `noto-sans-sc` | Noto Sans SC（思源黑体） | 万能黑体 | 全场景 | SIL OFL |
| `noto-serif-sc` | Noto Serif SC（思源宋体） | 正式宋体 | 文化/古典/深度 | SIL OFL |
| `lxgw-wenkai` | LXGW WenKai（霞鹜文楷） | 手写楷体 | 文艺/文化/情感（~24MB） | SIL OFL |
| `lxgw-wenkai-lite` | LXGW WenKai Lite（轻便版） | 手写楷体 | 同上，体积更小（~13MB） | SIL OFL |
| `lxgw-wenkai-bold` | LXGW WenKai Medium | 手写楷体粗 | 标题 | SIL OFL |
| `zcool-kuaile` | ZCOOL KuaiLe（站酷快乐体） | 圆润可爱 | 轻松/娱乐标题 | SIL OFL |
| `zcool-qingke-huangyou` | ZCOOL QingKe HuangYou（庆科黄油体） | 手写潮流 | 时尚/年轻标题 | SIL OFL |
| `zcool-xiaowei` | ZCOOL XiaoWei（站酷小薇体） | 清秀端正 | 正文/字幕 | SIL OFL |

### 英文字体

| 字体 ID | 名称 | 风格 | 用途 | 许可证 |
|---------|------|------|------|--------|
| `inter` | Inter | 现代无衬线 | 全场景 | SIL OFL |
| `montserrat` | Montserrat | 几何无衬线 | 标题 | SIL OFL |
| `poppins` | Poppins | 圆润几何 | 标题 | SIL OFL |
| `roboto` | Roboto | 中性现代 | 全场景 | Apache 2.0 |
| `oswald` | Oswald | 窄体无衬线 | 新闻/头条标题 | SIL OFL |
| `playfair-display` | Playfair Display | 优雅衬线 | 文艺/高端标题 | SIL OFL |

### 字体选择建议

**默认字体：LXGW WenKai Medium（霞鹜文楷中粗体）** — 手写楷体风格，兼具美观性和可读性，是口播/Vlog 类视频的最佳选择。如果未缓存，会自动下载。

**默认字号：120**（竖屏 1080px 宽度 ÷ 9 = 120），即每个汉字占屏幕宽度的约 1/9，在手机竖屏上清晰易读。

| 视频类型 | 推荐中文字体 | 推荐英文字体 |
|---------|------------|------------|
| **口播/Vlog（默认）** | **LXGW WenKai Medium** | Inter / Roboto |
| 科技/教程 | Noto Sans SC | Inter / Roboto |
| 新闻/资讯 | Noto Sans SC | Oswald / Montserrat |
| 文化/深度 | LXGW WenKai / Noto Serif SC | Playfair Display |
| 娱乐/轻松 | ZCOOL KuaiLe | Poppins |
| 时尚/潮流 | ZCOOL QingKe HuangYou | Montserrat |
| 正式/商务 | Noto Sans SC | Inter |

### 字体在 FFmpeg 中的使用

```bash
# 下载字体后，通过 --font-path 参数指定
python3 scripts/render_final.py --config render_config.json --output final.mp4 \
  --font-path fonts/LXGWWenKai-Regular.ttf
```

### 字体在 Remotion 中的使用

```tsx
// 方式 1: @remotion/google-fonts（英文字体推荐）
import { loadFont } from "@remotion/google-fonts/Inter";
const { fontFamily } = loadFont();

// 方式 2: @remotion/fonts（本地/CJK 字体推荐）
import { loadFont } from "@remotion/fonts";
loadFont({
  family: "LXGW WenKai",
  url: staticFile("fonts/LXGWWenKai-Regular.ttf"),
  format: "truetype",
});

// 方式 3: @remotion/google-fonts 加载 CJK
import { loadFont } from "@remotion/google-fonts/NotoSansSC";
const { fontFamily } = loadFont("normal", {
  weights: ["400", "700"],
  subsets: ["chinese-simplified"],  // 重要：指定子集减小体积
});
```

### 注意事项

- **CJK 字体体积大**（10-20MB），使用 `@remotion/google-fonts` 时务必指定 `subsets: ["chinese-simplified"]` 减小体积
- **字体缓存**：下载的字体保存在项目 `fonts/` 目录，`.gitignore` 已排除
- **中国用户**：字体下载自动使用 jsDelivr CDN 加速
- **商用安全**：目录中所有字体均为 SIL OFL 或 Apache 2.0 许可，可免费商用

## FAQ / Troubleshooting（常见问题诊断）

遇到错误时，先运行环境诊断：
```bash
python3 scripts/utils.py
```

### Q1: `No such filter: 'drawtext'` 或 `No such filter: 'ass'`

**原因**：ffmpeg 编译时未包含 `libfreetype`（drawtext 所需）或 `libass`（字幕所需）。

**诊断**：
```bash
ffmpeg -hide_banner -filters 2>/dev/null | grep -E "drawtext|ass|subtitles"
```
如果无输出，说明缺少对应滤镜。

**解决**：
- **macOS**：标准 `brew install ffmpeg` 可能不包含这些库。使用第三方 tap 安装完整版：
  ```bash
  brew tap homebrew-ffmpeg/ffmpeg
  brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
  ```
  该 tap 默认启用 `--enable-libfreetype --enable-libass --enable-libfontconfig`。
- **Linux/WSL**：`apt install ffmpeg` 通常已包含。如果缺少，安装开发依赖后从源码编译：
  ```bash
  sudo apt install libfreetype6-dev libfontconfig1-dev libass-dev
  ```
- **影响范围**：缺少 drawtext 时，字幕烧录和封面文字会失败或自动降级。

### Q2: `Undefined constant or missing '(' in 'iw*0.5-tw/2'`

**原因**：ffmpeg drawtext 的 `x` 表达式中使用了 `tw`（text width），但某些 ffmpeg 版本中 `tw` 在 `x` 参数的上下文中不可用。

**解决**：脚本已修复此问题（使用像素值 `{pixel_x}-text_w/2` 代替 `iw*{frac}-tw/2`）。如果你修改了脚本并遇到此错误，请使用 `text_w` 而非 `tw`，并确保 `x` 表达式中不包含 `iw*` 动态计算。

### Q3: `Invalid alpha value specifier '%{eif:...}'` (drawtext fontcolor)

**原因**：试图在 `fontcolor` 参数中嵌入 `%{eif}` 表达式来实现透明度渐变，但 ffmpeg 不支持在颜色值中使用此语法。

**解决**：使用 drawtext 的 `alpha` 参数（独立于 fontcolor），而非试图在 `fontcolor=white@'%{eif:...}'` 中嵌入表达式。正确写法：
```
drawtext=text='hello':fontcolor=white:alpha='if(lt(t,1),t,1)'
```
错误写法（会报错）：
```
drawtext=text='hello':fontcolor=white@'%{eif:if(lt(t,1),t,1):d:2}'
```

### Q4: ffmpeg 硬件编码器失败 (`h264_videotoolbox` / `h264_nvenc` / `h264_qsv` 报错)

**原因**：检测到的硬件编码器不支持当前的视频参数（如特殊分辨率、色彩空间），或驱动版本不兼容。

**诊断**：
```bash
ffmpeg -encoders 2>/dev/null | grep -E "nvenc|videotoolbox|qsv|amf"
```

**解决**：在 `scripts/utils.py` 中临时修改 `get_ffmpeg_encoder()` 函数，让它直接返回 `("libx264", ["-preset", "fast", "-crf", "18"])`。

### Q5: 中文字幕显示为方框（豆腐块）

**原因**：系统中没有可用的中文字体文件。

**诊断**：
```bash
python3 -c "from scripts.utils import find_chinese_font; print(find_chinese_font())"
```
如果返回 `(None, ...)`，说明未找到中文字体。

**解决**：
- **macOS**：系统自带 PingFang SC，一般不会出现此问题。
- **Linux/WSL**：安装中文字体包：
  ```bash
  sudo apt install fonts-noto-cjk
  ```
- **WSL 备选**：脚本会自动尝试 `/mnt/c/Windows/Fonts/msyh.ttc`（微软雅黑），前提是 Windows 已安装该字体。
- **手动指定**：使用 `--font-path /path/to/your/font.ttf` 参数。
- **自动下载**：脚本首次运行时会尝试从 Google Fonts（中国用户使用 jsDelivr CDN）下载 Noto Sans SC，缓存到 `fonts/` 目录。

### Q6: Whisper 模型下载失败 / 超时

**原因**：网络问题，尤其是中国用户无法访问 HuggingFace。

**解决**：
- 使用 `--mirror` 参数：`python3 scripts/transcribe.py audio.wav --mirror --model auto`
- 或手动设置环境变量：
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com
  ```
- 使用 faster-whisper 时，模型从 HuggingFace 下载；设置 `HF_ENDPOINT` 后会自动走镜像。
- 使用 openai-whisper 时，模型从 GitHub 下载，中国用户可能需要代理。建议改用 faster-whisper。

### Q7: `pip install faster-whisper` 安装失败 / 超时

**解决**：中国用户使用清华镜像：
```bash
pip install faster-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```

### Q8: WSL 环境下 ffmpeg 找不到或版本过旧

**诊断**：
```bash
which ffmpeg && ffmpeg -version | head -1
```

**解决**：
```bash
sudo apt update && sudo apt install ffmpeg
```
如果系统源的 ffmpeg 版本过旧（< 4.0），使用 PPA：
```bash
sudo add-apt-repository ppa:savoury1/ffmpeg4
sudo apt update && sudo apt install ffmpeg
```

### Q9: 视频质量差 / 模糊

**原因**：视频经过了多次重编码，每次编码都有质量损失。

**解决**：必须使用 `render_final.py` 单次编码。检查是否在流程中使用了 `split_video.py` + `burn_subtitles.py` + `merge_clips.py` + `generate_cover.py` + `add_chapter_bar.py` 的旧流程（会导致 4-5 次重编码）。改用 `render_final.py --config` 一步到位。

## Known Issues & Solutions（已知问题与解决方案）

以下问题在实际视频制作（Day 7-8）中遇到并验证了解决方案。

### K1: 混合帧率源素材用 concat copy 导致视频冻结

**症状**：使用 `ffmpeg -f concat -c copy` 拼接多段 B-roll 素材后，视频在约 1 分钟处画面冻结。

**根因**：DJI 素材为 30/1 fps，部分 DJI 和所有 iPhone MOV 文件为 30000/1001 (29.97fps)。`-c copy` 拼接不会重新编码，帧率不同导致时间戳不连续，播放器在切换点卡死。

**解决**：拼接不同来源的 B-roll 素材前，必须先统一帧率再拼接。对每段素材预处理：
```bash
ffmpeg -i clip.MOV -vf "fps=30" -pix_fmt yuv420p -c:a copy clip_30fps.mp4
```
然后再用 concat 拼接。**禁止对混合帧率素材使用 `-c copy` concat。**

**检测方法**：拼接前用 `ffprobe -v 0 -select_streams v:0 -show_entries stream=r_frame_rate` 检查每个素材的帧率，如果不一致就必须重编码统一。

---

### K2: 复杂 filter_complex 中音频被截断

**症状**：音频在视频中途（如 1:55 处）突然消失，画面正常继续播放。

**根因**：在单个 `filter_complex` 中同时使用 `amix`（混合 `anullsrc` 做静音填充）+ `atempo`（变速）+ 视频处理，`amix` 的 `duration=longest` 与合成的 null 音频源配合不可靠，导致音频流提前结束。

**解决**：将音频处理拆为独立步骤，不要在一个 filter_complex 中同时做音频填充+变速+视频处理：
1. `adelay` 添加封面静音段
2. `atempo=1.25` 变速处理
3. `apad=whole_dur=X` 填充到目标时长
4. 导出为 WAV，然后用 `-c:v copy -c:a aac` 与视频合并

**原则**：音频填充 + 变速 + 视频处理，三者不要放在同一个 filter_complex 中。

---

### K3: render_final.py select filter 在大量片段时 OOM

**症状**：使用 render_final.py 渲染 100+ 片段（如语音旁白配 B-roll 场景）时报 "Cannot allocate memory"。

**历史根因**：早期 `_clips_in_temporal_order()` 只检查是否所有 clip 来自同一视频且时间顺序递增，不检查是否有 broll 字段。当所有 clip 引用同一个语音视频（配不同 B-roll 画面）时，仍会走 select filter 路径，生成包含 100+ 个 `between()` 表达式的巨型 select 过滤器，导致 OOM。

**解决**：
- clip 级 `broll` 已会强制走 trim/concat，不再走 select filter。
- 对于 `auto_enrich.py` 产出的 B-roll cue，优先用 `render_final.py --enrich-plan work/enrich_plan.json`，它会以定时 video overlay 接入，不需要把口播切成大量小片段。
- 如果仍然手工构造 100+ 个 clip 片段，建议使用手动 ffmpeg 管线：
  1. 将所有 B-roll 片段统一帧率后 concat 拼接
  2. 单独处理音频（提取、裁切、变速、填充）
  3. 用 `-c:v copy -c:a aac` 合并视频和音频
- 如果必须使用 render_final.py 的大量 clip 模式，确保片段数量在合理范围内（建议 < 50 个片段）

---

### K4: 中文字幕中英文单词被截断

**症状**：字幕换行时英文单词（如 "OpenClaw"、"Claude Code"）被从中间劈开。

**根因**：`wrap_subtitle_text()` 的中文模式按字符数计算行宽，将英文字母视为与汉字等宽的单个字符。在中间位置换行时不感知 ASCII 单词边界，直接截断。

**解决**：换行函数需要：
1. 使用显示宽度计算：CJK 字符 = 1 单位，ASCII 字符约 0.5 单位
2. 在查找换行点时，检测 ASCII 单词边界（连续的字母/数字视为一个词），不在英文单词或数字中间断行
3. 优先在标点、空格、CJK/ASCII 边界处换行

---

### K5: 封面/结尾卡片不出现

**症状**：渲染配置中配置了 end_cards，但最终视频中看不到结尾卡片。

**根因**：B-roll 拼接后的总时长远超所需（如实际需要 215 秒但 B-roll 拼了 431 秒）。将封面+B-roll+结尾卡片 concat 后，用 `-t` 参数限制总时长时，结尾卡片被截断在时长限制之外。

**解决**：在拼接封面和结尾卡片之前，必须先将 B-roll 精确裁切到与音频时长匹配：
```bash
# 1. 获取音频时长
audio_dur=$(ffprobe -v 0 -show_entries format=duration -of csv=p=0 audio.wav)
# 2. 裁切 B-roll 到音频时长
ffmpeg -i broll_concat.mp4 -t $audio_dur -c copy broll_trimmed.mp4
# 3. 再拼接封面 + 裁切后的 B-roll + 结尾卡片
```
**原则**：拼接前确保每个部分的时长是精确已知的，不要依赖 `-t` 来事后截断。
