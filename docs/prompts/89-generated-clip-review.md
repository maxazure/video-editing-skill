# Generated Clip Review 生成视频片段复核

适用于 Dreamina/即梦、Seedance、Veo、Sora、LTX、Wan 等生成视频已经下载到本地，但还没有进入最终时间线的阶段。生成片段是原料，不是成片；通用 `render_qa.py` 能发现黑屏、静帧和音轨问题，却不能判断人物是否倒着走、门是否穿过身体、道具是否消失、动作是否重复或故事是否读得懂。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。生成视频仍按项目现有 provider 决策、额度审批和任务台账执行。

## 1. 生成 source-bound 复核请求

如果 `storyboard_assets.json` 已经把生成视频标为 ready：

```bash
python3 scripts/generated_clip_review.py prepare \
  --project-dir . \
  --asset-manifest work/storyboard_assets.json \
  --contact-sheet-dir verify/generated_clips \
  --output work/generated_clip_review_request.json \
  --markdown work/generated_clip_review_request.md \
  --response-template work/generated_clip_review_response.json
```

也可直接指定一条或多条片段：

```bash
python3 scripts/generated_clip_review.py prepare \
  --project-dir . \
  --clip shot_001=work/generated_video/shot_001.mp4 \
  --clip shot_002=work/generated_video/shot_002.mp4 \
  --contact-sheet-dir verify/generated_clips \
  --output work/generated_clip_review_request.json \
  --response-template work/generated_clip_review_response.json
```

请求会绑定每条 clip 的项目内相对路径、SHA-256、大小、时长、尺寸、fps、codec、pixel format、音轨契约，以及 contact sheet 的 SHA-256。默认每秒取 2 帧、每条最多 48 帧；长片会自动降低采样率，不会无限扩张图片。路径必须在项目内，拒绝 symlink；已有输出默认不覆盖，确需重建时显式加 `--force`。

## 2. 完整审片并填写 response

每条 clip 必须看四遍：

1. 1×、带声音看完整片段，判断整体故事和节奏。
2. 0.25× 看脸、手、肢体、接触、重力、机械运动和物体持续性。
3. 静音看完整画面，避免合理音效掩盖视觉错误。
4. 不依赖画面只听声音，检查音画矛盾、口型和环境声变化。

`scores` 六项固定为 1–5 的整数：

| 字段 | 权重 | 检查内容 |
|---|---:|---|
| `identity_wardrobe` | 25 | 脸、体型、发型、服装和关键锚点是否从头到尾一致 |
| `action_end_state` | 20 | 指定动作是否只发生一次，结尾是否落到预期状态 |
| `motion_anatomy_physics` | 20 | 肢体、手指、重量、接触、重力、速度和机械净空是否合理 |
| `camera_behavior` | 10 | 是否只有计划中的运镜，没有意外漂移、缩放或反向 |
| `frame_integrity` | 15 | 是否多出人物/物体、道具消失、背景 morph、生成文字或水印 |
| `look_consistency` | 10 | 光向、曝光、色温、颗粒和整体 look 是否稳定 |

必须单独记录 hard fail，不能用高分抵消：

- `identity_break`
- `missing_or_wrong_action`
- `anatomy_or_physics_failure`
- `extra_subject_or_object`
- `prop_disappearance_or_drift`
- `rendered_text_or_watermark`
- `continuity_contradiction`
- `audio_picture_mismatch`
- `explicit_must_avoid_violation`

判定规则：

- `pass`：加权分至少 80、故事清晰、无 hard fail，不需要删除范围。
- `pass_with_edits`：加权分至少 65、故事不能 unclear、无 hard fail；`keep_ranges` 与 `remove_ranges` 都必须填写，并无缝覆盖 `0..duration`，这样裁切决定才可执行。
- `fail`：`regenerate=true`，填写具体 `prompt_fix`。任何 hard fail、故事 unclear 或加权分低于 65 都必须走这里。

示例（省略其他 clip）：

```json
{
  "version": "generated_clip_review_response.v1",
  "request_id": "<复制 request_id>",
  "reviewed_by": "visual-review-agent",
  "reviews": [
    {
      "clip_id": "shot_001",
      "verdict": "pass_with_edits",
      "story_readability": "partial",
      "scores": {
        "identity_wardrobe": 4,
        "action_end_state": 4,
        "motion_anatomy_physics": 4,
        "camera_behavior": 4,
        "frame_integrity": 4,
        "look_consistency": 4
      },
      "hard_fail_codes": [],
      "keep_ranges": [
        {"start": 0.0, "end": 3.2, "reason": "setup and reveal remain coherent"}
      ],
      "remove_ranges": [
        {"start": 3.2, "end": 4.0, "reason": "unmotivated repeated tail action"}
      ],
      "regenerate": false,
      "prompt_fix": "",
      "notes": "Full-speed, slow, muted and audio-only passes completed."
    }
  ]
}
```

## 3. 审计与发布门禁

```bash
python3 scripts/generated_clip_review.py audit \
  --request work/generated_clip_review_request.json \
  --response work/generated_clip_review_response.json \
  --output work/generated_clip_review.json \
  --markdown work/generated_clip_review.md \
  --strict

python3 scripts/generated_clip_review.py verify \
  --report work/generated_clip_review.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require generated_clip_review \
  --strict
```

`audit` 会要求 response 精确覆盖 request 中全部 clip，重新检查源文件/contact sheet，计算加权分，校验 verdict、hard fail、裁切区间和重生建议，并输出 `generated_clip_review.v1`。`verify` 会重新执行同一套 live audit；换片、改片、改 contact sheet、篡改 summary/review/report id 都会让旧报告失效。

## 边界

- contact sheet 只是抽样，可能漏掉单帧或声音问题，不能代替完整播放。
- 本脚本不调用视觉模型，也不自动判断美感、常识或物理；review 内容由真正看过视频的人工或视觉 reviewer 提供。
- `reviewed_by` 只是本地标签，不是身份认证、数字签名或防抵赖证明。
- `pass_with_edits` 只批准列出的 `keep_ranges`；组装时不得悄悄恢复已移除区间。
- `fail` 代表回到生成阶段。不要靠极端快切、装饰性转场或音效掩盖破损素材。
