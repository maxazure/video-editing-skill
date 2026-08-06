# Portable Edit Recipe：换素材复用已审时间线

适用于同栏目、同广告结构、同片头/字幕/BGM/overlay 逻辑的批量视频：先把已经通过预检的 `render_config.json` 导出成不含本地路径的配方，再在新项目精确绑定素材并回放。

## 1. 导出

```bash
python3 scripts/edit_recipe.py export \
  --config work/render_config.json \
  --name fast-tech-explainer \
  --description "快节奏科技口播，双段原话 + 卡片 + ducking" \
  --output work/recipes/fast-tech-explainer_edit_recipe.json \
  --markdown work/recipes/fast-tech-explainer_edit_recipe.md
```

`--name` 只接受小写字母、数字和连字符。导出前会自动运行 `edit_preflight.py`；空 clips、缺素材、坏 transcript mapping 或其他 blocker 会直接拒绝导出。

脚本递归扫描 config 中的本地文件引用，把同一真实文件合并成一个槽位：

```json
{
  "clips": [
    {
      "video": "${video_1}",
      "transcript": "${transcript_1}",
      "segment_id": 1
    }
  ],
  "bgm": "${audio_1}"
}
```

recipe 只记录槽位名、类型、occurrence、原文件 SHA-256/大小/后缀、源 config SHA-256 和无路径 preflight 摘要，不记录原始文件路径。远程素材必须先下载并登记为本地文件。

## 2. 独立验证

```bash
python3 scripts/edit_recipe.py verify \
  --recipe work/recipes/fast-tech-explainer_edit_recipe.json \
  --markdown work/recipes/fast-tech-explainer_verify.md
```

验证会重新计算：

- recipe version/kind/name 和非空 clips；
- slot 名称、类型、原文件 digest、大小及 occurrence；
- template 的 placeholder 与 slot 集合精确相等；
- 没有残留本地路径、嵌入式 placeholder 或未槽位化远程输入；
- `edit_preflight` / `human_preview` 契约存在；
- canonical `portable_sha256` 与内容相符。

项目内出现 `edit_recipe.json` / `*_edit_recipe.json` 后，`pipeline_manifest.py` 也会现场执行同一验证；手改 `summary.blocking` 没有用。

## 3. 在新项目回放

先打开 recipe Markdown，看完整 slot 表，再为每个槽位各传一次 `--bind`：

```bash
python3 scripts/edit_recipe.py replay \
  --recipe work/recipes/fast-tech-explainer_edit_recipe.json \
  --bind video_1=origin/episode-02.mp4 \
  --bind transcript_1=work/episode-02_transcript_reviewed.json \
  --bind image_1=work/cards/episode-02.png \
  --bind audio_1=origin/music/episode-02.wav \
  --output work/render_config.json \
  --receipt work/edit_recipe_replay.json \
  --markdown work/edit_recipe_replay.md \
  --strict
```

缺失、重复或未知 binding 会失败；视频/音频/图片/字幕/transcript 扩展名类型不匹配也会失败。成功回放会：

1. 把槽位替换成新项目的绝对本地路径；
2. 为每个绑定文件记录 SHA-256、大小和类型；
3. 写出 `edit_recipe_replay.v1` receipt；
4. 对生成的 config 自动运行 `edit_preflight.py`；
5. 在 blocker 下退出 2，`--strict` 还会把 warning 升级为退出 2。

默认不覆盖已有 recipe、config、receipt 或 Markdown。确认目标正确后才能显式加 `--force`。

## 4. 回放后

`ready` 只表示路径、结构、时间范围和本地输入通过预检。它不证明新视频和 transcript 在语义上适合旧的 segment id、时间码、字幕文案或 overlay 内容。

```bash
python3 scripts/render_final.py \
  --config work/render_config.json \
  --output output/recipe-replay_master.mp4 \
  --versioned-output

python3 scripts/render_qa.py \
  output/recipe-replay_master_V1.mp4 \
  --platform douyin \
  --json verify/recipe-replay_render_qa.json
```

必须播放最终成片，重点核对切点、字幕、B-roll/PIP、卡片文字、BGM 和片尾。最终上传件仍用 `approval_receipt.py` 绑定；recipe digest 只证明模板内容身份，不是数字签名、作者身份或人工发布授权。

## 5. 适用边界

- 适合：固定栏目、品牌系列、产品广告变体、相同段落结构和相同素材类型的批量生产。
- 不适合：新素材时长/段落结构完全不同、需要重新找 highlight、目标稿变化大、镜头语义必须重排的项目。
- recipe 是 exact template replay，不做内容理解、不自动缩放时间码、不重新选择 take，也不提交任何生成任务。
