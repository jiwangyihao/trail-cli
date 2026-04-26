# CW 备战阶段 Skill 基础设施设计

## 背景

当前 active 货币战争 skill 拓扑已经覆盖入口、攻略选择和投资环境页：

- `trail-cw-entry` 负责从“我要玩货币战争”收束到目标、模式、难度与攻略优先/环境优先。
- `trail-cw-guide` 负责开局前攻略选择，并通过 `guide.fetch.cw --select` 建立当前攻略。
- `trail-cw-portal` 负责 `cw start` 或 `portal refresh` 成功后的投资环境页联合决策。

`cw.portal.select` 成功后会进入货币战争普通备战阶段，但当前没有 active skill 接手普通备战页的经营链路。命令面已经存在 `cw.stage.*`、`cw.slots.*`、`cw.shop.*`、`cw.crystals.collect`、`cw.hand.*`、`cw.battle.*` 和若干事件命令；这些命令缺少一个统一的普通备战阶段 owner 来约束 Agent 如何收集事实、保持输出协议、处理未知状态，以及避免把未讨论的经营策略写死。

本设计只做基础设施，不固化具体经营策略。

## 目标

1. 新增 active internal skill `trail-cw-prep`，作为普通备战阶段的最小 owner。
2. 让 `cw.portal.select` 成功后强提示 Agent 切到 `trail-cw-prep`。
3. 将当前攻略完整保存到 session，彻底移除“当前攻略依赖 artifact / 攻略快照ID”的语义。
4. 在 `cw.portal.select` 成功输出中加入 `info skill_info=运营思路 ...`，把当前攻略 `operation_guide` 动态传给后续 skill。
5. 新增买经验命令 `trail cw shop buy-exp`，canonical command 为 `cw.shop.buy_exp`。
6. 为后续正式讨论备战经营策略留下明确的决策点清单，但本轮不写具体优先级。

## 非目标

- 不定义“应该买哪个角色”“什么时候刷新商店”“什么时候买经验”“谁该上场”这类具体经营策略。
- 不处理最后一层 BOSS 前备战；后续单独设计对应 skill。
- 不处理补给、投资、遭遇、命运卜者、通用特殊事件的选项策略；这些事件各自拥有独立 skill。
- 不恢复或直接引用 archive 中旧的 `trail-cw-shop`、`trail-cw-slots`、`trail-cw-replenish` 作为 active skill。
- 不新增一键自动跑完整局的单命令。

## Skill 拓扑

新增：

```text
skills/trail-cw-prep/
  SKILL.md
  references/
    command-surface.md
    stage-boundaries.md
    decision-points-pending-strategy.md
  evals/
    triggers.json
```

`trail-cw-prep` 的 frontmatter 约束：

- `name: trail-cw-prep`
- `description` 使用中文，描述触发条件，不描述完整流程。
- 触发条件聚焦“已经进入货币战争普通备战阶段，需要收集事实、理解可用命令或继续普通备战链路”。
- 不写成 direct-user 公共入口，不覆盖 `trail-cw-entry` 的入口语义。

`skills/registry/scene-entries.yaml` 中新增 internal skill：

```yaml
internal_skills:
  - name: trail-cw-prep
    status: active
    exposure: internal
    caller_roles:
      - scene_entry
      - internal_skill
```

`trail-cw-prep` 不是 scene entry、不是整局 owner、不是 direct-user 公共入口。它只在普通备战阶段接管；遇到非普通备战状态时停止或交给后续专用 skill。`internal_skill` 表示当前阶段允许由上游内部 skill（第一批是 `trail-cw-portal`）在 scene entry 编排链内交接到 `trail-cw-prep`；这不是 direct-user 暴露。

Active skill 和 active 文档不得直接或间接调用 archive skill。`trail-cw-prep` 可以描述 `cw.shop.*`、`cw.slots.*`、`cw.hand.*` 等 CLI command family，但不得 import、链接、handoff、继承或推荐 archive 中旧的 `trail-cw-shop`、`trail-cw-slots`、`trail-cw-replenish` skill。

## Workflow Handoff

在 `skills/registry/workflow-handoffs.yaml` 中为 `cw.portal.select` 增加默认 handoff：

