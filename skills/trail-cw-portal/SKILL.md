---
name: trail-cw-portal
description: 当上游已经用 `cw start` 成功进入货币战争投资环境页，并且需要判断这一页接下来该如何行动、是否刷开局或是否切到攻略无人值守时使用。
---

# Skill: trail-cw-portal

## Role

- `trail-cw-portal` 是货币战争投资环境页的 internal 跟进 skill，不是 direct-user 公共入口，也不是 scene entry、不是 owner。
- 它只在 `cw start` 或 `portal refresh` 成功把流程推进到投资环境页之后接手，负责解释这一页接下来怎么行动。
- 它的核心职责不是先选环境再单独补攻略，而是在当前环境页联合决策“选哪套攻略 + 选哪个环境”。

## When To Use

- 上游已经进入投资环境页，用户要看当前环境卡片、判断值不值得刷开局，或继续决定这一页该怎么点。
- 用户已经确认 `环境优先`，想继续按环境页往下走。
- 当前还没确定攻略，但投资环境页已经出来，需要先解释这一页的行动逻辑。
- 纯 direct-user 的“我要玩货币战争”、纯“帮我选攻略”、或还停留在货币战争首页的请求，不进入本 skill。

## How To Act On The Portal Page

- 先用 `portal detect` 看当前可见环境卡片；需要刷新候选时用 `portal refresh`；确认整把不要时用 `portal restart`；决定继续当前环境时用 `portal select --card-idx ...`。
- `待收集=1` 通常表示选择这个投资环境更有利于完成当前收集奖励；除非它和已知目标明显冲突，否则不要轻易忽略。
- 环境页里的判断要先综合每张卡自带的推荐攻略、热度、版本和 `待收集=1`，再决定这一页是继续、refresh 还是切攻略。
- 如果上游已经定了 `环境优先`，就继续按环境走，比较当前环境是否服务目标，再决定 refresh、restart 还是 select。
- 如果允许刷开局，就把 `refresh` / `restart` 当成自动筛选手段，持续刷到出现接近攻略或明显服务目标的环境。
- 如果当前还没定攻略，就先切到 `trail-cw-guide` 的无人值守模式，让它在当前环境页上下文里挑出最合适的攻略，再回到 portal 流程决定选哪个环境。
- 如果不允许刷开局，仍可先做一次 refresh；只有在已确认 `环境优先` 时，portal 才在当前可见环境里选最贴近目标的一项；如果未确定攻略，则必须切到 `trail-cw-guide` 的无人值守模式，不由 portal 直接拍板。
- 只有当没有带 `待收集=1` 的环境，或所有推荐攻略互动数据都 < 5000 时，才允许先做一次 refresh，然后再综合选择；`版本过旧` 只作为权衡因素，不设硬阈值。
- `cw.portal.select` / `portal select --card-idx ...` 成功后会输出 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`，并自动收集水晶、stage/slots/equipment/shop 预备事实并关闭商店；装备读取发生在 slots fresh 后、shop open 前；先读原始截图，再参考输出中的 stage/slots/equipment/shop facts，不要手动再跑 slots/equipment/shop 初始扫描，按 handoff 切到 `trail-cw-prep`；如果 auto-collect 失败并输出 recover/taint 或失败状态，先按恢复语义处理，不要继续假设已进入 prep 并操作商店。装备读取失败仅输出 `CW_EQUIPMENT_AUTO_COLLECT_FAILED` soft warning，不阻止 shop 收集或 final handoff。消费 `cw.portal.select` 带截图 success 时必须先读原始截图；`# ` 行只是板块标题，不是 action/prefix/fact，Agent 只消费实体行。读完截图后，再消费这些标题下的事实：`# 综合信息` 下看 stage/status，`# 攻略提示` 下看 skill_info，`# 角色信息` 下看 slot，`# 羁绊信息` 下看 trait summary，`# 装备信息` 下看装备背包 item/summary info，`# 装备优先级` 下看装备推荐 guide，`# 角色装备需求` 下看角色装备需求 slot/info，`# 商店信息` 下看 item/coins/reserve facts。

## When To Hand Off To trail-cw-guide

- 如果未确定攻略，但投资环境页已经出来，就切到 `trail-cw-guide` 的无人值守模式，让它按当前环境页里的推荐攻略、热度、版本、`待收集=1` 和其他已知限制自动定攻略。
- 如果用户明确说“按当前环境直接定攻略”，也切到 `trail-cw-guide`，不要在这里继续追问。
- 选定攻略后，回到 portal 流程，用 `portal select --card-idx ...` 选中对应投资环境，再继续后续流程。
- 选中环境并成功进入普通备战后，后续不再由 portal 继续 owner，而是消费 `cw.portal.select` 已带回的备战事实，并按 `cw.portal.select -> trail-cw-prep` 的 handoff 转入普通备战阶段。

## Reference Map

- `references/portal-command-surface.md`：投资环境页里 `portal detect/refresh/restart/select` 的分工和使用时机。
- `references/portal-selection-rules.md`：这一页该怎么选环境、怎样理解 `待收集=1`、以及何时切到攻略无人值守。
- `references/portal-refresh-policy.md`：允许刷开局和不允许刷开局时，各自该怎样处理 refresh/restart/select。
