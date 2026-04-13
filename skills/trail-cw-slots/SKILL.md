---
name: trail-cw-slots
description: Use when an agent needs to inspect or mutate Currency Wars field, reserve, or hand slots with explicit slot commands.
---

# Skill: trail-cw-slots

## 输入

- `session_id`

## 职责

- 读取前台、后台、手牌快照
- 执行 `swap`、`place-one`、`collect`、`sell`
- 不决定整体阵容策略，只执行显式动作

## 标准流程

1. 读取槽位：`trail cw slots read --session <id>`
2. 如需收水晶：`trail cw crystals collect --session <id>`
3. 如需换位：`trail cw slots swap --session <id> --source <src> --target <dst>`
4. 如需从手牌上场：`trail cw slots place-one --session <id> --source <src> --target <dst>`
5. 如需卖牌，先看建议：`trail cw hand sell-plan --session <id>`
6. 真正出售时显式执行：`trail cw hand sell-one --session <id> --slot <n>`

## 执行规则

- `swap` 和 `place-one` 必须显式给出源位置与目标位置
- `sell-plan` 只提供建议，不会直接出售
- 任何修改槽位的动作后，如果主 skill 还需要最新布局，应重新执行 `trail cw slots read --session <id>`
- 这个 skill 只执行局部操作，不负责决定整局阵容路线