```yaml
commands:
  cw.portal.select:
    default:
      handoff_skill: trail-cw-prep
      handoff_strength: strong
      handoff_reason: preparation_stage_entered
```

渲染约束：

- `cw.portal.select` success 首行仍保持 `ok cw.portal.select idx=... 投资环境=...`。
- 如果返回截图，仍先输出 `shot path=...`，紧跟 `info read_image_first=1`。
- `info skill_info=运营思路 text="..."` 是普通 `info` 实体行，必须出现在 `warn` / `ref` 之前。
- `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered` 必须是 success 输出最后一行。
- 不新增正文前缀，继续使用允许的 `info` 前缀。

完整 success 行顺序固定为：首行 -> `shot` -> `info read_image_first=1` -> `info skill_info=...` -> 其它实体行/普通 `info` -> `warn` -> `ref` -> 最终 handoff 行。renderer 测试必须覆盖带 `warnings` 与 `references` 的场景，确保 handoff 仍为最后一行，且 `skill_info` 不被排到 `warn/ref` 之后。

示例：

```text
ok cw.portal.select idx=1 投资环境=击破概念股
shot path=.trail/shots/req-portal-select.png
info read_image_first=1
info skill_info=运营思路 text="前期：按攻略运营提示处理；中期：继续参考当前攻略字段。"
info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered
```

## 当前攻略 Session 化

当前代码中完整攻略 payload 包含 `operation_guide`，但写入 session 的当前 guide 摘要未保留该字段，并且 `cw.guide.current|apply` 仍输出 `攻略快照ID`。本轮改成：

1. `guide.fetch.cw --select --session <id>` 获取完整攻略后，将完整规范化 guide payload 写入 `session.scene_state["cw"]["guide"]`。
2. 当前攻略不再通过 artifact 恢复，也不再输出 `攻略快照ID`。
3. `cw.guide.current` 从 session 当前 guide 读摘要和必要详情。
4. `cw.guide.apply` 如仍保留为手动兜底，也从 session 当前 guide 读完整攻略，不接收 artifact id。
5. 普通 `guide.fetch.cw` 仍可作为预览命令返回完整攻略内容；如果实现继续为普通预览创建 artifact/cache，也不得把该 artifact 写入 `cw.guide`，不得参与当前攻略恢复。
6. 相关 README、AGENTS、skill 文档和测试中关于“当前攻略 artifact / 攻略快照ID”的语义必须同步删除或改写。

session shape 固定为：`cw.guide` 保存完整规范化 guide payload，并额外保留现有可变运行字段 `remaining_purchases`；`cw.constraints` 继续保存 `min_coins`、`min_level`、`mid_level`、`priority`、`positioning`。完整 guide 中的 `on_field`、`off_field`、`role_stages`、`first_fight_augments`、`second_fight_augments`、`order_basic`、`order_compose` 等字段不得因 session 化而丢失；`cw.shop.*` 的 `guide_summary`、`cw.strategy.detect|refresh` 的攻略推荐语义、`cw.slots.read` 的羁绊摘要仍应能从当前 session guide/constraints 获取所需事实。

该变更是有意的破坏性语义调整，不为 artifact-only 当前攻略状态新增兼容恢复层。若已有 session 只有 `guide.artifact` 或缺少完整 guide，后续命令应返回稳定错误，要求重新执行 `guide.fetch.cw --select`，不得读取 artifact 反向补全。

`guide.fetch.cw --select` 只改变 session 状态，不因完整 guide session 化而扩张 default/YAML 输出 shape。

## `skill_info` 数据

第一批 `skill_info` 只服务 `cw.portal.select -> trail-cw-prep`：

- 来源：session 当前完整 guide 的 `operation_guide`。
- response data contract：业务层在 `cw.portal.select` 结果 `data` 中写入 `skill_info=[{"name":"运营思路","text":...}]`；renderer 只消费 `data.skill_info`，不直接读取 session。
- 默认文本：`info skill_info=运营思路 text="..."`。
- 如果当前 guide 完整但 `operation_guide` 为空，则不输出该行，不伪造内容。
- 如果内容含空格、换行、等号或逗号歧义，按现有 `_encode_value` 双引号规则输出。
- `skill_info` 是动态提醒，不是策略规则；`trail-cw-prep` 只能要求 Agent 阅读它，不能把它当成已经解析完成的结构化策略。

