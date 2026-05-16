# 16 — Content Guard 平台雷区 lint

V3 加入了 `scripts/content_guard.py`，在导出前自动检测 80+ 条小红书平台雷区。

## 两级告警

**🚫 HARD-BLOCK**：直接阻断导出，必须修改才能继续。涵盖：

- **广告法极限词**：最 / 第一 / 唯一 / 万能 / 顶级 / 全网最低 / 遥遥领先 / 国家级 / 销量冠军 ……
- **导流外站**：微信 / 威信 / 薇信 / VX / wx / +V / 加微 / QQ / 手机号 / 抖音 / 淘宝 / 二维码 / 外站 URL ……
- **医美/医疗功效**（2026-02 新规）：治愈 / 根治 / 祛斑 / 抗衰 / 水光针 / 热玛吉 / 线雕 / 医生同款 / 三甲推荐 ……
- **财富诱导**：年入 X 万 / 月入 X 万 / 躺赚 / 财富自由 / 稳赚不赔 / 零成本 / 包过 / 暴利 ……

**⚠️ SOFT-WARN**：可发布但建议复核。涵盖：

- 标题 > 20 字
- 标点连用 ≥3（`!!!`、`???`、`。。。`）
- emoji 占比 > 30%
- 正文 > 800 字

## 用法

```bash
# 检查标题
python3 scripts/content_guard.py --title "AI失业焦虑？我看到更多机会"

# 检查清稿
python3 scripts/content_guard.py --script work/clean_script.md

# 检查发布正文
python3 scripts/content_guard.py --caption output/day58_caption.json

# 检查 render config 的所有可见文本字段
python3 scripts/content_guard.py --config work/render_config.json --strict
```

`--strict` 让任何违规（包括 SOFT）都返回非零退出码，适合放进 CI 或预提交钩子。

## 自动集成点

content_guard 已经被以下脚本默认调用：

- **`render_final.py`**：解析 config 后立即检查 title/subtitle/chapters；HARD 违规时退出码 2，提示 `--no-content-guard` 跳过
- **`rewrite_script.py`**：验证 LLM 重组的输出，避免错误输出污染清稿
- **`generate_caption.py`**：生成的 title 必须过 HARD 检查，body 过 SOFT 检查（允许更多 emoji）

## 越过守卫

只在你明确知道在干什么的时候用：

```bash
python3 scripts/render_final.py ... --no-content-guard
python3 scripts/generate_caption.py ... --no-strict
```

> ⚠️ 越过守卫 = 放弃账号安全网。绕过 HARD 规则导致的限流/封号要自己承担。

## 平台数据信源（2024-2026）

- 私信导流新规：2025-01-07
- 社区公约 2.0：2026-01-19
- 医美新规：2026-02-10
- 私域引流五类红线：100ec / 电商派
- 广告法极限词清单：adquan / 拓客吧 / 知乎自媒体
- 限流自查方法：新红数据 / 红薯编辑器 / 人人都是产品经理

## 关键日期锚点

| 日期 | 政策 |
|---|---|
| 2025-01-07 | 私信导流新规：仅允许企微/个微名片，五类违规累计 3 次限流，5 次封号 |
| 2026-01-19 | 社区公约 2.0 |
| 2026-02-10 | 医美内容审核新规（"治愈/根治/水光针/医生同款"等词进入硬性禁词） |

## 待办（V3.1+）

content_guard 目前覆盖**文本**雷区。后续会加：

- 画面二维码扫描（zxing-cpp）
- 外站水印 OCR（抖音/快手/视频号 logo）
- BGM 指纹比对（acoustID）
