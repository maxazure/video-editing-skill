# 71 — Reference Frame Preflight 生成参考帧预检

在把 image-to-video 分镜提交给 Dreamina/即梦 Seedance、Veo、LTX、Wan 或 Sora 前，先检查首帧和共享 style key。脚本只读本地文件，不联网、不提交任务、不消耗 credits。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 解决什么问题

- 参考图路径写进 prompt pack，但文件还没生成或已经移动。
- 横图误交给 9:16 生成任务，导致黑边、错误裁切或主体被切掉。
- style key 与成片方向/画幅严重冲突，跨 shot 风格和构图不稳定。
- PNG 带透明背景，provider 自动填成黑色、棋盘格或随机环境。
- 参考图分辨率太低，产品、人物或文字细节容易漂移。

## 推荐流程

先生成 prompt pack，并把同一 style key 绑定到所有 shot：

```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --style-reference work/imagegen/style-key.png \
  --animate-stills \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md
```

再运行参考帧预检：

```bash
python3 scripts/reference_frame_preflight.py \
  --prompt-pack work/video_prompt_pack.json \
  --output work/reference_frame_preflight.json \
  --markdown work/reference_frame_preflight.md \
  --require-style-reference \
  --strict
```

`--strict` 只在 blocker 存在时返回 `2`。透明背景或短边低于默认 512px 会产生 warning，但不会单独阻塞；缺文件、无法解码、横竖方向冲突或超过画幅容差会阻塞。

## 检查规则

| 检查 | 默认结果 | 修正方式 |
|---|---|---|
| image-to-video 首帧缺失 | blocker | 先生成/补链本地首帧 |
| style key 缺失且传了 `--require-style-reference` | blocker | 生成一张全局 style key 并重跑 prompt pack |
| 横图 → 9:16 或竖图 → 16:9 | blocker | 重构图、裁切或 outpaint，避免黑边 |
| 同方向但画幅相差超过 20% | blocker | 用目标画幅重新生成或裁切 |
| 短边 `< 512px` | warning | 换高分辨率 reference |
| 存在透明像素 | warning | 合成明确背景，或在 provider prompt 写清背景 |

默认 `--aspect-tolerance 0.20` 会接受常见的 1024×1536 参考图用于 9:16 工作流，但会拦截横竖方向冲突和更严重的画幅差异。需要更严格时可改成 `--aspect-tolerance 0.08`。

## 手工覆盖路径

如果 prompt pack 中某个 shot 的路径需要临时替换：

```bash
python3 scripts/reference_frame_preflight.py \
  --prompt-pack work/video_prompt_pack.json \
  --reference shot_001=work/approved/shot_001.png \
  --reference shot_004=work/approved/shot_004.webp \
  --style-reference work/approved/style-key.png \
  --output work/reference_frame_preflight.json \
  --markdown work/reference_frame_preflight.md \
  --require-style-reference \
  --strict
```

覆盖只影响本次 preflight，不会修改原 `video_prompt_pack.json`。

## 输出

`reference_frame_preflight.v1` 包含：

- `target`：目标画幅、数值 ratio 和方向。
- `style_lock`：共享 style key 路径和覆盖的 generated shot ids。
- `references[]`：每个首帧/style key 的尺寸、方向、像素格式、透明背景、状态、blockers、warnings 和修正建议。
- `summary.blocking`：`pipeline_manifest.py` 会把非零值作为发布前 gate。
- `next_steps[]`：可直接执行的重构图、裁切、补背景或提高分辨率建议。

## 可直接复制的提示词

```text
请先用 video_prompt_pack.py 生成视频提示词包，并用 --style-reference 把同一张 style key 绑定到所有生成 shot。然后运行 reference_frame_preflight.py 检查 image-to-video 首帧和 style key 的存在性、可解码性、尺寸、方向、目标画幅、分辨率与透明背景，输出 JSON 和 Markdown；有 blocker 时不要提交任何 paid provider job。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
```
