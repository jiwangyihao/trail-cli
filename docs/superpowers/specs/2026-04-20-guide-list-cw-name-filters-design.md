# `guide list cw` 名称筛选与版本摘要设计

## 目标

把 `guide list cw` 从“主要面向人工查 id 的原始筛选接口”收口成“Agent 可直接用名称决策的攻略筛选接口”，同时补齐版本摘要，降低模型在每局都先跑 `guide config cw` 的依赖。

本轮要同时解决四类问题：

1. 现在 trait 只暴露 `--trait-id`，Agent 不能直接按羁绊名称筛攻略。
2. 上游原生支持 `role_ids`，但当前 CLI 和 scene 层没有角色筛选入口。
3. 角色名称存在高相似/包含关系时，模型容易把目标角色认错，需要稳定的候选提示和不同级别的警告语义。
4. `guide list cw` 当前输出缺少 `version`，模型在不 fetch 详情的情况下难以判断攻略是否适用于当前版本。

## 背景

当前实现事实如下：

- `trail/commands/guide.py` 的 `guide list cw` 只暴露 `--trait-id`、`--portal`、`--portal-id`、`--match-change-job`、`--match-hard` 等参数，没有名称级 `trait` / `role` 入口，也没有详细 help 文案。
- `trail/scenes/cw/guide.py` 的 `_build_guide_list_request_payload()` 已经固定向上游发送 `role_ids` 和 `trait_ids`，但当前 `role_ids` 永远是空列表。
- `fetch_cw_guide_config()` 已经能拿到 `traits`、`roles`、`role_tags`、`portal_list`，因此本地具备做名称解析的 canonical 枚举来源。
- `_normalize_lineup_summary()` 已经把上游攻略条目的 `version` 归一化进 payload，但 `trail/output/rendering.py` 的 `_render_guide_list()` 没有把它输出到文本协议。
- 项目协议要求：`guide.list.cw` 继续走现有 renderer 家族；默认文本不新增 YAML allowlist，不打破 success / failure 的行序，不新增未登记前缀。

## 非目标

本轮不做下面这些扩面：

- 不改 `guide.fetch.cw` 的协议。
- 不新增 `guide list cw` 的 YAML allowlist。
- 不把 trait 过滤扩成多个值；trait 这轮仍只支持单个值。
- 不在 CLI 侧直接调用 `guide config cw` 做本地预解析；名称解析统一留在 scene 层。
- 不引入新的 renderer 家族。
- 不改现有 `portal` 过滤语义，只补 help 文案和与新筛选并存时的说明。

## 命令接口

`guide list cw` 新增并冻结以下参数：

- `--trait <name>`
  - 单值。
  - 与 `--trait-id` 互斥。
  - 用于按羁绊名称直接筛选。
- `--trait-id <id>`
  - 保持现有单值语义。
  - 与 `--trait` 互斥。
- `--role <name>`
  - 允许重复传入多个值。
  - 与 `--role-id` 互斥。
  - 用于按角色名称直接筛选。
- `--role-id <id>`
  - 允许重复传入多个值。
  - 与 `--role` 互斥。
  - 走精确 id 过滤，不做模糊匹配。

保留现有参数：

- `--page`
- `--limit`
- `--order`
- `--next-page-token`
- `--match-change-job`
- `--match-hard`
- `--portal`
- `--portal-id`

互斥矩阵：

- `--trait` 和 `--trait-id` 互斥。
- `--role` 和 `--role-id` 互斥。
- `--portal` 和 `--portal-id` 继续互斥。

help 文案需要明确说明：

- 名称输入用于“免查 config 的直接筛选”。
- id 输入用于“精确复现或脚本固化”。
- `--role` / `--role-id` 可重复传入多个值。
- `--trait` 当前只支持单个值。
- `--portal` / `--portal-id` 继续保留原有语义。

## 代码边界

这一轮的职责边界固定如下：

