# Remotion Voiceover Integration Guide

当有语音但没有画面（或只有语音）时，使用 Remotion 生成配合语音的视频画面。

## 适用场景

1. **纯音频口播** — 有录音/TTS 语音，需要生成匹配的画面
2. **音频 + 静态图片** — 有语音和一些图片素材，需要组合成动态视频
3. **播客可视化** — 将播客/对话音频转为带字幕和视觉效果的视频
4. **解说视频** — 配音 + 文字动画 + 背景图的组合

## Remotion 核心架构

### 项目结构

```
remotion-voiceover/
├── src/
│   ├── Root.tsx                 # 注册所有 Composition
│   ├── compositions/
│   │   ├── VoiceoverVideo.tsx   # 主 Composition（组合所有场景）
│   │   ├── Scene.tsx            # 单个场景组件
│   │   └── types.ts             # 类型定义
│   ├── components/
│   │   ├── AnimatedSubtitle.tsx  # 字幕动画组件
│   │   ├── BackgroundVisual.tsx  # 背景视觉效果
│   │   ├── AudioWaveform.tsx     # 音频波形可视化
│   │   ├── KenBurnsImage.tsx     # Ken Burns 图片动效
│   │   ├── LowerThird.tsx        # 下方三分之一信息条
│   │   ├── ProgressBar.tsx       # 进度条
│   │   └── TransitionEffect.tsx  # 转场效果
│   ├── hooks/
│   │   └── useAudioSync.ts      # 音频同步 Hook
│   └── styles/
│       └── fonts.ts             # 字体加载配置
├── public/
│   ├── audio/                   # 语音文件
│   ├── images/                  # 图片素材
│   └── fonts/                   # 本地字体文件
├── timeline.json                # 时间轴配置（AI 生成）
├── package.json
├── remotion.config.ts
└── tsconfig.json
```

### 关键依赖

```json
{
  "dependencies": {
    "remotion": "^4.x",
    "@remotion/cli": "^4.x",
    "@remotion/media-utils": "^4.x",
    "@remotion/captions": "^4.x",
    "@remotion/google-fonts": "^4.x",
    "@remotion/fonts": "^4.x",
    "@remotion/transitions": "^4.x",
    "react": "^18.x",
    "react-dom": "^18.x"
  }
}
```

## 视频模板样式

### 1. 口播字幕卡片风格（TikTok/抖音风）

适合：短视频口播、知识分享

```
┌─────────────────────────┐
│                         │
│   ┌─────────────────┐   │
│   │  渐变/纯色背景   │   │
│   │                 │   │
│   │   ┌─────────┐   │   │
│   │   │ 关键词   │   │   │
│   │   │ 高亮显示 │   │   │
│   │   └─────────┘   │   │
│   │                 │   │
│   └─────────────────┘   │
│                         │
│  ━━━━━━━━━━━━━━━━━━━━  │  ← 进度条
│  大号字幕逐词高亮显示    │  ← TikTok 风格字幕
│                         │
└─────────────────────────┘
```

**组件结构：**
```tsx
<AbsoluteFill>
  <GradientBackground colors={["#1a1a2e", "#16213e"]} />
  <KeywordCard word={currentKeyword} />
  <AnimatedSubtitle
    captions={captions}
    style="tiktok"           // 逐词高亮
    fontSize={64}
    highlightColor="#FFD700"
  />
  <ProgressBar progress={frame / durationInFrames} />
  <Audio src={voiceoverAudio} />
</AbsoluteFill>
```

**关键 API：**
- `@remotion/captions` → `createTikTokStyleCaptions()` 生成逐词字幕
- `useCurrentFrame()` + `interpolate()` 控制高亮动画
- `spring()` 实现弹性动效

### 2. 图文解说风格（教程/科普）

适合：教程、科普、产品介绍

```
┌─────────────────────────┐
│  标题区域                │
│  ─────────────────────  │
│                         │
│  ┌──────┐  ┌──────────┐ │
│  │      │  │ 文字说明  │ │
│  │ 图片  │  │ 逐行淡入  │ │
│  │ Ken   │  │          │ │
│  │ Burns │  │ • 要点1   │ │
│  │      │  │ • 要点2   │ │
│  └──────┘  └──────────┘ │
│                         │
│  字幕文字                │
└─────────────────────────┘
```

**组件结构：**
```tsx
<AbsoluteFill>
  <Sequence from={0} durationInFrames={sceneFrames}>
    <BackgroundVisual type="gradient" />
    <TitleBar text={sceneTitle} />
    <KenBurnsImage src={image} direction="zoomIn" />
    <BulletPoints items={points} staggerDelay={15} />
    <AnimatedSubtitle captions={captions} style="bottom" />
  </Sequence>
  <Audio src={voiceoverAudio} />
</AbsoluteFill>
```

