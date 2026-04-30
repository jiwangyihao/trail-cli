# Portal Command Surface

这里讲的是投资环境页上的动作分工，不是参数手册。

## 进入前提

- 只有在 `cw start` 或 `portal refresh` 已成功把流程推进到投资环境页之后，才进入这一组动作。
- 这一步是投资环境页内的跟进行为，不是总入口的启动阶段。

## 当前页动作

- `portal detect`：回读当前可见的环境卡片，适合先弄清这一屏现在有哪些候选。
- `portal refresh`：保留当前开局流程，只刷新这一屏可见的候选环境。
- `portal restart`：放弃当前这把开局，重新开始一把新的投资环境页筛选；只有在用户允许刷开局时才优先考虑。
- `portal select --card-idx ...`：确认当前选中的投资环境，继续这一把后续流程。

## 基本定位

- `detect` 负责看清现状，`refresh` 负责继续筛，`restart` 负责重开，`select` 负责定案。
- 先判断这一页是不是已经有足够好的环境，再决定要不要 refresh、restart 或直接 select。

## select 成功后的首帧事实

- `cw.portal.select` / `portal select --card-idx ...` 成功后仍 handoff 到 `trail-cw-prep`，并自动收集 stage/slots/equipment/shop；装备读取发生在 slots fresh 后、shop open 前。带截图 success 必须先读原始截图；`# ` 行只是板块标题，不是 action/prefix/fact，Agent 只消费实体行。事实分组按 `# 综合信息`、`# 攻略提示`、`# 角色信息`、`# 羁绊信息`、`# 装备信息`、`# 装备优先级`、`# 角色装备需求`、`# 商店信息` 消费；装备失败只作为 `CW_EQUIPMENT_AUTO_COLLECT_FAILED` soft warning，不阻止 shop 收集或 final handoff。
