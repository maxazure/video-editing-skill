# Multicam Sync — 多机位可逆同步

> 两台以上相机、手机、录屏或独立录音设备拍到同一事件时，先统一参考时间线，再进入选片、PIP、NLE 或多机位切换。脚本只写计划和可选短预览，绝不改写原片。

## 适用与边界

适用：

- 双机位访谈、播客、圆桌、课程、活动或产品演示。
- 相机开机时间不同，但各路录到了相同人声、拍手、键盘声或现场音乐。
- 专业相机有多条音轨，需要找出真正有声的 scratch mic。
- 某一路没有音轨，但已经从拍板、timecode 或人工波形得到 offset。

不适用：

- 每路录到的是不同内容，或其中一路是后期重新配音。
- 素材中间暂停/重启、剪过、拼过，需要多个不连续 offset。
- 只靠固定 offset 无法解释的长片时钟漂移。
- 想直接自动切换说话人机位。V1 只做同步准备，不做 active-speaker 自动剪辑。

## 自动对齐

```bash
python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mp4 \
  --angle origin/cam-b.mp4 \
  --angle origin/cam-c.mp4 \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --preview-output output/verify/multicam_sync_preview.mp4 \
  --apply-preview \
  --strict
```

默认行为：

- 参考机位 offset 固定为 `0`。
- 其他机位用 8 kHz、40 ms 音频能量包络做相关，默认搜索 `±60s`。
- 多音轨媒体会在中段跑 `volumedetect`，选择 `mean_volume` 最大的逻辑音轨 `0:a:N`。
- 每路输出自己的 offset、score/confidence、reference/source coverage。
- 计算所有机位都实际有画面的 `common_overlap_in_reference`。
- 三路以上自动素材会直接比较非参考机位，验证 offset 的传递一致性。
- 不传 `--apply-preview` 时只记录 FFmpeg 命令，不生成视频。

## offset 方向

`alignment.offset_seconds` 表示“该机位的 `t=0` 落在参考时间线的什么位置”：

- `+1.20`：该机位比参考晚 1.2 秒开始；参考 `1.20s` 对应它的 `0s`。
- `-1.20`：该机位在参考开始前已经录了 1.2 秒；参考 `0s` 对应它的 `1.20s`。

消费时统一用：

```text
source_local_time = reference_time - offset_seconds
```

不要把单路 `audio_sync.py` 的“延迟/裁剪外录音轨”说明机械套到多机位画面上。网格预览会从公共 overlap 开始，分别计算每路 local seek。

## 音轨选择

自动选择适合多数 MP4/手机素材，也覆盖 FX3/FX6 等可能把现场麦克风放在非首轨的相机。Markdown 会列出实际音轨 index；JSON 的 `audio_stream.candidates[]` 保存候选轨的 `mean_volume_db`。

已知正确音轨时直接覆盖：

```bash
python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mxf \
  --angle origin/cam-b.mxf \
  --audio-stream "origin/cam-a.mxf=2" \
  --audio-stream "origin/cam-b.mxf=3" \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --strict
```

最响不等于最好：削波、风噪、机内自动增益或远距离混响都可能误导选择。最终仍要听预览。

## 手工 offset 与无音轨机位

```bash
python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mp4 \
  --angle origin/drone.mp4 \
  --manual-offset "origin/drone.mp4=12.44" \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --preview-output output/verify/multicam_sync_preview.mp4 \
  --apply-preview \
  --strict
```

手工 offset 不需要该机位有音轨，但报告会保留 `manual_offset_not_independently_verified` / `manual_offset_without_audio`。它能通过 gate，是因为用户显式提供了位置，不代表脚本验证过它。

## Pairwise 一致性

假设参考推导：

- cam-b offset `+0.20s`
- cam-c offset `+0.50s`

那么 cam-b 直接对 cam-c 应得到约 `+0.30s`。默认差异超过 `0.08s` 时，两路都进入 `review`。这能发现坏参考音、重复节奏、错文件或错误相关峰。

需要临时跳过时可加 `--no-pairwise-check`，但必须解释为什么参考机位可信。阈值可用 `--pairwise-threshold 0.12` 调整；不要为了让 gate 变绿而盲目放宽。

## 对齐预览

`--preview-output` 只在计划中生成命令；`--apply-preview` 才实际渲染。预览：

- 每路缩放到 480×270 cell，最多两列排列。
- 从公共 overlap 起点读取每路对应 local time。
- 使用参考机位已选择的音轨。
- H.264 CRF 26 / AAC 128k / `+faststart`，只用于同步复核，不是发布成片。

重点看：

1. 拍手、关门、键盘、屏幕点击等瞬时动作。
2. 说话人口型和辅音起点。
3. 预览中段是否仍同步。
4. 30 分钟以上素材的结尾是否出现漂移。

## 长片与搜索范围

V1 只估计一个固定 offset，不测 sample-clock drift，也不做 `atempo`。任一路时长达到 30 分钟时会写 `clock_drift_not_measured_for_long_form`。

如果机位开机相差超过一分钟：

```bash
python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mp4 \
  --angle origin/cam-b.mp4 \
  --max-offset 300 \
  --max-probe-seconds 0 \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --strict
```

`--max-probe-seconds 0` 会读取完整音频，长片可能明显变慢。偏移接近搜索边界时先扩大 `--max-offset` 重跑，不要直接接受。

## Artifact 与发布 gate

`multicam_sync_plan.v1` 主要字段：

- `angles[]`：每路 media、audio stream、method、alignment、coverage、warnings、status。
- `common_overlap_in_reference`：所有机位共同覆盖的参考时间范围。
- `pairwise_consistency`：直接 offset、参考推导 offset、divergence 和 blocker。
- `preview.command` / `applied` / `output_exists`。
- `source_safety`：原片是否被修改/重编码。
- `summary.blocking`：低置信度、缺文件、无公共 overlap 或 pairwise 不一致数量。
- `summary.preview_failed = 1` 或 `preview_render_failed`：对齐计划可能有效，但预览产物渲染失败；修复渲染问题并重新生成预览后再继续。

项目里存在该 artifact 时，`pipeline_manifest.py` 会自动发现 `multicam_sync` 类别。需要把它设为显式必需项：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require multicam_sync \
  --target-stage publish_ready \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

计划通过后再把 offset 接入 Resolve/Premiere/FCP、OTIO/FCPXML 或专门的多机位渲染流程；本脚本不自动选择机位，也不创建 NLE 原生 multicam clip。