**关键技术：**
- Ken Burns 效果：`interpolate(frame, [0, duration], [1, 1.2])` 控制 scale
- 要点逐行淡入：每个 item 用 `spring({ frame: frame - delay * index })` 控制 opacity
- 图片与文字左右分栏布局

### 3. 全屏字幕动画风格（Kinetic Typography）

适合：情感类、激励类、文案类

```
┌─────────────────────────┐
│                         │
│                         │
│      这是一段           │  ← 大号文字
│      非常重要的          │  ← 逐行弹入
│      内容               │  ← 带弹性动效
│                         │
│                         │
│                         │
└─────────────────────────┘
```

**组件结构：**
```tsx
<AbsoluteFill style={{ backgroundColor: "#000" }}>
  <Sequence from={0} durationInFrames={sentenceFrames}>
    {lines.map((line, i) => (
      <AnimatedLine
        key={i}
        text={line}
        enterFrame={i * staggerFrames}
        animation="springIn"    // springIn | fadeUp | typewriter
        fontSize={80}
        color="#fff"
      />
    ))}
  </Sequence>
  <Audio src={voiceoverAudio} />
</AbsoluteFill>
```

**文字动画类型：**
- **springIn**：`spring()` 控制 translateY + opacity（从下方弹入）
- **fadeUp**：`interpolate()` 线性淡入 + 上移
- **typewriter**：字符串逐字截取 `text.slice(0, charIndex)`
- **wordHighlight**：当前说到的词变色/放大

### 4. 播客/对话可视化风格

适合：播客、访谈、双人对话

```
┌─────────────────────────┐
│  播客标题                │
│  ─────────────────────  │
│                         │
│  ┌────┐     ┌────┐      │
│  │头像│     │头像│      │
│  │说话│     │    │      │  ← 说话者头像发光
│  └────┘     └────┘      │
│   名字A      名字B       │
│                         │
│  ≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋  │  ← 音频波形
│                         │
│  "当前说的话引用显示"     │
└─────────────────────────┘
```

**关键 API：**
- `useAudioData()` + `visualizeAudio()` 生成频谱数据
- `visualizeAudioWaveform()` 生成波形数据
- 说话者检测：根据字幕时间戳判断当前说话者

### 5. 新闻/资讯播报风格

适合：新闻播报、行业资讯、每日简报

```
┌─────────────────────────┐
│  ┌──────────────────┐   │
│  │ BREAKING NEWS    │   │  ← 顶部红色横幅
│  └──────────────────┘   │
│                         │
│  背景图片/视频           │
│                         │
│  ┌──────────────────┐   │
│  │ 下方信息条        │   │  ← Lower Third
│  │ 标题 | 副标题     │   │
│  └──────────────────┘   │
│                         │
│  滚动字幕 ←←←←←←←←←←←  │  ← Ticker
└─────────────────────────┘
```

### 6. 幻灯片/演示风格（Slideshow）

适合：产品介绍、旅行 Vlog、相册

```
场景1 ──fade──> 场景2 ──slide──> 场景3
┌─────────┐    ┌─────────┐    ┌─────────┐
│  图片1   │    │  图片2   │    │  图片3   │
│ +Ken Burns│   │ +Ken Burns│   │ +Ken Burns│
│         │    │         │    │         │
│  说明文字 │    │  说明文字 │    │  说明文字 │
└─────────┘    └─────────┘    └─────────┘
```

**转场效果（@remotion/transitions）：**
- `fade()` — 淡入淡出
- `slide()` — 滑动
- `wipe()` — 擦除
- `clockWipe()` — 时钟擦除
- `flip()` — 翻转
- `none()` — 无转场（硬切）
- 自定义 `customPresentation()` — 任意 CSS 动画

## 核心 API 参考

### 音频处理

```tsx
import { Audio, useCurrentFrame, Sequence } from "remotion";
import { getAudioData, useAudioData, visualizeAudio, visualizeAudioWaveform } from "@remotion/media-utils";

// 1. 加载音频数据
const audioData = useAudioData(audioSrc);  // Hook 方式
// 或
const audioData = await getAudioData(audioSrc);  // 异步方式

// 2. 生成频谱数据（用于柱状可视化）
const visualization = visualizeAudio({
  fps,
  frame,
  audioData,
  numberOfSamples: 256,  // 频谱柱数量
  smoothing: true,
});
// 返回: [0.22, 0.1, 0.01, ...] — 每个值代表一个频段的振幅

// 3. 生成波形数据
const waveform = visualizeAudioWaveform({
  fps,
  frame,
  audioData,
  numberOfSamples: 512,
  windowSize: 2048,
});

// 4. 大文件优化（只加载当前帧附近的音频）
import { useWindowedAudioData } from "@remotion/media-utils";
const windowedData = useWindowedAudioData({
  src: audioSrc,     // 必须是 .wav 格式
  frame,
  fps,
  windowSize: 4096,
});
```

