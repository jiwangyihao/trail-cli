# 项目输出协议约束

## renderer 家族

- 新命令必须先归类到已有 renderer 家族，再决定首行事实与正文前缀；不要把每个命令都做成独立的随意格式。
- 常见 renderer 家族包括：检测/状态摘要、列表读取、攻略结果、control-plane 状态与恢复路径。
- 如果确实需要新增 renderer 家族，先在 `trail/output/rendering.py` 固定首行字段顺序、正文前缀和 must-keep 事实，再补对应测试。

## canonical command 与前缀冻结

- 默认模式一律使用内部 canonical command 名，必须是点号形式，例如 `cw.shop.buy_slot`、`daemon.request_status`、`screen.shot`；不要写成 `cw.shop.buy-slot`、`daemon request-status`。
- 默认正文前缀只允许使用 `request`、`shot`、`item`、`guide`、`text`、`slot`、`opt`、`why`、`warn`、`ref`、`recover`、`info`；`debug` 仅用于 `--verbose` 追加层。
- 如果某个命令需要新增正文前缀，先更新 `trail/output/rendering.py` 的 renderer、README 示例和对应测试，再写文档。

## 默认模式必出事实

- 默认模式的第一行必须是 `<ok|fail> <canonical_command> <核心事实...>`。
- 只要当前命令产出截图，就必须输出 `shot path=...`。
- 带截图的 success 结果在 `shot path=...` 之后必须紧跟 `info read_image_first=1`，提示 Agent 先读本次原始截图，再消费后续压缩文本。
- envelope 顶层若带 `screenshot`，同步生成 `image_guidance.read_image_first=1`；该元数据只存在于 envelope 顶层，不下沉到命令 `data`。
- 失败结果只要带 `request_id`，就必须输出 `request id=<id>` 供恢复或排障使用。
- 只有结果未知或当前失败显式可恢复时，才输出 `recover action=daemon.request_status request=<id>`。
- 会影响下一步决策的 `0`、`false`、`count`、`more`、`tainted` 不能因为“看起来为空”而省略。
- `guide.fetch.cw` 默认首行至少保留 `攻略标题`、`攻略码`、`版本`、`最低金币`、`最低等级`、`中期等级`；`适用超频博弈`、`星徽攻略`、`专家顾问` 改以 `#标签` 形式并入 `guide 攻略标签=...` 同一行。

## 正文顺序约束

- success 路径必须先输出首行，再按需要输出 `shot`；若当前结果带截图，再紧跟 `info read_image_first=1`；然后才是 `item`、`guide`、`text`、`slot`、`opt`、其余 `info` 这类实体行，最后才是 `warn`、`ref`。
- 若命令命中已配置 workflow handoff，success 路径允许在 `warn`、`ref` 之后追加一行尾行强提示 `info handoff_skill=... handoff_strength=... handoff_reason=...`，且该行必须是 success 输出最后一行。
- failure 路径正文顺序固定为：`request` -> `shot` -> `why` -> `warn` -> `ref` -> `recover`；`debug` 只能在 `--verbose` 时追加在最后。
- 不要为了单个命令“更自然”而重排 failure 行顺序；恢复链路必须稳定可扫读。

## 冻结字段与编码规则

- 高频冻结字段至少包括：`session_id` -> `session`、`next_page_token` -> `next`、`similarity` -> `sim`、`confidence/score` -> `score`、`bbox/rect` -> `box`、条目序号 -> `idx`、人类消息 -> `msg`。
- 默认文本统一使用 `key=value`；除首行的 `ok|fail` 和 `<command>` 外，不再新增位置参数。
- 布尔值统一编码为 `0/1`；含空格、引号、反斜杠、换行、等号或逗号歧义的值必须使用双引号。
- `box` 统一压成 `left,top,width,height`，不要回退成嵌套对象。
- `image_guidance.read_image_first=1` 是 envelope 顶层冻结元数据，不进入默认文本业务 body，也不下沉到各命令 `data`。
- 只省略语义缺失值，不能省略会影响下一步动作的 `0`、`false`、`count`、`more`、`tainted`。