如果完整 `operation_guide` 过长，实现时可以先输出全文，后续若实践证明影响扫读，再另行设计摘要截断规则；本设计不提前引入截断策略。

## 新增买经验命令

新增 CLI：

```text
trail cw shop buy-exp --session <id>
```

canonical command：

```text
cw.shop.buy_exp
```

命令职责：

- 点击当前备战页/商店区域的买经验按钮。
- 新增 scene 层抽象，不把固定坐标写进 daemon handler：`SHOP_EXP_BUY_POINT`、`ShopExpBuyer`、`build_cw_shop_exp_buyer(runtime)`、`buy_cw_shop_exp(...)`。
- 通过 `cw_service.py` 注入 `shop_exp_buyer_factory`，handler 只负责调用 scene 函数。
- 将 `cw.shop.buy_exp` 加入 `CW_MUTATING_METHODS` 或等价 mutation allowlist，并在 `CwService` handler 表注册。
- 更新 `session.scene_state["cw"]["shop"]` 为 fresh shop snapshot，而不是只改局部字段。
- 成功输出应包含可供下一步判断的 `coins`、`level`、`exp`、`reserve_full`、`team_size` 等事实；字段命名沿用现有 `cw.shop.scan/status` 的语义。
- 若成功路径带截图，必须输出 `shot path=...` 和紧随其后的 `info read_image_first=1`。

`cw.shop.buy_exp` 复用 shop action renderer family，但必须像 `cw.shop.scan` 一样追加 shop snapshot facts。成功输出合同冻结为：首行 `ok cw.shop.buy_exp opened=<0|1> stale=0 count=<n>`；截图块；必要的 `item ...` 行；普通 `info coins=... level=... exp=... reserve_full=0|1 team_size=...` 行；最后再输出 `warn/ref`。如果 `team_size` 本轮无法 fresh 读取，可以输出已有 fresh snapshot 中的值；缺失时显式保留 `team_size=null`，不得省略该决策事实。

买经验后的 state 合并规则：`opened` 表示命令结束时商店/备战 UI 的实际状态；`stale=False` 表示返回的金币、等级、经验和队伍上限事实是本次动作后的快照；`items` 可以沿用或重扫，但必须保持与 `count` 一致；`guide_summary` 继续保留当前 guide 的 `remaining_purchases` 与 constraints 摘要；`slots` 是否 stale 由等级/队伍上限变化决定，若买经验可能改变可上场容量，则必须标记槽位快照需要重新判断。

命令不负责决定何时买经验。`trail-cw-prep` 也只列出“买经验/升等级”是待讨论决策点，不在本轮给默认条件。

## `trail-cw-prep` 内容边界

`SKILL.md` 应包含以下章节：

- `Role`：说明它是普通备战阶段 internal owner。
- `When To Use`：本轮只配置 `cw.portal.select` 成功后的自动 handoff；若上游已经通过人工或专用逻辑确认当前在普通备战/商店阶段，也可以使用本 skill 收集事实。`cw.battle.run` / settle 回普通备战的自动 handoff 作为 future 设计，不在本轮配置。
- `Stage Boundaries`：普通备战/商店阶段可处理；特殊事件、BOSS 前备战、结算、game over、未知状态不继续自治。
- `Required First Actions`：读取命令截图；必要时 `cw.stage.detect` / `cw.stage.wait`；不跳过 `read_image_first=1`。
- `Command Surface`：列出 `cw.stage.*`、`cw.slots.*`、`cw.shop.*`、`cw.crystals.collect`、`cw.hand.*`、`cw.battle.run` 的使用边界。
- `Autonomy Boundary`：本轮可自治的只有读图、阶段确认、只读事实收集、输出协议判断和安全恢复；买角色、买经验、刷新、卖牌、上场、换位、收晶矿、出战等 mutation 只能在用户明确指示或后续策略设计给出决策后执行。
- `Decision Points Pending Strategy`：列出待后续专题讨论的经营决策点，不提供默认优先级。
- `Stop Conditions`：遇到特殊事件、BOSS 前备战、未知状态、mutation 结果未知、session tainted 时停止并按对应恢复/未来 skill 处理。
- `Reference Map`：指向三个 reference 文件。

