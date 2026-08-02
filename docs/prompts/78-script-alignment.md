# Target Script Alignment 目标脚本对齐剪辑

当成片文案已经确定，但同一句话可能录了多个 take、分散在不同素材里，使用 `script_alignment.py` 把目标文案逐行匹配回原始口播时间段。它只做本地词面匹配与剪辑计划，不调用 LLM、不转写、不渲染，也不会修改源素材。

## 适用场景

- 已有客户/编导确认的成片稿，需要从多次口播中找回最匹配原话。
- 新闻、课程、产品发布或广告口播按既定稿件装配 A-roll。
- 目标稿调整了段落顺序，需要按新顺序生成 `render_config.json`。
- 多个 take 内容几乎相同，需要把候选时间码和分数交给人工选最终版本。

如果目标稿是大幅改写、同义转述，而原素材没有相同或近似措辞，词面匹配会进入 `low_score`；此时应人工找片、重新录音，或先由 Agent 理解语义后显式确认时间段，不能把低分候选当成准确对齐。

## 第一次运行

目标脚本建议一行一个完整口播单元；Markdown 标题只作为 section，不参与匹配：

```markdown
# 开场
90% 的自动化时间都浪费在重复操作上。

# 方法
第一步先把输入和输出固定下来。
第二步再让 Agent 执行中间步骤。
```

先对每条素材生成带词级时间戳的 reviewed transcript，再运行：

```bash
python3 scripts/script_alignment.py \
  --target-script work/target_script.md \
  --transcript take-a=work/takes/take-a_transcript_reviewed.json \
  --transcript take-b=work/takes/take-b_transcript_reviewed.json \
  --media take-a=origin/take-a.mp4 \
  --media take-b=origin/take-b.mp4 \
  --output work/script_alignment.json \
  --markdown work/script_alignment.md \
  --render-config work/render_config.json \
  --clean-script work/clean_script.md \
  --strict
```

如果 transcript 顶层 `source.path` / `video` 已记录媒体路径，可以省略相应 `--media`。目录里有多份 transcript 时也可以使用：

```bash
python3 scripts/script_alignment.py \
  --target-script work/target_script.md \
  --transcripts-dir work/takes \
  --output work/script_alignment.json \
  --markdown work/script_alignment.md \
  --render-config work/render_config.json \
  --clean-script work/clean_script.md \
  --strict
```

默认按非空行拆目标稿。一个长段落需要按句拆分时加 `--target-unit sentence`。

## 匹配证据

每个 target unit 会输出：

- `chosen`：当前首选的 source label、时间段、原话和稳定 candidate id。
- `candidates[]`：默认前三名备选；同一句有多个 take 时可直接比较。
- `score_breakdown`：sequence、target/source coverage、字符 n-gram overlap、length fit 和 exact 标记。
- `timing_granularity`：`word` 表示切点来自词级时间戳；`segment` 表示只能保守落在 ASR segment 边界。
- `selection_origin`：`automatic` 或 `human_choice`。
- `blocking_reasons`：`low_score`、`review_score`、`ambiguous_match`、`no_candidate`、`source_media_*`、`overlap_conflict` 等。

默认分数 65 以下不采用；65-82 进入人工 review；82 以上仍会在第一、第二候选相差不超过 3 分时要求人工确认。源时间段默认不能被两个目标句重复占用。

## 解决重复 take / 多解

第一次 `--strict` 因 `ambiguous_match` 退出 2 时，打开 `work/script_alignment.md`，把确认后的 candidate id 写进 choices：

```json
{
  "choices": {
    "target-001": "<candidate-id copied from target-001 table>",
    "target-002": "<candidate-id copied from target-002 table>"
  }
}
```

然后重跑并加：

```bash
  --choices work/script_alignment_choices.json
```

显式人工 choice 会解决低分/同分歧义，但不会绕过素材缺失或源时间重叠。确实需要重复使用同一原话时才加 `--allow-reuse`，并在成片中复核听感与语义。

## 下游使用

- `work/render_config.json` 按目标脚本顺序写 clips，可直接交给 `render_final.py`、`edit_preflight.py` 或 EDL/FCPXML/OTIO 导出。
- `work/clean_script.md` 是规范化后的已审目标稿，供 `content_guard.py`、分镜、字幕和发布文案继续使用。
- `pipeline_manifest.py` 会发现 `script_alignment.json`；只要 `summary.blocking > 0` 就阻塞后续 gate。也可以显式加 `--require script_alignment`。

```bash
python3 scripts/edit_preflight.py \
  --config work/render_config.json \
  --output work/edit_preflight.json \
  --strict

python3 scripts/render_final.py \
  --config work/render_config.json \
  --output output/final.mp4 \
  --versioned-output
```

## 边界

- 匹配器只看 transcript 文字与时间戳，不判断表情、镜头、收音质量或表演优劣；同分 take 必须看/听素材。
- 词级 transcript 能把 exact match 收紧到词边界；只有 segment 时间戳时可能带入目标句前后的多余口播。
- 不会把没说过的话伪造进成片，也不会通过低分匹配宣称语义等价。
- `render_config` 可以在 review 阶段输出预览候选，但 `summary.blocking` 未清零时不应进入最终渲染或发布。
