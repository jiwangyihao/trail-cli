# Player Language Mapping

先用玩家常用说法理解诉求，再映射到官方化名词，最后才落到项目内命令或字段。

| 玩家常用说法 | 官方化名词 | 项目内命令或字段 |
| --- | --- | --- |
| 开新局 | 新开一局货币战争 | 通常先确认目标，再进入 `cw enter`，随后用 `cw start` 开局；需要桥接时再落到 `cw start --mode new` |
| 继续上一局 | 继续已有货币战争对局 | 通常先进入 `cw enter` 回到首页决策点，再由 `cw start` 继续；需要桥接时再落到 `cw start --mode continue` |
| 冲 A8 | 标准博弈里的高职级推进 | 通常先确认是不是以 `上分` 为目标，再进入 `cw enter` / `cw start` |
| 上分 | 以积分、晋升点为目标的标准博弈推进 | 通常会优先确认 `标准博弈`，再进入 `cw start`；必要时桥接 `battle_mode=standard` |
| 刷周常 / 刷奖励 | 以更快完成积分奖励为目标的超频博弈 | 通常会优先确认 `超频博弈`，再进入 `cw start`；必要时桥接 `battle_mode=overclock` |
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
