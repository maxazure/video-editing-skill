# 96 — Provider Capability Profile 生成供应商能力契约

在写 Dreamina/即梦、Veo、LTX、Wan、Sora 等执行设置前，先把“具体 provider + 具体 UI/API surface + 具体 model”当前实际支持的能力写成带日期的本地合同。它解决的是 provider 参数变化和不同入口能力不一致，不负责联网查询或替你猜设置。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 何时用

- 同一模型在网页、App、API 或第三方平台的 mode、时长、画幅、分辨率、参考素材上限不同。
- 需要在提交付费生成前证明当前设置来自哪个 surface、哪天核验、什么来源。
- 想让 `video_prompt_pack.py` 自动拒绝未核验 mode、过期 profile、超限参考图或不支持的分辨率。
- 编辑/扩展是否保留源音频、能否引用音频等 control 不确定，不能靠模型常识猜。

## 1. 建立 profile bundle

把下面模板保存为 `work/provider_capabilities.json`。示例值只是 schema 演示；必须替换为当前实际 UI/API 与来源，不能把它当作任一 provider 的现行规格。

```json
{
  "version": "video_provider_capabilities.v1",
  "profiles": [
    {
      "provider": "dreamina_seedance",
      "surface": "<exact UI or API name>",
      "model": "<exact displayed model>",
      "verified_at": "2026-08-21",
      "sources": [
        {
          "source_type": "official_documentation",
          "url": "https://example.com/replace-with-current-provider-doc",
          "note": "Which controls were checked on this surface"
        }
      ],
      "capabilities": {
        "modes": ["text_to_video", "image_to_video"],
        "aspect_ratios": ["9:16", "16:9"],
        "resolutions": ["720p"],
        "duration": {
          "kind": "range",
          "min_seconds": 2,
          "max_seconds": 8
        },
        "reference_limits": {
          "images": 2,
          "videos": 0,
          "audio": 1
        },
        "audio": {
          "generate": true,
          "reference": true,
          "preserve_source": "unknown"
        }
      }
    }
  ]
}
```

`duration` 支持两种形式：

- 连续范围：`{"kind":"range","min_seconds":2,"max_seconds":8}`
- 固定档位：`{"kind":"fixed","values_seconds":[5,10]}`

`audio.generate/reference/preserve_source` 只能是 `true`、`false` 或 `"unknown"`。未知就明确写 unknown，不要把“视频有音轨”误写成“provider 能保留源音频”。

来源类型：`official_documentation`、`official_model_card`、`official_ui`、`provider_support`、`first_party_test`、`community`。只有 community 证据时允许继续研究，但验证结果会保留 warning；不能把它写成官方能力。

## 2. 验证 profile

```bash
python3 scripts/provider_capability.py verify \
  --bundle work/provider_capabilities.json \
  --max-age-days 30 \
  --output work/provider_capabilities_verification.json \
  --markdown work/provider_capabilities_verification.md \
  --strict
```

默认 profile 超过 30 天就阻塞；临时研究可用 `--allow-stale` 把它降为 warning，但不应用于付费提交或客户交付。未来日期、重复 provider、非法 URL/source type、空 modes/aspects/resolutions、非法时长或参考上限都会 fail closed。

## 3. 绑定 prompt pack

```bash
python3 scripts/video_prompt_pack.py \
  --storyboard-plan work/storyboard_plan.json \
  --provider dreamina_seedance \
  --capability-profile work/provider_capabilities.json \
  --require-capability-profile \
  --resolution 720p \
  --asset-root work \
  --output work/video_prompt_pack.json \
  --markdown work/video_prompt_pack.md \
  --strict
```

每个 generated-video item 会记录 `surface`、`model`、`resolution`、`capability_profile.profile_id` 和 `capability_issues[]`。以下情况会进入 `summary.capability_blocking`：

- provider 没有匹配 profile；
- profile 无效或超过 freshness 上限；
- mode、画幅、时长、分辨率不在 profile 中；
- 首帧 + style key 的图片引用数量超过 profile 上限；
- 开启 capability gate 但没有明确选择 resolution。

`summary.blocking` 同时包含 paid approval 和 capability blockers，所以 `--strict` 只有在两类问题都清零后才通过。

## 4. 发布门禁

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require provider_capabilities \
  --strict
```

manifest 会现场重新验证 `provider_capabilities.json`，并把当前 bundle/profile id 与 `video_prompt_pack.json` 逐 shot 重算对照。profile 一旦改内容、缺失或超过 30 天，即使旧 prompt pack 还在，也会要求刷新当前 surface 证据并重新生成 prompt pack。

## 边界

- 脚本不联网、不访问 provider UI、不提交任务、不消耗 credits。
- profile 是操作员提供的事实合同，不是 provider 认证、数字签名或永久规格。
- 不要把一个 surface 的上限复制到另一个 surface；provider 相同也不代表 UI/API controls 相同。
- `resolutions`、价格、model id、输入上限和 API 行为都可能变化，执行前应优先核对官方来源与当前界面。
