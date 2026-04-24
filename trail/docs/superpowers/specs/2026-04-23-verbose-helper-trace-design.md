# `verbose helper trace` 设计说明

## 背景

当前项目的 `--verbose` 调试层已经统一走 `trail.output.debug.collect_debug_events` 与 `trail.output.debug.render_debug_lines`，但真正进入输出层的调试信息仍然偏少。

现状主要有两类问题：

1. `trail.runtime.operator.RuntimeOperator` 虽然已经零散记录了一些 `trace`，例如 `locate`、`ocr`、`click_point`、`capture_after_action`，但 major action 的埋点覆盖并不稳定，也没有统一的时间戳语义。
2. `trail.output.capture` 目前只会把 `trace` 和 OCR 少量 allowlist context 提升到 verbose 输出，这使得 `--verbose` 很容易退化成“只有少量 OCR 模式信息”，而不是“完整的 helper 执行过程”。

用户希望把改造范围限制在 runtime / output 这类通用层，不触及具体命令实现；同时要求 major action 在 verbose 模式下固定输出执行信息和时间戳，失败尝试也不能丢。

## 目标

本次改造目标：

1. 让 `--verbose` 固定输出 major action 的执行信息，而不是只在少数链路下偶发出现。
2. 统一 major action 的时间戳语义，使用绝对时间戳。
3. 失败尝试也保留动作 trace，例如输入后校验失败、可选截图失败、OCR backend 抛错等。
4. 保持改造范围停留在通用 helper / output 层，不修改具体命令文件。
5. 保持默认文本协议不变；变化仅发生在 `--verbose` 追加层。

## 非目标

1. 不调整任何具体命令的业务逻辑、参数或成功/失败语义。
2. 不改默认文本模式的首行事实、正文顺序或 renderer 家族。
3. 不把所有 scene 内部的自定义 trace 全量重写成同一模型；本次只保证复用 helper 稳定产出统一动作 trace。
4. 不把 `--verbose` 扩展成 YAML 或其他结构化主通道。

## 备选方案

### 方案 A：仅放开 capture 侧过滤

- 修改 `trail.output.capture`，不再限制 OCR allowlist，尽量把现有 runtime trace/context 全部带到 verbose。

优点：改动小，回归风险最低。

缺点：治标不治本。当前问题不只在 capture 过滤，还在于 helper 自身没有稳定、统一地记录 major action，因此仍然无法保证 `click`、`drag`、`screenshot`、`ocr` 等动作固定出现在 verbose 里。

### 方案 B：继续在 `RuntimeOperator` 内部堆叠统一埋点

- 在 `RuntimeOperator` 中增加更多 `_record_trace(...)` 规则，并补齐时间戳与失败路径。

优点：实现直接，改动集中在现有 runtime 文件。

缺点：随着 helper 增多，埋点规则会继续散落在 `RuntimeOperator` 各个方法里；capture 侧特例逻辑也仍然容易继续增长，不利于后续维护。

### 方案 C：引入独立 verbose 动作记录器（采纳）

- 新增独立 recorder 模块，专门负责 major action 事件模型、时间戳、成功/失败收口与请求级缓冲。
- `RuntimeOperator` 只负责在复用 helper 中调用 recorder。
- `trail.output.capture` 统一消费 recorder 快照，不再保留 OCR 特例 allowlist。

优点：职责边界最清晰，后续新增 helper 时只需要复用 recorder 约定；也最符合“不要碰具体命令，只改通用层”的范围要求。

缺点：这次改造面比最小补丁更大，需要同步更新 runtime、capture、README 与测试。

## 方案设计

### 1. 新增独立 recorder 模块

在 runtime/common 层新增一个独立的 verbose 动作记录器模块，建议职责如下：

1. 提供统一的 trace 事件构造接口。
2. 为事件写入绝对时间戳。
3. 在动作结束时统一收口成功/失败状态与耗时。
4. 支持 request scope 与非 request scope 两种缓冲模式。
5. recorder 作为 `RuntimeOperator` 的内部实现细节存在，对 capture 层继续只暴露现有 `consume_debug_trace()` / `consume_debug_context()` 协议，不新增第二套外部消费 API。
6. `consume_debug_trace()` / `consume_debug_context()` 必须继续保持 one-shot consume + clear 语义，避免破坏现有 capture 与测试约定。
7. recorder 全链路必须是 best-effort：记录、收口、消费任一步失败都只能降级为“丢失本条 debug/trace”，不得改变原始命令的 success/failure/recover 语义。

这样可以把“动作埋点长什么样”“失败尝试如何落 trace”“哪些字段是通用字段”从 `RuntimeOperator` 中抽离出来，避免继续散落在单个 helper 内。

