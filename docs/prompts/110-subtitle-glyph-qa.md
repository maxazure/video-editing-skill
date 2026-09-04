# Subtitle Glyph QA — 字幕逐字符字体覆盖门禁

当 SRT/ASS/JSON 字幕已经生成，但包含生僻字、繁体字、emoji、数学符号、品牌特殊字符或多语言文本时，用 `subtitle_glyph_qa.py` 检查最终字幕的每个可见 Unicode 字符是否真的存在于指定字体文件中。

它读取 `subtitle_pack.v1` 和项目内 TTF/OTF/TTC/OTC 的 OpenType `cmap`，不安装字体、不联网、不渲染、不改字幕。主字体、显式 fallback、字幕包与派生结果都会写入 SHA-256 绑定报告；系统隐式 fallback 不算通过证据。

## 推荐流程

先生成最终时间线字幕包：

```bash
python3 scripts/subtitle_pack.py \
  --config work/render_config.json \
  --output-dir output/subtitles \
  --basename final \
  --speed 1.25 \
  --offset 2.0
```

再用最终 renderer 实际配置的字体检查完整字幕：

```bash
python3 scripts/subtitle_glyph_qa.py analyze \
  --project-dir . \
  --subtitle-pack output/subtitles/final.json \
  --font 'fonts/NotoSansSC[wght].ttf' \
  --output verify/subtitle_glyph_qa.json \
  --markdown verify/subtitle_glyph_qa.md \
  --strict

python3 scripts/subtitle_glyph_qa.py verify \
  --project-dir . \
  --report verify/subtitle_glyph_qa.json \
  --strict
```

如果主字体缺少 emoji 或其他符号，把经过确认的字体文件复制到项目 `fonts/`，显式加入：

```bash
python3 scripts/subtitle_glyph_qa.py analyze \
  --project-dir . \
  --subtitle-pack output/subtitles/final.json \
  --font fonts/SourceHanSansSC-Regular.otf \
  --fallback-font fonts/NotoColorEmoji.ttf \
  --output verify/subtitle_glyph_qa.json \
  --markdown verify/subtitle_glyph_qa.md \
  --force --strict
```

显式 fallback 会通过完整覆盖检查，但保留 warning；需要品牌字体绝不换字形时加 `--require-primary`，任何 fallback 使用都会阻断。

## 报告怎么看

- `inventory.required[]`：去重后的可见字符、codepoint、Unicode 名称和出现的 cue。
- `coverage.assignments[]`：每个字符由哪一个显式字体覆盖。
- `coverage.primary_missing[]`：主字体缺少、需要 fallback 或最终完全缺失的字符。
- `coverage.missing[]`：所有显式字体都没有的字符，必须修复。
- `fonts[]`：字体相对路径、SHA-256、大小、容器、face 数和读取到的 cmap formats。
- `report_id`：字幕、字体、设置与完整派生结果的 canonical digest。

发布前可以强制要求：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require subtitle_glyph_qa \
  --strict
```

## 边界

- cmap 覆盖不等于字形一定漂亮，也不证明 shaping、kerning、竖排、彩色 emoji、ASS 布局或手机尺寸可读性。
- TTC/OTC 报告按 collection 各 face 的 cmap 并集检查，并给 warning；必须确认 renderer 选择的实际 face。
- 变体选择符、ZWJ、换行和空格不要求独立可见 glyph；组合附标、emoji modifier 等可见组成部分仍会检查。
- fallback 必须显式列出并在最终 renderer 中实际配置。系统“也许会自动找到一个字体”不是可审计证据。
- OpenType cmap format 13 的 many-to-one 映射常用于 last-resort/tofu 字体；脚本会识别并警告，但不会把它计作真实字形覆盖。
- 最终仍要正常速度看完字幕，并用全分辨率代表帧确认没有方框、错字形、重叠或不可读问题。