- `trail/commands/guide.py`
  - 负责新增 CLI 参数和 help 文案。
  - 继续把原始 payload 透传给 daemon。
  - 互斥参数校验沿用现有 `--portal` / `--portal-id` 模式，在 CLI 本地直接失败并返回 `GUIDE_INPUT_INVALID`。
- `trail/daemon/command_service.py`
  - 负责把新增 `trait` / `role` / `role_id` 透传给 `fetch_cw_guide_list()`。
  - 继续在 daemon 层做布尔参数解析。
  - 负责把 scene 返回的 role warning 提升到 envelope 顶层 `warnings`。
  - 负责把 trait 名称未命中映射成标准 failure envelope。
- `trail/scenes/cw/guide.py`
  - 负责名称解析、候选构建、warning 语义、上游 `trait_ids` / `role_ids` 生成。
  - success 返回值里允许带 `role_candidates` 与 `role_warnings`。
  - trait 名称未命中时抛出专用 lookup error，不在 scene 层手工拼最终 failure envelope。
- `trail/output/rendering.py`
  - 继续由 `_render_guide_list()` 渲染。
  - 只补 `version` 和新的候选描述实体行 / warning 渲染。

`guide.config.cw` 的公开 contract 本轮不扩面；scene 层如需 role tag 或更细粒度 lookup，可以直接使用 `trail.scenes.cw.guide` 内部基于原始 config 数据构建的私有索引，但不把这些字段额外暴露到 `guide.config.cw` 的对外返回里。

## trait 解析语义

trait 过滤的 canonical 名称/id 数据源固定为 `fetch_cw_guide_config()["traits"]`。

解析规则：

1. `--trait-id` 继续走精确 id。
2. `--trait` 先按名称精确匹配。
3. 若没有精确匹配：
   - 返回失败结果。
   - 错误码固定为 `GUIDE_TRAIT_INVALID`。
   - `why` 固定为 `guide trait invalid: <query>`。
   - warnings 中带最相近的 3 个 trait 候选。
4. trait 不做自动 fuzzy resolve，不返回攻略列表。
5. `--trait` / `--trait-id` 的互斥继续在 CLI 本地校验；scene 层只处理已经通过互斥检查后的单一路径。

候选排序：

- 使用现有 portal 候选同级别的字符串相似度思路。
- 先按名称归一化后的相似度降序。
- 同分时按 canonical trait name 字典序稳定排序。

failure 路径的候选 warning 需要冻结为：

- `warn trait=<name> trait_id=<id> score=<score>`

trait miss 的最终 failure envelope 仍必须服从仓库全局 failure 顺序：

1. `fail guide.list.cw code=GUIDE_TRAIT_INVALID`
2. `request id=<id>`（若当前请求存在 request_id）
3. `shot path=...`（若当前 failure 携带截图）
4. `why msg="guide trait invalid: <query>"`
5. 之后才是 trait 候选 `warn ...`
6. 最后才允许出现 `ref` / `recover`

本节只额外冻结新的 trait 候选 warning 形状，不推翻全局 failure 协议。

## role 解析语义

role 过滤的 canonical 名称/id 数据源固定为 `fetch_cw_guide_config()["roles"]`；若需要补候选描述里的 `role_tags`，则由 scene 层私有 lookup 从原始 config 补齐，但不改公开 `guide.config.cw` contract。

### 输入规则

1. `--role-id` 走精确 id，允许多个值。
2. `--role` 走名称解析，允许多个值。
3. `--role` 与 `--role-id` 互斥。
4. 多个 role query 按传入顺序逐个解析，最终 `role_ids` 去重但保持首次出现顺序。
5. `--role` / `--role-id` 的互斥继续在 CLI 本地校验；scene 层只处理已经通过互斥检查后的单一路径。

### 名称归一化

角色名称比较需要统一做轻量归一化：

- 去首尾空白。
- 折叠内部连续空白。
- 统一小写。
- 不额外删字，不做拼音或别名词典扩展。

相似度主分值冻结为与 portal 候选同源的 `SequenceMatcher(...).ratio()`；排序使用未 round 的原始分值，协议输出统一 round 到两位小数。

