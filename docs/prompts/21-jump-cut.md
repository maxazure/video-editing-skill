# Jump Cut 自动去停顿

用于口播、访谈、课程录屏这类“内容密度比画面连续性更重要”的素材。它会先检测静音/停顿，生成可审计 cut list，再按需一次 ffmpeg concat 渲染成更紧凑的成片。

## 什么时候用

- 口播里有长停顿、卡壳、思考空白。
- 想先复查要删掉哪些时间段，再决定是否渲染。
- 已经有完整视频，不想先拆成很多小 clip。

## 推荐流程

先只生成 cut list：

```bash
python3 scripts/jump_cut.py input/talking.mp4 \
  --dry-run \
  --cut-list output/talking.jumpcut.json
```

生成切点复盘图，快速查看每段删除区域附近的画面和波形：

```bash
python3 scripts/timeline_view.py input/talking.mp4 \
  --cut-list output/talking.jumpcut.json \
  --output-dir output/verify/jumpcut \
  --limit 12
```

确认 JSON 里的 `removed_segments` 和 `output/verify/jumpcut/*.png` 没有误切人声后再渲染：

```bash
python3 scripts/jump_cut.py input/talking.mp4 \
  --output output/talking.jumpcut.mp4 \
  --cut-list output/talking.jumpcut.json
```

## 参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--noise-db auto` | auto | 先跑 `loudnorm`，用 `input_thresh` 作为自适应静音阈值 |
| `--min-silence 0.5` | 0.5 | 超过多少秒才当作可删停顿 |
| `--pad 0.08` | 0.08 | 每个切点两侧保留的缓冲，避免咬字被切掉 |
| `--min-keep 0.15` | 0.15 | 太短的保留碎片会被丢弃 |
| `--dry-run` | 关 | 只输出 cut list，不渲染视频 |

## 输出 JSON

关键字段：

- `detected_silences`：ffmpeg 检测到的原始静音段。
- `removed_segments`：实际会删除的时间段，已经考虑 `pad`。
- `keep_segments`：最终拼接保留的时间段。
- `removed_seconds`：预计删除总秒数。
- `output_duration_estimate`：预计输出时长。
- `speedup_ratio`：节奏压缩比例。

## 注意

- 这是节奏剪辑，不是内容理解剪辑；如果停顿里有重要表情或屏幕操作，先看 cut list。
- 背景噪声很大的素材可手动指定阈值，例如 `--noise-db -32`。
- 默认只进行最终一次编码，符合本项目“单次编码原则”。
