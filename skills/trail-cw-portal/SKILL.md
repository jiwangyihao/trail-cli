---
name: trail-cw-portal
description: 当上游已经用 `cw start` 成功进入货币战争投资环境页，并且需要判断这一页接下来该如何行动、是否刷开局或是否切到攻略无人值守时使用。
---

# Skill: trail-cw-portal

## Role

- `trail-cw-portal` 是货币战争投资环境页的 internal 跟进 skill，不是 direct-user 公共入口，也不是 scene entry、不是 owner。
- 它只在 `cw start` 已成功把流程推进到投资环境页之后接手，负责解释这一页接下来怎么行动。
- 它负责判断这一页该继续看环境、刷新开局，还是把“按当前环境定攻略”交给 `trail-cw-guide`。

## When To Use

- 上游已经进入投资环境页，用户要看当前环境卡片、判断值不值得刷开局，或继续决定这一页该怎么点。
- 用户已经确认 `环境优先`，想继续按环境页往下走。
- 当前还没确定攻略，但投资环境页已经出来，需要先解释这一页的行动逻辑。
- 纯 direct-user 的“我要玩货币战争”、纯“帮我选攻略”、或还停留在货币战争首页的请求，不进入本 skill。

## How To Act On The Portal Page

- 先用 `portal detect` 看当前可见环境卡片；需要刷新候选时用 `portal refresh`；确认整把不要时用 `portal restart`；决定继续当前环境时用 `portal select`。
- `待收集=1` 通常表示选择这个投资环境更有利于完成当前收集奖励；除非它和已知目标明显冲突，否则不要轻易忽略。
- 如果上游已经定了 `环境优先`，就继续按环境走，比较当前环境是否服务目标，再决定 refresh、restart 还是 select。
- 如果允许刷开局，就把 `refresh` / `restart` 当成自动筛选手段，持续刷到出现接近攻略或明显服务目标的环境。
- 如果不允许刷开局，仍可先做一次 refresh；只有在已确认 `环境优先` 时，portal 才在当前可见环境里选最贴近目标的一项；如果未确定攻略，则必须切到 `trail-cw-guide` 的无人值守模式，不由 portal 直接拍板。

## When To Hand Off To trail-cw-guide

- 如果未确定攻略，但投资环境页已经出来，就切到 `trail-cw-guide` 的无人值守模式，让它按当前环境、`待收集=1` 和版本自动定攻略。
- 如果用户明确说“按当前环境直接定攻略”，也切到 `trail-cw-guide`，不要在这里继续追问。
- 如果上游已经确认 `环境优先`，就不切到 trail-cw-guide；先在当前环境页完成 refresh/restart/select，再决定后面是否补定攻略。

## Reference Map

- `references/portal-command-surface.md`：投资环境页里 `portal detect/refresh/restart/select` 的分工和使用时机。
- `references/portal-selection-rules.md`：这一页该怎么选环境、怎样理解 `待收集=1`、以及何时切到攻略无人值守。
- `references/portal-refresh-policy.md`：允许刷开局和不允许刷开局时，各自该怎样处理 refresh/restart/select。
