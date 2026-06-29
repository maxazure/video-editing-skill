# Audio Sync 外录音频自动对齐

> 用相机/录屏里的 scratch audio 作为参考，把单独录制的 lav、无线麦、录音笔或手机音频对齐到视频时间线。

适用场景：

- 相机声音可用来对齐，但最终想用外录高质量音频。
- 录屏和麦克风分开录制，需要先估计延迟再替换音轨。
- 访谈、教程、播客切片里有单独收音文件，想在渲染前生成可审计同步计划。

不适用：

- 参考视频完全没有可听见的人声、拍手、键盘声或环境同步点。
- 两条音频内容不同，比如后期重新配音或不同语言配音。
- 长时间录制出现逐渐漂移。当前工具估计单一 offset，不做 time-stretch drift correction。

## 自动估计 offset

```bash
python3 scripts/audio_sync.py \
  --reference-media origin/camera.mp4 \
  --external-audio origin/lav.wav \
  --output work/audio_sync_plan.json \
  --markdown work/audio_sync_plan.md \
  --replace-output output/camera_lav_synced.mp4 \
  --max-offset 5 \
  --strict
```

输出 `audio_sync_plan.v1`：

- `alignment.offset_seconds`：正数表示延迟外录音轨；负数表示裁掉外录音轨开头。
- `alignment.confidence`：自动估计置信度。低于 `--min-confidence` 时 status 为 `review`。
- `replace_audio.command`：可复核的 FFmpeg 命令。默认只生成命令，不直接执行。
- `summary.blocking`：低置信度、缺文件等会阻塞 `pipeline_manifest.py`。

## 确认后替换音轨

先读 `work/audio_sync_plan.md`，必要时用波形或短 preview 复核。确认 offset 后再执行：

```bash
python3 scripts/audio_sync.py \
  --reference-media origin/camera.mp4 \
  --external-audio origin/lav.wav \
  --output work/audio_sync_plan.json \
  --markdown work/audio_sync_plan.md \
  --replace-output output/camera_lav_synced.mp4 \
  --apply \
  --strict
```

`--apply` 会 copy 原视频流、把第二路外录音频按 offset 延迟或裁剪后编码为 AAC，并用 `-shortest` 避免成片尾部多出黑屏/静音。

## 手动 offset

如果自动估计低置信度，但你已经通过拍手点或波形读出了偏移，可以跳过自动估计：

```bash
python3 scripts/audio_sync.py \
  --reference-media origin/camera.mp4 \
  --external-audio origin/lav.wav \
  --offset 0.18 \
  --output work/audio_sync_plan.json \
  --markdown work/audio_sync_plan.md \
  --replace-output output/camera_lav_synced.mp4
```

方向规则：

- `--offset 0.18`：外录音轨整体延后 180ms。
- `--offset -0.18`：外录音轨开头裁掉 180ms，让它更早进入视频时间线。

## 发布前 gate

如果项目里存在 `audio_sync_plan.json`，`pipeline_manifest.py` 会读取 `summary.blocking`。低置信度或缺文件时，发布清单会阻塞并提示先复核外录音频对齐。
