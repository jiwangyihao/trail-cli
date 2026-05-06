---
name: trail-cw-event-unknown
description: 当 `cw.event.handle` 返回 `event_type=unknown` 且需要手工处理未知事件时使用。
---

# Skill: trail-cw-event-unknown

## Role

- `trail-cw-event-unknown` 是货币战争未知事件的 active internal 手工处理 skill，不是 scene entry、不是 direct-user、不是 owner。
- 它只在 `cw.event.handle` 已确认 `event_type=unknown`、`handled=0` 或 `next_action=manual` 后接手，目标是让 Agent 先看截图，手工处理未知事件，再用 reconcile 恢复可验证事实。
- 它不得直接或间接调用 archive skill；未知事件恢复只能通过当前 active command、截图和 `cw.event.reconcile` 完成。

## When To Use

- `cw.event.handle` success 或 failure 明确输出 `event_type=unknown`、`next_action=manual`，并带 `handoff_reason=event_unknown_manual_required`。
- 上游 active skill 已经停在未知事件页，且命令输出要求人工决策或人工点击。
- 只处理未知事件手工恢复边界；普通备战、投资环境页、首页入口、daemon 恢复层不由本 skill 接管。

## Required First Actions

- 若上一条输出有 `shot path=...` 和 `info read_image_first=1`，必须先读原始截图；不要只凭压缩文本判断未知事件选项。
- 读取 `why`、`warn`、`ref`、`info next_action=manual`、`stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles` 和 `crystals_stale=0`，确认哪些事实已被标记 stale。
- 如果看到 `CW_EVENT_LV999_STATE_STALE`，不要混淆 `银狼LV.999` 与普通 `银狼`；后续必须等 reconcile 或补扫恢复 cost / star / slot 事实后再做买卖判断。

## Manual Resolution Loop

1. 先读截图，识别未知事件页面的可见按钮、选项文字和当前阶段。
2. 手工处理未知事件：只执行截图中明确可见、能推进事件离开的动作；不发明通用事件策略。
3. 处理后再次观察页面或运行当前命令建议的下一步，直到未知事件页离开或 CLI 可以确认新阶段。
4. 不在本 skill 内继续普通备战经营；未知事件离开后进入 reconcile 合同。

## Reconcile Contract

- 手工处理未知事件后运行 `trail cw event reconcile --session <id>`。
- reconcile 用于解除或确认 `stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles`；`crystals_stale=0` 表示晶矿不因 unknown 路径自动 stale。
- 如果 reconcile 返回普通备战或商店阶段，并且仍提示 `strategy`、`sell_plan`、slots、shop 或 equipment stale，按输出补跑对应读命令；需要普通备战判断时再交回 `trail-cw-prep`。
- 如果 reconcile 仍返回 unknown、tainted、recover 或 request id，按输出协议继续，不自行升级或猜测 owner。

## Stop Conditions

- 未读截图时停止，不进行未知事件判断。
- 事件仍是 unknown 且没有可见安全动作时停止，交回用户或按 CLI `recover` / `request` 指示处理。
- `trail cw event reconcile --session <id>` 已确认进入普通备战、投资环境页、首页或恢复层后，本 skill 停止，由对应 active skill 或命令输出接管。

## Reference Map

- `references/manual-resolution-guide.md`：未知事件手工处理、截图优先和 reconcile 合同。
- `evals/triggers.json`：本 skill 与 prep、entry、portal、advanced 的触发和竞争边界。
