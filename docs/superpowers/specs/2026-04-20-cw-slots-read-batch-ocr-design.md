> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# `cw slots read` 批量 OCR 与定向读取设计

## 背景

`trail cw slots read` 当前的读取模型是逐槽位点击后立即 OCR：

1. 点击槽位
2. 等待信息面板稳定
3. 对固定的 `SLOT_NAME_REGION` 做一次 OCR
4. 关闭信息面板
5. 等待关闭稳定

默认不传 `--slot` 时，会把 `front(4) + back(6) + hand(9)` 共 19 个槽位全部顺序读一遍。

这条链路在真实使用里暴露出两个问题：

1. **速度过慢**：即使 OCR 区域只有约 `201x61`，单次 OCR 仍然不便宜；19 槽全量扫时，总耗时会迅速放大。
2. **识别不稳**：当前直接把 OCR 片段拼成名字，没有候选角色名归一化，容易出现 `用非`、`用椒`、`带带` 这类脏值。

用户这次明确希望同时处理两件事：

1. 命令本身应减少 OCR 调用次数，不能再对 19 个槽位分别做 19 次 OCR。
2. Agent 工作流应默认优先看已有截图，只读取“看见有角色但名字不确定”的槽位，而不是习惯性全量扫。

## 实验结论

为了验证“缩小 OCR 区域是否真的足够快”，本次先在本地静态截图上做了基准，不触发任何实机输入。样本为用户提供的 `50dc5de3...jpg`，provider 固定为 `cpu`，先 warm 一次模型，再计时。

测得结果：

### `high + never`

- 全屏 `1920x1080`：`8472ms / 4795ms`，均值约 `6634ms`
- 小区域 `201x61`（顶栏文本）：`2719ms / 2800ms`，均值约 `2760ms`
- 小区域 `201x61`（`SLOT_NAME_REGION` 同尺寸）：`2375ms / 2442ms`，均值约 `2408ms`

### `fast + never`

- 全屏 `1920x1080`：`8282ms / 4734ms`，均值约 `6508ms`
- 小区域 `201x61`（顶栏文本）：`2889ms / 2978ms`，均值约 `2934ms`
- 小区域 `201x61`（`SLOT_NAME_REGION` 同尺寸）：`2707ms / 2655ms`，均值约 `2681ms`

实验说明了三件事：

1. OCR 耗时**不会**随着区域缩小而按面积线性下降。
2. 对 `201x61` 这种槽位名区域，`fast` 基本没有优势，甚至略慢于 `high`。
3. 当前慢的根因不是“截图太大”，而是“**调用次数太多**”。即使单次压到 `2.4s` 左右，19 次 OCR 也仍然是几十秒量级。

因此，这次设计不能只调 OCR mode；必须同时减少 OCR 调用次数。

## 目标

1. 把 `cw slots read` 的 OCR 调用次数从“每槽位一次”降到“每次命令一次”。
2. 把槽位名读取改为 scene-specific OCR 策略，不再继续沿用这个场景下收益很差的 `fast + auto retry_high`。
3. 给 OCR 结果加角色名归一化，优先纠正明显的低置信脏值，但不为此引入新的在线依赖。
4. 强化 `--slot` 的帮助文本与 Agent skill 约束，让“先看截图，再定向读取”成为默认工作流。
5. 保持现有命令形态和默认全量语义不变，避免破坏既有调用链。

## 非目标

这次不做：

- 改变 `cw slots read` 的默认输出协议
- 删除默认全量读取能力
- 新增基于图像模板的非空槽位检测器
- 重写整个 OCR 运行时或替换 OCR 后端
- 为角色名维护人工别名表或大规模手工纠错词典
- 让 `cw slots read` 在读取时主动发起新的 guide/config 网络请求

## 方案比较

### 方案 A：仅改为 scene-specific `high + never`

做法：

- 保留逐槽位即时 OCR
- 仅把 OCR mode 改为 `high + never`

优点：

- 改动最小
- 可以立即去掉当前 `fast + auto retry_high` 的无效试探

