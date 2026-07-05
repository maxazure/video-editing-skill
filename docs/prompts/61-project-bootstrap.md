# Project Bootstrap 项目启动与素材导入

用于刚拿到一批原始素材时，先把素材目录整理成本项目约定的 `origin/`、`work/`、`output/`、`verify/`、`edit/` 结构，并生成 source inventory 和项目记忆文件。适合“把这个文件夹里的素材剪成一条视频”这类 folder-first 开工场景。

## 什么时候用

- 用户给了一个原始素材文件夹，还没有建立项目目录。
- 想保护外部原始素材路径，只在项目内的 working copy 上继续剪辑。
- 自动化或跨会话任务需要一个稳定的 `project.md` 和 `source_inventory.json`。
- 后续要用 `pipeline_manifest.py` / `project_resume.py` 继续跟踪项目状态。

## 常用方式

```bash
python3 scripts/project_bootstrap.py \
  --source ~/Downloads/raw-shoot \
  --project-dir work/day61 \
  --title "Day61 launch edit" \
  --output work/day61/work/source_inventory.json \
  --markdown work/day61/work/source_inventory.md \
  --project-note work/day61/project.md \
  --strict
```

默认使用 `--mode copy`，会把支持的 video/audio/image/sidecar 文件复制到：

- `origin/raw/`
- `origin/broll/`
- `origin/audio/`
- `origin/bgm/`
- `origin/images/`
- `origin/assets/`
- `origin/sidecars/`

同名文件会自动加 `-2`、`-3` 后缀，不覆盖已有文件。大素材盘内想减少复制时间时可用 `--mode hardlink`，如果硬链接失败会回退到 copy 并在 warnings 里记录。

## 输出

`source_inventory.json` 使用 `project_bootstrap.v1`，包含：

- `project_dir`
- `directories`
- `summary.files/categories/media_types/actions`
- `files[].source_path`
- `files[].project_path`
- `files[].relative_path`
- `files[].media_type`
- `files[].category`
- `next_actions[]`

同时会写：

- `work/source_inventory.md`：给人审的素材清单。
- `project.md`：给下一位 agent/下次会话看的项目记忆。
- `next_steps.md`：下一步命令清单。

## 推荐工作流

1. 先跑 `project_bootstrap.py` 建项目和 source inventory。
2. 打开 `work/source_inventory.md`，确认主口播/长视频素材。
3. 对主素材跑 `transcribe.py`，保存 `work/transcript.json`。
4. 用 `pipeline_manifest.py --require source_inventory` 检查 analysis 阶段是否有稳定项目入口：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day61 \
  --target-stage analysis \
  --require source_inventory \
  --strict
```

5. 需要视觉理解时继续跑 `video_understanding.py`；多 take 选择时继续跑 `takes_pack.py`。

## 注意

- `project_bootstrap.py` 不转码、不渲染、不上传、不调用 LLM，也不提交任何生成任务。
- `origin/` 是项目内 working copy；外部 `source_path` 只用于溯源，不应该被后续脚本直接改写。
- `--strict` 只在没有找到支持的素材文件时返回 2，方便自动化提前失败。
