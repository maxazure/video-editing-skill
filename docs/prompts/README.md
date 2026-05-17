# Video Editing Skill 提示词教程

> 本教程教你如何与 AI 对话，让它帮你完成各种视频剪辑任务。
> 每个场景都给出了**可以直接复制使用的提示词**。

## 使用前提

- 已安装 Video Editing Skill（参考项目 [README](../../README.md)）
- 已安装 FFmpeg 和 Python 依赖
- 在 Claude Code / OpenClaw 中可以调用该 Skill

## 教程目录

| 编号 | 场景 | 说明 |
|------|------|------|
| 01 | [口播素材处理](01-oral-broadcast.md) | 从拍摄素材到发布短视频的完整流程 |
| 02 | [分析素材并制定方案](02-analyze-material.md) | 让 AI 分析多条素材，给出剪辑建议 |
| 03 | [补录视频](03-reshoot-video.md) | AI 生成补录清单，补录后继续剪辑 |
| 04 | [补录音频](04-reshoot-audio.md) | 只替换声音，画面保持不变 |
| 05 | [动画配音视频](05-animation-voiceover.md) | 用 Remotion 把录音变成动画解说视频 |
| 06 | [多平台导出](06-multi-platform.md) | 一键导出抖音、Instagram、YouTube 等多比例版本 |
| 07 | [封面生成](07-cover.md) | 多种风格封面，一键生成 |
| 08 | [长视频拆短视频](08-long-to-short.md) | 10分钟长视频自动拆成多条1分钟短视频 |
| 09 | [背景音乐和片尾](09-bgm-endcard.md) | 添加 BGM、片尾卡片 |
| 10 | [B-roll 画面替换](10-broll.md) | 用其他画面替换口播片段，保留原声 |
| 11 | [批量处理](11-batch.md) | 批量处理多条素材 |
| 12 | [字幕风格定制](12-subtitle-style.md) | 6 种字幕风格，卡拉OK逐词高亮 |
| 13 | [提示词技巧和常见问题](13-tips.md) | 写好提示词的要点，以及常见问题解答 |
| 14 | [导出剪映工程](14-export-capcut.md) | 导出剪映/CapCut 草稿文件，免渲染直接编辑 |
| 15 | [小红书每日科技短视频（V3 完整流水线）](15-xhs-daily-tech-video.md) | 一条提示词跑完转写 → 重组 → 丰富 → 渲染 → 多平台 → 文案 |
| 16 | [Content Guard 平台雷区 lint](16-content-guard.md) | 自动检测违禁词/导流/医美/财富诱导，导出前拦截 |
| 17 | [一条视频 × 三平台导出](17-multi-platform.md) | 主视频 → 小红书 3:4 / 抖音 / 视频号 三版本 |
| 18 | [Auto-Enrich 自动丰富](18-auto-enrich.md) | 自动 B-roll / 章节卡 / 贴纸 / BGM 卡点 |
| 19 | [AI 生图（gpt-image-2 / Codex imagegen）](19-imagegen.md) | 抽象概念自动配图，提示词适配 gpt-image-2 |

## 快速上手

如果你是第一次使用，建议从 [01-口播素材处理](01-oral-broadcast.md) 开始，它覆盖了完整的工作流程。

如果你已经熟悉基本流程，可以直接跳到你需要的场景。

## 一句话速查

| 我想做什么 | 用哪个提示词 |
|-----------|-------------|
| 拍了口播想剪成短视频 | [01-口播素材处理](01-oral-broadcast.md) |
| 不知道素材怎么用 | [02-分析素材](02-analyze-material.md) |
| 有些地方讲得不好要重拍 | [03-补录视频](03-reshoot-video.md) |
| 画面没问题但声音要重录 | [04-补录音频](04-reshoot-audio.md) |
| 只有录音想做成视频 | [05-动画配音](05-animation-voiceover.md) |
| 一个视频发多个平台 | [06-多平台导出](06-multi-platform.md) |
| 需要视频封面 | [07-封面生成](07-cover.md) |
| 长视频拆成多条短的 | [08-长视频拆短视频](08-long-to-short.md) |
| 加背景音乐或片尾 | [09-背景音乐和片尾](09-bgm-endcard.md) |
| 口播换画面 | [10-B-roll 替换](10-broll.md) |
| 好多条视频一起处理 | [11-批量处理](11-batch.md) |
| 字幕好看一点 | [12-字幕风格](12-subtitle-style.md) |
| 提示词怎么写更好 | [13-技巧和FAQ](13-tips.md) |
| 导出到剪映继续编辑 | [14-导出剪映工程](14-export-capcut.md) |
| 每天做一条小红书科技短视频 | [15-V3 完整流水线](15-xhs-daily-tech-video.md) |
| 担心标题/正文触发平台限流 | [16-Content Guard](16-content-guard.md) |
| 一次发小红书+抖音+视频号 | [17-三平台导出](17-multi-platform.md) |
| 想让视频更"有质感"自动加丰富度 | [18-Auto-Enrich](18-auto-enrich.md) |
| 抽象概念想用 AI 生图（注意力机制/复利…） | [19-imagegen](19-imagegen.md) |
