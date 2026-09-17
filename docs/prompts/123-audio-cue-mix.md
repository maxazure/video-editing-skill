# Audio Cue Mix 音效 cue 单轨混音

`audio_cue_mix.py` 把已审 `audio_cue_sheet.v1` 和最终独立旁白轨变成一条 48 kHz stereo 音频母轨。每个 SFX cue 必须绑定项目内本地音频，或在显式使用 `--synthesize-missing` 时走内置 FFmpeg 合成配方。脚本不下载素材、不调用 provider、不混 BGM。

适用于这些情况：

- `audio_cue_sheet.py` 已经规划了 whoosh、ping、success chime 或 warning tick，但还没有真正落进音频。
- Remotion、HyperFrames 或 NLE 需要一条旁白 + SFX 单轨，避免多个音频元素造成重复、漂移或同步错误。
- 本地音效不足，希望用 FFmpeg 程序化生成短促音效，不消耗生成 credits。
- 发布前需要把 cue sheet、旁白、音效素材、输出字节和人工试听结论绑定到同一份 live gate。

## 工作流

先确认当前 FFmpeg 具备合成、延迟、混合、限幅和目标编码能力：

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile audio_cue_mix \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict
```

先生成计划：

```bash
python3 scripts/audio_cue_mix.py plan \
  --project-dir . \
  --cue-sheet work/audio_cue_sheet.json \
  --voice work/final_narration.wav \
  --delivery work/audio_cue_mix.wav \
  --synthesize-missing \
  --output work/audio_cue_mix.json \
  --markdown work/audio_cue_mix.md
```

`--synthesize-missing` 只支持四个现有 cue 类别：`transition_whoosh`、`emphasis_ping`、`success_chime`、`warning_tick`。未知类别、显式素材路径丢失、cue 超出旁白时长或增益越界都会继续阻塞。未加该选项时，所有缺失音效都必须先绑定本地文件。

渲染并完整解码：

```bash
python3 scripts/audio_cue_mix.py apply \
  --plan work/audio_cue_mix.json \
  --markdown work/audio_cue_mix.md
```

`apply` 把旁白保持为主轨；每个音效会统一到 48 kHz stereo，按 cue 时长 trim/pad、加短 fade、应用 `-40` 至 `-3 dB` 的受限增益，再按毫秒延迟到时间线。最终使用 `amix normalize=0`，避免因音效数量增加而自动压低旁白；`alimiter` 只负责峰值保护。输出先完整解码，再从同目录临时文件原子提升。

完整 1× 听完旁白 + SFX 后确认：

```bash
python3 scripts/audio_cue_mix.py confirm \
  --plan work/audio_cue_mix.json \
  --reviewed-by editor \
  --note "完整试听；转场和重点音效都低于人声，落点自然。" \
  --full-playback completed \
  --speech-intelligibility pass \
  --cue-timing pass \
  --sfx-level pass \
  --creative-fit pass \
  --clicks-or-clipping pass \
  --markdown work/audio_cue_mix.md \
  --strict

python3 scripts/audio_cue_mix.py verify \
  --plan work/audio_cue_mix.json \
  --markdown work/audio_cue_mix.md \
  --strict
```

任一复核项为 `fail` 都不会放行。`verify` 会现场重读 cue sheet、旁白、本地音效和输出，重新检查 SHA-256、媒体契约、计划摘要、48 kHz stereo、codec、时长和完整解码；重渲染或替换素材后旧 review 自动失效。

## 与 BGM 和最终成片的关系

本脚本只兑现 SFX。BGM 继续交给 `render_final.py --bgm-ducking`，让最终旁白驱动 sidechain；混入音乐后仍需完整 1× 看听成片，并运行 `audio_master_report.py`、`audio_channel_qa.py` 和需要的掉点检查。程序化音效不需要下载第三方文件，仍然需要人工确认听感与用途；本地音效则继续走素材来源和授权审查。

## Pipeline gate

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require audio_cue_mix \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

`pipeline_manifest.py` 会调用 live verifier。尚未 apply、未完整试听、任何输入或输出漂移都会阻塞；使用内置合成音效且全部复核通过时保留 warning，提醒最终混音还需复核。
