# 81 — Shot Color QA 镜头色彩 / 曝光门禁

`scripts/shot_color_qa.py` 在**已渲染 master 或平台导出文件**上运行 FFmpeg `signalstats`，按镜头聚合亮度、对比、色度、饱和度和 broadcast-range 指标，输出 `shot_color_qa.v1` JSON 与 Markdown 复核表。它只读视频，不调色、不重编码、不上传。

## 什么时候用

- 多机位、B-roll、生成视频和录屏混剪后，担心相邻镜头突然偏亮、偏暗或偏色；
- 使用 `color_grade.py` / LUT 后，需要检查最终编码是否仍有持续过暗、过亮或越界像素；
- 发布前想把色彩/曝光风险纳入 `pipeline_manifest.py`；
- 需要一份带时间码的复核清单，而不是凭缩略图扫完整条视频。

## 推荐命令

让脚本自己检测镜头边界：

```bash
python3 scripts/shot_color_qa.py output/day81_master.mp4 \
  --output output/verify/day81_shot_color_qa.json \
  --markdown output/verify/day81_shot_color_qa.md \
  --strict
```

如果已经跑过 `scene_boundaries.py`，复用同一份场景时间轴：

```bash
python3 scripts/shot_color_qa.py output/day81_master.mp4 \
  --scene-boundaries work/scene_boundaries.json \
  --output output/verify/day81_shot_color_qa.json \
  --markdown output/verify/day81_shot_color_qa.md \
  --strict
```

提供的 `scenes[]` 必须从 `0` 连续覆盖到成片结尾；重叠、明显缺口或超出媒体时长会 fail closed，避免一段视频静默漏检。

## 默认判断

脚本默认每秒抽 2 帧，缩小到 320px 宽后用每个镜头的中位数抵抗偶发闪帧，并尽量跳过切点前后 0.15 秒：

| 指标 | 默认 | 处理 |
|---|---:|---|
| median `YAVG` | ≤32 / ≥220 | 极暗 / 极亮，默认 WARN |
| median `YHIGH-YLOW` | ≤18 | 低对比，WARN |
| median `SATAVG` | ≥95 | 高饱和，WARN |
| adjacent `YAVG` delta | ≥45 | 相邻镜头亮度跳变，WARN |
| adjacent U/V distance | ≥55 | 相邻镜头色度跳变，WARN |
| median `BRNG` | >1% | 非 full-range 流默认 BLOCK |
| 场景无有效 sample | 任意 | BLOCK |

亮度/色度跳变默认只 WARN：地点、日夜、实拍与图形、黑场或刻意 look 本来就可能不同。需要把极暗/极亮或所有跳变变成当前项目的强人工门禁时，分别加 `--fail-on-extremes` / `--fail-on-jumps`。只有明确接受当前交付范围时才用 `--ignore-broadcast-range`；该 override 会留在 JSON `params` 和 shot flag 中。

## 怎么复核

先看 Markdown 的 Shot Metrics 和 Flagged Cuts，再复制其中生成的 `timeline_view.py --at <cut>` 命令查看切点前后 filmstrip + waveform。确认问题后应回到源 `render_config`、逐镜头调色或 `color_grade.py` 重渲染；不要给已经压缩完成的 master 反复叠加调色造成多次编码。

发布门禁：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require shot_color_qa \
  --strict
```

报告一旦存在，`summary.blocking > 0` 即使没有显式 `--require` 也会阻塞；普通视觉 WARN 不阻塞 manifest，必须结合正常速度 master 判断。

## 边界

- 这是压缩后、下采样的 YUV 统计，不是校准过的 waveform、vectorscope、HDR 测量或显示器 proof；
- U/V 位移只表示色度差异，不等于白平衡错误，也不做肤色线判断；
- 不知道导演意图，不给审美分、留存率或“电影感”分数；
- HDR、log、wide-gamut 或明确 full-range 项目需要按交付规范重设阈值并人工使用专业 scopes；
- 默认适合本项目常见的 SDR 社媒 H.264/H.265 输出。
