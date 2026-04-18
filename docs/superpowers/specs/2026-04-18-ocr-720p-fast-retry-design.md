# OCR 720p Fast Retry 设计

## 背景

当前项目已经完成了 OCR provider 配置化与 DirectML 加速，但默认 OCR 仍然直接对原始截图做全分辨率识别。用户希望继续压缩 OCR 延迟，并明确提出两点：

1. 提供单独的高精度参数，允许 Agent 在误差过大时主动切回高精度。
2. 同时支持自动高精度重试，但自动规则要足够保守，不能把吞吐收益吃掉。

前一轮已经确认：

- `provider=auto|cpu|dml`、`lang`、`use_cls`、`text_score` 已落地。
- DirectML 路线已经工作。
- 默认文本协议与 debug 管线已经收口。

因此这轮问题不再是“有没有多后端”，而是“在当前 OCR 路径前，是否要主动降分辨率，以及怎样把它做成可控的模式切换”。

## 用户约束

- 不能只拿 `1.0x` OCR 输出当绝对真值；要结合原始截图做常识判断。
- 版本号、明显图标误识别不应纳入关键准确率判断。
- 需要看 box 稳定性，而不只是文本列表。
- 需要支持：
  - 显式高精度
  - 自动高精度重试

## 实测结论

### 结论一：固定低分辨率确实能提速，但收益和页面类型强相关

在多轮实测后，`0.75x` 以下虽然有时会更快，但在若干页面上明显丢锚点，因此不适合作为默认快档。

### 结论二：`1280x720` 已经不是偶然点位，而是明确的 fast 候选

针对后续修好的截图链路和更复杂公告页重新测试后，有两个稳定现象：

- CPU 路径上，`1280x720` 明显优于 `1.0x`、`0.9x`、`0.8x`
- `0.5x` 没有继续变好，反而开始丢锚点，说明甜点不是“越低越快越好”

### 结论三：对 box 稳定性更敏感时，`0.8x/0.9x` 仍比 `1280x720` 更稳

在公告页的锚点对比里：

- `0.9x` 与 `0.8x` 的 box 中位 IoU 约在 `0.92` 左右
- `1280x720` 的 box 中位 IoU 约在 `0.88` 左右

也就是说，`1280x720` 更偏吞吐优先，而 `0.8x/0.9x` 更偏 box 稳定优先。

### 结论四：在当前用户确认后，本轮默认 fast 档以 `1280x720` 为准

虽然不同页面下 `0.8x` 与 `1280x720` 各有优势，但用户已明确选择：

- 默认 `fast = 1280x720`
- `high = 1.0x`

因此本轮设计不再继续引入 `balanced` 档，先把两档模式与自动重试做清楚。

## 目标

本轮设计目标：

1. 为 OCR 增加两档模式：`fast` 与 `high`
2. 让默认 OCR 模式切到 `fast=1280x720`
3. 引入可配置的自动高精度重试：`retry_high=auto|never|always`
4. 在 debug / verbose 中记录模式与重试事实，但不污染默认文本协议
5. 把性能、质量、语义回归都写成明确验证项

## 非目标

本轮不做：

- 新增第三档 `balanced`
- 引入“低分辨率检测 + 原图识别”的双阶段混合流水线
- 改写 provider 语义或 DirectML/provider cache 主逻辑
- 修改默认文本协议的成功/失败首行结构

## 方案选型

### 方案 A：默认仍是 `high=1.0x`，只新增显式 `fast`

优点：最稳，回归风险低。

缺点：不能兑现这轮想要的默认吞吐收益。

### 方案 B：默认切到 `fast=1280x720`，再加可控的 `high` 与自动重试

优点：

- 默认就能获得实际吞吐收益
- 仍保留显式高精度能力
- 自动重试可以保守启用，减小误伤

缺点：

- 默认行为会变化
- 必须把自动重试条件设计得足够保守

### 方案 C：继续先研究更复杂的检测/识别分离方案

