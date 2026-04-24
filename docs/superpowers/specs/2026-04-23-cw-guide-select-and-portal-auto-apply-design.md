# `cw guide` 选择拆分与 `cw.portal.select` 自动应用设计

## 背景

当前 `cw.guide.apply` 同时承担三件事：

1. 根据 `lineup_id` 拉取完整攻略。
2. 在游戏内通过 UI 输入攻略码并点击应用。
3. 将攻略摘要、约束与 artifact id 写回当前 session。

这套设计在“选完攻略后立刻应用”的路径上工作正常，但不适合当前实际流程：Agent 往往会先选定攻略，再经过若干开局动作，直到 `cw.portal.select` 选择投资环境后，才真正进入需要在游戏里应用攻略的时机。

结果是，“攻略选择”与“攻略应用”被绑得过紧：

1. 选攻略发生得太早，真正应用发生得太晚。
2. `cw.portal.select` 只负责点卡，不负责兑现当前已选攻略。
3. `cw.guide.apply` 这个命令名同时包含“选择攻略”和“应用攻略”两层语义，职责不清。

## 目标

1. 给 `guide.fetch.cw` 增加 `--select`，把“记录当前选中攻略”从 `cw.guide.apply` 中拆出来。
2. 保持 `guide.fetch.cw` 不带 `--select` 时的既有只读行为不变。
3. 把真正的游戏内攻略应用动作移动到 `cw.portal.select` 成功后自动执行。
4. 当 session 中没有已选攻略时，`cw.portal.select` 必须直接失败，并明确要求先执行 `guide.fetch.cw --select`。
5. 保留 `cw.guide.apply`，但把它降级为“对当前已选攻略执行手动兜底应用”的命令。
6. 保持默认文本协议、renderer 家族、失败顺序、README/skills/test 的稳定性。
7. 收口“当前攻略”的单一真相源，让 `cw.guide.current` 与相关读路径只认 session 中显式选中的 guide，不再被普通 `guide.fetch.cw` artifact 隐式恢复。

## 非目标

1. 不新增 `cw.guide.select` 新命令。
2. 不改变 `guide.fetch.cw` 默认文本的完整攻略输出格式。
3. 不改变 `cw.portal.select` 的 success renderer 家族与首行事实。
4. 不新增 YAML allowlist。
5. 不改变 `session.scene_state["cw"]["guide"]` 的核心 payload shape，除非为了复用现有 helper 必须补极小字段。
6. 不新增“selected guide / applied guide”两套并行 session state；本次继续以 `session.scene_state["cw"]["guide"]` 作为当前攻略的单一状态槽位。

## 备选方案

### 方案 A：`guide.fetch.cw --select` + `cw.portal.select` 自动应用（采纳）

- `guide.fetch.cw` 增加可选 `--select`，选择模式下要求 `--session`。
- 选择动作只负责 fetch、写 artifact、写 session guide/constraints，不执行 UI。
- `cw.portal.select` 改成要求当前已有已选攻略；选完投资环境后立即自动应用该攻略。
- `cw.guide.apply` 保留为手动兜底，但只消费 session 中当前已选攻略。

优点：

1. 与用户真实心智一致，先“选攻略”，后在正确时机“应用攻略”。
2. 命令入口最短，不需要额外新增 `cw guide select`。
3. 现有 `guide.fetch.cw` 已天然拥有完整攻略 payload，最适合承担“顺手写入 session”这件事。

缺点：

1. `guide.fetch.cw` 从纯 read 命令变成“可选带副作用”的命令，需要严格约束只在 `--select` 时生效。
2. 需要单独处理 read 路径和 select 路径的 daemon 分支，避免污染现有无 session 的调用。

### 方案 B：新增 `cw.guide.select`

- 保持 `guide.fetch.cw` 纯读；新增 `cw.guide.select --session --guide` 只写 session。
- `cw.portal.select` 一样负责自动应用当前已选攻略。

