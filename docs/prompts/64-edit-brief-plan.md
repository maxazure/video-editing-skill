# Edit Brief Plan 自然语言剪辑需求路由

把用户的一句话剪辑需求转成当前 skill 的本地执行 runbook：匹配平台、素材类型、手持防抖、字幕、长视频拆条、B-roll、生成素材、参考视频节奏、音频、PIP、调色、QA 和发布包等信号，然后输出现有脚本的建议顺序、命令、产物和 manifest gate。

## 适用场景

- 用户只说“把这个访谈剪成 3 条抖音短视频，加字幕和 BGM，最后给发布包”。
- 新 agent 接手项目，不确定应该先跑哪个脚本。
- 想把自然语言需求固化成 `edit_brief_plan.json` / Markdown，避免只存在聊天上下文里。
- 需要把本次剪辑范围作为 `pipeline_manifest.py --require edit_brief_plan` 的可见 gate。

## 生成计划

```bash
python3 scripts/edit_brief_plan.py \
  --brief "把 origin/interview.mp4 剪成三条抖音短视频，去停顿，加B-roll、BGM和字幕，最后生成发布包" \
  --project-dir . \
  --output work/edit_brief_plan.json \
  --markdown work/edit_brief_plan.md \
  --strict
```

如果 brief 里包含 `origin/interview.mp4` 这类路径，脚本会自动识别为 `source_media`。也可以显式传：

```bash
python3 scripts/edit_brief_plan.py \
  --brief-file work/user_request.md \
  --source-media origin/interview.mp4 \
  --platform xhs \
  --output work/edit_brief_plan.json \
  --markdown work/edit_brief_plan.md
```

输出：

- `work/edit_brief_plan.json`：`edit_brief_plan.v1`，包含 `signals[]`、`steps[]`、`gates[]`、`warnings[]` 和 `blockers[]`。
- `work/edit_brief_plan.md`：人工可读 runbook，列出每一步为什么需要、运行哪个脚本、命令模板和产物。
- `summary.blocking`：brief 为空或显式传入的 source media 不存在时为非零；`pipeline_manifest.py` 会把它作为 blocker。

## 常见信号

| 用户提法 | 路由方向 |
|---|---|
| 长视频、访谈、播客、拆短视频、精华 | `highlight_picker.py` → `audio_boundary_snap.py`，多条时接 `shorts_batch.py` |
| 停顿、剪紧、jump cut | `jump_cut.py` |
| 口头禅、卡壳、重复句 | `rough_cut.py` |
| 开头、前三秒、hook | `hook_variants.py` |
| B-roll、stock、补画面 | `stock_material_plan.py` + `auto_enrich.py` |
| 生图、Dreamina/即梦、Veo/Sora/LTX/Wan | `storyboard_plan.py` + `video_prompt_pack.py` |
| 生成经验库、prompt lessons、复用复盘经验 | `generation_lessons.py verify`；与生成视频同时出现时，在 prompt pack 前验证并注入 library |
| 多镜头/跨镜头连续性、镜头衔接、角色/道具连续性 | 先 `generated_clip_review.py`，再 `generated_sequence_review.py` 提取相邻边界证据并审计 |
| 参考视频节奏、参考广告节奏、复刻剪辑结构 | 成片后用 `reference_edit_rhythm.py analyze` 量化 hard-cut 结构和 contact sheets；默认 WARN，明确验收时才加 `--require-match` |
| BGM、音效、声音设计 | `audio_cue_sheet.py` |
| 手持抖动、画面抖动、视频防抖、stabilize | `video_stabilization.py plan` → `apply --comparison` → `confirm` |
| 录屏、点击、热点 | `screen_focus.py` |
| facecam、小窗、PIP | `pip_overlay.py` |
| 发布、标题、文案、上传包 | `generate_caption.py` + `publish_package.py` |

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 接入 manifest

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage analysis \
  --require edit_brief_plan \
  --strict
```

`edit_brief_plan.json` 默认会被发现；当 `summary.blocking > 0` 时，即使不是 required，也会作为可见 blocker。

## 使用建议

- 先看 `work/edit_brief_plan.md`，不要盲跑所有命令；它是 runbook，不是自动执行器。
- 对生成视频或 paid provider，只生成 prompt pack 和审批 gate；不会提交 Dreamina/即梦/Veo/Sora 任务。
- 如果实际项目已有 transcript、render_config 或 clean_script，可用 `--transcript` 指向现有文件，并删除 Markdown 里不需要的步骤。
- 复杂项目先跑这一步，再把确认后的 gate 交给 `review_dashboard.py` 或 `project_resume.py`。
