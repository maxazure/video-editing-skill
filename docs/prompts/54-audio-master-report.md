# Audio Master Report 成片响度报告

`scripts/audio_master_report.py` 用 FFmpeg `ebur128` + `silencedetect` 检查最终成片音频是否适合发布。它只读文件，不重写媒体、不上传、不调用 provider；适合接在 `render_qa.py` 之后，作为发布前 audio gate。

## 什么时候用

- 成片已经渲染完，想确认口播响度是否接近 -16 LUFS。
- 视频听感忽大忽小、过响、爆峰，想用可审计数据定位。
- 发布包前希望 `pipeline_manifest.py` 把不合格 audio master report 当作阻塞项。

## 常用命令

```bash
python3 scripts/audio_master_report.py output/day58_master.mp4 \
  --output output/day58_audio_master_report.json \
  --markdown output/day58_audio_master_report.md
```

严格门禁：

```bash
python3 scripts/audio_master_report.py output/day58_master.mp4 \
  --target-lufs -16 \
  --tolerance 2 \
  --max-true-peak -1 \
  --max-lra 18 \
  --max-silence-seconds 3 \
  --output output/day58_audio_master_report.json \
  --markdown output/day58_audio_master_report.md \
  --strict
```

## 输出

- `version: audio_master_report.v1`
- `measurements.integrated_lufs`
- `measurements.true_peak_dbfs`
- `measurements.lra_lu`
- `silence.segments[]`
- `checks[]`
- `summary.blocking`

默认目标适配社媒口播短视频：-16 LUFS、true peak 不高于 -1 dBFS、LRA 不超过 18 LU、长静音总量不超过 3 秒。`--strict` 会在 `summary.blocking > 0` 时返回退出码 2。

## 接入日常流水线

```bash
python3 scripts/render_qa.py output/day58_master.mp4 \
  --platform douyin \
  --json output/day58_master_qa.json \
  --review-dir output/verify/day58_qa

python3 scripts/audio_master_report.py output/day58_master.mp4 \
  --output output/day58_audio_master_report.json \
  --markdown output/day58_audio_master_report.md \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

如果响度失败，优先回到 `render_final.py` 的默认响度链路重新渲染，不要把已经压缩过的 master 反复二次压缩。必要时用报告里的 `suggested_filter` 作为排查线索。
