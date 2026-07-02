# 27 NLE Handoff：导出 EDL / FCPXML / OTIO 给 Premiere / FCP / Resolve

当自动剪辑方案已经确定，但还需要交给专业剪辑软件继续调色、混音、精剪或给协作剪辑师复核时，用 `scripts/export_edl.py`、`scripts/export_fcpxml.py` 或 `scripts/export_otio.py` 把本项目的 `render_config.json` 或 rough/jump cut list 导出成 NLE 时间线。

它不会渲染新视频，也不会改源素材；输出是：
- `*.edl`：CMX 3600 风格的单视频轨 edit decision list
- `*.edl.json`：本项目保留的 manifest，包含绝对源路径、精确秒数和事件清单
- `*.fcpxml`：Final Cut Pro / DaVinci Resolve 更友好的单 spine FCPXML
- `*.fcpxml.json`：FCPXML 对应 manifest，保留同一组精确秒数和事件清单
- `*.otio`：OpenTimelineIO JSON timeline，包含 V1 和可选 A1 track
- `*.otio.json`：OTIO 对应 manifest，保留同一组精确秒数和事件清单

## 从 render_config 导出 EDL

```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/day58_edit.edl \
  --fps 30 \
  --title DAY58_EDIT
```

适合：最终选段已经在 `render_config.json` 里，想把同一条剪辑时间线交给 NLE。

## 从 rough/jump cut list 导出 EDL

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

## 导出 FCPXML

如果目标是 Final Cut Pro，或 DaVinci Resolve 对 EDL 的文件路径/注释处理不稳定，优先导出 FCPXML：

```bash
python3 scripts/export_fcpxml.py \
  --config work/render_config.json \
  --output work/day58_edit.fcpxml \
  --fps 30 \
  --width 1080 \
  --height 1920 \
  --title DAY58_EDIT

python3 scripts/export_fcpxml.py \
  --cut-list work/rough_cut.json \
  --output work/rough_cut.fcpxml \
  --fps 30
```

`export_fcpxml.py` 会生成单 spine timeline：每个 keep segment 变成一个 `asset-clip`，`offset` 是成片时间线，`start` / `duration` 是源素材时间段；同一源素材只登记一次 `asset` resource。

## 导出 OTIO

如果团队用 Premiere / Resolve / Blender / Unreal 等支持 OpenTimelineIO 或可通过 OTIO adapter 转换的工具，导出 `.otio`：

```bash
python3 scripts/export_otio.py \
  --config work/render_config.json \
  --output work/day58_edit.otio \
  --fps 30 \
  --title DAY58_EDIT

python3 scripts/export_otio.py \
  --cut-list work/rough_cut.json \
  --output work/rough_cut.otio \
  --fps 30 \
  --include-transcript-metadata
```

`export_otio.py` 输出 `Timeline.1` / `Stack.1` / `Track.1` / `Clip.2` 结构：V1 为连续视频时间线，默认同时写 A1 音频 track；如果只需要视频 track，加 `--no-audio-track`。复杂字幕、overlay、章节卡、B-roll 和生成素材仍用本项目 JSON manifest / `render_final.py` 作为最终审计依据。

## 可选字幕注释

默认 EDL 只写源文件和时间码，避免中文/长文本影响 NLE 导入。如果希望剪辑师在 EDL 里看到口播文本：

```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/day58_edit.edl \
  --include-transcript-comments
```

## 注意

- 当前实现是单视频轨 EDL / 单 spine FCPXML / 简单 OTIO V1+A1，适合选段、粗剪、调色/混音交接；复杂 overlay、字幕、章节卡和 B-roll 仍以 `render_final.py` / `export_capcut.py` 为准。
- EDL 使用 non-drop-frame timecode；社媒短视频默认 `--fps 30` 足够，影视项目请按源项目时间线传 `--fps 24/25/30/60`。
- FCPXML 时间会按 `--fps` snap 到帧，默认 timeline 是竖屏 `1080x1920`，横屏项目请传 `--width 1920 --height 1080`。
- `*.edl.json` / `*.fcpxml.json` / `*.otio.json` manifest 是审计依据：如果 NLE 对 XML/EDL/OTIO 处理不一致，以 manifest 里的秒数和绝对路径为准。
