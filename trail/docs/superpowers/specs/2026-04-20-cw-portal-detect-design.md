# `cw portal detect` 设计说明

## 背景

当前 `trail cw start` 与 `trail cw portal refresh` 都会把界面推进或停留在投资环境页，并输出三张投资环境卡片及其 guide 摘要。

但 `cw start` 的链路较长，实际运行中可能已经抵达投资环境页，却在后续步骤里失败，导致当前 session 没有留下可供 `cw portal select` 直接消费的 portal 快照。此时用户已经手动回到或停留在投资环境页，仍然缺一个“只重新识别当前三张卡”的稳定入口。

## 目标

新增 `trail cw portal detect --session <id>`：

1. 仅在当前已经位于投资环境页时运行。
2. 重新识别当前三张投资环境卡。
3. 继续补挂与 `cw.start` / `cw.portal.refresh` 一致的 guide 摘要。
4. 把结果写回 `session.scene_state["cw"]["portal"]`，以便后续直接执行 `trail cw portal select`。
5. 保持现有文本协议、renderer 家族与帮助文档边界稳定。

## 非目标

1. 不负责把页面自动推进到投资环境页。
2. 不并入 `cw stage` 家族，也不扩大 `stage` 的职责边界。
3. 不新增 `mode` / `difficulty` / `battle_mode` 参数。
4. 不改变 `cw.portal.refresh` / `cw.portal.restart` / `cw.start` 的外部语义。

## 备选方案

### 方案 A：新增独立 `cw.portal.detect`（采纳）

- 新命令只负责“当前投资环境页的重新识别与快照回写”。
- CLI 与 daemon method 使用 canonical command `cw.portal.detect`。
- 输出复用现有 portal cards renderer，与 `cw.start` / `cw.portal.refresh` 保持同形。

优点：命令职责单一、命名直观、最符合现有 `portal` 家族边界，且对现有命令影响最小。

缺点：命令面上会新增一个叶子命令，需要同步 README、skills 与测试。

### 方案 B：并入 `cw.stage.detect`

- 让 `cw.stage.detect` 同时承担局内阶段与开局投资环境页识别。

优点：少一个命令。

缺点：直接破坏现有“`stage` 只用于已进入 CW 后的局内阶段检测”的文档与 skill 约束，也会让 renderer 语义混杂。

### 方案 C：给现有命令加 detect-only 开关

- 例如给 `cw.portal.refresh` 增加“只识别不点击”的参数。

优点：表面上不增加命令数量。

缺点：会让既有命令语义变宽，帮助文本与失败路径更难稳定，也不如独立命令直观。

## 方案设计

### 1. CLI 与命令归属

- 在 `trail/commands/cw.py` 的 `portal_app` 下新增 `detect` 子命令。
- 命令形态固定为：`trail cw portal detect --session <id>`。
- 该命令归入 `portal` 家族，帮助文本明确为“当前已在投资环境页时重新识别三张卡”。

### 2. daemon 路由与调用路径

- 在 `trail/daemon/cw_service.py` 的 handler 映射中新增 `cw.portal.detect`。
- 该命令不产生 UI side effect，因此不进入 `CW_MUTATING_METHODS`。
- 它沿用 `handle_with_capture` 路径：
  1. 加载 session 与 runtime
  2. 执行识别逻辑
  3. 保存 session
  4. 产出截图与标准 envelope

这保证它既能回写 session，又不会被标记成会点击/按键的 mutation。

### 3. portal 层共享逻辑

为避免 `detect` 与 `refresh` 再次分叉，portal 层应抽出“从当前 invest 页面构造 portal 快照”的共享 helper。

建议职责如下：

1. 校验当前页面必须是 `invest`。
2. 读取 OCR 结果。
3. 调用 `summarize_portal_cards(...)` 识别三张卡。
4. 识别 `portal.collection` 标记并标注 `new=1`。
5. 组合当前 portal 快照：
   - `cards`
   - 从现有 entry / portal 里继承的 `mode` / `difficulty` / `battle_mode`
   - `stale=False`
6. 写回 `session.scene_state["cw"]["portal"]`。
7. 把 `session.scene_state["cw"]["entry"]["page"]` 更新为 `invest`。

然后：

