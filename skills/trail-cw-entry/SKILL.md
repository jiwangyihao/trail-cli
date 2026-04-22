---
name: trail-cw-entry
description: 当用户明确想进入《崩坏：星穹铁道》的货币战争玩法，或已经把请求聚焦到该玩法的开局入口时使用。
---

# Skill: trail-cw-entry

## Role

- `trail-cw-entry` 是货币战争的开局入口说明 skill，不是整局 owner。
- 它负责把“我要玩货币战争”这类请求先收束到开局前决策点，再把后续动作交给具体命令与下游流程。

## When To Use

- 用户已经明确点名货币战争，例如“玩货币战争”“冲 A8”“刷周常奖励”“走超频博弈”“先看投资环境”“想抄攻略开局”。
- 用户请求已经聚焦到这个玩法的开局选择，而不是泛化成“继续玩星铁”。
- 纯 CLI 参数解释、纯 control-plane 恢复、或与货币战争无关的任务，不进入本 skill。

## What To Confirm First

- 先确认这一局是开新局还是继续上一局。
- 先确认用户更想上分，还是更偏速刷奖励。
- 先确认用户想走标准博弈，还是超频博弈。
- 先确认是按当前职级难度推进，还是先降到更低难度。
- 先确认用户想先按攻略/阵容玩，还是先看投资环境/词条。
- 先确认是否允许刷开局。
- 如果检测到未收尾进度，先确认是继续处理，还是先结算再开下一步。

`cw enter` 的定位是进入货币战争首页，也就是开局前决策点。`cw start` 的定位是在确认模式与目标后，把首页推进到投资环境页并真正开局。

## Workflow Handoff

- 用户直接说“玩货币战争”时，可以直接命中这个 skill。
- 如果没先加载本 skill 就运行了 `cw enter`，renderer 会强提示切到这个 scene entry，再回到开局确认链路。
- 一般是在确认项收齐后，才轮到 `cw enter`、`cw start`，以及后续的 `portal.*` 或 `guide.*`。

## Reference Map

- `references/gameplay-concepts.md`：按 BWiki 栏目顺序整理玩法概念，先讲官方化口径，再谈开局判断。
- `references/player-language-mapping.md`：把玩家常用说法映射到项目内命令或字段，避免一上来暴露内部术语。
- `references/confirmation-checklist.md`：开局前必须问清的确认项，以及确认后通常接什么命令。
