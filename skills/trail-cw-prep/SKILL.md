---
name: trail-cw-prep
description: 当上游已经进入货币战争普通备战阶段，并且需要先收集阶段、槽位、商店、晶矿或出战前事实时使用。
---

# Skill: trail-cw-prep

## Role

- `trail-cw-prep` 是货币战争普通备战阶段的 active internal 跟进 skill，不是 scene entry、不是 direct-user 公共入口、不是整局 owner。
- 它负责普通备战闭环的事实消费、机制解释和权衡提示；不得把具体经营策略写成固定程序或固定决策表。
- 它不得直接或间接调用 archive skill；`cw.shop.*`、`cw.slots.*`、`cw.hand.*` 只是 CLI command family，不是旧 skill。

## When To Use

- `cw.portal.select` 成功并返回 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。
- 上游已经明确确认当前在普通备战、商店或槽位阶段，需要消费首帧事实并推进普通备战闭环。
- 货币战争首页、投资环境页和攻略选择页仍由对应上游 skill 处理；其它阶段按 CLI 输出的 handoff、next_action 或 recover 指示继续。

## Stage Boundaries

- 普通备战和商店阶段由本 skill 消费事实并推进闭环。
- 补给、投资、遭遇、命运卜者、通用事件、投资策略页、BOSS 前备战、结算和 game over 不由本 skill 预设策略；遇到这些页面时，按相关 CLI 输出的 handoff、next_action 或 recover 指示继续。
- unknown、tainted、request-status、daemon 恢复问题同样以 CLI 输出为准；本 skill 不直接 handoff 到 `trail-hsr-advanced`，也不自行编造恢复层路径。

## Required First Actions

- 若上一条命令输出 `shot path=...` 和 `info read_image_first=1`，必须先读取原始截图。
- 若上一条 `cw.portal.select` success 已经同时输出阶段、槽位、装备、商店、羁绊等普通备战首帧结构化事实，先结合截图消费这些事实；不要为了“重新确认”而立刻重复运行 `stage` / `slots` / `equipment` / `shop` 读命令。
- 若上一条 `cw.portal.select` success 输出含 `info skill_info=运营思路 text=...`，必须先把它读作当前攻略的动态提醒；它不是已解析策略，必须不发明默认优先级。
- 若上一条 `cw.portal.select` success 输出含 stage/slots/equipment/shop facts，说明它可能已经提供最新首帧快照；必须先读截图，再用这些文本事实制定第一步备战动作。
- 接收 `cw.portal.select` handoff 时，优先复用该响应中标题下 facts；`# ` 行只是板块标题，不是 action/prefix/fact，Agent 只消费实体行。读完截图后，再消费这些标题下的事实：`# 综合信息` 下看 stage/status，`# 攻略提示` 下看 skill_info，`# 角色信息` 下看 slot，`# 羁绊信息` 下看 trait summary，`# 装备信息` 下看装备背包 item/summary info，`# 装备优先级` 下看装备推荐 guide，`# 角色装备需求` 下看角色装备需求 slot/info，`# 商店信息` 下看 item/coins/reserve facts；只有缺失、stale 或页面变化才重扫。
- 只有事实缺失、stale 或页面已变化时，才主动调用 `trail cw slots read`、`trail cw equipment read` 或 `trail cw shop scan` 刷新；不要在接收 handoff 后立刻重复扫描。优先复用同次 equipment facts；缺失/stale/page changed 时才重跑 `cw.equipment.read`。
- 若 `slots.read` 或 `shop.scan` 输出 `match_kind=low_confidence`、`raw_name` 或低置信度 `warn`，必须先读截图确认，再接受 canonicalized 名称。
- `cw.equipment.read` 返回截图时必须先读原始截图，再消费 `item pos=equipment:<idx> center=x,y ...` 行、`# 装备优先级` 的 `guide` 行，以及 `# 角色装备需求` 的 `slot` 行或 `info todo=slots`；这些装备推荐分块位于 `warn`、`ref` 之前。若看到 `info todo=slots`，先运行或刷新 `cw.slots.read`，不要用 stale slots 推断角色缺口。需要排查 `row/col` 时，使用 `trail --format yaml cw equipment read --session <id>` 或 `trail --format yaml state dump --session <id>`。
- `cw.equipment.read` daemon 默认热路径使用 bundle recognizer，不调用 prepare/download/load icon cache；不要为普通装备读取先跑 `cw.equipment.prepare`。
- 接收 `cw.portal.select` handoff 后，如果后续执行 `cw.equipment.read` 并看到装备推荐，仍要先读截图，再看 `# 装备优先级` 的基础装备 `have/need` 与需求角色，最后看 `# 角色装备需求` 的当前 canonical 角色缺口；不要为了低优先级装备过早消耗基础装备。
- `# 角色装备需求` 当前只消费攻略 `优选装备` / `first_equipments`；不要因为攻略里有 `次选装备` / `second_equipments` 就提前合成。TODO：最后一层 BOSS 战前的备战阶段再考虑次选装备。
- `cw.equipment.compose` / `trail cw equipment compose` 是只写 session 的记录命令，不执行真实 UI 合成。只有已决定合成并装备某个进阶装备时，才用 `--name`、`--slot`、`--role` 记录；slot 使用 `front:1`、`back:1`、`hand:1` 这种从 1 开始的位置，成功首行为 `ok cw.equipment.compose pos=... name=... 装备=... count=...`。
- 读完截图后，先判断本轮是否存在可收集晶矿奖励；这个信息不能只依赖结构化文本。
- 不确定阶段时先用 `trail cw stage detect --session <id>` 或 `trail cw stage wait --session <id>`。
- 如果截图和结构化文本冲突，以截图为准并重新读取相关事实。

