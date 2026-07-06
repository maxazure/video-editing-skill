# Hook Variants 开头钩子批量方案

`hook_variants.py` 用在转写之后、清稿之前：同一条视频先生成多种前三秒开头角度，再选一个进入 `rewrite_script.py` 或直接改 `clean_script.md` 的 Hook 段。

## 什么时候用

- 视频主题明确，但开头不够抓人。
- 想为小红书/抖音/TikTok/YouTube Shorts 准备多个 A/B hook 角度。
- 长视频切短视频时，已经有候选片段，但需要决定第一句怎么切入。
- 生成视频前想先确定“Hook 场景”的情绪、节奏和画面 cue。

## 常用方式

```bash
python3 scripts/hook_variants.py \
  --transcript work/transcript.json \
  --topic "AI剪辑" \
  --persona "剪辑师" \
  --platform xhs \
  --output work/hook_variants.json \
  --markdown work/hook_variants.md \
  --strict
```

英文 / YouTube Shorts 可用：

```bash
python3 scripts/hook_variants.py \
  --transcript work/transcript.json \
  --topic "product demos" \
  --persona "founder" \
  --platform youtube_shorts \
  --language en \
  --output work/hook_variants.json \
  --markdown work/hook_variants.md
```

## 输出

`hook_variants.json` 使用 `hook_variants.v1`，核心字段：

- `summary.recommended`：当前最高分且未被 content guard 阻塞的 hook id。
- `summary.usable`：可用 hook 数量；为 0 时 `--strict` 返回 2。
- `variants[].hook`：开头第一句。
- `variants[].family`：角度类型，如 `pattern_interrupt`、`pain_question`、`number_map`、`proof_first`。
- `variants[].visual_cue`：Hook 画面建议，可转成分镜第一镜。
- `variants[].risks`：长度、平台规则或导流等风险。

Markdown 会生成一个 review 表，适合直接给人工选 `hook_01` / `hook_02`。

## 接入清稿

1. 打开 `work/hook_variants.md`。
2. 选一个 hook id，把对应 `hook` 文本加入 `rewrite_script.py --emit-prompt` 生成的提示词，要求 LLM 把它作为最终 Hook。
3. 保存 LLM JSON 后照常运行：

```bash
python3 scripts/rewrite_script.py \
  --transcript work/transcript.json \
  --llm-output work/llm.json \
  --output work/clean_script.md
```

如果已经有 `clean_script.md`，也可以直接把 `## Hook` 段替换成选中的 hook，再继续跑 `content_guard.py`、`auto_enrich.py`、`edit_preflight.py` 和 `render_final.py`。

## 注意

- 脚本不调用 LLM、不上传、不提交任何生成任务。
- 只要 hook 内出现导流、外链、医疗功效、财富诱导等硬风险，对应 variant 会标记为 `blocked`。
- `pipeline_manifest.py --require hook_variants` 可把这一步作为项目 review gate。
