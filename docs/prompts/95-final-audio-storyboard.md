# Final Audio Storyboard — 锁定视觉 EDL 后重建声音分镜

多段生成视频或连续短片先完成视觉选段、裁切和重排，再设计最终声音。不要把剪辑前的整片配乐、旁白或逐段环境声原样套到已删短的画面上；被删除的动作、情绪转折和音效也必须明确删除、改写或作为有理由的画外声桥保留。

`final_audio_storyboard.py` 是本地、provider-neutral 的 `prepare → audit → verify` 门禁。它不生成音频、不调用 provider、不消耗 credits，也不把 JSON 直接当成任何音频模型的 prompt。

## 1. 先锁定视觉 EDL

确保 `render_config.json` 中每个 clip 的 `label` 是对应 `storyboard_plan.json` 的 shot id，例如 `shot_001`。然后导出视觉 EDL 和 JSON manifest：

```bash
python3 scripts/export_edl.py \
  --config work/render_config.json \
  --output work/locked_visual.edl \
  --manifest work/locked_visual.edl.json \
  --fps 30
```

本流程读取 `.edl.json`，不解析 CMX 文本。JSON manifest 的 `record_start / record_end` 是最终声音时间线；`source_start / source_end` 只用于保留段证据。

## 2. 生成复核请求

```bash
python3 scripts/final_audio_storyboard.py prepare \
  --project-dir . \
  --edl work/locked_visual.edl.json \
  --storyboard work/storyboard_plan.json \
  --output work/final_audio_storyboard_request.json \
  --markdown work/final_audio_storyboard_request.md \
  --response-template work/final_audio_storyboard_response.json \
  --strict
```

`prepare` 会：

- 检查 final timeline 从 0 开始连续、无 gap/overlap，且 EDL 声明时长与事件一致；
- 用 EDL event label、显式 `story_id/source_segment_id` 或源文件名映射 storyboard shot；
- 绑定 EDL、storyboard 和每个项目内源片的路径、大小与 SHA-256；
- 输出最终时间线 section、被完全省略的 story beat，以及已删短段落的改写 warning；
- 生成 response template，但不会替人决定旁白、音乐或声音设计。

源片必须在项目内且不能是 symlink。未映射事件、缺源片、外部路径、时间线 gap/overlap 或摘要漂移会 fail closed。

## 3. 填写最终声音决定

逐段填写 `work/final_audio_storyboard_response.json`：

- `reviewed_by`：非空 reviewer label；它不是身份认证或数字签名。
- `audio_strategy`：`single_track`、`sectioned_tracks` 或 `stems`。
- `shared_tone`：跨段保持一致的声学空间、情绪与音乐色彩。
- `sections[]`：保留不可修改的 final/source 时间和 story id，填写 `visual_beat`、`dialogue`、`narration`、`sound_design`、`music`、`stems` 与 `decision_note`。
- `stems[]`：只用 `dialogue / narration / source_audio / ambience / foley / music_like_bed / special_fx`。
- 没有音乐或设计音效时明确写 `none`，不要留空。
- 保留原生声音时设置 `preserve_source_audio=true`，同时加入 `source_audio` stem 并说明为什么值得保留。
- `omitted_story[]`：每个删掉的 story beat 必须选择 `remove`、`rewrite_into_adjacent` 或 `offscreen_bridge`，后两项必须指向实际保留的 `target_section_id`。

每句 dialogue/narration 在最终 voice ledger 中只能出现一次。一个 storyboard shot 被拆成多个 EDL event 时，不能把同一句旁白复制到每段。

## 4. 审计与现场验证

```bash
python3 scripts/final_audio_storyboard.py audit \
  --project-dir . \
  --request work/final_audio_storyboard_request.json \
  --response work/final_audio_storyboard_response.json \
  --output work/final_audio_storyboard.json \
  --markdown work/final_audio_storyboard.md \
  --strict

python3 scripts/final_audio_storyboard.py verify \
  --project-dir . \
  --report work/final_audio_storyboard.json \
  --strict

python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require final_audio_storyboard \
  --strict
```

`audit` 固定 final timeline mapping，构建无重复的 voice ledger，并要求所有 omitted beat 有明确去向。`verify` 现场重读 request、response、EDL、storyboard 和源片，重算全部派生 section、summary 与 report id；任何视觉重剪、源片替换、response/report 手改都会使旧结果失效。

## 5. 交给音频生成或后期

`final_audio_storyboard.json` 是叙事与时间线合同，不是 provider prompt。先按所用音频工具的官方提示规范，把它改写成带明确时码的 cue sheet；超过 provider 时长/字符限制，或对白、声学空间、音乐层需要独立控制时，再按安静转场拆 section 或 stem。不要在对白中间、音乐重拍、冲击瞬态或持续音中间拆分。

如果使用可能消耗 credits 的音频生成服务，提交前仍需单独确认。生成/混音后要重新运行 `audio_master_report.py`、完整 1× 审听、必要的 lip-sync review，以及最终 approval receipt。

## 边界

- SHA-256 只能发现复核对象是否漂移，不证明 reviewer 身份、版权或音质。
- 本工具不判断台词艺术质量，也不自动生成 Foley、配乐、对白或旁白。
- `single_track` 不总是更连续；对白精确时序、多个声学空间或需后期调节时优先 section/stems。
- 只有明确有叙事理由时才把已删画面的声音保留为 offscreen bridge。

## 可直接交给 Agent 的任务描述

```text
请先锁定视觉 render_config 并用 export_edl.py 输出 JSON manifest，确保每个 event label 对应 storyboard shot id。然后运行 final_audio_storyboard.py prepare，用最终 record timeline 逐段重建旁白、对白、环境声、Foley 和音乐；所有删掉的 story beat 必须明确 remove、rewrite_into_adjacent 或 offscreen_bridge，每句 voice 只出现一次。完成 response 后运行 audit、verify 和 pipeline_manifest --require final_audio_storyboard --strict。不要直接把 JSON 提交给音频 provider；先改写成 provider 适配的 timed cue sheet，付费生成前单独确认。
```
