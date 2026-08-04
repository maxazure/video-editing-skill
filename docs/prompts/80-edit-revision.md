# Edit Revision：剪辑 artifact 可逆修订

适用于已经有 `work/render_config.json`、`work/enrich_plan.json`、caption、字幕 sidecar 或其他文本剪辑 artifact，需要先审完整改动、再成组写入，并保留可验证 undo/redo 的场景。

它不管理原始视频、音频、生成素材或成片，不替代 Git，也不会重新运行分析或渲染。每次 revision 只保存明确列出的 UTF-8 文本/JSON 文件；`origin/`、`output/`、`verify/`、代码、symlink 和隐藏目录均拒绝写入。

## 1. 准备 source-bound proposal

```bash
python3 scripts/edit_revision.py prepare \
  --project-dir . \
  --artifact work/render_config.json \
  --artifact work/enrich_plan.json \
  --depends-on work/transcript_reviewed.json \
  --title "收紧开头并调整 B-roll" \
  --reason "已完成时间码审片，采用第二版开头。" \
  --output work/edit_revision_proposal.json \
  --markdown work/edit_revision_proposal.md
```

proposal 会记录每个 artifact 和 dependency 的 SHA-256。只修改 `artifacts[].proposed_content`；不要改 `path`、`base` 或 `dependencies`。当前版本只管理已经存在的文件，不用它创建或删除 artifact。

## 2. 审计完整提案

```bash
python3 scripts/edit_revision.py audit \
  --project-dir . \
  --proposal work/edit_revision_proposal.json \
  --output work/edit_revision_audit.json \
  --markdown work/edit_revision_audit.md \
  --strict
```

合法提案的状态是 `pending_approval`，所以 `audit --strict` 返回 2 是预期人工 gate。脚本会拒绝：

- 基础 artifact 或 `--depends-on` 文件在 prepare 后发生变化；
- 重复路径、越过项目根目录、symlink、二进制/超大文件；
- `origin/`、`output/`、`verify/`、代码或 revision 自身文件；
- 无实际变化、无 title/reason 或无效 JSON。

`review_id` 由 title、reason、before/after hash 和 dependency hash 确定。proposal 在 audit 后再改，旧 audit 和 approval 会自动失效。

## 3. 独立批准后成组应用

从 audit Markdown 复制 approval template，明确填写：

```json
{
  "version": "edit_revision_approval.v1",
  "review_id": "revision-...",
  "decision": "approve",
  "approved_by_label": "Jay"
}
```

保存为 `work/edit_revision_approval.json`，然后运行：

```bash
python3 scripts/edit_revision.py apply \
  --project-dir . \
  --proposal work/edit_revision_proposal.json \
  --audit work/edit_revision_audit.json \
  --approval work/edit_revision_approval.json \
  --journal work/edit_revision_history.json \
  --markdown work/edit_revision_history.md \
  --strict
```

apply 会再次读取 live 文件并重算 audit；全部通过后，多个 artifact 作为一个 revision 成组写入，运行期写入错误会尝试恢复旧 bytes。before/after 内容按 SHA-256 保存在 `work/.edit-revisions/blobs/`，journal 只保存项目相对路径、操作顺序、依赖和审批标签。跨多个文件无法提供操作系统级原子提交，进程崩溃或断电后必须先运行 `status --strict`；`approved_by_label` 是本地自报标签，不是身份认证或数字签名。

## 4. 状态、撤销和重做

```bash
python3 scripts/edit_revision.py status \
  --project-dir . \
  --journal work/edit_revision_history.json \
  --markdown work/edit_revision_history.md \
  --strict

python3 scripts/edit_revision.py undo --project-dir . --strict
python3 scripts/edit_revision.py redo --project-dir . --strict
```

undo 只在当前 artifact 仍等于该 operation 的 after hash 时执行；redo 还会确认原 `based-on` dependencies 未改变。任何手工覆盖、丢失 blob、symlink 或依赖漂移都会拒绝操作，不会把旧配置静默盖回去。

undo 后默认保留 redo。若已确认要从较早版本走另一条分支，准备并审计新 proposal 后显式应用：

```bash
python3 scripts/edit_revision.py apply \
  --project-dir . \
  --proposal work/edit_revision_proposal.json \
  --audit work/edit_revision_audit.json \
  --approval work/edit_revision_approval.json \
  --fork-history \
  --strict
```

`--fork-history` 会把未重做的旧操作保存在 journal 的 `archived_branches[]`，再从当前 cursor 建新 revision；它不会删除 content-addressed blobs。

## 5. 发布门禁

只要项目里存在 `edit_revision_history.json`，`pipeline_manifest.py` 就会 live 验证 journal、当前 artifact、已应用 dependency 和 before/after blobs：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require edit_revision_history \
  --strict
```

日常建议：用 revision 管理 `work/` 里的上游剪辑决策；最终 master、封面、caption、字幕和 QA 仍用 `approval_receipt.py` 绑定实际待上传字节。两者解决的是不同问题。
