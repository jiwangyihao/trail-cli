# CW Catalog 匹配与羁绊档位修复设计

## 背景

实机验证 `cw.slots.read` 和 `cw.shop.scan` 后发现三类问题：

1. `cw.slots.read` 把 OCR 结果 `交光` 原样输出，但当前货币战争 config 中没有该角色；真实最接近角色是 `爻光`。
2. `cw.slots.read` 的羁绊档位错误，例如公司应为 `2/3`，仙舟应为 `3/5/7/10`，击破应为 `2/4/6/8/10`，但当前代码在 config 缺少 `layers` 时回退为“拥有该羁绊的角色数量 1..N”。
3. `cw.shop.scan` 只输出商品 OCR 名称与价格，没有复用 `slots.read` 的 config 角色匹配、角色羁绊注入和羁绊摘要展示。

当前基础 config 的 `trait_info_list` 基本只有空壳，`layers`、`role_ids`、`simple_desc` 等字段为空。实测攻略列表接口里的每条攻略已包含完整 `tourn_detail.role_stages[*].traits[*]`，且三阶段阵容都可提供羁绊 entry。按 `trait_id` 筛选抽样确认可从攻略列表中得到正确层数：公司 `[2, 3]`、仙舟 `[3, 5, 7, 10]`、击破 `[2, 4, 6, 8, 10]`。

## 已确认决策

- 采用通用 catalog helper，而不是在 `slots.py`、`shop.py` 分别写补丁。
- `slots.read` 和 `shop.scan` 的非空 OCR 角色名必须尽可能落到 config 中的 canonical 角色名。
- 低分或非精确匹配不能继续输出不存在于 config 的角色名；应输出 canonical 名称，同时保留原始 OCR 与匹配分数，并追加 warning。
- 羁绊层数从攻略列表补齐，基础 config 的空 `layers` 不能再触发“按角色数量推断 1..N”的错误回退。
- 攻略 trait entry 使用稳定字段白名单：全局 catalog 保留 `id/name/icon/type/simple_desc/remarks/role_ids/layers`；`layers` 保留 `layer/quality/trait_desc`。
- 从攻略 trait entry 中剥离当前攻略或当前阶段状态，例如 `current_role_count` 和 `layers[*].is_activated`。
- `shop.scan` 增加与 `slots.read` 同源的角色匹配、traits 注入和羁绊摘要展示；摘要基于当前已上场 field slots，不把商店候选当作已拥有角色。
- 只把 Agent 可见编号统一为 1-based：CLI 参数和默认文本输出改为 `front:1`、`hand:1`、`--slot 1`；内部 list、session 和 RPC data 保持 0-based。

## 目标

1. 新增一个可复用的 CW catalog helper，集中处理角色 catalog、羁绊 catalog、角色名匹配与 trait enrichment。
2. 让需要羁绊档位的 config 调用显式取得补齐后的 `traits[].layers` 和稳定羁绊 metadata，同时不拖慢只需要基础 config 的入口命令。
3. 让 `cw.slots.read` 对角色 OCR 结果执行全 config 匹配，并用补齐后的羁绊 layers 生成正确 `trait_summary`。
4. 让 `cw.shop.scan`、`cw.shop.status` 输出角色 traits 与当前场上羁绊摘要；shop 变更命令输出 canonical 商品事实但不输出 stale 场上摘要。
5. 统一 Agent 可见槽位编号为 1-based，同时避免破坏内部状态和 daemon/RPC 合约。
6. 补齐 README、AGENTS、active skills/references、renderer 单测、CLI/stdout 测试、guide config 测试和 scene 单测。

## 非目标

- 不改变内部 session 中 `front/back/hand/items` 的 list 下标语义。
- 不改变 daemon RPC payload/data 的持久化下标语义；`raw_name/match_score/match_kind` 这类 OCR 诊断只属于当前命令 response/envelope，不写入 session 长期状态。
- 不为 `guide.list.cw` 新增 YAML 支持。
- 不把商店候选角色计入当前已上场羁绊激活状态。
- 不新增正文前缀；继续使用已有 `slot`、`item`、`info`、`warn`。
- 不把 archive skill 或 legacy skill 重新接回 active 拓扑。

## 方案比较

### 方案 A：通用 catalog helper（采用）

