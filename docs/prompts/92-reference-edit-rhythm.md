# Reference Edit Rhythm 参考视频剪辑节奏量化

当用户给出一条参考广告、短片或生成视频，并要求“参考这个节奏”“照这个片子的切法”时，用 `reference_edit_rhythm.py` 先量化结构，再对照最终成片。它只借鉴切点与时长结构，不复制参考片的画面、音频、品牌或故事内容。

## 先生成对照报告

把参考片复制到项目 `origin/`，候选成片放在 `output/`，然后运行：

```bash
python3 scripts/reference_edit_rhythm.py analyze \
  --project-dir . \
  --reference origin/reference-ad.mp4 \
  --candidate output/final.mp4 \
  --evidence-dir verify/reference_edit_rhythm \
  --output work/reference_edit_rhythm.json \
  --markdown work/reference_edit_rhythm.md \
  --strict
```

脚本会对两条视频运行 FFmpeg hard scene detection，并生成：

- 参考片与候选片的 SHA-256、大小、codec、像素格式、尺寸、fps、音轨和时长契约。
- `boundaries[]`、逐镜头时长、cuts/minute、median/p90/max shot、cadence CV、结尾 hold、归一化切点和 opening/middle/closing 切点占比。
- `verify/reference_edit_rhythm/reference_contact_sheet.jpg` 与 `candidate_contact_sheet.jpg`，两张图都绑定 hash。
- 切点密度、median shot、final-hold 比例、归一化切点位置和三阶段 cut share 的差异表。

默认结构差异是 `WARN`：它提醒人工比较，不会为了追求数字相同而机械加切点。如果“匹配参考节奏”是明确验收条件，加：

```bash
python3 scripts/reference_edit_rhythm.py analyze \
  --project-dir . \
  --reference origin/reference-ad.mp4 \
  --candidate output/final.mp4 \
  --evidence-dir verify/reference_edit_rhythm \
  --output work/reference_edit_rhythm.json \
  --markdown work/reference_edit_rhythm.md \
  --require-match \
  --strict
```

此时超过容差的结构差异进入 `summary.blocking`。需要重新分析同一路径时显式加 `--force`；默认不覆盖旧报告或 contact sheet。

## 发布前现场验证

```bash
python3 scripts/reference_edit_rhythm.py verify \
  --report work/reference_edit_rhythm.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require reference_edit_rhythm \
  --strict
```

`verify` 会重新读取参考片、候选片和两张 contact sheet，检查文件路径、symlink、大小、SHA-256、媒体契约、派生 metrics、comparison、summary 和 canonical report id。任何源文件重编码、替换、证据变化或手改派生字段都会让旧报告失效。

## 默认容差

| 指标 | 默认上限 | 含义 |
|---|---:|---|
| cut density relative delta | 40% | 每分钟 hard cut 数差异 |
| median shot relative delta | 50% | 中位镜头时长差异 |
| final-hold fraction delta | 0.15 | 结尾镜头占全片比例差异 |
| normalized boundary distance | 0.12 | 两组归一化切点的双向最近距离 |
| phase cut-share distance | 0.30 | opening/middle/closing 切点分布差异 |

容差可用对应 `--max-*` 参数调整，但应把调整理由写进项目记录。不要为了清零报告而复制参考片的具体镜头或受保护资产。

## 人工复核边界

- FFmpeg scene score 主要识别硬视觉变化；dissolve、match cut、持续运镜和镜头内动作可能漏检。参考片使用柔和转场时，先看 contact sheet，必要时降低 `--scene-threshold` 后重跑。
- 两张 contact sheet 只是抽样证据，不能替代两条视频的 1× 完整播放。
- 结构相似不是质量分、留存率预测、版权许可或品牌授权。只复制节奏结构，不复制 pixels/audio/branding/story。
- SHA-256 用于发现漂移，不是数字签名；最终发布仍需人工审批和 `approval_receipt.py`。

## 可直接交给 Agent 的任务描述

```text
请把参考视频复制到当前项目 origin/，用 reference_edit_rhythm.py 对参考片和最终候选片运行 hard-cut 节奏量化，生成两张 contact sheet、JSON 和 Markdown。默认把结构差异作为人工复核 warning；只有我明确把节奏相似度设为验收条件时才加 --require-match。完整看两条视频，禁止复制参考片的画面、音频、品牌或故事内容。发布前运行 verify 和 pipeline_manifest --require reference_edit_rhythm --strict。
```
