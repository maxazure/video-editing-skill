# Flash Safety QA — 最终成片闪烁 / 光敏风险预检

用于最终 master 或平台导出文件的本地启发式筛查：逐帧测量大面积亮度变化和饱和红变化，把相反方向的变化配成 flash，再检查滚动 1 秒和 5 秒窗口。

> 这是风险分流工具，不是医疗建议、法律合规证据或 WCAG / Harding / 广播认证。它不检测空间条纹 pattern，也可能漏掉局部、高分辨率、色彩管理或显示设备相关风险。高风险或受监管交付必须使用认可的 photosensitivity analyzer。

## 分析最终成片

```bash
python3 scripts/flash_safety_qa.py analyze output/final.mp4 \
  --project-dir . \
  --output verify/flash_safety_qa.json \
  --markdown verify/flash_safety_qa.md \
  --strict
```

默认分析参数：

- 最高 30 fps；源帧率更低时不补造更高采样率；
- 等比例缩小到 64 px 宽，降低噪声和计算量；
- 单像素亮度 / 红信号变化阈值 `0.10`；
- 变化面积至少覆盖画面的 `25%`；
- 两个相反方向 transition 在 `0.50s` 内配成一次 flash；
- 滚动 1 秒内超过 3 次 flash，或滚动 5 秒内至少 10 次 flash，进入 blocker。

需要复现实验参数时，可以显式传入：

```bash
python3 scripts/flash_safety_qa.py analyze output/final.mp4 \
  --analysis-fps 30 --analysis-width 64 \
  --luma-change 0.10 --red-change 0.10 \
  --area-fraction 0.25 --pair-gap 0.50 \
  --project-dir . --output verify/flash_safety_qa.json \
  --markdown verify/flash_safety_qa.md --strict
```

不要为了让报告变绿而随意放宽阈值。参数变化会进入 report contract；旧报告不能冒充新设置的结果。

## Live verify

```bash
python3 scripts/flash_safety_qa.py verify \
  --report verify/flash_safety_qa.json \
  --project-dir . \
  --strict

python3 scripts/pipeline_manifest.py . \
  --require flash_safety_qa \
  --strict
```

`verify` 会重新读取 source bytes / 媒体契约，并用报告中的参数重新分析。source、算法合同、analysis、派生状态或 report id 漂移都会阻断。

## 命中后怎么处理

1. 用正常速度完整播放 Markdown 中标出的时间段；contact sheet 不能判断闪烁频率。
2. 优先删除反复闪白 / 闪红，降低亮暗反差或红色饱和度，缩小闪烁面积，或降低交替频率。
3. 从时间线 / 原素材重新渲染，不要对最终 master 反复转码遮掩问题。
4. 重新运行 `analyze --strict` 和 `verify --strict`。
5. 广播、广告、医疗、教育或其他高风险交付，再交给认可的专业工具复核。

## Prompt 模板

```text
请对最终成片 output/final.mp4 运行 flash_safety_qa：
- 用默认参数检查大面积亮度与饱和红闪烁；
- 输出 JSON 和 Markdown；
- 命中 blocker 时列出精确区间和降低风险的剪辑建议，不要自行放宽阈值；
- live verify source/report 后，再把 flash_safety_qa 加入 pipeline_manifest 的 publish gate；
- 明确说明该结果只是启发式筛查，不是医疗或法规认证。
```
