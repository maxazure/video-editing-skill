---
name: video-editing
description: "Automated video editing skill for talk/vlog/standup videos. Use when: cutting video, splitting video into sentences, merging video clips, extracting audio, transcribing speech, auto-editing oral presentation videos, combining selected sentence clips into a final video, generating video cover/thumbnail with title, B-roll cutaway editing, persistent video overlay/watermark, blinking REC indicator, ending title cards, multi-source audio mixing, exporting to JianYing/CapCut project for further editing, generating voiceover videos with Remotion (audio-only to video with animated visuals/subtitles). Requires ffmpeg and whisper. Remotion workflow additionally requires Node.js and npm."
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

### Linux GPU 配置指南（NVIDIA / Intel Arc）

在 Linux 上使用 GPU 加速 Whisper 语音识别时，不同显卡需要不同的配置方案。运行 `python3 scripts/utils.py` 会自动检测显卡型号并给出建议，但如果遇到问题，请参考以下方案。

#### 方案 A：NVIDIA 40 系列显卡（RTX 4060 / 4070 / 4080 / 4090）

40 系列（Ada Lovelace 架构，Compute Capability 8.9）对 faster-whisper 支持最成熟，开箱即用。

**安装步骤：**
```bash
# 1. 安装 NVIDIA 驱动（535+）和 CUDA Toolkit 12.4+
sudo apt install nvidia-driver-535 nvidia-cuda-toolkit
# 或从 NVIDIA 官网安装最新驱动：https://www.nvidia.com/drivers

# 2. 验证 CUDA
nvidia-smi  # 应显示驱动版本和 CUDA 版本

# 3. 安装 faster-whisper（自动安装匹配的 CTranslate2）
pip install faster-whisper>=1.1.0
```

**配置说明：**
- CUDA Toolkit: 12.4+（推荐 12.6）
- CTranslate2: >= 4.5.0（自动随 faster-whisper 安装）
- 计算精度: `float16`（默认）, `int8_float16`, `int8` 均可使用
- Whisper 模型: 推荐 `large-v3`（VRAM >= 6GB）
- 无需特殊配置，`python3 scripts/transcribe.py` 会自动检测并使用 CUDA

#### 方案 B：NVIDIA 50 系列显卡（RTX 5060 / 5060 Ti / 5070 / 5080 / 5090）

50 系列（Blackwell 架构，Compute Capability 12.0，sm_120）需要额外注意 CUDA 版本和计算精度设置。

**已知问题：**
CTranslate2 在 Blackwell 架构上使用 INT8 精度时会报错 `cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED`，
这是因为 Blackwell 的 INT8 Tensor Core 需要矩阵维度为 16 的倍数对齐。CTranslate2 >= 4.7.1 已修复此问题，
但为保险起见，本工具在检测到 50 系列显卡时会自动使用 `float16` 精度。

**安装步骤：**
```bash
# 1. 安装 NVIDIA 驱动（565+，必须支持 Blackwell）
#    从 NVIDIA 官网下载最新驱动：https://www.nvidia.com/drivers
#    或使用包管理器安装 565 以上版本
sudo apt install nvidia-driver-565

# 2. 安装 CUDA Toolkit 12.8+（Blackwell 最低要求）
#    推荐从 NVIDIA 官网安装：https://developer.nvidia.com/cuda-downloads
#    选择 Linux > x86_64 > Ubuntu > deb (network)

# 3. 验证 CUDA
nvidia-smi  # 应显示 CUDA 12.8+

# 4. 安装 faster-whisper 和最新 CTranslate2
pip install faster-whisper>=1.1.0
pip install --upgrade ctranslate2>=4.7.1  # 确保包含 Blackwell 修复

# 5. 如果仍然报错，强制使用 float16 精度（本工具已自动处理）
#    手动测试：
python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('tiny', device='cuda', compute_type='float16')
print('CUDA float16 OK')
"
```

**配置说明：**
- CUDA Toolkit: >= 12.8（推荐 13.0+，最新为 13.2）
- NVIDIA 驱动: >= 565
- CTranslate2: >= 4.7.1（包含 INT8 padding 修复）
- 计算精度: 推荐 `float16`（最稳定）；`int8_float16` 在 CTranslate2 >= 4.7.1 上可能可用
- 如果 `int8` 仍然报错，工具会自动降级到 `float16`
- Whisper 模型: 推荐 `large-v3`（VRAM >= 6GB）
- `utils.py` 会自动检测 50 系列显卡（通过 `nvidia-smi` 查询 GPU 名称中的 "RTX 50"），并选择安全的 `float16` 精度

**排错：**
如果出现 `CUBLAS_STATUS_NOT_SUPPORTED` 错误：
1. 确认 CTranslate2 版本 >= 4.7.1：`python3 -c "import ctranslate2; print(ctranslate2.__version__)"`
2. 确认 CUDA 版本 >= 12.8：`nvidia-smi` 或 `nvcc --version`
3. 尝试手动指定 `--compute-type float16`（如果直接使用 transcribe.py 的话）
4. 确认驱动版本 >= 565：`nvidia-smi` 查看 Driver Version

#### 方案 C：Intel Arc 独立显卡（A770 / A750 / B580）

Intel Arc 显卡**不支持 CUDA**，因此 faster-whisper（依赖 CTranslate2/CUDA）无法直接在 Intel Arc 上 GPU 加速。
需要使用替代方案。

**推荐方案：OpenVINO + Whisper（最易用）**
```bash
# 1. 安装 OpenVINO
pip install openvino openvino-genai

# 2. 使用 OpenVINO GenAI 的 WhisperPipeline
python3 -c "
import openvino_genai as ov_genai
pipe = ov_genai.WhisperPipeline('OpenVINO/whisper-large-v3-fp16-ov', device='GPU')
result = pipe.generate('audio.wav', language='<|zh|>')
print(result.texts[0])
"

# 3. 或使用 Hugging Face 预转换模型
pip install optimum[openvino]
# 从 HuggingFace 下载 OpenVINO 格式 Whisper 模型
# https://huggingface.co/OpenVINO/whisper-medium-int8-ov
```

