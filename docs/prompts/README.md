# Video Editing Skill 提示词教程

> 本教程教你如何与 AI 对话，让它帮你完成各种视频剪辑任务。
> 每个场景都给出了**可以直接复制使用的提示词**。

## 使用前提

- 已安装 Video Editing Skill（参考项目 [README](../../README.md)）
- 已安装 FFmpeg 和 Python 依赖
- 在 Claude Code / OpenClaw 中可以调用该 Skill
- 推荐使用能处理复杂多步 Agent 工作流的模型；OpenAI 侧首选 GPT-5.6 Sol（API 模型 ID `gpt-5.6-sol`，别名 `gpt-5.6` 指向 Sol）

## 教程目录

| 编号 | 场景 | 说明 |
|------|------|------|
| 01 | [口播素材处理](01-oral-broadcast.md) | 从拍摄素材到发布短视频的完整流程 |
| 02 | [分析素材并制定方案](02-analyze-material.md) | 让 AI 分析多条素材，给出剪辑建议 |
| 03 | [补录视频](03-reshoot-video.md) | AI 生成补录清单，补录后继续剪辑 |
| 04 | [补录音频](04-reshoot-audio.md) | 只替换声音，画面保持不变 |
| 05 | [动画配音视频](05-animation-voiceover.md) | 用 Remotion 把录音变成动画解说视频 |
| 06 | [多平台导出](06-multi-platform.md) | 一键导出抖音、Instagram、YouTube 等多比例版本 |
| 07 | [封面生成](07-cover.md) | 多种风格封面，一键生成 |
| 08 | [长视频拆短视频](08-long-to-short.md) | 10分钟长视频自动拆成多条1分钟短视频 |
| 09 | [背景音乐、旁白 Ducking 和片尾](09-bgm-endcard.md) | 添加 BGM、旁白触发 sidechain 自动降音乐、片尾卡片 |
| 10 | [B-roll 画面替换](10-broll.md) | 用其他画面替换口播片段，保留原声 |
| 11 | [批量处理](11-batch.md) | 批量处理多条素材 |
| 12 | [字幕风格定制](12-subtitle-style.md) | 6 种字幕风格，卡拉OK逐词高亮 |
| 13 | [提示词技巧和常见问题](13-tips.md) | 写好提示词的要点，以及常见问题解答 |
| 14 | [导出剪映工程](14-export-capcut.md) | 导出剪映/CapCut 草稿文件，免渲染直接编辑 |
| 15 | [小红书每日科技短视频（V3 完整流水线）](15-xhs-daily-tech-video.md) | 一条提示词跑完转写 → 重组 → 丰富 → 渲染 → 多平台 → 文案 |
| 16 | [Content Guard 平台雷区 lint](16-content-guard.md) | 自动检测违禁词/导流/医美/财富诱导，导出前拦截 |
| 17 | [一条视频 × 三平台导出](17-multi-platform.md) | 主视频 → 小红书 3:4 / 抖音 / 视频号 三版本 |
| 18 | [Auto-Enrich 自动丰富](18-auto-enrich.md) | 自动 B-roll / 章节卡 / 贴纸 / 强调点；BGM 生成 beat edit slots 或吸附已有切点 |
| 19 | [AI 生图（gpt-image-2 / Codex imagegen）](19-imagegen.md) | 抽象概念自动配图，提示词适配 gpt-image-2 |
| 20 | [Render QA 渲染后质检](20-render-qa.md) | 检查尺寸/音频/黑屏/静帧/静音，批量留 QA JSON |
| 21 | [Jump Cut 自动去停顿](21-jump-cut.md) | 自适应静音检测，先出 cut list，再一次渲染去停顿成片，含 20% 删除预算和切点音频 fade |
| 22 | [Timeline View 源素材/成片切点复盘图](22-timeline-view.md) | 生成 filmstrip + waveform PNG，人工复核源素材删除段、成片输出切点和 QA 报警 |
| 23 | [Versioned Output 成片版本化](23-versioned-output.md) | `render_final.py --versioned-output` 自动写入 `_V<N>`，避免覆盖旧成片 |
| 24 | [Storyboard Plan 分镜与生成路由](24-storyboard-plan.md) | transcript/clean script → shot cards、生成路由、连续性锚点 |
| 25 | [Storyboard Assets 素材清单与预检](25-storyboard-assets.md) | storyboard_plan → 素材状态表、paid approval、ready 检查 |
| 26 | [ASR Rough Cut 口头禅/重复句粗剪](26-rough-cut.md) | transcript/filler metadata → 可审计 cut list，可选直接渲染 |
| 27 | [NLE Handoff 导出 EDL/FCPXML/OTIO](27-export-edl.md) | render_config / cut list → 单轨 EDL、FCPXML 或 OTIO + manifest，交给 Premiere/FCP/Resolve |
| 28 | [Screen Focus 点击聚焦](28-screen-focus.md) | 录屏点击/热点 → 自动放大、标记、标签计划 |
| 29 | [Subtitle Pack 字幕交付包](29-subtitle-pack.md) | transcript/render_config → SRT/VTT/ASS/JSON，支持加速和片头 offset |
| 31 | [Highlight Picker 长视频精华候选](31-highlight-picker.md) | 长视频 transcript → 精华候选；可用 `--brief/--query` 定向找片段 |
| 36 | [Transcript Review 同步视频校稿](36-transcript-review.md) | transcript + 本地媒体 → 行内编辑、播放高亮、CPS 提示、review.txt 回写 |
| 43 | [Audio Cue Sheet 音频设计清单](43-audio-cue-sheet.md) | transcript → BGM/SFX cue、生成审批和音频门禁 |
| 44 | [Stock Material Plan 远程素材搜索规划](44-stock-material-plan.md) | 主题/脚本 → stock 搜索词、Pexels/Pixabay/Coverr 查询计划、本地素材登记 |
| 45 | [Video Prompt Pack 视频生成提示词包](45-video-prompt-pack.md) | storyboard_plan → Dreamina/Veo/LTX/Wan/Sora 提示词、角色一致性和 paid approval gate |
| 46 | [Generation Task Log 异步生成任务台账](46-generation-task-log.md) | 记录 submit_id/task id、轮询/下载命令、本地落盘和发布前 blocking gate |
| 47 | [Video Understanding 抽样帧 + 可选 YOLO](47-video-understanding.md) | 视频 → frames/detections/tracks/scene_tags，供重构图、隐私遮挡和 B-roll 标签复用 |
| 48 | [Color Grade 调色计划](48-color-grade.md) | 生成 bounded 调色 plan，并在 render_final 单次编码中接入 |
| 49 | [Publish Package 最终上传包](49-publish-package.md) | 平台视频、封面、字幕、文案、章节和 gate 状态汇总 |
| 50 | [CapCut Subtitle Import 剪映字幕反向导入](50-import-capcut-subtitles.md) | 剪映/CapCut 自动字幕或 SRT → transcript / gap cut list |
| 51 | [PIP Overlay 摄像头小窗](51-pip-overlay.md) | 录屏 + facecam → timed PIP 小窗计划，render_final 单次编码合成 |
| 52 | [Project Resume 续跑上下文包](52-project-resume.md) | 本地 artifacts → agent handoff JSON/Markdown/CLAUDE.md，跨会话继续项目 |
| 53 | [Edit Preflight 渲染前预检](53-edit-preflight.md) | render_config/enrich_plan/cut list → 缺文件、非法时间段、危险参数 gate |
| 54 | [Audio Master Report 成片响度报告](54-audio-master-report.md) | final master → LUFS / true peak / LRA / silence 发布门禁 |
| 55 | [SRT Edit Plan 字幕编辑指令转剪辑方案](55-srt-edit-plan.md) | SRT + keep/drop 编辑指令 → render_config / cut list / review |
| 56 | [Audio Sync 外录音频自动对齐](56-audio-sync.md) | scratch audio + 外录音轨 → offset / replace-audio command / gate |
| 57 | [Review Dashboard 人工复核面板](57-review-dashboard.md) | 本地 artifacts → HTML/JSON review queue、next actions 和 gate snapshot |
| 58 | [Source Receipts 事实来源复核](58-source-receipts.md) | 视频 claim → URL/截图 proof deck、Markdown/HTML source deck 和发布 gate |
| 59 | [Auto Emphasis 口播重点自动落点](59-auto-emphasis.md) | 问句 / 数字 claim / 转折 / 结论 → badge + subtle push-in |
| 60 | [Takes Pack 多 take 阅读视图](60-takes-pack.md) | 多个 transcript / 顶层 Scribe words → phrase-level Markdown/JSON，保留 speaker 与 audio events |
| 61 | [Project Bootstrap 项目启动与素材导入](61-project-bootstrap.md) | 原始素材目录 → origin/work/output/verify/edit + source inventory + project memory |
| 62 | [Hook Variants 开头钩子批量方案](62-hook-variants.md) | transcript/clean script → 多个前三秒 hook 角度、风险检查和推荐排序 |
| 63 | [Shorts Batch 多条精华短视频渲染 job sheet](63-shorts-batch.md) | highlight_candidates → per-short render_config、渲染命令和 QA 命令 |
| 64 | [Edit Brief Plan 自然语言剪辑需求路由](64-edit-brief-plan.md) | 用户一句话需求 → 本地脚本 runbook、命令、产物和 manifest gate |
| 65 | [Audio Boundary Snap 音频感知剪辑边界](65-audio-boundary-snap.md) | selected highlights → 词/句末/静音边界、delta、blocker |
| 66 | [Review Proxy 低码率时间码审片视频](66-review-proxy.md) | master/platform MP4 → timecoded web-ready review proxy + manifest |
| 67 | [Speech Continuity QA 成片复读 / 口吃门禁](67-speech-continuity-qa.md) | master 二次 transcript → 切点复读、近重复 take、句内口吃 gate |
| 68 | [Cover Variants 封面 A/B 方案](68-cover-variants.md) | 同一视频生成多套封面、feed-size 预览、协同检查和最终选择 |
| 69 | [Retention Rhythm QA 成片留存节奏风险](69-retention-rhythm-qa.md) | master/platform export → hook activity、长镜头、注意力空窗和 cadence gate |
| 70 | [Subtitle Readability QA 最终字幕可读性门禁](70-subtitle-readability-qa.md) | output-aligned subtitle JSON → CPS、时长、行长、重叠和媒体越界 gate |
| 71 | [Reference Frame Preflight 生成参考帧预检](71-reference-frame-preflight.md) | video_prompt_pack → 首帧/style key 尺寸、方向、画幅、透明背景 gate |
| 72 | [Visual Dedupe 跨素材重复镜头复核](72-visual-dedupe.md) | 多来源 scene boundaries → 三点感知哈希、重复组、保留建议和 review gate |
| 73 | [Platform Safe Area QA 平台 UI 安全区门禁](73-platform-safe-area-qa.md) | render_config/enrich_plan/custom bbox → 字幕、PIP、CTA、marker 平台遮挡 gate + SVG guide |
| 74 | [Edit Compare 原片/成片 source-time 对照](74-edit-compare.md) | source + final + keep_segments → 双栏可播放审片视频、删段置黑和像素映射验证 |
| 75 | [Speech Denoise 口播稳态底噪清理](75-speech-denoise.md) | `render_final.py` 单次编码内可选 highpass + afftdn，默认关闭 |
| 76 | [Multicam Sync 多机位可逆同步](76-multicam-sync.md) | 2+ 设备 → offset / coverage / 有效音轨 / pairwise / 对齐预览 gate |
| 77 | [Approval Receipt 最终审批收据](77-approval-receipt.md) | 已复核视频/封面/文案/字幕/QA → SHA-256 收据、过期检测和发布 gate |
| 78 | [Target Script Alignment 目标脚本对齐剪辑](78-script-alignment.md) | 已审目标稿 + 多 take transcript → 原话候选、人工 choices、render_config 和 gate |
| 79 | [Semantic Transcript Review 全篇上下文语义校稿](79-semantic-transcript-review.md) | transcript → 前后文审校包、最小补丁验证、人工 choices 和 reviewed transcript |
| 80 | [Edit Revision 剪辑 artifact 可逆修订](80-edit-revision.md) | render_config/enrich_plan 等文本 artifact → source-bound proposal、独立审批、成组 apply、undo/redo 和 live gate |
| 81 | [Shot Color QA 镜头色彩 / 曝光门禁](81-shot-color-qa.md) | rendered master → 镜头亮度/对比/色度/饱和度/broadcast-range 与切点跳变复核 |
| 82 | [Portable Edit Recipe 可移植剪辑配方](82-edit-recipe.md) | 已审 render_config → typed slots / content digest / 新素材绑定回放 / preflight receipt |
| 84 | [Video Stabilization source-bound 手持防抖](84-video-stabilization.md) | 源 hash / 确切 FFmpeg 后端 / 新工作副本 / 全长 A/B 对照 / 人工确认 gate |

