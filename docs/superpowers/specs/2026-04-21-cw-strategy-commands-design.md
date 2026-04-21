# `cw strategy` 命令族设计说明

## 背景

当前仓库已经有完整的 `cw.portal.*` 命令族来处理开局投资环境页：`detect` / `select` / `refresh` / `restart`。

但用户提供的截图显示，对局内还存在独立的“请选择投资策略”页面。这个页面与投资环境页有三个关键差异：

1. 它发生在对局内，而不是开局首页之后。
2. 每张卡下方都有各自独立的刷新入口，不是一次刷新整组三张卡。
3. 它不需要像投资环境页那样挂攻略列表；真正需要暴露的是“当前已应用攻略是否推荐该投资策略”。

因此不能直接复用 `cw.portal.*` 的外部语义。与此同时，仓库里现有 `cw.invest.read|choose` 与 `stage.invest` 已经占用了“invest”这一粗粒度命名，但它们当前只提供“读取可选项编号/选择一个选项”的粗命令，无法覆盖“detect / 选择单张卡 / 按卡刷新”这套更接近 `portal` 的显式动作面。本设计的目标不是先删掉 `cw.invest.*`，而是补出更准确的 `cw.strategy.*` 原子命令族，并把主 skill 的首选编排入口切到这组新命令上。

## 目标

新增独立的 `trail cw strategy` 命令族，满足以下目标：

1. 提供 `detect` / `select` / `refresh` 三个原子命令。
2. `refresh` 必须按卡执行，强制要求 `--card-idx`。
3. 投资策略名称与描述使用 guide config 中的权威词表进行匹配归一。
4. 输出当前已应用攻略是否推荐该策略的稳定事实，但不挂攻略摘要列表。
5. 保持 renderer 家族、失败顺序、README 与 skill 边界稳定。

## 非目标

1. 不新增 `restart`，因为投资策略页不存在“整页重开”语义。
2. 不让 `cw.strategy.*` 参与开局投资环境选择或攻略筛选。
3. 不改变既有 `cw.portal.*`、`cw.guide.*` 的外部语义。
4. 不把投资策略页并入 `cw.stage.*` 的职责。
5. 不把 guide 推荐判断做成复杂的自动决策；这里只暴露稳定事实，最终仍由 agent 决定选哪张卡。

## 备选方案

### 方案 A：新增独立 `cw.strategy detect|select|refresh`（采纳）

- 在 `trail cw` 下新增 `strategy` 分组。
- `detect` / `select` / `refresh` 共享和 `portal` 相似的 snapshot 与 renderer 纪律，但字段改为投资策略页语义。
- `refresh` 强制传 `--card-idx`，只刷新单张卡。

优点：命名直观、职责清晰，最符合现有“显式原子动作命令”的项目边界，也最容易把投资环境页和投资策略页文档分清。

缺点：会新增一组 canonical command，需要同步 README、skills、tests 与帮助文本。

### 方案 B：继续挂在 `cw.portal` 下

- 例如增加 `cw.portal strategy-detect`，或通过 `--kind strategy` 复用同一组命令。

优点：表面上新增命令更少。

缺点：会把“开局投资环境页”和“对局内投资策略页”两套不同页面语义混在一起，README、help 与 skill 更难讲清；单卡 refresh 也会让 `portal refresh` 语义发散。

### 方案 C：只提供高层 skill，不补 CLI 原子命令

- 保持 CLI 不变，由 skill 直接根据截图做策略页决策。

优点：实现面最小。

缺点：违背仓库现有“CLI 提供显式固定动作、skill 负责编排”的边界，也不利于测试与恢复。

## 方案设计

### 1. CLI 与帮助文本

- 在 `trail/commands/cw.py` 中新增 `strategy_app = typer.Typer(...)`，并挂到 `cw_app.add_typer(..., name="strategy")`。
- 新增三个命令：
  1. `trail cw strategy detect --session <id>`
  2. `trail cw strategy select --session <id> --card-idx <n>`
  3. `trail cw strategy refresh --session <id> --card-idx <n>`
- 内部 canonical command 固定使用点号形式：
  1. `cw.strategy.detect`
  2. `cw.strategy.select`
  3. `cw.strategy.refresh`