新增 `trail/scenes/cw/catalog.py`，集中处理角色匹配、trait catalog 构建、trait entry 清洗、trait summary。`catalog.py` 是纯函数模块：不发网络请求、不读写 cache、不导入 guide fetcher。`guide.py` 负责获取原始配置和攻略列表，再调用 catalog helper 把数据变成 scene 可消费的稳定结构。

优点是 guide、slots、shop 共用一套匹配与层数语义，后续不会再次漂移。缺点是改动范围较大，需要更新较多测试。

### 方案 B：slots/shop 局部补丁

在 `slots.py` 和 `shop.py` 各自补角色匹配和羁绊汇总。

优点是短期改动少。缺点是会复制 fuzzy matching、trait layers 解析和 warning 逻辑，后续维护风险高。

### 方案 C：先修角色匹配，羁绊 enrichment 后置

先保证不存在的 OCR 名称不再输出，下一轮再补齐攻略 trait layers。

优点是风险更低。缺点是本轮仍不能修复羁绊档位错误，不满足实机反馈的核心问题。

## Catalog Helper 设计

新增文件：`trail/scenes/cw/catalog.py`。

### 角色目录

helper 接收 normalized config 的 `roles` 与 `traits`，构建：

- `role_by_name`：canonical 名称到角色 metadata。
- `trait_name_by_id`：羁绊 id 到 canonical 名称。
- `role_traits_by_name`：角色名到羁绊名称列表。
- `role_catalog`：用于 OCR/fuzzy 匹配的角色候选列表。

`guide.py` 中已有的 `_rank_role_matches()`、`_build_role_candidate_entry()` 等角色匹配逻辑应迁移或复用到 helper。`guide.list.cw --role` 继续使用同一套匹配规则，避免 guide 与 scene 命令行为不一致。

`catalog.py` 不允许调用 `urlopen`、`Request`、`_fetch_cw_guide_list_data()` 或任何 cache path helper；这些 I/O 仍归 `guide.py` 所有。

### 角色匹配结果

新增一个轻量结果结构，字段建议为：

```python
{
    "name": "爻光",
    "role_id": "1502",
    "raw_name": "交光",
    "match_score": 0.5,
    "match_kind": "low_confidence",
    "traits": ["仙舟", "战技点", "欢愉"],
}
```

规则：

- 空 OCR 名称仍表示空槽位或空商品，不强行匹配。
- 非空 OCR 名称必须选择一个 config 角色作为 `name`。
- 精确匹配不写 `raw_name`，不产生 warning。
- 非精确匹配的当前命令 response 写 `raw_name`、内部 `match_score` 与 `match_kind=fuzzy|low_confidence|ambiguous`；`match_kind` 不写入 session。
- exact match 不在默认文本中输出 `raw_name`、`score` 或 `match_kind`。
- 分数低于阈值或候选差距过小，仍使用最高分 canonical 名称，但追加 `warn code=CW_ROLE_MATCH_LOW_CONFIDENCE ...`。
- 高相似歧义沿用 guide 的候选排序思想，必要时追加 `warn code=CW_ROLE_MATCH_AMBIGUOUS ...`。

session 长期状态只保存 canonical `name`、必要的 `role_id/star/traits` 等稳定事实；`raw_name/match_score/match_kind` 和低置信 warning 只存在于当前命令 result/envelope。需要在保存 session 后返回带诊断的 response snapshot，不能直接把诊断字段持久化到 `cw_state.slots` 或 `cw_state.shop`。

## 羁绊 Enrichment 设计

### 数据来源

`fetch_cw_guide_config()` 默认只返回基础 config，避免拖慢 `cw.start`、`cw.portal.detect/refresh`、`cw.strategy.detect/refresh` 等只需要投资环境、策略列表或角色 canonical 化的低延迟路径。需要羁绊 layers 的调用显式传入 `enrich_traits=True`，例如 `cw.slots.read`、`guide.config.cw`，以及 `cw.shop.scan/status` 在 slots fresh 且确实要输出 field trait summary 时的 lazy enrichment。shop 商品 canonical 化统一使用基础 config；`buy_slot/buy_exp` 不请求 enriched config。

基础 config 仍提供角色列表、投资环境列表、策略列表等稳定入口。enriched cache 检查必须发生在任何攻略列表请求之前；已有有效 enriched cache 时不得访问攻略列表。

随后用攻略列表补齐羁绊：