`decision-points-pending-strategy.md` 只列主题，不写优先级：

- 商店中哪些角色值得买。
- 备战位满时如何决定卖牌。
- 前台/后台/手牌如何上场或换位。
- 什么时候买经验或升等级。
- 晶矿奖励何时收取以及收取后要重读哪些事实。
- 是否刷新商店。
- 何时结束备战并出战。

## 错误处理

- 所有 mutation 命令如果结果未知，不能盲目重试；应走 request-status / reconcile-session / advanced 恢复链路。
- `cw.shop.buy_exp` 未执行输入前失败时可以作为 before-side-effect 失败处理；一旦调用 exp buyer，后续 OCR、确认、持久化、截图或响应构造任一阶段失败，都必须作为 unknown result 处理，输出 `request id=...` 与 `recover action=daemon.request_status ...`，并保持 session taint 语义。
- `cw.portal.select` 必须在任何点击前 preflight 校验当前完整 guide。无 guide selected、legacy artifact-only guide、malformed/incomplete guide 都必须稳定失败并要求重新执行 `guide.fetch.cw --select`；不得在缺少完整 guide 时推进投资环境选择。只有当前 guide 完整但 `operation_guide` 为空时，才允许成功且省略 `skill_info`。
- `trail-cw-prep` 不得在特殊事件页里继续猜选项；它只能停止并等待未来专用 skill 或用户指示。

## 文档同步

必须同步更新：

- `AGENTS.md`：新增 `trail-cw-prep` active internal 拓扑；移除当前攻略 `攻略快照ID` artifact 语义；加入 `cw.portal.select -> trail-cw-prep` handoff 说明；必须按项目输出协议补充 `cw.shop.buy_exp` canonical command、renderer 家族与字段规则。
- `README.md`：新增 `skill_info` 示例、`cw.portal.select` handoff 示例、`trail cw shop buy-exp` 示例；移除 `攻略快照ID` 说明；更新“`coins/level/exp/reserve_full/team_size` 当前只在 `shop scan/status` 暴露”的旧表述，把 `buy-exp` 纳入 shop snapshot facts 说明。
- `skills/trail-hsr/references/scene-entry-index.md`：新增 `cw.portal.select -> trail-cw-prep` 的阶段内 handoff 说明，同时继续区分 `cw.enter -> trail-cw-entry` 是 scene entry handoff。
- `skills/trail-cw-entry` / `trail-cw-portal`：说明 portal 成功后交给 `trail-cw-prep`，不要继续停在 portal skill。
- `skills/trail-cw-guide`：说明 `--select` 将完整攻略写入 session，不再建立当前攻略 artifact。
- `skills/trail-cw-prep`：新增 skill 和 reference/eval 文件。

## 测试计划

### Skill 结构与触发

1. `tests/test_skill_structure.py` 增加 `trail-cw-prep` frontmatter、必需章节、reference 文件存在性测试。
2. `skills/trail-cw-prep/evals/triggers.json` 覆盖 should-trigger、should-not-trigger、competition 样例。
3. competition 样例必须区分：
   - 货币战争首页仍归 `trail-cw-entry`。
   - 投资环境页仍归 `trail-cw-portal`。
   - 攻略选择仍归 `trail-cw-guide`。
   - 普通备战页/`cw.portal.select` 后续归 `trail-cw-prep`。
   - 特殊事件页不归 `trail-cw-prep` 自治。
4. competition 矩阵至少覆盖：`trail-cw-entry` 对“我要玩货币战争/首页/开局目标”；`trail-cw-portal` 对“三张投资环境卡/refresh/restart/待收集=1”；`trail-cw-guide` 对“选攻略/按当前环境挑攻略”；`trail-cw-prep` 对 `cw.portal.select` success handoff、普通备战页、普通商店/槽位页；`none` 或 future skill 对补给、遭遇、命运卜者、普通投资事件、投资策略页、特殊事件、BOSS 前备战、结算、game over、unknown、tainted、request-status。

### 当前攻略 session 化

