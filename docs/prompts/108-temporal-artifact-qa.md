# Temporal Artifact QA — 单帧 / 少数帧瞬态伪影门禁

用于补齐 `render_qa.py` 与 `flash_safety_qa.py` 之间的空白：

- `render_qa.py` 擅长长黑场、长冻结、长静音和媒体规格；
- `flash_safety_qa.py` 擅长一段时间内反复出现的大面积亮度 / 饱和红闪烁；
- `temporal_artifact_qa.py` 专门筛查 **1–3 个采样帧突然偏离、随后又回到相近画面** 的瞬态异常，例如误插闪帧、单帧撕裂、少数帧生成变形或错误 overlay。

它只用本地 FFmpeg 与 Python 标准库，不上传素材、不调用视觉模型，也不会自动判断画面语义。

## 1. 自动筛查

```bash
python3 scripts/temporal_artifact_qa.py analyze output/final.mp4 \
  --project-dir . \
  --evidence-dir verify/temporal_artifact_frames \
  --output verify/temporal_artifact_qa.json \
  --markdown verify/temporal_artifact_qa.md \
  --response-template work/temporal_artifact_response.json \
  --strict
```

默认流程：

1. 最高按 30 fps、160 px 宽缩小为 8-bit 灰度帧；
2. 计算相邻帧 MSE；
3. 对 1–3 帧的候选窗口，要求进入和退出变化同时高于 `max(150, 局部中位数 × 2.5)`；
4. 要求候选前一帧与后一帧重新接近，避免把普通 hard cut 当成瞬态伪影；
5. 每个候选导出一张从左到右为 `before / suspect / after` 的 JPEG。

检测到候选时，`analyze --strict` 返回 2 是预期人工门禁，不代表算法已经证明存在坏帧。没有候选时报告可直接 `verify`，但仍不能跳过最终完整审片。

可按已明确写入项目验收标准的情况调整 `--analysis-fps`、`--analysis-width`、`--local-radius`、`--max-artifact-frames`、`--spike-ratio`、`--min-transition-mse` 或 `--recovery-ratio`。不要为了清除已出现的候选，在看过结果后临时放宽阈值。

## 2. 人工逐帧裁定

先完整以 1× 播放整条视频，再逐张打开证据 JPEG。填写 `work/temporal_artifact_response.json`：

```json
{
  "version": "temporal_artifact_qa_response.v1",
  "scan_id": "<保持模板原值>",
  "reviewed_by": "editor-label",
  "full_video_played_at_1x": true,
  "reviews": [
    {
      "candidate_id": "temporal_artifact_0001",
      "decision": "artifact",
      "frame_observations": {
        "before": "人物面向镜头，背景稳定",
        "suspect": "中间帧出现横向撕裂和错误人脸",
        "after": "下一帧恢复到原姿态"
      },
      "reason": "不是时间线中的计划转场，正常速度播放可见闪跳",
      "repair_action": "替换该生成片段并从锁定时间线重新渲染"
    }
  ]
}
```

允许的决定：

| 决定 | 含义 | gate |
|---|---|---|
| `intentional_edit` | 时间线中确有设计过的闪帧 / glitch / 快速插帧，且完整播放视觉干净 | WARN |
| `artifact` | 确认是误插帧、撕裂、生成变形或其他非预期异常 | BLOCK，必须写修复动作 |
| `uncertain` | 三帧证据或完整播放仍无法排除问题 | BLOCK，必须修复或升级复核 |

三张帧描述、决定理由、reviewer label 和完整 1× 播放声明都必须填写。reviewer label 只是本地记录，不是身份认证或数字签名。

## 3. 审计与现场验证

```bash
python3 scripts/temporal_artifact_qa.py audit \
  --report verify/temporal_artifact_qa.json \
  --response work/temporal_artifact_response.json \
  --output verify/temporal_artifact_qa.json \
  --markdown verify/temporal_artifact_qa.md \
  --project-dir . \
  --force \
  --strict

python3 scripts/temporal_artifact_qa.py verify \
  --report verify/temporal_artifact_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require temporal_artifact_qa \
  --strict
```

报告绑定 source SHA-256 / 字节数 / 媒体契约、检测参数、算法合同、完整分析、候选 JPEG、人工 response、summary 和 canonical ids。源视频、证据、参数、分析、response 或派生状态变化后都会 fail closed。

## 4. 边界

- 普通 hard cut 只有一次大变化，不应满足“进入和退出都高 + 前后恢复相似”的候选合同。
- whip pan、快速动作、计划闪帧、glitch art 和短 overlay 仍可能触发；必须结合分镜 / 时间线意图判断。
- 持续身份漂移、逐渐变形、局部肢体错误、摩尔纹、压缩噪声或低于采样分辨率的小区域问题可能漏检。
- 它不是人脸、身份、物理或语义检查。生成片仍需 `generated_clip_review.py`，最终片仍需完整 1× 审片。
- 修复后必须重新渲染，并重跑本报告、下游 approval receipt 与 publish package。