1. 先请求一次不带 trait 筛选的 raw 攻略列表。
2. 扫描 raw 返回攻略的全部 `tourn_detail.role_stages`，包括前期、中期、后期/最终阵容。
3. 对每个 `stage.traits` entry 执行稳定字段白名单清洗并合并到 trait catalog。
4. 对仍缺少 `layers` 的基础 config 羁绊，再按 `trait_id` 精准筛选请求攻略列表。
5. 每次请求都扫描该页所有攻略和所有阶段，同时记录新补齐的其它羁绊，避免为每个羁绊固定请求一次。

enrichment 必须消费 `_fetch_cw_guide_list_data()` 或新增的私有 raw-list helper 返回的原始 `data["list"]`。不得使用公开 `fetch_cw_guide_list()`、`_normalize_lineup_summary()` 或 `_normalize_role_stage_list()` 的输出作为 trait catalog 来源，因为这些路径会把 `stage.traits` 归一化为摘要字符串并丢失 `layers`、`trait_id`、`trait_icon`、`trait_type` 等字段。

### 清洗与合并

全局 catalog 中 trait entry 保留：

- `id`：来自 `trait_id` 或基础 config id。
- `name`：来自 `trait_name` 或基础 config name。
- `icon`：来自 `trait_icon` 或基础 config icon。
- `type`：来自 `trait_type`。
- `simple_desc`。
- `remarks`。
- `role_ids`。
- `layers`。

layer entry 保留：

- `layer`：激活所需角色数，是排序和档位展示的核心字段。
- `quality`。
- `trait_desc`。

剥离字段：

- `current_role_count`。
- `is_activated`。
- 其它只描述当前攻略、当前阶段或当前激活状态的字段。

同一羁绊合并规则：

- 优先按 `id` 合并；没有 id 时按 `name` 合并。
- `layers` 按 `layer` 去重，保留字段更完整的对象。
- `remarks` 和 `role_ids` 去重合并，`role_ids` 统一转字符串后排序稳定化。
- `simple_desc/icon/type` 保留非空值；冲突时保留已有值，并记录来源信息供测试断言或 debug 诊断。
- `layers[*].layer` 统一解析为正整数并按升序输出；无法解析为正整数的 layer 不进入 catalog。

### 缓存

基础 config 继续使用 `.trail/cache/cw-guide-config.json`。

为了避免每次 `slots.read` 或 `shop.scan` 都请求攻略列表，新增 enriched cache，建议路径为 `.trail/cache/cw-guide-config-enriched.json`。缓存 key 至少包含：

- `season_id`
- `sub_season_id`
- `rpg_game_big_version`
- `rpg_game_lineup_tourn_filter`

当基础 config 的这些 meta 改变时，enriched cache 失效并重建。写入 enriched cache 时使用临时文件加原子 replace，避免并发命令或 worktree 内并发测试产生半写 JSON。

只要 enrichment 请求链路成功穷尽，就可以写入有效 enriched cache；即使仍有上游确实缺少 layers 的羁绊，也应记录 `missing_trait_ids` 并作为有效 cache 命中，避免后续每次 `slots.read/shop` 重复扫描。任一 enrichment 请求失败时，不得覆盖已有有效 enriched cache；没有有效 cache 时返回基础 config，并保留空 layers。调用方不能回退到错误的 `1..N` 推断。输出可以缺少羁绊摘要，但不能输出错误档位。若需要记录请求失败诊断，可写入 `complete=0/error=...`，但 `complete=0` 不能作为有效 cache hit。

## Slots 设计

`read_cw_slots()` 的处理顺序调整为：

1. reader 读取 raw slots 与 stage status。
2. 构建 CW catalog。
3. 对 front/back/hand 的非空 raw 名称执行 config 角色匹配。
4. 合并定向读取目标与旧快照。
5. 注入角色 traits。
6. 使用补齐后的 `trait.layers` 计算 `trait_summary`。
7. 写入 `cw_state.slots` 和 `cw_state.stage.status.role_count`。
8. 当前命令 response 额外携带本次 OCR 匹配诊断和 warnings；session 只保存 canonical 稳定事实。

`trait_summary` 不再使用“角色数量推断层数”的 fallback。若某个羁绊仍缺 layers，则该羁绊可以：

- 输出 traits 到 slot 行；
- 不输出对应 summary，或输出 `档位` 缺失但不得伪造层数。

