# 货币战争装备合成真实动作设计

## 背景

`cw.equipment.compose` 当前只校验 fresh slots，并把指定进阶装备写入目标角色的 `equipments`。它不读取装备背包、不执行 UI 合成、不截图，也被 `AGENTS.md` 与 `skills/trail-cw-prep` 文档冻结为 session-only 命令。

现在需要把该命令改成真实 UI mutation：先刷新角色与装备快照，确认目标角色槽位正确；再根据 CW config 的进阶装备配方选择两件基础装备并执行拖动合成；合成后重新识别装备背包，强验证合成成功；最后把新合成装备拖到目标角色槽位并写入 session。

## 目标

1. `trail cw equipment compose --session <id> --name <进阶装备名> --slot <front|back|hand>:<1-based> --role <角色名>` 继续使用现有 CLI 参数和 canonical command `cw.equipment.compose`。
2. 命令开始时执行角色识别，刷新 `cw_state.slots`，并校验 `--slot` 与 `--role` 一致。
3. 执行装备识别刷新 `cw_state.equipment`，再按配方检查基础装备是否满足。
4. 材料不足时在失败输出中提供 Agent 可直接消费的持有信息与需求信息，不执行 UI 合成动作。
5. 材料满足时选择每种基础装备中 `idx` 最靠前的可用项；若两个基础装备相同，选择最靠前的两个不同 items。
6. 按“较大 idx 拖向较小 idx”执行合成；合成后重新识别装备并验证目标进阶装备处于较小 idx 位置、背包数量减少 1、被选材料身份计数按配方减少，后续装备顺移作为可识别序列的诊断事实。
7. 合成验证成功后从合成后的进阶装备位置拖到目标角色槽位，再重新识别装备背包验证被拖动的那一件进阶装备已离开背包，然后把装备记录到目标角色 `equipments`。
8. 成功输出保留旧首行核心字段，并追加截图与分段详细事实。
9. 更新项目协议、active skill 文档和契约测试，移除“只写 session、不执行真实 UI 合成”的过期说明。

## 非目标

1. 不新增 `cw.equipment.compose_ui` 或其他并行命令。
2. 不改变 CLI 参数名，不新增 YAML allowlist。
3. 不支持一次合成多个进阶装备。
4. 不把 `second_equipments` / 次选装备纳入自动目标选择；本命令只执行用户明确传入的 `--name`。
5. 不在材料不足失败路径执行任何拖动动作。

## 当前约束与发现

1. `trail/scenes/cw/equipment.py` 已有 `apply_cw_equipment_read()` 写入 `cw_state.equipment`，已有 `record_cw_equipment_compose()` 做目标角色校验和 session 写入。
2. `trail/scenes/cw/equipment_resources.py` 的 `build_cw_equipment_recipes()` 从 `equipment_list[*].compose_list[*].childrens[*]` 解析进阶装备配方，并把重复基础装备折叠成 `need` 数量。
3. `trail/scenes/cw/equipment_grid.py` 已有 `equipment_slot_center("equipment:N")`，可把装备背包编号转换成拖动坐标。
4. `trail/scenes/cw/slots.py` 已有 `build_cw_slots_reader()`、`read_cw_slots()`、`SLOT_POINTS_BY_AREA` 和 `canonical_cw_role_slots()`，可刷新并定位目标角色。
5. `trail/daemon/command_service.py` 当前把 `cw.equipment.compose` 放在 `CW_SESSION_ONLY_MUTATION_METHODS`，需要迁移到 `CW_MUTATING_METHODS`。
6. `trail/daemon/cw_service.py` 已有 runtime side-effect tracker、auto capture、taint/recover 机制，真实合成动作应复用这条 mutation 路径。
7. 默认输出协议允许 success 使用固定标题行和既有前缀；failure 不加标题，顺序固定为 `request -> shot -> why -> warn -> ref -> recover`。

## 推荐方案

采用端到端 scene 封装：新增 `compose_and_equip_cw_equipment()`，在 scene 层串联角色刷新、装备刷新、材料选择、UI 拖动、合成验证、装备到角色和 session 写入。`cw_service` 只负责注入 runtime、resource service、slots reader 和 recognizer。

这个方案的优点是业务规则集中、可单测，`cw_service` 不会变成大流程编排器，也不会留下两个行为相近的 compose 命令。

