# Video Editing Skill — 视频剪辑技能

这是一个面向 **口播、教程、访谈、播客切片、录屏演示 / facecam demo** 的 AI 视频剪辑生产线：给它原始口播音频/视频、transcript、B-roll、摄像头小窗或素材目录，它可以把“还没整理的素材”推进到 **可发布的小红书 / 抖音 / 视频号短视频**。

它不是一个单点 FFmpeg 脚本，而是一条完整工作流：**项目启动/素材导入 → 手持防抖 → 转写 → 长视频择段 → 清稿 → 去口头禅/停顿 / 多模态死区 → 重组故事 → 事实来源 proof deck → 分镜 → B-roll/生图/生成视频规划 → 字幕与声音设计 → BGM 卡点 / 局部 speed ramp / J-cut/L-cut → 可逆剪辑修订 / 可移植剪辑配方 → 渲染前预检 → 单次编码渲染 → 质检 → 多平台导出 / 目标大小交付编码 → 标题文案 → 续跑交接**。适配 **小红书 / 抖音 / 微信视频号** 的比例、节奏、字幕、文案和常见审核风险。

## 适合做什么

- **把口播短视频从“素材堆”推进到“发布包”**：项目目录、source inventory、转写、清稿、分镜、素材清单、渲染配置、字幕 sidecar、QA、标题正文和标签都能落成可审计 artifact。
- **针对中文社媒口播做过生产化调参**：Heavy CJK 字幕、1.25x 主输出、响度规范化、平台违禁词 lint、章节卡、贴纸、BGM/SFX cue、三平台导出都不是通用 demo。
- **噪声口播可在单次编码内保守清理**：`render_final.py --speech-denoise light|medium|strong` 会在变速、压缩、响度规范化和 BGM ducking 前处理低频震动与稳态底噪；默认关闭，最大降噪限制为 12 dB。
- **停顿删段可同时看声音和画面**：`multimodal_dead_air.py` 只有在静帧覆盖静音达到门槛时才提出候选，实际只删二者交集；源 hash、20% 删除预算、切点复盘、单次编码和完整解码都进入 gate。
- **多机位先同步再剪辑**：`multicam_sync.py` 把两台以上相机/手机/录音设备对齐到同一参考时间线，记录每路 offset、置信度、有效音轨、公共重叠区间，并可用多窗口 probe 测量长片时钟漂移；原片不改、不重编码。
- **手持防抖保留原片和 A/B 证据**：`video_stabilization.py` 把源 SHA-256、确切 FFmpeg 后端和人工决定写进计划；apply 只生成新工作副本与全长左右对照，完整 1× 复核并 confirm 后 manifest 才放行。
- **局部慢动作先计划再渲染**：`speed_ramp.py` 把显式 impact frame 周围的 `snap/ease/s_curve`、hold 和可选 FFmpeg 插帧编译成 source-bound 计划；源 hash 或 piece 时间映射漂移会阻塞，apply 采用同目录临时文件事务式落盘。
- **上传大小限制变成硬门禁**：`delivery_encode.py` 依据源片时长计算两遍 H.264/AAC 码率，绑定源与输出 SHA-256；完整解码、音视频契约或硬大小上限任一失败都不会提升成交付件。
- **专业声画错位不再靠手写 FFmpeg**：`audio_transition.py` 对明确边界规划 J-cut/L-cut，验证真实音频 handle、config/transcript/source hash 和 compiled timing；`render_final.py` 在同一次编码中完成画面硬切、音频 pre-lap/overhang、字幕、overlay 与 BGM。
- **事实型内容有 proof deck**：新闻、数据、产品事实或来源页截图可用 `source_receipts.py` 生成 URL/截图复核包，作为发布前 gate。
- **最终审批绑定到具体文件字节**：`approval_receipt.py` 为人工看过的视频、封面、文案、字幕和 QA 报告记录 SHA-256；任何重渲染、替换、删除或 symlink 漂移都会让旧审批过期并阻塞发布。
- **ASR 语义校稿不再只看单句**：`semantic_transcript_review.py` 为每条字幕附带全篇前后文，验证完整覆盖、源 transcript hash 和最小字符补丁；模型只能提建议，独立人工 choices 才能写 reviewed transcript。
- **上游剪辑配置可以安全撤销/重做**：`edit_revision.py` 把 `render_config` / `enrich_plan` 等文本 artifact 的完整改动绑定到基础和依赖 SHA-256；独立审批后成组写入，外部漂移时拒绝 undo/redo 并阻塞 manifest。
- **已审时间线可以换素材复用**：`edit_recipe.py` 把 `render_config.json` 的全部本地文件路径替换成类型化槽位，生成 content-addressed 可移植配方；回放必须完整绑定新素材、记录 SHA-256 并重新通过 `edit_preflight.py`。
- **生成式素材有明确审批和台账**：Codex `image_gen` / GPT Image 2 提示词、Dreamina/Veo/LTX/Wan/Sora 视频提示词、provider 决策、`submit_id` 轮询下载和本地落盘 gate 都先记录再执行。
- **生成片段复核会反哺下一次提示词**：`generation_lessons.py` 只从 canonical clip review 提取人工明确批准的通用经验，绑定 source digests，并按 provider/model/category 精确筛选后交给 `video_prompt_pack.py`；不会把单片修复建议自动当成全局规则。
- **参考片节奏先量化再借鉴**：`reference_edit_rhythm.py` 用同一套 hard-cut 检测比较参考片和成片的 cuts/minute、镜头时长、结尾 hold 与切点分布，同时绑定两条视频和 contact sheets；默认只提示差异，明确验收时才阻断。
- **适合交给强推理模型做长流程代理执行**：在 [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)（OpenAI 当前旗舰；API 别名 `gpt-5.6` 指向 Sol）和 [Claude Opus 4.8](https://docs.anthropic.com/en/docs/about-claude/models) 这类面向复杂专业任务、agent 工作流的模型下，本 skill 对 **口播类短视频** 至少可以替代 **80% 的常规视频剪辑工作**。

这里的“80%”是按口播短视频生产来评估的：它已经覆盖素材整理、ASR、清稿、粗剪、字幕、B-roll/图像/生成视频规划、声音 cue、渲染前预检、渲染、质检、多平台导出和发布文案。剩下通常需要人工负责的是选题判断、最终审美取舍、品牌口吻、客户确认、复杂手工精修、调色混音和需要逐帧 keyframe 的高级特效。

模型说明：本仓库不会在脚本中硬编码 LLM 型号；这里推荐的是负责理解需求、编排脚本、调用工具和复核产物的 Agent 模型。OpenAI 官方将 `gpt-5.6-sol` 定位为 GPT-5.6 家族的旗舰型号；追求更低成本或更高吞吐时，可按运行环境选用 GPT-5.6 Terra 或 Luna。

## 项目现状与边界

做得比较完整的地方：

- **定位清楚**：口播、教程、访谈、录屏和长视频切短视频是高重复剪辑场景，脚本化收益高。
- **流水线完整**：从 transcript 到 publish gate 的 artifact 很全，失败点可复查，不依赖聊天上下文记忆。
- **风险控制到位**：内容合规、素材授权、隐私遮挡、生成任务审批、渲染 QA、pipeline manifest 都有阻塞门禁。
- **本地优先**：核心剪辑、渲染、质检不需要云端服务；外部生成任务只做规划、审批和台账。

需要用户知道的边界：

- **不应包装成全类型剪辑替代品**：电影感剪辑、MV、广告大片、复杂调色混音和精细动效仍需要专业人工。
- **AI 生成素材不等于自动可用**：Dreamina/Veo/Sora 等结果必须经过下载、授权、视觉连续性和 QA 检查。
- **平台规则不是法律意见**：`content_guard.py` 能拦常见风险词，但最终发布仍要人审。
- **README 和 SKILL 偏长**：功能很全，但新用户需要先走推荐入口，不适合从完整脚本文档硬读。

## 视频素材理解

当前版本已经加入可选的视频理解层：基础流程不依赖机器视觉模型，安装 `ultralytics` 后可以用 YOLO 对抽样帧做物体检测，并把结果整理成统一的 `video_understanding.v1`。

- `extract_keyframes.py` 会抽关键帧和时序图，帮助 agent/用户快速看懂一段视频的视觉内容。
- `scene_boundaries.py` 支持固定阈值和邻域自适应 FFmpeg scene score，输出逐切点证据，供长视频拆条、抽帧和 highlight snap 使用。
- `visual_dedupe.py` 会读取多个来源及其 `scene_boundaries.v1`，对每个场景取 10%/50%/90% 三帧做感知哈希，找出跨素材重复镜头并生成保留建议；只输出复核计划，不删除源素材。
- `video_understanding.py` 会按固定间隔和场景边界抽帧；`--detector yolo` 会运行 Ultralytics YOLO，输出 `frames[]`、`detections[]`、`tracks[]`、`scene_tags[]` 和 review Markdown。
- `media_library.py` 会读取视频元数据、文件名、标签、素材来源和关联 transcript，用透明分数推荐本地 B-roll。
- `smart_reframe.py` 可以读取 `video_understanding.json`，按人脸、人物、主体、物体等权重生成竖屏/方屏重构图计划。
- `privacy_redact.py` 可以读取同一份检测框或人工框，对人脸、车牌、屏幕敏感区域生成 blur/pixelate/mask 计划。

这个设计是有意的：YOLO/RT-DETR/MediaPipe 等模型会带来模型下载、GPU/Metal/CUDA 兼容和速度问题；对大量口播视频来说，ASR + 关键帧 + 场景边界已经能覆盖主要剪辑决策。因此本项目把 YOLO 做成“需要时打开”的增强能力，而不是强制依赖。

`video_understanding.py` 的工作方式：

1. 先用 FFmpeg 按场景边界和固定间隔抽帧，避免对每一帧跑模型。
2. 如果安装了 `ultralytics`，用 YOLO 检测 `person`、`phone`、`laptop`、`screen/tv`、`car`、`bottle/cup` 等短视频常见对象。
3. 用采样帧上的 bbox 做轻量 IoU/中心点关联，合并成 `tracks[]`，用于判断主体是否移动、是否大面积占画、是否适合自动重构图。
4. 输出统一的 `video_understanding.v1`：
   - `frames[]`：时间戳、关键帧路径、场景 id。
   - `detections[]`：label、bbox、confidence、source model。
   - `tracks[]`：主体轨迹、出现时间段、中心点、面积变化、运动强度。
   - `scene_tags[]`：人物、屏幕、产品、街景、车辆、手部演示等可检索标签。
   - `warnings[]`：未启用 detector、未检测到对象、低置信度检测等。
5. 下游复用这个 JSON：`smart_reframe.py` 做主体感知裁切，`privacy_redact.py` 做隐私遮挡，`media_library.py` 写入视觉标签，`storyboard_assets.py` 选择更匹配的 B-roll，`pipeline_manifest.py` 可把未复核的视觉检测列为 gate。

常用方式：

```bash
# 可选：只有需要 YOLO 检测时安装
pip install ultralytics

python3 scripts/scene_boundaries.py origin/talking.mp4 \
  --method adaptive \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md

# 多机位/多条 B-roll 先分别生成 scene boundaries，再把来源写进 manifest。
# manifest 内的相对路径以 manifest 所在目录为基准。
python3 scripts/visual_dedupe.py \
  --manifest work/visual_dedupe_sources.json \
  --output work/visual_dedupe.json \
  --markdown work/visual_dedupe.md \
  --strict

python3 scripts/video_understanding.py origin/talking.mp4 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md \
  --frames-dir work/video_frames \
  --scene-boundaries work/scene_boundaries.json \
  --detector yolo \
  --model yolo11n.pt \
  --sample-interval 2 \
  --max-frames 32 \
  --strict

python3 scripts/smart_reframe.py origin/talking.mp4 \
  --detections work/video_understanding.json \
  --platform douyin \
  --output work/reframe_douyin.json \
  --markdown work/reframe_douyin.md
```

如果不想安装 YOLO，也可以只生成抽样帧和 review shell：

```bash
python3 scripts/video_understanding.py origin/talking.mp4 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md
```

对于需要更细的逐帧动态跟踪的素材，可以把 Ultralytics `model.track(..., tracker="bytetrack.yaml")`、BoT-SORT 或 Norfair 的结果转换成同一份 `detections[]` / `tracks[]` JSON 再交给本项目。当前内置版本优先服务口播剪辑：抽帧检测 + 轻量轨迹已经足够支持主体裁切、隐私遮挡提示和 B-roll 标签。

```
口播音频 + 无声素材
   │
   ├─→ project_bootstrap.py     原始素材目录 → origin/work/output/verify/edit + source inventory
   ├─→ edit_brief_plan.py       用户一句话需求 → 本地脚本 runbook / commands / gates
   ├─→ transcribe.py            转写 + 词级时间戳 + 口误标记
   │                            (mlx-whisper / faster-whisper / openai-whisper)
   ├─→ semantic_transcript_review.py
   │                            全篇前后文审校包 / 最小补丁验证 / 人工 choices gate
   ├─→ transcript_review.py     文本 round-trip / 本地同步视频 HTML 校稿
   │                            行内编辑 / 播放高亮 / 查找替换 / CPS 提示 / review.txt
   ├─→ takes_pack.py            多 take / Scribe transcript → phrase-level 阅读视图
   │                            speaker / audio_event / takes_packed.md / takes_pack.json
   ├─→ script_alignment.py      已审目标稿 → 多 take 原话候选 / choices / render_config
   │                            词/段边界 / 透明分数 / 歧义与缺素材 gate
   ├─→ audio_sync.py            外录音轨自动对齐 / 替换音轨计划
   │                            scratch audio + lav/recorder track → offset + gate
   ├─→ multicam_sync.py         多机位 → 同一参考时间线 / 公共重叠区间 / 对齐预览
   │                            最响音轨 / pairwise / 可选时钟漂移 / source-safe gate
   │
   ├─→ scene_boundaries.py      fixed/adaptive 视觉切点 + 逐切点 evidence
   ├─→ visual_dedupe.py         多来源场景 → 感知哈希重复组 / 保留建议 / review gate
   ├─→ video_understanding.py   抽样帧 + 可选 YOLO 物体检测
   │                            frames / detections / tracks / scene_tags
   ├─→ video_stabilization.py   手持素材 → source-bound 后端/决定/稳定工作副本
   │                            全长原片-vs-稳定版 A/B + confirm gate
   │
   ├─→ highlight_picker.py      长视频精华候选 / brief-query 定向找片段
   │                            输出 score / hook / reason / render_config
   ├─→ audio_boundary_snap.py   已选片段 → 词/句末/静音边界校正
   │                            adjustment delta / blocker / shorts_batch 兼容输出
   ├─→ shorts_batch.py          精华候选 → 多条短视频渲染 job sheet
   │                            per-short render_config / render command / QA command
   │
   ├─→ rough_cut.py             ASR 粗剪 → 去纯口头禅 / 相邻重复句
   │                            输出可审计 cut list，可选单次 concat 渲染
   │
   ├─→ hook_variants.py         transcript/clean_script → 8 个前三秒 hook 角度
   │                            推荐排序 / content guard 风险 / visual cue
   │
   ├─→ rewrite_script.py        LLM 重组为 5 段式 (hook/pain/turn/value[]/cta)
   │     ↑ 8 hook 模板 + 5 CTA 模板 + 3 故事结构
   │
   ├─→ content_guard.py         80+ 条平台雷区 lint (HARD-BLOCK / SOFT-WARN)
   │     极限词 / 导流 / 医美 / 财富诱导 ...
   │
   ├─→ source_receipts.py       事实 claim → URL/截图 source deck
   │                            Markdown/HTML proof deck + publish gate
   │
   ├─→ beat_sync.py             BGM beat-grid → 可审计 program-time 剪辑骨架
   │                            或把已有切点吸附到附近 beat
   ├─→ speed_ramp.py            局部慢动作 / velocity edit → source-bound 计划
   │                            snap/ease/s-curve / 可选插帧 / 音频同步 / 事务式 apply
   │
   ├─→ auto_enrich.py           调度 B-roll / 章节卡 / 贴纸 / 强调点 / BGM 卡点
   │     │ transition / entity match / emphasis cue / silence boundary / beat snap
   │     │
   │     └─→ imagegen_hint.py   抽象概念检测 → gpt-image-2 提示词
   │           ↓                 (Codex 内置 imagegen 工具直接执行；无 API key)
   │           Codex imagegen   注意力机制 / 复利 / 信息茧房 等自动配图
   │
   ├─→ audio_cue_sheet.py       transcript → BGM / SFX 音频设计清单
   │                            本地素材优先 / 生成审批 / pipeline gate
   │
   ├─→ storyboard_plan.py       transcript/clean_script → shot cards
   │                            生成路由 / 连续性锚点 / Dreamina 额度提醒
   │
   ├─→ video_prompt_pack.py     Dreamina/Veo/LTX/Wan/Sora 提示词包
   │                            角色/品牌/style lock / image-to-video / paid approval gate
   ├─→ reference_frame_preflight.py
   │                            首帧/style key 尺寸/方向/画幅/透明背景 gate
   │
   ├─→ generation_task_log.py   异步生成任务台账
   │                            submit_id / 轮询 / 下载 / 本地落盘 gate
   ├─→ generated_clip_review.py 下载后的生成视频片段复核
   │                            contact sheet / 常识物理 / 身份道具 / 裁切与重生 gate
   ├─→ generated_sequence_review.py 已审生成片段 → 相邻边界连续性复核
   │                            尾帧/首帧 / 无声预览 / 身份道具空间动作机位光色 gate
   ├─→ generation_lessons.py    canonical clip review → 人工批准经验库
   │                            provider/model/category scope → 下一次 prompt pack
   │
   ├─→ storyboard_assets.py     shot cards → 素材任务清单 / ready 预检
   │                            imagegen / Dreamina / motion / broll 状态表
   │                            可选接入 media_library.py recommend 排名候选素材
   │
   ├─→ stock_material_plan.py   stock B-roll 搜索规划
   │                            Pexels / Pixabay / Coverr 查询计划 + 素材登记提示
   │
   ├─→ screen_focus.py          录屏点击/热点 → focus_events 聚焦计划
   │                            render_final 自动放大、标记、标签
   ├─→ pip_overlay.py           录屏 + facecam → pip_overlays 小窗计划
   │                            render_final 单次编码合成 PIP camera
   │
   ├─→ jump_cut.py              自适应静音检测 → 20% 删除预算 → cut list → 去停顿成片 + 切点音频 fade
   │     └─→ timeline_view.py   源素材删除段 / 成片输出切点 filmstrip + waveform 人工复核图
   │
   ├─→ audio_transition.py      显式 J-cut/L-cut → source handle / hash / 单次编码 / 1× 试听 gate
   │
   ├─→ edit_revision.py         render_config/enrich_plan 等文本 artifact 可逆修订
   │                            source/dependency hash / 独立审批 / 成组 apply / undo / redo
   │
   ├─→ edit_recipe.py           已审 render_config → 无路径可移植配方 / 精确绑定回放
   │                            typed slots / portable SHA-256 / replay receipt / preflight
   │
   ├─→ edit_preflight.py        render_config/enrich_plan/cut list 渲染前预检
   │                            缺文件、空剪辑、非法时间段、危险参数 gate
   ├─→ platform_safe_area_qa.py 字幕/PIP/CTA/marker 平台 UI 安全区 gate
   │                            JSON/Markdown/SVG 证据 + 多平台 profile
   │
   ├─→ render_final.py          单次编码渲染 + enrich_plan 自动接入
   │     B-roll / 章节卡 / 贴纸 / 生成图 / 点击聚焦 / PIP camera + 可选口播降噪 + Heavy 字幕 + 响度规范化 + BGM ducking
   │     可选 --versioned-output：输出 _V<N>，避免覆盖旧成片
   │
   ├─→ render_qa.py             渲染后黑屏/静帧/静音/尺寸质检 + review packet
   ├─→ shot_color_qa.py         成片镜头亮度/对比/色度/饱和度/broadcast-range + 切点跳变门禁
   ├─→ retention_rhythm_qa.py   成片 hook 活动 / 长镜头 / 注意力空窗 / 节奏门禁
   ├─→ reference_edit_rhythm.py 参考片 vs 成片 hard-cut 结构 / contact sheets / live gate
   ├─→ speech_continuity_qa.py  成片二次 ASR → 切点复读 / 近重复 take / 句内口吃 gate
   ├─→ review_proxy.py          低码率完整审片 MP4 / 可见时间码 / faststart
   ├─→ audio_master_report.py   成片响度 / true peak / LRA / 长静音发布 gate
   │     └─→ timeline_view.py   QA 可疑区间可视化复盘
   │
   ├─→ subtitle_pack.py         SRT / VTT / ASS / JSON 字幕交付包
   │                            支持 render_config 串接、加速倍率、片头 offset 对齐
   ├─→ subtitle_readability_qa.py
   │                            最终字幕 CPS / 时长 / 行长 / 重叠 / 媒体越界发布 gate
   │
   ├─→ import_capcut_subtitles.py
   │                            剪映/CapCut 自动字幕 → transcript / gap cut list
   ├─→ srt_edit_plan.py         SRT + keep/drop 编辑指令 → render_config / cut list
   │
   ├─→ project_resume.py        续跑上下文包 / agent handoff / 可选 CLAUDE.md
   │
   ├─→ review_dashboard.py      静态 HTML/JSON 人工复核面板 / gate review queue
   │
   ├─→ export_edl.py            render_config / cut list → EDL + manifest
   ├─→ export_fcpxml.py         render_config / cut list → FCPXML + manifest
   ├─→ export_otio.py           render_config / cut list → OTIO + manifest
   │                            交给 Premiere / Final Cut Pro / Resolve
   │
   ├─→ multi_export.py          小红书 3:4 / 抖音 9:16 / 视频号 ≤60s
   ├─→ delivery_encode.py       硬大小上限 / 两遍 H.264 / 完整解码验证
   ├─→ generate_caption.py      标题 + 200-500 字正文 + 3-6 tags + 发布时段建议
   ├─→ cover_variants.py        2-4 套封面 A/B 方案 / 小尺寸预览 / 最终选择 gate
   ├─→ approval_receipt.py      已复核交付件 → SHA-256 收据 / stale approval gate
   └─→ publish_package.py       平台视频 / 已选封面 / 字幕 / 文案上传包
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
pytest tests/           # 715 个测试，约 14 秒
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

### 🧠 Semantic Transcript Review — 全篇上下文校稿
[`scripts/semantic_transcript_review.py`](scripts/semantic_transcript_review.py) · [详细文档](docs/prompts/79-semantic-transcript-review.md)

专业术语、人名、同音字或中英混说较多时，先把 transcript 变成带 previous/next context 的 provider-neutral review packet。任何模型都只能填写 response；`audit` 从源 transcript 推导覆盖率，校验 SHA-256、精确字符范围、最小补丁、数字/标点不变、无重叠/越界，并给每条合法建议生成稳定 proposal id。只有独立 choices 文件绑定相同 `source_sha256 + review_id`，逐项 `approve` / `reject` 后，`apply` 才写 reviewed transcript。

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

第一次 `audit --strict` 在存在合法建议时返回 2 是预期 gate：还缺人工 choices。成功 apply 会把同一 audit 更新为 `artifact_type=result`、`summary.blocking=0`，并重新分配改动 segment 的词级时间戳。模型 confidence 和 `reviewer` 都不是身份认证或音频事实证明；仍要把输出交给下一节的同步媒体 HTML 听审。

### 📝 Interactive Transcript Review — 边看视频边校稿
[`scripts/transcript_review.py`](scripts/transcript_review.py) · [详细文档](docs/prompts/36-transcript-review.md)

转写后、清稿和渲染前，用一个无依赖的本地 HTML 页面逐段核对 ASR：点击时间码让视频跳到对应位置，播放时自动高亮当前 segment，文字可直接行内修改，并支持浏览器自动保存、全文查找替换、review 文本复制/保存和 CPS 阅读压力提示。页面不上传 transcript 或媒体，也不直接覆盖原 JSON；保存出的 `transcript_review.txt` 继续交给既有 `apply` 命令，保留原 segment 时间和可审计变更记录。

```bash
python3 scripts/transcript_review.py html \
  --transcript work/transcript.json \
  --video origin/talking.mp4 \
  --corrections work/corrections.json \
  --output work/transcript_review.html \
  --max-cps 20

open work/transcript_review.html

python3 scripts/transcript_review.py apply \
  --transcript work/transcript.json \
  --review work/transcript_review.txt \
  --output work/transcript_reviewed.json
```

浏览器不支持直接写文件时会退回下载 `transcript_review.txt`；把它保存/移动到 `work/` 后再运行 `apply`。CPS 标黄只是预渲染提示，最终输出仍应运行 `subtitle_readability_qa.py --strict`。

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
- **Hook Variants**：`hook_variants.py` 可先生成 8 个前三秒开头角度、风险检查、推荐排序和 visual cue，再把选中的 hook 放进清稿提示或 `clean_script.md`

不绑定任何 LLM 提供商——脚本输出 prompt，你可以交给 GPT-5.6 / Claude 等支持结构化 JSON 的模型，再把返回 JSON 喂回脚本验证 + 物化为 `clean_script.md`。

### 🎬 Auto-Enrich — 自动加 B-roll / 章节卡 / 贴纸 / 强调点 / 卡点
[详细文档](docs/prompts/18-auto-enrich.md)

| 模块 | 触发逻辑 |
|---|---|
| [`auto_broll.py`](scripts/auto_broll.py) | 转折词（但是/然而/关键是/重点来了）/ 实体匹配素材库 / 长镜头守卫 |
| [`auto_chapter_cards.py`](scripts/auto_chapter_cards.py) | `## ` 章节标题 / 静音 ≥1.5s 边界 / Pillow PNG 渲染 |
| [`beat_sync.py`](scripts/beat_sync.py) | BGM → `beat_edit_plan.v1` 时间槽 / Markdown review，或把已有切点做 ±200ms snap；缺 `librosa` 时显式标记固定网格 fallback |
| [`speed_ramp.py`](scripts/speed_ramp.py) | 显式 impact ranges → `speed_ramp_plan.v1` / digest 验证 / 可选插帧 / 音频同步 / 事务式 FFmpeg apply |
| [`video_stabilization.py`](scripts/video_stabilization.py) | 源 hash + exact FFmpeg backend → 稳定工作副本 / 全长 A/B 对照 / 人工确认 gate |
| [`auto_stickers.py`](scripts/auto_stickers.py) | 情绪关键词→emoji 池（excited 🚀✨🔥 / doubt 🤔 / data 📈 等） |
| [`auto_emphasis.py`](scripts/auto_emphasis.py) | 问句 / 数字 claim / 转折 / 结论 / 风险提醒 / 停顿恢复 → `emphasis_cues[]` |
| [`auto_enrich.py`](scripts/auto_enrich.py) | 编排上面模块，输出综合 plan JSON（含 emphasis 和 imagegen cues） |

`render_final.py --enrich-plan work/enrich_plan.json` 会把 plan 里的 B-roll、章节卡、贴纸、强调点和已生成图片 cue 自动接回单次渲染；`emphasis_cues[]` 会转成 timed badge 和 marker-free center push-in。`--enrich-plan` 可重复传入，用来叠加 `screen_focus_plan.json` 这类独立计划。没有实际文件的 imagegen cue 会保留为提示，不会阻塞导出。

音乐视频、产品 montage 或明确要求“按 BGM 卡点”时，可先让 `beat_sync.py` 从音乐直接生成剪辑骨架：

```bash
python3 scripts/beat_sync.py \
  --bgm origin/bgm.mp3 \
  --generate-plan \
  --duration 30 \
  --beats-per-cut 4 \
  --min-segment 0.75 \
  --max-segment 3 \
  --output work/beat_edit_plan.json \
  --markdown work/beat_edit_plan.md
```

默认每 4 拍提出一个 program-time 切点，最短/最长镜头守卫会改选附近 beat；找不到合适 beat 才写入 `duration_guard`。输出只定义 `cut_times[]`、`segments[]` 和逐切点 evidence，不选择素材、不渲染、不修改源文件。`detection.method=fallback_grid` 时状态为 `review`，必须实际听音乐复核；确认后再把素材映射进 `render_config`、EDL 或 OTIO。已有 cut times 继续使用 `--cuts ... --window 0.2`。

### 🧭 Video Stabilization — source-bound 手持防抖
[`scripts/video_stabilization.py`](scripts/video_stabilization.py) · [详细文档](docs/prompts/84-video-stabilization.md)

手机、运动相机或手持相机出现不想要的高频抖动时，先检查本机后端并把决定写成计划：

```bash
python3 scripts/video_stabilization.py doctor
python3 scripts/video_stabilization.py plan origin/handheld.mp4 \
  --decision stabilize \
  --reviewed-by "editor" \
  --note "固定机位访谈中的高频手抖，不是有意摇摄" \
  --output work/video_stabilization_plan.json \
  --markdown work/video_stabilization_plan.md
python3 scripts/video_stabilization.py apply work/video_stabilization_plan.json \
  --output work/handheld-stabilized.mp4 \
  --comparison verify/handheld-stabilization-compare.mp4 \
  --markdown work/video_stabilization_plan.md
```

`--backend auto` 创建计划时优先两遍 `vidstabdetect + vidstabtransform`；本机缺少它们时才选择 FFmpeg 内置单遍 `deshake`，并把 fallback warning 永久保存在 artifact 中。apply 不会临场换算法，原片永不覆盖，输出还会验证 duration、尺寸和音频存在性。

用 1× 看完整左原片 / 右稳定版，检查人物、直线、画面四角、镜像边缘和有意 pan；可接受后运行 `confirm ... --reviewed-by "editor" --note "完整 A/B 已看..."`。确认前 `pipeline_manifest.py` 会阻塞，确认后仍实时验证源片、稳定版和 comparison 的 SHA-256。它不能修复滚动快门、运动模糊或失焦；稳定版只作为下游 working copy。

### ⚡ Speed Ramp — source-bound 局部慢动作 / velocity edit
[`scripts/speed_ramp.py`](scripts/speed_ramp.py) · [详细文档](docs/prompts/83-speed-ramp.md)

动作、产品 reveal、游戏或 montage 需要突出少量 impact moment 时，先用逐帧播放器 / `timeline_view.py` 找 source-time 锚点，再创建计划：

```bash
python3 scripts/speed_ramp.py plan origin/action.mp4 \
  --ramp 4.6,5.0,1,0.25,s_curve \
  --hold 5.0,5.8,0.25 \
  --ramp 5.8,6.2,0.25,1,ease \
  --interpolate-fps 120 \
  --output work/speed_ramp_plan.json \
  --markdown work/speed_ramp_plan.md

python3 scripts/speed_ramp.py verify work/speed_ramp_plan.json --strict
python3 scripts/speed_ramp.py apply work/speed_ramp_plan.json \
  --output work/action-speed-ramped.mp4 \
  --receipt work/speed_ramp_apply.json
```

计划把 `linear/ease/s_curve/snap` ramp 与 constant hold 编译成连续 source/output pieces，绑定源文件 SHA-256、fps、duration 和 canonical plan id；完整 coverage、速度范围、`source_duration / speed` 和 review contract 都会现场验证。低帧率极慢段会给出 native unique-fps warning；`--interpolate-fps` 使用 FFmpeg motion interpolation，不冒充 AI 生成补帧，也可能产生肢体 / 边缘伪影。apply 先写同目录临时 MP4，成功后才替换目标；默认拒绝覆盖、symlink 和原片自覆盖。

必须用 1×、带声音播放最终文件，检查 impact frame、曲线手感、插值伪影和极慢音频。局部变速会改变下游时间线：如果已有字幕、章节、cue 或 approval receipt，必须重新生成 / 审批；`pipeline_manifest.py --require speed_ramp_plan --strict` 可把 plan 设为显式 gate。

### 🎧 J-cut / L-cut — source-bound 声画错位转场
[`scripts/audio_transition.py`](scripts/audio_transition.py) · [详细文档](docs/prompts/86-audio-transition.md)

访谈、场景转换或叙事片需要“下一镜声音先进入”或“画面先切、上一镜声音继续”时，先完成 clip 选择，再逐边界试听并显式建计划：

```bash
python3 scripts/audio_transition.py plan work/render_config.json \
  --transition 1,j_cut,0.40 \
  --transition 3,l_cut,0.55 \
  --output work/audio_transition_plan.json \
  --markdown work/audio_transition_plan.md

python3 scripts/audio_transition.py apply work/audio_transition_plan.json \
  --output output/master.mp4 \
  --receipt work/audio_transition_apply.json

python3 scripts/audio_transition.py verify work/audio_transition_plan.json \
  --receipt work/audio_transition_apply.json \
  --strict
```

J-cut 读取下一 clip 画面入点之前的真实源音频，到视觉切点重新与画面对齐；L-cut 读取上一 clip 出点之后的真实源音频，并让下一 clip 的主音频从 overlap 之后恢复同步。没有足够 handle、L-cut 会意外吞掉下一句开头、config/transcript/source hash 漂移或 compiled timing 被修改都会阻塞。计划存在时，`pipeline_manifest.py` 会 live verify；`edit_brief_plan.py` 也能从“声音先行 / J-cut / 画面先切声音后走”自动路由。

`apply` 通过 `render_final.py --audio-transition-plan` 把画面、错位主音频、字幕、overlay、BGM 和响度链留在同一次 FFmpeg 编码里，并写 source-bound receipt。计划、Markdown、成片和 receipt 默认都拒绝覆盖；有外部 enrich/调色/降噪等 CLI 参数时，应在原 `render_final.py` 命令上追加 plan，而不是丢掉参数改用 wrapper。机器验证不能判断交叠对白是否正确；每个改变边界必须以 1× 在耳机和手机扬声器上试听，确认没有吞字、复读、双人声、click、泵动或环境底噪跳变。

### 🧱 Project Bootstrap — 项目启动与素材导入
[`scripts/project_bootstrap.py`](scripts/project_bootstrap.py) · [详细文档](docs/prompts/61-project-bootstrap.md)

借鉴 agent-native 视频工具的 folder-first / safe working copy / project memory 思路，但保持本项目本地 artifact-first：把原始素材目录整理成 `origin/`、`work/`、`output/`、`verify/`、`edit/`，并写出 `source_inventory.json`、`source_inventory.md`、`project.md` 和 `next_steps.md`。

常用：
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

默认 `--mode copy`，会按路径和扩展名把素材归入 `origin/raw`、`origin/broll`、`origin/audio`、`origin/bgm`、`origin/images`、`origin/assets` 或 `origin/sidecars`；同名文件自动加后缀，不覆盖已有文件。`pipeline_manifest.py` 会发现 `source_inventory.json`，需要把素材导入作为 analysis gate 时可加 `--require source_inventory`。脚本不转码、不渲染、不上传，也不调用 LLM 或生成服务。

### 🧭 Edit Brief Plan — 自然语言剪辑需求路由
[`scripts/edit_brief_plan.py`](scripts/edit_brief_plan.py) · [详细文档](docs/prompts/64-edit-brief-plan.md)

借鉴 GitHub 上视频 skill / MCP 项目的“用户自然语言 → agent 选工具、排顺序、留 checkpoint”的优点，但保持本项目轻量：只做本地确定性路由，输出 `edit_brief_plan.v1` JSON 和 Markdown runbook，不调用 LLM、不渲染、不上传、不提交付费生成任务。

常用：
```bash
python3 scripts/edit_brief_plan.py \
  --brief "把 origin/interview.mp4 剪成三条抖音短视频，去停顿，加B-roll、BGM和字幕，最后生成发布包" \
  --project-dir . \
  --output work/edit_brief_plan.json \
  --markdown work/edit_brief_plan.md \
  --strict
```

它会识别 `origin/interview.mp4` 这类源素材路径、目标平台、手持防抖、目标脚本对齐、多 take、长视频拆条、批量短视频、字幕、B-roll、BGM、去停顿、J-cut/L-cut、生成素材、PIP、调色、QA、发布包等信号，并把它们映射到已有脚本，例如 `video_stabilization.py`、`script_alignment.py`、`highlight_picker.py`、`shorts_batch.py`、`jump_cut.py`、`audio_transition.py`、`auto_enrich.py`、`render_final.py`、`render_qa.py` 和 `publish_package.py`。`pipeline_manifest.py` 会发现 `edit_brief_plan.json`；当 `summary.blocking > 0`（例如 brief 为空或显式 source 缺失）时会作为 blocker，也可以用 `--require edit_brief_plan` 把需求路由作为 analysis gate。

### 👁️ Video Understanding — 抽样帧 + 可选 YOLO 检测
[`scripts/video_understanding.py`](scripts/video_understanding.py) · [详细文档](docs/prompts/47-video-understanding.md)

为口播、访谈、产品演示和户外素材补上结构化视觉线索：先按时间和场景边界抽帧；需要时再用 Ultralytics YOLO 识别人、手机、电脑、屏幕、车辆、杯子等常见对象；最后输出可审计的 `video_understanding.v1`。

常用：
```bash
python3 scripts/video_understanding.py origin/talk.mp4 \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md

pip install ultralytics
python3 scripts/video_understanding.py origin/talk.mp4 \
  --scene-boundaries work/scene_boundaries.json \
  --detector yolo \
  --model yolo11n.pt \
  --output work/video_understanding.json \
  --markdown work/video_understanding.md \
  --strict
```

输出可以直接交给 `smart_reframe.py --detections` 做主体感知裁切，也可以交给 `privacy_redact.py --detections` 做隐私遮挡计划。`ultralytics` 不是必装依赖；没有 detector 时仍然能生成抽样帧和 review shell。

### 🧩 Takes Pack — 多 take phrase-level 阅读视图
[`scripts/takes_pack.py`](scripts/takes_pack.py) · [详细文档](docs/prompts/60-takes-pack.md)

借鉴 `browser-use/video-use` 把 phrase-level transcript 作为主要阅读视图、并保留音频事件的做法，但保持本项目 artifact-first：只读本地 `transcript.json`，输出 `takes_packed.md` 和可选 `takes_pack.json`，不转写、不渲染、不调用 LLM。除现有 `segments[].words[]` 外，也可直接读取 ElevenLabs Scribe 风格的顶层 `words[]`；`speaker_id` 会参与分段，`audio_event` 会作为带时间码的笑声、掌声、叹气或音乐剪辑节拍保留。

常用：
```bash
python3 scripts/takes_pack.py \
  --transcript take1=work/take1_transcript.json \
  --transcript take2=work/take2_transcript.json \
  --output work/takes_packed.md \
  --json work/takes_pack.json \
  --break-gap 0.5
```

`takes_packed.md` 按 source 分组列出 `take1-003` 这类 phrase id、源时间码、speaker、segment ids、audio events 和压缩文本，适合先比较多个 take 的表达质量，也能避免在笑点、掌声或反应声中间误切。`takes_pack.json` 还会给每个事件保留 label/start/end；确认后的 time range 可继续交给 `highlight_picker.py`、`srt_edit_plan.py`、`render_config.json` 或 EDL/FCPXML/OTIO。`pipeline_manifest.py` 会发现 `takes_pack.json`，但默认不把它作为 blocker；需要强制多 take review 时可加 `--require takes_pack`。

### 🧭 Target Script Alignment — 按确认稿从多 take 装配原话
[`scripts/script_alignment.py`](scripts/script_alignment.py) · [详细文档](docs/prompts/78-script-alignment.md)

客户、编导或 Agent 已经确认成片稿时，不必再靠人工逐条找时间码。`script_alignment.py` 把目标稿按行/句拆成 spoken units，在一份或多份 reviewed transcript 中搜索词级/segment 边界候选，输出稳定 candidate id、原话、source time、透明 score breakdown 和前三名备选；最终 `render_config.json` 按目标稿顺序排列，即使素材原始录制顺序不同也能重组。

第一次运行：

```bash
python3 scripts/script_alignment.py \
  --target-script work/target_script.md \
  --transcript take-a=work/takes/take-a_transcript_reviewed.json \
  --transcript take-b=work/takes/take-b_transcript_reviewed.json \
  --media take-a=origin/take-a.mp4 \
  --media take-b=origin/take-b.mp4 \
  --output work/script_alignment.json \
  --markdown work/script_alignment.md \
  --render-config work/render_config.json \
  --clean-script work/clean_script.md \
  --strict
```

默认 65 分以下不采用、65-82 分要求 review；即使分数更高，只要前两名相差不超过 3 分，也会以 `ambiguous_match` 阻塞，避免同文案多个 take 被静默选错。人工看/听候选后，把 `target-001 -> candidate id` 写进 `--choices work/script_alignment_choices.json` 再跑一次。显式 choice 能解决低分/同分歧义，但不会绕过源素材缺失或时间段重复占用。该脚本不调用 LLM、不改源文件、不判断表演和画面质量；大幅同义改写仍需要人工语义判断或补录。`pipeline_manifest.py` 会发现此报告，只要 `summary.blocking > 0` 就阻塞，也支持 `--require script_alignment`。

### 🎞️ Adaptive Scene Boundaries — 运动镜头自适应切点
[`scripts/scene_boundaries.py`](scripts/scene_boundaries.py) · [详细文档](docs/prompts/32-scene-boundaries.md)

固定 scene score 阈值对静态访谈很直接，但持续摇镜、游戏画面、手持走拍或高运动 B-roll 可能整段都高于阈值。`--method adaptive` 会读取每帧 FFmpeg scene score，把目标帧与前后邻域均值比较，同时保留绝对 `--min-scene-score` 门槛；真正的局部峰值才进入 `boundaries[]`，每个保留切点还会在 `boundary_evidence[]` 记录 score、adaptive ratio 和邻域均值。

```bash
python3 scripts/scene_boundaries.py origin/long-talk.mp4 \
  --method adaptive \
  --adaptive-threshold 3.0 \
  --min-scene-score 0.15 \
  --min-scene-duration 1.0 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md
```

先看 Markdown 的 cut evidence，再把 JSON 交给 `highlight_picker.py --scene-boundaries`、`video_understanding.py --scene-boundaries` 或 `retention_rhythm_qa.py --scene-boundaries`。固定机位素材、旧项目复现或已经调好阈值的流程继续用 `--method fixed --threshold 0.35`，默认行为保持兼容。

### 🔎 Highlight Picker — 长视频精华候选 / brief 定向找片段
[`scripts/highlight_picker.py`](scripts/highlight_picker.py) · [详细文档](docs/prompts/31-highlight-picker.md)

长视频拆短视频时，先从 `transcript.json` 生成可发布候选，输出透明 `score`、`signals`、`warnings`、`hook_text`、`reason` 和可选 `render_config`。默认模式会找 hook/value/duration 表现好的短视频片段；如果用户已经知道要找什么，加 `--brief` 或 `--query` 做 prompt-based clipping。

常用：
```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --brief "产品发布 用户反应 价格对比" \
  --scene-boundaries work/scene_boundaries.json \
  --video origin/long-talk.mp4 \
  --output work/brief_highlights.json \
  --markdown work/brief_highlights.md \
  --render-config work/brief_render_config.json \
  --platform douyin \
  --num-clips 3 \
  --strict
```

`--brief` 会把自然语言意图拆成英文关键词和中文短语片段，写入每条 candidate 的 `brief_match.score` 与 `matched_terms`，但仍保留原来的自包含结尾、弱 hook、时长偏离等 warning。适合“找产品 reveal / 用户强反应 / 教程关键步骤 / 失败教训”这类定向剪片。

### 🎧 Audio Boundary Snap — 词/句末/静音剪辑边界校正
[`scripts/audio_boundary_snap.py`](scripts/audio_boundary_snap.py) · [详细文档](docs/prompts/65-audio-boundary-snap.md)

在 `highlight_picker.py` 已经选好内容以后，用 transcript 词级时间戳把每条 start/end 对齐到完整词，必要时把结尾扩到附近句号、问号或感叹号；如果 transcript 已有 `silences[]`，或提供 `--media` 让 FFmpeg 跑 `silencedetect`，还会优先把切点放到相邻静音区中点。每条调整都会保留原始时间、前后 delta、首尾词、reason、warning 和 blocker，不渲染也不改源文件。

常用：
```bash
python3 scripts/audio_boundary_snap.py \
  --candidates work/highlight_candidates.json \
  --transcript work/long_transcript.json \
  --media origin/long-talk.mp4 \
  --output work/audio_boundary_plan.json \
  --markdown work/audio_boundary_plan.md \
  --strict

python3 scripts/shorts_batch.py \
  --highlights work/audio_boundary_plan.json \
  --video origin/long-talk.mp4 \
  --output work/shorts_batch.json \
  --strict
```

支持 Whisper `segments[].words[]` 和 ElevenLabs Scribe 风格顶层 `words[]`，其中 spacing/audio event 不会当成词。没有词级时间戳、候选时间非法、源媒体缺失或安全边界超出平台时长时，`summary.blocking` 会非零；`pipeline_manifest.py --require audio_boundary_plan --strict` 可把它设为显式 gate。

### 🎞️ Shorts Batch — 多条精华短视频渲染 job sheet
[`scripts/shorts_batch.py`](scripts/shorts_batch.py) · [详细文档](docs/prompts/63-shorts-batch.md)

借鉴 AI shorts 类项目“长视频一次上传，产出多条可追踪短视频”的做法，但保持本项目本地优先：读取 `highlight_picker.py` 的 `highlight_candidates.v1` 或 `audio_boundary_snap.py` 的 `audio_boundary_plan.v1`，为每条 selected highlight 写一份独立 `render_config`，并输出 `shorts_batch.v1` JSON、Markdown job sheet、`render_final.py` 命令和 `render_qa.py` 命令。脚本不渲染、不上传、不调用 LLM。

常用：
```bash
python3 scripts/shorts_batch.py \
  --highlights work/highlight_candidates.json \
  --video origin/long-talk.mp4 \
  --output work/shorts_batch.json \
  --markdown work/shorts_batch.md \
  --render-config-dir work/shorts_render_configs \
  --output-dir output/shorts \
  --qa-dir verify/shorts \
  --basename day63 \
  --platform douyin \
  --strict
```

输出后先打开 `work/shorts_batch.md` 看每条 job 的 hook、ending 和 warnings；确认后逐条运行表内 `render_shell`，再运行 `qa_shell` 生成 QA JSON/复核包。`pipeline_manifest.py` 会发现 `shorts_batch.json`；当 batch 自身有 `summary.blocking > 0`（例如源视频缺失）时会作为可见 blocker。

### 🎨 Color Grade — 可审计调色计划
[`scripts/color_grade.py`](scripts/color_grade.py) · [详细文档](docs/prompts/48-color-grade.md)

借鉴 agent 视频编辑工具对 color grading / filters 的重视，但保持本项目的单次编码原则：先生成 bounded `color_grade.v1` 调色计划和 Markdown review，最终由 `render_final.py --color-grade` 在字幕前接入同一条 FFmpeg filter graph。

常用：
```bash
python3 scripts/color_grade.py \
  --preset screen \
  --output work/color_grade.json \
  --markdown work/color_grade.md

python3 scripts/render_final.py \
  --config work/render_config.json \
  --color-grade work/color_grade.json \
  --output output/tutorial_master.mp4
```

内置 `natural`、`warm`、`cool`、`punchy`、`soft`、`cinematic`、`screen` 七个 preset；自定义 `brightness`、`contrast`、`saturation`、`gamma`、`temperature`、`tint`、`sharpness` 会被限制在保守范围内，`--strict` 在参数被 clamp 时返回 2。若主片已经渲染完，也可以用 `color_grade.py --input output/master.mp4 --render-output output/master_grade.mp4` 做单独复版；日常推荐仍是在 `render_final.py` 里一次编码完成。

### 🔬 Shot Color QA — 成片镜头色彩 / 曝光门禁
[`scripts/shot_color_qa.py`](scripts/shot_color_qa.py) · [详细文档](docs/prompts/81-shot-color-qa.md)

`color_grade.py` 负责渲染前的 bounded look，`shot_color_qa.py` 则在**最终编码文件**上按镜头复查实际结果。它用 FFmpeg `signalstats` 每秒默认抽 2 帧，对每个镜头聚合 `YLOW/YAVG/YHIGH`、U/V、`SATAVG` 和 `BRNG` 中位数，列出持续过暗/过亮、低对比、高饱和、broadcast-range 越界，以及相邻镜头的亮度/色度跳变。

```bash
python3 scripts/shot_color_qa.py output/day81_master.mp4 \
  --output output/verify/day81_shot_color_qa.json \
  --markdown output/verify/day81_shot_color_qa.md \
  --strict

# 已有 scene_boundaries.v1 时可复用同一场景时间轴
python3 scripts/shot_color_qa.py output/day81_master.mp4 \
  --scene-boundaries work/scene_boundaries.json \
  --output output/verify/day81_shot_color_qa.json \
  --markdown output/verify/day81_shot_color_qa.md \
  --strict
```

视觉跳变默认只 WARN，因为地点、日夜、图形/实拍或刻意 look 的变化可能完全合理；非 full-range 输出的持续 `BRNG` 越界和未覆盖镜头默认 BLOCK。需要把极暗/极亮或跳变作为当前项目的强人工门禁，可加 `--fail-on-extremes` / `--fail-on-jumps`。Markdown 会为可疑切点生成 `timeline_view.py` 命令；确认后应回到源 timeline / 调色计划重渲染，避免对 master 反复压缩。`pipeline_manifest.py --require shot_color_qa --strict` 可强制发布前存在报告；这是 SDR 社媒输出的轻量统计，不是校准 scopes、HDR proof、白平衡/肤色判断或审美评分。

### 🎧 Audio Cue Sheet — BGM / SFX 音频设计清单
[`scripts/audio_cue_sheet.py`](scripts/audio_cue_sheet.py) · [详细文档](docs/prompts/43-audio-cue-sheet.md)

借鉴 OpenMontage / vibeframe / Claude Code Video Toolkit 这类 agentic video 项目对 narration、music、SFX、成本和 review report 的一等公民设计，但保持本项目轻量：只读 transcript 和本地素材目录，不生成音乐、不提交 TTS、不消耗 provider credits。

常用：
```bash
python3 scripts/audio_cue_sheet.py \
  --transcript work/transcript.json \
  --asset-root media/bgm \
  --asset-root media/sfx \
  --output work/audio_cue_sheet.json \
  --markdown work/audio_cue_sheet.md

python3 scripts/audio_cue_sheet.py \
  --transcript work/transcript.json \
  --asset-root media \
  --require-local-music \
  --require-local-sfx \
  --output work/audio_cue_sheet.json \
  --markdown work/audio_cue_sheet.md \
  --strict
```

输出 `audio_cue_sheet.v1`：`voice_track` 记录主口播响度目标，`music[]` 给出全片 BGM mood / BPM / prompt / 本地候选或生成需求，`sfx[]` 根据“但是 / 重点 / 完成 / 风险”等触发词排 whoosh、ping、chime、warning tick。`--strict` 会在要求本地 BGM/SFX 但素材缺失时返回 2；`pipeline_manifest.py` 会自动识别 `audio_cue_sheet.json` 并把 `summary.blocking > 0` 列为 blocking gate。

选好 BGM 后，可让实际渲染兑现 cue sheet 里的 ducking 要求：

```json
{
  "bgm": "media/bgm/tech-pulse.mp3",
  "bgm_volume": 0.15,
  "bgm_fade_out": 3.0,
  "bgm_ducking": true
}
```

也可给 `render_final.py` 加 `--bgm-ducking` 临时启用。它会用最终旁白轨触发 FFmpeg `sidechaincompress`，在说话时动态压低 BGM，在封面、停顿和片尾无旁白时恢复；默认 threshold `0.03`、ratio `8`、attack `20ms`、release `500ms`。旧配置默认关闭以保持兼容，配置已开启时可用 `--no-bgm-ducking` 覆盖。详细参数与试听检查见 [背景音乐、旁白 Ducking 和片尾](docs/prompts/09-bgm-endcard.md)。

### 🎞️ Storyboard Plan — 分镜与生成路由
[`scripts/storyboard_plan.py`](scripts/storyboard_plan.py) · [`scripts/video_prompt_pack.py`](scripts/video_prompt_pack.py) · [`scripts/storyboard_assets.py`](scripts/storyboard_assets.py) · [分镜文档](docs/prompts/24-storyboard-plan.md) · [视频提示词包文档](docs/prompts/45-video-prompt-pack.md) · [素材清单文档](docs/prompts/25-storyboard-assets.md)

借鉴 GitHub 上视频生成类项目的 storyboard / shot continuity / provider routing 思路，但保持本项目的轻量原则：脚本只做本地规划，不提交任何付费生成任务。

| 输出 | 说明 |
|---|---|
| `storyboard_plan.json` | 每个 shot 的时间码、source segments、section、narration、keywords、visual first/motion/last frame |
| `generation_route` | `codex_imagegen` / `dreamina_video` / `remotion_hyperframes` / `media_library_broll` + fallback + why |
| `continuity.anchors` | 系列色彩、比例、字幕安全区、上一镜头引用、关键词线索 |
| `storyboard_plan.md` | 适合人工 review 的 shot cards，含 prompt 和检查项 |
| `video_prompt_pack.json` | 每个 shot 的 Dreamina/即梦 Seedance、Veo、LTX、Wan、Sora 提示词、参考图路径、负面提示词和审批状态 |
| `reference_frame_preflight.json` | image-to-video 首帧和共享 style key 的存在性、解码、尺寸、方向、画幅、透明背景 gate |
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
```

路由规则：抽象概念优先 `codex_imagegen`；数字/指标优先 `remotion_hyperframes`；动作/场景变化推荐 `dreamina_video` 但只标记为需确认，因为 Dreamina/即梦生成可能消耗 credits；其他先走本地素材库 B-roll。传 `--media-library <project_dir>` 时，`storyboard_assets.py` 会从 `media_index.json` / `media_index.db` 里按标签、文件名、时长和画幅推荐候选，并在 Markdown 表里显示分数。`storyboard_assets.py --strict` 会在素材未 ready 时返回退出码 2，适合渲染前拦截。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

### 🎥 Video Prompt Pack — 视频生成提示词包
[`scripts/video_prompt_pack.py`](scripts/video_prompt_pack.py) · [详细文档](docs/prompts/45-video-prompt-pack.md)

借鉴 GitHub 上视频生成 skill 对多模型提示词、角色参考 sheet、image-to-video 和 provider 成本审批的做法，但保持本项目 artifact-first：只把 `storyboard_plan.json` 转成 `video_prompt_pack.v1` 和 Markdown review，不提交 Dreamina/Veo/LTX/Wan/Sora 任务，不消耗 credits。

常用：
```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --character "same Chinese founder-host, navy jacket" \
  --brand-anchor "palette=charcoal,white,signal yellow" \
  --style-reference work/imagegen/style-key.png \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md \
  --strict

python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --provider dreamina_seedance \
  --animate-stills \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md
```

输出 `global.character_sheet_prompt`、`global.style_reference`、`items[].prompt`、`items[].negative_prompt`、`items[].reference.expected_path/resolved_path`、`items[].approval_status` 和 `summary.blocking`。`--style-reference` 会把同一 style key 绑定到每个生成 shot，并给 provider prompt 追加统一 `STYLE LOCK`。`--strict` 会在 generated-video provider 还没有 `--approved` 时返回 2；`pipeline_manifest.py` 会自动识别 `video_prompt_pack.json` 并把未清零的 `summary.blocking` 列为 blocking gate。Dreamina/即梦、Veo、LTX、Wan、Sora 等视频生成可能消耗 credits，提交前先确认并保持小批量。

### 🖼️ Reference Frame Preflight — 生成参考帧预检
[`scripts/reference_frame_preflight.py`](scripts/reference_frame_preflight.py) · [详细文档](docs/prompts/71-reference-frame-preflight.md)

借鉴 HeyGen 的 Frame Check、Higgsfield 的共享 style key 和 Seedance 的多素材角色标注：paid provider 提交前，先检查 image-to-video 首帧与共享 style reference 是否真的可用。

```bash
python3 scripts/reference_frame_preflight.py \
  --prompt-pack work/video_prompt_pack.json \
  --output work/reference_frame_preflight.json \
  --markdown work/reference_frame_preflight.md \
  --require-style-reference \
  --strict
```

脚本检查路径存在性、可解码性、尺寸、横竖方向、目标画幅、短边分辨率和透明背景。缺文件、损坏文件、横竖方向冲突或严重画幅冲突会写入 `summary.blocking` 并让 `--strict` 返回 2；低分辨率和透明背景写 warning 与修正建议。默认 20% 画幅容差会接受常见 1024×1536 → 9:16 参考工作流；需要临时改路径时可重复传 `--reference shot_001=/path/to/approved.png`。产物会被 `pipeline_manifest.py` 自动纳入 gate。

### 🧾 Generation Task Log — 异步生成任务台账
[`scripts/generation_task_log.py`](scripts/generation_task_log.py) · [详细文档](docs/prompts/46-generation-task-log.md)

借鉴 PixVerse skills 的 task polling / asset download 能力和 Claude Code Video Toolkit 的跨会话项目状态管理，但保持本项目本地化：只记录异步生成任务状态，不提交 paid jobs。

常用：
```bash
python3 scripts/generation_task_log.py import-provider-decision \
  --provider-decision work/provider_decision.json \
  --log work/generation_tasks.json \
  --markdown work/generation_tasks.md \
  --strict

python3 scripts/generation_task_log.py add \
  --log work/generation_tasks.json \
  --provider dreamina \
  --task-id "<submit_id>" \
  --shot-id shot_002 \
  --expected-path work/generated_video/shot_002.mp4 \
  --status submitted \
  --markdown work/generation_tasks.md \
  --strict

python3 scripts/generation_task_log.py update \
  --log work/generation_tasks.json \
  --provider dreamina \
  --task-id "<submit_id>" \
  --status downloaded \
  --asset-path work/generated_video/shot_002.mp4 \
  --markdown work/generation_tasks.md
```

输出 `generation_task_log.v1`：`tasks[].provider_task_id` 保存 Dreamina `submit_id` / provider task id，`poll_command` / `download_command` 保存下一步命令，`readiness` 区分 `needs_approval` / `pending` / `processing` / `needs_download` / `missing_asset` / `failed` / `ready`。`--strict` 会在 `summary.blocking > 0` 时返回 2；`pipeline_manifest.py` 会自动识别 `generation_tasks.json` 并把未清零的异步任务列为 blocking gate。

### 🎬 Generated Clip Review — 生成视频片段复核
[`scripts/generated_clip_review.py`](scripts/generated_clip_review.py) · [详细文档](docs/prompts/89-generated-clip-review.md)

生成视频下载成功只说明 provider 任务完成，不代表片段可进入时间线。这个本地 gate 把每条生成 clip 的文件 hash、媒体契约和有界 contact sheet 绑定到复核 request，再审计 reviewer 对常识/物理、身份服装、动作终态、镜头、道具/画面完整性和 look 的评分、hard fail、可用裁切范围与重生建议。

```bash
# 1. 从已刷新为 ready 的 storyboard_assets 提取生成视频并制作 contact sheets
python3 scripts/generated_clip_review.py prepare \
  --project-dir . \
  --asset-manifest work/storyboard_assets.json \
  --contact-sheet-dir verify/generated_clips \
  --output work/generated_clip_review_request.json \
  --markdown work/generated_clip_review_request.md \
  --response-template work/generated_clip_review_response.json

# 2. 完整看过 1× 带声、0.25×、静音画面和 audio-only 后填写 response，再审计
python3 scripts/generated_clip_review.py audit \
  --request work/generated_clip_review_request.json \
  --response work/generated_clip_review_response.json \
  --output work/generated_clip_review.json \
  --markdown work/generated_clip_review.md \
  --strict

# 3. 组装/发布前重新核对 live clips、contact sheets 和 canonical audit
python3 scripts/generated_clip_review.py verify \
  --report work/generated_clip_review.json \
  --strict
```

`pass` 要求加权分至少 80、故事清晰、没有 hard fail 且无需删段；`pass_with_edits` 要求至少 65，并让 `keep_ranges` / `remove_ranges` 无缝覆盖整条片段；身份断裂、错误/缺失动作、肢体或物理失败、多余主体、关键道具消失、生成文字/水印、连续性矛盾、音画矛盾和 explicit must-avoid 都会越过高分直接 `fail`，要求 `regenerate=true + prompt_fix`。clip/contact sheet 漂移、漏审、区间重叠/缺口和报告派生状态篡改都会 fail closed；`pipeline_manifest.py --require generated_clip_review --strict` 可设为发布门禁。reviewer label 不是身份认证或数字签名，contact sheet 也不能替代完整播放。

### 🎞️ Generated Sequence Review — 生成视频跨镜头连续性复核
[`scripts/generated_sequence_review.py`](scripts/generated_sequence_review.py) · [详细文档](docs/prompts/91-generated-sequence-review.md)

逐片 `pass` 不等于组装后连续。这个第二层 gate 读取 live-verifiable `generated_clip_review.json`，按 storyboard 顺序为每个相邻 clip 提取已批准范围的真实尾帧/首帧、并排 JPEG 和“上一镜尾部 + 下一镜头部”的无声 1× MP4；再把 identity/wardrobe、prop state、spatial orientation、action end state、camera framing、lighting/palette 六项决定绑定到 clip、上游 review、storyboard 和 evidence bytes。

```bash
# 1. 逐片 review 已通过后，生成相邻边界证据和 response 模板
python3 scripts/generated_sequence_review.py prepare \
  --project-dir . \
  --clip-review work/generated_clip_review.json \
  --storyboard-plan work/storyboard_plan.json \
  --evidence-dir verify/generated_sequence \
  --output work/generated_sequence_review_request.json \
  --markdown work/generated_sequence_review_request.md \
  --response-template work/generated_sequence_review_response.json

# 2. 看完每个 preview + comparison 后填写 response，再审计
python3 scripts/generated_sequence_review.py audit \
  --request work/generated_sequence_review_request.json \
  --response work/generated_sequence_review_response.json \
  --output work/generated_sequence_review.json \
  --markdown work/generated_sequence_review.md \
  --strict

# 3. 组装/发布前重算上游 review、clip、storyboard、evidence 和 canonical audit
python3 scripts/generated_sequence_review.py verify \
  --report work/generated_sequence_review.json \
  --strict
```

每项只允许 `match` / `intentional_change` / `mismatch` / `not_applicable`；至少两项必须真实评估。`mismatch` 必须 `fail`，同时给 failure code 和可执行 `repair_action`，不能用高分或一句“转场可接受”掩盖漂移；storyboard 明确设计的换场/换装/景别变化可以 `intentional_change` 通过，但保留 warning。`pass_with_edits` 上游片段只用首个/最后一个批准 `keep_range` 建边界，不会重新引入拒绝区间。任何 clip、上游 report、storyboard、frame/comparison/preview 漂移都会 fail closed；`pipeline_manifest.py --require generated_sequence_review --strict` 可设为组装/发布门禁。预览无声，不能替代最终 master 的完整声画复核；reviewer label 和 SHA-256 也不是身份认证或签名。

### 🧠 Generation Lessons — 生成视频复核经验闭环
[`scripts/generation_lessons.py`](scripts/generation_lessons.py) · [详细文档](docs/prompts/90-generation-lessons.md)

同类生成技能常把每次 QC 经验追加到一个自由文本文件，再要求下一次提示词读取。本项目把这条闭环收紧为显式 approval 和 scope：只有 canonical `generated_clip_review.json` 中结构有效的 clip 才能提供 evidence；operator 需另写一条可泛化 `lesson` 并给出 `approved_by` 标签。entry 会绑定 report/request/clip/contact-sheet 摘要、verdict、score、hard-fail codes 和原 `prompt_fix`，但 prompt pack 只自动复用人工批准的 `lesson`。

```bash
# 1. 从一条已审 clip 沉淀 provider/model scoped 经验
python3 scripts/generation_lessons.py add \
  --library work/generation_lessons.json \
  --review work/generated_clip_review.json \
  --clip-id shot_002 \
  --category hand_contact \
  --model seedance-2.0 \
  --lesson "For hand-to-prop contact, isolate one interaction and keep the hand visible through release." \
  --approved-by "<reviewer-label>" \
  --markdown work/generation_lessons.md

# 2. 每次复用前验证；也可 select 先看实际命中的规则
python3 scripts/generation_lessons.py verify \
  --library work/generation_lessons.json \
  --strict
python3 scripts/generation_lessons.py select \
  --library work/generation_lessons.json \
  --provider dreamina_seedance \
  --model seedance-2.0 \
  --limit 3 \
  --output work/selected_generation_lessons.json \
  --markdown work/selected_generation_lessons.md

# 3. 显式注入下一次 provider prompt pack
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --provider dreamina_seedance \
  --lesson-library work/generation_lessons.json \
  --lesson-model seedance-2.0 \
  --lesson-limit 3 \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md
```

未传 `--lesson-model` 时只会命中 provider-wide (`model=*`) 经验，避免模型专属行为外溢；provider 精确经验优先于 global 经验。新证据推翻旧规则时，用新 entry 的 `--supersedes <old-lesson-id>` 保留历史但停止选择旧规则；未知 id、自引用或重复 id 会阻断。`add` 允许“该失败片段需要重生”这一预期 blocker，以便从失败中学习，但 source/contact-sheet 漂移、非法 response、漏审或 stored audit 篡改仍会拒绝。经验库 SHA-256 用于发现漂移，不是签名；`approved_by` 也不是身份认证。脚本不会调用生成 provider、不会自动重生、不会消费 credits，且每个 shot 默认最多注入 3 条经验。`pipeline_manifest.py --require generation_lessons --strict` 可把库完整性设为项目门禁。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

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

# 本地素材不够时，先规划 stock 查询，不直接联网下载
python3 scripts/stock_material_plan.py \
  --subject "AI workflow automation" \
  --script work/transcript.json \
  --provider pexels \
  --provider pixabay \
  --provider coverr \
  --media-library . \
  --output work/stock_material_plan.json \
  --markdown work/stock_material_plan.md

# 下载/自有素材确认授权后，登记到素材库和 provenance 元数据
python3 scripts/media_library.py import /path/to/downloaded.mp4 \
  --project-dir . \
  --category broll \
  --copy \
  --provider pexels \
  --source-url "https://www.pexels.com/video/demo-123/" \
  --creator "Demo Creator" \
  --license "Pexels License" \
  --tag "workflow,dashboard"
```

打分规则：tag 命中权重大于文件名命中，其次是路径、metadata、关联 transcript；`category=broll`、视频类型、时长覆盖 cue、画幅接近目标比例会加分；默认过滤索引里已经不存在的文件，`--include-missing` 可用于清理 stale index。本地素材不足时，用 `stock_material_plan.py` 生成 Pexels / Pixabay / Coverr 查询计划；下载或客户给的素材再用 `media_library.py import` / `annotate` 写入 provider、source URL、creator、license 等元数据，供 `asset_provenance.py` 发布门禁复核。

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

### 🎥 PIP Overlay — 录屏摄像头小窗
[`scripts/pip_overlay.py`](scripts/pip_overlay.py) · [详细文档](docs/prompts/51-pip-overlay.md)

借鉴 Loop 这类录屏编辑工具把 screen、microphone 和 optional camera 合成一个短反馈闭环的做法，但保持本项目的 artifact-first 方式：先把 facecam/camera 录制转成 `pip_overlays[]` 计划，复核 Markdown 后再交给 `render_final.py --enrich-plan` 单次编码合成，不混入 camera audio。

常用：
```bash
python3 scripts/pip_overlay.py \
  --camera origin/facecam.mp4 \
  --segment "0,18,bottom_right" \
  --segment "18,42,top_right" \
  --sync-offset 0.18 \
  --width-ratio 0.24 \
  --output work/pip_overlay_plan.json \
  --markdown work/pip_overlay_plan.md

python3 scripts/render_final.py \
  --config work/render_config.json \
  --enrich-plan work/screen_focus_plan.json \
  --enrich-plan work/pip_overlay_plan.json \
  --output output/tutorial_master.mp4
```

`pip_overlays[]` 支持每段独立 `position`、`source_start`、`sync_offset`、`width_ratio`、`margin_ratio`、`opacity` 和 `transition`；`render_final.py` 会随 `--primary-speed` / `--speed` 同步压缩 camera 小窗时间线，避免变速输出时讲解人画面和主画面错位。

### 🎙️ Audio Sync — 外录音频自动对齐
[`scripts/audio_sync.py`](scripts/audio_sync.py) · [详细文档](docs/prompts/56-audio-sync.md)

借鉴 AICW Video 和 ffsubsync 把“同步”做成独立可复核能力的思路：先用相机/录屏 scratch audio 和外录 lav/recorder 音频估计 offset，输出 `audio_sync_plan.v1` 和 Markdown，再确认是否执行替换音轨。

常用：
```bash
python3 scripts/audio_sync.py \
  --reference-media origin/camera.mp4 \
  --external-audio origin/lav.wav \
  --output work/audio_sync_plan.json \
  --markdown work/audio_sync_plan.md \
  --replace-output output/camera_lav_synced.mp4 \
  --max-offset 5 \
  --strict

# 复核 work/audio_sync_plan.md 后再执行
python3 scripts/audio_sync.py \
  --reference-media origin/camera.mp4 \
  --external-audio origin/lav.wav \
  --output work/audio_sync_plan.json \
  --markdown work/audio_sync_plan.md \
  --replace-output output/camera_lav_synced.mp4 \
  --apply \
  --strict
```

`alignment.offset_seconds` 为正表示延迟外录音轨；为负表示裁掉外录音轨开头。自动估计低置信度时 status 会变成 `review`，也可以用 `--offset 0.18` 手动指定偏移。`pipeline_manifest.py` 会发现 `audio_sync_plan.json` 并在低置信度或缺文件时阻塞发布 gate。

### 🎥 Multicam Sync — 多机位可逆同步计划
[`scripts/multicam_sync.py`](scripts/multicam_sync.py) · [详细文档](docs/prompts/76-multicam-sync.md)

两台以上设备录下同一场访谈、播客、活动或演示时，先选一台参考机位，再把其他机位对齐到它的时间线：

```bash
python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mp4 \
  --angle origin/cam-b.mp4 \
  --angle origin/cam-c.mp4 \
  --measure-clock-drift \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --preview-output output/verify/multicam_sync_preview.mp4 \
  --apply-preview \
  --strict
```

输出 `multicam_sync_plan.v1`，每路记录 `alignment.offset_seconds`、置信度、参考/源时间覆盖区间和实际采用的 `0:a:N`。多音轨相机会用中段 `mean_volume` 自动选择最响音轨，也可用 `--audio-stream "origin/cam-b.mp4=2"` 覆盖。三路以上自动对齐会额外直接比较非参考机位，若“参考机位推导 offset”与“机位间直接估计”相差超过阈值，就进入 review gate。`--manual-offset "origin/cam-c.mp4=1.24"` 可接入无音轨机位或已经人工确认的拍板点。

`--measure-clock-drift` 会在每个机位自己的可用重叠时长中默认抽取 5 个 20 秒窗口，按置信度筛 probe，再用稳健共识模型拟合 `offset(R)=intercept+slope*R`；至少需要 4 个跨越有效时段的 fit inlier。JSON/Markdown 保存每个 probe、`offset_slope_ppm`、测量/斜率分辨率、累计漂移、拟合残差和未应用的 `atempo/setpts` advisory factors；累计漂移超过 80 ms 或拟合不可靠会进入 review gate。可用 `--drift-probes`、`--drift-probe-seconds`、`--drift-search-seconds`、`--drift-threshold-ms` 调整。该能力默认关闭，只测当前选择的参考/源音轨；它不自动证明视频 PTS 或容器内其他音轨使用同一时钟，也不会自动校正。

原片始终不修改；只有显式 `--apply-preview` 才会生成短网格预览。正 offset 表示该机位的 `t=0` 位于参考时间线更晚的位置，预览按 `source_local = reference_time - offset` 读取每路画面。未启用漂移测量时，30 分钟以上素材仍会警告并要求头/中/尾复核。`pipeline_manifest.py` 会发现该计划并拦截缺文件、低置信度、无公共重叠、pairwise 不一致或漂移 review。

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

### 🔎 Subtitle Readability QA — 最终字幕可读性门禁
[`scripts/subtitle_readability_qa.py`](scripts/subtitle_readability_qa.py) · [详细文档](docs/prompts/70-subtitle-readability-qa.md)

`subtitle_pack.py` 负责生成字幕，但生成成功不代表最终字幕一定可发布。`subtitle_readability_qa.py` 读取与 master 同速、同片头 offset 的 `subtitle_pack.v1` JSON，检查 cue 时间缺失/倒序、真实重叠、极短闪现、CPS、持续时间、行数、单行长度，并可用 `--media` 检查字幕是否超过成片结尾。

```bash
python3 scripts/subtitle_readability_qa.py \
  output/subtitles/day58_master.json \
  --media output/day58_master.mp4 \
  --output verify/subtitle_readability_qa.json \
  --markdown verify/subtitle_readability_qa.md \
  --strict
```

默认中文 18 字/行、英文 42 字符/行；18 CPS 以上、0.5 秒以下、7 秒以上或超过 2 行只 WARN，必须结合正常速度成片判断。时间无效、cue 重叠、超过媒体结尾、短于 0.15 秒或超过 25 CPS 会写入 `summary.blocking`，`--strict` 返回 2。报告存在且 blocking 非零时 `pipeline_manifest.py` 会阻塞；要强制具备报告可加 `--require subtitle_readability_qa`。本 gate 只读 timed text，不做 OCR，也不声称能判断字体、颜色、描边或画面安全区。

### 📐 Platform Safe Area QA — 平台 UI 遮挡门禁
[`scripts/platform_safe_area_qa.py`](scripts/platform_safe_area_qa.py) · [详细文档](docs/prompts/73-platform-safe-area-qa.md)

渲染前读取 `render_config.json`、一个或多个 enrich plan 及可选自定义元素 bbox，按 renderer 默认布局估算字幕、badge、PIP、CTA、章节卡和 focus marker 的位置。内置 `xhs`、`douyin`、`wxch`、`tiktok`、`reels`、`shorts`、`universal`、`landscape` profile；输出 `platform_safe_area_qa.v1` JSON、Markdown 和 SVG 安全区图。

```bash
python3 scripts/platform_safe_area_qa.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --platform xhs \
  --output verify/platform_safe_area_qa.json \
  --markdown verify/platform_safe_area_qa.md \
  --guide verify/platform_safe_area_guide.svg \
  --strict
```

关键元素越界会进入 `summary.blocking`，`--strict` 返回 2；非关键元素只 WARN。`pipeline_manifest.py` 会发现报告并传播 blocker，也可加 `--require platform_safe_area_qa` 强制发布前必须存在。平台 UI 会变化，内置 profile 是可复现的社区经验保守值，不是永久官方规范；可用 `--safe-left/top/right/bottom` 覆盖当前实测边距。脚本不做 OCR，无法判断生成图或全屏画面内部的人脸/标题位置，SVG 和最终成片人工复核仍是必要步骤。

### 🔁 CapCut Subtitle Import — 剪映字幕反向导入
[`scripts/import_capcut_subtitles.py`](scripts/import_capcut_subtitles.py) · [详细文档](docs/prompts/50-import-capcut-subtitles.md)

借鉴 SmartCut / CapCut 自动字幕工作流：先在剪映里用 Auto Captions 生成或人工校对字幕，再把字幕轨导回本项目，生成兼容 `rewrite_script.py`、`rough_cut.py`、`subtitle_pack.py` 的 `transcript.json`。需要按字幕间隙做初剪时，同一个脚本也能输出 `keep_segments` cut list，交给 `timeline_view.py` 或 `export_edl.py` 复核。

常用：
```bash
python3 scripts/import_capcut_subtitles.py \
  --draft ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/day58 \
  --transcript work/capcut_transcript.json \
  --cut-list work/capcut_gap_cut.json \
  --markdown work/capcut_subtitles.md \
  --gap-threshold 1.0

python3 scripts/import_capcut_subtitles.py \
  --srt exports/capcut_auto_captions.srt \
  --transcript work/capcut_transcript.json \
  --srt-output output/subtitles/capcut_clean.srt
```

默认只读取剪映草稿里的 subtitle 材料，避免把封面标题/贴纸文字误当口播字幕；如果某个草稿把自动字幕保存成普通文字轨，可加 `--include-overlays`。`--cut-list` 是“字幕间隙代理”的保守粗剪，最终渲染前仍应跑 `timeline_view.py --cut-list` 人工复核。

### 🧾 SRT Edit Plan — 字幕编辑指令转剪辑方案
[`scripts/srt_edit_plan.py`](scripts/srt_edit_plan.py) · [详细文档](docs/prompts/55-srt-edit-plan.md)

借鉴 video-use / OpenStoryline 这类“先让人或 agent 给出编辑意图，再生成可复核时间线”的方式；本项目把它压成一个本地确定性桥接脚本：SRT + keep/drop 字幕编号指令 → `srt_edit_plan.json`、`render_config.json`、source-time cut list 和 Markdown review。

`work/edit_guide.md` 示例：
```md
title: 发布会高光
platform: xhs
keep 3-5: 先用产品发布和用户反应
drop 1-2: 铺垫太慢
keep 8: 补一句核心结论
```

常用：
```bash
python3 scripts/srt_edit_plan.py \
  --srt work/captions.srt \
  --guide work/edit_guide.md \
  --source-media origin/talking.mp4 \
  --output work/srt_edit_plan.json \
  --render-config work/render_config.json \
  --cut-list work/srt_edit_cut.json \
  --markdown work/srt_edit_plan.md \
  --strict
```

`keep/include/use/select` 行按出现顺序生成最终输出；`drop/skip/exclude/remove` 行写入 review。`--cut-list` 按原素材时间排序，适合 `timeline_view.py --cut-list` 复核；真正重排后的输出看 `--render-config`。

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
| 删除预算 gate | 默认最多删除源时长 20%；超限写 blocked 计划并拒绝渲染，显式 `--allow-over-budget` 才放行 |
| 安全 padding | 默认每个切点保留 0.08s，避免咬字被切掉 |
| 防爆音 fade | 默认每个保留片段加 30ms 音频淡入/淡出；`--fade-duration 0` 可关闭 |
| 单次编码渲染 | 用 `trim/atrim + concat` 一次输出，不产生中间重编码文件 |

常用：
```bash
python3 scripts/jump_cut.py input/talking.mp4 --dry-run --cut-list output/talking.jumpcut.json --strict
python3 scripts/timeline_view.py input/talking.mp4 --cut-list output/talking.jumpcut.json --output-dir output/verify/cuts
python3 scripts/jump_cut.py input/talking.mp4 --output output/talking.jumpcut.mp4 --cut-list output/talking.jumpcut.json --fade-duration 0.03
```

cut list 会记录 `removal_budget`、`status`、`summary.blocking`、blockers/warnings。若预计删除超过 20%，先检查 `removed_segments` 和切点复盘图，再调整 `--min-silence` / `--pad` / `--max-removal-ratio`；只有明确接受超预算剪辑时才加 `--allow-over-budget`。`pipeline_manifest.py` 会把未批准的超预算 jump cut 识别为 `rough_cut` blocker。

### ✂️ Multimodal Dead-Air — 静音 + 静帧保守剪辑
[`scripts/multimodal_dead_air.py`](scripts/multimodal_dead_air.py) · [详细文档](docs/prompts/88-multimodal-dead-air.md)

当口播停顿里可能仍有表情、手势、产品展示或屏幕操作时，纯音频去静音过于激进。这个流程同时运行 FFmpeg `silencedetect` 与 `freezedetect`：默认静帧覆盖一段静音至少 60% 才入选，并且只删除二者交集。

```bash
python3 scripts/multimodal_dead_air.py plan origin/talking.mp4 \
  --delivery work/talking-dead-air-tight.mp4 \
  --output work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md \
  --strict
python3 scripts/multimodal_dead_air.py verify work/multimodal_dead_air_plan.json --strict
DEAD_AIR_CUT_COUNT="$(python3 -c 'import json; print(len(json.load(open("work/multimodal_dead_air_plan.json"))["removed_segments"]))')"
python3 scripts/timeline_view.py origin/talking.mp4 \
  --cut-list work/multimodal_dead_air_plan.json --output-dir verify/dead-air-cuts \
  --limit "$DEAD_AIR_CUT_COUNT"
python3 scripts/multimodal_dead_air.py apply work/multimodal_dead_air_plan.json \
  --markdown work/multimodal_dead_air_plan.md
```

计划绑定源文件 SHA-256 与媒体契约，保留 80ms 切点 padding、30ms 音频 fade 和 20% 总删除预算。apply 复用 `jump_cut.py` 的 `trim/atrim + concat` 单次编码器；临时 MP4 必须通过 H.264/AAC、`yuv420p`、尺寸、帧率、采样率、声道、时长和全长解码契约才原子提升，输出 hash 与媒体记录会写回计划并由 manifest live verify。`timeline_view.py` 默认最多显示 20 个切点，上面的实际计数确保全部候选进入复核；计数为 0 时保留原片。它不是表演或语义判断器，必须先看所有源切点，再 1× 带声音完整播放工作副本。

### 🔎 Timeline View — 源素材/成片切点复盘图
[`scripts/timeline_view.py`](scripts/timeline_view.py) · [详细文档](docs/prompts/22-timeline-view.md)

借鉴视频剪辑类 skill 的 `timeline_view` 工作台：在跳切前后或 QA 报警区间生成一张 PNG，上半部分是 filmstrip，下半部分是 waveform，方便快速判断“切点是否咬字、画面是否突跳、静音是否自然”。除源素材删除段外，现在也可把 cut list 的 `keep_segments` 累计映射到已渲染成片的实际输出切点，逐个检查拼接结果。

常用：
```bash
python3 scripts/timeline_view.py output/day58_master.mp4 --at 42.5 --radius 1.5 --output output/verify/42_5s.png
python3 scripts/timeline_view.py origin/talking.mp4 --cut-list work/jumpcut.json --output-dir output/verify/cuts --limit 12
python3 scripts/timeline_view.py output/rough_cut.mp4 --rendered-cut-list work/rough_cut.json --output-speed 1.25 --output-offset 1.0 --output-dir output/verify/rendered_cuts --json output/verify/rendered_cuts.json
```

`--rendered-cut-list` 读取 `keep_segments` 的既定顺序，按每段 source duration / `--output-speed` 累加，并把 `--output-offset` 作为片头封面或其他前置时长。JSON 会为每张复盘图保存 `boundary.output_time`、前后 source range 与 `source_gap`；如果计算切点超过实际成片时长，脚本会提示校正 speed/offset，而不会输出错位证据。

### 🔀 Edit Compare — 原片 vs 成片 source-time 对照
[`scripts/edit_compare.py`](scripts/edit_compare.py) · [详细文档](docs/prompts/74-edit-compare.md)

把原片放在左栏连续播放，把**最终交付像素**按既有 `keep_segments` 投回右栏的原片时间轴；被删除范围显示黑屏。这样可以播放复核“删了什么”和“最终字幕/B-roll/调色/裁切变成了什么”，而不只看静态 cut plan。

```bash
python3 scripts/edit_compare.py \
  origin/talking.mp4 \
  output/day74_master.mp4 \
  --cut-list work/rough_cut.json \
  --output-speed 1.25 \
  --output-offset 2.0 \
  --output output/verify/day74_source_vs_final.mp4 \
  --report output/verify/day74_edit_compare.json \
  --markdown output/verify/day74_edit_compare.md
```

脚本自动验证双栏尺寸、原片时长、source-clock 音轨、删除范围黑屏和代表性保留范围的最终像素映射；`pipeline_manifest.py` 会把 `summary.blocking > 0` 的 `edit_compare` 报告列为阻塞 gate。V1 只接受单来源、按时间升序、无重叠的 `keep_segments`，并支持一个全局 `--output-speed` / `--output-offset`；重排、多来源、逐段不同速度或倒放要用 NLE/OTIO 时间线复核。没有 source-time 位置的 offset 片头和末段之后片尾不会出现在右栏，仍需完整播放 final/review proxy。

### 🎨 AI 图像生成（gpt-image-2 / Codex imagegen）
[`scripts/imagegen_hint.py`](scripts/imagegen_hint.py) · [`scripts/prompts/imagegen_templates.yaml`](scripts/prompts/imagegen_templates.yaml) · [详细文档](docs/prompts/19-imagegen.md)

抽象概念（注意力机制 / 复利 / 信息茧房 / 长尾效应 …）自动检测 + 适配 **gpt-image-2** 七槽位提示词结构。

- **Codex 环境**：检测到的 prompt 直接喂给 Codex 内置 `imagegen` 工具——**无需** OpenAI API key，Codex 自动路由到 gpt-image-2
- **其他环境**：用 OpenAI Python SDK 自己接（`openai.OpenAI().images.generate(...)`，需 `OPENAI_API_KEY`）。本 skill 只产 prompt，不内置客户端
- **内置 7 个 sample**：注意力机制 / 信息茧房 / 复利 / 长尾效应 / 数据柱状图 / 章节标题卡 / 早晨笔记本 B-roll（每个都带双语 prompt + why-it-works）
- **5 个 structure 槽位**：chapter_background / chapter_title_card / broll_fallback / data_visualization / abstract_concept
- **gpt-image-2 规则全部编码**：引号 = 精确文字渲染、约束写进 prose（无 negative-prompt 字段）、具体相机+光圈+光照（避免 "AI 味"）、默认拒绝人脸/人手特写、中文标题不走 gpt-image-2

### 🧪 Edit Preflight — 渲染前预检 gate
[`scripts/edit_preflight.py`](scripts/edit_preflight.py) · [详细文档](docs/prompts/53-edit-preflight.md)

借鉴 agent 视频编辑工具的 structured preflight / risky-parameter guardrails 思路，但保持本项目本地 artifact-first：先检查 `render_config.json`、`enrich_plan.json` 和 rough/jump cut list，再决定是否允许进入 FFmpeg 渲染。

常用：
```bash
python3 scripts/edit_preflight.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output work/edit_preflight.json \
  --markdown work/edit_preflight.md \
  --strict
```

输出 `edit_preflight.v1`，会检查空剪辑、缺视频/图片/音频文件、`transcript + segment_id` 不匹配、非法时间段、overlay 超出输出时间线、PIP/focus 参数风险。`pipeline_manifest.py` 会识别 `edit_preflight.json`，如果 `summary.blocking > 0` 就把它列为 blocking gate。它不解码、不渲染、不上传；渲染后仍然要跑 `render_qa.py`。

### ♻️ Portable Edit Recipe — 换素材复用已审时间线

[`scripts/edit_recipe.py`](scripts/edit_recipe.py) · [详细文档](docs/prompts/82-edit-recipe.md)

`export` 先对现有 `render_config.json` 跑 `edit_preflight.py`，再递归把视频、transcript、BGM、图片、字幕、LUT 等本地文件引用替换成 `${video_1}` / `${transcript_1}` 这类类型化槽位。配方只保留模板、槽位位置、原始文件 SHA-256/大小/后缀、源 config SHA-256 和无路径 preflight 摘要；`portable_sha256` 绑定整个模板与复用契约。

```bash
python3 scripts/edit_recipe.py export \
  --config work/render_config.json \
  --name fast-tech-explainer \
  --description "快节奏科技口播，双段原话 + 卡片 + ducking" \
  --output work/recipes/fast-tech-explainer_edit_recipe.json \
  --markdown work/recipes/fast-tech-explainer_edit_recipe.md

python3 scripts/edit_recipe.py verify \
  --recipe work/recipes/fast-tech-explainer_edit_recipe.json
```

在新项目回放时，必须为 Markdown 表中的每个槽位各传一次 `--bind`；缺失、重复、未知、扩展名类型不符、文件不存在、recipe digest/occurrence 被改或模板残留本地路径都会退出 2。回放输出新的绝对路径 `render_config.json`、绑定文件哈希 receipt 和 Markdown，并自动运行 preflight：

```bash
python3 scripts/edit_recipe.py replay \
  --recipe work/recipes/fast-tech-explainer_edit_recipe.json \
  --bind video_1=origin/episode-02.mp4 \
  --bind transcript_1=work/episode-02_transcript_reviewed.json \
  --bind image_1=work/cards/episode-02.png \
  --bind audio_1=origin/music/episode-02.wav \
  --output work/render_config.json \
  --receipt work/edit_recipe_replay.json \
  --markdown work/edit_recipe_replay.md \
  --strict
```

默认拒绝覆盖已有输出，确实要替换时显式加 `--force`。配方哈希只证明内容身份，不是作者签名、人工审批或“新素材与旧时间码语义等价”的证明；`ready` 以后仍必须实际渲染并人工审片。`pipeline_manifest.py` 对任何已发现的 `*_edit_recipe.json` 都会现场重算 schema、槽位 occurrence、路径泄漏和 digest，不能靠手改 `summary.blocking` 绕过。

### 🎚️ 渲染层（V3 强化）
[`scripts/render_final.py`](scripts/render_final.py)

| 默认行为 | 触发命令 / 配置 |
|---|---|
| Heavy 字幕字体（Source Han Sans Heavy / STHeiti Medium） | `find_chinese_font()` 自动选 |
| 响度规范化 `dynaudnorm + acompressor + loudnorm` | 默认开启，`--no-loudnorm` 关 |
| 口播稳态底噪清理 | `--speech-denoise light|medium|strong` 或 config `"speech_denoise": "medium"`；默认 `off` |
| 速度直接生效（不留 1.0× 副本） | `--primary-speed 1.25` |
| 受众档位预设（节奏/字幕密度/BGM 增益） | `--profile tech_pro` |
| 内部 token 拦截 | 自动；任何 `1.25x`/`mlx-whisper`/`loudnorm` 出现在画面文本字段都退出 |
| 平台 lint | 自动；`--no-content-guard` 关 |
| 字幕风格 | `--subtitle-style normal/karaoke/bold_pop/neon/minimal/yellow_pop` |
| 自动丰富接入 | `--enrich-plan work/enrich_plan.json`，可重复传入 |
| 点击聚焦 | `--enrich-plan work/screen_focus_plan.json`，读取 `focus_events[]` |
| 调色接入 | `--color-grade work/color_grade.json` 或 config `"color_grade": "screen"` |
| 旁白驱动 BGM ducking | `--bgm-ducking` 或 config `"bgm_ducking": true`；`--no-bgm-ducking` 临时关闭 |
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

### 🧭 NLE Handoff — EDL / FCPXML / OTIO 导出
[`scripts/export_edl.py`](scripts/export_edl.py) · [`scripts/export_fcpxml.py`](scripts/export_fcpxml.py) · [`scripts/export_otio.py`](scripts/export_otio.py) · [详细文档](docs/prompts/27-export-edl.md)

借鉴自动剪辑/生成类项目常见的“先产 timeline，再交给专业剪辑软件继续精修”工作流：`export_edl.py` 可把本项目的 `render_config.json` 或 `rough_cut.py` / `jump_cut.py` 产生的 `keep_segments` 导出成单轨 CMX 3600 风格 EDL；`export_fcpxml.py` 导出 Final Cut Pro / DaVinci Resolve 更友好的单 spine FCPXML；`export_otio.py` 导出 OpenTimelineIO `.otio`，默认包含 V1 + A1 track。三者都会写 JSON manifest，保留绝对源路径和精确秒数。

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

python3 scripts/export_fcpxml.py \
  --config work/render_config.json \
  --output work/day58_edit.fcpxml \
  --fps 30 \
  --width 1080 \
  --height 1920

python3 scripts/export_otio.py \
  --config work/render_config.json \
  --output work/day58_edit.otio \
  --fps 30 \
  --title DAY58_EDIT
```

适合把自动粗剪交给 Premiere / Final Cut Pro / DaVinci Resolve 做调色、混音、精剪或协作复核。EDL 更通用，FCPXML 对 FCP / Resolve 更直接，OTIO 更适合使用 OpenTimelineIO adapter 的跨工具流程；复杂字幕、overlay、章节卡和 B-roll 仍以 `render_final.py` / `export_capcut.py` 为准。

### 2026-07-19 自动化升级记录（Subtitle Readability QA）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 字幕在最终输出时间线上应用 offset，并在 render 后自检字幕可读性 | 新增 output-aligned subtitle pack 的独立发布前门禁；不重复做转写或渲染 |
| [`hoodini/ai-agents-skills` 的 video-edit skill](https://github.com/hoodini/ai-agents-skills/blob/master/skills/video-edit/SKILL.md) | 长渲染前先确认 transcript，渲染后抽查 caption quality | 保留“先审文本、后验成片”的两阶段思路，并输出可定位 cue 的 JSON / Markdown evidence |
| [`SubtitleEdit/subtitleedit`](https://github.com/SubtitleEdit/subtitleedit) | CPS、最短/最长显示时间、最大行数、重叠等规则均可配置 | 默认用 18 CPS 提醒、25 CPS 阻塞，并检查时长、行数、单行长度和 overlap；仅结构错误与极端读速自动 BLOCK |
| [`SB-Jeff/documentary-junior-editor` 的 timecode validator](https://github.com/SB-Jeff/documentary-junior-editor/blob/main/scripts/validate_timecodes.py) | 用确定性规则尽早拦截塌缩、乱序和越界时间码 | 加入非有限值、非正时长、文件顺序倒退及相对真实媒体时长越界检查 |

新增/调整能力：新增 [`scripts/subtitle_readability_qa.py`](scripts/subtitle_readability_qa.py)，读取 `subtitle_pack.v1` 的 `cues[]`，本地、只读地产生 `subtitle_readability_qa.v1` JSON 和可选 Markdown。报告覆盖无效/负数/非正时长、乱序、空文本、cue overlap、媒体越界、闪现字幕、CPS、过长/过短显示、最大行数与单行字符数；中文/英文默认单行上限分别为 18/42 字符。`pipeline_manifest.py` 新增 `subtitle_readability_qa` category，报告存在且 `summary.blocking > 0` 时阻塞，也支持 `--require subtitle_readability_qa`。同步更新 SKILL、每日工作流、提示词索引和 [Subtitle Readability QA 文档](docs/prompts/70-subtitle-readability-qa.md)。这一步不做 OCR 或视觉安全区判断，字体、遮挡与画面边缘仍需观看 master。

使用方式：先按实际 speed / cover offset 生成 output-aligned subtitle pack，再执行 `python3 scripts/subtitle_readability_qa.py output/subtitles/final_master.json --media output/final_master.mp4 --output verify/subtitle_readability_qa.json --markdown verify/subtitle_readability_qa.md --strict`。`--strict` 在存在 BLOCK 时退出码为 2；必须修正源字幕、speed/offset 或 render config 并重新生成。WARN 用于人工观看相应 cue，不建议为了清零指标机械拆句。

验证结果：新增 `tests/test_subtitle_readability_qa.py` 10 项，更新 `tests/test_pipeline_manifest.py` 2 项；定向 `.venv/bin/python -m pytest tests/test_subtitle_readability_qa.py tests/test_pipeline_manifest.py -q` 通过 `55 passed in 0.63s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `553 passed in 5.47s`。真实 FFmpeg smoke 生成 4 秒 360×640 H.264/AAC master：正确对齐的 2 条字幕得到 `ready`、`blocking=0`、`warnings=0`、`max_cps=3.5`、媒体时长 `4.0s`；故意增加错误 offset 后检出 `out_of_bounds=1`，结果 `blocked` 且 strict 退出码为 2。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-18 自动化升级记录（Retention Rhythm QA）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`nopefallacy/vertical-video-editing-skills`](https://github.com/nopefallacy/vertical-video-editing-skills) | 明确要求前三秒 engineered hook、非机械等距 cut、preview 后再 render，并把 pacing 纳入最终 verification | 新增 render 后节奏报告；保留“非等距”和 hook activity 检查，不引入 HyperFrames 运行时 |
| [`byteplus-sa/polym` 的 reference video analyzer](https://github.com/byteplus-sa/polym/blob/main/skills/polym-explainer-video/scripts/analyze_reference_video.py) | 用 FFmpeg scene detection 统计 cut count 和 average shot duration，轻量、可复现 | 复用本项目已有 `scene_boundaries.py` 的稳健 `pts_time` 解析，扩展到 shot ranges、p90、CV、长 hold 和 attention gaps |
| [`liangali/video-editing-skills`](https://github.com/liangali/video-editing-skills) | storyboard guard 把 clip 时长、素材覆盖和“字幕每 3 秒内变化”写成可执行约束 | 可选读取 output-aligned `subtitle_pack.v1`，检查 subtitle hold / uncovered gap；不把每 3 秒硬切当成通用规则 |
| [`tuanvo2409/srt2viral_de` pacing analyzer](https://github.com/tuanvo2409/srt2viral_de/blob/main/src/viral/pacing_analyzer.py) | Hook / Problem / Content / Payoff 分段审查，结合 shot duration、silence 和 phase score | 吸收“hook 比正文更敏感”和逐项 evidence；不输出虚假的留存提升百分比，声音问题继续交给 `render_qa.py` / `audio_master_report.py` |

新增/调整能力：新增 [`scripts/retention_rhythm_qa.py`](scripts/retention_rhythm_qa.py)，对已渲染 master / platform export 运行 FFmpeg hard scene detection，并可合并与 speed / cover offset 对齐的 subtitle pack JSON；输出 `retention_rhythm_qa.v1` JSON / Markdown，检查前三秒 activity、6/10 秒长视觉 hold、6/10 秒 combined attention gap、镜头时长 CV、0.35 秒以下快切 burst、4.5 秒以上字幕 hold 和 1.5 秒以上无字幕区间。WARN 必须人工看 master，只有高置信严重项进入 `summary.blocking`；报告明确声明不预测真实留存率或爆款概率。`pipeline_manifest.py` 新增 `retention_rhythm_qa` category，报告存在且 blocking 非零会阻塞，也支持 `--require retention_rhythm_qa`。同步更新 SKILL、每日工作流、提示词索引和 [Retention Rhythm QA 文档](docs/prompts/69-retention-rhythm-qa.md)。

使用方式：先运行 `subtitle_pack.py --config work/render_config.json --output-dir output/subtitles --basename final_master --speed 1.25 --offset 2.0` 生成 output-aligned JSON，再运行 `python3 scripts/retention_rhythm_qa.py output/final_master.mp4 --timed-text output/subtitles/final_master.json --output verify/retention_rhythm_qa.json --markdown verify/retention_rhythm_qa.md --strict`。已有 `scene_boundaries.v1` 时可用 `--scene-boundaries <json>` 复用，避免重复检测。若 BLOCK，按报告时间范围看 master / `timeline_view.py`，回到源 `render_config`、enrich plan 或 cut list 重渲染；不要为了清零 WARN 机械加切点。

验证结果：新增 `tests/test_retention_rhythm_qa.py` 10 项，更新 `tests/test_pipeline_manifest.py` 2 项；定向 `.venv/bin/python -m pytest tests/test_retention_rhythm_qa.py tests/test_pipeline_manifest.py -q` 通过 `53 passed in 0.60s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `541 passed in 7.40s`。真实 FFmpeg smoke 生成 14 秒 360×640 非等距硬切视频，CLI 实测检出 5 个 cut / 6 个 shot、最长视觉 hold 4.00 秒、cadence CV 0.423，结果 `ready`、`blocking=0`、`warnings=0`。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-17 自动化升级记录（Cover Variants + Publish Selection）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AgriciDaniel/claude-youtube`](https://github.com/AgriciDaniel/claude-youtube) | thumbnail 子技能输出 3 套 A/B variant、构图/配色、移动端可读性和 title-thumbnail synergy | 新增本地 `cover_variants.v1` review artifact；保留标题协同和小图复核，不引入 CTR 伪预测或外部 SERP 服务 |
| [`mutonby/openshorts`](https://github.com/mutonby/openshorts) | YouTube Studio 把多标题、缩略图生成、真实人物/背景和最终发布放进同一条 creator workflow | 复用现有 Chrome 封面渲染，把多方案和发布选择接到 `publish_package.py`，不上传视频、不调用 Gemini |
| [`charlie947/social-media-skills`](https://github.com/charlie947/social-media-skills) 的 `youtube-thumbnail` | 3-5 个词、单一焦点、强对比、避免小字和右下角 UI 冲突等缩略图约束明确 | 增加中英文字数警告、平台尺寸和 feed-size preview；现有模板继续负责字体/布局 |
| [`op7418/guizang-ppt-skill`](https://github.com/op7418/guizang-ppt-skill) | 小红书封面强调 3:4、大标题和批次一致的字号层级 | `xhs` 固定输出 1080×1440；同一批 variant 共用封面文字和输出尺寸 |

新增/调整能力：新增 [`scripts/cover_variants.py`](scripts/cover_variants.py)，支持 `xhs`、`douyin`、`wxch`、`tiktok`、`reels`、`youtube_shorts`、`youtube`，默认输出 3 套 `cover-a/b/c` 方案：主风格、对比风格和真实画面证据风格；`--count 4` 增加去副标题版本。脚本输出 JSON/Markdown、标题—封面 overlap 检查、content guard 风险、每套可复现渲染命令、完整 PNG 和 `*_preview.png` 小图；`--select cover-c --require-selection --strict` 会记录 `selected_cover`。`generate_cover_image.py` 新增 `--width/--height`，保证不同平台封面按目标尺寸渲染；缺 Pillow 时小图自动回退 FFmpeg。`pipeline_manifest.py` 新增 `cover_variants` gate；`publish_package.py` 会优先采用已选择且存在的封面，并避免把未选择的 variant / preview 误当发布封面。同步更新 SKILL、每日工作流、封面 / 发布包文档和 [Cover Variants 提示词](docs/prompts/68-cover-variants.md)。若需要生成或编辑封面底图，生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

使用方式：先运行 `python3 scripts/cover_variants.py output/day68_master_xhs.mp4 --title "20分钟出片" --subtitle "AI剪辑完整流程" --caption output/day68_caption.json --platform xhs --frame-timestamp 12.5 --output-dir output/covers --render --output work/cover_variants.json --markdown work/cover_variants.md`；检查 `output/covers/*_preview.png` 后，重跑并加 `--select cover-c --require-selection --strict`。发布包无需再传 `--cover`，会读取 `selected_cover`；需要显式覆盖时仍可传 `publish_package.py --cover <path>`。

验证结果：新增 `tests/test_cover_variants.py` 7 项，更新 `tests/test_pipeline_manifest.py` 和 `tests/test_publish_package.py`；定向 `.venv/bin/python -m pytest tests/test_cover_variants.py tests/test_publish_package.py tests/test_pipeline_manifest.py -q` 通过 `57 passed in 0.85s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `529 passed in 4.91s`。真实 FFmpeg/Chrome smoke 用 1 秒 1080×1920 样片成功生成 3 张 1080×1440 小红书封面和 3 张 240×320 preview，选择 `cover-c` 后得到 `status=ready`、`rendered=3`、`blocking=0`。`.venv/bin/python -m compileall -q scripts tests`、两个 CLI `--help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-16 自动化升级记录（Rendered Speech Continuity QA）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`ElliotPadfield/autocut`](https://github.com/ElliotPadfield/autocut) | rough cut assemble 后重新转录成片，检查卡壳和重复，避免只验证 EDL | 把本项目原有“音频重复检测”人工说明实现成独立、本地、可机读 gate；不复制对方实现或引入 Node 管线 |
| [`openakita/openakita` ClipSense](https://github.com/openakita/openakita/blob/main/plugins/clip-sense/SKILL.md) | 编辑任务有明确 pipeline step、结构化状态、error kind 和本地/付费成本边界 | 延续本项目 JSON / Markdown / `summary.blocking` 约定；保持只读、零 API、零 credits |
| [`thesongzhu/Friday` video-editing-planner](https://github.com/thesongzhu/Friday/blob/main/skills/video-editing-planner/SKILL.md) | 以 story clarity 为先，避免为了节奏做过度剪辑 | 检测只报告技术性复读，不自动删片；命中后仍要求人工试听再改源 timeline |
| [`henryalouf/ruflow` storyboard](https://github.com/henryalouf/ruflow/blob/main/.agents/skills/storyboard/SKILL.md) | 每个镜头服务明确节拍，并与 voiceover beat 对齐 | 本项目已有 storyboard / motion / audio cue 层，本次不重复新增 shot planner，只补 render 后语义验证 |
| [`nikolovlazar/dotfiles` video-script](https://github.com/nikolovlazar/dotfiles/blob/main/.agents/skills/video-script/SKILL.md) | VIDEO/AUDIO 双栏让每个 beat 的听觉状态显式可查 | 本项目已有 storyboard + audio cue artifacts；保留为后续 AV 合并视图候选，本次聚焦已渲染成片事故 |

新增/调整能力：新增 [`scripts/speech_continuity_qa.py`](scripts/speech_continuity_qa.py)，读取已渲染 master 的二次 transcript，检测 `boundary_exact_repeat`（前段结尾/后段开头精确复读）、`adjacent_near_duplicate`（相邻近重复 take）和 `internal_immediate_repeat`（句内即时口吃）。中文按字、英文按词归一化；默认至少 3 个单位、near-duplicate 相似度 0.90、相邻间隔不超过 2 秒。不同 speaker 默认不互判，可显式加 `--include-speaker-changes`。输出 `speech_continuity_qa.v1` JSON / Markdown，`--strict` 命中返回 2；`pipeline_manifest.py` 会发现报告并把 `summary.blocking > 0` 视为发布 blocker，也支持 `--require speech_continuity_qa`。同步更新 SKILL 和 [Speech Continuity QA 提示词](docs/prompts/67-speech-continuity-qa.md)。

使用方式：先对**已渲染 master**运行 `extract_audio.py` 和 `transcribe.py`，再执行 `python3 scripts/speech_continuity_qa.py output/final_transcript.json --output verify/speech_continuity_qa.json --markdown verify/speech_continuity_qa.md --strict`。如果退出 2，按报告时间范围试听 master，并用 `timeline_view.py --at <seconds>` 看切点；调整源 `render_config` / cut list 后重新渲染、重新转录、复跑，不能复用源素材 transcript 或在成片上二次拼补。

验证结果：定向 `.venv/bin/python -m pytest tests/test_speech_continuity_qa.py tests/test_pipeline_manifest.py -q` 通过 `50 passed in 0.49s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `518 passed in 4.61s`。测试覆盖中英 token 化、最长边界复读、句内口吃及 segment evidence、近重复 take、跨 speaker 默认跳过、clean ready、Markdown、strict CLI 和 manifest blocker / required gate。`.venv/bin/python -m compileall -q scripts tests`、`speech_continuity_qa.py --help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-15 自动化升级记录（Jump Cut Removal Budget）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`htekdev/vidpipe`](https://github.com/htekdev/vidpipe) | 静音剪除先检测、再决定删除段，把总删除量限制为源时长 20%，并与字幕放进 single-pass edit | 为现有本地 `jump_cut.py` 增加同类安全预算；不引入 AI 决策，超限交给人工复核 |
| [`MastroMimmo/ffmpeg-skill`](https://github.com/MastroMimmo/ffmpeg-skill) | 18 个高层 FFmpeg 命令统一输出结构化 JSON，另有两遍 `vidstab` 稳定化 | 延续本项目 JSON artifact / CLI 模式；稳定化需先设计不破坏最终单次编码的接入方式，本次不混入 |
| [`luoluoluo22/jianying-editor-skill`](https://github.com/luoluoluo22/jianying-editor-skill) | 网页动效转视频、录屏智能变焦、语义素材匹配和剪映时间线自动化 | 本项目已有 Remotion、`screen_focus.py`、`media_library.py recommend` 和 CapCut handoff，本次不重复增加相邻入口 |
| [`video-db/skills`](https://github.com/video-db/skills) | 视觉/口播索引、时间戳证据、语义场景搜索和 server-side timeline | 本项目继续 local-first，以 `video_understanding.py`、`highlight_picker.py` 和 `timeline_view.py` 保留本地证据链，不新增云端 API 依赖 |

新增/调整能力：`scripts/jump_cut.py` 新增默认 `--max-removal-ratio 0.20` 删除预算。计划现在写入 `version=jump_cut_plan.v2`、`status`、`removal_budget`、`blockers` / `warnings` 和 `summary.blocking`；预计删除超过 20% 时仍会先写 cut list，但 dry-run 加 `--strict` 返回 2，实际渲染无论是否 strict 都返回 2 且不产生输出。人工检查 `removed_segments` 与 `timeline_view.py` 复盘图后，可调高预算，或用 `--allow-over-budget` 明确批准；批准会留在 JSON 的 `removal_budget.override=true` 和 warning 中。`pipeline_manifest.py` 现在把未批准的超预算 jump cut 识别为 `rough_cut` blocker；同步更新 SKILL、每日工作流和 [Jump Cut 提示词](docs/prompts/21-jump-cut.md)。

使用方式：先运行 `python3 scripts/jump_cut.py input/talking.mp4 --dry-run --cut-list work/jump_cut.json --strict`。如果退出 2，打开 JSON 检查 `removal_budget.proposed_ratio` 和 `removed_segments`，再用 `timeline_view.py --cut-list work/jump_cut.json` 看切点。确认全部删除段都安全后，可调整 `--min-silence` / `--pad` / `--max-removal-ratio`，或在渲染命令上显式加 `--allow-over-budget`；不要把 override 当成默认参数。

验证结果：定向 `.venv/bin/python -m pytest tests/test_jump_cut.py tests/test_pipeline_manifest.py -q` 通过 `47 passed in 0.45s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `505 passed in 5.55s`。10 秒真实 FFmpeg 样片含 3 秒静音，默认计划得到 `proposed_ratio=0.284`：strict dry-run 与实际渲染均返回 2，未生成 blocked MP4；加 `--allow-over-budget` 后得到 `status=ready`、`override=true`、`warnings=1`，成功输出 7.163 秒 MP4。`.venv/bin/python -m compileall -q scripts tests`、`jump_cut.py --help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-14 自动化升级记录（Rendered Cut Boundary Review）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 在渲染后对成片的每个 cut boundary 生成 timeline view，自检画面跳变、waveform 爆点、overlay/字幕层级与时长 | 扩展现有 `timeline_view.py`，把 `keep_segments` 累计映射到成片输出时间轴，不再把源素材删除段中点误当成最终拼接点 |
| [`ElliotPadfield/autocut`](https://github.com/ElliotPadfield/autocut) | rough cut assemble 后再复查成片，避免只验证 EDL 而漏掉渲染后的卡壳/重复 | 保持本地零 API 依赖，先补逐切点视觉 + 波形证据；二次 ASR 可按需另跑 `transcribe.py`，本次不强制重复转写 |
| [`hoodini/ai-agents-skills` video-edit](https://github.com/hoodini/ai-agents-skills/tree/master/skills/video-edit) | 最终交付前做跨时间轴 spot-check，并先完成 transcript 人工批准 | 本项目已有 `transcript_review.py` 和 `render_qa.py`；本次只补它们没有覆盖的“全部成片拼接点”批量视图 |
| [`ygtec/cut.skill`](https://github.com/ygtec/cut.skill) | 编辑器状态读取、原项目备份和跨剪映/Premiere 操控强调非破坏性 | 本项目继续使用本地 cut list、JSON manifest 与 EDL/FCPXML/OTIO 交接，不引入外部编辑器写入依赖 |

新增/调整能力：[`scripts/timeline_view.py`](scripts/timeline_view.py) 新增 `--rendered-cut-list`，读取 rough/jump cut 的 `keep_segments`，按输出顺序累计每段时长并为每个真实拼接点生成 filmstrip + waveform PNG。`--output-speed` 支持成片全局变速，`--output-offset` 支持片头封面/前置时长；两者映射不匹配、计算切点超过实际成片时长时会显式报错。JSON manifest 新增 `mode=rendered_output_boundaries` 和每张图的 `boundary`：包含 `output_time`、前后 source range 与被跳过的 `source_gap`。原有 `--cut-list` 源素材复核保持兼容；同步更新 SKILL、[`docs/prompts/22-timeline-view.md`](docs/prompts/22-timeline-view.md) 和提示词索引。

使用方式：rough/jump cut 渲染完成后运行 `python3 scripts/timeline_view.py output/rough_cut.mp4 --rendered-cut-list work/rough_cut.json --output-speed 1.25 --output-offset 1.0 --output-dir output/verify/rendered_cuts --json output/verify/rendered_cuts.json`。逐张查看切点前后的主体位置、字幕/overlay 遮挡、黑闪和 waveform 硬断；要看源素材里被删区间，仍使用 `--cut-list`。

验证结果：`tests/test_timeline_view.py` 从 7 项扩展到 11 项，覆盖累计输出时间、变速、片头偏移、limit、source gap、窗口定位和成片时长错配；定向 `.venv/bin/python -m pytest tests/test_timeline_view.py -q` 通过 `11 passed in 0.03s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `502 passed in 5.46s`。8 秒真实 FFmpeg smoke 含两段 1 秒静音，`jump_cut.py` 输出 6.433 秒成片，新模式准确定位 `2.10s` / `4.30s` 两个拼接点并生成两张 1600×490 RGB PNG；人工查看图中切点前后 filmstrip 与 waveform 均位于预期窗口。`.venv/bin/python -m compileall -q scripts tests`、`timeline_view.py --help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-13 自动化升级记录（Timecoded Review Proxy）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`heygen-com/hyperframes`](https://github.com/heygen-com/hyperframes) / [`NousResearch/hermes-agent` HyperFrames skill](https://github.com/NousResearch/hermes-agent/blob/main/optional-skills/creative/hyperframes/SKILL.md) | 把 `preview` 和 `render --quality draft\|standard\|high` 作为正式迭代阶段，先低成本看完整节奏再出高质量版本 | 新增独立 `review_proxy.py`，不改 `render_final.py` 的 publish master 参数，也不重复渲染素材时间线 |
| [`lennoxsaint/eddy`](https://github.com/lennoxsaint/eddy) | transcript → cut plan → simulation → proxy render → QA → judge/repair 的本地审片闭环 | 把完整审片代理放在 render/QA 后、dashboard/publish handoff 前，并接入 artifact manifest |
| [`lazniak/videoclipgenerator`](https://github.com/lazniak/videoclipgenerator) | TC burn-in 让剪辑反馈可以按精确时间码定位，容器 fast index/metadata 便于 seek | 默认烧入 elapsed timecode 与 `REVIEW PROXY` 标签，反馈可直接引用画面时间 |
| [`ychoi-kr/claude-ffmpeg-skill`](https://github.com/ychoi-kr/claude-ffmpeg-skill) | web-ready output、`faststart`、质量/体积平衡与输入校验 | 代理使用 H.264/AAC、`+faststart`、CRF 28、veryfast、最大 720p/24fps，原 master 保持不变 |

新增/调整能力：新增 [`scripts/review_proxy.py`](scripts/review_proxy.py)，可把任何已渲染 master / platform MP4 转成低码率完整审片副本，默认不放大源视频、最大 720p、24fps、H.264 `libx264 veryfast`、CRF 28、AAC 96k、`yuv420p` 和 `+faststart`；左上角默认烧入 `REVIEW PROXY` + elapsed timecode。脚本同时输出 `review_proxy.v1` JSON 和 Markdown，记录源/代理媒体参数、完整可复现 FFmpeg 命令、warning 与“不可发布”说明；`--dry-run` 只出计划，`--no-timecode` 可关闭时间码。`edit_brief_plan.py` 新增“审片代理/低码率时间码审片”路由，`pipeline_manifest.py` / `review_dashboard.py` 可发现 review proxy，但默认不阻塞发布；新增 [docs/prompts/66-review-proxy.md](docs/prompts/66-review-proxy.md)。

使用方式：`python3 scripts/review_proxy.py output/day66_master.mp4 --output verify/day66_review_proxy.mp4 --manifest verify/day66_review_proxy.json --markdown verify/day66_review_proxy.md`。把 `*_review_proxy.mp4` 发给审片人，要求按画面可见时间码反馈；具体疑点再用 `timeline_view.py --at <seconds>` 深查。最终 QA、平台导出和发布仍必须使用 master，不得把 review proxy 当成片。

验证结果：新增 `tests/test_review_proxy.py` 7 项，更新 `tests/test_edit_brief_plan.py` 和 `tests/test_pipeline_manifest.py`；定向 `.venv/bin/python -m pytest tests/test_review_proxy.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `51 passed in 0.48s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `498 passed in 4.74s`。4 秒真实 FFmpeg smoke 生成 360×640、24fps、H.264/AAC、4.01 秒的代理，抽取 1.25 秒帧确认双行 label/timecode 在小竖屏完整可读；文件头 `ftyp` 后紧接 `moov`（offset `0x20`），确认 `+faststart` 生效。新增回归测试确认 `*_master_review_proxy.mp4` 不能误满足正式 master gate。`.venv/bin/python -m compileall -q scripts tests`、`review_proxy.py --help`、`edit_brief_plan.py --help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-12 自动化升级记录（Audio-aware Boundary Snap）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AgriciDaniel/claude-shorts`](https://github.com/AgriciDaniel/claude-shorts) | 把已批准短视频范围再对齐到词边界、完整句结尾和 FFmpeg 静音区，并报告每条 adjustment delta | 新增本地 `audio_boundary_snap.py`，用本项目 artifact schema 重做可审计边界校正，不复制对方实现 |
| [`marvellam/interview-skill`](https://github.com/marvellam/interview-skill) | 长访谈重构强调每个剪辑块必须保留可追溯 source anchor，内部删除不能伪装成连续引语 | 校正后继续保留 highlight id、rank、segment ids、原始时间和首尾词，并附 `audio_boundary_snap` 审计字段 |
| [`imhzm/EDIT-REELS-LIKE-PRO-Claude-Skill`](https://github.com/imhzm/EDIT-REELS-LIKE-PRO-Claude-Skill) | 先做 silence/bad-take razor pass，再做动画和三层声音设计；切点需服务节奏而不是只看画面 | 本项目已有 `jump_cut.py`、`audio_cue_sheet.py` 和防爆音 fade，本次不重复声音设计，只补“已选片段的安全边界” |
| [`Canibuild-Ops/sketch-to-video-skill`](https://github.com/Canibuild-Ops/sketch-to-video-skill) | 用 beat-synced cut、speed ramp 和 transition 组织音乐视频 | 本项目已有 `beat_sync.py` / `transition_bridge.py`，本次不新增另一套节拍编辑器 |
| [`aiworkflowpro/video-editing-skill`](https://github.com/aiworkflowpro/video-editing-skill) | 用视觉 scene change 给 jump-cut 素材找 trim point | 本项目已有 `scene_boundaries.py` 和 `highlight_picker.py --scene-boundaries`，保留视觉吸附并在其后增加独立音频吸附 |

新增/调整能力：新增 [`scripts/audio_boundary_snap.py`](scripts/audio_boundary_snap.py)，可读取 `highlight_candidates.v1` 或其他含 `selected[]` / `segments[]` / `clips[]` 的候选计划，以及 Whisper `segments[].words[]` 或 ElevenLabs Scribe 顶层 `words[]`。脚本把 start/end 校正到完整词，在 `--sentence-window` 内补到句末，用 transcript `silences[]` 或可选 `--media` + FFmpeg `silencedetect` 吸附静音区中点，输出 `audio_boundary_plan.v1` JSON/Markdown；每条保留 original/snapped time、delta、reason、首尾词、warning 和 blocker。`shorts_batch.py` 现在直接接受该输出并把 `audio_boundary_snap` 写入 per-short render config；`edit_brief_plan.py` 的长视频拆短路由会自动把它排在 highlight 与 batch 之间；`pipeline_manifest.py` 新增 `audio_boundary_plan` 类别，发现 `summary.blocking > 0` 会阻塞。

使用方式：先跑 `highlight_picker.py` 并人工确认 selected 候选，再运行 `python3 scripts/audio_boundary_snap.py --candidates work/highlight_candidates.json --transcript work/long_transcript.json --media origin/long-talk.mp4 --output work/audio_boundary_plan.json --markdown work/audio_boundary_plan.md --strict`；打开 Markdown 复核 start/end delta 后，用 `python3 scripts/shorts_batch.py --highlights work/audio_boundary_plan.json --video origin/long-talk.mp4 --output work/shorts_batch.json --strict` 继续。缺词级时间戳、非法范围、显式媒体缺失或安全边界超平台时长会形成 blocker。详细说明见 [docs/prompts/65-audio-boundary-snap.md](docs/prompts/65-audio-boundary-snap.md)。

验证结果：新增 `tests/test_audio_boundary_snap.py` 8 项，更新 `tests/test_edit_brief_plan.py`、`tests/test_pipeline_manifest.py` 和 `tests/test_shorts_batch.py`；定向 `.venv/bin/python -m pytest tests/test_audio_boundary_snap.py tests/test_edit_brief_plan.py tests/test_shorts_batch.py tests/test_pipeline_manifest.py -q` 通过 `53 passed in 0.52s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `487 passed in 4.42s`。8 秒真实 FFmpeg smoke 检出 3 段静音，把候选从 `1.1-3.0s` 校正到 `0.5-4.5s`，句末扩展、静音吸附、`summary.blocking=0` 均符合预期；`.venv/bin/python -m compileall -q scripts tests`、`audio_boundary_snap.py --help`、`edit_brief_plan.py --help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-11 自动化升级记录（Audio-event-aware Takes Pack）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | Scribe transcript 用顶层 `words[]` 同时保留词、静音 spacing、speaker diarization 和 laughter/applause 等 `audio_event`；剪辑时把反应声当成应保留节拍 | 增强现有 `takes_pack.py`，直接读取该结构并输出带时间码的 `audio_events[]` |
| [`hoodini/ai-agents-skills` video-edit](https://github.com/hoodini/ai-agents-skills/tree/master/skills/video-edit) | 最终渲染前提供 transcript review/editor，降低字幕错词返工 | 本项目已有 `transcript_review.py` 的 export/apply 校稿闭环，本次不重复新增编辑器 |
| [`remotion-dev/skills`](https://github.com/remotion-dev/skills/blob/main/skills/remotion/SKILL.md) | 用 preview/still 做低成本视觉 sanity check | 本项目已有 `timeline_view.py` 与 `render_qa.py`，本次不再新增单帧工具 |
| [`video-db/skills`](https://github.com/video-db/skills) | 统一 spoken/visual moment 搜索与编辑接口，适合服务端实时/批处理 | 本项目继续保持本地 artifact-first，不引入 API key、上传或服务端依赖 |

新增/调整能力：[`scripts/takes_pack.py`](scripts/takes_pack.py) 现在除 `segments[].words[]` 外，也能直接读取 ElevenLabs Scribe 风格的顶层 `words[]`；`type=spacing` 不会混进正文，`speaker_id` 会和原有 `speaker` 一样触发 phrase 分段，`type=audio_event` 会以 `(laughter)` 形式留在可读文本，并在 `phrases[].audio_events[]` 中保留 label/start/end。`summary.audio_events` 与 `sources[].audio_events` 便于快速确认事件覆盖；没有 Scribe 或 ElevenLabs API key 也不影响原有 Whisper transcript 路径。

使用方式：`python3 scripts/takes_pack.py --transcript interview=work/scribe_transcript.json --output work/takes_packed.md --json work/takes_pack.json --break-gap 0.5`。打开 Markdown 的 Events 列复核笑声、掌声、叹气、音乐等反应节拍；选段时保留事件前后余量，再把 phrase time range 交给 `highlight_picker.py`、`render_config.json` 或 NLE handoff。详细格式见 [docs/prompts/60-takes-pack.md](docs/prompts/60-takes-pack.md)。

验证结果：新增 `tests/test_takes_pack.py` 顶层 Scribe words / spacing / speaker_id / audio_event 覆盖；`.venv/bin/python -m pytest tests/test_takes_pack.py -q` 通过 `6 passed in 0.06s`；`.venv/bin/python scripts/takes_pack.py --help`、`.venv/bin/python -m compileall -q scripts tests`、skill `quick_validate.py` 和 `git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `477 passed in 4.26s`。

### 2026-07-09 自动化升级记录（Edit Brief Plan Router）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`6missedcalls/video-editing-skill`](https://github.com/6missedcalls/video-editing-skill) | 用户用自然语言说 trim / jump cut / captions / overlay / speed，agent 读取 skill 后选择对应脚本 | 新增本地 `edit_brief_plan.py`，把一句话需求映射成本项目脚本 runbook |
| [`FireRedTeam/FireRed-OpenStoryline`](https://github.com/FireRedTeam/FireRed-OpenStoryline) | intention-driven directing、LLM planning、precise tool orchestration 和 human-in-the-loop | 本项目不新增 agent 框架；只输出可复核 JSON/Markdown、命令和 gate |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | typed operations、structured results、preflight guardrails、quality checkpoints，避免 agent 猜 FFmpeg 参数 | router 输出 `steps[]`、`gates[]`、`summary.blocking`，并接入 `pipeline_manifest.py` |
| [`hiteshK03/video-production-skill`](https://github.com/hiteshK03/video-production-skill) | skill 文件教 agent 选工具、传参数、排调用顺序和跨工具链编排 | 复用已有本地脚本，不引入 Resolve/MCP 依赖；只补“选哪个脚本、先后顺序”入口 |
| [`htekdev/vidpipe`](https://github.com/htekdev/vidpipe) | 从录制内容自动走 shorts、captions、social posts、brand voice 等发布链路 | router 可把发布、标题、字幕、BGM、短视频批量和 QA 一次排入本项目 artifact-first 流程 |

新增/调整能力：新增 [`scripts/edit_brief_plan.py`](scripts/edit_brief_plan.py)，可从 `--brief` 或 `--brief-file` 读取自然语言剪辑需求，自动识别源素材路径、目标平台、长视频拆条、批量短视频、字幕、B-roll、生成素材、BGM/SFX、去停顿、PIP、录屏聚焦、调色、QA、NLE handoff 和发布包等信号，输出 `edit_brief_plan.v1` JSON 与 Markdown runbook。新增 [docs/prompts/64-edit-brief-plan.md](docs/prompts/64-edit-brief-plan.md)，更新 daily workflow、提示词目录、SKILL、README 和 `pipeline_manifest.py` artifact 类别；`edit_brief_plan.json` 默认不阻塞，但当自身 `summary.blocking > 0`（如 brief 为空或显式 source 缺失）时会作为 gate blocker。

使用方式：先运行 `python3 scripts/edit_brief_plan.py --brief "把 origin/interview.mp4 剪成三条抖音短视频，去停顿，加B-roll、BGM和字幕，最后生成发布包" --project-dir . --output work/edit_brief_plan.json --markdown work/edit_brief_plan.md --strict`；打开 `work/edit_brief_plan.md` 检查每一步命令和产物，再按需要执行 runbook。需要把需求路由作为显式 gate 时用 `python3 scripts/pipeline_manifest.py --project-dir . --target-stage analysis --require edit_brief_plan --strict`。

验证结果：新增 `tests/test_edit_brief_plan.py` 6 项，更新 `tests/test_pipeline_manifest.py` 2 项；`.venv/bin/python -m pytest tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `38 passed in 0.38s`；`.venv/bin/python scripts/edit_brief_plan.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg edit_brief_plan` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `476 passed in 4.67s`。

### 2026-07-08 自动化升级记录（Shorts Batch Planner）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`backblaze-b2-samples/ai-shorts-generator`](https://github.com/backblaze-b2-samples/ai-shorts-generator) | 长视频 → 多条 9:16 shorts，并把每个 source/transcript/caption/rendered clip 持久化成可下载结果 | 新增本地 `shorts_batch.py`，把 selected highlights 变成多条可追踪 render job |
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 候选片段有质量评分、topic diversity、pre-render validation 和 creative-run history | batch job 保留 highlight score、warnings、segment ids、输出路径和 QA 命令 |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 结构化工具、preflight guardrails、release checkpoint，避免 agent 直接猜 FFmpeg 参数 | 本项目不引入 MCP server；输出 `render_shell` + `qa_shell`，并让 `pipeline_manifest.py` 发现 `shorts_batch` gate |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | pipeline-driven 视频生产强调多阶段 artifact 和质量 enforcement | 保持 JSON/Markdown artifact-first，不新增服务端 queue 或 provider 依赖 |

新增/调整能力：新增 [`scripts/shorts_batch.py`](scripts/shorts_batch.py)，读取 `highlight_picker.py` 产出的 `highlight_candidates.v1`，为每个 `selected[]` 生成独立 `render_config`、计划输出 MP4、`render_final.py` 命令、`render_qa.py` 命令和 `shorts_batch.v1` JSON/Markdown job sheet。新增 [docs/prompts/63-shorts-batch.md](docs/prompts/63-shorts-batch.md)，更新提示词目录、SKILL、README 和 `pipeline_manifest.py` artifact 类别；`shorts_batch.json` 默认不阻塞，但当自身 `summary.blocking > 0`（如源视频缺失）时会作为 publish/render gate blocker。

使用方式：先运行 `highlight_picker.py` 选出多个精华候选，再运行 `python3 scripts/shorts_batch.py --highlights work/highlight_candidates.json --video origin/long-talk.mp4 --output work/shorts_batch.json --markdown work/shorts_batch.md --render-config-dir work/shorts_render_configs --output-dir output/shorts --qa-dir verify/shorts --basename day63 --platform douyin --strict`；打开 `work/shorts_batch.md` 确认每条 job 后，逐条执行表内 `render_shell` 和 `qa_shell`。

验证结果：新增 `tests/test_shorts_batch.py` 5 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_shorts_batch.py tests/test_pipeline_manifest.py -q` 通过 `35 passed in 0.40s`；`.venv/bin/python scripts/shorts_batch.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg shorts_batch` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `468 passed in 5.25s`。

### 2026-07-07 自动化升级记录（Hook Variants）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`rishidandu/cutagent`](https://github.com/rishidandu/cutagent) | Hook Lab 一次生成 8 个不同开头角度，并把 hook 视为广告/短视频最重要的前三秒变量 | 新增本地 `hook_variants.py`，为同一 transcript 生成 8 个可审的 hook angle |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 用结构化工具、preflight guardrails 和 release checkpoint 避免 agent 直接猜 FFmpeg/发布参数 | `hook_variants.json` 输出结构化 `summary`、`variants[]`、`risks[]`，并接入 `pipeline_manifest.py --require hook_variants` |
| [`louisedesadeleer/b-roll-finder`](https://github.com/louisedesadeleer/b-roll-finder) | 强调 talking-head 视频里每个 cutaway/素材决策要贴合具体词、人物、产品或 claim，而不是随机 stock | 每个 hook variant 保留 `source_terms` 和 `visual_cue`，方便把选中的 hook 转成分镜第一镜或 B-roll 任务 |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | 多会话项目状态、scene/audio/status artifact 帮助 agent 继续视频项目 | 本项目继续保持本地 JSON/Markdown review artifact，不引入服务端状态或发布 API |

新增/调整能力：新增 [`scripts/hook_variants.py`](scripts/hook_variants.py)，可从 `transcript.json`、`clean_script.md`、`--topic` 和 `--persona` 生成 `hook_variants.v1`；内置 `pattern_interrupt`、`pain_question`、`number_map`、`contrast_turn`、`proof_first`、`time_box`、`mistake_fix`、`identity_lens` 8 类前三秒 hook；自动按平台限制长度，调用 `content_guard.scan_text()` 标记导流、极限词、医疗/财富等风险，输出推荐排序、visual cue、pacing、source terms 和 Markdown review 表。新增 [docs/prompts/62-hook-variants.md](docs/prompts/62-hook-variants.md)，更新 daily workflow、SKILL、提示词目录、README 和 `pipeline_manifest.py` artifact 类别。

使用方式：转写后先跑 `python3 scripts/hook_variants.py --transcript work/transcript.json --topic "AI剪辑" --persona "剪辑师" --platform xhs --output work/hook_variants.json --markdown work/hook_variants.md --strict`；打开 `work/hook_variants.md` 选择一个 `hook_XX`，把对应 `hook` 文本放进 `rewrite_script.py --emit-prompt` 生成的 LLM 提示，或直接替换 `clean_script.md` 的 `## Hook` 段。需要把这一步作为 review gate 时用 `python3 scripts/pipeline_manifest.py --project-dir . --target-stage analysis --require hook_variants --strict`。

验证结果：新增 `tests/test_hook_variants.py` 7 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_hook_variants.py tests/test_pipeline_manifest.py -q` 通过 `35 passed in 0.38s`；`.venv/bin/python scripts/hook_variants.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg hook_variants` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `461 passed in 4.61s`。

### 2026-07-06 自动化升级记录（Project Bootstrap）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 从“原始素材文件夹 → edit/final.mp4”开工，并把 `project.md` 作为跨会话记忆 | 新增 `project_bootstrap.py`，先生成项目目录、source inventory 和 `project.md` |
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 不直接编辑 original source，保存 working copy、timeline operations、session log 和 metadata | 默认 copy 到 `origin/`，保留外部 `source_path`，后续只在项目 working copy 上推进 |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | agent-safe workflow 强调 inspect/edit/verify、release checkpoint 和结构化结果 | `source_inventory.json` 作为第一个可审计 artifact，并接入 `pipeline_manifest.py --require source_inventory` |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | pipeline-driven 项目、reference/source 分析和 human approval gate 都落到项目文件 | 本项目先补轻量本地 bootstrap，不引入服务端或生成 provider |

新增/调整能力：新增 [`scripts/project_bootstrap.py`](scripts/project_bootstrap.py)，可从一个或多个素材文件/目录创建 `origin/`、`work/`、`output/`、`verify/`、`edit/`，按 raw/broll/audio/bgm/image/asset/sidecar 分类复制或 hardlink 素材，并输出 `project_bootstrap.v1` 的 `work/source_inventory.json`、`work/source_inventory.md`、`project.md` 和 `next_steps.md`。[`scripts/pipeline_manifest.py`](scripts/pipeline_manifest.py) 新增 `source_inventory` artifact 类别；需要把素材导入作为 analysis gate 时可 `--require source_inventory`。新增 [docs/prompts/61-project-bootstrap.md](docs/prompts/61-project-bootstrap.md)，并更新 README、SKILL 和提示词目录。

使用方式：新项目开工先运行 `python3 scripts/project_bootstrap.py --source ~/Downloads/raw-shoot --project-dir work/day61 --title "Day61 launch edit" --strict`；检查 `work/day61/work/source_inventory.md` 后，对主素材跑 `transcribe.py`，再用 `python3 scripts/pipeline_manifest.py --project-dir work/day61 --target-stage analysis --require source_inventory --strict` 确认项目入口和 transcript gate。默认 `--mode copy` 不改外部原始素材；同盘大素材可用 `--mode hardlink`，失败会回退 copy 并记录 warning。

验证结果：新增 `tests/test_project_bootstrap.py` 6 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_project_bootstrap.py tests/test_pipeline_manifest.py -q` 通过 `33 passed in 0.35s`；`.venv/bin/python scripts/project_bootstrap.py --help` 和 `.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg source_inventory` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `453 passed in 4.60s`。

### 2026-07-05 自动化升级记录（Takes Pack）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 把 `takes_packed.md` 作为 agent 主要阅读视图，并强调 audio-first、phrase-level cut decision | 新增本地 `takes_pack.py`，把多个 transcript 压成 phrase-level Markdown/JSON |
| [`Square-Zero-Labs/video-prompting-skill`](https://github.com/Square-Zero-Labs/video-prompting-skill) | video model routing、character sheet、scene-still handoff 做得完整 | 本项目已有 `video_prompt_pack.py`，本次不重复扩 provider prompt 层 |
| [`video-db/skills`](https://github.com/video-db/skills) | spoken/visual moments 可索引、可搜索、可回放 | 本项目保持本地优先，把多 take 先压成可搜索/可引用的 phrase artifact |
| [`GoogleCloudPlatform/vertex-ai-creative-studio` genmedia-video-editor](https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/blob/main/experiments/mcp-genmedia/skills/genmedia-video-editor/SKILL.md) | 把生成视频、overlay、concat、音画同步作为清晰工具面 | 本项目已有 overlay/render/audio sync，本次只补选段前阅读视图缺口 |

新增/调整能力：新增 `scripts/takes_pack.py`，可读取多份 `transcript.json`（支持 `label=path` 或 `--transcripts-dir`），按 word/segment 时间戳、静音 gap 和 speaker change 生成 `takes_packed.md` 与 `takes_pack.v1` JSON。`pipeline_manifest.py` 新增 `takes_pack` 可发现 artifact 类别，但默认不阻塞发布；需要强制多 take review 时可 `--require takes_pack`。新增 [docs/prompts/60-takes-pack.md](docs/prompts/60-takes-pack.md)，并更新 README、SKILL 和提示词目录。

使用方式：多 take 粗选前运行 `python3 scripts/takes_pack.py --transcript take1=work/take1_transcript.json --transcript take2=work/take2_transcript.json --output work/takes_packed.md --json work/takes_pack.json --break-gap 0.5`；让 agent/剪辑师按 `take1-003` 这类 phrase id 或源时间码挑选最佳表达，再进入 `highlight_picker.py`、`srt_edit_plan.py`、`render_config.json` 或 EDL/FCPXML/OTIO。

验证结果：新增 `tests/test_takes_pack.py` 5 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_takes_pack.py tests/test_pipeline_manifest.py -q` 通过 `31 passed in 0.33s`；`.venv/bin/python scripts/takes_pack.py --help` 和 `.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg takes_pack` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `446 passed in 4.02s`。

### 2026-07-04 自动化升级记录（Auto Emphasis Cues）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 把 context-aware auto emphasis effects 接到节奏、停顿、问句、数字 claim、转折和 payoff line | 新增本地 `auto_emphasis.py`，用确定性规则产出可复核 `emphasis_cues[]` |
| [`louisedesadeleer/b-roll-finder`](https://github.com/louisedesadeleer/b-roll-finder) | 强调用 word-level timestamps 把 cutaway 精确落在 spoken word 上，而不是只靠关键词 | `auto_emphasis.py` 优先读取 segment `words[]`，数字和转折词尽量锚到具体词时间戳 |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | effects / overlays / preflight guardrails 都作为结构化 agent 工具，而不是裸 FFmpeg 参数 | `render_final.py` 只消费 bounded cue 字段；`edit_preflight.py` 新增 `emphasis_cues[]` 校验 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | overlay animations、cut-boundary 自检和项目状态持久化都进入剪辑闭环 | `auto_enrich.py` 现在自动合并 emphasis；`pipeline_manifest.py` 可发现独立 `emphasis_plan.json` |

新增/调整能力：新增 [`scripts/auto_emphasis.py`](scripts/auto_emphasis.py)，可从 transcript 检测 `question_hook`、`numeric_claim`、`contrast_turn`、`payoff_line`、`risk_warning` 和 `pause_resume`，输出 `auto_emphasis_plan.v1` JSON + Markdown review；[`scripts/auto_enrich.py`](scripts/auto_enrich.py) 现在会把 `emphasis_cues[]` 放进综合 enrich plan；[`scripts/render_final.py`](scripts/render_final.py) 会把 cue 转成 timed ASS badge 和无红框的轻微 center push-in；[`scripts/edit_preflight.py`](scripts/edit_preflight.py) 会检查 emphasis cue 的时间、label、zoom 和坐标风险；[`scripts/pipeline_manifest.py`](scripts/pipeline_manifest.py) 会把 `emphasis_plan.json` 识别为 enrich artifact。新增 [docs/prompts/59-auto-emphasis.md](docs/prompts/59-auto-emphasis.md)，并更新 Auto-Enrich 文档、提示词索引、SKILL 和 README。

使用方式：单独生成强调计划用 `python3 scripts/auto_emphasis.py --transcript work/transcript.json --output work/emphasis_plan.json --markdown work/emphasis_plan.md --min-interval 3 --max-cues 12`；渲染前用 `python3 scripts/edit_preflight.py --config work/render_config.json --enrich-plan work/emphasis_plan.json --output work/edit_preflight.json --markdown work/edit_preflight.md --strict`；最终渲染加 `python3 scripts/render_final.py --config work/render_config.json --enrich-plan work/emphasis_plan.json --output output/master.mp4`。如果已经跑完整自动丰富，直接用 `auto_enrich.py --output work/enrich_plan.json`，其中会自动包含 `emphasis_cues[]`。

验证结果：新增 `tests/test_auto_emphasis.py` 4 项，并更新 `tests/test_auto_enrich.py`、`tests/test_render_enrich_plan.py`、`tests/test_edit_preflight.py` 和 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_auto_emphasis.py tests/test_auto_enrich.py tests/test_render_enrich_plan.py tests/test_edit_preflight.py tests/test_pipeline_manifest.py -q` 通过 `46 passed in 0.58s`；`.venv/bin/python scripts/auto_emphasis.py --help`、`.venv/bin/python scripts/auto_enrich.py --help`、`.venv/bin/python scripts/render_final.py --help` 和 `.venv/bin/python scripts/edit_preflight.py --help` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `440 passed in 4.96s`。

### 2026-07-03 自动化升级记录（OTIO NLE Handoff）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor/releases) | 近期发布记录把 `premiere-otio` / `.otio` 作为自动剪辑后的 NLE 交接路径之一 | 在已有 EDL/FCPXML 外新增本地 `export_otio.py`，复用同一份 edit event 解析 |
| [`AcademySoftwareFoundation/OpenTimelineIO`](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | OpenTimelineIO 是面向 editorial timeline 的开源 API 和交换格式 | 输出标准 OTIO JSON schema，不引入运行时重依赖 |
| [`tin2tin/VSE_OTIO_Export`](https://github.com/tin2tin/VSE_OTIO_Export) | Blender VSE 通过 `.otio` 交给 Resolve 等 NLE，说明轻量视频/音频 track 交接有实际价值 | 先支持连续 V1 + 可选 A1 track，保持复杂 overlay 仍由本项目 manifest 审计 |

新增/调整能力：新增 `scripts/export_otio.py`，可读取 `render_config.json`、`rough_cut.py` 或 `jump_cut.py` 的 `keep_segments`，输出 OpenTimelineIO `.otio` 和 `<output>.json` manifest。OTIO timeline 使用 `Timeline.1` / `Stack.1` / `Track.1` / `Clip.2` 结构，默认写 V1 视频 track 和 A1 音频 track；`--no-audio-track` 可只导出视频，`--include-transcript-metadata` 可把口播文本写进 clip metadata。`pipeline_manifest.py` 的 `nle_handoff` 类别新增 `.otio` / `.otio.json` 发现规则；README、SKILL、daily workflow 和 [docs/prompts/27-export-edl.md](docs/prompts/27-export-edl.md) 已同步为 EDL/FCPXML/OTIO 三种交接格式。

使用方式：从成片配置导出用 `python3 scripts/export_otio.py --config work/render_config.json --output work/day58_edit.otio --fps 30 --title DAY58_EDIT`；从粗剪 cut list 导出用 `python3 scripts/export_otio.py --cut-list work/rough_cut.json --output work/rough_cut.otio --fps 30 --include-transcript-metadata`。复杂字幕、overlay、章节卡、B-roll 和生成素材仍以 `render_final.py` / `export_capcut.py` / JSON manifest 为准；OTIO 只负责轻量选段时间线交接。

验证结果：新增 `tests/test_export_otio.py` 5 项，更新 `tests/test_pipeline_manifest.py` 覆盖 NLE handoff 发现；`.venv/bin/python -m pytest tests/test_export_otio.py tests/test_export_edl.py tests/test_export_fcpxml.py tests/test_pipeline_manifest.py -q` 通过 `40 passed in 0.40s`；`.venv/bin/python scripts/export_otio.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg nle_handoff` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `434 passed in 4.48s`。

### 2026-07-02 自动化升级记录（Source Receipts）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 把编辑会话和输出文件持久化，强调 agent 不依赖聊天上下文交付 | 新增独立 `source_receipts.v1` artifact，claim 和证据落盘后可复核 |
| [`Bomx/super-video-maker-skill`](https://github.com/Bomx/super-video-maker-skill) | source deck、timestamp、layout、technical QC 都作为质量 gate | 本项目补上 URL/截图 proof deck，和现有 publish gate 汇总 |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | structured guardrails / preflight checkpoint 避免 agent 直接产出不可审计结果 | `source_receipts.py --strict` 在缺 URL、截图或 primary source 时返回 2 |
| [`GoogleCloudPlatform/vertex-ai-creative-studio`](https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/tree/main/experiments/mcp-genmedia) | 生成视频工作流把 source、layout、media artifact 分开管理 | 本项目保持本地 JSON/Markdown/HTML，不引入 MCP server 或远程截图服务 |

新增/调整能力：新增 `scripts/source_receipts.py`，读取 `source_claims.json` 或 `--claim` 内联 claim，输出 `source_receipts.v1`、Markdown review 和可直接打开的 HTML source deck。每条 claim 支持 `text`、`source_url`、`source_title`、`source_type`、`screenshot/source_file`、`risk` 和 `timecode`；高风险 claim（news/data/finance/health/legal 等）必须有 URL，`--require-screenshot` 可强制本地截图/证据文件，`--require-primary-source` 可强制官方/primary/owned/government/academic 等来源类型。`pipeline_manifest.py` 新增 `source_receipts` artifact 类别，发现 `summary.blocking > 0` 会作为 publish blocker；新增 [docs/prompts/58-source-receipts.md](docs/prompts/58-source-receipts.md)，并更新 daily workflow、SKILL、提示词目录和 README。

使用方式：先写 `work/source_claims.json`，再跑 `python3 scripts/source_receipts.py --claims work/source_claims.json --project-dir . --output work/source_receipts.json --markdown work/source_receipts.md --html work/source_receipts.html --require-primary-source --strict`。事实型视频发布前可用 `python3 scripts/pipeline_manifest.py --project-dir . --target-stage publish_ready --require source_receipts --strict` 强制检查；纯观点类视频可跳过。

验证结果：新增 `tests/test_source_receipts.py` 8 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_source_receipts.py tests/test_pipeline_manifest.py -q` 通过 `31 passed in 0.31s`；`.venv/bin/python scripts/source_receipts.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg source_receipts` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `428 passed in 4.86s`。

### 2026-07-01 自动化升级记录（Review Dashboard）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`znyupup/ai-video-editing-skill`](https://github.com/znyupup/ai-video-editing-skill) | 自动 vlog workflow 把浏览器 Dashboard 作为“用户看一眼方案再出片”的确认点 | 新增静态 HTML/JSON review dashboard，不引入视觉 API 或服务端 |
| [`poseljacob/agentic-video-editor`](https://github.com/poseljacob/agentic-video-editor) | Director → Editor → Reviewer loop 会给 adherence、pacing、visual quality、watchability 打分并决定是否重试 | 本项目不自动重试创意判断，先把现有 gate/blocker 汇总成可复核队列 |
| [`laozuzhen/chatvideo-yucut`](https://github.com/laozuzhen/chatvideo-yucut) | Agent workflow 强调自动计划、执行、验证和修复，并有视觉验证/时间线界面 | 复用 `pipeline_manifest.py` 的本地 gate，生成一页 HTML 给人/agent 处理 blocker |
| [`lennoxsaint/eddy`](https://github.com/lennoxsaint/eddy) | 长流程有 simulation、proxy render、QA、judge、repair 和 launch kit gate | 本项目新增最终确认面板，和 `project_resume.py` 一起服务跨会话/自动化收尾 |

新增/调整能力：新增 `scripts/review_dashboard.py`，从项目目录扫描现有 artifacts，复用 `pipeline_manifest.py` 输出 `review_dashboard.v1` JSON 和可直接打开的 `review_dashboard.html`。它把 missing required、blocking 和 warning gate 排进 `review_items[]`，保留 `next_actions[]`、`latest_artifacts[]` 和完整 `gate_snapshot[]`；`pipeline_manifest.py` 新增 `review_dashboard` 可发现 artifact 类别但不把它作为发布 blocker。新增 [docs/prompts/57-review-dashboard.md](docs/prompts/57-review-dashboard.md)，并更新 README、SKILL 和提示词目录。

使用方式：发布确认或自动化收尾前运行 `python3 scripts/review_dashboard.py --project-dir work/day58 --target-stage publish_ready --output work/day58/review_dashboard.json --html work/day58/review_dashboard.html --strict`；需要额外要求音频/发布包 gate 时可重复加 `--require audio_master_report --require publish_package`；打开 HTML 后先处理 `Review Queue`，再按 `Next Actions` 补跑脚本。

验证结果：新增 `tests/test_review_dashboard.py` 4 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_review_dashboard.py tests/test_pipeline_manifest.py -q` 通过 `25 passed in 0.30s`；`.venv/bin/python scripts/review_dashboard.py --help` 和 `.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg review_dashboard` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `418 passed in 5.11s`。

### 2026-06-29 自动化升级记录（SRT Edit Plan）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 用 transcript 作为主要编辑界面，辅以 timeline review 和确认后执行 | 新增 SRT-first edit guide，把字幕编号编辑意见转成可审计 JSON/Markdown，不引入云端转写 |
| [`FireRedTeam/FireRed-OpenStoryline`](https://github.com/FireRedTeam/FireRed-OpenStoryline) | 支持 conversational refinement、resequence clips 和可复用 editing skill | 用普通 Markdown keep/drop 指令表达重排和删除，顺序可复用、可 review |
| [`iPythoning/ai-video-studio`](https://github.com/iPythoning/ai-video-studio) | Generate → Edit → Export 链路把生成和剪辑交付串起来 | 本项目保持本地 artifact-first，输出 `render_config` / cut list 交给现有渲染和 NLE handoff |
| [`linwownil/xmeml`](https://github.com/linwownil/xmeml) | 把 SRT/字幕时间数据用于剪辑软件 XML 交接 | 本次先把 SRT 时间码转成 `render_config` 和 source-time cut list，再复用现有 EDL/FCPXML |
| [`kyle95wm/srt-2-audacity`](https://github.com/kyle95wm/srt-2-audacity) | 把 SRT timing 转成编辑器可用 label track，适合精确人工复核 | Markdown review 保留字幕编号、时间、文本、keep/drop 理由，方便剪辑前核对 |

新增/调整能力：新增 `scripts/srt_edit_plan.py`，可读取 SRT 和人工/agent 写的 keep/drop 指令，输出 `srt_edit_plan.v1` JSON、`render_config.json`、按原素材时间排序的 cut list 和 Markdown review；支持 `title/platform/cover_style/profile` metadata，支持 `keep/include/use/select` 与 `drop/skip/exclude/remove`，`--require-all-reviewed --strict` 可把未复核字幕段作为 blocking。新增 [docs/prompts/55-srt-edit-plan.md](docs/prompts/55-srt-edit-plan.md)，并更新 README、SKILL 和提示词目录。

使用方式：先写 `work/edit_guide.md`，例如 `keep 3-5: 先用产品发布和用户反应`、`drop 1-2: 铺垫太慢`、`keep 8: 补一句核心结论`；再运行 `python3 scripts/srt_edit_plan.py --srt work/captions.srt --guide work/edit_guide.md --source-media origin/talking.mp4 --output work/srt_edit_plan.json --render-config work/render_config.json --cut-list work/srt_edit_cut.json --markdown work/srt_edit_plan.md --strict`。切点复核用 `timeline_view.py --cut-list work/srt_edit_cut.json`，最终渲染用 `render_final.py --config work/render_config.json`。

验证结果：新增 `tests/test_srt_edit_plan.py` 4 项；`.venv/bin/python -m pytest tests/test_srt_edit_plan.py tests/test_import_capcut_subtitles.py tests/test_subtitle_pack.py tests/test_export_edl.py tests/test_export_fcpxml.py -q` 通过 `24 passed in 0.24s`；`.venv/bin/python scripts/srt_edit_plan.py --help` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `406 passed in 4.42s`。

### 2026-06-28 自动化升级记录（Audio Master Report）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 把 FFmpeg 能力包装成 guardrailed tools，并已有 audio normalization / quality guardrails 模块 | 不引入 MCP server；新增只读 `audio_master_report.py`，把成片响度、true peak、LRA 和长静音变成可审计 gate |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 明确 audio-first 剪辑和 production-correctness hard rules，要求输出前自检 | 本项目已有 `render_qa.py`；本次补上 audio master 维度，避免“有音轨但不适合发布”的漏检 |
| [`donghaozhang/video-agent-skill`](https://github.com/donghaozhang/video-agent-skill) | AI video suite 把 transcribe / create-video / analyze-video 等能力做成 CLI，适合流水线组合 | 延续本项目 CLI artifact 风格，输出 JSON/Markdown 而不是隐藏在聊天上下文 |
| [`haidrrrry/claude-remotion-skill`](https://github.com/haidrrrry/claude-remotion-skill) | Remotion skill 把 captions、sound design 和 video render 放在同一创作面 | 本项目不扩大 Remotion 依赖，只给最终 master 增加声音质量报告 |

新增/调整能力：新增 `scripts/audio_master_report.py`，用 FFmpeg `ebur128=peak=true` 和 `silencedetect` 输出 `audio_master_report.v1` JSON/Markdown，检查 integrated LUFS、true peak、LRA 和长静音总量；默认门槛为 -16 LUFS ±2 LU、true peak ≤ -1 dBFS、LRA ≤ 18 LU、长静音总量 ≤ 3 秒。`pipeline_manifest.py` 新增 `audio_master_report` artifact 类别，发现 `summary.blocking > 0` 会作为 publish gate 阻塞。新增 [docs/prompts/54-audio-master-report.md](docs/prompts/54-audio-master-report.md)，并更新 daily workflow、提示词目录、SKILL 和 README。

使用方式：渲染和 `render_qa.py` 后运行 `python3 scripts/audio_master_report.py output/day58_master.mp4 --output output/day58_audio_master_report.json --markdown output/day58_audio_master_report.md --strict`。脚本只读最终文件，不重写媒体、不上传、不调用 provider；失败时优先回到 `render_final.py` 默认响度链路重新渲染，不要反复压缩已完成 master。

验证结果：新增 `tests/test_audio_master_report.py` 7 项，并更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_audio_master_report.py tests/test_pipeline_manifest.py -q` 通过 `26 passed in 0.24s`；`.venv/bin/python scripts/audio_master_report.py --help` smoke 通过；合成 1.5 秒 FFmpeg MP4 smoke 成功输出 JSON/Markdown，并正确把 -21.1 LUFS tone 标为 blocking；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `402 passed in 4.26s`。

### 2026-06-23 自动化升级记录（Edit Preflight Gate）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 把 FFmpeg 工具包装成 structured tools，并强调 preflight validation / risky edit parameter guardrails，避免 silent bad media output | 新增本地 `edit_preflight.py`，专注渲染前 artifact 预检，不引入 MCP server |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 把 production-correctness rules 和确认后再执行作为硬规则，减少 agent 直接改坏时间线 | preflight 在 `render_final.py` 前输出 JSON/Markdown review，供人/agent 先修再渲染 |
| [`video-db/skills`](https://github.com/video-db/skills) | 以 See → Understand → Act 的视频工作流提供搜索、编辑、字幕和导出能力，并返回可复核结果 | 本项目继续本地优先，只检查已有 `render_config` / `enrich_plan` / cut list |
| [`wizenheimer/vibestudio`](https://github.com/wizenheimer/vibestudio) | 透明、traceable 的本地 FFmpeg tool workflow，便于 agent 审计输入输出 | preflight 不解码、不渲染，只做结构、路径、时间和参数检查 |
| [`hiteshK03/video-production-skill`](https://github.com/hiteshK03/video-production-skill) | 视频生产 skill 教 agent 在多工具之间选择正确顺序和参数 | daily workflow 现在把 preflight 放在 content guard 后、render_final 前 |

新增/调整能力：新增 `scripts/edit_preflight.py`，输出 `edit_preflight.v1` JSON 和 Markdown，可检查 `render_config.clips[]` 非空、视频/图片/音频路径存在、direct `start/end` 或 `transcript + segment_id` 时间段合法、B-roll/image/PIP overlay 路径和时间线边界、focus 像素坐标是否缺 `source_width/source_height`、PIP `width_ratio/opacity/source_start` 等风险参数，以及 rough/jump cut `keep_segments[]`。`pipeline_manifest.py` 新增 `edit_preflight` artifact 类别；只要项目里已有 unresolved `edit_preflight.json` 且 `summary.blocking > 0`，manifest 会把它列为 blocking gate。新增 [docs/prompts/53-edit-preflight.md](docs/prompts/53-edit-preflight.md)，并更新 daily workflow、提示词目录、SKILL 和 README。

使用方式：渲染前跑 `python3 scripts/edit_preflight.py --config work/render_config.json --enrich-plan work/enrich_plan.json --output work/edit_preflight.json --markdown work/edit_preflight.md --strict`。如果有多个计划文件可重复传 `--enrich-plan`，如果要检查 rough/jump cut 结果可加 `--cut-list work/jump_cut.json`。脚本不会渲染、上传、下载或提交任何付费生成任务；渲染后仍需跑 `render_qa.py`。

验证结果：新增 `tests/test_edit_preflight.py` 7 项，并更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_edit_preflight.py tests/test_pipeline_manifest.py -q` 通过 `25 passed in 0.24s`；`.venv/bin/python scripts/edit_preflight.py --help` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `394 passed in 3.94s`。

### 2026-06-23 自动化升级记录（Jump Cut Audio Fades）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 把“每个 segment 边界加 30ms audio fade 防爆音”列为 production-correctness hard rule | `jump_cut.py` 默认给每个保留片段加 30ms fade-in/out，减少 concat 切点 pop |
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) | 自动静音剪辑支持 margin、audio/motion edit methods，并把 dead space removal 作为 first pass | 本项目保留现有自适应静音阈值和 `--pad`，只补缺失的音频边界处理，不引入复杂表达式 DSL |
| [`GoogleCloudPlatform/vertex-ai-creative-studio` genmedia-video-editor](https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/blob/main/experiments/mcp-genmedia/skills/genmedia-video-editor/SKILL.md) | 把视频生成、叠图、拼接、GIF、音视频同步拆成明确工具能力 | 本项目继续使用本地 FFmpeg CLI，把 fade 放进现有 single-pass filtergraph |
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/samuraigpt/ai-youtube-shorts-generator) | 长视频转短视频强调 Whisper、highlight ranking、auto crop 和 JSON 输出 | 本项目已有 highlight/reframe 路线，本次只修补最终 jump-cut 听感缺口 |
| [`jianchang512/pyvideotrans`](https://github.com/jianchang512/pyvideotrans) / [`krillinai/KrillinAI`](https://github.com/krillinai/KrillinAI) | 多语转写、翻译、配音和音画同步说明音频链路质量会直接影响发布体验 | 本次不新增配音服务，只确保自动去停顿成片的切点音频更稳 |

新增/调整能力：`scripts/jump_cut.py` 新增 `--fade-duration`，默认 `0.03` 秒。渲染时每个 `keep_segments[]` 音频片段会在同一个 `atrim/asetpts` filter chain 中追加 `afade=t=in` 和 `afade=t=out`，不增加中间文件、不改变单次 concat 编码模型。短片段会自动把 fade 限制到片段时长的一半；需要完全硬切原声时可传 `--fade-duration 0`。cut list JSON 新增 `fade_seconds` 字段，方便后续 EDL/FCPXML/复核时知道实际音频边界策略。README、SKILL、`docs/prompts/21-jump-cut.md`、`docs/prompts/15-xhs-daily-tech-video.md` 和提示词索引已同步。

使用方式：先审查切点仍用 `python3 scripts/jump_cut.py input/talking.mp4 --dry-run --cut-list output/talking.jumpcut.json`；确认后渲染用 `python3 scripts/jump_cut.py input/talking.mp4 --output output/talking.jumpcut.mp4 --cut-list output/talking.jumpcut.json --fade-duration 0.03`。如需旧行为，传 `--fade-duration 0`。

验证结果：新增/更新 `tests/test_jump_cut.py` 3 项覆盖默认 fade、关闭 fade、短片段 fade clamp；`.venv/bin/python -m pytest tests/test_jump_cut.py -q` 通过 `8 passed in 0.04s`；相关回归 `.venv/bin/python -m pytest tests/test_jump_cut.py tests/test_export_edl.py tests/test_export_fcpxml.py -q` 通过 `19 passed in 0.17s`；合成 1.2 秒 FFmpeg smoke 成功输出 MP4 且 cut list 含 `fade_seconds: 0.03`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；全量 `.venv/bin/python -m pytest tests -q` 通过 `386 passed in 4.41s`。

### 2026-06-21 自动化升级记录（Project Resume Handoff）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | `project.json` 跟踪 scenes/audio/sessions/phase，并把计划与实际文件 reconcile 后生成项目级 `CLAUDE.md` | 新增本地 `project_resume.py`，复用现有 gate，不引入项目数据库 |
| [`HKUDS/ViMax`](https://github.com/HKUDS/ViMax) | Agent Loop + TUI 支持 session resume、context compaction 和工作目录 artifacts | 输出 `project_resume.v1` + Markdown，让压缩上下文后的 agent 先读文件状态 |
| [`SamurAIGPT/Generative-Media-Skills`](https://github.com/SamurAIGPT/Generative-Media-Skills) | agent-native CLI、结构化 JSON 输出、semantic exit codes 和 recipe 化工作流 | `project_resume.py --strict` 在 blocked stage 返回 2，适合自动化收尾 |
| [`JossBen/mcp-video-editing-assistant`](https://github.com/JossBen/mcp-video-editing-assistant/blob/master/CLAUDE.md) | 视频编辑助手把 timeline/workflow 学习状态持久化到 JSON | 本项目继续只读本地 artifacts，生成可恢复 handoff，不绑定 Resolve/MCP |
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/samuraigpt/ai-youtube-shorts-generator) | LLM highlight detection、Whisper 和 auto-crop 证明长视频生产链需要可恢复状态 | 本项目已有 highlight/render/publish artifacts，本次补跨会话续跑入口 |

新增/调整能力：新增 `scripts/project_resume.py`，可扫描项目目录并复用 `pipeline_manifest.py` 的 gate，输出 `project_resume.v1`、Markdown resume 和可选项目级 `CLAUDE.md`。内容包括 `phase`、`recommended_first_action`、`next_actions[]`、`latest_artifacts[]`、ready artifacts、关键 gate snapshot、guardrails 和可直接交给下一位 agent 的 `suggested_prompt`。新增 [docs/prompts/52-project-resume.md](docs/prompts/52-project-resume.md)，更新 daily workflow、提示词目录、SKILL 和 README。另将 `SKILL.md` frontmatter 规范化：移除旧的顶层 `argument-hint`，并把过长 description 压缩到 skill 校验器允许范围。

使用方式：自动化收尾或跨会话接手前跑 `python3 scripts/project_resume.py --project-dir work/day58 --target-stage publish_ready --output work/day58/project_resume.json --markdown work/day58/project_resume.md --agent-note work/day58/CLAUDE.md --strict`。如果只想写默认项目级 agent note，可用 `--agent-note` 不带路径，脚本会写到 `--project-dir/CLAUDE.md`。脚本不渲染、不上传、不提交任何付费生成任务。

验证结果：新增 `tests/test_project_resume.py` 5 项；`.venv/bin/python -m pytest tests/test_project_resume.py tests/test_pipeline_manifest.py -q` 通过 `22 passed in 0.27s`；`.venv/bin/python scripts/project_resume.py --help` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python /Users/maxazure/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/maxazure/projects/video-editing-skill` 通过 `Skill is valid!`；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `384 passed in 4.40s`。

### 2026-06-20 自动化升级记录（PIP Overlay）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`tadaspetra/loop`](https://github.com/tadaspetra/loop) | 录屏工具把 screen、microphone、optional camera、实时 transcript 和 PIP camera preview 放在同一编辑闭环里 | 新增本地 PIP overlay plan，不引入 Electron/录屏端依赖 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 强调 audio-primary、transcript-driven edits，并把 overlay animations 当成 agent 可执行能力 | PIP 继续挂在 transcript/render_config 驱动的 `render_final.py --enrich-plan` |
| [`GoogleCloudPlatform/vertex-ai-creative-studio` genmedia-video-editor](https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/blob/main/experiments/mcp-genmedia/skills/genmedia-video-editor/SKILL.md) | 把 image/video overlay 坐标、尺寸和 ffmpeg compositing 做成明确工具能力 | 本项目新增 timed video PIP overlay，保留单次编码原则 |
| [`FireRedTeam/FireRed-OpenStoryline`](https://github.com/FireRedTeam/FireRed-OpenStoryline) | natural-language editing agent 强调 human-in-the-loop 和可复用 style/skills | PIP 先生成 JSON/Markdown review，再由用户/agent 复核后渲染 |
| [`bilibili/carocut`](https://github.com/bilibili/carocut) | 多 agent + Remotion 的视频制作助手说明 creator workflow 需要可组合的画面层 | 本项目仍用轻量 CLI artifact，不把 Remotion 变成 PIP 必需依赖 |

新增/调整能力：新增 `scripts/pip_overlay.py`，可把 facecam/camera 视频转成 `pip_overlay_plan.v1`，输出 `pip_overlays[]` 与 Markdown 复核表；支持多段 `--segment "start,end[,position]"`、`--sync-offset`、`--source-start`、`--width-ratio`、`--margin-ratio`、`--opacity` 和 `--transition`。`render_final.py --enrich-plan` 现在会合并 `pip_overlays[]`，把 camera 小窗作为 timed video overlay 接入 B-roll/image/focus 之后、字幕之前，并在 `--primary-speed` / `--speed` 输出中同步压缩 PIP 时间线；camera audio 默认忽略，避免污染主口播/BGM 音频链路。

使用方式：先跑 `python3 scripts/pip_overlay.py --camera origin/facecam.mp4 --segment "0,18,bottom_right" --segment "18,42,top_right" --sync-offset 0.18 --output work/pip_overlay_plan.json --markdown work/pip_overlay_plan.md`；渲染时重复传入 enrich plan，例如 `python3 scripts/render_final.py --config work/render_config.json --enrich-plan work/enrich_plan.json --enrich-plan work/screen_focus_plan.json --enrich-plan work/pip_overlay_plan.json --output output/tutorial_master.mp4`。详细说明见 [docs/prompts/51-pip-overlay.md](docs/prompts/51-pip-overlay.md)。

验证结果：新增 `tests/test_pip_overlay.py` 8 项；`.venv/bin/python -m pytest tests/test_pip_overlay.py tests/test_render_enrich_plan.py tests/test_screen_focus.py -q` 通过 `19 passed in 0.26s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/pip_overlay.py --help` 和 `.venv/bin/python scripts/render_final.py --help` smoke 验证通过；2 秒 FFmpeg 变速合成 smoke 验证 `pip_overlays[]` 可渲染为有效 MP4；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `379 passed in 4.01s`。

### 2026-06-19 自动化升级记录（Prompt-Based Highlight Picker）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`SamurAIGPT/Clip-Anything`](https://github.com/SamurAIGPT/Clip-Anything) | “describe what you want” 的 prompt-based clipping 很适合从长视频定向找时刻 | 在已有 `highlight_picker.py` 中新增 `--brief/--query`，不引入外部 API |
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/samuraigpt/ai-youtube-shorts-generator) | 长视频转短视频输出 highlights/score/hook/reason JSON，并强调 highlight selection criteria | 本项目已有 score/hook/reason；本次补 brief relevance 和 `brief_match` 字段 |
| [`gyoridavid/short-video-maker`](https://github.com/gyoridavid/short-video-maker) | 面向 TikTok/Reels/Shorts 的 MCP + REST 自动生成链路，说明 agent/API 双接口对视频生产有价值 | 本项目继续保持 CLI artifact-first；`--render-config` 直接交给 `render_final.py` |
| [`Anil-matcha/AI-B-roll`](https://github.com/Anil-matcha/AI-B-roll) | 用 AI B-roll 增强短视频可看性，强调按内容补画面 | 本项目已有 `auto_enrich.py` / `storyboard_assets.py`，本次不新增付费 B-roll API |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | 把 voiceover、music、image/video generation 和 review 工具拆成 agent 可调用脚本 | 本项目已有音频 cue、视频 prompt pack 和生成任务台账，本次只补定向择段缺口 |

新增/调整能力：`scripts/highlight_picker.py` 新增 `--brief` / `--query`，可把“产品发布 用户反应”“find the product reveal”这类自然语言意图拆成英文关键词和中文短语片段，并把 `brief_match.score` 纳入原有 hook/value/duration/completeness 打分。输出 JSON 和 Markdown 会显示 `brief_match.matched_terms`、`score_breakdown.brief` 和弱匹配 warning；`--render-config` 也会保留 `brief_match` 供后续渲染/复核。

使用方式：常规自动找精华仍用 `python3 scripts/highlight_picker.py --transcript work/long_transcript.json --output work/highlights.json --markdown work/highlights.md --platform douyin --strict`；定向找片段用 `python3 scripts/highlight_picker.py --transcript work/long_transcript.json --brief "产品发布 用户反应 价格对比" --video origin/long-talk.mp4 --output work/brief_highlights.json --markdown work/brief_highlights.md --render-config work/brief_render_config.json --platform douyin --num-clips 3 --strict`。详细说明见 [docs/prompts/31-highlight-picker.md](docs/prompts/31-highlight-picker.md)。

验证结果：新增/更新 `tests/test_highlight_picker.py` 2 项 brief/query 覆盖；`.venv/bin/python -m pytest tests/test_highlight_picker.py -q` 通过 `9 passed in 0.07s`；`.venv/bin/python scripts/highlight_picker.py --help` smoke 验证 `--brief/--query` 参数正常；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；完整 `.venv/bin/python -m pytest tests -q` 通过 `371 passed in 3.71s`。

### 2026-06-18 自动化升级记录（FCPXML NLE Handoff）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) | 自动剪辑后可导出 Premiere / Resolve / Final Cut Pro / Shotcut / Kdenlive 时间线 | 在已有 EDL handoff 外补 FCPXML，延续同一份剪辑计划 |
| [`leeyc09/Silence-Cutter`](https://github.com/leeyc09/Silence-Cutter) | 静音移除后直接输出 FCPXML，并强调 word-boundary / 字幕同步 | 本项目继续使用已有 rough/jump cut list；FCPXML 只做非破坏式 NLE 交接 |
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | typed shorts/edit plan、质量门禁、候选片段评分和 NLE handoff 思路清晰 | 复用本项目 manifest / QA / timeline_view gate，不新增云端依赖 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | transcript-first、EDL、render self-eval 的 agent 剪辑循环 | FCPXML 继续从 transcript/render_config/cut-list artifact 派生，保持可审计 |
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 把 FFmpeg、字幕、质量检查、repurpose package 包装为 typed/guardrailed 工具 | 本项目保持脚本式接口，但为 NLE handoff 增加更强格式覆盖 |

新增/调整能力：新增 `scripts/export_fcpxml.py`，可读取 `render_config.json`、`rough_cut.py` 或 `jump_cut.py` 的 `keep_segments`，生成单 spine FCPXML 和 `<output>.json` manifest；支持 `--fps`、`--width`、`--height`、`--title`、`--source` 和 `--fcpxml-version`。`export_edl.py` 保留，二者共享同一套 segment/event 解析。

使用方式：从成片配置导出用 `python3 scripts/export_fcpxml.py --config work/render_config.json --output work/day58_edit.fcpxml --fps 30 --width 1080 --height 1920`；从粗剪 cut list 导出用 `python3 scripts/export_fcpxml.py --cut-list work/rough_cut.json --output work/rough_cut.fcpxml --fps 30`。详细说明见 [docs/prompts/27-export-edl.md](docs/prompts/27-export-edl.md)。

验证结果：新增 `tests/test_export_fcpxml.py` 4 项；`.venv/bin/python -m pytest tests/test_export_fcpxml.py -q` 通过 `4 passed in 0.06s`；相关回归 `.venv/bin/python -m pytest tests/test_export_fcpxml.py tests/test_export_edl.py tests/test_pipeline_manifest.py -q` 通过 `28 passed in 0.29s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `369 passed in 4.76s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过。

### 2026-06-17 自动化升级记录（CapCut Subtitle Import）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`mrbuslov/capcut-ai-editor`](https://github.com/mrbuslov/capcut-ai-editor) | 直接读取 CapCut 自动字幕，用字幕间隙和重复表达做 talking-head 智能粗剪 | 新增本地反向导入，不直接改草稿；输出 transcript 和可复核 cut list |
| [`danyfernandes/capcut-srt-extractor-python`](https://github.com/danyfernandes/capcut-srt-extractor-python) | 从 CapCut 工程取回 SRT，解决剪映字幕难交付的问题 | 支持草稿 `draft_content.json` 和外部 SRT 两种入口 |
| [`mutonby/openshorts`](https://github.com/mutonby/openshorts) | 长视频切短视频流水线重视 Auto Subtitles、hook overlays 和发布前资产交付 | 本项目保持本地 transcript/artifact 交付，不接入云端发布 API |
| [`JiamanJemma/video-post-production-kit`](https://github.com/JiamanJemma/video-post-production-kit) | talking-head + screen recording 后期流程强调字幕校对和多轨可复核 | 新增 Markdown review + gap cut list，先复核再渲染/交给 NLE |
| [`jurczykpawel/reelstack`](https://github.com/jurczykpawel/reelstack) | 把外部生成器/编辑器当作内容来源，生产线负责统一 timing、caption、branding | 剪映可作为字幕校对入口，回流后继续走本项目统一 pipeline |

新增/调整能力：新增 `scripts/import_capcut_subtitles.py`，可从剪映/CapCut 草稿目录、`draft_content.json` 或 SRT 导入字幕，输出兼容本项目的 `capcut_subtitle_transcript`；可选 `--cut-list` 会按字幕间隙生成 `keep_segments`，供 `timeline_view.py`、`export_edl.py` 或后续粗剪流程复核；默认只导入 subtitle 材料，避免封面标题/贴纸文字混入 transcript，必要时用 `--include-overlays` 兜底。

使用方式：从剪映草稿导入用 `python3 scripts/import_capcut_subtitles.py --draft ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/day58 --transcript work/capcut_transcript.json --cut-list work/capcut_gap_cut.json --markdown work/capcut_subtitles.md --source-media origin/talking.mp4`；从 SRT 导入用 `python3 scripts/import_capcut_subtitles.py --srt exports/capcut_auto_captions.srt --transcript work/capcut_transcript.json --srt-output output/subtitles/capcut_clean.srt`。详细示例见 [docs/prompts/50-import-capcut-subtitles.md](docs/prompts/50-import-capcut-subtitles.md)。

验证结果：新增 `tests/test_import_capcut_subtitles.py` 5 项；`.venv/bin/python -m pytest tests/test_import_capcut_subtitles.py -q` 通过 `5 passed in 0.05s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `365 passed in 4.87s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；`.venv/bin/python scripts/import_capcut_subtitles.py --help` smoke 验证 CLI 参数正常。

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

### 📈 Retention Rhythm QA — 成片留存节奏风险审计
[`scripts/retention_rhythm_qa.py`](scripts/retention_rhythm_qa.py) · [详细文档](docs/prompts/69-retention-rhythm-qa.md)

`render_qa.py` 能发现信号和容器事故，但不会判断前三秒是否完全没有变化、镜头是否拖得过久、切点是否机械等距或字幕是否长时间不刷新。`retention_rhythm_qa.py` 对**已渲染 master / platform export**运行 FFmpeg scene detection，并可合并与成片速度、片头 offset 对齐的 `subtitle_pack.v1` JSON，输出 `retention_rhythm_qa.v1` JSON / Markdown。

常用：
```bash
python3 scripts/retention_rhythm_qa.py output/day58_master.mp4 \
  --timed-text output/subtitles/day58_master.json \
  --output verify/retention_rhythm_qa.json \
  --markdown verify/retention_rhythm_qa.md \
  --strict
```

默认检查前三秒 scene/subtitle attention event、6 秒以上视觉 hold、10 秒以上严重长镜头、scene + subtitle 的 combined attention gap、镜头时长 CV、0.35 秒以下快切 burst、4.5 秒以上字幕 hold 和 1.5 秒以上无字幕区间。`inactive_hook` 在没有 timed text 时只警告，避免把持续运镜/kinetic text 误判成硬失败；timed text 也无变化、视觉 hold 超过 10 秒或 combined attention gap 超过 10 秒时会进入 `summary.blocking`。这只是可观测的节奏风险，不预测真实留存率或“爆款概率”。报告存在且 blocking 非零时，`pipeline_manifest.py` 会阻塞；要强制具备报告可加 `--require retention_rhythm_qa`。

### 🎞️ Reference Edit Rhythm — 参考片剪辑结构量化
[`scripts/reference_edit_rhythm.py`](scripts/reference_edit_rhythm.py) · [详细文档](docs/prompts/92-reference-edit-rhythm.md)

当客户给出参考广告/短片并说“照这个节奏”时，不再靠肉眼猜。脚本用同一套 FFmpeg hard scene detection 量化参考片和候选片，比较 cuts/minute、median shot、final-hold 比例、归一化 cut positions 与 opening/middle/closing cut share，并为两条视频生成 hash-bound contact sheets。

```bash
python3 scripts/reference_edit_rhythm.py analyze \
  --project-dir . \
  --reference origin/reference-ad.mp4 \
  --candidate output/final.mp4 \
  --evidence-dir verify/reference_edit_rhythm \
  --output work/reference_edit_rhythm.json \
  --markdown work/reference_edit_rhythm.md \
  --strict

python3 scripts/reference_edit_rhythm.py verify \
  --report work/reference_edit_rhythm.json \
  --strict
```

默认结构差异只 WARN，避免为了追数字机械加切点；明确把节奏匹配设为验收条件时才加 `--require-match`。`verify` 会现场检查参考片、候选片、两张 contact sheet、媒体契约、全部派生 metrics/comparison/summary 和 canonical report id。任何重编码、替换、证据变化或手改报告都会使旧结果失效。它只允许借鉴结构，不复制参考片 pixels/audio/branding/story；scene score 会漏掉部分 dissolve 与镜头内动作，两张 contact sheet 和 1× 完整播放都必须人工复核。`pipeline_manifest.py --require reference_edit_rhythm --strict` 可设为发布门禁。

### 🗣️ Speech Continuity QA — 成片复读 / 口吃门禁
[`scripts/speech_continuity_qa.py`](scripts/speech_continuity_qa.py) · [详细文档](docs/prompts/67-speech-continuity-qa.md)

`render_qa.py` 和 waveform 能检查信号，却不能判断“上一句结尾是否又在下一句开头说了一遍”。`speech_continuity_qa.py` 读取**已渲染 master 的二次 transcript**，检测切点精确复读、相邻近重复 take 和句内即时口吃，输出 `speech_continuity_qa.v1` JSON / Markdown；不同 speaker 默认不互判，减少访谈误报。

常用：
```bash
python3 scripts/extract_audio.py output/day58_master.mp4
python3 scripts/transcribe.py output/day58_master_audio.wav --model auto --language zh --word-timestamps
python3 scripts/speech_continuity_qa.py output/day58_master_transcript.json \
  --output verify/speech_continuity_qa.json \
  --markdown verify/speech_continuity_qa.md \
  --strict
```

命中项会写入精确成片时间范围、重复文本、segment evidence 和修复建议；`--strict` 返回 2。先试听 master 并用 `timeline_view.py --at <seconds>` 看切点，再回到源 `render_config` / cut list 重渲染，避免在成片上二次拼补。只要报告存在且 `summary.blocking > 0`，`pipeline_manifest.py` 会阻塞；发布流程要强制具备此报告时加 `--require speech_continuity_qa`。

### 🎚️ Audio Master Report — 成片响度发布门禁
[`scripts/audio_master_report.py`](scripts/audio_master_report.py) · [详细文档](docs/prompts/54-audio-master-report.md)

借鉴 agent 视频工具对 audio-first correctness 和 render 后可审计报告的重视，但不做二次压缩：`render_final.py` 仍负责默认响度链路，`audio_master_report.py` 只读最终 master，用 FFmpeg `ebur128` / `silencedetect` 输出 JSON + Markdown。

常用：
```bash
python3 scripts/audio_master_report.py output/day58_master.mp4 \
  --output output/day58_audio_master_report.json \
  --markdown output/day58_audio_master_report.md \
  --strict
```

默认检查 -16 LUFS ±2 LU、true peak ≤ -1 dBFS、LRA ≤ 18 LU、长静音总量 ≤ 3 秒。输出 `audio_master_report.v1`，如果 `summary.blocking > 0`，`pipeline_manifest.py` 会把它列为 blocking gate。若失败，优先回到 `render_final.py` 默认响度链路重新渲染，不要反复压缩已完成 master。

### 🧾 Source Receipts — 事实来源 proof deck
[`scripts/source_receipts.py`](scripts/source_receipts.py) · [详细文档](docs/prompts/58-source-receipts.md)

借鉴 proof-backed video skill 对 source deck 的要求，但保持本地优先：脚本只验证你提供的 claim、URL 和本地截图/证据文件，输出 `source_receipts.v1`、Markdown review 和可直接打开的 HTML source deck，不联网抓取、不截图、不上传。

常用：
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

`source_claims.json` 中每条 claim 可写 `text`、`source_url`、`source_title`、`source_type`、`screenshot/source_file`、`risk` 和 `timecode`。新闻、数据、金融、健康、法律等高风险 claim 必须有 URL；需要视觉 proof card 时加 `--require-screenshot`。`pipeline_manifest.py --require source_receipts --strict` 会把未解决的 `summary.blocking` 列为发布 blocker。

### 📦 多平台导出
[`scripts/multi_export.py`](scripts/multi_export.py) · [详细文档](docs/prompts/17-multi-platform.md)

| 平台 | 尺寸 | 时长 | 说明 |
|---|---|---|---|
| 小红书 / RED | 1080×1440 (3:4) | — | 占满 feed 缩略图 (+40% 显示面积) |
| 抖音 / TikTok | 1080×1920 (9:16) | — | 全屏沉浸 |
| 微信视频号 | 1080×1920 (9:16) | ≤60s | 自动截断；社交链分发 |

### 📦 Delivery Encode — source-bound 目标大小交付
[`scripts/delivery_encode.py`](scripts/delivery_encode.py) · [详细文档](docs/prompts/85-delivery-encode.md)

对“视频必须小于 20 MiB”这类硬上传限制，不再靠 CRF 反复试猜。`plan` 会读取源片时长、音视频流、旋转后尺寸和帧率，预留 6% 容器安全余量，再为 libx264 两遍编码分配视频码率。`apply` 先写同目录临时 MP4，硬大小、容器、codec、尺寸、fps、时长、音轨、像素格式和全长解码都通过后才原子提升。

```bash
python3 scripts/delivery_encode.py plan output/master.mp4 \
  --delivery output/master-under-20m.mp4 \
  --max-size-mib 20 \
  --output work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md

python3 scripts/delivery_encode.py apply work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md

python3 scripts/delivery_encode.py verify work/delivery_encode_plan.json --strict
```

可选 `--max-width` / `--max-height` 保持画幅比缩小，`--fps` 只允许不高于源片。交付编码是最后一次重编码，不能替代人工观感审片；交付 MP4 还应重跑 `render_qa.py`、字幕/平台安全区检查和 `approval_receipt.py`。

### ✍️ Caption Generator — 标题 + 正文 + 标签
[`scripts/generate_caption.py`](scripts/generate_caption.py)

无 LLM 依赖，纯规则：
- 标题 ≤18 字，前 18 字含 2 个 TF-IDF 关键词
- 正文 200-500 字，每 ~60 字一个 emoji（`📌✨💡🔥👇✅🚀📈`）
- 3-6 个 # tag，混合垂类 + 长尾（避免纯热词堆叠被判搬运）
- 发布时段建议来自所选 audience profile

### 🖼️ Cover Variants — 封面 A/B 方案与选择
[`scripts/cover_variants.py`](scripts/cover_variants.py) · [详细文档](docs/prompts/68-cover-variants.md)

借鉴同类视频 / creator skill 把 3 套缩略图、标题—封面信息分工和移动端小图检查做成一等流程的优点，但保持本项目本地优先：复用现有 `generate_cover_image.py` 的 Chrome 模板，不调用图片模型或外部 API。

常用：
```bash
python3 scripts/cover_variants.py \
  output/day68_master_xhs.mp4 \
  --title "20分钟出片" \
  --subtitle "AI剪辑完整流程" \
  --caption output/day68_caption.json \
  --platform xhs \
  --frame-timestamp 12.5 \
  --output-dir output/covers \
  --render \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md
```

默认生成 `cover-a` 主方案、`cover-b` 对比色 / 层级方案和 `cover-c` 真实画面证据方案，并为每张图生成 feed-size `*_preview.png`。看完后用 `--select cover-c --require-selection --strict` 记录最终选择；`publish_package.py` 会优先读取 `selected_cover`，`pipeline_manifest.py --require cover_variants` 可把封面选择设为发布 gate。需要自定义 AI 底图时，生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

### 🔏 Approval Receipt — 最终审批收据
[`scripts/approval_receipt.py`](scripts/approval_receipt.py) · [详细文档](docs/prompts/77-approval-receipt.md)

人工完整审片并核对封面、文案、字幕和 QA 后，把稳定交付件显式列入 SHA-256 收据：

```bash
python3 scripts/approval_receipt.py create \
  --project-dir . \
  --artifact output/day77_xhs.mp4 \
  --artifact output/day77_douyin.mp4 \
  --artifact output/day77_wxch.mp4 \
  --artifact output/cover.png \
  --artifact output/day77_caption.json \
  --artifact verify/render_qa.json \
  --approved-by "Jay" \
  --note "三平台视频、封面、文案和字幕已人工复核。" \
  --output verify/approval_receipt.json \
  --markdown verify/approval_receipt.md

python3 scripts/approval_receipt.py verify \
  --project-dir . \
  --receipt verify/approval_receipt.json \
  --output verify/approval_receipt_verification.json \
  --strict
```

收据只保存项目相对路径、大小、修改时间和 SHA-256，不复制或锁定视频。任何文件后来被重渲染、替换、删除、改成 symlink 或在哈希期间变化，验证会输出 `changed` / `missing` / `unsafe` 并在 strict 模式返回 2。项目里存在收据时，`pipeline_manifest.py` 会实时重算最新一份并自动阻塞过期审批；`--require approval_receipt` 可强制必须有收据。`publish_package.py --require-approval-receipt` 即使拿到旧 pipeline manifest 也会独立验证当前文件。`approved_by` 只是本地自报标签，不是身份认证或数字签名；不要把会反复重写的 pipeline manifest、publish package 或 dashboard 放进收据。

### 📤 Publish Package — 最终上传包
[`scripts/publish_package.py`](scripts/publish_package.py) · [详细文档](docs/prompts/49-publish-package.md)

借鉴 `vidpipe` / `OpenShorts` / `youtube-shorts-pipeline` 这类项目的发布队列和多平台分发思路，但保持本项目本地优先：不登录平台、不调用上传 API，只把发布必需物料和 gate 状态整理成可审计的 JSON + Markdown。

常用：
```bash
python3 scripts/publish_package.py \
  --project-dir work/day58 \
  --platforms xhs douyin wxch \
  --require-approval-receipt \
  --output work/day58/publish_package.json \
  --markdown work/day58/publish_package.md \
  --strict
```

输出 `publish_package.v1`，包含每个平台的 MP4、封面图、SRT/VTT、标题、正文、tags、发布时间建议、上传 checklist、章节文本、`pipeline_manifest` 阻塞状态和 approval receipt 实时验证状态。若项目有 `cover_variants.json` 且 `selected_cover` 文件存在，会优先采用已复核封面；显式 `--cover` 仍可覆盖。`--strict` 会在缺少平台视频、caption 不完整、已有 gate blocked 或收据过期时返回 2；`--require-approval-receipt` 还会在没有收据时阻塞。`pipeline_manifest.py` 也会识别 `publish_package.json` 并把 `summary.blocking > 0` 列为 blocking gate。

### 🧭 Project Resume — 续跑上下文包
[`scripts/project_resume.py`](scripts/project_resume.py) · [详细文档](docs/prompts/52-project-resume.md)

借鉴 agent-native 视频工具的 project state / resume note 思路，但保持本项目本地 artifact-first：复用 `pipeline_manifest.py` 的 gate 判断，把状态、阶段、缺件、最近 artifacts 和下一步动作整理成可交给下一位 agent 的 JSON + Markdown。

常用：
```bash
python3 scripts/project_resume.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/project_resume.json \
  --markdown work/day58/project_resume.md \
  --agent-note work/day58/CLAUDE.md \
  --strict
```

输出 `project_resume.v1`，包含 `phase`、`recommended_first_action`、`next_actions[]`、`latest_artifacts[]`、关键 gate snapshot 和一句 `suggested_prompt`。`--agent-note` 可写出项目级 `CLAUDE.md`；不传路径时默认写到 `--project-dir/CLAUDE.md`。脚本不渲染、不上传、不提交任何生成任务，适合自动化收尾、上下文压缩后续跑和跨 agent 交接。

### 🧾 Review Dashboard — 人工复核面板
[`scripts/review_dashboard.py`](scripts/review_dashboard.py) · [详细文档](docs/prompts/57-review-dashboard.md)

借鉴自动 vlog 剪辑和 agentic editor 里的浏览器预览 / reviewer loop，但保持本项目静态、可审计：不启动服务、不调用模型，只把本地 artifacts、blocking gates、warning 和下一步动作整理成 HTML + JSON。

常用：
```bash
python3 scripts/review_dashboard.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/review_dashboard.json \
  --html work/day58/review_dashboard.html \
  --strict
```

输出 `review_dashboard.v1`，包含 `review_state`、`review_items[]`、`next_actions[]`、`latest_artifacts[]` 和完整 `gate_snapshot[]`。HTML 可直接在浏览器打开，适合最终发布前让用户看一眼，也适合自动化结束时把 blocker 留给下一位 agent。

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

# 0b. 可选：手持素材防抖；只写 working copy，完整 A/B 确认前 manifest 会阻塞
python3 $SKILL/scripts/video_stabilization.py plan $WORK/origin/handheld.mp4 \
  --decision stabilize \
  --reviewed-by "editor" \
  --note "不想要的高频手抖，不是有意运镜" \
  --output $WORK/work/video_stabilization_plan.json \
  --markdown $WORK/work/video_stabilization_plan.md
python3 $SKILL/scripts/video_stabilization.py apply $WORK/work/video_stabilization_plan.json \
  --output $WORK/work/handheld-stabilized.mp4 \
  --comparison $WORK/verify/handheld-stabilization-compare.mp4 \
  --markdown $WORK/work/video_stabilization_plan.md
# 用 1× 看完整 comparison 后：
python3 $SKILL/scripts/video_stabilization.py confirm $WORK/work/video_stabilization_plan.json \
  --reviewed-by "editor" \
  --note "完整 A/B 已看；人物、边缘和有意摇摄均可接受" \
  --markdown $WORK/work/video_stabilization_plan.md

# 1. 转写
python3 $SKILL/scripts/transcribe.py $WORK/origin/voice.mp3 \
  --word-timestamps --detect-fillers

# 1a. 生成本地同步视频校稿页；保存 review.txt 后回写 reviewed transcript
python3 $SKILL/scripts/transcript_review.py html \
  --transcript $WORK/work/transcript.json \
  --video $WORK/origin/voice.mp3 \
  --output $WORK/work/transcript_review.html
python3 $SKILL/scripts/transcript_review.py apply \
  --transcript $WORK/work/transcript.json \
  --review $WORK/work/transcript_review.txt \
  --output $WORK/work/transcript_reviewed.json

# 1b. 可选：按 reviewed ASR 去纯口头禅/重复句，先审查 cut list 再渲染
python3 $SKILL/scripts/rough_cut.py \
  --transcript $WORK/work/transcript_reviewed.json \
  --cut-list $WORK/work/rough_cut.json

# 2. 重组（手动喂 prompt 给 LLM，落地 JSON 后回放）
python3 $SKILL/scripts/rewrite_script.py \
  --transcript $WORK/work/transcript_reviewed.json --emit-prompt > $WORK/work/prompt.md
# ...LLM 输出 work/llm.json 后...
python3 $SKILL/scripts/rewrite_script.py \
  --transcript $WORK/work/transcript_reviewed.json \
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

# 3c. 可选：把分镜转成 Dreamina/Veo/LTX/Wan/Sora 视频生成提示词包
python3 $SKILL/scripts/video_prompt_pack.py \
  --storyboard-plan $WORK/work/storyboard_plan.json \
  --asset-root $WORK/work \
  --style-reference $WORK/work/imagegen/style-key.png \
  --output $WORK/work/video_prompt_pack.json \
  --markdown $WORK/work/video_prompt_pack.md \
  --strict
# style-key.png 可按 video_prompt_pack.md 的 Character / Style Reference prompt 用 Codex image_gen 生成。

# 3d. paid provider 提交前检查首帧和共享 style key
python3 $SKILL/scripts/reference_frame_preflight.py \
  --prompt-pack $WORK/work/video_prompt_pack.json \
  --output $WORK/work/reference_frame_preflight.json \
  --markdown $WORK/work/reference_frame_preflight.md \
  --require-style-reference \
  --strict

# 3e. 素材任务清单与预检：哪些已 ready，哪些要生图/审批/渲染/搜索
python3 $SKILL/scripts/storyboard_assets.py \
  --storyboard-plan $WORK/work/storyboard_plan.json \
  --asset-root $WORK/work \
  --output $WORK/work/storyboard_assets.json \
  --markdown $WORK/work/storyboard_assets.md

# 3f. 如果 imagegen[] 或 storyboard_assets 里的 needs_generation 非空，在 Codex 里直接调内置 imagegen 工具
#     生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
#     把每条 prompt_en 用 imagegen 生成 1024x1536，存到 $WORK/work/imagegen/
#     不需要 OPENAI_API_KEY；详见 docs/prompts/19-imagegen.md
#     如果 storyboard_assets 里有 needs_approval，提交 Dreamina/即梦前先确认，因为可能消耗 credits。

# 3f. 可选：软件教程/产品演示录屏，导入点击热点并生成自动聚焦计划
python3 $SKILL/scripts/screen_focus.py \
  --events $WORK/work/clicks.json \
  --screen-width 1920 \
  --screen-height 1080 \
  --output $WORK/work/screen_focus_plan.json \
  --markdown $WORK/work/screen_focus_plan.md

# 3g. 可选：录屏另有 facecam/camera，生成 PIP 小窗计划
python3 $SKILL/scripts/pip_overlay.py \
  --camera $WORK/origin/facecam.mp4 \
  --segment "0,42,bottom_right" \
  --sync-offset 0.18 \
  --output $WORK/work/pip_overlay_plan.json \
  --markdown $WORK/work/pip_overlay_plan.md

# 3h. 可选：生成调色计划，最终渲染时用 --color-grade 接入单次编码
python3 $SKILL/scripts/color_grade.py \
  --preset screen \
  --output $WORK/work/color_grade.json \
  --markdown $WORK/work/color_grade.md

# 3i. 可选：把已审 render_config 导出成无路径配方，供同栏目换素材复用
python3 $SKILL/scripts/edit_recipe.py export \
  --config $WORK/work/render_config.json \
  --name fast-tech-explainer \
  --description "快节奏科技口播" \
  --output $WORK/work/recipes/fast-tech-explainer_edit_recipe.json \
  --markdown $WORK/work/recipes/fast-tech-explainer_edit_recipe.md

# 4. 渲染前预检：先挡住缺文件、空剪辑、坏时间段和危险 overlay 参数
python3 $SKILL/scripts/edit_preflight.py \
  --config $WORK/work/render_config.json \
  --enrich-plan $WORK/work/enrich_plan.json \
  --output $WORK/work/edit_preflight.json \
  --markdown $WORK/work/edit_preflight.md \
  --strict

# 5. 渲染
#    如果生成了 screen_focus_plan.json，可额外追加：
#    --enrich-plan $WORK/work/screen_focus_plan.json
#    如果生成了 pip_overlay_plan.json，可额外追加：
#    --enrich-plan $WORK/work/pip_overlay_plan.json
#    如果生成了 color_grade.json，可额外追加：
#    --color-grade $WORK/work/color_grade.json
python3 $SKILL/scripts/render_final.py \
  --config $WORK/work/render_config.json \
  --enrich-plan $WORK/work/enrich_plan.json \
  --profile tech_pro \
  --primary-speed 1.25 \
  --subtitle-style karaoke \
  --output $WORK/output/day${DAY}_master.mp4

# 6. 主片质检
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_master.mp4 --platform douyin \
  --json $WORK/output/day${DAY}_master_qa.json \
  --review-dir $WORK/output/verify/day${DAY}_qa \
  --review-clips

# 6b. 主片镜头色彩 / 曝光 / broadcast-range 门禁
python3 $SKILL/scripts/shot_color_qa.py \
  $WORK/output/day${DAY}_master.mp4 \
  --output $WORK/output/verify/day${DAY}_shot_color_qa.json \
  --markdown $WORK/output/verify/day${DAY}_shot_color_qa.md \
  --strict

# 6c. 导出与 1.25x + 片头 offset 对齐的字幕 sidecar / timed-text JSON
python3 $SKILL/scripts/subtitle_pack.py \
  --config $WORK/work/render_config.json \
  --output-dir $WORK/output/subtitles \
  --basename day${DAY}_master \
  --speed 1.25 \
  --offset 2.0

# 6d. 最终字幕可读性门禁
python3 $SKILL/scripts/subtitle_readability_qa.py \
  $WORK/output/subtitles/day${DAY}_master.json \
  --media $WORK/output/day${DAY}_master.mp4 \
  --output $WORK/output/verify/day${DAY}_subtitle_readability_qa.json \
  --markdown $WORK/output/verify/day${DAY}_subtitle_readability_qa.md \
  --strict

# 6e. 主片留存节奏风险门禁
python3 $SKILL/scripts/retention_rhythm_qa.py \
  $WORK/output/day${DAY}_master.mp4 \
  --timed-text $WORK/output/subtitles/day${DAY}_master.json \
  --output $WORK/output/verify/day${DAY}_retention_rhythm_qa.json \
  --markdown $WORK/output/verify/day${DAY}_retention_rhythm_qa.md \
  --strict

# 6f. 主片响度/爆峰/长静音门禁
python3 $SKILL/scripts/audio_master_report.py \
  $WORK/output/day${DAY}_master.mp4 \
  --output $WORK/output/day${DAY}_audio_master_report.json \
  --markdown $WORK/output/day${DAY}_audio_master_report.md \
  --strict

# 6g. 如果 QA 有 WARN/FAIL，先看 review packet；想抽查关键切点再生成可视化复盘图
python3 $SKILL/scripts/timeline_view.py \
  $WORK/output/day${DAY}_master.mp4 --at 42.5 --radius 1.5 \
  --output $WORK/output/verify/day${DAY}_42_5s.png

# 6h. 有 rough/jump cut 时，可选生成原片连续时钟 vs 最终像素的可播放对照
python3 $SKILL/scripts/edit_compare.py \
  $WORK/origin/talking.mp4 \
  $WORK/output/day${DAY}_master.mp4 \
  --cut-list $WORK/work/rough_cut.json \
  --output-speed 1.25 \
  --output-offset 2.0 \
  --output $WORK/output/verify/day${DAY}_source_vs_final.mp4 \
  --report $WORK/output/verify/day${DAY}_edit_compare.json \
  --markdown $WORK/output/verify/day${DAY}_edit_compare.md

# 7. 多平台
python3 $SKILL/scripts/multi_export.py \
  $WORK/output/day${DAY}_master.mp4 --output-dir $WORK/output/

# 7b. 可选：交给专业剪辑软件继续精修/调色/混音
python3 $SKILL/scripts/export_edl.py \
  --config $WORK/work/render_config.json \
  --output $WORK/work/day${DAY}_edit.edl \
  --fps 30
python3 $SKILL/scripts/export_fcpxml.py \
  --config $WORK/work/render_config.json \
  --output $WORK/work/day${DAY}_edit.fcpxml \
  --fps 30 \
  --width 1080 \
  --height 1920
python3 $SKILL/scripts/export_otio.py \
  --config $WORK/work/render_config.json \
  --output $WORK/work/day${DAY}_edit.otio \
  --fps 30

# 8. 平台导出质检
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_xhs.mp4 --platform xhs
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_douyin.mp4 --platform douyin
python3 $SKILL/scripts/render_qa.py \
  $WORK/output/day${DAY}_wxch.mp4 --platform wxch

# 9. 文案
python3 $SKILL/scripts/generate_caption.py \
  --script $WORK/work/clean_script.md --profile tech_pro \
  --output $WORK/output/day${DAY}_caption.json

# 9b. 生成 3 套封面并在小图里选最终发布版
python3 $SKILL/scripts/cover_variants.py \
  $WORK/output/day${DAY}_xhs.mp4 \
  --title "<4-8字封面文字>" \
  --caption $WORK/output/day${DAY}_caption.json \
  --platform xhs \
  --output-dir $WORK/output/covers \
  --render \
  --select cover-c \
  --require-selection \
  --output $WORK/work/cover_variants.json \
  --markdown $WORK/work/cover_variants.md \
  --strict

# 10. 发布前 gate 汇总 + 最终上传包
python3 $SKILL/scripts/pipeline_manifest.py \
  --project-dir $WORK \
  --target-stage publish_ready \
  --require shot_color_qa \
  --output $WORK/work/pipeline_manifest.json \
  --markdown $WORK/work/pipeline_manifest.md \
  --strict

python3 $SKILL/scripts/publish_package.py \
  --project-dir $WORK \
  --platforms xhs douyin wxch \
  --output $WORK/work/publish_package.json \
  --markdown $WORK/work/publish_package.md \
  --strict

# 10b. 可选：自动化收尾或跨会话接手前生成续跑上下文包
python3 $SKILL/scripts/project_resume.py \
  --project-dir $WORK \
  --target-stage publish_ready \
  --output $WORK/work/project_resume.json \
  --markdown $WORK/work/project_resume.md \
  --agent-note $WORK/CLAUDE.md \
  --strict

# 10c. 可选：发布确认前打开静态复核面板
python3 $SKILL/scripts/review_dashboard.py \
  --project-dir $WORK \
  --target-stage publish_ready \
  --output $WORK/work/review_dashboard.json \
  --html $WORK/work/review_dashboard.html \
  --strict
```

---

## 测试

```bash
pytest tests/           # 完整本地测试套件，约 14 秒
```

按模块跑：
```bash
pytest tests/test_content_guard.py -v       # 80+ 规则的 38 个测试
pytest tests/test_rewrite_script.py -v      # Story Engine
pytest tests/test_auto_broll.py -v          # B-roll 调度
pytest tests/test_multi_export.py -v        # 多平台比例转换
pytest tests/test_hdr_sdr.py -v             # PQ/HLG → Rec.709 SDR / color tags / 完整解码门禁
pytest tests/test_delivery_encode.py -v     # 硬大小上限 / 两遍编码 / 完整解码门禁
pytest tests/test_render_qa.py -v           # 渲染后质检
pytest tests/test_shot_color_qa.py -v       # 成片镜头色彩 / 曝光 / broadcast-range 门禁
pytest tests/test_retention_rhythm_qa.py -v # 成片 hook / 长镜头 / 节奏风险门禁
pytest tests/test_reference_edit_rhythm.py -v # 参考片/成片 hard-cut 结构 / contact-sheet / stale gate
pytest tests/test_subtitle_readability_qa.py -v # 最终字幕 CPS / 时长 / 重叠 / 越界门禁
pytest tests/test_platform_safe_area_qa.py -v # 字幕 / PIP / CTA / marker 平台安全区门禁
pytest tests/test_audio_master_report.py -v # 成片响度 / true peak / LRA 门禁
pytest tests/test_render_enrich_plan.py -v  # enrich_plan 自动接入渲染
pytest tests/test_auto_emphasis.py -v      # 问句/数字/转折/结论 emphasis cues
pytest tests/test_beat_sync.py -v          # BGM → beat edit slots / fallback review / cut snap
pytest tests/test_takes_pack.py -v          # 多 take phrase-level 阅读视图
pytest tests/test_project_bootstrap.py -v   # 项目启动与 source inventory
pytest tests/test_transcript_review.py -v  # 文本/HTML 同步视频 transcript 校稿回路
pytest tests/test_semantic_transcript_review.py -v # 全篇上下文审校 / 最小补丁 / choices gate
pytest tests/test_edit_brief_plan.py -v     # 自然语言剪辑需求 → 本地 runbook
pytest tests/test_hook_variants.py -v       # 前三秒 hook 批量角度 + 风险检查
pytest tests/test_rough_cut.py -v           # ASR 粗剪：口头禅/重复句 cut list
pytest tests/test_multimodal_dead_air.py -v # 静音 AND 静帧死区计划 / 安全渲染 / live verify
pytest tests/test_timeline_view.py -v       # 源素材/成片切点可视化复盘图
pytest tests/test_edit_compare.py -v        # 原片/成片 source-time 双栏对照 + 像素映射验证
pytest tests/test_generate_caption.py -v    # 文案合成
pytest tests/test_cover_variants.py -v      # 多套封面 + 小图预览 + 发布选择
pytest tests/test_imagegen_hint.py -v       # gpt-image-2 提示词检测
pytest tests/test_storyboard_plan.py -v     # 分镜 shot cards + 生成路由
pytest tests/test_video_prompt_pack.py -v   # 视频生成提示词包 + 审批 gate
pytest tests/test_reference_frame_preflight.py -v # 首帧/style key 尺寸/方向/透明背景 gate
pytest tests/test_generation_task_log.py -v # 异步生成任务台账 + 下载 gate
pytest tests/test_generated_clip_review.py -v # 生成视频 contact sheet / 评分 / 裁切 / 重生 / stale gate
pytest tests/test_generated_sequence_review.py -v # 已审生成片段相邻边界证据 / 连续性 / stale gate
pytest tests/test_generation_lessons.py -v # 已审片段 → scoped prompt 经验库 / 选择 / stale gate
pytest tests/test_video_understanding.py -v # 抽样帧 + 可选 YOLO 检测 artifact
pytest tests/test_scene_boundaries.py -v # fixed/adaptive 场景检测 + cut evidence
pytest tests/test_visual_dedupe.py -v # 跨来源重复场景检测 + 保留建议
pytest tests/test_storyboard_assets.py -v   # 分镜素材 readiness manifest
pytest tests/test_export_edl.py -v          # NLE handoff EDL + manifest
pytest tests/test_export_fcpxml.py -v       # NLE handoff FCPXML + manifest
pytest tests/test_export_otio.py -v         # NLE handoff OTIO + manifest
pytest tests/test_screen_focus.py -v        # 录屏点击聚焦计划 + render 接入
pytest tests/test_subtitle_pack.py -v       # SRT/VTT/ASS/JSON 字幕交付包
pytest tests/test_srt_edit_plan.py -v       # SRT 编辑指令转 render_config/cut list
pytest tests/test_script_alignment.py -v    # 目标稿 → 多 take 原话匹配 / choices / render_config
pytest tests/test_audio_cue_sheet.py -v     # BGM/SFX 音频设计清单
pytest tests/test_multicam_sync.py -v       # 多机位 offset / 最响音轨 / pairwise / 真实预览
pytest tests/test_speech_denoise.py -v      # 口播降噪 preset / 顺序 / 真实 FFmpeg SNR smoke
pytest tests/test_bgm_ducking.py -v         # 旁白驱动 BGM sidechain + 真实 FFmpeg smoke
pytest tests/test_audio_transition.py -v    # J-cut/L-cut source handle / hash / 单次编码 / receipt
pytest tests/test_color_grade.py -v         # 调色计划 + render_final 接入
pytest tests/test_edit_preflight.py -v      # 渲染前结构/路径/参数预检 gate
pytest tests/test_edit_revision.py -v       # 文本剪辑 artifact source-bound revision / undo / redo
pytest tests/test_edit_recipe.py -v         # 可移植 render-config recipe / typed binding / replay preflight
pytest tests/test_video_stabilization.py -v # source-bound 后端计划 / 工作副本 / 全长 A/B / confirm gate
pytest tests/test_approval_receipt.py -v    # 最终交付件 SHA-256 审批收据 + stale gate
pytest tests/test_publish_package.py -v     # 最终上传包 + gate 状态汇总
pytest tests/test_project_resume.py -v      # 续跑上下文包 + agent handoff
pytest tests/test_review_dashboard.py -v    # 静态人工复核面板 + gate queue
pytest tests/test_source_receipts.py -v     # 事实来源 proof deck + 发布 gate
```

### 2026-08-17 自动化升级记录（Source-bound Reference Edit Rhythm）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`krea-ai/skills` Cinematic Product Ad](https://github.com/krea-ai/skills/blob/main/krea-marketing/workflows/cinematic-product-ad.md) | 有参考广告时先用 FFmpeg scene detection + contact sheet 实测 shot list、per-beat duration 和交替结构，不靠肉眼猜；只复制结构，不复制竞品 pixels/assets | 新增参考片/候选片同参数 hard-cut 测量和双 contact sheet；报告明确禁止复制画面、音频、品牌和故事，并保留 soft transition/镜头内动作漏检的人工边界 |
| [`allwavemedia/resolve-ai-toolkit` Pacing Analysis](https://github.com/allwavemedia/resolve-ai-toolkit#edit_pacing--pacing-analysis) | 把 shot durations、rhythm classification、energy/tempo 和 outliers 作为可读剪辑证据，并按内容风格提供 pacing 目标 | 不引入 Resolve/MCP 或静态风格模板；比较 cuts/minute、median shot、final hold、归一化切点和阶段 cut share，让参考片自身成为目标证据 |
| [`cliprise/awesome-seedance-2-prompts` reference roles](https://github.com/cliprise/awesome-seedance-2-prompts#seedance-20-reference-role-system) | 明确声明 `@video3 = edit rhythm reference`，只借鉴 cut pacing/final hold，避免模型把多模态参考随机混合成内容复制 | 把 reference role 固定为 edit structure；默认偏差只 WARN，只有明确验收时 `--require-match` 才阻断，避免参考片从灵感误升级为隐含硬约束 |

新增/调整能力：新增 [`scripts/reference_edit_rhythm.py`](scripts/reference_edit_rhythm.py)、[`tests/test_reference_edit_rhythm.py`](tests/test_reference_edit_rhythm.py) 和 [`docs/prompts/92-reference-edit-rhythm.md`](docs/prompts/92-reference-edit-rhythm.md)。`analyze` 要求参考片、候选片和证据都位于项目内，拒绝 symlink、同一源文件和 source/output/evidence 路径碰撞；对两片运行现有 `scene_boundaries.py` hard scene detection，生成并绑定 SHA-256/大小/媒体契约与双 contact sheet。报告记录逐镜头时长、cuts/minute、mean/median/p90/min/max、cadence CV、final-hold 秒数/比例、归一化切点和 opening/middle/closing cut share；comparison 检查 cut density、median shot、final hold、双向最近 cut-position distance 与阶段分布。默认差异为 review warning；`--require-match` 才变成 blocker。`verify` 现场重读 source/evidence bytes 和媒体契约，并从存储的原始 boundaries 重算全部 timeline metrics、comparison、summary 和 canonical report id，源/证据/派生字段漂移 fail closed。`pipeline_manifest.py` 新增存在即 live verify、可 `--require reference_edit_rhythm` 的 gate；`edit_brief_plan.py` 新增中英文“参考视频/广告节奏、复刻剪辑结构”路由，并把报告排在最终渲染后。

使用方式：运行 `python3 scripts/reference_edit_rhythm.py analyze --project-dir . --reference origin/reference-ad.mp4 --candidate output/final.mp4 --evidence-dir verify/reference_edit_rhythm --output work/reference_edit_rhythm.json --markdown work/reference_edit_rhythm.md --strict`；完整看两条视频和两张 contact sheet。只有结构相似是明确验收条件时才加 `--require-match`。发布前运行 `python3 scripts/reference_edit_rhythm.py verify --report work/reference_edit_rhythm.json --strict`，需要强制存在时再运行 `pipeline_manifest.py --require reference_edit_rhythm --strict`。`--force` 只用于明确覆盖同一路径的旧报告/证据；SHA-256 不是签名或版权许可。

验证结果：`.venv/bin/python -m pytest tests -q` 全量回归 **897 passed**，新增/关联模块定向回归 **110 passed**；`.venv/bin/python -m compileall -q scripts tests` 通过，`quick_validate.py .` 返回 `Skill is valid!`，`analyze/verify` CLI help 均可正常加载。真实 FFmpeg 冒烟用两条 1.8 秒三色 H.264 视频运行 `analyze → verify`，报告为 `ready`、双片各检测 1 个 hard cut / 2 个 shots、normalized boundary distance `0.0`，live verify 为 **0 blocker / 0 warning**，JSON、Markdown 与两张 contact sheet 均生成并完成 hash 校验；`git diff --check` 通过。

### 2026-08-16 自动化升级记录（Source-bound Generated Sequence Continuity Review）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`livingghost/pov-series-director`](https://github.com/livingghost/pov-series-director/blob/main/SKILL.md) | 后一 clip 的动作和构图必须建立在“已接受成片”的真实 terminal frame 上；逐次记录身份变化、道具转移、机位断裂和 endpoint drift，不能用规划图冒充最终证据 | 新增逐片 review 之后的独立 sequence gate；从 live clip bytes 和批准 keep ranges 提取真实尾帧/首帧、并排图与边界 preview，并绑定上游 review/storyboard，而不是只读 prompt 或计划图 |
| [`machina-exm/film-studio-skills` stress-test](https://github.com/machina-exm/film-studio-skills/blob/main/skills/stress-test/SKILL.md) | 角色、场景、道具和状态变体先以 canonical descriptor/reference 锁定；任何 identity drift 都是 miss，不能因为其他结果好看而平均掉 | 六项 boundary checks 用离散 `match / intentional_change / mismatch / not_applicable`，任何非预期 mismatch 必须 fail、带 failure code 和 repair action；不提供总分绕过通道 |
| [`zysilm/ai-video-producer-skill`](https://github.com/zysilm/ai-video-producer-skill/blob/main/SKILL.md) | 明确指出人物镜头只沿用 extracted end frame 会累积身份和服装漂移，角色可见时要重新使用原始角色 reference；同时把 scene/segment transition 与 review 分开 | 复核 identity/wardrobe、prop state、spatial orientation、action end state、camera framing、lighting/palette，并保留 storyboard continuity anchors；本轮不引入本地 ComfyUI/provider 执行器，只负责 provider-neutral 组装前 gate |
| [`lincwang123-bot/seedance-video-workflow`](https://github.com/lincwang123-bot/seedance-video-workflow/blob/main/skills/seedance-video-workflow/SKILL.md) | 先锁定每个镜头最佳候选，再做技术/语义 audit 和 assemble；重试只改失败相关字段，不整体重写已通过镜头 | `fail` 要求边界级 repair action，明确回到受影响 clip/动作/道具修复；脚本不自动重生、不消费 credits，也不修改已经通过的其他片段 |

新增/调整能力：新增 [`scripts/generated_sequence_review.py`](scripts/generated_sequence_review.py)、[`tests/test_generated_sequence_review.py`](tests/test_generated_sequence_review.py) 和 [`docs/prompts/91-generated-sequence-review.md`](docs/prompts/91-generated-sequence-review.md)。`prepare` 只接受至少两条、且上游 `generated_clip_review.json` 现场验证无 blocker 的片段；可按 `storyboard_plan.json` 排序并继承 expected first/last frame、reuse link 和 continuity anchors。每个相邻边界自动提取安全可解码 outgoing frame、incoming frame、并排 JPEG 和两侧默认各 1 秒的无声 1× H.264 preview；容器音频 padding 可能超出末个视频 packet，因此 outgoing 证据退回两个视频帧。上游 `pass_with_edits` 只用批准首/尾 keep range，不恢复 remove ranges。`audit` 固定六项 continuity checks、九类 failure code、完整 response coverage 和明确 repair action；`verify` 重算上游 canonical clip review、clip/storyboard/evidence SHA-256 与大小、clip order、相邻 boundary coverage、source times、派生 summary/report id。`pipeline_manifest.py` 新增存在即 live verify、可 `--require generated_sequence_review` 的门禁；`edit_brief_plan.py` 只在明确出现多镜头/跨镜头/角色或道具连续性意图时，把该步骤排在逐片 review 后。README、SKILL、daily workflow、Edit Brief、提示词索引和本节均已同步。

使用方式：先完成并验证逐片 `generated_clip_review.json`，再运行 `python3 scripts/generated_sequence_review.py prepare --project-dir . --clip-review work/generated_clip_review.json --storyboard-plan work/storyboard_plan.json --evidence-dir verify/generated_sequence --output work/generated_sequence_review_request.json --markdown work/generated_sequence_review_request.md --response-template work/generated_sequence_review_response.json`；逐个以 1× 查看无声 boundary preview 和全尺寸 comparison，填写 response 后运行 `audit --request ... --response ... --output work/generated_sequence_review.json --markdown work/generated_sequence_review.md --strict`，组装/发布前再 `verify --report work/generated_sequence_review.json --strict`。有意换场/换装/景别变化可标 `intentional_change`，但保留 warning；非预期 mismatch 必须 fail，修复或重生后重新 prepare，不能手改旧 hash。预览无声且 reviewer label/SHA-256 不是身份认证或签名，最终 master 仍需完整声画复核。

验证结果：新增 8 项 sequence request/audit/source drift/evidence drift/intentional change/storyboard coverage/真实 CLI 测试，并扩展 edit-brief 与 pipeline-manifest 回归；定向 `.venv/bin/python -m pytest tests/test_generated_sequence_review.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `108 passed in 2.00s`，含上游逐片链的最终定向组合通过 `116 passed in 2.27s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `887 passed in 18.41s`。真实 FFmpeg smoke 用两条 0.7 秒、160×90、24fps、H.264/AAC 样片完成 `prepare → audit → verify` ready round trip，产出 320×90 尾帧/首帧并排图和 0.458333 秒、160×90、24fps、H.264 无声 preview；人工查看确认左右 evidence 顺序正确。`.venv/bin/python -m compileall -q scripts tests`、四组新 CLI help、edit-brief/pipeline-manifest help 和 `git diff --check` 已通过。

### 2026-08-15 自动化升级记录（Evidence-bound Generation Lessons）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`0xadvait/ai-video-skill` learning loop](https://github.com/0xadvait/ai-video-skill/blob/main/LESSONS.md) | 每次生成后用 contact sheet/QC 提炼 cause → effect 经验，下一次工作流先读 `LESSONS.md`；冲突时追加 superseding lesson 而非静默覆盖 | 采用 review → lesson → next prompt 闭环；不直接自由追加文本，而是绑定 canonical review 与 clip/contact-sheet digests，要求显式 approval，并用 scope + `supersedes` 保留历史、停止选择旧规则 |
| [`Emily2040/seedance-2.0` retake protocol](https://github.com/Emily2040/seedance-2.0/blob/main/references/retake-protocol.md) | 对 keep / fix in post / edit / re-roll / rewrite 分流；同一轮只改一个变量并保留 shot log，避免多变量重试后无法判断哪项有效 | 保留原 review 的 verdict、score、hard-fail、evidence 和 clip-specific `prompt_fix` 作为学习来源；只有另行批准的通用 `lesson` 会进入未来提示词，不自动触发重生或多变量修复 |
| [`DojoCodingLabs/remotion-superpowers` review loop](https://github.com/DojoCodingLabs/remotion-superpowers/blob/main/commands/review-video.md) | render → review → actionable priority → re-render 的循环清晰，把复核结果转成下一步可执行修正 | 把 actionable feedback 持久化成可筛选 artifact 并交给 `video_prompt_pack.py`；仍保留人工选择和 provider credit gate，不让反馈循环自行修改工程或付费生成 |

新增/调整能力：新增 [`scripts/generation_lessons.py`](scripts/generation_lessons.py)、[`tests/test_generation_lessons.py`](tests/test_generation_lessons.py) 和 [`docs/prompts/90-generation-lessons.md`](docs/prompts/90-generation-lessons.md)。`add` 从 `generated_clip_review.json` 实时重算 canonical request/response：允许“该 clip 确实需要重生”这一预期 blocker，以便从失败学习，但拒绝 source/contact-sheet 漂移、非法 review、漏审、stored summary/report-id 篡改。每条 `generation_lesson.v1` 绑定 report/request/clip/contact-sheet SHA-256、verdict、score、hard-fail codes、evidence、原 `prompt_fix` 和 approval label，并生成 canonical lesson id；library 重新派生 summary/library id，限制 500 条，`verify` 发现 schema、重复/未知 supersedes id、自引用或内容漂移即阻断。`select` 按 provider/model/category 和 0–10 上限筛选，精确 provider/model 优先，并排除当前匹配 entry 显式 supersede 的旧规则；没有显式 model 时不会误用 model-specific 经验。`video_prompt_pack.py` 新增 `--lesson-library / --lesson-model / --lesson-category / --lesson-limit`，只把匹配的人工批准 `lesson` 追加为 `LEARNED CONSTRAINTS`，同时保留 source evidence；不自动拼入 clip-specific `prompt_fix`。`pipeline_manifest.py` 新增 `generation_lessons` live gate，`edit_brief_plan.py` 新增中英文经验库路由，并修正同一生成 runbook 的 provider 枚举为可执行的 `dreamina_seedance`。

使用方式：完成 `generated_clip_review.py audit` 后，运行 `python3 scripts/generation_lessons.py add --library work/generation_lessons.json --review work/generated_clip_review.json --clip-id shot_002 --category hand_contact --model seedance-2.0 --lesson "For hand-to-prop contact, isolate one interaction and keep the hand visible through release." --approved-by "<reviewer-label>" --markdown work/generation_lessons.md`；复用前运行 `python3 scripts/generation_lessons.py verify --library work/generation_lessons.json --strict`，可用 `select --provider dreamina_seedance --model seedance-2.0 --limit 3 --output work/selected_generation_lessons.json --markdown work/selected_generation_lessons.md` 预览命中；下一次 prompt pack 加 `--lesson-library work/generation_lessons.json --lesson-model seedance-2.0 --lesson-limit 3`。provider-wide 经验使用 `--model '*'`，真正跨 provider 的规则才显式 `--global`。approval label 和 SHA-256 都不是身份认证或签名；脚本不调用 provider、不自动重生，也不消费 credits。

验证结果：新增 11 项经验提取/源漂移/摘要篡改/scope 选择/supersedes/CLI/自然语言路由/manifest/prompt 注入回归；定向 `.venv/bin/python -m pytest tests/test_generation_lessons.py tests/test_video_prompt_pack.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `113 passed in 1.45s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `877 passed in 16.87s`。测试固定了失败 clip 的预期 regeneration blocker 可以供学习，但任何 source drift 必须拒绝；provider/model/category 精确筛选、model 未声明时不外溢、superseded 历史保留但不再选择、prompt pack 只注入批准 lesson、library id 篡改传播到 manifest、缺失 library 不得被当成空库 ready。`.venv/bin/python -m compileall -q scripts tests`、`generation_lessons.py` 三组 CLI help、`video_prompt_pack.py` lesson 参数、manifest category、Skill `quick_validate.py` 和 `git diff --check` 均通过。

### 2026-08-14 自动化升级记录（Source-bound Generated Clip Review）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`a86582751/doubao-seedance-video-skill` visual review standards](https://github.com/a86582751/doubao-seedance-video-skill/blob/main/references/visual-review-standards.md) | 把生成片段与最终组装分成两个 review phase；片段先查常识/物理、身份/道具漂移、重复动作、故事可读性和可用裁切范围，坏动作本身不能靠快切掩盖 | 新增独立的 per-clip `prepare → audit → verify` 门禁；本轮只批准片段或裁切范围，不扩张为自动多片 EDL/转场执行器 |
| [`wuwangzhang1216/DirectorSKILL` QC checklist](https://github.com/wuwangzhang1216/DirectorSKILL/blob/main/assets/qc-checklist.md) | POST gate 用 identity/action/physics/camera/frame/look 六项加权评分，同时规定 hard blocker 高于总分；65–79 只有存在 edit-side fix 才能保留 | 采用同一组可解释维度与 `80 pass / 65 pass_with_edits` 阈值；hard fail 无论高分都要求重生，trim-only 必须给可执行的完整 keep/remove coverage |
| [`memex-lab/product-launch-video-skill` pre-render review](https://github.com/memex-lab/product-launch-video-skill/blob/main/skills/product-launch-video/SKILL.md#phase-5-pre-render-review) | full render 前按场景导出代表帧，先找 layout/crop/overlap/contrast 问题，避免把昂贵渲染当第一轮检查 | 对每条生成 clip 自动产有界 contact sheet，长片按 `max_frames` 降采样；但明确抽样不能替代 1×、0.25×、静音和 audio-only 完整审片 |
| [`openai/skills` hatch-pet QA rubric](https://github.com/openai/skills/blob/main/skills/.curated/hatch-pet/references/qa-rubric.md) | 生成动画失败时先修最小范围：单帧、单 row，只有广泛身份/布局破损才整体重生 | `pass_with_edits` 精确冻结可保留/移除范围；只有可局部裁除且不含 hard fail 的问题才能保留，结构性身份/物理/叙事失败回到生成阶段 |

新增/调整能力：新增 [`scripts/generated_clip_review.py`](scripts/generated_clip_review.py)、[`tests/test_generated_clip_review.py`](tests/test_generated_clip_review.py) 和 [`docs/prompts/89-generated-clip-review.md`](docs/prompts/89-generated-clip-review.md)。`prepare` 接受重复 `--clip [id=]path` 或 `--asset-manifest storyboard_assets.json`，拒绝项目外路径、symlink、重复 id/path 和不可解码视频，为每条 clip 记录 SHA-256、大小、媒体契约，并用 FFmpeg 生成受 `--sample-fps / --max-frames` 约束的 contact sheet 后绑定其 hash。response 固定六项 1–5 分、story readability、9 类 hard-fail code、keep/remove range、regenerate、prompt fix 和 evidence notes；`audit` 要求 exact clip coverage，独立重算 100 分加权结果，检查 verdict 阈值、hard-fail override、区间边界/重叠/完整 coverage 和 source/contact-sheet drift，再输出 `generated_clip_review.v1`。`verify` 从嵌入的 request/response 重做 live canonical audit，识别源片、contact sheet、reviews、summary、status 和 report id 漂移；reviewer label 明确不是身份认证或数字签名。

使用方式：先刷新 `storyboard_assets.json`，运行 `python3 scripts/generated_clip_review.py prepare --project-dir . --asset-manifest work/storyboard_assets.json --contact-sheet-dir verify/generated_clips --output work/generated_clip_review_request.json --markdown work/generated_clip_review_request.md --response-template work/generated_clip_review_response.json`；完整执行 1× 带声、0.25×、静音画面和 audio-only 四遍审片后填 response，再运行 `python3 scripts/generated_clip_review.py audit --request work/generated_clip_review_request.json --response work/generated_clip_review_response.json --output work/generated_clip_review.json --markdown work/generated_clip_review.md --strict` 和 `python3 scripts/generated_clip_review.py verify --report work/generated_clip_review.json --strict`。生成素材 brief 现在会在 prompt pack 后安排 asset refresh 与 generated clip review；`pipeline_manifest.py --require generated_clip_review --strict` 可把报告设为发布门禁。`pass_with_edits` 只批准列出的 keep ranges，组装时不得恢复 remove ranges；`fail` 必须回到生成阶段，不能用装饰转场掩盖。

验证结果：新增 8 项 generated-clip 单元/篡改/真实 CLI 测试，并扩展 edit-brief 与 pipeline-manifest 回归；定向 `.venv/bin/python -m pytest tests/test_generated_clip_review.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `104 passed in 1.53s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `866 passed in 17.49s`。独立真实 FFmpeg smoke 用 4 秒、320×180、24fps、H.264/AAC 样片生成 8 帧 contact sheet（2596×188），人工查看确认帧序覆盖全片进度；真实 CLI 测试另完成 `prepare → audit → verify` ready round trip，并固定项目外/symlink 输入、source drift、stored summary/report id 篡改、hard-fail 高分绕过和 trim coverage 缺口都必须阻断。`.venv/bin/python -m compileall -q scripts tests`、三组新 CLI help、Skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-13 自动化升级记录（Source-bound Multimodal Dead-Air Cuts）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) | 支持把 audio 与 motion 分析组合成剪辑表达式，不只依赖单一静音阈值 | 采用音画联合判定，但把自动剪辑收紧为“静音 AND 静帧覆盖达到门槛”，避免保留现场动作时误删 |
| [`mazsola2k/ai-video-editor`](https://github.com/mazsola2k/ai-video-editor) | narrated/unboxing 模式同时运行 FFmpeg `silencedetect` 与 `freezedetect`，静帧覆盖静音达到 60% 才处理 | 采用 60% 默认覆盖门槛；实际只删除两种检测结果的交集，并保留切点 padding 和 30ms 音频 fade |
| [`htekdev/vidpipe`](https://github.com/htekdev/vidpipe) | context-aware silence removal 设有 20% 总删减上限，防止一次自动操作重写成片节奏 | 复用项目既有的 20% removal budget，超限默认阻断；只有显式 `--allow-over-budget` 才允许继续 |

新增/调整能力：新增 [`scripts/multimodal_dead_air.py`](scripts/multimodal_dead_air.py)、[`tests/test_multimodal_dead_air.py`](tests/test_multimodal_dead_air.py) 和 [`docs/prompts/88-multimodal-dead-air.md`](docs/prompts/88-multimodal-dead-air.md)。`plan` 用 FFmpeg 同时检测静音和静帧，只有静帧覆盖某段静音达到默认 60% 时才提出候选，最终删除范围严格取二者交集；默认保留 80ms 切点 padding、使用 30ms 音频 fade，并以 20% source-duration 删除预算 fail closed。计划绑定源视频绝对路径、SHA-256、大小、媒体契约、完整分析结果、canonical settings 与 plan id；`verify` 从 live source 重建派生状态，可识别源漂移、手工改写检测结果、canonical budget blocker/warning 和应用后输出漂移。`apply` 只写同目录临时 MP4，通过 H.264/AAC、`yuv420p`、尺寸、帧率、采样率、声道、时长、完整 `ffmpeg -xerror` 解码和输出 hash 检查后才原子提升；默认拒绝覆盖已有文件、symlink、源片或计划/Markdown/交付件路径碰撞。`pipeline_manifest.py` 新增存在即 live verify、可 `--require multimodal_dead_air_plan` 的 gate；`edit_brief_plan.py` 新增“静音且画面静止 / silence and freeze”意图路由，并避免同时重复安排普通 audio-only `jump_cut`。README、SKILL、daily workflow 和提示词索引已同步。

使用方式：运行 `python3 scripts/multimodal_dead_air.py plan origin/talking.mp4 --delivery work/dead-air-tight.mp4 --output work/multimodal_dead_air_plan.json --markdown work/multimodal_dead_air_plan.md --strict`；逐段复核 Markdown，并给 `timeline_view.py --cut-list work/multimodal_dead_air_plan.json --limit <removed_segments实际数量>` 显式传入全部切点数，再执行 `python3 scripts/multimodal_dead_air.py apply work/multimodal_dead_air_plan.json --markdown work/multimodal_dead_air_plan.md`。若没有删除段，停止并保留原片。交付前运行 `python3 scripts/multimodal_dead_air.py verify work/multimodal_dead_air_plan.json --strict` 和现有 render QA；完整项目进入发布阶段后，再运行默认 publish-ready 的 `pipeline_manifest.py --require multimodal_dead_air_plan --strict`。只有音频静音但画面仍有动作、表情、演示或有意停顿时不会自动删除；只想做音频静音跳剪时继续使用 `jump_cut.py`。

验证结果：定向 `.venv/bin/python -m pytest tests/test_multimodal_dead_air.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `110 passed in 1.18s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `856 passed in 16.91s`。真实 10 秒样片包含 2 秒“蓝色静帧 + 静音”，检测得到 `1 silence / 1 freeze / 1 candidate / 100% overlap`；切点 padding 后删除 `1.84s`（18.4%，低于预算），成功输出 `8.18s` H.264/AAC、`yuv420p`、320×180、30fps、48kHz 单声道 MP4，`blocking=0`，完整解码与 application hash 均通过。测试另固定了 canonical 超预算 blocker 必须阻断 apply/manifest、override warning 必须保留，以及 codec/pixel-format 漂移不得提升临时文件。独立前向演练确认用户请求会路由到多模态模式而非 audio-only jump cut，并据此补齐“全部切点计数”、空计划停止、平台不猜测和 manifest 边界说明。`.venv/bin/python -m compileall -q scripts tests`、四组新 CLI help、brief route、manifest category、Skill `quick_validate.py` 和 `git diff --check` 也全部通过。

### 2026-08-12 自动化升级记录（Source-bound HDR → Rec.709 SDR Delivery）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`browser-use/video-use` `helpers/render.py`](https://github.com/browser-use/video-use/blob/main/helpers/render.py) | 用 `ffprobe color_transfer` 识别 iPhone HLG / PQ，并在普通 8-bit 社媒输出前接 `zscale + tonemap`；明确 QuickTime 可能在本机掩盖平台上传后的过曝/霓虹问题 | 采用 PQ/HLG metadata 检测和 linear-light Hable 链，但不静默自动猜测；计划必须先绑定源片和色彩契约 |
| [`damionrashford/media-os` `ffmpeg-hdr-color`](https://github.com/damionrashford/media-os/blob/main/skills/ffmpeg-hdr-color/SKILL.md) | 区分真正 tone-map 与只改 primaries 的 colorspace conversion，要求 linear float sandwich、显式 BT.709 输出 tags，并在输出后复查 | 固定使用 `zscale → gbrpf32le → hable → BT.709 limited`，四项 color tag、pixel format 和完整解码全部进入硬契约 |
| [`openakita/openakita` Footage Gate validation](https://github.com/openakita/openakita/blob/main/plugins/footage-gate/VALIDATION.md) | 把 HDR 误调色列为真实上游回归，要求 tone-map 在 `eq`/调色前执行，并用测试固定滤镜顺序 | 本轮只负责 HDR master 的 SDR derivative，不把技术转换和 creative grade 混在一起；缺依赖在编码前 fail closed |
| [`theSamPadilla/montaj` render color-space contract](https://github.com/theSamPadilla/montaj/blob/main/docs/RENDER.md#project-color-space) | 为项目显式声明工作色彩空间，混合素材先归一化；输出 codec、bit depth 和 color metadata 必须一致 | 吸收“明确色彩契约 + 输出复查”；本轮保持单文件交付边界，不引入完整 HDR project/mixed-timeline 体系 |

新增/调整能力：新增 [`scripts/hdr_sdr.py`](scripts/hdr_sdr.py)、[`tests/test_hdr_sdr.py`](tests/test_hdr_sdr.py) 和 [`docs/prompts/87-hdr-sdr.md`](docs/prompts/87-hdr-sdr.md)。`plan` 绑定源绝对路径、SHA-256、大小、时长、显示尺寸、fps、codec、音轨、pixel format/bit depth、四项 color metadata 与 HDR side data；只接受 `smpte2084` PQ 或 `arib-std-b67` HLG，并要求 BT.2020 primaries/matrix，普通 SDR、未知或矛盾 tags 直接拒绝。`apply` 要求 FFmpeg 同时存在 `zscale` 和 `tonemap`，固定走 explicit input transfer → linear float → BT.709 primaries → Hable → BT.709 transfer/matrix/limited-range → `yuv420p`，输出 H.264/AAC MP4；临时文件必须通过 BT.709 四 tags、容器/codec/pixel format、尺寸/fps/时长/音轨和 `-xerror` 全长解码才原子提升。`verify` 现场重算源/输出 hash、media/color contract、canonical settings、plan id 和 decode receipt。`pipeline_manifest.py` 新增存在即 live verify、可 `--require hdr_sdr_plan` 的 gate；`edit_brief_plan.py` 新增 iPhone HDR/HLG/PQ/HDR 转 SDR/Rec.709/过曝意图路由，并在同时需要硬大小压缩时先产 SDR、再把 `delivery_encode.py` 接到 SDR 文件。README、SKILL、daily workflow 和提示词索引已同步。

使用方式：运行 `python3 scripts/hdr_sdr.py plan output/master_hdr.mp4 --delivery output/master_sdr.mp4 --output work/hdr_sdr_plan.json --markdown work/hdr_sdr_plan.md`；确认 metadata/profile/filter 后执行 `python3 scripts/hdr_sdr.py apply work/hdr_sdr_plan.json`，最后运行 `python3 scripts/hdr_sdr.py verify work/hdr_sdr_plan.json` 和 `pipeline_manifest.py --require hdr_sdr_plan --strict`。Dolby Vision/HDR10+ 动态 metadata 不会保留；技术通过后仍须在可信 SDR 显示器完整检查肤色、高光、阴影、渐变和饱和色，并对 SDR 文件重跑 render QA、shot color QA 和审批收据。

验证结果：HDR/manifest/brief 专项回归 `101 passed in 1.11s`，全量回归 `837 passed in 18.77s`；`compileall`、四组 HDR CLI help、manifest 分类枚举、`git diff --check` 和 Skill `quick_validate` 均通过。另以真实 10-bit BT.2020 HLG/HEVC 样片跑了 fail-closed smoke：计划正确识别 `arib-std-b67` HLG，同时明确报告缺 `zscale` 和尚未 apply 两项 blocker；`apply` 返回 1，且没有写出 SDR 交付文件。本机 `/opt/homebrew/bin/ffmpeg 8.1.1` 有 `tonemap` 但没有 `zscale`，因此没有虚报真实 tone-map 成功，也没有安装额外 FFmpeg 或使用退化滤镜；完整编码、输出契约、全长解码、原子提升和 live verify 生命周期由 deterministic mocked FFprobe/FFmpeg 测试覆盖。实际使用前须安装带 `zscale`（libzimg）和 `tonemap` 的 FFmpeg，再以真实 PQ/HLG 项目完成视觉验收。

### 2026-08-11 自动化升级记录（Source-bound J-cut / L-cut Audio Transitions）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`Rajbharti06/Ultimate-Video-Editing-Skills`](https://github.com/Rajbharti06/Ultimate-Video-Editing-Skills/blob/main/skills/ultimate-video-editor/SKILL.md) | 明确区分 J-cut（声音先于画面）和 L-cut（画面先于声音），并要求切点 30ms audio fade；sound-design 指南把 pre-lap 与 room tone 作为专业衔接手法 | 采用显式 per-boundary J/L 语义和 30ms 普通边缘 fade；不照搬“always”规则，只有用户/剪辑者选中的边界才启用 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) / [`helpers/render.py`](https://github.com/browser-use/video-use/blob/main/helpers/render.py) | 把 L-cut/J-cut 列为 agent 应能按素材需要构建的手法，实际 renderer 给每段烘焙 30ms fade，并要求从波形/时间线复核切点 | 保留 audio-first 与切点复核原则；新增可重建的 source-time/output-time layers，不把手写 FFmpeg 当作不可审计的最终状态 |
| [`Bomx/super-video-maker-skill`](https://github.com/Bomx/super-video-maker-skill/blob/main/recipes/avatar-hook-broll.json) | avatar hook 到连续 VO 使用短 acrossfade，并把音频存在、响度和 seam QC 写进交付检查 | 借鉴短交叠与 seam QC，但不绑定 avatar/provider；主音频转场继续接本项目 loudness、render QA 和 receipt 链路 |
| [`6missedcalls/video-editing-skill`](https://github.com/6missedcalls/video-editing-skill) | 单一职责脚本可独立运行，也可组合进完整 FFmpeg pipeline | 新功能保持标准库 CLI；`apply` 是安全 wrapper，同时 `render_final.py --audio-transition-plan` 可直接组合到现有单次编码渲染 |

新增/调整能力：新增 [`scripts/audio_transition.py`](scripts/audio_transition.py)、[`tests/test_audio_transition.py`](tests/test_audio_transition.py) 和 [`docs/prompts/86-audio-transition.md`](docs/prompts/86-audio-transition.md)。`plan` 从已有 `render_config` / transcript 编译画面硬切与独立主音频 layers，J-cut 读取 incoming clip 入点之前的真实 handle，L-cut 延续 outgoing clip 出点之后的真实 handle并让 incoming audio 从 overlap 后恢复同步；handle 不足、时长越界、重复边界或短 clip 会 fail closed。config、transcript、源视频/B-roll 的 SHA-256、大小、媒体契约、clip timing、fade、source/output coverage 与 canonical plan id 全部入账；`verify` 从 live inputs 重建，即使重写 plan id 也不能隐藏 compiled layer 漂移。计划、Markdown、成片与 receipt 默认拒绝覆盖已有目标。`apply` 通过新增的 `render_final.py --audio-transition-plan` 把画面、错位主音频、字幕、overlay、BGM 和响度链留在一次 FFmpeg 编码中，临时文件成功 probe 后才原子提升并写 receipt。`pipeline_manifest.py` 新增存在即 live verify、可 `--require audio_transition_plan` 的 gate；`edit_brief_plan.py` 新增 J-cut/L-cut/声音先行/声音延续路由。README、SKILL、daily workflow 和提示词索引已同步。

使用方式：先逐边界试听素材，再运行 `python3 scripts/audio_transition.py plan work/render_config.json --transition 1,j_cut,0.4 --transition 3,l_cut,0.5 --output work/audio_transition_plan.json --markdown work/audio_transition_plan.md`；用 `audio_transition.py apply ... --output output/master.mp4 --receipt work/audio_transition_apply.json` 安全渲染，或把 `--audio-transition-plan work/audio_transition_plan.json` 加到现有 `render_final.py` 命令；最后执行 `audio_transition.py verify ... --receipt ... --strict`。L-cut 会跳过 incoming clip 等长的开头音频以恢复同步，只能在该 handle 是 ambience/room tone/呼吸或明确要舍弃内容时使用。机器验证不能判断交叠对白是否合适；每个改变边界必须以 1× 在耳机和手机扬声器上试听。

验证结果：新增 13 项 audio-transition 计划/安全/篡改/lifecycle/真实渲染测试，并扩展 pipeline-manifest、edit-brief 与 render integration 回归；定向 `.venv/bin/python -m pytest tests/test_audio_transition.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py tests/test_render_guard_integration.py -q` 通过 `104 passed in 4.80s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `825 passed in 17.11s`。真实 FFmpeg smoke 分别执行 J-cut 与 L-cut `plan → apply → receipt verify`，两者都输出 2.4 秒、160×90、24fps 的 H.264/AAC 文件，保留音轨，临时输出清理且所有产物默认拒绝覆盖；CLI round-trip 另跑一遍 J-cut。`.venv/bin/python -m compileall -q scripts tests`、CLI help、manifest category、中文 brief route、Skill `quick_validate.py` 和 `git diff --check` 也均通过。

### 2026-08-10 自动化升级记录（Target-size Delivery Encode）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 依据媒体元数据规划目标大小两遍编码，执行前检查磁盘，交付时检查容器/流/尺寸/帧率/时长并全长解码 | 采用“先 plan、再 apply、现场 verify”和两遍编码；另外把超过用户上限从警告改为硬 blocker，并用 SHA-256 绑定源与交付字节 |
| [`MastroMimmo/ffmpeg-skill`](https://github.com/MastroMimmo/ffmpeg-skill) | 用“compress under 10MB”这类用户意图组织简单命令，并给出压缩前后结果 | 保留简单 CLI 意图，但不复用它只调 CRF、不保证目标大小的实现；本项目以硬 ceiling 和交付契约为准 |
| [`affaan-m/ECC` Video Editing skill](https://github.com/affaan-m/ECC/blob/main/skills/video-editing/SKILL.md) | 把确定性 FFmpeg 处理/代理文件与内容取舍分开，让自动化不假装完成审美判断 | 交付编码只证明技术契约；Markdown 和文档仍要求 1× 全片人工复核、交付后 QA 和新审批收据 |
| [`6missedcalls/video-editing-skill`](https://github.com/6missedcalls/video-editing-skill) | 小型、单一职责的 FFmpeg 脚本可独立运行也可组合进 pipeline，依赖面小 | 新能力继续作为独立标准库 CLI，只复用项目已需要的 FFmpeg/ffprobe，不新增运行时框架 |

新增/调整能力：新增 [`scripts/delivery_encode.py`](scripts/delivery_encode.py)、[`tests/test_delivery_encode.py`](tests/test_delivery_encode.py) 和 [`docs/prompts/85-delivery-encode.md`](docs/prompts/85-delivery-encode.md)。`plan` 绑定源路径、SHA-256、大小、时长、尺寸、fps、codec 和音轨，从硬 `--max-size-mib` 扣出 AAC 音频与 6% 安全余量，生成 canonical plan id 与 libx264 两遍命令。不可行的低码率直接拒绝；可选缩小分辨率/帧率，但不允许放大或插帧。`apply` 检查剩余磁盘，拒绝 symlink、路径冲突和默认覆盖；临时 MP4 必须通过硬大小、H.264/AAC、`yuv420p`、尺寸/fps/时长/音轨契约和 FFmpeg `-xerror` 全长解码，才会原子替换到交付路径。`verify` 重算源片/输出 hash 、canonical settings 和 derived status，即使手改 plan id 也不能隐藏漂移。`pipeline_manifest.py` 新增“存在即 live verify”的 `delivery_encode_plan` gate；`edit_brief_plan.py` 可从“压缩到 18MB 以内/满足上传限制”自动路由。README、SKILL、daily workflow 和提示词索引已同步。

使用方式：先运行 `python3 scripts/delivery_encode.py plan output/master.mp4 --delivery output/master-under-20m.mp4 --max-size-mib 20 --output work/delivery_encode_plan.json --markdown work/delivery_encode_plan.md`，检查计划中的码率、缩放和 warnings；再运行 `python3 scripts/delivery_encode.py apply work/delivery_encode_plan.json --markdown work/delivery_encode_plan.md`，最后执行 `python3 scripts/delivery_encode.py verify work/delivery_encode_plan.json --strict`。如交付路径已存在，只有显式 `--force` 才允许在新文件已完整验证后原子替换。交付件必须重跑 QA 并人工完整审片，原版审批收据不可复用。

验证结果：新增 11 项 delivery-encode 单元/安全/lifecycle 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_delivery_encode.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `97 passed in 1.01s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `809 passed in 14.78s`。严格真实 FFmpeg smoke 用 5 秒 640×360、30fps、H.264/AAC 高码率样片完成 `plan → apply → verify --strict`：源片 `3,024,189 bytes`，交付件 `803,194 bytes`，低于 `838,860 bytes` 硬上限，最终 `blocking=0 / warnings=0`，H.264/AAC、`yuv420p`、640×360、30fps、5.013s 契约和全长解码全部通过。`.venv/bin/python -m compileall -q scripts tests`、四个 CLI help、manifest category、中文 brief route、Skill `quick_validate.py` 和 `git diff --check` 也均通过。

### 2026-08-01 自动化升级记录（Long-form Multicam Clock Drift）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`jianshuo/claude-skills` 的 wjs-syncing-multicam](https://github.com/jianshuo/claude-skills/blob/main/wjs-syncing-multicam/SKILL.md) | 用分布在长片中的多个 probe 拟合漂移，记录 slope、残差和可逆消费方式，而不是把首段固定 offset 当成全片真值 | 新增 opt-in 多窗口测量；保留原 `alignment.offset_seconds` 语义，另存参考时间参数化的 affine fit 和中点锚点，不偷换固定 offset |
| [`jianshuo/polysync` 的 sync.py](https://github.com/jianshuo/polysync/blob/main/src/polysync/sync.py) / [`verify.py`](https://github.com/jianshuo/polysync/blob/main/src/polysync/verify.py) | 每隔一段时间重做局部相关、线性拟合，并用样本索引验证残差；明确 raw PCM 上不能靠 `-itsoffset` 假装完成验证 | `decode_audio_envelope()` 新增向后兼容的 `start_seconds`，只解码短窗口；每个 probe 保存 offset/confidence/拒绝原因，fit 另有独立残差 gate |
| [`wingedonezero/Video-Sync-GUI` 的 linear correction](https://github.com/wingedonezero/Video-Sync-GUI/blob/main/vsg_core/correction/linear.py) | 把漂移率转换为 tempo/resample 因子，同时保留原始音轨，强调校正必须可逆 | 报告 `selected_audio_atempo_factor` / `advisory_video_setpts_multiplier`，但明确 `applied=false`；本轮不生成“校正副本”，也不只修音频而留下画面时钟 |
| [`BCM-Neurosurgery/video-sync-nbu` 的多锚点同步](https://github.com/BCM-Neurosurgery/video-sync-nbu/blob/main/scripts/align/sync.py) | 多锚点和 affine/RANSAC 说明长时间同步不是一个 offset 问题，也揭示中途重启需要分段模型 | 本轮只做单一线性时钟模型；跳时、停录重启、非线性漂移继续明确进入人工/分段处理边界 |

新增/调整能力：[`scripts/multicam_sync.py`](scripts/multicam_sync.py) 新增 `--measure-clock-drift`，在每个机位自己的可用重叠时长中默认均匀抽取 5 个 20 秒窗口，并在每个窗口的预测位置附近搜索 ±2 秒。置信度合格的 probe 会进入 exhaustive pairwise consensus，至少 4 个 inlier 且早晚跨度充足才做最小二乘复核，拟合 `offset(reference_time)=intercept+slope*reference_time`。报告以 `selected_audio_drift.v1` 明确限定所选音轨，保存 `offset_slope_ppm` / `source_rate_error_ppm`、测量和斜率分辨率、累计漂移、拟合残差、参考时间中点锚点、source-zero 映射和未应用的 advisory factors。累计漂移超过默认 80 ms 时写 `correction_required`，inlier 太少、残差超过独立 80 ms/两帧下限、搜索峰贴边或速率超出 ±5000 ppm 可信范围时写 `unreliable`；两者都会进入现有 strict/manifest review gate。默认不启用，旧固定 offset 结果不变。`scripts/audio_sync.py` 仅增加可选 `start_seconds`，旧调用保持兼容。脚本始终不修改原片，也不自动证明视频 PTS/其他音轨共享该时钟。

使用方式：运行 `python3 scripts/multicam_sync.py --reference-media origin/cam-a.mp4 --angle origin/cam-b.mp4 --angle origin/cam-c.mp4 --measure-clock-drift --output work/multicam_sync_plan.json --markdown work/multicam_sync_plan.md --strict`。先检查 Markdown 的 accepted/rejected probes、ppm、累计漂移和最大残差；只有 `stable` 才表示在本次阈值内未检出需要校正的线性漂移。`correction_required` 的 `atempo/setpts` 只是消费建议，必须在 NLE/FFmpeg 下游对同一路音频和视频应用同一时钟映射并再次验证头/中/尾。周期音乐、静音、混响或中途重启导致 `unreliable` 时，不要放宽 gate 取巧；应换清晰音轨、扩大局部搜索、使用可靠 LTC/timecode，或改用分段同步。

验证结果：新增/扩展 `tests/test_multicam_sync.py` 与 `tests/test_audio_sync.py`，覆盖 seek 参数、非法起点、已知正负 slope、高置信度 outlier 共识剔除、残差拒绝、窗口边界、advisory factor 符号和 strict review；定向 `.venv/bin/python -m pytest tests/test_audio_sync.py tests/test_multicam_sync.py -q` 通过 `26 passed in 2.39s`，最终全量测试通过 `672 passed in 11.73s`。真实 FFmpeg smoke 生成 60 秒确定性 PCM，分别用 `atempo=0.996` / `1.004` 制造反向时钟偏差；7 个 6 秒 probe 均得到 `correction_required`、`applied=false`，ppm 符号分别为负/正，`--strict` 均正确退出 2。`compileall`、CLI `--help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-04 自动化升级记录（Semantic Transcript Review）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`natyang1234/auto-edit-video-skill` 1.7.0 contextual semantic calibration](https://github.com/natyang1234/auto-edit-video-skill/blob/main/CHANGELOG.md) | 逐条读取全篇编号 transcript + 有界前后文，建议与复核分两层；覆盖率从源 transcript 推导，只应用精确、局部、高置信且通过确定性 guard 的补丁，不把模型 confidence 当人工批准 | 新增 provider-neutral 三阶段 CLI；不绑定 Ollama/云模型，由 `prepare` 给任何 Agent/模型统一 schema，`audit` 负责源哈希、覆盖、字符范围和最小补丁验证，`apply` 另需人工 choices |
| [`openakita/openakita` ClipSense skill](https://github.com/openakita/openakita/blob/main/plugins/clip-sense/SKILL.md) | 把视频任务拆成明确 pipeline step/status，并对 dependency/format/timeout 等失败提供可行动原因，而不是只返回一段生成文本 | semantic audit 写 `status`、coverage、proposal validation、`summary.blocking` 和 Markdown 操作表；本轮保持同步本地文件流程，不引入远端任务服务 |
| [`pockebot/openpocket` CapCut Edit skill](https://github.com/pockebot/openpocket/blob/main/skills/capcut-edit/SKILL.md) | 自动字幕之后仍要求快速 proofread，并在 major edit block 后验证真实结果，不把 AutoCut/自动字幕直接视为完成 | semantic choices 后仍明确要求进入 `transcript_review.py html` 对着真实媒体听审；建议、文字批准与音频事实三者分开 |
| [`thesongzhu/Friday` Video Editing Planner](https://github.com/thesongzhu/Friday/blob/main/skills/video-editing-planner/SKILL.md) | 优化节奏/结构时强调 preserve story clarity，避免编辑建议破坏原意 | 新工具只允许 ASR 类最小 patch；整句润色、数字/标点变化、超长/越界/重叠和跨段重复全部 fail closed，叙事改写继续交给独立 `rewrite_script.py` |

新增/调整能力：新增 [`scripts/semantic_transcript_review.py`](scripts/semantic_transcript_review.py)、[`tests/test_semantic_transcript_review.py`](tests/test_semantic_transcript_review.py) 和 [`docs/prompts/79-semantic-transcript-review.md`](docs/prompts/79-semantic-transcript-review.md)。`prepare` 把每个 segment 与前后 1–4 段上下文、规范化 segment SHA-256、硬规则和 response template 写成 JSON/Markdown；脚本不调用模型、不上传内容。`audit` 从原 transcript 自己推导完整 coverage，拒绝旧 source hash、未知/重复 segment、字符 span 不匹配、未裁掉相同前后缀的非最小 patch、数字/标点变化、空理由、越界/超长/重叠/重复提案和跨 segment 边界重复字，并生成稳定 `patch-*` / `review-*` id。合法 proposal 仍以 `pending_choices` 阻塞；`apply` 要求另一份 choices 绑定相同 canonical source hash 与 review id，逐条 `approve|reject`，成功后写 `semantic_review` metadata、重分配改动 segment 的词时间，并把 audit 更新为 ready。`pipeline_manifest.py` 新增存在即检查的 `semantic_transcript_review` gate；`edit_brief_plan.py` 可从“语义校稿/上下文校稿/专业术语错词”等 brief 自动路由到 prepare。README、SKILL、daily workflow、Transcript Review 文档和提示词索引已同步。

使用方式：运行 `python3 scripts/semantic_transcript_review.py prepare --transcript work/transcript.json --output work/semantic_review_request.json --markdown work/semantic_review_request.md`，让当前 Agent/模型按 request schema 生成 `work/semantic_review_response.json`；再运行 `python3 scripts/semantic_transcript_review.py audit --transcript work/transcript.json --response work/semantic_review_response.json --output work/transcript_semantic_review.json --markdown work/transcript_semantic_review.md --strict`。首次 strict 在有合法建议时退出 2 是预期人工 gate；从 Markdown 复制 choices template，逐项确认后运行 `python3 scripts/semantic_transcript_review.py apply --transcript work/transcript.json --audit work/transcript_semantic_review.json --choices work/semantic_review_choices.json --output work/transcript_semantic_reviewed.json --markdown work/transcript_semantic_review.md`，最后仍用同步媒体 HTML 听审。

验证结果：新增 12 项 semantic-review 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_semantic_transcript_review.py tests/test_transcript_review.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py -q` 通过 `98 passed in 0.98s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `715 passed in 11.90s`。覆盖上下文 packet、canonical source hash、完整/部分 coverage、最小补丁、数字/标点、精确 span、重叠提案、稳定 review id、旧 choices、approve/reject、词时间重分配、CLI 三阶段和 manifest/edit-brief gate；`.venv/bin/python -m compileall -q scripts tests`、四个 CLI help、manifest category、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-02 自动化升级记录（Hash-bound Approval Receipt）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`natyang1234/auto-edit-video-skill`](https://github.com/natyang1234/auto-edit-video-skill/blob/main/skills/auto-edit-video/SKILL.md) | 最终渲染冻结 state/source/asset/clip/approval hashes，版本化写出 MP4，并在 delivery receipt 与 contact sheet 都通过人工审批后才开放下载 | 新增标准库实现的 `approval_receipt.py`，把人工审过的视频、封面、文案、字幕和 QA 文件绑定到具体 SHA-256；本轮不引入 GUI、ZIP 或服务端状态 |
| [`WhiteTowerAI/cut-as-code` project schema](https://github.com/WhiteTowerAI/cut-as-code/blob/main/skills/video-understand/reference/project-schema.md) | 每个 operation 保存 `revision` / `based_on`，preview/render 前检查是否仍基于当前依赖，防止旧决策静默进入新渲染 | 最终交付层直接重算文件哈希，比只依赖人工维护 revision 更能证明当前待上传字节未漂移；上游通用 revision graph 留待后续独立设计 |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage/blob/main/AGENT_GUIDE.md) | pipeline stage 明确声明 success criteria、review focus 和默认人工审批，并禁止绕过 checkpoint/review | `pipeline_manifest.py` 新增可选但“存在即校验”的 approval gate；`publish_package.py` 强制现场重建 live manifest 并独立复核收据，不信任旧 snapshot |
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 安全工作副本、可重建 manifest、QA 后 transactional promotion，避免失败产物替换已验证输出 | 收据不改、不锁、不复制成片，只对显式稳定交付件做原子记录；改变、删除、symlink 漂移或哈希期间变化都会 fail closed |

新增/调整能力：新增 [`scripts/approval_receipt.py`](scripts/approval_receipt.py) 和 [`docs/prompts/77-approval-receipt.md`](docs/prompts/77-approval-receipt.md)。`create` 只接受项目内普通文件和显式重复 `--artifact`，保存项目相对路径、大小、修改时间与 SHA-256；拒绝重复路径、项目外路径、symlink、收据自身和会循环重写的 manifest/package/dashboard。`verify` 重新读取当前文件并输出 `current` / `changed` / `missing` / `unsafe` / `invalid`，`--strict` 在任何过期项上返回 2。`pipeline_manifest.py` 可用 `--require approval_receipt` 强制收据；只要发现收据，即使未显式 require 也会现场重算并阻塞 stale。`publish_package.py --require-approval-receipt` 还会确认当前选择的平台 MP4、封面、caption、字幕和章节都在收据覆盖范围内，并忽略旧 publish package 的循环 gate。`approved_by` 明确只是本地自报标签，不是身份认证、数字签名或发布授权。

使用方式：完整审片后运行 `python3 scripts/approval_receipt.py create --project-dir . --artifact output/day77_xhs.mp4 --artifact output/day77_douyin.mp4 --artifact output/cover.png --artifact output/day77_caption.json --artifact verify/render_qa.json --approved-by "Jay" --output verify/approval_receipt.json --markdown verify/approval_receipt.md`；上传前运行 `python3 scripts/approval_receipt.py verify --project-dir . --receipt verify/approval_receipt.json --output verify/approval_receipt_verification.json --strict`，再运行 `python3 scripts/publish_package.py --project-dir . --platforms xhs douyin wxch --require-approval-receipt --strict`。重新渲染、换封面或改文案/字幕后必须重新审查并用 `create --replace` 生成新收据，不能手改旧 hash。

验证结果：定向 `.venv/bin/python -m pytest tests/test_approval_receipt.py tests/test_pipeline_manifest.py tests/test_publish_package.py -q` 通过 `82 passed in 1.20s`；重放到最新主分支后，最终 `.venv/bin/python -m pytest tests -q` 通过 `690 passed in 12.25s`。CLI create/strict verify/stale exit-code、路径穿越、symlink、重复/self/volatile artifact、缺失/变更、旧 manifest、收据覆盖范围和 publish-package 循环 gate 均有回归测试；`.venv/bin/python -m compileall -q scripts tests`、三个 CLI help/category smoke 和 `git diff --check` 全部通过。

### 2026-08-03 自动化升级记录（Target Script Alignment）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`0xsline/OpenChatCut` 的 talking-head guide](https://github.com/0xsline/OpenChatCut/blob/main/src/agent/skills/talking-head-guide/SKILL.md) | 把 target-script alignment 视为独立 A-roll 任务：按目标含义和目标顺序选原话，并要求只保留指定词句范围，不把整段无关上下文一起带入 | 新增词级优先的本地匹配器；exact 文本只在 timed-unit 边界安全时收紧，只有 segment 时间戳时明确标记精度边界 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 用 phrase-level 多 take 阅读视图挑最佳表达，强调 cut 必须落在词边界，并为选择保留 source range / reason | 复用本项目 `takes_pack.py` 的 transcript 兼容层，但新增稳定 candidate id、透明 score breakdown、目标顺序重排和 choices 复核闭环 |
| [`Ronvaknins/FirstCut`](https://github.com/Ronvaknins/FirstCut) | 新闻制作中按记者提供的脚本/CSV 自动搭建 Premiere base sequence，先把选定原话装配好，再覆盖 visuals | 输出 `render_final.py` 可直接消费的 `render_config.json` 和规范化 `clean_script.md`；不依赖 Premiere 扩展或显式人工 timecode CSV |
| [`ayushozha/AdobePremiereProMCP`](https://github.com/ayushozha/AdobePremiereProMCP) | 从脚本与素材库生成 rough cut / edit decision，再在真实 NLE 里继续精修 | 保持本项目本地 artifact-first：只生成可审计计划、候选和 gate，后续可渲染或再导出 EDL/FCPXML/OTIO |

新增/调整能力：新增 [`scripts/script_alignment.py`](scripts/script_alignment.py) 和 [`docs/prompts/78-script-alignment.md`](docs/prompts/78-script-alignment.md)。脚本接受重复 `--transcript label=path`、`--transcripts-dir` 和可选 `--media label=path`，把 Markdown/文本目标稿按行或句拆分，对每个 unit 在多来源 word/segment 时间轴上搜索候选，并记录 sequence、target/source coverage、字符 n-gram overlap、length fit 与 exact evidence。默认禁止复用同一源时间段；低分、次优分差过小、无候选、素材未登记/缺失和显式 choice 冲突都写入 `summary.blocking`。第一次 review 后可用稳定 candidate id 写 `--choices`；人工 choice 只解决词面低分/多解，不绕过物理素材和时间重叠。脚本按目标稿顺序输出 `render_config.json`，并可另存 `clean_script.md` 供内容风控、分镜和发布文案使用。`edit_brief_plan.py` 新增“目标脚本/按稿剪”路由；`pipeline_manifest.py` 新增存在即阻塞未清 review 的 `script_alignment` gate。

使用方式：先运行 `python3 scripts/script_alignment.py --target-script work/target_script.md --transcript take-a=work/take-a_transcript_reviewed.json --transcript take-b=work/take-b_transcript_reviewed.json --media take-a=origin/take-a.mp4 --media take-b=origin/take-b.mp4 --output work/script_alignment.json --markdown work/script_alignment.md --render-config work/render_config.json --clean-script work/clean_script.md --strict`。如果返回 2，打开 Markdown 比较原话和时间码，把确认候选写入 `work/script_alignment_choices.json`，再加 `--choices` 重跑；只有 `summary.blocking=0` 后才进入 `edit_preflight.py` 和最终渲染。

验证结果：新增 `tests/test_script_alignment.py` 7 项，并扩展 edit-brief / pipeline-manifest 回归；定向 `.venv/bin/python -m pytest tests/test_script_alignment.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py -q` 通过 `76 passed in 0.78s`，最终 `.venv/bin/python -m pytest tests -q` 通过 `700 passed in 11.02s`。覆盖 Markdown 标题/逐句拆分、GPT-5.6 小数点保护、透明分数、目标顺序重排、多 take 同分阻塞、choices 复核、源时间防复用、缺素材 gate、CLI strict 两阶段和 clean-script/render-config 输出；`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、manifest category、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-05 自动化升级记录（Source-bound Edit Revisions）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AKMessi/vex`](https://github.com/AKMessi/vex) | 项目保存 timeline history 和可重建 operation，支持 undo/redo，避免撤销时重新猜测分析结果 | 新增本地 content-addressed before/after blobs 和线性 revision cursor；只管理文本剪辑 artifact，不复制其实现，也不引入非商用依赖 |
| [`0xsline/OpenChatCut`](https://github.com/0xsline/OpenChatCut) | agent 先在不改变 live timeline 的 session 中准备 proposal；一批通过审批的操作原子提交为一个 undo step，stale auto session 直接失败 | 新增 `prepare → audit → 独立 approval → apply`；多文件作为一个可回退 operation 写入，audit 后任何 proposal/base 漂移都会 fail closed |
| [`WhiteTowerAI/cut-as-code` project schema](https://github.com/WhiteTowerAI/cut-as-code/blob/main/skills/video-understand/reference/project-schema.md) | operation 明确保存 `revision` / `depends_on` / `based_on`，预览和渲染前要求依赖 revision 仍然匹配 | 本项目直接记录并实时重算 SHA-256，而不是信任手填 revision；redo 和 manifest 会拒绝已改变的 based-on dependency |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage/blob/main/AGENT_GUIDE.md) | 每个 pipeline stage 都有 checkpoint、canonical artifact、success criteria 和 human approval 状态 | revision journal、audit Markdown、approval artifact 和 `pipeline_manifest.py` live gate 都落在项目目录；本轮不引入服务端状态机 |

新增/调整能力：新增 [`scripts/edit_revision.py`](scripts/edit_revision.py)、[`tests/test_edit_revision.py`](tests/test_edit_revision.py) 和 [`docs/prompts/80-edit-revision.md`](docs/prompts/80-edit-revision.md)。`prepare` 只接受项目根或 `work/` 中已经存在的 UTF-8 JSON/Markdown/text/subtitle artifact，记录完整内容、基础 SHA-256 和可选 dependency SHA-256；拒绝源素材、代码、输出/验证目录、symlink、隐藏/volatile/self artifact 和超大文件。`audit` 检查 title/reason、真实变化、JSON 可解析性、重复路径、基础/依赖漂移并生成稳定 `review_id`；合法 proposal 仍以 `pending_approval` 阻塞。`apply` 要求另一份 approval JSON 绑定同一 review id，live 重审后把多个文件作为一个 operation 成组写入，并把 before/after bytes 存进 `work/.edit-revisions/blobs/`。`status`、`undo`、`redo` 会验证 journal、当前 artifact、已应用 dependency 和 blob；redo 分支默认保留，新路线必须显式 `--fork-history`，旧操作存入 `archived_branches[]`。`pipeline_manifest.py` 新增存在即实时验证的 `edit_revision_history` gate，`edit_brief_plan.py` 新增“修订历史/撤销剪辑/重做剪辑”路由；README、SKILL、daily workflow 和提示词索引同步。

使用方式：运行 `python3 scripts/edit_revision.py prepare --project-dir . --artifact work/render_config.json --artifact work/enrich_plan.json --depends-on work/transcript_reviewed.json --title "收紧开头" --reason "时间码审片后采用第二版" --output work/edit_revision_proposal.json`，只改 proposal 的 `artifacts[].proposed_content`；再运行 `audit --proposal work/edit_revision_proposal.json --output work/edit_revision_audit.json --markdown work/edit_revision_audit.md --strict`。合法 audit 因等待人工审批返回 2 是预期；从 Markdown 复制 approval template，填写 `decision: approve` 和 reviewer label 后运行 `apply --proposal ... --audit ... --approval ... --strict`。日常用 `status --strict`，需要时运行 `undo` / `redo`；依赖或文件被手工改过时先处理 stale，不得绕过 hash gate。最终待上传字节仍用 `approval_receipt.py`，不要用 edit revision 代替成片审批。

验证结果：新增 21 项 edit-revision 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_edit_revision.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py -q` 通过 `95 passed in 1.06s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `738 passed in 11.84s`。覆盖 prepare/audit hash、依赖漂移、非法 JSON、路径与 symlink 限制、独立审批、多文件单 operation、写入前二次校验、运行期写入失败回滚路径、exact-byte undo/redo、外部修改、旧 artifact 漂移、redo dependency、显式 history fork、blob/journal 损坏、CLI round-trip 和 manifest live gate；`compileall`、CLI help、manifest category、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-06 自动化升级记录（Shot Color QA）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`AKMessi/vex` 的 Auto Color Grading](https://github.com/AKMessi/vex#auto-color-grading) | 按 shot 采样曝光/对比/饱和度/白平衡候选，跳过转场帧；渲染后再次检查 clipping、极端亮度和高饱和，并把 shot/output validation 纳入 creative QA | 新增独立 post-render QA，不复制其非商用实现、不自动调色；用标准 FFmpeg `signalstats`、镜头内中位数和 cut margin 做可复现本地测量 |
| [`browser-use/video-use` 的 SKILL](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 要求在 rendered output 的每个切点复核 timeline view；调色采用“看一帧、只改一个问题、再看一次”的闭环 | Markdown 为每个可疑切点生成 `timeline_view.py` 命令；视觉跳变默认只 WARN，必须看 master 后再回源修复 |
| [`walterlow/freecut`](https://github.com/walterlow/freecut) | 把 waveform、vectorscope、histogram 作为一等 color scopes，并同时提供曝光、饱和度、temperature/tint 等可调属性 | 本项目补轻量 headless 数值报告和 manifest gate，不引入 WebGPU/编辑器运行时；文档明确它不是校准 scopes、HDR proof、白平衡或肤色判断 |

新增/调整能力：新增 [`scripts/shot_color_qa.py`](scripts/shot_color_qa.py)、[`tests/test_shot_color_qa.py`](tests/test_shot_color_qa.py) 和 [`docs/prompts/81-shot-color-qa.md`](docs/prompts/81-shot-color-qa.md)。脚本读取最终 master / platform export，可复用连续覆盖全片的 `scene_boundaries.v1`，否则自动运行 FFmpeg scene detection；再默认每秒抽 2 帧、缩到 320px 宽，用每镜头中位数汇总 `YLOW/YAVG/YHIGH`、U/V、`SATAVG`、`BRNG`。持续极暗/极亮、低对比、高饱和和相邻镜头亮度/色度跳变写 review warning；非 full-range 输出的 median `BRNG > 1%`、场景无 sample、外部 scene plan 重叠/缺口/越界会 fail closed。`--fail-on-extremes` / `--fail-on-jumps` 可把当前项目的人工策略升级为 blocker，`--ignore-broadcast-range` 会显式留在 params/flags。`pipeline_manifest.py` 新增存在即检查且可 `--require shot_color_qa` 的 gate；README、SKILL、daily workflow 和提示词索引已同步。

使用方式：运行 `python3 scripts/shot_color_qa.py output/day81_master.mp4 --output output/verify/day81_shot_color_qa.json --markdown output/verify/day81_shot_color_qa.md --strict`。已有场景计划时加 `--scene-boundaries work/scene_boundaries.json`；先看 Markdown 的 shot metrics / flagged cuts，再复制其中 `timeline_view.py` 命令看正常速度 master。确认问题后回到源 timeline、逐镜头 grade 或 `color_grade.py` 重渲染，不要对已压缩 master 反复套滤镜。发布前可用 `pipeline_manifest.py --require shot_color_qa --strict` 强制报告存在且无 blocker。

验证结果：新增 11 项 shot-color 单元/CLI/真实 FFmpeg 测试，并扩展 pipeline-manifest 回归；定向 `.venv/bin/python -m pytest tests/test_shot_color_qa.py tests/test_pipeline_manifest.py -q` 通过 `75 passed in 0.88s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `750 passed in 13.99s`。真实双路径 smoke：合法 2 秒渐变 H.264 得到 `ready / shots=1 / sampled_frames=4 / blocking=0 / warnings=0`；故意把白色视频推到 Y=255 后得到 `blocked / broadcast_range_exceeded=1 / 100% BRNG`，`--strict` 正确退出 2。`.venv/bin/python -m compileall -q scripts tests`、CLI help、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-07 自动化升级记录（Portable Edit Recipe）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`FireRedTeam/FireRed-OpenStoryline` 的 create_profile_style_skill](https://github.com/FireRedTeam/FireRed-OpenStoryline/blob/main/.storyline/skills/create_profile_style_skill/SKILL.md) | 能从当前 timeline 的节奏、叙事、音频、字幕、调色和工具参数提炼并归档可复用 Editing Skill，换素材后复刻风格 | 补上本项目“已有很多单次 artifact，但不能把已审 config 封装成复用单元”的缺口；本轮只处理确定性的 render config，不让模型自由生成可执行 Skill |
| [`KyaniteLabs/kinocut` 的 project recipes](https://github.com/KyaniteLabs/kinocut/blob/master/kinocut/aivideo/learning/project_recipes.py) | 从已验证 revision 导出无路径 recipe，把源 digest 换成参数槽；portable digest 防止模板漂移，replay 要求完整 binding 并产生新 revision | 采用 typed slot + canonical SHA-256 + exact binding；本项目直接复用 `edit_preflight.py` 和 JSON artifact，不引入 projectstore/CAS/通用 operation DSL |
| [`gooseworks-ai/goose-video` 的视频模板](https://github.com/gooseworks-ai/goose-video/tree/main/skills/templates) | 每种视频形式是自包含 recipe，明确素材输入、阶段顺序、确定性脚本和付费前人工 gate，适合批量替换品牌/产品素材 | recipe Markdown 明确 slot 表、回放 contract 和 human preview gate；保留现有脚本组合，不复制 provider SDK、资产或特定广告模板 |

新增/调整能力：新增 [`scripts/edit_recipe.py`](scripts/edit_recipe.py)、[`tests/test_edit_recipe.py`](tests/test_edit_recipe.py) 和 [`docs/prompts/82-edit-recipe.md`](docs/prompts/82-edit-recipe.md)。`export` 拒绝空 timeline、缺文件、远程输入和 source preflight blocker，递归参数化全部本地文件引用，同一路径只生成一个 typed slot；recipe 不保存源路径，只保存 occurrence、原文件 hash/size/suffix、源 config hash 和无路径 preflight 摘要。`verify` 现场检查 schema、非空 clips、slot 唯一性/类型/occurrence、未参数化路径、remote input、必需 preflight/human-preview 契约和 canonical `portable_sha256`。`replay` 要求 slot 集合精确相等，校验绑定文件存在且类型匹配，输出新 config、每个 binding 的 SHA-256 receipt 和 Markdown，再运行现有 `edit_preflight.py`；默认拒绝覆盖，`--strict` 在 warning/blocker 时返回 2。`pipeline_manifest.py` 新增存在即 live verify 的 `edit_recipe` gate；`edit_brief_plan.py` 新增导出/套用剪辑配方路由。配方 digest 不是签名或人工审批，也不证明新素材内容适合旧时间线。

使用方式：先运行 `python3 scripts/edit_recipe.py export --config work/render_config.json --name fast-tech-explainer --description "快节奏科技口播" --output work/recipes/fast-tech-explainer_edit_recipe.json --markdown work/recipes/fast-tech-explainer_edit_recipe.md`，再用 `verify --recipe ...` 复核。新项目运行 `replay --recipe ... --bind video_1=origin/new.mp4 --bind transcript_1=work/new_transcript_reviewed.json --output work/render_config.json --receipt work/edit_recipe_replay.json --markdown work/edit_recipe_replay.md --strict`；实际 slot 名以 recipe Markdown 为准，每个都必须绑定一次。成功后仍要渲染并人工审片，最后审批继续使用 `approval_receipt.py`。

验证结果：新增 11 项 edit-recipe 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_edit_recipe.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py -q` 通过 `89 passed in 1.03s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `764 passed in 12.36s`。覆盖路径去除与同源槽位去重、source preflight、canonical digest 篡改、重算 digest 后的路径泄漏、slot occurrence、精确 binding、类型错配、新 binding hash、CLI export/verify/replay round-trip、existing-output/input collision、replay preflight 和 manifest live gate；`.venv/bin/python -m compileall -q scripts tests`、全部新 CLI help、manifest category、Skill `quick_validate.py` 和 `git diff --check` 均通过。

### 2026-08-09 自动化升级记录（Source-bound Video Stabilization）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`KyaniteLabs/kinocut` 的 stabilization engine](https://github.com/KyaniteLabs/kinocut/blob/master/kinocut/engine_stabilize.py) | 执行前强制检查 `vidstabdetect`，两遍 motion detect/transform 使用受控绝对临时路径，失败时不留下错误向量文件 | 新增 `doctor` 和 source-bound backend record；`vidstab` 可用时使用两遍临时 transforms，plan/apply 期间不静默切换算法 |
| [`damionrashford/media-os` 的 ffmpeg-stabilize skill](https://github.com/damionrashford/media-os/blob/main/skills/ffmpeg-stabilize/SKILL.md) | 明确区分高质量两遍 `vidstab` 与内置单遍 `deshake` fallback，说明 re-encode、边缘/zoom 风险，并要求完整 A/B 复核 | 当前机器无 `vidstab` 时显式选择 `deshake`，把降级 warning 永久写进 artifact；apply 强制生成全长左右 comparison，不把 fallback 冒充等价实现 |
| [`Fagan1024/smart-video-editor`](https://github.com/Fagan1024/smart-video-editor/blob/main/SKILL.md) | 先逐帧判断模糊、抖动、曝光、遮挡和镜头可用性，再决定取舍；自动参数不能替代画面判断 | `decision=review|stabilize|keep` 需要人区分不想要的抖动和有意运镜；稳定版完整复核并 `confirm` 前一直阻塞，不自动按粗糙 motion score 改素材 |
| [`genchebur90-debug/video-editor-skill` 的 polish.py](https://github.com/genchebur90-debug/video-editor-skill/blob/main/video-editor/polish.py) | 稳定化先于降噪/锐化，默认保留音频，并提醒高 smoothing 会造成 floaty / jelly 观感 | 稳定化作为源素材 working-copy 阶段独立运行；原片不覆盖，后续再进入重构图、调色与渲染，review checklist 检查漂浮感和边缘扭曲 |

新增/调整能力：新增 [`scripts/video_stabilization.py`](scripts/video_stabilization.py)、[`tests/test_video_stabilization.py`](tests/test_video_stabilization.py) 和 [`docs/prompts/84-video-stabilization.md`](docs/prompts/84-video-stabilization.md)。`doctor` 报告本机 `vidstab` / `deshake` 能力；`plan` 绑定源文件 SHA-256、大小、duration、fps、尺寸、音频状态、确切 backend/profile、人工决定和 canonical plan id。默认 `decision=review` 阻塞；`stabilize/keep` 必须有 reviewer label。`apply` 只写新 H.264/AAC working copy 和全长 720p A/B comparison，默认拒绝已有目标、symlink、source/plan 自覆盖，并验证稳定版 duration / 尺寸 / 音频契约；`confirm` 要求非空复核 note 后才清除 blocker。live `verify` 会重算 derived state、检查 FFmpeg filter、源片/稳定版/comparison hash 和 review status；即使重写 plan id，非规范 backend/settings 或 stale summary 仍会失败。`pipeline_manifest.py` 新增存在即 live verify、可 `--require video_stabilization_plan` 的 gate；`edit_brief_plan.py` 新增手持抖动/视频防抖路由，README、SKILL、daily workflow 和提示词索引已同步。

使用方式：先运行 `python3 scripts/video_stabilization.py doctor`，看原片后用 `plan origin/handheld.mp4 --decision stabilize --reviewed-by editor --note "不想要的高频手抖" --output work/video_stabilization_plan.json --markdown work/video_stabilization_plan.md`；再执行 `apply work/video_stabilization_plan.json --output work/handheld-stabilized.mp4 --comparison verify/handheld-stabilization-compare.mp4 --markdown work/video_stabilization_plan.md`。以 1× 播放完整左右对照，确认人物、直线、四角、镜像边缘和有意 pan 无异常后，运行 `confirm ... --reviewed-by editor --note "完整 A/B 已看..."`，最后 `verify ... --strict`。稳定化不能修复 rolling shutter、运动模糊或失焦；下游只使用新 working copy，`origin/` 原片继续保留。

验证结果：新增 11 项 stabilization 单元/安全/lifecycle 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_video_stabilization.py tests/test_pipeline_manifest.py tests/test_edit_brief_plan.py -q` 通过 `95 passed in 0.95s`，全量 `.venv/bin/python -m pytest tests -q` 通过 `796 passed in 12.32s`。真实 FFmpeg smoke 在本机 `vidstab=missing / deshake=available` 环境用 3 秒合成抖动 H.264/AAC 样片完成 `plan → apply → confirm → verify --strict`：最终 `blocking=0 / warnings=1`（明确 fallback）、稳定版 `640×360 / 30fps / 3.008s / audio=true`，全长 comparison 与 SHA-256 application record 均生成。`.venv/bin/python -m compileall -q scripts tests`、全部新 CLI help、edit-brief route、manifest category、Skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-08-08 自动化升级记录（Source-bound Speed Ramp）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`mory128/ai-skills` 的 speed-ramp-video](https://github.com/mory128/ai-skills/blob/main/speed-ramp-video/SKILL.md) | 强调慢动作必须精确落在 impact frame，区分 snap / ease / S-curve，并明确低帧率源片在极慢速度下需要补帧和全速预览 | 新增显式 source-time ramp / hold、四种曲线、native unique-fps evidence 和 opt-in FFmpeg interpolation；不复制其付费 API，也不声称本地插值等同生成式补帧 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | creative 技法可以自由扩展，但最终必须在 rendered output、正常速度、带声音自检，尤其关注切点 flash / audio pop / overlay 错位 | Markdown 和 review contract 强制 1× + audio 复核 impact / snap / interpolation，并要求变速后重跑 render QA 和时间码产物 |
| [`Rajbharti06/Ultimate-Video-Editing-Skills`](https://github.com/Rajbharti06/Ultimate-Video-Editing-Skills/blob/main/skills/ultimate-video-editor/SKILL.md) | 把平滑 speed ramp、避免突兀速度跳变写进交付 checklist，并建议对速度变化使用 easing | 实现 `linear/ease/s_curve/snap`，把非连续边界写成 review warning；保持确定性 piecewise 近似，不引入通用 motion-graphics runtime |

新增/调整能力：新增 [`scripts/speed_ramp.py`](scripts/speed_ramp.py)、[`tests/test_speed_ramp.py`](tests/test_speed_ramp.py) 和 [`docs/prompts/83-speed-ramp.md`](docs/prompts/83-speed-ramp.md)。`plan` 把显式 ramp / hold 编译成连续 source/output pieces，绑定源 MP4 的 SHA-256、大小、duration、fps、audio 状态和 canonical plan id；支持 `0.1x–4x`、`linear/ease/s_curve/snap`、可调 steps、`--mute-audio` 和 opt-in `--interpolate-fps`。`verify` 不只比 digest，还重新规范化 events、重编 pieces、重算 warning / summary / status，并检查完整 coverage、逐段 `source_duration / speed`、输出 fps/audio 契约和 review contract；即使篡改者重写 plan id 也不能掩盖结构漂移。`apply` 用 `setpts + atempo` 同步音画，插值时使用 FFmpeg `minterpolate`，concat 后强制源 fps CFR；先写同目录临时 MP4，成功后才替换目标，默认拒绝覆盖、symlink 和源片自覆盖，可选输出 apply receipt。`pipeline_manifest.py` 新增存在即 live verify、可 `--require speed_ramp_plan` 的 gate；`edit_brief_plan.py` 新增局部变速 / velocity-edit 路由，README、SKILL、daily workflow 和提示词索引已同步。

使用方式：先逐帧确定 impact，再运行 `python3 scripts/speed_ramp.py plan origin/action.mp4 --ramp 4.6,5.0,1,0.25,s_curve --hold 5.0,5.8,0.25 --ramp 5.8,6.2,0.25,1,ease --interpolate-fps 120 --output work/speed_ramp_plan.json --markdown work/speed_ramp_plan.md`；随后 `verify work/speed_ramp_plan.json --strict`，看完 Markdown 后执行 `apply work/speed_ramp_plan.json --output work/action-speed-ramped.mp4 --receipt work/speed_ramp_apply.json`。最终必须 1× 带音频播放，检查 impact frame、插值伪影和极慢音频；把新 MP4 作为新的 source 进入 render config，旧字幕 / cue / timecoded artifact / approval receipt 不可复用。

验证结果：新增 15 项 speed-ramp 单元、篡改、CLI 和真实 FFmpeg 测试，并扩展 pipeline-manifest / edit-brief 回归；定向 `.venv/bin/python -m pytest tests/test_speed_ramp.py tests/test_edit_brief_plan.py tests/test_pipeline_manifest.py -q` 通过 `96 passed in 1.32s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `782 passed in 12.67s`。独立真实 smoke 用 4 秒 320×180、30fps、H.264/AAC 样片执行 `plan → verify → apply`：3 个 events 编译为 19 个 pieces，`blocking=0 / warnings=0`，输出 `5.133333s / 30fps / audio=true`；烟测还定位并修复了 concat 后 average fps 漂移，最终强制 CFR。一次全量复跑中既有 speech-denoise 真实音频测试因浮点表示把恰好 `3.0 dB` 误判为低于阈值，隔离复跑及随后完整全量均通过，本轮未改该音频链或测试。`.venv/bin/python -m compileall -q scripts tests`、四个 speed-ramp CLI help、edit-brief help、manifest category、Skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-30 自动化升级记录（Beat Edit Plan）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`genchebur90-debug/video-editor-skill` 的 rhythm.py](https://github.com/genchebur90-debug/video-editor-skill/blob/main/video-editor/rhythm.py) | 不只检测 BPM，还按 musical phrase 直接提出 beat-snapped cut plan，并用不同 `beats_per_cut` 控制节奏密度 | 补上本项目原来只有“吸附已有切点”、不能从 BGM 起草剪辑骨架的缺口；默认每 4 拍一刀，同时受最短/最长镜头约束 |
| [`carrxau/clip-studio` 的 beat grid](https://github.com/carrxau/clip-studio/blob/main/clip-studio/SKILL.md) | 用绝对帧/时间边界表达 beat grid，先展示 EDL 再渲染，避免逐段取整造成累积漂移 | 输出单一 program-time `cut_times[]` / `segments[]`，供人工复核后再映射素材；本轮不直接渲染或改动原片 |
| [`gitethanwoo/video-editing` 的 video_analyzer.py](https://github.com/gitethanwoo/video-editing/blob/main/skills/analyze-video-editing/scripts/video_analyzer.py) | `librosa` 节拍结果明确定位为导航证据、要求人工复核；兼容新版 `beat_track` 返回的一元素 ndarray tempo | 新增 detector provenance 和 fallback 警告，并修复 `librosa 0.11` tempo ndarray 被旧 `float(...)` 路径误判为失败的问题 |
| [`WhiteTowerAI/cut-as-code`](https://github.com/WhiteTowerAI/cut-as-code/blob/main/AGENTS.md) | 把 JSON cut plan 当作可读、可 diff、可重跑的“剪辑代码”，由脚本保证时间精度、由人决定内容 | 新增 `beat_edit_plan.v1` JSON + Markdown review；计划只提供节奏时间槽和逐切点 evidence，不冒充已完成内容剪辑 |

新增/调整能力：扩展 [`scripts/beat_sync.py`](scripts/beat_sync.py)，保留原有 `--cuts` ±200 ms 吸附模式，并新增 `--generate-plan`。计划可从 BGM 时长或显式 `--duration` 生成 program-time 时间槽，默认每 4 拍提出切点；`--min-segment` / `--max-segment` 会在附近 beat 中改选，完全没有合适 beat 时才加入 `duration_guard`，并保护结尾最短镜头。JSON 保存 detector method、beat times、1-based beat index、选择原因、warnings 和 summary；Markdown 提供可直接审片的 slot 表。缺少 `librosa`、音频读取失败或 detector 失败时仍可用固定 BPM 网格继续，但显式写成 `detection.method=fallback_grid`、`status=review`，不会伪装成真实测得节拍。新版 `librosa` 的一元素 ndarray tempo 也已兼容。

使用方式：运行 `python3 scripts/beat_sync.py --bgm origin/bgm.mp3 --generate-plan --duration 30 --beats-per-cut 4 --min-segment 0.75 --max-segment 3 --output work/beat_edit_plan.json --markdown work/beat_edit_plan.md`。先听 BGM，并逐项核对 Markdown 的 cut time、beat index、duration guard 和 warning；确认后再把素材映射进这些时间槽或转换到 render config / EDL / OTIO。该命令不选择镜头、不渲染、不修改源文件。已有切点仍运行 `python3 scripts/beat_sync.py --bgm origin/bgm.mp3 --cuts work/cuts.json --window 0.2 --output work/cuts_snapped.json`。

验证结果：新增/扩展 `tests/test_beat_sync.py`，覆盖旧 cut snap、固定网格、每 4 拍选点、镜头时长守卫、最短尾镜头、fallback review、Markdown、CLI 输出、非法约束和 `librosa` ndarray tempo；定向 `.venv/bin/python -m pytest tests/test_beat_sync.py tests/test_auto_enrich.py -q` 通过 `21 passed in 1.77s`，最终 `.venv/bin/python -m pytest tests -q` 通过 `664 passed in 11.29s`。真实 FFmpeg click track + `librosa 0.11.0` smoke 检测到 `119.68 BPM / 15 beats`，输出 `2.016s / 4.011s / 6.016s` 三个切点、4 个时间槽、beat index `4 / 8 / 12`，`status=ready`、`warnings=0`；缺失音频路径 smoke 则正确退回 120 BPM 固定网格并标记 `status=review`。`.venv/bin/python -m compileall -q scripts tests`、CLI help、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-28 自动化升级记录（Source-safe Multicam Sync）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`jianshuo/claude-skills` 的 wjs-syncing-multicam](https://github.com/jianshuo/claude-skills/blob/main/wjs-syncing-multicam/SKILL.md) | 把多机位同步作为独立 skill；只写可逆 offset/coverage 元数据，不生成一批有损“同步副本” | 新增一份项目级 `multicam_sync_plan.v1`，原片不改；只有明确 `--apply-preview` 才生成短审查副本 |
| [`jianshuo/polysync`](https://github.com/jianshuo/polysync) | 用音频能量包络抵抗设备增益/频响差异；多音轨专业相机先找真正有声的轨道，并明确记录 overlap | 复用现有标准库包络相关算法；新增最响 `0:a:N` 选择、每路 reference/source coverage 和所有机位公共重叠区间 |
| [`samuelgursky/davinci-resolve-mcp` 的 multicam setup helper](https://github.com/samuelgursky/davinci-resolve-mcp/blob/main/docs/guides/multicam-setup-guide.md) | 先 dry-run/分析，再用 record offset 准备多机位；保留源素材，并要求视觉/听觉复核后才进入 Resolve 原生 multicam | 输出可审计 JSON/Markdown、显式 preview 命令和 manifest gate；本轮不冒充 Resolve 原生 multicam clip，也不自动切镜 |

新增/调整能力：新增 [`scripts/multicam_sync.py`](scripts/multicam_sync.py)，以 `--reference-media` 为公共时钟，重复 `--angle` 对齐任意多路相机/手机/录音素材。自动模式复用 `audio_sync.py` 的 8 kHz 音频包络相关，默认搜索 ±60 秒；多音轨输入用 FFmpeg `volumedetect` 选择中段最响轨，也可逐文件覆盖。报告保存每路 offset、score/confidence、音轨选择证据、reference/source coverage、公共重叠区间、source-safe 声明和短网格预览命令。三路以上自动素材会做非参考机位 pairwise 传递一致性检查；低置信度、无重叠、缺文件或 offset 不一致写入 `summary.blocking`。手工 offset 可接无音轨机位，但保留“未独立验证”警告。`pipeline_manifest.py` 新增 `multicam_sync` 可选阻塞 gate；`audio_sync.decode_audio_envelope()` 只增加可选音轨 index，旧调用保持不变。

使用方式：运行 `python3 scripts/multicam_sync.py --reference-media origin/cam-a.mp4 --angle origin/cam-b.mp4 --angle origin/cam-c.mp4 --output work/multicam_sync_plan.json --markdown work/multicam_sync_plan.md --preview-output output/verify/multicam_sync_preview.mp4 --apply-preview --strict`。先查看 Markdown 的 offset、confidence、audio stream、coverage 和 pairwise divergence，再完整播放预览的拍手、口型或屏幕动作；计划通过后才把 offset 接入 NLE/OTIO/FCPXML 或后续多机位剪辑。有效麦克风不是首轨时用 `--audio-stream "origin/cam-b.mp4=2"`；无音轨或已有拍板点时用 `--manual-offset "origin/cam-c.mp4=1.24"`。V1 不做自动切镜、漂移校正、timecode jam 读取或视频特征同步，长片必须复核头/中/尾。

验证结果：新增 `tests/test_multicam_sync.py` 12 项，并更新 `audio_sync` / `pipeline_manifest` 回归；定向 `.venv/bin/python -m pytest tests/test_multicam_sync.py tests/test_audio_sync.py tests/test_pipeline_manifest.py -q` 通过 `73 passed in 1.40s`，最终项目测试 `.venv/bin/python -m pytest tests -q` 通过 `652 passed in 8.21s`。真实 FFmpeg smoke 把同一组音画整体延后 `0.4s`，脚本得到 `offset=-0.4`、`confidence=1.0`、公共 overlap `2.6s`、`blocking=0`，成功渲染 1 秒 960×270 双栏预览；对齐后左右画面 `SSIM All=0.974652`。另一个真实多音轨 smoke 在两条 AAC 轨中正确选择更响的 `0:a:1`，并覆盖无音轨机位 + 手工 offset；预览失败回归确认报告仍写入，且 `summary.preview_failed=1`、`preview_render_failed` 和 strict 阻断同时生效。`.venv/bin/python -m compileall -q scripts tests`、CLI help、manifest category、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-27 自动化升级记录（Opt-in Speech Denoise）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`clawic/skills` 的 Video Audio Enhancement](https://github.com/clawic/skills/blob/main/skills/video-edit/audio.md) | 把降噪、EQ、压缩、de-ess、响度规范化拆成有顺序的旁白修复链 | 只吸收本项目当前明确缺失的稳态底噪处理；不照搬会削薄男声的 200 Hz 高通，也不新增云端服务 |
| [`oktaydbk54/vibeclip` 的 denoise stage](https://github.com/oktaydbk54/vibeclip/blob/main/pipeline/denoise.py) | 用 FFmpeg `afftdn` 做轻/中/强 preset，并明确放在 music ducking、SFX、loudnorm 之前 | 在 `render_final.py` 的单次编码图里加入同类 opt-in preset，让 BGM sidechain 使用清理后的旁白 |
| [`edhaynes/eds-rules` 的 gentle clean-audio](https://github.com/edhaynes/eds-rules/blob/main/demo/clean-audio.sh) | 85 Hz 高通 → 10 dB FFT 降噪 → loudnorm；删除了会切掉尾音的激进 noise gate，并写清无法修复的噪声边界 | 采用固定 80 Hz、默认关闭、必须 A/B 试听；不加 gate，不把瞬态噪声/混响包装成可自动修复 |
| [`linuxmatters/jive-vocals` 的音频链说明](https://github.com/linuxmatters/jive-vocals/blob/main/AGENTS.md) | 成熟实录语料把 FFT reduction 约束在 12 dB，并解释压缩前降噪、过强处理和数字静音 warble 风险 | `strong` 上限固定 12 dB；文档明确 VAD/gate 后的数字静音、多麦和噪声突变素材应保持 off 或先试听 |
| [`puuku0510/chotto-tachiyotte-skill`](https://github.com/puuku0510/chotto-tachiyotte-skill/blob/master/SKILL.md) | 把 SileroVAD、短促咳嗽候选和频谱证据纳入真人口播清理 | 记录为后续独立能力；本轮不引入 ONNX/VAD，也不把稳态 FFT 降噪错误宣传成咳嗽检测 |

新增/调整能力：`scripts/render_final.py` 新增 `--speech-denoise light|medium|strong` / `--no-speech-denoise`，render config 可写 `"speech_denoise": "light|medium|strong|off"`。三个 preset 固定使用 80 Hz、2-pole 高通，FFT reduction 分别为 6/9/12 dB；滤镜顺序是 `highpass → afftdn → atempo → dynaudnorm → acompressor → loudnorm → cover delay → BGM ducking/mix`。默认 `off`，旧项目输出不变；配置只接受明确字符串，非法 bool/强度在 FFmpeg 启动前退出 2。同步更新 SKILL、每日工作流、提示词索引和 [Speech Denoise 文档](docs/prompts/75-speech-denoise.md)。

使用方式：先对同一段 10–20 秒口播 A/B 试听 `off` 与 `--speech-denoise light`，确定只有稳定的空调/风扇/电流底噪时再试 `medium`；`strong` 必须确认没有 watery/metallic/warble artifact。已经经 Adobe Podcast、Descript、RX、VAD/noise gate 或机内强降噪的音轨保持 `off`。渲染后继续跑 `audio_master_report.py --strict` 并正常速度试听；这一步只修最终听感，不会改善上游 ASR/jump cut 使用的源音轨。

验证结果：新增 `tests/test_speech_denoise.py` 15 项，并更新旧 audio-chain 回归；定向 `/Users/maxazure/projects/video-editing-skill/.venv/bin/python -m pytest tests/test_speech_denoise.py tests/test_audio_chain.py tests/test_bgm_ducking.py -q` 通过 `30 passed in 1.18s`，最终全量通过 `638 passed in 17.88s`。真实 3 秒 H.264/AAC smoke 把 50 Hz 震动、持续白噪声和 1 kHz speech tone 混合后走 `strong` + 默认完整响度链：说话窗口的 band-limited SNR 从 `28.3 dB` 提升到 `31.8 dB`（`+3.5 dB`），50 Hz rumble 相对 voice 改善 `7.5 dB`，输出时长仍为 `3.000s`。Python compileall、CLI `--help`、skill `quick_validate.py` 和 `git diff --check` 全部通过；系统 Python 3.9 因仓库既有 `str | None` 语法在收集阶段不兼容，最终测试使用项目 Python 3.12 虚拟环境。

### 2026-07-26 自动化升级记录（Source-time Edit Compare）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`WhiteTowerAI/cut-as-code` 的 video-edit-compare skill](https://github.com/WhiteTowerAI/cut-as-code/blob/main/skills/video-edit-compare/SKILL.md) | 左侧连续原片，右侧把最终交付像素投回 source clock；删段置黑，并检查时长、像素和音轨 | 复用本项目既有 `rough_cut.py` / `jump_cut.py` `keep_segments`，支持全局 speed/offset，不引入另一套 timeline schema |
| [`nopefallacy/vertical-video-editing-skills`](https://github.com/nopefallacy/vertical-video-editing-skills/blob/main/skills/video-editing/SKILL.md) | 要求对最终 render 做 ffprobe + frame spot-check，不能只相信 preview | 渲染后自动检查双栏尺寸/时长/音轨，并抽样验证删段黑屏与保留段 final-frame 像素 |
| [`znyupup/ai-video-editing-skill`](https://github.com/znyupup/ai-video-editing-skill/blob/main/SKILL.md) | 把成品 QC 抽帧和可视化 review 当成正式交付阶段 | 同时输出可播放 MP4、机器可读 JSON 和人工 Markdown；报告可进入 pipeline manifest gate |
| [`Jaycheng1103/chatgpt-video-editing-skills` 的八步工作流](https://github.com/Jaycheng1103/chatgpt-video-editing-skills/blob/main/skills/chatgpt-short-video-editor/references/eight-step-workflow.md) | 把完整预览、QA 和正式定稿分成明确阶段 | 将本工具定位为最终 render 之后的结构剪辑复核，不替代 `render_qa.py`、发布 gate 或人工审片 |

新增/调整能力：新增 [`scripts/edit_compare.py`](scripts/edit_compare.py)，读取单一原片、实际最终成片和已有 cut list，生成 `original-vs-final-source-time` 双栏 MP4。左栏保持原片连续时钟；右栏按 `keep_segments` 计算 final program range，统一 `--output-speed` 后恢复到 source duration，删除范围用黑屏补齐。每个 part 的帧数来自绝对 source-time 边界，避免逐段独立取整造成累积漂移。默认复制原片时钟音轨，并在渲染后验证输出时长、尺寸、音轨、代表性删除段黑屏和保留段像素。JSON/Markdown 保存完整 source/program mapping 与抽样证据；`--dry-run` 可先写未完成计划。`pipeline_manifest.py` 新增 `edit_compare` gate，并避免把文件名含 `edit_compare` 的审片视频误识别成 master。

使用方式：运行 `python3 scripts/edit_compare.py origin/talking.mp4 output/master.mp4 --cut-list work/rough_cut.json --output-speed 1.25 --output-offset 2.0 --output output/verify/source_vs_final.mp4 --report output/verify/edit_compare.json --markdown output/verify/edit_compare.md`。左栏看原片，右栏黑屏表示已删范围；映射整体错位时先校正 speed/offset，再重跑。V1 会拒绝被 block、空、重叠、乱序或越界的 cut list，不支持多来源、镜头重排、逐段不同速度、倒放或非线性 time warp。

验证结果：新增 `tests/test_edit_compare.py` 8 项，更新 `tests/test_pipeline_manifest.py` 2 项；定向 `.venv/bin/python -m pytest tests/test_edit_compare.py tests/test_pipeline_manifest.py -q` 通过 `61 passed in 1.05s`，最终全量 `.venv/bin/python -m pytest tests -q` 通过 `623 passed in 6.79s`。真实 FFmpeg smoke 用 2 秒动态测试片删除中间 0.4 秒，并把保留段加速 2×、前置 0.2 秒片头，成功生成 320×90 双栏 MP4；报告得到 `2 kept / 1 dropped / 3 verification samples / blocking=0`，删除段黑屏、保留段 final-frame 像素、speed/offset 映射和 source-clock 音轨均通过；同一素材的 `--no-audio` 分支也通过。另有 rotation metadata、dry-run/strict 返回码和 final-too-short 诊断测试。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-25 自动化升级记录（Platform Safe Area QA）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`dansugc/reelclaw` 的 green-zone 指南](https://github.com/dansugc/reelclaw/blob/main/references/green-zone.md) | 给出 TikTok / Reels / Shorts 的像素级 UI 遮挡边界，并提供一个跨平台保守交集 | 转成可缩放 profile，支持 XHS 3:4 和 9:16 画布；不把社区值表述为永久官方规范 |
| [`hugobowne/show-us-your-agent-skills` 的 Remotion skill](https://github.com/hugobowne/show-us-your-agent-skills/blob/main/skills/remotion-video/SKILL.md) | 把竖屏 critical content 安全区纳入渲染前 checklist | 新增独立、确定性的发布前 gate，并输出可审查的 JSON / Markdown / SVG evidence |
| [`cognyai/claude-code-marketing-skills` 的 TikTok launch video skill](https://github.com/cognyai/claude-code-marketing-skills/blob/main/skills/tiktok-launch-video/SKILL.md) | 明确要求顶部和底部为平台 UI 留白，避免 CTA / 字幕被遮挡 | 内置 TikTok / Douyin 保守边距，允许用当前 App 截图的实测像素覆盖 |
| [`iart-ai/tiktok-video-skills` 的 lower-thirds skill](https://github.com/iart-ai/tiktok-video-skills/blob/main/skills/lower-thirds/SKILL.md) | 同时考虑 top/bottom title-safe 与右侧互动按钮 rail | bbox 检查四边 breach；PIP、focus marker、字幕和自定义 CTA 都纳入同一个报告 |

新增/调整能力：新增 [`scripts/platform_safe_area_qa.py`](scripts/platform_safe_area_qa.py)，可读取 `render_config`、多个 enrich plan 和自定义 `elements[]`，复用 `render_final.py` 的默认字幕、PIP、badge 与 focus marker 布局估算。报告包含每个元素 bbox、四边 breach、来源和可操作修复提示；关键元素越界 BLOCK，`critical: false` 只 WARN，`--strict` 在 blocker 存在时返回 2。新增 SVG guide 方便人工查看绿色 safe rectangle 与元素框。`pipeline_manifest.py` 新增 `platform_safe_area_qa` artifact category，报告存在且 `summary.blocking > 0` 时阻塞，也支持 `--require platform_safe_area_qa`。同步更新 SKILL、每日小红书工作流、提示词索引和 [Platform Safe Area QA 文档](docs/prompts/73-platform-safe-area-qa.md)。本工具不上传、不调用 LLM、不做 OCR，也不会推断图片内部主体位置。

使用方式：渲染前运行 `python3 scripts/platform_safe_area_qa.py --config work/render_config.json --enrich-plan work/enrich_plan.json --platform xhs --output verify/platform_safe_area_qa.json --markdown verify/platform_safe_area_qa.md --guide verify/platform_safe_area_guide.svg --strict`。多平台发布要按目标平台分别运行；当前 UI 与 profile 不一致时传 `--safe-left`、`--safe-top`、`--safe-right`、`--safe-bottom` 实测像素。自定义 CTA / Logo 可通过 `--elements work/platform_elements.json` 声明 pixel 或 normalized bbox。

验证结果：新增 `tests/test_platform_safe_area_qa.py` 9 项，更新 `tests/test_pipeline_manifest.py` 2 项；定向 `.venv/bin/python -m pytest tests/test_platform_safe_area_qa.py tests/test_pipeline_manifest.py -q` 通过 `60 passed in 0.65s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `613 passed in 7.45s`。真实 CLI smoke 用一个位于底部 UI 区的 normalized CTA 同时生成 JSON、Markdown 和 SVG，正确得到 `blocked`、`blocking=1`、`breaches=["bottom_ui"]`，strict 退出码为 2。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、manifest category smoke、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-24 自动化升级记录（Cross-source Visual Dedupe）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`mazsola2k/ai-video-editor` 的 Resolve exporter](https://github.com/mazsola2k/ai-video-editor/blob/main/export_resolve.py) | 用场景感知哈希在多条视频之间去重；重复时保留 `quality_score` 更高的镜头，直接服务最终时间线 | 新增跨来源 scene candidate 比较和显式质量分优先的保留建议，但不自动改时间线 |
| [`SysAdminDoc/OpenCut` 的 duplicate detector](https://github.com/SysAdminDoc/OpenCut/blob/main/opencut/core/duplicate_detect.py) | 每条视频在 10%/50%/90% 取三帧、聚类相似内容，并用清晰的 review group 推荐保留更高质量版本 | 对每个场景而非只对整条视频做三点采样；用 union-find 形成重复组，保留逐 pair evidence 和建议排除列表 |
| [`Parakh20/AI-Video-Editor` 的 duplicate frame detection](https://github.com/Parakh20/AI-Video-Editor/blob/main/src/ai_video_editor/vision/duplicate_frame_detection.py) | 64-bit average hash + Hamming distance 足够轻量，连续帧测试覆盖清楚 | 继续只依赖 FFmpeg + Python 标准库；使用 64-bit dHash，并增加平均 RGB 距离，避免不同纯色/低纹理帧都产生零哈希的误报 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 检测结果只是编辑决策证据，最终镜头和切点必须在渲染前后人工复核 | 输出 JSON + Markdown review artifact；脚本永不删除/移动源文件，`--strict` 只阻塞到重复组被人工处理 |

新增/调整能力：新增 [`scripts/visual_dedupe.py`](scripts/visual_dedupe.py)，可直接比较整条视频，也可通过 `sources[]` manifest 读取多条视频及各自的 `scene_boundaries.v1`。每个候选场景按 10%/50%/90% 取三帧，生成 64-bit dHash + mean-RGB 签名；默认要求至少 2/3 个采样点、场景时长比不低于 0.5、综合距离不超过 8，且只比较不同来源。重复 pair 用 union-find 合并为 review group；保留建议依次参考 `quality_score`、分辨率和源文件大小。`visual_dedupe.v1` 保存逐样本 Hamming/color distance、重复组、`recommended_keep` 和 `suggested_exclusions`；Markdown 直接链接本地媒体并展开 10%/50%/90% 的时间与距离证据。`pipeline_manifest.py` 会发现该 artifact，并在仍有重复组或无法解码的候选时阻塞。脚本不会删除、移动、覆盖素材，也不会把视觉相似误写成内容等价；strict 退出码 2 表示 review gate 未清，不表示源素材被改动或运行崩溃。

使用方式：先为每条素材生成 scene boundaries，再创建 `work/visual_dedupe_sources.json`，例如 `{"sources":[{"id":"cam-a","video":"../origin/cam-a.mp4","scene_boundaries":"cam-a-scenes.json","quality_score":0.9},{"id":"cam-b","video":"../origin/cam-b.mp4","scene_boundaries":"cam-b-scenes.json","quality_score":0.8}]}`；相对路径以 manifest 所在目录为基准。运行 `python3 scripts/visual_dedupe.py --manifest work/visual_dedupe_sources.json --output work/visual_dedupe.json --markdown work/visual_dedupe.md --strict`，逐组打开报告里的 source range，确认镜头在编辑语义上可互换后，只从下游 edit plan 排除建议项。只想检查整条文件是否重复时，也可直接传 `origin/a.mp4 origin/b.mp4`。

验证结果：定向 `.venv/bin/python -m pytest tests/test_visual_dedupe.py tests/test_pipeline_manifest.py -q` 通过 `59 passed in 0.50s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `602 passed in 6.78s`。真实 FFmpeg smoke 用同一动态测试片生成不同分辨率/CRF 的两份视频，并加入一条蓝色非重复视频，CLI 得到 `3 candidates / 1 duplicate pair / 1 group / 0 failed`，只把两份同源画面归为一组。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-23 自动化升级记录（Adaptive Scene Boundaries）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`byteplus-sa/polym` 的 reference video analyzer](https://github.com/byteplus-sa/polym/blob/main/skills/polym-explainer-video/scripts/analyze_reference_video.py) | 直接用 FFmpeg `scene` score 统计 cut，依赖轻、可复现，适合作为 agent workflow 的基础视觉信号 | 保留原有 `--method fixed --threshold` 路径，避免已有项目结果漂移；自适应模式仍只依赖 FFmpeg |
| [`Breakthrough/PySceneDetect` 的 AdaptiveDetector](https://github.com/Breakthrough/PySceneDetect/blob/main/scenedetect/detectors/adaptive_detector.py) | 当前帧变化分数与前后邻域滚动均值比较，同时要求最小绝对 content score 和最短场景长度，可减少快速运镜造成的误检 | 用 FFmpeg `lavfi.scene_score` 实现同类局部峰值判断，新增 ratio、绝对 score、窗口宽度和最短场景参数，不引入 OpenCV/PySceneDetect 运行时 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 把切点视为必须复核的生产决策，要求对最终时间线逐切点查看 evidence，而不是把检测器输出直接当答案 | `scene_boundaries.v1` 新增 `boundary_evidence[]` 和 Markdown cut evidence 表；视觉边界仍只供 highlight snap、抽样和节奏 QA，不能替代内容/音频复核 |

新增/调整能力：[`scripts/scene_boundaries.py`](scripts/scene_boundaries.py) 新增 `--method adaptive`。脚本用 FFmpeg 为每帧打印 `lavfi.scene_score`，仅当目标帧达到 `--min-scene-score` 且相对前后 `--window-width` 帧均值的 ratio 达到 `--adaptive-threshold` 时保留切点；持续摇镜、游戏画面、手持走拍和高运动 B-roll 因此不容易被固定阈值切成密集碎片。JSON 保持 `scene_boundaries.v1` 兼容，新增逐切点 `score`、`adaptive_ratio`、`local_average` evidence；Markdown 同步展示。原 `--method fixed --threshold 0.35` 行为保留。README 核心能力/测试入口、SKILL 流水线和 [`docs/prompts/32-scene-boundaries.md`](docs/prompts/32-scene-boundaries.md) 已更新。

使用方式：运行 `python3 scripts/scene_boundaries.py origin/long-talk.mp4 --method adaptive --adaptive-threshold 3.0 --min-scene-score 0.15 --min-scene-duration 1.0 --output work/scene_boundaries.json --markdown work/scene_boundaries.md`；先看 Markdown 的 cut evidence，再把 JSON 传给 `highlight_picker.py --scene-boundaries`、`video_understanding.py --scene-boundaries` 或 `retention_rhythm_qa.py --scene-boundaries`。需要复现旧结果时显式用 `--method fixed`。

验证结果：`.venv/bin/python -m pytest tests/test_scene_boundaries.py -q` 通过 `12 passed in 0.08s`，覆盖 FFmpeg metadata 解析、局部 spike、持续运镜拒绝、零邻域均值、evidence 去重、固定/自适应命令和 CLI saved-log round-trip；关联 `.venv/bin/python -m pytest tests/test_scene_boundaries.py tests/test_highlight_picker.py tests/test_video_understanding.py tests/test_retention_rhythm_qa.py -q` 通过 `39 passed in 0.27s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `590 passed in 6.74s`。真实 FFmpeg smoke 生成 2 秒红/蓝两场景 H.264 视频，自适应 CLI 只检出 `1.0s` 的唯一硬切并记录 `score=0.4`、`adaptive_ratio=255`、`local_average=0`。CLI `--help`、Python compileall、skill `quick_validate.py` 和 `git diff --check` 通过。

### 2026-07-22 自动化升级记录（Interactive Transcript Review）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`hoodini/ai-agents-skills` 的 HyperFrames Transcript Editor](https://github.com/hoodini/ai-agents-skills/tree/master/skills/video-edit/transcript-editor) | 本地浏览器里加载 transcript + 视频，点击 segment seek、播放高亮、行内编辑、字典修正、查找替换和 review.txt 导出，把“渲染前人工批准”做成明确 checkpoint | 复用本项目已有 `transcript_review.py export/apply` 格式，新加自包含 `html` 入口；不引入 WebLLM、CDN、server 或 1–2GB 浏览器模型下载 |
| [`literatecomputing/transcribe-with-whisper`](https://github.com/literatecomputing/transcribe-with-whisper) | 生成带媒体播放器的 HTML transcript，点击文本可跳到对应媒体时间，同时强调敏感素材在本机处理 | 页面预载本地 `file://` 媒体并提供文件选择 fallback；所有编辑只在浏览器内处理，不上传 transcript/媒体 |
| [`pluja/whishper`](https://github.com/pluja/whishper) | 本地 subtitle editor 随媒体位置高亮 transcript，并在编辑阶段给出 CPS 可读性 warning | 每个 segment 即时计算 CPS，超过 `--max-cps` 标黄；只做预渲染提示，最终发布门禁仍交给现有 `subtitle_readability_qa.py` |

新增/调整能力：[`scripts/transcript_review.py`](scripts/transcript_review.py) 新增 `html` 子命令，输出单文件、无外部依赖的同步媒体校稿页。页面支持时间码 seek、播放自动高亮、行内编辑、`localStorage` 自动保存、全文查找替换、重置、复制以及 File System Access API 保存；不支持时回退下载 `transcript_review.txt`。浏览器草稿 key 带 transcript 内容签名，重新转写同一路径时不会误载旧稿。生成阶段可继续应用现有 corrections 字典；JSON payload 做 HTML escaping，避免 transcript 里的 `</script>` 结束页面脚本。页面不直接改原 transcript，保存后的 review 仍由 `apply` 写出 `transcript_reviewed.json` 并重新分配词级时间戳。README 核心能力、流水线、日常命令、SKILL、daily prompt、教程索引和 [`docs/prompts/36-transcript-review.md`](docs/prompts/36-transcript-review.md) 已同步。

使用方式：运行 `python3 scripts/transcript_review.py html --transcript work/transcript.json --video origin/talking.mp4 --corrections work/corrections.json --output work/transcript_review.html --max-cps 20`，本地打开 HTML，校正后保存 `work/transcript_review.txt`；再运行 `python3 scripts/transcript_review.py apply --transcript work/transcript.json --review work/transcript_review.txt --output work/transcript_reviewed.json`。纯 SSH/终端环境继续使用原有 `export`，无需 HTML。

验证结果：`.venv/bin/python -m pytest tests/test_transcript_review.py -q` 通过 `14 passed in 0.19s`，覆盖 corrections、媒体 URI、内容签名草稿隔离、review 文件名收敛、恶意 `</script>` escaping、浏览器控件、CPS 参数、CLI HTML 输出、Node 内联 JavaScript 语法和原 export/apply round-trip；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `583 passed in 6.80s`。`.venv/bin/python -m compileall -q scripts tests`、主 CLI/`html --help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-21 自动化升级记录（Narration-driven BGM Ducking）

本次联网研究的 GitHub 参考：

| 来源 | 值得借鉴的优点 | 本项目处理 |
|---|---|---|
| [`aacamara/ai-video-editor` 的 audio mixing reference](https://github.com/aacamara/ai-video-editor/blob/main/references/audio-mixing.md) | 把 speech/music ducking 作为独立混音阶段，并给出 threshold、ratio、attack、release 的 sidechain 思路 | 在 `render_final.py` 单次编码图内实现旁白驱动 ducking；滤镜输入顺序按 FFmpeg 官方语义固定为“BGM 被处理、旁白触发” |
| [`worldwonderer/video-recap-skills` 的 audio mix](https://github.com/worldwonderer/video-recap-skills/blob/main/skills/video-assemble/scripts/audio_mix.py) | BGM ducking、旁白优先级和最终响度各自有清晰职责，避免把“选了音乐”误当成“混音完成” | 复用本项目已经完成响度处理的最终旁白轨作为 sidechain；封面、停顿、片尾无旁白时让音乐自然恢复 |
| [`gooseworks-ai/goose-video` 的 audio recipe](https://github.com/gooseworks-ai/goose-video/blob/main/skills/templates/imessage-video-ad/references/audio-recipe.md) | `amix normalize=0` 避免输入数导致整体衰减，混音末尾用 limiter 捕获峰值 | 仅在新 ducking 分支采用 `normalize=0 + alimiter=0.95`，保留旁白响度并控制叠加峰值；旧固定音量路径保持不变 |
| [FFmpeg `sidechaincompress` 官方文档](https://ffmpeg.org/ffmpeg-filters.html#sidechaincompress) | 明确第一输入是被压缩信号、第二输入是检测信号，并给出各参数合法范围 | 对 threshold / ratio / attack / release 做前置范围校验，非法配置在开始渲染前退出 2 |

新增/调整能力：`scripts/render_final.py` 新增 `--bgm-ducking` / `--no-bgm-ducking`，以及 render config 的 `bgm_ducking`、`bgm_ducking_threshold`、`bgm_ducking_ratio`、`bgm_ducking_attack_ms`、`bgm_ducking_release_ms`。启用后，最终旁白轨会经 `asplit` 同时进入主混音和 sidechain detector；BGM 先循环、裁时长、设基础音量和结尾 fade，再由旁白触发 `sidechaincompress`，最后与原旁白非归一化混合并过 limiter。默认参数为 threshold `0.03`、ratio `8`、attack `20ms`、release `500ms`；旧项目默认关闭，避免改变既有输出。`audio_cue_sheet.py` 的 next action、SKILL、日常工作流、提示词索引和 [背景音乐教程](docs/prompts/09-bgm-endcard.md) 已同步。

使用方式：在已有 BGM 的口播项目里运行 `python3 scripts/render_final.py --config work/render_config.json --output output/final.mp4 --bgm-ducking`；或在 config 写入 `"bgm_ducking": true`。音乐主导内容、MV 或已经手工做好 automation 的音轨可用 `--no-bgm-ducking` 覆盖。渲染后必须正常速度试听旁白入口、句间恢复和片尾，并继续运行 `audio_master_report.py --strict`，不能把滤镜存在当成混音已通过。

验证结果：新增 `tests/test_bgm_ducking.py` 12 项，覆盖默认兼容、官方参数范围、CLI override、sidechain 输入顺序、旁白 `asplit`、`normalize=0 + limiter`、旧混音路径和真实 FFmpeg 渲染；定向 `.venv/bin/python -m pytest tests/test_bgm_ducking.py tests/test_audio_chain.py tests/test_audio_cue_sheet.py -q` 通过 `22 passed in 0.74s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `577 passed in 6.35s`。4 秒 160×90 H.264/AAC smoke 中，220 Hz BGM 频段在无旁白窗口为 `-32.1 dB`，旁白窗口为 `-47.5 dB`，实际降低 `15.4 dB`；输出仍为 4.0 秒、48 kHz 双声道。`.venv/bin/python -m compileall -q scripts tests`、CLI `--help`、skill `quick_validate.py` 和 `git diff --check` 全部通过。

### 2026-07-20 自动化升级记录（Reference Frame Preflight + Style Lock）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`heygen-com/skills`](https://github.com/heygen-com/skills/blob/master/heygen-video/references/frame-check.md) | 视频提交前检查 avatar/reference 横竖方向与背景，并为不匹配画幅生成明确 framing correction | 新增首帧方向、画幅和透明背景预检，输出可执行修正建议 |
| [`higgsfield-ai/skills`](https://github.com/higgsfield-ai/skills/blob/main/higgsfield-video-explainer/SKILL.md) | 同一 style key 和 STYLE descriptor 贯穿全部 clip；参考图与目标画幅冲突时停止 | `video_prompt_pack.py --style-reference` 把共享 style key 绑定到所有 generated shot；preflight 把严重画幅冲突变成 blocker |
| [`rich5000/seedance-prompt-guide`](https://github.com/rich5000/seedance-prompt-guide/blob/master/SKILL.md) | 多素材输入必须明确角色，首帧、尾帧、人物、场景、动作和风格参考不能含糊 | preflight 区分 `first_frame` / `style_reference` role，并允许 `--reference shot_id=path` 显式覆写 |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 交付前自检、失败修正、产物持久化是 production-correctness 的一部分 | 输出 `reference_frame_preflight.v1` JSON + Markdown，并接入 `pipeline_manifest.py` |

新增/调整能力：新增 `scripts/reference_frame_preflight.py`，读取 `video_prompt_pack.json`，检查 image-to-video 首帧和共享 style key 的路径、解码、尺寸、方向、画幅、短边分辨率和透明背景；缺失/损坏/方向冲突/严重画幅冲突写入 `summary.blocking`，低分辨率和透明背景给 warning 与修正建议。`video_prompt_pack.py` 新增 `--style-reference`，在 `global` 和每个 item 中保存同一路径，并给所有 prompt 追加统一 `STYLE LOCK`。`pipeline_manifest.py` 新增 `reference_frame_preflight` gate；README、SKILL、daily workflow、提示词目录和 `docs/prompts/71-reference-frame-preflight.md` 已更新。

使用方式：先用 `python3 scripts/video_prompt_pack.py --storyboard-plan work/storyboard_plan.json --asset-root work --style-reference work/imagegen/style-key.png --animate-stills --approved --output work/video_prompt_pack.json --markdown work/video_prompt_pack.md` 生成带 style lock 的 prompt pack；再用 `python3 scripts/reference_frame_preflight.py --prompt-pack work/video_prompt_pack.json --output work/reference_frame_preflight.json --markdown work/reference_frame_preflight.md --require-style-reference --strict` 做 paid provider 提交前门禁。需要替换单镜头参考图时重复传 `--reference shot_001=/path/to/approved.png`。

验证结果：`.venv/bin/python -m pytest tests/test_reference_frame_preflight.py tests/test_video_prompt_pack.py tests/test_pipeline_manifest.py -q` 通过 `62 passed in 2.44s`；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `565 passed in 12.21s`；`.venv/bin/python -m compileall -q scripts tests`、CLI `--help` smoke、`pipeline_manifest.py --list-categories` 和 `git diff --check` 通过。

### 2026-06-16 自动化升级记录（Publish Package）

本次联网研究的 GitHub 参考：

| 来源 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`browser-use/video-use`](https://github.com/browser-use/video-use) | 输出固定落在项目 `edit/` 目录，渲染前后有自评和持久化上下文 | 本项目保持 artifact-first，把发布前物料汇总为 `publish_package.v1`，不依赖聊天上下文 |
| [`htekdev/vidpipe`](https://github.com/htekdev/vidpipe) | idea / platform / publish-by / output structure 进入内容生产管理 | `publish_package.py` 输出平台级 checklist、caption copy 和发布时间提示 |
| [`mutonby/openshorts`](https://github.com/mutonby/openshorts) | YouTube Studio、title/description/chapter、社交自动发布和排期是一等能力 | 本项目先做本地发布包，不引入账号 token 或第三方上传 API |
| [`rushindrasinha/youtube-shorts-pipeline`](https://github.com/rushindrasinha/youtube-shorts-pipeline) | research → script → visuals → voice → captions → assemble → upload 的完整链路 | 在 `multi_export` / `caption` / `subtitle_pack` / `pipeline_manifest` 之后新增最终上传 handoff |
| [`Bomx/super-video-maker-skill`](https://github.com/Bomx/super-video-maker-skill) | paid-call、source deck、timestamp、layout、technical QC 都作为质量 gate | `publish_package.py` 读取或构建 pipeline gate，把 blocked 状态带到上传包 |

新增/调整能力：新增 `scripts/publish_package.py`，支持 `xhs`、`douyin`、`wxch`、`youtube_shorts`、`tiktok`、`instagram_reels`；自动发现平台 MP4、封面、SRT/VTT、caption JSON、章节文本和 pipeline manifest，输出 `publish_package.json` 与 Markdown checklist。`pipeline_manifest.py` 新增 `publish_package` artifact 类别；如果发布包 `summary.blocking > 0`，会作为 blocking gate。README、SKILL、`docs/prompts/49-publish-package.md`、提示词目录和 daily workflow 已更新。

使用方式：常规发布前跑 `python3 scripts/publish_package.py --project-dir work/day58 --platforms xhs douyin wxch --output work/day58/publish_package.json --markdown work/day58/publish_package.md --strict`；如果平台文件不在默认位置，用 `--video xhs=/path/to/xhs.mp4 --video youtube_shorts=/path/to/shorts.mp4` 覆盖。脚本不上传、不登录平台、不调用外部 API，适合手工上传或把 JSON 交给发布 connector。

验证结果：新增 `tests/test_publish_package.py` 7 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_publish_package.py tests/test_pipeline_manifest.py -q` 通过 `24 passed in 0.36s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/publish_package.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg publish_package` smoke 通过；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `360 passed in 5.76s`。

### 2026-06-15 自动化升级记录（Color Grade）

本次联网研究的 GitHub 参考：

| 来源 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`KyaniteLabs/mcp-video`](https://github.com/KyaniteLabs/mcp-video) | 把 color grading、effects、preflight guardrails、repurposing manifest 放进 agent tool surface | 新增 bounded `color_grade.py`，不让 agent 手写不受控 FFmpeg filter |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 把 color grade 与 transcript/cut/subtitles 并列为 conversational video editing 能力 | 在本 skill 的实际渲染链中加入 `--color-grade`，而不是只写成提示词建议 |
| [`wizenheimer/vibestudio`](https://github.com/wizenheimer/vibestudio) | 视频 filters / color correction 作为本地、可追踪命令能力 | 输出 JSON + Markdown + FFmpeg filter，保持可审计和本地优先 |
| [`aicw-io/aicw-video`](https://github.com/aicw-io/aicw-video) | 先预览/确认 range、caption、privacy、format，再渲染 | 调色先产 `color_grade.v1` review artifact，再由 `render_final.py` 接入 |

新增/调整能力：新增 `scripts/color_grade.py`，支持 `natural`、`warm`、`cool`、`punchy`、`soft`、`cinematic`、`screen` 七个 preset；自定义 `brightness`、`contrast`、`saturation`、`gamma`、`temperature`、`tint`、`sharpness` 会被 clamp 到保守范围，`--strict` 在 clamp 时返回 2。`render_final.py` 新增 `--color-grade`，可读取 preset、FFmpeg 单链 filter 或 `color_grade.v1` JSON，并在 B-roll/image/focus 之后、字幕/HUD 前应用，避免二次压缩改变字幕颜色。`pipeline_manifest.py` 新增 `color_grade` artifact 类别；README、SKILL、`docs/prompts/48-color-grade.md` 和提示词目录已更新。

使用方式：先跑 `python3 scripts/color_grade.py --preset screen --output work/color_grade.json --markdown work/color_grade.md`；最终渲染加 `python3 scripts/render_final.py --config work/render_config.json --color-grade work/color_grade.json --output output/tutorial_master.mp4`。如果只是给已完成 master 做复版，可用 `python3 scripts/color_grade.py --preset cinematic --input output/master.mp4 --render-output output/master_grade.mp4 --output work/color_grade.json --markdown work/color_grade.md`。

验证结果：新增 `tests/test_color_grade.py` 7 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_color_grade.py tests/test_pipeline_manifest.py -q` 通过 `22 passed in 0.31s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/color_grade.py --help`、`.venv/bin/python scripts/render_final.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg color_grade` smoke 通过；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `351 passed in 3.88s`。

### 2026-06-14 自动化升级记录（Video Understanding + YOLO）

本次联网 research 参考：

| 来源 | 看到的要点 | 本项目吸收方式 |
|---|---|---|
| [Ultralytics YOLO Predict 文档](https://docs.ultralytics.com/modes/predict/) | `Results.boxes` 提供 `xyxy`、`conf`、`cls`、可选 `id` 等字段 | `video_understanding.py --detector yolo` 直接解析这些字段，输出统一 `detections[]` |
| [Ultralytics YOLO Track 文档](https://docs.ultralytics.com/modes/track/) | Ultralytics 支持 BoT-SORT / ByteTrack 等 tracker，可用 tracker YAML 配置 | README 说明高动态素材可用 `model.track(..., tracker="bytetrack.yaml")` 作为同一 JSON 的更密集上游 |
| [Norfair 文档](https://tryolabs.github.io/norfair/) | Norfair 可以给任意 detector 增加轻量多目标跟踪 | 本项目内置先做抽样帧 bbox 轻量关联；需要严格 MOT 时可用 Norfair 输出外部 tracks |

新增/调整能力：新增 `scripts/video_understanding.py`，支持默认无 detector 抽样帧、`--detector yolo` 可选 Ultralytics YOLO 检测、`--external-detections` 合并已有检测 JSON、按场景边界和固定间隔抽帧、生成轻量 `tracks[]`、`scene_tags[]`、`warnings[]` 和 Markdown review。`pipeline_manifest.py` 新增 `video_understanding` artifact 类别，可作为可选或显式 required gate 被发现。README、SKILL、`docs/prompts/47-video-understanding.md`、smart reframe 和 privacy redaction 文档都已更新为同一条视觉理解路径。

使用方式：无 detector 时用 `python3 scripts/video_understanding.py origin/talk.mp4 --output work/video_understanding.json --markdown work/video_understanding.md`；启用 YOLO 时先 `pip install ultralytics`，再加 `--detector yolo --model yolo11n.pt --scene-boundaries work/scene_boundaries.json --strict`；下游用 `smart_reframe.py --detections work/video_understanding.json` 做主体感知裁切，或用 `privacy_redact.py --detections work/video_understanding.json` 生成隐私遮挡计划。

验证结果：新增 `tests/test_video_understanding.py` 8 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_video_understanding.py tests/test_smart_reframe.py tests/test_privacy_redact.py tests/test_pipeline_manifest.py -q` 通过 `36 passed in 1.12s`；`.venv/bin/python scripts/video_understanding.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg video_understanding` smoke 通过；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `343 passed in 3.63s`。

### 2026-06-30 自动化升级记录（Audio Sync）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`aicw-io/aicw-video`](https://github.com/aicw-io/aicw-video) | 把 separate audio tracks 的 auto-match / sync 作为人物视频剪辑核心能力，并和 caption、privacy、export 放在同一工作流里 | 新增本地 `audio_sync.py`，用 scratch audio + 外录音轨生成可审计 offset 和替换音轨计划 |
| [`smacke/ffsubsync`](https://github.com/smacke/ffsubsync) | 把同步做成独立 CLI，输出前强调质量阈值、低质量跳过和可重复执行 | `audio_sync_plan.v1` 保留 confidence、score、warnings、`summary.blocking`，低置信度先 review |
| [`Huanshere/VideoLingo`](https://github.com/Huanshere/VideoLingo) | 重视字幕/配音对齐、单行字幕、word-level alignment 和可恢复处理流程 | 本项目继续保持音频对齐与字幕/配音分离，只补外录主音轨同步，不引入翻译/配音依赖 |
| [`ClipsAI/clipsai`](https://github.com/ClipsAI/clipsai) | 面向访谈/播客/演讲这类 audio-centric 视频，用 transcript 驱动剪辑和重构图 | 新能力优先服务访谈、教程、播客切片里常见的相机内录 + 外录麦克风素材 |
| [`haidrrrry/claude-remotion-skill`](https://github.com/haidrrrry/claude-remotion-skill) | 强制 render / inspect / fix 循环，不盲目交付 | `--apply` 默认不执行，先产 JSON/Markdown 让 agent 或人工复核 offset 后再替换音轨 |

新增/调整能力：新增 `scripts/audio_sync.py`，可从 `--reference-media` 的相机/录屏 scratch audio 和 `--external-audio` 的 lav/recorder 音频估计 `alignment.offset_seconds`；输出 `audio_sync_plan.v1`、Markdown review、FFmpeg replace-audio command；支持 `--offset` 手动偏移、`--replace-output` 生成命令、`--apply` 确认后执行替换；`pipeline_manifest.py` 新增 `audio_sync` 可选 gate，发现低置信度或缺文件的 `audio_sync_plan.json` 会阻塞发布清单。

使用方式：先跑 `python3 scripts/audio_sync.py --reference-media origin/camera.mp4 --external-audio origin/lav.wav --output work/audio_sync_plan.json --markdown work/audio_sync_plan.md --replace-output output/camera_lav_synced.mp4 --strict`；正数 offset 表示延迟外录音轨，负数 offset 表示裁掉外录音轨开头；复核 Markdown 后再加 `--apply` 执行替换。如果自动估计低置信度，但已经用拍手点/波形确认偏移，可用 `--offset 0.18` 跳过估计。

验证结果：新增 `tests/test_audio_sync.py` 6 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_audio_sync.py tests/test_pipeline_manifest.py -q` 通过 `26 passed in 0.36s`；合成 WAV smoke 用 FFmpeg 生成已知偏移音频，`audio_sync.py` 自动估到 `offset_seconds=0.24`、`confidence=0.884` 并通过 `--strict`；`.venv/bin/python scripts/audio_sync.py --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg audio_sync` 通过；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；最终全量 `.venv/bin/python -m pytest tests -q` 通过 `413 passed in 3.92s`。

### 2026-06-14 自动化升级记录（Generation Task Log）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`PixVerseAI/skills`](https://github.com/PixVerseAI/skills) | 把 task status / wait 和 asset download 拆成独立 capability，适合异步生成和批量任务 | 新增本地 `generation_task_log.py`，保存 task id、轮询命令、下载命令和 blocking 状态 |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | 用项目状态跟踪 scenes、asset status、phase，方便跨会话续作 | `generation_tasks.json` 成为可恢复 artifact，并由 `pipeline_manifest.py` 汇总发布前状态 |
| [`znyupup/ai-video-editing-skill`](https://github.com/znyupup/ai-video-editing-skill) | 自动剪辑 workflow 强调素材理解、预览确认、再渲染 | 本次先补生成素材的 review/落盘闭环；交互式 dashboard 暂不引入 |
| [`GoogleCloudPlatform/vertex-ai-creative-studio`](https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/blob/main/experiments/mcp-genmedia/skills/genmedia-video-editor/SKILL.md) | 视频生成和 FFmpeg 合成工具链分工明确 | 本项目继续保持 provider 生成与本地渲染分离，只记录生成任务，不提交 provider job |

新增/调整能力：新增 `scripts/generation_task_log.py`，支持 `add`、`update`、`import-provider-decision`、`report` 四个子命令；可记录 Dreamina/即梦 `submit_id`、PixVerse task id 或其他 provider id，自动生成 Dreamina `query_result` 轮询/下载命令，导入 provider JSON 状态，并计算 `readiness` 与 `summary.blocking`。`pipeline_manifest.py` 新增 `generation_task_log` 可选 gate，发现 `generation_tasks.json` 中存在未审批、未完成、未下载、失败或本地文件丢失的任务时会阻塞发布清单。新增 `docs/prompts/46-generation-task-log.md`，并更新 daily workflow、SKILL、提示词目录和 README 能力说明。

使用方式：从 provider 决策初始化台账用 `python3 scripts/generation_task_log.py import-provider-decision --provider-decision work/provider_decision.json --log work/generation_tasks.json --markdown work/generation_tasks.md --strict`；提交 Dreamina/即梦后保存任务用 `python3 scripts/generation_task_log.py add --log work/generation_tasks.json --provider dreamina --task-id "<submit_id>" --shot-id shot_002 --expected-path work/generated_video/shot_002.mp4 --status submitted --markdown work/generation_tasks.md --strict`；下载完成后用 `python3 scripts/generation_task_log.py update --log work/generation_tasks.json --provider dreamina --task-id "<submit_id>" --status downloaded --asset-path work/generated_video/shot_002.mp4 --markdown work/generation_tasks.md`。

验证结果：新增 `tests/test_generation_task_log.py` 7 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_generation_task_log.py tests/test_pipeline_manifest.py -q` 通过 `20 passed in 0.21s`；`.venv/bin/python -m compileall scripts tests` 通过；`.venv/bin/python scripts/generation_task_log.py --help`、`.venv/bin/python scripts/generation_task_log.py add --help`、`.venv/bin/python scripts/pipeline_manifest.py --list-categories | rg generation_task_log` smoke 通过；`git diff --check` 通过；第一次全量测试因 macOS `subprocess.Popen` 临时返回 `BlockingIOError: Resource temporarily unavailable` 导致 5 个旧 CLI smoke 失败，单独重跑这 5 项通过 `5 passed in 0.28s`，随后最终全量 `.venv/bin/python -m pytest tests -q` 通过 `334 passed in 3.30s`。

### 2026-06-12 自动化升级记录（Video Prompt Pack）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`Square-Zero-Labs/video-prompting-skill`](https://github.com/Square-Zero-Labs/video-prompting-skill) | 支持 Seedance、LTX、Sora、Veo、Wan 等模型指南，并把 character sheet 作为 image-to-video 前置工作流 | 新增 provider-specific prompt pack，包含角色/风格 reference sheet、参考图路径和模型化 prompt |
| [`browser-use/video-use`](https://github.com/browser-use/video-use/blob/main/SKILL.md) | 强调确认策略、执行、迭代、持久化，以及字幕/剪辑等 production-correctness hard rules | `video_prompt_pack.py --strict` 在 paid video generation 未审批时返回 2，生成前先持久化 review artifact |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | 用项目状态跟踪 scenes、audio、phase、asset status，适合跨会话续作 | 新增 `video_prompt_pack.json/.md` 作为可恢复 artifact，并接入 `pipeline_manifest.py` gate |
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | provider 选择、创意审批、自检和成本意识都进入生产流程 | prompt pack 记录 `approval_status`、negative prompt、review checks 和 `summary.blocking` |
| [`SamurAIGPT/AI-Youtube-Shorts-Generator`](https://github.com/samuraigpt/ai-youtube-shorts-generator) | 长视频转短视频时输出 JSON，保留分数、hook、reason 等下游可自动化字段 | 本项目继续保持 JSON + Markdown 双输出，方便 agent 和人工同时复核 |

新增/调整能力：新增 `scripts/video_prompt_pack.py`，可从 `storyboard_plan.json` 生成 `video_prompt_pack.v1`，自动/指定输出 Dreamina/即梦 Seedance、Veo、LTX、Wan、Sora、Codex imagegen、Remotion 或本地 B-roll 的提示词；支持 `--character`、`--brand-anchor`、`--animate-stills`、`--approved`、`--strict`；新增 `docs/prompts/45-video-prompt-pack.md`，更新 daily workflow、SKILL、提示词目录；`pipeline_manifest.py` 新增 `video_prompt_pack` 可选 gate，发现 `summary.blocking > 0` 会阻塞发布清单。

使用方式：普通 review 用 `python3 scripts/video_prompt_pack.py --storyboard-plan work/storyboard_plan.json --asset-root work --output work/video_prompt_pack.json --markdown work/video_prompt_pack.md --strict`；要把 Codex imagegen still route 转成 image-to-video 提示词，加 `--animate-stills`；确认 Dreamina/即梦或其他 provider credits 后加 `--approved` 再进入提交/下载流程。

验证结果：新增 `tests/test_video_prompt_pack.py` 5 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_video_prompt_pack.py tests/test_pipeline_manifest.py -q` 通过 `17 passed in 0.23s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `326 passed in 3.78s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；`.venv/bin/python scripts/video_prompt_pack.py --help` 和 `.venv/bin/python scripts/pipeline_manifest.py --list-categories` smoke 验证正常。

### 2026-06-10 自动化升级记录（Audio Cue Sheet）

本次联网研究的 GitHub 参考：

| 项目 | 看到的优点 | 本项目吸收方式 |
|---|---|---|
| [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) | 把 music、audio mixer、sound-design、预算和 stage gate 明确写进生产流程 | 新增本地 `audio_cue_sheet.py`，把 BGM/SFX 缺口变成 review artifact |
| [`vericontext/vibeframe`](https://github.com/vericontext/vibeframe) | storyboard cue、music/SFX/narration 生成和 build/review report 统一走 JSON | 输出 `audio_cue_sheet.v1`，保留 cue 来源、状态、route、approval note 和 next actions |
| [`digitalsamba/claude-code-video-toolkit`](https://github.com/digitalsamba/claude-code-video-toolkit) | 把 voiceover、music、SFX、timing sync 作为项目阶段管理 | 先规划音频，再让 `pipeline_manifest.py` 拦截未解决音频任务 |
| [`AIDC-AI/Pixelle-Video`](https://github.com/AIDC-AI/Pixelle-Video) | 自动短视频流程显式处理 narration、BGM 和音频/视频时长匹配 | `voice_track` 记录口播主轨时长和响度目标，BGM/SFX 作为次级音频层审查 |

新增/调整能力：新增 `scripts/audio_cue_sheet.py`，可从 `transcript.json` 生成 BGM mood、BPM 范围、music prompt、SFX cue、生成审批和本地素材缺口；扫描 `--asset-root` 下的 `.mp3/.wav/.m4a/.flac/.ogg` 等音频，优先匹配本地 BGM/SFX；新增 `docs/prompts/43-audio-cue-sheet.md`；`pipeline_manifest.py` 新增 `audio_cue_sheet` 可选 gate，发现 `summary.blocking > 0` 会阻塞发布清单。

使用方式：普通 review 用 `python3 scripts/audio_cue_sheet.py --transcript work/transcript.json --asset-root media/bgm --asset-root media/sfx --output work/audio_cue_sheet.json --markdown work/audio_cue_sheet.md`；发布前严格门禁加 `--require-local-music --require-local-sfx --strict`。如果需要生成音乐或音效，先确认 provider credits 和素材授权，再提交生成任务。

验证结果：新增 `tests/test_audio_cue_sheet.py` 7 项，更新 `tests/test_pipeline_manifest.py`；`.venv/bin/python -m pytest tests/test_audio_cue_sheet.py tests/test_pipeline_manifest.py -q` 通过 `18 passed in 0.22s`；完整 `.venv/bin/python -m pytest tests -q` 通过 `312 passed in 3.65s`；`.venv/bin/python -m compileall scripts tests` 通过；`git diff --check` 通过；`.venv/bin/python scripts/audio_cue_sheet.py --help` smoke 验证 CLI 参数正常。

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
| **22** | **[Timeline View](docs/prompts/22-timeline-view.md)** | **源素材删除段 / 成片输出切点人工复盘图** |
| **23** | **[Versioned Output](docs/prompts/23-versioned-output.md)** | **避免覆盖旧成片** |
| **24** | **[Storyboard Plan](docs/prompts/24-storyboard-plan.md)** | **分镜 shot cards + 生成路由** |
| **25** | **[Storyboard Assets](docs/prompts/25-storyboard-assets.md)** | **分镜素材任务清单 + ready 预检** |
| **26** | **[ASR Rough Cut](docs/prompts/26-rough-cut.md)** | **去口头禅/重复句粗剪** |
| **27** | **[NLE Handoff](docs/prompts/27-export-edl.md)** | **导出 EDL / FCPXML / OTIO 给 Premiere/FCP/Resolve** |
| **28** | **[Screen Focus](docs/prompts/28-screen-focus.md)** | **录屏点击/热点自动聚焦** |
| **29** | **[Subtitle Pack](docs/prompts/29-subtitle-pack.md)** | **导出 SRT/VTT/ASS/JSON 字幕包** |
| **70** | **[Subtitle Readability QA](docs/prompts/70-subtitle-readability-qa.md)** | **检查最终字幕 CPS、时长、行长、重叠和媒体越界** |
| **71** | **[Reference Frame Preflight](docs/prompts/71-reference-frame-preflight.md)** | **检查视频生成首帧/style key 的尺寸、方向、画幅和透明背景** |
| **75** | **[Speech Denoise](docs/prompts/75-speech-denoise.md)** | **可选清理口播低频震动与稳态底噪** |
| **76** | **[Multicam Sync](docs/prompts/76-multicam-sync.md)** | **多机位 offset / coverage / 对齐预览和 gate** |
| **77** | **[Approval Receipt](docs/prompts/77-approval-receipt.md)** | **把人工已复核交付件绑定到 SHA-256，并阻塞过期审批** |
| **78** | **[Target Script Alignment](docs/prompts/78-script-alignment.md)** | **按确认稿从多 take 找原话、人工选候选并生成 render_config** |
| **80** | **[Edit Revision](docs/prompts/80-edit-revision.md)** | **剪辑文本 artifact 的 source-bound 审批、成组 apply 与 undo/redo** |
| **82** | **[Portable Edit Recipe](docs/prompts/82-edit-recipe.md)** | **把已审 render_config 导出为 typed-slot 配方，并绑定新素材回放** |
| **83** | **[Speed Ramp](docs/prompts/83-speed-ramp.md)** | **给 impact moment 做 source-bound 局部慢动作 / velocity edit** |
| **84** | **[Video Stabilization](docs/prompts/84-video-stabilization.md)** | **手持素材 source-bound 防抖、全长 A/B 对照与人工确认 gate** |
| **86** | **[J-cut / L-cut Audio Transition](docs/prompts/86-audio-transition.md)** | **显式声音先行/延续边界、source handle/hash、单次编码与 1× 试听 gate** |
| **87** | **[HDR → Rec.709 SDR Delivery](docs/prompts/87-hdr-sdr.md)** | **PQ/HLG source hash、Hable tone-map、BT.709 tags、完整解码和 live gate** |
| **88** | **[Multimodal Dead-Air](docs/prompts/88-multimodal-dead-air.md)** | **只剪同时静音且画面静止的死区** |
| **89** | **[Generated Clip Review](docs/prompts/89-generated-clip-review.md)** | **生成视频下载后做逐片物理、身份、裁切与重生 gate** |
| **90** | **[Generation Lessons](docs/prompts/90-generation-lessons.md)** | **把已审片段经验按 provider/model scope 复用到下一次 prompt** |
| **91** | **[Generated Sequence Review](docs/prompts/91-generated-sequence-review.md)** | **逐片通过后复核相邻尾帧/首帧与跨镜头连续性** |
| **92** | **[Reference Edit Rhythm](docs/prompts/92-reference-edit-rhythm.md)** | **量化参考片 hard-cut 结构并对照成片，绑定 contact sheets 与 live gate** |
| **43** | **[Audio Cue Sheet](docs/prompts/43-audio-cue-sheet.md)** | **规划 BGM/SFX 和生成审批** |
| **45** | **[Video Prompt Pack](docs/prompts/45-video-prompt-pack.md)** | **视频生成提示词包 + paid approval gate** |
| **46** | **[Generation Task Log](docs/prompts/46-generation-task-log.md)** | **跟踪 submit_id、轮询、下载和本地落盘** |
| **49** | **[Publish Package](docs/prompts/49-publish-package.md)** | **汇总平台视频、文案、字幕和发布 gate** |
| **50** | **[CapCut Subtitle Import](docs/prompts/50-import-capcut-subtitles.md)** | **剪映/CapCut 自动字幕反向导入** |
| **52** | **[Project Resume](docs/prompts/52-project-resume.md)** | **生成跨会话续跑上下文包** |
| **54** | **[Audio Master Report](docs/prompts/54-audio-master-report.md)** | **检查 LUFS、true peak、LRA 和长静音** |
| **55** | **[SRT Edit Plan](docs/prompts/55-srt-edit-plan.md)** | **SRT + keep/drop 指令转剪辑方案** |
| **56** | **[Audio Sync](docs/prompts/56-audio-sync.md)** | **外录音轨自动对齐和替换计划** |
| **57** | **[Review Dashboard](docs/prompts/57-review-dashboard.md)** | **打开 HTML/JSON 总复核面板** |
| **58** | **[Source Receipts](docs/prompts/58-source-receipts.md)** | **事实 claim 的 URL/截图 proof deck 和发布 gate** |
| **59** | **[Auto Emphasis](docs/prompts/59-auto-emphasis.md)** | **数字/转折/结论自动落视觉重点** |
| **60** | **[Takes Pack](docs/prompts/60-takes-pack.md)** | **多 take / Scribe transcript 压成保留 speaker/audio events 的 phrase-level 阅读视图** |
| **61** | **[Project Bootstrap](docs/prompts/61-project-bootstrap.md)** | **原始素材目录 → source inventory + project memory** |
| **62** | **[Hook Variants](docs/prompts/62-hook-variants.md)** | **同一视频批量生成前三秒 hook 角度** |
| **67** | **[Speech Continuity QA](docs/prompts/67-speech-continuity-qa.md)** | **成片二次 ASR 检查复读、近重复 take 和句内口吃** |
| **68** | **[Cover Variants](docs/prompts/68-cover-variants.md)** | **多套封面、feed-size 预览、标题协同和最终选择** |
| **69** | **[Retention Rhythm QA](docs/prompts/69-retention-rhythm-qa.md)** | **成片前三秒活动、长镜头、注意力空窗和节奏风险门禁** |
| **73** | **[Platform Safe Area QA](docs/prompts/73-platform-safe-area-qa.md)** | **渲染前检查字幕、PIP、CTA、badge 和 marker 的平台 UI 遮挡风险** |

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
├── project_bootstrap.py        项目启动 + source inventory             [V3]
├── edit_brief_plan.py          自然语言剪辑需求 → 本地 runbook          [V3]
├── _internal_text_guard.py     内部 token 拦截器
├── transcribe.py               Whisper 转写
├── semantic_transcript_review.py 全篇上下文语义审校 / 人工 choices gate [V3]
├── takes_pack.py               多 take / Scribe phrase + audio event 阅读视图 [V3]
├── script_alignment.py         目标稿 → 多 take 原话候选 / choices / render_config [V3]
├── audio_sync.py               外录音轨自动对齐 / 替换音轨计划        [V3]
├── multicam_sync.py            多机位可逆同步计划 / 对齐预览          [V3]
├── video_understanding.py      抽样帧 + 可选 YOLO 检测 artifact       [V3]
├── highlight_picker.py         长视频精华候选 / brief 定向找片段      [V3]
├── audio_boundary_snap.py      词/句末/静音剪辑边界校正              [V3]
├── rough_cut.py                transcript 粗剪：去口头禅/重复句      [V3]
├── multimodal_dead_air.py      静音 AND 静帧 / source hash / 单次编码 gate [V3]
├── extract_audio.py            音频提取
├── split_video.py              按句切片（V2 兼容）
├── media_library.py            素材库索引（CLIP-ready）
├── merge_clips.py              合并片段（V2 兼容）
├── content_guard.py            平台雷区 lint                   [V3]
├── source_receipts.py          事实来源 proof deck + 发布 gate    [V3]
├── rewrite_script.py           Story Engine                    [V3]
├── hook_variants.py            前三秒 hook 批量角度 + 风险检查     [V3]
├── auto_broll.py               B-roll 调度                      [V3]
├── auto_chapter_cards.py       章节卡渲染                       [V3]
├── beat_sync.py                BGM beat edit slots / 切点吸附   [V3]
├── video_stabilization.py      source-bound 手持防抖 / 全长 A/B confirm gate [V3]
├── speed_ramp.py               source-bound 局部变速计划 / 验证 / apply [V3]
├── audio_transition.py         J-cut/L-cut source handle / 单次编码 / receipt [V3]
├── audio_cue_sheet.py          BGM/SFX 音频设计清单               [V3]
├── auto_stickers.py            情绪→贴纸                        [V3]
├── auto_emphasis.py            问句/数字/转折/结论强调点          [V3]
├── imagegen_hint.py            抽象概念→gpt-image-2 提示词       [V3]
├── auto_enrich.py              丰富度编排（B-roll/贴纸/强调点）  [V3]
├── storyboard_plan.py          分镜 shot cards + 生成路由         [V3]
├── video_prompt_pack.py        多模型视频生成提示词包 + 审批 gate  [V3]
├── reference_frame_preflight.py 首帧/style key 画幅与背景预检 gate [V3]
├── generation_task_log.py      异步生成任务台账 + 下载 gate         [V3]
├── generated_clip_review.py    source-bound 生成片段评分/裁切/重生 gate [V3]
├── generated_sequence_review.py 已审生成片段相邻尾帧/首帧/预览连续性 gate [V3]
├── generation_lessons.py       已审片段 → scoped prompt 经验库 / 选择 / verify [V3]
├── storyboard_assets.py        分镜素材任务清单 + ready 预检       [V3]
├── stock_material_plan.py      远程 stock 搜索规划                 [V3]
├── screen_focus.py             录屏点击/热点聚焦计划              [V3]
├── color_grade.py              bounded 调色计划 + FFmpeg filter    [V3]
├── edit_revision.py            文本剪辑 artifact 可逆修订 + stale gate [V3]
├── edit_recipe.py              可移植 render-config recipe + replay preflight [V3]
├── edit_preflight.py           渲染前结构/路径/参数预检 gate       [V3]
├── platform_safe_area_qa.py    字幕/PIP/CTA/marker 平台安全区 gate [V3]
├── render_final.py             单次编码渲染 + 可选口播降噪 + enrich_plan 接入（V3 强化）
├── render_qa.py                渲染后黑屏/静帧/静音/尺寸质检       [V3]
├── shot_color_qa.py            成片镜头色彩/曝光/broadcast-range gate [V3]
├── edit_compare.py             原片连续时钟 vs 最终像素双栏复核     [V3]
├── retention_rhythm_qa.py      成片 hook / 长镜头 / 注意力空窗门禁 [V3]
├── reference_edit_rhythm.py    参考片 vs 成片 hard-cut 结构 / contact-sheet / live gate [V3]
├── speech_continuity_qa.py     成片二次 ASR 复读 / 口吃发布 gate  [V3]
├── audio_master_report.py      成片响度 / true peak / LRA 发布门禁 [V3]
├── timeline_view.py            源素材/成片切点 filmstrip+waveform  [V3]
├── subtitle_pack.py            SRT/VTT/ASS/JSON 字幕交付包        [V3]
├── subtitle_readability_qa.py  最终字幕 CPS/时长/重叠/越界 gate   [V3]
├── import_capcut_subtitles.py  剪映/CapCut 字幕反向导入 + gap cut [V3]
├── srt_edit_plan.py            SRT 编辑指令 → render_config/cut   [V3]
├── project_resume.py           续跑上下文包 + agent handoff           [V3]
├── review_dashboard.py         静态 HTML/JSON 人工复核面板         [V3]
├── burn_subtitles.py           字幕 ASS 生成
├── generate_cover.py           封面生成
├── generate_cover_image.py     Chrome-rendered 封面
├── cover_variants.py           封面 A/B 方案 + 小尺寸预览 + 选择 gate [V3]
├── add_chapter_bar.py          章节进度条
├── export_capcut.py            剪映工程导出
├── export_edl.py               NLE handoff EDL + manifest          [V3]
├── export_fcpxml.py            NLE handoff FCPXML + manifest       [V3]
├── export_otio.py              NLE handoff OTIO + manifest         [V3]
├── generate_standup_timeline.py Remotion timeline
├── multi_export.py             三平台导出                       [V3]
├── hdr_sdr.py                  PQ/HLG → source-bound Rec.709 SDR / full-decode gate [V3]
├── delivery_encode.py          source-bound 硬大小交付编码      [V3]
├── generate_caption.py         标题/正文/标签                   [V3]
├── approval_receipt.py         SHA-256 审批收据 + stale gate       [V3]
├── publish_package.py          最终上传包 + gate 状态汇总           [V3]
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

PR 欢迎。新功能必须带测试，每个新脚本至少 5 个测试，全套应保持在轻量本地运行范围内。

---

## License

MIT.

---

_BestAI Labs · 2026_