优点：理论上更可能同时兼顾吞吐与 box 稳定性。

缺点：

- 工程改动面大
- 不适合作为当前这轮直接落地的方案

## 结论

当前采用 **方案 B**：

- 默认 `ocr_mode=fast`
- `fast` 固定到 `1280x720`
- `high` 固定到 `1.0x`
- 增加 `retry_high=auto|never|always`

## 设计

### 1. 公共配置面

新增稳定 OCR 配置字段：

- `ocr_mode=fast|high`
- `retry_high=auto|never|always`

对应默认值入口也在本轮冻结：

- CLI：`--ocr-mode fast|high`
- CLI：`--retry-high auto|never|always`
- 环境变量：`TRAIL_OCR_MODE`
- 环境变量：`TRAIL_OCR_RETRY_HIGH`

优先级继续沿用现有 OCR 选项模式：

- CLI 显式参数优先
- 环境变量只作为低优先级默认值

默认值：

- `ocr_mode=fast`
- `retry_high=auto`

语义：

- `fast`：先把截图缩到 `1280x720`，再进入现有 OCR 路径
- `high`：直接对原始截图做 OCR
- `retry_high=never`：严格只跑当前档位一次
- `retry_high=always`：先跑 `fast`，再无条件补跑一次 `high`
- `retry_high=auto`：只在保守触发条件命中时重跑 `high`

组合契约在这里也固定：

- `retry_high` 只在 `ocr_mode=fast` 时生效
- `ocr_mode=high` 时，runtime 始终只执行一次原图 OCR，`retry_high` 被视为 no-op，不会触发额外重跑
- 若 `fast` 首轮后发生 `high` 重试且 `high` 成功，最终对外返回的 `result` 与 `box` 以 `high` 那一轮为准；`fast` 的中间结果只保留在 `--verbose` 调试事实里
- 若 `fast` 首轮触发了低置信 warning，但 `high` 重试成功，则默认输出不再保留 fast 首轮 warning；这些中间事实只进 `--verbose`
- 若 `fast` 首轮已有结果，但 `high` 重试失败或 `high` 仍然没有结果，则最终对外保留 `fast` 首轮结果，并把重试失败事实只写进 `--verbose`
- 只有在 `fast` 首轮本身已经无结果、且 `high` 重试也失败或无结果时，才按现有无结果语义收口失败

### 2. 自动高精度重试策略

第一版采用保守触发，不追求把所有语义误识别都抓出来。

`retry_high=auto` 只在以下情况触发：

1. `hits == 0`
2. OCR 命中结果的平均 `score < 0.92`
3. runtime 已经产出 code 精确等于 `OCR_LOW_CONFIDENCE` 的 warning

若多个条件同时命中，`ocr_retry_reason` 的固定优先级为：

- `no_hits` > `low_confidence` > `warning` > `none`

另外，本轮默认把 `OCR_LOW_CONFIDENCE` 的生产逻辑纳入实现范围；若现有 runtime 路径尚未产出该 warning，需要在实现时补齐，而不是把它留成文档空洞。

设计理由：

- OCR 分数不足以覆盖所有语义错误，过于激进的自动重试会把默认 `fast` 的收益吃掉
- 用户已经明确要求保留显式 `high`，因此“分数看起来还行但 Agent 觉得语义不对”的情况，先由显式高精度处理
- 第一版先把保守自动重试跑通，后续再根据真实页面数据扩阈值或规则

### 3. debug / verbose 事实

默认文本协议不扩张，但 `--verbose` 里要补充以下事实：

- `debug kind=context key=ocr_mode_requested value=<...>`
- `debug kind=context key=ocr_mode_effective value=<...>`
- `debug kind=context key=ocr_scale_applied value=1280x720|native`
- `debug kind=context key=ocr_retry_high value=0|1`
- `debug kind=context key=ocr_retry_reason value=no_hits|low_confidence|warning|none`

