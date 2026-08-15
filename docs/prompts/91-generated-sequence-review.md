# Generated Sequence Review — 生成视频跨镜头连续性复核

适用于两条或以上生成视频已经逐片通过 `generated_clip_review.py`，但还没有进入最终组装的阶段。它检查的是**相邻片段之间**的连续性，不重复逐片常识、物理和音画质检。

## 解决什么问题

逐片都能用，不代表连起来能用。人物脸型或服装、手中道具、站位与屏幕方向、动作终态、机位尺度、光线和色调，都可能在镜头边界突然漂移。`generated_sequence_review.py` 从已批准的真实 clip bytes 提取每个边界的：

- 上一条片段最后一个安全可解码帧；
- 下一条片段第一个批准帧；
- 尾帧/首帧并排 JPEG；
- 上一条尾部 + 下一条头部的无声 1× MP4 预览；
- storyboard 的 expected outgoing/incoming state 与 continuity anchors。

脚本只生成和绑定证据，不宣称自己能判断视觉连续性，也不调用生成 provider 或消耗 credits。

## 1. 准备边界证据

先完成逐片复核，并保证 `generated_clip_review.py verify --strict` 通过：

```bash
python3 scripts/generated_sequence_review.py prepare \
  --project-dir . \
  --clip-review work/generated_clip_review.json \
  --storyboard-plan work/storyboard_plan.json \
  --evidence-dir verify/generated_sequence \
  --output work/generated_sequence_review_request.json \
  --markdown work/generated_sequence_review_request.md \
  --response-template work/generated_sequence_review_response.json
```

默认每侧取 1 秒；可用 `--preview-seconds 0.25..3.0` 调整。若逐片结论是 `pass_with_edits`，边界会使用首个和最后一个批准 `keep_range`，不会把已拒绝区间重新带回。

提供 storyboard 时，所有 reviewed generated shots 必须都能在 `shots[]` 中找到，镜头顺序以 storyboard 为准。没有 storyboard 时使用 clip review 中的顺序，但不会凭空补 continuity anchors。

## 2. 填写 response

先以 1× 播放每个 `*_preview.mp4`，再全尺寸查看 `*_comparison.jpg`。每个边界填写六项检查：

| 检查 | 关注点 |
|---|---|
| `identity_wardrobe` | 人物身份、脸、发型、服装与随身物 |
| `prop_state` | 道具形态、颜色、磨损、归属和状态变化 |
| `spatial_orientation` | 站位、左右关系、视线、运动方向和场景几何 |
| `action_end_state` | 上一镜动作终态是否能因果地接到下一镜起态 |
| `camera_framing` | 机位高度、朝向、焦段感、主体尺度与构图 |
| `lighting_palette` | 主光方向、时间感、曝光、色温和整体 palette |

每项只允许：

- `match`：连续；
- `intentional_change`：storyboard 明确设计的变化；
- `mismatch`：非预期漂移；
- `not_applicable`：确实不适用。

至少两项必须实际评估。示例：

```json
{
  "version": "generated_sequence_review_response.v1",
  "request_id": "<copy-from-request>",
  "reviewed_by": "<reviewer-label>",
  "reviews": [
    {
      "boundary_id": "shot_001__shot_002",
      "verdict": "fail",
      "checks": {
        "identity_wardrobe": "match",
        "prop_state": "mismatch",
        "spatial_orientation": "match",
        "action_end_state": "mismatch",
        "camera_framing": "intentional_change",
        "lighting_palette": "match"
      },
      "failure_codes": ["prop_state_drift", "action_state_mismatch"],
      "observed_transition": "上一镜红色马克杯在右手，下一镜变成透明玻璃且出现在左手。",
      "repair_action": "用已接受尾帧重新生成 shot_002，锁定红杯和右手持杯终态。",
      "notes": "景别变化是设计内切换；道具和动作交接不是。"
    }
  ]
}
```

`mismatch` 必须选择 `fail`、填写至少一个 failure code 和可执行 `repair_action`。`pass` 可以包含有依据的 `intentional_change`，但总门禁会保留 warning，提醒组装后完整观看。

## 3. 审计与现场验证

```bash
python3 scripts/generated_sequence_review.py audit \
  --request work/generated_sequence_review_request.json \
  --response work/generated_sequence_review_response.json \
  --output work/generated_sequence_review.json \
  --markdown work/generated_sequence_review.md \
  --strict

python3 scripts/generated_sequence_review.py verify \
  --report work/generated_sequence_review.json \
  --strict
```

`verify` 会重新检查：

- 上游 `generated_clip_review.json` 的 bytes、report id 与 live canonical audit；
- 原始 clip 的 SHA-256、大小与批准 ranges；
- storyboard bytes；
- 每张尾帧、首帧、并排图和边界预览的 SHA-256/大小；
- clip order、完整相邻 boundary coverage、source times、response coverage、派生 summary 和 report id。

任一源片、上游 review、storyboard 或证据变化都会 fail closed。重新逐片复核或修复片段后，应重新 `prepare → audit`，不能手改旧 request/report 的 hash。

发布前可加入总门禁：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require generated_clip_review \
  --require generated_sequence_review \
  --strict
```

## 边界与限制

- 预览是无声的，只负责视觉交接；音频转场仍需在最终 master 以 1×、耳机和手机扬声器复核。
- 并排图只展示边界帧，不能替代完整片段和最终组装播放。
- reviewer label 和 SHA-256 都不是身份认证、数字签名或防篡改安全系统。
- 有意换场、换装、时间跳跃可以标为 `intentional_change`；必须能由 storyboard/context 解释，不能用它掩盖模型漂移。
- 失败边界需要重生、换镜、插入合适 cutaway 或重新设计动作；本脚本不自动修改时间线。