- `cw.portal.detect` 直接调用这条共享路径。
- `cw.portal.refresh` 在完成点击并等待回到 invest 页面后，也调用这条共享路径。
- `cw.start` 仍保留现有开局推进链，但尽量继续共用卡片/guide 组装逻辑，避免字段定义漂移。

### 4. guide 摘要附加

`cw.portal.detect` 的输出需要和 `cw.start` / `cw.portal.refresh` 保持一致，因此识别完卡片后，继续通过现有 guide 查询逻辑为每张卡补挂最多三条 guide 摘要。

结果要求：

1. 首行仍然是 `ok cw.portal.detect cards=<n>`。
2. 有截图时必须输出 `shot path=...`。
3. 卡片与 guide 行继续使用现有 `opt` / `guide` 前缀与字段顺序。

### 5. session 真值处理

- `cw.portal.detect` 不接收 `mode` / `difficulty` / `battle_mode` 参数。
- 如果 session 中已有这些值，则在 portal 快照里原样保留。
- 如果 session 中没有这些值，则保持为空，不把“补录开局真值”变成 detect 的职责。

这意味着：

1. `cw.portal.select` 可以依赖 detect 刷新的 `portal.cards` 正常工作。
2. `cw.portal.restart` 仍然只在已有开局真值时可用；其现有前提不因 detect 被放宽。

## 数据流

`trail cw portal detect --session <id>`

1. CLI 调用 daemon method `cw.portal.detect`
2. daemon 加载 session 与 runtime
3. portal helper 校验当前页面为 `invest`
4. OCR 识别三张卡并补挂 guide 摘要
5. 写回 `cw.portal` 快照，更新 `cw.entry.page=invest`
6. renderer 输出与 `cw.portal.refresh` 同形的文本结果

## 失败与恢复语义

1. 如果当前不在投资环境页，返回与 portal 家族一致的 page invalid 错误，不自动跳转。
2. 如果 invest 页识别本身失败（例如页面校验失败、OCR 采样异常），沿用现有错误封装方式；只要结果携带截图，文本协议继续输出 `shot path=...`。
3. guide 摘要附加继续沿用当前容错策略：guide 查询失败时不把整条 detect 判失败，而是退化为“只有卡片、没有 guide 摘要”的成功结果。
4. 因为该命令没有 UI side effect，不需要 mutation unknown 的特殊恢复语义。
5. 如果 daemon 请求本身带 `request_id` 且失败路径满足现有恢复条件，继续遵守统一 failure renderer 顺序。

## 测试计划

至少补齐以下测试：

1. `tests/test_cw_portal.py`
   - invest 页下 `cw.portal.detect` 能写回 portal 快照。
   - 识别结果会保留已有 `mode` / `difficulty` / `battle_mode`。
   - 非 invest 页会报 `CW_PORTAL_PAGE_INVALID`。
2. `tests/test_cw_rpc_contracts.py`
   - `trail cw portal detect --session ...` 正确调用 `cw.portal.detect`。
   - stdout 与 `cw.portal.refresh` 保持同形，包括 `ok ... cards=...`、`shot`、`opt`、`guide` 行。
3. `tests/test_output_rendering.py`
   - `TEXT_RENDERERS` 注册 `cw.portal.detect`。
   - README/skills 对命令边界与恢复用法的文案断言同步更新。

## 文档更新

需要同步更新：

1. `README.md`
   - 在 CW 命令总览与示例中加入 `trail cw portal detect`。
   - 明确它的使用场景：已经在投资环境页，但需要重新识别并缓存三张卡。
2. `skills/trail-cw/SKILL.md`
   - 加入 `cw portal detect` 的恢复用法，避免 agent 在投资环境页误回退到 `cw start`。
3. 如有与 `portal` 边界直接相关的帮助或文档断言，也一并同步。

## 验收标准

满足以下条件即可视为完成：

1. 用户在手动进入投资环境页后，可以直接运行 `trail cw portal detect --session <id>`。
2. 命令输出的卡片与 guide 摘要格式和 `cw.start` / `cw.portal.refresh` 一致。
3. 命令会把 portal 快照写回 session，随后可直接执行 `trail cw portal select`。
4. `stage` 家族职责边界、默认文本协议与失败恢复语义均未被破坏。
5. README、相关 skills 与测试同步更新。