renderer 的 `slot` 行允许在非精确匹配时追加：

```text
slot pos=front:2 name=爻光 raw_name=交光 score=0.50 match_kind=low_confidence traits=仙舟|战技点|欢愉
```

scene 层可以返回匹配 warnings，但 daemon/CwService 必须把这些 warnings 提升到 envelope warnings，并与 runtime warnings 合并；warnings 不进入 session、不进入 command data。`warn` 行使用既有前缀，例如：

```text
warn code=CW_ROLE_MATCH_LOW_CONFIDENCE pos=front:2 query=交光 resolved=爻光 score=0.50 msg=角色名低置信匹配，请先看截图确认
```

## Shop 设计

`scan_cw_shop()` 增加可选基础 `guide_config` 或 catalog 参数。daemon service 在调用 `cw.shop.scan`、`cw.shop.buy_slot`、`cw.shop.buy_exp` 相关路径时，用同一基础 config 处理商品条目。`cw.shop.status` 优先投影已持久化的 canonical shop snapshot；只有在需要输出 field trait summary 且 slots fresh 时，才允许加载 enriched config。

商品处理规则：

- 空槽商品保持 `empty=1`。
- 非空商品名执行 config 角色匹配。
- session shop snapshot 中写入 canonical `name`、必要的 `role_id`、`traits`。
- 当前命令 response 的 item 可追加 `raw_name/match_score/match_kind` 诊断；这些诊断不持久化。
- renderer `item` 行追加 `traits/raw_name/score/match_kind`，字段仍使用 `item` 前缀。

示例：

```text
item idx=2 slot=2 name=Saber cost=3 traits=能量|星间旅人
item idx=3 slot=3 name=刃 cost=1 traits=燃血|星核猎手
```

`shop.scan/status` 增加与 slots 同族的羁绊摘要。摘要来源是当前 session 中非 stale 的 field slots，而不是商店商品。这样 Agent 能在同一输出中看到“当前场上羁绊状态”和“商店候选可补羁绊”。`buy_slot/buy_exp` 是会标记 slots/stage stale 的变更命令，不输出 field trait summary；它们仍输出 canonical 商品/快照事实。

只有 `cw_state.slots.stale is False` 且 front/back 快照存在时，`shop.scan/status` 才允许渲染当前场上羁绊摘要。slots stale 或缺失时，`shop.scan/status` 不得输出旧 `trait_summary`，也不得把旧摘要写入 shop snapshot。`shop.status` 必须先检查 slots 是否 fresh；不满足时不得为了摘要请求 enriched config。

`cw.shop.buy_slot --expect` 使用 Agent 上次看到的 canonical `name`。购买确认重扫必须复用同一 catalog helper，先 canonical 化后再与 `expect` 比较，避免 OCR raw 名称与 canonical 名称不一致导致误判。

## Agent 可见 1-based 编号设计

### 改动范围

仅改 Agent 可见边界：

- CLI 参数：`cw.slots.read --slot`、`cw.slots.swap --source/--target`、`cw.slots.place --action`、`cw.hand.sell --slot`。
- 默认文本输出：`cw.slots.read`、`cw.hand.sell_plan` 等所有 `slot pos=front/back/hand:<n>`。
- README、AGENTS、skills 文档中的示例。

不改内部边界：

- `SessionModel.scene_state["cw"]["slots"]` 的 list index。
- daemon RPC payload/data 中已有 list 顺序。
- daemon/service/scene 内部参数继续只消费 0-based；直接 RPC 调用仍按内部 0-based 合约处理。
- `FRONT_SLOT_POINTS`、`BACK_SLOT_POINTS`、`HAND_SLOT_POINTS` 等内部坐标数组。

### 解析与渲染

新增或调整 helper：

- CLI 层 `parse_agent_slot_reference("front:1") -> "front:0"`，再把内部值发给 daemon RPC。
- scene/daemon 层继续使用现有内部 parser 消费 `front:0`。
- renderer 层 `format_agent_slot_reference("front", 0) -> "front:1"`。
- CLI 层 `parse_agent_hand_slot(1) -> 0` 后再发给 daemon RPC。

所有 CLI 用户输入的 `0` 应按新协议视为无效并返回 `CW_OPTION_INVALID`，不做兼容回退。daemon/RPC 内部非法位置继续返回 `SLOTS_POSITION_INVALID`。