## 核心流程

1. 规范化输入：`name` 和 `role` 去首尾空白；`slot` 仍由 CLI 转换成 daemon/RPC internal 0-based，例如 `front:0`。
2. 刷新角色快照：调用 `read_cw_slots(session, reader=slots_reader, guide_config=guide_config(enrich_traits=True))` 做完整读取，不使用 targeted read。完整读取能把 `cw_state.slots.stale` 置为 `False`，并让 canonical slot/重复角色校验基于全局角色快照。完整读取属于 preflight read scope，读取过程中的 `click_point` / dismiss 不计入 mutation side effect；只有合成拖动和装备拖动开始后才进入 mutation side-effect 语义。
3. 校验目标角色和可写状态：在任何 `drag_to` 前完成 slot/role/canonical slot、重复装备、3 件装备上限、角色装备状态格式校验。该校验复用 `record_cw_equipment_compose()` 规则，但需要拆出 preflight helper，避免 UI 合成后才发现 session 不可写。
4. 刷新装备快照：调用 `apply_cw_equipment_read(session, runtime, raw_config, recognizer, request_id=...)`，写入 fresh `cw_state.equipment`。
5. 解析配方：用 `build_cw_equipment_recipes(raw_config)[name]` 获取 recipe。recipe 必须存在，并且基础装备总需求数量必须为 2。
6. 选择材料：按 recipe child 的身份匹配装备背包 items，身份优先级为 `cache_key`、`basic:<id>`、`name`。每个 child 从仍未占用的匹配 items 中取最小 `idx`；相同 child `need=2` 时取最小的两个不同 items。任一被选材料 `uncertain=1` 或低于动作阈值时，返回 preflight failure `CW_EQUIPMENT_MATERIAL_UNCERTAIN`，携带材料 idx/name/score/gap/alt，不执行拖动。
7. 材料不足：抛出 `CW_EQUIPMENT_MATERIALS_MISSING`，错误详情包含 `needed=[{name,need,have}]` 与 `held=[{idx,pos,name,equipment_id,cache_key}]`，不执行拖动。
8. 合成拖动：确定两个材料 idx 后，`from_idx=max(idx1, idx2)`，`to_idx=min(idx1, idx2)`；使用 `equipment_slot_center()` 得到坐标，执行 `runtime.drag_to(from_x, from_y, to_x, to_y)`。
9. 合成验证：再次 `apply_cw_equipment_read()` 刷新快照。验证 post-compose count 为 initial count - 1；`to_idx` 上的 item 高置信匹配目标进阶装备；从 initial item 身份多重集合中删除两个被选材料并加入目标进阶装备后，必须与 post-compose 可识别身份多重集合一致。后续顺移按可识别序列做 best-effort 诊断：可证明矛盾时验证失败，无法证明时输出 `verified_shift=0` 或 warning，但不单独阻止写入。
10. 装备到角色：用 `SLOT_POINTS_BY_AREA[area][index]` 得到目标角色槽位中心，从 `equipment_slot_center(f"equipment:{to_idx}")` 拖到目标角色槽位。装备拖动属于 mutation side effect；之后任意验证失败都不得作为普通业务失败返回。
11. 装备验证：装备拖给角色后等待 UI settle，并第三次执行 `apply_cw_equipment_read()` 刷新 post-equip 背包快照。验证 `post_equip.count == post_compose.count - 1`，且从 post-compose 身份多重集合中删除被拖动的那一件目标进阶装备后，与 post-equip 可识别身份多重集合一致；允许背包中仍存在其它同名或同身份目标装备。验证失败时不得写入角色 `equipments`；daemon 返回可恢复 unknown/tainted，并尽力携带截图或 verbose debug detail。
12. 写入 session：复用 preflight 已验证过的角色装备写入规则，把装备记录到目标角色。由于最终 equipment read 在角色写入前生成 recommendations，写入角色后必须把 `cw_state.equipment` 标记为 stale 并移除 recommendations；响应仍保留 `result_item`、`post_compose_equipment_count` 与 `post_equip_equipment_count`。
13. 返回响应数据：包含旧首行字段 `pos/name/equipment/count`，以及 `materials`、`result_item`、`compose_action`、`equip_action`、`verified`、`consumed`、`post_compose_equipment_count`、`post_equip_equipment_count`、`verified_shift`。

