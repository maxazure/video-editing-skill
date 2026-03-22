---
name: video-editing
description: "Automated video editing skill for talk/vlog/standup videos. Use when: cutting video, splitting video into sentences, merging video clips, extracting audio, transcribing speech, auto-editing oral presentation videos, combining selected sentence clips into a final video, generating video cover/thumbnail with title. Requires ffmpeg and whisper."
argument-hint: "Provide the path(s) to video file(s) to process"
metadata: { "openclaw": { "emoji": "🎬", "os": ["darwin", "linux", "win32"], "requires": { "bins": ["ffmpeg", "python3"] }, "install": [{ "id": "ffmpeg-brew", "kind": "brew", "formula": "ffmpeg", "bins": ["ffmpeg"], "label": "Install FFmpeg (brew)" }] } }
---

# Auto Video Editing（自动视频剪辑）

根据语音内容，将口播/脱口秀类视频按句子自动切分，然后按用户选择合成带字幕的最终视频。

## Prerequisites（前置要求）

在执行任何操作之前，先运行环境检测：

```bash
python3 scripts/utils.py
```

这会自动检测平台（macOS/Linux/WSL/Windows）、GPU 类型、可用编码器、Whisper 引擎，并给出诊断报告。

如果缺少依赖，提示用户安装：
- **ffmpeg**: `brew install ffmpeg`（macOS）或 `apt install ffmpeg`（Linux/WSL）或下载 Windows 版本
- **whisper**: `pip install faster-whisper`（推荐，速度快 4 倍）或 `pip install openai-whisper`
- **中国用户**加速安装：`pip install faster-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple`

如果项目根目录有 `.venv` 虚拟环境，运行 Python 脚本前先激活：
```bash
source .venv/bin/activate  # macOS/Linux/WSL
# Windows: .venv\Scripts\activate
```

### 平台说明

- **macOS (Apple Silicon)**: 自动使用 VideoToolbox 硬件编码加速，Whisper 推荐 large-v3-turbo 模型
- **macOS (Intel)**: 使用 VideoToolbox 编码，Whisper 使用 CPU 模式
- **Linux**: 自动检测 NVIDIA GPU (NVENC)、Intel QSV、AMD AMF
- **WSL**: 支持，自动检测 Windows 字体路径 (`/mnt/c/Windows/Fonts/`)
- **Windows**: 建议使用 WSL2 环境运行；支持 QSV/AMF 硬件编码
- **无独显 (集成显卡)**: Intel iGPU 使用 QSV 编码，AMD iGPU 使用 AMF 编码；Whisper 建议 medium 模型（而非 large）
- **中国用户**: 自动检测中国区域，使用清华 pip 镜像和 HuggingFace 镜像下载模型，也可通过 `--mirror` 参数强制启用

## Workflow（工作流程）

### Phase 1: Audio Extraction（音频提取）

对每个输入视频文件，使用 [extract_audio.py](./scripts/extract_audio.py) 提取音频：

```bash
python3 scripts/extract_audio.py "<video_path>"
```

输出：与视频同目录下的 `<video_name>_audio.wav` 文件。

### Phase 2: Speech Recognition（语音识别）

使用 [transcribe.py](./scripts/transcribe.py) 对音频进行语音识别，生成带时间戳的逐句文本：

```bash
python3 scripts/transcribe.py "<audio_path>" --model auto --language zh
```

- `--model auto`：根据硬件自动选择最佳模型（NVIDIA GPU → large-v3，Apple Silicon → large-v3-turbo，集成显卡 → medium，纯 CPU → small）
- 也可手动指定：`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`
- `--engine auto`：自动检测 faster-whisper（推荐）或 openai-whisper
- `--mirror`：中国用户使用镜像源下载模型
- `--language`：`zh`（中文），`en`（英文），`ja`（日文）等，也可省略让 whisper 自动检测

输出：与音频同目录下的 `<video_name>_transcript.json` 文件，格式如下：

```json
{
  "segments": [
    {"id": 1, "start": 0.0, "end": 2.5, "text": "大家好"},
    {"id": 2, "start": 2.5, "end": 5.1, "text": "今天我们来聊一个话题"}
  ]
}
```

### Phase 2.5: Transcript Review（转录文字校验）

转录完成后，**必须**对所有 transcript.json 中的文字进行逐条审查，修正以下两类问题：

**1. 语音识别错误（ASR errors）**：
Whisper 常见的识别错误类型：
- **专有名词/产品名**：如 "opencloud" → "OpenClaw"、"cloudcode" → "Claude Code"、"cloud ops" → "Claude Opus"
- **同音字错误**：如 "小红树" → "小红书"、"检映" → "剪映"、"断耕" → "断更"、"懒得讲" → "懒得剪"
- **英文拼写**：如 "scale" → "skill"、"箱子" → "视频"
- **尾部幻觉**：Whisper 有时在安静片段末尾生成无意义的重复文字，应直接删除