优点：职责最清晰。

缺点：用户要先 fetch 再 select，多一步；同时会引入新的 canonical command、help、README 与测试面。

### 方案 C：保留 `cw.guide.apply` 作为“延后执行的选择命令”

- `cw.guide.apply` 改名不改名义，内部只记录当前攻略，真正 UI apply 仍由 `cw.portal.select` 兑现。

优点：表面改动最少。

缺点：命令名与行为进一步错位；用户和 Agent 都会更难理解“apply 为什么不 apply”。

## 方案设计

### 1. 命令与外部语义

#### 1.1 `guide.fetch.cw`

- 新增 `--select` 布尔参数。
- 新增 `--session <id>`，仅在 `--select` 时必填；不带 `--select` 时不要求 session。
- `--session` 但未开启 `--select` 属于非法参数组合，不能静默忽略。
- 不带 `--select`：
  1. 保持当前行为，即 fetch 完整攻略并写入 artifact cache。
  2. 默认文本输出完全不变。
- 带 `--select`：
  1. 先 fetch 完整攻略。
  2. 继续写 artifact。
  3. 再把攻略摘要与约束写入 `session.scene_state["cw"]`。
  4. 不执行任何游戏内 UI 操作。
  5. 默认文本、success `data` 与 `--format yaml` body 仍返回完整攻略内容，不新增新的 renderer 家族，也不额外回传 `selected/session/artifact` 选择态元数据。

#### 1.2 `cw.portal.select`

- 对外命令名与参数保持不变：`trail cw portal select --session <id> --card-idx <n>`。
- 成功语义改为：
  1. 先严格校验当前 session 已存在“已选攻略”，且 guide payload 中 `share_code` 完整可用。
  2. 校验当前 portal snapshot fresh。
  3. 点击目标投资环境卡并确认。
  4. 自动执行当前攻略的 UI apply，并继续复用 `apply_cw_guide_via_ui(...)` 现有的页面稳定等待逻辑。
  5. 保存 session。
- 当没有已选攻略时：
  1. 不做 portal 点击副作用。
  2. 直接失败。
  3. 错误文案明确提示先执行 `guide.fetch.cw --select`。
- 当 portal 点击已发生，但后续 auto apply / session save / response 构建失败时：
  1. 不引入新的自定义半持久化流程。
  2. 继续复用现有 mutation 的“side effect 已发生”恢复语义。
  3. 由 `request id` / `daemon.request_status` / reconcile 链路承担恢复与排障。
- success 首行继续保持：`ok cw.portal.select idx=... 投资环境=...`。

#### 1.3 `cw.guide.apply`

- 保留命令，作为手动兜底入口。
- 去掉 `--lineup-id` / `--guide` 参数，只保留 `--session`。
- 语义改为：
  1. 严格读取当前 session 已选攻略。
  2. 执行 UI apply。
  3. 返回当前 guide summary。
- 不再在该命令内部重新 fetch 新攻略。

#### 1.4 `cw.guide.current` 与相关读命令

- 从这次改造开始，`session.scene_state["cw"]["guide"]` 表示“当前已选攻略”，不再额外区分“已选”和“已应用”两份 session state。
- `cw.guide.current` 的语义同步收口为“读取当前已选攻略摘要”：
  1. 只读取 session 中显式选中的 guide。
  2. 缺失时直接失败，并明确提示先执行 `guide.fetch.cw --select`。
  3. 不再从普通 `guide.fetch.cw` 产生的 latest artifact 隐式恢复当前攻略。
- `cw.shop.status`、`cw.strategy.detect|refresh` 等“可选消费当前攻略”的读路径，同样只看 session 中当前已选攻略：
  1. 有 guide 时继续附带 `guide_summary` 或 `info 已加载攻略=1`。
  2. 没有 guide 时按“未加载攻略”处理，例如省略 `guide_summary`、输出 `info 已加载攻略=0`。
  3. 不再把普通 `guide.fetch.cw` artifact 当作当前攻略的隐式后备来源。