**备选方案：whisper.cpp + SYCL（性能更好，配置更复杂）**
```bash
# 1. 安装 Intel oneAPI Base Toolkit
#    https://www.intel.com/content/www/us/en/developer/tools/oneapi/base-toolkit-download.html
wget -O- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
  | gpg --dearmor | sudo tee /usr/share/keyrings/oneapi-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] \
  https://apt.repos.intel.com/oneapi all main" \
  | sudo tee /etc/apt/sources.list.d/oneAPI.list
sudo apt update && sudo apt install intel-oneapi-base-toolkit

# 2. 编译 whisper.cpp（启用 SYCL 后端）
source /opt/intel/oneapi/setvars.sh
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build -DWHISPER_SYCL=ON
cmake --build build --config Release

# 3. 下载 Whisper 模型并运行
./build/bin/whisper-cli -m models/ggml-large-v3.bin -f audio.wav -l zh
```

**配置说明：**
- Intel Arc 不支持 CUDA，faster-whisper 在 Intel Arc 上只能用 CPU 模式
- OpenVINO 方案最简单，支持 Arc A770/A750/B580 和集成显卡
- whisper.cpp + SYCL 性能更好（A770 上接近 NVIDIA 中端显卡水平），但需要 oneAPI 环境
- B580（Battlemage 架构）的 SYCL 支持尚在优化中，A770 目前更稳定
- 推荐 Whisper 模型: `medium`（12GB VRAM 的 A770）或 `small`（8GB VRAM 的 A750）
- 如果用户有 Intel Arc 显卡，脚本会自动检测并使用 CPU 模式运行 faster-whisper（作为 fallback）

#### GPU 配置速查表

| 显卡系列 | 架构 | CUDA Toolkit | 驱动版本 | CTranslate2 | 计算精度 | Whisper 引擎 |
|---------|------|-------------|---------|-------------|---------|-------------|
| RTX 40xx | Ada Lovelace (sm_89) | >= 12.4 | >= 535 | >= 4.5.0 | float16 / int8 均可 | faster-whisper |
| RTX 50xx | Blackwell (sm_120) | >= 12.8 | >= 565 | >= 4.7.1 | **float16**（推荐） | faster-whisper |
| Intel Arc | Xe HPG / Battlemage | N/A | i915 | N/A | N/A | OpenVINO 或 whisper.cpp+SYCL |
| Intel iGPU | 集成显卡 | N/A | i915 | N/A | int8 (CPU) | faster-whisper (CPU 模式) |
| 无独显 | CPU | N/A | N/A | 任意版本 | int8 (CPU) | faster-whisper (CPU 模式) |

## Workflow（工作流程）

### Phase 0: Media Library Setup（素材库初始化）

首次使用时，帮助用户建立素材目录结构：

```bash
python3 scripts/media_library.py init [project_dir]
```

这会创建以下目录结构：
```
media/
├── raw/      — 原始素材（摄像机/手机直出的视频）
├── broll/    — B-roll 素材（城市街景、产品特写等）
├── bgm/      — 背景音乐（MP3/WAV/M4A）
├── assets/   — 叠加素材（水印 PNG、Logo 等）
└── output/   — 输出目录
```

**询问素材来源**：
1. 询问用户的视频文件位置（本地路径、外部设备或云端）
2. 建议将原始素材复制/移动到 `media/raw/` 目录
3. 询问是否有 B-roll、BGM 等辅助素材
4. 如果用户视频散落在多个目录，建议先集中到 `media/raw/`

**扫描并建立索引**：
```bash
python3 scripts/media_library.py scan [project_dir]
```

索引系统会自动：
- 扫描所有视频/音频/图片文件
- 提取时长、分辨率、帧率等元数据
- 关联已有的 transcript 文件
- 小型项目（< 200 文件）使用 JSON 索引（`media_index.json`）
- 大型项目自动升级为 SQLite 索引（`media_index.db`）
- 手动升级：`python3 scripts/media_library.py upgrade`

**查看素材库状态**：
```bash
python3 scripts/media_library.py status
```

**搜索素材**：
```bash
python3 scripts/media_library.py search "关键词"
```

### Phase 1: Audio Extraction（音频提取）

对每个输入视频文件，使用 [extract_audio.py](./scripts/extract_audio.py) 提取音频：

```bash
python3 scripts/extract_audio.py "<video_path>"
```

输出：与视频同目录下的 `<video_name>_audio.wav` 文件。

### Phase 2: Speech Recognition（语音识别）

使用 [transcribe.py](./scripts/transcribe.py) 对音频进行语音识别，生成带时间戳的逐句文本：

```bash
python3 scripts/transcribe.py "<audio_path>" --model auto --language zh --detect-fillers
```

- `--model auto`：根据硬件自动选择最佳模型（NVIDIA GPU → large-v3，Apple Silicon → large-v3-turbo，集成显卡 → medium，纯 CPU → small）
- 也可手动指定：`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`
- `--engine auto`：自动检测 faster-whisper（推荐）或 openai-whisper
- `--mirror`：中国用户使用镜像源下载模型
- `--language`：`zh`（中文），`en`（英文），`ja`（日文）等，也可省略让 whisper 自动检测
- `--silence-threshold 1.0`：静音检测阈值（秒），默认 1.0。设为 0 关闭
- `--word-timestamps`：启用逐词时间戳（卡拉OK字幕必需）
- `--detect-fillers`：检测填充词（中文：嗯/呃/那个/就是说；英文：um/uh/like/you know），标记纯填充词片段为建议跳过

输出：与音频同目录下的 `<video_name>_transcript.json` 文件，格式如下：

```json
{
  "segments": [
    {"id": 1, "start": 0.0, "end": 2.5, "text": "大家好"},
    {"id": 2, "start": 2.5, "end": 5.1, "text": "今天我们来聊一个话题"}
  ],
  "silences": [
    {"start": 15.2, "end": 18.5, "duration": 3.3, "before_segment": 5, "after_segment": 6}
  ],
  "filler_words": [
    {"segment_id": 3, "text": "嗯那个", "fillers_found": ["嗯", "那个"], "is_filler_only": true},
    {"segment_id": 7, "text": "就是说我觉得这个方案", "fillers_found": ["就是说"], "is_filler_only": false}
  ]
}
```

**静音检测**：transcribe.py 会自动分析相邻语音片段之间的间隙。超过阈值（默认 1 秒）的间隙会被标记为静音并输出到 `silences` 字段中。这些静音通常是说话人的停顿、卡壳或口误，在构建 render_config.json 选片时应注意避开这些区域。

### Phase 2a: Video Keyframe Extraction（视频关键帧提取）

