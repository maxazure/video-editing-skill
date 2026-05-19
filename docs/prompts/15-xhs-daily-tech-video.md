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
3. 自动选 B-roll、加章节卡、贴贴纸、卡点对齐 BGM
4. 用 Heavy 字体烧字幕（永远不漏内部 token 到画面）
5. 跑响度规范化 + atempo 加速
6. 导出 3 个平台版本（小红书 3:4、抖音 9:16、视频号 ≤60s）
7. 生成标题 + 发布正文 + 标签 + 发布时段建议

## 提示词模板

把下面这段贴给 Claude / ChatGPT，替换 `<...>` 占位：

```
我是 BestAI Labs 的 Jay，正在做 day<NN> 小红书短视频，主题是 <主题描述>。
口播素材在 ~/Movies/xiaohongshu/day<NN>/origin/<voice>.mp3，无声视频素材在
同目录其他 .mp4 文件里。请用 video-editing skill V3 流水线完成这条视频。

跑这套：

1. python3 scripts/transcribe.py origin/<voice>.mp3 \
     --engine auto --model auto --language zh --word-timestamps --detect-fillers \
     > work/transcript.json

2. python3 scripts/rewrite_script.py \
     --transcript work/transcript.json \
     --structure pain_solve \
     --hook-template auto \
     --max-duration 150 \
     --persona "BestAI Labs 创始人 / Mac mini M1 / AI + 小公司" \
     --emit-prompt > work/llm_prompt.md

   (我会把 work/llm_prompt.md 贴给你；你输出 JSON，按要求只输出 JSON 不要解释)

3. （你输出 JSON 后）保存到 work/llm.json，然后：
   python3 scripts/rewrite_script.py \
     --transcript work/transcript.json \
     --llm-output work/llm.json \
     --output work/clean_script.md

4. python3 scripts/auto_enrich.py \
     --transcript work/transcript.json \
     --clean-script work/clean_script.md \
     --bgm origin/<bgm>.mp3 \
     --output work/enrich_plan.json

4b. # 如果 enrich_plan.json 的 imagegen[] 非空 → 用 Codex 内置 imagegen 生图：
    # 生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
    # 把每条 prompt_en 喂给 imagegen，1024x1536 high quality，存到 work/imagegen/
    # （不需要 OPENAI_API_KEY；Codex 自动路由到 gpt-image-2）
    # 详见 docs/prompts/19-imagegen.md

5. python3 scripts/content_guard.py \
     --script work/clean_script.md \
     --title "<候选标题>" \
     --strict

   (任何 HARD violation 必须先去掉再继续；SOFT 警告需要权衡)

6. python3 scripts/render_final.py \
     --config work/render_config.json \
     --profile tech_pro \
     --primary-speed 1.25 \
     --subtitle-style karaoke \
     --output output/day<NN>_master.mp4

7. python3 scripts/render_qa.py \
     output/day<NN>_master.mp4 \
     --platform douyin \
     --json output/day<NN>_master_qa.json

8. python3 scripts/multi_export.py \
     output/day<NN>_master.mp4 \
     --output-dir output/ \
     --platforms xhs douyin wxch

9. python3 scripts/render_qa.py \
     output/day<NN>_master_xhs.mp4 \
     --platform xhs \
     --json output/day<NN>_xhs_qa.json

10. python3 scripts/render_qa.py \
     output/day<NN>_master_douyin.mp4 \
     --platform douyin \
     --json output/day<NN>_douyin_qa.json

11. python3 scripts/generate_caption.py \
     --script work/clean_script.md \
     --profile tech_pro \
     --output output/day<NN>_caption.json

最后给我：
- 三个平台的 mp4 路径
- caption.json 里的 title + caption_body + tags + publish_time_hint
- enrich_plan.json 里 broll/sticker/chapter 总数（确认丰富度足够）
- content_guard 的输出（必须 ✅ 无违规）
- render_qa 的输出（必须没有 FAIL；WARN 要解释）

注意事项：
- 永远不要在画面上漏 1.25x / mlx-whisper / loudnorm 这类内部 token
- 字幕字体走 Source Han Sans SC Heavy 或 STHeiti Medium，不要用 W3
- 1.25x 之后必须做响度规范化（render_final 默认会做，不要 --no-loudnorm）
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
│   ├── enrich_plan.json    # broll/sticker/chapter cues
│   └── render_config.json  # 喂给 render_final 的配置
└── output/
    ├── day<NN>_master.mp4              # 9:16 主版本
    ├── day<NN>_master_xhs.mp4          # 3:4 小红书发布版
    ├── day<NN>_master_douyin.mp4       # 9:16 抖音版
    ├── day<NN>_master_wxch.mp4         # 9:16 ≤60s 视频号版
    ├── day<NN>_master_qa.json          # 主片 QA
    ├── day<NN>_xhs_qa.json             # 小红书版 QA
    ├── day<NN>_douyin_qa.json          # 抖音版 QA
    ├── day<NN>_caption.json            # 标题 + 正文 + 标签
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
