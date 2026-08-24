# Scoped Video Edit Review — 局部 AI 视频编辑范围复核

用于 AI provider 对已有视频做局部 edit 之后，例如只换背景、服装、包装、道具、局部 VFX、文字或声音。它解决的不是“成片本身好不好看”，而是更严格的问题：**声明的目标是否真的改变，同时未声明区域是否保持原样**。

脚本全程本地运行，不上传素材、不调用 provider、不提交任务，也不消耗 credits。

## 为什么不能只审编辑结果

局部生成式编辑经常把目标改对，却同时漂移人物脸、手势、口型、机位、构图、灯光、道具、剪辑节奏或原声。只看编辑后视频，很难记住原片在同一时刻的准确状态。

`scoped_video_edit_review.py` 因此把一次 edit 固定成两部分：

- 一个 `change only`：单次 provider call 只允许一个明确方向；
- 至少两个 `preserve invariants`：逐项命名不能被损坏的层。

`prepare` 会绑定原片、编辑结果和证据 SHA-256/大小/媒体契约，在目标范围 15% / 50% / 85% 生成同时间点左右并排 JPEG，并生成完整范围左右并排 H.264 preview。两边都有音轨时，preview 保留两条独立 AAC track，供分别选择 source / edited 试听。

## 1. 准备 source-bound A/B 证据

以下示例只把蓝色夹克改成红色；人物身份、表演动作、机位、构图和原声都必须保持：

```bash
python3 scripts/scoped_video_edit_review.py prepare \
  --project-dir . \
  --source origin/presenter.mp4 \
  --edited work/presenter-red-jacket.mp4 \
  --change-category wardrobe \
  --change "change only the jacket from blue to red" \
  --preserve subject_identity \
  --preserve performance_motion \
  --preserve camera_motion \
  --preserve framing_composition \
  --preserve source_audio \
  --start 0 \
  --end 8 \
  --evidence-dir verify/scoped_video_edit \
  --output work/scoped_video_edit_review_request.json \
  --markdown work/scoped_video_edit_review_request.md \
  --response-template work/scoped_video_edit_review_response.json
```

`--change-category` 可选：`subject / wardrobe / object / background / look / lighting / vfx / text / audio / other`。

`--preserve` 可重复使用：

| key | 要检查什么 |
|---|---|
| `subject_identity` | 人脸、发型、肤色、身材比例和身份连续性 |
| `wardrobe` | 非目标服装与配饰 |
| `performance_motion` | 姿势、手势、口型、运动路径、速度和时机 |
| `camera_motion` | 机位、焦段感、推拉摇移、手持抖动 |
| `framing_composition` | crop、画幅、构图、透视和主体尺度 |
| `scene_outside_scope` | 目标以外的人、物体和场景区域 |
| `lighting_color` | 光向、曝光、色温和颜色关系 |
| `props_text` | 非目标道具、产品几何、Logo 和可读文字 |
| `edit_rhythm` | cuts、事件顺序、节奏和总时序 |
| `source_audio` | 原对白、音色、环境声、音效、音乐与同步 |

必须至少声明两个保护项。音频本身是目标时，不得同时写 `--preserve source_audio`。时长、尺寸和帧率始终作为自动媒体契约检查；超出容差会直接阻塞，不能靠人工 pass 覆盖。

输出 Markdown 同时给出 provider-neutral suggested prompt，结构是：直接 edit source、一个 target、明确时间范围、逐项 preserve、其余不得改变。它只是 scope contract；实际 surface 是否支持 edit、source-audio preservation、时间范围或标注框，仍需用当前 provider capability 证据核验，不能猜。

## 2. 填写人工 response

先完成三遍播放：

1. 原片完整 1×、带声；
2. 编辑结果完整 1×、带声；
3. 左原片/右编辑结果的完整 scope comparison 1×，并分别选择 source / edited 音轨。

再打开三张同时间点 JPEG，填写 `target_change` 和每个 protection。只允许 `pass / fail / not_observable`；遮挡、motion blur、音轨不可选或证据不足时必须填 `not_observable`，不能猜 pass。

```json
{
  "version": "scoped_video_edit_review_response.v1",
  "request_id": "<copy-from-request>",
  "reviewed_by": "<reviewer-label>",
  "playback": {
    "source_full_1x": true,
    "edited_full_1x": true,
    "comparison_scope_1x": true
  },
  "target_change": {
    "status": "pass",
    "evidence": "夹克在声明范围内始终为红色，领口和袖口没有漏改。"
  },
  "protections": [
    {
      "key": "subject_identity",
      "status": "pass",
      "evidence": "早中晚并排帧与完整播放中，脸、头发和身材比例一致。"
    },
    {
      "key": "performance_motion",
      "status": "fail",
      "evidence": "3.2 秒右手路径提前，触碰道具的时机和原片不同。"
    }
  ],
  "verdict": "fail",
  "repair_action": "只重做夹克颜色；锁定原片右手路径、速度、接触时机和口型，不改动作层。",
  "notes": "三遍播放与三张同时间点证据已检查。"
}
```

response 必须精确覆盖 request 中全部 protection，不能多、不能少。目标或任一保护项为 `fail/not_observable` 时，`verdict` 必须是 `fail`，并填写具体 `repair_action`。

## 3. Audit 与 live verify

```bash
python3 scripts/scoped_video_edit_review.py audit \
  --request work/scoped_video_edit_review_request.json \
  --response work/scoped_video_edit_review_response.json \
  --output work/scoped_video_edit_review.json \
  --markdown work/scoped_video_edit_review.md \
  --strict

python3 scripts/scoped_video_edit_review.py verify \
  --report work/scoped_video_edit_review.json \
  --strict
```

`verify` 会现场重读：

- 原片和编辑结果的 bytes、大小与媒体契约；
- 三张同时间点 JPEG 和 comparison preview；
- edit scope、sample times、suggested prompt 与保护项；
- response coverage、播放确认、canonical review、summary 和 report id。

任一源、编辑结果、证据或派生状态变化都会 fail closed。修复/重生后必须重新 `prepare → audit`，不能手改旧 hash。

发布前可以强制门禁：

```bash
python3 scripts/pipeline_manifest.py \
  --project-dir . \
  --target-stage publish_ready \
  --require scoped_video_edit_review \
  --strict
```

## 边界

- 每次 provider call 只做一个 edit direction。换背景、换装和改 Logo 应拆开提交、分别复核，避免无法归因的漂移。
- 同时间点证据和媒体契约不能自动判断身份、动作或构图是否一致；人工完整播放仍是 gate 的核心。
- preview 左侧是 source、右侧是 edited；两个音轨都存在时必须明确切换试听，默认播放其中一条不算完成音频复核。
- provider 重新编码导致 codec/pixel format 改变不自动失败；时长、尺寸和帧率漂移会阻塞。音质、音色和同步仍由 `source_audio` 人工检查。
- reviewer label 和 SHA-256 是本地审计标签，不是身份认证、数字签名或授权证明。
- 本流程不证明 provider 的 Edit 模式确实支持某项控制，也不替代上传/付费/真人/品牌/IP 的 `production_authorization.py`。
