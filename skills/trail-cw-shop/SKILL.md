---
name: trail-cw-shop
description: Use when an agent is in the Currency Wars shop stage and needs to scan, buy, refresh, or close the shop with explicit commands.
---

# Skill: trail-cw-shop

## 输入

- `session_id`

## 职责

- 调用 `trail cw shop *` 命令
- 执行显式购买、刷新、开关商店
- 不做整局调度，也不决定长期阵容策略

## 标准流程

1. 必要时先打开商店：`trail cw shop open --session <id>`
2. 扫描当前商店：`trail cw shop scan --session <id>`
3. 如需购买，使用显式槽位和角色名：`trail cw shop buy-slot --session <id> --slot <n> --expect <角色>`
4. 如需刷新，只调用：`trail cw shop refresh --session <id>`
5. 离开前可执行：`trail cw shop close --session <id>`

## 执行规则

- 先读 `scan` 返回的金币、等级、备战位状态，再决定是否继续动作
- `buy-slot` 必须显式指定 `--slot` 和 `--expect`
- 如果返回 `reserve_full` 或 `needs_reserve_clear`，把控制权交回主 skill 或 `trail-cw-slots` 处理卖牌，不在这里私自决定卖谁
- 每次购买或刷新后，优先使用最新返回结果，必要时重新 `scan`
