# 44. Stock Material Plan — 远程素材搜索规划与素材登记

参考 MoneyPrinterTurbo 的 `video_terms → Pexels/Pixabay/Coverr → video_count` 思路，但保持本 skill 的本地审查优先：先生成可复核的 JSON/Markdown，不联网、不下载、不消耗额度。确认素材来源和授权后，再把下载或自有素材登记进 `media_index`，让 `asset_provenance.py` 做发布前门禁。

## 适用场景

- transcript/storyboard 里有 B-roll 缺口，现有本地素材不够。
- 想把主题、脚本或分镜转成 stock 搜索词。
- 需要同时规划 Pexels / Pixabay / Coverr 查询，但还不想马上下载。
- 下载或客户给的素材需要写入 provider、source URL、creator、license。

## 生成素材搜索计划

```bash
python3 scripts/stock_material_plan.py \
  --subject "AI workflow automation" \
  --script work/transcript.json \
  --terms "dashboard, team collaboration" \
  --provider pexels \
  --provider pixabay \
  --provider coverr \
  --platform douyin \
  --clip-duration 5 \
  --video-count 2 \
  --media-library . \
  --output work/stock_material_plan.json \
  --markdown work/stock_material_plan.md
```

输出 `stock_material_plan.v1`：

- `terms[]`：显式词、主题词、脚本文本关键词，带来源和分数。
- `local_candidates{}`：从 `media_index.json/db` 排名出的本地 B-roll 候选。
- `provider_queries[]`：Pexels / Pixabay / Coverr 的查询 URL、目标画幅、最短时长、凭证环境变量和授权提醒。
- `summary.required_coverage_seconds`：按 `--video-count` 计算需要覆盖的素材时长。
- `warnings[]`：比如 Coverr 多为 16:9，竖屏输出需要裁切复核。

`--strict` 会在缺主题/搜索词或 provider 无效时返回 2，适合放进自动化门禁。

## 下载后登记素材

规划脚本不会下载文件。人工或 agent 审核后，如果下载了一个素材，可以登记到本地库：

```bash
python3 scripts/media_library.py import /path/to/downloaded.mp4 \
  --project-dir . \
  --category broll \
  --copy \
  --provider pexels \
  --source-url "https://www.pexels.com/video/demo-123/" \
  --creator "Demo Creator" \
  --license "Pexels License" \
  --tag "dashboard,workflow"
```

`--copy` 会复制到 `media/broll/` 并写入 `media_index.json/db`。不传 `--copy` 时，项目内路径会存相对路径，项目外路径会存绝对路径。

已有素材补元数据：

```bash
python3 scripts/media_library.py annotate media/broll/downloaded.mp4 \
  --project-dir . \
  --source-url "https://example.com/source" \
  --license "owned" \
  --tag "owned,client-approved"
```

登记后的素材会被 `media_library.py recommend`、`storyboard_assets.py --media-library` 和 `asset_provenance.py --media-library` 继续消费。

## 推荐流水线

1. 运行 `storyboard_plan.py` 生成 shot cards。
2. 运行 `storyboard_assets.py --media-library .` 找本地候选。
3. 对 `search_needed` 的镜头运行 `stock_material_plan.py`。
4. 人工审核 provider 查询结果，下载少量确认过授权的素材。
5. 用 `media_library.py import` 或 `annotate` 登记素材来源。
6. 再跑 `storyboard_assets.py --strict` 和 `asset_provenance.py --strict`。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。
