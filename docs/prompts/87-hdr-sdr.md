# HDR → Rec.709 SDR Delivery — PQ/HLG 社媒交付

用于 iPhone HDR、HDR10/PQ 或 HLG 素材需要交付给小红书、抖音、视频号、普通网页播放器或 SDR 客户端时，避免只把 10-bit 降成 8-bit、却保留 HDR transfer，导致平台重编码后过曝、饱和度异常或暗部错误。

本流程只生成 **Rec.709 SDR derivative**。它不做 Dolby Vision/HDR10+ 母版、不创造真实 HDR，也不替代人工调色。

## 1. 生成 source-bound 计划

```bash
python3 scripts/hdr_sdr.py plan output/master_hdr.mp4 \
  --delivery output/master_sdr.mp4 \
  --output work/hdr_sdr_plan.json \
  --markdown work/hdr_sdr_plan.md
```

计划会读取并绑定：

- 源文件绝对路径、大小和 SHA-256；
- 时长、显示尺寸、fps、音轨、codec、pixel format 和 bit depth；
- `color_primaries`、`color_transfer`、`color_space`、`color_range` 和 HDR side data；
- 固定 Hable tone-map、100-nit nominal peak、BT.709 limited-range 输出契约。

只接受两类有明确元数据的输入：

- PQ / HDR10：`color_transfer=smpte2084`；
- HLG：`color_transfer=arib-std-b67`。

同时要求 BT.2020 primaries 和 BT.2020 matrix。缺失/未知元数据、普通 BT.709 SDR 或互相矛盾的 color tags 会直接拒绝，不能靠猜测继续。

## 2. FFmpeg 依赖门禁

正确转换需要 FFmpeg 同时提供 `zscale` 和 `tonemap`：

```bash
ffmpeg -hide_banner -filters | grep -E 'zscale|tonemap'
```

计划会在缺滤镜时写 blocker，`apply` 不会开始编码。不要用 `format=yuv420p` 或裸 `tonemap` 作为静默回退：`tonemap` 必须在 linear-light floating-point frames 上运行，并在输出端重新写 BT.709 transfer/matrix/range。

## 3. 应用与原子提升

```bash
python3 scripts/hdr_sdr.py apply work/hdr_sdr_plan.json
```

`apply` 使用固定链路：

```text
zscale → linear float → BT.709 primaries → Hable tonemap → BT.709 transfer/matrix/limited range → yuv420p
```

输出为 H.264/AAC MP4，并显式写入：

```text
color_primaries=bt709
color_transfer=bt709
color_space=bt709
color_range=tv
```

脚本只先写交付目录里的临时 MP4。临时文件必须通过容器、codec、pixel format、尺寸、fps、时长、音轨、四项 color tag 和 FFmpeg `-xerror` 全长解码，才会原子提升到正式路径。默认拒绝覆盖源片、plan、symlink 或已有交付件；确实需要替换时显式加 `--force`。

## 4. Live verify 与发布门禁

```bash
python3 scripts/hdr_sdr.py verify work/hdr_sdr_plan.json

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require hdr_sdr_plan \
  --strict
```

`verify` 会重新读取源片和交付件，重算 hash、媒体/色彩契约、canonical settings、plan id 和 full-decode receipt。源片被替换、交付件被修改、color tags 漂移或 stored status 被手改都会阻塞。

## 5. 必须人工复核

技术验证通过后，仍需在可信 SDR 显示器上完整看完，并重点检查：

1. 肤色没有偏红、发灰或霓虹感；
2. 天空、灯光、白色 UI 和高光 roll-off 没有硬剪；
3. 阴影没有压死，黑位与 SDR 播放器一致；
4. 渐变、天空、雾和纯色背景没有明显 banding；
5. Dolby Vision/HDR10+ 来源的动态元数据被丢弃后，逐场景亮度仍可接受。

最后对 **SDR 交付件本身**重新运行 `render_qa.py`、`shot_color_qa.py`，并创建新的 `approval_receipt.py` 收据。HDR master 的旧审批不能证明 SDR derivative 已经人工看过。

## 边界

- 固定 Hable 是安全技术默认，不是唯一审美正确的 tone-map；
- 不保留 Dolby Vision RPU 或 HDR10+ 动态 metadata；
- 不处理 log/raw camera gamut，也不猜未知 color tags；
- 不做 HDR→HDR、SDR→HDR、ACES/OCIO 或校准显示器 proof；
- 全长解码与 BT.709 tags 只证明技术可读和标记正确，不证明感知画质已批准。
