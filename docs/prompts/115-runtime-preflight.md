# Runtime Preflight — 本机视频工具链能力门禁

## 什么时候用

- 开始新的本地剪辑、渲染或 QA 项目时。
- 任务需要烧录字幕、HDR → SDR、防抖或 Remotion，担心本机 FFmpeg/Node 组件不完整时。
- 报错提到 `No such filter`、`Unknown encoder`、找不到 `ffmpeg` / `ffprobe` / `node` / `npx` 时。
- 切换电脑、FFmpeg 安装、虚拟环境或 Node 版本后，需要确认旧环境报告仍然有效时。

脚本只读取命令版本以及 FFmpeg `-filters` / `-encoders` 清单，不打开媒体、不渲染、不调用 GPU、不上传素材，也不提交生成任务。

## Workflow profiles

| profile | 覆盖范围 |
|---|---|
| `media_io` | Python、FFmpeg、FFprobe；适合 probe、抽取或只转写 |
| `core_edit` | 在基础命令上检查 libx264/AAC，以及主渲染链使用的 scale/crop/overlay/concat/aresample/loudnorm |
| `captions` | FFmpeg `subtitles` / libass 烧录能力 |
| `qa` | black/freeze/silence、EBU R128、signalstats、SSIM/PSNR 和 waveform filters |
| `hdr_sdr` | `zscale + tonemap` |
| `stabilization` | `vidstabdetect + vidstabtransform`，或明确的 `deshake` fallback |
| `remotion` | `node + npx` |

profile 可以重复传入。每项状态固定为：

- `available`：命令成功返回版本，或已从可解析的 FFmpeg 清单中找到组件。
- `missing`：PATH 中没有命令，或可解析的清单明确没有组件。
- `unknown`：命令/清单超时、失败或无法解析。它不会被误报成 missing，也不会放行。

## 用法

```bash
python3 scripts/runtime_preflight.py list-profiles

python3 scripts/runtime_preflight.py analyze \
  --profile core_edit \
  --profile captions \
  --profile qa \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict

python3 scripts/runtime_preflight.py verify \
  --report work/runtime_preflight.json \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require runtime_preflight \
  --strict
```

HDR 或防抖任务只追加对应 profile。Remotion 项目追加 `--profile remotion`。只做转写/抽流时选 `media_io`，避免把 libx264、字幕或 QA filters 误设为无关 blocker。

`analyze` 输出 `runtime_preflight.v1`，绑定所选 profiles、Python/命令版本、FFmpeg listing 状态、逐能力结果、profile 结果、修复建议和 canonical `report_id`。`verify` 用原 profiles 与 timeout 现场重跑；版本、组件状态或报告内容变化都会让旧报告失效。`edit_brief_plan.py` 会按任务自动选择 profile，并把本步骤排在媒体处理前。

## 边界

- listing 中出现组件只能证明当前 build 声明支持；不能证明真实素材、字体、驱动、GPU session、像素格式或硬件编码一定成功。
- `stabilization` 只要求两种后端至少一条可用；最后选中的 exact backend 仍由 `video_stabilization.py plan` 固定。
- 报告不替代 `edit_preflight.py` 的项目输入检查、真实媒体 probe、完整解码、字幕像素复核、完整 1× 视听审片或最终交付 QA。

## 可直接复制的提示词

```text
开始剪辑前，先运行 runtime_preflight.py。按任务选择 media_io/core_edit/captions/qa/hdr_sdr/stabilization/remotion profile，把 JSON 和 Markdown 存进 work/。missing 或 unknown 都要停止并给出修复动作；环境变化后重新 verify，再继续媒体处理。
```