### 两类语义

#### 1. 没有精确匹配

当某个 `role` query 没有精确命中 config 中的角色时：

1. 取最相近的 1 个角色作为 resolved role。
2. 返回成功结果，不走 failure。
3. 在正文里先给该 query 的 3 个最相近候选描述，再给 resolved role 过滤后的攻略列表。
4. 追加一条“模糊代选”语义 warning，明确告诉模型：系统已经替它做了 fuzzy resolve，需要复核目标角色。
5. 第一版不设置最低接受分数门槛；只要 config 里存在角色集合，no-exact 情况就固定选排序后的 top 1 作为 resolved role，并通过 warning 明示这是代选结果。

warning 语义冻结为：

- `warn code=GUIDE_ROLE_FUZZY_MATCH query=<query> resolved=<role> msg="角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确"`

#### 2. 有精确匹配但存在高风险近似角色

当某个 `role` query 已精确命中，但仍存在高风险近似角色时：

1. 继续以精确命中角色作为 resolved role。
2. 返回成功结果。
3. 在正文里先给“当前精确命中角色 + 高风险近似角色”的候选描述，再给当前精确命中角色的攻略列表。
4. 追加一条“存在近似歧义”语义 warning，明确告诉模型：虽然系统没有替换角色，但名称邻近风险高，需要再次核对。

warning 语义冻结为：

- `warn code=GUIDE_ROLE_SIMILAR_CANDIDATES query=<query> resolved=<role> msg="角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确"`

### 高风险近似的判定

精确命中时，满足以下任一条件的其它角色都视为高风险近似候选：

- 归一化后，一个名称完整包含另一个名称。
- 归一化后，两边名称由同一组字符重排组成。
- 归一化后的字符串相似度达到高阈值。

第一版阈值冻结为 `0.75`。

候选只保留最多 3 个，且必须把最终 resolved role 放在第 1 个位置。

### 候选排序

对于 role 候选：

1. resolved / exact target 永远排第 1。
2. 其余候选按“字符重排命中”优先、再按“包含关系命中”优先、再按原始相似度降序。
3. 同分时按 canonical role name 字典序稳定排序。

### 候选描述字段

每个 role 候选需要输出：

- `query`
- `role`
- `id`
- `score`
- `selected`
- `front_back`
- `traits`
- `role_tags`

其中：

- `selected=1` 表示这条是 resolved / exact target。
- `selected=0` 表示只是相近候选。
- `traits` 取该角色对应 trait 名称摘要，不直接回传纯 id 列表。
- `role_tags` 由 scene 层内部基于原始 config 建私有 lookup 得到，不要求扩写 `guide.config.cw` 的公开 `roles[]` shape。

当一次传了多个 `--role` 时：

- 每个 query 的 candidate block 都保留，即使多个 query 最终 resolve 到同一个角色也不合并 block。
- 最终用于上游过滤的 `role_ids` 仍按首次出现顺序去重。
- 尾部 `warn` 也按 query 顺序逐条保留，不做按 code 或 resolved role 的二次折叠。

## 上游请求体

`_build_guide_list_request_payload()` 的请求体在本轮后固定为：

- `trait_ids`
  - 当没有 trait 过滤时仍是 `[]`。
  - 当有单个 trait 过滤时仍保持现有形状 `["<trait_id>", ""]`。
- `role_ids`
  - 当没有 role 过滤时是 `[]`。
  - 当有 role 过滤时是去重后的字符串 id 列表，例如 `["1001", "1002"]`。

无论最终走普通 list 还是 portal fan-out，本轮新增的 resolved `trait_ids` / `role_ids` 都必须一致地下传到同一组上游 list 请求里；不允许出现“普通 list 路径支持 role/trait，portal fan-out 路径漏掉 role/trait”的分叉。

本轮不顺手改 `trait_ids` 的既有形状。

## 输出协议

`guide.list.cw` 继续走 `_render_guide_list`，不新增 renderer 家族，不新增 YAML allowlist。

