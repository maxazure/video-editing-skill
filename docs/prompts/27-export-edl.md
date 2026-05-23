# 27 NLE Handoff：导出 EDL 给 Premiere / FCP / Resolve

当自动剪辑方案已经确定，但还需要交给专业剪辑软件继续调色、混音、精剪或给协作剪辑师复核时，用 `scripts/export_edl.py` 把本项目的 `render_config.json` 或 rough/jump cut list 导出成单轨 EDL。

它不会渲染新视频，也不会改源素材；输出是：
- `*.edl`：CMX 3600 风格的单视频轨 edit decision list
- `*.edl.json`：本项目保留的 manifest，包含绝对源路径、精确秒数和事件清单

## 从 render_config 导出

```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/day58_edit.edl \
  --fps 30 \
  --title DAY58_EDIT
```

适合：最终选段已经在 `render_config.json` 里，想把同一条剪辑时间线交给 NLE。

## 从 rough/jump cut list 导出

```bash
python3 scripts/export_edl.py \
  --cut-list work/rough_cut.json \
  --output work/rough_cut.edl \
  --fps 30 \
  --title ROUGH_CUT
```

如果 cut list 里没有 `input` 字段，显式补源素材：

```bash
python3 scripts/export_edl.py \
  --cut-list work/jump_cut.json \
  --source origin/talking.mp4 \
  --output work/jump_cut.edl
```

## 可选字幕注释

默认 EDL 只写源文件和时间码，避免中文/长文本影响 NLE 导入。如果希望剪辑师在 EDL 里看到口播文本：

```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/day58_edit.edl \
  --include-transcript-comments
```

## 注意

- 当前实现是单视频轨 EDL，适合选段、粗剪、调色/混音交接；复杂 overlay、字幕、章节卡和 B-roll 仍以 `render_final.py` / `export_capcut.py` 为准。
- EDL 使用 non-drop-frame timecode；社媒短视频默认 `--fps 30` 足够，影视项目请按源项目时间线传 `--fps 24/25/30/60`。
- `*.edl.json` manifest 是审计依据：如果 NLE 对 EDL 注释或文件名处理不一致，以 manifest 里的秒数和绝对路径为准。
