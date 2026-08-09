# Target-size Delivery Encode（目标大小交付编码）

当平台、客户系统、邮件或即时通讯明确要求“视频必须小于 10/20/100 MB”时，使用 `delivery_encode.py`。它不会修改 master，也不依赖 CRF 猜文件大小；它先按时长和音频预算计算两遍 H.264 目标码率，再对最终字节做技术验证。

## 1. 创建 source-bound 计划

```bash
python3 scripts/delivery_encode.py plan \
  output/day85_master.mp4 \
  --delivery output/day85_share.mp4 \
  --max-size-mib 18 \
  --max-width 1080 \
  --max-height 1920 \
  --fps 30 \
  --output work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md
```

`MiB` 是 1,048,576 bytes。计划会记录：

- source 的绝对路径、SHA-256、大小、时长、显示尺寸、fps、音频和 codec；
- 硬大小上限、6% 容器余量、视频/音频码率、输出尺寸和 CFR；
- 两遍 `libx264` + AAC + `yuv420p` + `faststart` 命令预览；
- 人工正常速度复核清单。

刚生成的计划显示 `blocked` 是预期行为，因为交付编码还没应用。目标太小、需要把视频码率压到 150 kbps 以下时，`plan` 会直接拒绝；应缩短视频、降低尺寸/fps，或提高大小上限，不要绕过计算结果。

## 2. 应用并验证

```bash
python3 scripts/delivery_encode.py apply \
  work/delivery_encode_plan.json \
  --markdown work/delivery_encode_plan.md

python3 scripts/delivery_encode.py verify \
  work/delivery_encode_plan.json
```

`apply` 会：

1. 重新检查 source hash 和规范化计划；
2. 检查目标目录的可用空间；
3. 在交付文件同目录写临时 MP4，并使用临时 passlog；
4. 检查硬大小上限、MP4/H.264/AAC、`yuv420p`、尺寸、fps、时长和音频；
5. 对临时文件运行完整 FFmpeg decode；
6. 全部通过后才原子替换最终交付路径，并把 output SHA-256 写回计划。

已有交付文件默认拒绝覆盖。只有确认目标路径正确时才给 `apply` 加 `--force`；即使加了 `--force`，源文件、计划文件和 symlink 仍不能被覆盖。

## 3. 发布前复核

完整解码通过只代表技术上可读取，不代表压缩画质可接受。必须以正常速度看完整交付文件，重点检查：

- 小字号字幕、界面文字和细线；
- 人脸、头发、渐变和暗部色块；
- 快速运动、粒子、树叶和水面；
- 口型同步、爆音、音乐瞬态和结尾音频；
- 平台再次压缩后可能恶化的画面。

随后对交付文件运行现有 QA，并绑定最终字节：

```bash
python3 scripts/render_qa.py output/day85_share.mp4 \
  --platform douyin \
  --json verify/day85_share_qa.json

python3 scripts/approval_receipt.py create \
  --project-dir . \
  --artifact output/day85_share.mp4 \
  --artifact verify/day85_share_qa.json \
  --approved-by "<reviewer label>" \
  --output verify/approval_receipt.json \
  --markdown verify/approval_receipt.md
```

如果 `verify` 报 source/output hash、stored summary、设置或 decode receipt 漂移，不要手改 plan id；重新从当前 master 建计划并编码。`pipeline_manifest.py --require delivery_encode_plan --strict` 可把这份交付契约设为发布必需 gate。
