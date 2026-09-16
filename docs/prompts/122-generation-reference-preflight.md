# 122 — Generation Reference Preflight 多模态生成参考门禁

把已批准的 `video_prompt_pack.json`、实际要提交的本地图片/视频/音频和带日期的 provider capability profile 绑定成可现场复查的生成前报告。工具只做本地媒体探测、完整解码、限额检查和 prompt 角色绑定；它不上传素材、不调用 provider、不提交任务或消耗 credits。

## 何时用

- `reference_to_video` 同时引用产品图、动作视频、音乐或旁白节奏。
- `video_edit / video_extension / clip_stitching` 需要明确每个输入的唯一用途。
- provider UI 使用 `@Image1 / @Video1 / @Audio1` 一类位置标签，提交顺序必须可复核。
- 不确定当前 surface 是否允许 exact frame 与语义 reference 混用、audio-only，或某类素材的数量/时长/大小。

只使用 image-to-video 首帧时，继续用 [71-Reference Frame Preflight](71-reference-frame-preflight.md)。涉及外部上传、真人、品牌/IP 或付费额度时，还要完成 [97-Production Authorization](97-production-authorization.md)。

## 1. 先固定当前 provider 能力

`provider_capabilities.json` 必须对应实际使用的 provider、surface 和 model，并记录当前核验日期与来源。多模态 reference 需要填写：

```json
{
  "reference_limits": {"images": 4, "videos": 3, "audio": 3},
  "reference_media": {
    "frame_reference_exclusive": true,
    "audio_only": false,
    "total_files": 8,
    "images": {
      "extensions": [".png", ".jpg"],
      "max_bytes": 30000000
    },
    "videos": {
      "extensions": [".mp4", ".mov"],
      "max_bytes": 200000000,
      "min_seconds": 2,
      "max_seconds": 15,
      "max_total_seconds": 15
    },
    "audio": {
      "extensions": [".wav", ".mp3"],
      "max_bytes": 15000000,
      "min_seconds": 2,
      "max_seconds": 15,
      "max_total_seconds": 15
    }
  }
}
```

这些数字只是 schema 示例，不能当作任何 provider 的现行规格。`frame_reference_exclusive`、`audio_only` 没有核实时写 `"unknown"`；相关场景会 fail closed。完整结构见 [96-Provider Capability Profile](96-provider-capability.md)。

## 2. 生成并填写 reference manifest

prompt pack 要先通过 capability gate 和 paid approval gate：

```bash
python3 scripts/generation_reference_preflight.py template \
  --project-dir . \
  --prompt-pack work/video_prompt_pack.json \
  --output work/generation_references.json
```

模板包含每个 generated-video shot。给每份实际提交素材填写 `kind / path / role / exclude`：

```json
{
  "version": "generation_reference_inputs.v1",
  "shots": [
    {
      "shot_id": "shot_001",
      "references": [
        {
          "kind": "image",
          "path": "work/references/product.png",
          "role": "the hero product geometry only",
          "exclude": "background, text, camera, hands, and lighting"
        },
        {
          "kind": "video",
          "path": "work/references/camera-motion.mp4",
          "role": "camera path and action timing only",
          "exclude": "identity, wardrobe, setting, product design, and audio"
        },
        {
          "kind": "audio",
          "path": "work/references/rhythm.wav",
          "role": "music rhythm and beat accents only",
          "exclude": "voice identity, dialogue, lyrics, and ambience"
        }
      ]
    }
  ]
}
```

同类素材的列表顺序就是标签顺序。上例会得到 `@Image1 / @Video1 / @Audio1`。共享 `--style-reference` 会自动成为第一张图片，角色固定为 palette、lighting 和 visual style；后续图片从 `@Image2` 开始。

每份素材只写一个主要控制职责。`exclude` 要具体列出不能继承的身份、场景、文字、声音或其他属性。若一个文件承担多个互相独立的任务，应先判断是否真的需要它；过多或含糊的 references 会降低可控性。

## 3. 分析并输出 provider prompt

```bash
python3 scripts/generation_reference_preflight.py analyze \
  --project-dir . \
  --prompt-pack work/video_prompt_pack.json \
  --references work/generation_references.json \
  --capability-profile work/provider_capabilities.json \
  --max-age-days 30 \
  --output work/generation_reference_preflight.json \
  --markdown work/generation_reference_preflight.md \
  --strict
```

`generation_reference_preflight.v1` 会记录：

- prompt pack、manifest、capability bundle 的项目内路径、大小和 SHA-256；
- 每份 reference 的稳定标签、顺序、role、exclude、媒体流信息和完整 FFmpeg decode 结果；
- exact provider/surface/model/profile id、mode 和逐类数量；
- extension、字节、单条/合计时长、每类/总文件数、audio-only 与 frame/reference 互斥结果；
- `shots[].provider_prompt`：先列 `REFERENCE ROLES`，再接原始 `MAIN REQUEST`。

以下情况会阻塞 `--strict`：

- manifest 漏掉 generated-video shot、包含未知/重复 shot，或引用项目外文件、symlink、重复文件；
- role/exclude 缺失，文件类型不符、不可解码、扩展名/大小/时长/数量超限；
- profile 缺 `reference_media` 事实、过期/无效，或 prompt pack 绑定的 profile id 已改变；
- audio-only、音频参考、exact frame + semantic references 或当前 mode 没有明确支持；
- prompt pack 仍有 capability/approval blocker。

## 4. 提交前现场复查

```bash
python3 scripts/generation_reference_preflight.py verify \
  --project-dir . \
  --report work/generation_reference_preflight.json \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require generation_reference_preflight \
  --strict
```

`verify` 会重读所有输入、重新探测并完整解码每份媒体，再重建完整派生状态。prompt、manifest、profile、素材字节、顺序、角色或限制发生变化后，旧报告都会失效。提交时按 ready 报告中的素材顺序和 `provider_prompt` 操作，不要在 provider UI 中临时交换位置或改写角色。

## 边界

- 完整解码只证明本地文件可读，不证明 provider 会接受，也不证明生成质量。
- role/exclude 是交接合同，模型仍可能忽略或混合属性；结果下载后要运行 generated clip/sequence review。
- 工具不判断版权、肖像、同意、商标、受保护角色或声音克隆权利。
- provider limits 会变化；每次付费提交前应核对 exact surface 的官方文档、当前 UI 或第一方实测，再更新 profile。