## 材料匹配规则

材料匹配必须防止进阶装备被误当成基础装备：

1. 若 child 有 `cache_key`，只匹配 item 的同一 `cache_key`。
2. 否则若 child 有 `id`，只匹配 item 的 `cache_key` 或 kind-aware identity 为 `basic:<id>` 的装备。
3. 只有 child 没有稳定 id/cache_key 时，才回退按 `name` 匹配。
4. 候选 items 先按 `idx` 升序排序；每选中一个 item 后从可用集合移除，保证相同基础装备需求两个时不会重复使用同一个 item。
5. 所有返回给 renderer 的材料事实保留 `idx`、`pos`、`name`、`equipment_id`、`cache_key`、`center`。

## 合成验证规则

合成验证采用“强核心验证 + 顺移诊断”，避免 UI 动作失败后误写 session，同时避免因为识别过滤或重复同名装备误杀真实成功场景：

1. `post_compose_snapshot.count == initial_snapshot.count - 1`。
2. `post_compose_snapshot.items` 中 `idx == min(material_idxs)` 的 item 必须高置信匹配目标进阶装备。
3. 身份多重集合必须符合“删除两个被选基础装备，新增一个目标进阶装备”的变化；不得只因为 `from_idx` 上出现同名基础装备就失败，因为那可能是后续同名装备顺移而来。
4. 对于 initial 中 `idx > max(material_idxs)` 且 final 中仍可识别的连续序列，检查它们是否按预期向前移动。可证明矛盾时返回验证失败；因过滤、空格或低置信导致无法证明时，记录 `verified_shift=0` 或 warning，但不单独阻止写入。
5. 若 post-compose snapshot 中目标装备 `uncertain=1`，触发 side-effect 后验证失败语义，要求 Agent 先看截图确认。
6. post-equip snapshot 必须证明被拖动的那一件目标进阶装备已离开背包，且数量为 post-compose count - 1；若背包中仍有其它同名或同身份目标装备，只要身份多重集合符合“删除一件目标装备”的变化即可通过。否则触发 side-effect 后验证失败语义。

## 输出契约

### Success

首行保持旧核心字段：

```text
ok cw.equipment.compose pos=<slot> name=<角色名> 装备=<进阶装备名> count=<角色装备数>
```

真实动作会产出截图。`cw.equipment.compose` 的 success envelope 必须有非空 `screenshot`；如果最终捕获失败，不得返回 ok success，应走现有 persisted-but-response-unknown / recover 路径。success 首行后必须紧跟：

```text
shot path=<path>
info read_image_first=1
```

随后用 `_append_section()` 输出固定分段，不新增标题名：

```text
# 综合信息
info action=compose drag_from=equipment:<大idx> drag_to=equipment:<小idx> verified=1 consumed=2 post_compose_equipment_count=<n> verified_shift=0|1
info action=equip drag_from=equipment:<小idx> drag_to=<slot> verified=1 post_equip_equipment_count=<n> equipment_stale=1
# 装备信息
item kind=material phase=pre_compose idx=<idx> pos=equipment:<idx> name=<基础装备名>
item kind=material phase=pre_compose idx=<idx> pos=equipment:<idx> name=<基础装备名>
item kind=result phase=post_compose idx=<小idx> pos=equipment:<小idx> name=<进阶装备名>
# 角色信息
slot pos=<slot> name=<角色名> 装备=<进阶装备名> count=<角色装备数>
```

`warn` 与 `ref` 仍在实体事实之后输出。

### Failure

failure 不加标题，遵循项目固定顺序。材料不足错误会在错误对象上挂 `warnings=[{code,message,需求,持有}]`；`CommandService._failure_envelope()` 仅当该属性是 list 时用 `to_jsonable()` 提升为 envelope 顶层 `warnings`，否则保持 `warnings=[]`，不得写入 `data.warnings`。`trail/output/rendering.py` 需要为 `CW_EQUIPMENT_MATERIALS_MISSING` 增加 warning 渲染分支，按 failure 顺序输出 `warn code=... 需求=... 持有=... msg=...`；不能依赖当前通用 `_append_warnings()` 分支。材料不足示例：