### 字幕系统

```tsx
import { createTikTokStyleCaptions, parseSrt } from "@remotion/captions";

// 1. 解析 SRT/VTT 字幕文件
const captions = parseSrt(srtContent);

// 2. 从 Whisper JSON 转换
// transcript.json 的 segments 可以映射为 captions 格式:
const captions = segments.map(seg => ({
  text: seg.text,
  startMs: seg.start * 1000,
  endMs: seg.end * 1000,
  timestampMs: seg.start * 1000,
  confidence: 1.0,
}));

// 3. 生成 TikTok 风格逐词字幕
const { pages } = createTikTokStyleCaptions({
  captions,
  combineTokensWithinMilliseconds: 800,  // 合并间隔
});
```

### 动画工具

```tsx
import { useCurrentFrame, interpolate, spring, Easing } from "remotion";

const frame = useCurrentFrame();

// 1. 线性插值
const opacity = interpolate(frame, [0, 30], [0, 1], {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
});

// 2. 弹性动画
const scale = spring({
  frame,
  fps: 30,
  config: { damping: 200, stiffness: 100, mass: 0.5 },
});

// 3. 缓动函数
const progress = interpolate(frame, [0, 60], [0, 1], {
  easing: Easing.bezier(0.25, 0.1, 0.25, 1),
});
```

### 转场效果

```tsx
import { TransitionSeries, linearTiming, springTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";

<TransitionSeries>
  <TransitionSeries.Sequence durationInFrames={90}>
    <Scene1 />
  </TransitionSeries.Sequence>
  <TransitionSeries.Transition
    presentation={fade()}
    timing={springTiming({ config: { damping: 200 } })}
  />
  <TransitionSeries.Sequence durationInFrames={90}>
    <Scene2 />
  </TransitionSeries.Sequence>
</TransitionSeries>
```

### 字体加载

```tsx
// 方式1: @remotion/google-fonts（推荐英文字体）
import { loadFont } from "@remotion/google-fonts/Inter";
const { fontFamily } = loadFont();
// 或指定子集和字重:
const { fontFamily } = loadFont("normal", {
  weights: ["400", "700"],
  subsets: ["latin"],
});

// 方式2: @remotion/fonts（本地字体/自定义字体）
import { loadFont } from "@remotion/fonts";
loadFont({
  family: "LXGW WenKai",
  url: staticFile("fonts/LXGWWenKai-Regular.ttf"),
  format: "truetype",
  weight: "400",
}).then(() => console.log("Font loaded"));

// 方式3: Google Fonts CSS 导入
// 在全局 CSS 中:
// @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700&display=swap');

// CJK 字体注意事项:
// - CJK 字体文件通常很大 (10-20MB)，需要指定 subsets 减小体积
// - 使用 @remotion/google-fonts 时指定 subsets: ["chinese-simplified"]
// - 本地字体加载需要确保渲染前完成加载
```

## 时间轴配置格式（timeline.json）

AI Agent 根据语音内容生成此配置，Remotion 据此渲染视频：

```json
{
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "audioSrc": "audio/voiceover.wav",
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
      "subtitle": "关于 AI 的未来",
      "background": {
        "type": "gradient",
        "colors": ["#667eea", "#764ba2"]
      },
      "transition": {
        "type": "fade",
        "durationMs": 500
      }
    },
    {
      "id": 2,
      "startMs": 5000,
      "endMs": 15000,
      "type": "content",
      "text": "AI 正在改变我们的生活方式",
      "image": "images/ai-illustration.jpg",
      "imageAnimation": "kenBurns",
      "layout": "imageLeft",
      "bullets": [
        "自动化工作流程",
        "个性化推荐",
        "创意辅助工具"
      ],
      "transition": {
        "type": "slide",
        "direction": "left",
        "durationMs": 300
      }
    },
    {
      "id": 3,
      "startMs": 15000,
      "endMs": 25000,
      "type": "kinetic",
      "lines": [
        "这不是未来",
        "这是现在"
      ],
      "animation": "springIn",
      "staggerMs": 500
    }
  ],
  "captions": {
    "enabled": true,
    "style": "tiktok",
    "position": "bottom",
    "fontSize": 56,
    "highlightColor": "#FFD700",
    "backgroundColor": "rgba(0,0,0,0.6)",
    "maxLines": 2
  },
  "progressBar": {
    "enabled": true,
    "position": "bottom",
    "color": "#e94560",
    "height": 4
  },
  "endCard": {
    "text": "感谢观看\n关注了解更多",
    "durationMs": 3000
  }
}
```

## 场景类型说明