这些事实必须继续走当前 debug 管线，不新增新的默认模式字段。

### 4. 质量判断口径

本轮不再用“是否与 `1.0x` OCR 结果完全一样”作为唯一标准。

需要综合：

- 原始截图的人类可见文本
- 关键锚点文本是否命中
- box 的中心漂移与 IoU
- 是否引入明显伪文本

也就是说，后续测试需要同时覆盖：

- 人能看出来是对的，但和 `1.0x` 文本不完全一致的情况
- box 数量相同，但几何明显发散的情况

### 5. 实现边界

这轮实现只在 OCR 调用前增加“缩放模式 + 自动重试”控制层，不改变当前 provider 体系与 OCR engine cache 的主体职责。

优先做法应是：

- 在 runtime OCR 调用前拿到原始截图
- 根据 `ocr_mode` 选择：原图 or 先缩到 `1280x720`
- 调用现有 OCR engine
- 若命中 `retry_high=auto|always` 的条件，再对原图补跑一次 `high`
- 结果与 debug 事实都在 request-local 范围内汇总

这里的缩放边界也固定为：

- 对全屏 OCR，`fast` 将原图按比例缩小到不超过 `1280x720`
- 对 region OCR，先按现有 `capture` 语义拿到裁剪后的局部图，再决定是否缩小
- `fast` 只做 downscale，不做 upscale；如果 region 本身已经小于 `1280x720`，则保持原始尺寸，不强行拉伸

## 验证方案

### 1. 性能回归

- 验证 `fast=1280x720` 在 CPU 路径下相对 `high=1.0x` 有明确收益
- 验证 `fast` 不会在常见页面上退化成比 `high` 更慢

### 2. 质量回归

- 选择至少两类页面：
  - 文本稀疏页
  - 文本密集页
- 对锚点文本做人工可判定校验
- 对共享锚点 box 做中心漂移与 IoU 比较

第一版验收线固定为：

- 默认 `fast=1280x720` 在人工选定锚点集合上的命中数，相对 `high=1.0x` 最多只允许少 `1` 个锚点
- 共享锚点 box 的中位中心漂移必须 `<= 2px`
- 共享锚点 box 的中位 IoU 必须 `>= 0.85`

### 3. 语义回归

- `ocr_mode=fast|high` 行为固定
- `retry_high=auto|never|always` 行为固定
- `auto` 的触发条件只在 spec 规定的保守范围内生效
- `ocr_mode=high` 时不发生额外重跑
- `fast` 重试成功后最终 `result` / `box` 来自 `high`，而不是快档结果与高精度结果的混合体

### 4. 协议回归

- 默认文本协议不泄漏 `ocr_mode_requested` / `ocr_mode_effective` / `ocr_scale_applied` / `ocr_retry_high` / `ocr_retry_reason`
- `--verbose` 才出现模式与重试事实
- 失败路径顺序不变
- `ocr.read --format yaml` 语义不变

## 风险与对策

### 风险 1：默认切到 `fast` 后，个别页面质量波动

对策：

- 保留显式 `high`
- 默认开启保守 `retry_high=auto`
- 用锚点文本与 box 指标做回归

### 风险 2：自动重试触发过多，吞掉吞吐收益

对策：

- 第一版只在 `no_hits` / 明显低置信度时触发
- 把触发原因写入 debug，后续按真实数据调阈值

### 风险 3：`1280x720` 的 box 稳定性不如轻度缩放

对策：

- 把 box 漂移 / IoU 直接纳入回归
- 若后续场景证明 box 质量是更重要目标，再评估恢复 `balanced` 档

## 最终建议

当前最合适的下一步，不是继续无限制地试更多倍率，而是先把已验证的两档模式和保守自动重试落成稳定接口：

- 默认 `fast=1280x720`
- 显式 `high=1.0x`
- 默认 `retry_high=auto`
- 所有模式与重试事实只进 `--verbose` debug，不改默认文本协议