- 普通 `guide.fetch.cw` 产生的 artifact 继续只是“攻略预览/cache”与显式追踪用途，不再承担“自动把当前攻略补回 session”的职责。

### 2. 状态边界与写入顺序

#### 2.1 session guide state

不能把 `guide.fetch.cw --select` 简单地继续套在现有 `apply_cw_guide(session, guide_data, reset_dependent_state=True)` 上，因为那会顺带重置 `slots` / `sell_plan` / `shop` / `stage`，与“只记录当前已选攻略”的新命令语义不符。

本次需要把“写入当前攻略”和“UI apply 后的运行态失效”拆成两个层次：

1. 当前攻略写入 helper
   - 负责 normalize guide payload，并只覆盖 `cw.guide`、`cw.constraints` 与 guide 自身的 `remaining_purchases` 初始值。
   - 不重置 `portal` / `shop` / `slots` / `sell_plan` / `stage`。
   - 可以通过抽出 `apply_cw_guide(...)` 的纯映射部分，或显式复用 `reset_dependent_state=False` 的 no-reset 路径来实现；关键是行为边界，而不是 helper 名字。
2. UI apply 后的运行态失效 helper
   - 只在 `cw.portal.select` / `cw.guide.apply` 的 UI apply 成功后调用。
   - 负责把 `slots` / `sell_plan` / `shop` / `stage` 标记为需要重新读取；`portal` 仍沿用 portal 选择链路自己的 stale 处理。

这样可以同时保持：

1. `remaining_purchases` 初始化逻辑仍从完整 guide payload 计算。
2. `constraints.min_coins/min_level/mid_level/priority/positioning` 的写入逻辑仍与当前 guide payload 保持一致。
3. “选中当前攻略”不会隐式清空当前运行态，而“真正 UI apply 完成”之后才会触发相关状态失效。

#### 2.2 三条链路的写入时机

1. `guide.fetch.cw --select`
   - 参数校验（必须 `select=1` 且有 session） -> fetch guide -> create artifact -> 写入当前 guide/constraints/remaining_purchases -> save session
2. `cw.portal.select`
   - 严格读取当前已选 guide -> portal click/confirm -> `apply_cw_guide_via_ui(runtime, share_code=...)` -> 标记运行态 stale -> save session
3. `cw.guide.apply`
   - 严格读取当前已选 guide -> `apply_cw_guide_via_ui(runtime, share_code=...)` -> 标记运行态 stale -> save session

其中：

1. 只有 `guide.fetch.cw --select` 会替换 session 中“当前攻略”内容。
2. `cw.portal.select` 与 `cw.guide.apply` 只消费当前 guide，不重新覆盖 guide payload。
3. `cw.guide.current` 与其他读取当前攻略的路径，不再把 latest `guide.fetch.cw` artifact 当作隐式 fallback。
4. `cw.portal.select` 一旦发生 portal 点击，后续失败统一走现有 mutation 的“side effect 已发生”恢复链，而不是额外设计新的半保存流程。

### 3. daemon / scene 接线

#### 3.1 CLI 层

`trail/commands/guide.py`

1. `guide fetch` 增加 `--select` 与 `--session`。
2. 当 `scene != cw` 时继续维持现有不支持逻辑。
3. 当 `--select` 但没有 `--session` 时，直接走现有 input invalid response。
4. 当带 `--session` 但没有 `--select` 时，同样直接视为非法参数组合，而不是静默忽略。
5. 进入 daemon 时，`--select` 模式必须把 `session_id` 作为请求顶层字段传下去，而不是只塞进业务 payload。

`trail/commands/cw.py`