### 2. `RuntimeOperator` 接入 recorder

`RuntimeOperator` 继续作为复用 helper 的承载者，但 major action 的 verbose 记录改为统一委托给 recorder。

本次纳入统一记录范围的 helper 包括：

1. `screenshot`
2. `capture_after_action`
3. `locate`
4. `wait_img`
5. `ocr`
6. `ocr_image`
7. `click_point`
8. `drag_to`
9. `press_key`
10. `hotkey`
11. `type_text`
12. `prepare_input`
13. 输入前后台检查与输入后前台校验

要求：

1. 每次 helper 被调用时，都至少产出一条统一 trace 事件。
2. 失败尝试不能丢；即使最终抛错，也要留下该 major action 的动作记录。
3. recorder 不能变成独立的第二套状态机；它必须挂在 `RuntimeOperator` 现有 warning/trace/debug context 生命周期之后，继续由 `RuntimeOperator` 对外提供消费接口。
4. `RuntimeOperator` 现有 warning/trace/debug context 的消费接口继续保留，避免影响上层 capture 代码与已有调用点。

scope 生命周期要求：

1. 观察型路径继续由 `with_auto_capture(...)` / `with_selective_capture(...)` 驱动 `begin_capture_scope()` / `end_capture_scope()`。
2. 变异型路径不能只依赖 capture 末尾再开 scope；对于 `trail/daemon/cw_service.py` 这类“先执行动作、再截图”的 shared mutation wrapper，必须在 `handlers[method]()` 之前开启 request scope，在最终截图、debug 收集与 unknown-result 兜底完成后再结束 scope。
3. 这意味着本次允许的 shared-layer 对接文件除 `trail/runtime/**`、`trail/output/**` 外，还包括 `trail/daemon/cw_service.py`；但仍然不允许把 recorder 逻辑扩散到具体 command/scene。

### 3. 统一事件模型

本次不新增新的正文前缀，也不改变 `debug kind=trace ...` 这一既有 verbose 呈现形式，而是在 trace payload 内统一字段。

对 recorder 产出的 major action，固定字段如下：

1. `step`：动作名，例如 `click_point`、`drag_to`、`ocr`、`capture_after_action`
2. `ts`：绝对时间戳，固定使用 UTC RFC3339 / ISO 8601 毫秒格式，例如 `2026-04-24T08:15:30.123Z`
3. `ok`：布尔结果，统一编码为 `0/1`

按需追加的统一字段如下：

1. `dur_ms`：动作级耗时，避免与 envelope 顶层 `timing.elapsed_ms` 混淆
2. `error_code`
3. `error_type`
4. `msg`

动作特定字段示例：

1. `click_point`
   - `point`
   - `screen_point`
2. `drag_to`
   - `from_point`
   - `to_point`
   - `duration`
3. `screenshot`
   - `capture`
   - `source=raw`
4. `capture_after_action`
   - `optional`
   - `screenshot`
5. `locate` / `wait_img`
   - `template`
   - `found`
   - `box`，统一编码为 `left,top,width,height`
   - `attempts`
6. `ocr` / `ocr_image`
   - `capture`
   - `pieces`
   - `mode_requested`
   - `mode_effective`
   - `retry_high`
   - `retry_reason`

说明：

1. `trace` 只承载“一次 helper 调用结束后的 finalized major action 事件”；不能再把动作结果拆到零散 `context` 中补洞。
2. 成功事件不强行带空字段。
3. 失败尝试仍然使用同一个动作事件模型，不再依赖零散的 `detail/context` 去补洞。
4. `context` 只用于跨动作的请求级派生事实；若某个事实属于某次 helper 的执行结果，就必须落在该 helper 的 `trace` 事件上。
5. 现有 OCR 的 `ocr_mode_requested`、`ocr_mode_effective`、`ocr_scale_applied`、`ocr_retry_high`、`ocr_retry_reason` 不再作为 top-level context 特例输出，而是迁入 `ocr` / `ocr_image` finalized trace；现有 `ocr_provider` trace 继续兼容保留。
6. 旧 trace / context payload 即使没有 `ts` / `ok`，也必须继续按现有 key/value 规则渲染，不能因为 recorder 新模型而被丢弃。

目标 verbose 形状示例：

