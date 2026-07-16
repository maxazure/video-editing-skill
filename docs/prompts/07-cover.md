# 07 封面生成

> 多种风格封面图，给视频一个吸引眼球的入口。

## 场景描述

视频需要一张好看的封面图（缩略图），用于抖音、小红书、YouTube 等平台展示。

---

## 基础封面

```
帮我给这个视频生成一张封面。
标题：5 分钟学会 Python
副标题：零基础也能上手
风格用 news（新闻标题卡片风）
```

---

## 从视频中取帧做封面

```
帮我从视频的第 15 秒位置截取一帧画面作为封面背景，
然后叠加标题"我的深圳生活"，用 frame 风格。
```

---

## 多个风格对比选择

不确定哪种风格好看？一次生成多个对比：

```
帮我用以下风格各生成一张封面，我来挑：
- bold（大标题）
- news（新闻风）
- gradient（渐变背景）
- techcard（卡片式，适合教程）
标题都用"程序员转行做自媒体"，副标题"第一个月的真实收入"。
```

需要把对比方案保存成 JSON/Markdown、批量渲染小尺寸预览并记录最终发布选择时，使用：

```bash
python3 scripts/cover_variants.py \
  origin/talking.mp4 \
  --title "程序员转行" \
  --subtitle "第一个月真实收入" \
  --caption output/caption.json \
  --platform xhs \
  --output-dir output/covers \
  --render \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md
```

完整流程见 [68 — Cover Variants](68-cover-variants.md)。

---

## 可用的封面风格

| 风格 | 效果 | 适合 |
|------|------|------|
| `bold` | 大标题 + 纯色背景 + 醒目色条 | 通用，强调标题 |
| `news` | 新闻标题卡片风 | 资讯、观点类 |
| `gradient` | 渐变背景 | 文艺、生活类 |
| `minimal` | 简洁居中文字 | 简约风格 |
| `white` | 白色背景 + 深色文字 | 干净、专业 |
| `techcard` | 圆角卡片布局 | 教程、技术类 |
| `frame` | 视频截帧做背景 + 叠加标题 | 需要真实画面的 |

---

## 指定字体

```
封面标题用站酷快乐体（zcool-kuaile），看起来活泼一些。
```

可用字体参考 [README](../../README.md) 的字体章节。
