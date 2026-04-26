# `cw.hand.sell_plan` 参考化设计

## 背景

CLI 命令 `trail cw hand sell-plan` 的 canonical command 是 `cw.hand.sell_plan`。当前实现语义存在根本问题：它只看 `slots.hand` 中是否非空，然后把所有非空手牌都放进 `candidates`。这会让命令在真实对局里几乎不可用，因为它没有区分攻略角色、阶段过渡角色、最终阵容角色、超买角色，也没有输出足够信息供 Agent 判断。

之前尝试用 `guide.on_field`、`guide.off_field`、`guide.remaining_purchases` 做保护集合，但这个方向不可靠：

- `on_field` / `off_field` 当前只是从攻略最终阶段派生出来的字段，不表达前期、中期、后期阶段差异。
- `remaining_purchases` 只是购买命令维护的待买计数，不是权威攻略定义；手动购买、事件赠送、初始角色、重复持有、出售后回补都会让它偏离真实局面。
- `sell-plan` 不应该被视为权威售出计划，而应该是给 Agent 的参考信息。

因此本轮设计目标是把 `cw.hand.sell_plan` 改为阶段化、参考化、可解释的输出，并同步清理 session guide 中不可靠的派生字段。

## 目标

1. session 中不再保存 `guide.on_field`、`guide.off_field`、`guide.remaining_purchases`。
2. session guide 只保存攻略原始/规范化字段，尤其保留 `role_stages` 中前期、中期、后期/Final 的角色卡信息。
3. `cw.hand.sell_plan` 基于当前 `slots.read` 持久化的角色名称和星级生成参考项。
4. `cw.hand.sell_plan` 对每个手牌角色输出分类、推荐度、原因和 todo token，而不是只输出不可解释的候选数量。
5. 只有 Final 中并且没有超买的角色被完全保护。
6. 缺少阶段、BOSS 前标记、人口等暂时还不能稳定获取的信息时，输出 TODO，并保持“只给参考”的语义。

## 非目标

本轮不做：

- 不把 `sell-plan` 升级为自动出售计划。
- 不新增 `trail cw hand sell-plan --execute` 或任何自动卖牌动作。
- 不实现 `battle.run` / 其他命令的完整阶段持久化链路；只消费已有或后续会持久化的字段。
- 不新增 YAML allowlist。
- 不新增默认正文前缀；继续使用现有 `slot` / `info` / `warn` / `ref` 等协议前缀。
- 不依赖 `remaining_purchases` 作为攻略角色定义。

## 数据模型

### Guide Session State

`apply_cw_guide()` 写入 session 时，`cw_state["guide"]` 不再包含：

- `on_field`
- `off_field`
- `remaining_purchases`

保留字段包括：

- `artifact`
- `lineup_id`
- `share_code`
- `source_url`
- `title`
- `author`
- `uploader`
- `labels`
- `support_hard`
- `has_change_equip`
- `has_expert`
- `version`
- `role_stages`
- `first_fight_augments`
- `second_fight_augments`
- `portals`
- `order_basic`
- `order_compose`

`constraints` 继续保留 `min_coins`、`min_level`、`mid_level`、`priority`、`positioning`。这些属于攻略约束，不属于购买剩余状态。

### Role Stages

`role_stages` 是攻略角色定义的唯一来源。每个阶段包含：

- `stage`
- `front_roles`
- `back_roles`

角色卡至少使用：

- `name`
- `star`
- `rarity`
- `is_carry`
- 装备字段如果存在则继续保留，但 `sell-plan` 不依赖装备判断。

## 阶段归类

`sell-plan` 从 `role_stages` 生成三个阶段集合：

- `前期`
- `中期`
- `后期`

归类规则：

1. `stage == "Final"` 的阶段永远视为 `后期` 和最终目标。
2. 如果没有 `Final`，最后一个阶段作为最终目标，同时输出 `todo=missing_final`，提示攻略缺少显式 Final。
3. 第一个非 Final 阶段归为 `前期`。
4. 最终阶段之前的中间阶段归为 `中期`。
5. 如果阶段数量不足以区分前/中/后，按能确定的顺序归类，并输出 `todo=stage_granularity`，提示阶段粒度不足。

