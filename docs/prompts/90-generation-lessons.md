# Generation Lessons — 生成视频复核经验闭环

适用场景：已经用 `generated_clip_review.py` 完成生成片段复核，希望把一条可泛化的 cause → effect 经验带回下一次 Dreamina/Seedance、Veo、LTX、Wan 或 Sora 提示词，而不是让经验只留在聊天记录里。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 1. 只从 canonical review 提取

先完成生成片段复核：

```bash
python3 scripts/generated_clip_review.py audit \
  --request work/generated_clip_review_request.json \
  --response work/generated_clip_review_response.json \
  --output work/generated_clip_review.json \
  --markdown work/generated_clip_review.md \
  --strict
```

失败片段会让 `audit --strict` 返回 2，这是正常的重生 gate。`generation_lessons.py add` 会允许“该 clip 需要重生”这一预期 blocker，但仍会实时重算 request/response、源 clip、contact sheet、summary 和 report id；任何漂移或非法 review 都不能进入经验库。

## 2. 人工批准一条可泛化经验

```bash
python3 scripts/generation_lessons.py add \
  --library work/generation_lessons.json \
  --review work/generated_clip_review.json \
  --clip-id shot_002 \
  --category hand_contact \
  --model seedance-2.0 \
  --lesson "For hand-to-prop contact, isolate one interaction and keep the hand visible through release." \
  --approved-by "<reviewer-label>" \
  --markdown work/generation_lessons.md
```

经验必须是可直接用于未来提示词的通用规则，不是“shot_002 的手坏了”这类片段描述。默认 provider 从 review 的 `provider_route` 读取；review 没有 provider 时显式传 `--provider`。`--model '*'` 表示 provider-wide；只有真正跨 provider 稳定成立的规则才使用 `--global`。

每条 entry 记录：

- provider / model / category scope；
- 人工给出的 `lesson`，以及原 review 的 `prompt_fix` 和 evidence；
- report id、request id、clip/contact-sheet SHA-256、verdict、score 和 hard-fail codes；
- `approved_by` 审计标签与 canonical `lesson_id`。

`approved_by` 不是身份认证或数字签名；SHA-256 用于发现漂移，不阻止有写权限的人改写内容并重算摘要。

新证据推翻旧经验时，不要删除历史。追加一条新 lesson，并显式标记替代关系：

```bash
python3 scripts/generation_lessons.py add \
  --library work/generation_lessons.json \
  --review work/generated_clip_review.json \
  --clip-id shot_009 \
  --category hand_contact \
  --lesson "For this provider, cut before contact and reveal the stable completed grip in the next shot." \
  --approved-by "<reviewer-label>" \
  --supersedes "<old-lesson-id>"
```

旧 entry 和 evidence 会保留；只有新 entry 本身匹配当前选择 scope 时，`select` 才排除它列出的旧 lesson id。未知 id、自引用、重复 id 都会让 library 验证失败。

## 3. 验证与筛选

```bash
python3 scripts/generation_lessons.py verify \
  --library work/generation_lessons.json \
  --strict

python3 scripts/generation_lessons.py select \
  --library work/generation_lessons.json \
  --provider dreamina_seedance \
  --model seedance-2.0 \
  --category hand_contact \
  --limit 3 \
  --output work/selected_generation_lessons.json \
  --markdown work/selected_generation_lessons.md \
  --require-match
```

未传 `--model` 时只选择 provider-wide (`model=*`) 经验，避免把模型专属行为误套到别的版本。每次最多选择 10 条；推荐保持 1–3 条，防止提示词被历史规则淹没。

## 4. 注入下一次 prompt pack

```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --provider dreamina_seedance \
  --lesson-library work/generation_lessons.json \
  --lesson-model seedance-2.0 \
  --lesson-category hand_contact \
  --lesson-limit 3 \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md
```

`video_prompt_pack.py` 会把匹配的人工批准 `lesson` 追加为 `LEARNED CONSTRAINTS`，同时把 lesson id、scope 和 source evidence 写入每个生成 shot。原 review 的 `prompt_fix` 会保留在 JSON 供复核，但不会自动拼进提示词，因为它可能只适用于旧片段。

脚本不会调用 provider、不会自动重生，也不会消费 credits。付费 Dreamina/即梦或其他生成仍要走原有 approval gate，并保持小批量。`pipeline_manifest.py --require generation_lessons --strict` 可把经验库完整性设为项目门禁。
