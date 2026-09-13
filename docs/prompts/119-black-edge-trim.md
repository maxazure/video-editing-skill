# Edge-black Trim — 首尾黑场安全裁切

用于清理采集卡、相机预录、转码或导出产生的片头/片尾黑场。脚本只处理接触源时间线两端的黑画面；中间黑场可能是转场、标题或叙事停顿，会完整保留。

## 适用范围

- 开头或结尾有一段黑屏，需要生成新的工作副本。
- 希望先查看检测区间，再决定是否裁切。
- 黑场可能仍有对白、音乐或环境声，需要避免按画面单独误删。

输入应先成为 progressive CFR SDR 工作副本。VFR 先用 `frame_rate_conform.py`，HDR/BT.2020/>8-bit 先用 `hdr_sdr.py`，交错或 telecine 先用 `interlace_conform.py`。

## 1. 建立 source-bound 计划

```bash
python3 scripts/black_edge_trim.py plan origin/capture.mp4 \
  --delivery work/capture-edge-trimmed.mp4 \
  --edge-proof verify/capture-edge-proof.mp4 \
  --audio-policy silent_only \
  --project-dir . \
  --output work/black_edge_trim_plan.json \
  --markdown work/black_edge_trim_plan.md
```

默认参数：

- `blackdetect`: `d=0.25 / pic_th=0.98 / pix_th=0.10`；
- 只接受起点在 `0.10s` 容差内或终点落在片尾 `0.10s` 容差内的黑场；
- 每个视觉边界保留 `0.08s` 黑场 padding，避免吃掉第一个或最后一个可见帧；
- 有音轨时运行 `silencedetect=noise=-45dB:d=0.15`，默认要求实际移除范围至少 95% 被静音覆盖；
- proof 在每个拟议切点的内容侧多保留 `0.75s`；两个窗口重叠时合并成一段。

计划绑定 source SHA-256、完整 decoded cadence、媒体合同、黑场/静音区间、确切裁切范围、proof windows、检测参数和 canonical plan id。没有可裁的首尾黑场，或 `silent_only` 下音频覆盖不足时，计划保持阻塞。

确知黑画面里的声音也应删除时，可以重新 plan 并显式使用 `--audio-policy allow_audible`。该模式会保留 warning；必须在 proof 与完整成片里听审，不能把 override 当成自动批准。

## 2. 渲染与技术验证

```bash
python3 scripts/black_edge_trim.py apply \
  work/black_edge_trim_plan.json \
  --markdown work/black_edge_trim_plan.md
```

apply 对音画使用同一 source-time `trim/atrim`，重置 PTS，按源精确 rate 输出 CFR、SAR 1:1、H.264/yuv420p；有音轨时输出 48 kHz stereo AAC。临时交付件需通过尺寸、方向、帧率、decoded cadence、目标时长、音画首尾和 `ffmpeg -xerror` 完整解码才会原子提升。

随后脚本从原片渲染正常速度 edge proof。proof 会保留拟议删除区域和紧邻内容，便于识别黑标题、淡入淡出、首尾口播或音乐；proof 也要通过媒体合同与完整解码。

## 3. 人工复核

完整 1× 播放 `work/capture-edge-trimmed.mp4` 和 `verify/capture-edge-proof.mp4`。确认首个/最后一个可见帧、内容覆盖和音频起止后运行：

```bash
python3 scripts/black_edge_trim.py confirm work/black_edge_trim_plan.json \
  --reviewed-by editor \
  --note "完整播放成片与原片首尾 proof，标题、淡入淡出、口播和音乐均未误删" \
  --full-playback completed \
  --proof-playback completed \
  --first-visible-frame pass \
  --last-visible-frame pass \
  --content-coverage pass \
  --audio-continuity pass \
  --markdown work/black_edge_trim_plan.md
```

无音轨素材把 `--audio-continuity` 设为 `not_applicable`。`fail`、`unobservable`、未完整播放或空 note 都不会放行。

## 4. Live gate

```bash
python3 scripts/black_edge_trim.py verify \
  work/black_edge_trim_plan.json --strict

python3 scripts/pipeline_manifest.py --project-dir . \
  --require black_edge_trim_plan --strict
```

verify 会重跑源媒体 probe、完整 cadence、`blackdetect` 与 `silencedetect`，再核对输出/proof SHA-256、媒体合同、完整解码 receipt 和人工结论。源文件、检测参数、区间、输出、proof 或 review 任一漂移都会让 gate 失效。

## 边界

- `blackdetect` 只衡量像素暗度，无法判断黑标题、淡入/淡出或黑场停顿是否有叙事意义。
- `silencedetect` 只衡量电平，不识别对白、音乐或语义；低电平声音仍可能被归为静音。
- 中间黑场保持不变；需要按黑场切分章节时应先生成独立计划并逐段复核。
- 本工具会生成新的 CFR 工作副本，不覆盖原片，也不采用关键帧不精确的 stream-copy 裁切。
