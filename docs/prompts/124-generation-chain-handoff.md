# 124 — Generation Chain Handoff 生成片末帧接力

用于两个生成镜头属于同一场景或连续动作时，把上一条已审成片的真实末帧绑定为下一条的精确首帧。该流程在本地提取 PNG、验证像素与上游报告、要求人工确认，再把接力 artifact 注入 `video_prompt_pack.py`。它不会上传素材、调用生成供应商或消耗 credits。

不要对换场、时间跳跃、故意跳切或只需要普通剪辑匹配的边界强行接力。角色、产品和整体风格仍以原始 reference 为准；上一镜末帧负责承接姿态、道具状态、环境、光线和空间关系。

## 前置条件

- `sequence_handoff.py` 已把该相邻边界审为 `approve`；
- 上一条生成片已完成 `generated_clip_review.py audit`，结果为 `pass` 或 `pass_with_edits`；
- `runtime_preflight.py --profile generation_chain_handoff --strict` 已通过；
- 两条镜头确实属于同一场景或一个有意连续的动作 beat；
- 外部上传、真人、品牌/IP 和付费生成范围已按需完成 `production_authorization.py`。

## 1. 提取已批准范围的真实末帧

```bash
python3 scripts/generation_chain_handoff.py prepare \
  --project-dir . \
  --clip-review work/generated_clip_review.json \
  --sequence-handoff work/sequence_handoff.json \
  --boundary boundary_001 \
  --frame-output work/generation_chain/boundary_001_tail.png \
  --output work/generation_chain_handoff.plan.json \
  --markdown work/generation_chain_handoff.plan.md
```

脚本会：

- 现场验证 `generated_clip_review.v1` 和 `sequence_handoff.v1`；
- 对 `pass` 使用完整 clip，对 `pass_with_edits` 使用时间上最后一个 approved keep range；
- 用 FFprobe 枚举解码帧并选择批准范围内最后一帧；
- 用 FFmpeg 按确切 decoded frame index 导出 PNG；
- 对源帧和 PNG 解码为 RGB24，要求像素 SHA-256 与字节数一致；
- 绑定两份上游报告、原 clip、批准区间、帧 index/PTS、PNG 和提示词合同。

`prepare` 后保持 blocked，人工确认前不要提交下一条生成任务。

## 2. 看图并确认

查看上一条 clip 和 `boundary_001_tail.png`，确认：

1. 两条镜头是同一场景或有意连续动作；
2. PNG 的人物姿态、道具状态、环境、光线和构图适合作为下一镜第一帧；
3. 原始角色、产品和 style reference 会继续保留；
4. 下一条短片只安排一个主动作和一种机位行为。

确认后运行：

```bash
python3 scripts/generation_chain_handoff.py confirm \
  --project-dir . \
  --plan work/generation_chain_handoff.plan.json \
  --output work/generation_chain_handoff.json \
  --decision use_exact_start_frame \
  --reviewed-by "<reviewer-label>" \
  --same-scene-confirmed \
  --tail-frame-accepted \
  --original-anchors-preserved \
  --notes "同一场景连续动作；保留原角色、产品和风格锚点。" \
  --markdown work/generation_chain_handoff.md \
  --strict
```

若尾帧不适合接力，使用 `--decision reject`，在 notes 中说明换场、动作终态或构图问题。被拒绝的 artifact 会继续阻塞，下一步应回到分镜/生成或采用普通剪辑边界。

## 3. 现场验证并重建下一镜提示词包

```bash
python3 scripts/generation_chain_handoff.py verify \
  --project-dir . \
  --plan work/generation_chain_handoff.json \
  --strict

python3 scripts/video_prompt_pack.py \
  --project-dir . \
  --storyboard-plan work/storyboard_plan.json \
  --provider dreamina_seedance \
  --sequence-handoff work/sequence_handoff.json \
  --generation-chain-handoff work/generation_chain_handoff.json \
  --character "<approved character identity>" \
  --style-reference work/imagegen/style-key.png \
  --approved \
  --output work/video_prompt_pack.chained.json \
  --markdown work/video_prompt_pack.chained.md \
  --strict
```

接力目标镜头会自动切换为 `image_to_video`，`reference` 指向审核后的 PNG，并在 prompt 中写入 exact-chain 起始状态、原始身份/产品/风格锚点策略、receive-in/match rule，以及“一项主动作 + 一种机位行为”的时长预算。一次可重复传入多个 `--generation-chain-handoff`，每个目标镜头最多一个，且必须对应当前 storyboard 的相邻边界。

如果 provider surface 不支持 exact first-frame、reference 数量或 frame/style 组合，继续由 `provider_capability.py` 与 `generation_reference_preflight.py` 阻塞，不要把模式强行改成语义 reference 来绕过。

## 4. 流水线门禁

```bash
python3 scripts/pipeline_manifest.py . \
  --require generation_chain_handoff \
  --strict
```

以下任一变化都会让 live verification 失效：

- 上游 clip review 或 sequence handoff 文件/报告 ID 改变；
- 原生成片字节、媒体合同或 approved keep range 改变；
- 末帧不再是批准范围内最后一个 decoded frame；
- PNG 文件或解码像素改变；
- same-scene、tail suitability 或 original-anchor 确认缺失；
- artifact 内容或 canonical ID 被改写。

确认标签只用于工作流记录，不是身份认证或数字签名。下一条生成片下载后仍需重新执行 `generated_clip_review.py`；全部片段完成后再运行 `generated_sequence_review.py` 检查真实相邻边界。