- `cw` 总帮助、`strategy --help`、README、`skills/trail-cw/SKILL.md` 与相关子 skill 文档都要明确：
  1. `strategy` 只处理对局内投资策略页。
  2. `detect` 只重建当前三张策略卡快照，不点击。
  3. `refresh` 是单卡刷新，不会整页刷新，也没有 `restart`。
  4. `cw.stage.detect` 的 stage 值本轮继续保持 `invest`，不新增新的 coarse stage 名。
  5. 主 skill 从现在开始在 `stage=invest` 时首选 `cw.strategy.*`，而不是 `cw.invest.*`。
  6. `cw.invest.read|choose` 本轮保留为兼容/粗粒度入口，但 README 与 skill 都要明确它们不再是 agent 处理该页面的首选命令。

### 2. config 权威词表与 guide 偏好来源

投资策略页需要两个来源的数据：

1. guide config 权威词表，用来把 OCR 文本归一成稳定策略名/描述。
2. 当前 session 已应用攻略中的偏好字段，用来判断该策略是否被当前攻略推荐。

对应设计如下：

- 在 `trail.scenes.cw.guide.fetch_cw_guide_config()` 的归一化链路里补充投资策略词表。
- 该词表至少要包含：
  1. 权威策略名
  2. 描述（若上游提供）
  3. 供 OCR 归一匹配使用的稳定 id/名称字段
- `guide.config.cw` 默认文本必须保持当前冻结中文摘要不变：首行仍然只保留 `赛季/子赛季/大版本`，第二行仍然只保留 `搜牌档位/羁绊/角色/角色标签/投资环境` 这五个统计项。
- 投资策略词表只允许作为内部字段使用，或追加到 `guide.config.cw --format yaml` 的 raw data 中；不得新增默认 `info 投资策略=...` 之类的新文本行。
- `cw.strategy.*` 不查询 guide list，也不附加 guide 摘要；推荐命中只读取 `session.scene_state["cw"]["guide"]` 中已保存的：
  1. `first_fight_augments`
  2. `second_fight_augments`

### 3. scene state 与 snapshot 边界

- 投资策略页状态独立存放在 `session.scene_state["cw"]["strategy"]`，不要和 `cw.portal` 混用。
- `CwSceneState` 默认结构需要新增 `strategy` 键，避免实现时在多处手写散落 dict。
- snapshot 结构与 `portal` 家族保持相似，但改为策略页字段：
  1. `cards`
  2. `stale`
  3. 仅在需要时保留用于调试的附加事实，但不改变 coarse page 真值
- `detect` / `refresh` 会生成 fresh snapshot，并写回 `cw.strategy`。
- `select` 成功后会把 `cw.strategy` 标记为 `stale=True`，防止旧卡信息被再次消费。
- `select` 继续要求 fresh snapshot；如果当前只有 stale snapshot，则返回与现有命令一致风格的 snapshot-required 错误。
- 开局投资环境页与局内投资策略页在 coarse page 真值上必须严格区分：
  1. `cw.portal.*` 继续只在 `entry.page="invest"` 语义下工作。
  2. `cw.strategy.*` 只在对局内页面工作，coarse page 必须保持 `in_game`，不得把局内策略页也写成 `entry.page="invest"`。
  3. `cw.strategy.*` 的授权条件必须是 live-first 页面/阶段检测；缓存 state 只能补充错误信息，不能单独放行动作。

### 3.1 状态失效矩阵

- 以下命令成功后必须把 `cw.strategy` 视为失效：
  1. `cw.enter`
  2. `cw.start`
  3. `cw.portal.restart`
  4. `cw.guide.apply`
- 以下命令成功后必须把 `cw.stage` 置为 `{"stale": True}`，因为页面已发生显式动作：
  1. `cw.strategy.select`
  2. `cw.strategy.refresh`
- `cw.strategy.detect` 只刷新 `cw.strategy` snapshot，本身不改 coarse `entry.page`。

### 4. 页面识别与动作语义

- `cw.strategy.detect`：
  1. 要求当前已在投资策略页。
  2. 当前 coarse page 必须仍属于 `in_game`，但 live stage/页面标题要能确认是“请选择投资策略”页。
  2. OCR 当前三张卡。
  3. 基于 config 词表归一策略名与描述。
  4. 读取每张卡的刷新次数。
  5. 结合当前 guide state 计算推荐命中标记。
  6. 写回 fresh snapshot。