Agent 可见 failure 文本也不得泄漏内部 0-based slot 引用。slot 相关 `TrailError` 应携带结构化内部位置供 renderer 转 1-based，或 CLI/daemon 在抛错前保留原始 Agent 输入用于错误消息；默认 `why msg=...` 中出现的 `front/back/hand:<n>` 必须是 1-based。

## 输出协议影响

不新增正文前缀，不新增 YAML allowlist。

新增或扩展字段：

- 默认文本使用冻结字段 `score=...`，不输出 `match_score=...`；内部 response/envelope 可以保留 `match_score`。
- `slot raw_name=... score=... match_kind=... traits=...`
- `item raw_name=... score=... match_kind=... traits=...`
- `warn code=CW_ROLE_MATCH_LOW_CONFIDENCE pos=... query=... resolved=... score=... candidates=... msg=...`
- `warn code=CW_ROLE_MATCH_AMBIGUOUS pos=... query=... resolved=... score=... candidates=... msg=...`

warnings shape 固定为 `code/position/query/resolved/score/candidates/message`；renderer 输出时把 slot position 统一转 Agent 可见 1-based `pos=front:2`。shop item warning 使用 `slot=<shop slot>` 定位已有商品槽；无商品槽时使用当前输出顺序的 `idx=<1-based idx>`。renderer 输出 `msg=`，`score` 保留两位小数，`candidates` 用 `角色:分数|角色:分数` 压缩。warning 只通过既有 `warn` 前缀输出，在 success 的实体/info 行之后、`ref` 之前；failure 路径仍保持 `request -> shot -> why -> warn -> ref -> recover`。

`cw.shop.scan` 若带截图，success 顺序保持：首行、`shot`、`info read_image_first=1`、`item`、snapshot `info`、羁绊 `info`、`warn`、`ref`。`cw.shop.status` 不产出截图，success 顺序为首行、`item`、snapshot `info`、羁绊 `info`、`warn`、`ref`，不得输出 `shot` 或 `info read_image_first=1`。`cw.shop.buy_slot/buy_exp` 不输出 field 羁绊摘要，若带截图则顺序为首行、`shot`、`info read_image_first=1`、`item`、snapshot `info`、`warn`、`ref`。

`cw.slots.read` 的 success 顺序保持：首行、截图、`info read_image_first=1`、`slot`、羁绊 `info`、`warn`、`ref`。

## 测试计划

### Guide/config

- `fetch_cw_guide_config(enrich_traits=True)` 在基础 config 的 `trait_info_list.layers=[]` 时，从攻略列表补齐 layers。
- `fetch_cw_guide_config(enrich_traits=False)` 不请求攻略列表；`guide.config.cw` 显式使用 enriched config。
- 无筛选请求会扫描每条攻略的全部 stage。
- 仍缺 layers 的羁绊会触发按 `trait_id` 的精准补齐。
- trait 清洗保留稳定字段白名单，并剥离 `current_role_count`、`is_activated`。
- enrichment 必须分别断言公司 `[2, 3]`、仙舟 `[3, 5, 7, 10]`、击破 `[2, 4, 6, 8, 10]`。
- 同一羁绊按 `id/name` 合并时，覆盖 `layers` 按 `layer` 去重、`remarks/role_ids` 去重、`simple_desc/icon/type` 冲突保留既有值并记录诊断来源。
- enriched cache 的 meta 命中和失效行为。
- 攻略列表失败时不使用错误的 `1..N` fallback。
- `guide.config.cw --format yaml` 保持中文摘要，YAML body 保留原英文 key，并能看到 enriched `traits[].layers`。

### Catalog helper

- `交光` 匹配到 `爻光`，并返回低置信 metadata。
- 精确命中不输出 `raw_name`。
- 高相似候选生成 ambiguous warning。
- 角色 traits 来自 config role trait ids 与 trait name lookup。

### Slots

