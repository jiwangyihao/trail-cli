---
name: trail-cw-entry
description: 当用户已经明确要进入《崩坏：星穹铁道》的货币战争，并且需要先按开局目标、玩法概念与入口判断收束决策时使用。
---

# Skill: trail-cw-entry

## Role

- `trail-cw-entry` 是货币战争的 active public 开局入口说明 skill，不是整局 owner。
- 它先用官方/百科概念、玩家说法映射和目标驱动确认链路，把“我要玩货币战争”收束成可执行的开局决定，再把真正的开局动作交给命令或下游 skill。
- 它只在 `攻略优先` 链路里确认“是否允许刷开局”，不在这里展开投资环境页里的 `refresh/restart/select` 细节。

## When To Use

- 用户已经明确点名货币战争，并且想先定开局目标、模式倾向或投资环境入口。
- 用户想知道“上分 / 周常 / 羁绊成就”这三类目标在货币战争里该怎样落到实际开局判断。
- 纯 control-plane 恢复、纯 CLI 参数解释、或泛化成“继续玩星铁”的请求，不进入本 skill。

## What To Confirm First

- 第一问先看是不是要提升职级；若是，默认按 `标准博弈 + highest` 理解，对应玩家视角的“标准博弈 + 最高难度”。
- 第一问先看是不是要速刷周常/奖励；若是，默认按 `超频博弈 + lowest` 理解，对应玩家视角的“超频博弈 + 最低难度”。
- 第一问先看是不是要完成羁绊/成就；若是，默认按 `标准博弈 + A5-1 + 攻略优先` 理解，对应玩家视角的“标准博弈 + 紫金1 + 攻略优先”。
- 如果用户不是先按目标表达，而是在手动指定开局难度，再确认是继续当前职级、先降到更低、回最高职级，还是直接指定某个 `AX-X` 层级（公开范围 `A0-1..A8-40`，例如 `A7-3`）。
- 对攻略还有没有进一步限制，例如指定投资环境、角色或阵容倾向；这是同一个确认问题，Agent 尽量满足这些进一步限制。
- 继续保留“攻略优先 / 环境优先”这个分叉：想顺带完成羁绊或成就，可偏攻略优先；若对特定羁绊成就没有要求，可偏环境优先以减少刷环境时间。
- 如果走 `攻略优先`，本 skill 只负责确认是否允许刷开局；即使这次不刷开局，也允许执行一次 refresh，方便后续按攻略决定要不要刷开局。
- 如果走 `环境优先`，不再在这里追问是否刷开局；把投资环境页里的后续判断交给 `trail-cw-portal`，由它在投资环境页决定是否 refresh/restart/select。
- 只有后续真的检测到未结束对局或未收尾进度时，才追问是继续还是先结算，不把旧局续打当第一问。

`cw enter` 的定位仍是进入货币战争首页，也就是开局前决策点。`cw start` 的定位仍是在目标、优先级与默认难度表达确认后，把首页推进到投资环境页并真正开局。

## Battle Flow Resume

- 常规 battle / settle 流程默认使用 `trail cw battle run --session <id>`；默认 timeout 现在是 `90s`，不要再把长 timeout 当默认流程。
- `trail cw battle run` 返回 `status=in_progress` 时，先读取本次截图；如果判断仍在 battle flow 中就继续运行 `trail cw battle run --session <id>`。
- battle flow 包含战斗中、结算页、结算翻页但未回到下一稳定阶段；结算页也属于 battle flow，仍在 battle flow 中就继续运行 `trail cw battle run --session <id>`，不要因为看到结算页就切回旧 `settle next`。
- `trail cw battle clear-in-progress --session <id>` 只清 battle.run 的内部续跑提示位，不清 battle 摘要、截图或阶段事实。
- battle in-progress 本轮只补场景/命令说明，不实现新的 skill 本体。

## Workflow Handoff

- 用户直接说“玩货币战争”时，可以直接命中这个 skill。
- 如果用户选择“攻略优先”或明确要先定攻略，应切到 `trail-cw-guide`，由它继承已确认的目标、羁绊/成就与进一步限制，只补问缺失项；确认候选后先完成 `guide.fetch.cw --select` 记录当前攻略，再回到开局入口动作。
- 如果用户选择“环境优先”，或只是要先看这一局值不值得玩，则继续保留在本 skill，后续通过 `cw enter` / `cw start` 进入投资环境页，再把环境页决策交给 `trail-cw-portal`。
- 是否允许刷开局只在 `攻略优先` 链路里确认；`环境优先` 链路把这件事留给投资环境页里的 `trail-cw-portal` 再决定。
- 如果是从 `trail-cw-guide` 返回，说明当前 session 已经有当前攻略；默认继续 `cw enter` / `cw start` / `cw.portal.select` 这条开局链路，后续在投资环境页执行 `cw.portal.select` 成功后，会自动应用当前已选攻略，不要把 `cw guide apply` 当成默认第一步；只有自动应用失败或需要手动重试时，`cw guide apply` 才作为兜底。
- `cw.portal.select` 成功进入普通备战后，会通过 handoff 进入 `trail-cw-prep`；该响应已经自动收集初始备战 slots/shop 信息，入口 skill 不需要再立即重复扫描相同事实。
- 消费 `cw.portal.select` 带截图 success 时必须先读截图；`# ` 行只是板块标题，不是事实行，不要当作 action/prefix。读完截图后，再消费这些标题下的事实：`# 综合信息` 下看 stage/status，`# 攻略提示` 下看 skill_info，`# 角色信息` 下看 slot，`# 羁绊信息` 下看 trait summary，`# 商店信息` 下看 item/coins/reserve facts。
- 只有后续真的检测到未结束对局时，才补问继续还是结算，然后再决定 `cw start` 的走向。

## Reference Map

- `references/gameplay-concepts.md`：先讲官方/百科概念，再补玩家目标、官方职级与项目内难度表达的桥接事实。
- `references/player-language-mapping.md`：把玩家常用说法映射到官方化名词与项目内动作，避免一上来暴露参数手册。
- `references/confirmation-checklist.md`：开局前必须问清的目标驱动确认项，以及确认后通常接什么命令或下游 skill。