同名角色可能出现在多个阶段。分类优先级按“最早出现阶段”用于出售排序：前期角色优先于中期角色，中期角色优先于后期角色。Final 的目标星级仍以最终阶段角色卡为准。

## 当前局面来源

`sell-plan` 只消费 session 中已有快照：

- `cw_state["slots"]`：来自 `trail cw slots read`，包含 `front`、`back`、`hand`、`stale`，当前应有角色 `name` 和 `star`。
- `cw_state["shop"]["team_size"]`：人口信息，当前可能来自商店扫描；后续其他命令可更稳定地持久化。
- `cw_state["stage"]`：当前小节/层数/BOSS 前标记，后续由 `battle.run` 等稳定识别命令持久化。

如果 `slots.stale != False`，继续返回 `SLOTS_STALE`，要求先读槽位。

## 完全保护规则

完全保护只适用于 Final 角色中“没有超买”的部分。

定义：

- Final 目标星级来自 Final 阶段中同名角色卡的 `star`，输出 item 中命名为 `target_star`。
- 当前场上星级来自 `slots.front` 和 `slots.back` 中同名角色的最大 `star`，输出 item 中命名为 `current_star`。
- 如果 Final 角色不在场上，则该角色视为未达成目标，手牌中的同名角色完全保护。
- 如果 Final 角色在场上，但场上星级 `<` 目标星级，则手牌中的同名角色完全保护。
- 如果 Final 角色在场上且场上星级 `>=` 目标星级，则手牌中的同名角色属于 `后期超买`，可进入参考列表。

如果某个 Final 角色缺少目标星级、目标星级不在可比较范围内，或当前槽位缺少该角色星级，无法确认超买时，归类为 `后期`，标记 `protected=1`，输出 `todo=star`，不把它标为可卖候选。

## 分类与排序

手牌中每个有名称的角色都生成一个参考 item。

分类规则：

1. 不在任何 `role_stages` 里的角色：`非攻略`。
2. 在前期阶段出现，且不属于 Final 未达标保护：`前期`。
3. 在中期阶段出现，且不属于 Final 未达标保护：`中期`。
4. 在后期/Final 阶段出现，且未超买：`后期`，`protected=1`。
5. Final 角色已达目标星级后，手牌中的同名角色：`后期超买`。

排序优先级：

1. `非攻略`
2. `前期`
3. `中期`
4. `后期超买`
5. `后期`

同一分类内按手牌槽位升序稳定排序。

## 推荐度规则

推荐度只有三个等级：

- `推荐`
- `可以`
- `不推荐`

推荐度不是强制动作，只表示 Agent 参考强弱。

### 人口不足保护

当能可靠取得 `team_size` 时：

- 如果 `场上角色数量 + 手牌角色数量 < 人口数量`，所有角色推荐度都降为 `不推荐`。
- 场上角色数量以未来持久化的人口/队伍信息为主；当前尚未有独立持久化动作时，fresh `slots.front/back` 的非空数量作为本轮可用近似来源。

当缺少 `team_size` 时：

- 按用户确认的“只给参考”策略，不输出权威 candidates。
- 输出 `todo=team_size`。
- 可继续展示分类和推荐度，但结果必须标记 `reference_only=1`。

### 阶段推荐表

当前阶段由 `cw_state["stage"]` 提供。期望能识别：

- 第 1 层：`1-x`
- 第 2 层：`2-x`
- 第 2 层 BOSS 前关卡
- 第 3 层：`3-x`
- 第 3 层 BOSS 前关卡

规则：

