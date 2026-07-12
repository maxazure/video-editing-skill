# Review Proxy 低码率时间码审片视频

> 把已渲染的 master 或平台版本转成体积更小、可快速拖动、带可见时间码的审片 MP4；原文件不修改。

适用场景：

- 把整条视频发给客户、同事或下一位 agent 复核，不想传几百 MB 的 master。
- 反馈需要精确引用时间，例如“`00:00:18.400` 的字幕遮脸”。
- `timeline_view.py` 的局部 filmstrip 不够，需要完整播放确认节奏、字幕、B-roll 和声音。

不适用：

- 不能把 review proxy 当发布文件；它是低分辨率、较高 CRF 的审片副本。
- 它不会替代 `render_qa.py` 或 `audio_master_report.py`；最终门禁仍应针对 master / 平台导出文件运行。

## 生成审片代理

```bash
python3 scripts/review_proxy.py output/day66_master.mp4 \
  --output verify/day66_review_proxy.mp4 \
  --manifest verify/day66_review_proxy.json \
  --markdown verify/day66_review_proxy.md
```

默认配置：

- 最大高度 720px，不放大小于 720p 的源视频。
- 24fps、H.264 `libx264`、`veryfast`、CRF 28。
- AAC 96k、双声道；无音轨素材会保留为 video-only 并写 warning。
- 左上角烧入 `REVIEW PROXY` 和 elapsed timecode。
- MP4 写 `+faststart`，便于网页/聊天工具边下边播与快速 seek。

自定义：

```bash
python3 scripts/review_proxy.py output/day66_master.mp4 \
  --max-height 540 \
  --fps 15 \
  --crf 30 \
  --label "CLIENT REVIEW V3"
```

只检查命令和 artifact，不执行 FFmpeg：

```bash
python3 scripts/review_proxy.py output/day66_master.mp4 --dry-run
```

不需要可见时间码时可加 `--no-timecode`。建议至少保留 label，避免低码率代理被误当成正式成片。

## 审片顺序

1. 先在 review proxy 里完整播放，按可见时间码记录节奏、字幕、遮挡和声音问题。
2. 对具体疑点用 `timeline_view.py --at <seconds>` 生成 filmstrip + waveform 深查。
3. 修改后重新跑 `render_final.py --versioned-output`，再对新 master 跑 `render_qa.py` / `audio_master_report.py`。
4. 发布只使用通过 QA 的 master 或 `multi_export.py` 输出，不使用 `*_review_proxy.mp4`。

`review_proxy.json` 会被 `pipeline_manifest.py` 和 `review_dashboard.py` 发现，但默认不是发布 blocker；需要把客户审片代理作为显式交付项时可用 `--require review_proxy`。
