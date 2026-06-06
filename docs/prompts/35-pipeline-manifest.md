# Pipeline Manifest 生产线状态清单

当一个视频项目已经跑过多步脚本，但你不确定还缺什么、哪些生成任务还没审批、成片能不能发布时，用 `pipeline_manifest.py` 汇总当前目录。

它不会渲染视频、不会调用 LLM、不会提交 Dreamina/即梦等付费任务；只扫描本地 artifact，输出 JSON + Markdown 门禁报告。

## 常用命令

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/pipeline_manifest.json \
  --markdown work/day58/pipeline_manifest.md \
  --strict
```

`--strict` 在缺少必需 artifact、render QA fail、storyboard/provider/transition/motion/speaker/privacy redaction 仍有阻塞项时返回退出码 2，适合放在自动化发布前。

## Target Stage

| stage | 必需 artifact |
|---|---|
| `analysis` | `transcript` |
| `plan_review` | `transcript` / `clean_script` / `storyboard_plan` |
| `render_ready` | `transcript` / `clean_script` / `render_config` |
| `publish_ready` | `transcript` / `clean_script` / `render_config` / `master_video` / `render_qa` / `caption` |

额外要求某类 artifact：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require subtitles \
  --require chapter_markers \
  --require privacy_redaction \
  --strict
```

## 会自动识别的阻塞

| artifact | 阻塞逻辑 |
|---|---|
| `storyboard_assets.json` | `summary.blocking > 0` |
| `provider_decision.json` | `approval_required` / `budget_blocked` / `selected_missing_requirements` 非 0 |
| `transition_bridge_plan.json` | `summary.blocking > 0` |
| `motion_guard.json` | `summary.blocking > 0` |
| `speaker_turns.json` | `summary.blocking > 0` |
| `privacy_redaction.json` | `summary.blocking > 0` |
| `render_qa.json` | `status == fail` 或任一 file `status == fail` |

这些 artifact 即使不是当前 stage 的必需项，只要存在且未解决，也会让 manifest 进入 `blocked`，避免把未审批 paid generation 或失败 QA 漏到发布阶段。

## 输出

- `pipeline_manifest.json`：机器可读的 `pipeline_manifest.v1`，包含 gates、artifact 绝对路径、缺口和 next_actions。
- `pipeline_manifest.md`：给人看的状态表，适合贴进任务说明或发布检查单。

## 自动化建议

在每日短视频流水线最后加：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir "$WORK" \
  --target-stage publish_ready \
  --require subtitles \
  --output "$WORK/output/pipeline_manifest.json" \
  --markdown "$WORK/output/pipeline_manifest.md" \
  --strict
```

如果返回 2，先看 Markdown 的 `Next Actions`，补齐缺件或解除阻塞后再发布。
