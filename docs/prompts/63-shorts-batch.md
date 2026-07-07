# Shorts Batch 多条精华短视频渲染 job sheet

把 `highlight_picker.py` 选出的多条 selected highlights 变成可复核、可逐条执行的短视频渲染队列。

## 适用场景

- 长访谈、播客、课程、直播回放已经跑过 `highlight_picker.py`。
- 想一次规划 3-10 条短视频，但仍逐条人工确认 hook、结尾和 warnings。
- 想保留每条短视频自己的 `render_config`、输出路径、渲染命令和 QA 命令。
- 不想引入服务端 queue、对象存储或上传 API。

## 先生成 highlights

```bash
python3 scripts/highlight_picker.py \
  --transcript work/long_transcript.json \
  --scene-boundaries work/scene_boundaries.json \
  --video origin/long-talk.mp4 \
  --output work/highlight_candidates.json \
  --markdown work/highlight_candidates.md \
  --platform douyin \
  --num-clips 5 \
  --strict
```

先看 `work/highlight_candidates.md`，确认 selected 片段没有明显弱 hook、断尾或误选。

## 生成 batch job sheet

```bash
python3 scripts/shorts_batch.py \
  --highlights work/highlight_candidates.json \
  --video origin/long-talk.mp4 \
  --output work/shorts_batch.json \
  --markdown work/shorts_batch.md \
  --render-config-dir work/shorts_render_configs \
  --output-dir output/shorts \
  --qa-dir verify/shorts \
  --basename day63 \
  --platform douyin \
  --primary-speed 1.25 \
  --strict
```

输出：

- `work/shorts_batch.json`：`shorts_batch.v1`，包含 `jobs[]`、`render_shell`、`qa_shell`、warnings 和 blockers。
- `work/shorts_batch.md`：人工复核表，适合直接打开检查。
- `work/shorts_render_configs/day63_001_render_config.json` 等：每条短视频一份独立 `render_config`。
- `output/shorts/day63_001.mp4` 等：计划输出路径，脚本不会自动渲染。
- `verify/shorts/day63_001_qa.json` 等：计划 QA 输出路径。

## 执行方式

打开 `work/shorts_batch.md` 后，对每条确认可用的 job 运行表内命令：

```bash
python3 scripts/render_final.py \
  --config work/shorts_render_configs/day63_001_render_config.json \
  --output output/shorts/day63_001.mp4 \
  --primary-speed 1.25

python3 scripts/render_qa.py output/shorts/day63_001.mp4 \
  --platform douyin \
  --json verify/shorts/day63_001_qa.json \
  --review-dir verify/shorts/day63_001
```

如需把 batch 规划作为显式 gate：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage render_ready \
  --require shorts_batch \
  --strict
```

`shorts_batch.py` 在源视频缺失或没有 selected highlight 时会把 `summary.blocking` 置为非零；`pipeline_manifest.py` 会把这个 batch 标为 blocker。

## 使用建议

- `shorts_batch.py` 不替代 `highlight_picker.py`，只负责把已选候选变成可执行 job sheet。
- 候选 warning 不是自动 blocker；弱 hook、断尾、时长偏离仍需要人工判断是否先回到 highlight plan 修改。
- 大批量渲染前先跑 1 条 smoke：确认字幕、封面、响度和画幅，再执行剩余 job。
- 每条渲染后都跑 `render_qa.py`；不要只看 render 命令成功。
