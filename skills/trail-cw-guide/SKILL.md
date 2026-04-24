---
name: trail-cw-guide
description: 当用户想先为《崩坏：星穹铁道》的货币战争选攻略、定攻略，或在开局前比较不同攻略路线时使用。
---

# Skill: trail-cw-guide

## Role

- `trail-cw-guide` 是货币战争的攻略选择入口 skill，不是整局 owner。
- 它负责把“先选攻略 / 先定攻略”这类请求收束成可执行的筛选与读取步骤，再把真正开局交回场景入口。

## When To Use

- 用户直接说“帮我选货币战争攻略”“先定攻略”“想挑一套路线”。
- 用户已经明确要按攻略优先开局，或想先比较不同攻略再决定这一局是否值得玩。
- 纯 control-plane 恢复、纯参数解释、或泛化成“继续玩星铁”的请求，不进入本 skill。

## What To Confirm First

- 这一把的目标是什么：上分、周常奖励，还是某个特定目标。
- 有没有必须顺带完成的羁绊或成就。
- 当前要跟哪一版攻略对齐，是否需要先排除旧版本内容。
- 是否已经有偏好的投资环境，还是要反过来让攻略帮助决定投资环境。
- 更想围绕哪个主C或阵容倾向来选路线。

## Command Surface

- `guide list cw`：先筛候选攻略，适合按投资环境、羁绊或角色方向缩小范围。
- `guide fetch cw`：在候选里读取完整攻略内容，确认标签、阵容、运营思路与当前目标是否一致。
- `guide.fetch.cw --select`：在确认候选后，把攻略记录到当前 session，作为当前攻略；这是从选攻略切回入口前的关键一步。
- `cw guide current`：只用来回看当前已选攻略摘要，不是“apply 之后才有”的状态查询。
- `cw guide apply`：仅当需要手动兜底时才用；正常流程应回到 `trail-cw-entry`，真正进入游戏后由 `cw.portal.select` 成功时自动应用当前已选攻略，只有这条链路失效时才轮到它。

## Workflow Handoff

- 用户直接点名“帮我选货币战争攻略”时，可以直接命中这个 skill。
- 如果 `trail-cw-entry` 的确认结果是“攻略优先”或“先定攻略”，就把后续推荐切到本 skill。
- 如果这是从 `trail-cw-entry` handoff 过来的，且上游已经确认了目标、羁绊/成就或环境偏好，就默认继承这些结论，只追问缺失项。
- 一旦攻略已经选定，就先执行 `guide.fetch.cw --select` 记录当前攻略，再把控制权交回 `trail-cw-entry`，继续 `cw enter` / `cw start`。
- 返回开局链路后，`cw.portal.select` 成功时会自动应用当前已选攻略；只有这条链路失效时，才退回 `cw guide apply` 手动兜底。

## Reference Map

- `references/guide-selection-criteria.md`：选攻略前要看什么，以及不同目标下应该优先比较哪些维度。
- `references/command-surface.md`：攻略相关命令家族的定位，以及为什么 `apply/current` 不是第一步。
- `references/confirmation-checklist.md`：选攻略前必须问清的关键信息，避免一上来只看命令名。
