> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# `cw portal detect` 设计说明

## 背景

当前 `trail cw start` 与 `trail cw portal refresh` 都会把界面推进或停留在投资环境页，并输出三张投资环境卡片及其 guide 摘要。

但 `cw start` 链路较长，实际运行中可能已经抵达投资环境页，却在后续步骤里失败，导致当前 session 没有留下可供 `cw portal select` 直接消费的 fresh portal snapshot。此时用户已经手动停留在投资环境页，仍然缺一个“只重建当前三张卡识别结果”的稳定入口。

本设计新增 `trail cw portal detect --session <id>`，专门覆盖这个恢复场景。

## 目标

新增 `trail cw portal detect --session <id>`，满足以下目标：

1. 仅在当前已经位于投资环境页时运行。
2. 重新识别当前三张投资环境卡。
3. 继续补挂与 `cw.start` / `cw.portal.refresh` 一致的 guide 摘要。
4. 把结果写回 `session.scene_state["cw"]["portal"]`，以便后续直接执行 `trail cw portal select`。
5. 保持 `portal` 家族职责、默认文本协议、README 与 skills 边界稳定。

## 非目标

1. 不负责把页面自动推进到投资环境页。
2. 不并入 `cw stage` 家族，也不扩大 `stage` 的职责边界。
3. 不新增 `mode` / `difficulty` / `battle_mode` 参数。
4. 不放宽 `cw.portal.restart` 对已有开局真值的前提要求。
5. 不改变 `cw.start` / `cw.portal.refresh` / `cw.portal.restart` / `cw.portal.select` 的既有外部语义。

## 备选方案

### 方案 A：新增独立 `cw.portal.detect`（采纳）

- 新命令只负责“当前投资环境页的重新识别与快照回写”。
- CLI 与 daemon method 使用 canonical command `cw.portal.detect`。
- 输出复用现有 portal cards renderer，与 `cw.start` / `cw.portal.refresh` 保持同形。

优点：命令职责单一、命名直观、最符合现有 `portal` 家族边界，且对现有命令影响最小。

缺点：命令面会新增一个叶子命令，需要同步 README、skills、help 断言与测试。

### 方案 B：并入 `cw.stage.detect`

- 让 `cw.stage.detect` 同时承担局内阶段与开局投资环境页识别。

优点：少一个命令。

缺点：会破坏现有“`stage` 只用于已进入 CW 后的局内阶段检测/等待”的文档与 skill 约束，也会让 renderer 家族混杂。

### 方案 C：给现有 `cw.portal.refresh` 增加 detect-only 开关

- 例如给 `refresh` 增加“只识别不点击”的参数。

优点：表面上不增加命令数量。

缺点：会让既有命令语义变宽，帮助文本与失败路径更难稳定，也不如独立命令直观。

## 方案设计

### 1. CLI、帮助文本与命令边界

- 在 `trail/commands/cw.py` 的 `portal_app` 下新增 `detect` 子命令。
- 命令形态固定为：`trail cw portal detect --session <id>`。
- 内部 canonical command 固定为 `cw.portal.detect`。
- `portal` 分组帮助文本必须把 `detect` 与 `refresh` 明确区分：
  - `detect`：当前已在投资环境页时，重新识别并保存当前三张卡，不点击、不刷新、不重开。
  - `refresh`：点击刷新并等待页面稳定后，重新生成三张卡。
- `cw` 总览、`cw portal --help`、README、`skills/trail-cw/SKILL.md` 都必须出现这个边界说明，避免 agent 在“已经手动进到投资环境页”时误回退到 `cw start`，或把 `cw.portal.refresh` 误当成无副作用识别。
- 由于仓库里已存在 `cw.stage.detect`，`cw.portal.detect` 的叶子帮助文案也应明确写出“仅在当前已位于投资环境页时重新识别并保存 portal snapshot”，降低命名相似带来的误用风险。

### 2. daemon 路由与 capture 路径

