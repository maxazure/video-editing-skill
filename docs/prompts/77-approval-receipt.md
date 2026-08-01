# 77 — Approval Receipt 最终审批收据

把已经人工复核的最终视频、封面、文案、字幕和 QA 报告绑定到具体 SHA-256。任何一个文件后来被重渲染、替换、删除或改写，旧审批都会变成 `stale`，发布门禁会阻塞。

## 为什么需要

`render_qa.py` 和 `pipeline_manifest.py` 能证明某一刻文件存在、QA 通过，但不能证明“现在准备上传的字节”仍是人看过的版本。审批收据补的是这个时间差：

1. 先完整审片并核对最终交付件。
2. 对明确列出的文件创建收据。
3. 上传前重新计算哈希。
4. 只在全部文件仍为 `current` 时继续。

它是本地一致性检查，不是数字签名；`approved_by` 只是用户提供的标签，不验证身份。

## 创建收据

只列真正交付或作为审批依据的稳定文件，不要加入每次都会重写的 `pipeline_manifest.json`、`publish_package.json` 或 dashboard：

```bash
python3 scripts/approval_receipt.py create \
  --project-dir . \
  --artifact output/day77_xhs.mp4 \
  --artifact output/day77_douyin.mp4 \
  --artifact output/day77_wxch.mp4 \
  --artifact output/cover.png \
  --artifact output/day77_caption.json \
  --artifact output/subtitles/day77.srt \
  --artifact verify/render_qa.json \
  --approved-by "Jay" \
  --note "三平台视频按正常速度完整看过；封面、文案和字幕已核对。" \
  --output verify/approval_receipt.json \
  --markdown verify/approval_receipt.md
```

规则：

- 至少一个 `--artifact`，可重复。
- 文件必须存在、位于 `--project-dir` 内，并解析成普通文件。
- 收据保存项目相对路径、字节数、修改时间和 SHA-256。
- 重复路径、项目外路径和把收据自身列为 artifact 都会失败。
- 创建收据不会锁文件，也不会复制视频；收据生成后修改文件会让它自动过期。

## 上传前验证

```bash
python3 scripts/approval_receipt.py verify \
  --project-dir . \
  --receipt verify/approval_receipt.json \
  --output verify/approval_receipt_verification.json \
  --markdown verify/approval_receipt_verification.md \
  --strict
```

`approval_receipt_verification.v1` 会把每个文件标为：

- `current`：路径、大小和 SHA-256 都与审批时一致。
- `changed`：文件仍在，但字节数或 SHA-256 改变。
- `missing`：文件已删除。
- `unsafe`：路径变成 symlink、跳到其他 canonical 位置、离开项目目录，或哈希期间发生变化。
- `invalid`：收据 schema、哈希或重复路径非法。

`--strict` 在不是 `current` 时返回 2。任何变化都应重新审片/核对，然后创建新收据；不要手改旧 JSON 的 hash。

## 接入发布门禁

只要项目里存在 `approval_receipt.json`，`pipeline_manifest.py` 就会实时重算最新一份收据，发现过期时自动阻塞。需要强制每个项目都必须有收据：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require approval_receipt \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

最终发布包也可强制：

```bash
python3 scripts/publish_package.py \
  --project-dir . \
  --platforms xhs douyin wxch \
  --require-approval-receipt \
  --output work/publish_package.json \
  --markdown work/publish_package.md \
  --strict
```

即使传入的是旧 `pipeline_manifest.json`，`publish_package.py` 仍会独立验证当前收据，避免旧 manifest 绕过文件变化。

## 边界

- SHA-256 证明“字节是否变化”，不证明审片质量、版权、事实准确性或发布授权。
- 本地 JSON 可被人为篡改；它是误操作/版本漂移门禁，不是对抗恶意攻击的签名系统。
- 大视频验证需要顺序读取整个文件，耗时与文件大小成正比。
- 只绑定最终交付范围。源素材、缓存、临时预览、可再生成 dashboard 不应默认加入。
