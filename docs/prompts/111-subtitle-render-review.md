# Subtitle Render Review — 最终成片字幕像素复核

字幕 JSON、CPS、字体 cmap 和渲染前样式预览都通过后，最终 MP4 仍可能漏烧字幕、沿用旧文案、裁掉边缘、被贴纸或平台画面元素遮住，或缩小到手机尺寸后无法阅读。`subtitle_render_review.py` 从确切交付候选导出字幕证据，要求人工正常速度看完整片，再生成可现场复验的发布门禁。

脚本只读项目内 `subtitle_pack.v1` 和最终视频。它按首条、末条、最长文字、最高 CPS、最短时长、时间线中段和均匀覆盖选择最多 8 条字幕；也可重复传 `--cue-id` 强制加入已知高风险 cue。每条样本会从最终 MP4 生成一段带上下文的 1× H.264/AAC 片段和一张字幕中点 JPEG。

## 推荐流程

先保证字幕包与最终渲染使用同一剪辑顺序、速度和片头 offset：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --mode concat \
  --speed 1.25 \
  --offset 2.0 \
  --output-dir output/subtitles \
  --basename final
```

从确切交付文件准备证据和人工 response 模板：

```bash
python3 scripts/subtitle_render_review.py prepare \
  --project-dir . \
  --video output/final.mp4 \
  --subtitle-pack output/subtitles/final.json \
  --proof-dir verify/subtitle_render \
  --output work/subtitle_render_review_request.json \
  --markdown work/subtitle_render_review_request.md \
  --response-template work/subtitle_render_review_response.json
```

接着完整播放 `output/final.mp4`，从头到尾保持 1×，检查每条实际显示的字幕。再逐个打开 request 中列出的 context clip 和 midpoint frame，与 `expected text` 核对。填写 response：

- `full_playback=completed` 与 `full_video_verdict=pass` 表示已经看完确切 final 且全片字幕无已知问题。
- 每条样本填写 `caption_presence`、`text_match`、`readability`、`layout` 和 `repair_action`。
- 通过项必须是 `visible / matches / readable / clear / none`。
- `missing`、`mismatch`、`unreadable`、`clipped`、`obscured` 或 `not_observable` 都要选择具体修复动作并重新渲染。

审计与现场复验：

```bash
python3 scripts/subtitle_render_review.py audit \
  --request work/subtitle_render_review_request.json \
  --response work/subtitle_render_review_response.json \
  --output work/subtitle_render_review.json \
  --markdown work/subtitle_render_review.md \
  --strict

python3 scripts/subtitle_render_review.py verify \
  --report work/subtitle_render_review.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require subtitle_render_review \
  --strict
```

## 证据与失效条件

request/report 会绑定最终视频、字幕包、全部 proof 文件的相对路径、SHA-256、大小、媒体契约、cue 文字与时间、采样设置和 canonical id。以下变化会让旧报告失效：

- 最终 MP4 被重编码、替换、裁切、变速或换音；
- 字幕文字、顺序、时间或 cue id 改变；
- proof clip / JPEG 被替换、缺失、改尺寸或与输入发生 symlink/hardlink 碰撞；
- response、派生 verdict、统计或 report id 被改写；
- 换到不同项目目录复用报告。

修复后重新运行完整 `prepare → 完整 1× 播放 → audit → verify`，不要复用旧 response。

## 边界

- 本工具不做 OCR、forced alignment、字体 shaping 或自动可读性评分。它记录人工看到的最终像素及其确切来源。
- 最多 8 条的默认样本用于覆盖高风险位置，不能证明未抽中的 cue。全片正常速度播放是通过条件。
- 中点帧可能落在淡入淡出或逐字动画中；可用 `--cue-id` 加样本，并以 context clip 和完整成片为准。无法观察时 fail closed。
- `reviewed_by` 是自填标签，不提供身份认证、数字签名或审批权限证明。最终发布审批仍使用 `approval_receipt.py`。
- 字幕包的 `--speed` 与 `--offset` 必须和最终渲染完全一致；时间线合同错误时先重建字幕包与 proofs。
