# 125 — Reference Story Formula 参考片故事公式迁移

当用户要求借鉴一条参考广告、短片或 UGC 的“情绪公式”“叙事机制”，并希望新片使用不同主题、人物、产品和表达时，使用 `reference_story_formula.py`。它把参考视频、timecoded transcript 和目标 `storyboard_plan.v1` 绑定成可复核 artifact，再把逐镜结构注入 `video_prompt_pack.py`。

这套流程只迁移抽象结构。参考片的画面、音频、原话、品牌和具体情节必须明确排除。报告不能证明版权、原创度、留存效果或生成质量。

## 1. 准备绑定请求

先完整看参考片，并准备与视频时间线一致的 transcript：

```bash
python3 scripts/reference_story_formula.py prepare \
  --project-dir . \
  --reference-video origin/reference.mp4 \
  --reference-transcript origin/reference_transcript.json \
  --target-storyboard work/storyboard_plan.json \
  --beat-count 5 \
  --output work/reference_story_formula_request.json \
  --markdown work/reference_story_formula_request.md \
  --response-template work/reference_story_formula_response.json \
  --strict
```

`prepare` 会：

- 绑定三份输入的项目内路径、大小和 SHA-256；参考视频同时绑定 duration、fps、尺寸、codec、像素格式和音轨契约。
- 校验 transcript segment 的唯一 ID、时间顺序、非空文本和媒体边界。
- 校验目标 storyboard 版本、shot ID、顺序与时间范围。
- 把 reference segments 分成 1–12 个连续草稿 beat，并生成逐 beat、逐目标 shot 的 response template。

自动分组只负责给出编辑起点，不负责理解故事。Reviewer 必须基于完整视频、声音和 transcript 重写语义字段。

## 2. 填写并审核公式

每个 `formula_beats[]` 都要填写：

- `mechanism`: `hook / setup / tension / escalation / reveal / proof / relief / payoff / cta / custom`
- `viewer_state_before` 与 `viewer_state_after`
- `trigger`: 什么证据、动作或信息让观众状态发生变化
- `camera_function`: 镜头语言在这个 beat 中承担什么作用
- `transferable_rule`: 可以迁移到新主题的抽象规则
- `do_not_copy`: 参考片里必须排除的具体表达
- `decision=approve` 与具体 `review_note`

可以调整 beat 的 evidence 分组，但全部 reference segment 必须恰好出现一次、保持原顺序，beat 的 start/end 必须等于首尾 evidence segment 的时间范围。

每个 `shot_mappings[]` 都要映射到一个已审 beat，并填写：

- `content_anchor`: 新主题/产品在这个 beat 的具体落点
- `viewer_shift`: 目标观众此镜前后的心态变化
- `surface_change`: 与参考片相比，新片换成了什么主体、场景、事实或产品功能
- `visual_action`: 能在这一镜实际拍摄或生成的动作
- `decision=approve` 与具体 `review_note`

映射顺序只能向前或停留在同一 beat，不能让后面的目标镜头倒退回早先公式阶段。根级 `copy_policy` 五项必须全部为 `true`，明确排除 reference pixels、audio、words、branding 和 specific plot。

## 3. 生成 source-bound 报告

```bash
python3 scripts/reference_story_formula.py audit \
  --project-dir . \
  --request work/reference_story_formula_request.json \
  --response work/reference_story_formula_response.json \
  --output work/reference_story_formula.json \
  --markdown work/reference_story_formula.md \
  --strict

python3 scripts/reference_story_formula.py verify \
  --project-dir . \
  --report work/reference_story_formula.json \
  --strict
```

`audit` 会检查所有 beat、evidence、target shot、copy policy 和 review 决定。脚本还会做一层保守的 normalized longest-span 检查：目标 narration 与参考 transcript 连续重合达到默认 18 字符时进入 warning，要求人工判断它是必要事实/专名还是应该改写。该检查只是分流信号，不是抄袭检测。

`verify` 会现场重读 reference video、transcript、target storyboard、request 和 response，重新 probe 媒体并重建完整报告。源文件替换、分镜修改、response 改动或派生字段/canonical ID 漂移都会让旧 artifact 失效。

## 4. 注入视频生成提示词

```bash
python3 scripts/video_prompt_pack.py \
  --project-dir . \
  --storyboard-plan work/storyboard_plan.json \
  --reference-story-formula work/reference_story_formula.json \
  --provider dreamina_seedance \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md \
  --strict
```

`video_prompt_pack.py` 会先 live-verify 报告，再确认它绑定的是当前 storyboard 的同一路径和字节。每个 shot prompt 得到对应 beat mechanism、viewer-state shift、trigger、transferable rule、target-specific content anchor、surface change、visual action 和禁止复制合同。

公式迁移不会替代：

- `sequence_handoff.py` 的相邻镜头空间/动作接力设计；
- `reference_edit_rhythm.py` 的最终成片 hard-cut 节奏对照；
- `generated_clip_review.py` 与 `generated_sequence_review.py` 的真实像素复核；
- 素材权利、品牌/IP、真人和外部 provider 所需的授权。

## 5. 流水线门禁

```bash
python3 scripts/pipeline_manifest.py . \
  --require reference_story_formula \
  --strict
```

存在报告时，manifest 会调用 live verifier；blocker 会阻止继续，原文长 span warning 会显示为 `warn`。报告中的 reviewer label 只是工作流记录，不是身份认证或数字签名。

## 可直接交给 Agent 的任务描述

```text
请完整观看参考片并读取其 timecoded transcript，用 reference_story_formula.py prepare 建立 source-bound 请求。逐 beat 标注观众状态、触发、镜头作用、可迁移规则和禁止复制内容，再把目标 storyboard 每一镜映射到具体 beat 和新主题 content anchor。确认排除参考片的画面、音频、原话、品牌与具体情节后运行 audit、verify，并把 ready 报告通过 --reference-story-formula 注入 video_prompt_pack.py。最后对真实生成片和成片另做逐片、跨镜头与节奏复核。
```