1. `cw guide apply` 改成只接收 `--session`。
2. `CW_GUIDE_HELP` 需要改写，明确：
   - `guide.fetch.cw --select` 负责选择当前攻略。
   - `cw.portal.select` 会自动应用已选攻略。
   - `cw.guide.apply` 是手动兜底。
   - `cw.guide.current` / `cw.guide.apply` 查看的是“当前已选攻略”，不是“最近一次普通 fetch 的攻略预览”。
3. 旧的 `--guide` / `--lineup-id` 参数必须直接移除；无论 CLI 还是 direct RPC，都不能静默忽略或偷偷回退到旧 fetch+apply 行为。

#### 3.2 `CommandService`

当前 `guide.fetch.cw` 走 `_handle_guide_fetch()`，只 fetch 并写 artifact。新设计下要把两条路径明确拆开：

1. 普通 `guide.fetch.cw`
   - 继续走现有只读 `_handle_guide_fetch()` 路径。
   - 不要求 session，也不写 session。
2. `guide.fetch.cw --select`
   - 在进入 plain fetch return path 之前，切到一个专用的“fetch + artifact + session 写入”分支。
   - 虽然它不执行游戏内 UI，也不需要截图，但因为它会写 session，所以仍应走 request tracking / mutation locking 语义，避免与 `cw.portal.select`、`cw.guide.apply` 等命令并发改同一个 session。
   - 该分支必须先完成 fetch 与 artifact，再写入 session 当前 guide 状态，然后返回与普通 `guide.fetch.cw` 完全一致的 success payload。
   - direct daemon 请求若出现 `select=true` 但缺少顶层 `session_id`，必须统一收口为稳定的 `GUIDE_INPUT_INVALID`，不能把错误推迟到更底层。

这里不需要额外截图，也不应该把 `guide.fetch.cw --select` 误归类为新的 renderer family；它仍是 control-plane 侧的攻略选择动作，只是拥有 session mutation 语义。

#### 3.3 `CwService`

新增一个共享 helper，专门负责“从当前 session 严格解析出一个可应用的已选攻略”：

1. 校验 `session.scene_state["cw"]["guide"]` 为 dict。
2. 校验 `share_code` 存在且可用。
3. 如缺失，抛出明确的 `TrailError`，提示先执行 `guide.fetch.cw --select`，或提示当前 guide 状态损坏。
4. 不做 artifact IO、不做 latest-artifact fallback、不做 session mutation，也不顺手构造别的摘要对象。

然后让两条 mutation 复用它：

1. `cw.portal.select`
   - 先调用共享 helper 取 guide。
   - 再调用 `select_cw_portal(...)`。
   - 再 `apply_cw_guide_via_ui(...)`。
   - 再标记 `slots` / `sell_plan` / `shop` / `stage` stale。
   - 最后返回原 `selected` portal 摘要。
   - 若 portal 点击之后的任何一步失败，继续复用现有 `CwSideEffectAppliedError` / mutation recover 语义，而不是额外设计分步保存。
2. `cw.guide.apply`
   - 不再 fetch lineup。
   - 直接调用共享 helper 取 guide。
   - 再 `apply_cw_guide_via_ui(...)`。
   - 再标记 `slots` / `sell_plan` / `shop` / `stage` stale。
   - 返回当前 `cw.guide` 摘要。
3. `cw.guide.current` 与其他“当前攻略”读路径
   - 不再复用旧的 latest-artifact fallback 行为。
   - `cw.guide.current` 在当前 guide 缺失时直接失败。
   - `cw.shop.status` / `cw.strategy.detect|refresh` 在 guide 缺失时保持“无当前攻略”的读语义，例如省略 `guide_summary`、输出 `info 已加载攻略=0`。

### 4. 错误处理

需要新增或收口的错误分支：

1. `guide.fetch.cw --select` 缺少 `--session`
   - CLI 直接返回 `GUIDE_INPUT_INVALID`。
2. `guide.fetch.cw` 传了 `--session` 但没有 `--select`
   - 同样直接视为 `GUIDE_INPUT_INVALID`，避免形成“带 session 的隐式 select”。