## Command Surface

- `trail cw stage detect|wait`：确认当前 CW 阶段。
- `trail cw slots read`：读取前台、后台、手牌和羁绊摘要；Agent 可见槽位编号从 1 开始。
- `trail cw shop scan|status|buy-slot|buy-exp|refresh|close`：读取和执行商店动作；`shop.scan` 有截图，`shop.status` 无截图。
- `trail cw equipment read --session <id>`：读取当前装备背包图标；返回截图时必须先读原始截图，再消费背包 `item`、`# 装备优先级` 的 `guide` 行、`# 角色装备需求` 的 `slot` 行或 `info todo=slots`。若看到 `info todo=slots`，先运行或刷新 `cw.slots.read`；这些分块位于 `warn`、`ref` 之前。需要诊断 `row/col` 时用 `trail --format yaml cw equipment read --session <id>` 或 `trail --format yaml state dump --session <id>`。
- `trail cw equipment compose --session <id> --name <进阶装备名> --slot front:1 --role <角色名>`：canonical command 为 `cw.equipment.compose`；只写 session，不执行真实 UI 合成，slot 使用 Agent 可见 1-based。
- `trail cw equipment prepare --session <id> [--refresh]`：默认只验证/汇总 bundle 装备资源，不下载图标；只有明确要刷新同版本 URL 变化或重建 workspace equipment override 时才使用 `--refresh`。
- `trail cw crystals collect`：截图确认本轮有可收晶矿时执行；收取后根据新截图判断手牌区是否变化。
- `trail cw hand sell-plan|sell`：读取或执行卖牌动作。
- `trail cw battle run --timeout 570`：出战前检查完成后执行出战和战斗链。

## Autonomy Boundary

- 在普通备战范围内，Agent 可以全自动执行符合当前局面判断的读图、收晶矿、买角色、买经验、刷新、卖牌、上场、换位和出战。
- 全自动不等于机械执行固定顺序；每个 mutation 前都要说明当前依据，每个 mutation 后都要先消费截图和命令输出再继续。
- 若 CLI 输出 `handoff_skill`、`next_action`、`recover` 或其它强指示，按命令输出继续，不自行发明替代流程。

## Strategy Principles

- 本 skill 面向会思考的 Agent，不提供可直接照抄的固定攻略；它说明普通备战中必须考虑哪些因素，以及这些因素为什么重要。
- 当前攻略的运营思路、攻略推荐角色、阶段阵容、最终阵容、最低金币、最低等级和中期等级都是推理输入；它们用于解释局面，不是硬编码程序。
- 截图是一手事实。结构化结果用于减少重复命令；截图与结构化结果冲突时，先信截图，再只补读受影响事实。

### 晶矿奖励

- 进入备战并读首帧截图后，先看本轮是否有可收集晶矿奖励；晶矿提示可能无法稳定结构化。
- 看到可收晶矿时，先执行 `trail cw crystals collect --session <id>`，再读本次命令截图。
- 收晶矿不会改变商店内容，不要因为收晶矿而重读 `shop scan`。
- 收晶矿后从截图判断手牌区是否变化；若有变化，只对变化槽位执行定向 `trail cw slots read --session <id> --slot ...`。
- 金币数量可以从截图直接判断；只有后续动作需要稳定结构化金币事实而当前输出缺失时，才补充相关读命令。

### 手牌压力与首次商店购买

- 手牌压力和第一次商店购买是同一个联合判断：Agent 同时拥有手牌区和商店信息，不能先孤立决定卖牌，再孤立决定买牌。
- 空位放不下商店中的攻略推荐角色时，这是硬指标手牌压力。
- 攻略相关角色拥有最高保留和购买优先级；即使当前还在前中期，大后期攻略角色也不应因为“暂时不上场”而轻易放弃。
- 其它类型角色不需要强行排序，但要认识到它们各自的价值：能补当前羁绊或人口、能提升当前战力、能形成有意义升星进度、能作为过渡角色，或能暂存为后续决策保留弹性。
- 非攻略稀有角色的暂存价值来自机制：未升星角色卖出不亏；在不升星的情况下保留非攻略稀有角色，可以帮助后续刷新时争取攻略稀有角色机会。
- 升星后卖出会亏。非攻略角色不应仅因为“暂存”而无意义升星；已经升星或接近升星的非攻略角色，卖出前要额外考虑亏损、当前战力和空间压力。
- 低价值占位通常来自：非攻略、无当前羁绊或战力价值、没有有意义升星进度、当前不上场且占用手牌空间的角色。

