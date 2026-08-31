# Encode Quality QA — 同时间线重编码画质损失门禁

用于回答一个很窄但重要的问题：**同一条视频经过压缩或转码后，相对参考 master 的像素损失是否越过当前交付门槛？**

它不会剪辑、重编码、上传或调用 provider。脚本只用本地 FFmpeg 的 `ssim` / `psnr` filter 完整解码并比较两条视频，输出 source-bound JSON 与 Markdown。

## 适用范围

适用：

- `delivery_encode.py` 生成的目标大小 H.264/AAC；
- 同构图的平台再次压缩件、审片 proxy 或 codec/preset/CRF A/B；
- 分辨率变化但显示画幅、时间线、内容保持不变的衍生件。

不适用：

- 中心裁切、cover/contain/blur、logo/字幕/overlay 变化；
- 调色、HDR → SDR tone-map、去噪、锐化、抠像或局部 AI 编辑；
- 变速、插帧、删段、补帧、重新排序或音画同步修复；
- 两条内容不同的视频。

这些处理会有意改变像素或时间线，SSIM/PSNR 可能把正确的创意变化当作损失，也可能因对齐错误给出无意义分数，应改用对应 A/B、framing、HDR、color、retiming 或人工 review gate。

## 分析

```bash
python3 scripts/encode_quality_qa.py analyze \
  output/master.mp4 \
  output/final_delivery.mp4 \
  --project-dir . \
  --output verify/encode_quality_qa.json \
  --markdown verify/encode_quality_qa.md \
  --strict
```

默认门槛：

| 指标 | 默认 | 作用 |
|---|---:|---|
| mean SSIM | `≥0.95` | 整体结构相似度 |
| P05 SSIM | `≥0.88` | 低尾部 5% 帧，避免平均值掩盖局部坏帧 |
| finite-frame mean PSNR | `≥35 dB` | 非完全相同帧的平均误差 |
| fps delta | `≤0.01` | 确认同时间线帧率 |
| duration delta | `≤1 frame` | 防止只比较共同前缀或错位尾帧 |

可用 `--min-mean-ssim`、`--min-p05-ssim`、`--min-mean-psnr-db`、`--fps-tolerance`、`--duration-tolerance-frames` 调整**明确的项目验收合同**。不要为了让失败文件通过而在测量后临时降阈值。

候选分辨率与参考片不同但显示画幅一致时，候选会用 Lanczos 缩放到参考显示尺寸，再统一 `setsar=1 / yuv420p`。报告会保留 warning；画幅不同则拒绝比较。

## 输出与复核

JSON 保存：

- reference / candidate 相对路径、SHA-256、字节数和媒体契约；
- thresholds、FFmpeg filter contract 与 canonical report id；
- `mean / P05 / minimum SSIM`；
- finite/infinite PSNR 帧统计；
- 默认 12 个最低 SSIM 帧的 frame number、timecode、SSIM 与 PSNR；
- blocker / warning / limitations。

Markdown 的最差帧时间码是人工复核入口。每个时间码都要：

1. 同时打开 reference 和 candidate；
2. 看文字、脸、渐变、运动边缘和细纹理；
3. 从时间码前后正常速度播放，而不是只看暂停帧；
4. 在实际目标屏幕上看完整候选文件；
5. 有可见 artifact 时提高码率、降低缩小幅度或调整编码器，再重新生成报告。

接近门槛会 WARN；低于门槛会阻断 `--strict`。完全相同的帧会得到 infinite PSNR，JSON 用 `null + psnr_infinite=true` 表示，不写非标准 `Infinity`。

## 现场验证与 manifest

```bash
python3 scripts/encode_quality_qa.py verify \
  --report verify/encode_quality_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require encode_quality_qa \
  --strict
```

`verify` 会重新读取两条视频，检查 SHA-256、大小和媒体契约，并现场重跑完整 SSIM/PSNR。下列情况都会 fail closed：

- reference/candidate 被替换、删除、改成 symlink 或现在指向同一文件；
- 阈值、算法合同、指标、summary、status 或 report id 漂移；
- fps、时长、显示画幅不再符合比较前提；
- 项目路径变化或 artifact 逃逸项目目录。

`--force` 也不能让 JSON/Markdown 通过同路径或 hard link 覆盖 reference/candidate。

## 指标边界

- SSIM/PSNR 是 full-reference 工程指标，不是主观画质、品牌审美或可读性批准。
- 本实现不测声音；继续运行 `render_qa.py`、`audio_master_report.py`、`audio_channel_qa.py` 与完整试听。
- 本实现不调用 `libvmaf`，因此不输出或声称 VMAF。需要 VMAF 时应使用带 `libvmaf` 的独立 FFmpeg 构建并建立新的、明确版本化的算法合同。
- 不同内容、色彩处理、时间线或画幅不能靠降低阈值“兼容”；应换正确的复核方法。
