# Cover Variants 封面 A/B 方案

`cover_variants.py` 用在 `generate_caption.py` 之后、`publish_package.py` 之前：同一条视频先生成 2-4 套封面测试方案，批量渲染平台尺寸 PNG 和小尺寸预览，再记录最终选中的发布封面。

## 什么时候用

- 单张封面能生成，但不确定 `bold`、`news`、真实画面或教程卡片哪种更适合。
- 想把“换配色 / 换视觉证据 / 减少文字”拆成可比较的 A/B 变量。
- 想检查发布标题和封面文字是否重复，避免两个入口只传达同一条信息。
- 准备交给 `publish_package.py`，需要明确记录哪张图是已复核的最终封面。

## 生成三套方案并渲染

```bash
python3 scripts/cover_variants.py \
  origin/talking.mp4 \
  --title "20分钟出片" \
  --subtitle "AI剪辑完整流程" \
  --caption output/day68_caption.json \
  --platform xhs \
  --frame-timestamp 12.5 \
  --output-dir output/covers \
  --render \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md
```

默认三套方案：

1. `cover-a`：按主题自动选择的主方案。
2. `cover-b`：只测试更强的配色 / 视觉层级对比。
3. `cover-c`：使用真实视频帧，测试“视觉证据”是否更可信。

传 `--count 4` 会再增加一套去掉副标题的低文字密度版本。

## 小尺寸预览

加 `--render` 后，每张完整 PNG 旁边都会生成 `*_preview.png`：

- 小红书：完整图 1080×1440，预览宽 240px。
- 抖音 / 视频号 / TikTok / Reels / YouTube Shorts：完整图 1080×1920，预览宽 180px。
- 普通 YouTube：完整图 1280×720，预览宽 168px。

先看预览图，再看完整图。小图里看不清的细节，在真实 feed 里通常也没有价值。

## 标题—封面协同

`--post-title` 或 `--caption` 会触发 title-cover synergy 检查：

```bash
python3 scripts/cover_variants.py \
  origin/talking.mp4 \
  --post-title "我用 AI 把剪辑时间从 3 小时缩到 20 分钟" \
  --title "真实结果" \
  --subtitle "完整工作流" \
  --platform xhs \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md
```

如果封面文字只是重复发布标题，JSON 会写 `synergy.status=warn`。需要强制信息分工时加：

```bash
--require-distinct-cover-text --strict
```

推荐分工：

- 发布标题负责主题、关键词和承诺。
- 封面文字负责结果、情绪、反差或证据。
- 不要在封面堆完整句子；中文建议 4-8 个字，英文建议不超过 5 个词。

## 记录最终选择

看完预览后，重新运行并记录选择：

```bash
python3 scripts/cover_variants.py \
  origin/talking.mp4 \
  --title "真实结果" \
  --subtitle "完整工作流" \
  --caption output/day68_caption.json \
  --platform xhs \
  --output-dir output/covers \
  --select cover-c \
  --require-selection \
  --output work/cover_variants.json \
  --markdown work/cover_variants.md \
  --strict
```

`selected_cover` 会写入 JSON。`publish_package.py` 在没有显式 `--cover` 时，会优先使用这张已选择且存在的图片；`pipeline_manifest.py --require cover_variants` 可把封面选择设为发布 gate。

## AI 背景的可选路线

当前脚本默认复用本地 Chrome 模板和真实视频帧，不调用图片模型。如果需要定制人物、产品或场景背景，先生成 / 编辑底图，再把结果作为后续封面设计素材。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

无论底图来自实拍还是生成，都要保留大标题可读性、单一视觉焦点和足够负空间。