**2. 口误标记（Speaker errors）**：
- **重复/卡壳**：说话人重复说同一句话或卡住后重新说，标记为可跳过
- **乱码片段**：语音模糊导致识别为无意义文字的片段（如连续的单字碎片），标记为可跳过

**校验流程**：
1. 读取所有 transcript.json 的文字内容
2. 逐条检查，列出发现的问题（原文 → 修正 或 标记为可跳过）
3. 将修正列表展示给用户确认
4. 用户确认后，直接修改 transcript.json 文件中的 text 字段
5. 对于口误/乱码片段，在展示片段列表时（Phase 3）标注为建议跳过

**注意**：此步骤必须在 Phase 5（渲染）之前完成，因为字幕文字来源于 transcript.json。修正后再渲染，才能保证最终视频中的字幕文字正确。

### Phase 3: User Interaction（用户交互）

**展示片段列表给用户**，格式如下：

```
视频片段列表：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  #   | 时间区间          | 内容
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1   | 00:00.0 - 00:02.5 | 大家好
  2   | 00:02.5 - 00:05.1 | 今天我们来聊一个话题
  3   | 00:05.1 - 00:08.3 | 这个话题非常有意思
  ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择要合成的片段（示例）：
  - 连续范围：1-10
  - 多个片段：1,3,5,7
  - 混合选择：1-4,6,8-10
```

如果有多个视频文件，分别展示每个视频的片段列表，让用户跨视频选择。

等待用户回复选择后，进入 Phase 4。

### Phase 4: Render Config（渲染配置）

根据用户的选择，生成 `render_config.json` 配置文件：

```json
{
  "clips": [
    {"video": "path/to/video1.MOV", "segment_id": 4, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video1.MOV", "segment_id": 5, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video2.MOV", "segment_id": 1, "transcript": "path/to/transcript2.json"}
  ],
  "title": "封面标题文字",
  "chapters": [
    {"title": "章节名", "start": 0.0, "end": 30.0}
  ],
  "chapter_style": "mono"
}
```

**封面标题**：
1. 如果用户提供了标题，直接使用。
2. 如果用户没有特别要求，**站在观众角度**总结一个吸引人的标题（6-15 个字）。

**章节划分**：
- 根据视频内容逻辑划分章节，建议 **不超过 4 个章节**
- 章节名要**简短**（2-4 个字），如：痛点、原因、方案、工具
- 章节时间需要根据选定片段的累计时长精确计算

### Phase 5: Single-Pass Render（单次渲染）

使用 [render_final.py](./scripts/render_final.py) 从原始视频**一次编码**生成最终视频：

```bash
python3 scripts/render_final.py --config render_config.json --output final.mp4 --speed 1.25 1.5
```

**核心原理**：使用 ffmpeg `filter_complex` 的 `trim/atrim` 直接从原始视频裁切片段，然后 `concat` 拼接，最后叠加字幕、封面、章节时间轴，全部在**一次编码**中完成。

参数说明：
- `--config`：渲染配置 JSON 路径
- `--output`：输出文件路径
- `--speed 1.25 1.5`：同时输出变速版本（每个变速版本也是从原始视频直接编码，不是从已编码视频二次压缩）
- `--font-path`：自定义字体文件
- `--font-size`：字幕字号（默认 48，基于 1080p 自动缩放）
- `--no-subtitles`、`--no-cover`、`--no-chapters`：跳过对应功能

**输出**：
- `final.mp4`（原速）
- `final_1_25x.mp4`（1.25 倍速）
- `final_1_5x.mp4`（1.5 倍速）

**自动功能**：
- 字幕自动检测语言、自动折行、竖屏优化定位
- 封面自动叠加标题文字（带描边和阴影）在第一帧
- 章节时间轴自动叠加在视频顶部（竖屏）或底部（横屏）
- 所有章节名全程显示，当前章节高亮，其他章节半透明
- 变速版本的字幕时间、章节时间自动缩放

### Phase 6: Post-render Validation（渲染后验证）

渲染完成后，对最终视频执行验证流程：

**6a. 音频重复检测**：
1. 提取最终视频的音频
2. 重新进行语音识别
3. 检查识别结果中是否存在相邻片段的文字重复（前一句末尾 2-3 个字与后一句开头重复）
4. 如发现技术性重复（非自然语言重复），需要调整 render_config.json 中的片段选择

**6b. 字幕文字最终校验**：
1. 读取最终视频使用的所有 transcript 片段的文字
2. 按最终视频的片段顺序，逐条检查以下问题：
   - **语音识别残留错误**：Phase 2.5 可能遗漏的同音字、专有名词错误
   - **口误未清理**：说话人的口误（如说反了、重复了）是否仍然保留在最终视频中
   - **上下文连贯性**：跨视频拼接后，相邻片段之间是否存在语义断裂或逻辑跳跃
   - **字幕一致性**：同一个词/名称在不同片段中是否拼写一致