```text
fail cw.equipment.compose code=CW_EQUIPMENT_MATERIALS_MISSING
request id=<request_id>
why msg=<材料不足说明>
warn code=CW_EQUIPMENT_MATERIALS_MISSING 需求=<基础装备:have/need|...> 持有=<equipment:1:基础装备|...>
```

若失败发生在合成拖动或装备拖动之后，使用现有 daemon taint/recover 机制，返回可恢复 unknown，而不是伪装成普通材料不足。默认文本首行为 `fail cw.equipment.compose code=DAEMON_UNAVAILABLE tainted=1`，并带 `recover action=daemon.request_status ...`；业务失败原因通过 verbose `debug.detail` 暴露，不要求默认模式新增 `detail_code` warning。

## 错误码

1. `CW_EQUIPMENT_NAME_INVALID`：装备名为空或不在当前 config/guide 可识别集合。
2. `CW_EQUIPMENT_RECIPE_MISSING`：目标进阶装备没有可用配方。
3. `CW_EQUIPMENT_RECIPE_UNSUPPORTED`：配方基础装备总需求不是 2。
4. `CW_EQUIPMENT_MATERIALS_MISSING`：材料不足，且未执行任何 UI 合成动作。
5. `CW_EQUIPMENT_MATERIAL_UNCERTAIN`：被选基础装备低置信，且未执行任何 UI 合成动作。
6. `CW_EQUIPMENT_COMPOSE_VERIFY_FAILED`：scene/helper 层表示合成拖动后 post-compose snapshot 不能证明成功；daemon 默认把它包装为 side-effect 后可恢复 unknown。
7. `CW_EQUIPMENT_COMPOSE_VERIFY_UNCERTAIN`：scene/helper 层表示目标进阶装备识别低置信；daemon 默认把它包装为 side-effect 后可恢复 unknown。
8. `CW_EQUIPMENT_COMPOSE_EQUIP_VERIFY_FAILED`：scene/helper 层表示装备拖给角色后 post-equip snapshot 不能证明目标装备离开背包；daemon 默认把它包装为 side-effect 后可恢复 unknown。
9. 保留既有角色相关错误：`CW_EQUIPMENT_ROLE_SLOT_STALE`、`CW_EQUIPMENT_ROLE_SLOT_EMPTY`、`CW_EQUIPMENT_ROLE_SLOT_MISMATCH`、`CW_EQUIPMENT_ROLE_DUPLICATE_SLOT`、`CW_EQUIPMENT_ALREADY_HELD`、`CW_EQUIPMENT_ROLE_EQUIPMENT_FULL`、`CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID`。这些错误必须在任何 `drag_to` 前触发。

## 代码变更点

1. `trail/scenes/cw/equipment.py`：新增材料选择、recipe 校验、装备身份匹配、合成验证 helper，并新增 `compose_and_equip_cw_equipment()`。保留并复用 `record_cw_equipment_compose()` 的 session 写入规则，必要时拆出“完整 preflight 校验”和“写入角色装备”内部 helper，避免真实动作前后重复代码。
2. `trail/daemon/cw_service.py`：导入并调用 `compose_and_equip_cw_equipment()`，为 compose 注入 `runtime()`、cached raw config、equipment recognizer、slots reader、guide config，并提供 preflight read scope，使角色/装备读取不被 side-effect tracker 当成状态变更。只有合成拖动和装备拖动计入 mutation side effect。
3. `trail/daemon/command_service.py`：将 `cw.equipment.compose` 从 `CW_SESSION_ONLY_MUTATION_METHODS` 移入 `CW_MUTATING_METHODS`，并在 `_failure_envelope()` 中仅当错误对象 `warnings` 是 list 时保留为 top-level warnings，使动作前失败也能携带材料不足详情。
4. `trail/output/rendering.py`：扩展 `_render_cw_equipment_compose()`，首行保持旧字段，随后通过 `_append_success_capture_block()` 与 `_append_section()` 输出详细事实；为 `CW_EQUIPMENT_MATERIALS_MISSING` warning 增加专用渲染分支，输出 `code/需求/持有/msg`，不新增 failure 标题或正文前缀。
5. `trail/commands/cw.py`：更新 help 文案，删除“只写 session、不执行真实 UI 合成”。
6. 文档同步：`AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`。