### 买卖后的快速确认

- 买牌或卖牌后，先读命令返回的截图，不要默认补跑结构化读命令。
- 截图里最需要快速确认的是剩余空位和金币变化；这两个信息会直接影响后续商店购买、刷新和升级判断。
- 空位和金币能从截图判断时，直接用截图继续推理，比立刻运行 `slots.read` 或 `shop.scan` 更快。
- 结构化读命令用于后续需要稳定事实、角色名或羁绊细节时补充，不作为每次买卖后的固定步骤。

### 经济、升级与刷新

- 经济判断先区分模式机制：标准博弈有存款利息，金币低于 50 时每 10 金币会带来每局额外收益，50 以上不再增加；超频博弈没有这条利息规则。
- 讨论是否刷新前，先假设当前商店里该买的高价值或高优先级角色已经处理过；如果商店仍有攻略推荐角色，通常应先买，除非当前金币确实不够。
- `5级搜牌`、`速升9级` 这类攻略标签表达的是同一类等级节点，只是写法不同：到达攻略要求等级前，优先考虑买经验接近该等级；到达后，才更主动考虑刷新商店继续找攻略角色。
- 所有花费都要考虑对利息的影响。在标准模式下，40 到 50 金币附近通常是重要经济区间；这不是死规则，而是提醒 Agent 花钱前要意识到利息机会成本。
- 达到攻略要求等级后，不代表完全不再买经验。若当前已有的纯后期攻略角色多于可上场人口，且下一场战斗难度较高或临近关键节点，买经验升人口让这些角色立刻上场，可能比继续刷新更有价值。
- 上一条只用于纯后期攻略角色；前期或中期过渡角色到后期该卖就卖，不应因为“角色数量超过人口”而被错误保护。
- “后期”需要结合关卡阶段和攻略阵容判断：接近第三层或 BOSS 前通常更接近后期；但如果在一层、二层时已经集齐大量 final 阵容，也可以提前把买经验视为更有价值的选择。
- 决定是否刷新时，考虑是否已达到攻略等级节点、攻略角色缺口是否仍明显、当前金币与利息损失是否可接受、当前人口和战力是否足以应对下一场战斗，以及刷新是否比买经验更能改善局面。
- 当继续买经验或刷新带来的利息损失已经不可接受时，经济循环自然停止，转入上场、换位和出战前判断。

### 上场与换位

- 经济循环结束后，总是先用满人口；如果可上场人数未用满，出战命令也会拦截。
- 用满人口后，再按攻略阵容位置和羁绊目标调整前后台。
- 攻略通常会明确阵容位置；有些角色只能放前台或后台，必须尊重角色位置限制。
- 对前后台均可的角色，在可上场角色较少时可以优先放前台，帮助当前阵容形成有效战力。
- 攻略角色不足时，可以上场非攻略角色补当前羁绊、人口或战力，但不要为低价值过渡角色破坏攻略核心羁绊或攻略角色位置。
- 同一位置或同一功能有多个候选时，综合考虑星级、当前战力表现、羁绊贡献和攻略目标。
- 不管下一战难度高低，都应积极调整阵容，使当前阵容成为已知条件下更好的阵容，而不是因为看似简单就跳过换位。

### 出战判断

- 出战前确认人口已经用满。
- 确认已经根据攻略位置、当前羁绊和可用角色完成上场与换位。
- 确认商店里没有应买的攻略推荐或其它高价值角色；如果有但金币确实不够，按当前经济事实继续判断。
- 确认继续买经验或刷新带来的利息损失已经不可接受，或继续花钱的收益不如保留经济。
- 确认手牌压力已经处于可接受状态，不会阻碍后续关键购买或当前出战判断。
- 最后再读当前截图，确认没有晶矿奖励或其它普通备战内明显待办动作。
- 上述检查完成后，运行 `trail cw battle run --session <id> --timeout 570`；若命令输出 `status=in_progress` 和 `next_action=cw.battle.run`，先读截图，再按输出继续 `cw.battle.run`。

## Stop Conditions

- 本 skill 不维护额外人工停止清单；普通备战内应持续自治直到完成出战判断。
- 如果命令输出 `handoff_skill`、`next_action`、`recover`、`request id=...`、`tainted=1` 或明确错误码，按输出协议继续，不用本 skill 猜测替代路径。
- 如果当前攻略缺失或不完整，避免推进依赖攻略推荐角色、攻略等级节点或攻略阵容的判断；先按现有命令输出补齐或重新选择攻略。

## Reference Map

- `references/command-surface.md`：普通备战阶段可用命令和边界。
- `references/stage-boundaries.md`：普通备战与其它阶段的分界。