3. 如发现问题，列出问题清单并展示给用户：
   ```
   字幕校验结果：
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     #  | 问题类型     | 原文 → 建议修正
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     3  | 识别错误     | "检映" → "剪映"
     7  | 口误        | "先说了结果" → 建议删除此片段
    12  | 名称不一致   | "opencloud" → 统一为 "OpenClaw"
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ```
4. 用户确认修正后，修改对应的 transcript.json，然后重新执行 Phase 5 渲染

## Important Notes（注意事项）

### 视频质量与编码准则（最重要）

1. **单次编码原则**：从原始视频到最终输出，**只允许一次编码**。严禁多次重编码（如先切分编码、再烧字幕编码、再加封面编码），每次重编码都会累积质量损失。使用 `render_final.py` 的 `filter_complex` + `trim/atrim` 方案，在一条 ffmpeg 命令中完成裁切、拼接、字幕、封面、章节时间轴的全部操作。
2. **变速版本也从原始视频直接编码**：`--speed 1.25 1.5` 的变速版本在 `filter_complex` 中集成 `setpts` + `atempo`，直接从原始视频一步到位，**不要**从已编码的 1x 视频再次压缩。
3. **编码参数**：使用固定比特率（如 `-b:v 12M`）而非质量参数（如 `-q:v`）。固定比特率可以精确控制文件大小和质量，避免 `-q:v` 在不同编码器上表现不一致。参考原始视频比特率（通常 8-15 Mbps）设定。
4. **旧流程脚本仅用于预览**：`split_video.py`、`burn_subtitles.py`、`merge_clips.py`、`generate_cover.py`、`add_chapter_bar.py` 仍可单独使用，但**最终输出必须使用 `render_final.py`**。旧脚本适合快速预览单个片段效果。

### Rotation 检测

5. **Rotation 检测**：iPhone 等设备录制的竖屏视频，编码尺寸可能是 1920x1080 + rotation=-90 元数据。所有脚本通过 `utils.get_video_info()` 统一检测 rotation 并自动交换宽高，确保获取正确的显示尺寸。检测视频信息时必须首先检查 rotation。

### 章节时间轴

6. **章节数量**：建议不超过 4 个章节，章节名 2-4 个字。
7. **章节标签样式**：推荐使用 `mono` 样式（半透明白色），所有章节名全程显示，当前章节高亮(0.95)，其他半透明(0.4)。标签使用 `borderw=3` + 轻阴影，确保压缩后仍清晰可读。
8. **时间轴位置**：竖屏视频在顶部（避开底部平台 UI），横屏视频在底部。

### 其他

9. **多视频处理**：如果用户提供多个视频，对每个视频独立执行 Phase 1-2.5，然后在 Phase 3 统一展示所有视频的片段列表，支持跨视频混合选择片段。
10. **识别模型选择**：中文视频建议使用 `large` 模型，`base`/`small` 模型中文识别率较低。`large` 模型约需 2.9GB 下载空间。
11. **工作目录**：所有中间文件（音频、转录）都保存在视频文件所在目录下，便于管理。渲染完成后应清理临时文件（ASS 字幕文件、filter_complex 脚本）。
12. **错误处理**：如果某一步失败，向用户报告具体错误信息，并建议可能的解决方案。
13. **字幕字体**：ffmpeg 需要编译包含 `libass` 和 `libfreetype`。macOS 可通过 `brew install ffmpeg` 获取。
14. **竖屏适配**：字幕位置和字体大小已针对 9:16 竖屏视频（如小红书、抖音）优化。横屏视频同样支持。

## FAQ / Troubleshooting（常见问题诊断）

遇到错误时，先运行环境诊断：
```bash
python3 scripts/utils.py
```

### Q1: `No such filter: 'drawtext'` 或 `No such filter: 'ass'`

**原因**：ffmpeg 编译时未包含 `libfreetype`（drawtext 所需）或 `libass`（字幕所需）。

**诊断**：
```bash
ffmpeg -hide_banner -filters 2>/dev/null | grep -E "drawtext|ass|subtitles"
```
如果无输出，说明缺少对应滤镜。

**解决**：
- **macOS**：标准 `brew install ffmpeg` 可能不包含这些库。使用第三方 tap 安装完整版：
  ```bash
  brew tap homebrew-ffmpeg/ffmpeg
  brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
  ```
  该 tap 默认启用 `--enable-libfreetype --enable-libass --enable-libfontconfig`。