1. `debug kind=trace step=click_point ts=2026-04-24T08:15:30.123Z ok=1 dur_ms=12 point="[150, 892]" screen_point="[150, 892]"`
2. `debug kind=trace step=ocr ts=2026-04-24T08:15:31.004Z ok=0 dur_ms=187 pieces=0 mode_requested=fast mode_effective=high retry_high=1 retry_reason=low_confidence error_code=OCR_BACKEND_UNAVAILABLE msg="ocr backend unavailable"`
3. `debug kind=trace step=ocr_provider requested_provider=dml effective_provider=dml ...` 这类现有 trace 继续兼容；若在本次改造中触及，优先补齐同样的 `ts` / `ok` 语义。

### 4. capture 链路改造

`trail.output.capture` 目前通过 `_OCR_DEBUG_CONTEXT_ALLOWLIST` 提升 OCR 少量字段，这会让 verbose 数据模型继续耦合在单一场景上。

本次改造后：

1. 去掉 `_OCR_DEBUG_CONTEXT_ALLOWLIST` 这种 OCR 特例过滤。
2. capture 不直接依赖 recorder 新对象，而是继续只通过 `runtime.consume_debug_trace()` / `runtime.consume_debug_context()` 获取快照；recorder 的存在只体现在 runtime 内部实现。
3. `with_auto_capture(...)` 与 `with_selective_capture(...)` 的行为保持一致：
   - verbose 关闭时，不泄漏调试层
   - verbose 打开时，统一保留 recorder 产出的 major action trace
4. capture 仍然只负责“取快照并塞进 envelope”，不负责定义某个动作的字段 shape，也不做第二次事件归一化。
5. envelope 的 `debug` shape 继续保持当前契约：`request_id`、`trace`、`detail` 与其余 top-level context；capture 只做现有 shape 的装配，不新造一层事件模型。
6. 对 recorder 的消费仍需保留 safe-collect 兜底；即使 recorder 或其消费链路异常，也只能丢失 debug，不得覆盖原始命令结果。

这样可以保证 verbose 的职责边界清晰：

1. recorder 定义动作事件
2. capture 收集事件
3. debug renderer 输出事件

### 5. debug 渲染保持单入口

根据当前项目约束，`--verbose` 事件必须继续统一经过：

1. `trail.output.debug.collect_debug_events`
2. `trail.output.debug.render_debug_lines`

因此本次不改 verbose 的最终出口，只扩展它们对统一 trace shape 的覆盖。

结果上：

1. 默认文本协议事实集合和顺序保持不变。
2. `--verbose` 仍然只是在最后追加 `debug kind=...` 行。
3. `collect_debug_events(...)` 仍然是 debug 归一化的唯一入口；如果需要把原始 `box` dict 压成 `left,top,width,height` 这样的 canonical 形式，只能在这里完成。
4. `render_debug_lines(...)` 仍然只负责把归一化事件编码为文本，不承担业务语义判断。
5. major action 会固定带 `ts` 和执行信息，不再依赖少量 OCR context 或零散 trace 才能看见过程。

### 6. 兼容性与边界

为了把范围限制在通用层，本次兼容策略如下：

1. 允许改动范围固定为：`trail/runtime/**`、`trail/output/**`，以及为 shared mutation scope 对接所必需的 `trail/daemon/cw_service.py`。
2. 明确禁止改动范围：`trail/commands/**`、`trail/scenes/**`、命令专属 renderer 分支，以及为 verbose 单独新增 command-specific trace shape。
3. 不要求 scene 层已有自定义 trace 立刻全部改写。
4. 对已有 trace payload，debug renderer 继续按当前 key/value 规则渲染，不因为 recorder 新模型而拒绝旧事件。
5. 新 recorder 主要保证复用 helper 固定产出统一 major action trace；现有命令获得更完整 verbose 的方式，必须来自 shared helper 接入，而不是命令层补洞。

## 数据流

观察型路径：

1. `with_auto_capture(...)` / `with_selective_capture(...)` 开启 capture scope
2. 命令或 daemon 调用 runtime helper
3. `RuntimeOperator` 进入复用 helper，并通过 recorder 记录 finalized action event
4. helper 成功或失败时，recorder 把事件写入当前 request scope 缓冲
5. capture 通过现有 `consume_debug_trace()` / `consume_debug_context()` 消费快照并清空缓冲
6. `trail.output.debug.render_debug_lines` 把 trace 渲染为 `debug kind=trace ...`

变异型 shared 路径：

1. `trail/daemon/cw_service.py` 在 `handlers[method]()` 之前开启 request scope
2. mutation handler 运行期间调用 runtime helper，所有 major action 都进入同一 recorder 缓冲
3. session 保存、截图与 debug 收集在同一 scope 内完成
4. 只有在截图、debug 收集和 unknown-result 兜底都结束后，shared wrapper 才结束 scope
5. 这样可以保证 mutation 路径也能拿到“动作先发生、截图后发生”的完整 trace

