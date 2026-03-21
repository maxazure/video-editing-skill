# 🎬 Video Editing Skill for OpenClaw

一个基于 AI 的自动视频剪辑 Skill，专为口播/脱口秀/Vlog 类视频设计。

它可以自动将你的视频按每句话切分，去掉口误和卡壳，加上字幕，最终合成一个可以直接发布的短视频。

> 🎯 适用于：小红书、抖音、视频号等竖屏短视频平台

## ✨ 功能特性

- **语音识别切分** — 基于 OpenAI Whisper，自动将视频按句子切分成独立片段
- **精确剪辑** — 使用重编码模式切割，音频在句子边界精确切断，无重复尾音
- **自动字幕** — 中英文自动检测，自动折行，竖屏位置优化（适配小红书等平台）
- **灵活合成** — 选择需要的片段，按任意顺序合成最终视频
- **多视频支持** — 可同时处理多个视频文件，跨视频混合选择片段
- **速度调整** — 支持加速/减速输出（1.1x、1.25x、1.5x 等）
- **自动封面** — 从视频第一帧生成封面，自动根据内容总结标题，支持自定义

## 📦 安装

### 方式一：通过 OpenClaw Skills 安装（推荐）

在 OpenClaw 中直接安装：

```bash
openclaw skills add https://github.com/maxazure/video-editing-skill.git
```

或手动克隆到 OpenClaw skills 目录：

```bash
git clone https://github.com/maxazure/video-editing-skill.git ~/.openclaw/skills/video-editing
```

### 方式二：手动克隆

```bash
git clone https://github.com/maxazure/video-editing-skill.git
cd video-editing-skill
```

### 安装系统依赖

**macOS：**
```bash
brew install ffmpeg
```

**Ubuntu/Debian：**
```bash
sudo apt install ffmpeg
```

### 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install openai-whisper
```

> ⚠️ Whisper 的 `large` 模型（推荐用于中文）首次运行时会自动下载，约需 2.9GB 磁盘空间。

### 验证安装

```bash
ffmpeg -version | head -1
source .venv/bin/activate && whisper --help | head -1
```

## 🚀 使用方式

### 配合 OpenClaw / Claude Code 使用（推荐）

本项目是一个 **OpenClaw Skill**，设计为配合 AI 编程助手使用。安装完依赖后，你只需要用自然语言告诉 AI：

```
帮我剪一下 videos/ 目录下的视频，按句子切分，去掉口误，加上字幕
```

AI 会自动调用各个脚本，完成整个剪辑流程，并在需要时询问你选择哪些片段。

### 手动使用

如果你想手动执行每一步：

#### Step 1: 提取音频

```bash
bash scripts/extract_audio.sh "your_video.mp4"
# 输出: your_video_audio.wav
```

#### Step 2: 语音识别

```bash
source .venv/bin/activate
python3 scripts/transcribe.py "your_video_audio.wav" --model large --language zh
# 输出: your_video_transcript.json
```

支持的模型：`tiny` / `base` / `small` / `medium` / `large`
- 中文建议使用 `large`，识别率最高
- 英文可以用 `base` 或 `small`

#### Step 3: 视频切分

```bash
python3 scripts/split_video.py "your_video.mp4" "your_video_transcript.json"
# 输出: your_video_clips/ 目录，包含 clip_001.mp4, clip_002.mp4, ...
```

#### Step 4: 烧录字幕

```bash
python3 scripts/burn_subtitles.py "your_video_clips" "your_video_transcript.json"
# 输出: your_video_clips_subtitled/ 目录
```

可选参数：
- `--font-path /path/to/font.ttf` — 指定自定义字体
- `--font-size 48` — 调整字号（默认 48）

#### Step 5: 合成视频

```bash
python3 scripts/merge_clips.py "your_video_clips_subtitled" --select "1-5,8,10-15" --output "final.mp4"
```

选择格式：
- 连续范围：`1-10`
- 多个片段：`1,3,5,7`
- 混合选择：`1-4,6,8-10`

#### Step 6: 生成封面

```bash
python3 scripts/generate_cover.py "final.mp4" --title "你的封面标题" --transcript "your_video_transcript.json"
# 输出: final_cover.jpg
```

如果不指定 `--title`，脚本会输出视频转录全文，供 AI 自动总结标题。

#### Step 7（可选）: 调整播放速度

```bash
ffmpeg -i final.mp4 \
  -filter_complex "[0:v]setpts=PTS/1.25[v];[0:a]atempo=1.25[a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k \
  final_1.25x.mp4
```

## 📁 目录结构

```
video-editing-skill/
├── README.md           # 本文件
├── SKILL.md            # Skill 定义（OpenClaw 读取）
├── scripts/            # 核心脚本
│   ├── extract_audio.sh      # 音频提取
│   ├── transcribe.py          # 语音识别
│   ├── split_video.py         # 视频切分
│   ├── burn_subtitles.py      # 字幕烧录
│   ├── merge_clips.py         # 视频合成
│   └── generate_cover.py     # 封面生成
├── fonts/              # 字体缓存（自动下载，已 gitignore）
└── videos/             # 用户视频工作目录（已 gitignore）
```

## 🔧 技术细节

| 组件 | 技术 |
|------|------|
| 语音识别 | OpenAI Whisper (large model) |
| 视频处理 | FFmpeg (libx264 + libass) |
| 字幕渲染 | ASS 格式 + Noto Sans SC 字体 |
| 视频切分 | 精确重编码（非 stream copy） |

### 字幕说明

- 竖屏视频（9:16）字幕位于画面 72% 高度处，避开底部交互按钮
- 字体大小按视频短边缩放，确保在任何分辨率下可读
- 超长字幕自动在中间位置折行，不超出屏幕边界
- 中文使用 Noto Sans SC 粗体，英文使用 Arial

## 📋 系统要求

- macOS / Linux
- Python 3.8+
- FFmpeg（需包含 libass、libfreetype）
- 磁盘空间：约 3GB（Whisper large 模型）

## 📄 License

MIT
