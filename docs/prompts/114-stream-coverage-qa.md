# Stream Coverage QA — 最终音视频轨覆盖门禁

最终 MP4 可以正常打开，容器总时长也可能看似正确，但视频轨或音频轨已经提前结束。常见原因包括静态图循环、`zoompan` / `xfade` 组合、字幕去除、remux、错误的 `-shortest`、转码或平台交付编码。

`stream_coverage_qa.py` 完整解码最终文件，再分别读取首条视频、音频流的 decoded frame PTS。报告会核对：

- FFmpeg 全量解码是否成功；
- 视频、音频首帧 PTS 与尾帧结束时间；
- 两条流的开头与结尾偏差；
- 每条流对容器时间线的头尾覆盖；
- 视频实际解码帧数与 `nb_frames`；
- 可选的预期总时长和预期视频帧数。

## 基本用法

```bash
python3 scripts/stream_coverage_qa.py analyze output/final.mp4 \
  --project-dir . \
  --output verify/stream_coverage_qa.json \
  --markdown verify/stream_coverage_qa.md \
  --strict

python3 scripts/stream_coverage_qa.py verify \
  --report verify/stream_coverage_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require stream_coverage_qa \
  --strict
```

如果 render plan 已定义确切交付时长和帧数，把它们写成硬门禁：

```bash
python3 scripts/stream_coverage_qa.py analyze output/final.mp4 \
  --project-dir . \
  --expected-duration 30 \
  --expected-video-frames 900 \
  --max-expected-duration-delta-ms 40 \
  --max-expected-frame-delta 0 \
  --output verify/stream_coverage_qa.json \
  --markdown verify/stream_coverage_qa.md \
  --strict
```

默认允许音视频开头相差 80 ms、结尾相差 100 ms、各流与容器头尾相差 100 ms。AAC priming/discard 可能让音频 `nb_frames` 与可解码帧数相差一帧，所以音频覆盖以 decoded PTS 和样本时长为准；视频会额外核对 `nb_frames`。确实需要无声交付时显式传 `--allow-no-audio`，报告会保留 warning。

## 修复与复核

发现视频轨提前结束时，回到发生问题的 scene/frame-count/filter graph 修复并重新渲染。音频轨提前结束时，检查混音时长、`apad` / trim、`-shortest` 和 mux 设置。不要通过放宽阈值掩盖秒级差异。

报告绑定成片 SHA-256、媒体合同、算法、阈值、decoded timeline digest、checks 与 canonical report id。任何渲染、remux、字幕烧录或交付编码都会让旧报告失效。门禁通过后仍需在 1× 完整播放确切交付文件；它不能判断口型同步、创意留白或结尾节奏。
