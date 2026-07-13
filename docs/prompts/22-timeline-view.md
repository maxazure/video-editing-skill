# 22 — Timeline View 源素材/成片切点复盘图

`scripts/timeline_view.py` 用于把某个时间窗口渲染成一张 PNG：上半部分是 filmstrip，下半部分是 waveform。它既能查看源素材里的删除段，也能把 `keep_segments` 累计映射到已渲染成片的输出时间轴，逐个检查真正发生的拼接边界。

## 什么时候用

- `jump_cut.py` 生成了 cut list，想快速看每个删除段附近是否咬字。
- rough/jump cut 已经渲染，想检查成片每个拼接点的画面跳变、waveform 硬断或字幕遮挡。
- `render_qa.py` 报了 WARN/FAIL，需要定位可疑区间的画面和音频。
- 成片发布前想抽查 hook、转场、片尾等关键节点。
- 无声 B-roll 也可以用；无音频时脚本只输出 filmstrip。

## 常用命令

单点复盘：

```bash
python3 scripts/timeline_view.py output/day58_master.mp4 \
  --at 42.5 \
  --radius 1.5 \
  --output output/verify/day58_42_5s.png
```

指定区间复盘：

```bash
python3 scripts/timeline_view.py output/day58_master.mp4 \
  --start 38.0 \
  --end 44.0 \
  --output output/verify/day58_38_44s.png
```

从 jump-cut cut list 批量生成：

```bash
python3 scripts/timeline_view.py origin/talking.mp4 \
  --cut-list work/jumpcut.json \
  --output-dir output/verify/cuts \
  --limit 12 \
  --json output/verify/cuts_manifest.json
```

在已渲染成片上逐切点生成；`--output-speed` 要与渲染速度一致，`--output-offset` 用于片头封面或其他前置时长：

```bash
python3 scripts/timeline_view.py output/rough_cut.mp4 \
  --rendered-cut-list work/rough_cut.json \
  --output-speed 1.25 \
  --output-offset 1.0 \
  --output-dir output/verify/rendered_cuts \
  --json output/verify/rendered_cuts.json
```

只看命令和窗口，不实际渲染：

```bash
python3 scripts/timeline_view.py output/day58_master.mp4 \
  --at 42.5 \
  --output output/verify/day58_42_5s.png \
  --dry-run
```

## 参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--at` | 无 | 单点复盘的中心秒数 |
| `--start / --end` | 无 | 明确指定复盘窗口 |
| `--radius 1.5` | 1.5 | `--at` 前后各取多少秒 |
| `--frames 12` | 12 | filmstrip 抽帧数量 |
| `--width 1600` | 1600 | 输出图宽度 |
| `--waveform-height 180` | 180 | waveform 高度 |
| `--cut-list` | 无 | 读取 `jump_cut.py` 的 JSON |
| `--cut-source removed_segments` | removed_segments | 批量窗口来源，也可用 `keep_segments` |
| `--rendered-cut-list` | 无 | 用 cut list 的 `keep_segments` 计算成片输出切点；与 `--cut-list` 互斥 |
| `--output-speed` | 1.0 | 成片的全局播放速度，用于源时长 → 输出时长换算 |
| `--output-offset` | 0 | 第一段素材前的片头/封面秒数 |
| `--output-dir` | 无 | 批量输出目录 |
| `--json` | 无 | 写出 manifest，记录窗口和 ffmpeg 命令 |

## 输出怎么看

- filmstrip 里如果切点前后主体位置突变、字幕跳帧、画面黑掉，回到 render config 或 cut list 调整。
- waveform 如果在切点附近被削成硬断，增大 `jump_cut.py --pad` 或提高 `--min-silence`。
- rendered 模式的 JSON 会为每张图记录 `boundary.output_time`、前后 source range 和被跳过的 `source_gap`，可直接定位原始 cut decision。
- 如果计算出的切点超过成片时长，脚本会报错并提示检查 `--output-speed` / `--output-offset`，不会静默生成错位复盘图。
- 如果 `render_qa.py` 报长静音但 waveform 对应片尾/转场自然留白，可以把 QA WARN 解释为可接受。

## 推荐接入点

在 daily 流水线里，先跑机器检查：

```bash
python3 scripts/render_qa.py output/day58_master.mp4 \
  --platform douyin \
  --json output/day58_master_qa.json
```

如果有 WARN/FAIL 或需要抽查单点，再跑：

```bash
python3 scripts/timeline_view.py output/day58_master.mp4 \
  --at <可疑秒数> \
  --radius 1.5 \
  --output output/verify/<name>.png
```

rough/jump cut 成片建议再补一次 `--rendered-cut-list`，按输出时间轴复核全部拼接边界，而不只查看源素材删除段。