对于口播类视频（尤其是在户外行走中拍摄的、带有环境音的素材），仅靠音频转录无法了解视频的视觉内容。使用 [extract_keyframes.py](./scripts/extract_keyframes.py) 提取视频关键帧并生成时序图，以便全面理解视频内容：

```bash
python3 scripts/extract_keyframes.py "<video_path>"
```

参数说明：
- `--max-frames 16`：最大关键帧数量（默认 16）
- `--threshold 0.4`：场景变化检测灵敏度（0.0-1.0，越低提取越多关键帧，默认 0.4）
- `--cols 4`：时序图网格列数（默认 4）
- `--thumb-width 320`：缩略图宽度（默认 320px）
- `--output-dir`：关键帧输出目录（默认 `<video_name>_keyframes/`）
- `--no-storyboard`：仅提取关键帧，不合成时序图

输出：
- `<video_name>_keyframes/` — 各关键帧 PNG 图片（带时间戳命名）
- `<video_name>_storyboard.png` — 合成的时序图（网格布局 + 时间戳标注）
- `<video_name>_keyframes.json` — 关键帧元数据（时间戳、帧号、文件路径）

**为什么需要关键帧提取**：
- 口播视频经常在走路中拍摄，画面中的场景变化（街道→公园→咖啡店）是重要的叙事线索
- 时序图让 AI 可以同时看到音频内容（transcript）和视觉内容（keyframes），做出更好的选片判断
- 可以发现纯音频分析无法捕捉的信息：肢体语言、表情变化、环境切换、产品展示等

**典型工作流**：
```bash
# 1. 提取音频并转录
python3 scripts/extract_audio.py video.mp4
python3 scripts/transcribe.py video_audio.wav --model auto --language zh --detect-fillers

# 2. 提取关键帧生成时序图
python3 scripts/extract_keyframes.py video.mp4

# 3. AI 结合 transcript + storyboard 进行综合分析
# → 查看 video_storyboard.png 了解视频画面内容
# → 对照 video_transcript.json 了解语音内容
# → 综合判断哪些片段最适合保留
```

**AI Agent 综合分析要点**：
- 将关键帧时序图与转录文本对照，标注每个时间段的「说了什么 + 画面是什么」
- 找出画面与语音最匹配的高质量片段（如：讲到"这家店"时画面正好对着店铺）
- 识别画面模糊、遮挡、光线不佳的片段，建议跳过
- 户外拍摄时，注意画面抖动严重的片段，在选片时降低优先级

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

#### AI 智能选片建议

在展示片段列表时，AI agent 应基于以下维度为每个片段提供推荐评分（1-5 星）：

**吸引力评分维度**：
1. **Hook 强度**（前 3 秒）：是否有吸引人的开头（提问、反直觉观点、情感触发）
2. **信息密度**：每秒传递的有效信息量（避免重复、废话）
3. **情感变化**：是否有情感起伏（幽默→严肃→惊喜）
4. **完整性**：片段是否构成完整叙事单元（有开头、展开、收尾）

**自动跳过建议**：
- transcript 中 `is_filler_only: true` 的片段（纯填充词）
- 静音间隙 > 2 秒的相邻片段（卡壳后重说）
- 转录文字与前一片段高度重复的片段（口误重说）

**长视频自动拆短片**（视频 > 3 分钟时）：
AI agent 应分析 transcript 识别话题转换点（语义断裂、过渡词如"接下来"、"另外"），将片段按话题分组为独立短视频（每个 30-90 秒），并为每组计算整体吸引力评分：

```
推荐短视频拆分方案：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  方案 | 片段范围    | 时长  | 主题         | 推荐指数
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  A    | #1-#8       | 45s   | 痛点引入      | ★★★★★
  B    | #9-#18      | 62s   | 核心方法      | ★★★★☆
  C    | #19-#25     | 38s   | 实操演示      | ★★★☆☆
  D    | #1-#25      | 2m25s | 完整版       | ★★★★☆
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

等待用户回复选择后，进入 Phase 4。

### Phase 4: Render Config（渲染配置）

根据用户的选择，生成 `render_config.json` 配置文件。

**重要说明：渲染配置是项目级文件**

SKILL 中展示的 render_config.json 格式只是**模板/参考**，不要直接修改 Skill 仓库中的任何文件。每个视频项目应在**用户工作目录**下创建独立的渲染配置：

```bash
# 在用户的视频项目工作目录下创建 script/ 文件夹
mkdir -p script/