缺点：

- 仍然是 19 次 OCR
- 只能优化单次 OCR，不能解决根本调用次数问题

### 方案 B：先截图，再拼图，一次命令只做一次 OCR

做法：

- 逐槽位点击并缓存 `SLOT_NAME_REGION` 截图到内存
- 把所有截图按固定 strip 规则拼成一张图
- 对拼图统一做一次 OCR，再按 box 位置映射回槽位

优点：

- 直接砍掉绝大部分 OCR 次数
- 全量 19 槽和 `--slot` 定向读取都能复用同一条流水线

缺点：

- 需要新增 strip 拼接与 box 回映射逻辑
- 需要让 runtime 支持对内存图像做 OCR，不能只依赖“先截图再 OCR”的窗口接口

### 方案 C：先做无输入占位检测，再只点非空槽位

做法：

- 先从编队页静态画面判断哪些槽位非空
- 只点击非空槽位读取名字

优点：

- 理论速度最佳

缺点：

- 新增一套更重的检测逻辑
- 需要额外维护模板/图像规则
- 明显超出这次“先解决命令慢和名字脏值”的范围

## 结论

采用 **方案 B** 作为命令内主方案，同时叠加 scene-specific OCR 与名字归一化，并把 `--slot` 明确提升为 Agent 首选工作流。

理由：

- 实验已经证明，单次 OCR 即使缩小区域也仍然昂贵；只换 mode 不够。
- 用户明确希望“命令本身降 OCR 次数”和“Agent 先看图定向读”一起做。
- 拼图批量 OCR 可以显著改善默认全量路径，而 `--slot` 可以避免不必要的全量路径；两者并不冲突，反而互补。

## 设计细节

### 读取流水线

`build_cw_slots_reader()` 改为统一的两阶段流水线：

1. 解析目标槽位集合
2. 逐槽位执行点击与截图，但**不立刻 OCR**
3. 把所有截图拼成一张批量 OCR 图
4. 对拼图做一次 OCR
5. 按 OCR box 的纵向位置把文本映射回每个槽位
6. 对每个槽位文本做归一化后写回快照

无论是默认全量还是显式 `--slot`，都走同一条路径；区别只是目标槽位数量不同。

局部读取与 `stale` 语义需要单独冻结，避免把局部结果误当成整盘新快照：

1. **全量读取**
   - 结果覆盖整份 `front/back/hand`
   - 成功后 `stale = False`
2. **局部读取 + 旧快照 fresh**
   - 只更新 target 槽位
   - 未 target 槽位沿用旧快照
   - 成功后仍可保持 `stale = False`
3. **局部读取 + 旧快照 stale 或不存在**
   - 只更新 target 槽位
   - 未 target 槽位不得因为局部成功而被“洗白”为 fresh
   - 成功后整体 `stale` 仍保持 `True`

`SLOTS_READ_EMPTY` 也按这个矩阵解释：

1. **全量读取**：当整份读取结果没有任何名字时触发
2. **局部读取 + fresh 基线**：以“合并后的 fresh 快照”判定；允许 target 全空但整体快照仍有效
3. **局部读取 + stale/无基线**：只以本次 target 结果判定；不能让 stale 旧值替本次空结果兜底

### 槽位截图收集

每个目标槽位保留以下元信息：

- `area`
- `index`
- `point`
- 截得的 `SLOT_NAME_REGION` 图片

单槽位步骤保持为：

1. 点击槽位
2. `sleep(SLOT_PANEL_SETTLE_SECONDS)`
3. 抓取 `SLOT_NAME_REGION` 到内存
4. 点击关闭点
5. `sleep(SLOT_PANEL_SETTLE_SECONDS)`

这里仍保留现有等待节奏，不在本次顺手重构成条件等待；否则会把问题从“减少 OCR 次数”扩展成“输入时序系统改造”。

### 批量拼图格式

拼图规则固定如下：

1. 每个槽位截图保留原始尺寸
2. 纵向堆叠为 strip
3. strip 之间插入固定高度空白间隔
4. 不额外画标签文字，避免人为引入 OCR 噪声