## verbose 事件约束

- `--verbose` 只追加开发/排障层，不改变默认文本协议的事实集合和顺序。
- `--verbose` 事件必须统一经过 `trail.output.debug.collect_debug_events` 和 `trail.output.debug.render_debug_lines` 写入。
- shared helper 的 major action trace 固定输出到 verbose `debug kind=trace ...` 行；finalized 事件至少保留 `ts=<UTC RFC3339 毫秒时间戳>` 与 `ok=0|1`。
- `trace/context` 边界固定：`trace` 只承载 finalized helper 动作事件；`context` 只承载跨动作请求级事实，不能再拿来补动作结果。
- OCR 的 mode/retry 事实不再作为 top-level debug context 暴露，而是通过 `debug kind=trace step=ocr ...` 出现；legacy trace 继续兼容渲染。
- 禁止各命令直接拼接 `debug ...` stdout，也不要把临时调试信息混入默认模式。
- `--verbose` 不为 `image_guidance` 新增独立 guidance 事件；默认模式里的 `info read_image_first=1` 仍只在 success 文本层出现。
- 默认模式不得泄漏 verbose 事件；需要排障时才通过 `--verbose` 查看 `debug kind=...` 行。
- recorder / collect / render 继续按 best-effort 处理；即使 debug 收集失败，也只能丢 debug，不得改默认文本的 success/failure/recover 语义。

## YAML allowlist 约束

- `--format yaml` 是结构化兜底，不是默认主通道。
- 只有进入 YAML allowlist 的命令才允许输出 YAML；新增命令前先确认是否真的存在结构化兜底需求。
- 当前 YAML allowlist 是 `daemon.status`、`state.dump`、`guide.fetch.cw`、`guide.config.cw`。
- `image_guidance` 不进入 YAML body；YAML 继续只回落命令数据本体。
- 非 allowlist 命令不要回退到旧式结构化 envelope；保持默认文本协议，并在不支持时显式返回格式不支持错误。

## 文档与测试同步要求

- 新命令或现有命令输出发生变化时，必须同步更新 `README.md` 示例与说明。
- 所有会影响普通用户或 Agent 使用方式的变更，都必须同步更新相关 `skills/*/SKILL.md`，不要只改 README 或 spec/plan。
- 新命令至少要补 renderer 单测，以及受影响的 CLI stdout 测试或 RPC/契约测试增量。
- 如果新增前缀词、冻结字段、恢复语义或 `--verbose` 事件类型，必须同步更新本文件与 README。
- 评审输出变更时，优先检查：renderer 家族是否明确、默认模式必出事实是否稳定、YAML allowlist 是否合理、README 与测试是否已同步。

## guide.fetch.cw 约束

- `guide.fetch.cw` 默认首行按中文字段返回攻略核心事实，至少保留：`攻略标题`、`攻略码`、`版本`、`最低金币`、`最低等级`、`中期等级`。
- `guide.fetch.cw` 正文继续使用 `guide` 行补充：`攻略标签`、`羁绊列表`、`投资环境`、`优选投资策略`、`次选投资策略`、`简易装备优先度`、`进阶装备优先度`、`运营思路`，以及按阶段展开的阵容摘要与角色推荐装备；`适用超频博弈`、`星徽攻略`、`专家顾问` 折叠成 `#标签` 并入 `攻略标签` 同一行，`羁绊列表` 与阶段 `羁绊` 在上游提供层数时要保留成 `6贝洛伯格` 这类形式。
- `guide.fetch.cw` 允许 `--format yaml`，用于在默认文本之外回落到完整结构化 `data`。

## guide / cw 攻略摘要约束

