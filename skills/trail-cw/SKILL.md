---
name: trail-cw
description: Use when an agent needs to orchestrate a full Currency Wars run by looping on stage detection and dispatching to Trail sub-skills.
---

# Skill: trail-cw

## 职责

- 编排一整局货币战争
- 在关键阶段切换到攻略、编队、商店、补给、事件子 skill
- 负责循环、阶段切换和失败恢复，不重写 CLI 原子动作

## 输入

- `session_id`
- `guide_url` 或已存在的攻略 artifact
- 开局模式：`new` 或 `continue`

## 验收清单

1. 创建 session
2. 进入货币战争
3. 拉取并应用攻略
4. 循环：stage detect -> 分发到 slots/shop/replenish/events 子 skill
5. 直到 settle next / game over

## 标准流程

1. `trail session create`
2. `trail cw enter --session <id> --mode new|continue`
3. `trail guide fetch cw <url>`
4. `trail cw guide apply --session <id> --guide <artifact>`
5. 循环执行：
   - `trail cw stage detect --session <id>`
   - 根据 `data.value` 分发：
     - `preparation` 或需要调整编队时，切到 `trail-cw-slots`
     - `shop` 时，切到 `trail-cw-shop`
     - `replenish`、`invest`、`encounter`、`fortune` 时，切到 `trail-cw-replenish`
     - `boss_preview`、`event`、`settle`、`game_over` 或战斗衔接时，切到 `trail-cw-events`
   - 每次动作后优先消费当前命令返回的 `screenshot` 与 `data`，不要假设旧状态仍然有效
6. 在 `settle` 阶段执行 `trail cw settle next --session <id>` 后继续下一轮识别；在 `game_over` 后退出

## 执行规则

- 攻略拉取和攻略应用分两步，不能隐式复用“上一条 fetch 结果”
- 所有 `trail cw ...` 命令都必须显式传入 `--session <id>`
- 阶段切换由这个 skill 决定，子 skill 不负责整局调度
- 如果当前动作让 `stage` 失效，立刻回到 `trail cw stage detect --session <id>`