# 在 script/ 下为每个视频项目创建独立的渲染配置文件
# 例如：script/render_config.json
```

**目录结构示例**：
```
~/Videos/my-project/          ← 用户工作目录
├── script/                   ← 渲染配置目录（每个项目独立）
│   └── render_config.json    ← 本项目的渲染配置
├── media/
│   ├── raw/                  ← 原始素材
│   ├── broll/
│   └── output/               ← 最终输出
├── *_audio.wav               ← 提取的音频
└── *_transcript.json         ← 转录文件
```

**渲染配置文件格式**（`script/render_config.json`）：

```json
{
  "clips": [
    {"video": "path/to/video1.MOV", "segment_id": 4, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video1.MOV", "segment_id": 5, "transcript": "path/to/transcript1.json"},
    {"video": "path/to/video2.MOV", "segment_id": 1, "transcript": "path/to/transcript2.json",
     "broll": "path/to/cityscape.mp4", "broll_start": 5.0}
  ],
  "title": "封面标题文字",
  "subtitle": "副标题/情感钩子（可选）",
  "cover_style": "news",
  "cover_duration": 2.0,
  "cover_image": "path/to/custom_cover.png",
  "cover_use_frame": false,
  "video_overlay": "path/to/overlay.png",
  "rec_blink": {
    "dot_image": "path/to/dot.png",
    "x": 55, "y": 66,
    "period": 1.0
  },
  "end_cards": [
    {"text": "感谢观看\n更多内容敬请期待", "duration": 3.5}
  ],
  "bgm": "path/to/background_music.mp3",
  "bgm_volume": 0.15,
  "bgm_fade_out": 3.0,
  "subtitle_style": "karaoke",
  "subtitle_highlight_color": "#FFFF00",
  "chapters": [
    {"title": "章节名", "start": 0.0, "end": 30.0}
  ]
}
```

**使用渲染配置**：
```bash
# 渲染时指定 script/ 下的配置文件
python3 scripts/render_final.py --config script/render_config.json --output media/output/final.mp4
```

**B-roll 替换**（`broll` 字段）：
- 在 clip 中添加 `"broll": "path/to/video.mp4"` 可替换该片段的画面，同时保留原始音频
- `broll_start` 指定从 B-roll 视频的哪个时间点开始截取（默认 0.0）
- B-roll 视频会自动缩放/裁切以匹配主视频分辨率
- 适用场景：屏幕录制口播配城市街景、音频配音配画面等

**自定义封面**（`cover_image`）：
- 提供自定义封面 PNG 路径，优先于自动生成的封面
- 尺寸应与视频分辨率匹配（如 1080x1920）

**持续叠加层**（`video_overlay`）：
- 提供透明 PNG 路径，在封面之后的整个视频上持续叠加显示
- 适用场景：品牌水印、系列标识（如 DAY 标签、数据指标）等
- PNG 必须包含 Alpha 通道（RGBA），透明区域不会遮挡视频

**闪烁圆点**（`rec_blink`）：
- 在视频上叠加一个周期性闪烁的小圆点 PNG（如录像机 REC 标志）
- `dot_image`：圆点 PNG 路径（建议 12-16px，RGBA 格式）
- `x`, `y`：圆点在视频画面中的像素坐标
- `period`：闪烁周期（秒），默认 1.0（0.5 秒亮 + 0.5 秒灭）

**结尾卡片**（`end_cards`）：
- 在视频末尾追加黑屏文字卡片，每张卡片有 300ms 淡入淡出
- `text`：卡片文字内容，用 `\n` 换行
- `duration`：每张卡片显示时长（秒），建议 3.0-4.0
- 文字居中显示，字号为正文字幕的 1.4 倍

**背景音乐**（`bgm`）：
- 提供背景音乐文件路径（MP3/M4A/WAV 等 FFmpeg 支持的格式）
- `bgm_volume`：BGM 音量（0.0-1.0），默认 0.15（人声为主，BGM 为辅）
- `bgm_fade_out`：结尾淡出时长（秒），默认 3.0
- BGM 自动循环播放直到视频结束，不需要预先剪辑长度
- 推荐免费可商用音乐源：Pixabay Music、Mixkit、YouTube Audio Library
- 选曲建议：口播/教程用轻柔纯音乐（无人声），节奏不要太强，避免抢人声

**字幕风格预设**（`subtitle_style`）：
| 风格 | 效果 | 适用场景 |
|------|------|---------|
| `normal` | 白字黑描边（默认） | 适合所有场景 |
| `karaoke` | 逐词高亮 | 音乐/节奏感内容 |
| `bold_pop` | 粗描边高对比 | MrBeast/Hormozi 风格 |
| `neon` | 霓虹灯青紫色 | 科技/潮流内容 |
| `minimal` | 极简无描边 | 文艺/安静内容 |
| `yellow_pop` | 黄字黑描边 | 高可见度，户外/嘈杂画面 |

**卡拉OK字幕 / 逐词高亮**（`subtitle_style: "karaoke"`）：
- 在 config 中设置 `"subtitle_style": "karaoke"` 启用逐词高亮字幕
- 需要先用 `--word-timestamps` 参数进行语音识别，获取逐词时间戳
- `subtitle_highlight_color`：当前词高亮颜色，默认 `"#FFFF00"`（黄色）
- `subtitle_base_color`：未说到的词底色，默认 `"#FFFFFF"`（白色）
- `subtitle_base_alpha`：底色透明度 hex，默认 `"80"`（半透明）
- 如果 transcript 中没有 word 级时间戳，会自动回退到按字符均匀分布（效果稍差）
- 也可通过 CLI 参数 `--subtitle-style karaoke` 覆盖 config
- 典型工作流：
  ```bash
  # 1. 转录时开启逐词时间戳
  python3 scripts/transcribe.py audio.wav --model auto --language zh --word-timestamps
  # 2. 渲染时选择 karaoke 字幕风格
  python3 scripts/render_final.py --config render_config.json --output final.mp4 --subtitle-style karaoke
  ```

**音频源替代（M4A/独立音频）**：
- 如果有独立录制的音频文件（M4A 等），可先用 ffmpeg 转为带黑屏视频轨的 MP4：
  ```bash
  ffmpeg -f lavfi -i "color=c=black:s=1080x1920:r=30:d=60" -i audio.m4a -c:v libx264 -c:a aac -shortest audio.mp4
  ```
- 然后在 clip 中用 `"video": "audio.mp4"` 提供音频源，用 `"broll"` 提供画面
- 这样可以将补录的配音与任意画面组合

**封面标题**：
1. 如果用户提供了标题，直接使用。
2. 如果用户没有特别要求，**站在观众角度**总结一个吸引人的标题（6-15 个字）。
3. `subtitle` 可选，用于补充情感钩子或关键信息。
4. **移动端优先**：封面在手机列表页里只是一个缩略图，标题必须先保证可读性，再考虑画面细节。

**封面文字排版规则**：
- 标题按**一行最多约 8 个汉字**来设计；超过时自动换行，不要把单行塞得过满。
- 英文/数字按**约半个汉字宽度**估算；例如 `AI`、`GPT-5` 之类不应把整行宽度挤爆。
- `subtitle` 字号默认按标题的**约 50%** 处理，只承担补充信息，不和主标题争抢视觉中心。
- 如果标题超过两行，优先**缩短文案**，不要继续缩小字号来硬塞。
- 做教程/工具类封面时，主标题尽量控制在 **4-8 个字**，副标题控制在 **6-12 个字**。

**封面风格**（`cover_style`）— 根据视频内容选择最合适的风格：
| 风格 | 适用场景 | 视觉效果 |
|------|---------|---------|
| `bold` | 教程、科普、技术 | 黑底 + 大号白色粗体字，简洁有力 |
| `news` | 热点、观点、争议 | 深色渐变底 + 白色标题 + 黄色副标题，冲击力强 |
| `frame` | Vlog、实拍、场景 | 视频首帧做背景 + 暗色遮罩 + 描边白字 |
| `gradient` | 生活、情感、艺术 | 紫粉渐变底 + 发光白字，温柔优雅 |
| `minimal` | 思考、文化、深度 | 纯黑底 + 细体白字，极简克制 |
| `white` | 教程、产品、品牌化内容 | 纯白底 + 深色字，现代感更强 |
| `techcard` | 屏幕录制、软件教程、AI 工具演示 | 左侧大标题 + 右侧画面卡片，兼顾信息量和可读性 |

AI agent 应根据视频主题和内容语气自动选择：
- 科技/工具类 → `bold` 或 `news`
- 争议/观点类 → `news`（白标题+黄副标题效果最抢眼）
- Vlog/实拍类 → `frame`
- 情感/生活类 → `gradient`
- 深度/文化类 → `minimal`
- 纯桌面录屏 / 软件教程 → `techcard` 优先；如果画面太杂，就退回 `bold` / `white`

**背景取帧规则**：
- 不要机械地使用第一帧。对于录屏教程，优先选择**信息密度更高**、界面更完整的一帧做背景或卡片图。
- 如果背景画面会影响标题识别，优先使用 `bold` / `white` / `minimal` 这类纯底风格。
- 单独预览封面时，可用 `scripts/generate_cover_image.py --frame-timestamp 00:10:00` 指定取帧时间。

**封面时长**（`cover_duration`）：
- 默认 2.0 秒，将第一帧冻结并叠加封面
- 也可通过 `--cover-duration` 命令行参数覆盖

**章节划分**：
- 根据视频内容逻辑划分章节，建议 **不超过 4 个章节**
- 章节名要**简短**（2-4 个字），如：痛点、原因、方案、工具
- 章节时间需要根据选定片段的累计时长精确计算

### Phase 5: Single-Pass Render（单次渲染）

使用 [render_final.py](./scripts/render_final.py) 从原始视频**一次编码**生成最终视频：

```bash
python3 scripts/render_final.py --config render_config.json --output final.mp4 --speed 1.25 1.5
```

**核心原理**：
- **单视频**（最常见场景）：使用 `select/aselect` + `between()` 表达式一次性筛选所有保留片段，FFmpeg 解码完整源视频但只编码选中的帧，配合 `-crf 18 -preset medium` 只编码一次
- **多视频混剪**：使用 `trim/atrim` 裁切 + `concat` 拼接（自动降级）
- 封面使用 `tpad` 冻结第一帧 + `adelay` 添加静音，字幕时间自动偏移，全部在**一次编码**中完成
- 章节时间轴不烧入视频，渲染完成后以文本形式输出，供用户手动粘贴到小红书等平台

参数说明：
- `--config`：渲染配置 JSON 路径
- `--output`：输出文件路径
- `--speed 1.25 1.5`：同时输出变速版本（每个变速版本也是从原始视频直接编码，不是从已编码视频二次压缩）
- `--cover-duration 2.0`：封面冻结时长（秒），覆盖配置中的 `cover_duration`
- `--font-path`：自定义字体文件
- `--font-size`：字幕字号（默认 120，即竖屏宽度 1080 ÷ 9 = 120，每个汉字占屏幕宽度的 1/9，基于实际分辨率自动缩放）
- `--no-subtitles`、`--no-cover`：跳过对应功能

**输出**：
- `final.mp4`（原速）
- `final_1_25x.mp4`（1.25 倍速）
- `final_1_5x.mp4`（1.5 倍速）
- 渲染完成后终端输出章节时间轴文本，可直接复制到小红书

**多平台格式导出**：
```bash
python3 scripts/render_final.py --config render_config.json --output final.mp4 \
  --formats vertical square horizontal
```

同时输出：
- `final_vertical.mp4`（9:16 抖音/小红书/TikTok）
- `final_square.mp4`（1:1 Instagram）
- `final_horizontal.mp4`（16:9 YouTube/B站）

裁切策略为中心裁切（center-crop），保持画面主体不变。

**自动功能**：
- 字幕自动检测语言、自动折行、竖屏优化定位
- 封面自动叠加标题文字（带描边和阴影），冻结首帧 1-2 秒
- 变速版本的字幕时间自动缩放
- B-roll 自动缩放裁切匹配主视频分辨率
- 结尾卡片自动拼接黑帧 + 静音 + 淡入淡出字幕
- 持续叠加层和闪烁圆点在封面之后自动启用
- 视频/音频时长自动对齐（`-shortest`），避免平台上传时的时长不匹配问题

### Phase 5b: Export to JianYing / CapCut（导出剪映工程）

如果用户希望在剪映中继续编辑（添加特效、调色、精修转场等），可以将剪辑方案**直接导出为剪映工程文件**，无需 ffmpeg 渲染：

```bash
python3 scripts/export_capcut.py --config render_config.json --output ./my_draft
```

**导出内容**：
- **视频轨道**：所有选定的片段按顺序排列在主视频轨道上，包含片头封面（冻结首帧或自定义封面图）
- **字幕轨道**：每个片段的转录文字作为独立字幕段落，位于画面下方
- **文字轨道**：封面标题、副标题、结尾卡片文字（与字幕分离，便于单独编辑）
- **音频轨道**：背景音乐（BGM），自动设置音量
- **转场**：片段之间自动添加淡入淡出转场（300ms）
- **B-roll**：支持 B-roll 替换画面（使用 broll 路径作为视频源）

参数说明：
- `--config`：渲染配置 JSON 路径（与 render_final.py 共用同一个 render_config.json）
- `--output`：输出文件夹路径（会创建 draft_content.json 和 draft_meta_info.json）
- `--name`：项目名称（默认使用配置中的标题）

**使用方式**：
1. 将导出的文件夹复制到剪映草稿目录：
   - macOS: `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`
   - Windows: `%APPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft/`
2. 打开剪映，在"草稿"列表中即可看到该项目
3. 在剪映中可继续添加特效、调整转场、调色、导出等

**注意**：
- 导出的工程文件使用**绝对路径**引用视频/音频素材，确保素材文件不要移动位置
- 支持剪映专业版（JianyingPro）；剪映 6+ 版本均可打开生成的草稿
- 此功能与 Phase 5（ffmpeg 渲染）**二选一**：如果用户只需要最终视频，用 render_final.py；如果需要进一步在剪映中编辑，用 export_capcut.py

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

1. **单次编码原则**：从原始视频到最终输出，**只允许一次编码**。严禁多次重编码（如先切分编码、再烧字幕编码、再加封面编码），每次重编码都会累积质量损失。使用 `render_final.py` 的 `select/aselect` + `between()` 方案（单视频）或 `trim/atrim` + `concat` 方案（多视频），在一条 ffmpeg 命令中完成裁切、拼接、字幕、封面的全部操作。
2. **变速版本也从原始视频直接编码**：`--speed 1.25 1.5` 的变速版本在 `filter_complex` 中集成 `setpts` + `atempo`，直接从原始视频一步到位，**不要**从已编码的 1x 视频再次压缩。
3. **编码参数**：使用固定比特率（如 `-b:v 12M`）而非质量参数（如 `-q:v`）。固定比特率可以精确控制文件大小和质量，避免 `-q:v` 在不同编码器上表现不一致。参考原始视频比特率（通常 8-15 Mbps）设定。
4. **旧流程脚本仅用于预览**：`split_video.py`、`burn_subtitles.py`、`merge_clips.py`、`generate_cover.py`、`add_chapter_bar.py` 仍可单独使用，但**最终输出必须使用 `render_final.py`**。旧脚本适合快速预览单个片段效果。

### Rotation 检测

5. **Rotation 检测**：iPhone 等设备录制的竖屏视频，编码尺寸可能是 1920x1080 + rotation=-90 元数据。所有脚本通过 `utils.get_video_info()` 统一检测 rotation 并自动交换宽高，确保获取正确的显示尺寸。检测视频信息时必须首先检查 rotation。

### 章节时间轴

6. **章节数量**：建议不超过 4 个章节，章节名 2-4 个字。
7. **时间轴不烧入视频**：渲染完成后以文本形式输出章节时间轴（含封面偏移），用户手动复制到小红书等平台的视频描述中。

### 其他

8. **多视频处理**：如果用户提供多个视频，对每个视频独立执行 Phase 1-2.5，然后在 Phase 3 统一展示所有视频的片段列表，支持跨视频混合选择片段。
9. **识别模型选择**：中文视频建议使用 `large` 模型，`base`/`small` 模型中文识别率较低。`large` 模型约需 2.9GB 下载空间。
10. **工作目录**：所有中间文件（音频、转录）都保存在视频文件所在目录下，便于管理。渲染完成后应清理临时文件（ASS 字幕文件、filter_complex 脚本）。
11. **错误处理**：如果某一步失败，向用户报告具体错误信息，并建议可能的解决方案。
12. **字幕字体**：默认使用 LXGW WenKai Medium（霞鹜文楷中粗体），自动下载缓存。ffmpeg 需要编译包含 `libass` 和 `libfreetype`。macOS 可通过 `brew install ffmpeg` 获取。
13. **竖屏适配**：字幕位置和字体大小已针对 9:16 竖屏视频（如小红书、抖音）优化。默认字号 120（= 屏幕宽度 1080 ÷ 9），每个汉字占竖屏宽度的约 1/9。横屏视频同样支持，字号会按实际分辨率自动缩放。

## Remotion Voiceover Workflow（语音生成视频）

当用户只有语音（或音频文件）但没有画面时，使用 Remotion 生成配合语音的视频画面。

详细的 Remotion API 参考、模板样式、组件结构见 [REMOTION_VOICEOVER.md](./REMOTION_VOICEOVER.md)。

### 适用场景

- **纯音频口播** — 有录音/TTS 语音，需要生成匹配的画面
- **音频 + 静态图片** — 有语音和图片素材，需要组合成动态视频
- **播客可视化** — 将播客/对话音频转为带字幕和视觉效果的视频
- **解说视频** — 配音 + 文字动画 + 背景图的组合

### Remotion Workflow（工作流）

1. **音频准备** — 使用 `extract_audio.py` 提取音频，或直接使用用户提供的音频文件
2. **语音识别** — 使用 `transcribe.py` 生成 transcript.json（逐句时间戳）
3. **时间轴生成** — AI Agent 根据 transcript 内容分析语义，生成 `timeline.json`：
   - 将多个 segments 按语义分组为 scenes
   - 为每个 scene 选择类型（title/content/kinetic/quote 等）
   - 选择背景视觉、文字动画、转场效果
   - 配置字幕样式（TikTok 逐词高亮 / 底部字幕 / 全屏文字）
4. **素材准备** — 收集/生成场景所需的图片素材
5. **Remotion 渲染** — 使用 `npx remotion render` 根据 timeline.json 渲染最终视频
6. **后处理（可选）** — 使用 `render_final.py` 与其他视频片段合并

### 视频模板风格选择

| 风格 | 适用场景 | 视觉效果 |
|------|---------|---------|
| `tiktok` | 短视频口播、知识分享 | 渐变背景 + 关键词卡片 + TikTok 风格逐词字幕 + 进度条 |
| `tutorial` | 教程、科普、产品介绍 | 图文分栏 + Ken Burns 图片 + 要点逐行淡入 + 底部字幕 |
| `kinetic` | 情感类、激励类、文案 | 全屏大号文字逐行弹入 + 弹性动效 |
| `podcast` | 播客、访谈、对话 | 头像 + 音频波形可视化 + 引用文字 |
| `news` | 新闻播报、行业资讯 | 顶部横幅 + Lower Third 信息条 + 滚动字幕 |
| `slideshow` | 产品介绍、旅行、相册 | 多图 Ken Burns + 转场效果 + 说明文字 |

### Remotion 环境要求

```bash
# 需要 Node.js 18+ 和 npm
node --version  # >= 18.0.0
npm --version

# 安装 Remotion standup 项目依赖
cd remotion-standup
npm install
```

### 脱口秀/纯音频视频生成（Standup Comedy Workflow）

当用户有一段脱口秀音频（或任何纯语音内容）但没有画面时，可以生成带文字动效的视频：

**Step 1: 语音识别**
```bash
python3 scripts/transcribe.py audio.wav --model auto --language zh
```

**Step 2: 生成时间轴**
```bash
# 默认风格
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --output remotion-standup/public/timeline.json

# 活力风格（更多夸张动效）
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --style energetic \
    --output remotion-standup/public/timeline.json

# 选择特定片段 + 自定义字体
python3 scripts/generate_standup_timeline.py transcript.json \
    --audio audio/standup.wav \
    --segments 1-10,12,15-20 \
    --font "LXGW WenKai, sans-serif" \
    --output remotion-standup/public/timeline.json
```

**Step 3: 预览和渲染**
```bash
cd remotion-standup

# 把音频文件复制到 public 目录
cp /path/to/audio.wav public/audio/standup.wav

# 开发预览
npx remotion studio

# 渲染最终视频
npx remotion render StandupVideo out/standup.mp4 --codec=h264 --crf=18
```

**脚本会自动：**
- 检测笑点/短句（感叹号、关键词、短句跟长句的对比）→ 用 slam/shake/bounce 等夸张动效
- 短句放大字号（emphasis 1.3-1.6x），长句缩小避免溢出
- 笑点使用径向渐变 + 醒目配色（红/橙/金/绿）
- 常规句子使用深色渐变背景 + 不同动画循环
- 自动在中文长句中间插入换行

**12 种文字动画效果：**

| 动画 | 效果 | 适合场景 |
|------|------|---------|
| `fadeIn` | 渐显 | 平稳叙述 |
| `springIn` | 弹性入场 | 正常对话 |
| `scaleUp` | 由小放大 | 强调 |
| `scaleDown` | 由大缩小 | 砸入感 |
| `typewriter` | 打字机 | 引述/对话 |
| `bounce` | 弹跳 | 活泼/搞笑 |
| `shake` | 抖动 | 笑点/吐槽 |
| `slam` | 急速砸入 | 重磅笑点 |
| `wave` | 逐字波浪 | 开场/结尾 |
| `glitch` | 故障闪烁 | 意外/反转 |
| `rotateIn` | 旋转入场 | 切换话题 |
| `splitReveal` | 中间展开 | 揭示/揭晓 |

**3 种风格预设：**

| 风格 | 说明 |
|------|------|
| `default` | 均衡混合所有动画，适合大多数内容 |
| `calm` | 只用平缓动画（fadeIn/springIn/typewriter），适合讲故事/深度内容 |
| `energetic` | 偏重夸张动画（slam/shake/bounce/glitch），适合脱口秀/搞笑内容 |

### timeline.json 配置格式

```json
{
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "audioSrc": "public/audio/voiceover.wav",
  "style": "tiktok",
  "font": {
    "family": "Noto Sans SC",
    "titleWeight": "700",
    "bodyWeight": "400",
    "titleSize": 72,
    "bodySize": 48
  },
  "colors": {
    "background": "#1a1a2e",
    "text": "#ffffff",
    "highlight": "#FFD700",
    "accent": "#e94560"
  },
  "scenes": [
    {
      "id": 1,
      "startMs": 0,
      "endMs": 5000,
      "type": "title",
      "title": "今天聊一个话题",
      "background": { "type": "gradient", "colors": ["#667eea", "#764ba2"] },
      "transition": { "type": "fade", "durationMs": 500 }
    }
  ],
  "captions": {
    "enabled": true,
    "style": "tiktok",
    "fontSize": 56,
    "highlightColor": "#FFD700"
  }
}
```

## Font Catalog（字体目录）

项目内置了一套可下载的免费字体目录，覆盖中文和英文视频制作常用字体。

### 字体管理

```bash
# 查看环境报告（包含已缓存字体列表）
python3 scripts/utils.py

# 在 Python 中使用字体 API
python3 -c "
from scripts.utils import list_available_fonts, download_font

# 列出所有可用字体
for f in list_available_fonts():
    status = '✓' if f['cached'] else '○'
    print(f\"{status} {f['id']:25s} {f['name']:25s} {f['use_case']:8s} {f['description']}\")

# 下载指定字体
download_font('lxgw-wenkai')
download_font('inter')
"
```

### 中文字体

| 字体 ID | 名称 | 风格 | 用途 | 许可证 |
|---------|------|------|------|--------|
| `noto-sans-sc` | Noto Sans SC（思源黑体） | 万能黑体 | 全场景 | SIL OFL |
| `noto-serif-sc` | Noto Serif SC（思源宋体） | 正式宋体 | 文化/古典/深度 | SIL OFL |
| `lxgw-wenkai` | LXGW WenKai（霞鹜文楷） | 手写楷体 | 文艺/文化/情感（~24MB） | SIL OFL |
| `lxgw-wenkai-lite` | LXGW WenKai Lite（轻便版） | 手写楷体 | 同上，体积更小（~13MB） | SIL OFL |
| `lxgw-wenkai-bold` | LXGW WenKai Medium | 手写楷体粗 | 标题 | SIL OFL |
| `zcool-kuaile` | ZCOOL KuaiLe（站酷快乐体） | 圆润可爱 | 轻松/娱乐标题 | SIL OFL |
| `zcool-qingke-huangyou` | ZCOOL QingKe HuangYou（庆科黄油体） | 手写潮流 | 时尚/年轻标题 | SIL OFL |
| `zcool-xiaowei` | ZCOOL XiaoWei（站酷小薇体） | 清秀端正 | 正文/字幕 | SIL OFL |

### 英文字体

| 字体 ID | 名称 | 风格 | 用途 | 许可证 |
|---------|------|------|------|--------|
| `inter` | Inter | 现代无衬线 | 全场景 | SIL OFL |
| `montserrat` | Montserrat | 几何无衬线 | 标题 | SIL OFL |
| `poppins` | Poppins | 圆润几何 | 标题 | SIL OFL |
| `roboto` | Roboto | 中性现代 | 全场景 | Apache 2.0 |
| `oswald` | Oswald | 窄体无衬线 | 新闻/头条标题 | SIL OFL |
| `playfair-display` | Playfair Display | 优雅衬线 | 文艺/高端标题 | SIL OFL |

### 字体选择建议

**默认字体：LXGW WenKai Medium（霞鹜文楷中粗体）** — 手写楷体风格，兼具美观性和可读性，是口播/Vlog 类视频的最佳选择。如果未缓存，会自动下载。

**默认字号：120**（竖屏 1080px 宽度 ÷ 9 = 120），即每个汉字占屏幕宽度的约 1/9，在手机竖屏上清晰易读。

| 视频类型 | 推荐中文字体 | 推荐英文字体 |
|---------|------------|------------|
| **口播/Vlog（默认）** | **LXGW WenKai Medium** | Inter / Roboto |
| 科技/教程 | Noto Sans SC | Inter / Roboto |
| 新闻/资讯 | Noto Sans SC | Oswald / Montserrat |
| 文化/深度 | LXGW WenKai / Noto Serif SC | Playfair Display |
| 娱乐/轻松 | ZCOOL KuaiLe | Poppins |
| 时尚/潮流 | ZCOOL QingKe HuangYou | Montserrat |
| 正式/商务 | Noto Sans SC | Inter |

### 字体在 FFmpeg 中的使用

```bash
# 下载字体后，通过 --font-path 参数指定
python3 scripts/render_final.py --config render_config.json --output final.mp4 \
  --font-path fonts/LXGWWenKai-Regular.ttf
```

### 字体在 Remotion 中的使用

```tsx
// 方式 1: @remotion/google-fonts（英文字体推荐）
import { loadFont } from "@remotion/google-fonts/Inter";
const { fontFamily } = loadFont();

// 方式 2: @remotion/fonts（本地/CJK 字体推荐）
import { loadFont } from "@remotion/fonts";
loadFont({
  family: "LXGW WenKai",
  url: staticFile("fonts/LXGWWenKai-Regular.ttf"),
  format: "truetype",
});

// 方式 3: @remotion/google-fonts 加载 CJK
import { loadFont } from "@remotion/google-fonts/NotoSansSC";
const { fontFamily } = loadFont("normal", {
  weights: ["400", "700"],
  subsets: ["chinese-simplified"],  // 重要：指定子集减小体积
});
```

### 注意事项

- **CJK 字体体积大**（10-20MB），使用 `@remotion/google-fonts` 时务必指定 `subsets: ["chinese-simplified"]` 减小体积
- **字体缓存**：下载的字体保存在项目 `fonts/` 目录，`.gitignore` 已排除
- **中国用户**：字体下载自动使用 jsDelivr CDN 加速
- **商用安全**：目录中所有字体均为 SIL OFL 或 Apache 2.0 许可，可免费商用

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
- **影响范围**：缺少 drawtext 时，字幕烧录和封面文字会失败或自动降级。

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

## Known Issues & Solutions（已知问题与解决方案）

以下问题在实际视频制作（Day 7-8）中遇到并验证了解决方案。

### K1: 混合帧率源素材用 concat copy 导致视频冻结

**症状**：使用 `ffmpeg -f concat -c copy` 拼接多段 B-roll 素材后，视频在约 1 分钟处画面冻结。

**根因**：DJI 素材为 30/1 fps，部分 DJI 和所有 iPhone MOV 文件为 30000/1001 (29.97fps)。`-c copy` 拼接不会重新编码，帧率不同导致时间戳不连续，播放器在切换点卡死。

**解决**：拼接不同来源的 B-roll 素材前，必须先统一帧率再拼接。对每段素材预处理：
```bash
ffmpeg -i clip.MOV -vf "fps=30" -pix_fmt yuv420p -c:a copy clip_30fps.mp4
```
然后再用 concat 拼接。**禁止对混合帧率素材使用 `-c copy` concat。**

**检测方法**：拼接前用 `ffprobe -v 0 -select_streams v:0 -show_entries stream=r_frame_rate` 检查每个素材的帧率，如果不一致就必须重编码统一。

---

### K2: 复杂 filter_complex 中音频被截断

**症状**：音频在视频中途（如 1:55 处）突然消失，画面正常继续播放。

**根因**：在单个 `filter_complex` 中同时使用 `amix`（混合 `anullsrc` 做静音填充）+ `atempo`（变速）+ 视频处理，`amix` 的 `duration=longest` 与合成的 null 音频源配合不可靠，导致音频流提前结束。

**解决**：将音频处理拆为独立步骤，不要在一个 filter_complex 中同时做音频填充+变速+视频处理：
1. `adelay` 添加封面静音段
2. `atempo=1.25` 变速处理
3. `apad=whole_dur=X` 填充到目标时长
4. 导出为 WAV，然后用 `-c:v copy -c:a aac` 与视频合并

**原则**：音频填充 + 变速 + 视频处理，三者不要放在同一个 filter_complex 中。

---

### K3: render_final.py select filter 在大量片段时 OOM

**症状**：使用 render_final.py 渲染 100+ 片段（如语音旁白配 B-roll 场景）时报 "Cannot allocate memory"。

**根因**：`_clips_in_temporal_order()` 只检查是否所有 clip 来自同一视频且时间顺序递增，不检查是否有 broll 字段。当所有 clip 引用同一个语音视频（配不同 B-roll 画面）时，仍会走 select filter 路径，生成包含 100+ 个 `between()` 表达式的巨型 select 过滤器，导致 OOM。

**解决**：
- 对于语音旁白+B-roll 工作流（大量片段、每个片段有独立 broll），绕过 render_final.py，使用手动 ffmpeg 管线：
  1. 将所有 B-roll 片段统一帧率后 concat 拼接
  2. 单独处理音频（提取、裁切、变速、填充）
  3. 用 `-c:v copy -c:a aac` 合并视频和音频
- 如果必须使用 render_final.py，确保片段数量在合理范围内（建议 < 50 个片段）

**代码层面**：`_clips_in_temporal_order()` 应增加 broll 检查，当任意 clip 含有 broll 字段时返回 False，强制走 trim/concat 路径。

---

### K4: 中文字幕中英文单词被截断

**症状**：字幕换行时英文单词（如 "OpenClaw"、"Claude Code"）被从中间劈开。

**根因**：`wrap_subtitle_text()` 的中文模式按字符数计算行宽，将英文字母视为与汉字等宽的单个字符。在中间位置换行时不感知 ASCII 单词边界，直接截断。

**解决**：换行函数需要：
1. 使用显示宽度计算：CJK 字符 = 1 单位，ASCII 字符约 0.5 单位
2. 在查找换行点时，检测 ASCII 单词边界（连续的字母/数字视为一个词），不在英文单词或数字中间断行
3. 优先在标点、空格、CJK/ASCII 边界处换行

---

### K5: 封面/结尾卡片不出现

**症状**：渲染配置中配置了 end_cards，但最终视频中看不到结尾卡片。

**根因**：B-roll 拼接后的总时长远超所需（如实际需要 215 秒但 B-roll 拼了 431 秒）。将封面+B-roll+结尾卡片 concat 后，用 `-t` 参数限制总时长时，结尾卡片被截断在时长限制之外。

**解决**：在拼接封面和结尾卡片之前，必须先将 B-roll 精确裁切到与音频时长匹配：
```bash
# 1. 获取音频时长
audio_dur=$(ffprobe -v 0 -show_entries format=duration -of csv=p=0 audio.wav)
# 2. 裁切 B-roll 到音频时长
ffmpeg -i broll_concat.mp4 -t $audio_dur -c copy broll_trimmed.mp4
# 3. 再拼接封面 + 裁切后的 B-roll + 结尾卡片
```
**原则**：拼接前确保每个部分的时长是精确已知的，不要依赖 `-t` 来事后截断。
