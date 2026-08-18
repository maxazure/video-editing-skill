# Subtitle Style Preview — 真实画面字幕样式预览

在最终编码前，把 `render_final.py` 的真实 ASS 字幕预设渲染到源片早、中、晚代表帧，比较后再记录最终选择。它解决的是“知道预设名，却直到整片渲染后才发现描边、字重、位置或画面冲突”的问题。

## 1. 生成三套默认方案

```bash
python3 scripts/subtitle_style_preview.py create \
  --project-dir . \
  --video origin/talking.mp4 \
  --platform xhs \
  --text "这句字幕要覆盖中英文和数字 2026" \
  --preview-dir verify/subtitle_styles \
  --output work/subtitle_style_preview.json \
  --markdown work/subtitle_style_preview.md \
  --require-selection
```

默认比较：

- `normal`：高对比通用方案；
- `minimal`：干净、叙事感较强；
- `bold_pop`：粗描边、社媒感较强。

每种样式输出一张 JPEG，横向并列源片 15% / 50% / 85% 三个时间点。画面会先按目标平台画幅走与最终 renderer 相同的中心裁切，再通过 `render_final.py` 的 ASS builder、字体、字号和预设渲染；这不是另做的一套近似 CSS mockup。

可用 `--styles normal yellow_pop karaoke` 自定义候选，用多个 `--time 3.2 --time 18.5 --time 42.0` 指定更有代表性的源时间。`--width` 和 `--height` 必须成对提供；不传时按 `--platform` 选择画布。

## 2. 人工比较并记录选择

同时在手机显示尺寸和全尺寸打开每张 JPEG，检查：

1. 早、中、晚不同背景上的对比度；
2. 中文、英文、数字是否清楚，是否有不自然换行；
3. 字幕位置是否与人物脸部、产品 UI 或平台底部信息区冲突；
4. 风格是否与内容语气和品牌一致；
5. `karaoke` 的高亮/底色是否足够明显。

选择后不必重渲染预览：

```bash
python3 scripts/subtitle_style_preview.py select \
  --report work/subtitle_style_preview.json \
  --style bold_pop

python3 scripts/subtitle_style_preview.py verify \
  --report work/subtitle_style_preview.json \
  --strict
```

然后把选择传给最终 renderer：

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --subtitle-style bold_pop \
  --output output/final.mp4
```

## 3. 门禁语义

`subtitle_style_preview.v1` 绑定：

- 项目内源视频的路径、SHA-256、大小和媒体契约；
- 实际字体文件的路径、SHA-256、大小和字体名；
- 目标平台、画布、字号、样本文字、时间点与样式顺序；
- 由当前 `render_final.py` ASS builder 生成的样式摘要；
- 每张预览 JPEG 的项目内路径、SHA-256、大小与几何信息；
- `selected_style`、`selected_preview` 和 canonical `report_id`。

存在以下任一情况时 `verify --strict` 返回 2：源片、字体、ASS 样式定义或 JPEG bytes 改变；预览缺失/变成 symlink；样式顺序、选择或派生字段被手改；启用了 `--require-selection` 但尚未选择。

发布/渲染门禁可用：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage render_ready \
  --require subtitle_style_preview \
  --strict
```

## 4. 边界

- JPEG 只展示代表帧，不验证完整视频里的每个背景，也不替代 1× 成片审片。
- `karaoke` 预览使用均匀分配的示例字级时间，只验证视觉样式；真实高亮节奏取决于 transcript 的词级时间戳。
- 平台 UI 会变化；最终仍要运行 `platform_safe_area_qa.py` 并检查实际平台导出。
- 字幕 CPS、重叠、闪现和媒体越界由 `subtitle_readability_qa.py` 在成片时间线上负责。
- SHA-256 证明复核对象的 bytes 没变，不是身份认证、签名或审美质量分数。
