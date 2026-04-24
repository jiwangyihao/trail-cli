---
name: trail-cw-guide
description: 当用户想先为《崩坏：星穹铁道》的货币战争选攻略、定攻略，或在开局前比较不同攻略路线时使用。
---

# Skill: trail-cw-guide

## Role

- `trail-cw-guide` 是货币战争的 active public 攻略选择入口 skill，不是 scene entry，也不是整局 owner。
- 它负责把“先选攻略 / 先定攻略”这类请求收束成可执行的筛选与读取步骤；`direct-user` / 开局前链路在选定后交回 `trail-cw-entry`，投资环境页里的无人值守链路在选定后交回 `trail-cw-portal`。
- `interactive` / `direct-user` 模式下，它可以继续确认缺失项；从投资环境页内部切入时，它也支持无人值守选择。

## When To Use

- 用户直接说“帮我选货币战争攻略”“先定攻略”“想挑一套路线”。
- 用户已经明确要按攻略优先开局，或想先比较不同攻略再决定这一局是否值得玩。
- 投资环境页内部已经拿到当前环境卡片，需要根据环境自动挑一套最合适的攻略。
- 纯 control-plane 恢复、纯参数解释、或泛化成“继续玩星铁”的请求，不进入本 skill。

## What To Confirm First

- 如果这是从 `trail-cw-entry` handoff 过来的，就继承上游已确认的目标、羁绊/成就与其他限制，只追问缺失项。
- `interactive` / `direct-user` 模式下，可以继续确认这一把的目标是什么：上分、周常奖励，还是某个特定目标。
- `interactive` / `direct-user` 模式下，可以继续确认有没有必须顺带完成的羁绊、成就或其他限制。
- `interactive` / `direct-user` 模式下，可以继续确认当前要跟哪一版攻略对齐；版本越新越好，但不是自动排除旧版本。
- `interactive` / `direct-user` 模式下，可以继续确认是否已经有偏好的投资环境，以及主C或阵容倾向等限制。
- 从投资环境页内部切入时，进入无人值守模式，不再继续追问，而是根据当前投资环境、`待收集=1`、热门度、版本等信号自动选攻略。

## Command Surface

- `guide list cw`：先筛候选攻略，适合按投资环境、羁绊或角色方向缩小范围。
- `guide fetch cw`：在候选里读取完整攻略内容，确认标签、阵容、运营思路与当前目标是否一致。
- `guide fetch cw --select`：读取攻略详情并把攻略写入当前 session，后续真正进入对局时自动生效。
- `cw guide current`：用来回看当前已挂载的攻略摘要，确认现在游戏里实际挂着哪套路线。

## Workflow Handoff

- 用户直接点名“帮我选货币战争攻略”时，可以直接命中这个 skill。
- 如果 `trail-cw-entry` 的确认结果是“攻略优先”或“先定攻略”，就把后续推荐切到本 skill。
- 如果这是从 `trail-cw-entry` handoff 过来的，就继承上游已确认的目标、羁绊/成就与其他限制，只追问缺失项。
- 如果这是从投资环境页内部切入的无人值守模式，就不再继续追问，而是按当前投资环境、`待收集=1`、热门度、版本自动选攻略。
- 如果这是 `interactive` / `direct-user` 或开局前链路，一旦攻略已经选定、用户准备真正开局，就把控制权交回 `trail-cw-entry`，再进入 `cw enter` / `cw start`。
- 如果这是从投资环境页内部切入的无人值守模式，攻略选定后就把控制权交回 `trail-cw-portal`，由它继续 `portal select --card-idx ...` 选中对应环境。

## Reference Map

- `references/guide-selection-criteria.md`：选攻略前要看什么，以及不同目标、不同模式下应该优先比较哪些维度。
- `references/command-surface.md`：攻略相关命令家族在选攻略阶段该怎么分工，重点解释 `guide list cw`、`guide fetch cw`、`guide fetch cw --select`、`cw guide current`。
- `references/confirmation-checklist.md`：选攻略前必须问清的关键信息，以及从入口 handoff 或投资环境页切入时该怎么收束。
