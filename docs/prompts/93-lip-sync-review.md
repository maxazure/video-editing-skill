# Lip-sync Review 最终成片口型同步证据门禁

数字人、AI talking-head、短段对口型或歌唱 close-up 在 provider 原始 clip 上看过一次还不够。最终剪切、变速、音频替换、拼接、字幕遮挡或重新编码都可能改变交付效果；发布前应从最终 master 本身重新导出 phrase-level proof。

## 适用范围

使用本流程：

- 最终视频中有需要可信对口型的数字人、生成角色或 lip-sync close-up。
- 旁白/歌曲已经锁定，且最终 master 已完成拼接、变速和换音。
- 需要把人工审片结论绑定到具体 master 和 proof bytes。

不使用本流程：纯 B-roll、画外音、嘴部不可见、没有音轨的视频。它也不是自动音素对齐、人脸识别、深伪检测或身份授权工具。

## 1. 选择完整短语

每个 review segment 应是 1–10 秒的完整短语，优先包含可见的 p/b/m 类闭唇锚点和清晰元音。不要从任意固定窗口截半句话；字幕或 overlay 挡住嘴时先处理遮挡，不能用 `not_observable` 冒充通过。

```bash
python3 scripts/lip_sync_review.py prepare \
  --project-dir . \
  --video output/final.mp4 \
  --segment hook=2.40:6.10 \
  --anchor hook="把品牌卖点说清楚" \
  --speaker hook="avatar-a" \
  --segment cta=18.20:22.80 \
  --anchor cta="评论区告诉我们" \
  --speaker cta="avatar-a" \
  --proof-dir verify/lip_sync \
  --output work/lip_sync_review_request.json \
  --markdown work/lip_sync_review_request.md \
  --response-template work/lip_sync_review_response.json
```

默认在短语前后各保留 0.35 秒 context；可用 `--context` 调整到 0–2 秒。每段生成：

- `<segment>_1x.mp4`：正常速度、带最终 master 音频，用于判断实际同步。
- `<segment>_025x_silent.mp4`：四倍慢放、静音，用于观察讲话区间是否冻嘴、跳嘴或不自然停顿。

请求会绑定最终 master 和 proofs 的路径、SHA-256、大小、codec、像素格式、尺寸、fps、时长、音轨契约、source/proof 时间范围与 canonical request id。所有文件必须在项目内；symlink、路径碰撞、无音轨或越界 phrase 会直接拒绝。默认不覆盖旧产物，确认重做同一路径时才加 `--force`。

## 2. 按固定顺序人工审片

对每个 segment：

1. 在 1× 带声 proof 中循环完整短语至少两次。
2. 看 p/b/m 类音是否在听到辅音时出现嘴唇闭合。
3. 看元音嘴形是否持续早于或晚于声音，而不是只抓一帧。
4. 看 0.25× 静音 proof，确认讲话期间没有长时间冻嘴或突跳。
5. 确认可见的目标说话人确实对应听到的声音，并检查音频是否爆音、混入另一人或失真。

填写 `work/lip_sync_review_response.json`：

```json
{
  "version": "lip_sync_review_response.v1",
  "request_id": "保持模板中的值不变",
  "reviewed_by": "reviewer label",
  "reviews": [
    {
      "segment_id": "hook",
      "verdict": "pass",
      "plosive_closures": "aligned",
      "vowel_timing": "aligned",
      "frozen_mouth": "absent",
      "speaker_assignment": "correct",
      "audio_quality": "clean",
      "repair_action": "none",
      "notes": "1x looped twice; 0.25x silent pass."
    }
  ]
}
```

合法修复动作是 `regenerate_from_locked_audio`、`trim_or_retime`、`cut_to_broll` 或 `switch_model`。`fail` 必须写具体 action 和 evidence notes；`pass` 必须五项全过并使用 `repair_action=none`。任何 `not_observable` 都不满足 pass。

## 3. 审计并现场验证

```bash
python3 scripts/lip_sync_review.py audit \
  --request work/lip_sync_review_request.json \
  --response work/lip_sync_review_response.json \
  --output work/lip_sync_review.json \
  --markdown work/lip_sync_review.md \
  --strict

python3 scripts/lip_sync_review.py verify \
  --report work/lip_sync_review.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require lip_sync_review \
  --strict
```

`verify` 会重新读取 master 和所有 proofs，核对 hash、大小、媒体契约、时长/速度/音轨、request id、response 派生状态、summary、blockers 和 report id。最终 master 的任何剪切、换音、变速、重编码，proof 的任何变化，或人工修改派生字段，都会使旧报告失效；重新 `prepare → review → audit`。

## 人工边界

- 1× proof 是实际同步判断的主证据；0.25× 静音 proof 只帮助看嘴部运动，不能单独证明声画同步。
- 脚本不会自动识别人脸、音素、语言、说话人身份或同意授权，也不输出虚假的“同步百分比”。
- `reviewed_by` 是本地标签，不是身份认证或数字签名；SHA-256 只发现文件漂移。
- 嘴部过小、侧脸严重、遮挡、画面模糊或没有可见闭唇锚点时，证据不足，应重选镜头、裁切/B-roll 或重生。
- 通过 lip-sync gate 不代表表演自然、身份授权、平台合规或整体成片审批完成；仍需 generated clip/sequence review、render QA、内容与最终 approval receipt。

## 可直接交给 Agent 的任务描述

```text
请从最终交付候选本身选择 1–10 秒的完整口型短语，优先包含清晰 p/b/m 闭唇锚点。用 lip_sync_review.py prepare 导出每段 1× 带声和 0.25× 静音 proof；逐段检查爆破音闭唇、元音提前/滞后、讲话时冻嘴、可见说话人和音频质量。证据不可观察时不得 pass。填写 response 后运行 audit --strict、verify --strict 和 pipeline_manifest --require lip_sync_review。任何最终成片或音频变化都重新生成 proofs 并复审。
```
