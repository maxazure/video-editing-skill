# 58 — Source Receipts 事实来源复核

把视频里的事实型 claim、数据、新闻判断、产品截图或来源页，整理成可审计的 `source_receipts.v1`。它适合在写完 clean script 后、正式分镜和发布前运行，避免“视频里说了一个事实，但后面找不到证据”。

`source_receipts.py` 只验证你提供的 URL 和本地截图/证据文件；它不联网抓取、不截图、不上传，也不调用生成 provider。

## 常用命令

先准备 `work/source_claims.json`：

```json
{
  "claims": [
    {
      "id": "c001",
      "text": "这个产品发布了新的 API 能力。",
      "source_url": "https://example.com/product-note",
      "source_title": "Official product note",
      "source_type": "official",
      "screenshot": "receipts/product-note.png",
      "risk": "news",
      "timecode": "00:04-00:09"
    }
  ]
}
```

再生成 JSON、Markdown 和 HTML source deck：

```bash
python3 scripts/source_receipts.py \
  --claims work/source_claims.json \
  --project-dir . \
  --output work/source_receipts.json \
  --markdown work/source_receipts.md \
  --html work/source_receipts.html \
  --require-screenshot \
  --require-primary-source \
  --strict
```

也可以临时从命令行写一条 claim：

```bash
python3 scripts/source_receipts.py \
  --claim "核心数据来自官方报告|https://example.com/report|work/receipts/report.png|official|data|Official report" \
  --output work/source_receipts.json \
  --markdown work/source_receipts.md \
  --html work/source_receipts.html
```

## 字段说明

| 字段 | 说明 |
|---|---|
| `text` | 视频中会说出的事实型 claim |
| `source_url` | 可验证 URL；高风险 claim 必须有 |
| `source_type` | 推荐填 `official` / `primary` / `owned` / `government` / `academic` |
| `screenshot` / `source_file` | 本地截图、PDF、HTML 或证据文件，路径相对 claims JSON 所在目录 |
| `risk` | `normal`、`news`、`data`、`finance`、`health`、`legal` 等 |
| `timecode` | 可选，claim 在成片中的大致出现时间 |

## 发布门禁

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require source_receipts \
  --output work/pipeline_manifest.json \
  --markdown work/pipeline_manifest.md \
  --strict
```

`source_receipts.json` 存在且 `summary.blocking > 0` 时，`pipeline_manifest.py` 会把它列为 blocking gate。对于纯观点类视频，可以不要求这个 gate；对于新闻、金融、健康、法律、产品测评、SEO 研究或带来源截图的视频，建议强制 `--require source_receipts`。