- `cw.strategy.select`：
  1. 要求当前在投资策略页。
  2. 要求存在 fresh strategy snapshot。
  3. 点击目标卡，再点击确认。
  4. 返回所选卡摘要，并把 strategy snapshot 标 stale。
- `cw.strategy.refresh`：
  1. 要求当前在投资策略页。
  2. 强制要求 `card_idx` 为 1|2|3。
  3. 只点击该卡下方对应的刷新入口。
  4. 等待页面稳定后，重新采样整组三张卡并写回新 snapshot。
  5. 结果返回刷新后的完整三卡，而不是只回单张卡，方便后续直接继续决策。

这里再冻结几条实现不可偏离的细则：

1. `detect` / `refresh` 可以在没有历史 strategy snapshot 时冷启动；`select` 必须依赖 fresh snapshot。
2. `refresh` 成功后必须整组三卡全量重采样并原子覆盖，禁止只替换一张卡、保留另外两张旧卡。
3. 每次 `detect` / `refresh` 完成后，`card_idx` 与具体卡面身份的绑定都以新 snapshot 为准，旧绑定全部作废。
4. `select` 的成功标准本轮先固定为“点击目标卡并点击确认后，动作请求成功返回”；但成功后仍必须把 `cw.strategy` 与 `cw.stage` 一并视为失效，后续决策必须重新 `cw.stage.detect`。

### 4.1 与现有 `stage=invest`/`cw.invest.*` 的迁移边界

- 本轮不修改 `cw.stage.detect` 返回值；投资策略页仍然落在现有 `stage=invest`。
- 但主编排语义要明确切换：`trail-cw` 主 skill 在 `stage=invest` 时，应优先调用 `cw.strategy.detect|refresh|select`。
- `trail-cw-replenish` 需要同步更新，去掉“`invest` 由 replenish skill 直接处理”的旧说法，并明确看到“请选择投资策略”页面时要把控制权交回主 skill 或新的 strategy 子 skill。
- `cw.invest.read|choose` 本轮暂不删除，但文档与 skill 必须将其降级为兼容/粗粒度入口，不再作为 agent 的首选流程命令。

### 5. 策略匹配与推荐命中字段

投资策略页的匹配规则要尽量靠近 `portal` 对投资环境的做法：

- 先从 OCR 中提取每张卡的标题、描述、刷新次数等原始文本。
- 使用 config 权威词表做 best-effort 归一匹配，得到稳定策略名与描述。
- 若 OCR 文本无法稳定命中权威词表，则仍返回卡片，但字段应尽量退化为原始文本而不是直接失败。

每张卡额外暴露一个稳定的“当前攻略推荐命中”事实，建议语义固定为三态：

1. `攻略推荐=优选`：策略名命中当前 guide 的 `first_fight_augments`
2. `攻略推荐=次选`：策略名命中当前 guide 的 `second_fight_augments`
3. `攻略推荐=否`：当前没有命中优选/次选；这可能表示“当前 guide 未推荐”，也可能表示“当前 session 尚无可用 guide”

这里不建议输出布尔值，因为用户明确区分了“优选/次选”两档推荐强度；直接压成 `0/1` 会损失决策价值。`攻略推荐` 在协议里是稳定枚举，不是布尔值。

为了避免“无 guide”和“明确不推荐”被完全混淆，本轮默认文本协议固定同时追加一个稳定事实：

- `info 已加载攻略=0|1`

这样 `攻略推荐=否` 仍可保留为三态之外的稳定枚举，而 agent 可以再结合 `已加载攻略` 判断这是“未知”还是“明确未命中”。`cw.strategy.detect` / `cw.strategy.refresh` 的 success 路径都必须输出这一行，位置固定在所有 `opt` 行之后、`warn/ref` 之前。

### 6. renderer 与默认文本协议

`cw.strategy.*` 沿用现有 renderer 家族，不新增随意格式：

1. `cw.strategy.detect` / `cw.strategy.refresh` 复用与 `cw.portal.detect` / `cw.portal.refresh` 同类的 cards family。
2. `cw.strategy.select` 复用与 `cw.portal.select` 同类的 summary family。

默认文本协议建议固定为：