回映射依赖于：

- 每个 strip 的起止 `y` 范围
- OCR piece 的 box 中心 `y`

把 piece 分配到其中心点落入的 strip，再归并成该槽位的 OCR 原文列表。

这里还需要冻结两个细节，否则实现与测试都无法稳定：

1. **无几何 piece 的降级规则**
   - 当 target 只有 1 个时，可把无几何 piece 直接归给该唯一目标
   - 当 target 大于 1 个时，无几何 piece 不参与槽位映射，并追加一条 warning 或内部调试事件
   - 不能因为无几何 piece 直接让整个命令崩溃
2. **同一 strip 内的稳定排序规则**
   - 先按 strip 归属分组
   - 组内按 `top`、`left`、原始顺序做稳定排序后再拼接文本
   - 如果 piece 的中心点落在 gap 或分隔边界的歧义区域，按“忽略并记录 warning/debug”处理，而不是猜测归属

### Runtime 扩展

当前 runtime 只支持“先从窗口截图，再 OCR”，而且窗口层对 capture 结果会统一归一化到 `1920x1080`。这意味着：如果直接复用现有区域截图接口，`SLOT_NAME_REGION` 这种小框拿到的并不是原始小图，而是被拉伸后的 canonical 图，无法满足“拼接原尺寸 strip”的性能前提。

因此 runtime 需要新增两类能力，而且都要走统一运行时，不让 scene 层直接依赖 `RapidOcrAdapter`：

1. **原始区域截图入口**
   - 允许 scene 层抓取保持原始像素尺寸的 region 图片
   - 不走当前固定 `1920x1080` 归一化
   - 只用于内存态批量 OCR，不改变现有对外 screenshot/shot 语义
2. **对内存图片做 OCR 的入口**
   - 复用现有 `OcrRequestConfig`
   - 复用现有 warning/debug 上下文收集语义
   - 允许 scene 层传入 `PIL.Image` 或 image bytes

这样 `cw.slots.read` 的批量 OCR 才能真正建立在“小 strip 拼图 + 一次 OCR”之上，同时不破坏现有 screenshot、warning、debug、failure envelope 的契约。

### Scene-specific OCR 策略

槽位名读取固定使用场景内 OCR 配置：

- `provider`: 继续沿用 runtime/provider 解析，不单独锁死
- `ocr_mode`: `high`
- `retry_high`: `never`

选择这个组合的原因：

1. 实验表明该尺寸下 `high` 不慢于 `fast`
2. 既然已经批量做一次 OCR，再做 `retry_high` 没有意义
3. scene 内固定策略比继续依赖全局默认更符合这个命令的稳定性需求

### 名字归一化

每个槽位的 OCR 原文不直接入状态，而是先经过归一化。

候选集按优先级分两层：

1. 当前 session 内上下文
   - 已应用 guide 的角色名
   - 现有 slots 快照里的已知角色名，但**仅当 `stale == False` 时才可作为候选**
2. 本地可得的静态或缓存候选
   - 仅允许使用当前工作区或 session 中已经存在的本地角色名快照
   - **`cw.slots.read` 本身不新增网络拉取**

这里再冻结一个边界：

- 本次版本不要求 `cw.slots.read` 主动调用 `fetch_cw_guide_config()`
- 如果未来要把 `guide.config.cw.roles` 接进候选集，前提也必须是“本地已有缓存/快照可用”，不能把本地 UI 读取命令扩成实时网络命令

匹配流程：

1. 先尝试精确匹配
2. 精确失败时，再对候选集做模糊匹配
3. 仅当最佳候选分数足够高，且与第二名有明确差距时，才自动纠正
4. 若不满足自动纠正条件，则保留原文；若原文为空则保持 `None`

这里冻结一个边界：

- 不做“强行一定要映射成某个角色名”的策略
- 宁可保留原文或 `None`，也不要把低置信垃圾值硬纠成错误角色
- `stale == True` 的旧槽位快照不能参与自动纠正候选，否则会把旧名字误纠回新读结果