- 在 `trail/daemon/cw_service.py` 的 handler 映射中新增 `cw.portal.detect`。
- `cw.portal.detect` 不进入 `CW_MUTATING_METHODS`，因为它本身不产生 UI side effect。
- 但它不能走普通的 `_run_cw()` 路径；必须和 `cw.slots.read` 一样，显式进入 `CommandService._run_cw_with_capture()`。
- 因此实现时需要同时修改 `trail/daemon/command_service.py`，至少满足以下任一形式：
  1. 为 `cw.portal.detect` 增加显式 capture 分支。
  2. 或抽出独立的 `CW_CAPTURE_METHODS` 集合，把 `cw.slots.read` 与 `cw.portal.detect` 一起纳入 `_run_cw_with_capture()`。

这样可以保证：

1. 成功路径会带 request-scoped `screenshot`，从而稳定输出 `shot path=...`。
2. 失败路径会沿用 selective capture 的封装，而不是退回普通 `success(self._run_cw(...))`。
3. 命令仍然是非 mutating，不会引入 mutation unknown 的 `tainted=1` / `recover action=daemon.request_status ...` 语义。

### 3. portal 层共享 helper 的边界

为避免 `detect` 与 `refresh` 再次分叉，portal 层应抽出“在稳定 invest 页面上生成 raw portal snapshot”的共享 helper。命名可在实现时确定，例如 `_snapshot_cw_portal_from_invest_page(...)`，但职责边界必须固定。

这个 helper 只负责：

1. 校验当前页面必须是 `invest`。
2. 读取 OCR 结果。
3. 调用 `summarize_portal_cards(...)` 识别三张卡。
4. 调用 `detect_portal_collection_matches(runtime)` 标记 `new=1`。
5. 通过与当前 `_portal_entry_truth(session)` 一致的优先级计算真值：
   - `entry.field if entry.field is not None else portal.field`
6. 组合 raw snapshot：
   - `cards`
   - `mode`
   - `difficulty`
   - `battle_mode`
   - `stale=False`
7. 写回 `session.scene_state["cw"]["portal"]`。
8. 把 `session.scene_state["cw"]["entry"]` 写回为完整对象，而不是只改 `page`：
   - `{"page": "invest", "mode": ..., "difficulty": ..., "battle_mode": ...}`

这个 helper 不负责：

1. 点击刷新按钮。
2. 等待页面从刷新态重新稳定。
3. 拉取或附加 guide 摘要。

前置条件也必须写死：调用方必须已经拿到“稳定的 invest 页面”。这点对 `refresh` 尤其重要，避免实现者把 helper 过早复用到“页面看起来还在 invest，但其实尚未完成刷新 settle”的时刻。

### 4. `detect`、`refresh`、`start` 的职责拆分

- `cw.portal.detect`：直接调用共享 helper，得到 raw snapshot。
- `cw.portal.refresh`：继续保留现有“点击刷新 -> 至少过一轮 settle -> 等待回到 invest 页面稳定”逻辑；只有在页面稳定后，才调用共享 helper。该命令现有等待语义不能因为抽 helper 而变短。
- `cw.start`：继续保留现有从首页推进到投资环境页的入口链与 entry-truth 记录语义，不强制改造成和 `detect` 共用同一个 scene helper。

本轮共享的重点是：

1. `detect` 与 `refresh` 共用 scene 层 raw snapshot 生成逻辑。
2. `start` / `refresh` / `detect` 继续共用 daemon 层的 guide enrich 逻辑，避免字段定义漂移。

### 5. guide 摘要附加与 session 持久化

guide 查询继续保留在 daemon 层，而不是下沉到 `trail/scenes/cw/portal.py`。

推荐分层如下：