| 当前阶段 | 非攻略 | 前期 | 中期 | 后期超买 | 后期未超买 |
| --- | --- | --- | --- | --- | --- |
| `1-x` | 可以 | 不推荐 | 不推荐 | 不推荐 | 不推荐 |
| `2-x` | 推荐 | 可以 | 不推荐 | 不推荐 | 不推荐 |
| `2-x` BOSS 前 | 推荐 | 推荐 | 不推荐 | 不推荐 | 不推荐 |
| `3-x` | 推荐 | 推荐 | 可以 | 不推荐 | 不推荐 |
| `3-x` BOSS 前 | 推荐 | 推荐 | 推荐 | 可以 | 不推荐 |

缺少当前阶段时：

- 输出 `todo=stage`。
- 不输出权威 candidates。
- 仍输出分类供 Agent 参考。

缺少 BOSS 前标记时：

- 输出 `todo=boss_preview`。
- 按非 BOSS 前规则给出保守推荐度。

## 输出协议

默认输出首行：

```text
ok cw.hand.sell_plan count=<items> reference_only=1 candidates=<n> todos=<n>
```

其中：

- `count` 是参考 item 数。
- `reference_only=1` 固定表示这不是权威售出计划。
- `candidates` 当前保持 `0`，除非未来明确引入权威候选语义。
- `todos` 是缺失信息数量。

每个参考 item 使用 `slot` 行：

```text
slot pos=hand:<idx> name=<角色名> star=<n> target_star=<n> current_star=<n> 分类=<分类> 推荐度=<推荐|可以|不推荐> priority=<n> protected=<0|1> reason=<说明>
```

缺失信息使用 `info` 行，字段名固定为小写 `todo`：

```text
info todo=stage
info todo=team_size
info todo=boss_preview
```

不新增正文前缀，不新增 YAML allowlist。

## 兼容与迁移

旧 session 或旧 artifact 可能仍含 `on_field` / `off_field` / `remaining_purchases`。本轮实现应忽略这些字段，不再从它们推导 sell-plan。

`guide.fetch.cw` 的结构化 payload 仍可保留 `on_field` / `off_field` 用于兼容外部输出和历史测试，但 `apply_cw_guide()` 不再把它们写入 session guide。后续如要彻底移除 fetch payload 中的派生字段，需要单独评估 README、YAML 契约和 guide.fetch.cw 输出协议。

## 测试要求

至少覆盖：

1. `apply_cw_guide()` 不再写入 `on_field` / `off_field` / `remaining_purchases`。
2. shop 购买不再修改 `remaining_purchases`，shop guide_summary 不再暴露该字段。
3. `sell-plan` 能按 `非攻略 -> 前期 -> 中期 -> 后期超买 -> 后期` 排序。
4. Final 未达目标星级时完全保护。
5. Final 已达目标星级时同名手牌归为 `后期超买`。
6. 缺 stage / team_size / boss_preview 时输出 TODO，并保持 `reference_only=1`。
7. renderer 输出首行、`slot` 行、`info todo` 行顺序稳定。
8. CLI/RPC stdout 契约同步更新。
9. `AGENTS.md` 同步冻结 `cw.hand.sell_plan` 的首行字段、`slot` 行字段和 `info todo=...` 语义。
10. 检查 active skills 是否有卖牌或满员处理说明；如有，更新为“sell-plan 只提供参考，出售仍需显式 `cw.hand.sell`”。

## 风险

- 当前 `team_size` 和 BOSS 前标记并不一定稳定存在，因此本轮必须保留 TODO 语义，不能伪装成权威判断。
- 角色星级识别依赖 `slots.read` 的持久化结果；如果星级缺失，Final 超买判断必须降级。
- 移除 session guide 派生字段会影响依赖 `remaining_purchases` 的 shop summary 测试，需要同步调整。

## 后续工作

后续命令完善后，可以继续增强：

1. 由 `battle.run`、`stage.detect` 或其他稳定命令持久化当前小节、层数和 BOSS 前状态。
2. 持久化更可靠的人口信息，减少对 shop scan 的临时依赖。
3. 在确认足够可靠后，再考虑是否恢复 `candidates` 作为可机器执行候选；本轮不做。
