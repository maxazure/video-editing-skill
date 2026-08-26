# Generated Motion Window 生成视频有效运动窗口

适用于 Dreamina/即梦、Seedance、Veo、Sora、LTX、Wan 等短生成视频已经下载并通过基础视觉复核，但片头先冻住、动作延迟开始、片尾无意义停住，或稀疏 contact sheet 看不出真实运动覆盖的场景。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。生成视频仍按项目现有 provider、额度审批和任务台账执行。

这个脚本只用本地 FFmpeg `freezedetect` 测量全帧相似区间，不调用 provider、不上传素材、不消耗 credits。它不能判断“动作是否有意义”，也不能检查人物身份、手部/物理、产品几何或故事质量；先完成 `generated_clip_review.py`，再把 motion window 作为时间维度的补充证据。

## 1. 分析单条生成片

多条生成片逐片运行，并给每条使用独立 JSON/Markdown：

```bash
python3 scripts/generated_motion_window.py analyze \
  work/generated_video/shot_001.mp4 \
  --project-dir . \
  --output work/generated_motion_window/shot_001.json \
  --markdown work/generated_motion_window/shot_001.md
```

默认配置：

- `--min-freeze 0.25`：连续全帧相似至少 0.25 秒才记为 freeze。
- `--freeze-noise 0.003`：FFmpeg 全帧变化容差。
- `--min-active 0.25`：推荐窗口至少要有 0.25 秒 active motion。

输出绑定源片绝对路径、SHA-256、大小、duration、fps、尺寸、rotation、codec、pixel format 和音轨状态，并保存：

- `freezes[]`：检测到的全帧冻结区间；
- `active_intervals[]`：freeze 补集，即时间上存在全帧变化的区间；
- `leading_freeze` / `trailing_freeze`：片头/片尾冻结证据；
- `interior_freezes[]`：中间 stop-start 风险；
- `recommendation`：`trim | keep | reject_or_repair` 与建议 in/out。

刚 analyze 的计划固定为 `blocked`，直到人工完整播放源片并确认。contact sheet 只回答“抽样帧里有什么”，不能回答“动作什么时候开始”。

## 2. 完整播放并确认

先用 1× 完整播放源片，确认推荐入点已经进入真实动作，内部没有不可修复的“长时间冻住 → 突然动一下 → 又冻住”。再选择：

### 接受推荐裁切

```bash
python3 scripts/generated_motion_window.py confirm \
  work/generated_motion_window/shot_001.json \
  --decision trim \
  --reviewed-by editor \
  --note "完整 1× 已看；入点在动作内，片尾冻结无叙事作用" \
  --markdown work/generated_motion_window/shot_001.md
```

`trim` 默认使用 recommendation；如需人工修正，显式传 `--start` / `--end`。起点和终点必须落在 active interval 内，不能落在已检测 freeze 中，且保留长度不能短于 `--min-active`。

### 有意保留完整片段

```bash
python3 scripts/generated_motion_window.py confirm \
  work/generated_motion_window/shot_001.json \
  --decision keep \
  --reviewed-by editor \
  --note "开场 0.4 秒产品静置是有意的视觉建立镜头"
```

有 edge freeze 的 `keep` 会保留 warning，但不阻塞。必须在 note 里解释这是真实创意决定，而不是把 detector 当成误报后跳过。

### 拒绝片段

```bash
python3 scripts/generated_motion_window.py confirm \
  work/generated_motion_window/shot_001.json \
  --decision reject \
  --reviewed-by editor \
  --note "内部多次 stop-start，裁边不能恢复连续动作"
```

`reject` 继续阻塞，回到 provider 重生或换素材；不要用快切、转场或音效掩盖破损动作。

## 3. 应用裁切并 live verify

`trim` 决定在 apply 前保持阻塞。apply 永不覆盖原片，只生成新 H.264/AAC、`yuv420p` 工作副本；先写同目录临时文件，核对时长/尺寸/fps/音轨、完整解码后才原子提升：

```bash
python3 scripts/generated_motion_window.py apply \
  work/generated_motion_window/shot_001.json \
  --output work/generated_motion_window/shot_001-active.mp4 \
  --markdown work/generated_motion_window/shot_001.md

python3 scripts/generated_motion_window.py verify \
  work/generated_motion_window/shot_001.json \
  --strict
```

帧准确裁切会重编码工作副本，因此完整 1× 播放新文件，检查第一帧、最后一帧、内部 stop-start、音频起止和人物/产品完整性。随后重新运行：

```bash
python3 scripts/generated_clip_review.py prepare ...
python3 scripts/render_qa.py output/final.mp4 --json verify/final_qa.json
```

如果已有 sequence review、最终 master、字幕、cue、approval receipt 或 publish package，换成 active working copy 后这些下游 artifact 都要重建。

## 4. Pipeline gate

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require generated_motion_window \
  --strict
```

manifest 会逐个 live verify `work/generated_motion_window/*.json`。以下情况 fail closed：

- 尚未人工 confirm；
- 批准 trim 但尚未 apply；
- source、freeze evidence、decision、plan id 或 output 漂移；
- 项目外路径、symlink、原片/计划/output 冲突；
- trim 起止落在 freeze、长度不足或媒体契约不匹配；
- 输出 hash/大小/codec/pixel format/时长/尺寸/fps/音轨发生变化。

`reviewed_by` 只是本地标签，不是身份认证、数字签名或防抵赖证明。SHA-256 只能发现字节漂移，也不证明动作、人物或产品质量合格。
