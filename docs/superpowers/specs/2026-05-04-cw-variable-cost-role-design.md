# CW 变费角色处理设计规格

> 本规格先固定领域规则与实现边界，不授权直接实现。后续实现计划必须以本规格为依据，并在动手前先处理当前错误方向的工作树改动。

**日期：** 2026-05-04

**范围：** 货币战争中 `银狼LV.999` 这类变费角色的 slots、shop、购买验证、缓存更新、默认文本输出与 skill 文档协议。

## 背景

`银狼LV.999` 是货币战争中的变费角色。它和普通 `银狼` 是两个完全不相干的角色；不能因为名称包含关系、相似图标、fuzzy match 或历史 fixture 写法而互相归一。

`银狼LV.999` 自身存在 3 费、4 费、5 费阶段。这些阶段是同一角色的一条进化链，而不是三个彼此独立的业务角色。实现中如果大量使用底层 `role_id` 作为业务身份，反而会破坏同一变费角色在不同费用阶段之间的一致性。

## 已确认领域规则

1. `银狼LV.999` 与普通 `银狼` 没有角色等价、攻略等价、购买等价或补星等价关系。
   该无关关系适用于所有业务上下文，包括 canonicalization、fuzzy matching、guide matching、shop priority、purchase count、sell_plan protection 与 Agent 可见业务身份。
2. `银狼LV.999` 初始是 3 费角色。
3. 二星 `银狼LV.999` 只有在上场时才触发特殊选择。
4. 选择升费时，当前 `银狼LV.999` 会变为下一费用阶段的 1 星角色：3 费二星 -> 4 费一星，4 费二星 -> 5 费一星。
5. 选择不升费时，不进入下一费用阶段，而是获得一件装备。
6. 本地实现沿用“装备”来承载不升费奖励；公开资料中的原始术语包括 Hacking Component / Hacker Mod，不得反向改变本地规则含义。
7. 同一费用阶段的二星特殊选择确认后视为该次触发已消费，不得重复应用同一次选择结果；进入下一费用阶段后，可在新阶段重新按规则达成二星并触发下一段选择。
8. 确认场上 `银狼LV.999` 升费后，商店中已有的 `银狼LV.999` 费用也会同步更新到新的费用阶段。
9. 因为第 8 条是确定规则，触发并确认升费后不应通过刷新商店来“重新猜”；应对当前 session 中的 shop snapshot 应用确定性费用更新。

### 来源边界

公开资料只用于佐证 `银狼LV.999` 的 3/4/5 费链路、二星特殊选择与升费/奖励方向。商店 snapshot 的确定性同步更新、购买到的 `银狼LV.999` 与当前场上阶段一致、以及本地不升费奖励如何映射到装备流程，均以用户确认规则和本地测试为准。

## 身份模型

### 必须区分的两类角色

- 普通 `银狼`：固定角色，与 `银狼LV.999` 无关。
- `银狼LV.999`：同一变费角色，拥有 3/4/5 费阶段。

### 业务身份

Agent 可见与业务决策层应使用：

```text
name=银狼LV.999 cost=<3|4|5> star=<n>
```

而不是使用底层 `role_id` 作为主要身份。

### `role_id` 的边界

`role_id` 只能用于内部资源映射、识别器候选、图标/费用阶段解析或调试诊断。默认文本协议、攻略决策、购买验证、sell_plan 保护与 Agent 推理不得依赖 `role_id` 来表达变费角色阶段。

如果内部数据必须携带 `role_id`，也必须同时保留并输出对 Agent 有意义的 `name` 与 `cost`。不能只给 `role_id`。

## `cost` 字段要求

`cost` 对变费角色是 must-keep fact。

### slots

`cw.slots.read` 对 `银狼LV.999` 必须保留费用阶段。例如：

```text
slot pos=front:1 name=银狼LV.999 cost=4 star=1
```

如果当前识别链路无法确定费用阶段，不得把该角色当作完整可靠事实输出；应保留低置信或待确认提示，要求先看截图或重读。

### shop

shop 中的 `银狼LV.999` 费用应跟随当前已确认的 `银狼LV.999` 进化阶段。购买到的 `银狼LV.999` 与当前场上同一角色处于同一费用阶段。

shop 输出中 `银狼LV.999` 不应只有 `name`，必须能让 Agent 看到费用阶段。例如：

