# 17 — 一条视频 × 三平台导出

`scripts/multi_export.py` 把一条主视频自动转成三个平台版本：

| 平台 | 分辨率 | 比例 | 时长上限 | 备注 |
|---|---|---|---|---|
| **小红书 / RED** | 1080×1440 | 3:4 | — | 占满 feed 缩略图 (+40% 显示面积) |
| **抖音 / TikTok** | 1080×1920 | 9:16 | — | 全屏沉浸式 |
| **微信视频号** | 1080×1920 | 9:16 | **≤ 60s** | 社交链分发，超时自动截断 |

## 用法

```bash
python3 scripts/multi_export.py \
  output/day58_master.mp4 \
  --output-dir output/ \
  --platforms xhs douyin wxch
```

如果源视频是横屏真人/访谈，不建议直接中心裁切。先生成主体感知裁切计划，再导出对应平台：

```bash
python3 scripts/smart_reframe.py origin/talk.mp4 \
  --detections work/detections.json \
  --scene-boundaries work/scene_boundaries.json \
  --platform douyin \
  --output work/reframe_douyin.json \
  --markdown work/reframe_douyin.md

python3 scripts/multi_export.py origin/talk.mp4 \
  --platforms douyin \
  --reframe-plan work/reframe_douyin.json \
  --output-dir output/
```

一份 `reframe_plan` 只匹配一个目标尺寸；要同时导出 3:4 和 9:16，分别生成 `xhs` / `douyin` 两份计划。

输出：

```
output/
├── day58_master.mp4                # 你给的源
├── day58_master_xhs.mp4            # 3:4 上下裁
├── day58_master_douyin.mp4         # 9:16 直通
├── day58_master_wxch.mp4           # 9:16 + ≤60s 截断
└── multi_export_manifest.json      # 路径清单
```

## 比例转换规则

| 源 → 目标 | 处理 |
|---|---|
| 9:16 → 3:4 | 上下中心裁切 |
| 9:16 → 9:16 | 直接 scale |
| 16:9 → 9:16 | 左右中心裁切 |
| 16:9 → 3:4 | 左右中心裁切 |

传入 `--reframe-plan` 后，固定中心裁切会被替换为 `smart_reframe.py` 生成的 `track` / `center` / `letterbox` 计划：人物偏左/偏右时跟随主体，多人过宽时保留全画面并补边。

> 建议主出走 **9:16 1080×1920**（最通用），让 multi_export 去派生 3:4 版本。如果主出是 3:4，再裁到 9:16 会损失内容（顶部钩子区可能被裁掉）。

## 平台运营差异提醒

| 项 | 小红书 | 抖音 | 视频号 |
|---|---|---|---|
| 完播率门槛 | 完播 ≥ 40%（短视频） / 60%（30s 以下） | 完播 ≥ 45% | 完播 + 社交链 |
| 标签数 | 3-6 个 #tag | 3-5 个，蹭热点 | 2-3 个 + 话题 |
| 封面 | 决定 70% 点击；三层大字 | 首帧即封面 | 首帧 + 引导关注 |
| 评论权重 | 8 分（关注）> 4 分（评论/分享）> 1 分（点赞/收藏） | 评论 + 转发 | 朋友点赞最重要 |
| 发布时段 | 工作日 7:30/21:00 | 工作日 12:00/20:00 | 周末 + 晚间 |

## 与 generate_caption 联用

```bash
# 1. 出主视频
python3 scripts/render_final.py --config <cfg> --profile tech_pro --output output/day58_master.mp4

# 2. 三平台导出
python3 scripts/multi_export.py output/day58_master.mp4 --output-dir output/

# 3. 三平台文案各一份（手工微调时只改 title 长度/tag 顺序，body 共用）
python3 scripts/generate_caption.py --script work/clean_script.md --profile tech_pro \
  --output output/day58_caption.json
```

> 单一 caption 通常够用。差异化主要发生在**标题** —— 小红书可以稍长（≤20 字），抖音要更短（≤15 字），视频号偏正经（避免感叹号）。手工 fork 三份 title 即可。