### 常规成功路径

首行继续保持：

- `ok guide.list.cw count=<n> more=<0|1> next=<token>`
- 或多 portal 分组场景下的 `ok guide.list.cw groups=<n> count=<n> more=<0|1>`

普通 guide 条目需要补一个新事实：

- `version=<适用版本>`

单条 guide 摘要第一行冻结为：

- `guide id=<id> title=<title> version=<version> idx=<n> carry=<name> hard=<0|1> change_equip=<0|1> expert=<0|1> like=<n> favour=<n>`

portal 分组内的 guide 条目同样补 `version=<version>`。

### 带 role 候选说明的成功路径

当存在 role fuzzy resolve 或 exact-but-ambiguous 情况时，success 正文顺序固定为：

1. 首行 `ok ...`
2. `shot`（若有）
3. 每个触发 query 各自的候选说明块
4. 攻略 `guide ...` 行；若当前返回是 grouped portal 形态，则候选说明块只在所有 portal group 之前统一输出一次，不在每个 group 前重复
5. 最后才是 `warn ...`

候选说明块固定使用现有允许前缀，不新增前缀：

- 块头：
  - `info role_query=<query> role_resolution=<fuzzy|exact_ambiguous> resolved=<role> candidates=<n>`
- 候选条目：
  - `opt query=<query> role=<name> id=<id> selected=<0|1> score=<score> front_back=<front|back> traits=<...> role_tags=<...>`

如果一次传了多个 `--role`，则按 query 传入顺序重复多组 `info` + `opt` 块，即使多个 query 最终 resolve 到同一个角色也不合并。

### trait 未命中的 failure 路径

trait 名称未命中时，新增的候选输出只体现在 `warn trait=... trait_id=... score=...` 这几行；其余顺序继续完全服从本 spec 前面冻结的全局 failure 顺序。

本轮不为 trait miss 追加攻略列表。

## 数据 shape

为了让 renderer 能稳定输出候选说明，scene 层 success 返回值允许新增：

- `role_candidates`
  - list
  - 每个元素对应一个 query block
  - shape 至少包含：
    - `query`
    - `role_resolution`
    - `resolved`
    - `candidates`
- `role_warnings`
  - success 时由 scene 生成
  - daemon 必须把它们提升为 envelope 顶层 `warnings`
  - renderer 不直接读取 `data.role_warnings`

其中 `role_candidates[].candidates[]` 至少包含：

- `role`
- `id`
- `score`
- `selected`
- `front_back`
- `traits`
- `role_tags`

trait 名称 miss 不走 success 返回值；scene 层抛出 `GuideTraitLookupError`，daemon 负责把它转换成标准 failure envelope，并把 trait 候选放到顶层 `warnings`。

本轮不新增新的 top-level envelope。

## 帮助文案

`trail guide list cw --help` 至少要明确出现以下信息：

- `--trait`：按羁绊名称筛选，免查 config。
- `--trait-id`：按羁绊 id 精确筛选。
- `--role`：按角色名称筛选，可重复传入多个值；名称存在歧义时会返回候选与警告。
- `--role-id`：按角色 id 精确筛选，可重复传入多个值。
- `--portal` / `--portal-id`：按投资环境筛选。
- `--match-change-job` / `--match-hard`：保留当前布尔筛选语义。

help 不需要讲完整协议，但必须让 Agent 看到：

- 哪些参数支持名称直输。
- 哪些参数支持重复传值。
- 哪些参数是互斥的。

## 测试与验收

### `tests/test_cw_guide.py`

需要覆盖：

1. `--role` 解析后确实把 id 写进上游 `role_ids`。
2. `--role-id` 直接透传成上游 `role_ids`。
3. `--trait` 精确命中后仍生成单个 `trait_ids=[id, ""]`。
4. 普通 list 路径与 portal fan-out 路径都会把 resolved `trait_ids` / `role_ids` 下传到上游 list 请求。
5. trait 名称 miss 抛出 `GuideTraitLookupError`，并带 top 3 候选。
6. role 名称 miss 返回 success，且：
   - payload 带 1 组 `role_candidates`
   - resolved role 正确
   - `role_warnings` 带 `GUIDE_ROLE_FUZZY_MATCH`
