# Clip Assembly — 多源视频安全拼接

用于把片头、主片、生成片、手机素材或横竖屏片段按明确顺序合成一条 MP4。输入可以有不同分辨率、方向、帧率、SAR、起始时间戳、采样率和声道数；脚本会在一条 FFmpeg filter graph 内统一后再 hard cut，不会先尝试未经验证的 `-c copy`。

## 适用范围

- 多条独立视频需要按给定顺序首尾拼接。
- 片头、主片、片尾来自不同编码或时间基。
- 生成片混合横屏、竖屏、无声片段或不同帧率。
- 旧 concat 结果会冻结、时长异常、缺声或逐段失步。

同一源片重复使用 `loop_fill.py`；单个 VFR 源进入复杂剪辑前可先用 `frame_rate_conform.py`；交错或 telecine 先用 `interlace_conform.py`。本工具只做 hard-cut assembly，不做 crossfade、J-cut/L-cut 或语义选镜。

## 1. 建立 source-bound 计划

```bash
python3 scripts/clip_assembly.py plan \
  origin/intro.mp4 \
  origin/main.mov \
  origin/outro.mp4 \
  --width 1080 \
  --height 1920 \
  --fps 30 \
  --fit contain \
  --audio-mode fill-silence \
  --delivery output/assembled.mp4 \
  --boundary-proof verify/clip-assembly-boundaries.mp4 \
  --project-dir . \
  --output work/clip_assembly_plan.json \
  --markdown work/clip_assembly_plan.md
```

省略 `--width/--height/--fps` 时沿用第一条视频的显示尺寸与精确 rate。尺寸必须为偶数。`--fit contain` 完整保留画面并补黑边；`--fit crop` 铺满画布并中心裁切，含人物、UI、Logo 或产品边缘时应先确定裁切是否安全。

`--audio-mode fill-silence` 在至少一条视频有音频时输出 48 kHz stereo AAC，并为无音轨片段生成等长静音；全部输入都无声时保持无声。`--audio-mode drop` 显式丢弃全部输入音频。已有音轨与视频首尾差异超过容差会阻断，避免在拼接时悄悄截断或拉长内容。

计划记录：

- 每条源文件的绝对路径、SHA-256、大小、显示尺寸、rotation、codec、色彩、音频和完整 decoded cadence；
- 最终顺序、目标画布、精确有理帧率、fit/audio 策略、预计总时长；
- 每处输出接缝的最终时间，以及其在 proof 视频中的位置；
- 交付件、proof、人工 review 和 canonical plan id。

HDR/BT.2020/>8-bit 输入会停止，先为该素材建立明确的 Rec.709 工作副本。原片、计划、交付件和 proof 必须都在项目目录内且互不覆盖。

## 2. 单次归一编码与技术验证

```bash
python3 scripts/clip_assembly.py apply \
  work/clip_assembly_plan.json \
  --markdown work/clip_assembly_plan.md
```

每条视频在同一次输出编码里执行：

- `setpts=PTS-STARTPTS` 重置时间戳；
- scale + pad/crop 统一显示尺寸；
- `fps` 统一 CFR，`setsar=1` 统一方形像素，`format=yuv420p` 统一像素格式；
- 音频统一为 48 kHz stereo，缺音轨片段按视频时长补静音；
- `concat` hard cut 后输出 H.264/AAC MP4。

临时文件只有在尺寸、rotation、SAR、decoded cadence、总时长、音频首尾、codec/pixel format 和完整 `ffmpeg -xerror` 解码通过后才原子提升。随后从确切交付件抽出每处接缝前后窗口，按顺序拼成一条正常速度 boundary proof，并执行相同媒体合同与完整解码检查。

## 3. 人工接缝复核

完整播放 `output/assembled.mp4` 和 `verify/clip-assembly-boundaries.mp4`，两条都使用 1×；有音频时打开声音。确认后记录：

```bash
python3 scripts/clip_assembly.py confirm work/clip_assembly_plan.json \
  --reviewed-by editor \
  --note "按顺序完整播放交付件，并逐处检查 proof 中的 hard cut" \
  --full-playback completed \
  --proof-playback completed \
  --clip-order pass \
  --visual-seams pass \
  --frame-continuity pass \
  --audio-seams pass \
  --complete-coverage pass \
  --markdown work/clip_assembly_plan.md
```

无声交付件把 `--audio-seams` 设为 `not_applicable`。任一项无法观察或失败都保留 blocker。重点检查：

- clip 顺序和首尾是否完整；
- 接缝是否出现冻结、重复帧、少帧、黑闪或构图突变；
- 声音是否有 click、pop、空洞、重复词或截断；
- contain 黑边或 crop 裁切是否符合交付意图。

## 4. 发布前 live verify

```bash
python3 scripts/clip_assembly.py verify \
  work/clip_assembly_plan.json --strict

python3 scripts/pipeline_manifest.py --project-dir . \
  --require clip_assembly_plan --strict
```

`verify` 会重新读取全部源片、交付件、proof 和人工 review，重算媒体/cadence 合同、设置、边界表、SHA-256 与 plan id。源片被替换、输出重编码、proof 改动、顺序/参数手改或人工结论不再绑定当前字节时都会阻断。

## 边界

- 本工具采用 hard cut，不自动增加转场；需要声音先行/延续时另用 `audio_transition.py`。
- `contain` / `crop` 只执行几何规则，不理解人物、UI、字幕或品牌安全区。
- `fps` 可以把 VFR 归一成 CFR；升帧会复制画面，降帧会丢运动采样。
- 归一化只处理 SDR 8-bit 交付；HDR、交错、telecine 与独立设备 clock drift 需要各自的前置流程。
- boundary proof 便于集中检查所有接缝，仍需完整播放最终交付件。
