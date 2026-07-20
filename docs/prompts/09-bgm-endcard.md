# 09 背景音乐、旁白自动 Ducking 和片尾

> 给视频加 BGM、让音乐在旁白出现时自动降低，再加片尾卡片提升完整感。

## 场景描述

视频已经剪好了，想加上背景音乐让氛围更好，加上片尾卡片引导关注。

---

## 加背景音乐 + 片尾卡片

```
视频已经剪好了，帮我加上：
1. 背景音乐：media/bgm/lofi.mp3，音量 15%，结尾淡出 3 秒
2. 片尾卡片：黑底白字，显示：
   "感谢观看
   关注我获取更多内容"
   持续 3 秒，带淡入淡出效果
```

---

## 只加背景音乐

```
帮我给视频加一段轻柔的背景音乐。
音乐文件是 media/bgm/piano.mp3，音量 10%，不要盖过人声，结尾淡出。
```

> **音量建议**：口播视频的 BGM 一般 10%-15% 就够了。旁白与停顿交替明显时，再启用 `--bgm-ducking`，不要只靠固定音量硬压整首音乐。

## 旁白驱动 BGM Ducking

在已有 `render_config.json` 中加入：

```json
{
  "bgm": "media/bgm/lofi.mp3",
  "bgm_volume": 0.15,
  "bgm_fade_out": 3.0,
  "bgm_ducking": true
}
```

也可以只对本次渲染启用：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --output output/final.mp4 \
  --bgm-ducking
```

`render_final.py` 会把最终旁白轨拆成“听得见的主混音”和“只用于检测的 sidechain”两路；BGM 是被压低的第一输入，旁白是触发压缩的第二输入。默认 threshold `0.03`、ratio `8`、attack `20ms`、release `500ms`，适合口播短视频。需要微调时可在 config 设置：

```json
{
  "bgm_ducking_threshold": 0.03,
  "bgm_ducking_ratio": 8,
  "bgm_ducking_attack_ms": 20,
  "bgm_ducking_release_ms": 500
}
```

- 人声开头仍被音乐盖住：降低 threshold，或缩短 attack。
- 每句话结束后音乐“抽吸”：延长 release；停顿很多且恢复太慢则缩短。
- 音乐下降不够：提高 ratio；先确认 `bgm_volume` 本身没有设得过高。
- 配置已默认开启但某条视频是音乐主导内容：用 `--no-bgm-ducking` 覆盖。

渲染后仍需运行 `audio_master_report.py --strict` 检查 LUFS / true peak / LRA / 长静音，并按正常播放速度试听旁白入口、句间停顿和片尾恢复，不能只看滤镜参数判断混音质量。

---

## 只加片尾

```
帮我在视频最后加一个片尾卡片：
- 黑色背景
- 白色文字："点赞关注不迷路"
- 持续 3 秒
- 有淡入淡出效果
```

---

## 多张片尾卡片

```
帮我在结尾加两张片尾卡片：
第一张："本期内容就到这里"，显示 2 秒
第二张："关注我，下期更精彩 ✨"，显示 3 秒
都是黑底白字，有淡入淡出。
```
