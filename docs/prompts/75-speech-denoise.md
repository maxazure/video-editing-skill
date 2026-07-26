# Speech Denoise — 口播稳态底噪清理

`render_final.py` 可以在单次编码里对口播主轨做可选的 80 Hz 高通和 FFmpeg `afftdn` FFT 降噪。它适合空调、风扇、轻微电流声、房间底噪这类相对稳定的噪声；默认关闭，不会改变旧项目。

## 用法

先从 `light` 开始：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --speech-denoise light \
  --output output/master.mp4
```

噪声明显时再试：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --speech-denoise medium \
  --output output/master.mp4
```

也可以写进 `render_config.json`：

```json
{
  "speech_denoise": "medium",
  "clips": [
    {
      "video": "origin/talking.mp4",
      "transcript": "work/transcript_reviewed.json",
      "segment_id": 1
    }
  ]
}
```

临时关闭配置里的降噪：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --no-speech-denoise \
  --output output/master.mp4
```

## Preset

| preset | 处理 | 适合 |
|---|---|---|
| `light` | 80 Hz 高通 + 6 dB FFT 降噪 | 轻微空调声、风扇声、低频震动 |
| `medium` | 80 Hz 高通 + 9 dB FFT 降噪 | 稳定且能明显听见的房间底噪 |
| `strong` | 80 Hz 高通 + 12 dB FFT 降噪 | 只在 A/B 试听确认没有水下声、金属声后使用 |

滤镜顺序固定为：

```text
highpass → afftdn → atempo → dynaudnorm → acompressor → loudnorm → cover delay → BGM ducking/mix
```

降噪在变速和动态增益之前，避免先把底噪放大；BGM ducking 使用清理后的旁白作为触发信号。整条链仍在 `render_final.py` 的一次 FFmpeg 编码内完成。

## 必须人工试听

- 先渲染同一段 10–20 秒的 `off` / `light` / `medium` A/B，正常速度戴耳机听辅音、尾音和停顿。
- 已经经过 VAD、noise gate、Adobe Podcast、Descript Studio Sound、iZotope RX 或机内强降噪的音轨通常保持 `off`。
- 数字静音很多、噪声随片段突变、多人多麦、音乐主导内容或瞬态噪声（敲击、关门、咳嗽）不适合盲目使用 FFT preset。
- `strong` 不代表“更专业”。过强降噪可能产生 warble / watery / metallic artifact。
- 渲染后继续运行 `audio_master_report.py --strict` 检查 LUFS、true peak、LRA 和长静音；机器报告不能替代 A/B 试听。

本功能是轻量的最终听感修饰，不会清理 ASR、jump cut 或 audio boundary 上游使用的源音轨，也不是语音修复、去混响、咳嗽检测或人声分离工具。