7. role 精确命中但存在高风险近似名时返回 success，且：
   - payload 带 `role_resolution=exact_ambiguous`
   - `role_warnings` 带 `GUIDE_ROLE_SIMILAR_CANDIDATES`
8. 多 `--role` query 即使 resolve 到同一角色，也会保留多个 candidate block，且最终 `role_ids` 去重顺序稳定。
9. `_normalize_lineup_summary()` 继续保留 `version`，并在 list 场景可用。

### `tests/test_guide_rpc_contracts.py`

需要覆盖：

1. CLI 对 `--trait` / `--role` / `--role-id` 的 payload 映射。
2. `--trait` 与 `--trait-id` 互斥错误。
3. `--role` 与 `--role-id` 互斥错误。
4. `guide list cw --help` 中新参数和关键说明文案。
5. 多 `--role` query 的文本协议，包含“两个 query resolve 到同一角色但 candidate block / warning 仍分别保留”的场景。
6. `--portal + --role`、`--portal + --role-id`、`--portal + --trait` 的 payload / 输出路径仍然成立。
7. role fuzzy success 的文本协议：
   - `info role_query=...`
   - `opt ...`
   - `guide ... version=...`
   - `warn code=GUIDE_ROLE_FUZZY_MATCH query=... resolved=...`
8. role exact-ambiguous success 的文本协议。
9. trait miss failure 的文本协议。

### `tests/test_atomic_commands.py`

需要覆盖：

1. `trail guide list cw --help` 至少出现 `--trait`、`--role`、`--role-id`。
2. `--role` 的 help 锚点里明确写出“可重复传入多个值”。
3. `--trait` 的 help 锚点里明确写出“按羁绊名称筛选”。
4. `--trait-id` / `--role-id` 的 help 锚点里明确写出“按 id 精确筛选”。

### `tests/test_output_rendering.py`

需要覆盖：

1. guide list 单条摘要补 `version` 后的固定顺序。
2. portal 分组输出中的 `version`。
3. success payload 中 `role_candidates` 的 `info` / `opt` 渲染。
4. grouped portal 场景下 `role_candidates` 只在 group 之前出现一次。
5. 多 query role warning 的 `query=` / `resolved=` 渲染顺序。
6. 两类 role warning 的 success 尾部渲染。
7. trait miss failure 的 `warn trait=... trait_id=... score=...` 渲染。
8. `guide.list.cw` 继续拒绝 YAML。

## 文档同步

这轮会影响 Agent 的日常用法与默认文本协议，因此必须同步：

- `README.md`
  - 补 `guide list cw` 新的名称级筛选能力。
  - 明确攻略列表现在会直接返回 `version`。
  - 若 README 里保留 `guide list cw` 的示例输出，必须同步补 `version=`。
- `skills/trail-cw-guide/SKILL.md`
  - 补按羁绊名 / 角色名筛攻略的用法。
  - 说明角色名称可能返回候选说明与警告。
  - 明确 `guide list cw` 已直接返回 `version`，Agent 在 list 阶段就应把版本兼容性纳入筛选判断。
- `skills/trail-cw/SKILL.md`
  - 只在其已直接指导“按环境/攻略筛选”处补充角色筛选入口。
  - 若该处仍指导 Agent 在 list 阶段选攻略，也要同步提醒读取 `version` 事实，而不是只看 portal / hard / change_equip / expert。

## 实施顺序

实现阶段按这个顺序拆计划：

1. 先锁 scene 层名称解析与上游请求体。
2. 再锁 CLI / daemon payload 映射与 help。
3. 最后锁 renderer、文本协议、README / skills 文档。

这样可以先把 `guide.list.cw` 的实际数据语义收稳，再补用户可见协议，避免测试假绿。
