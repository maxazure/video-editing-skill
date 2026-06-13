# 46 — Generation Task Log 异步生成任务台账

把 Dreamina/即梦、PixVerse、Veo、Sora 等异步生成任务的 `submit_id` / task id、轮询命令、下载命令和本地落盘状态写进一个可审计 JSON。脚本只记录和检查任务，不提交 paid generation，不消耗 credits。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时用

- `provider_decision.py` 或 `video_prompt_pack.py` 已经确认某些 shot 要交给生成视频 provider。
- 已经提交 Dreamina/即梦任务，需要保存 `submit_id` 并跨会话继续查询。
- provider 显示任务完成，但生成的视频还没下载到 `work/generated_video/`。
- 发布前想用 `pipeline_manifest.py` 拦住未完成、失败、未下载或丢失的生成素材。

## 推荐流程

先从 provider 决策里生成待审批任务台账：

```bash
python3 scripts/generation_task_log.py import-provider-decision \
  --provider-decision work/provider_decision.json \
  --log work/generation_tasks.json \
  --markdown work/generation_tasks.md \
  --strict
```

提交 Dreamina/即梦任务前先确认 credits。提交后把 `submit_id` 记入台账：

```bash
python3 scripts/generation_task_log.py add \
  --log work/generation_tasks.json \
  --provider dreamina \
  --task-id "<submit_id>" \
  --shot-id shot_002 \
  --expected-path work/generated_video/shot_002.mp4 \
  --status submitted \
  --markdown work/generation_tasks.md \
  --strict
```

脚本会自动写入：

```bash
dreamina query_result --submit_id=<submit_id>
dreamina query_result --submit_id=<submit_id> --download_dir=work/generated_video
```

下载完成后更新本地文件路径：

```bash
python3 scripts/generation_task_log.py update \
  --log work/generation_tasks.json \
  --provider dreamina \
  --task-id "<submit_id>" \
  --status downloaded \
  --asset-path work/generated_video/shot_002.mp4 \
  --markdown work/generation_tasks.md \
  --strict
```

也可以把 provider 返回的 JSON 直接导入：

```bash
python3 scripts/generation_task_log.py update \
  --log work/generation_tasks.json \
  --provider dreamina \
  --raw-json work/dreamina_query_result.json \
  --markdown work/generation_tasks.md
```

## 输出内容

`generation_task_log.v1` 包含：

- `tasks[].provider_task_id`：Dreamina `submit_id`、PixVerse task id 等。
- `tasks[].poll_command` / `download_command`：下一步可执行命令。
- `tasks[].expected_path` / `asset_path`：生成素材应落盘的位置和实际位置。
- `tasks[].readiness`：`needs_approval` / `pending` / `processing` / `needs_download` / `missing_asset` / `failed` / `ready`。
- `summary.blocking`：未完成、未下载、失败或缺本地文件的任务数。

`--strict` 会在 `summary.blocking > 0` 时返回退出码 `2`。`pipeline_manifest.py` 会自动识别 `generation_tasks.json`，并在发布前把未清零的 `summary.blocking` 当作 blocking gate。

## 设计原则

- 台账只管理状态，不替用户提交 paid generation。
- Dreamina/即梦任务通常异步执行，必须保存 `submit_id`，再用 `query_result` 轮询和下载。
- `completed` 但没有本地 `asset_path` 仍然阻塞，因为最终渲染只能使用本地文件。
- 已下载素材还要继续通过 `storyboard_assets.py --strict`、`asset_provenance.py` 和 `render_qa.py` 进入发布前检查。
