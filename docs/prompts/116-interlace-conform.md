# Interlace Conform 交错扫描检测与逐行工作副本

用于旧电视节目、DV、DVD/VOB、采集卡或摄像机素材出现梳齿、场序错误、`TFF/BFF`、`1080i/576i/480i` 等情况。它先区分 progressive、真实交错、疑似 telecine 和混合/不确定素材，再决定是否创建逐行工作副本。

## 核心边界

- 疑似 3:2 pulldown / telecine 时停止，先走 inverse telecine（IVTC）。直接去交错会损失原本可以恢复的逐行帧。
- 已是 progressive 的素材保持原样。对它运行去交错只会增加一次有损重编码。
- `mixed_or_uncertain` 需要逐帧看运动区域；只有保存了判断依据时才使用 `--classification-override interlaced_tff|interlaced_bff`。
- HDR、BT.2020 或高于 8-bit 的源片不进入本工具的 H.264/yuv420p 工作副本，应先确定单独的色彩流程。

## 先检测

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile core_edit \
  --profile interlace \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict

python3 scripts/interlace_conform.py analyze origin/broadcast.mkv \
  --project-dir . \
  --sample-seconds 8 \
  --output work/interlace_analysis.json \
  --markdown work/interlace_analysis.md
```

`analyze` 在头、中、尾各取一段 FFmpeg `idet` 样本，保存 repeated fields、single/multiple frame 统计、stream `field_order`、分类理由和限制。它是采样式启发检测，不能证明原始拍摄制式；剪辑混源、动画、静帧和 cadence break 仍需人工逐帧确认。

## 创建计划并执行

确认是真实 TFF/BFF 交错后：

```bash
python3 scripts/interlace_conform.py plan origin/broadcast.mkv \
  --project-dir . \
  --mode field \
  --parity auto \
  --reviewed-by editor \
  --note "运动区域持续梳齿，idet 与逐帧检查均为 TFF" \
  --delivery work/broadcast-progressive.mp4 \
  --comparison verify/broadcast-interlace-compare.mp4 \
  --output work/interlace_conform_plan.json \
  --markdown work/interlace_conform_plan.md

python3 scripts/interlace_conform.py apply \
  work/interlace_conform_plan.json \
  --markdown work/interlace_conform_plan.md
```

默认优先 `bwdif`，缺失时把 `yadif` fallback 写入 warning。`--mode frame` 保持输入帧率；`--mode field` 每个 field 输出一帧并精确加倍有理帧率，适合保留体育、新闻、家庭录像的场时间运动。`--parity auto` 会从确认后的 `interlaced_tff/interlaced_bff` 解析场序。

apply 只写项目内新的 H.264/AAC MP4 和全长左右 A/B 文件。输出需明确标记 progressive、通过 live `idet`、目标帧率、尺寸、时长、音画起止、SHA-256 与 FFmpeg `-xerror` 全量解码检查后才提升；左侧为原片，右侧为逐行工作副本。

## 完整审片与 gate

以 1× 播放完整 comparison，重点看横向运动、斜线、细文字、发丝、快速 pan 和口型：

```bash
python3 scripts/interlace_conform.py confirm \
  work/interlace_conform_plan.json \
  --reviewed-by editor \
  --note "完整 1× 播放，梳齿已消失，运动、细节、场序和同步正常" \
  --full-playback completed \
  --residual-combing pass \
  --motion-smoothness pass \
  --line-detail pass \
  --field-order pass \
  --audio-sync pass \
  --markdown work/interlace_conform_plan.md

python3 scripts/interlace_conform.py verify \
  work/interlace_conform_plan.json \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require interlace_conform_plan \
  --strict
```

任一复核项为 `fail` / `unobservable`，或源片、输出、comparison、FFmpeg 版本/filter、检测统计、计划内容发生漂移，gate 都会阻断。后续转写、裁切、字幕和渲染使用 `work/broadcast-progressive.mp4`，保留 `origin/` 原片。

## 给 Agent 的提示词

```text
先对旧电视/DV素材运行 runtime_preflight 的 interlace profile，再用 interlace_conform.py analyze 判断 progressive、真实交错、telecine candidate 或 uncertain。telecine 停止并建议 IVTC；真实交错才创建 source-bound plan。优先 bwdif，明确 frame/field mode 和 TFF/BFF，apply 后完整播放左右 A/B，五项全部 pass 才 confirm。最后 live verify manifest，后续只用逐行工作副本，原片不覆盖。
```
