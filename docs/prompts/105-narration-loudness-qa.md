# Narration Loudness QA — 最终旁白逐短语响度一致性

整条旁白或最终成片的 integrated LUFS 合格，不代表每一句都一致。多次 TTS、分段录音、局部降噪或逐句后处理可能让某一句突然变大、变小或削顶；整片平均值会把这种局部问题隐藏掉。

`narration_loudness_qa.py` 对**最终处理后的独立旁白音轨**按精确短语时间范围逐段运行 FFmpeg `ebur128=peak=true`，检查：

- 每段 integrated loudness：默认目标 `-18 LUFS ±2 LU`；
- 非例外段之间的最大响度差：默认 `≤1 LU`；
- 每段 true peak：默认 `≤-2 dBTP`；
- 每段 LRA：默认 `≤5 LU`；
- 最短可测片段：默认 `0.5s`。

这组门槛针对**独立最终旁白**，不是最终节目混音。旁白与 BGM/SFX 合成后，仍要用 `audio_master_report.py` 对整条 master 检查默认 `-16 LUFS`、true peak、LRA 和长静音。

## 1. 准备最终旁白与时间清单

不要使用原始 TTS、剪辑前录音或含 BGM 的 master。输入媒体应是已经完成逐句增益、降噪、压缩、淡入淡出等处理后，准备进入最终混音的那条旁白音轨。

时间清单支持 `segments[]` 或 `phrases[]`：

```json
{
  "segments": [
    {
      "id": "line-001",
      "start": 0.0,
      "end": 2.24,
      "text": "先说结论，这不是一个剪辑速度问题。"
    },
    {
      "id": "line-002",
      "start": 2.64,
      "end": 5.18,
      "text": "真正的问题，是每一句处理后的音量不一致。"
    }
  ]
}
```

要求：

- 至少两段，`id` 唯一；
- `start/end` 使用最终旁白文件本身的时间轴；
- 范围不能重叠、越界或短于测量门槛；
- 源媒体和清单必须在项目目录内，不能是 symlink。

## 2. 分析并现场复核

```bash
python3 scripts/narration_loudness_qa.py analyze \
  work/final_narration.wav \
  --segments work/final_narration_segments.json \
  --project-dir . \
  --output verify/narration_loudness_qa.json \
  --markdown verify/narration_loudness_qa.md \
  --strict

python3 scripts/narration_loudness_qa.py verify \
  --report verify/narration_loudness_qa.json \
  --project-dir . \
  --strict
```

报告绑定：

- 最终旁白文件路径、SHA-256、大小和音频媒体契约；
- 时间清单路径、SHA-256、大小与规范化 ranges；
- LUFS / spread / dBTP / LRA / 最短时长参数；
- 当前算法合同、逐段 measurements、派生状态和 canonical report id。

`verify` 会重新读取并逐段实测。旁白字节、清单空白/内容、媒体信息、参数、算法、measurements、summary 或 report id 漂移都会 fail closed。

## 3. 创意例外

刻意耳语、远景声或角色化小声台词可以在对应 segment 记录例外：

```json
{
  "id": "line-003",
  "start": 5.58,
  "end": 7.42,
  "text": "这一句是刻意压低的耳语。",
  "loudness_exception": {
    "reason": "intentional whispered reveal approved after 1x listening",
    "reviewer": "editor-name"
  }
}
```

例外段不参加 target / spread / LRA 阻断计算，但会保留 warning，并且：

- `reason` 和 `reviewer` 都不能为空；
- 至少还要有两段非例外短语，才能判断一致性；
- true peak 上限永远不能被例外绕过；
- reviewer label 只是本地记录，不是身份认证或数字签名。

## 4. 接入发布门禁

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require narration_loudness_qa \
  --strict
```

只要项目里存在 `narration_loudness_qa.json`，manifest 就会现场复测；旧报告不能在旁白或时间清单变更后继续放行。

## 5. 人工复核边界

数字不能检查音色冷硬、发音错误、机械逐字感、呼吸被截断、拼接 click、变速感或语气表演。报告通过后仍必须：

1. 正常速度完整听每一句和所有边界；
2. 戴耳机检查尾音、呼吸和接缝；
3. 用手机/笔记本扬声器确认可懂度；
4. 混入 BGM/SFX 后对最终 master 运行 `audio_master_report.py`；
5. 如是数字人，再从最终 master 运行 `lip_sync_review.py`。