- guide.list.cw 的默认文本改用 攻略ID/攻略标题/版本/主C/攻略标签/最终阵容，并继续保留 `count`、`more`、`next`、`idx` 这些冻结控制字段。
- `guide.list.cw` 非分组条目字段冻结为 `攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`；第二行固定使用 `guide ... 最终阵容=...`，不能回退成 `final_role_cards` 或布尔技术位直出。
- `guide.list.cw` 分组视图固定使用 `guide 投资环境=... count=... more=...`；只有分组行允许携带 `next=...`，顶层 grouped 首行不带 `next`。
- `cw.start` / `cw.portal.select|refresh|restart` 的 portal 卡片字段使用 `投资环境/说明/待收集`；`待收集` 必须统一编码为 `0/1`，即使为 `0` 也不能省略。
- `cw.portal.select` success 首行固定为 `ok cw.portal.select idx=... 投资环境=...`；`cw.start` / `cw.portal.*` 下挂攻略摘要继续复用 `guide.list.cw` 的中文条目与 `最终阵容` 语义。
- `cw.portal.select` 若响应 `data.skill_info` 非空，默认正文使用 `info skill_info=运营思路 text=...`；该行属于 success entity/info 行，必须在 `warn`、`ref` 之前输出，不新增正文前缀、不属于 verbose/debug、不进入 YAML allowlist。
- `cw.portal.select` 命中 workflow handoff 时，success 最后一行必须是 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。
- `cw.strategy.detect|refresh` success 首行固定为 `ok cw.strategy.<...> cards=<n>`。
- `cw.strategy.select` success 首行固定为 `ok cw.strategy.select idx=... 投资策略=...`。
- `cw.strategy.detect|refresh` 的 cards family 正文字段固定使用 `投资策略/攻略推荐/刷新次数`；说明继续使用 `opt idx=... 说明=...`。
- `cw.strategy.detect|refresh` 必须输出 `info 已加载攻略=0|1`，且固定在所有 `opt` 行之后。
- `cw.shop.buy_exp` 属于 shop action renderer family；canonical command 是 `cw.shop.buy_exp`，success 首行固定为 `ok cw.shop.buy_exp opened=1 stale=0 count=<n>`，正文先输出 `item idx=... slot=... name=... cost=...`，再输出 snapshot facts `coins/level/exp/reserve_full/team_size`。
- `cw.shop.buy_exp` 的 `team_size=null` 是 must-keep null fact；`cw.shop.buy_exp` 不在 YAML allowlist，`--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
- `cw.battle.run` 属于检测/状态摘要 renderer 家族；success 首行固定使用 `ok cw.battle.run status=... result=... stage=... stale=... in_battle=...` 的顺序，缺失语义值按默认省略规则处理。
- `cw.battle.run` 的 `status=in_progress` success 必须输出 `info next_action=cw.battle.run why=battle_flow_not_finished`，提示 Agent 先看截图并在仍处于 battle flow 时重跑 `cw.battle.run`。
- `cw.battle.clear_in_progress` canonical command 固定为 `cw.battle.clear_in_progress`，归入检测/状态摘要 renderer 家族；success 首行固定为 `ok cw.battle.clear_in_progress cleared=0|1`。
- `cw.battle.clear_in_progress` 只清 battle.run 的内部续跑提示位，不清 battle 摘要、`last_result`、`last_screenshot` 或阶段事实；它不产出截图，不加入 YAML allowlist。
- `cw.shop.scan|status` 的 stage 投影固定使用 `stage_level/stage_exp/stage_team_size/stage_status_stale`，属于既有 `info` 行，不新增正文前缀。
- `cw.shop.scan|status` 必须保留 `stage_status_stale=0|1`；只有 `stage_status_stale=0` 时才允许输出 `stage_level/stage_exp/stage_team_size`，stale 或缺失时不得把旧值渲染成有效事实。
- `cw.slots.read` 会刷新 `cw_state.stage.status`；`cw.shop.scan` 只扫描商店页商品/金币切片，并只投影 session 中已有的 `cw_state.stage.status`，不得重新 OCR 全局状态。
- `cw.guide.current|apply` 使用 `攻略ID/攻略标题/攻略码/版本`，并以 `info 攻略快照ID=...` 表示 artifact id。
- `cw.guide.current|apply` 的 `攻略快照ID` 是 artifact id / 恢复追踪 id，不是 `shot path` 截图路径；`current/apply` 只看当前已选攻略摘要，完整攻略仍由 `guide.fetch.cw` 提供。
- `guide.fetch.cw --select` 只负责把当前攻略写入 session，不扩张 success / YAML shape；真正回到开局链路后，由 `cw.portal.select` 成功时自动兑现当前已选攻略。
- `guide.config.cw` 使用 `赛季/子赛季/大版本/搜牌档位/羁绊/角色/角色标签/投资环境`；这五个统计项即使为 `0` 也必须保留。
- `guide.config.cw --format yaml` 仍然先输出中文摘要，再追加原英文 key 的 YAML shape，不得回退成纯英文首屏。
- `cw.hand.sell_plan` success 首行固定为 `ok cw.hand.sell_plan count=... reference_only=1 candidates=... todos=...`；该命令只提供 Agent 参考信息，不是权威出售计划。
- `cw.hand.sell_plan` 正文 `slot` 行固定使用 `pos/name/star/target_star/current_star/分类/推荐度/priority/protected/reason`，缺失值按默认 key=value 省略规则处理。
- `cw.hand.sell_plan` 缺失参考信息用 `info todo=stage|team_size|boss_preview|missing_final|stage_granularity|star`；不新增正文前缀，不进入 YAML allowlist。

## skill 拓扑约束

- `trail-hsr` 是对外总入口；`trail-<scene>-entry` 是对外场景入口；`trail-hsr-advanced` 是内部恢复层，不作为用户直达入口。
- `trail-cw-entry` 现在是当前 active public 的货币战争 scene entry，不是整局 owner。
- `trail-cw-guide` 是当前 active public 的攻略选择 skill，可 direct-user 命中，也可以由 `trail-cw-entry` 在“攻略优先 / 先定攻略”场景下推荐切入。
- `trail-cw-guide` 不是 scene entry、不是默认 owner、也不是整局 owner；真正进入开局流程仍要回到 `trail-cw-entry`。
- `trail-cw-portal` 是当前 active internal 的投资环境页 skill，主要在 `trail cw start` 或 `trail cw portal refresh` 成功停留在投资环境页后由 scene entry 内部切入。
- `trail-cw-portal` 不是 direct-user 公共入口、不是 scene entry、也不是 owner；它负责 `portal detect/refresh/restart/select` 与环境优先逻辑，若攻略未定则切到 `trail-cw-guide` 的无人值守模式。
- `trail-cw-prep` 是当前 active internal 的普通备战阶段 skill，只能由 `cw.portal.select` success 后的 workflow handoff 或上游内部阶段切入。
- `trail-cw-prep` 不是 public scene entry、不是 direct-user、不是 owner；`cw.portal.select` success final handoff 固定指向 `trail-cw-prep`。
- 只有 registry 中 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前入口出现在 active 文档与测试中。
- 当命令 success 输出 `info handoff_skill=... handoff_strength=strong ...` 时，Agent 应把它视为推荐的下一步 skill 切换信号；当前固定映射包括 `cw.enter -> trail-cw-entry` 与 `cw.portal.select -> trail-cw-prep`。
- `AGENTS.md` 的 active 拓扑说明不得出现 archive skill 名称或 legacy 场景 skill 名称。
- 任何 active skill 都不得直接或间接调用 archive skill。
- 仍然禁止 legacy 货币战争 archive skill 回流为 active owner、默认 owner 或推荐入口。
