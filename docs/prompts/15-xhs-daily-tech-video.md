# 15 — 小红书每日科技短视频（完整 V3 流水线）

> 这是 AI/创业/效率方向 daily 短视频的提示词模板，跑完一遍输出可发布的 3:4 / 9:16 多版本视频 + 标题 + 正文 + 标签。

## 使用场景

你有：
- 一段 5-15 分钟的口播音频（mp3/m4a/wav）
- 若干段无声素材（DJI/手机/屏幕录制）
- 一个大致的主题方向

你要 AI 帮你完成：
1. 转写口播 + 标记口误/填充词/长停顿
2. 让 LLM 重组成符合小红书爆款公式的 5 段式结构
3. 自动选 B-roll、加章节卡、贴贴纸；音乐主导内容可从 BGM 先生成节拍剪辑骨架
4. 用 Heavy 字体烧字幕（永远不漏内部 token 到画面）
5. 跑响度规范化 + atempo 加速
6. 导出 3 个平台版本（小红书 3:4、抖音 9:16、视频号 ≤60s）
7. 生成标题 + 发布正文 + 标签 + 发布时段建议

## 提示词模板

把下面这段交给 GPT-5.6 / Claude 等支持长流程工具调用的 Agent，替换 `<...>` 占位：

```
我是 BestAI Labs 的 Jay，正在做 day<NN> 小红书短视频，主题是 <主题描述>。
口播素材在 ~/Movies/xiaohongshu/day<NN>/origin/<voice>.mp3，无声视频素材在
同目录其他 .mp4 文件里。请用 video-editing skill V3 流水线完成这条视频。

跑这套：

0. # 可选：先把这次自然语言需求固化成本地 runbook：
   python3 scripts/edit_brief_plan.py \
     --brief "day<NN> 小红书短视频，主题 <主题描述>，口播 + 无声素材，重组故事、自动丰富、渲染、多平台导出和发布文案" \
     --project-dir . \
     --output work/edit_brief_plan.json \
     --markdown work/edit_brief_plan.md \
     --platform xhs
   # 详见 docs/prompts/64-edit-brief-plan.md

0a. # 可选：如果同一访谈/播客/活动由两台以上设备录制，转写和选片前先统一时间线：
    python3 scripts/multicam_sync.py \
      --reference-media origin/<cam-a>.mp4 \
      --angle origin/<cam-b>.mp4 \
      --angle origin/<cam-c>.mp4 \
      --output work/multicam_sync_plan.json \
      --markdown work/multicam_sync_plan.md \
      --preview-output output/verify/multicam_sync_preview.mp4 \
      --apply-preview \
      --strict
    # 先看 offset/confidence/有效音轨/common overlap/pairwise divergence，再播放拍手、口型或屏幕动作。
    # 原片不改；30 分钟以上还要复核头/中/尾，因为 V1 不测相机 clock drift。
    # 详见 docs/prompts/76-multicam-sync.md

0b. # 可选：手持素材有不想要的抖动时，先保留原片并生成稳定工作副本：
    python3 scripts/video_stabilization.py doctor
    python3 scripts/video_stabilization.py plan origin/<handheld>.mp4 \
      --decision stabilize \
      --reviewed-by "<reviewer-label>" \
      --note "<为什么这是需要去掉的抖动，而不是有意运镜>" \
      --output work/video_stabilization_plan.json \
      --markdown work/video_stabilization_plan.md
    python3 scripts/video_stabilization.py apply work/video_stabilization_plan.json \
      --output work/<handheld>-stabilized.mp4 \
      --comparison verify/<handheld>-stabilization-compare.mp4 \
      --markdown work/video_stabilization_plan.md
    # 用 1× 看完整左原片/右稳定版；可接受后才 confirm：
    python3 scripts/video_stabilization.py confirm work/video_stabilization_plan.json \
      --reviewed-by "<reviewer-label>" \
      --note "完整 A/B 已看；人物、边缘和有意摇摄均可接受" \
      --markdown work/video_stabilization_plan.md
    # 后续用 work/<handheld>-stabilized.mp4，不覆盖 origin；详见 docs/prompts/84-video-stabilization.md

1. python3 scripts/transcribe.py origin/<voice>.mp3 \
     --engine auto --model auto --language zh --word-timestamps --detect-fillers \
     > work/transcript.json

1a. # 可选：专业术语/中英混说较多时，先做全篇上下文语义审校；模型建议不会直接应用：
    python3 scripts/semantic_transcript_review.py prepare \
      --transcript work/transcript.json \
      --output work/semantic_review_request.json \
      --markdown work/semantic_review_request.md
    # 让当前 Agent/模型按 request 的 response_template 写 work/semantic_review_response.json。
    python3 scripts/semantic_transcript_review.py audit \
      --transcript work/transcript.json \
      --response work/semantic_review_response.json \
      --output work/transcript_semantic_review.json \
      --markdown work/transcript_semantic_review.md \
      --strict
    # 首次 strict 返回 2 代表仍需人工 choices；逐项 approve/reject 后应用：
    python3 scripts/semantic_transcript_review.py apply \
      --transcript work/transcript.json \
      --audit work/transcript_semantic_review.json \
      --choices work/semantic_review_choices.json \
      --output work/transcript_semantic_reviewed.json \
      --markdown work/transcript_semantic_review.md
    # 详见 docs/prompts/79-semantic-transcript-review.md

1b. # 生成零上传的同步媒体校稿页；人工保存 review.txt 后回写 reviewed transcript：
    python3 scripts/transcript_review.py html \
      --transcript work/transcript_semantic_reviewed.json \
      --video origin/<voice>.mp3 \
      --corrections work/corrections.json \
      --output work/transcript_review.html \
      --max-cps 20
    # 如果跳过了 1a，把上面和下面的 transcript_semantic_reviewed.json 改回 transcript.json。
    # 打开 work/transcript_review.html，点击时间码试听、修正文字、保存 work/transcript_review.txt。
    python3 scripts/transcript_review.py apply \
      --transcript work/transcript_semantic_reviewed.json \
      --review work/transcript_review.txt \
      --output work/transcript_reviewed.json
    # 详见 docs/prompts/36-transcript-review.md

1c. # 可选：先生成多个前三秒 hook 角度，选一个再进入清稿：
    python3 scripts/hook_variants.py \
      --transcript work/transcript_reviewed.json \
      --topic "<主题描述>" \
      --persona "BestAI Labs 创始人 / Mac mini M1 / AI + 小公司" \
      --platform xhs \
      --output work/hook_variants.json \
      --markdown work/hook_variants.md \
      --strict
    # 打开 work/hook_variants.md，选中的 hook 文本放进下一步 LLM prompt。
    # 详见 docs/prompts/62-hook-variants.md

1d. # 可选：如果已经有确认的成片稿，且同一句录了多个 take，就按稿装配原话：
    python3 scripts/script_alignment.py \
      --target-script work/target_script.md \
      --transcripts-dir work/takes \
      --output work/script_alignment.json \
      --markdown work/script_alignment.md \
      --render-config work/render_config.json \
      --clean-script work/clean_script.md \
      --strict
    # 低分/同分候选先看听素材，把 candidate id 写进 choices JSON，再加 --choices 重跑。
    # summary.blocking=0 后可跳过下面 2-3 的自由重写，直接进入 enrich / preflight / render。
    # 详见 docs/prompts/78-script-alignment.md

2. python3 scripts/rewrite_script.py \
     --transcript work/transcript_reviewed.json \
     --structure pain_solve \
     --hook-template auto \
     --max-duration 150 \
     --persona "BestAI Labs 创始人 / Mac mini M1 / AI + 小公司" \
     --emit-prompt > work/llm_prompt.md

   (我会把 work/llm_prompt.md 贴给你；你输出 JSON，按要求只输出 JSON 不要解释)

3. （你输出 JSON 后）保存到 work/llm.json，然后：
   python3 scripts/rewrite_script.py \
     --transcript work/transcript_reviewed.json \
     --llm-output work/llm.json \
     --output work/clean_script.md

4. python3 scripts/auto_enrich.py \
     --transcript work/transcript_reviewed.json \
     --clean-script work/clean_script.md \
     --bgm origin/<bgm>.mp3 \
     --output work/enrich_plan.json

4a. # 仅音乐视频 / 产品 montage / 明确要求卡点时：先生成可审计时间槽，不自动选素材或渲染
    python3 scripts/beat_sync.py \
      --bgm origin/<bgm>.mp3 \
      --generate-plan \
      --duration <成片秒数> \
      --beats-per-cut 4 \
      --min-segment 0.75 \
      --max-segment 3 \
      --output work/beat_edit_plan.json \
      --markdown work/beat_edit_plan.md
    # detection.method=fallback_grid 时必须听音乐逐切点复核；确认后再把素材映射进 render_config。

4aa. # 可选：动作 / 产品 reveal 有明确 impact frame 时，先把局部慢动作做成新的 source：
     python3 scripts/speed_ramp.py plan origin/<action>.mp4 \
       --ramp <ramp_start>,<impact>,1,0.25,s_curve \
       --hold <impact>,<hold_end>,0.25 \
       --ramp <hold_end>,<ramp_end>,0.25,1,ease \
       --interpolate-fps 120 \
       --output work/speed_ramp_plan.json \
       --markdown work/speed_ramp_plan.md
     python3 scripts/speed_ramp.py verify work/speed_ramp_plan.json --strict
     python3 scripts/speed_ramp.py apply work/speed_ramp_plan.json \
       --output work/action-speed-ramped.mp4 \
       --receipt work/speed_ramp_apply.json
     # 用新 MP4 进入 render_config；1×带音频复核 impact / 插值伪影后再继续，旧字幕时间码不可复用。
     # 详见 docs/prompts/83-speed-ramp.md

4b. # 如果 enrich_plan.json 的 imagegen[] 非空 → 用 Codex 内置 imagegen 生图：
    # 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
    # 把每条 prompt_en 喂给 imagegen，1024x1536 high quality，存到 work/imagegen/
    # （不需要 OPENAI_API_KEY；Codex 自动路由到 gpt-image-2）
    # 详见 docs/prompts/19-imagegen.md

4c. # 生成分镜 shot cards，先审查生成路由和连续性，再决定是否消耗视频生成额度：
    python3 scripts/storyboard_plan.py \
      --transcript work/transcript_reviewed.json \
      --clean-script work/clean_script.md \
      --output work/storyboard_plan.json \
      --markdown work/storyboard_plan.md \
      --max-shots 8 \
      --target-aspect 9:16
    # codex_imagegen 用 Codex 内置 image_gen；dreamina_video 只是建议，提交 Dreamina/即梦前先确认 credits。
    # 详见 docs/prompts/24-storyboard-plan.md

4d. # 可选：把分镜转成 Dreamina/Veo/LTX/Wan/Sora 视频生成提示词包：
    python3 scripts/video_prompt_pack.py \
      --storyboard-plan work/storyboard_plan.json \
      --asset-root work \
      --style-reference work/imagegen/style-key.png \
      --output work/video_prompt_pack.json \
      --markdown work/video_prompt_pack.md \
      --strict
    # --strict 会在视频生成还没确认 credits 时返回 2；审批后再加 --approved。
    # style-key.png 可按 video_prompt_pack.md 的 Character / Style Reference prompt 用 Codex image_gen 生成。
    # 详见 docs/prompts/45-video-prompt-pack.md

4e. # paid provider 提交前，检查 image-to-video 首帧和共享 style key：
    python3 scripts/reference_frame_preflight.py \
      --prompt-pack work/video_prompt_pack.json \
      --output work/reference_frame_preflight.json \
      --markdown work/reference_frame_preflight.md \
      --require-style-reference \
      --strict
    # blocker 未清零时不要提交生成任务；本步骤不联网、不消耗 credits。
    # 详见 docs/prompts/71-reference-frame-preflight.md

4f. # 如果已经提交 Dreamina/即梦或其他异步生成任务，保存 submit_id/task id 并跟踪下载：
    python3 scripts/generation_task_log.py add \
      --log work/generation_tasks.json \
      --provider dreamina \
      --task-id "<submit_id>" \
      --shot-id shot_002 \
      --expected-path work/generated_video/shot_002.mp4 \
      --status submitted \
      --markdown work/generation_tasks.md \
      --strict
    # 任务完成并下载后，用 update 写入 asset-path；未完成/未下载会阻塞 pipeline_manifest。
    # 详见 docs/prompts/46-generation-task-log.md

4g. # 把分镜转成素材清单，先审查哪些素材 ready、需要生成、需要审批或要补 B-roll：
    python3 scripts/storyboard_assets.py \
      --storyboard-plan work/storyboard_plan.json \
      --asset-root work \
      --output work/storyboard_assets.json \
      --markdown work/storyboard_assets.md
    # 渲染前可加 --strict；如果有 needs_approval，提交 Dreamina/即梦前必须确认 credits。
    # 详见 docs/prompts/25-storyboard-assets.md

4h. # 如果输入是完整口播视频、访谈或录屏，且停顿很多，可先生成去停顿 cut list：
    python3 scripts/jump_cut.py origin/<talking_video>.mp4 \
      --dry-run \
      --cut-list work/jumpcut.json \
      --strict
    python3 scripts/timeline_view.py origin/<talking_video>.mp4 \
      --cut-list work/jumpcut.json \
      --output-dir output/verify/jumpcut \
      --limit 12
    # 先检查 work/jumpcut.json 的 removed_segments，确认没有误切人声；
    # 再查看 output/verify/jumpcut/*.png 的 filmstrip + waveform；
    # 需要独立去停顿成片时再加 --output output/day<NN>_jumpcut.mp4；
    # 默认 --fade-duration 0.03 会降低切点爆音，只有需要硬切原声时才设为 0。
    # 默认最多删除源时长的 20%；超限会 blocked，审查后才调高 --max-removal-ratio 或加 --allow-over-budget。
    # 详见 docs/prompts/21-jump-cut.md 和 docs/prompts/22-timeline-view.md

5. python3 scripts/content_guard.py \
     --script work/clean_script.md \
     --title "<候选标题>" \
     --strict

   (任何 HARD violation 必须先去掉再继续；SOFT 警告需要权衡)

5a. # 如果视频包含新闻、数据、产品事实、来源页或截图证据，先做 source receipts：
    python3 scripts/source_receipts.py \
      --claims work/source_claims.json \
      --project-dir . \
      --output work/source_receipts.json \
      --markdown work/source_receipts.md \
      --html work/source_receipts.html \
      --require-primary-source \
      --strict
   # source_claims.json 里的 screenshot/source_file 路径相对该 JSON 所在目录；
   # 纯观点类视频可跳过，事实型视频发布前建议在 pipeline_manifest 里 --require source_receipts。
   # 详见 docs/prompts/58-source-receipts.md

5aa. # 可选：如果 render_config / enrich_plan 已经过审，后续调整需要可撤销、可重做：
     python3 scripts/edit_revision.py prepare \
       --project-dir . \
       --artifact work/render_config.json \
       --artifact work/enrich_plan.json \
       --depends-on work/transcript_reviewed.json \
       --title "<本次剪辑修订>" \
       --reason "<审片依据>" \
       --output work/edit_revision_proposal.json
     # 只改 proposal 的 proposed_content；再依次 audit、独立 approval、apply。
     # audit --strict 在 pending_approval 时退出 2 是预期；apply 后可用 status/undo/redo。
     # 详见 docs/prompts/80-edit-revision.md

5aaa. # 可选：把已经通过预检的 render_config 存成无路径配方，供同栏目换素材复用：
      python3 scripts/edit_recipe.py export \
        --config work/render_config.json \
        --name "<series-name>" \
        --description "<固定节奏/字幕/BGM/overlay 结构>" \
        --output "work/recipes/<series-name>_edit_recipe.json" \
        --markdown "work/recipes/<series-name>_edit_recipe.md"
      # 新项目先看 Markdown slot 表，再用 replay --bind SLOT=PATH（每槽一次）生成新 render_config。
      # replay 会记录 binding hash 并再次 preflight；成功后仍必须渲染并人工审片。
      # 详见 docs/prompts/82-edit-recipe.md

5b. python3 scripts/edit_preflight.py \
      --config work/render_config.json \
      --enrich-plan work/enrich_plan.json \
      --output work/edit_preflight.json \
      --markdown work/edit_preflight.md \
      --strict

   (缺文件、空剪辑、非法时间段、overlay 超出时间线或像素坐标缺尺寸时，先修 artifact 再渲染)

5c. # 每个平台分别检查字幕、PIP、CTA、章节卡和点击 marker 是否会被平台 UI 遮挡：
     python3 scripts/platform_safe_area_qa.py \
       --config work/render_config.json \
       --enrich-plan work/enrich_plan.json \
       --platform xhs \
       --output output/verify/day<NN>_xhs_platform_safe_area_qa.json \
       --markdown output/verify/day<NN>_xhs_platform_safe_area_qa.md \
       --guide output/verify/day<NN>_xhs_platform_safe_area_guide.svg \
       --strict

   (抖音/视频号派生版分别改用 --platform douyin / wxch；平台 UI 有变化时传 --safe-left/top/right/bottom 实测像素)

6. python3 scripts/render_final.py \
     --config work/render_config.json \
     --enrich-plan work/enrich_plan.json \
     --profile tech_pro \
     --primary-speed 1.25 \
     --bgm-ducking \
     --subtitle-style karaoke \
     --output output/day<NN>_master.mp4

   （仅当源口播有稳定空调/风扇/电流底噪并已 A/B 试听时，加 `--speech-denoise light`；
   噪声明显才试 `medium`，已做过云端/机内降噪或 VAD/noise gate 时保持 off。）

7. python3 scripts/render_qa.py \
     output/day<NN>_master.mp4 \
     --platform douyin \
     --json output/day<NN>_master_qa.json

7a. # 对最终编码文件做镜头级亮度/色度/饱和度/broadcast-range 复核。
    python3 scripts/shot_color_qa.py \
      output/day<NN>_master.mp4 \
      --output output/verify/day<NN>_shot_color_qa.json \
      --markdown output/verify/day<NN>_shot_color_qa.md \
      --strict
    # 色彩/亮度跳变默认只 WARN；按 Markdown 时间码看 master 后再决定是否回源重调。
    # 多机位/B-roll/生成素材混剪或使用 color_grade 后推荐必跑。
    # 详见 docs/prompts/81-shot-color-qa.md

7b. # 先生成与主片 speed / cover offset 对齐的 timed-text JSON，再做留存节奏风险审计。
     python3 scripts/subtitle_pack.py \
       --config work/render_config.json \
       --output-dir output/subtitles \
       --basename day<NN>_master \
       --speed 1.25 \
       --offset 2.0
     python3 scripts/subtitle_readability_qa.py \
       output/subtitles/day<NN>_master.json \
       --media output/day<NN>_master.mp4 \
       --output output/verify/day<NN>_subtitle_readability_qa.json \
       --markdown output/verify/day<NN>_subtitle_readability_qa.md \
       --strict
     python3 scripts/retention_rhythm_qa.py \
       output/day<NN>_master.mp4 \
       --timed-text output/subtitles/day<NN>_master.json \
       --output output/verify/day<NN>_retention_rhythm_qa.json \
       --markdown output/verify/day<NN>_retention_rhythm_qa.md \
       --strict

7c. python3 scripts/audio_master_report.py \
      output/day<NN>_master.mp4 \
      --output output/day<NN>_audio_master_report.json \
      --markdown output/day<NN>_audio_master_report.md \
      --strict

8. # 如果 render_qa 有 WARN/FAIL，或要抽查 hook / 转场 / 片尾：
   python3 scripts/timeline_view.py output/day<NN>_master.mp4 \
     --at <可疑秒数> \
     --radius 1.5 \
     --output output/verify/day<NN>_<秒数>s.png

9. python3 scripts/multi_export.py \
     output/day<NN>_master.mp4 \
     --output-dir output/ \
     --platforms xhs douyin wxch

9a. # 可选：客户/平台明确限制文件大小时，对选定发布版做 source-bound 两遍交付编码：
    python3 scripts/delivery_encode.py plan \
      output/day<NN>_master_douyin.mp4 \
      --delivery output/day<NN>_douyin_delivery.mp4 \
      --max-size-mib <上限> \
      --output work/delivery_encode_plan.json \
      --markdown work/delivery_encode_plan.md
    python3 scripts/delivery_encode.py apply work/delivery_encode_plan.json \
      --markdown work/delivery_encode_plan.md
    python3 scripts/delivery_encode.py verify work/delivery_encode_plan.json
    # 完整解码通过不等于画质批准；正常速度看完整交付版，再跑 render_qa 并纳入 approval receipt。

9b. # 可选：如果要交给 Premiere / FCP / Resolve 继续精修：
    python3 scripts/export_edl.py \
      --config work/render_config.json \
      --output work/day<NN>_edit.edl \
      --fps 30
    python3 scripts/export_fcpxml.py \
      --config work/render_config.json \
      --output work/day<NN>_edit.fcpxml \
      --fps 30 \
      --width 1080 \
      --height 1920
    python3 scripts/export_otio.py \
      --config work/render_config.json \
      --output work/day<NN>_edit.otio \
      --fps 30

10. python3 scripts/render_qa.py \
     output/day<NN>_master_xhs.mp4 \
     --platform xhs \
     --json output/day<NN>_xhs_qa.json

11. python3 scripts/render_qa.py \
     output/day<NN>_master_douyin.mp4 \
     --platform douyin \
     --json output/day<NN>_douyin_qa.json

12. python3 scripts/generate_caption.py \
     --script work/clean_script.md \
     --profile tech_pro \
     --output output/day<NN>_caption.json

12b. # 为同一条视频生成 3 套封面并先看 feed-size 预览：
     python3 scripts/cover_variants.py \
       output/day<NN>_master_xhs.mp4 \
       --title "<4-8字封面文字>" \
       --subtitle "<可选副标题>" \
       --caption output/day<NN>_caption.json \
       --platform xhs \
       --output-dir output/covers \
       --render \
       --output work/cover_variants.json \
       --markdown work/cover_variants.md
     # 看完 output/covers/*_preview.png 后，重跑并加：
     # --select cover-c --require-selection --strict
     # 详见 docs/prompts/68-cover-variants.md

13. python3 scripts/pipeline_manifest.py \
     --project-dir . \
     --target-stage publish_ready \
     --require shot_color_qa \
     --output work/pipeline_manifest.json \
     --markdown work/pipeline_manifest.md \
     --strict

14. # 完整审片并确认封面/文案/字幕后，把最终交付件绑定到具体 SHA-256：
    python3 scripts/approval_receipt.py create \
      --project-dir . \
      --artifact output/day<NN>_master_xhs.mp4 \
      --artifact output/day<NN>_master_douyin.mp4 \
      --artifact output/day<NN>_master_wxch.mp4 \
      --artifact output/day<NN>_caption.json \
      --artifact output/day<NN>_master_qa.json \
      --approved-by "<reviewer label>" \
      --note "<正常速度完整审片和文案/封面/字幕复核说明>" \
      --output work/approval_receipt.json \
      --markdown work/approval_receipt.md
    # 如果选了独立封面/字幕 sidecar，也分别加 --artifact；不要把会重写的 manifest/package 放进收据。
    # 详见 docs/prompts/77-approval-receipt.md

15. python3 scripts/publish_package.py \
     --project-dir . \
     --platforms xhs douyin wxch \
     --require-approval-receipt \
     --output work/publish_package.json \
     --markdown work/publish_package.md \
     --strict

16. python3 scripts/project_resume.py \
     --project-dir . \
     --target-stage publish_ready \
     --output work/project_resume.json \
     --markdown work/project_resume.md \
     --agent-note CLAUDE.md

17. python3 scripts/review_proxy.py \
     output/day<NN>_master.mp4 \
     --output output/verify/day<NN>_review_proxy.mp4 \
     --manifest output/verify/day<NN>_review_proxy.json \
     --markdown output/verify/day<NN>_review_proxy.md

18. python3 scripts/review_dashboard.py \
     --project-dir . \
     --target-stage publish_ready \
     --output work/review_dashboard.json \
     --html work/review_dashboard.html \
     --strict

最后给我：
- 三个平台的 mp4 路径
- caption.json 里的 title + caption_body + tags + publish_time_hint
- publish_package.md 里的每个平台上传 checklist 和 blockers
- approval_receipt.md 和最新 verify 状态（必须 `current`；它不是数字签名）
- project_resume.md 里的 status / phase / recommended_first_action（方便下次续跑）
- review_proxy.mp4 路径；审片反馈要引用画面可见时间码，不能把它当发布成片
- review_dashboard.html 路径，以及 review_dashboard.json 的 review_state / review_items 数量
- 如果跑了 source_receipts，给我 source_receipts.html 路径和 `summary.blocking` 数量
- enrich_plan.json 里 broll/sticker/chapter 总数（确认丰富度足够）
- content_guard 的输出（必须 ✅ 无违规）
- render_qa 的输出（必须没有 FAIL；WARN 要解释）
- shot_color_qa 的输出（broadcast-range / coverage BLOCK 必须修；切点 WARN 要看 master）
- subtitle_readability_qa 的输出（BLOCK 必须修；WARN 要在正常速度看成片）
- retention_rhythm_qa 的输出（BLOCK 必须修；WARN 要结合成片人工判断）
- audio_master_report 的输出（必须 `summary.blocking == 0`）
- 如有 jump_cut 或 QA WARN/FAIL，给我 timeline_view PNG 路径和人工判断

注意事项：
- 永远不要在画面上漏 1.25x / mlx-whisper / loudnorm 这类内部 token
- 字幕字体走 Source Han Sans SC Heavy 或 STHeiti Medium，不要用 W3
- 1.25x 之后必须做响度规范化（render_final 默认会做，不要 --no-loudnorm）
- `--speech-denoise` 默认关闭；只处理稳态底噪，先用 10–20 秒样片比较 off/light，不能用它代替咳嗽、敲击、混响或多人多麦修复
- 多机位素材先跑 `multicam_sync.py`；不能只看起点 offset，长片还要检查头/中/尾是否逐渐漂移
- 音乐主导内容可先跑 `beat_sync.py --generate-plan`；固定 BPM fallback 只能作为复核草稿，不能冒充真实节拍检测
- 有 BGM 的口播成片用 `--bgm-ducking`，并在正常速度试听旁白入口、停顿恢复和片尾；音乐主导视频可不启用
- 发布前用 audio_master_report 确认 LUFS / true peak / 长静音，不要只凭耳朵判断
- shot_color_qa 的亮度/色度跳变是审片提示，不是审美分；不要为了清 WARN 把有意的日夜/图形切换调平
- subtitle_readability_qa 的 CPS / 行长 WARN 是人工复核提示，不要为了清零机械拆句
- retention_rhythm_qa 只是可观测节奏风险，不是留存率或爆款预测；不要为了消除 WARN 机械加切点
- 如果 content_guard 拦截，先重写标题再继续，不要 --no-content-guard 绕过
```

