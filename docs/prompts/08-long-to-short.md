# 08 长视频拆短视频

> 把 10-15 分钟的长视频自动拆成多条 1 分钟短视频。

## 场景描述

你录了一个比较长的口播视频，讲了好几个话题。想把它拆成多条独立的短视频，每条可以单独发布。

---

## 按主题拆分（你知道有哪些话题）

```
我有一个 12 分钟的口播视频 media/raw/long-talk.mp4，
讲了 3 个话题：自媒体入门、内容选题、涨粉技巧。

帮我做语音识别，然后按话题拆成 3 条独立短视频。
每条要求：
1. 控制在 60-90 秒
2. 有自己的开头和结尾
3. 去掉卡壳和口误
4. 加字幕
5. 各自生成封面
6. 竖屏格式
```

---

## 自动拆分（让 AI 判断怎么分）

如果已经有 `transcript.json`，先跑本地候选打分，拿到可复核的短视频候选：

```bash
python3 scripts/scene_boundaries.py media/raw/long-talk.mp4 \
  --output work/scene_boundaries.json \
  --markdown work/scene_boundaries.md

python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --scene-boundaries work/scene_boundaries.json \
  --scene-snap-tolerance 1.5 \
  --video media/raw/long-talk.mp4 \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --render-config work/highlight_render_config.json \
  --platform xhs \
  --num-clips 4
```

先看 `work/scene_boundaries.md` 确认视觉切点没有过密，再看 `work/highlight_candidates.md`，确认每条都有完整 hook 和收尾。`Scene Snap` 会显示候选片段向前/向后扩展了多少秒来对齐画面切换；确认后再用 `work/highlight_render_config.json` 进入渲染。

```
这个视频比较长（15 分钟），帮我分析内容后自动拆分成几条短视频。
你来判断按什么话题分比较合理，每条大概 1 分钟。
要确保每条内容完整，有头有尾，不能话说到一半就断了。
```

---

## 拆分后微调

```
你分成了 4 条，但第 2 条和第 3 条的内容有点重叠。
帮我把这两条合成一条，控制在 90 秒以内。
```

```
第 1 条的开头 hook 不够好，帮我换一个更抓人的开场白。
如果素材里没有合适的，告诉我需要补录什么。
```

---

## 精华集锦

从长视频里只挑最精彩的片段：

```
这个视频 20 分钟，帮我从里面挑出最精彩的内容，
剪成一条 60 秒的精华集锦。
优先选信息量大、表达流畅、有吸引力的片段。
```
