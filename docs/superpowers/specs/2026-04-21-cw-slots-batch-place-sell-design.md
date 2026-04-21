# `cw slots place` 与 `cw hand sell` 批量动作设计

## 背景

当前货币战争编队相关命令里：

1. `trail cw slots place-one` 每次只能执行一次从手牌到前后排的上场动作。
2. `trail cw hand sell-one` 每次只能卖出一张手牌。

这导致 Agent 在一次编队整理里需要反复发多个命令，既慢，也让动作链变得冗长。

用户这次明确要求：

1. 直接把 `place-one` 替换成 `place`。
2. `place` 支持一次传入多个动作，并按传入顺序依次执行。
3. 直接把 `sell-one` 替换成 `sell`。
4. `sell` 支持一次传入多个手牌位置，并按传入顺序依次卖出。
5. 两个批量命令都采用“遇错即停”的语义；已经成功的前置动作不回滚。
6. 不保留旧命令兼容层。

## 目标

1. 把单步上场/卖牌命令替换为批量命令，减少一次编队整理需要的 CLI 往返次数。
2. 保留显式动作风格，不让命令自行推断阵容策略。
3. 冻结批量动作的顺序语义，保证 Agent 可以确定性地描述和复现动作链。
4. 在中途失败时仍保存“槽位状态已失效”这一关键信号，避免实际 UI 已变化但 session 仍表现为旧快照。
5. 保持默认文本输出协议稳定，不新增新的 renderer 家族、前缀或首行字段。

## 非目标

这次不做：

- 修改 `trail cw slots swap` 的单步语义
- 修改 `trail cw hand sell-plan` 的建议语义
- 自动决定要上什么牌或卖什么牌
- 提供事务式回滚或“全成全败”保证
- 保留 `place-one` / `sell-one` 兼容别名
- 修改现有成功路径的首行摘要格式

## 方案比较

### 方案 A：重复 `--action` / 重复 `--slot`

做法：

- `trail cw slots place --action hand:0,front:0 --action hand:1,back:2`
- `trail cw hand sell --slot 0 --slot 2`

优点：

- 与现有 `trail cw slots read --slot ...` 的重复参数风格一致
- 保序天然明确，CLI 使用成本最低
- payload 结构清晰，便于在 daemon 层统一转成列表

缺点：

- `--action` 需要新增一个轻量字符串解析规则

### 方案 B：重复 `--source` / `--target` 成对传参

做法：

- `trail cw slots place --source hand:0 --target front:0 --source hand:1 --target back:2`

优点：

- 不需要额外定义 `source,target` 字符串格式

缺点：

- CLI 层更难校验源/目标数量是否严格配对
- 用户和 Agent 都更容易把一串源/目标看错位

### 方案 C：单个结构化列表参数

做法：

- `trail cw slots place --actions '[{"source":"hand:0","target":"front:0"}]'`
- `trail cw hand sell --slots '[0,2]'`

优点：

- 扩展性最好

缺点：

- 明显背离当前 CLI 的简单显式风格
- 对终端 Agent 和人工使用都不友好

## 结论

采用 **方案 A**。

理由：

1. 这是对当前命令面最自然的批量扩展，用户确认接受。
2. 改动集中在参数解析和 scene orchestration，不需要把 CLI 变成 JSON 入口。
3. 对 README、技能文档和 stdout 契约的改写成本最低。

## 设计细节

### 1. CLI 与 canonical command 调整

命令面直接替换为：

- `trail cw slots place --session <id> --action <source,target> [--action <source,target> ...]`
- `trail cw hand sell --session <id> --slot <n> [--slot <n> ...]`

canonical command 直接替换为：

- `cw.slots.place`
- `cw.hand.sell`

需要同步替换的注册面：

- `trail/commands/cw.py`
- `trail/daemon/command_service.py`
- `trail/daemon/cw_service.py`
- `trail/output/rendering.py`
- README 示例
- `skills/trail-cw-slots/SKILL.md`
- 相关 CLI / RPC / renderer 测试

不提供 `place-one` / `sell-one` 的别名或兼容入口。

### 2. 参数与 payload 形态

