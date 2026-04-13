---
name: trail-cw-events
description: Use when an agent needs to handle boss preview, special events, battle continuation, settle paging, or game over transitions in Currency Wars.
---

# Skill: trail-cw-events

## 输入

- `session_id`

## 职责

- 处理 Boss 预览、特殊事件、结算翻页与战斗继续
- 在 `game_over` 时停止对局
- 不负责商店、补给和整局循环

## 标准流程

1. 强敌预览阶段：`trail cw boss-preview confirm --session <id>`
2. 特殊事件阶段：`trail cw event handle --session <id>`
3. 准备进入战斗时：`trail cw battle start --session <id>`
4. 战斗结束继续时：`trail cw battle continue --session <id>`
5. 结算翻页时：`trail cw settle next --session <id>`
6. 如果 `trail cw stage detect --session <id>` 返回 `game_over`，停止循环并把结果交回主 skill

## 执行规则

- 每次事件命令后都把 `stage` 视为失效，交回主 skill 重新识别
- `trail cw event handle` 的返回至少要看 `event_type` 和 `handled_action`
- 这个 skill 负责固定事件处理，不负责决定何时购物、何时补给、何时换阵容