```text
item idx=1 slot=1 name=银狼LV.999 cost=4
```

如果无法从当前已确认阶段确定 shop 中 `银狼LV.999` 的 `cost`，不得输出缺少 `cost` 的 fresh `item`；必须将该事实标记为 stale、uncertain 或 low-confidence，或者要求先读截图/重新确认。不得静默省略 `cost`。

## 状态转移与缓存规则

### 升费选择触发点

升费不是购买瞬间自动发生，也不是达成二星瞬间自动发生；它只在二星 `银狼LV.999` 上场并触发特殊选择时发生。

### 选择升费

确认选择升费后：

1. 场上对应 `银狼LV.999` 更新为下一费用阶段的一星。
2. 当前 session 中 shop snapshot 内所有 `name=银狼LV.999` 的 item 费用同步更新到新费用阶段。
3. 不需要刷新商店。
4. 不应把 shop 标记为 stale 来规避建模；应应用确定性状态转移。

### 选择装备不升费

确认选择装备且不升费后：

1. `银狼LV.999` 费用阶段不变。
2. shop snapshot 中 `银狼LV.999` 费用不变。
3. 装备事实按已有装备流程处理。

## 购买验证影响

普通固定费用角色仍可使用现有的星级等价数量模型：

```text
1星=1, 2星=3, 3星=9
```

`银狼LV.999` 不能直接套用这个模型作为完整验证。原因：它的二星上场选择可能把角色转入下一费用阶段的一星，使星级与费用发生非单调状态转移。

对 `cw.shop.buy_slot`，购买 `银狼LV.999` 的验证应关注：

1. 购买前 shop item 是 `name=银狼LV.999 cost=<current_phase>`。
2. 购买后 slots 中相同 `name` 与当前 `cost` 阶段的数量/星级变化合理。
3. 如果购买造成二星但尚未上场选择，应记录“可触发选择”状态，而不是提前假设升费。
4. 只有确认上场选择升费后，才执行费用阶段转移与 shop snapshot 同步更新。

## sell_plan 与攻略进度影响

### sell_plan

`银狼LV.999` 的一星 4 费或一星 5 费不能被当成普通“低星单卡”。它代表已经完成一次或两次进化的状态。

sell_plan 必须保护当前攻略需要的 `银狼LV.999` 进化链状态，不能只按 `star=1`、同名数量或普通角色规则降权。

### 攻略进度

`银狼LV.999` 的攻略进度不应仅表达为“还缺 N 张同名卡”。至少需要表达当前费用阶段、当前星级、是否已经达到可触发升费选择，以及选择结果是否已确认。

## 输出协议影响

默认文本协议必须让 Agent 能直接判断 `银狼LV.999` 的费用阶段。

涉及 `银狼LV.999` 的 slots/shop 行应保留：

- `name`
- `cost`
- `star`（slots 中可见时）
- 现有位置字段，例如 `pos` / `idx` / `slot`

不要把 `cost` 当作可省略诊断字段。

`cost` 只能作为既有 `slot` / `item` 实体行上的 `key=value` 字段追加，例如 `slot ... cost=4` 或 `item ... cost=4`；不得新增正文前缀、标题或位置参数来承载 `cost`。

本规格不新增 YAML allowlist。`cw.slots.read`、`cw.shop.scan|status`、`cw.shop.buy_slot` 继续遵守现有 YAML 支持状态。

涉及带截图 success 的命令时，仍必须保持既有截图顺序：先输出 `shot path=...`，紧跟 `info read_image_first=1`，然后才输出标题与实体行。

## 文档与 skill 同步要求

实现本规格时必须同步更新：

- `AGENTS.md`
- `skills/trail-cw-prep/SKILL.md`
- `skills/trail-cw-prep/references/command-surface.md`
- 其他读取 slots/shop/sell_plan/guide 输出的 active skill 文档（若现有内容引用受影响事实）
- 相关 renderer 契约测试
- 相关 CLI stdout / session state 测试
- active skill 文档结构测试，例如 `tests/test_skill_structure.py` 中对应断言

测试同步至少覆盖受影响的 renderer、slots、shop、RPC/session state 与 skill 文档契约。不得把 `docs/superpowers/specs` 或 `docs/superpowers/plans` 作为契约测试来源。

根目录 `README.md` 只有在影响普通安装、用户入口或公开定位时才更新。

## 回滚前置要求

