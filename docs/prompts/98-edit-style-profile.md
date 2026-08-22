# 98 — Creator-owned Edit Style Profile

当你希望多条视频保持同一种字幕、封面、调色、声音和节奏方向，但又不想把某一条片子的时间线或素材路径复制到下一条片子时，使用 `edit_style_profile.py`。

它与 `edit_recipe.py` 的边界不同：

- `edit_style_profile` 保存“怎么判断与默认怎么做”，没有素材路径或 clip timeline。
- `edit_recipe` 保存一条已经审过的具体 timeline 结构，并用 typed slots 换素材重放。
- 项目 `render_config` 和 CLI 始终优先；style profile 只填缺省值，不会覆盖这条片子的明确决定。

## 1. 生成并填写 spec

```bash
python3 scripts/edit_style_profile.py template \
  --output work/edit_style_profile_spec.json
```

修改模板里的以下部分：

- `creative_direction`：一个主方向、最多一个 accent、必须坚持和明确避免的规则。
- `pacing`：去停顿力度、B-roll 密度、hook/body 镜头间隔、目标时长；这些是 Agent 规划依据，不会偷偷改写 timeline。
- `render_defaults`：允许进入本地渲染/封面脚本的有界默认值，包括字幕/封面 preset、BGM、ducking、口播降噪、调色和版本化输出。
- `caption_defaults`：常用发布时段与品牌/产品名强制拼写。
- `approval`：`manual_direction`、`approved_outputs` 或 `reference_study`，以及实际复核者标签、日期和说明。
- `evidence`：如果 basis 是 `approved_outputs` 或 `reference_study`，至少记录一项 `sha256:<64 hex>` 证据；这里只保存 portable provenance，不保存本地路径。

`approved_by` 和 evidence hash 是本地自报 provenance，不是身份认证、数字签名、授权文件或法律结论。

生图优先使用 Codex 内置 `image_gen` 工具，即 OpenAI GPT Image 2（`gpt-image-2`）。

## 2. 编译与验证 profile

```bash
python3 scripts/edit_style_profile.py create \
  --spec work/edit_style_profile_spec.json \
  --output work/edit_style_profile.json \
  --markdown work/edit_style_profile.md \
  --strict

python3 scripts/edit_style_profile.py verify \
  --profile work/edit_style_profile.json \
  --strict
```

脚本拒绝未知字段、路径型 render default、非法 preset、越界数值、空审批、未来日期、重复 evidence 和缺失证据。`verify` 会重算 schema、stored status/summary 与 canonical `profile_id`；手改 profile 内容后旧 id 会失效。

## 3. 直接用于最终渲染

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --style-profile work/edit_style_profile.json \
  --output output/final.mp4
```

合并顺序是：

1. style profile 填补缺失字段；
2. `render_config.json` 已有值保留；
3. `--subtitle-style`、`--color-grade`、`--bgm-volume` 等 CLI 参数继续拥有最高优先级。

这意味着同一 profile 可以为多数项目提供统一基线，同时允许某条片子因素材、客户或平台需要做明确例外。

## 4. 生成发布文案

```bash
python3 scripts/generate_caption.py \
  --script work/clean_script.md \
  --profile tech_pro \
  --style-profile work/edit_style_profile.json \
  --output work/caption.json
```

style profile 的 `force_spelling` 会先统一品牌/产品名，`preferred_windows` 会优先于通用 audience profile 的发布时间；content guard 仍照常运行。

封面 A/B 方案也可沿用同一默认风格：

```bash
python3 scripts/cover_variants.py output/final_xhs.mp4 \
  --title "<封面文字>" \
  --style-profile work/edit_style_profile.json \
  --output work/cover_variants.json
```

如果同时传了 `cover_variants.py --style`，该项目级选择优先于 profile 的 `cover_style`。

## 5. 先落成 styled config（可选）

需要人工 diff 或交给 NLE/其他脚本前，可以生成一份显式配置和 receipt：

```bash
python3 scripts/edit_style_profile.py apply \
  --profile work/edit_style_profile.json \
  --config work/render_config.json \
  --output work/render_config_styled.json \
  --receipt work/edit_style_profile_apply.json \
  --markdown work/edit_style_profile_apply.md \
  --strict
```

receipt 记录输入/输出 config digest、实际填入的字段和被项目配置保留的 override。它不代替 `edit_preflight.py`、渲染 QA 或最终人工审批。

## 6. 发布门禁

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --require edit_style_profile \
  --strict
```

只要项目中出现 `edit_style_profile.json`，manifest 就会现场验证；schema、summary 或 `profile_id` 漂移会阻塞。创意方向本身仍需人判断，canonical digest 只能证明“现在看到的是同一份配置”，不能证明它审美正确。