1. scene helper 只返回 raw snapshot，并先把 raw snapshot 写回 `session.scene_state["cw"]["portal"]`。
2. daemon 层复用现有 `_attach_guides_to_portal_snapshot()` / `_attach_guides_to_cards()` 逻辑，对 raw snapshot 做 best-effort enrich。
3. enrich 完成后，再把 enriched snapshot 回写到 `session.scene_state["cw"]["portal"]`，并作为最终响应数据返回。

这样可以保持与当前 `cw.portal.refresh` / `cw.start` 相同的分层：

- scene 负责页面内识别与状态采样。
- daemon 负责 guide 查询、容错与最终响应组装。

guide 查询失败时的语义也保持不变：

1. `detect` / `refresh` / `start` 仍然成功。
2. 返回值退化为“只有卡片、没有 guide 摘要”。
3. session 中至少保留 raw snapshot，不得因为 guide 查询失败而把整个命令判成失败。

### 6. session 真值与下游命令语义

- `cw.portal.detect` 不接收 `mode` / `difficulty` / `battle_mode` 参数。
- 若 session 里已有这些值，则 detect 生成的 snapshot 与 `entry` 都原样保留。
- 若 session 中没有这些值，则保持为空，不把“补录开局真值”变成 detect 的职责。

这意味着：

1. `cw.portal.select` 可以依赖 detect 刷新的 `portal.cards + stale=False` 正常工作。
2. `cw.portal.restart` 仍然只在已有开局真值时可用；detect 不承诺解锁 restart。
3. 文档与测试都必须明确这一点，避免用户误解为“detect 后 restart 也一定可用”。

## 数据流

`trail cw portal detect --session <id>`

1. CLI 调用 daemon method `cw.portal.detect`
2. `CommandService.handle()` 显式把该 method 路由到 `_run_cw_with_capture()`
3. `CwService.handle_with_capture()` 加载 session 与 runtime，并通过 selective capture 执行 handler
4. handler 在稳定 invest 页面上调用共享 scene helper，生成 raw snapshot 并回写 session
5. daemon 层对 raw snapshot 做 guide best-effort enrich，并把 enriched snapshot 再次写回 session
6. renderer 复用 `_render_cw_portal_cards`，输出与 `cw.portal.refresh` 同形的文本结果

## 输出协议与失败语义

`cw.portal.detect` 归入现有 portal cards renderer 家族，不新增新的 renderer 家族。

默认文本协议必须满足：

1. 成功首行固定为 `ok cw.portal.detect cards=<n>`。
2. 只要当前结果有截图，就必须输出 `shot path=...`。
3. 卡片与 guide 行继续使用现有 `opt` / `guide` 前缀与字段顺序。
4. `--format yaml` 不进入 allowlist；detect 必须和 `cw.portal.refresh` 一样返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。

失败路径必须满足：

1. 页面不在投资环境页时，返回 `CW_PORTAL_PAGE_INVALID`，不做隐式跳转。
2. OCR/页面采样类的可预期失败，应在 scene/helper 内归一为 `TrailError`，避免直接退化成通用 `DAEMON_UNAVAILABLE`。
3. guide 查询失败继续按成功退化处理，不把整条 detect 判失败。
4. 因为 detect 不是 mutation：
   - 不应输出 `tainted=1`
   - 不应输出 `recover action=daemon.request_status ...`
   - 若响应本身携带 `request_id`，仍可保留 `request id=<id>`
5. 一旦进入 failure renderer，正文顺序仍必须遵守统一顺序：`request -> shot -> why -> warn -> ref -> recover`。

## 测试计划

至少补齐以下测试：

### 1. `tests/test_cw_portal.py`

- invest 页下 `cw.portal.detect` 能写回 raw portal snapshot。
- detect 结果会保留已有 `mode` / `difficulty` / `battle_mode`。
- detect 会把 `cw.entry` 写成完整的 `{"page": "invest", ...}` 对象，而不是只改 `page`。
- 非 invest 页会报 `CW_PORTAL_PAGE_INVALID`。
- 当真值缺失时，detect 后 `cw.portal.restart` 仍报 `CW_PORTAL_ENTRY_TRUTH_REQUIRED`。
- `refresh` 复用 helper 后，现有“至少等待一轮 settle 再 OCR”“等待回到 invest 后再识别”的行为不回归。