- `cw.strategy.detect` / `cw.strategy.refresh`
  1. 首行：`ok cw.strategy.detect cards=<n>` / `ok cw.strategy.refresh cards=<n>`
  2. 有截图时先输出 `shot path=...`
  3. 每张卡使用 `opt` 行，至少保留：
     - `idx=<n>`
     - `投资策略=...`
     - `攻略推荐=<优选|次选|否 中的单个具体值>`
     - `刷新次数=<n>`
  4. 描述单独使用第二条 `opt idx=<n> 说明=...`
  5. 固定输出 `info 已加载攻略=0|1`
- `cw.strategy.select`
  1. 首行：`ok cw.strategy.select idx=<n> 投资策略=...`
  2. 有截图时输出 `shot path=...`

失败路径继续遵守项目现有顺序：

1. `request`
2. `shot`
3. `why`
4. `warn`
5. `ref`
6. `recover`

`cw.strategy.*` 不进入 YAML allowlist；显式 `--format yaml` 时应返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。

兼容性细则必须写死：

1. 只要当前结果有截图，就必须输出 `shot path=...`。
2. failure 结果只要带 `request_id`，就必须输出 `request id=<id>`。
3. `recover action=daemon.request_status ...` 只有在结果未知或当前失败显式可恢复时才允许出现。
4. `cw.strategy.* --format yaml` 必须沿用现有 non-allowlist command 的 failure renderer，不得新增 YAML envelope，也不得做另一套前置报错格式。

### 7. daemon 与 capture 路径

- 在 `trail/daemon/cw_service.py` 中增加三个 handler。
- `cw.strategy.detect` 与 `cw.strategy.refresh` 需要像 `cw.portal.*` 一样返回截图与结构化 cards 数据。
- `cw.strategy.select` 需要返回已选择卡片摘要，并带 request-scoped screenshot。
- 是否纳入 mutating method 取决于动作本身：
  1. `detect` 不点击，应保持 non-mutating。
  2. `select` / `refresh` 会点击，应沿用现有 mutating capture 纪律。

### 8. README 与技能边界

- README 至少要同步更新 4 个现有段落，而不是只补一处概览：
  1. `货币战争流程`
  2. `命令面概览`
  3. `输出约定` 里的示例块
  4. `Guide 字段语义`
- README 需要至少补 4 类具体内容：
  1. 一条边界句：开局投资环境页用 `cw.portal.*`，局内投资策略页用 `cw.strategy.*`，普通局内 invest 事件/兼容入口才是 `cw.invest.*`
  2. 一条流程示例：`cw stage detect -> cw strategy detect -> cw strategy refresh --card-idx <n> -> cw strategy select`
  3. 三个默认文本示例块：`cw.strategy.detect`、`cw.strategy.refresh`、`cw.strategy.select`
  4. 一条 Guide 语义说明：`优选投资策略/次选投资策略` 会成为 `cw.strategy.*` 中 `攻略推荐` 的来源，不用于 `cw.portal.*`
- `cw invest --help` 也必须同步改成防误用文案：它是兼容/粗粒度入口，不用于“请选择投资策略”页面；当 screenshot 或页面标题显示策略页时，应改走 `cw.strategy.*`。
- `skills/trail-cw/SKILL.md` 需要写清：当 stage/截图表明当前处于投资策略页时，应优先使用 `trail cw strategy detect|select|refresh`，不要误用 `cw.portal.*`。
- `skills/trail-cw-replenish/SKILL.md` 需要删掉或改写“invest 由本 skill 处理”的旧边界，明确看到“请选择投资策略”页面时交回主 skill。
- `skills/trail-cw-guide/SKILL.md` 需要补一句：guide 中的 `优选投资策略/次选投资策略` 会被 `cw.strategy.*` 消费为 `攻略推荐` 事实。
- 如果后续选择新增独立 `skills/trail-cw-strategy/SKILL.md`，则需要在 spec 的实现计划阶段一并明确其 owner 关系；本轮 spec 先固定由主 `trail-cw` skill 直接接管 `stage=invest`。
- 需要明确提醒 agent：
  1. 投资策略不用于开局攻略反查。
  2. 当前攻略会影响“推荐命中”字段，但 CLI 只暴露事实，不替 agent 自动做最终选择。

## 数据流

`trail cw strategy detect --session <id>`

1. CLI 调用 daemon method `cw.strategy.detect`
2. daemon 进入带截图的 CW 执行路径
3. scene 层确认当前在投资策略页并 OCR 三张卡
4. 使用 config 词表归一名称/描述
5. 从 `session.scene_state["cw"]["guide"]` 读取当前攻略的优选/次选投资策略
6. 生成带推荐命中标记的 snapshot，并写回 `session.scene_state["cw"]["strategy"]`
7. renderer 输出 cards 摘要

