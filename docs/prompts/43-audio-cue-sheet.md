# Audio Cue Sheet 音频设计清单

`audio_cue_sheet.py` 把 transcript 里的口播节奏转成 BGM / SFX 审核清单。它不生成音乐、不提交 TTS、不调用外部 provider；只扫描本地音频素材，输出 JSON + Markdown，让 agent 或人工先确认音频设计、素材缺口、credits/授权风险，再进入渲染。

## 适用场景

- 口播视频已经有 transcript，但还没确定背景音乐和转场音效。
- 想先看哪些地方适合 whoosh / ping / success chime，而不是直接把音效烧进成片。
- BGM/SFX 可能要走 AI 生成、stock 或本地素材库，需要先做 provider/credits 审批。
- 发布前想让 `pipeline_manifest.py` 把 unresolved audio cue 当成可选门禁。

## 常用命令

```bash
python3 scripts/audio_cue_sheet.py \
  --transcript work/transcript.json \
  --asset-root media/bgm \
  --asset-root media/sfx \
  --output work/audio_cue_sheet.json \
  --markdown work/audio_cue_sheet.md
```

严格模式要求本地 BGM/SFX 都 ready：

```bash
python3 scripts/audio_cue_sheet.py \
  --transcript work/transcript.json \
  --asset-root media \
  --require-local-music \
  --require-local-sfx \
  --output work/audio_cue_sheet.json \
  --markdown work/audio_cue_sheet.md \
  --strict
```

如果缺音乐或音效，`--strict` 返回退出码 2。需要 AI 生成音乐/音效时，先确认 provider credits 和素材授权，再提交生成任务。

## 输出内容

| 字段 | 说明 |
|---|---|
| `voice_track` | 口播主轨时长、段落数量、响度目标 |
| `music[]` | 一条全片 BGM bed，包含 mood、BPM 范围、prompt、本地候选或生成需求 |
| `sfx[]` | 基于“但是/重点/完成/风险”等触发词的音效 cue |
| `summary.blocking` | 严格要求下缺本地音频素材的阻塞数量 |
| `next_actions` | 应先补本地素材、删除 cue，还是审批生成 |

## 接入发布清单

`pipeline_manifest.py` 会自动识别 `audio_cue_sheet.json`。如果其中 `summary.blocking > 0`，发布清单会把 `audio_cue_sheet` 列为 blocking gate。

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

## 设计原则

- 先本地素材，后生成；先审核，后消耗 credits。
- BGM 默认是 speech-safe instrumental，不要用带歌词音乐压住口播。
- SFX 是短促点缀，不替代内容表达；不确定就删 cue。
- AI/stock 音频生成后仍需要跑 `asset_provenance.py` 或人工授权审查。
