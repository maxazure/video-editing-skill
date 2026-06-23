# Edit Preflight 渲染前预检

在 `render_final.py` 前跑一遍本地预检，提前发现缺素材、空剪辑、非法时间段、找不到 transcript segment、overlay 超出时间线，以及 PIP / focus 这类参数会被 clamp 或误解的问题。

它只读 JSON 和本地路径，不渲染、不解码、不上传、不调用任何生成服务。

## 推荐位置

放在 `content_guard.py` 之后、`render_final.py` 之前：

```bash
python3 scripts/edit_preflight.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output work/edit_preflight.json \
  --markdown work/edit_preflight.md \
  --strict
```

`--strict` 会让 warning 也返回 2，适合自动化或发布前 gate。不加 `--strict` 时，只有 blocking issue 返回 2。

## 能检查什么

- `render_config.clips[]`：非空、视频路径存在、`start/end` 合法，或 `transcript + segment_id` 能解析到 transcript segment。
- `broll_overlays[]` / `image_overlays[]` / `pip_overlays[]`：本地素材存在、时间段合法、不会跑到输出时间线之外。
- `focus_events[]`：`x/y` 是 0-1 归一化坐标；如果传像素坐标，必须带 `source_width/source_height`。
- `enrich_plan`：检查 B-roll、imagegen、PIP、stickers、chapter cards 和 focus events；没有本地文件的 advisory 生图/B-roll 会警告， timed PIP 缺文件会阻塞。
- `cut_list`：检查 rough/jump cut 的 `keep_segments[]` 非空、时间段合法、输入视频存在。

## 多输入

```bash
python3 scripts/edit_preflight.py \
  --config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --enrich-plan work/pip_overlay_plan.json \
  --cut-list work/jump_cut.json \
  --output work/edit_preflight.json \
  --markdown work/edit_preflight.md
```

输出 `edit_preflight.v1`，其中 `summary.blocking` 可被 `pipeline_manifest.py` 识别。只要项目里存在 unresolved `edit_preflight.json`，manifest 会把它列为 blocking gate。

## 典型修复

- `missing_file`：把素材路径补回 `render_config` / `enrich_plan`，或先生成/下载文件。
- `segment_not_found`：检查 transcript 是否是同一版，或把 clip 改成直接 `start/end`。
- `cue_after_timeline`：overlay 时间是输出时间线，不是源视频时间线；按剪辑后的总时长重算。
- `pixel_coordinate_without_source_size`：把录屏点击坐标改成 0-1，或补 `source_width/source_height`。

渲染后仍然必须跑 `render_qa.py`。preflight 只证明计划合理，不证明编码后的画面、声音和字幕没有问题。