1. `guide.fetch.cw --select` 保存完整 guide，包括 `operation_guide`。
2. `cw.guide.current` 从 session 读取当前 guide，不依赖 artifact。
3. `cw.guide.apply` 若保留，读取 session 当前 guide，不依赖 artifact。
4. 当前攻略缺失时返回清晰错误或无攻略状态，不从普通 fetch artifact 隐式恢复。
5. 删除或改写所有 `攻略快照ID` 断言。
6. `guide.fetch.cw --select` 不扩张 default/YAML 输出 shape。
7. 完整 guide session 化后，`cw.shop.*` 仍输出/更新 `guide_summary`，`cw.strategy.detect|refresh` 仍能输出 `info 已加载攻略=1` 和攻略推荐，`cw.slots.read` 仍能使用当前 guide/config 生成羁绊摘要。

### `cw.portal.select` 输出

1. 成功输出包含 `info skill_info=运营思路 text=...`，且该行位于截图/业务信息之后、handoff 行之前。
2. handoff 行为 success 输出最后一行。
3. 完整 guide 的 `operation_guide` 为空时不输出 `skill_info`；guide 缺失、legacy artifact-only 或 incomplete 时不能成功进入 `cw.portal.select` 点击链路。
4. 文本协议仍遵守 `shot path=...` 后紧跟 `info read_image_first=1`。
5. 带 `warnings` / `references` 时，`skill_info` 仍在 `warn/ref` 之前，handoff 仍是最后一行。

### `cw.shop.buy_exp`

1. CLI 命令 `trail cw shop buy-exp --session <id>` 映射到 `cw.shop.buy_exp`。
2. daemon/RPC 注册该 command，并将 `cw.shop.buy_exp` 加入 `CW_MUTATING_METHODS` / request journal 路径。
3. 成功状态更新 shop 快照中的金币、等级、经验、队伍上限相关事实。
4. renderer success 首行使用 canonical `cw.shop.buy_exp`，并输出会影响下一步决策的 `0/false/count` 类事实。
5. 点击后失败进入 request-status / recover / taint 语义，而不是当纯读取失败处理。
6. `tests/test_cw_rpc_contracts.py` 覆盖 CLI 到 RPC method/payload 映射。
7. `tests/test_output_rendering.py` 覆盖 success、failure、YAML unsupported、snapshot info 行顺序。
8. `tests/test_cw_shop.py` 覆盖 exp buyer builder 点击点、state snapshot merge、journal completed、点击后 OCR/确认失败的 taint/recover。

### Skill 压力场景

按照 writing-skills 的 RED/GREEN/REFACTOR 方法，为 `trail-cw-prep` 设计压力场景：

1. Agent 看到“买角色、买经验、刷新商店”主题后，不得擅自发明具体优先级。
2. Agent 收到 `info skill_info=运营思路 ...` 后，必须把它当动态提醒读取，而不是忽略。
3. Agent 在特殊事件页不得继续套用普通备战流程。
4. Agent 在 mutation 结果未知时不得重复点击。

## 风险

- 完全移除当前攻略 artifact 是破坏性变更，需要同步清理文档和测试，否则旧语义会被测试拉回。
- `skill_info` 可能让默认文本过长；本轮先保留全文，后续根据真实输出再评估摘要规则。
- `trail-cw-prep` 如果写入具体策略，会和本轮“基础设施优先”目标冲突；实现时必须用测试防止策略占位被当成默认策略。
- `cw.shop.buy_exp` 涉及 UI 点击与 OCR 更新，必须按 mutation 命令处理失败恢复。

## 验证方式

实现完成后至少运行：

```text
uv run pytest tests/test_skill_structure.py -q --basetemp .trail/pytest-tmp/tests-skill-prep
uv run pytest tests/test_skill_registry.py tests/test_skill_routing_contracts.py -q --basetemp .trail/pytest-tmp/tests-skill-routing-prep
uv run pytest tests/test_cw_guide.py tests/test_cw_portal.py tests/test_cw_shop.py -q --basetemp .trail/pytest-tmp/tests-cw-prep
uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_events.py tests/test_cw_battle_run.py -q --basetemp .trail/pytest-tmp/tests-output-prep
```

若实现中新增或调整具体测试文件名，应以实际受影响测试为准，但必须覆盖上述四类合同：skill 拓扑、当前攻略 session 化、`cw.portal.select` 输出、`cw.shop.buy_exp` mutation 行为。
