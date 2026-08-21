# Production Authorization 生产授权合同

适用于任何会把项目素材交给外部服务、改变原始叙事意图、消耗生成额度、克隆真人声音或直接发布的流程。它把“用户已经同意”从聊天里的模糊印象变成与确切素材字节、provider/surface、用途和权利对象绑定的本地 `prepare → audit → verify` gate。

它不上传、不剪辑、不生成、不消费 credits、不发布。reviewer label、basis 和 evidence note 都是自报记录，不是身份认证、数字签名、监护关系证明或法律意见。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 1. 写 scope

先创建 `work/production_authorization_scope.json`：

```json
{
  "version": "production_authorization_scope.v1",
  "assets": [
    {
      "id": "host_video",
      "path": "origin/host.mp4",
      "role": "talking-head source"
    }
  ],
  "actions": [
    {
      "id": "cloud_transcription",
      "kind": "external_upload",
      "description": "Upload the talking-head source for word-level transcription.",
      "purpose": "Create a timed transcript for source-aligned edits.",
      "provider": "Exact provider and surface",
      "cost_or_quota": "May consume hosted transcription quota.",
      "asset_ids": ["host_video"]
    },
    {
      "id": "opening_reorder",
      "kind": "editorial_reorder",
      "description": "Move one approved quote to the first three seconds.",
      "purpose": "Strengthen the opening without inventing speech.",
      "provider": "",
      "cost_or_quota": "",
      "asset_ids": ["host_video"]
    }
  ],
  "rights_items": [
    {
      "id": "host_likeness",
      "kind": "real_person_likeness",
      "subject": "Host",
      "intended_use": "Edit and publish the supplied talking-head footage.",
      "asset_ids": ["host_video"]
    }
  ]
}
```

支持的 action kind：

- `external_upload`：把命名素材上传到确切 provider/surface。
- `editorial_reorder`：把高光、金句或其他内容移到原时间顺序之外。
- `content_removal`：删除讲话、剧情或其他内容。
- `creative_addition`：添加 B-roll、音乐、CTA、包装或其他创意层。
- `paid_generation`：提交可能消耗 credits/quota 的生成任务。
- `voice_clone`：上传声音样本并克隆/合成该说话人声音。
- `publish`：把确定交付件发布到明确平台/账号流程。

`external_upload / paid_generation / voice_clone / publish` 必须写 exact provider/surface（发布时是明确平台/入口）；前三类还必须写 `cost_or_quota`。外部上传必须列出实际会被上传的 `asset_ids`。

支持的 rights kind 与允许依据：

| kind | 允许的 basis |
|---|---|
| `real_person_likeness` | `subject_self` / `explicit_subject_permission` / `licensed_performer` |
| `minor_likeness` | `guardian_permission` / `licensed_performer_with_guardian` |
| `public_figure_likeness` | `explicit_subject_permission` / `licensed_material` |
| `voice_clone` | `speaker_self` / `explicit_speaker_permission` |
| `brand_or_trademark` | `brand_owner` / `licensed_use` / `explicit_brand_permission` |
| `protected_character` | `rights_owner` / `licensed_use` / `explicit_rights_permission` |

公众人物素材可公开访问不等于同意克隆或代言；未成年人不能使用 `subject_self` 代替监护授权；声音克隆 action 必须同时存在 `voice_clone` rights item。

## 2. Prepare

```bash
python3 scripts/production_authorization.py prepare \
  --project-dir . \
  --scope work/production_authorization_scope.json \
  --output work/production_authorization_request.json \
  --markdown work/production_authorization_request.md \
  --response-template work/production_authorization_response.json \
  --strict
```

`prepare` 要求 scope 和所有声明素材都在项目内且不是 symlink；保存相对路径、大小和 SHA-256。重复 id/path、缺文件、项目外路径、未知 action/right kind、未知 asset id、缺 provider/cost note 都会阻塞。

## 3. 填 response 并 audit

逐项填写 response：

- 每个 action 必须 `approve` 或 `reject`，并给出非空 `note`。
- 每个 rights item 必须 `approve` 或 `reject`；批准时 basis 必须来自 request 列出的集合。
- 每个 rights item 都必须写 `evidence_note`，说明当前 operator 依据什么记录此决定。
- 任一 reject 会阻塞当前 scope；不要删除 response 行绕过。修改 scope、重新 prepare、再复核。

```bash
python3 scripts/production_authorization.py audit \
  --project-dir . \
  --request work/production_authorization_request.json \
  --response work/production_authorization_response.json \
  --output work/production_authorization.json \
  --markdown work/production_authorization.md \
  --strict
```

## 4. 在动作前 live verify

```bash
python3 scripts/production_authorization.py verify \
  --project-dir . \
  --report work/production_authorization.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require production_authorization \
  --strict
```

`verify` 会重读 scope、所有 source assets、request、response，重算派生 decisions 和 report id。任何源文件重编码/替换、provider/用途/成本提示变化、rights subject/用途变化、response 改写或手工修报告都会使旧授权失效。

## 边界

- 一份授权只覆盖 scope 中命名的动作、provider/surface、用途和素材，不向其他项目或后续 provider 传播。
- SHA-256 只证明字节有没有变化，不证明签字人是谁、授权是否合法有效或对方有处分权。
- 脚本不读取聊天记录来猜同意；没有 explicit response 就没有 ready report。
- 权利场景复杂、存在争议或面向重大商业发布时，仍应取得真实合同/授权文件并让专业人士审查；本地 JSON 不能替代它。
