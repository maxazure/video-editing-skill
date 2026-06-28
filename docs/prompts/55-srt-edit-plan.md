# 55 SRT Edit Plan 字幕编辑指令转剪辑方案

> 把一份 SRT 和人工/agent 写的 keep/drop 指令，转成可审计的 `srt_edit_plan.json`、`render_config.json`、cut list 和 Markdown review。

## 什么时候用

- 客户、剪辑师或 agent 已经看完字幕，只给出“保留 3-6、删 7、把 12-14 放前面”这类文字编辑意见。
- 已经从剪映/CapCut、YouTube Studio 或平台工具导出了 SRT，但不想先转换成完整 transcript 再手写 `render_config`。
- 想先用字幕编号做一版可复核粗剪，再交给 `render_final.py`、`timeline_view.py`、`export_edl.py` 或 `export_fcpxml.py`。

## 写编辑指令

编辑指令是普通 Markdown/文本。顺序就是最终输出顺序：

```md
title: 发布会高光
platform: xhs
cover_style: bold

- keep 3-5: 先用产品发布和用户反应
- skip 1-2: 铺垫太慢
- keep 8: 补一句核心结论
- drop 9-10: 重复解释
```

支持的保留词：`keep` / `include` / `use` / `select`。
支持的删除词：`drop` / `skip` / `exclude` / `remove`。

`keep` 行可以写范围和逗号，例如 `keep 3-5,8`。不连续编号会被拆成多个 clip；重新排序用多行 `keep` 控制。

## 生成剪辑方案

```bash
python3 scripts/srt_edit_plan.py \
  --srt work/captions.srt \
  --guide work/edit_guide.md \
  --source-media origin/talking.mp4 \
  --output work/srt_edit_plan.json \
  --render-config work/render_config.json \
  --cut-list work/srt_edit_cut.json \
  --markdown work/srt_edit_plan.md \
  --strict
```

输出：

- `srt_edit_plan.json`：完整 SRT、guide 指令、保留/删除/未复核段落和 blocking 状态。
- `render_config.json`：按 `keep` 行顺序生成 clips，可直接交给 `render_final.py`。
- `srt_edit_cut.json`：按原素材时间排序的 `keep_segments`，适合 `timeline_view.py --cut-list` 复核。
- `srt_edit_plan.md`：给人看的 review 表。

如果要强制每个字幕编号都被 `keep` 或 `drop` 审过：

```bash
python3 scripts/srt_edit_plan.py \
  --srt work/captions.srt \
  --guide work/edit_guide.md \
  --source-media origin/talking.mp4 \
  --output work/srt_edit_plan.json \
  --require-all-reviewed \
  --strict
```

`--require-all-reviewed --strict` 会在有未复核 SRT 段落时返回 2。

## 复核与渲染

先看切点：

```bash
python3 scripts/timeline_view.py \
  origin/talking.mp4 \
  --cut-list work/srt_edit_cut.json \
  --output-dir output/verify/srt_edit
```

确认后渲染：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --output output/srt_edit_master.mp4
```

如果要交给专业剪辑软件：

```bash
python3 scripts/export_edl.py --config work/render_config.json --output work/srt_edit.edl
python3 scripts/export_fcpxml.py --config work/render_config.json --output work/srt_edit.fcpxml
```

注意：`srt_edit_plan.py` 只做字幕编号到剪辑时间线的确定性转换；它不会自动判断画面质量，也不会做逐 clip 变速。最终渲染前仍应跑 `timeline_view.py` 和 `edit_preflight.py`。