- `read_cw_slots()` 使用完整 config roles 修正无攻略状态下的 OCR 名称。
- 羁绊层数使用 enriched layers；Slots 展示路径至少覆盖公司、仙舟、击破之一，同时 guide/config enrichment 已分别覆盖三者 layers。
- 缺 layers 时不再按角色数量推断错误档位。
- 输出 pos 改为 1-based。
- 定向读取只更新目标槽并与旧快照合并；旧快照 stale/fresh 两类路径都覆盖。
- 低置信匹配输出 canonical name、默认文本 `raw_name/score/match_kind`、内部 response `match_score` 和 warning，但 session 不持久化诊断字段。
- `cw_state.stage.status.role_count` 基于合并后的内部 0-based 数据保持正确。
- CLI `--slot front:1` 定位内部第一个槽位，`front:0` 返回 `CW_OPTION_INVALID`。
- CLI/RPC/stdout 契约测试覆盖 `slots.read --slot`、`slots.swap --source/--target`、`slots.place --action`、`hand.sell --slot` 的 1-based 输入转换为内部 0-based，并分别断言 `0` 输入返回 `CW_OPTION_INVALID`。
- failure stdout 测试覆盖 slot 相关错误不会在 `why msg=...` 泄漏内部 0-based 引用。

### Shop

- `cw.shop.scan` 商品名 canonical 化，并注入 traits。
- `cw.shop.status` 保留已 canonical 化的商品和 traits。
- `cw.shop.scan/status` 输出追加当前 field trait summary，但不把商品计入 owned roles。
- stale slots、missing slots、fresh slots 三种情况下，`cw.shop.scan/status` 羁绊摘要分别不输出、不输出、输出。
- `cw.shop.buy_exp` 保持既有 `team_size=null` 等 must-keep fact。
- `cw.shop.buy_slot` 购买后确认复用同一 catalog helper，确认快照中的 canonical `name/traits` 与 `scan/status/buy_exp` 一致；失败/重试路径不污染旧快照。
- `cw.shop.scan` 低置信 item 的 immediate response 含 `raw_name/match_score/match_kind` 和 warning，但 persisted shop snapshot 与后续 `cw.shop.status` 只保留 canonical `name/role_id/traits`；`buy_slot` 确认路径同样覆盖。
- `buy_slot/buy_exp` 即使命令前 slots fresh，也不输出 field `trait_summary`，且不请求 `fetch_cw_guide_config(enrich_traits=True)`。
- `cw.shop.status` 在 slots stale/missing 时不调用 `fetch_cw_guide_config(enrich_traits=True)`。

### 输出/文档

- renderer 单测用精确 `splitlines()` 覆盖 `slot`/`item` 新字段顺序、`shot -> info read_image_first=1 -> entity -> info -> warn -> ref` 顺序、low-confidence warning 位置、`stage_status_stale=1` 不渲染旧 stage 值。
- renderer/stdout 测试覆盖 `cw.shop.status` 不输出 `shot` 或 `info read_image_first=1`。
- daemon/RPC/CLI 契约测试覆盖低置信匹配 warning 从 scene result 提升到 envelope warnings，并与 runtime warnings 合并；diagnostics/warnings 不进入 command data 或 session。
- README、AGENTS、active skills 与 references 更新 1-based 示例和新增匹配/羁绊说明。至少覆盖 `skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`，以及受 1-based、shop traits、field trait summary、low-confidence warning 影响的 `trail-cw-entry` / `trail-cw-guide` / `trail-hsr` 相关引用。
- 受影响 CLI/RPC/stdout 契约测试同步更新。
- 文档断言测试覆盖 README、AGENTS 和 active skill command surface 中的 0-based 示例已移除或改为 1-based。

## 风险与缓解

- 网络请求增加：通过显式 `enrich_traits=True`、enriched cache 和成功穷尽缓存缓解，并避免为每个羁绊固定请求一次。
- 攻略 trait entry 字段变化：清洗逻辑保持宽松，只要求 `layer` 能解析；字段缺失时不伪造档位。
- 1-based 可见编号是破坏性用户体验变化：文档和测试要明确新协议，并拒绝 `0`，避免双协议混用。
- 低置信匹配可能 canonical 到错误角色：必须在当前 response 保留 `raw_name/match_score`，默认文本渲染 `raw_name/score` 和 `warn`，并继续输出截图优先提示。

## 自检

- 规格内容完整，所有要求已有明确归属。
- 设计范围聚焦于 catalog 匹配、羁绊 enrichment、shop/slots 展示和 Agent 可见编号统一。
- 输出协议不新增前缀，不扩大 YAML allowlist。
- 编号变更明确限制在 Agent 可见边界，避免破坏内部持久化和 RPC 数据。
