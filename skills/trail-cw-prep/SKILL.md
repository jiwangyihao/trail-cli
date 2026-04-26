---
name: trail-cw-prep
description: 当上游已经进入货币战争普通备战阶段，并且需要先收集阶段、槽位、商店、晶矿或出战前事实时使用。
---

# Skill: trail-cw-prep

## Role

- `trail-cw-prep` 是货币战争普通备战阶段的 active internal 跟进 skill，不是 scene entry、不是 direct-user 公共入口、不是整局 owner。
- 它只负责事实收集、安全边界、命令地图和待讨论决策点，不写具体经营策略。
- 它不得直接或间接调用 archive skill；`cw.shop.*`、`cw.slots.*`、`cw.hand.*` 只是 CLI command family，不是旧 skill。

## When To Use

- `cw.portal.select` 成功并返回 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。
- 上游已经明确确认当前在普通备战、商店或槽位阶段，需要先收集事实。
- 货币战争首页、投资环境页、攻略选择页、特殊事件页、BOSS 前备战、结算和 game over 不进入本 skill 自治。

## Stage Boundaries

- 普通备战和商店阶段可以继续收集事实。
- 补给、投资、遭遇、命运卜者、通用事件、投资策略页交给后续专用 skill 或上游。
- BOSS 前备战交给后续单独 skill。
- unknown、tainted、request-status、daemon 恢复问题停止自治，保留事实并交回上游 scene entry；本 skill 不能直接 handoff 到 `trail-hsr-advanced`。

## Required First Actions

- 若上一条命令输出 `shot path=...` 和 `info read_image_first=1`，必须先读取原始截图。
- 若上一条 `cw.portal.select` success 输出含 `info skill_info=运营思路 text=...`，必须先把它读作当前攻略的动态提醒；它不是已解析策略，必须不发明默认优先级。
- 若上一条 `cw.portal.select` success 输出含 `slot`、`item` 或 `info stage_` 事实，说明它可能已经提供最新 slots/shop/stage 快照；必须先读截图，再用这些文本事实制定第一步备战动作。
- 只有事实缺失、stale 或页面已变化时，才主动调用 `trail cw slots read` 或 `trail cw shop scan` 刷新；不要在接收 handoff 后立刻重复扫描。
- 不确定阶段时先用 `trail cw stage detect --session <id>` 或 `trail cw stage wait --session <id>`。
- 如果截图和结构化文本冲突，以截图为准并重新读取相关事实。

## Command Surface

- `trail cw stage detect|wait`：确认当前 CW 阶段。
- `trail cw slots read`：读取前台、后台、手牌和羁绊摘要。
- `trail cw shop scan|status|buy-slot|buy-exp|refresh|close`：读取和执行商店动作。
- `trail cw crystals collect`：执行晶矿收集动作，但是否执行属于后续策略决策。
- `trail cw hand sell-plan|sell`：读取或执行卖牌动作。
- `trail cw battle run --timeout 570`：执行出战和战斗链，但是否出战属于后续策略决策。

## Autonomy Boundary

- 本轮可自治的只有读图、阶段确认、只读事实收集、输出协议判断和安全恢复。
- 买角色、买经验、刷新、卖牌、上场、换位、收晶矿、出战等 mutation 只能在用户明确指示或后续策略设计给出决策后执行。

## Decision Points Pending Strategy

- 本 skill 只列出待讨论主题，不提供默认优先级。
- 需要讨论的主题见 `references/decision-points-pending-strategy.md`。

## Stop Conditions

- mutation 结果未知、session tainted、request-status 未确认时停止自治。
- 特殊事件、BOSS 前备战、结算、game over、unknown 阶段停止自治。
- 当前攻略缺失或不完整时，不继续推进依赖攻略的动作。
- daemon 恢复问题停止并交回上游 scene entry，不直接升级恢复层。

## Reference Map

- `references/command-surface.md`：普通备战阶段可用命令和边界。
- `references/stage-boundaries.md`：普通备战与其它阶段的分界。
- `references/decision-points-pending-strategy.md`：后续策略讨论主题清单。
