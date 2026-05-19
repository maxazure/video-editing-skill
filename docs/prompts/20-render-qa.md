# 20 — Render QA 渲染后质检

`scripts/render_qa.py` 用于渲染完成后的机器质检，适合检查 Remotion 输出、`render_final.py` 主片，以及 `multi_export.py` 生成的平台派生文件。

## 什么时候用

- 渲染后发现平台上传失败，先确认尺寸、时长、音频流是否正常
- B-roll 或封面合成后担心黑屏、素材丢失、长静帧
- 口播视频担心人声链路丢失或中间长静音
- 批量导出后需要留下 JSON QA 记录

## 常用命令

```bash
# 9:16 主片 / 抖音 / 视频号
python3 scripts/render_qa.py output/day58_master.mp4 \
  --platform douyin \
  --json output/day58_master_qa.json

# 小红书 3:4 派生版
python3 scripts/render_qa.py output/day58_master_xhs.mp4 \
  --platform xhs \
  --json output/day58_xhs_qa.json

# 只检查容器元数据，不跑 ffmpeg 检测滤镜
python3 scripts/render_qa.py output/day58_master.mp4 --no-filters
```

## 检查项

| 检查 | 默认判断 |
|---|---|
| video/audio stream | 无视频流失败；无音频流失败，除非加 `--allow-no-audio` |
| duration | 默认短于 1 秒失败 |
| platform dimensions | `xhs=1080x1440`，`douyin/wxch=1080x1920` |
| black frames | 累计黑屏超过 0.5 秒失败 |
| frozen video | 累计静帧超过 2 秒失败 |
| silence | 累计静音超过 3 秒失败 |

阈值都可以通过 CLI 调整，例如：

```bash
python3 scripts/render_qa.py output/day58_master.mp4 \
  --max-black-seconds 1.0 \
  --max-freeze-seconds 4.0 \
  --max-silence-seconds 5.0
```

## 输出怎么看

- `PASS`：可以继续发布或进入下一步
- `WARN`：有可解释风险，例如片尾自然静音；发布前人工看一眼
- `FAIL`：先回到 render config / 素材 / 音频链路修复，再重新渲染

批量工作流里建议把 `*_qa.json` 和最终视频一起归档，这样之后能快速定位是源素材问题、渲染问题，还是平台派生导出问题。
