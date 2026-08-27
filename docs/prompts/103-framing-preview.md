# 103 — Platform Framing Preview

在把 master 导出为小红书 3:4、抖音 / 视频号 9:16 前，用真实画面比较三种保持比例的处理方式，并把人工选择绑定到源视频和预览证据。

## 为什么需要

`multi_export.py` 默认用中心裁切填满目标画布。对单个居中人物通常合适，但横屏访谈、录屏、产品、海报、文档或多人画面可能丢掉脸、手、UI 文字、logo、产品边缘或第二位人物。单纯在导出后检查 safe area 也无法证明被裁掉的信息仍然存在。

`framing_preview.py` 为每个平台在源片 15% / 50% / 85% 三个时间点生成：

- `cover`：保持比例放大并中心裁切，画面铺满；
- `contain`：保持完整画面，用深色留边；
- `blur`：保持完整前景，用同源模糊背景铺满；
- `native`：源显示比例与目标比例一致时自动选择，不制造无意义的三套相同预览。

## 创建预览

```bash
python3 scripts/framing_preview.py create \
  --project-dir . \
  --video output/master.mp4 \
  --platforms xhs douyin wxch \
  --preview-dir verify/framing \
  --output work/framing_preview.json \
  --markdown work/framing_preview.md \
  --require-selection \
  --strict
```

首次运行在非原生比例平台没有选择时返回退出码 2，这是预期的人工 gate；JSON、Markdown 和 JPEG 仍会写出供复核。可重复 `--time <seconds>` 自定义 1–6 个抽样点。

## 选择并验证

逐平台查看所有 JPEG 的手机尺寸和全尺寸版本。`cover` 只有在每个抽样点都没有裁掉受保护信息时才应选择。记录决定：

```bash
python3 scripts/framing_preview.py select \
  --report work/framing_preview.json \
  --platform xhs \
  --strategy contain

python3 scripts/framing_preview.py select \
  --report work/framing_preview.json \
  --platform douyin \
  --strategy blur

python3 scripts/framing_preview.py verify \
  --report work/framing_preview.json \
  --strict
```

## 使用选择导出

```bash
python3 scripts/multi_export.py output/master.mp4 \
  --output-dir output/ \
  --platforms xhs douyin wxch \
  --framing-preview work/framing_preview.json
```

`multi_export.py` 会先现场验证报告，只接受同一 source bytes、同一显示方向、当前 FFmpeg filter contract、未变化的预览 JPEG，以及每个请求平台已经记录的选择；任一不一致都会在编码前退出 2。导出 manifest 会记录每个平台实际使用的 `framing_strategy`。

## 人工检查清单

- 人脸、头部、手势和多人关系是否完整；
- UI、字幕、文档、海报、表格和屏幕四边是否可读；
- logo、品牌条、产品边缘、包装文字和主体动作是否被裁；
- `contain` 留边是否符合视觉语气；
- `blur` 背景是否抢主体、产生明显接缝或让 UI 看起来重复；
- 三个抽样点是否足够代表内容；镜头变化多时增加 `--time`，最终仍完整播放平台导出。

## 安全与边界

- 所有缩放都保持源比例，不支持横纵独立拉伸。
- report、source、Markdown 和预览路径不得互相覆盖或通过 hard link 指向同一文件；项目外路径和 symlink traversal 会被拒绝。
- SHA-256 / `report_id` 只是完整性绑定，不是签名或人工审批身份认证。
- JPEG 抽样不能替代完整 1× 审片。镜头内主体移动、动态 reframe 或逐镜头不同构图仍应使用 `smart_reframe.py` / NLE，并在最终平台文件上复核。
- `pipeline_manifest.py --require framing_preview --strict` 可把报告设为发布前门禁。
