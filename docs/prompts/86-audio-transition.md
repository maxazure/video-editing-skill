# J-cut / L-cut Audio Transition — 声音先行 / 延续转场

用于两个已选片段之间需要更自然的声画错位衔接：

- **J-cut**：下一镜头的声音先进入，随后画面再切过去；
- **L-cut**：画面先切到下一镜头，上一镜头的声音继续一小段。

本流程不自动猜测转场位置。先完成 transcript review、片段选择和 `render_config.json`，再逐个边界试听并显式指定类型与时长。

从项目根目录运行命令。`render_config.json` 内的相对素材路径与 `render_final.py` 一致，按命令当前工作目录解析；计划会记录该工作目录，换目录执行会被 live verification 阻塞。

## 1. 生成计划

```bash
python3 scripts/audio_transition.py plan work/render_config.json \
  --transition 1,j_cut,0.40 \
  --transition 3,l_cut,0.55 \
  --output work/audio_transition_plan.json \
  --markdown work/audio_transition_plan.md
```

`--transition AFTER_CLIP,TYPE,DURATION` 可重复：

- `1,j_cut,0.40`：在第 1/2 个 clip 边界做 0.40 秒 J-cut；
- `3,l_cut,0.55`：在第 3/4 个 clip 边界做 0.55 秒 L-cut；
- 类型可写 `j_cut` / `l_cut`，短写 `j` / `l` 也可；
- 时长限制为 0.05–2.0 秒；没有足够源音频 handle 时直接阻塞。

计划绑定 render config、transcript、所有源视频/B-roll 的路径、大小和 SHA-256，并保存每段画面时间、音频 source time、output time、fade 和 canonical plan id。修改任何输入后必须重新生成。

计划 JSON、Markdown、最终视频和 receipt 默认都拒绝覆盖已有文件；迭代时使用带版本号的新路径，例如 `audio_transition_plan_v2.json`，不要复用旧文件名。

## 2. 时序语义

J-cut 会读取下一 clip 画面入点之前的源音频。例如 clip 2 从源片 10.0 秒开始，0.4 秒 J-cut 会从 9.6 秒开始放声音；到视觉切点时，声音与 clip 2 的 10.0 秒画面重新对齐。

L-cut 会读取上一 clip 画面出点之后的源音频，同时让下一 clip 的画面先出现。为恢复同步，下一 clip 的主音频从其入点加 overlap 时长处恢复；因此只能在这段是 room tone、ambience、呼吸或明确要舍弃的声音时使用，不能无意跳过下一句开头。

## 3. 单次编码渲染

推荐用安全 wrapper：

```bash
python3 scripts/audio_transition.py apply work/audio_transition_plan.json \
  --output output/day<NN>_master.mp4 \
  --receipt work/audio_transition_apply.json
```

如果原渲染依赖外部 `--enrich-plan`、`--color-grade`、CLI-only `--speech-denoise` 或其他参数，应在原命令上增加 transition plan，而不是使用只负责安全基础渲染的 wrapper：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --audio-transition-plan work/audio_transition_plan.json \
  --enrich-plan work/enrich_plan.json \
  --primary-speed 1.0 \
  --output output/day<NN>_master.mp4
```

画面仍按 render config 硬切；主音频按计划提前/延后并混合。字幕、overlay、B-roll、BGM ducking、片头和响度处理继续留在同一次 FFmpeg 编码中。`apply` 先渲染同目录临时文件，确认有音轨且可 probe 后才原子提升，默认不覆盖已有输出或任何输入。

## 4. 验证与人工复核

```bash
python3 scripts/audio_transition.py verify \
  work/audio_transition_plan.json \
  --receipt work/audio_transition_apply.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require audio_transition_plan \
  --strict
```

机器验证只能确认 plan/config/source/receipt hash、音频 handle、时序覆盖和输出音轨。必须以 **1×** 速度逐个检查改变的边界：

1. 没有吞字、复读或双人声；
2. 没有 click、泵动或环境底噪突然变化；
3. 声音先行/延续确实帮助叙事，不只是为了“看起来专业”；
4. 耳机与手机扬声器都能听清对白。

需要看切点画面和波形时，继续使用 `timeline_view.py --at <秒数>`。计划中的视觉切点不含片头；检查最终 master 时要把实际片头时长加到 `--at`。最终仍需跑 `render_qa.py`、`audio_master_report.py` 和审批收据流程。

## 边界

- 不做语义判断，不自动选择 J-cut/L-cut；
- 不生成 room tone，也不修复混响、音色或麦克风差异；
- 不允许用缺失 handle 的静音填充假装完成转场；
- 它是主音频边界编辑，不替代 BGM sidechain ducking 或外录音频同步。
