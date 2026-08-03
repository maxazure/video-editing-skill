# Semantic Transcript Review 全篇上下文语义校稿

当 Whisper 的逐段文字“看起来都像中文”，但专业术语、人名、同音字或中英混说仍可能错时，用 `semantic_transcript_review.py` 在人工同步媒体校稿前增加一层全篇上下文审校。

这个脚本不调用 LLM、不上传 transcript，也不把模型置信度当批准。它把流程拆成：

1. `prepare`：从当前 transcript 生成带前后文的 provider-neutral review packet。
2. `audit`：验证外部模型/人工返回的覆盖率和最小字符补丁，但不改 transcript。
3. `apply`：只有独立 choices 文件逐项 `approve` / `reject` 后才写 reviewed transcript。

## 1. 生成上下文审校包

```bash
python3 scripts/semantic_transcript_review.py prepare \
  --transcript work/transcript.json \
  --output work/semantic_review_request.json \
  --markdown work/semantic_review_request.md \
  --context-radius 2
```

把 `semantic_review_request.json` 交给当前 Agent/模型，只让它填写 `response_template`：必须列出全部 `reviewed_segment_ids`，每个 correction 使用 Python 的零基字符范围，并只包含最小错误片段。

示例 response：

```json
{
  "version": "semantic_transcript_review.v1",
  "source_sha256": "<copy from request>",
  "reviewed_segment_ids": ["1", "2", "3"],
  "proposals": [
    {
      "segment_id": "1",
      "span_start": 3,
      "span_end": 4,
      "source": "检",
      "replacement": "剪",
      "category": "homophone",
      "confidence": 0.98,
      "recommendation": "accept",
      "reason": "前后文讨论剪映和视频剪辑。"
    }
  ]
}
```

## 2. 审计模型建议

```bash
python3 scripts/semantic_transcript_review.py audit \
  --transcript work/transcript.json \
  --response work/semantic_review_response.json \
  --output work/transcript_semantic_review.json \
  --markdown work/transcript_semantic_review.md \
  --strict
```

有合法 correction 时，`--strict` 此时返回 2 是预期行为：每条建议仍缺人工 choice。打开 Markdown，先检查原词、替换词、segment、字符范围、类别、理由和置信度；底部会给出绑定 `source_sha256 + review_id` 的 choices 模板。

`audit` 会拒绝：

- 未覆盖全部 segment、未知/重复 segment id；
- transcript SHA-256 已变化；
- source 与 `text[span_start:span_end]` 不一致；
- 整句润色、带未变化前后缀的非最小补丁；
- 数字或标点/符号变化；
- 超长、越界、重复或重叠补丁；
- 跨 segment 边界制造重复字；
- 非法 category、confidence 或 recommendation。

## 3. 人工 choices 后应用

从 Markdown 复制 choices 模板，把每个 proposal id 改成明确选择：

```json
{
  "version": "semantic_transcript_review.v1",
  "source_sha256": "<copy from audit>",
  "review_id": "review-...",
  "reviewer": "Jay",
  "choices": {
    "patch-123456789abc": "approve",
    "patch-abcdef123456": "reject"
  }
}
```

然后运行：

```bash
python3 scripts/semantic_transcript_review.py apply \
  --transcript work/transcript.json \
  --audit work/transcript_semantic_review.json \
  --choices work/semantic_review_choices.json \
  --output work/transcript_semantic_reviewed.json \
  --markdown work/transcript_semantic_review.md
```

成功后会把同一份 audit 原子更新为 `artifact_type=result`、`summary.blocking=0`，并在输出 transcript 写入 `semantic_review` 审计信息。改过的 segment 默认重新分配词级时间戳；只有确定原 words 仍准确时才使用 `--keep-words`。

`reviewer` 只是本地标签，不是身份认证、数字签名或发布授权。语义 choices 也不证明音频真的说了该词；下一步仍应运行同步媒体校稿：

```bash
python3 scripts/transcript_review.py html \
  --transcript work/transcript_semantic_reviewed.json \
  --video origin/talking.mp4 \
  --output work/transcript_review.html
```

## Pipeline gate

`pipeline_manifest.py` 会自动发现 `transcript_semantic_review.json`。只要 coverage、确定性验证或人工 choices 未清零，`summary.blocking > 0` 就阻塞；成功 `apply` 后变为 ready。也可以显式要求：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage render_ready \
  --require semantic_transcript_review \
  --strict
```