| type | 用途 | 视觉效果 |
|------|------|---------|
| `title` | 片头/标题页 | 大号标题 + 副标题 + 渐变背景 |
| `content` | 图文内容页 | 图片 + 文字说明 + 要点列表 |
| `kinetic` | 动态文字页 | 全屏文字逐行弹入 |
| `quote` | 引用/金句页 | 引号装饰 + 大号引文 + 出处 |
| `comparison` | 对比页 | 左右分栏对比 |
| `stats` | 数据展示页 | 数字动画 + 图表 |
| `image` | 纯图片页 | 全屏图片 + Ken Burns 动效 |
| `podcast` | 播客可视化 | 头像 + 波形 + 引用文字 |
| `blank` | 过渡空白页 | 纯色/渐变背景 |

## 背景视觉类型

| background.type | 效果 |
|-----------------|------|
| `gradient` | 渐变色 `colors: ["#667eea", "#764ba2"]` |
| `solid` | 纯色 `color: "#000000"` |
| `image` | 图片背景（带可选暗色遮罩） |
| `particles` | 粒子动画背景 |
| `waves` | 波浪动画背景 |
| `grid` | 网格线背景 |
| `blur` | 模糊图片背景 |

## 文字动画参考

| animation | 效果 | 适用场景 |
|-----------|------|---------|
| `springIn` | 弹性弹入（从下方） | 标题、重点句 |
| `fadeUp` | 淡入上移 | 正文、说明 |
| `typewriter` | 打字机效果 | 代码、引用 |
| `wordHighlight` | 逐词高亮 | 字幕跟读 |
| `scaleIn` | 缩放弹入 | 数字、关键词 |
| `slideLeft` | 从右滑入 | 列表项、要点 |
| `blur` | 模糊到清晰 | 背景切换 |

## 渲染命令

```bash
# 开发预览
npx remotion studio

# 渲染视频
npx remotion render VoiceoverVideo out/video.mp4 \
  --props='{"timelineUrl": "timeline.json"}' \
  --codec=h264 \
  --crf=18

# 竖屏 9:16 (1080x1920)
npx remotion render VoiceoverVideo out/vertical.mp4 \
  --width=1080 --height=1920

# 横屏 16:9 (1920x1080)
npx remotion render VoiceoverVideo out/horizontal.mp4 \
  --width=1920 --height=1080

# 使用 GPU 加速渲染
npx remotion render VoiceoverVideo out/video.mp4 \
  --gl=angle  # 使用 GPU 加速
```

## 与现有工作流集成

### 工作流程

1. **音频准备** — 使用现有 `extract_audio.py` 提取音频，或直接使用 TTS 生成的音频
2. **语音识别** — 使用现有 `transcribe.py` 生成 transcript.json
3. **时间轴生成** — AI Agent 根据 transcript 内容生成 timeline.json
4. **素材准备** — AI 搜集/生成需要的图片素材
5. **Remotion 渲染** — 使用 timeline.json 渲染最终视频
6. **后处理（可选）** — 使用 `render_final.py` 与其他视频片段合并

### transcript.json → timeline.json 转换逻辑

```
transcript.json 的 segments:
  [
    {id:1, start:0.0, end:2.5, text:"大家好"},
    {id:2, start:2.5, end:5.1, text:"今天聊一个话题"},
    ...
  ]

  ↓ AI Agent 分析内容语义 ↓

timeline.json 的 scenes:
  - 将多个 segments 按语义分组为 scenes
  - 每个 scene 选择合适的 type（title/content/kinetic 等）
  - 为每个 scene 选择背景、动画、布局
  - 生成 captions 配置
```

## 常用 Remotion 模板参考

| 模板 | 来源 | 特点 |
|------|------|------|
| **Prompt to Video** | `remotion-dev/template-prompt-to-video` | AI 生成故事 + ElevenLabs TTS + 图片，完整口播管线 |
| **TikTok Template** | `remotion-dev/template-tiktok` | TikTok 风格短视频，逐词字幕 |
| **Overlay Template** | `remotion-dev/template-overlay` | 叠加层模板，可集成到传统剪辑软件 |
| **Fireship Template** | `thecmdrunner/remotion-fireship` | 类 Fireship 风格教程视频 |
| **Ken Burns Effect** | React Video Editor | 图片平移缩放效果 |
| **Watercolor Map** | Remotion 官方 | 旅行视频地图动画 |
| **Text Animations** | Remotion 官方 Snippets | 打字机、词高亮等文字动效 |

## 注意事项

1. **CJK 字体必须预加载** — 中文字体文件大（10-20MB），必须在渲染前确保加载完成
2. **音频格式** — `useWindowedAudioData` 仅支持 `.wav` 格式
3. **竖屏优先** — 移动端短视频默认 1080x1920 (9:16)
4. **性能** — 复杂场景建议使用 `--concurrency=4` 并行渲染
5. **字幕时间对齐** — 字幕时间来自 transcript.json，必须与音频精确对应
