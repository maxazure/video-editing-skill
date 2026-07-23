# 72 Visual Dedupe 跨素材重复镜头复核

> 在多机位、多 take、B-roll 素材库或重复转码文件进入时间线前，先找出视觉重复场景，并生成“建议保留哪一份”的可审阅证据。

`visual_dedupe.py` 只调用本地 FFmpeg 和 Python 标准库，不上传视频、不调用 LLM、不删除或移动源素材。它生成的是 edit-plan review gate，不是自动清理器。

## 基本用法

先为每条素材分别生成场景边界：

```bash
python3 scripts/scene_boundaries.py origin/cam-a.mp4 \
  --method adaptive \
  --output work/cam-a-scenes.json

python3 scripts/scene_boundaries.py origin/cam-b.mp4 \
  --method adaptive \
  --output work/cam-b-scenes.json
```

在 `work/visual_dedupe_sources.json` 写入：

```json
{
  "sources": [
    {
      "id": "cam-a",
      "video": "../origin/cam-a.mp4",
      "scene_boundaries": "cam-a-scenes.json",
      "quality_score": 0.9
    },
    {
      "id": "cam-b",
      "video": "../origin/cam-b.mp4",
      "scene_boundaries": "cam-b-scenes.json",
      "quality_score": 0.8
    }
  ]
}
```

manifest 里的相对路径以 manifest 所在目录为基准。`quality_score` 可省略；也可以在 scene plan 的单个 scene 上设置它，覆盖 source 默认值。

运行：

```bash
python3 scripts/visual_dedupe.py \
  --manifest work/visual_dedupe_sources.json \
  --output work/visual_dedupe.json \
  --markdown work/visual_dedupe.md \
  --strict
```

如果只想检查整条文件是否是重复转码，不需要 scene plan：

```bash
python3 scripts/visual_dedupe.py \
  origin/export-v1.mp4 origin/export-v2.mp4 \
  --output work/visual_dedupe.json \
  --markdown work/visual_dedupe.md
```

## 判断方式

每个候选场景在 10%、50%、90% 位置各取一帧：

- 64-bit dHash 捕捉画面结构差异；
- mean-RGB 距离防止黑屏、纯色卡和低纹理镜头都落到相同零哈希；
- 默认至少 2/3 个采样点的综合距离不超过 8；
- 默认要求较短场景至少达到较长场景的 50%；
- 默认只比较不同 `source_id`，避免同一长镜头内部的相邻 scene 被过度去重。

需要检查同一来源里的重复场景时加 `--include-same-source`。压缩或调色差异导致漏报时，可以小幅提高 `--hamming-threshold`；误报时降低阈值或提高 `--min-matching-samples 3`。阈值变化必须重新看 Markdown evidence，不能机械套用。

## 输出与处理

`visual_dedupe.v1` 包含：

- `pairs[]`：达到阈值的候选对和逐样本 Hamming/color distance；
- `duplicate_groups[]`：通过 union-find 合并的重复组；
- `recommended_keep`：优先保留显式 `quality_score` 更高者，再比较分辨率和源文件大小；
- `suggested_exclusions[]`：建议从下游 edit plan 排除的候选；
- `errors[]`：无法解码的采样点；
- `summary.blocking`：仍需人工处理的重复组和无法可靠取样的候选数。

Markdown 会为每个候选附本地媒体链接，并展开 10%/50%/90% 的成对时间、综合距离、hash distance 和 color distance；需要同时看画面与波形时，把列出的时间交给 `timeline_view.py`。

Review 时：

1. 打开每组里列出的两个 source range，确认不只是“长得像”，而是真的可以在叙事上互换。
2. 比较对焦、抖动、遮挡、表情、口型、构图和音频可用性；`quality_score` 只是已有人工/模型评分，不是事实。
3. 把确认重复的 `suggested_exclusions` 从 `render_config`、B-roll plan 或 NLE 时间线候选中排除。
4. 保留源文件。不要根据本报告直接删素材。

`--strict` 在存在重复组或不可可靠取样的候选时返回 2；这是“需要 review”的门禁状态，不表示脚本崩溃或素材被修改。处理完成后重新生成来源 manifest/候选或记录人工选择，再运行 `pipeline_manifest.py`。

## 何时不用

- 两个镜头画面相似但承载不同对白、反应或证据时，不能按视觉哈希去重。
- 需要识别同一人物/产品但构图明显不同的语义近似镜头时，应该用 `video_understanding.py` 或人工标签。
- 要找成片里的冻结帧、黑屏或异常静态段时，使用 `render_qa.py`；本脚本解决的是跨来源候选重复。