## 失败与恢复语义

1. 本次不改变默认 failure 文本协议顺序。
2. recorder 只增强 `--verbose` 的可观察性，不负责改变 recover 语义。
3. recorder、trace 收口、trace/context 消费必须全部按 best-effort 处理；它们的异常不能覆盖原始 `TrailError`、success payload 或 mutation unknown-result 语义。
4. 对可选截图失败、输入后校验失败、OCR backend 抛错等情况，verbose 仍然固定保留 major action trace，便于排障。
5. 若上层 envelope 已包含 `request` / `why` / `recover`，仍然遵守现有 failure renderer 顺序；debug 只追加在最后。

## 测试计划

至少补齐以下测试：

1. recorder 专属测试（可新增独立测试文件）
   - request scope 并发隔离
   - nested scope / begin-end 配对
   - `consume_*` 后清空
   - verbose 关闭时不泄漏
   - recorder 或 collect 失败时只丢 debug，不改主流程 success/failure 语义
2. `tests/test_runtime_backends.py`
   - `screenshot`、`capture_after_action`、`locate`、`wait_img`、`ocr`、`ocr_image`、`click_point`、`drag_to`、`press_key`、`hotkey`、`type_text`、前后台检查等 helper 的成功路径都能产出 finalized trace
   - 可选截图失败、OCR backend 抛错、输入后前台校验失败等失败路径也能留下 finalized trace
   - 所有新模型 finalized major action trace 在成功/失败路径都必须断言 `ok=1/0`
   - `ts` 使用冻结的 UTC RFC3339 毫秒格式，`dur_ms` 为动作级耗时；runtime 原始 `box` 只要求保持可被 debug 归一化入口压成 canonical `left,top,width,height` 的 shape，不在 runtime 层提前字符串化
3. `tests/test_output_envelope.py`
   - `with_auto_capture` / `with_selective_capture` 不再依赖 OCR allowlist
   - OCR 相关事实迁入 `trace` 后，envelope `debug` shape 仍保持当前契约
   - mutation 路径中“动作先发生、截图后发生”的 shared scope 也能收集到 trace
   - safe collect 失败只丢 debug，不污染主结果
4. `tests/test_output_debug.py`
   - `collect_debug_events` 与 `render_debug_lines` 能稳定渲染统一 trace shape
   - 所有新模型 finalized major action trace 在归一化/渲染后都必须断言 `ok=1/0`
   - legacy trace/context 即使缺少 `ts` / `ok` 也继续兼容输出
   - `box` canonical 化只发生在 debug 归一化入口
5. `tests/test_output_rendering.py`
   - README / AGENTS 中关于 verbose 的新增约束有对应断言
   - OCR verbose 事实从 context 迁到 trace 后，stdout 契约按新示例固定
    - `--verbose` 关闭时仍然不能泄漏 debug 行
6. `tests/test_daemon_protocol.py`
   - 现有命令路径通过 shared helper 即可得到更完整 verbose trace
   - mutation 路径无需改 command/scene 也能透传 major action trace

## 文档更新

需要同步更新：

1. `README.md`
   - 明确 `--verbose` 的职责仍然是开发/排障层
   - 增加 major action trace 的示例
   - 补充“helper 固定输出执行信息 + 绝对时间戳”的说明
2. `AGENTS.md`
   - 在 verbose 约束中补充：major action trace 固定输出、绝对时间戳格式、`trace/context` 边界、recorder best-effort 原则
3. `skills/trail-hsr-advanced/SKILL.md` 与 `skills/trail-hsr-advanced/references/advanced-command-surface.md`
   - 如果 advanced skill 仍承担高级排障命令面的说明，则同步补充 verbose major action trace 的读取方式
   - 若实现阶段确认 `SKILL.md` 或 reference 文档无需改动，必须在变更说明里明确“已检查、无需更新”的理由，不能跳过检查
4. 相关 renderer/debug/skill 文档断言
   - 保证项目协议断言与 README 示例一致

## 验收标准

满足以下条件即可视为完成：

1. `--verbose` 下，复用 helper 的 major action 能稳定输出，不再只在少数场景出现零散 trace。
2. 这些 major action 固定带 UTC RFC3339 毫秒格式的绝对时间戳。
3. 失败尝试也会留下对应动作记录。
4. 默认文本模式完全不变。
5. recorder 自身故障不会改变原始命令 success/failure/recover 语义。
6. 观察型与变异型 shared 路径都能通过通用层拿到完整 trace，而不需要改具体命令文件。
7. README、`AGENTS.md`、相关 advanced skill/reference 文档与测试同步更新。