`place` 的 CLI 解析结果统一转换成：

```python
{"actions": [{"source": "hand:0", "target": "front:0"}, ...]}
```

`sell` 的 CLI 解析结果统一转换成：

```python
{"slots": [0, 2, ...]}
```

冻结以下输入规则：

1. `place` 至少需要一个 `--action`。
2. `sell` 至少需要一个 `--slot`。
3. `--action` 固定为 `源位置,目标位置`，只接受一个英文逗号分隔。
4. 位置引用继续沿用现有 `front:<idx>` / `back:<idx>` / `hand:<idx>` 语义。
5. `sell` 的 `--slot` 继续沿用当前手牌零基索引语义。

校验层级也需要冻结，避免实现分叉：

1. **CLI 本地输入校验**：缺失 `--action` / `--slot`，以及 `--action` 不满足单个英文逗号分隔时，直接在 CLI 层返回 input invalid，不发 RPC。
2. **daemon / scene 运行前校验**：`source/target` 的区域名、索引范围，以及 `sell` 的手牌索引范围，继续交给现有 scene 规则校验并走正常 envelope。
3. **daemon / scene 执行期业务失败**：例如拖拽后目标位不能上场，属于“已经发生副作用后的已知业务失败”，不与执行前输入错误混淆。

执行前输入失败不触发任何运行时副作用，也不会创建 daemon request 记录。

### 3. Scene 层执行模型

`trail.scenes.cw.slots` 保留当前单步 runtime helper：

- `build_cw_slot_swapper()`
- `build_cw_hand_seller()`

在其上新增两个批量 orchestration：

- `place_cw_slots(session, *, actions, placer=None)`
- `sell_cw_hand_slots(session, *, slots, seller=None)`

它们的职责仅限于：

1. 校验与标准化输入列表
2. 按顺序调用单步 helper
3. 在成功返回或“已发生副作用的失败”退出前刷新 session 中的最小可信状态

这里明确收紧边界：

1. 只做单项格式/范围解析，不做跨步骤去重、冲突检测或整批前瞻推演。
2. 不在内存里尝试推导新的 `front/back/hand` 快照内容。
3. 不自动重读 UI；命令执行后的可信状态仍然只是 `slots.stale=True` 与 `sell_plan={}`。
4. `sell_cw_hand_slots` 只是显式 `slots` 列表的顺序包装，不吸收 `sell-plan`、自动筛选或“整手出售”语义。

`swap` 继续保持单步，不并入这次批量接口。

### 4. 顺序与停止语义

批量命令按传入顺序严格执行。

例如：

```text
trail cw slots place --action hand:0,front:0 --action hand:1,back:2
```

必须先完成 `hand:0 -> front:0`，再执行 `hand:1 -> back:2`。

一旦某一步在运行期失败：

1. 立即停止后续步骤
2. 不回滚已经完成的前置动作
3. 返回 failure envelope，供上层明确知道本次批量链未全部完成

### 5. 状态写回与失败持久化

当前 mutation 流如果在副作用发生后抛异常，容易走到 “side effect applied but state not persisted” 分支，并把 session 标成未知污染。这对本次批量命令不够理想，因为“前几步已成功、当前步骤失败”是一个**已知且可解释**的业务结果，不应一律视为未知态。

因此这里要冻结的是**外部语义**，而不是发明新的 request-status 状态种类。实现应复用现有 known-failure / completed 通道，不新增新的 `final_state` 字面量，也不新增新的 taint 规则：

1. 只要批量命令实际执行过至少一步，哪怕当前失败发生在“本步拖拽后再发现目标不可上场”这种场景，也统一把 `cw.slots.stale = True`
2. 同时清空 `sell_plan`
3. 先把上述状态保存进 session
4. 然后返回已知业务 failure envelope；对应 `daemon.request_status` 仍应表现为 `final_state=completed`、`tainted=0`
5. 不新增新的 `daemon.request_status` 状态字面量，不新增新的 `recover` action

这样可以冻结一个稳定恢复路径：

- 用户或 Agent 看到失败后，知道前面某些动作可能已经生效
- 但 session 已明确标记 `slots` 为 stale
- 下一步应该重新执行 `trail cw slots read`