### `--slot` 与帮助文案

命令本身继续保留现有 `--slot` 参数语义，但帮助文案要显式升级：

- 优先读取截图中看见有角色、但需要确认名字的槽位
- 不建议默认全量扫，除非确实需要完整快照

这次要同步三个入口：

1. `trail/commands/cw.py` 中 `cw slots read --help`
2. `README.md`
3. `skills/trail-cw-slots/SKILL.md`
4. `skills/trail-cw/SKILL.md`

其中 skill/README 要明确要求 Agent：

1. 先利用当前阶段已有 screenshot 判断哪些槽位值得读取
2. 只对“看见有角色但无法确认名字”的槽位传 `--slot`
3. 默认全量扫是兜底，不是首选

这里需要冻结一条必须在 help / README / skill 里同时出现的语义边界：

- **推荐工作流**：Agent 首选传 `--slot` 做定向确认
- **命令契约**：不传 `--slot` 时，仍保留现有全量读取语义，仅作为完整快照兜底

CLI 层面不能只改组级帮助，还必须直接补到 `cw_slots_read()` 的命令 help / `slot` 选项 `help=`，并为 `trail cw slots read --help` 单独补测试，确保用户在叶子命令帮助页也能看到这个边界。

## 测试与验证

至少覆盖以下测试面：

1. `trail/scenes/cw/slots.py`
   - fresh/stale 基线 + 全量/局部 target 的 merge 矩阵正确
   - `SLOTS_READ_EMPTY` 在全量读、局部读、fresh 基线、stale 基线下的判定符合设计
   - 批量 strip 拼接顺序正确
   - OCR box 可以稳定映射回对应槽位
   - 当 OCR piece 缺少几何信息时不会崩溃，并按设计降级处理
   - 单 target / 多 target 下无几何 piece 的行为不同且契约稳定
   - strip 边界、gap、返回顺序交错时的排序与归属稳定
   - `targets` 只会收集指定槽位
   - scene-specific OCR config 固定为 `high + never`
   - 名字归一化在高分唯一最佳时会纠正，在歧义场景下不会误纠
   - `stale == True` 的旧槽位快照不会参与候选集
2. runtime/operator
   - 新增的“原始区域截图”与“内存图片 OCR”入口复用既有 OCR 配置和 debug/warning 语义
   - 不会破坏现有 `cw.slots.read` 的 failure envelope 与 selective capture 语义
3. CLI/help
   - `cw slots read --help` 包含新的 `--slot` 指引
   - 叶子命令 help 明确同时表达“推荐定向读”和“默认全量语义保留”
4. 文档同步
   - `README.md` 示例与说明
   - `skills/trail-cw-slots/SKILL.md` 的执行规则与标准流程
   - `skills/trail-cw/SKILL.md` 中对槽位识别与通用 OCR 的上层引导

验证重点：

- 不锁死 OCR 引擎内部输出格式细节
- 优先断言批量回映射和归一化后的行为契约
- 帮助文本以关键锚点断言为主，避免对 Typer 排版过度脆弱

## 风险与边界

主要风险：

1. **strip 回映射错误**
   - 如果空白间隔过小或 OCR box 跨 strip，可能把文本分错槽位
   - 通过固定间隔、中心点归属和单测覆盖来约束
2. **模糊匹配误纠**
   - 若阈值过低，可能把陌生脏值纠成错误角色
   - 通过“最佳分数阈值 + 与第二名差距阈值”双门槛控制
3. **文档与 Agent 行为不同步**
   - 如果只改命令不改 skill，Agent 仍可能继续默认全量扫
   - 因此 README、`skills/trail-cw-slots/SKILL.md` 与 `skills/trail-cw/SKILL.md` 都是这次的必改项

## 后续演进

如果这版落地后默认全量路径仍然偏慢，再进入下一阶段：

- 加入“无输入占位检测”
- 在点击前先筛掉明显空槽位

但这属于下一轮优化，不放进本次实现范围。
