# Player Language Mapping

先用玩家常用说法理解诉求，再映射到官方化名词，最后才落到项目内命令或字段。

| 玩家常用说法 | 官方化名词 | 项目内命令或字段 |
| --- | --- | --- |
| 冲 A8 / 冲段位 / 提升职级 | 以更高职级为目标的标准博弈推进 | 先把目标记成 `标准博弈 + highest`，确认后进入 `cw enter` / `cw start`；需要桥接时再落到 `battle_mode=standard` + `difficulty=highest` |
| 上分 | 以积分、晋升点为目标的标准博弈推进 | 通常会优先确认 `标准博弈 + highest`，再进入 `cw start` |
| 刷周常 / 刷奖励 | 以更快完成积分奖励为目标的超频博弈 | 先把目标记成 `超频博弈 + lowest`，确认后进入 `cw enter` / `cw start`；需要桥接时再落到 `battle_mode=overclock` + `difficulty=lowest` |
| 补羁绊 / 补成就 | 以完成特定羁绊或成就为目标的标准博弈推进 | 先把目标记成 `标准博弈 + A5-1 + 攻略优先`，优先切到 `trail-cw-guide` / `guide.*` 选攻略；需要桥接时再记录 `battle_mode=standard` + `difficulty=A5-1` |
| 标准博弈 | 标准博弈 | 通常通过 `cw start` 开局；必要时桥接 `battle_mode=standard` |
| 超频博弈 | 超频博弈 | 通常通过 `cw start` 开局；必要时桥接 `battle_mode=overclock` |
| 当前这档 / 当前职级难度 / 继续当前职级 | 按当前职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=current` |
| 更低难度 / 降一档 / 先降到更低 | 降到更低职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=lowest` |
| 最高档 / 回最高职级 | 回到当前可选最高职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=highest` |
| 指定 A7-3 / 指定某个 A 段 | 指定某个公开职级层级 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=AX-X`，公开范围 `A0-1..A8-40` |
| 看投资环境 / 看词条 | 投资环境 | 通常先进入 `cw enter`，再继续到 `portal.*` |
| 看路线 / 看策略 | 投资策略 | 通常先看 `portal.*` 给出的环境，再继续到 `strategy` 相关动作 |
| 抄攻略开局 / 攻略开局 | 攻略 | 通常先看 `guide.*`，再决定是否按攻略推进到 `cw start` |
| 刷开局 | 重看投资环境或直接重开 | 通常会先走 `portal.*`；确实需要重刷时再桥接 `cw.portal.refresh` / `cw.portal.restart` |
| 当前这档 / 当前职级难度 / 继续当前职级 | 按当前职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=current` |
| 更低难度 / 降一档 / 先降到更低 | 降到更低职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=lowest` |
| 最高档 / 回最高职级 | 回到当前可选最高职级继续推进 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=highest` |
| 指定 A7-3 / 指定某个 A 段 | 指定某个公开职级层级 | 作为 `cw start` 前的难度确认；必要时桥接 `difficulty=AX-X`，公开范围 `A0-1..A8-40` |
| 看投资环境 / 看词条 | 投资环境 | 通常先进入 `cw enter` / `cw start`，再继续到 `portal.*` |
| 看路线 / 看策略 | 投资策略 | 通常先看 `portal.*` 给出的环境，再继续到 strategy 相关动作 |
| 攻略开局 / 先定攻略 | 攻略 | 优先切到 `trail-cw-guide` 或 `guide.*` 完成筛选，再回到 `cw enter` / `cw start` |
| 攻略优先 | 先按攻略收束投资环境、主C 与阵容倾向 | 想顺带完成羁绊或成就时更常见；通常先走 `guide.*` |
| 环境优先 | 先看投资环境，再决定是否匹配攻略 | 对特定羁绊成就没有要求时更常见；通常先走 `portal.*` 以减少刷环境时间 |
| 刷开局 | 重看投资环境或直接重开 | 即使没有明确选择刷开局，也允许先执行一次 `cw.portal.refresh`；确实需要重刷时再桥接 `cw.portal.restart` |
| 开新局 | 新开一局货币战争 | 通常先确认目标，再进入 `cw enter`，随后用 `cw start` 开局 |
| 继续上一局 | 继续已有货币战争对局 | 不作为第一问；只有后续检测到未结束对局时，才在继续或结算之间确认，再决定 `cw start` 走向 |