### 2. `tests/test_daemon_protocol.py`

- `cw.portal.detect` 通过 capture 路径返回 `screenshot`，而不是普通 `_run_cw()` 路径。
- detect 不在 `CW_MUTATING_METHODS`；失败时不产生 `tainted=1` / `recover`。
- guide 查询失败时，detect 仍返回成功且保留 raw cards。
- refresh 抽 helper 后，guide best-effort 语义与当前保持一致。

### 3. `tests/test_cw_rpc_contracts.py`

- `trail cw portal detect --session ...` 正确调用 `cw.portal.detect`。
- payload 仅透传 `session_id`，canonical command 为 `cw.portal.detect`。
- stdout 与 `cw.portal.refresh` 保持同形，包括 `ok ... cards=...`、`shot`、`opt`、`guide` 行。
- 新增顺序场景：先 detect，再 `cw portal select`，验证 detect 产物可被 select 直接消费。

### 4. `tests/test_output_rendering.py`

- `TEXT_RENDERERS` 注册 `cw.portal.detect`，并复用 `_render_cw_portal_cards`。
- `cw.portal.detect` 的 text 输出与 `cw.portal.refresh` 同形。
- `cw.portal.detect --format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
- README、skills 中新增的命令边界与恢复文案断言同步更新。

### 5. `tests/test_atomic_commands.py`

- `trail cw --help` 与 `trail cw portal --help` 需要把 `detect` 纳入 `portal` 家族描述。
- `trail cw portal detect --help` 需要明确“当前已在投资环境页时重新识别并保存 portal snapshot”。
- `portal` 分组帮助文本要同时保留“选择 / 识别 / 刷新 / 重开”的区分，并避免重新出现“进入投资环境页”之类越界描述。

## 文档更新

需要同步更新以下入口，而且要写清 `detect` 与 `start` / `refresh` 的区别，不能只机械追加命令名：

1. `README.md`
   - 更新 CW 流程说明与命令边界说明。
   - 明确写出恢复场景：当 `cw start` 已把你带到投资环境页，但链路中途失败或 session 没缓存到 fresh portal snapshot 时，可手动运行 `trail cw portal detect --session <id>` 重新识别当前三张卡。
   - 明确 detect 与 refresh 的区别：
     - detect = 重识别当前三张卡，不点击
     - refresh = 点击刷新后生成新的三张卡
2. `skills/trail-cw/SKILL.md`
   - 在标准流程或执行规则中加入同一恢复场景，避免 agent 在投资环境页误回退到 `cw start`。
   - 明确 `portal.select|detect|refresh|restart` 的职责边界。
3. `trail/commands/cw.py` 的 help 文案
   - `cw` 总览与 `portal` 分组帮助同步更新。
   - `detect` 叶子帮助要强调“只重建 snapshot，不推进流程”。

## 验收标准

满足以下条件即可视为完成：

1. 用户在手动进入投资环境页后，可以直接运行 `trail cw portal detect --session <id>`。
2. `CommandService` 会把 `cw.portal.detect` 路由到 capture 路径，成功结果稳定带截图。
3. 命令输出的卡片与 guide 摘要格式和 `cw.start` / `cw.portal.refresh` 一致。
4. 命令会把 portal 快照写回 session，随后可直接执行 `trail cw portal select`。
5. 若缺少 `mode` / `difficulty` / `battle_mode`，detect 不会伪造这些值，`cw.portal.restart` 的既有前提保持不变。
6. `cw.portal.refresh` 的 settle/wait 行为、guide best-effort 语义与现有 contract 均未被破坏。
7. README、相关 skills、help 断言与 renderer/CLI/RPC 测试同步更新。
