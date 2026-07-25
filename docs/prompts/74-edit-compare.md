# Edit Compare — 原片 vs 成片 source-time 对照

`edit_compare.py` 把原片和最终成片放进一条可播放的双栏审片视频：

- 左栏始终按原片时间连续播放。
- 右栏把最终成片像素投回对应的原片时间。
- cut list 删除的范围在右栏显示为黑屏。
- 音频默认保留原片音轨，所以听到的时间码与左栏一致。

这比只看 cut list 或静态切点图更适合回答：“这一段到底删了什么？”、“字幕、调色、B-roll 和画面裁切在最终成片里变成了什么？”

## 适用输入

- 单一原片。
- `rough_cut.py`、`jump_cut.py` 或 `srt_edit_plan.py` 输出的、按原片时间升序排列的 `keep_segments`。
- 已完成的最终渲染文件；不要传中间 plan 或代理视频。
- 可选的全局变速与片头 offset。

V1 会明确拒绝重叠、重排和越界的 `keep_segments`。多来源时间线、逐段不同速度、倒放或其他非线性映射不在本模式范围内；这些情况应使用 NLE/OTIO 时间线审查。

`--output-offset` 对应的片头，以及 final 在最后一个映射片段之后的片尾，没有 source-time 位置，因此不会出现在右栏；必须另外完整播放 final 或 review proxy。手机素材的 display rotation 会从 FFprobe metadata 读取，双栏尺寸按实际显示方向计算。

## 基本用法

```bash
python3 scripts/edit_compare.py \
  origin/talking.mp4 \
  output/day74_master.mp4 \
  --cut-list work/rough_cut.json \
  --output output/verify/day74_source_vs_final.mp4 \
  --report output/verify/day74_edit_compare.json \
  --markdown output/verify/day74_edit_compare.md
```

如果最终成片把所有保留段统一加速为 `1.25x`，并在片头加了 2 秒封面：

```bash
python3 scripts/edit_compare.py \
  origin/talking.mp4 \
  output/day74_master.mp4 \
  --cut-list work/rough_cut.json \
  --output-speed 1.25 \
  --output-offset 2.0 \
  --output output/verify/day74_source_vs_final.mp4 \
  --strict
```

只想先验证映射参数、写出待执行报告，不渲染视频：

```bash
python3 scripts/edit_compare.py \
  origin/talking.mp4 \
  output/day74_master.mp4 \
  --cut-list work/rough_cut.json \
  --output output/verify/day74_source_vs_final.mp4 \
  --dry-run
```

`--dry-run --strict` 返回 2，因为对比视频尚未生成；不加 `--strict` 时返回 0，方便只做规划。

## 自动验证

渲染完成后脚本会自动检查：

1. 输出宽度是否为单栏宽度的两倍，高度是否正确。
2. 输出时长是否与原片时间轴在一帧容差内一致。
3. 默认 source-clock 音轨是否存在。
4. 代表性的删除范围在右栏是否为黑屏。
5. 代表性的保留范围是否与最终成片对应 program frame 像素一致。

JSON 的 `verification.samples[]` 保存抽样 source/program 时间和像素指标。默认最多检查 12 个代表范围；`--sample-limit 0` 会检查全部范围。`summary.blocking > 0` 时，`pipeline_manifest.py` 会把 `edit_compare` 列为阻塞 gate。

## 人工复核顺序

1. 先看 JSON/Markdown 是否 `status: pass`。
2. 播放 MP4，重点查看右栏黑屏开始/结束处，确认删段完整且没有吞字。
3. 检查右栏保留段的字幕、调色、B-roll、章节卡和裁切是否是最终发布像素。
4. 如果映射整体错位，先校正 `--output-speed` / `--output-offset`；不要用调整像素阈值掩盖时间线错误。
5. 修正 cut list 或重新渲染 master 后，重新生成对比视频。

## 研究来源

- [WhiteTowerAI/cut-as-code 的 video-edit-compare skill](https://github.com/WhiteTowerAI/cut-as-code/blob/main/skills/video-edit-compare/SKILL.md)：最终交付像素回投到原片时间轴、删除区间置黑、自动验证。
- [nopefallacy/vertical-video-editing-skills](https://github.com/nopefallacy/vertical-video-editing-skills/blob/main/skills/video-editing/SKILL.md)：最终 render 必须配合 ffprobe 和 frame spot-check，而不只相信 preview。
- [znyupup/ai-video-editing-skill](https://github.com/znyupup/ai-video-editing-skill/blob/main/SKILL.md)：成片 QC 抽帧与浏览器 review 是可验证交付的一部分。

本项目复用既有 `keep_segments`、`--output-speed` 和 `--output-offset` 语义，使用 Python 标准库 + FFmpeg/FFprobe，不引入新的模型、云端服务或素材写入。