3. direct daemon `guide.fetch.cw` 请求出现 `select=true` 但缺少顶层 `session_id`
   - 仍统一收口为 `GUIDE_INPUT_INVALID`。
4. `cw.guide.current` 无已选攻略
   - 直接失败，错误文案明确包含 `guide.fetch.cw --select`。
5. `cw.portal.select` 无已选攻略
   - 直接失败，错误文案明确包含 `guide.fetch.cw --select`。
   - 不发生 portal 点击副作用。
6. `cw.guide.apply` 无已选攻略
   - 直接失败，错误文案同样指向 `guide.fetch.cw --select`。
7. 当前 guide payload 缺少 `share_code`
   - 抛 guide 状态损坏类错误，不静默回退到最新 artifact。
8. `cw.portal.select` 在 portal 点击之后、auto apply / save / response 任一环节失败
   - 复用现有 mutation 的 post-side-effect failure 语义。
   - 失败输出继续遵守固定顺序，并通过 `request id` / `recover action=daemon.request_status ...` 暴露恢复入口。
9. `cw.guide.apply` 收到 legacy `--guide` / `--lineup-id` 选择参数
   - 直接报稳定的参数错误，不忽略、不兼容旧行为。

这里明确不做的一件事是：`cw.portal.select` 不根据投资环境和已选攻略的 `portals` 字段做匹配校验。按已确认约束，只要当前已有已选攻略，就一律自动应用；不匹配与否由上游编排负责。

这里同样明确不做的一件事是：不再允许普通 `guide.fetch.cw` artifact 隐式把当前 guide 补回 session。显式 select 才能建立当前攻略状态。

### 5. 输出协议

#### 5.1 `guide.fetch.cw --select`

- success 文本完全复用现有 `guide.fetch.cw` renderer。
- 不增加“已选择=1”之类的新行，避免扩张现有稳定协议。
- success `data` 与 `--format yaml` body 也必须与普通 `guide.fetch.cw` 保持一致，不新增 `selected`、`session_id`、`artifact_id` 等选择态字段。

#### 5.2 `cw.portal.select`

- success 首行继续为：`ok cw.portal.select idx=... 投资环境=...`。
- 若命令带截图，仍保持现有顺序：首行 -> `shot path=...` -> `info read_image_first=1` -> 其余实体行。
- 自动应用攻略是内部副作用，不新增 `guide` 或 `info` 行。
- 若失败发生在 portal 点击之后，则继续复用现有 mutation failure 协议与恢复顺序，不额外发明 portal+guide 组合错误 renderer。

#### 5.3 `cw.guide.current` / `cw.guide.apply`

- 继续复用当前 `cw.guide.current|apply` summary family。
- 其语义同步收口为“当前已选攻略摘要”，不再表述为“最近一次已应用攻略摘要”。
- 数据来源从“刚 fetch 的 guide / latest fetch artifact fallback”收口为“session 当前已选 guide”。
- `info 攻略快照ID=...` 继续表示当前已选 guide 对应的 artifact id。

### 6. 测试与文档同步

至少需要覆盖以下增量：

1. `tests/test_guide_rpc_contracts.py`
   - `guide.fetch.cw --select --session` 正常透传，并把 `session_id` 放到 daemon 顶层请求。
   - `guide.fetch.cw --select` 缺少 session 时的 CLI / daemon 稳定报错。
   - `guide.fetch.cw --session <id>` 但不带 `--select` 时的非法参数报错。
   - `guide.fetch.cw --select` 与普通 `guide.fetch.cw` 的 success `data` / YAML shape 完全一致。
2. `tests/test_cw_rpc_contracts.py` 与 `tests/test_atomic_commands.py`
   - `cw.guide.apply` 新签名只传 session。
   - `cw.guide.apply --guide|--lineup-id` 明确报错。
   - `cw.guide.current|apply` 的 stdout/help 语义改成“当前已选攻略”，不再写“当前已应用攻略”。