## 快速上手

如果你是第一次使用，建议从 [01-口播素材处理](01-oral-broadcast.md) 开始，它覆盖了完整的工作流程。

如果你已经熟悉基本流程，可以直接跳到你需要的场景。

## 一句话速查

| 我想做什么 | 用哪个提示词 |
|-----------|-------------|
| 拍了口播想剪成短视频 | [01-口播素材处理](01-oral-broadcast.md) |
| 不知道素材怎么用 | [02-分析素材](02-analyze-material.md) |
| 有些地方讲得不好要重拍 | [03-补录视频](03-reshoot-video.md) |
| 画面没问题但声音要重录 | [04-补录音频](04-reshoot-audio.md) |
| 只有录音想做成视频 | [05-动画配音](05-animation-voiceover.md) |
| 一个视频发多个平台 | [06-多平台导出](06-multi-platform.md) |
| 需要视频封面 | [07-封面生成](07-cover.md) |
| 长视频拆成多条短的 | [08-长视频拆短视频](08-long-to-short.md) |
| 加背景音乐、旁白时自动降音乐或加片尾 | [09-背景音乐和片尾](09-bgm-endcard.md) |
| 口播换画面 | [10-B-roll 替换](10-broll.md) |
| 好多条视频一起处理 | [11-批量处理](11-batch.md) |
| 字幕好看一点 | [12-字幕风格](12-subtitle-style.md) |
| 提示词怎么写更好 | [13-技巧和FAQ](13-tips.md) |
| 导出到剪映继续编辑 | [14-导出剪映工程](14-export-capcut.md) |
| 每天做一条小红书科技短视频 | [15-V3 完整流水线](15-xhs-daily-tech-video.md) |
| 担心标题/正文触发平台限流 | [16-Content Guard](16-content-guard.md) |
| 一次发小红书+抖音+视频号 | [17-三平台导出](17-multi-platform.md) |
| 想让视频更"有质感"自动加丰富度 | [18-Auto-Enrich](18-auto-enrich.md) |
| 只想给口播重点加轻强调 | [59-Auto Emphasis](59-auto-emphasis.md) |
| 抽象概念想用 AI 生图（注意力机制/复利…） | [19-imagegen](19-imagegen.md) |
| 渲染后想确认没有黑屏/静帧/静音/尺寸错 | [20-Render QA](20-render-qa.md) |
| 口播停顿太多想自动剪紧 | [21-Jump Cut](21-jump-cut.md) |
| 想人工看源素材或成片切点附近画面和波形 | [22-Timeline View](22-timeline-view.md) |
| 不想每次渲染覆盖上一版成片 | [23-Versioned Output](23-versioned-output.md) |
| 生成图/生成视频前想先审分镜和路由 | [24-Storyboard Plan](24-storyboard-plan.md) |
| 渲染前想确认分镜素材是否都 ready | [25-Storyboard Assets](25-storyboard-assets.md) |
| 口头禅、卡壳和重复句太多 | [26-ASR Rough Cut](26-rough-cut.md) |
| 想把自动剪辑方案交给专业剪辑软件 | [27-NLE Handoff](27-export-edl.md) |
| 软件录屏里想自动放大点击位置 | [28-Screen Focus](28-screen-focus.md) |
| 录屏教程想叠加讲解人小窗 | [51-PIP Overlay](51-pip-overlay.md) |
| 平台要上传 SRT/VTT 字幕文件 | [29-Subtitle Pack](29-subtitle-pack.md) |
| 想从长视频里按主题/brief 找片段 | [31-Highlight Picker](31-highlight-picker.md) |
| 想先规划 BGM 和音效再渲染 | [43-Audio Cue Sheet](43-audio-cue-sheet.md) |
| 本地 B-roll 不够，想先规划 stock 素材搜索 | [44-Stock Material Plan](44-stock-material-plan.md) |
| 分镜要交给 Dreamina/Veo/LTX/Wan/Sora 生成视频 | [45-Video Prompt Pack](45-video-prompt-pack.md) |
| 已提交异步生成任务，要保存 submit_id 并跟踪下载 | [46-Generation Task Log](46-generation-task-log.md) |
| 想识别视频里的人、屏幕、手机或动态主体 | [47-Video Understanding](47-video-understanding.md) |
| 想统一成片色彩或加轻微质感 | [48-Color Grade](48-color-grade.md) |
| 准备上传前想核对平台视频、文案和 gate | [49-Publish Package](49-publish-package.md) |
| 已在剪映里生成/校对自动字幕，想回到本 pipeline | [50-CapCut Subtitle Import](50-import-capcut-subtitles.md) |
| 视频项目暂停后想让下一位 agent 接着做 | [52-Project Resume](52-project-resume.md) |
| 渲染前想先挡住缺文件/坏时间段 | [53-Edit Preflight](53-edit-preflight.md) |
| 想确认成片音量、爆峰和长静音是否达标 | [54-Audio Master Report](54-audio-master-report.md) |
| 已有 SRT 和保留/删除字幕编号，想生成剪辑方案 | [55-SRT Edit Plan](55-srt-edit-plan.md) |
| 相机内录音和外录麦克风音频需要自动对齐 | [56-Audio Sync](56-audio-sync.md) |
| 渲染或发布前想打开一个总复核面板 | [57-Review Dashboard](57-review-dashboard.md) |
| 视频里有事实、数据、新闻或来源截图要复核 | [58-Source Receipts](58-source-receipts.md) |
| 数字、转折、结论想自动落视觉重点 | [59-Auto Emphasis](59-auto-emphasis.md) |
| 多个 take 或带笑声/掌声事件的 Scribe transcript 想先压成可读时间码清单 | [60-Takes Pack](60-takes-pack.md) |
| 刚拿到一批原始素材，想先建项目目录 | [61-Project Bootstrap](61-project-bootstrap.md) |
| 想给同一条视频准备多个前三秒开头 | [62-Hook Variants](62-hook-variants.md) |
| 长视频已选好多个精华片段，想批量规划渲染 | [63-Shorts Batch](63-shorts-batch.md) |
| 只有一句剪辑需求，不确定该跑哪些脚本 | [64-Edit Brief Plan](64-edit-brief-plan.md) |
| 精华片段已经选好，想避免吞字或半句结尾 | [65-Audio Boundary Snap](65-audio-boundary-snap.md) |
| 想把整条视频发给客户审片并精确引用时间码 | [66-Review Proxy](66-review-proxy.md) |
| 成片剪完后想检查是否残留复读或口吃 | [67-Speech Continuity QA](67-speech-continuity-qa.md) |
| 想给同一条视频生成多套封面并选一张发布 | [68-Cover Variants](68-cover-variants.md) |
| 成片剪完后想检查前三秒、长镜头和节奏空窗 | [69-Retention Rhythm QA](69-retention-rhythm-qa.md) |
| 发布前想检查字幕是否重叠、闪现或来不及读 | [70-Subtitle Readability QA](70-subtitle-readability-qa.md) |
| 视频生成前想检查首帧和 style key 是否适配目标画幅 | [71-Reference Frame Preflight](71-reference-frame-preflight.md) |
| 多机位、多 take 或 B-roll 里有重复镜头，想先去重候选 | [72-Visual Dedupe](72-visual-dedupe.md) |
| 发布前想检查字幕、PIP、CTA 或点击标记会不会被平台 UI 挡住 | [73-Platform Safe Area QA](73-platform-safe-area-qa.md) |
| 想逐秒对照原片和最终成片到底保留、删除了什么 | [74-Edit Compare](74-edit-compare.md) |
| 同一访谈/活动有多个机位，需要先对齐到同一时间线 | [76-Multicam Sync](76-multicam-sync.md) |
| 已有确认文案，需要从多个 take 按目标顺序找回原话 | [78-Target Script Alignment](78-script-alignment.md) |
| 想确保准备上传的文件仍是人工看过的那一版 | [77-Approval Receipt](77-approval-receipt.md) |
| Whisper 字幕要用全篇上下文检查专业术语和同音错词 | [79-Semantic Transcript Review](79-semantic-transcript-review.md) |
| 想让 render_config/enrich_plan 的修改可审、可撤销、可重做 | [80-Edit Revision](80-edit-revision.md) |
| 成片混了多机位/B-roll，想查曝光、偏色或切点色彩跳变 | [81-Shot Color QA](81-shot-color-qa.md) |
| 想把已审 render_config 存成模板，换一批素材继续复用 | [82-Portable Edit Recipe](82-edit-recipe.md) |
| 想给动作 / 产品 reveal 的 impact moment 做局部慢动作或 velocity edit | [83-Speed Ramp](83-speed-ramp.md) |
| 手持素材有不想要的抖动，想保留原片并对照防抖结果 | [84-Video Stabilization](84-video-stabilization.md) |
