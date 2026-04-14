---
name: trail-cw-events
description: Use when an agent needs to handle boss preview, special events, battle continuation, settle paging, or game over transitions in Currency Wars.
---

# Skill: trail-cw-events

## 输入

- `session_id`

## 职责

- 处理 Boss 预览、特殊事件、结算翻页与战斗继续
- 不负责商店、补给和整局循环
- 依赖主 skill 结合截图判断当前是不是该调用这里的命令，不假设 CLI 已内建穷尽识别

## 标准流程

1. 强敌预览阶段：`trail cw boss-preview confirm --session <id>`
2. 特殊事件阶段：`trail cw event handle --session <id>`
3. 准备进入战斗时：`trail cw battle start --session <id>`
4. 战斗结束继续时：`trail cw battle continue --session <id>`
5. 结算翻页时：`trail cw settle next --session <id>`
6. 如果主 skill 判断当前是 `game_over`，把结果交回主 skill 结束整局，不在这个子 skill 内部自行控制循环

## 执行规则

- 每次事件命令后都把 `stage` 视为失效，交回主 skill 重新识别
- `trail cw event handle` 的返回至少要看 `event_type` 和 `handled_action`
- 这个 skill 负责固定事件处理，不负责决定何时购物、何时补给、何时换阵容，也不负责决定何时退出整局
- 如果事件截图和 `event_type` / `handled_action` 不一致，以截图为准，并由主 skill 决定下一步是否重试或改走别的命令
- 即使 CLI 提供了 `event_type`，也不要假设它已经穷尽所有事件分支；必要时直接根据截图做多模态判断
