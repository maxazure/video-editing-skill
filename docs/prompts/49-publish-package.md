# 49 — Publish Package 最终上传包

把最终成片、平台导出、封面、字幕 sidecar、标题正文 tags、章节文本和 gate 状态汇总成一个可上传前复核的发布包。

## 适用场景

- 已经跑完 `render_final.py`、`render_qa.py`、`multi_export.py` 和 `generate_caption.py`。
- 准备手工上传到小红书、抖音、视频号，或把物料交给外部发布 connector。
- 想在上传前确认平台 MP4、caption、封面、字幕和 pipeline blockers 都齐全。

## 常用命令

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir work/day58 \
  --target-stage publish_ready \
  --output work/day58/pipeline_manifest.json \
  --markdown work/day58/pipeline_manifest.md \
  --strict

python3 scripts/publish_package.py \
  --project-dir work/day58 \
  --platforms xhs douyin wxch \
  --output work/day58/publish_package.json \
  --markdown work/day58/publish_package.md \
  --strict
```

## 指定平台文件

如果平台视频不在默认位置，用 `--video platform=path` 覆盖：

```bash
python3 scripts/publish_package.py \
  --project-dir work/day58 \
  --platforms xhs douyin wxch youtube_shorts \
  --video xhs=work/day58/output/day58_xhs_v2.mp4 \
  --video youtube_shorts=work/day58/output/day58_douyin.mp4 \
  --caption work/day58/output/day58_caption.json \
  --cover work/day58/output/cover.png \
  --chapters work/day58/output/chapters-youtube.txt \
  --pipeline-manifest work/day58/pipeline_manifest.json \
  --output work/day58/publish_package.json \
  --markdown work/day58/publish_package.md \
  --strict
```

支持的平台名：`xhs`、`douyin`、`wxch`、`youtube_shorts`、`tiktok`、`instagram_reels`。

## 输出内容

`publish_package.json` 使用 `publish_package.v1` schema：

- `platforms[]`：每个平台的 `video`、`cover_image`、`subtitles`、`caption`、`upload_checklist`、`notes`。
- `caption`：从 `generate_caption.py` 输出中提取 `title`、`caption_body`、`tags`、`publish_time_hint`，并生成可直接粘贴的 `upload_copy`。
- `pipeline_status`：读取或现场构建 `pipeline_manifest` 的状态。
- `blockers[]`：缺少平台视频、caption 为空、pipeline blocked 等上传前必须解决的问题。
- `warnings[]`：非阻塞但应该关注的问题。

`publish_package.md` 是手工上传用 checklist：先看 Status，再按平台复制标题/正文/tags，并核对视频、封面和字幕文件。

## 规则

- `publish_package.py` 不上传、不登录平台、不调用外部 API。
- `--strict` 在 `status=blocked` 时返回 2，适合自动化任务或发布前门禁。
- 如果要交给外部发布 connector，优先传 `publish_package.json`，不要让 connector 自己重新猜文件。
- 如果重新渲染或重导出平台视频，重新跑一次 `pipeline_manifest.py` 和 `publish_package.py`，避免旧路径被误用。