- **Linux/WSL**：`apt install ffmpeg` 通常已包含。如果缺少，安装开发依赖后从源码编译：
  ```bash
  sudo apt install libfreetype6-dev libfontconfig1-dev libass-dev
  ```
- **影响范围**：缺少 drawtext 时，字幕烧录、封面文字和章节标题标签会失败或自动降级。章节进度条的色块和播放头不受影响（仅使用 drawbox）。

### Q2: `Undefined constant or missing '(' in 'iw*0.5-tw/2'`

**原因**：ffmpeg drawtext 的 `x` 表达式中使用了 `tw`（text width），但某些 ffmpeg 版本中 `tw` 在 `x` 参数的上下文中不可用。

**解决**：脚本已修复此问题（使用像素值 `{pixel_x}-text_w/2` 代替 `iw*{frac}-tw/2`）。如果你修改了脚本并遇到此错误，请使用 `text_w` 而非 `tw`，并确保 `x` 表达式中不包含 `iw*` 动态计算。

### Q3: `Invalid alpha value specifier '%{eif:...}'` (drawtext fontcolor)

**原因**：试图在 `fontcolor` 参数中嵌入 `%{eif}` 表达式来实现透明度渐变，但 ffmpeg 不支持在颜色值中使用此语法。

**解决**：使用 drawtext 的 `alpha` 参数（独立于 fontcolor），而非试图在 `fontcolor=white@'%{eif:...}'` 中嵌入表达式。正确写法：
```
drawtext=text='hello':fontcolor=white:alpha='if(lt(t,1),t,1)'
```
错误写法（会报错）：
```
drawtext=text='hello':fontcolor=white@'%{eif:if(lt(t,1),t,1):d:2}'
```

### Q4: ffmpeg 硬件编码器失败 (`h264_videotoolbox` / `h264_nvenc` / `h264_qsv` 报错)

**原因**：检测到的硬件编码器不支持当前的视频参数（如特殊分辨率、色彩空间），或驱动版本不兼容。

**诊断**：
```bash
ffmpeg -encoders 2>/dev/null | grep -E "nvenc|videotoolbox|qsv|amf"
```

**解决**：在 `scripts/utils.py` 中临时修改 `get_ffmpeg_encoder()` 函数，让它直接返回 `("libx264", ["-preset", "fast", "-crf", "18"])`。

### Q5: 中文字幕显示为方框（豆腐块）

**原因**：系统中没有可用的中文字体文件。

**诊断**：
```bash
python3 -c "from scripts.utils import find_chinese_font; print(find_chinese_font())"
```
如果返回 `(None, ...)`，说明未找到中文字体。

**解决**：
- **macOS**：系统自带 PingFang SC，一般不会出现此问题。
- **Linux/WSL**：安装中文字体包：
  ```bash
  sudo apt install fonts-noto-cjk
  ```
- **WSL 备选**：脚本会自动尝试 `/mnt/c/Windows/Fonts/msyh.ttc`（微软雅黑），前提是 Windows 已安装该字体。
- **手动指定**：使用 `--font-path /path/to/your/font.ttf` 参数。
- **自动下载**：脚本首次运行时会尝试从 Google Fonts（中国用户使用 jsDelivr CDN）下载 Noto Sans SC，缓存到 `fonts/` 目录。

### Q6: Whisper 模型下载失败 / 超时

**原因**：网络问题，尤其是中国用户无法访问 HuggingFace。

**解决**：
- 使用 `--mirror` 参数：`python3 scripts/transcribe.py audio.wav --mirror --model auto`
- 或手动设置环境变量：
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com
  ```
- 使用 faster-whisper 时，模型从 HuggingFace 下载；设置 `HF_ENDPOINT` 后会自动走镜像。
- 使用 openai-whisper 时，模型从 GitHub 下载，中国用户可能需要代理。建议改用 faster-whisper。

### Q7: `pip install faster-whisper` 安装失败 / 超时

**解决**：中国用户使用清华镜像：
```bash
pip install faster-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```

### Q8: WSL 环境下 ffmpeg 找不到或版本过旧

**诊断**：
```bash
which ffmpeg && ffmpeg -version | head -1
```

**解决**：
```bash
sudo apt update && sudo apt install ffmpeg
```
如果系统源的 ffmpeg 版本过旧（< 4.0），使用 PPA：
```bash
sudo add-apt-repository ppa:savoury1/ffmpeg4
sudo apt update && sudo apt install ffmpeg
```

### Q9: 视频质量差 / 模糊

**原因**：视频经过了多次重编码，每次编码都有质量损失。

**解决**：必须使用 `render_final.py` 单次编码。检查是否在流程中使用了 `split_video.py` + `burn_subtitles.py` + `merge_clips.py` + `generate_cover.py` + `add_chapter_bar.py` 的旧流程（会导致 4-5 次重编码）。改用 `render_final.py --config` 一步到位。
