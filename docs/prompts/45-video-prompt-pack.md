# 45 — Video Prompt Pack 视频生成提示词包

把 `storyboard_plan.json` 转成可提交给 Dreamina/即梦 Seedance、Veo、LTX、Wan、Sora 等视频生成模型的提示词包。脚本只做本地提示词、参考图路径、角色/品牌一致性和付费审批门禁，不会联网、不提交任务、不消耗 credits。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时用

- 已经有 `storyboard_plan.json`，准备把部分 shot 交给视频生成模型。
- 想把同一组分镜导出为 Dreamina/即梦、Veo、LTX、Wan 或 Sora 的不同提示词版本。
- 需要先生成角色/风格参考 sheet，再做 image-to-video。
- 需要把同一张 style key 绑定到所有生成 shot，减少跨镜头风格漂移。
- 想在执行 paid video generation 前，用 `--strict` 拦住未审批任务。

## 命令

```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --character "same Chinese founder-host, navy jacket" \
  --brand-anchor "palette=charcoal,white,signal yellow" \
  --style-reference work/imagegen/style-key.png \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md \
  --strict
```

`--strict` 会在任何视频生成 provider 还没有 `--approved` 时返回退出码 `2`。这适合放在生成前和发布前检查，避免 agent 直接批量提交 paid jobs。

审批后再导出可执行包：

```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --provider dreamina_seedance \
  --animate-stills \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md
```

## Provider 路由

| 输入 route / 参数 | 默认输出 | 说明 |
|---|---|---|
| `dreamina_video` | `dreamina_seedance` | 标记 `approval_required`，提交前确认 credits |
| `codex_imagegen` | `codex_imagegen` | 先用 Codex `image_gen` 做静态参考图 |
| `codex_imagegen + --animate-stills` | `dreamina_seedance` `image_to_video` | 把参考图转成视频生成提示词 |
| `remotion_hyperframes` | `remotion_hyperframes` | 本地动效 brief，不需要 provider credits |
| `media_library_broll` | `media_library_broll` | 本地素材搜索 query，不生成 |
| `--provider veo/ltx/wan/sora` | 指定 provider | 同一分镜可导出不同模型提示词 |

## 输出内容

`video_prompt_pack.v1` 包含：

- `global.character_sheet_prompt`：角色/品牌/风格参考 sheet 提示词。
- `global.style_reference`：共享 style key 的 expected/resolved path。
- `items[].prompt`：按 provider 改写后的 shot 提示词。
- `items[].style_reference`：每个 shot 指向同一 style key；prompt 会追加一致的 `STYLE LOCK`。
- `items[].reference.expected_path/resolved_path`：默认查找 `work/imagegen/<shot_id>.png`，供 image-to-video 使用。
- `items[].negative_prompt`：统一避免字幕、水印、UI、畸形手、闪烁等问题。
- `items[].approval_status`：`needs_approval` / `approved` / `not_required`。
- `summary.blocking`：未审批 paid video generation 数量；`pipeline_manifest.py` 会读取它作为 gate。

## 推荐流程

```bash
# 1. 先生成分镜
python3 scripts/storyboard_plan.py \
  --transcript work/transcript.json \
  --clean-script work/clean_script.md \
  --output work/storyboard_plan.json \
  --markdown work/storyboard_plan.md

# 2. 把抽象 shot 先做成参考图
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md

# 3. 按 video_prompt_pack.md 用 Codex image_gen 做 reference sheet / stills
#    注意：Dreamina/即梦等视频生成可能消耗 credits，提交前先确认。

# 4. 确认后再生成可执行视频提示词包，并锁定共享 style key
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --style-reference work/imagegen/style-key.png \
  --animate-stills \
  --approved \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md

# 5. paid provider 提交前，检查首帧/style key
python3 scripts/reference_frame_preflight.py \
  --prompt-pack work/video_prompt_pack.json \
  --output work/reference_frame_preflight.json \
  --markdown work/reference_frame_preflight.md \
  --require-style-reference \
  --strict

# 6. 生成视频落盘后，回到素材预检
python3 scripts/storyboard_assets.py \
  --storyboard-plan work/storyboard_plan.json \
  --asset-root work \
  --output work/storyboard_assets.json \
  --markdown work/storyboard_assets.md \
  --strict
```

## 设计原则

- 提示词和审批状态先落盘，provider 执行后置。
- 默认小批量、逐 shot 审批，避免无意消耗 Dreamina/即梦或其他 provider credits。
- 先用 Codex `image_gen` 做角色/风格/首帧参考，再进入 image-to-video，减少人物和品牌漂移。
- 共享 style key 必须在所有生成 shot 中保持同一路径；提交前用 `reference_frame_preflight.py` 检查画幅和背景。
- 生成视频仍要经过 `storyboard_assets.py`、`asset_provenance.py`、`render_qa.py` 和必要的 `timeline_view.py`。
