---
name: trail-cw-replenish
description: Use when an agent needs to read or choose replenish, invest, encounter, or fortune options for a Currency Wars session.
---

# Skill: trail-cw-replenish

## 输入

- `session_id`

## 职责

- 读取并选择补给、投资、遭遇、命运卜者选项
- 一次只处理当前阶段
- 不负责阶段切换和整局循环

## 标准流程

1. 根据当前阶段选择读取命令：
   - 补给：`trail cw replenish read --session <id>`
   - 投资：`trail cw invest read --session <id>`
   - 遭遇：`trail cw encounter read --session <id>`
   - 命运卜者：`trail cw fortune read --session <id>`
2. 根据显式策略选择一个选项：
   - `trail cw replenish choose --session <id> --option <n>`
   - `trail cw invest choose --session <id> --option <n>`
   - `trail cw encounter choose --session <id> --option <n>`
   - `trail cw fortune choose --session <id> --option <n>`
3. 动作完成后，把控制权交回主 skill 重新识别阶段

## 执行规则

- 先 `read`，再 `choose`，不要直接盲点
- `--option` 必须显式给出
- `choose` 之后把 `stage` 视为失效，回到 `trail-cw` 重新 `stage detect`
- 这个 skill 不处理商店、编队和战斗逻辑
