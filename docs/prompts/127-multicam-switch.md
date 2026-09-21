# 127 — Audio-guided Multicam Switch 按说话者音频生成多机位导播草稿

当双机位或多机位访谈已经通过 `multicam_sync.py` 对齐，并且每个出镜者都有一条能代表自己发言活动的相机音轨时，使用 `multicam_switch.py` 生成可审的自动导播草稿。

音频能量只用于选择候选机位。它不做声纹识别，也不能证明画面里的人正在说话。共享混音、热麦、自动增益、风噪、掌声和串音都会误导结果，因此成片必须完整带声复核。

## 1. 运行环境与上游同步

```bash
python3 scripts/runtime_preflight.py analyze \
  --profile multicam_switch \
  --output work/runtime_preflight.json \
  --markdown work/runtime_preflight.md \
  --strict

python3 scripts/multicam_sync.py \
  --reference-media origin/cam-a.mp4 \
  --angle origin/cam-b.mp4 \
  --output work/multicam_sync_plan.json \
  --markdown work/multicam_sync_plan.md \
  --preview-output verify/multicam-sync-preview.mp4 \
  --apply-preview \
  --strict
```

先播放同步预览，检查拍手、辅音、口型和头中尾同步。上游计划中任一机位处于 `review/blocked`，或时钟漂移是 `correction_required/unreliable`，都不能进入自动切镜。

## 2. 建立切换计划

从 `multicam_sync_plan.json` 读取实际 angle ID，为每个候选画面显式绑定出镜者：

```bash
python3 scripts/multicam_switch.py plan \
  --sync-plan work/multicam_sync_plan.json \
  --speaker angle_00_cam-a=主持人 \
  --speaker angle_01_cam-b=嘉宾 \
  --program-audio angle_00_cam-a \
  --window 0.5 \
  --min-shot 1.5 \
  --delivery output/multicam-switch-draft.mp4 \
  --output work/multicam_switch_plan.json \
  --markdown work/multicam_switch_plan.md \
  --strict
```

计划会：

- 在公共参考时间线内按窗口读取每路已选择音轨。
- 以各路自己的 P10 噪声底和 P95 语音峰值归一，降低热麦增益差异。
- 只有最高分达到活动阈值且领先第二名时才换机位；证据含糊时保持上一机位。
- 合并连续窗口，并把短于 `--min-shot` 的闪切折回相邻镜头。
- 记录每个窗口的候选、分数、margin、选择原因和最终 source-time 映射。
- 比较各路电平包络相关性；超过默认 `0.985` 时按共享混音/强串音风险阻断。

`--allow-correlated-audio` 只适用于人工已经确认各路仍有可用差异的情况；它保留 warning，不会把共享混音变成可靠的 speaker detector。广角或没有独立说话者音轨的机位不要放进 `--speaker`。

## 3. 渲染与完整解码

```bash
python3 scripts/multicam_switch.py apply \
  work/multicam_switch_plan.json \
  --markdown work/multicam_switch_plan.md
```

apply 按同步 offset 把每个参考区间映射回源机位，统一到参考画布、帧率、SAR 和 `yuv420p`，以硬切拼成 H.264/AAC 草稿。声音始终来自 `--program-audio`，不会跟随画面来回换麦。输出尺寸、帧率、时长、音轨与完整 FFmpeg decode 全部通过后才原子提升。

如果已经有混音器 master 或独立 recorder，应先把它纳入同步计划，再用它的 angle ID 作为 `--program-audio`。只用某位说话者的隔离麦作为节目声音，会漏掉其他人的发言。

## 4. 完整人工复核

以正常速度、带声完整播放草稿，逐项检查：

- `speaker_selection`：被切到的画面确实是当前发言者，掌声、笑声和串音没有抢镜。
- `cut_timing`：切点自然，没有迟切、抢切、半秒闪切或长时间错误 hold。
- `sync`：所有镜头的口型和动作保持同步。
- `audio_continuity`：节目声音来源正确、连续，没有因画面切换产生音色跳变或缺人声。

```bash
python3 scripts/multicam_switch.py confirm \
  work/multicam_switch_plan.json \
  --reviewer editor \
  --speaker-selection pass \
  --cut-timing pass \
  --sync pass \
  --audio-continuity pass \
  --markdown work/multicam_switch_plan.md
```

任一项为 `fail` 时保持阻断。应回到计划参数、speaker/audio stream 映射或人工剪辑修正，不能仅把 review 改成 pass。

## 5. Live gate

```bash
python3 scripts/multicam_switch.py verify \
  work/multicam_switch_plan.json --strict

python3 scripts/pipeline_manifest.py . \
  --require multicam_switch_plan --strict
```

同步计划、任一源文件、speaker mapping、分析参数、switch timeline、输出字节、完整解码绑定或人工结论发生漂移，旧计划都会失效。

## 可直接交给 Agent 的任务描述

```text
请先用 multicam_sync.py 对齐全部机位并完整复核同步预览，再用 multicam_switch.py 为有明确独立说话者音频的画面建立 source-bound 自动导播草稿。逐路填写 angle ID 与说话者，显式选择连续的 program audio；不要把共享混音或最响相机直接当成可靠说话者识别。执行 plan、apply 和完整解码后，正常速度带声看完整草稿，逐项确认 speaker_selection、cut_timing、sync、audio_continuity，最后运行 verify 与 pipeline manifest。
```
