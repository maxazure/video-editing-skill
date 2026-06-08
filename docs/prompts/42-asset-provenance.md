# Asset Provenance 素材授权 / 署名门禁

用途：发布前确认最终视频用到的 B-roll、图片、音乐、生成素材是否有来源、授权和署名证据。

`asset_provenance.py` 不搜索、不下载、不调用 stock API；它只读取本地 artifact 和素材元数据，输出 JSON + Markdown review + credits。生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 常用命令

```bash
python3 scripts/asset_provenance.py \
  --media-library work/day58 \
  --asset-manifest work/storyboard_assets.json \
  --render-config work/render_config.json \
  --enrich-plan work/enrich_plan.json \
  --output work/asset_provenance.json \
  --markdown work/asset_provenance.md \
  --strict
```

强制每个素材都有授权元数据：

```bash
python3 scripts/asset_provenance.py \
  --media-library work/day58 \
  --render-config work/render_config.json \
  --require-known-license \
  --output work/asset_provenance.json \
  --markdown work/asset_provenance.md \
  --strict
```

只检查几个显式素材：

```bash
python3 scripts/asset_provenance.py \
  --asset output/custom_broll.mp4 \
  --asset output/generated_cover.png \
  --output work/asset_provenance.json \
  --markdown work/asset_provenance.md
```

## 元数据来源

推荐把来源信息放进 `media_index.json` / `media_index.db` 的 `metadata` 字段：

```json
{
  "path": "broll/city.mp4",
  "type": "video",
  "category": "broll",
  "metadata": {
    "provider": "pexels",
    "source_url": "https://www.pexels.com/video/city-123/",
    "creator": "Alex Example",
    "license": "Pexels License"
  }
}
```

也可以给单个文件放 sidecar：

```text
broll/city.mp4
broll/city.provenance.json
```

sidecar 示例：

```json
{
  "provider": "pixabay",
  "source_url": "https://pixabay.com/videos/example-123/",
  "creator": "Creator Name",
  "license": "Pixabay Content License"
}
```

## 输出

`asset_provenance.v1` 包含：

| 字段 | 说明 |
|---|---|
| `summary.blocking` | 缺文件、授权不可发布、CC BY 缺署名等阻塞数 |
| `items[]` | 每个素材的 provider/source_url/creator/license/issues/warnings |
| `credits[]` | 建议粘贴到发布说明或项目备注的署名行 |
| `next_steps[]` | 需要补 source_url、license 或 attribution 的动作 |

## 发布门禁

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --require asset_provenance \
  --output work/day58/pipeline_manifest.json \
  --markdown work/day58/pipeline_manifest.md \
  --strict
```

`asset_provenance.json` 存在但 `summary.blocking > 0` 时，`pipeline_manifest.py --strict` 会返回 2。