## 测试计划

1. `tests/test_cw_equipment.py`：覆盖 recipe 为两个不同基础装备时选择最小 idx、同一基础装备需求 2 时选择最小两个不同 idx、同名但不同稳定 id/cache_key 的基础装备不误选、进阶装备与基础装备 id/name 冲突时不得选进阶装备做材料、材料不足携带 `needed/held` 且不调用 `runtime.drag_to()`、材料低置信返回 `CW_EQUIPMENT_MATERIAL_UNCERTAIN` 且不调用 `runtime.drag_to()`、已有重复装备/角色装备满/角色装备状态非法在任何 `drag_to` 前失败、slots 读取失败不调用任何 `runtime.drag_to()` 且不写角色装备、equipment read 失败不调用任何 `runtime.drag_to()` 且不写角色装备、合成动作从大 idx 拖到小 idx、post-compose 验证目标进阶装备位于小 idx 且 count 减 1、顺移无法证明时不失败但记录 `verified_shift=0`、post-compose 验证失败不写角色 `equipments`、装备拖给角色后重新识别并验证被拖动的目标装备离开背包、背包已有另一件同名目标进阶装备时 post-equip 仍可通过、验证成功后写入角色装备并把 `cw_state.equipment.stale` 设为 `True` 且清理 recommendations。
2. `tests/test_daemon_protocol.py`：覆盖 `cw.equipment.compose` 走真实 mutation、不再走 session-only mutation、preflight read scope 不把角色/装备读取计为 mutation side effect、service 使用 resource service 的 raw config/recognizer 且两次以上 equipment read 不重复下载图标、side effect 后异常进入 taint/recover、`_failure_envelope()` 会把 list 形态的 `error.warnings` 提升到 envelope 且可 JSON 化、mutation 成功后截图捕获为空或失败时返回可恢复 unknown/tainted 而不是 `ok cw.equipment.compose`。
3. `tests/test_cw_rpc_contracts.py`：覆盖 CLI 参数与 payload 保持不变、success 输出包含 `shot`、`info read_image_first=1` 与三段详细事实、`--format yaml` 仍返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
4. `tests/test_output_rendering.py`：覆盖 success 首行字段顺序保持 `pos/name/装备/count`、success 分段顺序为 `# 综合信息`、`# 装备信息`、`# 角色信息`、材料不足 failure 输出 `why` 后的 `warn code=... 需求=... 持有=...`。
5. `tests/test_output_debug.py`：更新 AGENTS/skill 文档断言，确保不再出现 active 文档把 compose 描述为只写 session。
6. `tests/test_atomic_commands.py`：更新 `cw equipment` group 和 `compose` help 断言，确认不再出现“只写 session / 不执行真实 UI 合成”，改为说明会执行真实合成/装备动作并返回截图；继续保留 `--slot` 1-based 帮助。

默认快速回归运行 `uv run pytest`；如只验证本改动，可先运行相关测试文件，再跑完整快速回归。

## 文档同步要求

1. `AGENTS.md` 中 `cw.equipment.compose` 条目改为真实 UI mutation，说明截图、分段事实、材料不足 `warn` 详情与 YAML 不支持。
2. `skills/trail-cw-prep/SKILL.md` 更新 Agent 使用方式：调用 compose 前无需手动再次 `cw.equipment.read`，命令会自行刷新角色与装备快照；成功后先看截图再消费事实。
3. `skills/trail-cw-prep/references/command-surface.md` 更新 command surface。
4. 不更新 README，除非实现时发现 README 已公开描述该命令。

## 风险与缓解

1. UI 拖动后状态不确定：任何 runtime side effect 后异常都走现有 taint/recover，不写入确定性 session 事实。
2. 装备识别低置信：合成前材料低置信可阻止动作；合成后目标低置信必须失败并提示看截图。
3. 配方数据结构变化：recipe helper 只依赖现有 `build_cw_equipment_recipes()`，并对缺失或非 2 材料配方给明确错误码。
4. 协议变更影响 Agent：保持首行不变，只追加分段事实；同步更新 AGENTS 与 active skill 文档。
5. 已有未提交工作树变更：实现时只触碰本任务相关文件，不修改用户已有无关变更。
