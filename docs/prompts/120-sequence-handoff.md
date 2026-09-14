# 120 - Sequence Handoff 生成前镜头接力与剪辑边界

多镜头生成前，先把每个相邻镜头的接力关系写成可审计合同。`sequence_handoff.py` 读取 `storyboard_plan.v1`，逐边界给出建议，并要求人工确认：

- 上一镜交出的可见或可听载体，以及下一镜如何接住
- `action / eyeline / screen direction / composition / prop / space / motion / light / color / sound / occlusion` 等载体类型
- hard cut、动作匹配、视线匹配、运动/构图匹配、cutaway、insert、reaction、遮挡切、J-cut/L-cut 或刻意跳切
- 180° 轴、运动方向、音频桥、头尾剪辑余量、风险和 fallback

脚本只写本地 JSON/Markdown，不生成素材、不提交 provider、不消耗 credits。自动建议来自 storyboard 文本，无法看见未来生成结果；必须由人逐边界复核。

## 1. 准备审核包

```bash
python3 scripts/sequence_handoff.py prepare \
  --project-dir . \
  --storyboard work/storyboard_plan.json \
  --output work/sequence_handoff_request.json \
  --markdown work/sequence_handoff_request.md \
  --response-template work/sequence_handoff_response.json \
  --strict
```

`prepare` 绑定 storyboard 的路径、大小和 SHA-256。每个 `boundary_###` 都对应确切的 `from_shot → to_shot`；源分镜变化后，旧审核会失效。

## 2. 填写逐边界决定

打开 `work/sequence_handoff_response.json`，填写 `reviewed_by`，并复核每条预填建议。每条必须填写：

- `decision`: `approve` 或 `revise`
- `carrier_type`, `offer_from`, `receive_in`
- `edit_type`, `match_requirement`, `audio_bridge`
- `axis_decision`: `maintain` / `reset` / `not_applicable`，并写 `axis_note`
- `screen_direction_decision`: `maintain` / `reverse_with_reset` / `not_applicable`，并写说明
- `head_handle_seconds`, `tail_handle_seconds`
- `risk`, `fallback_cut`, `review_note`

需要翻转轴线或屏幕运动方向时，必须声明能重建地理关系的 hard cut、cutaway、insert、reaction、occlusion cut 或刻意跳切。刻意破坏连续性时，`carrier_type=deliberate_rupture` 必须和 `edit_type=deliberate_jump_cut` 成对使用。

## 3. 审核并现场验证

```bash
python3 scripts/sequence_handoff.py audit \
  --project-dir . \
  --request work/sequence_handoff_request.json \
  --response work/sequence_handoff_response.json \
  --output work/sequence_handoff.json \
  --markdown work/sequence_handoff.md \
  --strict

python3 scripts/sequence_handoff.py verify \
  --project-dir . \
  --report work/sequence_handoff.json \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require sequence_handoff \
  --strict
```

缺决定、空说明、`revise`、非法枚举、未声明的轴线/方向翻转和源文件漂移都会阻断。零长度 head/tail handle 会保留 warning，因为部分镜头确实无法额外生成余量，但剪辑弹性会降低。

## 4. 写入最终生成提示词

```bash
python3 scripts/video_prompt_pack.py \
  --project-dir . \
  --storyboard-plan work/storyboard_plan.json \
  --sequence-handoff work/sequence_handoff.json \
  --provider dreamina_seedance \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md \
  --strict
```

`video_prompt_pack.py` 会先 live-verify 报告，再把每镜的 `RECEIVE IN`、`HANDOFF OUT`、edit type、匹配要求、轴线/方向、音频桥和剪辑余量写入 provider prompt。首镜只有 handoff-out，末镜只有 receive-in，中间镜头同时包含两者。

生成完成后仍要执行 `generated_clip_review.py` 和 `generated_sequence_review.py`，并在组装成片中正常速度复核全部边界。文本合同只约束生成意图，不能证明最终像素、动作、声音或剪辑连续性。