`trail cw strategy refresh --session <id> --card-idx <n>`

1. 点击第 `n` 张卡自己的刷新入口
2. 等待页面稳定
3. 重新采样整组三张卡
4. 写回新的 strategy snapshot
5. 输出刷新后的三卡摘要

## 测试计划

至少补齐以下测试：

### 1. `tests/test_cw_strategy.py` 或对应 scene 测试文件

- `detect` 能在策略页生成 fresh snapshot。
- `select` 需要 fresh snapshot，成功后会把 snapshot 标 stale。
- `refresh` 强制要求合法 `card_idx`，且只点击对应卡的刷新入口。
- 策略名会走 config 词表归一。
- `攻略推荐` 会正确区分优选 / 次选 / 否。
- 当前无 guide 时，策略卡仍成功返回，但 `攻略推荐=否`。

### 2. `tests/test_daemon_protocol.py`

- 新 method 正确进入带截图的执行路径。
- `cw.strategy.detect` 不进入 mutating methods。
- `cw.strategy.select` / `cw.strategy.refresh` 的 side effect 与 screenshot 语义正确。
- 覆盖 request envelope、capture、mutating / non-mutating 分类与 request status 相关语义，但不在这一层冻结最终 stdout 文本顺序。

### 3. `tests/test_cw_rpc_contracts.py`

- CLI 命令正确映射到 `cw.strategy.detect|select|refresh`。
- `refresh` 正确透传 `card_idx`。
- stdout 冻结为约定的 canonical command 与字段顺序。

### 4. `tests/test_output_rendering.py`

- 冻结 `cw.strategy.detect` / `cw.strategy.refresh` 的首行、`shot`、`opt` 字段顺序。
- 冻结 `cw.strategy.select` 的摘要首行。
- 增加 renderer 注册断言，明确 `cw.strategy.detect|refresh` 与 cards family、`cw.strategy.select` 与 summary family 的映射关系。
- 验证 `--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
- 验证失败路径顺序仍满足协议要求。
- 验证 `攻略推荐=否` 与 `刷新次数=0` 不能被省略。
- 验证 `info 已加载攻略=0|1` 必出，且固定在所有 `opt` 行之后。

### 5. 文档与 help 断言

- `tests/test_atomic_commands.py` 中的 `cw --help`、`cw strategy --help`、`cw strategy refresh --help` 文案断言，以及 `CW_HELP_COMMANDS` 新增 `strategy`。
- `tests/test_atomic_commands.py` 中的 `cw invest --help` 文案断言，明确其“兼容/粗粒度入口、非策略页首选”的边界。
- README 原文断言：边界句、流程示例、三个 stdout 示例块。
- `skills/trail-cw/SKILL.md`、`skills/trail-cw-replenish/SKILL.md`、`skills/trail-cw-guide/SKILL.md` 的边界句断言。
- 必须同步更新项目级 `AGENTS.md`，为 `cw.strategy.detect|refresh|select` 补充首行 must-keep 事实、正文字段与相关边界说明。

## 风险与控制

1. 上游 config 未必已经提供现成的投资策略词表。
   处理：实现前先确认 config 实际 shape；若缺失，则在 spec 对应实现计划里明确“如何从现有字段或额外接口推导词表”。

2. 投资策略页的刷新按钮位置是按卡分布，不能偷用 `portal.refresh` 的全局按钮坐标。
   处理：scene 层应把每张卡的刷新点作为独立几何规则或模板识别逻辑，不共享 `portal` 的刷新入口。

3. 当前 guide 可能不存在。
   处理：这不应导致命令失败，而应稳定退化到 `攻略推荐=否`。

4. “优选/次选/否”如果后续还要细分，可能影响文本协议。
   处理：本轮先冻结三态；若未来要扩展，需同步更新 renderer、README、测试与 AGENTS 约束。

## 结论

采纳方案 A：新增独立 `cw.strategy` 命令族，只提供 `detect|select|refresh`，其中 `refresh` 为按卡单独刷新；投资策略名通过 config 权威词表归一，输出不挂攻略摘要，而是稳定暴露当前攻略对该策略的推荐命中事实。
