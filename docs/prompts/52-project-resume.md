# Project Resume 续跑上下文包

当一个视频项目跨天、跨 agent 或被压缩上下文后继续做时，先用 `project_resume.py` 扫描本地 artifacts，生成一份给下一位 agent 直接接手的 JSON + Markdown。

它不会渲染视频、不会上传平台、不会提交 Dreamina/即梦或其他付费生成任务；只复用 `pipeline_manifest.py` 的 gate 判断，把“现在到哪一步、缺什么、先看哪些文件、下一步从哪里开始”整理成 handoff packet。

## 常用命令

```bash
python3 scripts/project_resume.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/project_resume.json \
  --markdown work/day58/project_resume.md \
  --agent-note work/day58/CLAUDE.md \
  --strict
```

`--agent-note` 如果不传路径，会默认写到 `--project-dir/CLAUDE.md`：

```bash
python3 scripts/project_resume.py \
  --project-dir work/day58 \
  --markdown work/day58/project_resume.md \
  --agent-note
```

## 什么时候用

- 长流程视频项目中途暂停，要给下一次 Codex/Claude 接着做。
- `pipeline_manifest.py` 已经能判断 gate，但你还需要更短、更像接手说明的 Markdown。
- 自动化或后台线程结束前，要把当前项目状态写成可恢复 artifact。
- 生成任务、QA、发布包混在多个目录里，需要按修改时间列出最近产物。

## 输出内容

`project_resume.json` 使用 `project_resume.v1` schema：

- `status` / `phase`：来自 pipeline gate 的总体状态和续跑阶段，例如 `needs_render`、`needs_qa_fix`、`waiting_on_generation_tasks`。
- `recommended_first_action`：当前最优先的一条下一步动作。
- `next_actions[]`：缺件、阻塞 gate 和 warning 的完整动作列表。
- `latest_artifacts[]`：按修改时间排序的最近 artifacts，带类别、路径、大小和时间。
- `ready_artifacts[]`：已 ready 的关键 artifact 入口。
- `gates[]`：只保留 required、blocked、warn gate 的短表。
- `suggested_prompt`：可以直接交给下一位 agent 的一句续跑 prompt。

Markdown / agent note 会包含同样信息，适合放到 `CLAUDE.md`、任务说明或自动化 inbox 里。

## 严格模式

`--strict` 在当前 target stage blocked 时返回 2，适合自动化收尾：

```bash
python3 scripts/project_resume.py \
  --project-dir "$WORK" \
  --target-stage publish_ready \
  --output "$WORK/project_resume.json" \
  --markdown "$WORK/project_resume.md" \
  --strict
```

如果只想生成续跑说明，不要让缺件阻断外层脚本，就去掉 `--strict`。

## 与 pipeline_manifest 的关系

- `pipeline_manifest.py` 是完整 gate 表，适合发布前机器判定。
- `project_resume.py` 是 agent handoff 包，适合跨会话恢复。

两者都只读本地文件。需要发布前最终确认时，仍然先跑 `pipeline_manifest.py --strict`，再跑 `publish_package.py --strict`。