## 输出对照

跑完后你应该有：

```
day<NN>/
├── origin/                 # 你提供的原始素材
├── work/
│   ├── transcript.json
│   ├── llm_prompt.md       # 喂给 LLM 的 prompt
│   ├── llm.json            # LLM 返回的 JSON
│   ├── clean_script.md     # 5 字段重组后的清稿
│   ├── source_receipts.json # 事实来源 proof deck（事实型视频可选/推荐）
│   ├── source_receipts.html # 浏览器复核版 source deck
│   ├── enrich_plan.json    # broll/sticker/chapter cues
│   ├── storyboard_plan.json # 分镜 shot cards + 生成路由
│   ├── storyboard_plan.md   # 人工 review 版分镜卡
│   ├── video_prompt_pack.json # 视频生成提示词包 + paid approval gate
│   ├── video_prompt_pack.md   # 人工 review 版 provider prompts
│   ├── reference_frame_preflight.json # 首帧/style key 画幅与背景 gate
│   ├── reference_frame_preflight.md   # 人工 review 版参考帧检查
│   ├── generation_tasks.json # 异步生成任务 submit_id / 下载 gate
│   ├── generation_tasks.md   # 人工 review 版任务台账
│   ├── storyboard_assets.json # 素材任务清单 + ready/paid 预检
│   ├── storyboard_assets.md   # 人工 review 版素材表
│   ├── jumpcut.json        # 可选：去停顿 cut list
│   ├── day<NN>_edit.edl    # 可选：NLE handoff
│   ├── day<NN>_edit.edl.json # 可选：EDL manifest
│   ├── day<NN>_edit.fcpxml # 可选：FCPXML handoff
│   ├── day<NN>_edit.fcpxml.json # 可选：FCPXML manifest
│   ├── day<NN>_edit.otio   # 可选：OTIO handoff
│   ├── day<NN>_edit.otio.json # 可选：OTIO manifest
│   ├── render_config.json  # 喂给 render_final 的配置
│   ├── edit_preflight.json # 渲染前预检 gate
│   ├── edit_preflight.md
│   ├── pipeline_manifest.json # 发布前 gate 汇总
│   ├── pipeline_manifest.md
│   ├── delivery_encode_plan.json # 可选：目标大小交付编码 / source + output hash gate
│   ├── delivery_encode_plan.md
│   ├── cover_variants.json # 封面 A/B 方案 + selected_cover
│   ├── cover_variants.md
│   ├── approval_receipt.json # 人工已复核交付件的 SHA-256 收据
│   ├── approval_receipt.md
│   ├── publish_package.json # 最终上传包
│   ├── publish_package.md
│   ├── project_resume.json # 续跑上下文包
│   ├── project_resume.md
│   ├── review_dashboard.json # 人工复核面板
│   └── review_dashboard.html
└── output/
    ├── verify/                         # timeline_view / review proxy 审片产物
    │   ├── day<NN>_review_proxy.mp4     # 低码率 timecoded 审片视频，不可发布
    │   ├── day<NN>_review_proxy.json    # review_proxy.v1 / 可复现命令
    │   ├── day<NN>_review_proxy.md      # 审片说明
    │   ├── day<NN>_subtitle_readability_qa.json # CPS / 时长 / 重叠 / 越界门禁
    │   ├── day<NN>_subtitle_readability_qa.md   # cue 时间范围 + 修复建议
    │   ├── day<NN>_retention_rhythm_qa.json # 成片 hook / 长镜头 / 节奏风险门禁
    │   ├── day<NN>_retention_rhythm_qa.md   # 时间范围 + 修复建议
    │   ├── day<NN>_shot_color_qa.json       # 镜头色彩 / 曝光 / broadcast-range gate
    │   └── day<NN>_shot_color_qa.md         # 镜头指标 + 可疑切点复核命令
    ├── day<NN>_master.mp4              # 9:16 主版本
    ├── day<NN>_master_xhs.mp4          # 3:4 小红书发布版
    ├── day<NN>_douyin_delivery.mp4     # 可选：有硬大小上限的两遍交付版
    ├── day<NN>_master_douyin.mp4       # 9:16 抖音版
    ├── day<NN>_master_wxch.mp4         # 9:16 ≤60s 视频号版
    ├── day<NN>_master_qa.json          # 主片 QA
    ├── day<NN>_audio_master_report.json # 成片响度报告
    ├── day<NN>_audio_master_report.md
    ├── day<NN>_xhs_qa.json             # 小红书版 QA
    ├── day<NN>_douyin_qa.json          # 抖音版 QA
    ├── day<NN>_caption.json            # 标题 + 正文 + 标签
    ├── covers/                         # 完整封面 + feed-size preview
    └── multi_export_manifest.json
```

## 故事结构选择指南

`--structure` 参数三选一：

| 选项 | 适合 | Hook 模板 | CTA 模板 |
|---|---|---|---|
| `pain_solve` | 干货 / 教程 / AI 工具横评 | anti_consensus / pain_relate / number_result | save_bait + comment_lure |
| `story_reversal` | 个人故事 / 创业复盘 / 心路 | scene_immersion / contrast_reverse | resonance_seek |
| `listicle` | 盘点 / N 个 X / 资源清单 | benefit_save / number_result | save_bait + cliffhanger |

## 节奏参数（由 `--profile tech_pro` 提供，无需手动）

- 时长 90 秒（max 180）
- 镜头节奏：前 3s 每 0.6s 一切（3-5 个钩子镜头），正文每 2.5s 一切
- 字幕：≤14 字/行，1.2-3.0s 显示，64px Heavy 字体，4px 描边
- 音频：BGM 比人声低 16 dB；正文密度 > 2.5 字/秒时 BGM 自动降到 -20 dB
- 比例：主出 9:16，小红书版裁到 3:4