这比让请求直接落入 `applied_but_not_persisted` 更符合这类“部分完成、结果已知”的业务语义。

同时也要明确保留现有 unknown fallback 边界：

1. 如果 `save_session()` 自身失败，仍按现有规则落入 `applied_but_not_persisted` 并 taint。
2. 如果状态已保存，但 capture / metadata / journal 完结阶段失败，仍按现有规则落入 `persisted_but_response_unknown` 并 taint。
3. `shot path=...` 在已知业务 failure 路径上是 best-effort 目标；若 capture 自身失败，不应为了强行保留截图而篡改 unknown fallback 语义。

### 6. 输出协议

成功路径保持现有 `cw slots` renderer 家族：

- `ok cw.slots.place front=<n> back=<n> hand=<n> stale=<0|1>`
- `ok cw.hand.sell front=<n> back=<n> hand=<n> stale=<0|1>`

并继续遵守：

1. 只要命令产生截图，就输出 `shot path=...`
2. 不新增新的正文前缀
3. 失败路径仍按 `request -> shot -> why -> warn -> ref -> recover` 顺序输出

对本次新增的“已知业务 failure”路径，再冻结两点：

1. 如果这条失败来自 daemon mutation，仍应保留 `request id=...`，并在有截图时输出 `shot path=...`。
2. 因为这不是 unknown-result failure，所以默认不输出 `recover action=daemon.request_status request=<id>`，也不新增其他 `recover` 行。

本次不改变 `sell-plan` 的输出，它仍然只返回候选数量摘要。

### 7. 文档与技能同步

README 与 `skills/trail-cw-slots/SKILL.md` 需要同步更新，不仅是替换旧示例：

- 把 `place-one` 改为 `place`
- 把 `sell-one` 改为 `sell`
- 明确 `place` / `sell` 都支持一次传多个动作
- 明确中途失败后应重新 `trail cw slots read`
- 如果 README 当前没有对应示例，需要新增一段批量 `place` / `sell` 的示例与失败后重读说明，而不是因为“暂无旧示例”就跳过更新

## 受影响文件

核心代码：

- `trail/commands/cw.py`
- `trail/scenes/cw/slots.py`
- `trail/daemon/cw_service.py`
- `trail/daemon/command_service.py`
- `trail/output/rendering.py`

文档与技能：

- `README.md`
- `skills/trail-cw-slots/SKILL.md`

测试：

- `tests/test_cw_slots.py`
- `tests/test_cw_rpc_contracts.py`
- `tests/test_output_rendering.py`
- `tests/test_daemon_protocol.py`
- `tests/test_daemon_session_service.py`

## 测试计划

至少补齐以下覆盖：

1. `place` / `sell` 的 CLI 参数解析与 payload 形态，包括：旧命令删除后的 `No such command`、重复参数保序、以及 `--action` 缺失/格式错误在 CLI 本地失败且不发 RPC
2. scene 层批量执行顺序正确，且不会做跨步骤去重、冲突推演或自动 snapshot 推导
3. 运行期中途失败时“遇错即停”且不继续执行后续动作
4. 运行期中途失败时，只要已执行过至少一步，`slots.stale` 与 `sell_plan` 会被持久化到 session
5. 这类已知业务 failure 的 `request_status` 仍为 `final_state=completed`、`tainted=0`，且不会错误触发 tainted gate
6. `save_session` 失败与 save 后 capture/journal 失败仍继续覆盖现有 unknown fallback / tainted 语义
7. renderer 直接断言新 canonical command 的 success / known-failure 文本协议顺序，不新增新前缀、recover 或 YAML allowlist 行为
8. README 与 skill 文档都新增/更新到新命令面，而不是只改已有字符串

## 验收标准

满足以下条件即可视为完成：

1. 用户可以用一条 `trail cw slots place` 执行多个上场动作。
2. 用户可以用一条 `trail cw hand sell` 卖出多张手牌。
3. 两类命令都严格保序、遇错即停。
4. 中途失败不会把 session 留在“旧快照仍 fresh”的错误状态。
5. 输出协议、README、skill 文档与测试都已同步到新命令面。
