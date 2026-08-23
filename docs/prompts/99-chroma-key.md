# Chroma Key 绿幕 / 蓝幕抠像与换背景

适用于已经在绿幕、蓝幕或已知纯色幕布前拍摄的视频。流程只使用本地 FFmpeg；不上传素材、不调用 AI 抠图服务，也不消耗生成额度。

## 为什么先预览再完整渲染

单条 `chromakey` 命令看不出头发、手指、半透明物体、阴影、同色衣物和溢色是否被破坏。`chroma_key.py` 固定执行：

1. `prepare`：绑定前景/背景字节和参数，在 15% / 50% / 85% 时间点生成合成图与黑白 matte。
2. `review`：人工逐项确认边缘、主体完整性、溢色和背景匹配。
3. `apply`：四项全部 `pass` 后才完整渲染 H.264/AAC 成片。
4. `verify`：现场重读源、背景、预览和输出，重算媒体契约与 canonical report id。

## 完整流程

```bash
python3 scripts/chroma_key.py prepare \
  --project-dir . \
  --foreground origin/presenter-green.mp4 \
  --background origin/studio.png \
  --output-video output/presenter-studio.mp4 \
  --preview-dir verify/chroma_key \
  --key-color green \
  --similarity 0.10 \
  --blend 0.08 \
  --despill 0.50 \
  --report work/chroma_key.json \
  --markdown work/chroma_key.md
```

打开 `work/chroma_key.md` 中列出的每组 composite / matte：

- `edge_quality`：头发、手指、衣物边缘没有明显锯齿、硬边或透明光晕。
- `subject_integrity`：脸、衣物、道具和半透明区域没有被打洞；同幕布颜色的主体细节仍在。
- `spill_control`：头发、肩膀和反光表面没有明显绿边/蓝边，也没有因过强 despill 变色。
- `background_fit`：裁切、透视、景深、亮度和色温不会让人物像漂浮在背景上。

四项确认后记录 review：

```bash
python3 scripts/chroma_key.py review \
  --report work/chroma_key.json \
  --reviewer "<reviewer-label>" \
  --note "早中晚合成图与 matte 已看；头发、手、衣物、溢色和背景匹配可接受" \
  --edge-quality pass \
  --subject-integrity pass \
  --spill-control pass \
  --background-fit pass \
  --markdown work/chroma_key.md

python3 scripts/chroma_key.py apply \
  --report work/chroma_key.json \
  --markdown work/chroma_key.md \
  --strict

python3 scripts/chroma_key.py verify \
  --project-dir . \
  --report work/chroma_key.json \
  --strict
```

发布/交付前如果必须存在该 gate：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require chroma_key \
  --strict
```

## 调参顺序

先从默认值开始，每次只改一个参数并重新 `prepare --force`：

- 幕布仍残留：小幅提高 `--similarity`。
- 主体被打洞：降低 `--similarity`，必要时降低 `--blend`。
- 边缘太硬：小幅提高 `--blend`，但必须防止透明光晕。
- 仍有绿边/蓝边：小幅提高 `--despill`；真实绿色/蓝色衣物变色时降低。
- 蓝幕：使用 `--key-color blue`。自定义纯色可传 `#RRGGBB`，脚本会记录推断的 green/blue despill family，并保留人工复核 warning。
- 特殊时间点更容易暴露问题：重复 `--time <seconds>` 自选 1–6 帧，不只看默认早/中/晚。

## 输出和边界

- 图片背景保持整段；视频背景从第一帧循环，声音被忽略。最终声音只沿用前景视频。
- 背景会按前景尺寸等比放大并居中裁切；成片尺寸、帧率和时长以当前景为准。
- `apply` 默认拒绝覆盖；确认要替换同一路径旧输出时才加 `--force`。前景、背景、预览、报告和输出不能互相覆盖或通过 hard link 指向同一文件。
- 报告存在即由 `pipeline_manifest.py` live verify；任何源、背景、预览、参数、filter contract 或输出字节漂移都会阻塞。
- 代表帧不能证明整条视频。`apply` 后必须用 1× 完整播放，特别检查快速动作、motion blur、头发、手和背景循环接缝。
- 这不是无绿幕的人像分割、逐帧 roto 或 AI video matting。复杂阴影、透明物、反光、幕布皱褶或主体与幕布同色时，应重拍或交给专业 keyer/rotoscope。
- reviewer label 和 SHA-256 不是身份认证、数字签名或审美质量自动证明。