3. `tests/test_cw_guide.py`
   - `guide.fetch.cw --select` 只写 guide/constraints/remaining_purchases，不重置 `portal/shop/slots/sell_plan/stage`。
   - `cw.guide.current` 严格读取 session 当前 guide；无 guide 时失败。
   - 普通 `guide.fetch.cw` artifact 不再隐式恢复 `cw.guide.current` / `cw.shop.status` 的当前攻略语义。
   - `cw.shop.status` / `cw.strategy.detect|refresh` 在 guide 缺失时按“未加载攻略”表现，而不是自动 recovery。
   - `cw.guide.apply` 从 session 读取 guide 并 UI apply；无已选攻略或 `share_code` 损坏时失败。
4. `tests/test_cw_portal.py` / `tests/test_daemon_protocol.py`
   - `cw.portal.select` 在已有 guide 时会自动 apply。
   - 无已选攻略时直接失败，且不点击 portal。
   - portal 点击后的 auto apply/save/response failure 继续走现有 mutation recover 语义。
   - 选卡成功后仍保持原 portal renderer/output 事实。
5. `tests/test_output_rendering.py` 与 `tests/test_output_debug.py`
   - `guide.fetch.cw --select` 的 stdout/YAML 契约不扩张。
   - `cw.guide.current|apply` 的摘要字段不变，但相关文案断言改成“当前已选攻略”。
6. `README.md`
   - 更新推荐链路：先 `guide fetch cw ... --select --session <id>`，再 `cw portal select --session <id> --card-idx <n>`。
   - 明确 `guide.fetch.cw` 默认只是预览/缓存，不会建立当前攻略。
   - 明确 `cw.guide.current|apply` 看到的是当前已选攻略；`cw.guide.apply` 是手动兜底，不再是“传 lineup id 即 fetch+apply”。
7. skills / help / `AGENTS.md`
   - 同步更新 `skills/trail-cw-guide/**`、`skills/trail-cw-entry/**`、CLI help、项目级 `AGENTS.md` 中与 guide 选择、portal 自动应用、`cw.guide.current|apply` 术语相关的说明。

## 风险与取舍

1. `guide.fetch.cw` 变成“可选带 session 副作用”的命令后，最主要风险是无意中让纯读调用也要求 session，或把 `--session` 当成隐式 select。实现时必须把 `--select` 分支与默认分支严格隔离，并冻结非法参数矩阵。
2. “当前攻略”从现在起只允许由显式 select 建立，因此必须同步拆掉普通 fetch artifact 的隐式 recovery；否则 `cw.guide.current` / `cw.shop.status` / strategy load-info 会继续把旧语义带回来。
3. `guide.fetch.cw --select` 与 `cw.portal.select` 的职责边界变清晰后，另一个风险是把 `guide.fetch.cw --select` 误实现成会清空运行态，或把 `cw.portal.select` 自动 apply 失败做成不稳定的半持久化。spec 必须先把 no-reset 写入与 post-click recover 语义钉死。
4. `cw.guide.apply` 去掉 `--guide/--lineup-id` 后，相关 CLI help、contract tests、README/skills/AGENTS 文案都必须同步更新，否则很容易出现文档与实现漂移。

## 结论

采纳方案 A：

1. 用 `guide.fetch.cw --select` 承担“选中当前攻略并写 session”。
2. 用 `cw.portal.select` 承担“选择投资环境并自动应用当前攻略”。
3. 让 `cw.guide.apply` 退化为“手动重试当前已选攻略的 UI apply”兜底命令。
4. 让 `session.scene_state["cw"]["guide"]` 成为“当前已选攻略”的单一真相源，禁止普通 `guide.fetch.cw` artifact 再隐式把当前攻略补回 session。

这样可以把“选择攻略”和“应用攻略”拆到正确的时机，同时尽量复用现有 guide payload、session shape 与 renderer 协议，避免引入新的命令面和新的文本协议家族。