当前工作树中已经存在一轮错误方向的改动，其问题包括：

1. 将 `银狼LV.999` 与普通 `银狼` 放入错误的身份边界讨论。
2. 过度使用 `role_id` 来区分变费角色阶段。
3. 没有以 `name + cost + star` 作为 Agent 可见和业务决策事实。
4. 在 `cw.shop.buy_slot` 购买验证里把变费角色塞进普通 star-equivalent count 模型。

后续实现计划必须把“回滚错误方向改动”列为第一个任务。不得在错误 diff 上继续叠加修正。

后续实现必须使用项目内 git worktree，从干净基线开始，并按 `subagent-driven-development` 逐任务执行。禁止在当前 dirty worktree 上继续叠加实现。

允许的前置处理方案二选一，但最终实现工作都必须落在项目内 worktree 的干净基线中：

1. 在当前 worktree 中只回滚本轮错误方向改动，再创建项目内 worktree 从干净基线重新实现。
2. 直接新建项目内隔离 worktree，从干净基线实现，并保持当前 worktree 不再追加改动。

## 状态边界与本轮范围

本规格不要求实现二星 `银狼LV.999` 触发特殊选择的 UI 自动化。实现范围只要求在选择结果已确认后，将 `name + cost + star`、session 状态与 shop snapshot 做确定性同步。

后续计划必须为 `可触发选择` 与 `已确认选择结果` 定义明确的 session/命令返回字段，避免用隐式推断驱动 shop snapshot 更新。

`已确认升费` 只能来自明确命令结果或 session 状态，不得因为购买、达成二星、角色数量变化或 `role_id` 变化而自动推断。

## 主要实现落点

后续计划至少需要检查这些实现与测试面：

- `trail/output/rendering.py`
- `trail/daemon/cw_service.py`
- `trail/scenes/cw/slots.py`
- `trail/scenes/cw/shop.py`
- `trail/scenes/cw/guide.py`
- `trail/scenes/cw/catalog.py`
- `tests/test_output_rendering.py`
- `tests/test_cw_slots.py`
- `tests/test_cw_shop.py`
- `tests/test_cw_rpc_contracts.py`
- `tests/test_skill_structure.py`

推荐实施顺序：先清理错误 diff 并建立项目内 worktree；再写身份边界、`cost` 输出与 shop 未知费用路径的红灯测试；然后实现最小 cost 阶段模型、shop snapshot 确定性同步、购买验证、sell_plan/guide 进度；最后更新 AGENTS、active skills 与契约测试并运行验证。

## 验收标准

1. `银狼` 与 `银狼LV.999` 不会在 canonicalization、fuzzy matching、攻略匹配、shop priority、购买计数或 sell_plan 保护中互相等价。
2. `银狼LV.999` 在 slots/shop 默认文本中保留费用阶段。
3. `role_id` 不作为 Agent 可见的主要业务身份。
4. 确认升费后，shop snapshot 中已有 `银狼LV.999` 的费用确定性更新，不依赖刷新商店。
5. 选择装备不升费时，不更新费用阶段。
6. shop 中 `银狼LV.999` 的 `cost` 未知时，不输出缺失 `cost` 的 fresh `item`。
7. `cw.shop.buy_slot` 对普通角色保留原有购买验证；对 `银狼LV.999` 不使用普通 star-equivalent count 作为完整验证。
8. sell_plan 不会把已升费的一星 `银狼LV.999` 当成低价值普通一星角色。
9. Targeted 与 full pytest 通过；CLI 表面验证覆盖 help、错误路径与 `cw.shop.buy_slot` 默认文本输出。

## 已参考资料

- BWIKI：`货币战争/银狼LV.999`，`https://wiki.biligame.com/sr/货币战争/银狼LV.999`
- Fandom：`Silver Wolf LV.999 (Currency Wars)`，`https://honkai-star-rail.fandom.com/wiki/Silver_Wolf_LV.999_(Currency_Wars)`
- MEmu：4.2 货币战争更新说明，`https://www.memuplay.com/blog/honkai-star-rail-currency-wars-4-2.html`
- Reddit：3-star 5-cost SW999 攻略讨论，作为社区辅助证据使用，链接需在计划阶段复核稳定 permalink

这些公开资料确认变费链路的大方向；商店已有角色费用会同步更新这一点以用户提供的实机/规则确认为准。
